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

    # High-key snow/limestone is often exposed below 0.92; it must not exploit the old gate's
    # permissive 0.80→0.92 ramp and score as maximum haze.
    for value in (0.80, 0.85):
        high_key = np.full((64, 64, 3), value, np.float32)
        g_high, _ = stats.compute(Image(rgb=high_key), {})
        assert g_high.veil < 0.15


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

    all_sky = np.ones((64, 64), bool)                     # empty foreground → same fallback
    g3, _ = stats.compute(Image(rgb=rgb), {"sky": all_sky})
    assert abs(g3.fg_lum - g3.mean_lum) < 1e-6
    assert abs(g3.fg_p50 - g3.lum_p50) < 1e-6


def test_global_coverages_can_use_arbitrated_masks():
    rgb = np.full((10, 10, 3), 0.7, np.float32)
    snow = np.zeros((10, 10), bool)
    snow[:5, :5] = True                                  # 25% raw union mask

    g, regions = stats.compute(Image(rgb=rgb), {"snow": snow}, {})

    assert regions["snow"].coverage == 0.25              # union stats still measure the concept
    assert g.snow_cov == 0.0                              # rule coverage follows arbitration


def test_detail_rms_is_root_mean_square():
    from autoedit.edit.filters import gaussian_blur

    lum = np.zeros((32, 32), np.float32)
    lum[8:24, 8:24] = 0.8
    detail = np.abs(lum - gaussian_blur(lum, 0.01 * max(lum.shape) + 1.0))
    _veil, measured = stats._veil_and_detail(lum, np.zeros_like(lum), np.zeros_like(lum))

    expected = float(np.sqrt(np.mean(detail * detail)))
    assert abs(measured - expected) < 1e-8
    assert abs(measured - float(detail.mean())) > 1e-4


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
