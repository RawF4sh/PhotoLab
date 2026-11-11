"""Rules evaluator: helpers, the sandbox, and apply()."""

import math
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


def test_planned_exposure_is_scaled_order_independent_and_local_to_each_block():
    rules = {"people": {
        "global": {"whites": "planned_exposure", "exposure": "0.4"},
        "skin": {"whites": "planned_exposure", "exposure": "0.8"},
        "subject": {"whites": "planned_exposure"},
    }}
    g = GlobalStatsModel(mean_lum=0.4, mean_sat=0.1, shadow_clip=0, highlight_clip=0, haze=0)
    regions = {name: RegionStatsModel(name=name, lum=0.4, sat=0.1, clip_low=0,
                                     clip_high=0, coverage=0.2) for name in ("skin", "subject")}
    global_settings, region_settings = apply(rules, "people", g, regions,
                                             {name: name for name in regions}, intensity=0.5)
    assert global_settings == {"exposure": 0.4, "whites": 0.2}
    assert region_settings["skin"] == {"exposure": 0.8, "whites": 0.4}
    assert region_settings["subject"]["whites"] == 0.0


def test_whites_cannot_spend_headroom_already_used_by_exposure():
    """The failing neutral RAW has a dark foreground and about half a stop of headroom.
    It needs an exposure lift, but the later white-point adjustment must share that budget."""
    rules = load_rules(config_path("rules.toml"))
    source = GlobalStatsModel(mean_lum=0.32, mean_sat=0.1, shadow_clip=0,
                              highlight_clip=0, haze=0.1, pro_stats_valid=1,
                              fg_p50=0.24, headroom_ev=0.52, lum_p90=0.62)
    for scene in ("people", "landscape", "other"):
        settings, _ = apply(rules, scene, source, {}, {}, intensity=0.8)
        assert abs(settings["exposure"] * 0.8 - 0.3973333333333334) < 1e-9
        assert settings["whites"] == 0.0, scene
        # A subtler exposure leaves room; do not subtract a full-strength lift at low intensity.
        subtle, _ = apply(rules, scene, source, {}, {}, intensity=0.2)
        assert subtle["whites"] > 0.0, scene
        # A foreground already at target needs no lift, so its available headroom remains usable.
        at_target = source.model_copy(update={"fg_p50": 0.45})
        assert apply(rules, scene, at_target, {}, {}, 0.8)[0]["whites"] > 0.0, scene


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
        detail_rms=0.02, headroom_ev=1.0,                 # washed frame: plenty of highlight room
    )
    gset, _ = apply(rules, "landscape", flat, {}, {}, intensity=1.0)
    assert gset["contrast"] >= 10                         # decisive contrast on a flat frame
    assert gset["blacks"] <= -10                          # deliberate black point
    assert gset["whites"] > 5                             # dull highlights placed brighter

    crisp = GlobalStatsModel(                             # contrasty frame: full spread, deep blacks
        mean_lum=0.5, mean_sat=0.2, shadow_clip=0.0, highlight_clip=0.0, haze=0.2,
        pro_stats_valid=1.0, lum_p10=0.02, lum_p50=0.5, lum_p90=0.92, fg_p50=0.5,
        detail_rms=0.06, headroom_ev=0.0,
    )
    gset, _ = apply(rules, "landscape", crisp, {}, {}, intensity=1.0)
    assert gset["contrast"] == 0.0                        # near-white tail → no extra contrast
    assert gset["blacks"] == -gset["shadows"]             # blacks already deep → anchor only, nothing crushed
    assert gset["clarity"] == 3.0                         # already detailed → minimum clarity

    legacy = GlobalStatsModel(                            # zero-filled percentiles → old behavior
        mean_lum=0.55, mean_sat=0.08, shadow_clip=0.0, highlight_clip=0.0, haze=0.1,
    )
    gset, _ = apply(rules, "landscape", legacy, {}, {}, intensity=1.0)
    assert gset["contrast"] == 5.0 and gset["blacks"] == 0.0 and gset["clarity"] == 6.0
    assert abs(gset["exposure"] - 0.0) < 1e-9             # old mean_lum formula at 0.55 → 0


