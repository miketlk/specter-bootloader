"""Focused framing and schema tests for mock telemetry."""

import zlib

import pytest

from mock_telemetry import frames, validate


def frame(payload: bytes, *, corrupt: bool = False) -> bytes:
    header = bytes((1, 0)) + len(payload).to_bytes(4, "big")
    checksum = zlib.crc32(header + payload) & 0xFFFFFFFF
    if corrupt:
        checksum ^= 1
    return b"SPMF" + header + payload + checksum.to_bytes(4, "big")


def record() -> dict:
    return {
        "schema_version": 1,
        "record_type": "status",
        "sequence": 0,
        "uptime_ms": 1,
        "session_id": 2,
        "app": {},
        "platform": {"board": "lcd-4p3"},
        "bloat": {"requested_bytes": 7, "linked_bytes": 7},
        "partition": {"label": "main", "exact_main": True},
        "approval": {"state": "absent"},
        "checks": {"main_role": "pass", "esp_image": "pass", "bloat": "pass"},
        "boot": {},
        "ui": {"ready": True},
        "errors": [],
        "additive_key": "ignored",
    }


def test_parser_recovers_from_noise_false_magic_bad_crc_and_partial_frame():
    good = frame(b"good")
    capture = b"ROM text SPMF\x09\x00\xff\xff\xff\xff" + frame(
        b"bad", corrupt=True
    ) + good + frame(b"partial")[:-2]
    assert list(frames(capture)) == [b"good"]


def test_parser_accepts_back_to_back_frames():
    assert list(frames(frame(b"one") + frame(b"two"))) == [b"one", b"two"]


def test_parser_recovers_from_incomplete_plausible_header():
    incomplete = b"SPMF\x01\x00" + (8192).to_bytes(4, "big")
    assert list(frames(incomplete + frame(b"good"))) == [b"good"]


def test_schema_ignores_unknown_additive_keys():
    validate(record(), "lcd-4p3", 7, False)


def test_schema_rejects_mismatch():
    with pytest.raises(ValueError):
        validate(record(), "lcd-5", 7, False)


def test_schema_rejects_non_map_root():
    with pytest.raises(ValueError, match="root must be a map"):
        validate([], "lcd-4p3", 7, False)


@pytest.mark.parametrize("key", ("platform", "bloat", "partition", "checks", "ui"))
def test_schema_rejects_non_map_nested_value(key):
    malformed = record()
    malformed[key] = []
    with pytest.raises(ValueError, match=f"{key} must be a map"):
        validate(malformed, "lcd-4p3", 7, False)
