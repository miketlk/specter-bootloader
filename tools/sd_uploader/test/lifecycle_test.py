"""Fault injection across durable lifecycle boundaries and immutable inputs."""
from contextlib import nullcontext
import copy
import json
from pathlib import Path
import sys
import time

import pytest

pytest.importorskip('cbor2', reason='optional SD uploader dependency')
pytest.importorskip('elftools', reason='optional SD uploader dependency')
pytest.importorskip('serial', reason='optional SD uploader dependency')

from sd_uploader import runner, ram_image
from sd_uploader.client import DeviceError
from sd_uploader.runtime import Failure, Operation


@pytest.fixture
def rig(tmp_path, monkeypatch):
    source = tmp_path/'specter_upgrade.bin'
    source.write_bytes(b'first image')
    fixture = dict(schema_version=2, authorized=True, board='lcd-5', bridge_serial='bridge',
                   bridge_location='location', chip='chip', card_cid='card', idf_python=sys.executable,
                   ram_manifest='manifest', starting_state=None, reset_controller='qualified-ch343',
                   preservation='current', allowed_resets=['rom-entry', 'rom-verify', 'normal-boot'])
    artifact = dict(build='build', image_sha256='image')
    state = dict(chip='chip', full_nor='original')
    data = dict(files={'unrelated.txt': b'keep'}, events=[], fail=None, nor=state, hello=None)
    fixture_path = tmp_path/'fixture.json'
    fixture_path.write_text(json.dumps(fixture))
    monkeypatch.setattr(runner, 'load_fixture', lambda _: (copy.deepcopy(fixture), artifact, None))
    monkeypatch.setattr(runner, 'check_interpreter', lambda _: None)
    monkeypatch.setattr(runner, 'identify', lambda **_:dict(port='new-port', serial='bridge'))
    monkeypatch.setattr(runner, 'FixtureLock', lambda *_:nullcontext())
    monkeypatch.setattr(ram_image, 'verify_manifest', lambda _:artifact)
    def snapshot(f, c, a):
        f, c = copy.deepcopy(f), copy.deepcopy(c)
        for file in c['files']:
            dest = tmp_path/('snapshot-' + str(len(list(tmp_path.glob('snapshot-*')))))
            dest.write_bytes(Path(file['path']).read_bytes())
            file['path'] = str(dest)
        return f, c, a
    monkeypatch.setattr(runner, 'snapshot', snapshot)
    transition = runner.transition
    def boundary(evidence, state, name, device_state):
        transition(evidence, state, name, device_state)
        data['events'].append(name)
        if data['fail'] == name:
            data['fail'] = None
            raise KeyboardInterrupt(name)
    monkeypatch.setattr(runner, 'transition', boundary)
    class Loader:
        def __init__(self, *_): self.transport = nullcontext()
        def inspect(self, name, **_):
            data['events'].append(name)
            return dict(data['nor'])
        def load(self, *_): data['events'].append('load')
    class Client:
        def __init__(self, *_, **__): self.session = bytes.fromhex('12'*16)
        def hello(self, *_):
            if data['hello']: raise ValueError(data['hello'])
            return {}
        def listing(self):
            return [dict(name=n, size=len(b), temporary=int(n.startswith('_sdu_')), type='file') for n,b in data['files'].items()]
        def verify(self, source, name):
            data['events'].append('VERIFY')
            if data['files'][name] != Path(source).read_bytes():
                raise DeviceError(dict(status=11, opcode='VERIFY', body={}))
        def upload(self, source, name, **_):
            data['events'].append('UPLOAD')
            data['files'][name] = Path(source).read_bytes()
            if data['fail'] == 'commit':
                data['fail'] = None
                raise KeyboardInterrupt('lost commit reply')
        def request(self, command, body=None):
            data['events'].append(command)
            if command in ('REMOVE', 'CLEANUP'): data['files'].pop(body['name'], None)
            return dict(state='RELEASED')
    monkeypatch.setattr(runner, 'RamLoader', Loader)
    monkeypatch.setattr(runner, 'Client', Client)
    monkeypatch.setattr(runner, 'SerialTransport', lambda *_:nullcontext())
    monkeypatch.setattr(runner, 'normal_boot', lambda *_:data['events'].append('normal-boot'))
    return fixture_path, source, data, fixture


