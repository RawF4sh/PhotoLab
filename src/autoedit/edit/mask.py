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
