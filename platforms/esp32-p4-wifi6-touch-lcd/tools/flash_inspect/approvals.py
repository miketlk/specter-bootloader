"""Revision-2 approval records; Root rules and project checks stay distinct."""

import struct
import zlib
from . import abi
from .address_space import MissingRange
from .checks import check, cstring, all_pass
from .versions import version


def inspect(space, offset, capacity, role, image, board="auto", mode="auto"):
    result = {"offset": offset, "state": "unknown", "checks": {}, "project_checks": {}}
    checks = result["checks"]
    if mode == "ciphertext":
        checks["content"] = check(None, "CONTENT_UNREADABLE")
        return result
    try:
        raw = space.read_exact(offset, abi.APPROVAL_SIZE)
    except MissingRange:
        checks["coverage"] = check(None, "RANGE_MISSING")
        return result
    if raw == b"\xff" * len(raw):
        result["state"] = "erased"
        return result
    revision, size = struct.unpack_from("<II", raw, 8)
    result.update(revision=revision, record_size=size, platform=cstring(raw[16:56]))
    if (
        raw[:8] != abi.APPROVAL_MAGIC
        and not raw.startswith(b"SPAPRV")
        and mode == "auto"
    ):
        checks["magic"] = check(None, "CONTENT_UNREADABLE")
        return result
    checks["magic"] = check(raw[:8] == abi.APPROVAL_MAGIC, "APPROVAL_MAGIC_INVALID")
    checks["revision"] = check(
        revision == abi.APPROVAL_REVISION, "APPROVAL_REVISION_UNSUPPORTED"
    )
    if revision != abi.APPROVAL_REVISION:
        checks["revision"]["status"] = "unsupported"
        result["state"] = "unsupported"
        return result
    r, v, length, sequence, status = struct.unpack_from("<IIIII", raw, 56)
    result.update(
        role=r,
        version=version(v),
        image_length=length,
        sequence=sequence,
        status=status,
        image_sha256=raw[76:108].hex(),
    )
    checks["size"] = check(size == abi.APPROVAL_SIZE, "APPROVAL_SIZE_INVALID")
    checks["platform"] = check(
        result["platform"]["valid"]
        and result["platform"]["value"] in abi.PLATFORMS.values(),
        "APPROVAL_PLATFORM_INVALID",
    )
    checks["board"] = check(
        None
        if board == "auto"
        else result["platform"]["value"] == abi.PLATFORMS[board],
        "BOARD_UNKNOWN" if board == "auto" else "BOARD_MISMATCH",
    )
    checks["role"] = check(r == role, "APPROVAL_ROLE_INVALID")
    checks["version"] = check(v != 0, "APPROVAL_VERSION_INVALID")
    checks["sequence"] = check(sequence != 0, "APPROVAL_SEQUENCE_INVALID")
    checks["bounds"] = check(0 < length <= capacity, "APPROVAL_LENGTH_INVALID")
    checks["status"] = check(status == abi.APPROVED, "APPROVAL_STATUS_INVALID")
    stored, computed = (
        struct.unpack_from("<I", raw, 112)[0],
        zlib.crc32(raw[:112]) & 0xFFFFFFFF,
    )
    checks["crc"] = check(
        stored == computed, "APPROVAL_CRC_MISMATCH", stored=stored, computed=computed
    )
    verified = image and image.get("checks", {}).get("hash", {}).get("status") == "pass"
    checks["image"] = check(
        None
        if not image or image["state"] == "unknown"
        else bool(
            verified
            and image["state"] == "valid"
            and image["canonical_length"] == length
            and image["appended_sha256"] == result["image_sha256"]
        ),
        "APPROVAL_IMAGE_MISMATCH",
    )
    result["project_checks"]["version_range"] = check(
        result["version"]["valid"], "VERSION_RANGE_INVALID"
    )
    values = {v["raw"] for v in (image or {}).get("versions", [])}
    result["project_checks"]["embedded_version"] = check(
        None
        if (image or {}).get("versions_truncated")
        else all(t["valid"] for t in (image or {}).get("versions", []))
        and (not values or values == {v}),
        "VERSION_DETAIL_TRUNCATED"
        if (image or {}).get("versions_truncated")
        else "VERSION_CONFLICT",
    )
    # Both writers use an erased prefix word. Firmware zero-initializes the
    # commit padding; initial USB provisioning leaves that padding erased.
    result["padding_canonical"] = raw[108:112] == b"\xff" * 4 and raw[116:128] in (
        bytes(12),
        b"\xff" * 12,
    )
    result["state"] = (
        "valid"
        if all_pass(checks)
        else "malformed"
        if any(c["status"] == "fail" for c in checks.values())
        else "unknown"
    )
    return result
