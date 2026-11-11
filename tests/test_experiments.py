"""Experiment identity, matched controls, frozen protocol, and sharded audit correctness."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image as PILImage
import pytest

from autoedit import experiments as ex
from autoedit.captures import sha256_file, stable_hash
from autoedit.contract import RunConfig
from autoedit.imageio import Image, load_image
from autoedit.pipeline import Pipeline


def _picture(path: Path, offset: int):
    yy, xx = np.mgrid[:32, :48]
    rgb = np.stack([offset + xx, offset + yy, offset + (xx + yy)//2], axis=-1)
    PILImage.fromarray(rgb.astype(np.uint8)).save(path, quality=95)


def _prepared(tmp_path, count=2):
    tuning, holdout = tmp_path/'tuning', tmp_path/'holdout'
    tuning.mkdir(); holdout.mkdir()
    for i in range(count):
        _picture(tuning/f'tune-{i}.jpg', 40 + i*15)
        _picture(holdout/f'hold-{i}.jpg', 110 + i*15)
    root = tmp_path/'run'
    ex.prepare_experiment(root, tuning, holdout)
    return root


def test_transitive_controls_are_matched_and_unknown_names_rejected():
    specs = ex.matched_candidates(ex.candidates(), 'ablations', ['B-region-first'])
    assert [s['name'] for s in specs] == ['B-global', 'B-stub-analysis', 'B-stub-regions', 'B-region-first']
    assert 'B-reinhard' in {s['name'] for s in ex.candidates()}
    with pytest.raises(ValueError):
        ex.matched_candidates(ex.candidates(), 'primary', ['does-not-exist'])
    with pytest.raises(ValueError):
        ex.matched_candidates(ex.candidates(), 'primary', [])
    with pytest.raises(ValueError):
        ex.matched_candidates([{'name':'a','control':'b'}, {'name':'b','control':'a'}], 'primary', ['a'])


def test_prepare_requires_independent_partitions_and_never_replaces_existing_run(tmp_path):
    root = _prepared(tmp_path)
    assert (root/'baseline'/'src'/'autoedit'/'pipeline.py').is_file()
    with pytest.raises(FileExistsError):
        ex.prepare_experiment(root, tmp_path/'tuning', tmp_path/'holdout')
    with pytest.raises(ValueError):
        ex.prepare_experiment(tmp_path/'another', tmp_path/'tuning', tmp_path/'tuning')
    entries = json.loads((root/'holdout-sources.json').read_text())['entries']
    assert len(entries) == 2 and all(e['split'] == 'holdout' and e['source_sha256'] for e in entries)


def test_partition_rejects_hash_identity_and_burst_leakage():
    a = {'capture_id':'a', 'identity':'one', 'source_sha256':'hash-a', 'scene':'scene',
         'camera':'camera', 'group':'group-a', 'split':'tuning'}
    b = {**a, 'capture_id':'b', 'identity':'two', 'source_sha256':'hash-b', 'group':'group-b', 'split':'holdout'}
    ex._validate_partition({'tuning': {'entries':[a]}, 'holdout': {'entries':[b]}})
    for field in ('source_sha256', 'identity', 'group'):
        with pytest.raises(ValueError):
            ex._validate_partition({'tuning': {'entries':[a]}, 'holdout': {'entries':[{**b, field:a[field]}]}})


def test_lock_freezes_baseline_runtime_and_source_hashes(tmp_path):
    root = _prepared(tmp_path)
    locked = ex.lock_experiment(root, ablation_groups=1)
    assert locked['schema_version'] == 2 and locked['baseline_hashes'] and locked['runtime_versions']
    assert any(name.startswith('scripts/') for name in locked['code_hashes'])
    assert all(len(captures) == 1 for captures in locked['ablation_captures'].values())
    with pytest.raises(FileExistsError):
        ex.lock_experiment(root)
    pipeline = root/'baseline'/'src'/'autoedit'/'pipeline.py'
    pipeline.write_text(pipeline.read_text()+'\n# changed\n')
    with pytest.raises(ValueError):
        ex.evaluate(root, 'tuning', names=['A-current'], max_px=48)


def test_decode_cache_identity_includes_modes_dependencies_and_metadata(tmp_path):
    source = tmp_path/'one.jpg'; _picture(source, 50)
    entry = {'source':str(source), 'source_sha256':sha256_file(source)}
    options = {'raw_mode':'none'}
    cache = tmp_path/'cache'
    image = Image(np.full((12,16,3), .2, np.float32), source_bits=16, exif={'iso':6400},
                  decode_ev=.4, decode_info={'method':'fixture'})
    with patch.object(ex, 'load_image', return_value=image) as loader:
        first = ex.cached_decode(entry, options, cache, 16)
        second = ex.cached_decode(entry, options, cache, 16)
        assert loader.call_count == 1
        assert first.decode_info['benchmark_cache_hit'] is False
        assert second.decode_info['benchmark_cache_hit'] is True
        assert second.exif == {'iso':6400} and second.source_bits == 16 and second.decode_ev == .4
        ex.cached_decode(entry, {'raw_mode':'preview_ev'}, cache, 16)
        assert loader.call_count == 2
        versions = ex.runtime_versions()
        with patch.object(ex, 'runtime_versions', return_value={**versions, 'Pillow':'different'}):
            ex.cached_decode(entry, options, cache, 16)
        assert loader.call_count == 3


def test_frozen_control_preserves_pixels_metadata_and_ignores_stdout_noise(tmp_path):
    root = _prepared(tmp_path, count=1)
    pipeline = root/'baseline'/'src'/'autoedit'/'pipeline.py'
    pipeline.write_text(pipeline.read_text().replace('from __future__ import annotations',
                        'from __future__ import annotations\nprint("fixture stdout noise")', 1))
    source = tmp_path/'tuning'/'tune-0.jpg'
    image = load_image(str(source))
    image.exif = {'iso':6400.0, 'exposure_ev':-1.0}
    image.decode_ev = .3
    expected, result = Pipeline.build(RunConfig()).process_one(str(source), image=image)
    worker = ex.FrozenControl(root, tmp_path/'scratch')
    try:
        actual, frozen, masks = worker.process({'source':str(source)}, image, {})
        assert np.array_equal(expected, actual)
        assert frozen.global_stats.iso == result.global_stats.iso == 6400
        assert frozen.global_stats.exposure_ev == result.global_stats.exposure_ev == -1
        assert frozen.decode_ev == result.decode_ev == .3
        assert frozen.audit['frozen_baseline'] is True
        entry = {'source':str(source), 'source_sha256':sha256_file(source)}
        decoded = worker.decode(entry, 48, False, tmp_path/'decode-cache')
        assert np.array_equal(decoded.rgb, load_image(str(source)).rgb)
    finally:
        worker.close()


def test_consistency_bias_uses_exact_ev_and_marks_singletons():
    entries = [{'capture_id':'a','scene':'s','group':'g'}, {'capture_id':'b','scene':'s','group':'g'},
               {'capture_id':'c','scene':'s','group':'other'}]
    biases = ex.consistency_biases(entries, {'a':.1, 'b':.2, 'c':.4}, .15)
    assert biases['a']['bias_ev'] == .15 and biases['b']['bias_ev'] == -.15
    assert biases['a']['available'] and not biases['c']['available']
    assert biases['c']['bias_ev'] == 0


def test_evaluation_has_common_masks_complete_audit_and_strict_resume(tmp_path):
    root = _prepared(tmp_path, count=1)
    ex.lock_experiment(root, ablation_groups=1)
    report = ex.evaluate(root, 'tuning', names=['A-current','B-global'], max_px=48)
    assert report['complete'] and report['expected'] == report['completed'] == 2
    assert len({row['common_mask_digest'] for row in report['rows']}) == 1
    assert all(row['sidecar_sha256'] == sha256_file(row['sidecar']) for row in report['rows'])
    assert all('candidate_render_ms' in row and 'candidate_decode_ms' in row and 'resolved_audit' in row
               for row in report['rows'])
    resumed = ex.evaluate(root, 'tuning', names=['A-current','B-global'], max_px=48, resume=True)
    assert len(resumed['rows']) == 2
    with pytest.raises(ValueError):
        ex.evaluate(root, 'tuning', names=['A-current'], max_px=48, resume=True)
    Path(report['rows'][0]['sidecar']).write_text('{}')
    with pytest.raises(ValueError):
        ex.evaluate(root, 'tuning', names=['A-current','B-global'], max_px=48, resume=True)


def test_shards_merge_full_grid_and_keep_source_integrity(tmp_path):
    root = _prepared(tmp_path, count=2)
    ex.lock_experiment(root)
    one = ex.evaluate(root, 'tuning', names=['A-current'], max_px=48, shard=(0,2))
    two = ex.evaluate(root, 'tuning', names=['A-current'], max_px=48, shard=(1,2))
    assert one['complete'] and two['complete'] and one['capture_ids'] != two['capture_ids']
    merged = ex.merge_evaluations(root, 'tuning', max_px=48, shard_count=2)
    assert merged['complete'] and merged['expected'] == merged['completed'] == 2
    assert len({row['capture_id'] for row in merged['rows']}) == 2
    assert not merged['source_errors']
    with pytest.raises(FileExistsError):
        ex.merge_evaluations(root, 'tuning', max_px=48, shard_count=2)


def test_runtime_and_lock_tampering_fail_before_render(tmp_path):
    root = _prepared(tmp_path, count=1)
    ex.lock_experiment(root)
    with patch.object(ex, 'runtime_versions', return_value={'python':'different'}):
        with pytest.raises(ValueError):
            ex.evaluate(root, 'tuning', names=['A-current'], max_px=48)
    path = root/'locked-experiment.json'; lock = json.loads(path.read_text())
    lock['candidates'][0]['options']['tampered'] = True
    path.write_text(json.dumps(lock))
    with pytest.raises(ValueError):
        ex.evaluate(root, 'tuning', names=['A-current'], max_px=48)


def test_camera_distance_is_diagnostic_and_camera_provenance_retained(tmp_path):
    root = _prepared(tmp_path, count=1)
    camera = tmp_path/'camera-reference.jpg'; _picture(camera, 140)
    manifest_path = root/'tuning-sources.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['entries'][0].update(camera_reference=str(camera), camera_reference_sha256=sha256_file(camera))
    manifest_path.write_text(json.dumps(manifest))
    ex.lock_experiment(root)
    report = ex.evaluate(root, 'tuning', max_px=48, names=['A-current'])
    row = report['rows'][0]
    assert row['camera_reference']['kind'] == 'paired_camera_jpeg'
    assert isinstance(row['camera_comparison']['ev_vs_camera'], float)
    assert report['summary']['A-current']['camera_reference_count'] == 1
    assert row['assessment']['passed']  # distance to this brighter reference is not a hard failure


def test_controlled_correction_masks_are_fixed_and_resegmentation_is_separate(tmp_path):
    root = _prepared(tmp_path, count=1)
    ex.lock_experiment(root, ablation_groups=1)
    report = ex.evaluate(root, 'tuning', suite='ablations', max_px=48,
                         names=['B-stub-regions', 'B-palette', 'B-stub-resegment-regions'])
    assert report['complete']
    rows = {row['candidate']: row for row in report['rows']}
    for name in ('B-stub-analysis', 'B-stub-regions', 'B-palette'):
        row = rows[name]
        assert row['mask_policy'] == 'fixed-source'
        assert row['resolved_audit']['fixed_mask_names']
        assert row['candidate_mask_digest'] == row['common_mask_digest']
    live = rows['B-stub-resegment-regions']
    assert live['mask_policy'] == 'resegment'
    assert live['resolved_audit']['fixed_mask_names'] == []
    registered = {spec['name']: spec for spec in ex.candidates()}
    assert registered['B-sam-analysis']['control'] == 'B-stub-resegment-analysis'


def test_paired_jpeg_policy_arms_keep_capture_identity_and_measure_policy_difference(tmp_path):
    root = _prepared(tmp_path, count=1)
    camera = tmp_path/'paired-camera.jpg'; _picture(camera, 50)
    path = root/'tuning-sources.json'; manifest = json.loads(path.read_text())
    entry = manifest['entries'][0]
    capture_id = entry['capture_id']
    # The manifest routing is tested with inexpensive pixel fixtures; RAW decode math has
    # its own tests. Declaring the selected member RAW enables the retained companion arm.
    entry.update(source_format='cr2', camera_reference=str(camera), camera_reference_sha256=sha256_file(camera))
    path.write_text(json.dumps(manifest))
    ex.lock_experiment(root, ablation_groups=1)
    report = ex.evaluate(root, 'tuning', suite='ablations', max_px=48, names=['B-shared-format-jpeg'])
    assert report['complete'] and report['failed'] == 0 and report['expected'] == 3
    assert {row['capture_id'] for row in report['rows']} == {capture_id}
    rows = {row['candidate']: row for row in report['rows']}
    separate, shared = rows['B-global-jpeg'], rows['B-shared-format-jpeg']
    assert separate['source_variant'] == shared['source_variant'] == 'paired_jpeg'
    assert separate['source'] == shared['source'] == str(camera)
    assert separate['source_format'] == shared['source_format'] == 'jpg'
    assert separate['source_sha256'] == shared['source_sha256'] == sha256_file(camera)
    assert separate['settings']['exposure'] != shared['settings']['exposure']
    # Both safety assessments start from the same JPEG, not from the darker neutral RAW.
    assert separate['assessment']['metrics']['source'] == shared['assessment']['metrics']['source']
    assert separate['assessment']['metrics']['source'] != rows['B-global']['assessment']['metrics']['source']


def test_paired_jpeg_absence_is_explicitly_unmeasured_and_resumable(tmp_path):
    root = _prepared(tmp_path, count=1)
    ex.lock_experiment(root, ablation_groups=1)
    report = ex.evaluate(root, 'tuning', suite='ablations', max_px=48, names=['B-shared-format-jpeg'])
    assert report['complete'] and report['not_applicable'] == 2 and report['completed'] == 1
    assert report['summary']['B-shared-format-jpeg']['n'] == 0
    assert report['summary']['B-shared-format-jpeg']['not_applicable'] == 1
    assert report['summary']['B-shared-format-jpeg']['eligible_proxy_safety'] is False
    resumed = ex.evaluate(root, 'tuning', suite='ablations', max_px=48,
                          names=['B-shared-format-jpeg'], resume=True)
    assert len(resumed['rows']) == 3 and resumed['not_applicable'] == 2


def test_runtime_versions_include_rendering_distributions_without_imports():
    import builtins
    real_import = builtins.__import__
    def blocked(name, *args, **kwargs):
        if name.split('.')[0] in {'torch', 'torchvision', 'cv2', 'rawpy', 'scipy'}:
            raise AssertionError('runtime metadata attempted optional-module import: '+name)
        return real_import(name, *args, **kwargs)
    with patch.object(builtins, '__import__', side_effect=blocked):
        versions = ex.runtime_versions()
    assert {'scipy', 'torch', 'torchvision', 'opencv-python', 'opencv-python-headless',
            'opencv-contrib-python', 'opencv-contrib-python-headless'} <= set(versions)


def test_contributing_opencv_or_torchvision_change_blocks_frozen_evaluation(tmp_path):
    root = _prepared(tmp_path, count=1)
    ex.lock_experiment(root)
    real_version = ex.importlib.metadata.version
    for dependency in ('torchvision', 'opencv-python', 'opencv-python-headless',
                       'opencv-contrib-python', 'opencv-contrib-python-headless'):
        def changed(name):
            return 'test-different-distribution-version' if name == dependency else real_version(name)
        with patch.object(ex.importlib.metadata, 'version', side_effect=changed):
            with pytest.raises(ValueError):
                ex.evaluate(root, 'tuning', names=['A-current'], max_px=48)
    assert not (root/'tuning-primary-48px').exists()  # Reject before decoding or publishing.


def test_consistency_context_keeps_rendered_sample_fixed(tmp_path):
    root = _prepared(tmp_path, count=2)
    path = root/'tuning-sources.json'; manifest = json.loads(path.read_text())
    for entry in manifest['entries']:
        entry['group'] = 'shared-burst'
    path.write_text(json.dumps(manifest))
    lock = ex.lock_experiment(root, ablation_groups=1)
    report = ex.evaluate(root, 'tuning', suite='ablations', max_px=48, names=['B-consistency'])
    assert report['complete']
    assert len(report['capture_ids']) == 1 and report['expected'] == 2
    assert len(report['consistency_context_capture_ids']) == 2
    row = next(row for row in report['rows'] if row['candidate'] == 'B-consistency')
    assert row['consistency']['available'] and row['consistency']['group_members'] == 2
    assert report['capture_ids'] == lock['ablation_captures']['tuning']


def test_candidate_region_failure_keeps_fixed_mask_safety_result(tmp_path):
    root = _prepared(tmp_path, count=1)
    ex.lock_experiment(root, ablation_groups=1)
    real_engine, real_assess = ex.WorkflowEngine, ex.assess_pixels
    def engine_with_candidate_mask(cfg, workflow, options):
        engine = real_engine(cfg, workflow, options)
        original = engine.process
        def process(*args, **kwargs):
            rgb, result = original(*args, **kwargs)
            if options.get('segmentation') and engine.fixed_masks is None:
                engine.last_masks = {'audit_only_region': np.ones(rgb.shape[:2], bool)}
            return rgb, result
        engine.process = process
        return engine
    def assessment_with_own_mask_failure(source, output, masks=None, **kwargs):
        result = real_assess(source, output, masks=masks, **kwargs)
        if 'audit_only_region' in (masks or {}):
            # Inject a measurement-only failure on the distinct candidate mask; pixels
            # and the original common-mask assessment are unchanged.
            result['failures'].append('audit_only_region_warning')
            result['passed'] = False
        return result
    with patch.object(ex, 'WorkflowEngine', side_effect=engine_with_candidate_mask), \
         patch.object(ex, 'assess_pixels', side_effect=assessment_with_own_mask_failure):
        report = ex.evaluate(root, 'tuning', suite='ablations', max_px=48,
                             names=['B-stub-resegment-analysis'])
    row = next(r for r in report['rows'] if r['candidate'] == 'B-stub-resegment-analysis')
    assert row['status'] == 'measured'
    fixed = row['fixed_mask_safety']
    assert fixed['passed'] and fixed['failures'] == [] and 'fixed-source' in fixed['scope']
    assert not row['assessment']['passed']
    assert 'candidate_region:audit_only_region_warning' in row['assessment']['failures']
    assert not row['candidate_region_assessment']['passed']
    assert 'audit_only_region' not in row['assessment']['metrics']['regions']
    assert 'audit_only_region' in row['candidate_region_assessment']['metrics']['regions']
    audit = json.loads(Path(row['sidecar']).read_text())['audit']
    assert audit['fixed_mask_safety'] == fixed
    assert not audit['assessment']['passed']  # Conservative combined eligibility retained.


def test_consistency_audit_snapshot_matches_each_hash_verified_sidecar(tmp_path):
    root = _prepared(tmp_path, count=2)
    ex.lock_experiment(root, ablation_groups=2)
    def distinct_biases(entries, measurements, maximum):
        return {entry['capture_id']: {'bias_ev': -.15 if index == 0 else 0.,
                                      'available': True, 'group_members': 2}
                for index, entry in enumerate(entries)}
    with patch.object(ex, 'consistency_biases', side_effect=distinct_biases):
        report = ex.evaluate(root, 'tuning', suite='ablations', names=['B-consistency'], max_px=48)
    rows = [row for row in report['rows'] if row['candidate'] == 'B-consistency']
    assert report['complete'] and len(rows) == 2
    assert {row['consistency']['bias_ev'] for row in rows} == {-.15, 0.}
    saved_report = json.loads((root/'tuning-ablations-48px/report.json').read_text())
    serialized = {row['capture_id']: row for row in saved_report['rows'] if row['candidate'] == 'B-consistency'}
    for row in rows:
        assert sha256_file(row['sidecar']) == row['sidecar_sha256']
        sidecar = json.loads(Path(row['sidecar']).read_text())
        expected = row['consistency']['bias_ev']
        assert sidecar['audit']['options']['workflow']['consistency_bias'] == expected
        assert row['resolved_audit']['options'] == sidecar['audit']['options']
        assert serialized[row['capture_id']]['resolved_audit']['options'] == sidecar['audit']['options']
