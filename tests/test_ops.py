"""Apply-engine op math: identity no-op, exposure, saturation."""

import numpy as np

from autoedit.edit import apply_ops
from autoedit.edit.ops import vibrance
from autoedit.imageio import srgb_to_linear


def test_identity_is_noop():
    rng = np.random.default_rng(0)
    img = rng.random((24, 24, 3)).astype(np.float32)
    out = apply_ops(img, {})
    assert np.abs(out - img).max() == 0.0


def test_exposure_doubles_linear_on_dark_patch():
    img = np.full((8, 8, 3), 0.2, np.float32)  # dark enough that +1 stop won't clip
    out = apply_ops(img, {"exposure": 1.0})
    ratio = srgb_to_linear(out).mean() / srgb_to_linear(img).mean()
    assert abs(ratio - 2.0) < 0.02


def test_saturation_increases_chroma():
    img = np.zeros((4, 4, 3), np.float32)
    img[..., 0], img[..., 1], img[..., 2] = 0.6, 0.4, 0.4  # reddish, some chroma
    out = vibrance(img, vib=0.0, sat=50.0)
    chroma_in = img.max(axis=2) - img.min(axis=2)
    chroma_out = out.max(axis=2) - out.min(axis=2)
    assert chroma_out.mean() > chroma_in.mean()


def test_negative_saturation_reduces_chroma():
    img = np.zeros((4, 4, 3), np.float32)
    img[..., 0], img[..., 1], img[..., 2] = 0.6, 0.4, 0.4
    out = vibrance(img, vib=0.0, sat=-100.0)
    assert (out.max(axis=2) - out.min(axis=2)).mean() < 1e-5
