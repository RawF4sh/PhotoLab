"""Mask upsample + edge-aware feather.

Takes a low-res boolean proxy mask, upsamples to full resolution as a float alpha, and snaps
the edge to the real image boundary. When a `guide` (the full-res luma) is supplied, a guided
filter refines the alpha so masked edits follow sky/mountain/hair edges instead of haloing
across them; without a guide it degrades to a plain Gaussian feather (the dependency-free MVP).
"""

from __future__ import annotations

import numpy as np
from PIL import Image as PILImage

from .filters import gaussian_blur, guided_filter

_GUIDE_CAP = 1600  # run the guided filter at ≤ this long edge for speed, then upsample the alpha


def build_alpha(proxy_mask: np.ndarray, full_hw: tuple[int, int], feather_frac: float,
                guide: np.ndarray | None = None) -> np.ndarray:
    """proxy_mask: bool HxW (proxy res). guide: optional full-res luma HxW for edge-aware
    refinement. Returns float alpha [0,1] at full_hw."""
    h, w = full_hw
    src = (proxy_mask.astype(np.uint8) * 255)
    pil = PILImage.fromarray(src, mode="L").resize((w, h), PILImage.BILINEAR)
    alpha = np.asarray(pil).astype(np.float32) / 255.0
    radius = max(1, int(feather_frac * max(full_hw)))

    if guide is not None and guide.shape == (h, w):
        alpha = _guided_feather(alpha, guide, radius)
    else:
        alpha = gaussian_blur(alpha, radius)
    return np.clip(alpha, 0.0, 1.0)


def _resize2d(a: np.ndarray, hw: tuple[int, int]) -> np.ndarray:
    h, w = hw
    pil = PILImage.fromarray(np.clip(a, 0.0, 1.0).astype(np.float32), mode="F").resize((w, h), PILImage.BILINEAR)
    return np.asarray(pil).astype(np.float32)


def _guided_feather(alpha: np.ndarray, guide: np.ndarray, radius: int) -> np.ndarray:
    """Edge-aware feather via a guided filter, capped to _GUIDE_CAP for speed (the alpha is
    smooth, so refining at reduced resolution then upsampling keeps the edge snap)."""
    h, w = alpha.shape
    scale = min(1.0, _GUIDE_CAP / max(h, w))
    eps = 1e-3
    if scale < 1.0:
        sh, sw = max(1, int(h * scale)), max(1, int(w * scale))
        a_s = _resize2d(alpha, (sh, sw))
        g_s = _resize2d(guide, (sh, sw))
        q = guided_filter(g_s, a_s, max(1, int(radius * scale)), eps)
        q = _resize2d(np.clip(q, 0.0, 1.0), (h, w))
    else:
        q = guided_filter(guide, alpha, radius, eps)
    return q


def radial_alpha(full_hw, cx, cy, rx, ry, feather=0.18, inside=True) -> np.ndarray:
    """Elliptical radial alpha. Center/radii are fractions of width/height (0..1).
    inside=True → 1 within the ellipse fading out; inside=False → the inverse (vignette)."""
    h, w = full_hw
    yy, xx = np.mgrid[0:h, 0:w]
    nx = (xx - cx * w) / max(rx * w, 1.0)
    ny = (yy - cy * h) / max(ry * h, 1.0)
    d = np.sqrt(nx * nx + ny * ny)  # 0 at center, 1 at the ellipse edge
    a = np.clip((1.0 + feather - d) / max(feather, 1e-3), 0.0, 1.0)
    if not inside:
        a = 1.0 - a
    return a.astype(np.float32)


def linear_alpha(full_hw, frm="top", coverage=0.5) -> np.ndarray:
    """Linear gradient alpha (graduated-ND style). `coverage` = fraction of the frame the
    ramp spans from the named edge; alpha 1 at the edge → 0 at the coverage line."""
    h, w = full_hw
    yy = np.mgrid[0:h, 0:w][0].astype(np.float32) / max(h, 1)
    xx = np.mgrid[0:h, 0:w][1].astype(np.float32) / max(w, 1)
    cov = max(coverage, 1e-3)
    if frm == "top":
        a = (cov - yy) / cov
    elif frm == "bottom":
        a = (yy - (1.0 - cov)) / cov
    elif frm == "left":
        a = (cov - xx) / cov
    else:  # right
        a = (xx - (1.0 - cov)) / cov
    return np.clip(a, 0.0, 1.0).astype(np.float32)
