"""Independent candidate math, hard search budgets, and production-control fidelity."""
import numpy as np

from autoedit.contract import RunConfig
from autoedit.imageio import Image, _compatible_preview, develop_ev, linear_to_srgb
from autoedit.workflows import (WorkflowEngine, _region_rgb, adaptive_contrast,
                                load_workflow_options, photographic_reinhard)


def _image(value=0.2, shape=(32, 48, 3)):
    return Image(rgb=np.full(shape, value, np.float32), path="sample.jpg")


def test_current_workflow_preserves_production_pixels_and_settings():
    from autoedit.pipeline import Pipeline
    cfg = RunConfig(concepts=[])
    source = _image()
    before = source.rgb.copy()
    expected, er = Pipeline.build(cfg).process_one("sample.jpg", image=source)
    actual, ar = WorkflowEngine(cfg, "current").process("sample.jpg", image=source)
    assert np.array_equal(actual, expected)
    assert ar.global_settings == er.global_settings and ar.composition == er.composition
    assert np.array_equal(source.rgb, before)
    assert ar.audit["control_pixels_unchanged"]
    checkpoints = ar.audit["checkpoints"]
    assert [row["stage"] for row in checkpoints][0] == "candidate_decode"
    assert [row["stage"] for row in checkpoints][-1] == "final_float"
    assert all("assessment" in row and "proxy" in row["scope"] for row in checkpoints)


def test_global_first_lifts_dark_source_and_is_deterministic():
    source = _image()
    before = source.rgb.copy()
    engine = WorkflowEngine(RunConfig(concepts=[]), "global-first")
    a, ar = engine.process("sample.jpg", image=source)
    b, br = engine.process("sample.jpg", image=source)
    assert np.array_equal(a, b) and np.array_equal(source.rgb, before)
    assert float(a.mean()) > float(source.rgb.mean())
    assert not ar.needs_review and ar.audit["developed_source_safety"]["passed"]
    assert ar.global_settings == br.global_settings
    assert set(ar.stage_timings_ms) >= {"decode", "segment_resolve", "stats", "decide", "apply", "assess"}
    assert not engine.last_masks


def test_workflow_toml_proposal_goldens_cover_dark_highlights_and_snow_wb():
    from autoedit.stats import compute
    engine = WorkflowEngine(RunConfig(concepts=[]), "global-first")
    cases = [
        (np.full((32, 48, 3), .25, np.float32), {},
         {"exposure": .8309625965, "shadows": 3., "contrast": 8., "temp": 0., "tint": 0., "vibrance": 2.}),
        (np.concatenate([np.full((95, 100, 3), .2, np.float32), np.full((5, 100, 3), .95, np.float32)]), {},
         {"exposure": 0., "shadows": 6., "contrast": 8., "temp": 0., "tint": 0., "vibrance": 2.}),
        (np.tile(np.array([.60, .65, .70], np.float32), (32, 48, 1)), {"snow": np.ones((32, 48), bool)},
         {"exposure": 0., "shadows": 0., "contrast": 8., "temp": 8., "tint": -4., "vibrance": 2.}),
    ]
    for rgb, masks, expected in cases:
        source = Image(rgb, path="source.cr2")
        measured, _ = compute(source, masks)
        actual = engine._proposal(source, measured, masks)
        assert set(actual) == set(expected)
        assert all(abs(actual[k]-expected[k]) < 1e-6 for k in expected), (actual, expected)


def test_optimizer_obeys_budget_and_repeatable_settings():
    opts = {"max_passes": 1, "max_evaluations": 8}
    engine = WorkflowEngine(RunConfig(concepts=[]), "optimized", opts)
    a, ar = engine.process("sample.jpg", image=_image())
    b, br = engine.process("sample.jpg", image=_image())
    assert ar.audit["search"]["evaluations"] <= 8
    assert ar.audit["search"]["passes"] <= 1
    assert np.array_equal(a, b) and ar.global_settings == br.global_settings
    assert ar.audit["search"]["objective_vector"]
    alternatives = ar.audit["search"]["pareto_candidates"]
    assert len(alternatives) >= 2
    for i, row in enumerate(alternatives):
        a = row["objective_vector"]
        assert set(a) >= {"foreground_deficit", "color_change", "highlight_change"}
        for j, other in enumerate(alternatives):
            if i != j:
                b = other["objective_vector"]
                assert not (all(a[k] <= b[k] for k in a) and any(a[k] < b[k] for k in a))
    limits = load_workflow_options({"max_passes": 100, "max_evaluations": 999, "max_seconds": 999,
                                   "proxy_edge": 4000})["search"]
    assert (limits["max_passes"], limits["max_evaluations"], limits["max_seconds"], limits["proxy_edge"]) == (6, 144, 30., 512)


