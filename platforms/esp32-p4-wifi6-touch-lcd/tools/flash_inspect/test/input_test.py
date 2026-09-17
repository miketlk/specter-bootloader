import pytest
from flash_inspect.address_space import AddressSpace, MissingRange
from flash_inspect.input_formats import decode


def ihex(address, kind, data=b""):
    r = bytes([len(data)]) + address.to_bytes(2, "big") + bytes([kind]) + data
    return ":" + (r + bytes([-sum(r) & 255])).hex()


@pytest.mark.parametrize(
    "fmt,data",
    [
        ("bin", b"\x01\x02\xab\xcd"),
        ("hex", b"01 02 ab cd"),
        ("xxd", b"00000000: 0102 abcd  ....\n"),
        ("hexdump", b"00000000  01 02 ab cd  |....|\n00000004\n"),
        ("intelhex", (ihex(0, 0, b"\x01\x02\xab\xcd") + "\n" + ihex(0, 1)).encode()),
    ],
)
def test_equivalent(fmt, data):
    space, detected, _ = decode(data, fmt)
    assert space.read_exact(0, 4) == b"\x01\x02\xab\xcd"
    assert decode(data)[1] == fmt


@pytest.mark.parametrize(
    "fmt,data",
    [
        ("hex", b"abc"),
        ("hex", b"01 z0"),
        ("intelhex", b":010000000100"),
        ("intelhex", ihex(0, 0, b"a").encode()),
        ("xxd", b"00000000: zzzz  .."),
        ("hexdump", b"*\n00000010"),
        ("hexdump", b"00000000  01  |.|\n00000002"),
    ],
)
def test_malformed(fmt, data):
    with pytest.raises(ValueError):
        decode(data, fmt)


def test_sparse_and_overlap():
    space = AddressSpace([(0xFFFF0000, b"\xff" * 2), (0xFFFF0004, b"\xff" * 2)])
    assert space.coverage(0xFFFF0000, 6) == {"status": "partial", "covered_bytes": 4}
    assert space.population(0xFFFF0000, 6) == "unknown"
    with pytest.raises(MissingRange):
        space.read_exact(0xFFFF0000, 6)
    with pytest.raises(ValueError):
        AddressSpace([(2, b"ab"), (2, b"ab")])
    with pytest.raises(ValueError):
        AddressSpace([(0, b"a"), (100, b"b")], 50)
    assert AddressSpace([(1, bytes(4))]).population(1, 4) == "non_erased"


@pytest.mark.parametrize("kind,expected", [(2, 0x12340), (4, 0x12340000)])
def test_extended(kind, expected):
    text = "\n".join(
        [
            ihex(0, kind, b"\x12\x34"),
            ihex(2, 0, b"ab"),
            ihex(0, 5, bytes(4)),
            ihex(0, 1),
        ]
    )
    s, _, metadata = decode(text.encode())
    assert s.read_exact(expected + 2, 2) == b"ab"
    assert metadata["start_records"] == [{"type": 5, "value": 0}]
    with pytest.raises(ValueError):
        decode(text.encode(), base=0)


def test_hexdump_repeat():
    text = b"00000000  00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00  |................|\n*\n00000040\n"
    assert decode(text)[0].read_exact(0, 64) == bytes(64)
    with pytest.raises(ValueError):
        decode(text.replace(b"00000040", b"00000041"))


def test_binary_base_and_auto_ambiguity():
    assert decode(b"aa")[0].read_exact(0, 1) == b"\xaa"
    assert decode(b"aa", "bin", 100)[0].read_exact(100, 2) == b"aa"
    assert decode(b"", "bin")[0].supplied() == []


@pytest.mark.parametrize("base", [None, 0x2000])
def test_auto_binary_with_hexdump_delimiter(base):
    payload = b"\xe9\x00\x00\x00|" + bytes(range(256))
    space, fmt, _ = decode(payload, base=base)
    assert fmt == "bin"
    assert space.read_exact(base or 0, len(payload)) == payload


def test_auto_rejects_malformed_addressed_hexdump():
    with pytest.raises(ValueError, match="invalid hexdump bytes"):
        decode(b"00000000  zz  |.|\n00000001\n")


def test_intelhex_rejects_overlap_and_data_after_eof():
    for records in [
        [ihex(0, 0, b"ab"), ihex(1, 0, b"bc"), ihex(0, 1)],
        [ihex(0, 1), ihex(0, 0, b"a")],
        [ihex(0, 4, b"a"), ihex(0, 1)],
    ]:
        with pytest.raises(ValueError):
            decode("\n".join(records).encode())


def test_xxd_short_final_and_out_of_order_ranges():
    text = b"00000002: 0203  ..\n00000000: 0001  ..\n00000004: 04    .\n"
    assert decode(text, "xxd")[0].read_exact(0, 5) == bytes(range(5))
    with pytest.raises(ValueError):
        decode(text + b"00000005: 05    .\n", "xxd")


def test_range_limit_and_32_bit_overflow():
    with pytest.raises(ValueError):
        decode(b"00\n00\n00\n", "hex", max_ranges=2)
    with pytest.raises(ValueError):
        AddressSpace([(2**32 - 1, b"ab")])


@pytest.mark.parametrize("payload", [b"    ", b"  x ", b"abcd", bytes(range(17))])
def test_actual_xxd_ascii_spacing(payload):
    import shutil
    import subprocess

    if not shutil.which("xxd"):
        pytest.skip("xxd unavailable")
    text = subprocess.check_output(["xxd"], input=payload)
    assert decode(text, "xxd")[0].read_exact(0, len(payload)) == payload
