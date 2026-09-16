"""Retry exact packets and reject ambiguous commit/restart observations."""
import hashlib
from pathlib import Path
import pytest

pytest.importorskip('cbor2', reason='optional SD uploader dependency')
from sd_uploader.client import Client, DeviceError
from sd_uploader.protocol import Frames, decode, encode


class FakeDevice:
    def __init__(self):
        self.pending=b''
        self.requests=[]
        self.seen={}
        self.contents=bytearray()
        self.writes=0
        self.drop=set()
        self.corrupt_receipt=False
        self.restart=False
        self.receipt=None
        self.drop_all=set()

    def write(self,packet):
        self.requests.append(packet)
        request=decode(Frames().feed(packet)[0][1])
        opcode=request['opcode']
        if packet in self.seen:
            if opcode not in self.drop_all:
                self.pending+=self.seen[packet]
            return
        body={'state':'IDLE','offset':len(self.contents)}
        if opcode=='HELLO':
            body.update(board='lcd-4p3',transport='uart',chunk_max=4096,file_max=2**31-1,heap_free=100000,stack_free=8192)
        if opcode=='BEGIN':
            self.begin=request['body']
            self.contents.clear()
            body.update(state='RECEIVING',offset=0)
        if opcode=='WRITE':
            self.writes+=1
            self.contents.extend(request['body']['data'])
            body.update(state='RECEIVING',offset=len(self.contents))
        if opcode=='COMMIT':
            body.update(state='COMMITTED',name=self.begin['name'],length=len(self.contents),
                        sha256=bytes(32) if self.corrupt_receipt else hashlib.sha256(self.contents).digest())
            self.receipt=body.copy()
        if opcode=='STATUS' and self.receipt:
            body=self.receipt.copy()
        response=dict(request,kind='response',boot_nonce=b'r'*16 if self.restart else b'n'*16,status=0,body=body)
        reply=encode(response)
        self.seen[packet]=reply
        if opcode in self.drop_all:
            return
        if opcode not in self.drop:
            self.pending+=reply
        else:
            self.drop.remove(opcode)

    def read(self):
        data=self.pending
        self.pending=b''
        return data


def test_upload_and_lost_ack(tmp_path):
    source=tmp_path/'binary'
    source.write_bytes(bytes(range(256))*20)
    device=FakeDevice()
    client=Client(device,'lcd-4p3')
    client.hello()
    # Use a very short command deadline for deterministic retry tests.
    original=client.request
    client.request=lambda op,body=None,timeout=5: original(op,body,timeout=.002)
    device.drop={'BEGIN','WRITE','COMMIT'}
    receipt=client.upload(source,'development-fixture.bin')
    assert receipt['sha256']==hashlib.sha256(source.read_bytes()).digest()
    assert device.writes==2
    assert len(device.requests)>len(set(device.requests))


def test_wrong_commit_digest(tmp_path):
    source=tmp_path/'empty'
    source.write_bytes(b'')
    device=FakeDevice()
    device.corrupt_receipt=True
    client=Client(device,'lcd-4p3')
    client.hello()
    with pytest.raises(ValueError,match='receipt'):
        client.upload(source,'empty.bin')


def test_lost_commit_recovers_retained_status(tmp_path):
    source=tmp_path/'payload'
    source.write_bytes(b'committed despite lost replies')
    device=FakeDevice()
    client=Client(device,'lcd-4p3')
    client.hello()
    original=client.request
    client.request=lambda op,body=None,timeout=5: original(op,body,timeout=.002)
    device.drop_all={'COMMIT'}
    receipt=client.upload(source,'fixture.dat')
    assert receipt['sha256']==hashlib.sha256(source.read_bytes()).digest()
    assert device.writes==1
    assert decode(Frames().feed(device.requests[-1])[0][1])['opcode']=='STATUS'


def test_restart_invalidates_session():
    device=FakeDevice()
    client=Client(device,'lcd-4p3')
    client.hello()
    device.restart=True
    with pytest.raises(RuntimeError,match='restarted'):
        client.request('STATUS')


def test_lost_early_hello_retries_same_packet():
    device=FakeDevice()
    device.drop={'HELLO'}
    client=Client(device,'lcd-4p3')
    client.hello()
    assert len(device.requests)==2
    assert device.requests[0]==device.requests[1]


def test_case_digest_rechecked_before_begin(tmp_path):
    source=tmp_path/'changed'
    source.write_bytes(b'new content')
    device=FakeDevice()
    client=Client(device,'lcd-4p3')
    client.hello()
    with pytest.raises(ValueError,match='declared fixture'):
        client.upload(source,'fixture.dat',expected_sha256='00'*32)
    assert len(device.requests)==1


def test_chunk_override_is_bounded_before_mutation(tmp_path):
    source=tmp_path/'payload'
    source.write_bytes(b'a'*4097)
    device=FakeDevice()
    client=Client(device,'lcd-4p3')
    client.hello()
    with pytest.raises(ValueError,match='chunk size'):
        client.upload(source,'fixture.dat',chunk_size=4097)
    assert len(device.requests)==1
    client.upload(source,'fixture.dat',chunk_size=1024)
    assert device.writes==5
