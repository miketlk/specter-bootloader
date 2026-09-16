"""Stage microSD fixtures through the standalone ESP32-P4 RAM uploader."""
import argparse
import importlib
import json
import os
import sys
import traceback
from contextlib import redirect_stdout
from pathlib import Path
from .runtime import Failure, Operation, failure_result, phase


def parser():
    parser = Parser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('status', 'list', 'upload', 'verify', 'remove', 'abort', 'release'):
        descriptions = {'upload': 'upload to an already running RAM service',
                        'release': 'release SD ownership; does not boot the device'}
        command = commands.add_parser(name, help=descriptions.get(name, name + ' on a running RAM service'))
        presentation(command)
        if name == 'list':
            command.add_argument('--candidates', action='store_true')
            command.add_argument('--cursor', type=int, default=0)
            command.add_argument('--pages', type=int, default=16, help='bounded wire pages (1..8192)')
        command.add_argument('--port', required=True)
        command.add_argument('--baud', type=int, choices=(115200,460800,921600,2000000,3000000,4000000),
                             default=4000000, help='UART baud (default: 4000000); must match the RAM image')
        command.add_argument('--board', choices=('lcd-4p3','lcd-5'), required=True)
        command.add_argument('--session', required=True, help='host-selected 32 hex digits; reuse until board reset')
        command.add_argument('--identity', type=Path, help='expected HELLO JSON (required for media mutations)')
        command.add_argument('--save-identity', type=Path, help='save verified HELLO identity for subsequent commands')
        command.add_argument('--evidence', type=Path)
        if name in ('upload','verify'):
            command.add_argument('file', type=Path)
            command.add_argument('--name', required=True)
        if name == 'remove':
            command.add_argument('name')
        if name == 'upload':
            command.add_argument('--chunk-size', type=int, help='at most the HELLO chunk_max')
    command = commands.add_parser('run-case', help='run an explicit pinned qualification case')
    command.add_argument('--fixture', type=Path, default=os.environ.get('SD_UPLOADER_FIXTURE'))
    command.add_argument('--case', required=True, type=Path)
    command.add_argument('--run', required=True, type=Path)
    command.add_argument('--dry-run', action='store_true')
    command = commands.add_parser('recover', help='reconcile an interrupted run from immutable snapshots')
    command.add_argument('--fixture', type=Path, default=os.environ.get('SD_UPLOADER_FIXTURE'))
    command.add_argument('--run', required=True, type=Path)
    command = commands.add_parser('profile', help='summarize timing evidence; no device access')
    command.add_argument('--run', required=True, type=Path)
    for name in ('stage', 'doctor'):
        command = commands.add_parser(name, help='stage one candidate through the full RAM lifecycle' if name == 'stage' else 'offline dependencies, manifest and USB enumeration; no resets')
        command.add_argument('--fixture', type=Path, default=os.environ.get('SD_UPLOADER_FIXTURE'))
        if name == 'stage':
            command.add_argument('file', type=Path)
            command.add_argument('--name')
            command.add_argument('--replace', action='store_true')
            command.add_argument('--after', choices=('rom', 'boot'), default='rom')
            command.add_argument('--run', type=Path)
            command.add_argument('--dry-run', action='store_true')
    commands.add_parser('devices', help='enumerate supported bridges without opening serial')
    command = commands.add_parser('result', help='read compact durable run state; no device access')
    command.add_argument('--run', type=Path, required=True)
    for name, command in commands.choices.items():
        if name not in ('status', 'list', 'upload', 'verify', 'remove', 'abort', 'release'):
            presentation(command)
    return parser


