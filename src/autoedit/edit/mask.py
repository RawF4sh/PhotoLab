"""Mask upsample + edge-aware-ish feather.

Takes a low-res boolean proxy mask, upsamples to full resolution as a float alpha, and
feathers the edge (Gaussian) to avoid hard seams/halos when the region edit is composited.
A guided filter (Kornia/OpenCV, `accel` extra) is the quality upgrade; bilinear+Gaussian
is the dependency-free MVP.
"""

from __future__ import annotations

import numpy as np
from PIL import Image as PILImage

from .filters import gaussian_blur


def build_alpha(proxy_mask: np.ndarray, full_hw: tuple[int, int], feather_frac: float) -> np.ndarray:
    """proxy_mask: bool HxW (proxy res). Returns float alpha [0,1] at full_hw."""
    h, w = full_hw
    src = (proxy_mask.astype(np.uint8) * 255)
    pil = PILImage.fromarray(src, mode="L").resize((w, h), PILImage.BILINEAR)
    alpha = np.asarray(pil).astype(np.float32) / 255.0
    radius = max(1, int(feather_frac * max(full_hw)))
    alpha = gaussian_blur(alpha, radius)
    return np.clip(alpha, 0.0, 1.0)


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
