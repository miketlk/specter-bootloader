import subprocess
import sys
import pytest
from flash_inspect import nvs
from flash_inspect.address_space import AddressSpace
from flash_inspect.backends import IDF


@pytest.fixture
def nvs_data(tmp_path):
    csv = tmp_path / "nvs.csv"
    csv.write_text(
        "key,type,encoding,value\nspecter,namespace,,\napproval_seq,data,u32,7\nfloor_boot,data,u32,100000299\nfloor_main,data,u32,100000199\nwallet,namespace,,\nsecret,data,string,DO_NOT_EXPOSE\n"
    )
    output = tmp_path / "nvs.bin"
    subprocess.run(
        [
            sys.executable,
            str(
                IDF
                / "components/nvs_flash/nvs_partition_generator/nvs_partition_gen.py"
            ),
            "generate",
            str(csv),
            str(output),
            "0x3000",
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return output.read_bytes()


def test_idf_nvs(nvs_data):
    import json

    r = nvs.inspect(AddressSpace([(0, nvs_data)]), 0, len(nvs_data), "plaintext")
    assert r["values"]["approval_seq"] == {"state": "known", "value": 7}
    assert r["values"]["floor_boot"] == {"state": "known", "value": 100000299}
    assert "DO_NOT_EXPOSE" not in json.dumps(r)
    assert "wallet" not in json.dumps(r)


@pytest.mark.parametrize(
    "kind", ["crc", "partial", "ciphertext", "duplicate", "deleted", "wrong_type"]
)
def test_nvs_edge(nvs_data, kind):
    import struct
    import zlib

    raw = bytearray(nvs_data)
    if kind == "crc":
        raw[28] ^= 1
    elif kind == "partial":
        raw = raw[:-1]
    elif kind == "duplicate":
        raw[320:352] = raw[96:128]
        # entry index 8 -> Written state bits 16-17
        bitmap = int.from_bytes(raw[32:64], "little")
        bitmap = (bitmap & ~(3 << 16)) | (2 << 16)
        raw[32:64] = bitmap.to_bytes(32, "little")
    elif kind == "deleted":
        raw[32] &= ~(3 << 2)
    elif kind == "wrong_type":
        raw[97] = 1
        struct.pack_into(
            "<I", raw, 100, zlib.crc32(raw[96:100] + raw[104:128], 0xFFFFFFFF)
        )
    r = nvs.inspect(
        AddressSpace([(0, raw)]),
        0,
        len(nvs_data),
        "ciphertext" if kind == "ciphertext" else "plaintext",
    )
    assert r["values"]["approval_seq"]["state"] == {
        "duplicate": "ambiguous",
        "deleted": "absent",
    }.get(kind, "unknown")
    assert r["values"]["approval_seq"]["value"] is None


def test_nvs_rollover_and_erasing_are_unknown(nvs_data):
    import struct
    import zlib

    raw = bytearray(nvs_data)
    raw[4096:8192] = raw[:4096]
    struct.pack_into("<I", raw, 4100, 0xFFFFFFFF)
    struct.pack_into("<I", raw, 4124, zlib.crc32(raw[4100:4124], 0xFFFFFFFF))
    result = nvs.inspect(AddressSpace([(0, raw)]), 0, len(raw), "plaintext")
    assert result["values"]["approval_seq"]["state"] == "ambiguous"
    struct.pack_into("<I", raw, 0, 0xFFFFFFF8)
    result = nvs.inspect(AddressSpace([(0, raw)]), 0, len(raw), "plaintext")
    assert result["values"]["approval_seq"]["state"] == "unknown"


def test_wrong_namespace_type_does_not_claim_absent(nvs_data):
    import struct
    import zlib

    raw = bytearray(nvs_data)
    raw[65] = 4
    struct.pack_into("<I", raw, 68, zlib.crc32(raw[64:68] + raw[72:96], 0xFFFFFFFF))
    result = nvs.inspect(AddressSpace([(0, raw)]), 0, len(raw), "plaintext")
    assert result["values"]["approval_seq"]["state"] == "unknown"


@pytest.mark.parametrize("role", [1, 2, 3])
def test_below_install_floor_is_informational(nvs_data, tmp_path, role):
    from cli_test import run
    from fixtures import snapshot, image, approval, journal_record

    raw = snapshot()
    raw[0x11000 : 0x11000 + len(nvs_data)] = nvs_data
    offset = {1: 0x20000, 2: 0x120000, 3: 0x220000}[role]
    trailer = {1: 0x11F000, 2: 0x21F000, 3: 0x61F000}[role]
    older = image(version=100000099)
    raw[offset : offset + len(older)] = older
    raw[trailer : trailer + 128] = approval(older, role=role, version=100000099)
    if role == 2:
        raw[0x621000:0x621040] = journal_record()
    path = tmp_path / "snapshot.bin"
    path.write_bytes(raw)
    proc, report = run(path, "--strict")
    assert proc.returncode == 0, report["findings"]
    findings = [
        f for f in report["findings"] if f["code"] == "NVS_VERSION_BELOW_INSTALL_FLOOR"
    ]
    assert len(findings) == 1
    assert findings[0]["severity"] == "info"
    assert findings[0]["details"]["affects_root_selection"] is False
    assert report["boot_analysis"]["selection"] == "partition:2:boot_a"
