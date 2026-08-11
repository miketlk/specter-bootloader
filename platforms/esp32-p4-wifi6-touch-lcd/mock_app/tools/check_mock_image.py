#!/usr/bin/env python3
"""Validate a mock-main ELF/image and write its machine-readable manifest."""

import argparse
import hashlib
import json
import os
import re
import struct
import subprocess
from pathlib import Path


SECTION_RE = re.compile(
    r"\[\s*\d+\]\s+\.specter_mock_bloat\s+(\S+)\s+"
    r"[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+"
    r"\S+\s+(\S+)"
)
SYMBOL_RE = re.compile(
    r"^\s*\d+:\s+([0-9a-fA-F]+)\s+\S+\s+\S+\s+\S+\s+\S+\s+"
    r"(\d+)\s+(specter_mock_bloat_(?:start|end))$",
    re.MULTILINE,
)
MAP_BLOAT_RE = re.compile(
    r"^ \.specter_mock_bloat\s*\n"
    r"\s*(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)\s+",
    re.MULTILINE,
)
ESP_IMAGE_HEADER_SIZE = 24
ESP_SEGMENT_HEADER_SIZE = 8
ESP_IMAGE_MAGIC = 0xE9
ESP_APP_DESC_MAGIC = bytes.fromhex("3254cdab")
ESP_APP_DESC_ELF_SHA_OFFSET = 144
SHA256_SIZE = 32


def integer(value: str) -> int:
    return int(value, 0)


def esp_image_segments(image: bytes) -> list[tuple[int, bytes]]:
    """Return load-address/data pairs from a version-one ESP image."""
    if len(image) < ESP_IMAGE_HEADER_SIZE or image[0] != ESP_IMAGE_MAGIC:
        raise ValueError("canonical image has an invalid ESP image header")
    segment_count = image[1]
    cursor = ESP_IMAGE_HEADER_SIZE
    segments = []
    for _ in range(segment_count):
        if cursor + ESP_SEGMENT_HEADER_SIZE > len(image):
            raise ValueError("canonical image has a truncated segment header")
        address, length = struct.unpack_from("<II", image, cursor)
        cursor += ESP_SEGMENT_HEADER_SIZE
        end = cursor + length
        if end > len(image):
            raise ValueError("canonical image has truncated segment data")
        segments.append((address, image[cursor:end]))
        cursor = end
    return segments


def validate_image_bloat(image: bytes, start: int, end: int) -> None:
    """Prove that the linked filler range is present in an ESP image segment."""
    segments = esp_image_segments(image)
    if start == end:
        return
    for address, data in segments:
        offset = start - address
        if offset >= 0 and end <= address + len(data):
            if data[offset : offset + end - start] != bytes([0xA5]) * (end - start):
                raise ValueError("canonical image mock bloat is not entirely 0xA5")
            return
    raise ValueError("canonical image does not contain the linked mock bloat range")


