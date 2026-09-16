"""Fail-closed ELF/image audit against the pinned P4 revision-1 linker ranges."""
import argparse
import hashlib
import json
import re
import struct
import subprocess
from pathlib import Path
from elftools.elf.elffile import ELFFile

ROOT = Path(__file__).resolve().parents[2]
IDF = ROOT / 'third_party/esp-idf'
APP = ROOT / 'platforms/esp32-p4-wifi6-touch-lcd/sd_uploader'
RESERVE = 65536


def sha(path):
    with Path(path).open('rb') as data:
        return hashlib.file_digest(data, 'sha256').hexdigest()


def config(path):
    return dict(line.split('=', 1) for line in Path(path).read_text().splitlines()
                if line.startswith('CONFIG_') and '=' in line)


def regions(settings):
    """Extract boundaries from pinned sources; never use chip-agnostic ranges."""
    if settings.get('CONFIG_ESP32P4_SELECTS_REV_LESS_V3') != 'y':
        raise ValueError('only revision 1.x profile implemented')
    if settings.get('CONFIG_ESP32P4_REV_MIN_FULL') != '100':
        raise ValueError('revision minimum must be 1.0')
    source = (IDF / 'components/esp_system/ld/esp32p4/memory.ld.in').read_text()
    def constant(name):
        match = re.search(r'^#define\s+' + name + r'\s+(0x[0-9a-fA-F]+)\b', source, re.M)
        if not match:
            raise ValueError(f'pinned linker constant unavailable: {name}')
        return int(match[1], 16)
    cache = int(settings['CONFIG_CACHE_L2_CACHE_SIZE'], 0)
    if cache != 0x20000:
        raise ValueError('only 128 KiB cache reservation qualified')
    high_size = re.search(r'^#define SRAM_HIGH_SIZE\s+(0x[0-9a-fA-F]+) - CONFIG_CACHE_L2_CACHE_SIZE', source, re.M)
    if not high_size:
        raise ValueError('pinned high SRAM expression changed')
    return [(constant('SRAM_LOW_START'), constant('SRAM_LOW_END')),
            (constant('SRAM_HIGH_START'), constant('SRAM_HIGH_START') + int(high_size[1], 16) - cache)]


def check_ranges(spans, allowed):
    ordered = sorted(spans)
    previous = 0
    for address, size in ordered:
        if size <= 0 or address < previous or address > 0xffffffff or size > 0x100000000-address:
            raise ValueError('segment overlap/overflow/empty')
        end = address + size
        if not any(low <= address < end <= high for low, high in allowed):
            raise ValueError(f'outside internal RAM allow-list: {address:#x}+{size:#x}')
        previous = end


