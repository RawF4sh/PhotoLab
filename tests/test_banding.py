"""Banding detector + auto-remediation (spec Phase 1). A posterized smooth sky scores high and
triggers the reduce/fail action; a clean gradient passes; remediation actually lowers the score."""

import numpy as np

from autoedit.edit import banding
from autoedit.edit.ops import apply_ops


def _gradient(h=256, w=512, lo=0.78, hi=0.94):
    g = np.linspace(lo, hi, h)[:, None].repeat(w, 1)
    return np.repeat(g[..., None], 3, axis=2).astype(np.float32)


def _posterize(rgb, levels):
    return (np.round(rgb * (levels - 1)) / (levels - 1)).astype(np.float32)


def test_posterized_sky_flags_banding():
    src = _gradient()
    post = _posterize(src, levels=14)            # harsh contour steps
    score, action, cov = banding.assess(src, post)
    assert score > banding.DEFAULTS["reduce_ops"]
    assert action in ("reduce", "fail")
    assert cov > 0.5                              # a smooth gradient is mostly "smooth region"


def test_clean_gradient_passes():
    src = _gradient()
    score, action, _ = banding.assess(src, src)  # no edit → no new banding
    assert action == "pass" and score <= banding.DEFAULTS["warning"]


def test_already_posterized_source_flags_absolute_banding():
    src = _posterize(_gradient(), levels=14)
    score, action, cov = banding.assess(src, src)
    assert score > banding.DEFAULTS["warning"]
    assert action in ("warn", "reduce", "fail")
    assert cov > 0.5


def test_textured_region_not_flagged():
    yy, xx = np.mgrid[0:256, 0:256]
    tex = (((xx + yy) % 2)).astype(np.float32)                      # fine checkerboard → all detail
    tex = np.repeat(tex[..., None], 3, axis=2)
    score, action, cov = banding.assess(tex, _posterize(tex, 10))
    assert cov < banding.DEFAULTS["min_coverage"]                   # no smooth pixels → not assessed
    assert action == "pass"


def test_remediation_reduces_banding():
    # stretch a narrow-range gradient hard → banding; reducing the stretch ops lowers the score
    src = _gradient(lo=0.82, hi=0.90)
    strong = apply_ops(src, {"dehaze": 24, "contrast": 12, "curve": 16})
    gentle = apply_ops(src, {"dehaze": 24 * 0.5, "contrast": 12 * 0.5, "curve": 16 * 0.5})
    s_strong = banding.banding_score(*(_lum(src), _lum(strong)), banding.smooth_region_mask(_lum(src)))
    s_gentle = banding.banding_score(*(_lum(src), _lum(gentle)), banding.smooth_region_mask(_lum(src)))
    assert s_gentle <= s_strong


def _lum(rgb):
    from autoedit.imageio import luma
    return luma(rgb)