def validate_image_matches_elf(image: bytes, elf: bytes) -> None:
    """Verify the ESP app descriptor's embedded digest of the source ELF."""
    segments = esp_image_segments(image)
    if not segments:
        raise ValueError("canonical image has no segments")
    app_desc = segments[0][1]
    digest_end = ESP_APP_DESC_ELF_SHA_OFFSET + SHA256_SIZE
    if len(app_desc) < digest_end or not app_desc.startswith(ESP_APP_DESC_MAGIC):
        raise ValueError("canonical image has an invalid ESP app descriptor")
    if app_desc[ESP_APP_DESC_ELF_SHA_OFFSET:digest_end] != hashlib.sha256(elf).digest():
        raise ValueError("canonical image app descriptor does not match ELF")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--elf", type=Path, required=True)
    parser.add_argument("--bin", dest="binary", type=Path, required=True)
    parser.add_argument("--map", dest="map_file", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--board", required=True)
    parser.add_argument("--requested", type=integer, required=True)
    parser.add_argument("--partition-size", type=integer, required=True)
    parser.add_argument("--trailer-size", type=integer, required=True)
    parser.add_argument("--project-version", required=True)
    parser.add_argument("--idf-version", required=True)
    args = parser.parse_args()

    readelf = os.environ.get("READELF", "readelf")
    sections = subprocess.run(
        [readelf, "-SW", str(args.elf)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    symbols = subprocess.run(
        [readelf, "-sW", str(args.elf)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    symbol_values = {
        name: (int(value, 16), int(section_index))
        for value, section_index, name in SYMBOL_RE.findall(symbols)
    }
    if set(symbol_values) != {
        "specter_mock_bloat_start",
        "specter_mock_bloat_end",
    }:
        raise SystemExit("mock bloat boundary symbols are missing")
    start, section_index = symbol_values["specter_mock_bloat_start"]
    end, end_section_index = symbol_values["specter_mock_bloat_end"]
    linked = end - start
    if section_index != end_section_index:
        raise SystemExit("mock bloat boundary symbols use different sections")

    match = SECTION_RE.search(sections)
    if not match:
        containing_section = re.search(
            rf"\[\s*{section_index}\]\s+\S+\s+(\S+)\s+"
            r"([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+"
            r"\S+\s+(\S+)",
            sections,
        )
        if not containing_section:
            raise SystemExit("cannot resolve mock bloat containing ELF section")
        section_type, address_hex, offset_hex, size_hex, flags = (
            containing_section.groups()
        )
        address = int(address_hex, 16)
        offset = int(offset_hex, 16) + start - address
        if end > address + int(size_hex, 16):
            raise SystemExit("mock bloat symbols exceed their ELF section")
    else:
        section_type, offset_hex, size_hex, flags = match.groups()
        offset = int(offset_hex, 16)
    if section_type != "PROGBITS" or "A" not in flags:
        raise SystemExit("mock bloat is not allocatable PROGBITS")
    if linked != args.requested:
        raise SystemExit(f"linked bloat {linked} != requested {args.requested}")
    elf_bytes = args.elf.read_bytes()
    if linked and elf_bytes[offset : offset + linked] != bytes([0xA5]) * linked:
        raise SystemExit("mock bloat section does not contain only 0xA5")
    map_match = MAP_BLOAT_RE.search(args.map_file.read_text(errors="replace"))
    if not map_match:
        raise SystemExit("mock bloat section is missing from linker map")
    map_start, map_size = (int(value, 16) for value in map_match.groups())
    if map_start != start or map_size != linked:
        raise SystemExit("linker map mock bloat range does not match ELF")

    image = args.binary.read_bytes()
    capacity = args.partition_size - args.trailer_size
    if len(image) > capacity:
        raise SystemExit(
            f"canonical image {len(image)} exceeds payload capacity {capacity}"
        )
    if len(image) < 32 or hashlib.sha256(image[:-32]).digest() != image[-32:]:
        raise SystemExit("canonical image does not end in its appended SHA-256")
    try:
        validate_image_matches_elf(image, elf_bytes)
        validate_image_bloat(image, start, end)
    except ValueError as error:
        raise SystemExit(str(error)) from error

    manifest = {
        "schema_version": 1,
        "app": "mock-main",
        "board": args.board,
        "requested_bloat_bytes": args.requested,
        "linked_bloat_bytes": linked,
        "bloat_section": ".specter_mock_bloat",
        "bloat_fill_byte": 0xA5,
        "canonical_image_length": len(image),
        "payload_capacity": capacity,
        "remaining_bytes": capacity - len(image),
        "partition_size": args.partition_size,
        "trailer_size": args.trailer_size,
        "project_version": args.project_version,
        "esp_idf_version": args.idf_version,
        "artifact_sha256": hashlib.sha256(image).hexdigest(),
        "appended_image_sha256": image[-32:].hex(),
    }
    args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
