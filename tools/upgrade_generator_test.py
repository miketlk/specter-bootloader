"""Tests for canonical ESP-IDF payload packaging."""

import importlib.util
from importlib.util import find_spec
import io
import os
from pathlib import Path

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


_PATH = Path(__file__).with_name("upgrade-generator.py")
_SPEC = importlib.util.spec_from_file_location("upgrade_generator", _PATH)
upgrade_generator = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(upgrade_generator)


def test_create_esp_idf_payload_section():
    image = minimal_esp32p4_app_image()
    source = io.BytesIO(image)
    source.name = "mock.bin"
    section = upgrade_generator.create_esp_idf_payload_section(
        source, "main", "esp32-p4-wifi6-touch-lcd-4p3")
    assert section.payload == image
    assert section.version == 100000299
    assert section.attributes["bl_attr_payload_format"] == "esp-idf-app"
    assert section.attributes["bl_attr_payload_target"] == "main"
    assert section.attributes["bl_attr_payload_sha256"] == image[-32:]
    assert "bl_attr_base_addr" not in section.attributes


def test_rejects_noncanonical_esp_idf_payload():
    source = io.BytesIO(minimal_esp32p4_app_image()[:-1] + b"\x00")
    source.name = "bad.bin"
    try:
        upgrade_generator.create_esp_idf_payload_section(
            source, "main", "esp32-p4-wifi6-touch-lcd-4p3")
    except click.ClickException:
        pass
    else:
        raise AssertionError("corrupt appended digest was accepted")


@pytest.mark.parametrize(
    "mutation",
    ["segments", "checksum", "descriptor", "load_address", "trailing"],
)
def test_rejects_structurally_invalid_esp_idf_payload(mutation):
    image = bytearray(minimal_esp32p4_app_image())
    if mutation == "segments":
        image[1] = 2
    elif mutation == "checksum":
        image[-33] ^= 1
    elif mutation == "descriptor":
        image[32] ^= 1
    elif mutation == "load_address":
        image[24:28] = (0x20000000).to_bytes(4, "little")
    else:
        image.extend(b"trailing")
    if mutation != "trailing":
        refresh_appended_digest(image)
    source = io.BytesIO(image)
    source.name = f"bad-{mutation}.bin"
    with pytest.raises(click.ClickException):
        upgrade_generator.create_esp_idf_payload_section(
            source, "main", "esp32-p4-wifi6-touch-lcd-4p3")
