"""Validate fixture/case declarations before any hardware action."""
import json
import re
from pathlib import Path
from .protocol import valid_name
from .ram_image import sha, verify_manifest


def load_fixture(fixture_path):
    fixture_path = Path(fixture_path).resolve()
    fixture = json.loads(fixture_path.read_text())
    required = {'schema_version', 'authorized', 'board', 'bridge_serial', 'bridge_location',
                'chip', 'card_cid', 'ram_manifest', 'idf_python', 'starting_state', 'reset_controller'}
    if not isinstance(fixture, dict):
        raise ValueError('fixture must be a JSON object')
    version = fixture.get('schema_version')
    if version == 2:
        required |= {'preservation', 'allowed_resets'}
    if set(fixture) != required or type(version) is not int or version not in (1, 2) or fixture['authorized'] is not True:
        raise ValueError('explicit authorized fixture declaration required')
    if fixture['board'] not in ('lcd-4p3', 'lcd-5'):
        raise ValueError('unsupported board')
    if not isinstance(fixture['chip'], str) or not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', fixture['chip']):
        raise ValueError('canonical lowercase chip MAC required')
    if not isinstance(fixture['card_cid'], str) or not re.fullmatch(r'[0-9a-f]{32}', fixture['card_cid']):
        raise ValueError('normalized 16-byte card CID required')
    for key in ('bridge_serial', 'bridge_location'):
        if not isinstance(fixture[key], str) or not fixture[key] or len(fixture[key]) > 128:
            raise ValueError(f'invalid {key}')
    if fixture['reset_controller'] != 'qualified-ch343':
        raise ValueError('qualified external reset/download controller required')
    if version == 2:
        if fixture['preservation'] not in ('pinned', 'current'):
            raise ValueError('unsupported preservation policy')
        resets = fixture['allowed_resets']
        if (not isinstance(resets, list) or any(not isinstance(r, str) for r in resets) or len(set(resets)) != len(resets) or
                not {'rom-entry', 'rom-verify'} <= set(resets) or
                not set(resets) <= {'rom-entry', 'rom-verify', 'normal-boot'}):
            raise ValueError('invalid fixture reset authorization')
        if fixture['preservation'] == 'current' and fixture['starting_state'] is not None:
            raise ValueError('current policy requires null starting_state')
    for key in ('ram_manifest', 'idf_python', 'starting_state'):
        if key == 'starting_state' and fixture.get('preservation') == 'current':
            continue
        if not isinstance(fixture[key], str) or not fixture[key]:
            raise ValueError(f'nonempty {key} path required')
        if fixture[key] is not None:
            fixture[key] = str((fixture_path.parent / fixture[key]).absolute())
    artifact = verify_manifest(fixture['ram_manifest'])
    if artifact['board'] != fixture['board']:
        raise ValueError('fixture and RAM image board differ')
    start = None
    if fixture.get('preservation', 'pinned') == 'pinned':
        start = json.loads(Path(fixture['starting_state']).read_text())
        if (not isinstance(start, dict) or start.get('chip') != fixture['chip'] or
                not isinstance(start.get('full_nor'), str) or
                not re.fullmatch(r'[0-9a-f]{32}', start['full_nor'])):
            raise ValueError('starting-state NOR evidence required')
    return fixture, artifact, start


def prepare(fixture_path, case_path):
    fixture, artifact, start = load_fixture(fixture_path)
    case_path = Path(case_path).resolve()
    case = json.loads(case_path.read_text())
    if start is None:
        raise ValueError('qualification cases require pinned starting state')
    if not isinstance(case, dict):
        raise ValueError('case must be a JSON object')
    required_case = {'schema_version', 'id', 'starting_state_id', 'files', 'remove',
                     'expected_candidates', 'allowed_resets', 'outcome', 'timeout', 'observation'}
    if set(case) != required_case or type(case['schema_version']) is not int or case['schema_version'] != 1:
        raise ValueError('invalid case schema')
    if case['starting_state_id'] != sha(fixture['starting_state']):
        raise ValueError('starting-state manifest identity mismatch')
    required_resets = {'rom-entry', 'rom-verify'}
    if case['outcome'] != 'stage-only':
        required_resets.add('normal-boot')
    if (not isinstance(case['allowed_resets'], list) or
            any(not isinstance(r, str) for r in case['allowed_resets']) or
            not required_resets <= set(case['allowed_resets']) or
            not set(case['allowed_resets']) <= {'rom-entry', 'rom-verify', 'normal-boot'}):
        raise ValueError('case must explicitly declare each reset boundary')
    if fixture['schema_version'] == 2 and not required_resets <= set(fixture['allowed_resets']):
        raise ValueError('fixture does not authorize requested resets')
    if case['outcome'] not in ('stage-only', 'approved-mock'):
        raise ValueError('unsupported assertion: negative/trial cases require specific loader observations')
    if type(case['timeout']) not in (int, float) or not 1 <= case['timeout'] <= 600:
        raise ValueError('case observation timeout must be 1..600 seconds')
    if not isinstance(case['files'], list) or len(case['files']) > 64:
        raise ValueError('case must declare at most 64 files')
    if not isinstance(case['remove'], list) or len(case['remove']) > 64:
        raise ValueError('case must declare at most 64 removals')
    if (not isinstance(case['expected_candidates'], list) or
            any(not valid_name(n) or not n.startswith('specter_upgrade') or
                not n.endswith('.bin') for n in case['expected_candidates'])):
        raise ValueError('invalid declared candidate set')
    names = []
    for file in case['files']:
        if (not isinstance(file, dict) or set(file) != {'path', 'name', 'sha256'} or
                not valid_name(file['name']) or not isinstance(file['path'], str) or not file['path']):
            raise ValueError('invalid case file declaration')
        file['path'] = str((case_path.parent / file['path']).resolve())
        if sha(file['path']) != file['sha256']:
            raise ValueError('fixture file hash mismatch')
        names.append(file['name'].lower())
    if len(set(names)) != len(names):
        raise ValueError('case-insensitive destination collision')
    if any(not valid_name(name) for name in case['remove']):
        raise ValueError('invalid permitted removal')
    if len({n.lower() for n in case['remove']}) != len(case['remove']):
        raise ValueError('duplicate permitted removal')
    if case['outcome'] == 'approved-mock':
        if not isinstance(case['observation'], dict) or set(case['observation']) != {'version', 'build', 'bloat'}:
            raise ValueError('approval-aware version/build observation required')
        observation = case['observation']
        if (type(observation['version']) is not int or not 0 <= observation['version'] <= 0xffffffff or
                type(observation['bloat']) is not int or not 0 <= observation['bloat'] <= 0x400000 or
                not isinstance(observation['build'], str) or not re.fullmatch(r'[0-9a-f]{64}', observation['build'])):
            raise ValueError('invalid approved-mock version/build/bloat assertion')
    return fixture, case, artifact, start
