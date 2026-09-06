"""Post-edit safety: the review gate must read the EDITED output, the quality assessment must
flag clipping/oversaturation, and reference transfer must not bypass the chroma gate."""

import numpy as np
from PIL import Image as PILImage

from autoedit.contract import GlobalStatsModel, RunConfig
from autoedit.edit.gates import assess
from autoedit.pipeline import Pipeline
from autoedit.web.jobs import _safety


def _g(**kw):
    base = dict(mean_lum=0.5, mean_sat=0.2, shadow_clip=0.0, highlight_clip=0.0, haze=0.1)
    base.update(kw)
    return GlobalStatsModel(**base)


def test_assess_flags_clipping():
    score, needs, reason, warns = assess(_g(), _g(highlight_clip=0.10))
    assert needs and "highlight" in reason and score < 0.85


def test_assess_flags_oversaturation():
    score, needs, reason, warns = assess(_g(mean_sat=0.2), _g(mean_sat=0.4))
    assert any("saturation" in w for w in warns) and score < 1.0


def test_assess_clean_edit_passes():
    score, needs, reason, warns = assess(_g(), _g(mean_sat=0.22))
    assert not needs and score >= 0.85 and reason == ""


def test_added_highlight_clipping_triggers_review_below_absolute_threshold():
    from autoedit import tonemetrics as tm

    pre = _g(highlight_clip=0.002, lum_p90=0.7)
    post = _g(highlight_clip=0.008, lum_p90=0.75)
    assert tm.compare(tm.from_global(pre), tm.from_global(post))["bright_pushed_to_clip"]
    score, needs, reason, _ = assess(pre, post)
    assert needs and score <= 0.75 and "highlight clipping increased +0.60%" in reason
    # This must remain a hard trigger even when a caller relaxes the review score threshold
    # or the request asks to preserve a dark exposure.
    assert assess(pre, post, thr={"review_score": 0.1}, preserve_exposure=True)[1]


def test_added_clipping_budget_is_configurable_and_source_relative():
    pre = _g(highlight_clip=0.009, lum_p90=0.9)
    assert not assess(pre, pre)[1]
    assert not assess(pre, _g(highlight_clip=0.005))[1]
    assert not assess(pre, _g(highlight_clip=0.013))[1]
    assert assess(pre, _g(highlight_clip=0.016))[1]
    assert not assess(pre, _g(highlight_clip=0.016), thr={"clip_budget": 0.01})[1]


def test_new_single_channel_clipping_is_not_hidden_by_luminance():
    pre = _g(rgb_highlight_clip_any=0.001)
    post = _g(rgb_highlight_clip_any=0.01)  # luminance clipping stays zero
    score, needs, reason, _ = assess(pre, post)
    assert needs and score <= 0.75 and "RGB highlight clipping increased" in reason


def test_jobs_safety_reads_post_edit(tmp_path):
    from autoedit.contract import Result

    ok = Result(image="a", scene="landscape", confidence=0.6, needs_review=False)
    bad = Result(image="b", scene="landscape", confidence=0.6, needs_review=True,
                 review_reason="highlight clipping 9%")
    assert _safety(ok)[0] == "finished"
    assert _safety(bad) == ("review", "highlight clipping 9%")


def test_pipeline_populates_post_edit_fields(tmp_path):
    a = np.zeros((96, 96, 3), np.uint8)
    a[:48] = [120, 150, 210]
    p = tmp_path / "p.jpg"
    PILImage.fromarray(a, "RGB").save(str(p))
    _, res = Pipeline.build(RunConfig(backend="stub")).process_one(str(p), True)
    assert res.post_edit_stats is not None          # stats came from the edited output
    assert 0.0 <= res.quality_score <= 1.0
    assert isinstance(res.needs_review, bool)


def test_pipeline_routes_new_clipping_to_review():
    from autoedit.imageio import Image

    line = np.concatenate((np.linspace(0.3, 0.55, 800), np.linspace(0.65, 0.8, 150),
                           np.linspace(0.9, 1.0, 50))).astype(np.float32)
    rgb = np.broadcast_to(line[None, :, None], (64, 1000, 3)).copy()
    pipe = Pipeline.build(RunConfig(backend="stub", concepts=[], compose=False))
    # Reintroduce the old contrast policy (and switch the highlight knee off): a later recipe
    # or tuning change can still clip, and the gate must route it to review.
    pipe.rules["other"]["global"]["contrast"] = "8"
    pipe.rules["other"]["global"]["highlight_rolloff"] = "0"
    _, result = pipe.process_one("bright-fixture.png", image=Image(rgb=rgb, exif={}))
    assert result.post_edit_stats.highlight_clip < 0.05  # below the old hard review threshold
    assert result.post_edit_stats.highlight_clip - result.global_stats.highlight_clip > 0.005
    status, reason = _safety(result)
    assert status == "review" and "highlight clipping increased" in reason


def _pro(**kw):
    """Freshly measured (pro_stats_valid) dark source: foreground median 0.17, bottom band 0.14."""
    base = dict(mean_lum=0.2, mean_sat=0.15, shadow_clip=0.0, highlight_clip=0.0, haze=0.1,
                pro_stats_valid=1.0, fg_p50=0.17, band_bottom_p50=0.14)
    base.update(kw)
    return GlobalStatsModel(**base)


def test_gate_flags_dark_source_made_darker():
    score, needs, reason, warns = assess(_pro(), _pro(fg_p50=0.15, band_bottom_p50=0.12))
    assert needs and "dark source made darker" in reason and score <= 0.75


def test_gate_passes_dark_source_improved():
    score, needs, reason, warns = assess(_pro(), _pro(fg_p50=0.30, band_bottom_p50=0.26))
    assert not needs and score == 1.0 and not warns


def test_gate_passes_night_frame_left_alone():
    night = _pro(fg_p50=0.08, band_bottom_p50=0.05)
    score, needs, reason, warns = assess(night, night)
    assert not needs and score == 1.0                      # relative, never an absolute target


def test_gate_ignores_bright_sources_preserve_intent_and_legacy_stats():
    bright = _pro(fg_p50=0.60, band_bottom_p50=0.55)
    assert not assess(bright, _pro(fg_p50=0.50, band_bottom_p50=0.48))[1]   # bright → a bit dimmer: fine
    darker = _pro(fg_p50=0.15, band_bottom_p50=0.12)
    assert assess(_pro(), darker)[1]                                          # control: flagged…
    assert not assess(_pro(), darker, preserve_exposure=True)[1]              # …unless the user asked for dark
    legacy = GlobalStatsModel(mean_lum=0.2, mean_sat=0.1, shadow_clip=0.0, highlight_clip=0.0, haze=0.1)
    assert not assess(legacy, legacy)[1]                                      # zero-filled stats never trip it
