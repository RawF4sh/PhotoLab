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


def test_headroom_dark_cov_and_spatial_bands():
    rgb = np.empty((60, 40, 3), np.float32)
    rgb[:20] = 0.8                                          # bright top third
    rgb[20:40] = 0.4
    rgb[40:] = 0.05                                         # dark, unclipped bottom third
    g, _ = stats.compute(Image(rgb=rgb), {})
    assert abs(g.band_top_p50 - 0.8) < 0.01 and abs(g.band_mid_p50 - 0.4) < 0.01
    assert abs(g.band_bottom_p50 - 0.05) < 0.01
    assert abs(g.dark_cov - 1 / 3) < 0.02
    assert g.shadow_clip == 0.0                             # dark but NOT clipped — the case shadow_clip misses
    assert abs(g.lum_p99 - 0.8) < 0.01
    assert 0.5 < g.headroom_ev < 0.8                        # log2(lin(0.98)/lin(0.8)) ≈ 0.66 stops of room
    white = np.full((10, 10, 3), 0.99, np.float32)
    assert stats.compute(Image(rgb=white), {})[0].headroom_ev == 0.0


def test_headroom_bounds_the_brightest_channel_even_when_luma_is_dim():
    from autoedit.edit.ops import exposure

    for channel in range(3):
        rgb = np.full((20, 20, 3), 0.4, np.float32)
        rgb[:2, :, channel] = 0.9
        measured, _ = stats.compute(Image(rgb=rgb), {})
        # Applying the measured EV reaches the clipping threshold in that channel. A luma
        # percentile would permit a much larger lift and already clip it to 1.0.
        at_limit = exposure(rgb, measured.headroom_ev)
        assert abs(float(at_limit.max()) - 0.98) < 1e-5, channel
        assert exposure(rgb, measured.headroom_ev + 0.02).max() > 0.98


def test_headroom_budget_counts_any_clipped_channel_across_the_frame():
    rgb = np.full((100, 100, 3), 0.4, np.float32)
    pixels = rgb.reshape(-1, 3)
    for channel in range(3):
        pixels[channel * 30:(channel + 1) * 30, channel] = 0.99
    measured, _ = stats.compute(Image(rgb=rgb), {})
    assert measured.highlight_clip == 0.0               # luminance sees no clipped pixels
    assert measured.channel_highlight_clip < 0.005      # each channel individually fits the budget
    assert measured.rgb_highlight_clip_any > 0.005      # their union does not
    assert measured.headroom_ev == 0.0