def test_global_contrast_reserves_highlight_headroom_in_every_scene():
    """Golden contrast values for a midtone foreground with a small bright tail. The tail
    must constrain contrast even when p90 is modest, and legacy stats must remain unchanged."""
    rules = load_rules(config_path("rules.toml"))
    source = GlobalStatsModel(
        mean_lum=0.5, mean_sat=0.1, shadow_clip=0.0, highlight_clip=0.0, haze=0.2,
        pro_stats_valid=1.0, fg_p50=0.45, band_bottom_p50=0.4, lum_p10=0.1, lum_p90=0.6,
    )
    for scene, golden, legacy in (
        ("people", (0.0, 2.0, 8.0, 8.0), 8.0),
        ("landscape", (0.0, 1.75, 7.0, 7.0), 5.0),
        ("other", (0.0, 1.75, 7.0, 7.0), 6.0),
    ):
        for headroom, expected in zip((0.0, 0.25, 1.0, 2.0), golden):
            measured = source.model_copy(update={"headroom_ev": headroom})
            settings, _ = apply(rules, scene, measured, {}, {}, intensity=1.0)
            assert abs(settings["contrast"] - expected) < 1e-9, (scene, headroom)
        old_stats = source.model_copy(update={"pro_stats_valid": 0.0})
        assert apply(rules, scene, old_stats, {}, {}, intensity=1.0)[0]["contrast"] == legacy
        dark = source.model_copy(update={"fg_p50": 0.17, "headroom_ev": 2.0})
        assert apply(rules, scene, dark, {}, {}, intensity=1.0)[0]["contrast"] == 0.0


def test_shipped_exposure_targets_foreground_not_sky():
    """A dark foreground under a big bright sky must be lifted by fg_p50 (which the sky-skewed
    mean would have blocked) with EV-CORRECT magnitude: 0.6·2.2·log2(0.45/0.28) ≈ +0.90 EV, not
    the old linear-delta 0.20 — and never past the highlight headroom."""
    rules = load_rules(config_path("rules.toml"))
    skyheavy = GlobalStatsModel(
        mean_lum=0.60, mean_sat=0.1, shadow_clip=0.0, highlight_clip=0.0, haze=0.3,
        pro_stats_valid=1.0, lum_p10=0.06, lum_p50=0.55, lum_p90=0.95, fg_p50=0.28,
        detail_rms=0.03, headroom_ev=2.0,
    )
    gset, _ = apply(rules, "landscape", skyheavy, {}, {}, intensity=1.0)
    assert 0.85 < gset["exposure"] < 0.95
    capped = skyheavy.model_copy(update={"headroom_ev": 0.2})   # bright sky owns the frame
    # headroom − 0.15 margin, plus the 0.3 EV the highlight knee absorbs (scaled by how short headroom is)
    assert abs(apply(rules, "landscape", capped, {}, {}, intensity=1.0)[0]["exposure"] - (0.05 + 0.3 * 0.7 / 0.9)) < 1e-9
    bright = skyheavy.model_copy(update={"fg_p50": 0.65})
    assert apply(rules, "landscape", bright, {}, {}, intensity=1.0)[0]["exposure"] == -0.06
    dark = skyheavy.model_copy(update={"fg_p50": 0.17, "dark_cov": 0.25})
    dset, _ = apply(rules, "landscape", dark, {}, {}, intensity=1.0)
    assert dset["exposure"] == 1.0                          # 60% of a 3.1 EV deficit, capped at +1.0
    # contrast fades on a dark source; blacks carries only the anchor that pins black under the lift
    assert dset["shadows"] >= 30 and dset["contrast"] == 0.0 and dset["blacks"] == -dset["shadows"]


