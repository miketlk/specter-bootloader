"""Summarize uploader timing evidence without retaining payloads or per-chunk data."""
import json
from pathlib import Path


def report(directory):
    totals = {}
    uploads = []
    phases = {}
    pending = {}
    baud = None
    for line in (Path(directory) / 'journal.jsonl').open():
        record = json.loads(line)
        action = record['action']
        if action == 'timing':
            target = totals.setdefault(record['opcode'], {'count': 0})
            target['count'] += 1
            for key in ('total_s', 'encode_s', 'serial_write_s',
                        'transmitted_bytes', 'retries', 'device_us', 'frame_us', 'storage_write_us'):
                target[key] = target.get(key, 0) + record.get(key, 0)
        elif action == 'upload-timing':
            # A bounded summary, even for long-running benchmark journals.
            if len(uploads) >= 256:
                raise ValueError('profile supports at most 256 uploads per journal')
            uploads.append({key: record[key] for key in ('name', 'length', 'total_s', 'local_hash_s')})
        elif action in ('ram-load', 'rom-command'):
            pending[action] = record['time_ns']
        elif action in ('ram-executing', 'rom-result'):
            start = 'ram-load' if action == 'ram-executing' else 'rom-command'
            if start in pending:
                phases[start + '_s'] = phases.get(start + '_s', 0) + (record['time_ns'] - pending.pop(start)) / 1e9
        elif action == 'response' and record['message']['opcode'] == 'HELLO':
            baud = record['message']['body'].get('baud')
        elif action == 'response' and record['message']['opcode'] == 'COMMIT':
            body = record['message']['body']
            for key in ('flush_us', 'temp_verify_us', 'publish_us', 'final_verify_us', 'read_us', 'hash_us'):
                if key in body:
                    phases[key] = phases.get(key, 0) + body[key]
    for entry in uploads:
        entry['bytes_per_second'] = entry['length'] / entry['total_s'] if entry['total_s'] else 0
    for target in totals.values():
        target['host_and_wait_s'] = max(0, target['total_s'] - target['encode_s'] - target['serial_write_s'] - (target['device_us'] + target['frame_us']) / 1e6)
        if baud:
            target['minimum_tx_wire_s'] = target['transmitted_bytes'] * 10 / baud
    return dict(uart_baud=baud, commands=totals, uploads=uploads, phases=phases,
                note='Host/wait is residual latency, including serial receive, scheduling and evidence writes. Device framing can overlap transmission; timings are not an exclusive CPU breakdown. Wire estimates cover requests only, using 8N1. Device operation time excludes response transmission.')
