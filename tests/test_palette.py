"""OKLab + the regional split-tone palette engine.

Guards: OKLab round-trips; a blue palette cools shadows (negative OKLab b) without touching
lightness; neutral protection + the guard mask hold whites/protected pixels neutral; strength 0 is
a strict no-op; and prompt/auto palette selection maps obvious colour stories.
"""

from types import SimpleNamespace

import numpy as np

from autoedit.edit.oklab import linear_to_oklab, oklab_to_linear
from autoedit.edit.palette import palette_grade
from autoedit.contract import RunConfig
from autoedit.imageio import Image, linear_to_srgb, srgb_to_linear
from autoedit.palette import auto_palette_for_image, load_palettes, palette_for_prompt
from autoedit.paths import configs_dir
from autoedit.pipeline import Pipeline
import os


def test_oklab_roundtrip():
    rng = np.linspace(0.0, 1.0, 12)
    lin = np.stack(np.meshgrid(rng, rng, rng), -1).reshape(-1, 3).astype(np.float32)
    back = oklab_to_linear(linear_to_oklab(lin))
    assert np.allclose(lin, back, atol=1e-4)


def _palettes():
    return load_palettes(os.path.join(configs_dir(), "palettes.toml"))


def _gray(v):  # flat neutral gray image at gamma value v
    return np.full((8, 8, 3), v, np.float32)


def test_cool_blue_cools_shadows_preserves_lightness():
    pals = _palettes()
    shadow = _gray(0.2)                                  # a dark midtone-shadow gray
    out = palette_grade(shadow, pals["cool_blue"], strength=1.0)
    b_before = linear_to_oklab(srgb_to_linear(shadow))[..., 2].mean()
    lab_after = linear_to_oklab(srgb_to_linear(out))
    assert lab_after[..., 2].mean() < b_before - 0.01     # b went negative → bluer
    # lightness (OKLab L) essentially unchanged — colour moved, brightness didn't
    L_before = linear_to_oklab(srgb_to_linear(shadow))[..., 0].mean()
    assert abs(lab_after[..., 0].mean() - L_before) < 0.01


def test_warm_gold_does_not_cool_shadows():
    pals = _palettes()
    shadow = _gray(0.2)
    out = palette_grade(shadow, pals["warm_gold"], strength=1.0)
    b_before = linear_to_oklab(srgb_to_linear(shadow))[..., 2].mean()
    b_after = linear_to_oklab(srgb_to_linear(out))[..., 2].mean()
    assert b_after >= b_before


def test_neutral_protection_keeps_highlights_neutral():
    pals = _palettes()
    hi = _gray(0.95)                                      # near-white highlight
    out = palette_grade(hi, pals["cool_blue"], strength=1.0)
    # with neutral_protection 0.9 the bright pixel barely moves
    assert np.max(np.abs(out - hi)) < 0.02


def test_guard_mask_holds_protected_pixels():
    pals = _palettes()
    img = _gray(0.3)
    protect = np.zeros((8, 8), np.float32)
    protect[:, :4] = 1.0                                  # left half protected (e.g. snow/skin)
    out = palette_grade(img, pals["cool_blue"], strength=1.0, protect=protect)
    assert np.allclose(out[:, :4], img[:, :4], atol=1e-3)  # protected half untouched
    assert np.max(np.abs(out[:, 4:] - img[:, 4:])) > 0.01  # graded half changed


def test_strength_zero_is_identity():
    pals = _palettes()
    img = _gray(0.4)
    assert np.array_equal(palette_grade(img, pals["cool_blue"], 0.0), img)


def test_prompt_to_palette():
    pals = _palettes()
    assert palette_for_prompt("cool blue winter", pals) == "cool_blue"
    assert palette_for_prompt("warm sunset over the bay", pals) == "warm_gold"
    assert palette_for_prompt("teal orange cinematic", pals) == "teal_orange"
    assert palette_for_prompt("nothing relevant here", pals) is None


def test_auto_palette_selects_cool_winter_landscape():
    pals = _palettes()
    stats = SimpleNamespace(mean_lum=0.55, mean_sat=0.06, haze=0.52, warmth=0.0,
                            mean_chroma=0.018, sky_lum=0.75)
    name, strength, reason = auto_palette_for_image("landscape", stats, {"sky", "water"}, pals)
    assert name == "cool_blue"
    assert 0.20 <= strength <= 0.40                       # unrequested grades whisper (capped)
    assert "cool" in reason


def test_auto_palette_never_cools_a_warm_scene():
    """Regression for the two blind-test losses: hazy low-chroma alpine scenes with POSITIVE
    warmth (+0.015/+0.023) must not get a cool grade — the warmth veto is hard."""
    pals = _palettes()
    for w in (0.015, 0.023, 0.008):
        stats = SimpleNamespace(mean_lum=0.55, mean_sat=0.15, haze=0.60, warmth=w,
                                mean_chroma=0.023, sky_lum=0.70)
        name, _s, _r = auto_palette_for_image("landscape", stats, {"sky", "mountain", "snow"}, pals)
        assert name != "cool_blue", f"cool grade fired on warm scene (warmth={w})"


def test_auto_palette_selects_warm_scene():
    pals = _palettes()
    stats = SimpleNamespace(mean_lum=0.58, mean_sat=0.20, haze=0.22, warmth=0.055,
                            mean_chroma=0.05, sky_lum=0.70)
    name, strength, reason = auto_palette_for_image("landscape", stats, {"sky"}, pals)
    assert name == "warm_gold"
    assert 0.30 <= strength <= 0.55                       # capped like every inferred grade
    assert "warm" in reason


def test_auto_palette_passes_uncertain_scene():
    pals = _palettes()
    stats = SimpleNamespace(mean_lum=0.48, mean_sat=0.18, haze=0.20, warmth=0.0,
                            mean_chroma=0.04, sky_lum=0.0)
    name, strength, reason = auto_palette_for_image("other", stats, {"subject"}, pals)
    assert name is None
    assert strength == 0.0
    assert reason == ""


def test_pipeline_caps_inferred_palette_after_intensity():
    import autoedit.edit.apply as apply_mod
    import autoedit.palette as palette_mod

    strengths: list[float] = []

    def record_grade(rgb, pal, strength, protect=None):
        strengths.append(float(strength))
        return rgb

    original_grade = apply_mod.palette_grade
    original_auto = palette_mod.auto_palette_for_image
    try:
        apply_mod.palette_grade = record_grade
        palette_mod.auto_palette_for_image = (
            lambda *_args, **_kwargs: ("warm_gold", 0.55, "forced auto test")
        )
        image = Image(rgb=np.full((64, 64, 3), 0.5, np.float32))

        auto_cfg = RunConfig(
            backend="stub", concepts=["sky"], intensity=2.0, palette_strength=1.0,
        )
        auto_pipe = Pipeline.build(auto_cfg)
        auto_pipe.banding = {}
        auto_pipe.process_one("synthetic.jpg", do_apply=True, image=image)
        assert strengths[-1] == 0.55

        explicit_cfg = auto_cfg.model_copy(update={"palette": "warm_gold"})
        explicit_pipe = Pipeline.build(explicit_cfg)
        explicit_pipe.banding = {}
        explicit_pipe.process_one("synthetic.jpg", do_apply=True, image=image)
        assert strengths[-1] == 2.0                      # explicit user choice remains uncapped
    finally:
        apply_mod.palette_grade = original_grade
        palette_mod.auto_palette_for_image = original_auto
