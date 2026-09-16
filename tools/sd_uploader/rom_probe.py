"""Pinned esptool read-only ROM backend; intentionally no flash programming API."""
import argparse
import json
import re
from pathlib import Path
import esptool
from esptool.targets.esp32p4 import ESP32P4ROM


def inspect(port, output, reset=False, hashes=False):
    if esptool.__version__ != '4.12.0':
        raise ValueError('ROM backend requires pinned esptool 4.12.0')
    esp = ESP32P4ROM(port, baud=115200)
    try:
        esp.connect(mode='default_reset' if reset else 'no_reset', attempts=3)
        if esp.IS_STUB:
            raise ValueError('ROM required; resident stub rejected')
        info = esp.get_security_info()
        if info['flags'] != 0 or info['flash_crypt_cnt'] != 0:
            raise ValueError('only unrestricted plaintext development devices supported')
        esp.flash_spi_attach(0)
        jedec = esp.flash_id()
        capacity_code = jedec >> 16 & 255
        if not 20 <= capacity_code <= 25:
            raise ValueError('unsupported NOR capacity')
        capacity = 1 << capacity_code
        esp.flash_set_parameters(capacity)
        mac = ':'.join(f'{b:02x}' for b in esp.read_mac())
        result = dict(chip=mac, revision=esp.get_chip_revision(), security=info,
                      flash_id=jedec, flash_size=capacity, algorithm='ROM-MD5')
        if hashes:
            # Full detected capacity, not just populated partitions. ROM MD5 is
            # used for accidental-change detection, not release authentication.
            result['full_nor'] = esp.flash_md5sum(0, capacity)
            layout = Path(__file__).resolve().parents[2] / 'platforms/esp32-p4-wifi6-touch-lcd/partition_layout.cmake'
            constants = {k: int(v,16) for k,v in re.findall(r'set\((SPECTER_\w+_SIZE) (0x[0-9a-fA-F]+)\)', layout.read_text())}
            sizes = [('root', constants['SPECTER_ROOT_LOADER_PARTITION_SIZE']),
                     ('boot_a', constants['SPECTER_BOOTLOADER_PARTITION_SIZE']),
                     ('boot_b', constants['SPECTER_BOOTLOADER_PARTITION_SIZE']),
                     ('main', constants['SPECTER_MAIN_PARTITION_SIZE']),
                     ('main_aux', constants['SPECTER_MAIN_AUX_PARTITION_SIZE']),
                     ('journal', constants['SPECTER_JOURNAL_PARTITION_SIZE'])]
            offset = 0
            result['regions'] = {}
            for label, size in sizes:
                if size:
                    if offset + size > capacity:
                        raise ValueError('canonical layout exceeds detected capacity')
                    result['regions'][label] = dict(offset=offset, size=size,
                                                   md5=esp.flash_md5sum(offset,size))
                offset += size
            if offset < capacity:
                result['regions']['remaining_nor'] = dict(offset=offset, size=capacity-offset,
                    md5=esp.flash_md5sum(offset,capacity-offset))
        Path(output).write_text(json.dumps(result, indent=2) + '\n')
    finally:
        esp._port.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--reset', action='store_true')
    parser.add_argument('--hashes', action='store_true')
    args = parser.parse_args()
    inspect(args.port, args.output, args.reset, args.hashes)


if __name__ == '__main__':
    main()
