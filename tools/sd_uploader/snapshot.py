"""Independent, hashed copies of every mutable recovery input."""
import copy
import json
import os
from pathlib import Path
import shutil
import uuid
from .ram_image import ROOT, APP, audit, sha, verify_manifest


def snapshot(fixture, case, artifact):
    directory = ROOT / 'build/sd-uploader/artifacts' / uuid.uuid4().hex
    directory.mkdir(parents=True)
    fixture, case = copy.deepcopy(fixture), copy.deepcopy(case)

    def freeze(source, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        with target.open('rb') as stream:
            os.fsync(stream.fileno())
        target.chmod(0o444)
        return str(target)

    inputs = [p for p in sorted((APP / 'main').glob('*')) if p.suffix in ('.c', '.h')]
    inputs += [APP.parent / 'lcd-4p3/board_config.h', APP.parent / 'lcd-5/board_config.h',
               APP / 'dependencies.lock', ROOT / 'third_party/esp-idf/components/esp_system/ld/esp32p4/memory.ld.in']
    audit_root = directory / 'audit'
    hashes = {}
    for source in inputs:
        relative = str(source.relative_to(ROOT))
        archived = Path(artifact.get('audit_root', ROOT)) / relative
        hashes[relative] = sha(freeze(archived, audit_root / relative))
    for key in ('elf', 'image', 'sdkconfig'):
        freeze(artifact[key], directory / key)
    frozen = audit(directory / 'elf', directory / 'image', directory / 'sdkconfig',
                   fixture['board'], audit_root)
    paths = {'elf', 'image', 'sdkconfig', 'audit_root', 'audit_inputs'}
    frozen_values = {key: value for key, value in frozen.items() if key not in paths}
    original_values = {key: value for key, value in artifact.items() if key not in paths}
    if json.loads(json.dumps(frozen_values)) != json.loads(json.dumps(original_values)):
        raise ValueError('RAM artifacts changed while snapshotting')
    frozen.update(audit_root=str(audit_root), audit_inputs=hashes)
    manifest = directory / 'ram-manifest.json'
    with manifest.open('w') as stream:
        stream.write(json.dumps(frozen, indent=2) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    manifest.chmod(0o444)
    fixture['ram_manifest'] = str(manifest)
    if fixture.get('starting_state'):
        fixture['starting_state'] = freeze(fixture['starting_state'], directory / 'starting-state.json')
        if sha(fixture['starting_state']) != case['starting_state_id']:
            raise ValueError('starting-state input changed while snapshotting')
    for index, file in enumerate(case['files']):
        file['path'] = freeze(file['path'], directory / f'input-{index}.bin')
        if sha(file['path']) != file['sha256']:
            raise ValueError('input changed while snapshotting')
    verify_manifest(manifest)
    # File contents and directory entries must survive interruption independently.
    directories = [p for p in directory.rglob('*') if p.is_dir()]
    directories += [directory, directory.parent, directory.parent.parent]
    for path in sorted(directories, key=lambda p: len(p.parts), reverse=True):
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return fixture, case, frozen
