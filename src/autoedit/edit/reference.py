"""Reference-style color matching — "edit like this photo".

Transfers the *palette and tonality* of a reference image onto the target using Reinhard
color transfer in CIELAB: match the per-channel mean + standard deviation of L*, a*, b*.
This is the no-training "pick a look by example" path — give it a hero edit (or any image
with a look you like) and the whole batch is graded toward it, blended by `strength`.

numpy-only (core dep). It grades the *real* pixels — it never fabricates content (no sky
replacement); it only shifts color/tone distribution.
"""

from __future__ import annotations

import numpy as np

from ..imageio import linear_to_srgb, srgb_to_linear

# linear sRGB <-> XYZ (D65)
_RGB2XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                     [0.2126729, 0.7151522, 0.0721750],
                     [0.0193339, 0.1191920, 0.9503041]], dtype=np.float32)
_XYZ2RGB = np.linalg.inv(_RGB2XYZ).astype(np.float32)
_WHITE = np.array([0.95047, 1.0, 1.08883], dtype=np.float32)  # D65
_EPS = np.float32(216.0 / 24389.0)   # (6/29)^3
_KAPPA = np.float32(24389.0 / 27.0)  # (29/3)^3


def _f(t: np.ndarray) -> np.ndarray:
    return np.where(t > _EPS, np.cbrt(t), (_KAPPA * t + 16.0) / 116.0)


def _f_inv(t: np.ndarray) -> np.ndarray:
    t3 = t ** 3
    return np.where(t3 > _EPS, t3, (116.0 * t - 16.0) / _KAPPA)


def rgb_to_lab(rgb01: np.ndarray) -> np.ndarray:
    """sRGB-gamma [0,1] HxWx3 -> CIELAB (L in ~[0,100], a/b ~[-128,128])."""
    lin = srgb_to_linear(np.clip(rgb01, 0.0, 1.0))
    xyz = lin.reshape(-1, 3) @ _RGB2XYZ.T
    fr = _f(xyz / _WHITE)
    fx, fy, fz = fr[:, 0], fr[:, 1], fr[:, 2]
    lab = np.stack([116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)], axis=1)
    return lab.reshape(rgb01.shape).astype(np.float32)


def lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    """CIELAB HxWx3 -> sRGB-gamma [0,1]."""
    flat = lab.reshape(-1, 3)
    fy = (flat[:, 0] + 16.0) / 116.0
    fx = fy + flat[:, 1] / 500.0
    fz = fy - flat[:, 2] / 200.0
    xyz = np.stack([_f_inv(fx), _f_inv(fy), _f_inv(fz)], axis=1) * _WHITE
    lin = xyz @ _XYZ2RGB.T
    rgb = linear_to_srgb(np.clip(lin, 0.0, 1.0))
    return rgb.reshape(lab.shape).astype(np.float32)


def _stats(lab: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    flat = lab.reshape(-1, 3)
    return flat.mean(axis=0), flat.std(axis=0)


def color_transfer(target_rgb01: np.ndarray, reference_rgb01: np.ndarray,
                   strength: float = 0.7, ratio_clamp: tuple[float, float] = (0.4, 2.5)) -> np.ndarray:
    """Grade `target` toward the look of `reference` (Reinhard LAB mean/std match).

    strength: 0 = no change, 1 = full match. ratio_clamp bounds the per-channel std
    stretch so a near-flat target can't explode into wild color. Returns sRGB [0,1]."""
    strength = float(np.clip(strength, 0.0, 1.0))
    if strength <= 0.0:
        return np.clip(target_rgb01, 0.0, 1.0).astype(np.float32)

    t_lab = rgb_to_lab(target_rgb01)
    tm, ts = _stats(t_lab)
    rm, rs = _stats(reference_rgb01 if reference_rgb01.ndim == 2 else rgb_to_lab(reference_rgb01))

    out = t_lab.copy()
    for c in range(3):
        if ts[c] < 1e-4:
            # no variation to rescale; just shift the channel mean toward the reference
            out[..., c] = t_lab[..., c] + strength * (rm[c] - tm[c])
            continue
        gain = float(np.clip(rs[c] / ts[c], ratio_clamp[0], ratio_clamp[1]))
        matched = (t_lab[..., c] - tm[c]) * gain + rm[c]
        out[..., c] = t_lab[..., c] + strength * (matched - t_lab[..., c])
    return np.clip(lab_to_rgb(out), 0.0, 1.0).astype(np.float32)
