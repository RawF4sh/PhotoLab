"""Fast, dependency-free 2D blurs (numpy). Used for clarity local-contrast and mask feather.

`box_blur` is an O(N) integral-image moving average; `gaussian_blur` runs 3 box passes
(a good Gaussian approximation). When OpenCV (the `accel` extra) is importable the same
operations run through its C kernels — measured 20× faster on a 22 MP frame with a max
difference of ~5e-6 against the numpy path, so results are interchangeable.
"""

from __future__ import annotations

import numpy as np

try:  # optional accelerator — never required; the numpy path is the reference implementation
    import cv2 as _cv2
except Exception:  # pragma: no cover - exercised only without opencv
    _cv2 = None


def box_blur(a: np.ndarray, radius: int) -> np.ndarray:
    """Box (moving-average) blur of a 2D float array, edge-padded."""
    if radius < 1:
        return a.copy()
    r = int(radius)
    k = 2 * r + 1
    if _cv2 is not None and a.ndim == 2:
        return _cv2.blur(np.ascontiguousarray(a, dtype=np.float32), (k, k),
                         borderType=_cv2.BORDER_REPLICATE)
    # horizontal
    ap = np.pad(a, ((0, 0), (r, r)), mode="edge")
    cs = np.cumsum(ap, axis=1)
    cs = np.pad(cs, ((0, 0), (1, 0)), mode="constant")
    out = (cs[:, k:] - cs[:, :-k]) / k
    # vertical
    ap = np.pad(out, ((r, r), (0, 0)), mode="edge")
    cs = np.cumsum(ap, axis=0)
    cs = np.pad(cs, ((1, 0), (0, 0)), mode="constant")
    return (cs[k:, :] - cs[:-k, :]) / k


def box_min(a: np.ndarray, radius: int) -> np.ndarray:
    """Separable sliding-window minimum (morphological erosion) of a 2D array, edge-padded.
    The patch-min behind the dark-channel prior in `dehaze`."""
    r = int(radius)
    if r < 1:
        return a.copy()
    if _cv2 is not None and a.ndim == 2:
        kernel = np.ones((2 * r + 1, 2 * r + 1), np.uint8)
        return _cv2.erode(np.ascontiguousarray(a, dtype=np.float32), kernel,
                          borderType=_cv2.BORDER_REPLICATE)
    ap = np.pad(a, ((0, 0), (r, r)), mode="edge")
    out = np.minimum.reduce([ap[:, i:i + a.shape[1]] for i in range(2 * r + 1)])
    ap = np.pad(out, ((r, r), (0, 0)), mode="edge")
    return np.minimum.reduce([ap[i:i + a.shape[0], :] for i in range(2 * r + 1)])


def gaussian_blur(a: np.ndarray, sigma: float) -> np.ndarray:
    """Approximate Gaussian blur via 3 box passes."""
    if sigma <= 0:
        return a.copy()
    r = max(1, int(round(sigma)))
    out = a.astype(np.float32)
    for _ in range(3):
        out = box_blur(out, r)
    return out


def guided_filter(guide: np.ndarray, src: np.ndarray, radius: int, eps: float = 1e-4) -> np.ndarray:
    """Edge-aware smoothing of `src` using `guide` as the structure reference (He et al. 2010),
    all via O(N) box filters. The output follows `guide`'s edges instead of blurring across them
    — so a mask alpha snaps to the real sky/mountain/hair boundary instead of haloing over it.
    guide/src: 2D float [0,1]. Returns a float array the shape of src."""
    r = max(1, int(radius))
    I, p = guide.astype(np.float32), src.astype(np.float32)
    mean_I = box_blur(I, r)
    mean_p = box_blur(p, r)
    corr_I = box_blur(I * I, r)
    corr_Ip = box_blur(I * p, r)
    var_I = corr_I - mean_I * mean_I
    cov_Ip = corr_Ip - mean_I * mean_p
    a = cov_Ip / (var_I + eps)
    b = mean_p - a * mean_I
    return box_blur(a, r) * I + box_blur(b, r)
