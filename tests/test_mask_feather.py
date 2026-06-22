"""Edge-aware feathering (Codex review F6): the guided filter must snap a mask edge to the
image boundary (less bleed/halo) rather than blurring symmetrically across it."""

import numpy as np

from autoedit.edit.filters import guided_filter
from autoedit.edit.mask import build_alpha


def test_guided_filter_follows_a_hard_edge():
    # guide: left half dark, right half bright, sharp vertical edge at x=50
    guide = np.zeros((40, 100), np.float32)
    guide[:, 50:] = 1.0
    # a rough mask that roughly covers the right half but spills 3px into the dark side
    rough = np.zeros((40, 100), np.float32)
    rough[:, 47:] = 1.0
    out = guided_filter(guide, rough, radius=8, eps=1e-4)
    # deep in the dark region the alpha should be ~0 (didn't bleed left across the edge)
    assert out[:, :40].mean() < 0.15
    # deep in the bright region it should be ~1
    assert out[:, 60:].mean() > 0.85


def test_build_alpha_guided_sharpens_vs_gaussian():
    proxy = np.zeros((20, 50), bool)
    proxy[:, 25:] = True
    guide = np.zeros((40, 100), np.float32)
    guide[:, 50:] = 1.0  # hard edge aligned with the mask boundary

    plain = build_alpha(proxy, (40, 100), 0.05)               # gaussian feather (no guide)
    guided = build_alpha(proxy, (40, 100), 0.05, guide=guide)  # edge-aware

    # transition band (how many pixels are mid-alpha) is tighter with the guide
    band = lambda a: int(((a > 0.1) & (a < 0.9)).sum())
    assert band(guided) < band(plain)


def test_build_alpha_no_guide_is_unchanged_behavior():
    proxy = np.zeros((16, 16), bool)
    proxy[4:12, 4:12] = True
    a = build_alpha(proxy, (32, 32), 0.05)
    assert a.shape == (32, 32) and 0.0 <= a.min() and a.max() <= 1.0
