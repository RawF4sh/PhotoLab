"""Region-aware auto white balance (P2): the snow/neutral gray-card cast measurement and the
asymmetric, warmth-preserving correction rule. Blue casts get neutralized; golden light is kept."""

import numpy as np

from autoedit import stats
from autoedit.contract import GlobalStatsModel
from autoedit.decide.base import DecisionContext
from autoedit.decide.rules_decider import RulesDecider
from autoedit.imageio import Image, linear_to_srgb
from autoedit.looks import load as load_looks
from autoedit.paths import config_path
from autoedit.rules import eval_expr, load as load_rules

_RULES = load_rules(config_path("rules.toml"))
_TEMP_RULE = _RULES["landscape"]["global"]["temp"]


def _eval_temp(g, intensity=1.0):
    return eval_expr(_TEMP_RULE, {**g.model_dump(), "intensity": intensity})


def _snow_scene(snow_rgb, size=32):
    rgb = np.empty((size, size, 3), np.float32)
    rgb[:] = snow_rgb
    mask = np.ones((size, size), bool)
    return Image(rgb=rgb), {"snow": mask}


def test_blue_cast_on_snow_measured_and_warmed():
    # snow with a blue cast (shade / overcast): correction must be positive (warm it)
    g, _ = stats.compute(*_snow_scene([0.72, 0.72, 0.80]))
    assert g.wb_confidence == 1.0                          # full-frame snow = trusted gray card
    assert g.wb_temp_correction > 20                       # solid warm-correction signal
    temp = _eval_temp(g)
    assert 3.0 < temp <= 12.0                              # bounded, assertive warm-up


def test_golden_light_is_preserved_not_neutralized():
    # warm-cast snow (alpenglow): the card says "cool it" but measured warmth fades the rule to ~0
    g, _ = stats.compute(*_snow_scene([0.80, 0.76, 0.62]))
    assert g.wb_temp_correction < -20                      # the card DOES measure a warm cast...
    assert g.warmth > 0.02
    temp = _eval_temp(g)
    assert -5.0 <= temp <= 0.0                             # ...but cooling is floored at -5
    if g.warmth >= 0.035:
        assert temp == 0.0                                 # fully warm scene → untouched


def test_neutral_pixel_fallback_has_lower_confidence():
    # no snow region: bright low-sat pixels become the reference at reduced confidence
    rgb = np.full((32, 32, 3), 0.7, np.float32)
    rgb[..., 2] = 0.76                                     # slight blue cast, sat ≈ 0.08 (neutral)
    g, _ = stats.compute(Image(rgb=rgb), {})
    assert 0.0 < g.wb_confidence <= 0.6
    assert g.wb_temp_correction > 0


def test_no_reference_means_identity():
    # dark, saturated scene: nothing trustworthy to measure → confidence 0 → rule yields 0
    rgb = np.zeros((16, 16, 3), np.float32)
    rgb[..., 0] = 0.5                                      # saturated red, dark
    g, _ = stats.compute(Image(rgb=rgb), {})
    assert g.wb_confidence == 0.0
    assert _eval_temp(g) == 0.0


def test_joint_temperature_tint_solve_targets_corrected_neutral():
    # In linear light, temp=+50 moves R=.18 and B=.22 to the same .198 target. Green=.199
    # therefore needs a small NEGATIVE tint, not the positive tint from the original R/B mean.
    linear = np.array([0.18, 0.199, 0.22], np.float32)
    rgb = np.broadcast_to(linear_to_srgb(linear), (16, 16, 3)).copy()
    snow = np.ones((16, 16), bool)

    g, _ = stats.compute(Image(rgb=rgb), {"snow": snow})

    assert 49.0 < g.wb_temp_correction < 51.0
    assert g.wb_tint_correction < 0.0


def test_warmth_fade_reaches_exact_zero_at_threshold():
    g = GlobalStatsModel(
        mean_lum=0.6, mean_sat=0.1, shadow_clip=0.0, highlight_clip=0.0, haze=0.1,
        pro_stats_valid=1.0, wb_temp_correction=-100.0, wb_confidence=1.0, warmth=0.035,
    )
    assert _eval_temp(g) == 0.0


def test_wb_fallback_excludes_material_biased_regions():
    """A pale, low-saturation ROCK region with its own inherent warm bias (beige limestone, not
    a lighting cast) must NOT become the neutral-pixel WB reference — it would otherwise get
    misread as a cast, exactly the false-positive class concepts.toml already guards against for
    skin ('warm rock/grass can score as skin')."""
    size = 32
    rgb = np.full((size, size, 3), [0.78, 0.74, 0.70], np.float32)  # sat≈0.10, lum≈0.75 — would
    rock = np.ones((size, size), bool)                              # otherwise pass the fallback
    g, _ = stats.compute(Image(rgb=rgb), {"rock": rock})
    assert g.wb_confidence == 0.0                          # excluded → nothing trustworthy left
    assert g.wb_temp_correction == 0.0


def test_wb_fallback_still_uses_genuine_neutral_pixels_beside_a_biased_region():
    """The exclusion is scoped to the biased region's pixels only — a real neutral reference
    elsewhere in frame still measures its actual cast."""
    size = 32
    rgb = np.empty((size, size, 3), np.float32)
    rgb[:16] = [0.78, 0.74, 0.70]   # warm rock — excluded
    rgb[16:] = [0.72, 0.72, 0.80]   # genuinely neutral gray with a blue cast
    rock = np.zeros((size, size), bool)
    rock[:16] = True
    g, _ = stats.compute(Image(rgb=rgb), {"rock": rock})
    assert g.wb_confidence > 0.0
    assert g.wb_temp_correction > 0.0   # measured from the neutral half only


def test_auto_wb_bounds_survive_intensity_shaping():
    looks = load_looks(config_path("looks.toml"))
    base = GlobalStatsModel(
        mean_lum=0.6, mean_sat=0.1, shadow_clip=0.0, highlight_clip=0.0, haze=0.1,
        pro_stats_valid=1.0, wb_confidence=1.0, warmth=0.0,
    )

    def decide(correction):
        g = base.model_copy(update={"wb_temp_correction": correction})
        ctx = DecisionContext(
            scene="landscape", gstats=g, region_stats={}, region_rule={}, intensity=2.0,
            rules=_RULES, looks=looks, look="natural",
        )
        return RulesDecider().decide(ctx)[0]

    assert decide(-100.0)["temp"] == -5.0
    assert decide(100.0)["temp"] == 12.0
