"""Test support for producing a minimal ESP32-P4 app image."""

import hashlib
import struct


def minimal_esp32p4_app_image(
    version=b"0100000299", *, app_address=0x48000020,
    extra_segments=(), entrypoint=None
):
    """Create a minimal canonical app image accepted by ESP-IDF esptool."""
    header = bytearray(24)
    if entrypoint is None:
        entrypoint = app_address
    struct.pack_into(
        "<BBBBI", header, 0, 0xE9, 1 + len(extra_segments), 2, 0x2F, entrypoint
    )
    header[8] = 0xEE
    struct.pack_into("<H", header, 12, 18)
    header[23] = 1

    segment = bytearray(300)
    struct.pack_into("<I", segment, 0, 0xABCD5432)
    segment[16:21] = b"1.0.0"
    segment[48:60] = b"specter-test"
    segment[112:118] = b"v5.5.5"
    segment[180] = 16
    tag = b"<version:tag10>" + version + b"</version:tag10>"
    segment[256: 256 + len(tag)] = tag

    image = bytes(header)
    checksum = 0xEF
    for address, data in [(app_address, segment), *extra_segments]:
        image += struct.pack("<II", address, len(data)) + bytes(data)
        for value in data:
            checksum ^= value
    padding = (-len(image) - 1) % 16
    image += bytes(padding) + bytes([checksum])
    return image + hashlib.sha256(image).digest()


def refresh_appended_digest(image):
    """Recompute an image digest after a structural test mutation."""
    image[-32:] = hashlib.sha256(image[:-32]).digest()
