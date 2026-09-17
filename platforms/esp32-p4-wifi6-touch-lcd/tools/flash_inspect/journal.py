"""Physical journal traversal, including ignored forensic records after stop."""

import struct
import zlib
from . import abi
from .address_space import MissingRange
from .checks import check


def inspect_sector(space, offset, role, sequence):
    result = {
        "role": role,
        "offset": offset,
        "records": [],
        "effective_state": "none",
        "traversal_stop": None,
        "free_capacity": None,
        "append_available": None,
        "exhausted": None,
        "checks": {},
    }
    effective, stopped, unknown = "none", False, False
    erased = 0
    for slot in range(abi.JOURNAL_SECTOR_SIZE // abi.JOURNAL_SIZE):
        address = offset + slot * abi.JOURNAL_SIZE
        try:
            raw = space.read_exact(address, abi.JOURNAL_SIZE)
        except MissingRange:
            if not stopped:
                unknown = True
                stopped = True
                result["traversal_stop"] = {"slot": slot, "reason": "missing"}
            continue
        if raw == b"\xff" * abi.JOURNAL_SIZE:
            erased += 1
            if not stopped:
                result["traversal_stop"] = {"slot": slot, "reason": "erased"}
                stopped = True
            continue
        magic, revision, seq, state = struct.unpack_from("<IIII", raw)
        stored, computed = (
            struct.unpack_from("<I", raw, 32)[0],
            zlib.crc32(raw[:32]) & 0xFFFFFFFF,
        )
        valid = (
            magic == abi.JOURNAL_MAGIC
            and revision == abi.JOURNAL_REVISION
            and state in (abi.ATTEMPTED, abi.CONFIRMED)
            and stored == computed
        )
        symbolic = {abi.ATTEMPTED: "attempted", abi.CONFIRMED: "confirmed"}.get(
            state, "unknown"
        )
        result["records"].append(
            {
                "slot": slot,
                "offset": address,
                "magic": magic,
                "revision": revision,
                "sequence": seq,
                "state": state,
                "state_name": symbolic,
                "matching_sequence": seq == sequence if sequence is not None else None,
                "ignored_by_firmware": stopped,
                "validation": check(
                    valid,
                    "JOURNAL_RECORD_INVALID",
                    stored_crc=stored,
                    computed_crc=computed,
                ),
                "padding_canonical": raw[16:32] == bytes(16) and raw[36:] == bytes(28),
            }
        )
        if valid and not stopped and seq == sequence:
            effective = symbolic
    complete = space.coverage(offset, abi.JOURNAL_SECTOR_SIZE)["status"] == "complete"
    result["effective_state"] = "unknown" if unknown or sequence is None else effective
    result["free_capacity"] = erased if complete else None
    stop = result["traversal_stop"]
    result["append_available"] = (
        True if stop and stop["reason"] == "erased" else False if complete else None
    )
    result["exhausted"] = erased == 0 if complete else None
    result["checks"]["coverage"] = check(True if complete else None, "RANGE_MISSING")
    if result["exhausted"]:
        result["checks"]["capacity"] = check(False, "JOURNAL_FULL")
    return result
