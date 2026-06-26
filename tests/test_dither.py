"""Output dither — breaks up 8-bit gradient banding (the contour lines in foggy skies) while
staying deterministic (coordinate-hashed, so golden tests don't flake)."""

import numpy as np

from autoedit.imageio import _to_u8


def _gradient():
    # a smooth low-contrast vertical ramp over a narrow range — the banding-prone case
    g = np.linspace(0.80, 0.94, 256)[:, None].repeat(64, 1)
    return np.repeat(g[..., None], 3, axis=2).astype(np.float32)


def test_dither_breaks_hard_bands():
    grad = _gradient()
    plain = _to_u8(grad, dither=False)
    dith = _to_u8(grad, dither=True)
    # without dither every row is a single flat code → hard horizontal band edges (0 mixed rows);
    # with dither, rows near a level boundary mix two codes → the band edges dissolve.
    mixed = lambda im: sum(len(np.unique(im[r, :, 0])) > 1 for r in range(im.shape[0]))
    assert mixed(plain) == 0
    assert mixed(dith) > plain.shape[0] // 2


def test_dither_is_deterministic():
    grad = _gradient()
    assert np.array_equal(_to_u8(grad), _to_u8(grad))   # no RNG → reproducible


def test_dither_stays_within_one_lsb():
    grad = _gradient()
    plain = _to_u8(grad, dither=False).astype(int)
    dith = _to_u8(grad, dither=True).astype(int)
    assert np.abs(dith - plain).max() <= 1               # ±1 LSB, imperceptible


def test_flat_color_barely_moves():
    flat = np.full((32, 32, 3), 0.5, np.float32)         # exact mid-gray
    out = _to_u8(flat)
    assert set(np.unique(out)).issubset({127, 128})      # never drifts more than a code
