"""Lifecycle failures must not cause a normal firmware boot."""
from contextlib import nullcontext
import json
import pytest

pytest.importorskip('cbor2', reason='optional SD uploader dependency')
pytest.importorskip('elftools', reason='optional SD uploader dependency')
pytest.importorskip('serial', reason='optional SD uploader dependency')
from sd_uploader import runner


@pytest.fixture
def lifecycle(monkeypatch):
    events=[]
    fixture=dict(board='lcd-4p3',bridge_serial='fixture',bridge_location='test',
                 chip='chip',card_cid='card',idf_python='python',ram_manifest='manifest')
    case=dict(id='test',outcome='stage-only',remove=[],files=[],expected_candidates=[],timeout=1)
    artifact=dict(build='build',image_sha256='image')
    state=dict(chip='chip',full_nor='baseline')
    monkeypatch.setattr(runner,'prepare',lambda *_:(fixture,case,artifact,state))
    monkeypatch.setattr(runner,'check_interpreter',lambda *_:None)
    monkeypatch.setattr(runner,'snapshot',lambda f,c,a:(dict(f),dict(c),dict(a)))
    monkeypatch.setattr(runner,'identify',lambda **_:dict(port='fake',serial='fixture'))
    monkeypatch.setattr(runner,'FixtureLock',lambda *_:nullcontext())
    class Loader:
        def __init__(self,*_): self.transport=nullcontext()
        def inspect(self,name,**_):
            events.append(name)
            return dict(state) if name=='nor-before.json' else dict(after)
        def load(self,*_): events.append('load');return artifact
    class Client:
        def __init__(self,*_,**__): self.session=bytes.fromhex('01'*16)
        def hello(self,*_): events.append('hello');return {}
        def listing(self): events.append('list');return []
        def request(self,opcode,*_): events.append(opcode);return {'state':'RELEASED'}
    after=dict(state)
    monkeypatch.setattr(runner,'RamLoader',Loader)
    monkeypatch.setattr(runner,'Client',Client)
    monkeypatch.setattr(runner,'SerialTransport',lambda *_:nullcontext())
    monkeypatch.setattr(runner,'observe',lambda *_:events.append('normal-boot'))
    return events,case,after


def test_stage_only_keeps_board_in_rom(lifecycle,tmp_path):
    events,_,_=lifecycle
    assert runner.run_case('fixture','case',tmp_path/'run')['status']=='passed'
    assert events==['nor-before.json','load','hello','list','list','RELEASE','nor-after.json']


def test_incomplete_candidate_set_never_releases_or_boots(lifecycle,tmp_path):
    events,case,_=lifecycle
    case['expected_candidates']=['specter_upgrade.bin']
    with pytest.raises(ValueError,match='candidate'):
        runner.run_case('fixture','case',tmp_path/'run')
    assert 'RELEASE' not in events and 'normal-boot' not in events


def test_nor_mismatch_never_boots(lifecycle,tmp_path):
    events,case,after=lifecycle
    case['outcome']='approved-mock'
    after['full_nor']='changed'
    with pytest.raises(ValueError,match='NOR'):
        runner.run_case('fixture','case',tmp_path/'run')
    assert 'normal-boot' not in events


def test_dry_run_never_opens_serial(lifecycle,tmp_path):
    events,_,_=lifecycle
    assert runner.run_case('fixture','case',tmp_path,True)['status']=='dry-run'
    assert events==[]


def recovery_files(tmp_path, monkeypatch):
    from sd_uploader import ram_image
    fixture, case, artifact, _ = runner.prepare(None, None)
    fixture = dict(fixture, authorized=True)
    (tmp_path/'declared.json').write_text(json.dumps(fixture))
    (tmp_path/'fixture.json').write_text(json.dumps(fixture))
    (tmp_path/'case.json').write_text(json.dumps(case))
    (tmp_path/'state.json').write_text(json.dumps(dict(ready=True, phase='staging',
        device_state='unknown', sessions=[], media_started=True)))
    (tmp_path/'ram-manifest.json').write_text(json.dumps(artifact))
    (tmp_path/'nor-before.json').write_text(json.dumps({'chip':'chip','full_nor':'baseline'}))
    (tmp_path/'card-before.json').write_text('[]')
    fixture['idf_python'] = str((tmp_path/'python').absolute())
    (tmp_path/'fixture.json').write_text(json.dumps(fixture))
    monkeypatch.setattr(ram_image, 'verify_manifest', lambda _:dict(artifact))


def test_recovery_recreates_and_releases_without_boot(lifecycle,tmp_path,monkeypatch):
    events,_,_=lifecycle
    recovery_files(tmp_path, monkeypatch)
    assert runner.recover(tmp_path/'declared.json',tmp_path)['recovered']
    assert events==['nor-recovery.json','load','hello','list','list','RELEASE','nor-after-recovery.json']


def test_recovery_refuses_changed_ram_image(lifecycle,tmp_path,monkeypatch):
    events,_,_=lifecycle
    recovery_files(tmp_path, monkeypatch)
    (tmp_path/'ram-manifest.json').write_text(json.dumps({'image_sha256':'different'}))
    with pytest.raises(ValueError,match='same checked RAM image'):
        runner.recover(tmp_path/'declared.json',tmp_path)
    assert events==[]


@pytest.mark.parametrize('occupied', [False, True])
def test_existing_run_is_preserved(lifecycle, tmp_path, occupied):
    events, _, _ = lifecycle
    if occupied:
        for name in ('fixture.json', 'case.json', 'ram-manifest.json',
                     'nor-before.json', 'card-before.json', 'result.json', 'journal.jsonl'):
            (tmp_path/name).write_text('original ' + name)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(FileExistsError):
        runner.run_case('fixture', 'case', tmp_path)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    assert events == []


@pytest.mark.parametrize('existing', [False, True])
def test_rejected_lock_never_writes_evidence(lifecycle, tmp_path, monkeypatch, existing):
    from sd_uploader.transport import FixtureLock

    events, _, _ = lifecycle
    directory = tmp_path/'run'
    if existing:
        directory.mkdir()
        for name in ('fixture.json', 'case.json', 'ram-manifest.json',
                     'nor-before.json', 'card-before.json', 'result.json', 'journal.jsonl'):
            (directory/name).write_text('original ' + name)
    before = {p.name: p.read_bytes() for p in directory.iterdir()} if existing else {}
    # Exercise actual nonblocking lock contention without accessing a serial port.
    lock = FixtureLock(str(tmp_path))
    lock.path = tmp_path/'bridge.lock'
    def competing_lock(_):
        contender = FixtureLock(str(tmp_path))
        contender.path = lock.path
        return contender
    monkeypatch.setattr(runner, 'FixtureLock', competing_lock)
    with lock:
        with pytest.raises(BlockingIOError):
            runner.run_case('fixture', 'case', directory)
    assert directory.exists() == existing
    if existing:
        assert {p.name: p.read_bytes() for p in directory.iterdir()} == before
    assert events == []


def test_observed_mock_success_is_distinct_from_boot_request(lifecycle, tmp_path):
    events, case, _ = lifecycle
    case['outcome'] = 'approved-mock'
    result = runner.run_case('fixture', 'case', tmp_path/'observed')
    assert result['outcome'] == result['device_state'] == 'approved-mock'
    assert events[-1] == 'normal-boot'
