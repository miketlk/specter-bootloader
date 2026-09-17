"""Regression cases for corrupted metadata and incomplete flash evidence."""

import hashlib
import struct

import pytest
from flash_inspect import images, journal, nvs, versions
from flash_inspect.address_space import AddressSpace
from flash_inspect.boot_policy import analyze
from flash_inspect.input_formats import decode
from fixtures import image
from policy_test import candidate
from nvs_test import nvs_data
from input_test import ihex


def test_erased_span_cannot_hide_written_counter(nvs_data):
    raw = bytearray(nvs_data)
    # Preserve namespace at slot 0; move approval_seq to slot 2, then make
    # slot 1 an erased string with a corrupt span covering that counter.
    raw[128:160] = raw[96:128]
    raw[97:99] = bytes([0x21, 2])
    raw[32] &= ~(3 << 2)
    result = nvs.inspect(AddressSpace([(0, raw)]), 0, len(raw), "plaintext")
    assert result["values"]["approval_seq"]["state"] in ("known", "unknown")
    assert result["checks"]["integrity"]["status"] == "fail"
    assert result["checks"]["effective_values"]["status"] == "unknown"


@pytest.mark.parametrize("main", ["unknown", "unsupported", None])
def test_main_uncertainty_does_not_block_normal_boot(main):
    result = analyze(
        [
            candidate(1, 1, 1, "confirmed"),
            candidate(2, 1, 1, "none", approved="erased"),
            *([candidate(3, 1, 1, "none", approved=main)] if main else []),
        ],
        {"checks": {"root_policy": {"status": "pass"}}},
    )
    assert result["selection"] == "boot_1"
    assert not result["main"]["eligible"]


def test_sparse_journal_known_append_slot():
    trial = candidate(1, 1, 1, "none")
    trial["journal"] = journal.inspect_sector(
        AddressSpace([(0, b"\xff" * 64)]), 0, 1, 1
    )
    assert trial["journal"]["free_capacity"] is None
    assert trial["journal"]["append_available"] is True
    result = analyze(
        [trial, candidate(2, 1, 1, "none", approved="erased")],
        {"checks": {"root_policy": {"status": "pass"}}},
    )
    assert result["selection"] == "boot_1"
    assert result["state"] == "conditional_selection"


def test_unknown_capacity_is_not_exhaustion():
    result = analyze(
        [
            candidate(1, 1, 1, "none", capacity=None),
            candidate(2, 1, 1, "none", approved="erased"),
            candidate(3, 1, 1, "none"),
        ],
        {"checks": {"root_policy": {"status": "pass"}}},
    )
    assert result["state"] == "unknown"
    assert result["reason"] == "TRIAL_MARKER_CAPACITY_UNKNOWN"


@pytest.mark.parametrize(
    "field,value", [(4, 0), (24, 0x50000000), (24, 0x4FFBFFFC), (24, 0xFFFFFFFC)]
)
def test_root_static_addresses(field, value):
    raw = bytearray(image(root=True))
    struct.pack_into("<I", raw, field, value)
    raw[-32:] = hashlib.sha256(raw[:-32]).digest()
    result = images.inspect(AddressSpace([(0, raw)]), 0, len(raw), root=True)
    assert result["checks"]["hash"]["status"] == "pass"
    assert result["checks"]["checksum"]["status"] == "pass"
    check_name = "root_entry" if field == 4 else "segment_0_address"
    assert result["checks"][check_name]["status"] == "fail"
    assert result["state"] == "invalid"


@pytest.mark.parametrize(
    "tag",
    [
        b"<version:tag10>010000029x</version:tag10>",
        b"<version:tag10>0100000299</version:tag1x>",
        b"<version:tag10>01",
    ],
)
def test_malformed_version_is_present(tag):
    found = versions.tags(b"prefix" + tag, 100)
    assert len(found) == 1
    assert found[0]["offset"] == 106
    assert not found[0]["valid"]


