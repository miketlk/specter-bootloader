"""The ROM subprocess adapter cannot turn into a flash programming wrapper."""
import pytest

pytest.importorskip('elftools', reason='optional SD uploader dependency')
pytest.importorskip('serial', reason='optional SD uploader dependency')
from sd_uploader.ram_loader import RamLoader


@pytest.mark.parametrize('operation',['write_flash','erase_flash','erase_region','burn_efuse','load_ram'])
def test_subprocess_allowlist(operation):
    loader=object.__new__(RamLoader)
    with pytest.raises(ValueError,match='read-only allow-list'):
        loader._run(['-m','esptool',operation],timeout=1)
