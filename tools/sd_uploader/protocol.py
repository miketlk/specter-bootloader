"""SPSD v1: bounded, canonical CBOR and CRC-32/ISO-HDLC framing."""
import struct
import time
import zlib
import cbor2

MAX_CHUNK = 16384
MAX_PAYLOAD = 18432
ZERO = bytes(16)
OPS = ('HELLO', 'BEGIN', 'WRITE', 'COMMIT', 'STATUS', 'LIST', 'REMOVE', 'ABORT', 'RELEASE', 'VERIFY', 'CLEANUP')


def decode(payload):
    """Validate before letting a general-purpose CBOR decoder allocate objects."""
    def item(pos, depth):
        if depth > 4 or pos >= len(payload):
            raise ValueError('CBOR depth/truncation')
        initial = payload[pos]
        pos += 1
        major, ai = initial >> 5, initial & 31
        if ai < 24:
            n = ai
        elif ai <= 27:
            size = 1 << (ai - 24)
            if pos + size > len(payload):
                raise ValueError('CBOR integer truncated')
            n = int.from_bytes(payload[pos:pos + size], 'big')
            pos += size
            if n < (24 if size == 1 else 1 << (8 * (size // 2))):
                raise ValueError('noncanonical integer')
        else:
            raise ValueError('indefinite/reserved CBOR')
        if major == 0:
            return pos
        if major in (2, 3):
            if n > (MAX_CHUNK if major == 2 else 128) or n > len(payload) - pos:
                raise ValueError('CBOR string bound')
            if major == 3:
                payload[pos:pos + n].decode('utf-8', errors='strict')
            return pos + n
        if major in (4, 5):
            if n > (32 if major == 5 else 64):
                raise ValueError('CBOR collection bound')
            last = None
            for _ in range(n):
                if major == 5:
                    start = pos
                    if pos >= len(payload) or payload[pos] >> 5 != 3:
                        raise ValueError('map keys must be text')
                    pos = item(pos, depth + 1)
                    key = payload[start:pos]
                    order = (len(key), key)
                    if last is not None and order <= last:
                        raise ValueError('duplicate/unsorted map key')
                    last = order
                pos = item(pos, depth + 1)
            return pos
        raise ValueError('unsupported CBOR type')
    if len(payload) > MAX_PAYLOAD or item(0, 0) != len(payload):
        raise ValueError('CBOR size/trailing bytes')
    value = cbor2.loads(payload)
    if not isinstance(value, dict):
        raise ValueError('map required')
    return value


def validate(message):
    required = {'schema_version', 'service', 'kind', 'opcode', 'request_id', 'boot_nonce', 'session_id', 'body'}
    if message.get('kind') in ('response', 'event'):
        required.add('status')
    if set(message) != required or message['schema_version'] != 1 or message['service'] != 'sd-uploader':
        raise ValueError('unsupported schema')
    if message['kind'] not in ('request', 'response', 'event') or message['opcode'] not in OPS:
        raise ValueError('kind/opcode')
    for key in ('request_id',) + (('status',) if 'status' in message else ()):
        if type(message[key]) is not int or not 0 <= message[key] < 2**64:
            raise ValueError(key)
    for key in ('boot_nonce', 'session_id'):
        if type(message[key]) is not bytes or len(message[key]) != 16:
            raise ValueError(key)
    if type(message['body']) is not dict:
        raise ValueError('body')
    return message


def encode(message):
    validate(message)
    payload = cbor2.dumps(message, canonical=True)
    decode(payload)
    header = struct.pack('>BBI', 1, 0, len(payload))
    return b'SPSD' + header + payload + struct.pack('>I', zlib.crc32(header + payload))


class Frames:
    """Demultiplex complete frames; never inspect an accepted frame's contents."""
    def __init__(self):
        self.buffer = bytearray()
        self.since = None

    def feed(self, data, now=None):
        now = time.monotonic() if now is None else now
        result = []
        if self.since is not None and now - self.since > 2:
            self.buffer.clear()
            self.since = None
        # Bytewise admission bounds retained memory even for huge noisy inputs.
        for byte in data:
            self.buffer.append(byte)
            while len(self.buffer) >= 4:
                if self.buffer[:4] not in (b'SPSD', b'SPMF'):
                    del self.buffer[0]
                    self.since = None
                    continue
                if self.since is None:
                    self.since = now
                if len(self.buffer) < 10:
                    break
                version, flags, length = struct.unpack('>BBI', self.buffer[4:10])
                if version != 1 or flags or length > MAX_PAYLOAD:
                    del self.buffer[0]
                    self.since = None
                    continue
                end = 14 + length
                if len(self.buffer) < end:
                    break
                packet = bytes(self.buffer[:end])
                if zlib.crc32(packet[4:-4]) != int.from_bytes(packet[-4:], 'big'):
                    del self.buffer[0]
                    self.since = None
                    continue
                result.append((packet[:4], packet[10:-4]))
                del self.buffer[:end]
                self.since = None
        return result


def valid_name(name):
    # Restrict v1 to printable ASCII, a safe UTF-8/LFN subset on CP437 FAT.
    if not isinstance(name, str) or not 1 <= len(name.encode('utf-8')) <= 128:
        return False
    return not (name in ('.', '..') or name.lower().startswith('_sdu_') or
                name[-1] in ' .' or any(ord(c) < 32 or ord(c) > 126 or c in '/\\:"*?<>|' for c in name))
