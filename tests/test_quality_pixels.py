"""Source-relative safety: destruction cannot hide behind net/global brightness."""
import numpy as np

from autoedit.quality import assess_pixels, load_quality_config


def _gray(line):
    return np.broadcast_to(np.asarray(line, np.float32)[None, :, None], (40, len(line), 3)).copy()


def test_quality_identity_and_intentional_dark_remain_safe():
    for line in (np.linspace(.01, .12, 100), np.linspace(.05, .97, 100)):
        source = _gray(line)
        assert assess_pixels(source, source)["passed"]
        assert np.array_equal(source, _gray(line))


def test_quality_rejects_nan_and_invalid_shapes():
    source = _gray(np.linspace(.1, .8, 100))
    bad = source.copy(); bad[0, 0] = np.nan
    assert "output_invalid" in assess_pixels(source, bad)["failures"]
    assert not assess_pixels(source, bad[:, :-1])["passed"]


def test_new_clip_pixels_fail_even_when_net_clipping_is_unchanged():
    source = _gray(np.r_[np.ones(10), np.full(90, .5)])
    output = source.copy(); output[:, :10] = .5; output[:, 10:20] = 1
    result = assess_pixels(source, output)
    assert result["metrics"]["clipping"]["delta_rgb_high"] == 0
    assert "introduced_rgb_high_clipping" in result["failures"]


def test_new_channel_clipping_is_not_hidden_by_another_already_clipped_channel():
    from autoedit.quality import prepare_quality_source
    for end, before, after in (("high", [.5, 1., .5], [1., 1., .5]),
                                ("low", [.5, 0., .5], [0., 0., .5])):
        source = np.broadcast_to(np.asarray(before, np.float32), (32, 48, 3)).copy()
        output = np.broadcast_to(np.asarray(after, np.float32), source.shape).copy()
        prepared = prepare_quality_source(source)
        result = assess_pixels(source, output)
        clipping = result["metrics"]["clipping"]
        assert clipping[f"source_rgb_{end}"] == clipping[f"output_rgb_{end}"] == 1
        assert clipping[f"delta_rgb_{end}"] == 0
        assert clipping[f"new_rgb_{end}"] == 1
        assert f"introduced_rgb_{end}_clipping" in result["failures"]
        assert assess_pixels(source, output, prepared=prepared) == result
        assert assess_pixels(source, source, prepared=prepared)["passed"]


def test_shadow_clipping_and_darkness_are_independent_failures():
    source = _gray(np.linspace(.08, .2, 100))
    output = source * .05
    result = assess_pixels(source, output)
    assert "introduced_luma_low_clipping" in result["failures"]
    assert "dark_made_darker" in result["failures"]
    assert assess_pixels(source, source + .02)["passed"]


def test_encoded_texture_collapse_is_detected_below_white():
    source = _gray(np.linspace(.90, .979, 100))
    output = np.full_like(source, .97)
    result = assess_pixels(source, output)
    assert not result["metrics"]["comparison"]["bright_pushed_to_clip"]
    assert "encoded_highlight_texture_loss" in result["failures"]
    # Tiny float differences do not count as preserved encoded texture.
    almost_flat = .965 + (source-.9) * .001
    assert "encoded_highlight_texture_loss" in assess_pixels(source, almost_flat)["failures"]
    noise = np.random.default_rng(1).normal(0, .002, source.shape).astype(np.float32)
    assert "encoded_highlight_texture_loss" in assess_pixels(source, output+noise)["failures"]


def test_clean_affine_highlight_ramp_survives_rgb8_transition_phase_changes():
    from autoedit.quality import prepare_quality_source
    for width in (100, 256, 512):
        source = _gray(np.linspace(.84, .96, width))
        prepared = prepare_quality_source(source)
        for gain, offset in ((.97, .01), (1., .003), (.90, .05)):
            output = source*gain + offset
            result = assess_pixels(source, output)
            assert result["passed"], (width, gain, offset, result["failures"])
            texture = result["metrics"]["highlight_texture"]
            assert texture["available"]
            assert texture["minimum_retention"] > .5
            assert any(p["lag_pixels"] > 1 for p in texture["patches"])
            assert assess_pixels(source, output, prepared=prepared) == result


