"""Shoot-level consistency (Codex review F5).

A batch shouldn't edit every photo in a vacuum — adjacent shots of the same scene should land
at a coherent brightness instead of drifting per-image on segmentation/stat noise. This is a
cheap two-pass anchor: pass 1 measures each image's brightness, clusters images into lighting
groups, and computes a per-group target; pass 2 nudges each image a BOUNDED amount toward its
group target (the per-scene look still dominates — this only removes drift).

Kept deliberately light: grouping uses brightness only (no extra segmentation). Time/EXIF/WB
clustering is a future upgrade; the data structure already carries a group id per image.
"""

from __future__ import annotations

import numpy as np

from .imageio import downscale_long_edge, load_image, luma

# A pixel-brightness gap wider than this starts a new lighting group (0..1 luma).
_GROUP_GAP = 0.08
# Max brightness nudge toward the group. UNITS: this is a display-referred luma delta (0..1)
# fed to the exposure op as if it were EV — not true photometric stops. At this magnitude the
# approximation is fine (it only removes drift), but don't read the value as camera EV.
_MAX_BIAS_EV = 0.15


def quick_lum(path: str, px: int = 384) -> float:
    """Cheap mean luminance from a small proxy — no segmentation (pass-1 signal)."""
    proxy = downscale_long_edge(load_image(path), px)
    return float(luma(proxy.rgb).mean())


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def plan_groups(lums: list[tuple[str, float]], gap: float = _GROUP_GAP) -> dict[str, tuple[str, float]]:
    """Cluster images by brightness (1-D split on gaps). Returns path -> (group_id, target_lum)."""
    if not lums:
        return {}
    order = sorted(lums, key=lambda t: t[1])
    groups: list[list[tuple[str, float]]] = [[order[0]]]
    for item in order[1:]:
        if item[1] - groups[-1][-1][1] > gap:
            groups.append([item])
        else:
            groups[-1].append(item)
    out: dict[str, tuple[str, float]] = {}
    for gi, grp in enumerate(groups, start=1):
        target = _median([lm for _p, lm in grp])
        gid = f"g{gi}"
        for path, _lm in grp:
            out[path] = (gid, target)
    return out


def exposure_bias(image_lum: float, target_lum: float) -> float:
    """Bounded exposure nudge toward the group target — a luma delta used as an EV
    approximation (see _MAX_BIAS_EV note on units)."""
    return float(np.clip(target_lum - image_lum, -_MAX_BIAS_EV, _MAX_BIAS_EV))
