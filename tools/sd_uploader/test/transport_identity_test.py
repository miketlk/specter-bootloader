"""Stable USB identity and custom-baud transport regression coverage."""
import pytest

pytest.importorskip('serial', reason='optional SD uploader dependency')
from sd_uploader.transport import SerialTransport


def test_custom_baud_not_reconfigured_by_each_read_or_write():
    class Port:
        in_waiting = 1
        @property
        def timeout(self): return .1
        @timeout.setter
        def timeout(self, _): pytest.fail('timeout setter reconfigures custom UART rate')
        @property
        def write_timeout(self): return 5
        @write_timeout.setter
        def write_timeout(self, _): pytest.fail('write_timeout setter reconfigures custom UART rate')
        def read(self, _): return b'x'
        def write(self, packet): return len(packet)
        def flush(self): pass
    transport = object.__new__(SerialTransport)
    transport.serial, transport.evidence = Port(), None
    transport.write(b'packet')
    assert transport.read() == b'x'


@pytest.mark.parametrize('count', [0, 2])
def test_missing_or_ambiguous_bridge_never_selects_first(monkeypatch, count):
    from types import SimpleNamespace
    from sd_uploader import transport
    from sd_uploader.runtime import Failure
    device = SimpleNamespace(vid=0x1a86, pid=0x55d3, device='new-port', serial_number='serial', location='location')
    monkeypatch.setattr(transport, 'comports', lambda:[device]*count)
    with pytest.raises(Failure) as caught:
        transport.identify(serial_number='serial', location='location')
    assert caught.value.code == 'DEVICE_SELECTION' and caught.value.exit_code == 3


def test_port_change_uses_stable_identity(monkeypatch):
    from types import SimpleNamespace
    from sd_uploader import transport
    device = SimpleNamespace(vid=0x1a86, pid=0x55d3, device='new-port', serial_number='serial', location='location')
    monkeypatch.setattr(transport, 'comports', lambda:[device])
    assert transport.identify(serial_number='serial', location='location')['port'] == 'new-port'
