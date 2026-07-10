"""Rules evaluator: helpers, the sandbox, and apply()."""

import numpy as np
import pytest

from autoedit.contract import GlobalStatsModel, RegionStatsModel
from autoedit.paths import config_path
from autoedit.rules import apply, eval_expr
from autoedit.rules import load as load_rules


def test_eval_helpers():
    assert eval_expr("clamp(5, 0, 3)", {}) == 3.0
    assert eval_expr("if(1 > 0, 2, 9)", {}) == 2.0
    assert eval_expr("if(1 > 0, 2, 1 / 0)", {}) == 2.0   # unchosen branch is not evaluated
    assert eval_expr("2 if 1 > 0 else 1 / 0", {}) == 2.0
    assert eval_expr("max(min(0.7, 1), 0)", {}) == 0.7


def test_eval_reads_context():
    assert eval_expr("mean_lum * 2", {"mean_lum": 0.25}) == 0.5


def test_sandbox_blocks_attribute_access():
    with pytest.raises(Exception):
        eval_expr("(1).__class__", {})


def test_sandbox_blocks_unknown_call():
    with pytest.raises(Exception):
        eval_expr("__import__('os')", {})


def test_apply_lifts_buried_skin():
    rules = {"people": {
        "global": {"vibrance": "6"},
        "skin": {"exposure": "clamp(0.5 * max(0, mean_lum - region_lum), 0, 0.5)"},
    }}
    g = GlobalStatsModel(mean_lum=0.6, mean_sat=0.2, shadow_clip=0.0, highlight_clip=0.0, haze=0.0)
    region_stats = {"skin": RegionStatsModel(name="skin", lum=0.2, sat=0.3, clip_low=0, clip_high=0, coverage=0.1)}
    gset, rset = apply(rules, "people", g, region_stats, {"skin": "skin"}, intensity=1.0)
    assert gset["vibrance"] == 6.0
    # mean_lum 0.6, region 0.2 -> 0.5*0.4 = 0.2 lift
    assert abs(rset["skin"]["exposure"] - 0.2) < 1e-6


def test_shipped_landscape_sky_avoids_mask_imprinted_stretch_ops():
    rules = load_rules(config_path("rules.toml"))
    g = GlobalStatsModel(
        mean_lum=0.55, mean_sat=0.08, shadow_clip=0.0, highlight_clip=0.0,
        haze=0.52, sky_lum=0.82, sky_sat=0.04, blown_sky=0.02,
    )
    region_stats = {
        "sky": RegionStatsModel(name="sky", lum=0.82, sat=0.04, clip_low=0, clip_high=0.02, coverage=0.4)
    }
    _, rset = apply(rules, "landscape", g, region_stats, {"sky": "sky"}, intensity=1.0)
    sky = rset["sky"]
    for op in ("exposure", "highlights", "contrast", "clarity", "dehaze"):
        assert sky.get(op, 0.0) == 0.0


def test_shipped_rules_recover_channel_clipped_highlights():
    rules = load_rules(config_path("rules.toml"))
    clean = GlobalStatsModel(
        mean_lum=0.55, mean_sat=0.08, shadow_clip=0.0, highlight_clip=0.0, haze=0.1,
        channel_highlight_clip=0.0,
    )
    clipped = clean.model_copy(update={"channel_highlight_clip": 0.25, "r_highlight_clip": 0.25})

    clean_global, _ = apply(rules, "landscape", clean, {}, {}, intensity=1.0)
    clipped_global, _ = apply(rules, "landscape", clipped, {}, {}, intensity=1.0)

    assert clipped_global["highlights"] < clean_global["highlights"]
    assert clipped_global["whites"] < clean_global["whites"]


