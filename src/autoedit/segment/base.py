"""The Segmenter protocol and the raw per-concept mask type.

A backend (StubSegmenter or Sam3Segmenter) takes the proxy image + a list of concept
prompts and returns one `RegionMask` per detected concept (instances already unioned).
Gating (min_score / min_coverage) and overlap arbitration happen later in resolve.py, so
backends stay dumb and swappable — the SAM license risk is isolated to one file (sam3.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np

from ..imageio import Image


@dataclass
class RegionMask:
    """A concept's union mask at proxy resolution."""

    name: str
    mask: np.ndarray  # bool, HxW (proxy resolution)
    score: float = 1.0
    coverage: float = 0.0
    instance_count: int = 1


@runtime_checkable
class Segmenter(Protocol):
    def segment(self, img: Image, concepts: list[str]) -> list[RegionMask]:
        """Return one RegionMask per detected concept (union of instances)."""
        ...
