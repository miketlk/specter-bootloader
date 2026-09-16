"""Explicit RAM-load/stage/release/ROM-verify/reset lifecycle."""
import importlib.util
import json
import time
import uuid
import traceback
from pathlib import Path
import cbor2
from .client import Client, DeviceError
from .evidence import Evidence
from .fixture import prepare, load_fixture
from .protocol import Frames
from .ram_image import ROOT, sha
from .ram_loader import RamLoader
from .transport import identify, FixtureLock, SerialTransport


def candidates(entries):
    # Match the firmware's case-sensitive specter_upgrade*.bin wildcard.
    return sorted(e['name'] for e in entries
                  if e['name'].startswith('specter_upgrade') and e['name'].endswith('.bin'))


def observe(transport, board, case, evidence):
    path = ROOT / 'platforms/esp32-p4-wifi6-touch-lcd/mock_app/tools/mock_telemetry.py'
    spec = importlib.util.spec_from_file_location('specter_mock_telemetry', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    parser = Frames()
    # Capture is active before external EN reset. This is not a power cut.
    transport.serial.reset_input_buffer()
    transport.serial.dtr = False
    transport.serial.rts = True
    time.sleep(0.1)
    transport.serial.rts = False
    evidence.record('normal-reset')
    end = time.monotonic() + remaining(case['timeout'])
    while time.monotonic() < end:
        remaining()
        for magic, payload in parser.feed(transport.read()):
            if magic != b'SPMF':
                continue
            record = cbor2.loads(payload)
            try:
                module.validate(record, board, case['observation']['bloat'], True)
                app = record['app']
                if record['approval'].get('semantic_version') != case['observation']['version'] or app.get('image_digest', b'').hex() != case['observation']['build']:
                    continue
            except (ValueError, KeyError, TypeError):
                continue
            evidence.save('mock-observation.json', record)
            return
    raise TimeoutError('no matching approved mock frame in current reset window')


from .runtime import ACTIVE, Failure, failure_result, phase, remaining
from .snapshot import snapshot


def actions(case):
    result = ['lock bridge', 'snapshot inputs', 'enter ROM and capture full NOR',
              'audit/load RAM', 'verify chip/build/card', 'preflight all destinations',
              'stage and independently verify files', 'check candidates',
              'release SD', 'enter ROM and compare complete NOR']
    if case['outcome'] != 'stage-only':
        result.append('authorized normal reset')
    return result


def run_case(fixture_path, case_path, directory, dry_run=False):
    fixture, case, artifact, start = prepare(fixture_path, case_path)
    return run_plan(fixture, case, artifact, start, directory, dry_run, fixture_path)


def stage(fixture_path, source, name=None, replace=False, after='rom', directory=None, dry_run=False):
    from .protocol import valid_name
    fixture, artifact, start = load_fixture(fixture_path)
    source = Path(source).resolve()
    name = name or source.name
    if not valid_name(name) or not name.startswith('specter_upgrade') or not name.endswith('.bin'):
        raise Failure('INVALID_NAME', 'stage requires a specter_upgrade*.bin candidate basename')
    resets = ['rom-entry', 'rom-verify'] + (['normal-boot'] if after == 'boot' else [])
    # Version 1's explicit authorization retains its historical reset policy.
    if not set(resets) <= set(fixture.get('allowed_resets', resets)):
        raise Failure('RESET_NOT_AUTHORIZED', 'Fixture does not authorize the requested reset')
    case = dict(schema_version=1, id='stage-' + uuid.uuid4().hex, stage=True,
                starting_state_id=sha(fixture['starting_state']) if start else None,
                files=[dict(path=str(source), name=name, sha256=sha(source))],
                remove=[name] if replace else [], expected_candidates=[name],
                allowed_resets=resets, outcome='boot-requested' if after == 'boot' else 'stage-only',
                timeout=30, observation={})
    return run_plan(fixture, case, artifact, start, directory, dry_run, fixture_path)


def check_interpreter(fixture):
    import sys
    import esptool
    if Path(sys.executable).absolute() != Path(fixture['idf_python']).absolute() or esptool.__version__ != '4.12.0':
        raise Failure('INTERPRETER_MISMATCH', 'Use the fixture idf_python with pinned esptool 4.12.0')


def run_plan(fixture, case, artifact, start, directory, dry_run=False, fixture_path=None):
    if dry_run:
        return dict(status='dry-run', actions=actions(case), preservation=fixture.get('preservation', 'pinned'),
                    device_state='unknown', identity_checked=False, media_checked=False,
                    destination=case['files'][0]['name'] if case.get('stage') else None)
    check_interpreter(fixture)
    endpoint = identify(serial_number=fixture['bridge_serial'], location=fixture['bridge_location'])
    with FixtureLock(endpoint['serial']):
        directory = Path(directory) if directory else ROOT / 'build/sd-uploader/runs' / (time.strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:12])
        directory = directory.absolute()
        directory.mkdir(parents=True, exist_ok=False)
        evidence = Evidence(directory)
        operation = ACTIVE.get()
        if operation:
            operation.run = directory
        state = dict(schema_version=1, phase='snapshot', device_state='unknown', sessions=[], boot_pending=False)
        evidence.save('state.json', state)
        try:
            phase('snapshot')
            fixture, case, artifact = snapshot(fixture, case, artifact)
            evidence.save('fixture.json', fixture)
            evidence.save('case.json', case)
            evidence.save('ram-manifest.json', artifact)
            evidence.save('plan.json', dict(fixture=str(Path(fixture_path).resolve()) if fixture_path else None,
                                          endpoint=endpoint, actions=actions(case)))
            # Only a complete plan is recoverable; no hardware action precedes it.
            state['ready'] = True
            evidence.save('state.json', state)
            return execute(fixture, case, artifact, start, endpoint, evidence, state)
        except BaseException as error:
            save_failure(evidence, state, error)
            raise


