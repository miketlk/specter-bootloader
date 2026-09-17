"""Pinned IDF partition decoding plus sparse recovery and policy comparison."""

import contextlib
import hashlib
import io
import re
import struct
from .backends import PLATFORM, partition_backend
from .address_space import MissingRange
from .checks import check


def defaults():
    config = (PLATFORM / "sdkconfig.defaults").read_text()
    offset = int(re.search(r"^CONFIG_PARTITION_TABLE_OFFSET=(\S+)", config, re.M)[1], 0)
    capacity = (
        int(re.search(r"^CONFIG_ESPTOOLPY_FLASHSIZE_(\d+)MB=y", config, re.M)[1]) << 20
    )
    return offset, capacity


def entry(p, flags=None):
    return {
        "label": p.name,
        "type": p.type,
        "subtype": p.subtype,
        "type_name": {0: "app", 1: "data"}.get(p.type, "unknown"),
        "subtype_name": {
            0: {0: "factory", 16: "ota_0", 17: "ota_1"},
            1: {0: "ota", 2: "nvs", 4: "nvs_keys", 65: "boot_journal"},
        }.get(p.type, {}).get(p.subtype, "unknown"),
        "offset": p.offset,
        "size": p.size,
        "flags": flags
        if flags is not None
        else sum(1 << p.FLAGS[f] for f in p.get_flags_list()),
    }


def expected(path, offset):
    raw = path.read_bytes()
    backend = partition_backend(offset)
    with (
        contextlib.redirect_stderr(io.StringIO()),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        try:
            table = backend.PartitionTable.from_csv(raw.decode("utf-8"))
            table.verify()
        except backend.InputError as error:
            raise ValueError(str(error)) from error
    entries = [entry(p) for p in table]
    if any(p["size"] <= 0 or p["offset"] + p["size"] > 2**32 for p in entries):
        raise ValueError("invalid expected layout bounds")
    return entries, hashlib.sha256(raw).hexdigest()


def inspect(space, offset, expected_entries):
    backend = partition_backend(offset)
    result = {
        "offset": offset,
        "entries": [],
        "checks": {},
        "missing_expected": [],
        "backend": "idf/gen_esp32part.py",
    }
    checks = result["checks"]
    raw_entries = b""
    terminated = False
    incomplete = False
    md5_seen = False
    structural = True
    framing_valid = True
    for relative in range(0, backend.MAX_PARTITION_LENGTH, 32):
        try:
            raw = space.read_exact(offset + relative, 32)
        except MissingRange:
            incomplete = True
            break
        if raw[:4] == b"\xff" * 4:
            terminated = True
            checks["terminator_padding"] = check(
                raw == b"\xff" * 32, "PARTITION_TERMINATOR_PADDING"
            )
            break
        if raw[:2] == b"\xeb\xeb":
            computed = hashlib.md5(raw_entries).hexdigest()
            valid = not md5_seen and raw[16:].hex() == computed
            checks["md5_padding"] = check(
                raw[:16] == backend.MD5_PARTITION_BEGIN, "PARTITION_MD5_PADDING"
            )
            framing_valid &= valid
            checks["md5"] = check(
                valid,
                "PARTITION_MD5_MISMATCH",
                stored=raw[16:].hex(),
                computed=computed,
            )
            md5_seen = True
            continue
        if md5_seen:
            structural = False
            framing_valid = False
        try:
            with (
                contextlib.redirect_stderr(io.StringIO()),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                p = backend.PartitionDefinition.from_binary(raw)
            e = entry(p, struct.unpack_from("<I", raw, 28)[0])
            e["entry_offset"] = offset + relative
            result["entries"].append(e)
            raw_entries += raw
        except (ValueError, UnicodeError, backend.InputError):
            structural = False
            framing_valid = False
            break
    checks.setdefault(
        "md5", check(None if incomplete else False, "PARTITION_MD5_MISSING")
    )
    checks["terminator"] = check(
        None if incomplete else terminated, "PARTITION_TERMINATOR_MISSING"
    )
    entries = result["entries"]
    labels = [p["label"] for p in entries]
    structural &= len(labels) == len(set(labels))
    previous_end = offset + 4096
    for p in sorted(entries, key=lambda p: p["offset"]):
        structural &= (
            p["size"] > 0
            and p["offset"] >= previous_end
            and p["offset"] + p["size"] <= 2**32
            and p["offset"] % (65536 if p["type"] == 0 else 4096) == 0
            and p["size"] % 4096 == 0
        )
        previous_end = max(previous_end, p["offset"] + p["size"])
    checks["structure"] = check(
        False if not structural else None if incomplete else True,
        "PARTITION_STRUCTURE_INVALID",
    )
    observed = {p["label"]: p for p in entries}
    fields = ("type", "subtype", "offset", "size", "flags")
    mismatch = []
    for p in expected_entries:
        q = observed.get(p["label"])
        if q is None:
            result["missing_expected"].append(p["label"])
        if q is None or any(p[k] != q[k] for k in fields):
            mismatch.append(p["label"])
    checks["expected_layout"] = check(
        None if incomplete else not mismatch, "LAYOUT_MISMATCH", labels=mismatch
    )
    required = ("boot_a", "boot_b", "main", "boot_journal")
    expected_map = {p["label"]: p for p in expected_entries}
    policy = all(
        name in observed
        and name in expected_map
        and all(observed[name][k] == expected_map[name][k] for k in fields[:-1])
        for name in required
    )
    policy &= all(
        not (p["type"] == 0 and p["label"] not in required[:3])
        and not (p["type"] == 1 and p["subtype"] == 0)
        for p in entries
    )
    policy &= not bool(observed.get("boot_journal", {}).get("flags", 0) & 1)
    policy &= all(labels.count(name) == 1 for name in required)
    policy &= all(
        expected_map.get(name, {}).get("type") == 0
        and expected_map.get(name, {}).get("subtype") == subtype
        for name, subtype in zip(required[:3], (0, 16, 17))
    )
    policy &= (
        expected_map.get("boot_journal", {}).get("size") == 8192
        and expected_map.get("boot_journal", {}).get("subtype") == 65
    )
    checks["root_policy"] = check(
        None if incomplete else bool(policy and framing_valid and terminated),
        "LAYOUT_MISMATCH",
    )
    result["valid"] = structural and framing_valid and terminated
    return result
