"""Per-region image statistics (numpy port of the Rust stats.rs).

Computed on the proxy (analysis-resolution) image. Produces the named variables the rules
read: global stats + per-region stats + a few well-known convenience vars (sky_*, subj_*,
bg_lum) derived from named regions when present.
"""

from __future__ import annotations

import numpy as np

from .contract import GlobalStatsModel, RegionStatsModel
from .imageio import Image, luma

_SHADOW = 0.02
_HIGHLIGHT = 0.98


def _saturation(rgb: np.ndarray) -> np.ndarray:
    """HSV-style saturation per pixel, HxW, in [0,1]."""
    mx = rgb.max(axis=2)
    mn = rgb.min(axis=2)
    out = np.zeros_like(mx)
    nz = mx > 1e-6
    out[nz] = (mx[nz] - mn[nz]) / mx[nz]
    return out


def compute(
    img: Image, region_masks: dict[str, np.ndarray]
) -> tuple[GlobalStatsModel, dict[str, RegionStatsModel]]:
    rgb = img.rgb
    lum = luma(rgb)
    sat = _saturation(rgb)
    dark = rgb.min(axis=2)  # dark channel (haze proxy)

    ex = img.exif or {}
    g = GlobalStatsModel(
        mean_lum=float(lum.mean()),
        mean_sat=float(sat.mean()),
        shadow_clip=float((lum < _SHADOW).mean()),
        highlight_clip=float((lum > _HIGHLIGHT).mean()),
        haze=float(np.clip(dark.mean(), 0.0, 1.0)),
        iso=float(ex.get("iso", 0.0) or 0.0),
        exposure_ev=float(ex.get("exposure_ev", 0.0) or 0.0),
    )

    region_stats: dict[str, RegionStatsModel] = {}
    for name, mask in region_masks.items():
        if mask is None or mask.sum() == 0:
            continue
        rl = lum[mask]
        rs = sat[mask]
        region_stats[name] = RegionStatsModel(
            name=name,
            lum=float(rl.mean()),
            sat=float(rs.mean()),
            clip_low=float((rl < _SHADOW).mean()),
            clip_high=float((rl > _HIGHLIGHT).mean()),
            coverage=float(mask.mean()),
        )

    # well-known convenience vars (0.0 when absent), matching rules.toml expectations
    if "sky" in region_stats:
        g.sky_lum = region_stats["sky"].lum
        g.sky_sat = region_stats["sky"].sat
        g.blown_sky = region_stats["sky"].clip_high
    if "subject" in region_masks and region_masks["subject"].sum() > 0:
        subj = region_masks["subject"]
        g.subj_lum = float(lum[subj].mean())
        bg = ~subj
        if bg.sum() > 0:
            g.bg_lum = float(lum[bg].mean())

    return g, region_stats
