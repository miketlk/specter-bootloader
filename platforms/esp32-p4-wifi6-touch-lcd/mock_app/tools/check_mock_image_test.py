"""Focused ESP image correlation tests for the mock image checker."""

import hashlib
import struct

import pytest

from check_mock_image import (
    esp_image_segments,
    validate_image_bloat,
    validate_image_matches_elf,
)


def image_segment(address: int, data: bytes) -> bytes:
    header = bytes((0xE9, 1)) + bytes(22)
    body = struct.pack("<II", address, len(data)) + data
    without_hash = header + body
    return without_hash + hashlib.sha256(without_hash).digest()


def test_image_bloat_must_match_linked_address_and_contents():
    image = image_segment(0x48000000, b"prefix" + bytes([0xA5]) * 17 + b"suffix")
    validate_image_bloat(image, 0x48000006, 0x48000017)


def test_crossed_image_without_elf_bloat_range_is_rejected():
    zero_bloat_image = image_segment(0x48000000, b"another build")
    with pytest.raises(ValueError, match="does not contain"):
        validate_image_bloat(zero_bloat_image, 0x48010000, 0x48010011)


def test_crossed_image_with_different_bytes_is_rejected():
    other_image = image_segment(0x48000000, bytes(17))
    with pytest.raises(ValueError, match="not entirely 0xA5"):
        validate_image_bloat(other_image, 0x48000000, 0x48000011)


def test_truncated_esp_segment_is_rejected():
    malformed = bytes((0xE9, 1)) + bytes(22) + struct.pack("<II", 0, 100)
    with pytest.raises(ValueError, match="truncated segment data"):
        esp_image_segments(malformed)


def test_image_segments_must_come_from_the_same_elf():
    elf = b"matching ELF bytes"
    descriptor = (
        bytes.fromhex("3254cdab")
        + bytes(140)
        + hashlib.sha256(elf).digest()
    )
    image = image_segment(0x48000000, descriptor)
    validate_image_matches_elf(image, elf)

    with pytest.raises(ValueError, match="does not match ELF"):
        validate_image_matches_elf(image, b"different artifact")
