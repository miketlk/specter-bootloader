#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Script making firmware for one-step initial programming"""

from intelhex import IntelHex
import click
from pathlib import Path
import struct
import zlib
from core.integritychk import *
from core.memmap import *
from core.blsection import MAX_PAYLOAD_SIZE, find_payload_version
from core.espidf import EspIdfImageError, validate_esp32p4_app_image
__author__ = "Mike Tolkachev <contact@miketolkachev.dev>"
__copyright__ = "Copyright 2020 Crypto Advance GmbH. All rights reserved"
__version__ = "1.0.0"

# Types representing sequence of bytes, for type checking
_byteslike = (bytes, bytearray)


@click.group()
@click.version_option(__version__, message="%(version)s")
def cli():
    """Makes firmware for one-step initial programming."""


@cli.command()
@click.option(
    '-s', '--startup', 'startup_hex',
    required=False,
    type=click.File('r'),
    help='Intel HEX file containing the Start-up code.',
    metavar='<file.hex>'
)
@click.option(
    '-b', '--bootloader', 'bootloader_hex',
    required=False,
    type=click.File('r'),
    help='Intel HEX file containing the Bootloader.',
    metavar='<file.hex>'
)
@click.option(
    '-f', '--firmware', 'firmware_hex',
    type=click.File('r'),
    help='Intel HEX file containing the Main Firmware.',
    metavar='<file.hex>'
)
@click.option(
    '--esp32-app', 'esp32_app', type=click.File('rb'),
    help='Canonical hash-appended ESP-IDF application image.',
    metavar='<file.bin>'
)
@click.option('--esp32-platform', type=str, metavar='<platform>')
@click.option(
    '--esp32-role', type=click.Choice(['boot_a', 'boot_b', 'main']))
@click.option('--sequence', type=click.IntRange(min=1, max=0xffffffff),
              default=1, show_default=True)
@click.option('--confirmed', is_flag=True,
              help='Write a confirmed record to --journal-output for a boot role.')
@click.option('--journal-output', type=click.Path(dir_okay=False),
              help='Separate plaintext 4 KiB journal sector for --esp32-role.')
@click.option(
    '-bin', '--bin-output', 'bin_out',
    required=False,
    is_flag=True,
    default=False,
    help='Outputs firmware in raw binary format.'
)
@click.argument(
    'out_file',
    required=True,
    type=click.STRING,
    metavar='<output_file_name>'
)
def combine(out_file, startup_hex, bootloader_hex, firmware_hex, esp32_app,
            esp32_platform, esp32_role, sequence, confirmed, journal_output,
            bin_out):
    """This command makes a firmare file for initial programming of a "clean"
    defice. The firmware file is made by combining together the Start-up code,
    the Bootloader, and, optionally, the Main Firmware.
    """

    if esp32_app:
        if startup_hex or bootloader_hex or firmware_hex or bin_out:
            raise click.ClickException(
                "ESP32 trailer mode cannot be combined with STM32 inputs")
        if not esp32_platform or not esp32_role:
            raise click.ClickException(
                "ESP32 trailer mode requires --esp32-platform and --esp32-role")
        if confirmed and not journal_output:
            raise click.ClickException("--confirmed requires --journal-output")
        outputs = [out_file] + ([journal_output] if journal_output else [])
        if any(paths_alias(output, esp32_app.name) for output in outputs):
            raise click.ClickException("Outputs must differ from the application input")
        if journal_output and paths_alias(journal_output, out_file):
            raise click.ClickException("Journal and trailer outputs must differ")
        journal = (make_esp32p4_journal(esp32_role, sequence, confirmed)
                   if journal_output else None)
        trailer = make_esp32p4_trailer(
            esp32_app.read(), esp32_platform, esp32_role, sequence)
        with open(out_file, "wb") as file_obj:
            file_obj.write(trailer)
        if journal is not None:
            Path(journal_output).write_bytes(journal)
        return

    if journal_output or confirmed:
        raise click.ClickException("Journal options require --esp32-app")
    if not startup_hex or not bootloader_hex:
        raise click.ClickException(
            "STM32 mode requires --startup and --bootloader")

    # Create initial firmware: begin with a HEX file of the Start-up code
    out_ih = IntelHex(startup_hex)

    # Read and process a HEX file of the Bootloader
    bootloader_ih = IntelHex(bootloader_hex)
    memmap = get_memmap(intelhex_to_bytes(bootloader_ih))
    intelhex_add_icr(bootloader_ih, memmap['bootloader_size'])
    out_ih.merge(bootloader_ih, overlap='ignore')

    # Read and process a HEX file of the Main Firmware if specified
    if firmware_hex:
        main_ih = IntelHex(firmware_hex)
        if main_ih.minaddr() != memmap['main_firmware_start']:
            raise click.ClickException(
                "Main Firmware is incomatible with the Bootloader")
        intelhex_add_icr(main_ih, memmap['main_firmware_size'])
        out_ih.merge(main_ih, overlap='ignore')

    # Write resulting firmware in HEX or binary format
    if bin_out:
        file_obj = open(out_file, "wb")
        file_obj.write(intelhex_to_bytes(out_ih))
        file_obj.close()
    else:
        out_ih.write_hex_file(out_file)