def test_snow_dominant_scene_gets_extra_shadow_lift():
    """P3: sub-scene branching via a stat (snow_cov), not a new scene class — alpine shadow
    detail under snow reads flatter than a typical landscape shadow and gets a touch more lift."""
    rules = load_rules(config_path("rules.toml"))
    base = dict(mean_lum=0.55, mean_sat=0.1, shadow_clip=0.1, highlight_clip=0.0,
                haze=0.2, pro_stats_valid=1.0, fg_p50=0.5)   # normal foreground → no darkness lift
    plain = GlobalStatsModel(**base, snow_cov=0.0)
    snowy = GlobalStatsModel(**base, snow_cov=0.5)
    g_plain, _ = apply(rules, "landscape", plain, {}, {}, intensity=1.0)
    g_snowy, _ = apply(rules, "landscape", snowy, {}, {}, intensity=1.0)
    assert g_snowy["shadows"] == g_plain["shadows"] + 3.0

    at_threshold = GlobalStatsModel(**base, snow_cov=0.2)
    capped_plain = GlobalStatsModel(**{**base, "shadow_clip": 0.6}, snow_cov=0.0)
    capped_snow = GlobalStatsModel(**{**base, "shadow_clip": 0.6}, snow_cov=0.5)
    g_threshold, _ = apply(rules, "landscape", at_threshold, {}, {}, intensity=1.0)
    g_capped_plain, _ = apply(rules, "landscape", capped_plain, {}, {}, intensity=1.0)
    g_capped_snow, _ = apply(rules, "landscape", capped_snow, {}, {}, intensity=1.0)
    assert g_threshold["shadows"] == g_plain["shadows"]
    assert g_capped_plain["shadows"] == g_capped_snow["shadows"] == 45.0

    # dark-but-unclipped foreground: shadow_clip ≈ 0 but fg_p50 / dark_cov open the shadows
    dark = GlobalStatsModel(**{**base, "shadow_clip": 0.0, "fg_p50": 0.17, "dark_cov": 0.25})
    assert apply(rules, "landscape", dark, {}, {}, intensity=1.0)[0]["shadows"] >= 30


def test_wildlife_exposes_for_subject_not_the_frame():
    """P3: a dark subject (wildlife) against a bright sky/background must be exposed for the
    ANIMAL, not judged by mean_lum — the priority chain is subj_lum > fg_p50 > mean_lum."""
    rules = load_rules(config_path("rules.toml"))
    bright_frame_dark_subject = GlobalStatsModel(
        mean_lum=0.70, mean_sat=0.1, shadow_clip=0.0, highlight_clip=0.0, haze=0.1,
        pro_stats_valid=1.0, subj_lum=0.22, subj_cov=0.1, fg_p50=0.80, headroom_ev=1.5,
    )
    gset, _ = apply(rules, "other", bright_frame_dark_subject, {}, {}, intensity=1.0)
    assert gset["exposure"] == 1.0                        # 0.6·2.2·log2(0.45/0.22) ≈ 1.36 → capped +1.0
    # Without a subject, fresh stats use the sky-excluded foreground branch.
    no_subject = bright_frame_dark_subject.model_copy(update={"subj_lum": 0.0, "subj_cov": 0.0})
    gset2, _ = apply(rules, "other", no_subject, {}, {}, intensity=1.0)
    assert gset2["exposure"] == -0.06                     # bright foreground → bounded darkening

    # Presence, not luminance, selects the subject branch: a clipped-black silhouette is valid.
    black_subject = bright_frame_dark_subject.model_copy(update={"subj_lum": 0.0})
    gset3, _ = apply(rules, "other", black_subject, {}, {}, intensity=1.0)
    assert gset3["exposure"] == 1.0
    # …but never past the highlight headroom: a blown sky caps the lift for the subject too.
    no_room = bright_frame_dark_subject.model_copy(update={"headroom_ev": 0.0})
    assert abs(apply(rules, "other", no_room, {}, {}, intensity=1.0)[0]["exposure"] - 0.3) < 1e-9  # knee-only lift


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
    # A measured-zero foreground selects the NEW policy (not the legacy mean_lum branch). With no
    # highlight headroom (25% already clipped) the global lift is 0 — the sky owns the frame and
    # the shadows/region rules carry the foreground instead of a lift that would blow it further.
    gset, _ = apply(rules, "landscape", clipped_black_foreground, {}, {}, intensity=1.0)
    assert abs(gset["exposure"] - 0.3) < 1e-9             # only what the highlight knee can absorb
    assert gset["highlight_rolloff"] == 40.0
    assert gset["shadows"] == 45.0                        # the deficit moved into shadows
    roomy = clipped_black_foreground.model_copy(update={"highlight_clip": 0.0, "headroom_ev": 3.0})
    assert apply(rules, "landscape", roomy, {}, {}, intensity=1.0)[0]["exposure"] == 1.0


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


