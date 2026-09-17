import pytest
from flash_inspect import approvals, images, journal, layout, versions
from flash_inspect.address_space import AddressSpace
from flash_inspect.backends import PLATFORM
from fixtures import PARTITIONS, table, image, approval, journal_record


def test_layout_and_independent_values():
    expected, _ = layout.expected(PLATFORM / "partitions.csv", 0x10000)
    report = layout.inspect(AddressSpace([(0x10000, table())]), 0x10000, expected)
    assert report["valid"]
    assert report["checks"]["root_policy"]["status"] == "pass"
    assert report["entries"][4]["offset"] == 0x220000
    assert report["entries"][5]["size"] == 8192


@pytest.mark.parametrize(
    "change",
    [
        "md5",
        "duplicate",
        "overlap",
        "otadata",
        "extra_app",
        "missing",
        "journal_encrypted",
        "subtype",
        "size",
    ],
)
def test_bad_layout(change):
    entries = list(PARTITIONS)
    if change == "duplicate":
        entries.append(PARTITIONS[-1])
    elif change == "overlap":
        entries[1] = ("nvs_keys", 1, 4, 0x11000, 4096, 1)
    elif change in ("otadata", "extra_app"):
        entries.append(
            ("extra", 1 if change == "otadata" else 0, 0, 0x630000, 65536, 0)
        )
    elif change == "missing":
        entries.pop(2)
    elif change == "journal_encrypted":
        entries[-1] = (*entries[-1][:-1], 1)
    elif change == "subtype":
        entries[2] = ("boot_a", 0, 17, 0x20000, 0x100000, 0)
    elif change == "size":
        entries[2] = ("boot_a", 0, 0, 0x20000, 0xF0000, 0)
    data = bytearray(table(entries))
    if change == "md5":
        data[6 * 32 + 16] ^= 1
    expected, _ = layout.expected(PLATFORM / "partitions.csv", 0x10000)
    report = layout.inspect(AddressSpace([(0x10000, data)]), 0x10000, expected)
    if change == "overlap":
        assert report["checks"]["structure"]["status"] == "fail"
        assert report["checks"]["root_policy"]["status"] == "pass"
    else:
        assert report["checks"]["root_policy"]["status"] == "fail"


@pytest.mark.parametrize("root", [True, False])
def test_images(root):
    payload = image(root=root)
    r = images.inspect(AddressSpace([(0, payload)]), 0, 10000, root, "plaintext")
    assert r["state"] == "valid", r
    assert r["canonical_length"] == len(payload)
    assert r["versions"][0]["display"] == "1.0.2"
    assert r["descriptor"]["kind"] == ("bootloader" if root else "app")


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("hash", "hash"),
        ("checksum", "checksum"),
        ("length", "bounds"),
        ("chip", "chip"),
        ("no_hash", "hash"),
    ],
)
def test_image_corruption(mutation, code):
    payload = bytearray(image(hashed=mutation != "no_hash"))
    if mutation == "hash":
        payload[-1] ^= 1
    elif mutation == "checksum":
        payload[-33] ^= 1
    elif mutation == "length":
        payload[28:32] = b"\xff" * 4
    elif mutation == "chip":
        payload[12] = 1
    r = images.inspect(AddressSpace([(0, payload)]), 0, 4096, mode="plaintext")
    assert r["checks"][code]["status"] == "fail"


@pytest.mark.parametrize("length", [0, 12, 30, 100, 350])
def test_image_holes(length):
    r = images.inspect(AddressSpace([(0, image()[:length])]), 0, 4096)
    assert r["state"] == "unknown"


@pytest.mark.parametrize("board", ["lcd-4p3", "lcd-5"])
def test_approval(board):
    payload = image()
    parsed = images.inspect(AddressSpace([(0, payload)]), 0, 4096)
    r = approvals.inspect(
        AddressSpace([(4096, approval(payload, board=board))]),
        4096,
        4096,
        1,
        parsed,
        board,
    )
    assert r["state"] == "valid"
    assert r["version"]["raw"] == 100000299


