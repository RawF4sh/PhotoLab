"""Segmentation: the Segmenter protocol, backends (stub / SAM 3), and mask resolution."""

from .base import RegionMask, Segmenter
from .cache import MASK_CACHE_DIR, CachingSegmenter, DiskMaskCache
from .resolve import ResolvedRegions, resolve
from .stub import StubSegmenter

__all__ = ["RegionMask", "Segmenter", "ResolvedRegions", "resolve", "StubSegmenter",
           "CachingSegmenter", "DiskMaskCache", "MASK_CACHE_DIR", "make_segmenter",
           "sam3_ready", "resolve_backend"]


def make_segmenter(backend: str = "stub", device: str = "auto", model_id: str = "facebook/sam3") -> Segmenter:
    """Construct a segmenter backend. `sam3` is imported lazily so torch stays optional."""
    backend = resolve_backend(backend)   # "auto" is accepted here so no caller has to know about it
    if backend == "stub":
        return StubSegmenter()
    if backend == "sam3":
        from .sam3 import Sam3Segmenter

        return Sam3Segmenter(model_id=model_id, device=device)
    raise ValueError(f"unknown segmentation backend: {backend!r}")


def sam3_ready(model_id: str = "facebook/sam3", cache_dir: str | None = None) -> bool:
    """True when SAM 3 can run without a download: torch and a transformers that has SAM 3 are
    installed, the config AND a weights file are in the local Hugging Face cache (a config alone
    would start a download), and the SAM 3 classes import. The cheap checks come first; only
    when they all pass are the classes imported, once, in `segment/sam3.py` (that pulls in
    torch): a broken dependency shows on import and nowhere else."""
    import importlib.util
    import os

    try:
        if not (importlib.util.find_spec("torch") and importlib.util.find_spec("transformers")
                and importlib.util.find_spec("transformers.models.sam3")):
            return False
        from huggingface_hub import try_to_load_from_cache

        config = try_to_load_from_cache(model_id, "config.json", cache_dir=cache_dir)
        if not isinstance(config, str):
            return False
        if not any(n.endswith((".safetensors", ".bin")) for n in os.listdir(os.path.dirname(config))):
            return False
        from . import sam3

        return sam3.importable()
    except Exception:
        return False


def resolve_backend(backend: str) -> str:
    """"auto" -> "sam3" when it is ready, else "stub". Any other name is returned as is."""
    return ("sam3" if sam3_ready() else "stub") if backend == "auto" else backend