def test_optimizer_watchdog_fallback_is_identity():
    engine = WorkflowEngine(RunConfig(concepts=[]), "optimized", {"max_seconds": 0})
    source = _image()
    output, result = engine.process("sample.jpg", image=source)
    assert np.array_equal(output, source.rgb)
    assert result.global_settings == {}
    assert result.audit["search"]["evaluations"] == 0
    assert "identity" in result.audit["search"]["fallback_reason"]


def test_adaptive_contrast_preserves_pivot_and_bounds_before_clipping():
    x = np.repeat(np.linspace(0.05, 0.95, 101, dtype=np.float32)[None, :, None], 3, axis=-1)
    assert adaptive_contrast(x, 0, 0.2) is x
    y = adaptive_contrast(x, 50, 0.2)
    assert y.min() >= -1e-6 and y.max() <= 1.0+1e-6
    assert np.all(np.diff(y[0, :, 0]) > 0)
    pivot = np.full((4, 4, 3), 0.2, np.float32)
    assert np.array_equal(adaptive_contrast(pivot, 30, 0.2), pivot)


def test_reinhard_identity_known_math_and_channel_ratios():
    source = np.array([[[0., 0., 0.], [.5, .5, .5], [.8, .4, .2]]], np.float32)
    assert photographic_reinhard(source, 0., 2.) is source
    output = photographic_reinhard(source, 1., 2.)
    assert np.array_equal(output[0, 0], source[0, 0])
    # Extended Reinhard: .5*(1+.5/4)/(1+.5) = .375.
    assert np.allclose(output[0, 1], .375, atol=1e-7)
    assert abs(float(output[0, 2, 0]/output[0, 2, 1])-2.) < 1e-6
    assert abs(float(output[0, 2, 1]/output[0, 2, 2])-2.) < 1e-6
    assert np.isfinite(output).all() and output.min() >= 0 and output.max() <= 1


def test_reinhard_compresses_hdr_monotonically_without_highlight_plateau():
    levels = np.linspace(.01, 4., 1024, dtype=np.float32)
    rgb = np.repeat(levels[None, :, None], 3, axis=-1)
    output = photographic_reinhard(rgb, .75, 2., ceiling=.98)
    assert output.max() <= .980001
    assert np.all(np.diff(output[0, :, 0]) > 0)
    # Unlike clipping an HDR multiply, more than the top endpoint retains variation.
    encoded = np.rint(linear_to_srgb(output)*255).astype(np.uint8)
    assert np.unique(encoded[0, -256:, 0]).size > 10


def test_reinhard_workflow_is_an_explicit_reversible_option():
    source = _image()
    options = {"tone_mapping": "reinhard"}
    engine = WorkflowEngine(RunConfig(concepts=[]), "global-first", options)
    output, result = engine.process("sample.jpg", image=source)
    assert np.isfinite(output).all() and output.shape == source.rgb.shape
    assert result.audit["options"]["workflow"]["tone_mapping"] == "reinhard"
    assert load_workflow_options()["workflow"]["tone_mapping"] == "shoulder"


def test_cached_linear_search_render_matches_uncached_pixels_exactly():
    from autoedit.imageio import srgb_to_linear
    rng = np.random.default_rng(982)
    source = rng.uniform(.05, .85, (24, 32, 3)).astype(np.float32)
    linear = srgb_to_linear(source)
    saved = linear.copy()
    settings = {"exposure": .3, "temp": 4., "tint": -2., "shadows": 8., "contrast": 3., "vibrance": 2.}
    for mode in ("shoulder", "current_curve", "reinhard", "none"):
        engine = WorkflowEngine(RunConfig(concepts=[]), "optimized", {"tone_mapping": mode})
        for s in ({}, {"contrast": 3.}, settings):
            plain = engine._render(source, s, .3)
            cached = engine._render(source, s, .3, linear_source=linear)
            assert np.array_equal(cached, plain), mode
            assert np.array_equal(linear, saved)


def test_regional_exposure_bound_preserves_snow_before_global_rolloff():
    rgb = np.repeat(np.linspace(.90, .98, 100, dtype=np.float32)[None, :, None], 32, axis=0)
    rgb = np.repeat(rgb, 3, axis=-1)
    out, settings = _region_rgb(rgb, np.ones(rgb.shape[:2], np.float32), {"exposure": .3},
                                load_workflow_options()["regional"])
    assert settings["exposure"] == 0.0
    assert np.array_equal(out, rgb)
    assert np.unique(out[..., 0]).size == 100


