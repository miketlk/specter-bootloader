"""Independently constructed wire fixtures, never parsed to build expectations."""

import hashlib
import struct
import zlib
from functools import reduce
from operator import xor

# Deliberately explicit test geometry: catches drift in the production reader.
PARTITIONS = [
    ("nvs", 1, 2, 0x11000, 0xE000, 0),
    ("nvs_keys", 1, 4, 0x1F000, 0x1000, 1),
    ("boot_a", 0, 0, 0x20000, 0x100000, 0),
    ("boot_b", 0, 16, 0x120000, 0x100000, 0),
    ("main", 0, 17, 0x220000, 0x400000, 0),
    ("boot_journal", 1, 65, 0x620000, 0x2000, 0),
]


def table(entries=PARTITIONS, md5=True):
    data = b"".join(
        struct.pack(
            "<HBBII16sI", 0x50AA, typ, subtype, offset, size, label.encode(), flags
        )
        for label, typ, subtype, offset, size, flags in entries
    )
    if md5:
        data += b"\xeb\xeb" + b"\xff" * 14 + hashlib.md5(data).digest()
    return data.ljust(4096, b"\xff")


def image(root=False, version=100000299, project="specter", chip=18, hashed=True):
    descriptor = bytearray(256 if not root else 80)
    if root:
        descriptor[0] = 80
        struct.pack_into("<I", descriptor, 4, 1)
        descriptor[8:14] = b"v5.5.5"
    else:
        struct.pack_into("<I", descriptor, 0, 0xABCD5432)
        descriptor[16:21] = b"1.0.2"
        descriptor[48 : 48 + len(project)] = project.encode()
        descriptor[112:118] = b"v5.5.5"
        descriptor[180] = 16
    payload = (
        bytes(descriptor) + f"<version:tag10>{version:010d}</version:tag10>".encode()
    )
    payload += bytes(-len(payload) % 4)
    header = bytearray(24)
    header[0:4] = bytes([0xE9, 1, 0, 0x40])
    address = 0x4FF2A000 if root else 0x40000020
    struct.pack_into("<I", header, 4, address)
    struct.pack_into("<H", header, 12, chip)
    header[23] = int(hashed)
    data = bytes(header) + struct.pack("<II", address, len(payload)) + payload
    data += bytes((15 - len(data)) % 16) + bytes([reduce(xor, payload, 0xEF)])
    if hashed:
        data += hashlib.sha256(data).digest()
    return data


def approval(payload, role=1, version=100000299, sequence=1, board="lcd-5"):
    raw = bytearray(128)
    struct.pack_into(
        "<8sII40sIIIII32s",
        raw,
        0,
        b"SPAPRV2\0",
        2,
        128,
        ("esp32-p4-wifi6-touch-" + board).encode(),
        role,
        version,
        len(payload),
        sequence,
        0x41505052,
        payload[-32:],
    )
    struct.pack_into("<I", raw, 108, 0xFFFFFFFF)
    struct.pack_into("<I", raw, 112, zlib.crc32(raw[:112]))
    return bytes(raw)


def journal_record(sequence=1, state=0x434F4E46):
    raw = bytearray(64)
    struct.pack_into("<IIII", raw, 0, 0x4A525053, 2, sequence, state)
    struct.pack_into("<I", raw, 32, zlib.crc32(raw[:32]))
    return bytes(raw)


def snapshot():
    raw = bytearray(b"\xff" * 0x622000)
    raw[0x10000:0x11000] = table()
    root = image(root=True)
    raw[0x2000 : 0x2000 + len(root)] = root
    app = image()
    raw[0x20000 : 0x20000 + len(app)] = app
    raw[0x11F000:0x11F080] = approval(app)
    raw[0x620000:0x620040] = journal_record()
    return raw