def test_region_metrics_and_proxy_masks_are_explicit():
    source = _gray(np.linspace(.1, .9, 100))
    sky = np.zeros((20, 50), bool); sky[:10] = True
    result = assess_pixels(source, source, masks={"sky": sky})
    assert result["passed"]
    assert "sky" in result["metrics"]["regions"]
    assert result["metrics"]["regions"]["sky"]["source"]["lum"] > 0
    assert load_quality_config()["clip_budget"] == .005


def test_jpeg_roundtrip_of_clean_gradient_does_not_manufacture_a_failure(tmp_path):
    from autoedit.imageio import save_image, load_image
    source = _gray(np.linspace(.06, .96, 256))
    path = tmp_path / "gradient.jpg"
    save_image(source, str(path), "jpeg", 95)
    result = assess_pixels(source, load_image(str(path)).rgb)
    assert result["passed"], result["failures"]


def test_mask_targeted_patch_detects_small_texture_loss_between_grid_patches():
    source = np.full((600, 600, 3), .4, np.float32)
    source[290:310, 290:310] = np.linspace(.9, .979, 20, dtype=np.float32)[None, :, None]
    output = source.copy(); output[290:310, 290:310] = .97
    mask = np.zeros((600, 600), bool); mask[290:310, 290:310] = True
    report = assess_pixels(source, output, masks={"snow": mask})
    assert "encoded_highlight_texture_loss" in report["failures"]


def test_prepared_source_has_exact_parity_and_reuses_source_statistics():
    from unittest.mock import patch
    from autoedit import quality
    source = _gray(np.linspace(.04, .97, 160))
    mask = np.zeros(source.shape[:2], bool); mask[:16] = True
    masks = {"sky": mask}
    prepared = quality.prepare_quality_source(source, masks=masks)
    for output in (source, source*.7, np.minimum(source*1.3, 1)):
        plain = assess_pixels(source, output, masks=masks)
        with patch.object(quality.stats, "compute", wraps=quality.stats.compute) as compute:
            cached = assess_pixels(source, output, masks=masks, prepared=prepared)
            assert compute.call_count == 1  # only the candidate needs new statistics
        assert cached == plain
    assert "detail_rms" in cached["metrics"]["source"]


def test_prepared_source_rejects_changed_pixels_masks_or_thresholds():
    import pytest
    from autoedit.quality import prepare_quality_source
    source = _gray(np.linspace(.1, .8, 100))
    mask = np.zeros(source.shape[:2], bool); mask[:16] = True
    prepared = prepare_quality_source(source, masks={"sky": mask})
    changed = source.copy(); changed[0, 0] += .01
    with pytest.raises(ValueError):
        assess_pixels(changed, changed, masks={"sky": mask}, prepared=prepared)
    with pytest.raises(ValueError):
        assess_pixels(source, source, masks={"sky": ~mask}, prepared=prepared)
    with pytest.raises(ValueError):
        assess_pixels(source, source, masks={"sky": mask}, thresholds={"dark_source": .2}, prepared=prepared)


def test_thin_image_preserves_three_bands_when_sampling():
    source = np.full((3, 4000, 3), .3, np.float32)
    result = assess_pixels(source, source)
    assert result["passed"] and result["metrics"]["sample_shape"] == [3, 1000, 3]


def test_new_sky_rim_requires_a_brighter_peak_than_both_flanks():
    from autoedit.quality import sky_edge_halo
    from autoedit.imageio import luma

    cfg = load_quality_config()
    y, x = np.ogrid[:144, :512]
    ridge = 65 + np.rint(6 * np.sin(x / 35))
    sky = y < ridge
    source = np.repeat(np.where(sky, 0.7, 0.34).astype(np.float32)[..., None], 3, axis=2)
    # Darkening the sky on an imperfect feather leaves a thin bright strip of sky untouched.
    feather = np.clip((ridge - y - 1) / 3, 0.0, 1.0)
    output = (source - 0.30 * feather[..., None]).astype(np.float32)
    masks = {"sky": sky & (x < 245), "cloud": sky & (x >= 245)}
    assessment = assess_pixels(source, output, masks)
    assert "sky_edge_halo" in assessment["failures"]
    assert assessment["metrics"]["sky_edge_halo"] > cfg["ridge_delta"]
    reduced = source + 0.25 * (output - source)
    assert "sky_edge_halo" not in assess_pixels(source, reduced, masks)["failures"]
    # An existing rim, or a stronger clean step with no bright rim, must pass.
    assert sky_edge_halo(luma(output), luma(output), masks, cfg) == 0
    sharp = source - 0.30 * sky[..., None]
    assert sky_edge_halo(luma(source), luma(sharp), masks, cfg) < 1e-5
    assert sky_edge_halo(luma(source), luma(output), {}, cfg) is None


