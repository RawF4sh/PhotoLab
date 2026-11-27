"""Source-relative safety: destruction cannot hide behind net/global brightness."""
import numpy as np

from autoedit.quality import added_faults, assess_pixels, load_quality_config


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


def test_a_light_white_in_the_original_may_not_be_made_grey():
    source = np.full((96, 128, 3), 0.46, dtype=np.float32)
    light = np.zeros((96, 128), bool)
    light[13:16, 15:19] = True
    source[light] = 1.0

    def run(level, name="sun", src=source, **thresholds):
        out = src.copy()
        out[light] = level
        return assess_pixels(src, out, {name: light}, thresholds or None)

    grey = run(0.60)
    assert "dulled_light_source:sun" in grey["failures"]
    assert grey["metrics"]["sun_dulled_fraction"] == 1.0
    assert "dulled_light_source:sun" not in run(0.95)["failures"]
    bright = source.copy(); bright[light] = 0.95
    assert "dulled_light_source:sun" not in run(0.50, src=bright)["failures"]
    out = source.copy(); out[light] = 1.0
    ys, xs = np.nonzero(light); keep = np.arange(len(ys)) < len(ys) // 2
    out[ys[keep], xs[keep]] = 0.60
    assert "dulled_light_source:sun" not in assess_pixels(
        source, out, {"sun": light}, {"light_clip_fraction": 0.6})["failures"]
    assert "dulled_light_source:lights" in run(0.60, name="lights")["failures"]
    assert assess_pixels(source, source, {"sun": light})["passed"]


def _ramp_pair():
    source = _gray(np.linspace(.90, .979, 100))
    return source, np.full_like(source, .97)


def test_added_faults_charges_auto_only_for_what_simple_does_not_already_have():
    source, flat = _ramp_pair()
    texture = "encoded_highlight_texture_loss"
    # Simple already flattens the ramp and Auto adds nothing (same picture, or uniformly darker).
    assert added_faults(source, flat, None, None, flat) == ([], [texture])
    assert added_faults(source, flat * .9, None, None, flat) == ([], [texture])
    # Simple keeps the ramp and Auto flattens it.
    assert added_faults(source, flat, None, None, source) == ([texture], [])
    assert added_faults(source, flat, None, None, None) == ([texture], [])
    # No failure: nothing to split.
    assert added_faults(source, source, None, None, flat) == ([], [])
    # Two ramps: Simple flattens one, Auto flattens the other as well.
    two = np.concatenate([source, source], axis=1)
    half = two.copy(); half[:, :100] = .97
    assert added_faults(two, np.full_like(two, .97), None, None, half) == ([texture], [])
    # Each step passes alone, the result fails: a third of the contrast, then a twentieth.
    third, twentieth = .94 + (source - .94) / 3, .94 + (source - .94) / 20
    assert assess_pixels(source, third)["passed"] and assess_pixels(third, twentieth)["passed"]
    assert added_faults(source, twentieth, None, None, third) == ([texture], [])
    # An unmeasured baseline inherits nothing.
    nan = source.copy(); nan[0, 0, 0] = np.nan
    assert added_faults(source, flat, None, None, nan) == ([texture], [])


def test_added_faults_compares_failing_texture_patches_by_position():
    from unittest.mock import patch
    source, flat = _ramp_pair()
    texture = "encoded_highlight_texture_loss"
    low, fine = {"x": 3, "y": 0, "retention": 0.0}, {"x": 3, "y": 0, "retention": 1.0}

    def report(failures, *patches):
        return {"failures": list(failures), "metrics": {"comparison": {},
                                                        "highlight_texture": {"patches": list(patches)}}}
    first, clean = report([texture], low), report([])
    for base_patch, expect in ((fine, ([texture], [])), (low, ([], [texture]))):
        reports = iter((report([texture], base_patch), clean))      # base, then own
        with patch("autoedit.quality.assess_pixels", side_effect=lambda *a: next(reports)):
            assert added_faults(source, flat, None, None, flat, first=first) == expect


def _auto_fixture():
    """A pipeline plus a fake `assess_pixels` that answers from the two pictures it is given."""
    from autoedit.contract import RunConfig
    from autoedit.imageio import Image
    from autoedit.pipeline import Pipeline

    rgb = np.broadcast_to(np.linspace(.1, .55, 96, dtype=np.float32)[None, :, None], (64, 96, 3)).copy()
    rgb[:32, :, 2] += .08
    image = Image(rgb=rgb)
    pipe = Pipeline.build(RunConfig(style="auto", scene="daylight", intensity=1, auto_unreleased=True,
                                    backend="stub", compose=False, concepts=["sky"]))
    return pipe, image


class _FakeAssess:
    """Reports from a table keyed by (strength of the source picture or None, strength of the output)."""
    def __init__(self, table=None):
        self.table, self.pictures, self.source = table or {}, {}, None
        self.recording = None

    def _key(self, picture):
        if self.source is not None and np.array_equal(picture, self.source):
            return None
        return next((s for s, p in self.pictures.items() if np.array_equal(picture, p)), "?")

    def __call__(self, source, output, masks=None, thresholds=None, prepared=None):
        if self.recording is not None:
            self.source = np.array(source)
            self.pictures[self.recording] = np.array(output)
            failures = []
        else:
            failures = list(self.table.get((self._key(source), self._key(output)), []))
        halo = {"sky_edge_halo": .07}
        patches = [{"x": 0, "y": 0, "retention": 0.0}] if "encoded_highlight_texture_loss" in failures else []
        return {"passed": not failures, "failures": failures, "scopes": {},
                "metrics": {**halo, "comparison": {}, "highlight_texture": {"patches": patches}}}


