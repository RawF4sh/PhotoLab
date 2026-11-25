"""Scene classification (port of the Rust scene.rs) — pick which rule block set runs.

v1 classifies from the concepts SAM actually detected + global stats (a cheap proxy for
the anchor-probe described in the plan). Returns the scene name used to index rules.toml.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .contract import GlobalStatsModel, RegionStatsModel

_PEOPLE = {"skin", "person", "face"}
_LANDSCAPE = {"sky", "snow", "mountain", "grass", "water", "foliage", "trees", "rock"}
# Min skin/face coverage to call a frame a portrait. Below this, a "skin" detection is almost
# always warm rock/grass/sand — it must NOT drag a landscape into the people ruleset (or warm
# scattered specks into red dots). A real face/half-body portrait is well above this.
_SKIN_PORTRAIT_COV = 0.08


@dataclass
class Scene:
    name: str
    confidence: float
    finer: str = ""
    reason: str = ""


LANDSCAPES = ("sunset", "twilight", "daylight", "mist")


def sky_colour(rgb, sky, cfg):
    """Colour in the sky. Separate gold light from a pink band in otherwise blue twilight."""
    if sky is None or not sky.any():
        return 0.0, 0.0, 0.0
    r, g, b = rgb[sky > 0].T
    warmth = r - b
    gold = (warmth >= cfg["warm_pixel"]) & (g >= b)
    rose = (r - g >= cfg["rose_red"]) & (b - g >= cfg["rose_blue"])
    return float(warmth.mean()), float(gold.mean()), float(rose.mean())


def cloud_cover(rgb, sky, cfg, cloud=None):
    """Is there a continuous neutral ceiling, or significant openings onto blue sky?

    Normalise by brightness: shadow on a grey cloud changes its brightness, whereas
    a break reveals more blue relative to *both* green and red. A stray patch on a
    tiny sky mask provides neither kind of evidence.
    """
    if sky is None or sky.mean() < cfg["cloud_min_sky"]:
        return None
    pixels = rgb[sky > 0]
    red, green, blue = pixels.T
    peak = np.maximum(pixels.max(axis=1), np.finfo(np.float32).eps)
    blue_red = (blue - red) / peak
    blue_pixels = (blue_red >= cfg["cloud_blue_red"]) & ((blue - green) / peak >= cfg["cloud_blue_green"])
    blue_share = float(blue_pixels.mean())
    if cloud is not None:
        # A grey ceiling can have a blue cast over its whole width. A cloud model
        # can also include real openings, so check the blue tail against the median
        # as well as the extent of its mask. Extra cloud outside the trusted sky
        # (snow, steam or fog in front of terrain) is not evidence of a ceiling.
        overlap = cloud & sky
        if overlap.sum() >= sky.sum() * cfg["deck_min_coverage"]:
            columns = sky.sum(axis=0)
            visible = columns > 0
            across = overlap.sum(axis=0)[visible] / columns[visible]
            median, upper = np.quantile(blue_red, [.5, cfg["deck_blue_quantile"]])
            if (np.mean(across >= cfg["deck_column_coverage"]) >= cfg["deck_width_min"]
                    and median <= cfg["deck_max_blue_cast"] and upper - median <= cfg["deck_max_blue_tail"]):
                return "deck"
    neutral_share = float(np.mean((peak - pixels.min(axis=1)) / peak <= cfg["mist_sky_sat_max"]))
    if blue_share <= cfg["cloud_blue_max"] and neutral_share >= cfg["cloud_neutral_min"]:
        return "ceiling"
    if blue_share >= cfg["clear_sky_blue_min"]:
        low, high = np.percentile(blue_red, [10, 95])
        if high - low >= cfg["cloud_blue_variation"] or blue_share >= cfg["clear_sky_blue_dominant"]:
            return "open"
    return None


def landscape_scene(g, cfg, sky_warmth, warm_share, rose_share, capture_ev, light, night, clouds=None):
    """A compact evidence trail for the scene. Ambiguous evidence uses the broader goal."""
    sky = g.sky_cov >= cfg["min_sky"]
    contrast = g.lum_p90 - g.lum_p10
    warm_light = light is not None and light.cy >= cfg["warm_light_y"]
    bright_capture = capture_ev is not None and capture_ev >= cfg["day_capture_min_ev"]
    dim_capture = capture_ev is not None and capture_ev <= cfg["dusk_capture_max_ev"]
    # A shaded foreground alone is common in midday mountains. When capture metadata is
    # absent, require evidence from both the sky and the bright end of the frame.
    dark_view = g.sky_lum <= cfg["dusk_sky_max"] and g.lum_p50 <= cfg["dusk_median_max"]
    dim_highlights = g.lum_p90 <= cfg["dusk_highlight_max"]
    rose_dusk = rose_share >= cfg["rose_share"] and rose_share > warm_share
    cool_sky = sky_warmth <= cfg["cool_sky"]
    neutral_sky = sky and g.sky_sat <= cfg["mist_sky_sat_max"]
    # The camera may meter a rose or blue twilight sky to mid-grey. Conversely an
    # isolated lamp may shoot through p90 in an otherwise dark long exposure.
    dim_midtone = g.lum_p50 <= cfg["metered_dusk_median_max"]
    night_capture = capture_ev is not None and capture_ev <= cfg["night_capture_max_ev"]
    very_dark = night or (night_capture and g.lum_p50 <= cfg["dusk_median_max"] and
                          (sky or dim_highlights))
    dusk_colour = sky and (cool_sky or rose_dusk)
    metered_dusk = dim_capture and dim_midtone and (dusk_colour or (sky and dark_view and dim_highlights))
    unmetered_dusk = dusk_colour and dark_view and dim_highlights
    # A bright neutral patch can be a white sunlit cloud. Strong daylight
    # exposure plus a full tonal range makes that weak evidence for overcast;
    # a genuinely low-contrast veil still takes precedence.
    clear_lighting = bright_capture and contrast > cfg["mist_contrast_max"] and (
        capture_ev >= cfg["sunlit_capture_min_ev"] or g.blown_sky >= cfg["sunlit_sky_clip_min"]
        or (sky and not neutral_sky))
    diffuse = neutral_sky and light is None and (contrast <= cfg["mist_contrast_max"] or (
        g.sky_lum >= cfg["overcast_sky_min"] and g.sky_sat <= cfg["overcast_sat_max"]))
    strong, likely = cfg["strong_confidence"], cfg["likely_confidence"]
    if very_dark and not bright_capture:
        label, confidence, reason = "twilight", strong, "night exposure and low ambient light"
    elif sky and warm_share >= cfg["warm_min_share"] and not (dim_capture and rose_dusk) and (
            warm_light or sky_warmth >= cfg["warm_sky"] or not bright_capture):
        label, confidence, reason = "sunset", strong if warm_light else likely, "gold light in the sky"
    elif not bright_capture and (metered_dusk or unmetered_dusk):
        label, confidence, reason = "twilight", strong if metered_dusk else likely, (
            "dim capture with dusk sky or low ambient light" if metered_dusk else "dark frame with a cool or rose sky")
    elif (clouds == "deck" and light is None and not dim_capture and (bright_capture or g.sky_lum >= cfg["day_sky_min"])
          and g.blown_sky <= cfg["deck_clip_max"]):
        label, confidence, reason = "mist", likely, "a continuous cloud deck with no distinct blue openings"
    elif (clouds == "ceiling" and light is None and not dim_capture
          and g.sky_lum >= cfg["day_sky_min"] and g.blown_sky <= cfg["cloud_clip_max"]):
        label, confidence, reason = "mist", likely, "continuous neutral cloud cover across the sky"
    elif (clouds == "open" and not dim_capture and (bright_capture or g.sky_lum >= cfg["day_sky_min"])
          and not (g.veil >= cfg["mist_veil"] and contrast <= cfg["mist_contrast_max"])):
        label, confidence, reason = "daylight", likely, "clear blue sky or blue openings between clouds"
    elif not clear_lighting and (g.veil >= cfg["mist_veil"] or diffuse):
        label, confidence, reason = "mist", likely, "atmospheric veil or diffuse light under a neutral sky"
    elif bright_capture or (sky and g.sky_lum >= cfg["day_sky_min"] and not dim_capture) or (
            g.lum_p50 >= cfg["day_median_min"] and contrast >= cfg["day_contrast_min"] and not dim_capture):
        label, confidence, reason = "daylight", likely, "daylight exposure or bright, well-defined highlights"
    else:
        label, confidence, reason = "landscape", cfg["weak_confidence"], "no clear lighting signature"
    if confidence < cfg["min_confidence"]:
        label = "landscape"
        reason += "; low confidence"
    return Scene("landscape", confidence, label, reason)


def classify(g: GlobalStatsModel, present: set[str],
             region_stats: dict[str, RegionStatsModel] | None = None,
             override: str | None = None, *, thresholds=None, sky_warmth=0.0, warm_share=0.0,
             light=None, night=False, rose_share=0.0, capture_ev=None, clouds=None) -> Scene:
    if override:
        broad = "landscape" if override in LANDSCAPES else override
        return Scene(broad, 1.0, override, "scene chosen by the user")
    rs = region_stats or {}

    def cov(name: str) -> float:
        r = rs.get(name)
        return r.coverage if r is not None else 0.0

    skin_cov = max(cov("skin"), cov("face"), cov("person"))
    # A genuine portrait: an explicit person/face concept, or skin covering a real fraction of
    # the frame. A tiny high-confidence skin blob on a landscape does NOT qualify.
    if (present & {"person", "face"}) or skin_cov >= _SKIN_PORTRAIT_COV:
        return Scene(name="people", confidence=0.7)
    if present & _LANDSCAPE:
        if thresholds is not None:
            return landscape_scene(g, thresholds, sky_warmth, warm_share, rose_share, capture_ev, light, night, clouds)
        return Scene(name="landscape", confidence=0.6)
    if present & _PEOPLE:  # small skin, no landscape context → still a person-ish frame
        return Scene(name="people", confidence=0.55)
    return Scene(name="other", confidence=0.5)
