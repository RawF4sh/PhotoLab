"""apply_image: the cells of one region (a list entry) blend in parallel, so a cut leaves no seam."""

import numpy as np

from autoedit.edit.apply import apply_image


def _setup():
    rng = np.random.default_rng(7)
    img = rng.uniform(0.1, 0.9, (96, 96, 3)).astype(np.float32)
    a = np.zeros((96, 96), np.float32)
    a[20:76, 20:76] = 1.0
    k = np.ones(9, np.float32) / 9
    for axis in (0, 1):                      # blur the rectangle so the edge is soft
        a = np.apply_along_axis(lambda v: np.convolve(v, k, mode="same"), axis, a)
    m = np.zeros_like(a)
    m[:, :50] = 1.0
    m = np.apply_along_axis(lambda v: np.convolve(v, k, mode="same"), 1, m)
    return img, a.astype(np.float32), (a * m).astype(np.float32), (a * (1 - m)).astype(np.float32)


def test_sibling_cells_blend_without_a_seam():
    img, a, a1, a2 = _setup()
    s = {"exposure": 0.4, "contrast": 10}
    whole = apply_image(img, [(a, s)], {})
    split = apply_image(img, [[(a1, s), (a2, s)]], {})
    assert np.abs(whole - split).max() < 1e-5
    s2 = {"exposure": -0.3, "contrast": -10}
    one, two = apply_image(img, [(a, s)], {}), apply_image(img, [(a, s2)], {})
    mixed = apply_image(img, [[(a1, s), (a2, s2)]], {})
    lo, hi = np.minimum(one, two), np.maximum(one, two)
    assert (mixed >= lo - 1e-5).all() and (mixed <= hi + 1e-5).all()
    assert np.abs(mixed - one).max() > 0.01 and np.abs(mixed - two).max() > 0.01