@pytest.mark.parametrize("offset", [0, 12, 16, 56, 60, 64, 68, 72, 76, 108, 112])
def test_approval_corruption(offset):
    payload = image()
    parsed = images.inspect(AddressSpace([(0, payload)]), 0, 4096)
    raw = bytearray(approval(payload))
    raw[offset] ^= 1
    r = approvals.inspect(
        AddressSpace([(0, raw)]), 0, 4096, 1, parsed, "lcd-5", "plaintext"
    )
    assert r["state"] == "malformed"


def test_approval_unsupported_and_padding():
    payload = image()
    parsed = images.inspect(AddressSpace([(0, payload)]), 0, 4096)
    raw = bytearray(approval(payload))
    raw[116] = 4
    r = approvals.inspect(AddressSpace([(0, raw)]), 0, 4096, 1, parsed, "lcd-5")
    assert r["state"] == "valid" and not r["padding_canonical"]
    raw[8] = 3
    r = approvals.inspect(AddressSpace([(0, raw)]), 0, 4096, 1, parsed, "lcd-5")
    assert r["state"] == "unsupported"


@pytest.mark.parametrize("prefix", [0, 0xFFFFFFFF])
@pytest.mark.parametrize("suffix", [bytes(12), b"\xff" * 12, b"\x04" * 12])
def test_approval_writer_padding(prefix, suffix):
    import struct
    import zlib

    payload = image()
    parsed = images.inspect(AddressSpace([(0, payload)]), 0, 4096)
    raw = bytearray(approval(payload))
    struct.pack_into("<I", raw, 108, prefix)
    struct.pack_into("<I", raw, 112, zlib.crc32(raw[:112]))
    raw[116:128] = suffix
    result = approvals.inspect(AddressSpace([(0, raw)]), 0, 4096, 1, parsed, "lcd-5")
    assert result["state"] == "valid"
    assert result["padding_canonical"] == (
        prefix == 0xFFFFFFFF and suffix in (bytes(12), b"\xff" * 12)
    )


def test_journal_physical_semantics():
    data = bytearray(b"\xff" * 4096)
    data[:64] = bytes(64)  # Invalid occupied record consumes space.
    data[64:128] = journal_record(1, 0x4154544D)
    data[128:192] = journal_record(2)  # Stale sequence is ignored.
    data[256:320] = journal_record(1)  # Beyond erased stop, ignored.
    r = journal.inspect_sector(AddressSpace([(0, data)]), 0, 1, 1)
    assert r["effective_state"] == "attempted"
    assert r["traversal_stop"] == {"slot": 3, "reason": "erased"}
    assert r["records"][-1]["ignored_by_firmware"]
    assert r["free_capacity"] == 60


def test_journal_exhaustion_and_partial():
    r = journal.inspect_sector(AddressSpace([(0, journal_record() * 64)]), 0, 1, 1)
    assert r["effective_state"] == "confirmed" and r["exhausted"]
    r = journal.inspect_sector(AddressSpace([(0, journal_record())]), 0, 1, 1)
    assert r["effective_state"] == "unknown" and r["free_capacity"] is None
    r = journal.inspect_sector(AddressSpace([(0, b"\xff" * 64)]), 0, 2, 1)
    assert r["effective_state"] == "none"


@pytest.mark.parametrize(
    "raw,display",
    [
        (100000299, "1.0.2"),
        (100000200, "1.0.2-rc0"),
        (4199999999, "41.999.999"),
        (0, None),
        (4200000000, None),
    ],
)
def test_versions(raw, display):
    assert versions.version(raw)["display"] == display


def test_md5_absence_is_project_failure_not_root_rejection():
    expected, _ = layout.expected(PLATFORM / "partitions.csv", 0x10000)
    report = layout.inspect(
        AddressSpace([(0x10000, table(md5=False))]), 0x10000, expected
    )
    assert report["checks"]["md5"]["status"] == "fail"
    assert report["checks"]["root_policy"]["status"] == "pass"
    assert report["valid"]