def test_landscape_and_other_share_the_documented_sync_keys():
    """rules.toml's schema has no block-inheritance, so [landscape.global]/[other.global] share 6
    keys by hand copy-paste (see the SYNC NOTE comments atop each block). Pin them byte-identical
    so a future edit to only one side is caught here instead of silently drifting. (`blacks` left
    the list on purpose: its anchor mirrors each block's own `shadows` rule — see
    test_black_anchor_pins_black_under_the_shadow_lift.)"""
    rules = load_rules(config_path("rules.toml"))
    landscape = rules["landscape"]["global"]
    other = rules["other"]["global"]
    synced_keys = ("vibrance", "dehaze", "clarity", "sharpen", "temp", "tint")
    for key in synced_keys:
        assert landscape[key] == other[key], (
            f"[landscape.global].{key} and [other.global].{key} have drifted apart — "
            "update both (see the SYNC NOTE comment in rules.toml) or remove the key from "
            "this test's synced_keys list if the divergence is now intentional."
        )


def test_black_anchor_pins_black_under_the_shadow_lift():
    """Golden (improvement loop iter-02). The `shadows` op adds its lift at full weight on pure
    black, which raised the black floor and read as a grey veil on dark frames. `blacks` now
    subtracts the same amount, so the pair opens the shadows, pins black and darkens no tone.
    The older "deepen washed blacks" term only acts when the frame has no dark tail — with real
    blacks present it pushed them into clipping."""
    rules = load_rules(config_path("rules.toml"))
    dark = GlobalStatsModel(
        mean_lum=0.20, mean_sat=0.15, shadow_clip=0.025, highlight_clip=0.0, haze=0.21,
        pro_stats_valid=1.0, lum_p10=0.035, lum_p50=0.13, lum_p90=0.61, fg_p50=0.13,
        dark_cov=0.41, headroom_ev=0.56,
    )
    for scene in ("landscape", "other", "people"):
        gset, _ = apply(rules, scene, dark, {}, {}, intensity=1.0)
        assert gset["shadows"] >= 30, scene
        assert gset["blacks"] == -gset["shadows"], scene      # anchor only: exactly cancels at black

    washed = GlobalStatsModel(
        mean_lum=0.50, mean_sat=0.10, shadow_clip=0.0, highlight_clip=0.0, haze=0.20,
        pro_stats_valid=1.0, lum_p10=0.13, lum_p50=0.45, lum_p90=0.70, fg_p50=0.45,
        dark_cov=0.0, headroom_ev=1.0,
    )
    tailed = washed.model_copy(update={"dark_cov": 0.02})
    for scene in ("landscape", "other"):
        clean, _ = apply(rules, scene, washed, {}, {}, intensity=1.0)
        kept, _ = apply(rules, scene, tailed, {}, {}, intensity=1.0)
        assert clean["blacks"] < -clean["shadows"] - 10, scene    # no dark tail → deliberate black point
        assert kept["blacks"] == -kept["shadows"], scene          # real blacks present → never deepened


def test_highlight_pull_down_is_capped_at_a_gentle_shoulder():
    """Golden (improvement loop iter-03). Negative highlights/whites only darken — a clipped JPEG
    area has no detail to bring back — and the old pulls (global -42/-18 stacked with the snow
    region's -52/-28) turned white snow and overcast sky into a flat grey plateau and smeared the
    snow/sky mask edge. The knee (`highlight_rolloff`) already keeps lifted highlights unclipped."""
    rules = load_rules(config_path("rules.toml"))
    blown = GlobalStatsModel(
        mean_lum=0.80, mean_sat=0.08, shadow_clip=0.0, highlight_clip=0.40, haze=0.5,
        channel_highlight_clip=0.50, pro_stats_valid=1.0, lum_p10=0.30, lum_p50=0.80,
        lum_p90=0.99, fg_p50=0.60, headroom_ev=0.0,
    )
    for scene in ("landscape", "other", "people"):
        gset, _ = apply(rules, scene, blown, {}, {}, intensity=1.0)
        assert gset["highlights"] == -10.0 and gset["whites"] == -4.0, scene

    snow = RegionStatsModel(name="snow", lum=0.90, sat=0.04, clip_low=0.0, clip_high=0.60,
                            coverage=0.30, channel_highlight_clip=0.80)
    _, rset = apply(rules, "landscape", blown, {"snow": snow}, {"snow": "snow"}, intensity=1.0)
    assert rset["snow"]["highlights"] == -10.0 and rset["snow"]["whites"] == -4.0
    unclipped = snow.model_copy(update={"clip_high": 0.0, "channel_highlight_clip": 0.0})
    _, rset = apply(rules, "landscape", blown, {"snow": unclipped}, {"snow": "snow"}, intensity=1.0)
    assert rset["snow"]["highlights"] == -4.0 and rset["snow"]["whites"] == 0.0


