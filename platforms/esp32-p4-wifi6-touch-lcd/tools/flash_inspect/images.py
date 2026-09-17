"""Bounded ESP image framing and metadata, with shared app validation."""

import contextlib
import hashlib
import io
import struct
from functools import reduce
from operator import xor
from .address_space import MissingRange
from .backends import app_validator
from .checks import check, cstring
from .versions import tags


def descriptor(data, root):
    if root:
        if len(data) < 80 or data[0] != 80:
            return {
                "kind": "bootloader",
                "supported": False,
                "prefix_hex": data[:8].hex(),
            }
        return {
            "kind": "bootloader",
            "supported": True,
            "secure_version": data[3],
            "version": struct.unpack_from("<I", data, 4)[0],
            "idf_version": cstring(data[8:40]),
            "date_time": cstring(data[40:64]),
        }
    if len(data) < 256 or data[:4] != b"\x32\x54\xcd\xab":
        return {"kind": "app", "supported": False, "prefix_hex": data[:8].hex()}
    result = {
        "kind": "app",
        "supported": True,
        "secure_version": struct.unpack_from("<I", data, 4)[0],
        "elf_sha256": data[144:176].hex(),
        "min_efuse_revision": int.from_bytes(data[176:178], "little"),
        "max_efuse_revision": int.from_bytes(data[178:180], "little"),
        "mmu_page_size_log2": data[180],
    }
    for key, a, b in [
        ("version", 16, 48),
        ("project_name", 48, 80),
        ("time", 80, 96),
        ("date", 96, 112),
        ("idf_version", 112, 144),
    ]:
        result[key] = cstring(data[a:b])
    result["mock_main"] = "mock" in (result["project_name"]["value"] or "").lower()
    return result