def test_unknown_data_and_future_aux_are_inventory():
    expected, _ = layout.expected(PLATFORM / "partitions.csv", 0x10000)
    for label, subtype in [("future", 200), ("main_aux", 66)]:
        entries = PARTITIONS + [(label, 1, subtype, 0x630000, 4096, 0)]
        report = layout.inspect(
            AddressSpace([(0x10000, table(entries))]), 0x10000, expected
        )
        assert report["checks"]["root_policy"]["status"] == "pass"
        assert report["entries"][-1]["type"] == 1


def test_noncanonical_md5_padding_is_not_root_rejection():
    raw = bytearray(table())
    raw[6 * 32 + 2] = 0
    expected, _ = layout.expected(PLATFORM / "partitions.csv", 0x10000)
    r = layout.inspect(AddressSpace([(0x10000, raw)]), 0x10000, expected)
    assert r["checks"]["root_policy"]["status"] == "pass"
    assert r["checks"]["md5_padding"]["status"] == "fail"


def test_project_version_range_does_not_rewrite_root_acceptance():
    raw = image(version=4200000000)
    decoded = images.inspect(AddressSpace([(0, raw)]), 0, 4096)
    result = approvals.inspect(
        AddressSpace([(0, approval(raw, version=4200000000))]),
        0,
        4096,
        1,
        decoded,
        "lcd-5",
    )
    assert result["state"] == "valid"
    assert result["project_checks"]["version_range"]["status"] == "fail"
    assert result["checks"]["version"]["status"] == "pass"


def test_partial_erased_approval_never_becomes_erased():
    result = approvals.inspect(AddressSpace([(0, b"\xff" * 127)]), 0, 4096, 1, None)
    assert result["state"] == "unknown"


def test_app_hash_hole_and_exact_payload_capacity():
    raw = image()
    exact = images.inspect(AddressSpace([(0, raw)]), 0, len(raw))
    assert exact["remaining_payload_capacity"] == 0
    assert exact["state"] == "valid"
    hole = images.inspect(
        AddressSpace([(0, raw[:-20]), (len(raw) - 10, raw[-10:])]), 0, 4096
    )
    assert hole["state"] == "unknown"
    too_small = images.inspect(AddressSpace([(0, raw)]), 0, len(raw) - 1)
    assert too_small["checks"]["bounds"]["status"] == "fail"


def test_descriptor_malformed_strings_are_bounded():
    raw = bytearray(256)
    raw[:4] = b"\x32\x54\xcd\xab"
    raw[48:80] = b"X" * 32
    descriptor = images.descriptor(raw, False)
    assert descriptor["project_name"] == {
        "value": None,
        "valid": False,
        "raw_hex": (b"X" * 32).hex(),
    }


def test_approval_board_role_length_and_digest_contradictions():
    import struct
    import zlib

    payload = image()
    parsed = images.inspect(AddressSpace([(0, payload)]), 0, 4096)
    for offset, value, code in [
        (56, 2, "role"),
        (64, len(payload) - 1, "image"),
        (76, 0, "image"),
    ]:
        raw = bytearray(approval(payload))
        struct.pack_into("<I", raw, offset, value)
        struct.pack_into("<I", raw, 112, zlib.crc32(raw[:112]))
        result = approvals.inspect(
            AddressSpace([(0, raw)]), 0, 4096, 1, parsed, "lcd-5"
        )
        assert result["checks"][code]["status"] == "fail"
    result = approvals.inspect(
        AddressSpace([(0, approval(payload))]), 0, 4096, 1, parsed, "lcd-4p3"
    )
    assert result["platform"]["value"] == "esp32-p4-wifi6-touch-lcd-5"
    assert result["checks"]["board"]["status"] == "fail"


def test_version_details_are_bounded():
    raw = b"<version:tag10>0100000299</version:tag10>" * 1000
    assert len(versions.tags(raw, 0)) == 129
