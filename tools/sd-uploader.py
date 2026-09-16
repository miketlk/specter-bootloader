#!/usr/bin/env python3
"""Stage microSD fixtures through the standalone ESP32-P4 RAM uploader."""
import argparse
import json
import sys
from pathlib import Path
from sd_uploader.client import Client
from sd_uploader.evidence import Evidence, json_value
from sd_uploader.transport import identify, FixtureLock, SerialTransport


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('status', 'list', 'upload', 'verify', 'remove', 'abort', 'release'):
        command = commands.add_parser(name)
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
    command = commands.add_parser('run-case')
    command.add_argument('--fixture', required=True, type=Path)
    command.add_argument('--case', required=True, type=Path)
    command.add_argument('--run', required=True, type=Path)
    command.add_argument('--dry-run', action='store_true')
    command = commands.add_parser('recover')
    command.add_argument('--fixture', required=True, type=Path)
    command.add_argument('--run', required=True, type=Path)
    command = commands.add_parser('profile', help='summarize timing evidence; no device access')
    command.add_argument('--run', required=True, type=Path)
    args = parser.parse_args()
    if args.command == 'profile':
        from sd_uploader.profile import report
        return report(args.run)
    if args.command == 'run-case':
        from sd_uploader.runner import run_case
        return run_case(args.fixture,args.case,args.run,args.dry_run)
    if args.command == 'recover':
        from sd_uploader.runner import recover
        return recover(args.fixture,args.run)
    if args.command in ('upload','remove','abort','release') and not args.identity:
        parser.error('--identity is required before media mutation')
    expected = json.loads(args.identity.read_text()) if args.identity else None
    session = bytes.fromhex(args.session)
    if len(session) != 16 or session == bytes(16):
        parser.error('--session requires 32 nonzero hex digits')
    if expected is not None and (not isinstance(expected,dict) or
            any(not isinstance(expected.get(key),str) or not expected[key] for key in ('chip','card_cid','build'))):
        parser.error('--identity must contain chip, card_cid and build strings')
    endpoint = identify(port=args.port)
    evidence = Evidence(args.evidence) if args.evidence else None
    with FixtureLock(endpoint['serial']), SerialTransport(args.port,evidence,args.baud) as transport:
        client = Client(transport,args.board,session,evidence)
        hello = client.hello(expected)
        if args.save_identity:
            args.save_identity.write_text(json.dumps(hello,indent=2,default=json_value)+'\n')
        if args.command == 'status':
            return dict(hello=hello, **client.request('STATUS'))
        if args.command == 'list':
            return dict(hello=hello,entries=client.listing())
        if args.command == 'upload':
            return client.upload(args.file,args.name,chunk_size=args.chunk_size)
        if args.command == 'verify':
            return client.verify(args.file,args.name)
        return client.request(args.command.upper(), {'name':args.name} if args.command == 'remove' else {})


if __name__ == '__main__':
    try:
        print(json.dumps(main(), indent=2, default=json_value))
    except Exception as error:
        print(json.dumps(dict(status='failed',error=str(error))),file=sys.stderr)
        raise SystemExit(1)
