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


@pytest.mark.parametrize('interruption', ['cancel', 'partial', None])
def test_probe_output_is_published_only_after_parsing(tmp_path, interruption):
    import json
    from pathlib import Path
    from sd_uploader.evidence import Evidence
    loader = object.__new__(RamLoader)
    loader.evidence = Evidence(tmp_path)
    loader.endpoint = dict(port='test')
    target = tmp_path/'nor-before.json'
    old = dict(chip='old', full_nor='old')
    target.write_text(json.dumps(old))
    current = dict(chip='chip', full_nor='digest')
    def run(args, timeout):
        output = Path(args[args.index('--output') + 1])
        output.write_text('{"chip":' if interruption else json.dumps(current))
        if interruption == 'cancel':
            raise KeyboardInterrupt()
    loader._run = run
    if interruption:
        with pytest.raises(KeyboardInterrupt if interruption == 'cancel' else ValueError):
            loader.inspect('nor-before.json')
        assert json.loads(target.read_text()) == old
    else:
        assert loader.inspect('nor-before.json') == current
        assert json.loads(target.read_text()) == current
