import numpy as np

from autoedit import stats
from autoedit.imageio import Image


def test_channel_highlight_clip_catches_single_channel_clipping():
    rgb = np.full((4, 4, 3), 0.45, np.float32)
    rgb[:2, :2, 0] = 1.0  # red channel clips, luma does not

    g, _ = stats.compute(Image(rgb=rgb), {})

    assert g.highlight_clip == 0.0
    assert g.r_highlight_clip == 0.25
    assert g.channel_highlight_clip == 0.25
    assert g.rgb_highlight_clip_any == 0.25
    assert g.rgb_highlight_clip_all == 0.0
    assert g.r_highlight_recoverable == 0.25
    assert g.rgb_highlight_recoverable == 0.25


def test_channel_shadow_clip_catches_color_channel_underexposure():
    rgb = np.full((4, 4, 3), 0.40, np.float32)
    rgb[:, :2, 2] = 0.0

    g, _ = stats.compute(Image(rgb=rgb), {})

    assert g.shadow_clip == 0.0
    assert g.b_shadow_clip == 0.5
    assert g.channel_shadow_clip == 0.5
    assert g.rgb_shadow_clip_any == 0.5
    assert g.rgb_shadow_clip_all == 0.0


def test_veil_detects_haze_but_not_bright_snow():
    """The blind-test discovery, encoded: `haze` (dark-channel mean) conflates bright/washed with
    hazy; `veil` must fire on true atmospheric veil and stay near zero on bright snow + blue sky."""
    # hazy frame: midtone-bright, desaturated, smooth, lifted blacks — classic fog
    hazy = np.full((64, 64, 3), 0.55, np.float32)
    g_hazy, _ = stats.compute(Image(rgb=hazy), {})
    assert g_hazy.veil > 0.4
    assert g_hazy.haze > 0.5                              # legacy stat also high — no divergence

    # bright snow field under a saturated blue sky: NOT hazy, but legacy haze still reads high
    snowy = np.empty((64, 64, 3), np.float32)
    snowy[:32] = [0.35, 0.55, 0.95]                       # saturated blue sky
    snowy[32:] = 0.97                                     # near-white snow
    g_snow, _ = stats.compute(Image(rgb=snowy), {})
    assert g_snow.haze > 0.5                              # the conflation the old stat suffers
    assert g_snow.veil < 0.15                             # veil correctly says "no atmosphere"


def test_fg_stats_exclude_the_sky():
    rgb = np.empty((64, 64, 3), np.float32)
    rgb[:32] = 0.9                                        # bright sky, top half
    rgb[32:] = 0.3                                        # dark foreground
    sky = np.zeros((64, 64), bool)
    sky[:32] = True

    g, _ = stats.compute(Image(rgb=rgb), {"sky": sky})
    assert abs(g.mean_lum - 0.6) < 0.02                   # mean is sky-skewed
    assert abs(g.fg_lum - 0.3) < 0.02                     # fg stats see the land, not the sky
    assert abs(g.fg_p50 - 0.3) < 0.02
    assert g.sky_cov > 0.4

    g2, _ = stats.compute(Image(rgb=rgb), {})             # no sky mask → graceful fallback
    assert abs(g2.fg_lum - g2.mean_lum) < 1e-6
    assert abs(g2.fg_p50 - g2.lum_p50) < 1e-6


def test_region_channel_stats_are_computed_inside_mask():
    rgb = np.full((4, 4, 3), 0.40, np.float32)
    mask = np.zeros((4, 4), bool)
    mask[:2, :2] = True
    rgb[mask, 1] = 1.0

    _, regions = stats.compute(Image(rgb=rgb), {"sky": mask})
    sky = regions["sky"]

    assert sky.g_highlight_clip == 1.0
    assert sky.channel_highlight_clip == 1.0
    assert sky.rgb_highlight_recoverable == 1.0
