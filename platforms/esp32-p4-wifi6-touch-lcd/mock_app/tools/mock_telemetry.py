#!/usr/bin/env python3
"""Decode and assert SPMF-framed CBOR status records."""

import argparse
import json
import sys
import time
import zlib
from collections.abc import Mapping
from pathlib import Path
from typing import Iterator

MAGIC = b"SPMF"
MAX_PAYLOAD = 8192


def frames(data: bytes) -> Iterator[bytes]:
    """Yield valid payloads while resynchronizing over arbitrary noise."""
    cursor = 0
    while True:
        start = data.find(MAGIC, cursor)
        if start < 0:
            return
        if len(data) - start < 10:
            return
        version, flags = data[start + 4 : start + 6]
        length = int.from_bytes(data[start + 6 : start + 10], "big")
        end = start + 10 + length + 4
        if version != 1 or flags != 0 or length > MAX_PAYLOAD:
            cursor = start + 1
            continue
        if end > len(data):
            cursor = start + 1
            continue
        expected = int.from_bytes(data[end - 4 : end], "big")
        actual = zlib.crc32(data[start + 4 : end - 4]) & 0xFFFFFFFF
        if actual != expected:
            cursor = start + 1
            continue
        yield data[start + 10 : end - 4]
        cursor = end


def validate(
    record: Mapping, board: str, bloat: int, require_approval: bool
) -> None:
    if not isinstance(record, Mapping):
        raise ValueError("mock telemetry root must be a map")
    mandatory = {
        "schema_version", "record_type", "sequence", "uptime_ms",
        "session_id", "app", "platform", "bloat", "partition",
        "approval", "checks", "boot", "ui", "errors",
    }
    missing = mandatory - record.keys()
    if missing:
        raise ValueError(f"missing mandatory keys: {sorted(missing)}")
    if record["schema_version"] != 1 or record["record_type"] != "status":
        raise ValueError("unsupported mock telemetry schema")
    nested = {}
    for key in ("platform", "bloat", "partition", "approval", "checks", "ui"):
        value = record[key]
        if not isinstance(value, Mapping):
            raise ValueError(f"mock telemetry {key} must be a map")
        nested[key] = value
    if nested["platform"].get("board") != board:
        raise ValueError("unexpected board profile")
    if nested["bloat"].get("requested_bytes") != bloat:
        raise ValueError("unexpected requested bloat")
    if nested["bloat"].get("linked_bytes") != bloat:
        raise ValueError("requested and linked bloat differ")
    if (
        nested["partition"].get("label") != "main"
        or nested["partition"].get("exact_main") is not True
    ):
        raise ValueError("application is not running from exact main role")
    for check in ("main_role", "esp_image", "bloat"):
        if nested["checks"].get(check) != "pass":
            raise ValueError(f"check {check} did not pass")
    if require_approval:
        if nested["approval"].get("state") != "valid":
            raise ValueError("approval record is not valid")
        if nested["checks"].get("approval_digest") != "pass":
            raise ValueError("approval digest did not pass")
    if nested["ui"].get("ready") is not True:
        raise ValueError("mock UI is not ready")


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group()
    source.add_argument("capture", type=Path, nargs="?")
    source.add_argument("--port")
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--board", choices=("lcd-4p3", "lcd-5"), required=True)
    parser.add_argument("--bloat", type=int, default=0)
    parser.add_argument("--require-approval", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    if args.port:
        try:
            import serial
        except ImportError as error:
            raise SystemExit("pyserial is required for live capture") from error
        deadline = time.monotonic() + args.timeout
        capture = bytearray()
        with serial.Serial(args.port, 115200, timeout=0.25) as connection:
            while time.monotonic() < deadline:
                capture.extend(connection.read(4096))
        data = bytes(capture)
    else:
        data = args.capture.read_bytes() if args.capture else sys.stdin.buffer.read()
    try:
        import cbor2
    except ImportError as error:
        raise SystemExit("cbor2 is required to decode mock telemetry") from error
    for payload in frames(data):
        try:
            record = cbor2.loads(payload)
            validate(record, args.board, args.bloat, args.require_approval)
        except (ValueError, cbor2.CBORDecodeError):
            continue
        if args.pretty:
            print(json.dumps(record, indent=2, default=lambda value: value.hex()))
        return 0
    print("no matching valid mock status frame", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
