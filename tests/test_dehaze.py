"""Real dehaze (dark-channel prior) + the look saturation_add lever.

The old dehaze was a contrast+saturation fake; this guards the physical version: it must be a
strict no-op at amt=0 and must actually invert the atmospheric-scattering model on a hazy frame
(recover blacks + restore contrast), not just bump saturation.
"""

import numpy as np

from autoedit.edit.filters import box_min
from autoedit.edit.ops import apply_ops, dehaze
from autoedit.looks import Look, shape


def _hazy():
    """A synthetic hazy frame: a high-contrast scene J veiled by I = J·t + A·(1−t)."""
    yy, xx = np.mgrid[0:64, 0:64].astype(np.float32)
    J = ((xx // 8 + yy // 8) % 2).astype(np.float32)        # 0/1 checkerboard → full contrast
    J = np.repeat(J[..., None], 3, axis=2)
    t, A = 0.5, 0.9                                          # uniform transmission + bright airlight
    return (J * t + A * (1.0 - t)).astype(np.float32), J


def test_dehaze_zero_is_identity():
    rgb = _hazy()[0]
    assert np.array_equal(dehaze(rgb, 0.0), rgb)
    # and through apply_ops: dehaze key present but zero must not touch pixels
    assert np.array_equal(apply_ops(rgb, {"dehaze": 0.0}), rgb)


def test_dehaze_recovers_contrast_and_blacks():
    hazy, _ = _hazy()
    out = dehaze(hazy, 80.0)
    # blacks come back down (veil lifted) and overall contrast widens
    assert out.min() < hazy.min() - 0.05
    assert out.std() > hazy.std() * 1.2
    assert out.min() >= 0.0 and out.max() <= 1.0


def test_dehaze_tapers_smooth_bright_gradients():
    g = np.linspace(0.82, 0.92, 128)[:, None].repeat(128, 1)
    smooth_sky = np.repeat(g[..., None], 3, axis=2).astype(np.float32)
    out = dehaze(smooth_sky, 60.0)

    # Fragile fog/sky gradients should not be stretched into visible contour plateaus.
    assert float(np.abs(out - smooth_sky).mean()) < 0.025


def test_dehaze_negative_adds_veil():
    hazy, _ = _hazy()
    out = dehaze(hazy, -40.0)            # inverse: should reduce contrast, not raise it
    assert out.std() < hazy.std()


def test_box_min_erodes():
    a = np.ones((9, 9), np.float32)
    a[4, 4] = 0.0
    out = box_min(a, 2)                  # the single zero spreads over a (2*2+1)^2 neighbourhood
    assert out[4, 4] == 0.0 and out[2, 2] == 0.0 and out[6, 6] == 0.0
    assert out[0, 0] == 1.0             # far corner untouched


def test_saturation_add_is_global_only():
    g, region = {"contrast": 10.0}, {"contrast": 10.0}
    look = Look(saturation_add=12.0)
    shape(g, look, intensity=1.0, is_global=True)
    shape(region, look, intensity=1.0, is_global=False)
    assert g.get("saturation") == 12.0          # global block gets the lift
    assert "saturation" not in region           # region block does not (skin guard relies on this)
