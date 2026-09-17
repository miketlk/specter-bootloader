"""Pinned NVS parser adapter exposing only the Specter U32 counters.

The upstream parser decodes entries but does not reconstruct effective values.
This adapter validates pages/entries and conservatively marks duplicate written
keys, rollover, erasing pages and damaged metadata ambiguous/unknown.
"""

from .backends import nvs_backend
from .address_space import MissingRange
from .checks import check

KEYS = ("approval_seq", "floor_boot", "floor_main")


def inspect(space, offset, size, mode="auto"):
    result = {
        "backend": "idf/nvs_parser.py",
        "pages": [],
        "records": [],
        "namespaces": [],
        "values": {k: {"state": "unknown", "value": None} for k in KEYS},
        "checks": {},
    }
    if mode == "ciphertext":
        result["checks"]["content"] = check(None, "CONTENT_UNREADABLE")
        return result
    backend = nvs_backend()
    complete, healthy = True, True
    entries, sequences = [], []
    result["detail_truncated"] = size > 1024 * 4096
    if result["detail_truncated"]:
        complete = False
    for relative in range(0, min(size, 1024 * 4096), 4096):
        address = offset + relative
        if relative + 4096 > size:
            complete = False
            break
        try:
            raw = space.read_exact(address, 4096)
        except MissingRange:
            complete = False
            result["pages"].append({"offset": address, "state": "unknown"})
            continue
        if raw == b"\xff" * 4096:
            result["pages"].append({"offset": address, "state": "erased"})
            continue
        try:
            page = backend.NVS_Page(bytearray(raw), address)
        except (ValueError, IndexError, KeyError, TypeError):
            healthy = False
            result["pages"].append({"offset": address, "state": "unreadable"})
            continue
        header = page.header
        crc = header["crc"]
        valid = (
            header["status"] in ("Active", "Full")
            and header["version"] in (1, 2)
            and crc["original"] == crc["computed"]
        )
        healthy &= valid
        sequences.append(header["page_index"])
        result["pages"].append(
            {
                "offset": address,
                "state": header["status"],
                "sequence": header["page_index"],
                "version": header["version"],
                "validation": check(valid, "NVS_PAGE_INVALID", **crc),
            }
        )
        # The upstream parser groups by raw span, even for erased entries.
        # Do not trust a grouping that crosses physical bitmap states: firmware
        # skips erased entries individually, so children may be live counters.
        for e in page.entries:
            if any(child.state != e.state for child in e.children):
                healthy = False
            if e.state == "Invalid":
                healthy = False
            if e.state not in ("Written", "Erased"):
                continue
            m = e.metadata
            good = (
                valid
                and m["crc"]["original"] == m["crc"]["computed"]
                and 1 <= m["span"] <= 126 - e.index
                and e.key is not None
            )
            if e.state == "Written" and not good:
                healthy = False
            entries.append((address, header["page_index"], e, good))
    # Do not guess an ordering over wrapped or duplicate page sequence numbers.
    ordering = len(sequences) == len(set(sequences)) and (
        not sequences or max(sequences) - min(sequences) < 2**31
    )
    namespace_ids = set()
    for address, sequence, e, good in entries:
        if e.metadata["namespace"] == 0 and e.key == "specter":
            valid = (
                good
                and e.metadata["type"] == "uint8_t"
                and e.metadata["span"] == 1
                and e.data is not None
                and 0 < e.data["value"] < 255
            )
            ns = e.data["value"] if valid else None
            if e.state == "Written" and not valid:
                healthy = False
            if len(result["namespaces"]) >= 128:
                result["detail_truncated"] = True
                complete = False
                continue
            result["namespaces"].append(
                {
                    "offset": address + 64 + 32 * e.index,
                    "state": e.state,
                    "id": ns,
                    "valid": valid,
                }
            )
            if e.state == "Written" and valid:
                namespace_ids.add(ns)
    for address, sequence, e, good in entries:
        if e.metadata["namespace"] not in namespace_ids or e.key not in KEYS:
            continue
        if len(result["records"]) >= 512:
            result["detail_truncated"] = True
            complete = False
            break
        valid = (
            good
            and e.metadata["type"] == "uint32_t"
            and e.metadata["span"] == 1
            and e.data is not None
        )
        result["records"].append(
            {
                "key": e.key,
                "offset": address + 64 + 32 * e.index,
                "page_sequence": sequence,
                "entry_index": e.index,
                "state": e.state,
                "type": e.raw[1],
                "span": e.metadata["span"],
                "value": e.data["value"] if valid else None,
                "validation": check(valid, "NVS_ENTRY_INVALID", **e.metadata["crc"]),
            }
        )
    for key in KEYS:
        records = [
            r for r in result["records"] if r["key"] == key and r["state"] == "Written"
        ]
        if not complete or not healthy:
            state = "unknown"
        elif not ordering or len(namespace_ids) > 1 or len(records) > 1:
            state = "ambiguous"
        elif records and records[0]["validation"]["status"] == "pass":
            state = "known"
        elif records:
            state = "unknown"
        else:
            state = "absent"
        result["values"][key] = {
            "state": state,
            "value": records[0]["value"] if state == "known" else None,
        }
    result["checks"]["coverage"] = check(True if complete else None, "RANGE_MISSING")
    result["checks"]["integrity"] = check(
        True if healthy else False if mode == "plaintext" else None,
        "NVS_CONTENT_INVALID" if mode == "plaintext" else "CONTENT_UNREADABLE",
    )
    result["checks"]["effective_values"] = check(
        True
        if all(v["state"] in ("known", "absent") for v in result["values"].values())
        else None,
        "NVS_VALUES_UNRESOLVED",
    )
    return result
