"""Durable host journal and raw capture."""
import json
import os
import time
from pathlib import Path


def json_value(value):
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(type(value).__name__)


class Evidence:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def record(self, action, **values):
        record = dict(time_ns=time.time_ns(), action=action, **values)
        with (self.directory / 'journal.jsonl').open('a') as output:
            output.write(json.dumps(record, default=json_value, sort_keys=True) + '\n')
            output.flush()
            os.fsync(output.fileno())

    def capture(self, data):
        with (self.directory / 'serial.bin').open('ab') as output:
            output.write(data)

    def save(self, name, value):
        target = self.directory / name
        temp = target.with_suffix('.tmp')
        with temp.open('w') as output:
            json.dump(value, output, indent=2, default=json_value, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        temp.replace(target)
        directory_fd = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
