"""Sam3Segmenter — real open-vocabulary segmentation via Meta's SAM 3.

Isolated here so the SAM custom license + the torch dependency never leak into the rest
of the package (CLAUDE-style invariant). Requires the `sam` extra: `pip install '.[sam]'`.

NOTE: the exact SAM 3 inference call is pinned to the published `sam3` API at integration
time (Phase S spike). The wrapper below establishes the device handling, the
concept-prompt loop, and instance→union reduction; the marked line is where the model's
concept-segmentation call goes. Weights are fetched from Hugging Face on first use — never
vendored into this repo (license).
"""

from __future__ import annotations

import numpy as np

from ..imageio import Image
from .base import RegionMask


def pick_device(requested: str = "auto") -> str:
    """Resolve device: explicit, else cuda > mps > cpu."""
    try:
        import torch
    except Exception as exc:  # pragma: no cover - exercised only with the sam extra
        raise RuntimeError("SAM 3 backend needs torch — install with `pip install '.[sam]'`") from exc

    if requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class Sam3Segmenter:
    name = "sam3"

    def __init__(self, model_id: str = "facebook/sam3", device: str = "auto"):
        self.device = pick_device(device)
        self.model_id = model_id
        self._model = None  # lazily loaded, then kept resident for the whole run

    def _ensure_model(self):
        if self._model is not None:
            return
        # Lazy import keeps `import autoedit` cheap and torch optional.
        from sam3 import Sam3Model  # type: ignore  # provided by the `sam` extra

        self._model = Sam3Model.from_pretrained(self.model_id).to(self.device).eval()

    def segment(self, img: Image, concepts: list[str]) -> list[RegionMask]:
        self._ensure_model()
        h, w = img.height, img.width
        out: list[RegionMask] = []
        for concept in concepts:
            instances = self._run_concept(img.rgb, concept)
            if not instances:
                continue
            union = np.zeros((h, w), dtype=bool)
            best = 0.0
            for mask, score in instances:
                union |= np.asarray(mask, dtype=bool)
                best = max(best, float(score))
            if not union.any():
                continue
            out.append(
                RegionMask(
                    name=concept,
                    mask=union,
                    score=best,
                    coverage=float(union.mean()),
                    instance_count=len(instances),
                )
            )
        return out

    def _run_concept(self, image_rgb, concept: str):
        """The ONE SAM 3 inference call. Returns [(bool HxW mask, float score), ...] for every
        instance of `concept`. Pin to the published `sam3` API during the Phase-A spike on a
        GPU box; the method name below is the expected entry point."""
        fn = getattr(self._model, "segment_concept", None)
        if fn is None:  # pragma: no cover - exercised only with the sam extra installed
            raise RuntimeError(
                "SAM 3 model has no `segment_concept`; wire Sam3Segmenter._run_concept to the "
                "installed sam3 API (Phase-A spike). The rest of the pipeline already consumes "
                "(mask, score) instances."
            )
        return fn(image_rgb, concept)
