"""Per-region image statistics (numpy port of the Rust stats.rs).

Computed on the proxy (analysis-resolution) image. Produces the named variables the rules
read: global stats + per-region stats + a few well-known convenience vars (sky_*, subj_*,
bg_lum) derived from named regions when present.
"""

from __future__ import annotations

import numpy as np

from .contract import GlobalStatsModel, RegionStatsModel
from .imageio import Image, luma, srgb_to_linear

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


def _oklab_color_stats(lin: np.ndarray) -> tuple[float, float]:
    """(mean OKLab chroma, mean warmth=b-axis) for the frame, from an already-LINEAR-light rgb
    array (caller's `srgb_to_linear(rgb)` — computed once in `compute()` and reused for the WB
    solve too, rather than re-decoding gamma twice). Perceptual colourfulness + warm/cool bias
    the rules/targets can read. Imported lazily so the stub path needn't touch the colour code."""
    from .edit.oklab import linear_to_oklab

    lab = linear_to_oklab(lin)
    chroma = np.sqrt(lab[..., 1] ** 2 + lab[..., 2] ** 2)
    return float(chroma.mean()), float(lab[..., 2].mean())


def _veil_and_detail(lum: np.ndarray, sat: np.ndarray, dark: np.ndarray) -> tuple[float, float]:
    """(veil, detail_rms) — TRUE atmospheric-haze estimate + RMS local detail.

    The legacy `haze` stat (mean dark channel) conflates *bright/washed* with *hazy*: bright snow
    and limestone score 0.5-0.7 with zero atmosphere (measured on the Dolomites corpus). Veil
    requires all four haze signatures at once — lifted dark channel, smooth, desaturated, and
    NOT near-white (the term that separates fog from snow/high-key). Imported blur lazily so the
    stub path stays light."""
    from .edit.filters import gaussian_blur

    sigma = 0.01 * max(lum.shape) + 1.0
    detail = np.abs(lum - gaussian_blur(lum, sigma))
    # soft smoothness gate (0.05, not the dehaze op's strict 0.015): calibrated on real photos —
    # a strict gate zeroes veil on genuinely foggy frames because proxy-scale texture exceeds it
    smooth = np.clip((0.05 - detail) / 0.05, 0.0, 1.0)
    desat = np.clip((0.18 - sat) / 0.18, 0.0, 1.0)
    # Treat display-referred luma >= 0.82 as high-key/near-white. The former 0.80→0.92 ramp
    # assigned a uniform 0.80 neutral snow field the maximum possible veil score.
    notwhite = np.clip((0.82 - lum) / 0.18, 0.0, 1.0)
    norm_dark = np.clip((dark - 0.25) / 0.45, 0.0, 1.0)
    veil = float((norm_dark * smooth * desat * notwhite).mean())
    return veil, float(np.sqrt(np.mean(detail * detail)))


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
    img: Image,
    region_masks: dict[str, np.ndarray],
    region_coverages: dict[str, float] | None = None,
) -> tuple[GlobalStatsModel, dict[str, RegionStatsModel]]:
    rgb = img.rgb
    lum = luma(rgb)
    sat = _saturation(rgb)
    dark = rgb.min(axis=2)  # dark channel (haze proxy)
    lin = srgb_to_linear(rgb)  # computed once, reused for OKLab stats AND the WB gray-card solve

    # perceptual colour stats in OKLab (mean chroma + warmth); cheap on the proxy.
    p10, p50, p90, p99 = (float(v) for v in np.percentile(lum, (10, 50, 90, 99)))
    # Highlight headroom uses each pixel's brightest RGB channel: luminance averaging can hide
    # channel clipping in bright snow/clouds. Take the percentile AFTER the per-pixel maximum
    # so the 0.5% budget covers any-channel clipping across the frame, not 0.5% per channel.
    # Exposure, contrast and whites consume this same measured headroom in the rule baseline.
    p995 = float(np.percentile(rgb.max(axis=-1), 99.5))
    headroom_ev = max(0.0, float(np.log2(
        float(srgb_to_linear(np.float32(_HIGHLIGHT))) / max(float(srgb_to_linear(np.float32(p995))), 1e-5))))
    dark_cov = float((lum < 0.10).mean())
    third = max(1, lum.shape[0] // 3)
    band_top, band_mid, band_bottom = (float(np.median(b)) for b in
                                       (lum[:third], lum[third:2 * third], lum[2 * third:]) if b.size)
    mean_chroma, warmth = _oklab_color_stats(lin)
    channel_stats = _channel_exposure_stats(rgb)
    veil, detail_rms = _veil_and_detail(lum, sat, dark)

    # sky-excluded foreground tone — the pro exposure signal. A big bright sky skews mean_lum
    # toward "already bright" while the actual subject (foreground) sits dark; rules that key on
    # fg_p50 expose for the land, not the sky. Falls back to the global values when there's no
    # usable sky mask, so the fields are never 0 on a real photo.
    fg_lum, fg_p50 = float(lum.mean()), p50
    sky_mask = region_masks.get("sky")
    if sky_mask is not None and 0.05 < float(sky_mask.mean()) < 0.98:
        fg = lum[~sky_mask]
        if fg.size:
            fg_lum, fg_p50 = float(fg.mean()), float(np.percentile(fg, 50))

    # Region-aware auto-WB signal: measure the colour cast on a natural gray card. SAM's `snow`
    # region is the best reference (snow IS neutral); else fall back to bright near-neutral
    # pixels — EXCLUDING any region known to carry its own inherent material colour (rock, grass,
    # foliage, mountain, trees can be pale-and-desaturated — beige limestone, dry grass — without
    # being a neutral illuminant reference; mistaking one for the gray card reads the material's
    # own colour as a lighting cast). This mirrors the same false-positive pattern concepts.toml
    # already guards against for skin ("warm rock/grass can score as skin"). Else confidence 0
    # (rules multiply by confidence → identity). Corrections are solved in the white_balance op's
    # own units (rg=1+0.2t, bg=1-0.2t, gg=1+0.1n) so a rule can apply a bounded partial correction
    # with one multiply. This is a differentiator: a global auto has no idea which pixels SHOULD
    # be neutral — the segmenter does.
    wb_temp = wb_tint = wb_conf = 0.0
    ref_mask, ref_is_snow = None, False
    snow_mask = region_masks.get("snow")
    if snow_mask is not None and float(snow_mask.mean()) >= 0.01:
        ref_mask, ref_is_snow = snow_mask, True
    else:
        neutral = (lum >= 0.55) & (lum <= 0.95) & (sat < 0.15)
        for _biased_name in ("rock", "grass", "foliage", "mountain", "trees"):
            biased_mask = region_masks.get(_biased_name)
            if biased_mask is not None:
                neutral = neutral & ~biased_mask
        if float(neutral.mean()) >= 0.005:
            ref_mask = neutral
    if ref_mask is not None:
        ref = lin[ref_mask]  # reuse the frame's already-decoded linear light, not a fresh decode
        r_m, g_m, b_m = (float(ref[:, c].mean()) for c in range(3))
        if r_m + b_m > 1e-6 and g_m > 1e-6:
            wb_temp = float(np.clip(500.0 * (b_m - r_m) / (b_m + r_m), -100.0, 100.0))
            # Solve tint against the red/blue level AFTER the (possibly clipped) temperature
            # correction. Using the original arithmetic mean can give tint the wrong sign.
            r_target = r_m * (1.0 + 0.002 * wb_temp)
            b_target = b_m * (1.0 - 0.002 * wb_temp)
            rb_target = (r_target + b_target) / 2.0
            wb_tint = float(np.clip(1000.0 * (rb_target - g_m) / g_m, -100.0, 100.0))
            wb_conf = float(np.clip(float(ref_mask.mean()) / 0.05, 0.0, 1.0))
            wb_conf *= 1.0 if ref_is_snow else 0.6

    ex = img.exif or {}
    g = GlobalStatsModel(
        mean_lum=float(lum.mean()),
        mean_sat=float(sat.mean()),
        shadow_clip=float((lum < _SHADOW).mean()),
        highlight_clip=float((lum > _HIGHLIGHT).mean()),
        haze=float(np.clip(dark.mean(), 0.0, 1.0)),
        pro_stats_valid=1.0,
        veil=veil, detail_rms=detail_rms,
        dark_p10=float(np.percentile(dark, 10)),
        fg_lum=fg_lum, fg_p50=fg_p50,
        wb_temp_correction=wb_temp, wb_tint_correction=wb_tint, wb_confidence=wb_conf,
        iso=float(ex.get("iso", 0.0) or 0.0),
        exposure_ev=float(ex.get("exposure_ev", 0.0) or 0.0),
        lum_p10=p10, lum_p50=p50, lum_p90=p90, lum_p99=p99, headroom_ev=headroom_ev,
        dark_cov=dark_cov, band_top_p50=band_top, band_mid_p50=band_mid, band_bottom_p50=band_bottom,
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
    def coverage_for(name: str, fallback: float) -> float:
        # `None` means the caller has no arbitration information. An explicit mapping means
        # arbitration ran; a missing key then represents a fully claimed/zero-coverage region.
        if region_coverages is None:
            return float(fallback)
        return float(region_coverages.get(name, 0.0))

    if "snow" in region_stats:
        g.snow_cov = coverage_for("snow", region_stats["snow"].coverage)
    if "sky" in region_stats:
        g.sky_cov = coverage_for("sky", region_stats["sky"].coverage)
    if "water" in region_stats:
        g.water_cov = coverage_for("water", region_stats["water"].coverage)
    if "subject" in region_masks and region_masks["subject"].sum() > 0:
        subj = region_masks["subject"]
        g.subj_lum = float(lum[subj].mean())
        g.subj_cov = coverage_for("subject", float(subj.mean()))
        bg = ~subj
        if bg.sum() > 0:
            g.bg_lum = float(lum[bg].mean())

    return g, region_stats
