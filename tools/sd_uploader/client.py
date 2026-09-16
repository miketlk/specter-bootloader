"""Bounded request retries and streaming, independently verified transactions."""
import hashlib
import os
import time
import zlib
from pathlib import Path
from .runtime import remaining, Failure
from .protocol import ZERO, MAX_CHUNK, Frames, encode, decode, validate, valid_name


class DeviceError(RuntimeError):
    def __init__(self, message):
        self.response = message
        self.status = message['status']
        super().__init__(f"{message['opcode']} status={self.status}: {message['body']}")


class Client:
    def __init__(self, transport, board, session=None, evidence=None):
        self.transport = transport
        self.board = board
        self.session = os.urandom(16) if session is None else session
        if len(self.session) != 16 or self.session == ZERO:
            raise ValueError('nonzero 16-byte session required')
        self.nonce = ZERO
        # Allows explicitly reused session tokens across separate CLI processes.
        self.request_id = time.time_ns()
        self.frames = Frames()
        self.evidence = evidence
        self.identity = None
        self.metrics = {}
        self.last_upload = None

    def _timing(self, opcode, **values):
        totals = self.metrics.setdefault(opcode, {'count': 0})
        totals['count'] += 1
        for key, value in values.items():
            totals[key] = totals.get(key, 0) + value
        if self.evidence:
            self.evidence.record('timing', opcode=opcode, **values)

    def request(self, opcode, body=None, timeout=5):
        started = time.perf_counter()
        self.request_id += 1
        message = dict(schema_version=1, service='sd-uploader', kind='request',
                       opcode=opcode, request_id=self.request_id,
                       boot_nonce=ZERO if opcode == 'HELLO' else self.nonce,
                       session_id=self.session, body={} if body is None else body)
        packet = encode(message)
        encoded = time.perf_counter() - started
        write_seconds = 0
        total_end = time.monotonic() + remaining(timeout * 4)
        if self.evidence:
            self.evidence.record('request', opcode=opcode, request_id=self.request_id,
                                 packet_sha256=hashlib.sha256(packet).hexdigest())
        for attempt in range(4):
            remaining()
            writing = time.perf_counter()
            self.transport.write(packet)
            write_seconds += time.perf_counter() - writing
            # The first HELLO can precede UART initialization after ROM execute.
            # Retry that harmless packet promptly, retaining full subsequent
            # deadlines for slower card initialization.
            attempt_timeout = min(timeout, .25) if opcode == 'HELLO' and attempt == 0 else timeout
            deadline = min(total_end, time.monotonic() + attempt_timeout)
            while time.monotonic() < deadline:
                remaining()
                for magic, payload in self.frames.feed(self.transport.read()):
                    if magic != b'SPSD':
                        continue
                    try:
                        response = validate(decode(payload))
                    except (ValueError, UnicodeError):
                        continue
                    if (response['request_id'] != self.request_id or
                            response['opcode'] != opcode or response['session_id'] != self.session or
                            response['kind'] not in ('event', 'response')):
                        continue
                    if opcode != 'HELLO' and response['boot_nonce'] != self.nonce:
                        raise Failure('IDENTITY_MISMATCH', 'Device restarted; fixture requires revalidation', 3)
                    if self.evidence:
                        self.evidence.record(response['kind'], message=response)
                    if response['kind'] == 'event':
                        continue
                    if response['status']:
                        raise DeviceError(response)
                    if opcode == 'HELLO':
                        if response['boot_nonce'] == ZERO:
                            raise Failure('IDENTITY_MISMATCH', 'invalid device nonce', 3)
                        self.nonce = response['boot_nonce']
                    self._timing(opcode, total_s=time.perf_counter()-started,
                                 encode_s=encoded, serial_write_s=write_seconds,
                                 transmitted_bytes=len(packet)*(attempt+1), retries=attempt,
                                 frame_us=response['body'].get('frame_us', 0),
                                 storage_write_us=response['body'].get('write_us', 0),
                                 device_us=response['body'].get('operation_us', 0))
                    return response['body']
        raise TimeoutError(f'{opcode}: no matching response after bounded retries')

    def hello(self, expected=None):
        identity = self.request('HELLO')
        if identity.get('board') != self.board or identity.get('transport') != 'uart':
            raise Failure('IDENTITY_MISMATCH', 'board/transport identity mismatch', 3)
        if not 1 <= identity.get('chunk_max', 0) <= MAX_CHUNK:
            raise Failure('IDENTITY_MISMATCH', 'invalid device limits', 3)
        if identity.get('heap_free', 0) < 65536 or identity.get('stack_free', 0) < 4096:
            raise Failure('IDENTITY_MISMATCH', 'device runtime RAM margin below qualified minimum', 3)
        if expected:
            for key in ('chip', 'card_cid', 'build'):
                if identity.get(key) != expected[key]:
                    raise Failure('IDENTITY_MISMATCH', f'{key} identity mismatch', 3)
        self.identity = identity
        return identity

    def listing(self):
        cursor = 0
        entries = []
        while True:
            page = self.request('LIST', {'cursor': cursor})
            entries.extend(page['entries'])
            next_cursor = page['next_cursor']
            if not next_cursor:
                return entries
            if not cursor < next_cursor <= 65536 or len(entries) > 65536:
                raise Failure('VERIFY_MISMATCH', 'invalid listing cursor', 4)
            cursor = next_cursor

    def upload(self, source, name, expected_sha256=None, chunk_size=None):
        started = time.perf_counter()
        if not self.identity:
            raise ValueError('HELLO required before upload')
        if not valid_name(name):
            raise ValueError('invalid destination basename')
        chunk_size = self.identity['chunk_max'] if chunk_size is None else chunk_size
        if type(chunk_size) is not int or not 1 <= chunk_size <= self.identity['chunk_max']:
            raise ValueError('chunk size exceeds device limit')
        path = Path(source)
        with path.open('rb') as input_file:
            original = os.fstat(input_file.fileno())
            length = original.st_size
            if not 0 <= length <= self.identity['file_max']:
                raise ValueError('file exceeds device limit')
            hashing = time.perf_counter()
            digest = hashlib.file_digest(input_file, 'sha256').digest()
            hash_seconds = time.perf_counter() - hashing
            if expected_sha256 is not None and digest.hex() != expected_sha256:
                raise ValueError('source differs from declared fixture digest')
            input_file.seek(0)
            self.request('BEGIN', dict(name=name, length=length, sha256=digest))
            offset = 0
            streamed = hashlib.sha256()
            while offset < length:
                chunk = input_file.read(min(chunk_size, length - offset))
                if not chunk:
                    raise OSError('local file shortened during upload')
                response = self.request('WRITE', dict(offset=offset, data=chunk,
                                                     chunk_crc=zlib.crc32(chunk)))
                streamed.update(chunk)
                offset += len(chunk)
                if response['offset'] != offset:
                    raise Failure('VERIFY_MISMATCH', 'unexpected accepted offset', 4)
            current = os.fstat(input_file.fileno())
            if (input_file.read(1) or streamed.digest() != digest or
                    (original.st_size, original.st_mtime_ns, original.st_ctime_ns) !=
                    (current.st_size, current.st_mtime_ns, current.st_ctime_ns)):
                self.request('ABORT')
                raise OSError('local file changed during upload')
        try:
            receipt = self.request('COMMIT', timeout=10 + length / 32768)
        except TimeoutError:
            # A lost commit receipt must not cause a second publication. STATUS
            # retains the verified result until the next transaction.
            receipt = self.request('STATUS')
        if (receipt.get('state') != 'COMMITTED' or receipt.get('name') != name or
                receipt.get('length') != length or receipt.get('sha256') != digest):
            raise Failure('VERIFY_MISMATCH', 'commit readback receipt mismatch', 4)
        self.last_upload = dict(length=length, total_s=time.perf_counter()-started,
                                local_hash_s=hash_seconds, chunk_size=chunk_size)
        if self.evidence:
            self.evidence.record('upload-timing', name=name, **self.last_upload)
        return receipt

    def verify(self, source, name):
        path = Path(source)
        with path.open('rb') as stream:
            length = os.fstat(stream.fileno()).st_size
            digest = hashlib.file_digest(stream, 'sha256').digest()
        result = self.request('VERIFY', dict(name=name,length=length,sha256=digest),
                              timeout=10 + length / 32768)
        if result.get('name') != name or result.get('length') != length or result.get('sha256') != digest:
            raise Failure('VERIFY_MISMATCH', 'readback verification receipt mismatch', 4)
        return result
