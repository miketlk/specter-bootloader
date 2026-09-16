"""Timing summaries distinguish device work, serial writes and residual latency."""
import json
from sd_uploader.profile import report


def test_timing_aggregation(tmp_path):
    records = [dict(action='timing', opcode='WRITE', total_s=.5, encode_s=.01,
                    serial_write_s=.3, transmitted_bytes=4200, retries=0, device_us=20000)] * 2
    records += [dict(action='upload-timing',name='fixture.dat',length=8192,total_s=1.1,local_hash_s=.001)]
    (tmp_path/'journal.jsonl').write_text('\n'.join(map(json.dumps,records)))
    result=report(tmp_path)
    assert result['commands']['WRITE']['count']==2
    assert abs(result['commands']['WRITE']['host_and_wait_s']-.34)<1e-10
    assert result['uploads'][0]['bytes_per_second']==8192/1.1
