"""Exclusive serial ownership with deliberate inactive line levels."""
import fcntl
import hashlib
import os
import tempfile
import sys
import termios
from pathlib import Path
import serial
from serial.tools.list_ports import comports


def identify(port=None, serial_number=None, location=None):
    matches = [p for p in comports() if
               (port is None or p.device == port) and
               (serial_number is None or p.serial_number == serial_number) and
               (location is None or p.location == location) and
               p.vid == 0x1a86 and p.pid == 0x55d3]
    if len(matches) != 1:
        raise ValueError('expected exactly one configured CH343 USB-to-UART endpoint')
    endpoint = matches[0]
    return dict(port=endpoint.device, serial=endpoint.serial_number,
                location=endpoint.location, vid=endpoint.vid, pid=endpoint.pid)


class FixtureLock:
    """Held across serial/esptool handoffs, including process failure recovery."""
    def __init__(self, identity):
        key = hashlib.sha256(identity.encode()).hexdigest()
        self.path = Path(tempfile.gettempdir()) / f'specter-sdu-{key}.lock'
        self.fd = None

    def __enter__(self):
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise
        return self

    def __exit__(self, *_):
        os.close(self.fd)
        self.fd = None


class SerialTransport:
    def __init__(self, port, evidence=None, baudrate=115200):
        self.evidence = evidence
        self.serial = serial.Serial(port=None, baudrate=115200, timeout=0.1,
                                    write_timeout=5, exclusive=True)
        self.serial.dtr = sys.platform == "darwin"
        self.serial.rts = sys.platform == "darwin"
        self.serial.port = port
        self.serial.open()
        attributes = termios.tcgetattr(self.serial.fileno())
        attributes[2] &= ~termios.HUPCL
        termios.tcsetattr(self.serial.fileno(), termios.TCSANOW, attributes)
        # macOS rejects tcsetattr after pyserial selects a custom UART rate.
        # Configure line flags at a standard rate before applying that rate.
        self.serial.baudrate = baudrate

    def write(self, packet):
        if self.serial.write(packet) != len(packet):
            raise OSError('short serial write')
        self.serial.flush()

    def read(self):
        # Wait only for the first byte, then drain what is already available.
        # read(4096) made every small ACK pay the complete 100 ms timeout.
        data = self.serial.read(max(1, min(self.serial.in_waiting, 32768)))
        if self.evidence and data:
            self.evidence.capture(data)
        return data

    def close(self):
        self.serial.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
