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
