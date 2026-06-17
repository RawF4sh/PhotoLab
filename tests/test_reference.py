"""Reference-style color matching (edit/reference.py) — LAB color transfer math."""

import numpy as np

from autoedit.edit.reference import color_transfer, lab_to_rgb, rgb_to_lab


def _const(h, w, rgb):
    return np.tile(np.array(rgb, np.float32), (h, w, 1))


def test_lab_roundtrip_is_identity():
    rng = np.random.default_rng(0)
    img = rng.random((16, 16, 3)).astype(np.float32)
    back = lab_to_rgb(rgb_to_lab(img))
    assert np.allclose(img, back, atol=2e-3)


def test_strength_zero_is_noop():
    img = _const(8, 8, [0.4, 0.6, 0.3])
    ref = _const(8, 8, [0.1, 0.1, 0.9])
    out = color_transfer(img, ref, strength=0.0)
    assert np.allclose(out, img, atol=1e-6)


def test_self_match_is_identity():
    rng = np.random.default_rng(1)
    img = rng.random((12, 12, 3)).astype(np.float32)
    out = color_transfer(img, img, strength=1.0)   # mean/std already match -> no change
    assert np.allclose(out, img, atol=2e-3)


def test_matches_toward_reference_palette():
    gray = _const(10, 10, [0.5, 0.5, 0.5])      # neutral target
    blue = _const(10, 10, [0.1, 0.2, 0.7])      # cool reference
    out = color_transfer(gray, blue, strength=1.0)
    # neutral pixels should pick up the reference's cool cast: blue > red
    assert out[..., 2].mean() > out[..., 0].mean() + 0.05
    # and it stays in range
    assert out.min() >= 0.0 and out.max() <= 1.0
