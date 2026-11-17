"""The Segmenter protocol and the raw per-concept mask type.

A backend (StubSegmenter or Sam3Segmenter) takes the proxy image + a list of concept
prompts and returns one `RegionMask` per detected concept (instances already unioned).
Gating (min_score / min_coverage) and overlap arbitration happen later in resolve.py, so
backends stay dumb and swappable — the SAM license risk is isolated to one file (sam3.py).
"""

from __future__ import annotations

import re
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


MARK = "mark:"      # a concept name that is a mark the user drew (F37), not a noun phrase
_NUM = re.compile(r"[0-9]+(?:\.[0-9]+)?|\.[0-9]+")   # ASCII digits; no backtracking on a long run


def mark_name(kind: str, box, word: str = "") -> str:
    """The one spelling of a mark: `mark:thing:x0,y0,x1,y1:word` or `mark:area:x0,y0,x1,y1`.
    The box is in shares of the frame, written with four decimals. Raises ValueError for a kind
    that is not thing or area, a thing without a word, a box that is not four finite numbers in
    0..1, or x1 <= x0 or y1 <= y0 (after rounding)."""
    if kind not in ("thing", "area"):
        raise ValueError(f"mark kind {kind!r} is not thing or area")
    box = tuple(box)
    # 0 <= v <= 1 also refuses NaN and infinity, and never converts a huge integer to float
    if len(box) != 4 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1
                                for v in box):
        raise ValueError("a mark box is four numbers in 0..1")
    x0, y0, x1, y1 = (round(float(v), 4) + 0.0 for v in box)   # + 0.0: a negative zero is spelled 0.0000
    if not (x0 < x1 and y0 < y1):
        raise ValueError("a mark box needs x0 < x1 and y0 < y1")
    spelled = f"{MARK}{kind}:{x0:.4f},{y0:.4f},{x1:.4f},{y1:.4f}"
    word = " ".join(str(word).lower().split())
    if kind == "area":
        return spelled
    if not word:
        raise ValueError("a thing mark needs a word")
    return f"{spelled}:{word}"


def parse_mark(name: str) -> tuple[str, tuple[float, float, float, float], str] | None:
    """(kind, box, word) of a mark name. None for anything else, a malformed mark included."""
    if not isinstance(name, str) or not name.startswith(MARK):
        return None
    parts = name.split(":", 3)
    if len(parts) < 3 or parts[0] != "mark":
        return None
    nums = parts[2].split(",")
    if len(nums) != 4 or not all(_NUM.fullmatch(n) for n in nums):
        return None
    word = parts[3] if len(parts) == 4 else ""
    try:
        box = tuple(round(float(n), 4) for n in nums)
        spelled = mark_name(parts[1], box, word)
    except ValueError:
        return None
    if parts[1] == "area" and len(parts) == 4:
        return None
    return parts[1], box, spelled.split(":", 3)[3] if parts[1] == "thing" else ""