def invoke(rig, directory, **kwargs):
    fixture, source, _, _ = rig
    with Operation(30):
        return runner.stage(fixture, source, directory=directory, **kwargs)


@pytest.mark.parametrize('boundary', ['starting-state', 'ram-load', 'identity', 'preflight', 'staging',
                                      'commit', 'release', 'flash-preservation'])
def test_interrupt_recover_uses_first_snapshot(rig, tmp_path, boundary):
    fixture, source, data, _ = rig
    data['fail'] = boundary
    with pytest.raises(KeyboardInterrupt): invoke(rig, tmp_path/'run')
    source.write_bytes(b'second build at the same path')
    result = runner.recover(fixture, tmp_path/'run')
    assert result['recovered'] and result['device_state'] == 'rom'
    assert data['files'] == {'unrelated.txt': b'keep', 'specter_upgrade.bin': b'first image'}
    assert 'normal-boot' not in data['events']
    invoke(rig, tmp_path/'new-run', replace=True)
    assert data['files']['specter_upgrade.bin'] == source.read_bytes()


def test_lost_commit_is_verified_not_republished(rig, tmp_path):
    fixture, _, data, _ = rig
    data['fail'] = 'commit'
    with pytest.raises(KeyboardInterrupt): invoke(rig, tmp_path/'run')
    runner.recover(fixture, tmp_path/'run')
    assert data['events'].count('UPLOAD') == 1 and 'VERIFY' in data['events']


@pytest.mark.parametrize('existing,replace,code', [(b'first image', False, None),
    (b'other image', False, 'DEST_EXISTS'), (b'other image', True, None)])
def test_noop_and_same_size_replacement(rig, tmp_path, existing, replace, code):
    _, _, data, _ = rig
    data['files']['specter_upgrade.bin'] = existing
    if code:
        with pytest.raises(Failure) as caught: invoke(rig, tmp_path/'run', replace=replace)
        assert caught.value.code == code and 'REMOVE' not in data['events']
    else:
        result = invoke(rig, tmp_path/'run', replace=replace)
        assert result['no_op'] == (existing == b'first image')
        assert data['files']['specter_upgrade.bin'] == b'first image'
        if existing != b'first image': assert 'replacement-reload' in data['events']
    assert data['files']['unrelated.txt'] == b'keep'


def test_unexpected_candidate_prevents_all_mutations(rig, tmp_path):
    _, _, data, _ = rig
    data['files'].update({'specter_upgrade_other.bin': b'other', 'specter_upgrade.bin': b'old'})
    with pytest.raises(Failure) as caught: invoke(rig, tmp_path/'run', replace=True)
    assert caught.value.code == 'CANDIDATE_CONFLICT'
    assert 'REMOVE' not in data['events'] and 'UPLOAD' not in data['events']


def test_wrong_card_prevents_listing_and_mutation(rig, tmp_path):
    _, _, data, _ = rig
    data['hello'] = 'card_cid identity mismatch'
    with pytest.raises(Failure) as caught: invoke(rig, tmp_path/'run')
    assert caught.value.code == 'IDENTITY_MISMATCH'
    assert 'UPLOAD' not in data['events'] and 'normal-boot' not in data['events']


@pytest.mark.parametrize('boundary', ['boot-observation', 'complete'])
def test_uncertain_boot_never_replayed(rig, tmp_path, boundary):
    fixture, _, data, _ = rig
    data['fail'] = boundary
    with pytest.raises(KeyboardInterrupt): invoke(rig, tmp_path/'run', after='boot')
    original = list(data['events'])
    if boundary == 'complete':
        assert runner.recover(fixture, tmp_path/'run')['device_state'] == 'boot-requested'
    else:
        with pytest.raises(Failure) as caught: runner.recover(fixture, tmp_path/'run')
        assert caught.value.code == 'HANDOFF_REQUIRED'
    assert data['events'] == original


