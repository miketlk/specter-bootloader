"""Tests for ESP32-P4 initial approval trailers."""

import importlib.util
from importlib.util import find_spec
import os
from pathlib import Path
import struct
import zlib

import click
from click.testing import CliRunner
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
        image, 'esp32-p4-wifi6-touch-lcd-4p3', 'boot_a', 7)
    assert len(trailer) == 0x1000
    assert trailer[:8] == b'SPAPRV2\0'
    assert struct.unpack_from('<I', trailer, 112)[
        0] == zlib.crc32(trailer[:112])
    assert trailer[128:] == bytes([0xff]) * (0x1000 - 128)
    journal = make_initial_firmware.make_esp32p4_journal('boot_a', 7, True)
    assert len(journal) == 0x1000
    assert struct.unpack_from('<I', journal, 8)[0] == 7
    assert struct.unpack_from('<I', journal, 12)[0] == 0x434F4E46
    assert struct.unpack_from('<I', journal, 32)[0] == zlib.crc32(journal[:32])
    assert journal[64:] == bytes([0xff]) * (0x1000 - 64)


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


def test_unconfirmed_journal_is_physically_erased():
    journal = make_initial_firmware.make_esp32p4_journal('boot_b', 2)
    assert journal == bytes([0xff]) * 0x1000
    with pytest.raises(click.ClickException):
        make_initial_firmware.make_esp32p4_journal('main', 2)


def test_confirmed_cli_requires_separate_journal(tmp_path):
    image = tmp_path / 'boot.bin'
    image.write_bytes(minimal_esp32p4_app_image())
    trailer = tmp_path / 'boot.trailer'
    journal = tmp_path / 'boot.journal'
    args = ['--esp32-app', str(image), '--esp32-platform',
            'esp32-p4-wifi6-touch-lcd-4p3', '--esp32-role', 'boot_b',
            '--confirmed', str(trailer)]
    result = CliRunner().invoke(make_initial_firmware.combine, args)
    assert result.exit_code != 0
    assert '--journal-output' in result.output
    assert not trailer.exists()
    result = CliRunner().invoke(make_initial_firmware.combine,
                               args + ['--journal-output', str(journal)])
    assert result.exit_code == 0, result.output
    assert trailer.read_bytes()[128:] == bytes([0xff]) * (0x1000 - 128)
    assert journal.read_bytes() == make_initial_firmware.make_esp32p4_journal(
        'boot_b', 1, True)


@pytest.mark.parametrize('alias_kind', ['same_path', 'symlink', 'hardlink'])
@pytest.mark.parametrize('collision', ['journal_input', 'trailer_input', 'outputs'])
def test_cli_rejects_aliases_without_changing_files(tmp_path, alias_kind, collision):
    image = tmp_path / 'boot.bin'
    trailer = tmp_path / 'boot.trailer'
    journal = tmp_path / 'boot.journal'
    original = minimal_esp32p4_app_image()
    image.write_bytes(original)
    trailer.write_bytes(b'previous trailer')
    journal.write_bytes(b'previous journal')
    target = trailer if collision == 'outputs' else image
    alias = tmp_path / 'alias'
    if alias_kind == 'same_path':
        alias = target
    elif alias_kind == 'symlink':
        alias.symlink_to(target)
    else:
        os.link(target, alias)
    trailer_output = alias if collision == 'trailer_input' else trailer
    journal_output = alias if collision != 'trailer_input' else journal
    result = CliRunner().invoke(make_initial_firmware.combine, [
        '--esp32-app', str(image), '--esp32-platform',
        'esp32-p4-wifi6-touch-lcd-4p3', '--esp32-role', 'boot_a',
        '--confirmed', '--journal-output', str(journal_output),
        str(trailer_output),
    ])
    assert result.exit_code != 0
    assert 'must differ' in result.output
    assert image.read_bytes() == original
    assert trailer.read_bytes() == b'previous trailer'
    assert journal.read_bytes() == b'previous journal'