def _record(pipe, image, fake, strengths=(0.0, .125, .25, .5, .75, 1.0)):
    from unittest.mock import patch
    with patch("autoedit.quality.assess_pixels", fake):
        for s in strengths:
            fake.recording = s
            pipe.process_one("retry.jpg", image=image, auto_strength=s, batch_group="one shoot")
    fake.recording = None


def _run(pipe, image, fake, **kwargs):
    from unittest.mock import patch
    import autoedit.pipeline as pipeline
    with patch.object(pipe.decider, "decide", wraps=pipe.decider.decide) as decider, \
         patch.object(pipe.segmenter, "segment", wraps=pipe.segmenter.segment) as segmenter, \
         patch.object(pipeline, "apply_image", wraps=pipeline.apply_image) as applied, \
         patch("autoedit.quality.assess_pixels", fake):
        out = pipe.process_one("retry.jpg", image=image, batch_group="one shoot", **kwargs)
    return out, decider.call_count, segmenter.call_count, applied.call_count


def test_auto_keeps_its_strength_when_simple_already_has_the_fault():
    texture = "encoded_highlight_texture_loss"
    pipe, image = _auto_fixture()
    fake = _FakeAssess()
    _record(pipe, image, fake)
    fake.table = {(None, 0.0): [texture], (None, .75): [texture], (None, 1.0): [texture]}
    for given, strength in ((None, 1.0), (.75, .75)):
        kwargs = {} if given is None else {"auto_strength": given}
        (pixels, result), decisions, segmentations, renders = _run(pipe, image, fake, **kwargs)
        audit = result.audit["auto"]
        assert audit["strength"] == strength and "safety_attempts" not in audit
        assert audit["fault_check"]["failures"] == [] and audit["fault_check"]["inherited"] == [texture]
        assert not result.needs_review
        assert any("the Simple edit already fails " + texture in n for n in result.notes)
        assert decisions == segmentations == 1 and renders == 3
        with patch_assess(fake):
            plain, _ = pipe.process_one("retry.jpg", image=image, auto_strength=strength, batch_group="one shoot")
        assert np.array_equal(pixels, plain)
    stages = []
    (shown, result), *_ = _run(pipe, image, fake, on_stage=lambda name, rgb: stages.append(name))
    assert stages.count("candidate_decode") == stages.count("final_float") == 1
    assert "safety_attempts" not in result.audit["auto"]


def patch_assess(fake):
    from unittest.mock import patch
    return patch("autoedit.quality.assess_pixels", fake)


def test_auto_is_pulled_back_for_a_fault_it_adds():
    texture, halo = "encoded_highlight_texture_loss", "sky_edge_halo"
    pipe, image = _auto_fixture()
    fake = _FakeAssess()
    _record(pipe, image, fake)
    # Auto adds a fault at full strength only.
    fake.table = {(None, 1.0): [halo], (0.0, 1.0): [halo]}
    (pixels, result), decisions, segmentations, _ = _run(pipe, image, fake)
    audit = result.audit["auto"]
    assert audit["strength"] == .75 and len(audit["safety_attempts"]) == 2
    assert audit["safety_attempts"][0] == {"strength": 1.0, "failures": [halo]}
    assert halo in result.notes[-1] and decisions == segmentations == 1
    with patch_assess(fake):
        plain, _ = pipe.process_one("retry.jpg", image=image, auto_strength=.75, batch_group="one shoot")
    assert np.array_equal(pixels, plain)
    # Simple already fails the check and Auto's own step fails it again at full strength.
    fake.table = {(None, 0.0): [texture], (None, .75): [texture], (None, 1.0): [texture], (0.0, 1.0): [texture]}
    (pixels, result), *_ = _run(pipe, image, fake)
    audit = result.audit["auto"]
    assert audit["strength"] == .75 and audit["safety_attempts"][0]["failures"] == [texture]
    stages = []
    (shown, replay), *_ = _run(pipe, image, fake, on_stage=lambda name, rgb: stages.append(name))
    assert stages.count("candidate_decode") == stages.count("final_float") == 1
    assert np.array_equal(pixels, shown)


def test_auto_ends_on_simple_when_every_strength_adds_a_fault():
    halo = "sky_edge_halo"
    pipe, image = _auto_fixture()
    fake = _FakeAssess()
    _record(pipe, image, fake)
    above = (.125, .25, .5, .75, 1.0)
    fake.table = {(None, s): [halo] for s in above} | {(0.0, s): [halo] for s in above}
    (pixels, result), *_ = _run(pipe, image, fake)
    audit = result.audit["auto"]
    assert audit["strength"] == 0 and audit["fault_check"]["failures"] == [] and not result.needs_review
    with patch_assess(fake):
        simple, _ = pipe.process_one("retry.jpg", image=image, auto_strength=0.0, batch_group="one shoot")
    assert np.array_equal(pixels, simple)


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
