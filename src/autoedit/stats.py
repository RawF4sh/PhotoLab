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


def _oklab_color_stats(rgb: np.ndarray) -> tuple[float, float]:
    """(mean OKLab chroma, mean warmth=b-axis) for the frame. Perceptual colourfulness + warm/cool
    bias the rules/targets can read. Imported lazily so the stub path needn't touch the colour code."""
    from .edit.oklab import linear_to_oklab
    from .imageio import srgb_to_linear

    lab = linear_to_oklab(srgb_to_linear(rgb))
    chroma = np.sqrt(lab[..., 1] ** 2 + lab[..., 2] ** 2)
    return float(chroma.mean()), float(lab[..., 2].mean())


def _channel_exposure_stats(rgb: np.ndarray) -> dict[str, float]:
    """Histogram-style RGB clipping stats.

    `*_recoverable` is a display-referred approximation: a channel is clipped while at least one
    companion channel is not fully clipped. True RAW headroom needs RAW sensor data, but this still
    catches color-channel clipping that luma-only stats miss.
    """
    if rgb.size == 0:
        return {}
    clipped_low = rgb < _SHADOW
    clipped_high = rgb > _HIGHLIGHT
    any_low = clipped_low.any(axis=1 if rgb.ndim == 2 else 2)
    all_low = clipped_low.all(axis=1 if rgb.ndim == 2 else 2)
    any_high = clipped_high.any(axis=1 if rgb.ndim == 2 else 2)
    all_high = clipped_high.all(axis=1 if rgb.ndim == 2 else 2)
    recoverable = clipped_high & ~all_high[..., None]
    r_low, g_low, b_low = (float(clipped_low[..., i].mean()) for i in range(3))
    r_high, g_high, b_high = (float(clipped_high[..., i].mean()) for i in range(3))
    return {
        "r_shadow_clip": r_low,
        "g_shadow_clip": g_low,
        "b_shadow_clip": b_low,
        "r_highlight_clip": r_high,
        "g_highlight_clip": g_high,
        "b_highlight_clip": b_high,
        "r_highlight_recoverable": float(recoverable[..., 0].mean()),
        "g_highlight_recoverable": float(recoverable[..., 1].mean()),
        "b_highlight_recoverable": float(recoverable[..., 2].mean()),
        "channel_shadow_clip": max(r_low, g_low, b_low),
        "channel_highlight_clip": max(r_high, g_high, b_high),
        "rgb_shadow_clip_any": float(any_low.mean()),
        "rgb_shadow_clip_all": float(all_low.mean()),
        "rgb_highlight_clip_any": float(any_high.mean()),
        "rgb_highlight_clip_all": float(all_high.mean()),
        "rgb_highlight_recoverable": float((any_high & ~all_high).mean()),
    }


def compute(
    img: Image, region_masks: dict[str, np.ndarray]
) -> tuple[GlobalStatsModel, dict[str, RegionStatsModel]]:
    rgb = img.rgb
    lum = luma(rgb)
    sat = _saturation(rgb)
    dark = rgb.min(axis=2)  # dark channel (haze proxy)

    # perceptual colour stats in OKLab (mean chroma + warmth); cheap on the proxy.
    p10, p50, p90 = (float(v) for v in np.percentile(lum, (10, 50, 90)))
    mean_chroma, warmth = _oklab_color_stats(rgb)
    channel_stats = _channel_exposure_stats(rgb)

    ex = img.exif or {}
    g = GlobalStatsModel(
        mean_lum=float(lum.mean()),
        mean_sat=float(sat.mean()),
        shadow_clip=float((lum < _SHADOW).mean()),
        highlight_clip=float((lum > _HIGHLIGHT).mean()),
        haze=float(np.clip(dark.mean(), 0.0, 1.0)),
        iso=float(ex.get("iso", 0.0) or 0.0),
        exposure_ev=float(ex.get("exposure_ev", 0.0) or 0.0),
        lum_p10=p10, lum_p50=p50, lum_p90=p90,
        mean_chroma=mean_chroma, warmth=warmth,
        **channel_stats,
    )

    region_stats: dict[str, RegionStatsModel] = {}
    for name, mask in region_masks.items():
        if mask is None or mask.sum() == 0:
            continue
        rl = lum[mask]
        rs = sat[mask]
        rr = rgb[mask]
        region_stats[name] = RegionStatsModel(
            name=name,
            lum=float(rl.mean()),
            sat=float(rs.mean()),
            clip_low=float((rl < _SHADOW).mean()),
            clip_high=float((rl > _HIGHLIGHT).mean()),
            coverage=float(mask.mean()),
            **_channel_exposure_stats(rr),
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
