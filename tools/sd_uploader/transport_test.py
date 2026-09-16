"""Short acknowledgements must not wait for a full receive buffer."""
import pytest

pytest.importorskip('serial', reason='optional SD uploader dependency')
from sd_uploader.transport import SerialTransport


def test_reads_only_available_bytes():
    class Port:
        in_waiting=3
        def read(self, size):
            assert size==3
            return b'ack'
    transport=object.__new__(SerialTransport)
    transport.serial=Port()
    transport.evidence=None
    assert transport.read()==b'ack'


def test_empty_port_waits_for_one_byte():
    class Port:
        in_waiting=0
        def read(self, size):
            assert size==1
            return b''
    transport=object.__new__(SerialTransport)
    transport.serial=Port()
    transport.evidence=None
    assert transport.read()==b''
