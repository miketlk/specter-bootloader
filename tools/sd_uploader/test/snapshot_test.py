"""Immutable recovery snapshots retain independently auditable RAM artifacts."""
from pathlib import Path
import pytest

pytest.importorskip('elftools', reason='optional SD uploader dependency')
from .ram_image_test import built


def test_snapshot_survives_rebuilt_sources_and_artifacts(built, tmp_path, monkeypatch):
    from sd_uploader import snapshot as snapshots
    from sd_uploader import ram_image
    import json
    import shutil
    artifact = ram_image.verify_manifest(built/'ram-manifest.json')
    source = tmp_path/'source.bin'
    source.write_bytes(b'first')
    fixture = dict(board='lcd-4p3', ram_manifest=str(built/'ram-manifest.json'), starting_state=None)
    case = dict(files=[dict(path=str(source), name='specter_upgrade.bin', sha256=ram_image.sha(source))])
    frozen_fixture, frozen_case, frozen_artifact = snapshots.snapshot(fixture, case, artifact)
    try:
        source.write_bytes(b'rebuild at original path')
        assert Path(frozen_case['files'][0]['path']).read_bytes() == b'first'
        assert Path(frozen_case['files'][0]['path']).stat().st_ino != source.stat().st_ino
        assert ram_image.verify_manifest(frozen_fixture['ram_manifest']) == frozen_artifact
        # Reject reads of live application source files during snapshot audit.
        original_sha = ram_image.sha
        def no_live_sources(path):
            assert not str(path).startswith(str(ram_image.APP))
            return original_sha(path)
        monkeypatch.setattr(ram_image, 'sha', no_live_sources)
        assert ram_image.verify_manifest(frozen_fixture['ram_manifest']) == frozen_artifact
    finally:
        shutil.rmtree(Path(frozen_fixture['ram_manifest']).parent)


def test_snapshot_audit_rejects_modified_archived_input(built):
    from sd_uploader.snapshot import snapshot
    from sd_uploader import ram_image
    import shutil
    artifact = ram_image.verify_manifest(built/'ram-manifest.json')
    fixture, _, frozen = snapshot(dict(board='lcd-4p3', starting_state=None), dict(files=[]), artifact)
    try:
        relative = next(iter(frozen['audit_inputs']))
        path = Path(frozen['audit_root'])/relative
        path.chmod(0o644)
        path.write_bytes(path.read_bytes() + b'\n')
        with pytest.raises(ValueError, match='snapshot audit input hash'):
            ram_image.verify_manifest(fixture['ram_manifest'])
    finally:
        shutil.rmtree(Path(fixture['ram_manifest']).parent)


def test_recovery_accepts_json_roundtrip_of_real_snapshot(built, tmp_path, monkeypatch):
    pytest.importorskip('cbor2', reason='optional SD uploader dependency')
    pytest.importorskip('serial', reason='optional SD uploader dependency')
    from contextlib import nullcontext
    from sd_uploader import runner, ram_image
    from sd_uploader.snapshot import snapshot
    from sd_uploader.evidence import Evidence
    import json
    import shutil
    import sys
    artifact = ram_image.verify_manifest(built/'ram-manifest.json')
    fixture = dict(schema_version=2, authorized=True, board='lcd-4p3', starting_state=None,
                   preservation='current', allowed_resets=['rom-entry', 'rom-verify'],
                   bridge_serial='bridge', bridge_location='location', chip='chip', card_cid='card',
                   reset_controller='qualified-ch343', idf_python=sys.executable)
    case = dict(files=[])
    fixture, case, frozen = snapshot(fixture, case, artifact)
    evidence = Evidence(tmp_path/'run')
    for name, value in [('fixture.json',fixture),('case.json',case),('ram-manifest.json',frozen),
                        ('state.json',dict(ready=True, phase='staging', device_state='uploader'))]:
        evidence.save(name, value)
    (tmp_path/'declared.json').write_text(json.dumps(fixture))
    monkeypatch.setattr(runner, 'check_interpreter', lambda _:None)
    monkeypatch.setattr(runner, 'identify', lambda **_:dict(serial='bridge'))
    monkeypatch.setattr(runner, 'FixtureLock', lambda _:nullcontext())
    monkeypatch.setattr(runner, 'execute', lambda *_:dict(status='passed', recovered=True))
    try:
        assert runner.recover(tmp_path/'declared.json', evidence.directory)['recovered']
    finally:
        shutil.rmtree(Path(fixture['ram_manifest']).parent)
