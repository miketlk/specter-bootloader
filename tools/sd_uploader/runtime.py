"""Stable failures, operation deadlines and bounded stderr progress."""
import contextvars
import json
import math
import signal
import sys
import threading
import time

ACTIVE = contextvars.ContextVar('sd_uploader_operation', default=None)


class Failure(ValueError):
    def __init__(self, code, message, exit_code=2):
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


def remaining(limit=None):
    operation = ACTIVE.get()
    if operation is None:
        return limit
    seconds = operation.end - time.monotonic()
    if seconds <= 0:
        raise TimeoutError('Overall operation deadline expired')
    return seconds if limit is None else min(seconds, limit)


def phase(name, device_state=None):
    operation = ACTIVE.get()
    if operation:
        remaining()
        operation.phase = name
        if device_state is not None:
            operation.device_state = device_state
        operation.emit('phase')


class Operation:
    def __init__(self, timeout=900, progress='off'):
        if not math.isfinite(timeout) or timeout <= 0:
            raise Failure('CONFIG', 'Timeout must be a finite positive number')
        self.timeout = timeout
        self.progress = progress
        self.phase = 'preflight'
        self.device_state = 'unknown'
        self.run = None
        self.stop = threading.Event()
        self.cleaning = False

    def emit(self, event):
        if self.progress == 'json':
            print(json.dumps(dict(event=event, phase=self.phase), separators=(',', ':')), file=sys.stderr, flush=True)
        elif self.progress == 'auto' and sys.stderr.isatty():
            print(f'sd-uploader: {self.phase}', file=sys.stderr, flush=True)

    def heartbeat(self):
        while not self.stop.wait(30):
            self.emit('heartbeat')

    def __enter__(self):
        self.end = time.monotonic() + self.timeout
        self.token = ACTIVE.set(self)
        self.handlers = {}
        # POSIX main-thread alarm also bounds third-party C/serial/subprocess waits.
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT):
                self.handlers[sig] = signal.getsignal(sig)
            def alarm(*_):
                if self.cleaning:
                    raise TimeoutError('Five-second ownership cleanup allowance expired')
                self.cleaning = True
                signal.setitimer(signal.ITIMER_REAL, 5)
                raise TimeoutError('Overall operation deadline expired')
            def terminate(*_):
                self.cleaning = True
                signal.setitimer(signal.ITIMER_REAL, 5)
                raise KeyboardInterrupt('Termination requested')
            signal.signal(signal.SIGALRM, alarm)
            signal.signal(signal.SIGTERM, terminate)
            signal.signal(signal.SIGINT, terminate)
            signal.setitimer(signal.ITIMER_REAL, self.timeout)
        self.worker = threading.Thread(target=self.heartbeat, daemon=True)
        self.worker.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        if self.handlers:
            signal.setitimer(signal.ITIMER_REAL, 0)
            for sig, handler in self.handlers.items():
                signal.signal(sig, handler)
        ACTIVE.reset(self.token)


def failure_result(error, operation=None):
    from subprocess import TimeoutExpired
    code, status = 'INTERNAL', 1
    if isinstance(error, Failure):
        code, status = error.code, error.exit_code
    elif isinstance(error, KeyboardInterrupt):
        code, status = 'INTERRUPTED', 130
    elif isinstance(error, (TimeoutError, TimeoutExpired)):
        code, status = 'TIMEOUT', 5
    elif isinstance(error, ModuleNotFoundError):
        code, status = 'DEPENDENCY_MISSING', 2
    elif isinstance(error, BlockingIOError):
        code, status = 'LOCKED', 3
    elif isinstance(error, (ValueError, FileNotFoundError, FileExistsError)):
        code, status = 'CONFIG', 2
    elif isinstance(error, OSError):
        code, status = 'TRANSPORT', 5
    if hasattr(error, 'status') and hasattr(error, 'response'):
        code = {7: 'NO_MEDIA', 8: 'MOUNT_FAILED', 9: 'NO_SPACE', 11: 'DIGEST_MISMATCH',
                12: 'DEST_EXISTS', 13: 'MEDIA_UNCERTAIN'}.get(error.status, 'DEVICE_ERROR')
        status = 4
    message = str(error) or 'Interrupted'
    if hasattr(error, 'status') and hasattr(error, 'response'):
        message = f"Device {error.response.get('opcode', 'operation')} failed ({code}); inspect run evidence"
    if code == 'DEPENDENCY_MISSING':
        message = f'Missing {error.name}; use the declared IDF Python and tools/requirements-sd-uploader.txt'
    result = dict(schema_version=1, status='failed', code=code,
                  phase=operation.phase if operation else 'preflight', message=message[:400],
                  device_state=operation.device_state if operation else 'unknown')
    if operation and operation.run:
        result['run'] = str(operation.run)
    return result, status
