"""Explicit RAM-load/stage/release/ROM-verify/reset lifecycle."""
import importlib.util
import json
import time
from pathlib import Path
import cbor2
from .client import Client
from .evidence import Evidence
from .fixture import prepare
from .protocol import Frames
from .ram_image import ROOT
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
    end = time.monotonic() + case['timeout']
    while time.monotonic() < end:
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


def run_case(fixture_path, case_path, directory, dry_run=False):
    fixture, case, artifact, start = prepare(fixture_path, case_path)
    actions = ['lock bridge', 'enter ROM download', 'verify security/chip/full NOR starting state',
               'audit and load internal RAM image without stub', 'verify HELLO board/build/card',
               'list card', *[f'remove {n}' for n in case['remove']],
               *[f"upload/readback {f['name']}" for f in case['files']],
               'compare candidate set', 'release SD', 'enter ROM and compare complete NOR']
    if case['outcome'] != 'stage-only':
        actions += ['arm telemetry capture', 'normal external reset', 'assert approved mock identity']
    if dry_run:
        return dict(status='dry-run', actions=actions)
    endpoint = identify(serial_number=fixture['bridge_serial'], location=fixture['bridge_location'])
    with FixtureLock(endpoint['serial']):
        # Atomic reservation: interrupted runs belong exclusively to recovery.
        Path(directory).mkdir(parents=True, exist_ok=False)
        evidence = Evidence(directory)
        evidence.save('fixture.json', fixture)
        evidence.save('case.json', case)
        evidence.save('ram-manifest.json', artifact)
        evidence.record('start', endpoint=endpoint, actions=actions)
        phase = 'preflight'
        try:
            loader = RamLoader(fixture['idf_python'], endpoint, evidence)
            phase = 'starting-state'
            before = loader.inspect('nor-before.json', reset=True)
            if before['chip'] != fixture['chip'] or before['full_nor'] != start['full_nor']:
                raise ValueError('installed NOR differs from declared starting state')
            phase = 'ram-load'
            loader.load(fixture['ram_manifest'], fixture['chip'])
            phase = 'staging'
            with loader.transport as transport:
                client = Client(transport, fixture['board'], evidence=evidence)
                hello = client.hello(dict(chip=fixture['chip'], card_cid=fixture['card_cid'], build=artifact['build']))
                evidence.save('hello.json', hello)
                before_entries = client.listing()
                evidence.save('card-before.json', before_entries)
                if any(e['temporary'] for e in before_entries):
                    raise ValueError('reserved leftovers require explicit card recovery; no normal boot')
                for name in case['remove']:
                    client.request('REMOVE', {'name': name})
                for file in case['files']:
                    client.upload(file['path'], file['name'], expected_sha256=file['sha256'])
                entries = client.listing()
                evidence.save('card-after.json', entries)
                if candidates(entries) != sorted(case['expected_candidates']):
                    raise ValueError('final upgrade candidate set differs from declared fixture')
                phase = 'release'
                if client.request('RELEASE')['state'] != 'RELEASED':
                    raise ValueError('SD release not confirmed')
            phase = 'flash-preservation'
            after = loader.inspect('nor-after.json', reset=True)
            if after != before:
                raise ValueError('NOR/identity changed during staging; no automatic repair or normal boot')
            if case['outcome'] != 'stage-only':
                phase = 'boot-observation'
                with SerialTransport(endpoint['port'], evidence) as transport:
                    observe(transport, fixture['board'], case, evidence)
            result = dict(status='passed', case=case['id'], outcome=case['outcome'])
        except Exception as error:
            result = dict(status='failed', phase=phase, error=str(error))
            evidence.save('result.json', result)
            evidence.record('failed', **result)
            raise
        evidence.save('result.json', result)
        evidence.record('complete', **result)
        return result


def recover(fixture_path, directory):
    """Recreate the entire interrupted fixture after unchanged-NOR verification."""
    evidence = Evidence(directory)
    declared = json.loads(Path(fixture_path).read_text())
    fixture, case, artifact, _ = prepare(evidence.directory/'fixture.json',
                                        evidence.directory/'case.json')
    if declared.get('authorized') is not True:
        raise ValueError('recovery fixture must remain explicitly authorized')
    for key in ('chip', 'card_cid', 'bridge_serial', 'bridge_location', 'board'):
        if declared[key] != fixture[key]:
            raise ValueError('recovery fixture identity differs from interrupted run')
    archived = json.loads((evidence.directory/'ram-manifest.json').read_text())
    if archived['image_sha256'] != artifact['image_sha256']:
        raise ValueError('recovery requires the same checked RAM image as the interrupted run')
    before = json.loads((evidence.directory/'nor-before.json').read_text())
    original = json.loads((evidence.directory/'card-before.json').read_text())
    original_names = {entry['name'].lower() for entry in original}
    permitted = {name.lower() for name in case['remove']}
    endpoint = identify(serial_number=fixture['bridge_serial'], location=fixture['bridge_location'])
    phase = 'recovery-preflight'
    try:
        with FixtureLock(endpoint['serial']):
            loader = RamLoader(fixture['idf_python'], endpoint, evidence)
            current = loader.inspect('nor-recovery.json', reset=True)
            if current != before:
                raise ValueError('recovery NOR mismatch; never restore baseline')
            phase = 'recovery-staging'
            loader.load(fixture['ram_manifest'], fixture['chip'])
            with loader.transport as transport:
                client = Client(transport, fixture['board'], evidence=evidence)
                client.hello(dict(chip=fixture['chip'], card_cid=fixture['card_cid'], build=artifact['build']))
                entries = client.listing()
                for entry in entries:
                    if entry['temporary']:
                        client.request('CLEANUP', {'name':entry['name']})
                existing = {entry['name'].lower() for entry in entries if not entry['temporary']}
                for name in case['remove']:
                    client.request('REMOVE', {'name':name})
                    existing.discard(name.lower())
                for file in case['files']:
                    name = file['name']
                    if name.lower() in existing:
                        if name.lower() in original_names and name.lower() not in permitted:
                            raise ValueError('recovery would overwrite an unrelated pre-existing destination')
                        client.request('REMOVE', {'name':name})
                    client.upload(file['path'], name, expected_sha256=file['sha256'])
                final = client.listing()
                if candidates(final) != sorted(case['expected_candidates']):
                    raise ValueError('recovery candidate set differs from declared fixture')
                evidence.save('card-recovered.json', final)
                if client.request('RELEASE')['state'] != 'RELEASED':
                    raise ValueError('recovery release not confirmed')
            phase = 'recovery-flash-preservation'
            if loader.inspect('nor-after-recovery.json', reset=True) != before:
                raise ValueError('NOR changed during recovery; no normal boot or repair')
            if case['outcome'] != 'stage-only':
                phase = 'recovery-boot-observation'
                with SerialTransport(endpoint['port'], evidence) as transport:
                    observe(transport, fixture['board'], case, evidence)
        result = dict(status='passed', case=case['id'], outcome=case['outcome'], recovered=True)
        evidence.save('recovery-result.json', result)
        evidence.record('recovered', **result)
        return result
    except Exception as error:
        evidence.save('recovery-result.json', dict(status='failed',phase=phase,error=str(error)))
        raise
