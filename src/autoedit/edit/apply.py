"""Compose region edits + global finishing into the final image.

Regions arrive in priority-descending order (from segment/resolve). Even though the
arbitrated masks are non-overlapping, feathering softens edges, so we still composite with
`a_eff = alpha * (1 - claimed)` to guarantee no pixel is double-edited. Region edits run
first (masked), then global finishing over the whole frame, then a final clip.
"""

from __future__ import annotations

import numpy as np

from ..contract import Settings
from . import ops


def apply_image(
    full_rgb: np.ndarray,
    regions: list[tuple[np.ndarray, Settings]],
    global_settings: Settings,
) -> np.ndarray:
    """full_rgb: float HxWx3 gamma [0,1]. regions: [(alpha_full, settings), ...] priority desc."""
    out = full_rgb.astype(np.float32).copy()
    claimed = np.zeros(out.shape[:2], dtype=np.float32)

    for alpha, settings in regions:
        if not settings:
            continue
        a_eff = alpha * (1.0 - claimed)
        if float(a_eff.max()) <= 1e-4:
            continue
        edited = ops.apply_ops(out, settings)
        a3 = a_eff[..., None]
        out = out * (1.0 - a3) + edited * a3
        claimed = np.clip(claimed + a_eff, 0.0, 1.0)

    if global_settings:
        out = ops.apply_ops(out, global_settings)

    return np.clip(out, 0.0, 1.0)
