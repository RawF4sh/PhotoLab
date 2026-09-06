"""Dark-scene tone stack: the shipped rules must give a dark-but-unclipped frame a real,
headroom-capped lift, and no later op (contrast, blacks, curve, sky grad, vignette) may erase it.
Measured per op on a synthetic dark image — the failure this pins was a +0.25 EV lift fully
cancelled by contrast/curve/vignette on real CR2s."""

import numpy as np

from autoedit.compose import load_compose, plan
from autoedit.contract import GlobalStatsModel
from autoedit.edit import apply_image, ops
from autoedit.imageio import luma
from autoedit.looks import Look, load as load_looks, shape
from autoedit.paths import config_path
from autoedit.rules import apply as rules_apply, load as load_rules

DARK = dict(mean_lum=0.20, mean_sat=0.15, shadow_clip=0.001, highlight_clip=0.0, haze=0.15,
            pro_stats_valid=1.0, lum_p10=0.07, lum_p50=0.17, lum_p90=0.40, lum_p99=0.6,
            fg_p50=0.17, dark_cov=0.25, headroom_ev=1.3, detail_rms=0.03,
            band_top_p50=0.30, band_mid_p50=0.17, band_bottom_p50=0.14)


def _dark_image(h=96, w=128):
    yy = np.linspace(0.32, 0.06, h, dtype=np.float32)[:, None]        # brighter top, dark bottom
    xx = 1.0 + 0.15 * np.sin(np.linspace(0, 6, w, dtype=np.float32))[None, :]
    l = np.clip(yy * xx, 0, 1)
    return np.stack([l * 1.02, l, l * 0.92], axis=-1).astype(np.float32)


def _settings(scene="landscape", intensity=0.8):
    rules = load_rules(config_path("rules.toml"))
    g = GlobalStatsModel(**DARK)
    gset, _ = rules_apply(rules, scene, g, {}, {}, intensity=1.0)
    look = load_looks(config_path("looks.toml")).get("natural", Look())
    shape(gset, look, intensity, is_global=True)
    return gset


def test_dark_frame_rules_lift_and_do_not_fight():
    s = _settings()
    assert s["exposure"] >= 0.6                     # EV-correct: 60% of log2(0.45/0.17)·2.2, capped by headroom
    assert s["exposure"] <= 1.3 * 0.8 + 1e-9        # never past headroom (×intensity)
    assert s["shadows"] >= 20                       # darkness, not just clipped pixels, opens shadows
    assert s.get("contrast", 0.0) == 0.0            # mid-pivot ops fade out on a dark source
    assert s.get("blacks", 0.0) == 0.0


def test_people_and_other_scenes_share_the_dark_policy():
    for scene in ("people", "other"):
        s = _settings(scene)
        assert s["exposure"] >= 0.5, scene
        assert s.get("contrast", 0.0) == 0.0 and s.get("blacks", 0.0) == 0.0, scene
        assert s["shadows"] >= 15, scene


def test_no_op_in_the_frozen_order_erases_the_lift():
    s = _settings()
    img = _dark_image()
    cur = img
    means = [float(luma(cur).mean())]
    steps = [
        lambda x: ops.exposure(x, s["exposure"]),
        lambda x: ops.tone(x, s.get("highlights", 0), s.get("shadows", 0), s.get("whites", 0), s.get("blacks", 0)),
        lambda x: ops.contrast(x, s["contrast"]) if s.get("contrast") else x,
        lambda x: ops.curve(x, s["curve"]) if s.get("curve") else x,
        lambda x: ops.dehaze(x, s["dehaze"]) if s.get("dehaze") else x,
        lambda x: ops.clarity(x, s["clarity"]) if s.get("clarity") else x,
    ]
    for fn in steps:
        cur = np.clip(fn(cur), 0, 1)
        means.append(float(luma(cur).mean()))
    drops = [a - b for a, b in zip(means, means[1:])]
    assert max(drops) < 0.01, f"an op erased brightness: per-op means {means}"
    assert means[-1] >= means[0] + 0.05, f"stack did not brighten a dark frame: {means}"
    # the full engine path (apply_ops) agrees with the per-op trace
    full = ops.apply_ops(img, s)
    assert float(luma(full).mean()) >= means[0] + 0.05


def test_compositional_moves_cannot_undo_a_dark_lift():
    s = _settings()
    img = _dark_image()
    moves = load_compose(config_path("compose.toml"))
    union = {"sky": np.zeros(img.shape[:2], bool), "subject": np.zeros(img.shape[:2], bool)}
    union["sky"][:30] = True                        # 31% sky → sky_grad fires
    union["subject"][40:80, 40:90] = True
    comp = plan(moves, union, img.shape[:2], intensity=0.8)
    assert {n for n, _a, _s in comp} == {"subject_vignette", "sky_grad", "frame_vignette"}
    out = apply_image(img, [], s, compositional=[(a, st) for _n, a, st in comp])
    before, after = float(luma(img).mean()), float(luma(out).mean())
    bottom_before = float(np.median(luma(img)[64:]))
    bottom_after = float(np.median(luma(out)[64:]))
    assert after >= before + 0.05
    assert bottom_after >= bottom_before + 0.03     # the dark bottom band came UP


