"""Reference-style color matching (edit/reference.py) — LAB color transfer math."""

import numpy as np

from autoedit.edit.ops import apply_ops
from autoedit.edit.reference import (
    color_transfer,
    lab_to_rgb,
    masked_reference_loss,
    reference_loss,
    rgb_to_lab,
    top_gradient_to_reference,
    tune_regions_to_reference,
    tune_settings_to_reference,
)


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


def test_reference_feedback_reduces_look_loss():
    y, x = np.mgrid[0:48, 0:64].astype(np.float32)
    base = 0.24 + 0.36 * (x / 63.0) + 0.12 * (y / 47.0)
    source = np.clip(np.stack([base * 0.82, base * 0.96, base * 1.08], axis=2), 0.0, 1.0)
    # Build the target with real ops: darker blacks, stronger contrast, warmer/greener color.
    reference = apply_ops(source, {
        "exposure": 0.10,
        "contrast": 22.0,
        "curve": 12.0,
        "blacks": -10.0,
        "vibrance": 18.0,
        "temp": 10.0,
        "hsl_green_sat": 12.0,
        "clarity": 8.0,
    })

    before = reference_loss(source, reference)
    tuned, report = tune_settings_to_reference(source, reference, {}, strength=0.85, passes=5)
    after = reference_loss(apply_ops(source, tuned), reference)

    assert after < before * 0.92
    assert report["after_loss"] < report["before_loss"]
    assert any(abs(v) > 0.01 for v in tuned.values())


def test_reference_feedback_strength_zero_is_noop_settings():
    source = _const(12, 12, [0.35, 0.4, 0.45])
    reference = _const(12, 12, [0.5, 0.5, 0.5])
    tuned, report = tune_settings_to_reference(source, reference, {"contrast": 5.0}, strength=0.0)

    assert tuned == {"contrast": 5.0}
    assert report["passes"] == 0


def test_reference_region_feedback_reduces_masked_loss():
    source = _const(32, 48, [0.55, 0.60, 0.68])
    source[18:] = [0.22, 0.38, 0.20]
    reference = source.copy()
    reference[:18] = apply_ops(reference[:18], {"exposure": -0.35, "contrast": 20.0, "dehaze": 10.0})
    mask = np.zeros((32, 48), np.float32)
    mask[:18] = 1.0

    settings = {"sky": {"saturation": 2.0}}
    before = masked_reference_loss(apply_ops(source, settings["sky"]), reference, mask)
    tuned, reports = tune_regions_to_reference(
        source, reference, settings, {"sky": mask}, strength=0.9, passes=4)
    after = masked_reference_loss(apply_ops(source, tuned["sky"]), reference, mask)

    assert after < before * 0.9
    assert reports and reports[0]["name"] == "sky"
    assert tuned["sky"].get("exposure", 0.0) < 0.0 or tuned["sky"].get("highlights", 0.0) < 0.0


def test_reference_top_gradient_darkens_stormy_reference_sky():
    source = _const(40, 60, [0.55, 0.60, 0.68])
    source[18:] = [0.24, 0.38, 0.22]
    reference = source.copy()
    reference[:18] = apply_ops(reference[:18], {"exposure": -0.45, "contrast": 28.0, "clarity": 10.0})

    settings, report = top_gradient_to_reference(source, reference, strength=0.9)

    assert settings["exposure"] < 0.0
    assert settings["highlights"] < 0.0
    assert report["reference_top"] < report["before_top"]
