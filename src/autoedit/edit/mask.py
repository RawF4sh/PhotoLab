"""Mask upsample + edge-aware feather.

Takes a low-res boolean proxy mask, upsamples to full resolution as a float alpha, and snaps
the edge to the real image boundary. When a `guide` (the full-res luma) is supplied, a guided
filter refines the alpha so masked edits follow sky/mountain/hair edges instead of haloing
across them; without a guide it degrades to a plain Gaussian feather (the dependency-free MVP).
"""

from __future__ import annotations

import numpy as np
from PIL import Image as PILImage

from . import par
from .filters import gaussian_blur, guided_filter

_GUIDE_CAP = 1600  # run the guided filter at ≤ this long edge for speed, then upsample the alpha


def blobs(mask: np.ndarray, grid: int) -> list[np.ndarray]:
    """The 8-connected groups of True pixels, found on a grid of at most grid x grid cells,
    largest first, each as a full-size mask."""
    h, w = mask.shape
    b = max(1, -(-max(h, w) // grid))
    gh, gw = -(-h // b), -(-w // b)
    padded = np.zeros((gh * b, gw * b), bool)
    padded[:h, :w] = mask
    cells = padded.reshape(gh, b, gw, b).any(axis=(1, 3))
    labels = np.zeros((gh, gw), np.int32)
    sizes: list[int] = []
    for y0, x0 in zip(*np.nonzero(cells)):
        if labels[y0, x0]:
            continue
        sizes.append(0)
        k = len(sizes)
        stack = [(int(y0), int(x0))]
        labels[y0, x0] = k
        while stack:
            y, x = stack.pop()
            sizes[-1] += 1
            for yy in range(max(y - 1, 0), min(y + 2, gh)):
                for xx in range(max(x - 1, 0), min(x + 2, gw)):
                    if cells[yy, xx] and not labels[yy, xx]:
                        labels[yy, xx] = k
                        stack.append((yy, xx))
    if not sizes:
        return []
    full = np.kron(labels, np.ones((b, b), np.int32))[:h, :w]   # upsampled once
    order = sorted(range(1, len(sizes) + 1), key=lambda k: -sizes[k - 1])   # stable: scan order breaks ties
    return [mask & (full == k) for k in order]


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


def _smoothstep(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def gradient_alpha(full_hw, angle_deg: float, start: float, end: float) -> np.ndarray:
    """Linear gradient at any angle. The ramp runs along the direction `angle_deg` (0 = from the
    top down, 90 = from the right to the left, 180 = from the bottom up, 270 = from the left to
    the right). Positions are fractions of the frame along that direction, measured from the
    edge (or corner) the gradient starts at: alpha 1 before `start`, 0 after `end`, a smoothstep
    between. `start == end` is a hard step.

    Unlike `linear_alpha` the ramp is a smoothstep, not a straight line, and pixels are sampled at
    their centres; so the two agree at both ends and the midpoint (within a pixel) but not between."""
    h, w = full_hw
    rad = np.deg2rad(angle_deg)
    dx, dy = -np.sin(rad), np.cos(rad)            # unit direction of the ramp, x to the right, y down
    extent = max(abs(dx) * w + abs(dy) * h, 1e-6)  # length of the frame along that direction
    origin = min(0.0, dx * w) + min(0.0, dy * h)    # smallest projection: the start corner
    span = max(end - start, 1e-6)

    def strip(ys):
        t = ((np.arange(w, dtype=np.float32)[None, :] + 0.5) * dx + (ys[:, :1] + 0.5) * dy - origin) / extent
        return (1.0 - _smoothstep((t - start) / span)).astype(np.float32)

    return par.rows(strip, np.broadcast_to(np.arange(h, dtype=np.float32)[:, None], (h, w)))


def glow_alpha(full_hw, cx: float, cy: float, radius: float, falloff: float = 2.5) -> np.ndarray:
    """A soft round light: 1 at the centre, a Gaussian-like falloff that reaches 0.5 at `radius`
    (fraction of the frame's long side) and fades out by `falloff * radius`. Never a hard edge:
    the Gaussian is eased to zero over its outer part with a smoothstep. `cx`, `cy` are fractions
    of width and height and may lie outside the frame."""
    h, w = full_hw
    r_px = max(radius * max(h, w), 1.0)
    fall = max(falloff, 1.5)                      # below this the 0.5-at-radius promise cannot hold

    def strip(ys):
        d = np.hypot((np.arange(w, dtype=np.float32)[None, :] + 0.5 - cx * w),
                     (ys[:, :1] + 0.5 - cy * h)) / r_px
        g = np.exp(-np.log(2.0) * d * d)
        return (g * (1.0 - _smoothstep((d - 0.7 * fall) / (0.3 * fall)))).astype(np.float32)

    return par.rows(strip, np.broadcast_to(np.arange(h, dtype=np.float32)[:, None], (h, w)))