def save_failure(evidence, state, error):
    result, _ = failure_result(error, ACTIVE.get())
    result.update(phase=state['phase'], device_state=state['device_state'], run=str(evidence.directory))
    if state.get('ready') and not state.get('boot_pending'):
        result['recovery'] = 'recover'
    elif state.get('boot_pending'):
        result['recovery'] = 'external-observation-required'
    operation = ACTIVE.get()
    if operation:
        operation.phase = state['phase']
        operation.device_state = state['device_state']
    # Keep the original terminal error even if best-effort diagnostics cannot be saved.
    try:
        error.result = result
    except AttributeError:
        pass
    try:
        evidence.save('result.json', result)
        evidence.record('failed', **result)
        evidence.save('diagnostic.json', dict(traceback=traceback.format_exc()))
    except OSError:
        pass


def transition(evidence, state, name, device_state):
    remaining()
    state.update(phase=name, device_state=device_state)
    evidence.save('state.json', state)
    evidence.record('phase', phase=name, device_state=device_state)
    phase(name, device_state)


def preflight(client, entries, case, original, recovering, different):
    by_name = {e['name'].lower(): e for e in entries if not e['temporary']}
    permitted = {n.lower() for n in case['remove']}
    planned = {f['name'].lower() for f in case['files']}
    predicted = [e for e in entries if not e['temporary'] and e['name'].lower() not in permitted | planned]
    predicted += [dict(name=f['name']) for f in case['files']]
    if candidates(predicted) != sorted(case['expected_candidates']):
        raise Failure('CANDIDATE_CONFLICT', 'Unexpected upgrade candidate; no media changes made', 4)
    for name in permitted | planned:
        if name in by_name and by_name[name]['type'] != 'file':
            raise Failure('DEST_CONFLICT', 'Destination/removal is not a regular file', 4)
    originals = {e['name'].lower() for e in original}
    uploads, removals = [], list(case['remove']) if not case.get('stage') else []
    for file in case['files']:
        name = file['name'].lower()
        entry = by_name.get(name)
        if not entry:
            uploads.append(file)
            continue
        can_replace = name in permitted or (recovering and name not in originals)
        # Recovery and convenience staging verify existing bytes, never trust a receipt alone.
        if case.get('stage') or recovering:
            if entry['size'] == Path(file['path']).stat().st_size and name not in different:
                try:
                    client.verify(file['path'], entry['name'])
                    if entry['name'] == file['name']:
                        removals = [n for n in removals if n.lower() != name]
                        continue
                    if not can_replace:
                        raise Failure('DEST_EXISTS', 'Existing FAT name has different candidate spelling; use --replace', 4)
                except DeviceError as error:
                    if error.status != 11:
                        raise
                    if not can_replace:
                        raise Failure('DEST_EXISTS', 'Destination exists; use --replace for this name.', 4) from error
                    different.add(name)
                    # A mismatched VERIFY leaves this wire-v1 service UNCERTAIN.
                    return None
            if not can_replace:
                raise Failure('DEST_EXISTS', 'Destination exists; use --replace for this name.', 4)
            if name not in {n.lower() for n in removals}:
                removals.append(entry['name'])
        elif name not in permitted:
            raise Failure('DEST_EXISTS', 'Destination exists without an explicit removal', 4)
        uploads.append(file)
    return removals, uploads