def test_sky_grad_needs_real_sky_coverage():
    moves = load_compose(config_path("compose.toml"))
    sliver = {"sky": np.zeros((50, 50), bool)}
    sliver["sky"][:2] = True                        # 4% of the frame
    assert "sky_grad" not in {n for n, _a, _s in plan(moves, sliver, (50, 50), 1.0)}


def test_whites_lift_fades_without_highlight_headroom():
    """Bright limestone/snow: p90 below 0.82 but the 99.5th percentile already at white. The
    dull-highlight `whites` lift must not fire there (it was adding 0.5–1.4% clipping)."""
    rules = load_rules(config_path("rules.toml"))
    base = dict(mean_lum=0.55, mean_sat=0.1, shadow_clip=0.0, highlight_clip=0.0, haze=0.2,
                pro_stats_valid=1.0, fg_p50=0.5, lum_p10=0.1, lum_p90=0.70, lum_p99=0.99)
    roomy = GlobalStatsModel(**base, headroom_ev=1.0)
    tight = GlobalStatsModel(**base, headroom_ev=0.05)
    for scene in ("landscape", "other", "people"):
        w_roomy = rules_apply(rules, scene, roomy, {}, {}, intensity=1.0)[0]["whites"]
        w_tight = rules_apply(rules, scene, tight, {}, {}, intensity=1.0)[0]["whites"]
        assert w_roomy > w_tight, scene
        assert w_tight == 0.0, scene                    # neither positive lift has room to run


def test_whites_headroom_preserves_recovery_and_legacy():
    rules = load_rules(config_path("rules.toml"))
    base = GlobalStatsModel(mean_lum=0.55, mean_sat=0.1, shadow_clip=0.0,
                            highlight_clip=0.0, haze=0.2, pro_stats_valid=1.0,
                            fg_p50=0.5, lum_p90=0.70, headroom_ev=0.30)
    for scene, lifted, recovered in (("people", 3.9, -8.0), ("landscape", 4.5, -9.0),
                                    ("other", 4.5, -8.0)):
        assert abs(rules_apply(rules, scene, base, {}, {}, 1.0)[0]["whites"] - lifted) < 1e-9
        clipped = base.model_copy(update={"channel_highlight_clip": 0.2, "headroom_ev": 0.0})
        assert rules_apply(rules, scene, clipped, {}, {}, 1.0)[0]["whites"] == recovered
        legacy = base.model_copy(update={"pro_stats_valid": 0.0, "headroom_ev": 0.0})
        assert rules_apply(rules, scene, legacy, {}, {}, 1.0)[0]["whites"] == 3.0


def test_bright_tail_keeps_detail_through_the_full_finishing_stack():
    """Midtone foreground, bright background and a small near-white tail. Before contrast
    used headroom, this added 1.4–1.8 percentage points of clipping even with no exposure lift."""
    from autoedit import tonemetrics as tm
    from autoedit.imageio import Image
    from autoedit.stats import compute

    line = np.concatenate((np.linspace(0.3, 0.55, 800), np.linspace(0.65, 0.8, 150),
                           np.linspace(0.9, 1.0, 50))).astype(np.float32)
    rgb = np.broadcast_to(line[None, :, None], (64, 1000, 3)).copy()
    source, _ = compute(Image(rgb=rgb, exif={}), {})
    assert source.headroom_ev == 0.0 and source.lum_p90 > 0.6
    rules = load_rules(config_path("rules.toml"))
    look = load_looks(config_path("looks.toml"))["natural"]
    for scene in ("people", "landscape", "other"):
        settings, _ = rules_apply(rules, scene, source, {}, {}, intensity=1.0)
        shape(settings, look, 0.8, is_global=True)
        out = apply_image(rgb, [], settings)
        measured = tm.compare(tm.from_global(source), tm.describe(out))
        assert not measured["bright_pushed_to_clip"], (scene, measured)
        assert not measured["dark_made_darker"], (scene, measured)
        assert not np.array_equal(rgb, out)             # preserve detail while still editing


def test_exposure_and_whites_share_headroom_without_losing_the_foreground_lift():
    """A dark foreground plus a bright tail with half a stop of headroom. Independently
    budgeting exposure and whites used to clip 0.8–1.0% of this frame after the full stack."""
    from autoedit import tonemetrics as tm
    from autoedit.imageio import Image
    from autoedit.stats import compute

    line = np.concatenate((np.linspace(0.08, 0.4, 800), np.linspace(0.5, 0.7, 150),
                           np.linspace(0.73, 0.85, 50))).astype(np.float32)
    rgb = np.broadcast_to(line[None, :, None], (64, 1000, 3)).copy()
    source, _ = compute(Image(rgb=rgb), {})
    rules = load_rules(config_path("rules.toml"))
    look = load_looks(config_path("looks.toml"))["natural"]
    for scene in ("people", "landscape", "other"):
        settings, _ = rules_apply(rules, scene, source, {}, {}, intensity=0.8)
        shape(settings, look, 0.8, is_global=True)
        out = apply_image(rgb, [], settings)
        measured = tm.compare(tm.from_global(source), tm.describe(out))
        assert measured["d_fg_p50"] > 0.045, (scene, measured)
        assert not measured["bright_pushed_to_clip"], (scene, measured)
        assert not measured["dark_made_darker"], (scene, measured)