def test_shadow_lift_rule_spends_the_exposure_the_headroom_denied():
    """Golden (improvement loop iter-05). A dark foreground under a bright sky gets almost no global
    exposure (the highlight headroom caps it), and with black pinned the additive shadows slider
    cannot open it either. `shadows_ev` (black-pinned gain, edit/ops.py shadow_lift) takes half of
    the exposure ask the headroom denied, up to 1.5 stops; with enough headroom it is zero and the
    key is omitted, so the settings dict keeps its old shape."""
    rules = load_rules(config_path("rules.toml"))
    capped = GlobalStatsModel(
        mean_lum=0.45, mean_sat=0.2, shadow_clip=0.0, highlight_clip=0.0, haze=0.2,
        pro_stats_valid=1.0, lum_p10=0.04, lum_p50=0.30, lum_p90=0.95, fg_p50=0.10,
        dark_cov=0.35, headroom_ev=0.0,
    )
    ask = 0.6 * 2.2 * math.log2(0.45 / 0.10)                       # 2.86 EV wanted for the foreground
    for scene in ("landscape", "other", "people"):
        gset, _ = apply(rules, scene, capped, {}, {}, intensity=1.0)
        assert abs(gset["exposure"] - 0.30) < 1e-9, scene          # all the knee can absorb
        assert abs(gset["shadows_ev"] - 0.5 * (ask - 0.30)) < 1e-9, scene
    # the lift reads the exposure actually planned at this intensity, not the unscaled rule
    half, _ = apply(rules, "landscape", capped, {}, {}, intensity=0.5)
    assert abs(half["shadows_ev"] - 0.5 * (ask - 0.15)) < 1e-9
    deep = capped.model_copy(update={"fg_p50": 0.02})
    assert apply(rules, "landscape", deep, {}, {}, intensity=1.0)[0]["shadows_ev"] == 1.5   # capped

    roomy = capped.model_copy(update={"fg_p50": 0.30, "headroom_ev": 2.0})
    legacy = capped.model_copy(update={"pro_stats_valid": 0.0})
    for stats in (roomy, legacy):
        for scene in ("landscape", "other", "people"):
            assert "shadows_ev" not in apply(rules, scene, stats, {}, {}, intensity=1.0)[0], scene

    # wildlife: the subject, not the sky-excluded foreground, sets the ask (as for exposure)
    subject = capped.model_copy(update={"fg_p50": 0.40, "subj_cov": 0.2, "subj_lum": 0.10})
    assert abs(apply(rules, "other", subject, {}, {}, 1.0)[0]["shadows_ev"] - 0.5 * (ask - 0.30)) < 1e-9


def test_regional_rules_cannot_turn_a_dark_region_into_a_patch():
    """Golden (improvement loop iter-07). A region edit stops at the mask edge, and a mask often
    ends where the picture has no edge. On a dusk hillside the old mountain rules (additive shadows
    up to 30, +0.5 EV, mid-pivot contrast and dehaze) produced a flat grey blob or a black cut-out.
    Regional lifts are now a nudge (the frame-wide `shadows_ev` opens darkness), the additive lift
    is gone, dehaze fades out on a dark region and local contrast comes from the end-pinned curve."""
    rules = load_rules(config_path("rules.toml"))
    g = GlobalStatsModel(
        mean_lum=0.20, mean_sat=0.2, shadow_clip=0.0, highlight_clip=0.0, haze=0.2,
        pro_stats_valid=1.0, lum_p10=0.03, lum_p50=0.20, lum_p90=0.60, fg_p50=0.25, headroom_ev=1.0,
    )
    dark = RegionStatsModel(name="x", lum=0.05, sat=0.2, clip_low=0.0, clip_high=0.0, coverage=0.3)
    for concept, block in (("mountain", "mountain"), ("grass", "grass"), ("trees", "foliage")):
        _, rset = apply(rules, "landscape", g, {concept: dark}, {concept: block}, intensity=1.0)
        assert rset[concept]["exposure"] == 0.15, concept          # 2.5 EV below the foreground → nudge only
        assert "shadows" not in rset[concept], concept             # nothing additive raises the region's blacks
    _, rset = apply(rules, "landscape", g, {"mountain": dark}, {"mountain": "mountain"}, intensity=1.0)
    assert rset["mountain"]["dehaze"] == 0.0 and "contrast" not in rset["mountain"]
    assert rset["mountain"]["curve"] == 7.0                        # local contrast, both ends pinned
    lit = dark.model_copy(update={"lum": 0.50})
    _, rset = apply(rules, "landscape", g, {"mountain": lit}, {"mountain": "mountain"}, intensity=1.0)
    assert rset["mountain"]["dehaze"] == 8.0                       # a lit ridge keeps its definition