def inspect(space, offset, capacity, root=False, mode="auto"):
    result = {
        "state": "unknown",
        "checks": {},
        "segments": [],
        "versions": [],
        "descriptor": None,
        "canonical_length": None,
        "appended_sha256": None,
    }
    checks = result["checks"]
    if mode == "ciphertext":
        checks["content"] = check(None, "CONTENT_UNREADABLE")
        return result
    try:
        header = space.read_exact(offset, 24)
        if header == b"\xff" * 24 and space.population(offset, capacity) == "erased":
            result["state"] = "erased"
            return result
        if header[0] != 0xE9:
            checks["magic"] = check(
                False if mode == "plaintext" else None, "CONTENT_UNREADABLE"
            )
            result["state"] = "invalid" if mode == "plaintext" else "unknown"
            return result
        result["header"] = {
            "magic": header[0],
            "segment_count": header[1],
            "flash_mode": header[2],
            "flash_frequency_encoding": header[3] & 15,
            "flash_size_encoding": header[3] >> 4,
            "entry_point": int.from_bytes(header[4:8], "little"),
            "chip_id": int.from_bytes(header[12:14], "little"),
            "min_chip_revision": header[14],
            "min_chip_revision_full": int.from_bytes(header[15:17], "little"),
            "max_chip_revision_full": int.from_bytes(header[17:19], "little"),
            "hash_appended": header[23],
        }
        checks["chip"] = check(result["header"]["chip_id"] == 18, "IMAGE_CHIP_MISMATCH")
        checks["header"] = check(
            1 <= header[1] <= 16 and header[23] in (0, 1), "IMAGE_HEADER_INVALID"
        )
        if checks["header"]["status"] != "pass":
            result["state"] = "invalid"
            return result
        cursor, checksum = 24, 0xEF
        for i in range(header[1]):
            if cursor + 8 > capacity:
                raise ValueError("segment header exceeds payload")
            address, length = struct.unpack("<II", space.read_exact(offset + cursor, 8))
            cursor += 8
            if length > capacity - cursor:
                raise ValueError("segment exceeds payload")
            segment = {
                "index": i,
                "offset": offset + cursor,
                "load_address": address,
                "length": length,
            }
            result["segments"].append(segment)
            data = space.read_exact(offset + cursor, length)
            checks[f"segment_{i}_alignment"] = check(
                length % 4 == 0, "IMAGE_SEGMENT_INVALID"
            )
            checksum = reduce(xor, data, checksum)
            if i == 0:
                result["descriptor"] = descriptor(data, root)
            found = tags(data, offset + cursor, max(1, 129 - len(result["versions"])))
            result["versions"].extend(found)
            result["versions_truncated"] = (
                result.get("versions_truncated", False) or len(result["versions"]) > 128
            )
            del result["versions"][128:]
            cursor += length
        checksum_end = (cursor + 16) & ~15
        canonical = checksum_end + (32 if header[23] else 0)
        if canonical > capacity:
            raise ValueError("image checksum/hash exceeds payload")
        result["canonical_length"] = canonical
        result["remaining_payload_capacity"] = capacity - canonical
        stored_checksum = space.read_exact(offset + checksum_end - 1, 1)[0]
        checks["checksum"] = check(
            stored_checksum == checksum,
            "IMAGE_CHECKSUM_MISMATCH",
            stored=stored_checksum,
            computed=checksum,
        )
        payload = space.read_exact(offset, canonical)
        result["full_image_sha256"] = hashlib.sha256(payload).hexdigest()
        if header[23]:
            stored = payload[-32:].hex()
            computed = hashlib.sha256(payload[:-32]).hexdigest()
            result["appended_sha256"] = stored
            checks["hash"] = check(
                stored == computed,
                "IMAGE_HASH_MISMATCH",
                stored=stored,
                computed=computed,
            )
        elif not root:
            checks["hash"] = check(False, "IMAGE_HASH_MISSING")
        checks["descriptor"] = check(
            result["descriptor"]["supported"], "IMAGE_DESCRIPTOR_UNSUPPORTED"
        )
        for name, value in result["descriptor"].items():
            if isinstance(value, dict) and "valid" in value:
                checks["descriptor_" + name] = check(
                    value["valid"], "IMAGE_DESCRIPTOR_STRING_INVALID"
                )
        values = {v["raw"] for v in result["versions"]}
        checks["version_consistency"] = check(
            False
            if len(values) > 1 or not all(v["valid"] for v in result["versions"])
            else None
            if result.get("versions_truncated")
            else True,
            "VERSION_DETAIL_TRUNCATED"
            if result.get("versions_truncated") and len(values) <= 1
            else "VERSION_CONFLICT",
        )
        if root:
            # Pinned ESP32-P4 soc.h defines HP SRAM at [0x4ff00000,
            # 0x4ffc0000); bootloader.ld places all Root sections there.
            # Root runs before the app's flash mappings are established.
            low, high = 0x4FF00000, 0x4FFC0000
            entry = result["header"]["entry_point"]
            checks["root_entry"] = check(
                low <= entry < high
                and entry % 2 == 0
                and any(
                    s["load_address"] <= entry < s["load_address"] + s["length"]
                    for s in result["segments"]
                ),
                "ROOT_ENTRY_INVALID",
            )
            for segment in result["segments"]:
                address, length = segment["load_address"], segment["length"]
                checks[f"segment_{segment['index']}_address"] = check(
                    low <= address < high
                    and address + length <= high
                    and address % 4 == 0,
                    "ROOT_LOAD_RANGE_INVALID",
                )
        else:
            validator = app_validator()
            try:
                with (
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    validator(payload)
                checks["project_image"] = check(True, "IMAGE_VALIDATION_FAILED")
            except ValueError:
                checks["project_image"] = check(False, "IMAGE_VALIDATION_FAILED")
        result["policy_valid"] = all(
            c["status"] == "pass"
            for name, c in checks.items()
            if name != "version_consistency" and not name.startswith("descriptor_")
        )
        result["state"] = "valid" if result["policy_valid"] else "invalid"
    except MissingRange:
        checks["coverage"] = check(None, "RANGE_MISSING")
    except (ValueError, struct.error):
        checks["bounds"] = check(False, "IMAGE_BOUNDS_INVALID")
        result["state"] = "invalid"
    return result
