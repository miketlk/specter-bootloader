"""The public command contract is usable without optional hardware libraries."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

CLI = Path(__file__).resolve().parents[2] / 'sd-uploader.py'


def call(*args, isolated=False, env=None):
    return subprocess.run([sys.executable, *(['-S'] if isolated else []), str(CLI), *args],
                          capture_output=True, text=True, env=env, timeout=10)


@pytest.mark.parametrize('args', [('--help',), ('stage', '--help'), ('recover', '--help')])
def test_help_without_site_packages(args):
    result = call(*args, isolated=True)
    assert result.returncode == 0 and 'usage:' in result.stdout and not result.stderr


@pytest.mark.parametrize('args,code', [([], 'USAGE'), (['not-a-command'], 'USAGE'),
    (['stage', 'file.bin'], 'CONFIG'), (['devices'], 'DEPENDENCY_MISSING'),
    (['stage', 'file.bin', '--timeout', 'nan'], 'CONFIG')])
def test_compact_failure_without_dependencies(args, code):
    result = call(*args, isolated=True, env={k:v for k,v in os.environ.items() if k != 'SD_UPLOADER_FIXTURE'})
    body = json.loads(result.stdout)
    assert result.returncode == 2 and body['code'] == code
    assert len(result.stdout.encode()) <= 768 and len(result.stdout.splitlines()) == 1
    assert not result.stderr and body['device_state'] == 'unknown'


def test_usage_text():
    result = call('stage', '--format', 'text', isolated=True)
    assert result.returncode == 2 and result.stdout.startswith('failed [USAGE]')


def test_offline_result_compact(tmp_path):
    (tmp_path/'state.json').write_text(json.dumps(dict(ready=True, phase='staging', device_state='uploader')))
    result = call('result', '--run', str(tmp_path), isolated=True)
    assert result.returncode == 0 and len(result.stdout.encode()) < 1024
    assert json.loads(result.stdout)['recovery'] == 'recover'


def test_fixture_precedence(monkeypatch):
    from sd_uploader.cli import parser
    monkeypatch.setenv('SD_UPLOADER_FIXTURE', 'environment.json')
    assert str(parser().parse_args(['stage', 'file']).fixture) == 'environment.json'
    assert str(parser().parse_args(['stage', 'file', '--fixture', 'explicit.json']).fixture) == 'explicit.json'


def test_third_party_stdout_is_captured(monkeypatch, capsys):
    from sd_uploader import cli
    def dispatch(_):
        print('third party banner')
        return dict(status='passed', destination='specter_upgrade.bin', sha256='01'*32, length=1024)
    monkeypatch.setattr(cli, 'dispatch', dispatch)
    assert cli.main(['devices']) == 0
    out = capsys.readouterr()
    assert json.loads(out.out)['status'] == 'passed'
    assert len(out.out) < 1024 and out.err == 'third party banner\n'


def test_doctor_without_dependencies():
    result = call('doctor', '--fixture', 'nonexistent.json', isolated=True)
    body = json.loads(result.stdout)
    assert result.returncode == 2 and body['code'] == 'DOCTOR_FAILED'
    assert not body['identity_checked'] and not body['media_checked']


def test_sigterm_returns_one_terminal_result(tmp_path):
    import signal
    import time
    script = tmp_path/'cancel.py'
    script.write_text('''import time
from sd_uploader import cli
def dispatch(args):
    print('ready', flush=True)
    time.sleep(60)
cli.dispatch = dispatch
raise SystemExit(cli.main(['devices', '--progress', 'off']))
''')
    process = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True,
                               env=dict(os.environ, PYTHONPATH=str(CLI.parent)))
    assert process.stderr.readline().strip() == 'ready'
    process.send_signal(signal.SIGTERM)
    out, err = process.communicate(timeout=5)
    assert process.returncode == 130 and not err
    assert json.loads(out)['code'] == 'INTERRUPTED' and len(out.splitlines()) == 1


def test_deadline_returns_one_terminal_result(tmp_path):
    script = tmp_path/'deadline.py'
    script.write_text('''import time
from sd_uploader import cli
cli.dispatch = lambda args: time.sleep(10)
raise SystemExit(cli.main(['devices', '--timeout', '.05', '--progress', 'off']))
''')
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                            env=dict(os.environ, PYTHONPATH=str(CLI.parent)), timeout=3)
    assert result.returncode == 5 and json.loads(result.stdout)['code'] == 'TIMEOUT'
    assert not result.stderr


def test_doctor_never_opens_serial(monkeypatch):
    for dependency in ('cbor2', 'elftools', 'serial', 'esptool'):
        pytest.importorskip(dependency, reason='optional SD uploader dependency')
    from sd_uploader import cli, fixture, runner, transport
    monkeypatch.setattr(fixture, 'load_fixture', lambda _: (dict(bridge_serial='s', bridge_location='l'), {}, None))
    monkeypatch.setattr(runner, 'check_interpreter', lambda _:None)
    monkeypatch.setattr(transport, 'identify', lambda **_:dict(port='enumerated'))
    monkeypatch.setattr(transport, 'SerialTransport', lambda *_:pytest.fail('serial open'))
    assert cli.doctor('fixture')['identity_checked'] is False


def test_declared_interpreter_mismatch():
    for dependency in ('cbor2', 'elftools', 'serial', 'esptool'):
        pytest.importorskip(dependency, reason='optional SD uploader dependency')
    from sd_uploader.runner import check_interpreter
    from sd_uploader.runtime import Failure
    with pytest.raises(Failure) as caught:
        check_interpreter(dict(idf_python='/wrong/python'))
    assert caught.value.code == 'INTERPRETER_MISMATCH'


@pytest.mark.parametrize('args, expected_count, truncated', [([], 2, True), (['--candidates'], 1, True)])
def test_list_has_bounded_continuation_without_hello(monkeypatch, capsys, args, expected_count, truncated):
    for dependency in ('cbor2', 'elftools', 'serial', 'esptool'):
        pytest.importorskip(dependency, reason='optional SD uploader dependency')
    from contextlib import nullcontext
    from sd_uploader import cli, client, transport
    monkeypatch.setattr(transport, 'identify', lambda **_:dict(serial='serial'))
    monkeypatch.setattr(transport, 'FixtureLock', lambda *_:nullcontext())
    monkeypatch.setattr(transport, 'SerialTransport', lambda *_:nullcontext())
    class Client:
        def __init__(self, *_): pass
        def hello(self, *_): return dict(huge='do not return this')
        def request(self, command, body):
            assert command == 'LIST' and body == dict(cursor=0)
            return dict(entries=[dict(name='unrelated'),dict(name='specter_upgrade.bin')], next_cursor=2)
    monkeypatch.setattr(client, 'Client', Client)
    assert cli.main(['list', '--port', 'p', '--board', 'lcd-5', '--session', '12'*16,
                     '--pages', '1', *args]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result['entries']) == expected_count
    assert result['truncated'] == truncated and result['next_cursor'] == 2 and 'hello' not in result


def test_cleanup_has_separate_bounded_allowance(tmp_path):
    script = tmp_path/'cleanup.py'
    script.write_text('''import time
from sd_uploader import cli
def dispatch(args):
    try:
        time.sleep(60)
    finally:
        time.sleep(60)
cli.dispatch = dispatch
raise SystemExit(cli.main(['devices', '--timeout', '.05', '--progress', 'off']))
''')
    import time
    start = time.monotonic()
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                            env=dict(os.environ, PYTHONPATH=str(CLI.parent)), timeout=8)
    assert result.returncode == 5 and json.loads(result.stdout)['code'] == 'TIMEOUT'
    assert 5 <= time.monotonic()-start < 7


@pytest.mark.parametrize('boot_pending,ready,recovery', [
    (True, True, 'external-observation-required'),
    (False, True, 'recover'), (False, False, 'start-new-run')])
def test_result_uses_state_over_prior_failure(tmp_path, boot_pending, ready, recovery):
    old = dict(status='failed', phase='staging', device_state='uploader',
               code='CANCELLED', message='interrupted', recovery='recover')
    (tmp_path/'result.json').write_text(json.dumps(old))
    (tmp_path/'state.json').write_text(json.dumps(dict(
        ready=ready, boot_pending=boot_pending, phase='boot-observation', device_state='unknown')))
    result = call('result', '--run', str(tmp_path), isolated=True)
    body = json.loads(result.stdout)
    assert result.returncode == 0
    assert body['status'] == 'incomplete'
    assert body['phase'] == 'boot-observation' and body['device_state'] == 'unknown'
    assert body['recovery'] == recovery
    assert body['previous_failure'] == old
