"""Quality gates: the chroma (oversaturation) gate must cap colour increase, preserve a
neutral edit, protect snow/skin, and leave a within-budget edit untouched."""

import numpy as np

from autoedit.edit import apply_image
from autoedit.edit.gates import chroma_guard
from autoedit.imageio import luma


def _chroma(rgb):
    return rgb.max(-1) - rgb.min(-1)


def test_chroma_guard_caps_oversaturation():
    src = np.full((8, 8, 3), 0.5, np.float32)
    src[..., 0] = 0.6  # mildly red source
    edited = src.copy()
    edited[..., 0] = 0.95  # cranked WAY up → big chroma jump
    edited[..., 2] = 0.25
    out, frac = chroma_guard(src, edited, max_increase=0.30)
    assert frac > 0.99                                   # every pixel was over budget
    # output chroma is pulled to ~source*(1.3) + small additive, far below the cranked edit
    assert _chroma(out).mean() < _chroma(edited).mean() * 0.6
    assert _chroma(out).mean() <= _chroma(src).mean() * 1.30 + 0.05


def test_chroma_guard_palette_allowance_permits_intentional_neutral_color():
    src = np.full((8, 8, 3), 0.5, np.float32)
    edited = np.full((8, 8, 3), [0.46, 0.52, 0.60], np.float32)
    default, _ = chroma_guard(src, edited, max_increase=0.30)
    graded, frac = chroma_guard(src, edited, max_increase=0.30, neutral_allowance=0.16)

    assert _chroma(default).mean() < _chroma(edited).mean()
    assert np.allclose(graded, edited)
    assert frac == 0.0


def test_chroma_guard_preserves_luma():
    rng = np.random.default_rng(3)
    src = rng.random((16, 16, 3)).astype(np.float32)
    edited = np.clip(src * 1.5, 0, 1)  # saturation + brightness change
    out, _ = chroma_guard(src, edited, max_increase=0.2)
    assert np.abs(luma(out) - luma(edited)).max() < 1e-4  # gate sheds chroma, not brightness


def test_chroma_guard_within_budget_is_noop():
    src = np.full((4, 4, 3), 0.4, np.float32)
    src[..., 2] = 0.6
    edited = src.copy()
    edited[..., 2] = 0.62  # tiny increase, within +30%
    out, frac = chroma_guard(src, edited, max_increase=0.30)
    assert frac == 0.0
    assert np.abs(out - edited).max() == 0.0


def test_chroma_guard_protects_snow():
    src = np.full((8, 8, 3), 0.8, np.float32)  # near-white "snow"
    src[..., 2] = 0.82
    edited = src.copy()
    edited[..., 2] = 0.95               # a blue cast creeping into the snow
    protect = np.ones((8, 8), np.float32)
    out, _ = chroma_guard(src, edited, max_increase=0.30, protect=protect)
    # protected region barely allowed to gain chroma → stays close to neutral source
    assert _chroma(out).mean() < _chroma(src).mean() + 0.03


def test_apply_image_gate_off_is_identity_path():
    rng = np.random.default_rng(4)
    base = rng.random((10, 10, 3)).astype(np.float32)
    a = apply_image(base, [], {"saturation": 80.0})                 # gate off
    b = apply_image(base, [], {"saturation": 80.0}, chroma_max_increase=0.30)  # gate on
    assert _chroma(b).mean() < _chroma(a).mean()                    # gate tamed the +80 saturation