def paths_alias(first, second):
    """Recognize identical paths, symlinks, and existing hard links."""
    first, second = Path(first), Path(second)
    try:
        return first.resolve() == second.resolve() or first.samefile(second)
    except FileNotFoundError:
        return False
    except OSError as error:
        raise click.ClickException(f"Cannot validate output paths: {error}") from error


def intelhex_to_bytes(ih_obj):
    """Converts IntelHex object to raw bytes with size checking."""

    if not isinstance(ih_obj, IntelHex):
        raise TypeError("Storage object should be IntelHex")
    data_len = ih_obj.maxaddr() - ih_obj.minaddr() + 1
    if data_len > MAX_PAYLOAD_SIZE:
        raise click.ClickException(f"Error while parsing '{hex_file.name}'")
    return ih_obj.tobinstr()


def intelhex_add_data(ih_obj, addr, data):
    """ Writes bytes-like data to IntelHex object at given address."""

    if not isinstance(ih_obj, IntelHex):
        raise TypeError("Storage object should be IntelHex")
    if not isinstance(data, _byteslike):
        raise TypeError("Data should be bytes-like")
    curr_addr = addr
    for byte in data:
        ih_obj[curr_addr] = byte
        curr_addr += 1
    pass


def intelhex_add_icr(ih_obj, storage_size):
    """Adds an integrity check record to to IntelHex object at address
    calculated using provided storage size.
    """

    if not isinstance(ih_obj, IntelHex):
        raise TypeError("Storage object should be IntelHex")

    data_len = ih_obj.maxaddr() - ih_obj.minaddr() + 1
    if (data_len > MAX_PAYLOAD_SIZE or
            data_len + BL_FW_SECT_OVERHEAD > storage_size):
        raise click.ClickException(f"Error while parsing '{hex_file.name}'")

    icr = icr_create(intelhex_to_bytes(ih_obj))
    addr = ih_obj.minaddr() + storage_size - BL_ICR_OFFSET_FROM_END
    intelhex_add_data(ih_obj, addr, icr)


def make_esp32p4_trailer(image, platform, role, sequence):
    """Builds a fixed-role approval trailer for initial USB provisioning."""
    try:
        digest = validate_esp32p4_app_image(image)
    except EspIdfImageError as error:
        raise click.ClickException(
            f"ESP32 application image is not canonical: {error}") from error
    role_ids = {'boot_a': 1, 'boot_b': 2, 'main': 3}
    if role not in role_ids or not isinstance(platform, str):
        raise click.ClickException("Invalid ESP32 platform or role")
    platform_bytes = platform.encode('ascii')
    if len(platform_bytes) >= 40:
        raise click.ClickException("ESP32 platform identifier is too long")
    version = find_payload_version(image)
    if not version:
        raise click.ClickException("ESP32 application has no version tag")

    approval_prefix = struct.pack(
        '<8sII40sIIIII32sI', b'SPAPRV2', 2, 128,
        platform_bytes, role_ids[role], version, len(image), sequence,
        0x41505052, digest, 0xffffffff)
    approval = (approval_prefix + struct.pack('<I', zlib.crc32(approval_prefix)) +
                bytes([0xff]) * 12)
    trailer = bytearray([0xff]) * 0x1000
    trailer[:len(approval)] = approval

    return bytes(trailer)


def make_esp32p4_journal(role, sequence, confirmed=False):
    """Build one plaintext sector: boot_a at partition +0, boot_b at +0x1000."""
    if role not in ('boot_a', 'boot_b'):
        raise click.ClickException("Only Bootloader roles have journals")
    if not 1 <= sequence <= 0xffffffff:
        raise click.ClickException("Invalid approval sequence")
    sector = bytearray([0xff]) * 0x1000
    if confirmed:
        journal_prefix = struct.pack(
            '<IIII16s', 0x4A525053, 2, sequence, 0x434F4E46,
            bytes([0xff]) * 16)
        journal = (journal_prefix +
                   struct.pack('<I', zlib.crc32(journal_prefix)) +
                   bytes([0xff]) * 28)
        sector[:len(journal)] = journal
    return bytes(sector)


if __name__ == '__main__':
    combine()
