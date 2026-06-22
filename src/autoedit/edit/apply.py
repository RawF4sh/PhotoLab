"""Compose region edits + global finishing into the final image.

Regions arrive in priority-descending order (from segment/resolve). Even though the
arbitrated masks are non-overlapping, feathering softens edges, so we still composite with
`a_eff = alpha * (1 - claimed)` to guarantee no pixel is double-edited. Region edits run
first (masked), then global finishing over the whole frame, then a final clip.
"""

from __future__ import annotations

import numpy as np

from ..contract import Settings
from . import gates, ops


def apply_image(
    full_rgb: np.ndarray,
    regions: list[tuple[np.ndarray, Settings]],
    global_settings: Settings,
    compositional: list[tuple[np.ndarray, Settings]] | None = None,
    guard_mask: np.ndarray | None = None,
    guard_ops: set[str] | None = None,
    chroma_max_increase: float | None = None,
    protect_chroma: np.ndarray | None = None,
    report: dict | None = None,
) -> np.ndarray:
    """full_rgb: float HxWx3 gamma [0,1].
    regions: [(alpha_full, settings), ...] priority desc — arbitrated, claim-composited.
    compositional: [(alpha_full, settings), ...] — radial/linear moves blended independently
    (they intentionally overlap regions, e.g. a vignette over the background).
    guard_mask / guard_ops: skin-safe guard. Over `guard_mask` (float HxW [0,1]) the final
    GLOBAL pass skips the high-risk ops in `guard_ops` (e.g. clarity/saturation/grade) so faces
    keep global exposure/tone but not the aggressive look. Both None → byte-identical to before.
    chroma_max_increase: if set, a final oversaturation gate caps each pixel's chroma increase
    vs the source by this fraction (snow/skin in `protect_chroma` held tighter). None → off.
    report: optional dict the gate fills with {'chroma_limited': frac} for the sidecar."""
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

    for alpha, settings in compositional or []:
        if not settings or float(alpha.max()) <= 1e-4:
            continue
        edited = ops.apply_ops(out, settings)
        a3 = alpha[..., None]
        out = out * (1.0 - a3) + edited * a3

    if global_settings:
        if guard_mask is not None and guard_ops:
            full = ops.apply_ops(out, global_settings)
            kept = {k: v for k, v in global_settings.items() if k not in guard_ops}
            guarded = ops.apply_ops(out, kept) if kept else out
            m = guard_mask[..., None]
            out = full * (1.0 - m) + guarded * m  # protected pixels get global minus guarded ops
        else:
            out = ops.apply_ops(out, global_settings)

    out = np.clip(out, 0.0, 1.0)

    # Final safety pass: never let the edit be more saturated than the source allows.
    if chroma_max_increase is not None:
        out, limited = gates.chroma_guard(
            full_rgb.astype(np.float32), out,
            max_increase=chroma_max_increase, protect=protect_chroma,
        )
        if report is not None:
            report["chroma_limited"] = limited

    return out
