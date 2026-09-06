"""Highlight knee: the shoulder that lets an exposure lift compress into white instead of clipping.
Math on synthetic linear arrays, the op inside the frozen order, and the shipped rule."""

import numpy as np

from autoedit.contract import GlobalStatsModel
from autoedit.edit import ops
from autoedit.imageio import luma, srgb_to_linear
from autoedit.paths import config_path
from autoedit.rules import apply as rules_apply, load as load_rules


def test_knee_is_identity_below_the_knee_and_monotonic_above():
    x = np.linspace(0.0, 2.0, 2001, dtype=np.float32)
    y = ops.highlight_rolloff(x, 40.0)                  # kp = 0.8·W ≈ 0.76 linear
    kp = ops._WHITE_LIN * 0.8
    assert np.array_equal(y[x <= kp], x[x <= kp])      # untouched below the knee
    assert np.all(np.diff(y) >= -1e-7)                 # monotonic
    assert y.max() < ops._WHITE_LIN and y[-1] > ops._WHITE_LIN - 0.02   # asymptote = clip level
    # continuous at the knee with slope 1
    i = int(np.searchsorted(x, kp))
    assert abs((y[i + 1] - y[i]) / (x[i + 1] - x[i]) - 1.0) < 0.05
    assert np.array_equal(ops.highlight_rolloff(x, 0.0), x)          # amount 0 → identity


def test_exposure_with_knee_lifts_without_clipping():
    # bright gradient: top 20% of pixels sit within one stop of white
    l = np.linspace(0.05, 0.62, 200, dtype=np.float32)                # linear
    rgb = np.repeat(np.stack([l, l, l], -1)[None], 4, axis=0)
    rgb = np.clip(rgb, 0, 1) ** (1 / 2.2)                             # crude gamma encode
    plain = ops.exposure(rgb, 1.0)
    knee = ops.exposure(rgb, 1.0, rolloff=40.0)
    assert (luma(plain) > 0.98).mean() > 0.05                        # bare lift clips the top
    assert (luma(knee) > 0.98).mean() == 0.0                          # knee stays under the clip level
    assert knee.max() < 1.0
    assert luma(knee).mean() > luma(rgb).mean() + 0.1                 # …and still a real lift
    assert np.abs(ops.exposure(rgb, 0.0) - rgb).max() < 2e-6          # ev 0, no knee → identity


def test_apply_ops_routes_the_knee_and_identity_holds():
    rgb = np.random.default_rng(2).random((6, 8, 3)).astype(np.float32)
    assert np.array_equal(ops.apply_ops(rgb, {}), rgb)
    assert np.array_equal(ops.apply_ops(rgb, {"highlight_rolloff": 0.0, "exposure": 0.0}), rgb)
    kneed = ops.apply_ops(rgb, {"highlight_rolloff": 40.0})           # knee alone: only bright px move
    lin = srgb_to_linear(rgb)
    bright = lin.max(axis=-1) > ops._WHITE_LIN * 0.8
    assert np.abs(kneed[~bright] - rgb[~bright]).max() < 2e-6       # gamma round-trip only
    assert (kneed[bright] <= rgb[bright] + 1e-6).all()
    assert "highlight_rolloff" in ops.OP_NAMES and ops.OP_BOUNDS["highlight_rolloff"] == (0.0, 100.0)


def test_shipped_rule_turns_the_knee_on_only_when_headroom_is_short():
    rules = load_rules(config_path("rules.toml"))
    base = dict(mean_lum=0.5, mean_sat=0.1, shadow_clip=0.0, highlight_clip=0.0, haze=0.2,
                pro_stats_valid=1.0, fg_p50=0.17, lum_p10=0.07, lum_p90=0.5, dark_cov=0.2)
    for scene in ("landscape", "other", "people"):
        roomy = rules_apply(rules, scene, GlobalStatsModel(**base, headroom_ev=1.5), {}, {}, 1.0)[0]
        tight = rules_apply(rules, scene, GlobalStatsModel(**base, headroom_ev=0.0), {}, {}, 1.0)[0]
        assert roomy["highlight_rolloff"] == 0.0, scene
        assert tight["highlight_rolloff"] == 40.0, scene
        # with no headroom the knee buys a bounded lift (0.3 EV) that was 0 before
        assert abs(tight["exposure"] - 0.3) < 1e-9, scene
        assert roomy["exposure"] > tight["exposure"], scene
    legacy = rules_apply(rules, "landscape", GlobalStatsModel(mean_lum=0.5, mean_sat=0.1, shadow_clip=0.0,
                                                              highlight_clip=0.0, haze=0.2), {}, {}, 1.0)[0]
    assert legacy["highlight_rolloff"] == 0.0