def test_shipped_pro_tone_rules_adapt_to_percentiles():
    """The v0.4 'visibly finished' policy: flat/washed frames get decisive levels + contrast;
    already-contrasty frames stay gentle; zero-filled percentile stats reproduce old behavior."""
    rules = load_rules(config_path("rules.toml"))

    flat = GlobalStatsModel(                              # flat overcast: narrow spread, washed
        mean_lum=0.5, mean_sat=0.08, shadow_clip=0.0, highlight_clip=0.0, haze=0.2,
        pro_stats_valid=1.0, lum_p10=0.13, lum_p50=0.40, lum_p90=0.48, fg_p50=0.45,
        detail_rms=0.02,
    )
    gset, _ = apply(rules, "landscape", flat, {}, {}, intensity=1.0)
    assert gset["contrast"] >= 10                         # decisive contrast on a flat frame
    assert gset["blacks"] <= -10                          # deliberate black point
    assert gset["whites"] > 5                             # dull highlights placed brighter

    crisp = GlobalStatsModel(                             # contrasty frame: full spread, deep blacks
        mean_lum=0.5, mean_sat=0.2, shadow_clip=0.0, highlight_clip=0.0, haze=0.2,
        pro_stats_valid=1.0, lum_p10=0.02, lum_p50=0.5, lum_p90=0.92, fg_p50=0.5,
        detail_rms=0.06,
    )
    gset, _ = apply(rules, "landscape", crisp, {}, {}, intensity=1.0)
    assert gset["contrast"] == 3.0                        # already contrasty → gentle
    assert gset["blacks"] == 0.0                          # blacks already deep → never crush more
    assert gset["clarity"] == 3.0                         # already detailed → minimum clarity

    legacy = GlobalStatsModel(                            # zero-filled percentiles → old behavior
        mean_lum=0.55, mean_sat=0.08, shadow_clip=0.0, highlight_clip=0.0, haze=0.1,
    )
    gset, _ = apply(rules, "landscape", legacy, {}, {}, intensity=1.0)
    assert gset["contrast"] == 5.0 and gset["blacks"] == 0.0 and gset["clarity"] == 6.0
    assert abs(gset["exposure"] - 0.0) < 1e-9             # old mean_lum formula at 0.55 → 0


def test_shipped_exposure_targets_foreground_not_sky():
    """A dark foreground under a big bright sky must be lifted by fg_p50, which the sky-skewed
    mean would have blocked."""
    rules = load_rules(config_path("rules.toml"))
    skyheavy = GlobalStatsModel(
        mean_lum=0.60, mean_sat=0.1, shadow_clip=0.0, highlight_clip=0.0, haze=0.3,
        pro_stats_valid=1.0, lum_p10=0.06, lum_p50=0.55, lum_p90=0.95, fg_p50=0.28,
        detail_rms=0.03,
    )
    gset, _ = apply(rules, "landscape", skyheavy, {}, {}, intensity=1.0)
    assert gset["exposure"] > 0.15                        # (0.50-0.28)*0.9 ≈ +0.20 EV for the land


def test_snow_dominant_scene_gets_extra_shadow_lift():
    """P3: sub-scene branching via a stat (snow_cov), not a new scene class — alpine shadow
    detail under snow reads flatter than a typical landscape shadow and gets a touch more lift."""
    rules = load_rules(config_path("rules.toml"))
    base = dict(mean_lum=0.55, mean_sat=0.1, shadow_clip=0.1, highlight_clip=0.0,
                haze=0.2, pro_stats_valid=1.0)
    plain = GlobalStatsModel(**base, snow_cov=0.0)
    snowy = GlobalStatsModel(**base, snow_cov=0.5)
    g_plain, _ = apply(rules, "landscape", plain, {}, {}, intensity=1.0)
    g_snowy, _ = apply(rules, "landscape", snowy, {}, {}, intensity=1.0)
    assert g_snowy["shadows"] == g_plain["shadows"] + 3.0

    at_threshold = GlobalStatsModel(**base, snow_cov=0.2)
    capped_plain = GlobalStatsModel(**{**base, "shadow_clip": 0.5}, snow_cov=0.0)
    capped_snow = GlobalStatsModel(**{**base, "shadow_clip": 0.5}, snow_cov=0.5)
    g_threshold, _ = apply(rules, "landscape", at_threshold, {}, {}, intensity=1.0)
    g_capped_plain, _ = apply(rules, "landscape", capped_plain, {}, {}, intensity=1.0)
    g_capped_snow, _ = apply(rules, "landscape", capped_snow, {}, {}, intensity=1.0)
    assert g_threshold["shadows"] == g_plain["shadows"]
    assert g_capped_plain["shadows"] == g_capped_snow["shadows"] == 30.0