def nor_identity(probe):
    # Regional hashes are diagnostics based on the current host partition layout.
    # Full-capacity NOR and every device identity/security field remain invariant.
    return {key: value for key, value in probe.items() if key != 'regions'}


def execute(fixture, case, artifact, start, endpoint, evidence, state, recovering=False):
    recorded = None
    if recovering:
        try:
            recorded = json.loads((evidence.directory / 'nor-before.json').read_text())
            if not isinstance(recorded, dict) or any(
                    not isinstance(recorded.get(key), str) or not recorded[key]
                    for key in ('chip', 'full_nor')):
                recorded = None
        except (FileNotFoundError, ValueError):
            pass
        if recorded is None and state.get('media_started'):
            raise Failure('EVIDENCE_INCOMPLETE', 'Missing or corrupt NOR evidence after possible media access', 4)
    loader = RamLoader(fixture['idf_python'], endpoint, evidence)
    transition(evidence, state, 'starting-state', 'unknown')
    current = loader.inspect('nor-recovery.json' if recovering else 'nor-before.json', reset=True)
    transition(evidence, state, 'starting-state', 'rom')
    if current['chip'] != fixture['chip']:
        raise Failure('IDENTITY_MISMATCH', 'ROM chip differs from authorized fixture', 3)
    if (recorded is not None and nor_identity(current) != nor_identity(recorded)) or (start and current['full_nor'] != start['full_nor']):
        raise Failure('NOR_MISMATCH', 'Installed NOR differs from declared starting state', 4)
    if recorded is None:
        evidence.save('nor-before.json', current)
    before = recorded or current
    original_path = evidence.directory / 'card-before.json'
    original = json.loads(original_path.read_text()) if recovering and original_path.exists() else None
    if recovering and original is None and state.get('media_started'):
        raise Failure('EVIDENCE_INCOMPLETE', 'Missing card evidence after possible media access', 4)
    different = set()
    while True:
        transition(evidence, state, 'ram-load', 'unknown')
        loader.load(fixture['ram_manifest'], fixture['chip'])
        with loader.transport as transport:
            transition(evidence, state, 'identity', 'uploader')
            client = Client(transport, fixture['board'], evidence=evidence)
            try:
                hello = client.hello(dict(chip=fixture['chip'], card_cid=fixture['card_cid'], build=artifact['build']))
            except ValueError as error:
                raise Failure('IDENTITY_MISMATCH', str(error), 3) from error
            evidence.save('hello.json', hello)
            old_sessions = list(state['sessions'])
            state['sessions'].append(client.session.hex())
            evidence.save('state.json', state)
            transition(evidence, state, 'preflight', 'uploader')
            entries = client.listing()
            if any(Path(file['path']).stat().st_size > hello.get('file_max', 2147483647) for file in case['files']):
                raise Failure('FILE_TOO_LARGE', 'Input exceeds the service file limit', 4)
            if original is None:
                original = entries
                evidence.save('card-before.json', original)
            reserved = {f'_sdu_{session}.part' for session in old_sessions}
            leftovers = [e['name'] for e in entries if e['temporary']]
            if any(name not in reserved for name in leftovers) or (leftovers and not recovering):
                raise Failure('TEMP_CONFLICT', 'Unowned reserved leftovers require explicit card recovery', 4)
            decision = preflight(client, entries, case, original, recovering, different)
            if decision is not None:
                removals, uploads = decision
                state['media_started'] = True
                transition(evidence, state, 'staging', 'uploader')
                for name in leftovers:
                    client.request('CLEANUP', {'name': name})
                for name in removals:
                    client.request('REMOVE', {'name': name})
                for file in uploads:
                    client.upload(file['path'], file['name'], expected_sha256=file['sha256'])
                final = client.listing()
                evidence.save('card-after.json', final)
                if candidates(final) != sorted(case['expected_candidates']):
                    raise Failure('CANDIDATE_CONFLICT', 'Final candidate set differs from declared fixture', 4)
                transition(evidence, state, 'release', 'uploader')
                if client.request('RELEASE')['state'] != 'RELEASED':
                    raise Failure('RELEASE_FAILED', 'SD release not confirmed', 4)
                break
        transition(evidence, state, 'replacement-reload', 'unknown')
        probe = loader.inspect('nor-reload-identity.json', reset=True, hashes=False)
        if probe['chip'] != fixture['chip']:
            raise Failure('IDENTITY_MISMATCH', 'ROM chip changed before replacement', 3)
    transition(evidence, state, 'flash-preservation', 'unknown')
    after = loader.inspect('nor-after-recovery.json' if recovering else 'nor-after.json', reset=True)
    transition(evidence, state, 'flash-preservation', 'rom')
    if nor_identity(after) != nor_identity(before):
        raise Failure('NOR_MISMATCH', 'NOR/identity changed during staging; no automatic repair or normal boot', 4)
    if case['outcome'] != 'stage-only':
        # Persist intent BEFORE opening capture or touching EN. Never replay this boundary.
        state['boot_pending'] = True
        transition(evidence, state, 'boot-observation', 'unknown')
        with SerialTransport(endpoint['port'], evidence) as transport:
            if case['outcome'] == 'approved-mock':
                observe(transport, fixture['board'], case, evidence)
            else:
                normal_boot(transport, evidence)
        state['device_state'] = 'boot-requested' if case['outcome'] == 'boot-requested' else 'approved-mock'
    result = dict(status='passed', case=case['id'], outcome=case['outcome'],
                  run=str(evidence.directory), preservation=fixture.get('preservation', 'pinned'),
                  device_state=state['device_state'])
    if case.get('stage'):
        file = case['files'][0]
        result.update(destination=file['name'], length=Path(file['path']).stat().st_size,
                      sha256=file['sha256'], no_op=not uploads and not removals)
    if recovering:
        result['recovered'] = True
    state['complete'] = True
    state['result'] = result
    transition(evidence, state, 'complete', state['device_state'])
    evidence.save('result.json', result)
    evidence.record('complete', **result)
    return result


