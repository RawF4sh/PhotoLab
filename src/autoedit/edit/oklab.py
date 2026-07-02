"""OKLab perceptual color space (Björn Ottosson, 2020) — vectorized over HxWx3 arrays.

OKLab is the space the universal palette engine grades in: its L axis is perceptual lightness and
its (a, b) axes are a roughly uniform chroma plane, so we can push *colour* (hue/chroma) toward a
target while leaving *lightness* untouched — luminance-preserving grading the way pros expect.

Conversions are to/from **linear** sRGB (not gamma). Callers linearize first
(`imageio.srgb_to_linear`) and re-encode after (`imageio.linear_to_srgb`).
"""

from __future__ import annotations

import numpy as np

# linear sRGB → LMS (cone response), Ottosson's M1
_M1 = np.array([
    [0.4122214708, 0.5363325363, 0.0514459929],
    [0.2119034982, 0.6806995451, 0.1073969566],
    [0.0883024619, 0.2817188376, 0.6299787005],
], dtype=np.float64)
# nonlinear LMS → OKLab, Ottosson's M2
_M2 = np.array([
    [0.2104542553, 0.7936177850, -0.0040720468],
    [1.9779984951, -2.4285922050, 0.4505937099],
    [0.0259040371, 0.7827717662, -0.8086757660],
], dtype=np.float64)
_M1_INV = np.linalg.inv(_M1)
_M2_INV = np.linalg.inv(_M2)


def linear_to_oklab(lin: np.ndarray) -> np.ndarray:
    """linear sRGB (…x3, [0,1]) → OKLab (…x3): channels L, a, b."""
    lms = lin.astype(np.float64) @ _M1.T
    lms_ = np.cbrt(lms)                 # np.cbrt handles the (rare) negative on out-of-gamut input
    return (lms_ @ _M2.T).astype(np.float32)


def oklab_to_linear(lab: np.ndarray) -> np.ndarray:
    """OKLab (…x3) → linear sRGB (…x3). May land slightly out of [0,1]; caller clips."""
    lms_ = lab.astype(np.float64) @ _M2_INV.T
    lms = lms_ ** 3
    return (lms @ _M1_INV.T).astype(np.float32)
