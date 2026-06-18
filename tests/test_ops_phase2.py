"""Phase 2 vocabulary: HSV roundtrip, filmic curve, color grading (split-tone), targeted HSL."""

import numpy as np

from autoedit.contract import RunConfig
from autoedit.edit.ops import (
    OP_NAMES,
    _hsv_to_rgb,
    _rgb_to_hsv,
    apply_ops,
    color_grade,
    curve,
    targeted_hsl,
)
from autoedit.imageio import luma
from autoedit.pipeline import Pipeline


def test_hsv_roundtrip():
    rng = np.random.default_rng(0)
    rgb = rng.random((16, 16, 3)).astype(np.float32)
    h, s, v = _rgb_to_hsv(rgb)
    out = _hsv_to_rgb(h, s, v)
    assert np.abs(out - rgb).max() < 1e-3


def test_all_new_ops_zero_is_noop():
    rng = np.random.default_rng(1)
    img = rng.random((8, 8, 3)).astype(np.float32)
    out = apply_ops(img, {k: 0.0 for k in OP_NAMES})
    assert np.abs(out - img).max() == 0.0


def test_curve_increases_contrast():
    ramp = np.linspace(0, 1, 256, dtype=np.float32)
    img = np.repeat(ramp[None, :, None], 3, axis=2)  # gray ramp 1x256x3
    out = curve(img, 60.0)
    assert luma(out).std() > luma(img).std()


def test_color_grade_warms_highlights():
    img = np.full((4, 4, 3), 0.8, np.float32)  # bright (highlight) gray
    out = color_grade(img, hi_temp=80.0)
    assert out[..., 0].mean() > 0.8 and out[..., 2].mean() < 0.8  # +R, -B


def test_targeted_hsl_desaturates_only_green():
    img = np.zeros((1, 2, 3), np.float32)
    img[0, 0] = [0.2, 0.7, 0.2]  # green
    img[0, 1] = [0.7, 0.2, 0.2]  # red
    out = targeted_hsl(img, green_sat=-100.0)
    chroma = lambda a: a.max(axis=2) - a.min(axis=2)
    assert chroma(out)[0, 0] < chroma(img)[0, 0] * 0.5      # green desaturated
    assert abs(chroma(out)[0, 1] - chroma(img)[0, 1]) < 0.05  # red ~untouched


def test_style_injects_grade(tmp_path):
    from PIL import Image as PILImage

    a = np.zeros((64, 64, 3), np.uint8)
    a[:32] = [100, 140, 200]
    yy, xx = np.mgrid[0:64, 0:64]
    a[(((xx - 32) / 16) ** 2 + ((yy - 40) / 18) ** 2) <= 1.0] = [200, 150, 120]
    p = tmp_path / "p.jpg"
    PILImage.fromarray(a, "RGB").save(str(p))

    g_warm = Pipeline.build(RunConfig(backend="stub", style="warm_film")).process_one(str(p), False)[1].global_settings
    g_nat = Pipeline.build(RunConfig(backend="stub", style="natural")).process_one(str(p), False)[1].global_settings
    assert g_warm.get("grade_highlights_temp", 0) > 0     # warm film grades highlights warm
    # the default now carries a subtle cinematic grade (cool shadows / warm highlights)
    assert g_nat.get("grade_highlights_temp", 0) > 0
    assert g_nat.get("grade_shadows_temp", 0) < 0
