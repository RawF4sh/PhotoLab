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
