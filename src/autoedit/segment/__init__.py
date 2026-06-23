"""Segmentation: the Segmenter protocol, backends (stub / SAM 3), and mask resolution."""

from .base import RegionMask, Segmenter
from .cache import CachingSegmenter
from .resolve import ResolvedRegions, resolve
from .stub import StubSegmenter

__all__ = ["RegionMask", "Segmenter", "ResolvedRegions", "resolve", "StubSegmenter",
           "CachingSegmenter", "make_segmenter"]


def make_segmenter(backend: str = "stub", device: str = "auto", model_id: str = "facebook/sam3") -> Segmenter:
    """Construct a segmenter backend. `sam3` is imported lazily so torch stays optional."""
    if backend == "stub":
        return StubSegmenter()
    if backend == "sam3":
        from .sam3 import Sam3Segmenter

        return Sam3Segmenter(model_id=model_id, device=device)
    raise ValueError(f"unknown segmentation backend: {backend!r}")
