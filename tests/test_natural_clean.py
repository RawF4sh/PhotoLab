"""The opt-in natural candidate retains colour and exposure while reducing added contrast."""
import numpy as np

from autoedit import looks, paths
from autoedit.edit.ops import apply_ops


def test_natural_clean_golden_keeps_colour_exposure_and_reduces_local_contrast():
    library = looks.load(paths.config_path('looks.toml', packaged_fallback=True))
    base = {'exposure': .3, 'temp': 2, 'tint': -1, 'saturation': -3,
            'contrast': 4, 'clarity': 8, 'dehaze': 6, 'curve': 2}
    current, candidate = base.copy(), base.copy()
    looks.shape(current, library['natural'], .8, is_global=True)
    looks.shape(candidate, library['natural_clean'], .8, is_global=True)
    wanted = {'exposure': .24, 'temp': 1.6, 'tint': -.8, 'saturation': -2.4,
              'contrast': 3.2, 'clarity': 3.2, 'dehaze': 2.4, 'curve': 1.6}
    assert set(candidate) == set(wanted)
    for key, value in wanted.items():
        assert abs(candidate[key] - value) < 1e-8
    for key in ('exposure', 'temp', 'tint', 'saturation'):
        assert candidate[key] == current[key]
    for key in ('clarity', 'dehaze', 'curve'):
        assert candidate[key] < current[key]
    assert not library['natural_clean'].preserve_exposure


def test_natural_clean_adds_no_cast_or_unrequested_curve_to_neutral_ramp():
    library = looks.load(paths.config_path('looks.toml', packaged_fallback=True))
    settings = {}
    looks.shape(settings, library['natural_clean'], .8, is_global=True)
    assert settings == {}
    ramp = np.repeat(np.linspace(.02, .98, 64, dtype=np.float32)[None, :, None], 3, axis=2)
    assert np.array_equal(apply_ops(ramp, settings), ramp)