def test_explicit_boot_result_and_completed_recovery_is_offline(rig, tmp_path):
    fixture, _, data, _ = rig
    result = invoke(rig, tmp_path/'run', after='boot')
    assert result['outcome'] == result['device_state'] == 'boot-requested'
    assert data['events'].index('nor-after.json') < data['events'].index('normal-boot')
    before = list(data['events'])
    assert runner.recover(fixture, tmp_path/'run') == result
    assert data['events'] == before


def test_early_missing_evidence_restarts_but_missing_late_evidence_refuses(rig, tmp_path):
    fixture, _, data, _ = rig
    data['fail'] = 'release'
    with pytest.raises(KeyboardInterrupt): invoke(rig, tmp_path/'run')
    (tmp_path/'run/card-before.json').unlink()
    with pytest.raises(Failure) as caught: runner.recover(fixture, tmp_path/'run')
    assert caught.value.code == 'EVIDENCE_INCOMPLETE'
    assert data['events'].count('UPLOAD') == 1


def test_only_recorded_temporary_files_can_be_cleaned(rig, tmp_path):
    fixture, _, data, _ = rig
    data['fail'] = 'staging'
    with pytest.raises(KeyboardInterrupt): invoke(rig, tmp_path/'run')
    data['files']['_sdu_' + 'ff'*16 + '.part'] = b'foreign'
    with pytest.raises(Failure) as caught: runner.recover(fixture, tmp_path/'run')
    assert caught.value.code == 'TEMP_CONFLICT' and 'CLEANUP' not in data['events']
    del data['files']['_sdu_' + 'ff'*16 + '.part']
    data['files']['_sdu_' + '12'*16 + '.part'] = b'owned'
    runner.recover(fixture, tmp_path/'run')
    assert 'CLEANUP' in data['events']


def test_deadline_bounds_blocking_work_without_boot(rig, tmp_path, monkeypatch):
    _, _, data, _ = rig
    monkeypatch.setattr(runner, 'snapshot', lambda *_:time.sleep(5))
    start = time.monotonic()
    with pytest.raises(TimeoutError), Operation(.05):
        runner.stage(rig[0], rig[1], directory=tmp_path/'run')
    assert time.monotonic() - start < 1
    assert not data['events']


def test_dry_run_has_no_writes_or_device_selection(rig, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'identify', lambda **_:pytest.fail('device access'))
    before = set(tmp_path.iterdir())
    result = invoke(rig, tmp_path/'run', dry_run=True)
    assert not result['identity_checked'] and not result['media_checked']
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize('policy', ['current', 'pinned'])
def test_before_after_nor_mismatch_refuses_boot(rig, tmp_path, monkeypatch, policy):
    _, _, data, fixture = rig
    fixture['preservation'] = policy
    transition = runner.transition
    def change_after_release(evidence, state, name, device_state):
        transition(evidence, state, name, device_state)
        if name == 'flash-preservation': data['nor']['full_nor'] = 'changed'
    monkeypatch.setattr(runner, 'transition', change_after_release)
    with pytest.raises(Failure) as caught: invoke(rig, tmp_path/'run', after='boot')
    assert caught.value.code == 'NOR_MISMATCH' and 'normal-boot' not in data['events']


def test_pinned_refusal_has_no_fallback(rig, tmp_path, monkeypatch):
    _, _, data, fixture = rig
    fixture['preservation'] = 'pinned'
    fixture['starting_state'] = str(tmp_path/'baseline.json')
    Path(fixture['starting_state']).write_text('{}')
    monkeypatch.setattr(runner, 'load_fixture', lambda _: (fixture, dict(build='build', image_sha256='image'),
                                                         dict(full_nor='other baseline')))
    with pytest.raises(Failure) as caught: invoke(rig, tmp_path/'run')
    assert caught.value.code == 'NOR_MISMATCH' and 'load' not in data['events']