def test_small_light_and_skin_faults_are_measured_against_the_same_region_of_the_original():
    source = np.full((96, 128, 3), 0.46, dtype=np.float32)
    light = np.zeros((96, 128), bool)
    light[13:16, 15:19] = True
    skin = np.zeros_like(light)
    skin[40:50, 45:52] = True
    source[light] = 0.95
    source[skin] = (0.4, 0.26, 0.2)
    masks = {"sun": light, "skin": skin}
    assert assess_pixels(source, source, masks)["passed"]
    output = source.copy()
    output[light] = 1.0
    output[skin] = (0.25, 0.7, 0.25)
    report = assess_pixels(source, output, masks)
    assert "blown_light_source:sun" in report["failures"]
    assert "skin_colour_shift:skin" in report["failures"]
    assert "introduced_luma_high_clipping" not in report["failures"]


def test_auto_retries_share_one_decision_and_return_the_accepted_editable_settings():
    from unittest.mock import patch
    from autoedit.contract import RunConfig
    from autoedit.imageio import Image
    from autoedit.pipeline import Pipeline

    rgb = np.broadcast_to(np.linspace(.1, .55, 96, dtype=np.float32)[None, :, None], (64, 96, 3)).copy()
    rgb[:32, :, 2] += .08
    image = Image(rgb=rgb)
    pipe = Pipeline.build(RunConfig(style="auto", scene="daylight", intensity=1, auto_unreleased=True,
                                    backend="stub", compose=False, concepts=["sky"]))
    fault = {"failures": ["sky_edge_halo"], "metrics": {"sky_edge_halo": .18}}
    ok = {"failures": [], "metrics": {"sky_edge_halo": .07}}
    resolved = []
    with patch.object(pipe.decider, "decide", wraps=pipe.decider.decide) as decider, \
         patch.object(pipe.segmenter, "segment", wraps=pipe.segmenter.segment) as segmenter, \
         patch("autoedit.quality.assess_pixels", side_effect=[fault, ok]):
        pixels, result = pipe.process_one("retry.jpg", image=image, batch_group="one shoot", on_resolved=resolved.append)
    assert decider.call_count == segmenter.call_count == len(resolved) == 1
    audit = result.audit["auto"]
    assert audit["strength"] == .75 and len(audit["safety_attempts"]) == 2
    assert "sky_edge_halo" in result.notes[-1] and result.batch_group == "one shoot"
    with patch("autoedit.quality.assess_pixels", return_value=ok):
        expected, fresh = pipe.process_one("retry.jpg", image=image, auto_strength=.75, batch_group="one shoot")
    assert np.array_equal(pixels, expected) and fresh.global_settings == result.global_settings
    stages = []
    with patch("autoedit.quality.assess_pixels", side_effect=[fault, ok, ok]):
        shown, replay = pipe.process_one("retry.jpg", image=image, on_stage=lambda name, rgb: stages.append(name))
    assert stages.count("candidate_decode") == stages.count("final_float") == 1
    assert np.array_equal(pixels, shown) and replay.audit["auto"]["safety_attempts"] == audit["safety_attempts"]


def test_auto_quality_uses_the_active_landscape_budget_only():
    from autoedit.quality_runner import style_thresholds
    cfg = load_quality_config()
    style = {"name": "auto", "intent": ["darker", "richer"], "target": "auto_landscape",
             "clip_budget": .011, "shadow_clip_budget": .018}
    for label in ("sunset", "mist", "twilight", "daylight", "landscape"):
        audit = {"local_style": style, "auto": {"target": "auto_landscape", "scene": label}}
        active = style_thresholds(audit, cfg, "auto")
        assert active["preserve_exposure"] and active["richer"]
        assert (active["clip_budget"], active["shadow_clip_budget"]) == (.011, .018)
        assert active["ridge_delta"] == cfg["ridge_delta"] and active["banding_fail"] == cfg["banding_fail"]
        assert style_thresholds(audit, cfg, "standard") == cfg
    for info in ({"scene": "people", "target": "auto_landscape"},
                 {"scene": "sunset", "target": None, "fallback": "Simple"}):
        assert style_thresholds({"local_style": style, "auto": info}, cfg, "auto") == cfg
