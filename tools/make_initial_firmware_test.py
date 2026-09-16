"""Tests for ESP32-P4 initial approval trailers."""

import importlib.util
from importlib.util import find_spec
import os
from pathlib import Path
import struct
import zlib

import click
import pytest

from esp32_image_test_support import (
    minimal_esp32p4_app_image,
    refresh_appended_digest,
)


pytestmark = pytest.mark.skipif(
    find_spec("esptool") is None and "IDF_PYTHON_ENV_PATH" not in os.environ,
    reason="ESP32 image tests require the pinned ESP-IDF Python environment",
)


_PATH = Path(__file__).with_name("make-initial-firmware.py")
_SPEC = importlib.util.spec_from_file_location("make_initial_firmware", _PATH)
make_initial_firmware = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(make_initial_firmware)


def test_confirmed_boot_trailer():
    image = minimal_esp32p4_app_image(b"0100000099")
    trailer = make_initial_firmware.make_esp32p4_trailer(
        image, 'esp32-p4-wifi6-touch-lcd-4p3', 'boot_a', 7, True)
    assert len(trailer) == 0x1000
    assert trailer[:8] == b'SPAPRV2\0'
    assert struct.unpack_from('<I', trailer, 112)[
        0] == zlib.crc32(trailer[:112])
    assert struct.unpack_from('<I', trailer, 0x80 + 32)[0] == zlib.crc32(
        trailer[0x80:0x80 + 32])
    assert trailer[0x80 + 64:] == bytes([0xff]) * (0x1000 - 0x80 - 64)


def test_main_trailer_has_no_journal():
    trailer = make_initial_firmware.make_esp32p4_trailer(
        minimal_esp32p4_app_image(b"0100000099"),
        'esp32-p4-wifi6-touch-lcd-4p3', 'main', 8)
    assert trailer[0x80:] == bytes([0xff]) * (0x1000 - 0x80)


def test_rejects_bad_esp_image_checksum():
    image = bytearray(minimal_esp32p4_app_image(b"0100000099"))
    image[-33] ^= 1
    refresh_appended_digest(image)
    with pytest.raises(click.ClickException):
        make_initial_firmware.make_esp32p4_trailer(
            image, 'esp32-p4-wifi6-touch-lcd-4p3', 'main', 8)