def test_rom_only_does_not_require_boot_permission(rig, tmp_path):
    _, _, _, fixture = rig
    fixture['allowed_resets'] = ['rom-entry', 'rom-verify']
    assert invoke(rig, tmp_path/'rom')['device_state'] == 'rom'
    with pytest.raises(Failure) as caught: invoke(rig, tmp_path/'boot', after='boot')
    assert caught.value.code == 'RESET_NOT_AUTHORIZED'


def test_multiple_destinations_all_checked_before_removal(rig, tmp_path):
    _, source, data, fixture = rig
    data['files'].update({'remove.dat': b'old', 'collision.dat': b'unrelated'})
    case = dict(id='case', outcome='stage-only', remove=['remove.dat'], timeout=30,
                expected_candidates=[], files=[dict(path=str(source), name='collision.dat', sha256=ram_image.sha(source))])
    with pytest.raises(Failure) as caught:
        runner.run_plan(fixture, case, dict(build='build', image_sha256='image'), None, tmp_path/'run')
    assert caught.value.code == 'DEST_EXISTS' and data['files']['remove.dat'] == b'old'
    assert 'REMOVE' not in data['events']


def test_fat_case_insensitive_replacement(rig, tmp_path):
    _, _, data, _ = rig
    data['files']['SPECTER_UPGRADE.BIN'] = b'first image'
    with pytest.raises(Failure) as caught: invoke(rig, tmp_path/'refused')
    assert caught.value.code == 'DEST_EXISTS'
    invoke(rig, tmp_path/'replaced', replace=True)
    assert 'SPECTER_UPGRADE.BIN' not in data['files']
    assert data['files']['specter_upgrade.bin'] == b'first image'


def test_automatic_run_allocation_is_unique(rig, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setattr(runner, 'ROOT', tmp_path)
    # Exercise reservation concurrently, without a fake shared device filesystem.
    monkeypatch.setattr(runner, 'execute', lambda f,c,a,s,ep,e,st: dict(run=str(e.directory)))
    with ThreadPoolExecutor(2) as workers:
        results = list(workers.map(lambda _:runner.stage(rig[0], rig[1]), range(2)))
    assert results[0]['run'] != results[1]['run']
    assert all((Path(result['run'])/'state.json').is_file() for result in results)


@pytest.mark.parametrize('baseline', [None, '{"chip":', 'null', '{}'])
@pytest.mark.parametrize('boundary', ['starting-state', 'release'])
def test_interrupted_baseline_recovery(rig, tmp_path, baseline, boundary):
    fixture, _, data, _ = rig
    data['fail'] = boundary
    directory = tmp_path/'run'
    with pytest.raises(KeyboardInterrupt): invoke(rig, directory)
    path = directory/'nor-before.json'
    if baseline is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(baseline)
    events = list(data['events'])
    if boundary == 'release':
        with pytest.raises(Failure) as caught: runner.recover(fixture, directory)
        assert caught.value.code == 'EVIDENCE_INCOMPLETE'
        assert data['events'] == events
    else:
        assert runner.recover(fixture, directory)['recovered']
        assert json.loads(path.read_text()) == data['nor']


@pytest.mark.parametrize('changed', ['regions', 'full_nor', 'chip', 'revision'])
def test_recovery_separates_layout_diagnostics_from_nor_identity(rig, tmp_path, changed):
    fixture, _, data, _ = rig
    data['nor'].update(revision=1, regions=dict(main=dict(offset=0, size=16, md5='old')))
    data['fail'] = 'release'
    with pytest.raises(KeyboardInterrupt): invoke(rig, tmp_path/'run')
    # Model the probe output after host partition sizes change, with identical NOR.
    data['nor'][changed] = dict(main=dict(offset=0, size=32, md5='new')) if changed == 'regions' else 'changed'
    if changed == 'regions':
        assert runner.recover(fixture, tmp_path/'run')['recovered']
    else:
        with pytest.raises(Failure) as caught: runner.recover(fixture, tmp_path/'run')
        assert caught.value.code in ('NOR_MISMATCH', 'IDENTITY_MISMATCH')
    assert data['events'].count('UPLOAD') == 1
