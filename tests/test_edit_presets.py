import json
import pytest

from autoedit.web import edit_presets


def test_edit_presets_round_trip_update_delete_and_portable_controls(tmp_path):
    profile = str(tmp_path)
    assert edit_presets.listing(profile) == []
    a = edit_presets.save('Soft afternoon', {'look': 'natural', 'intensity': .7, 'sliders': {'exposure': .25, 'temp': 3}}, profile=profile)
    assert edit_presets.listing(profile) == [a]
    assert set(a['settings']) == {'look', 'intensity', 'sliders'}
    b = edit_presets.save('Soft afternoon II', {'look': '', 'sliders': {}}, a['id'], profile)
    assert b['id'] == a['id'] and edit_presets.listing(profile) == [b]
    assert edit_presets.remove(a['id'], profile)
    assert not edit_presets.remove(a['id'], profile)
    assert edit_presets.listing(profile) == []


def test_edit_presets_reject_paths_credentials_coordinates_and_nonfinite(tmp_path):
    invalid = [
        {'api_key': 'not-a-real-key'}, {'reference': '/not/a/photo'}, {'photo_geometry': {'angle': 1}},
        {'region_settings': {'sky': {'exposure': .2}}}, {'look': '../look'},
        {'intensity': float('nan')}, {'intensity': True}, {'intensity': 3},
        {'sliders': {'exposure': float('inf')}}, {'sliders': {'made_up_op': 1}},
    ]
    for settings in invalid:
        with pytest.raises(ValueError):
            edit_presets.save('Bad', settings, profile=str(tmp_path))
    assert not (tmp_path / 'edit-presets.json').exists()


def test_edit_presets_failed_change_keeps_previous_file(tmp_path):
    a = edit_presets.save('Keep', {'sliders': {'saturation': -3}}, profile=str(tmp_path))
    path = tmp_path / 'edit-presets.json'
    before = path.read_bytes()
    for name, settings, key in [('keep', {}, None), ('', {}, None), ('name', {}, '../x'), ('name', {}, 'f' * 32)]:
        with pytest.raises(ValueError):
            edit_presets.save(name, settings, key, str(tmp_path))
    with pytest.raises(ValueError):
        edit_presets.remove('../x', str(tmp_path))
    assert path.read_bytes() == before
    assert edit_presets.listing(str(tmp_path)) == [a]


def test_edit_presets_corrupt_store_is_not_overwritten(tmp_path):
    path = tmp_path / 'edit-presets.json'
    path.write_text(json.dumps({'version': 1, 'presets': [{'api_key': 'not-a-real-key'}]}))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        edit_presets.save('New', {}, profile=str(tmp_path))
    assert path.read_bytes() == before