def test_output_sharpening_is_restrained_and_applied_once():
    """Golden (improvement loop iter-08). "Sharpness added" is a named user dislike, and the unsharp
    mask's over/undershoot at snow/rock edges was what the safety assessment rejected as mask-edge
    artifacts (panel: 11 → 15 of 16 proposals safe as rendered). Global sharpening is halved and no
    region sharpens again on top of it."""
    rules = load_rules(config_path("rules.toml"))
    base = dict(mean_lum=0.5, mean_sat=0.1, shadow_clip=0.0, highlight_clip=0.0, haze=0.2,
                pro_stats_valid=1.0, fg_p50=0.5, headroom_ev=1.0)
    for scene, amount in (("landscape", 7.0), ("other", 7.0), ("people", 6.0)):
        assert apply(rules, scene, GlobalStatsModel(**base), {}, {}, 1.0)[0]["sharpen"] == amount
        noisy = GlobalStatsModel(**base, iso=6400)
        assert apply(rules, scene, noisy, {}, {}, 1.0)[0]["sharpen"] == amount / 2     # still eases with ISO
        for name, block in rules[scene].items():
            assert name == "global" or "sharpen" not in block, (scene, name)


def test_depth_curve_follows_the_lift_and_spares_dark_frames():
    """Golden (improvement loop iter-10). An exposure lift is a multiply: it raises the black point
    with the foreground, and `contrast` is faded to zero on dark sources, so a lifted frame went
    flat. The end-pinned filmic curve now scales with the exposure actually spent beyond a small
    lift — and only where the source has no deep shadows of its own, so a dark frame with real
    blacks keeps all of its opened shadows (the guard in test_tone_stack.py)."""
    rules = load_rules(config_path("rules.toml"))
    washed = GlobalStatsModel(
        mean_lum=0.40, mean_sat=0.2, shadow_clip=0.0, highlight_clip=0.0, haze=0.2,
        pro_stats_valid=1.0, lum_p10=0.12, lum_p50=0.40, lum_p90=0.55, fg_p50=0.15,
        dark_cov=0.05, headroom_ev=2.0,
    )
    for scene in ("landscape", "other", "people"):
        full, _ = apply(rules, scene, washed, {}, {}, intensity=1.0)
        lift = full["exposure"]                                    # 1.0 landscape/other, 0.8 people
        assert full["curve"] == min(30.0, 60 * (lift - 0.25)), scene
        # the curve reads the exposure planned at this intensity
        soft, _ = apply(rules, scene, washed, {}, {}, intensity=0.5)
        assert abs(soft["curve"] - 60 * (0.5 * lift - 0.25)) < 1e-9, scene
        # ramps in with the source's 10th-percentile luma: nothing at 0.07, half at 0.095
        half, _ = apply(rules, scene, washed.model_copy(update={"lum_p10": 0.095}), {}, {}, intensity=0.5)
        assert abs(half["curve"] - 0.5 * 60 * (0.5 * lift - 0.25)) < 1e-9, scene
        deep, _ = apply(rules, scene, washed.model_copy(update={"lum_p10": 0.07}), {}, {}, intensity=1.0)
        assert deep["curve"] == 0.0, scene
        # a small lift earns no extra curve, and legacy stats keep the old behaviour
        small, _ = apply(rules, scene, washed.model_copy(update={"fg_p50": 0.40}), {}, {}, intensity=1.0)
        assert small["exposure"] < 0.25 and small["curve"] == 0.0, scene
        legacy, _ = apply(rules, scene, washed.model_copy(update={"pro_stats_valid": 0.0}), {}, {}, intensity=1.0)
        assert legacy["curve"] == 0.0, scene