@pytest.mark.parametrize(
    "separator", [b"\r", b"\n", b"\r\n", b"\v", b"\f", b"\x1c", b"\x1d", b"\x1e"]
)
@pytest.mark.parametrize("terminated", [False, True])
@pytest.mark.parametrize(
    "fmt,lines,expected",
    [
        ("xxd", [b"00000000: 0102", b"00000002: 0304"], b"\x01\x02\x03\x04"),
        (
            "intelhex",
            [ihex(0, 0, b"\x01\x02").encode(), ihex(0, 1).encode()],
            b"\x01\x02",
        ),
        ("hexdump", [b"00000000  01 02  |..|", b"00000002"], b"\x01\x02"),
    ],
)
def test_record_limit_counts_actual_lines(separator, terminated, fmt, lines, expected):
    raw = separator.join(lines) + (separator if terminated else b"")
    # Both records are valid at the boundary; a grammar error cannot satisfy
    # the rejection assertion when the record limit is lowered.
    space, _, _ = decode(raw, fmt, max_ranges=2)
    assert space.read_exact(0, len(expected)) == expected
    with pytest.raises(ValueError, match="record limit"):
        decode(raw, fmt, max_ranges=1)


@pytest.mark.parametrize("separator", [b"\r", b"\n", b"\r\n", b"\v", b"\f"])
@pytest.mark.parametrize("terminated", [False, True])
def test_plain_hex_record_limit(separator, terminated):
    raw = separator.join([b"0102", b"0304"]) + (separator if terminated else b"")
    assert decode(raw, "hex", max_ranges=2)[0].read_exact(0, 4) == b"\x01\x02\x03\x04"
    with pytest.raises(ValueError, match="record limit"):
        decode(raw, "hex", max_ranges=1)


@pytest.mark.parametrize("part", ["digits", "closing"])
def test_malformed_tag_fails_strict_with_repaired_integrity(tmp_path, part):
    from functools import reduce
    from operator import xor
    from cli_test import run
    from fixtures import approval, snapshot

    payload = bytearray(image())
    start = payload.index(b"<version:tag10>") + len(b"<version:tag10>")
    payload[start + (0 if part == "digits" else 10)] = ord("x")
    length = struct.unpack_from("<I", payload, 28)[0]
    payload[-33] = reduce(xor, payload[32 : 32 + length], 0xEF)
    payload[-32:] = hashlib.sha256(payload[:-32]).digest()
    raw = snapshot()
    raw[0x20000 : 0x20000 + len(payload)] = payload
    raw[0x11F000:0x11F080] = approval(payload)
    path = tmp_path / "malformed.bin"
    path.write_bytes(raw)
    proc, result = run(path, "--strict")
    assert proc.returncode == 1
    assert result["summary"]["validation"] == "fail"
    assert any(f["code"] == "VERSION_CONFLICT" for f in result["findings"])
    boot = next(r for r in result["regions"] if r["role"] == 1)
    assert boot["image"]["checks"]["hash"]["status"] == "pass"
    assert boot["image"]["checks"]["checksum"]["status"] == "pass"
    assert boot["approval"]["state"] == "valid"
    assert boot["approval"]["project_checks"]["embedded_version"]["status"] == "fail"


def test_missing_before_erased_slot_is_not_known_append_capacity():
    result = journal.inspect_sector(AddressSpace([(64, b"\xff" * 64)]), 0, 1, 1)
    assert result["append_available"] is None
    assert result["effective_state"] == "unknown"


def test_invalid_root_fails_strict(tmp_path):
    from cli_test import run
    from fixtures import snapshot

    payload = bytearray(image(root=True))
    payload[4:8] = bytes(4)
    payload[-32:] = hashlib.sha256(payload[:-32]).digest()
    raw = snapshot()
    raw[0x2000 : 0x2000 + len(payload)] = payload
    path = tmp_path / "invalid-root.bin"
    path.write_bytes(raw)
    proc, result = run(path, "--strict")
    assert proc.returncode == 1
    assert result["summary"]["validation"] == "fail"
    assert result["boot_analysis"]["state"] == "unknown"
    assert any(f["code"] == "ROOT_ENTRY_INVALID" for f in result["findings"])
