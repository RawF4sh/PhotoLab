"""Invalid safety configuration must fail closed before numeric comparisons."""
import tomllib

import numpy as np
import pytest

from autoedit.quality import assess_pixels, load_quality_config, prepare_quality_source


def _pixels():
    return np.full((32, 32, 3), .5, np.float32)


def test_quality_default_config_is_unchanged_and_identity_passes():
    from pathlib import Path
    import autoedit.quality as quality
    expected = tomllib.loads(Path(quality.__file__).with_name('configs').joinpath('quality.toml').read_text())
    assert load_quality_config() == expected
    assert assess_pixels(_pixels(), _pixels())['passed']
    assert not assess_pixels(_pixels(), np.full_like(_pixels(), .99))['passed']


def test_quality_toml_rejects_nan_and_infinities_before_assessment(tmp_path):
    path = tmp_path / 'quality.toml'
    for value in ('nan', '+nan', '-nan', 'inf', '+inf', '-inf'):
        for field in ('clip_budget', 'shadow_clip_budget', 'dark_regress', 'halo_delta'):
            path.write_text(f'{field} = {value}\n')
            with pytest.raises(ValueError):
                load_quality_config(path)
    path.write_text('remediation_factors = [0.5, nan, 0.0]\n')
    with pytest.raises(ValueError):
        load_quality_config(path)


def test_quality_direct_overrides_reject_nonfinite_numeric_values():
    source = _pixels()
    clipped = np.full_like(source, .99)
    for key, value in load_quality_config().items():
        if isinstance(value, (int, float)):
            for bad in (float('nan'), float('inf'), float('-inf')):
                with pytest.raises(ValueError):
                    assess_pixels(source, clipped, thresholds={key: bad})
                with pytest.raises(ValueError):
                    prepare_quality_source(source, thresholds={key: bad})


def test_quality_rejects_invalid_types_ranges_and_unknown_keys():
    invalid = [
        {'clip_budget': None}, {'clip_budget': '0.005'}, {'clip_budget': True},
        {'clip_budget': -0.005}, {'chunk_rows': 0}, {'chunk_rows': 1.5},
        {'schema_version': 2}, {'halo_percentile': 101},
        {'texture_high_min': .99, 'texture_high_max': .98},
        {'highlight_level': 1.1}, {'neutral_luma_min': .96, 'neutral_luma_max': .95},
        {'remediation_factors': '0.5'}, {'remediation_factors': [False]},
        {'remediation_factors': [float('nan')]}, {'remediation_factors': [1.1]},
        {'preserve_exposure': 1}, {'clip_budet': .005},
    ]
    for overrides in invalid:
        with pytest.raises(ValueError):
            assess_pixels(_pixels(), _pixels(), thresholds=overrides)


def test_quality_valid_booleans_integer_settings_and_partial_toml_remain_supported(tmp_path):
    path = tmp_path / 'quality.toml'
    path.write_text('chunk_rows = 8\nclip_budget = 0.002\npreserve_exposure = true\nremediation_factors = [0.5, 0.0]\n')
    cfg = load_quality_config(path)
    assert cfg['chunk_rows'] == 8 and cfg['preserve_exposure'] is True
    assert cfg['clip_budget'] == .002 and cfg['remediation_factors'] == [.5, 0.]
    dark = np.full_like(_pixels(), .2)
    dim = np.full_like(dark, .15)
    assert assess_pixels(dark, dim, thresholds=cfg)['passed']
    assert not assess_pixels(dark, dim, thresholds={**cfg, 'preserve_exposure': False})['passed']
    assert assess_pixels(_pixels(), _pixels(), thresholds={'chunk_rows': np.int64(8),
                                                            'clip_budget': np.float32(.005)})['passed']


def test_quality_nan_cannot_disable_clipping_with_a_prepared_source():
    source = _pixels()
    prepared = prepare_quality_source(source)
    with pytest.raises(ValueError):
        assess_pixels(source, np.full_like(source, .99), thresholds={'clip_budget': float('nan')},
                      prepared=prepared)
