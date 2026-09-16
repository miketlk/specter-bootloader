"""Bounded ROM subprocesses and continuous-descriptor, no-stub RAM execution."""
import json
import io
from contextlib import redirect_stdout, redirect_stderr
import os
import subprocess
import sys
import time
from pathlib import Path
from .ram_image import verify_manifest, ROOT
from .transport import SerialTransport


class RamLoader:
    def __init__(self, python, endpoint, evidence):
        self.python = str(Path(python).absolute())
        self.endpoint = endpoint
        self.evidence = evidence
        version = subprocess.run([self.python, '-m', 'esptool', 'version'],
                                 check=True, capture_output=True, text=True, timeout=10).stdout
        if version.strip().splitlines()[-1] != '4.12.0':
            raise ValueError('only pinned esptool 4.12.0 qualified')
        help_text = subprocess.run([self.python, '-m', 'esptool', '--help'],
                                   check=True, capture_output=True, text=True, timeout=10).stdout
        self.load_command = 'load_ram' if 'load_ram' in help_text else 'load-ram'
        if self.load_command not in help_text:
            raise ValueError('installed esptool has no RAM loading command')

    def _run(self, args, timeout):
        if args[:2] != ['-m', 'sd_uploader.rom_probe']:
            raise ValueError('ROM subprocess operation is outside the read-only allow-list')
        self.evidence.record('rom-command', args=args)
        env = dict(os.environ, PYTHONPATH=str(ROOT / 'tools'))
        result = subprocess.run([self.python, *args], capture_output=True, timeout=timeout, env=env)
        self.evidence.record('rom-result', returncode=result.returncode,
                             stdout=result.stdout.decode(errors='replace'),
                             stderr=result.stderr.decode(errors='replace'))
        if result.returncode:
            raise RuntimeError('ROM operation failed; board must remain out of normal boot')
        return result

    def inspect(self, name, reset=False, hashes=True):
        output = self.evidence.directory / name
        args = ['-m', 'sd_uploader.rom_probe', '--port', self.endpoint['port'], '--output', str(output)]
        if reset:
            args += ['--reset']
        if hashes:
            args += ['--hashes']
        self._run(args, timeout=1800)
        return json.loads(output.read_text())

    def load(self, manifest, expected_chip):
        # Do not reopen the bridge between execution and HELLO: macOS CH343
        # line transitions can reset into the installed firmware.
        import esptool
        from esptool.targets.esp32p4 import ESP32P4ROM
        if esptool.__version__ != '4.12.0' or Path(sys.executable).absolute() != Path(self.python):
            raise ValueError('run the client with the declared pinned IDF Python interpreter')
        artifact = verify_manifest(manifest)
        self.transport = SerialTransport(self.endpoint['port'], self.evidence)
        try:
            rom = ESP32P4ROM(self.transport.serial)
            log = io.StringIO()
            with redirect_stdout(log), redirect_stderr(log):
                rom.connect(mode='no_reset', attempts=3)
            self.evidence.record('rom-connect-output', output=log.getvalue())
            info = rom.get_security_info()
            chip = ':'.join(f'{b:02x}' for b in rom.read_mac())
            if (rom.IS_STUB or info['flags'] or info['flash_crypt_cnt'] or chip != expected_chip or
                    not artifact['revision_min'] <= rom.get_chip_revision() <= artifact['revision_max']):
                raise ValueError('ROM chip/revision/security mismatch')
            args = ['--chip', 'esp32p4', '--before', 'no_reset', '--after', 'no_reset',
                    '--no-stub', '--baud', '921600', self.load_command, artifact['image']]
            self.evidence.record('ram-load', args=args, image_sha256=artifact['image_sha256'])
            deadline = time.monotonic() + 120
            original_command = rom.command
            def bounded_command(*command_args, **command_kwargs):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('RAM load exceeded 120-second deadline')
                command_kwargs['timeout'] = min(command_kwargs.get('timeout', 3), remaining)
                return original_command(*command_args, **command_kwargs)
            rom.command = bounded_command
            log = io.StringIO()
            try:
                with redirect_stdout(log), redirect_stderr(log):
                    esptool.main(args, esp=rom)
            finally:
                self.evidence.record('ram-load-output', output=log.getvalue())
            self.transport.serial.timeout = 0.1
            self.transport.serial.write_timeout = 5
            self.transport.serial.baudrate = artifact['uart_baud']
            self.evidence.record('ram-executing', entry=artifact['entry'])
            return artifact
        except BaseException:
            self.transport.close()
            raise
