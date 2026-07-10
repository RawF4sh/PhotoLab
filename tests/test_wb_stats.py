"""Region-aware auto white balance (P2): the snow/neutral gray-card cast measurement and the
asymmetric, warmth-preserving correction rule. Blue casts get neutralized; golden light is kept."""

import numpy as np

from autoedit import stats
from autoedit.imageio import Image
from autoedit.rules import eval_expr

_TEMP_RULE = ("clamp(wb_temp_correction * 0.45 * wb_confidence * "
              "clamp(1 - max(0, warmth) / 0.035, 0, 1), -5, 12)")


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
    temp = eval_expr(_TEMP_RULE, g.model_dump())
    assert 3.0 < temp <= 12.0                              # bounded, assertive warm-up


def test_golden_light_is_preserved_not_neutralized():
    # warm-cast snow (alpenglow): the card says "cool it" but measured warmth fades the rule to ~0
    g, _ = stats.compute(*_snow_scene([0.80, 0.76, 0.62]))
    assert g.wb_temp_correction < -20                      # the card DOES measure a warm cast...
    assert g.warmth > 0.02
    temp = eval_expr(_TEMP_RULE, g.model_dump())
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
    assert eval_expr(_TEMP_RULE, g.model_dump()) == 0.0