def test_raw_headroom_caps_any_channel_and_missing_preview_has_no_fixed_lift():
    lin = np.full((100, 100, 3), .05, np.float32)
    lin[:10, :, 0] = .8
    camera = np.full((100, 100, 3), .5, np.float32)
    ev = develop_ev(lin, camera, {"raw_mode": "preview_ev_headroom"})
    out = linear_to_srgb(lin * (2**ev))
    assert float((out.max(-1) > .98).mean()) <= .005
    assert develop_ev(lin, None, {"raw_mode": "preview_ev"}) == 0
    assert develop_ev(lin, None, {"raw_mode": "linear_srgb"}) == 0


def test_preview_compatibility_rejects_crop_and_unknown_orientation():
    landscape = np.ones((60, 90, 3), np.float32)
    assert _compatible_preview(np.ones((20, 30, 3), np.float32), landscape)
    assert _compatible_preview(np.ones((30, 20, 3), np.float32), landscape)
    assert not _compatible_preview(np.ones((30, 20, 3), np.float32), landscape, True)
    assert not _compatible_preview(np.ones((30, 30, 3), np.float32), landscape)
    assert not _compatible_preview(np.full((20, 30, 3), np.nan), landscape)


def test_linear_srgb_decode_and_preview_fallback_have_auditable_provenance():
    import sys
    import types
    from autoedit.imageio import load_image, downscale_long_edge
    calls = []
    class Raw:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def postprocess(self, **kwargs):
            calls.append(kwargs)
            return np.full((32, 48, 3), 16384, np.uint16)

        def extract_thumb(self):
            raise ValueError("no preview")
    previous = sys.modules.get("rawpy")
    sys.modules["rawpy"] = types.SimpleNamespace(imread=lambda path: Raw())
    try:
        developed = load_image("missing.cr2", {"raw_mode": "linear_srgb"})
        expected = linear_to_srgb(np.full((32, 48, 3), 16384/65535, np.float32))
        assert np.array_equal(developed.rgb, expected) and developed.decode_ev == 0
        assert calls[-1]["gamma"] == (1.0, 1.0) and calls[-1]["no_auto_bright"]
        assert developed.decode_info["transfer"] == "srgb"
        fallback = load_image("missing.cr2", {"raw_mode": "preview_ev"})
        assert np.array_equal(fallback.rgb, expected) and fallback.decode_ev == 0
        assert fallback.decode_info["fallback_reason"] and not fallback.decode_info["preview_used"]
        assert downscale_long_edge(fallback, 16).decode_info == fallback.decode_info
        historical = load_image("missing.cr2", {"raw_mode": "none"})
        assert calls[-1]["gamma"] == (2.222, 4.5)
        assert np.array_equal(historical.rgb, np.full((32, 48, 3), 16384/65535, np.float32))
    finally:
        if previous is None:
            sys.modules.pop("rawpy", None)
        else:
            sys.modules["rawpy"] = previous


def test_fixed_masks_enable_reproducible_regional_ablation_without_model():
    mask = np.ones((32, 48), bool)
    engine = WorkflowEngine(RunConfig(backend="stub"), "global-first",
                            {"fixed_masks": {"mountain": mask}, "regional": True})
    output, result = engine.process("sample.jpg", image=_image(.1))
    assert np.isfinite(output).all()
    assert list(engine.last_masks) == ["mountain"]
    assert result.audit["fixed_mask_names"] == ["mountain"]
    assert result.audit["checkpoints"].get("after_regions")


def test_optional_segmentation_follows_global_normalization_by_default():
    seen = []
    class Recorder:
        name = "recorder"

        def segment(self, img, concepts):
            seen.append(float(img.rgb.mean()))
            return []
    engine = WorkflowEngine(RunConfig(concepts=["sky"]), "global-first", {"segmentation": True})
    engine.segmenter = Recorder()
    source = _image()
    output, result = engine.process("sample.jpg", image=source)
    assert len(seen) == 1 and seen[0] > float(source.rgb.mean())
    assert abs(seen[0]-float(output.mean())) < 1e-6
    assert result.audit["options"]["workflow"]["segment_order"] == "after_global"


def test_explicit_low_key_intent_preserves_dark_pixels_in_both_candidates():
    source = _image(.08)
    for workflow in ("global-first", "optimized"):
        cfg = RunConfig(concepts=[], prompt="Preserve exposure for this low-key night scene", raw_mode="preview_ev")
        engine = WorkflowEngine(cfg, workflow, {"composition": True, "vignette": True,
                                               "palette": True, "tone_mapping": "reinhard"})
        output, result = engine.process("sample.jpg", image=source)
        assert np.array_equal(output, source.rgb)
        assert result.global_settings == {} and result.audit["preserve_exposure"]
        assert engine.decode_options()["raw_mode"] == "linear_srgb"
        if workflow == "optimized":
            assert result.audit["search"]["evaluations"] == 0