def audit(elf_path, image_path, sdkconfig, board):
    if board not in ('lcd-4p3', 'lcd-5'):
        raise ValueError('unsupported board')
    settings = config(sdkconfig)
    for key in ('APP_BUILD_TYPE_RAM', 'APP_BUILD_TYPE_PURE_RAM_APP', 'ESP_SYSTEM_PANIC_PRINT_HALT'):
        if settings.get('CONFIG_' + key) != 'y':
            raise ValueError(f'{key} required')
    forbidden_config = ('APP_BUILD_BOOTLOADER', 'APP_BUILD_USE_FLASH_SECTIONS', 'SPIRAM',
                        'SECURE_BOOT', 'SECURE_FLASH_ENC_ENABLED', 'ESP_SYSTEM_ALLOW_RTC_FAST_MEM_AS_HEAP',
                        'ESP_COREDUMP_ENABLE_TO_FLASH', 'BOOTLOADER_EFUSE_SECURE_VERSION_EMULATE',
                        'ESP32P4_REV_MIN_300', 'ESP32P4_REV_MIN_301', 'ESP_SYSTEM_PMP_IDRAM_SPLIT')
    for key in forbidden_config:
        if settings.get('CONFIG_' + key) == 'y':
            raise ValueError(f'forbidden config {key}')
    expected_board = 'CONFIG_SDU_BOARD_4P3' if board == 'lcd-4p3' else 'CONFIG_SDU_BOARD_5'
    if settings.get(expected_board) != 'y':
        raise ValueError('board config mismatch')
    allowed = regions(settings)
    with Path(elf_path).open('rb') as stream:
        elf = ELFFile(stream)
        if elf.elfclass != 32 or not elf.little_endian or elf['e_machine'] != 'EM_RISCV':
            raise ValueError('expected ELF32 little-endian RISC-V')
        loads = [s for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD' and s['p_memsz']]
        spans = [(s['p_paddr'], s['p_memsz']) for s in loads]
        check_ranges(spans, allowed)
        for s in loads:
            if s['p_paddr'] != s['p_vaddr'] or s['p_filesz'] > s['p_memsz']:
                raise ValueError('ELF alias/filesize mismatch')
        sections = [s for s in elf.iter_sections() if s['sh_flags'] & 2 and s['sh_size']]
        check_ranges([(s['sh_addr'], s['sh_size']) for s in sections], allowed)
        for section in sections:
            if not any(s['p_vaddr'] <= section['sh_addr'] and
                       section['sh_addr'] + section['sh_size'] <= s['p_vaddr'] + s['p_memsz'] for s in loads):
                raise ValueError('allocated section outside PT_LOAD')
        entry = elf['e_entry']
        if not any(s['p_flags'] & 1 and s['p_vaddr'] <= entry < s['p_vaddr'] + s['p_filesz'] for s in loads):
            raise ValueError('entry point is not initialized executable RAM')
        symbols = elf.get_section_by_name('.symtab')
        forbidden = re.compile(r'^(esp_flash_(write|erase|init)|spi_flash_(write|erase)|esp_partition_(write|erase)|'
                               r'esp_ota_|nvs_flash_|wl_(mount|write|erase)|esp_efuse_(write|burn|batch_write)|'
                               r'esp_secure_boot_enable|esp_flash_encrypt|esp_core_dump_to_flash)')
        rejected = [s.name for s in symbols.iter_symbols() if s['st_shndx'] != 'SHN_UNDEF' and
                    s['st_info']['type'] == 'STT_FUNC' and forbidden.search(s.name)]
        if rejected:
            raise ValueError(f'forbidden flash/eFuse functions: {rejected}')
        syms = {s.name: s['st_value'] for s in symbols.iter_symbols()}
        for required in ('cache_hal_init', '__esp_system_init_fn_sdu_reserve'):
            if not syms.get(required):
                raise ValueError('required pure-RAM cache/heap initialization is missing')
        for section in sections:
            if (section['sh_type'] != 'SHT_NOBITS' and
                    section['sh_addr'] >= allowed[1][0] and
                    section['sh_addr'] + section['sh_size'] > syms['_bss_start_high']):
                raise ValueError('initialized high SRAM must precede expandable BSS')
        reserves = []
        for suffix, (low, high) in zip(('low', 'high'), allowed):
            heap = syms['_heap_start_' + suffix]
            if not low <= heap <= high:
                raise ValueError('heap marker outside RAM')
            reserves.append(high - heap)
        if sum(reserves) < RESERVE:
            raise ValueError('insufficient internal runtime reserve')
        data = Path(image_path).read_bytes()
        if len(data) < 24 or data[0] != 0xe9 or data[1] > 16 or data[23] != 1:
            raise ValueError('invalid image header/hash mode')
        if struct.unpack_from('<H', data, 12)[0] != 18:
            raise ValueError('image is not ESP32-P4')
        if struct.unpack_from('<I', data, 4)[0] != entry:
            raise ValueError('image/ELF entry mismatch')
        min_rev, max_rev = struct.unpack_from('<HH', data, 15)
        if (min_rev, max_rev) != (100, 199):
            raise ValueError('image revision mismatch')
        pos = 24
        image_spans = []
        checksum = 0xef
        for _ in range(data[1]):
            if pos + 8 > len(data):
                raise ValueError('truncated image')
            address, length = struct.unpack_from('<II', data, pos)
            pos += 8
            if not length or length > len(data)-pos:
                raise ValueError('invalid image segment size')
            content = data[pos:pos + length]
            image_spans.append((address, length))
            matching = [s for s in loads if s['p_vaddr'] <= address and address + length <= s['p_vaddr'] + s['p_filesz']]
            if not matching:
                raise ValueError('image segment outside initialized ELF data')
            segment = matching[0]
            offset = address - segment['p_vaddr']
            if segment.data()[offset:offset+length] != content:
                raise ValueError('ELF/image bytes differ')
            for byte in content:
                checksum ^= byte
            pos += length
        check_ranges(image_spans, allowed)
        # Every nonempty initialized allocated section must occur in the image.
        for section in sections:
            if section['sh_type'] != 'SHT_NOBITS' and not any(
                    a <= section['sh_addr'] and section['sh_addr'] + section['sh_size'] <= a+n for a,n in image_spans):
                raise ValueError(f'initialized section missing from image: {section.name}')
        checksum_pos = pos | 15
        if checksum_pos + 33 != len(data) or data[checksum_pos] != checksum:
            raise ValueError('image checksum/length')
        if any(data[pos:checksum_pos]) or hashlib.sha256(data[:checksum_pos+1]).digest() != data[-32:]:
            raise ValueError('image padding/digest')
    identity = subprocess.check_output(['git', '-C', str(IDF), 'rev-parse', 'HEAD'], text=True).strip()
    pinned = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD:third_party/esp-idf'], text=True).strip()
    if identity != pinned:
        raise ValueError('IDF checkout differs from repository pin')
    inputs = [p for p in sorted((APP/'main').glob('*')) if p.suffix in ('.c', '.h')]
    inputs += [APP.parent/'lcd-4p3/board_config.h', APP.parent/'lcd-5/board_config.h', Path(sdkconfig)]
    source_hashes = ''.join(sha(p) for p in inputs)
    build_id = hashlib.sha256(source_hashes.encode()).hexdigest()
    if build_id.encode() not in Path(elf_path).read_bytes():
        raise ValueError('ELF build identity differs from current sources; reconfigure and rebuild')
    baud = int(settings.get('CONFIG_SDU_UART_BAUD', '0'))
    if baud not in (115200, 460800, 921600, 2000000, 3000000, 4000000):
        raise ValueError('unsupported UART baud profile')
    return dict(schema_version=1, board=board, transport='uart', protocol=1, uart_baud=baud,
                build=build_id,
                elf=str(Path(elf_path).resolve()), image=str(Path(image_path).resolve()),
                sdkconfig=str(Path(sdkconfig).resolve()), elf_sha256=sha(elf_path),
                image_sha256=sha(image_path), sdkconfig_sha256=sha(sdkconfig),
                idf_commit=identity, dependencies_sha256=sha(APP/'dependencies.lock'),
                revision_min=min_rev, revision_max=max_rev, entry=entry,
                allowed_regions=allowed, elf_segments=spans, image_segments=image_spans,
                runtime_reserve=reserves, required_reserve=RESERVE)


def verify_manifest(path):
    expected = json.loads(Path(path).read_text())
    actual = audit(expected['elf'], expected['image'], expected['sdkconfig'], expected['board'])
    # JSON-normalize tuple arrays before comparison.
    if json.loads(json.dumps(actual)) != expected:
        raise ValueError('RAM manifest does not match current audited artifacts')
    return actual


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--elf', required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--sdkconfig', required=True)
    parser.add_argument('--board', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = audit(args.elf, args.image, args.sdkconfig, args.board)
    Path(args.output).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
