"""Cross-language protocol admission and noisy shared-stream regression tests."""
import ctypes
import struct
import subprocess
import zlib
from pathlib import Path
import pytest

cbor2 = pytest.importorskip('cbor2', reason='optional SD uploader dependency')
from sd_uploader.protocol import Frames, ZERO, decode, encode, valid_name

ROOT = Path(__file__).resolve().parents[3]


def request():
    return dict(schema_version=1,service='sd-uploader',kind='request',opcode='HELLO',
                request_id=1,boot_nonce=ZERO,session_id=bytes(range(16)),body={})


@pytest.fixture(scope='module')
def codec(tmp_path_factory):
    library = tmp_path_factory.mktemp('codec')/'codec.so'
    source = ROOT/'platforms/esp32-p4-wifi6-touch-lcd/sd_uploader/main/protocol.c'
    subprocess.run(['cc','-shared','-fPIC','-Wall','-Wextra','-Werror',str(source),'-o',str(library)],check=True)
    lib = ctypes.CDLL(str(library))
    lib.sdu_cbor.argtypes = [ctypes.c_char_p,ctypes.c_size_t]
    lib.sdu_cbor.restype = ctypes.c_bool
    lib.sdu_crc.argtypes = [ctypes.c_char_p,ctypes.c_size_t]
    lib.sdu_crc.restype = ctypes.c_uint32
    lib.sdu_name.argtypes = [ctypes.c_char_p]
    lib.sdu_name.restype = ctypes.c_bool
    return lib


def test_golden(codec):
    frame = encode(request())
    golden = (Path(__file__).parent.parent/'fixtures/hello.hex.txt').read_text().strip()
    assert frame.hex() == golden
    payload = frame[10:-4]
    assert decode(payload) == request()
    assert codec.sdu_cbor(payload,len(payload))
    assert codec.sdu_crc(b'123456789',9) == 0xcbf43926
    out = ctypes.create_string_buffer(len(frame))
    codec.sdu_frame.argtypes=[ctypes.c_void_p,ctypes.c_char_p,ctypes.c_size_t]
    codec.sdu_frame.restype=ctypes.c_size_t
    assert codec.sdu_frame(out,payload,len(payload)) == len(frame)
    assert out.raw == frame


def test_maximum_chunk_cross_language(codec):
    message=request()
    message['opcode']='WRITE'
    data=bytes(range(256))*64
    message['body']=dict(offset=0,data=data,chunk_crc=zlib.crc32(data))
    frame=encode(message)
    payload=frame[10:-4]
    assert decode(payload)==message
    assert codec.sdu_cbor(payload,len(payload))
    message['body']['data']=data+b'x'
    with pytest.raises(ValueError):
        encode(message)


@pytest.mark.parametrize('length', [0, 1, 16, 255, 4096, 16384])
def test_crc_table_matches_independent_reference(codec, length):
    payload=bytes((i*71+i//7)&255 for i in range(length))
    assert codec.sdu_crc(payload,length)==zlib.crc32(payload)


@pytest.mark.parametrize('payload',[
    b'\xbf\xff', b'\xa2\x61a\x01\x61a\x02', b'\xa1\x61x\x18\x01',
    b'\xa1\x61x\x20', b'\xa1\x61x\xf5', b'\xa1\x61x\xc0\x00',
    b'\xa1\x61x\xfa\0\0\0\0', b'\xa1\x61x\x61\xff', b'\xa0\xa0',
    b'\xa1\x61x\x9f\xff', b'\xa1\x61x\x81\x81\x81\x81\x00',
    b'\xa1\x61x\x59\x40\x01'+bytes(16385), b'\xb8\x21', b'\xa1\x01\x01',
])
def test_reject_cbor(codec,payload):
    with pytest.raises((ValueError,UnicodeError)):
        decode(payload)
    assert not codec.sdu_cbor(payload,len(payload))


def packet(magic,payload):
    header=struct.pack('>BBI',1,0,len(payload))
    return magic+header+payload+struct.pack('>I',zlib.crc32(header+payload))


def test_demultiplex():
    good=encode(request())
    mock=packet(b'SPMF',cbor2.dumps({'embedded':good}))
    corrupt=bytearray(good)
    corrupt[-1]^=1
    stream=b'ESP-ROM text\r\n'+bytes(corrupt)+mock+good
    parser=Frames()
    frames=[]
    for byte in stream:
        frames.extend(parser.feed(bytes([byte]),now=0))
    assert [magic for magic,_ in frames] == [b'SPMF',b'SPSD']
    assert decode(frames[-1][1]) == request()
    assert len(parser.buffer) < 4


def test_timeout_and_bound():
    parser=Frames()
    assert not parser.feed(b'SPSD\1\0\0\0\x20\0',now=0)
    assert parser.feed(encode(request()),now=3)
    assert not parser.feed(b'x'*100000)
    assert len(parser.buffer) <= 3


@pytest.mark.parametrize('name',['../x','/x','x/y','x\\y','_SDU_test.part','x.','x ','','a:b','é','a'*129,'x\x01','..'])
def test_names(codec,name):
    assert not valid_name(name)
    assert not codec.sdu_name(name.encode())


def test_upgrade_lfn(codec):
    name='specter_upgrade_main_v1.23.45-development.bin'
    assert valid_name(name) and codec.sdu_name(name.encode())
