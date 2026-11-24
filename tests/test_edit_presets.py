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
        {'region_settings': {'/private/a.jpg': {'exposure': .2}}}, {'look': '../look'},
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


def test_portable_recipe_copies_only_settings_and_validated_conditions(tmp_path):
    profile = str(tmp_path)
    settings = {'region_settings': {'coat': {'exposure': .3}, 'sky': {'contrast': 0}},
                'condition': {'scene': 'people', 'tests': [{'measure': 'iso', 'op': 'above', 'value': 800}]}}
    saved = edit_presets.save('Coat detail', settings, profile=profile)
    portable = edit_presets.portable(saved['id'], profile)
    assert set(portable) == {'name', 'settings'}
    assert portable['settings']['region_settings'] == {'coat': {'exposure': .3}}
    assert portable['settings']['condition'] == settings['condition']
    imported = edit_presets.import_portable(dict(portable, name='Coat detail copy'), profile)
    assert imported['settings'] == saved['settings'] and imported['id'] != saved['id']
    for extra in ({'id': saved['id']}, {'mask_refinements': {}}, {'path': '../hidden'}):
        with pytest.raises(ValueError):
            edit_presets.import_portable({**portable, **extra}, profile)


def test_conditions_and_order_validate_and_use_all_tests(tmp_path):
    from autoedit.web import recipe_conditions as rules
    table = rules.measures()
    test = {'measure': 'mean_lum', 'op': 'above', 'value': .3}
    condition = rules.check({'scene': 'people', 'tests': [test, {'measure': 'iso', 'op': 'below', 'value': 400}]}, table)
    assert rules.matches(condition, 'people', {'mean_lum': .4, 'iso': 320})
    assert not rules.matches(condition, 'other', {'mean_lum': .4, 'iso': 320})
    assert not rules.matches(condition, 'people', {'mean_lum': .4, 'iso': None})
    assert not rules.matches(condition, 'people', {'mean_lum': .3, 'iso': 320})
    invalid = [{'tests': [test] * 4}, {'scene': 'any'}, {'test': test}, {'tests': 'x'}]
    for bad in ({'measure': 'private'}, {'op': 'exec'}, {'value': True}, {'value': float('inf')}, {'value': 2}):
        invalid.append({'tests': [{**test, **bad}]})
    for cond in invalid:
        with pytest.raises(ValueError):
            edit_presets.save('invalid', {'condition': cond}, profile=str(tmp_path))
    for order in (['a' * 32] * 2, ['../bad'], [f'{i:032x}' for i in range(21)]):
        with pytest.raises(ValueError):
            edit_presets.check_order(order)


def test_portable_offsets_use_folded_totals_without_retaining_source_details(tmp_path):
    source = {'photo_settings': {'exposure': -.1}, 'region_settings': {'sky': {'temp': 4}},
              'edit_steps': [{'id': 'abc-0', 'origin': 'suggested', 'label': 'Personal note',
                              'amount': .5, 'on': True, 'global': {'exposure': .4},
                              'regions': {'sky': {'temp': -2}}, 'ref': 'test-model'}]}
    preset = edit_presets.save('Clouds', {'look': 'natural'}, profile=str(tmp_path), offsets=source)
    s = preset['settings']
    assert s == {'look': 'natural', 'sliders': {'exposure': .1}, 'region_settings': {'sky': {'temp': 3}}}
    assert 'Personal note' not in (tmp_path / 'edit-presets.json').read_text()