def test_backend_audit_records_calls_even_if_no_masks_are_found():
    from unittest.mock import patch
    source = _image(.4)
    # A requested heavy backend must not be reported as used by a global-only pass.
    cfg = RunConfig(backend="sam3", concepts=["snow"])
    with patch("autoedit.segment.make_segmenter", side_effect=AssertionError("unexpected model construction")):
        engine = WorkflowEngine(cfg, "global-first")
        _, result = engine.process("sample.jpg", image=source)
    assert engine.last_backend == "none" and result.audit["segmentation_events"] == []
    class EmptySam:
        name = "sam3"
        calls = 0
        def segment(self, image, concepts):
            self.calls += 1
            return []
    observed = EmptySam()
    engine = WorkflowEngine(cfg, "global-first", {"segmentation": True})
    engine.segmenter = observed
    _, result = engine.process("sample.jpg", image=source)
    assert observed.calls == 1 and not engine.last_masks
    assert result.audit["actual_backend"] == "sam3"
    assert result.audit["segmentation_events"] == [{"backend": "sam3", "concepts": ["snow"]}]


def test_backend_audit_distinguishes_fixed_masks_stub_and_disabled_current():
    source = _image(.4)
    mask = np.ones(source.rgb.shape[:2], bool)
    for workflow in ("current", "global-first"):
        engine = WorkflowEngine(RunConfig(concepts=["snow"]), workflow,
                                {"segmentation": True, "fixed_masks": {"snow": mask}})
        _, result = engine.process("sample.jpg", image=source)
        assert result.audit["actual_backend"] == "fixed-masks"
        assert result.audit["segmentation_events"][0]["backend"] == "fixed-masks"
    for concepts, expected in (([], "none"), (["snow"], "stub")):
        engine = WorkflowEngine(RunConfig(concepts=concepts), "current")
        original = engine.segmenter
        _, result = engine.process("sample.jpg", image=source)
        assert result.audit["actual_backend"] == expected
        assert engine.segmenter is original


def test_stage_diagnostic_detects_erased_recovery_without_new_hard_gate():
    from autoedit.quality import assess_pixels
    from autoedit.workflows import summarize_stage_recovery
    source = np.full((32, 48, 3), .12, np.float32)
    recovered = source + .14
    final = source + .04
    rows = [{"stage": label, "assessment": assess_pixels(source, values)}
            for label, values in (("candidate_decode", source), ("after_global", recovered), ("final_float", final))]
    assert rows[-1]["assessment"]["passed"]  # Still brighter than source; no new failure criterion.
    metric = summarize_stage_recovery(rows)
    assert metric["available"] and not metric["hard_gate_applied"]
    assert metric["peak_stage"] == "after_global" and metric["final_stage"] == "final_float"
    assert abs(metric["peak_foreground_delta"]-.14) < 1e-6
    assert abs(metric["final_foreground_delta"]-.04) < 1e-6
    assert abs(metric["recovered_then_lost"]-.10) < 1e-6
    assert abs(metric["fraction_of_recovery_lost"]-5/7) < 1e-6
    assert summarize_stage_recovery({row["stage"]: {"assessment": row["assessment"]} for row in rows}) == metric
    assert not summarize_stage_recovery([])["available"]
    steady = summarize_stage_recovery(rows[:1])
    assert steady["recovered_then_lost"] == 0 and steady["fraction_of_recovery_lost"] is None


def test_audit_options_snapshot_survives_reused_engine_consistency_changes():
    import json
    source = _image(.2)
    engine = WorkflowEngine(RunConfig(concepts=[], analysis_long_edge=64), 'global-first',
                            {'consistency': True, 'consistency_bias': -.15})
    _, first = engine.process('first.jpg', image=source)
    sidecar_at_render = json.loads(first.model_dump_json())
    earlier_row = {'resolved_audit': first.audit}  # The benchmark retains the same audit object.
    first_settings = dict(first.global_settings)
    engine.options['workflow']['consistency_bias'] = 0.
    engine.options['workflow']['preserve_terms'].append('second-photo-only')
    _, second = engine.process('second.jpg', image=source)
    assert first.audit['options'] == sidecar_at_render['audit']['options']
    assert earlier_row['resolved_audit']['options'] == sidecar_at_render['audit']['options']
    assert first.audit['options']['workflow']['consistency_bias'] == -.15
    assert second.audit['options']['workflow']['consistency_bias'] == 0.
    assert 'second-photo-only' not in first.audit['options']['workflow']['preserve_terms']
    assert first.global_settings == first_settings
