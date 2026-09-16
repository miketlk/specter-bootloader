"""Fixture preflight rejects missing authorization, identity, and reset evidence."""
import json
from pathlib import Path
import pytest

pytest.importorskip('cbor2', reason='optional SD uploader dependency')
pytest.importorskip('elftools', reason='optional SD uploader dependency')
from sd_uploader import fixture
from sd_uploader.ram_image import sha


def declarations(tmp_path,monkeypatch):
    state=tmp_path/'starting.json'
    state.write_text(json.dumps({'chip':'80:f1:b2:d1:c1:32','full_nor':'01'*16}))
    f=dict(schema_version=1,authorized=True,board='lcd-4p3',bridge_serial='bridge',
           bridge_location='location',chip='80:f1:b2:d1:c1:32',card_cid='01'*16,ram_manifest='ram.json',
           idf_python='/pinned/python',starting_state='starting.json',reset_controller='qualified-ch343')
    c=dict(schema_version=1,id='case',starting_state_id=sha(state),files=[],remove=[],
           expected_candidates=[],allowed_resets=['rom-entry','rom-verify','normal-boot'],
           outcome='stage-only',timeout=30,observation={})
    monkeypatch.setattr(fixture,'verify_manifest',lambda _:dict(board='lcd-4p3'))
    return f,c


@pytest.mark.parametrize('change',['authorization','identity','starting-hash','resets','negative','timeout','path'])
def test_reject_fixture_before_hardware(tmp_path,monkeypatch,change):
    f,c=declarations(tmp_path,monkeypatch)
    if change=='authorization': f['authorized']=False
    if change=='identity': f['chip']='other'
    if change=='starting-hash': c['starting_state_id']='wrong'
    if change=='resets': c['allowed_resets']=[]
    if change=='negative': c['outcome']='no-telemetry-means-rejected'
    if change=='timeout': c['timeout']=999999
    if change=='path': c['remove']=['../unrelated']
    (tmp_path/'fixture.json').write_text(json.dumps(f))
    (tmp_path/'case.json').write_text(json.dumps(c))
    with pytest.raises(ValueError):
        fixture.prepare(tmp_path/'fixture.json',tmp_path/'case.json')


def test_valid_stage_only(tmp_path,monkeypatch):
    f,c=declarations(tmp_path,monkeypatch)
    (tmp_path/'fixture.json').write_text(json.dumps(f))
    (tmp_path/'case.json').write_text(json.dumps(c))
    prepared=fixture.prepare(tmp_path/'fixture.json',tmp_path/'case.json')
    assert prepared[0]['authorized'] and prepared[1]['outcome']=='stage-only'


def test_version_two_current_and_relative_paths(tmp_path, monkeypatch):
    f, _ = declarations(tmp_path, monkeypatch)
    f.update(schema_version=2, preservation='current', starting_state=None,
             allowed_resets=['rom-entry', 'rom-verify'])
    (tmp_path/'fixture.json').write_text(json.dumps(f))
    result, _, start = fixture.load_fixture(tmp_path/'fixture.json')
    assert start is None and result['ram_manifest'] == str(tmp_path/'ram.json')


def test_current_not_accepted_for_qualification_case(tmp_path, monkeypatch):
    f, c = declarations(tmp_path, monkeypatch)
    f.update(schema_version=2, preservation='current', starting_state=None,
             allowed_resets=['rom-entry', 'rom-verify'])
    (tmp_path/'fixture.json').write_text(json.dumps(f))
    (tmp_path/'case.json').write_text(json.dumps(c))
    with pytest.raises(ValueError, match='pinned'):
        fixture.prepare(tmp_path/'fixture.json', tmp_path/'case.json')


@pytest.mark.parametrize('field,value', [('chip',None), ('card_cid',13), ('schema_version',True),
    ('idf_python',None), ('starting_state',None)])
def test_bad_field_types_are_configuration_errors(tmp_path, monkeypatch, field, value):
    f, _ = declarations(tmp_path, monkeypatch)
    f[field] = value
    (tmp_path/'fixture.json').write_text(json.dumps(f))
    with pytest.raises(ValueError):
        fixture.load_fixture(tmp_path/'fixture.json')