def test_wildlife_exposes_for_subject_not_the_frame():
    """P3: a dark subject (wildlife) against a bright sky/background must be exposed for the
    ANIMAL, not judged by mean_lum — the priority chain is subj_lum > fg_p50 > mean_lum."""
    rules = load_rules(config_path("rules.toml"))
    bright_frame_dark_subject = GlobalStatsModel(
        mean_lum=0.70, mean_sat=0.1, shadow_clip=0.0, highlight_clip=0.0, haze=0.1,
        pro_stats_valid=1.0, subj_lum=0.22, subj_cov=0.1, fg_p50=0.80,
    )
    gset, _ = apply(rules, "other", bright_frame_dark_subject, {}, {}, intensity=1.0)
    assert gset["exposure"] > 0.10                        # lifted for the dark subject
    # Without a subject, fresh stats use the sky-excluded foreground branch.
    no_subject = bright_frame_dark_subject.model_copy(update={"subj_lum": 0.0, "subj_cov": 0.0})
    gset2, _ = apply(rules, "other", no_subject, {}, {}, intensity=1.0)
    assert gset2["exposure"] == -0.06                     # bright foreground → bounded darkening

    # Presence, not luminance, selects the subject branch: a clipped-black silhouette is valid.
    black_subject = bright_frame_dark_subject.model_copy(update={"subj_lum": 0.0})
    gset3, _ = apply(rules, "other", black_subject, {}, {}, intensity=1.0)
    assert gset3["exposure"] == 0.315


def test_legacy_subject_stats_reproduce_pre_retune_other_exposure():
    """A legacy sidecar could already contain subj_lum; the new P3 policy must still be gated by
    the explicit pro-stats availability marker, not by that pre-existing field."""
    rules = load_rules(config_path("rules.toml"))
    legacy = GlobalStatsModel(
        mean_lum=0.70, mean_sat=0.1, shadow_clip=0.0, highlight_clip=0.0, haze=0.1,
        subj_lum=0.22,  # old field populated, all newly computed fields/default marker remain zero
    )
    gset, _ = apply(rules, "other", legacy, {}, {}, intensity=1.0)
    assert gset["exposure"] == -0.04
    assert "temp" not in gset and "tint" not in gset


def test_valid_zero_foreground_uses_new_exposure_policy():
    rules = load_rules(config_path("rules.toml"))
    clipped_black_foreground = GlobalStatsModel(
        mean_lum=0.25, mean_sat=0.0, shadow_clip=0.75, highlight_clip=0.25, haze=0.25,
        pro_stats_valid=1.0, fg_p50=0.0, lum_p10=0.0, lum_p90=1.0, detail_rms=0.02,
    )
    gset, _ = apply(rules, "landscape", clipped_black_foreground, {}, {}, intensity=1.0)
    assert gset["exposure"] == 0.32


def test_region_channel_clip_is_available_to_rules():
    rules = {"landscape": {
        "snow": {
            "highlights": "clamp(-max(region_clip_high, region_channel_highlight_clip) * 55 - 12, -52, -8)"
        }
    }}
    g = GlobalStatsModel(mean_lum=0.55, mean_sat=0.08, shadow_clip=0.0, highlight_clip=0.0, haze=0.0)
    snow = RegionStatsModel(
        name="snow", lum=0.72, sat=0.05, clip_low=0.0, clip_high=0.0, coverage=0.2,
        channel_highlight_clip=0.4, b_highlight_clip=0.4,
    )

    _, rset = apply(rules, "landscape", g, {"snow": snow}, {"snow": "snow"}, intensity=1.0)

    assert rset["snow"]["highlights"] == -34.0