def dispatch(args):
    if args.command in ('stage', 'doctor', 'run-case', 'recover') and not args.fixture:
        raise Failure('CONFIG', '--fixture or SD_UPLOADER_FIXTURE is required')
    if args.command == 'devices':
        from .transport import devices
        return dict(devices=devices())
    if args.command == 'doctor':
        return doctor(args.fixture)
    if args.command == 'stage':
        from .runner import stage
        return stage(args.fixture, args.file, args.name, args.replace, args.after, args.run, args.dry_run)
    if args.command == 'result':
        state = json.loads((args.run / 'state.json').read_text())
        if state.get('complete'):
            return state['result']
        result = dict(status='incomplete', phase=state['phase'], device_state=state['device_state'],
                      run=str(args.run), recovery='external-observation-required' if state.get('boot_pending') else
                      ('recover' if state.get('ready') else 'start-new-run'))
        if (args.run / 'result.json').exists():
            result['previous_failure'] = json.loads((args.run / 'result.json').read_text())
        return result
    if args.command == 'profile':
        from .profile import report
        return report(args.run)
    if args.command == 'run-case':
        from .runner import run_case
        return run_case(args.fixture,args.case,args.run,args.dry_run)
    if args.command == 'recover':
        from .runner import recover
        return recover(args.fixture,args.run)
    from .client import Client
    from .evidence import Evidence, json_value
    from .transport import identify, FixtureLock, SerialTransport
    if args.command in ('upload','remove','abort','release') and not args.identity:
        raise Failure('USAGE', '--identity is required before media mutation')
    expected = json.loads(args.identity.read_text()) if args.identity else None
    session = bytes.fromhex(args.session)
    if len(session) != 16 or session == bytes(16):
        raise Failure('USAGE', '--session requires 32 nonzero hex digits')
    if expected is not None and (not isinstance(expected,dict) or
            any(not isinstance(expected.get(key),str) or not expected[key] for key in ('chip','card_cid','build'))):
        raise Failure('USAGE', '--identity must contain chip, card_cid and build strings')
    if args.command == 'list' and (not 0 <= args.cursor <= 65536 or not 1 <= args.pages <= 8192):
        raise Failure('USAGE', 'cursor must be 0..65536 and pages 1..8192')
    endpoint = identify(port=args.port)
    evidence = Evidence(args.evidence) if args.evidence else None
    with FixtureLock(endpoint['serial']), SerialTransport(args.port,evidence,args.baud) as transport:
        phase('identity', 'unknown')
        client = Client(transport,args.board,session,evidence)
        hello = client.hello(expected)
        phase(args.command, 'uploader')
        if args.save_identity:
            args.save_identity.write_text(json.dumps(hello,indent=2,default=json_value)+'\n')
        if args.command == 'status':
            return client.request('STATUS')
        if args.command == 'list':
            cursor, entries = args.cursor, []
            for _ in range(args.pages):
                page = client.request('LIST', {'cursor': cursor})
                entries.extend(e for e in page['entries'] if not args.candidates or
                               (e['name'].startswith('specter_upgrade') and e['name'].endswith('.bin')))
                next_cursor = page['next_cursor']
                if next_cursor and not cursor < next_cursor <= 65536:
                    raise Failure('DEVICE_ERROR', 'Invalid listing continuation', 4)
                cursor = next_cursor
                if not cursor:
                    break
            return dict(entries=entries, truncated=bool(cursor), next_cursor=cursor)
        if args.command == 'upload':
            return client.upload(args.file,args.name,chunk_size=args.chunk_size)
        if args.command == 'verify':
            return client.verify(args.file,args.name)
        return client.request(args.command.upper(), {'name':args.name} if args.command == 'remove' else {})


def doctor(path):
    issues = []
    missing = []
    for name in ('cbor2', 'elftools', 'serial', 'esptool'):
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(name)
    if missing:
        issues.append(dict(code='DEPENDENCY_MISSING', message=f"Missing {', '.join(missing)}; install tools/requirements-sd-uploader.txt in the declared IDF Python"))
    endpoint = None
    if not issues:
        try:
            from .fixture import load_fixture
            from .runner import check_interpreter
            from .transport import identify
            fixture, _, _ = load_fixture(path)
            check_interpreter(fixture)
            endpoint = identify(serial_number=fixture['bridge_serial'], location=fixture['bridge_location'])
        except Exception as error:
            item, _ = failure_result(error)
            issues.append(dict(code=item['code'], message=item['message']))
    if issues:
        error = Failure('DOCTOR_FAILED', 'Resolve the reported host/configuration issues')
        error.result = dict(schema_version=1, status='failed', code=error.code, phase='preflight',
                            message=str(error), device_state='unknown', issues=issues,
                            identity_checked=False, media_checked=False)
        raise error
    return dict(status='passed', endpoint=endpoint, identity_checked=False, media_checked=False)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Failure('USAGE', message)


def presentation(command):
    command.add_argument('--format', choices=('json', 'text'), default='json')
    command.add_argument('--verbose', action='store_true')
    command.add_argument('--timeout', type=float, default=900, help='overall seconds, default 900; observation timeout remains separate')
    command.add_argument('--progress', choices=('off', 'auto', 'json'), default='auto')


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    output_format = 'text' if '--format=text' in argv or any(
        argv[i:i + 2] == ['--format', 'text'] for i in range(len(argv))) else 'json'
    operation = None
    verbose = '--verbose' in argv
    try:
        args = parser().parse_args(argv)
        output_format = args.format
        operation = Operation(args.timeout, args.progress)
        with operation, redirect_stdout(sys.stderr):
            result = dispatch(args)
        result = dict({'schema_version': 1, 'status': 'passed'}, **result)
        exit_code = 0
    except (Exception, KeyboardInterrupt) as error:
        result, exit_code = failure_result(error, operation)
        result = getattr(error, 'result', result)
        if verbose:
            traceback.print_exc(file=sys.stderr)
    from .evidence import json_value
    if output_format == 'json':
        print(json.dumps(result, default=json_value, separators=(',', ':')))
    else:
        if result.get('status') == 'failed':
            detail = '; '.join(issue['message'] for issue in result.get('issues', []))
            print(f"failed [{result['code']}] {result['phase']}: {result['message']}" +
                  (f"; {detail}" if detail else '') +
                  (f" (run: {result['run']})" if result.get('run') else '') +
                  (f"; recovery: {result['recovery']}" if result.get('recovery') else ''))
        else:
            print(' '.join(f'{key}={json.dumps(value, default=json_value)}' for key, value in result.items() if key != 'schema_version'))
    return exit_code
