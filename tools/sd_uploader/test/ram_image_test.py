"""Reject address manipulation before any ROM operation."""
import pytest

pytest.importorskip('elftools', reason='optional SD uploader dependency')
from sd_uploader.ram_image import check_ranges, regions


def test_derived_regions():
    ranges = regions({'CONFIG_ESP32P4_SELECTS_REV_LESS_V3':'y',
                      'CONFIG_ESP32P4_REV_MIN_FULL':'100','CONFIG_CACHE_L2_CACHE_SIZE':'0x20000'})
    assert len(ranges)==2 and ranges[0][1] < ranges[1][0]
    check_ranges([(low,high-low) for low,high in ranges],ranges)
    with pytest.raises(ValueError):
        check_ranges([(ranges[0][1]-1,2)],ranges)


@pytest.mark.parametrize('spans',[
    [(0xfffffff0,32)], [(0x40000000,4)], [(0x48000000,4)],
    [(0x50108000,4)], [(0x30100000,4)], [(0x4ff30000,4)],
    [(0x4ff00000,16),(0x4ff00008,16)], [(0x4ff00000,0)],
])
def test_bad_ranges(spans):
    with pytest.raises(ValueError):
        check_ranges(spans,[(0x4ff00000,0x4ff2cbd0),(0x4ff40000,0x4ffa0000)])

from pathlib import Path
import struct
from elftools.elf.elffile import ELFFile
from sd_uploader.ram_image import audit, ROOT


@pytest.fixture(scope='module')
def built():
    directory=ROOT/'build/esp32-p4-wifi6-touch-lcd/sd-uploader/lcd-4p3/uart'
    if not (directory/'ram-manifest.json').exists():
        pytest.skip('build RAM uploader to run linked artifact corruption tests')
    return directory


@pytest.mark.parametrize('mutation', ['image-chip','image-entry','image-address','image-length',
    'image-hash','elf-address','elf-entry','elf-bss-overflow','config-flash','config-panic-reset',
    'missing-cache-init'])
def test_corrupt_linked_artifacts(built,tmp_path,mutation):
    elf=bytearray((built/'sd-uploader.elf').read_bytes())
    image=bytearray((built/'sd-uploader.bin').read_bytes())
    settings=(built/'sdkconfig').read_text()
    if mutation=='image-chip': struct.pack_into('<H',image,12,0)
    if mutation=='image-entry': struct.pack_into('<I',image,4,0x40000020)
    if mutation=='image-address': struct.pack_into('<I',image,24,0x48000000)
    if mutation=='image-length': struct.pack_into('<I',image,28,0xffffffff)
    if mutation=='image-hash': image[-1]^=1
    if mutation=='elf-entry': struct.pack_into('<I',elf,24,0x40000020)
    if mutation=='missing-cache-init': elf=elf.replace(b'cache_hal_init\0',b'cache_hal_noop\0')
    with (built/'sd-uploader.elf').open('rb') as stream:
        parsed=ELFFile(stream)
        load_index=next(i for i,s in enumerate(parsed.iter_segments()) if s['p_type']=='PT_LOAD' and s['p_memsz'])
        offset=parsed['e_phoff']+load_index*parsed['e_phentsize']
    if mutation=='elf-address': struct.pack_into('<II',elf,offset+8,0x48000000,0x48000000)
    if mutation=='elf-bss-overflow': struct.pack_into('<I',elf,offset+20,0xffffffff)
    if mutation=='config-flash': settings+='\nCONFIG_APP_BUILD_USE_FLASH_SECTIONS=y\n'
    if mutation=='config-panic-reset': settings=settings.replace('CONFIG_ESP_SYSTEM_PANIC_PRINT_HALT=y','CONFIG_ESP_SYSTEM_PANIC_PRINT_REBOOT=y')
    (tmp_path/'app.elf').write_bytes(elf)
    (tmp_path/'app.bin').write_bytes(image)
    (tmp_path/'sdkconfig').write_text(settings)
    with pytest.raises(ValueError):
        audit(tmp_path/'app.elf',tmp_path/'app.bin',tmp_path/'sdkconfig','lcd-4p3')