def normal_boot(transport, evidence):
    remaining()
    transport.serial.dtr = False
    transport.serial.rts = True
    time.sleep(min(.1, remaining(.1)))
    remaining()
    transport.serial.rts = False
    evidence.record('normal-reset')


def recover(fixture_path, directory):
    directory = Path(directory).absolute()
    # Never create evidence or touch a device for an unknown run.
    operation = ACTIVE.get()
    if operation:
        operation.run = directory
    if not (directory / 'state.json').exists():
        raise Failure('EVIDENCE_INCOMPLETE', 'Run has no durable lifecycle state; manual reconciliation required', 4)
    state = json.loads((directory / 'state.json').read_text())
    if operation:
        operation.phase = state.get('phase', 'preflight')
        operation.device_state = state.get('device_state', 'unknown')
    if not state.get('ready'):
        raise Failure('EVIDENCE_INCOMPLETE', 'Snapshot plan incomplete; start a new run', 4)
    if state.get('complete'):
        return state['result']
    if state.get('boot_pending'):
        raise Failure('HANDOFF_REQUIRED', 'Normal reset may have occurred; external observation required', 4)
    fixture = json.loads((directory / 'fixture.json').read_text())
    case = json.loads((directory / 'case.json').read_text())
    declared = json.loads(Path(fixture_path).read_text())
    if declared.get('authorized') is not True:
        raise Failure('CONFIG', 'Recovery fixture must remain explicitly authorized')
    for key in ('schema_version', 'chip', 'card_cid', 'bridge_serial', 'bridge_location', 'board',
                'reset_controller', 'preservation', 'allowed_resets'):
        if declared.get(key) != fixture.get(key):
            raise Failure('IDENTITY_MISMATCH', 'Recovery fixture identity/policy differs from interrupted run', 3)
    if str((Path(fixture_path).resolve().parent / declared['idf_python']).absolute()) != fixture['idf_python']:
        raise Failure('INTERPRETER_MISMATCH', 'Recovery interpreter differs from recorded fixture')
    from .ram_image import verify_manifest
    artifact = verify_manifest(fixture['ram_manifest'])
    archived = json.loads((directory / 'ram-manifest.json').read_text())
    if json.loads(json.dumps(artifact)) != archived:
        raise Failure('ARTIFACT_MISMATCH', 'Recovery requires the same checked RAM image')
    for file in case['files']:
        if sha(file['path']) != file['sha256']:
            raise Failure('ARTIFACT_MISMATCH', 'Recovery input snapshot hash mismatch')
    start = json.loads(Path(fixture['starting_state']).read_text()) if fixture.get('starting_state') else None
    check_interpreter(fixture)
    endpoint = identify(serial_number=fixture['bridge_serial'], location=fixture['bridge_location'])
    with FixtureLock(endpoint['serial']):
        # Re-read under ownership: another recovery may have finished since preflight.
        state = json.loads((directory / 'state.json').read_text())
        if state.get('complete'):
            return state['result']
        if state.get('boot_pending'):
            raise Failure('HANDOFF_REQUIRED', 'Normal reset may have occurred; external observation required', 4)
        evidence = Evidence(directory)
        operation = ACTIVE.get()
        if operation:
            operation.run = directory
        try:
            return execute(fixture, case, artifact, start, endpoint, evidence, state, True)
        except BaseException as error:
            save_failure(evidence, state, error)
            raise
