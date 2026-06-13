"""Mask resolution: gate absent concepts, arbitrate overlaps into a non-overlapping
label map so no pixel is edited by more than one region.

- Union masks (raw, possibly overlapping) are kept for stats where overlap is fine
  (e.g. measuring mean skin luminance even where skin sits inside subject).
- Arbitrated masks (non-overlapping) drive the actual edits — higher `priority` wins
  contested pixels. Edit/apply composes regions in priority order (highest first).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..concepts import ConceptMeta, meta_for
from ..contract import DetectedConcept
from .base import RegionMask


@dataclass
class ResolvedRegions:
    regions: list[RegionMask]  # arbitrated, non-overlapping, priority-desc order (for apply)
    union: dict[str, np.ndarray]  # present concept -> union bool mask (for stats)
    detected: list[DetectedConcept]  # every requested concept, present or not (for sidecar)
    label_map: np.ndarray  # int HxW; 0 = background
    shape: tuple[int, int] = field(default=(0, 0))


def resolve(
    raw: list[RegionMask],
    requested: list[str],
    meta: dict[str, ConceptMeta],
    shape: tuple[int, int],
) -> ResolvedRegions:
    by_name = {rm.name: rm for rm in raw}

    detected: list[DetectedConcept] = []
    present: list[tuple[RegionMask, ConceptMeta]] = []
    union: dict[str, np.ndarray] = {}

    for name in requested:
        rm = by_name.get(name)
        cm = meta_for(name, meta)
        if rm is None:
            detected.append(DetectedConcept(name=name, present=False, score=0.0, coverage=0.0))
            continue
        cov = float(rm.mask.mean())
        is_present = rm.score >= cm.min_score and cov >= cm.min_coverage and rm.mask.any()
        detected.append(
            DetectedConcept(
                name=name,
                present=is_present,
                score=float(rm.score),
                coverage=cov,
                instance_count=rm.instance_count,
            )
        )
        if is_present:
            present.append((rm, cm))
            union[name] = rm.mask

    # arbitrate: highest priority claims contested pixels first
    present.sort(key=lambda pc: pc[1].priority, reverse=True)
    label_map = np.zeros(shape, dtype=np.int32)
    regions: list[RegionMask] = []
    for idx, (rm, _cm) in enumerate(present, start=1):
        claim = rm.mask & (label_map == 0)
        if not claim.any():
            continue
        label_map[claim] = idx
        regions.append(
            RegionMask(
                name=rm.name,
                mask=claim,
                score=rm.score,
                coverage=float(claim.mean()),
                instance_count=rm.instance_count,
            )
        )

    return ResolvedRegions(regions=regions, union=union, detected=detected, label_map=label_map, shape=shape)
