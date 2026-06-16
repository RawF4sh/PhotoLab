"""Sam3Segmenter — real open-vocabulary segmentation via Meta's SAM 3.

Isolated here so the SAM custom license + the torch dependency never leak into the rest
of the package (CLAUDE-style invariant). Requires the `sam` extra: `pip install '.[sam]'`.

SAM 3 ships *inside* Hugging Face Transformers (>=5.12) as `Sam3Model` + `Sam3Processor`
(model id `facebook/sam3`) — there is no standalone `sam3` PyPI package. It does Promptable
Concept Segmentation: given a short noun-phrase ("the dress", "snow", "sky") it returns a mask
+ score for *every* instance of that concept. We run one text prompt per concept and union the
instances; per-concept gating + overlap arbitration happen downstream in resolve.py.

Weights are gated on Hugging Face: accept the license at https://huggingface.co/facebook/sam3
and authenticate (`huggingface-cli login`, or set HF_TOKEN) before first run. They are fetched
on first use and cached — never vendored into this repo (license).

Apple Silicon (M-series): runs on the `mps` (Metal) device. We default-enable
`PYTORCH_ENABLE_MPS_FALLBACK=1` so the handful of ops MPS doesn't implement fall back to CPU
instead of crashing the run.
"""

from __future__ import annotations

import os

import numpy as np

from ..imageio import Image
from .base import RegionMask

# Must be set before torch initialises the MPS backend; harmless elsewhere.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")


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

    def __init__(self, model_id: str = "facebook/sam3", device: str = "auto",
                 score_threshold: float = 0.4, mask_threshold: float = 0.5):
        self.device = pick_device(device)
        self.model_id = model_id
        # Kept a touch permissive — resolve.py applies the authoritative per-concept min_score
        # (configs/concepts.toml); this only drops near-zero DETR queries before that.
        self.score_threshold = score_threshold
        self.mask_threshold = mask_threshold
        self._model = None  # lazily loaded, then kept resident for the whole run
        self._processor = None

    def _ensure_model(self):
        if self._model is not None:
            return
        # Lazy import keeps `import autoedit` cheap and torch/transformers optional.
        try:
            from transformers import Sam3Model, Sam3Processor  # type: ignore
        except Exception as exc:  # pragma: no cover - exercised only with the sam extra
            raise RuntimeError(
                "SAM 3 needs Transformers >=5.12 with torch. Install the backend with "
                "`pip install '.[sam]'` (Apple Silicon: see README — uses the mps device)."
            ) from exc

        try:
            self._model = Sam3Model.from_pretrained(self.model_id).to(self.device).eval()
            self._processor = Sam3Processor.from_pretrained(self.model_id)
        except Exception as exc:  # pragma: no cover - network/auth, only with the sam extra
            raise RuntimeError(
                f"Could not load SAM 3 weights ('{self.model_id}'). The repo is gated: accept the "
                "license at https://huggingface.co/facebook/sam3 and authenticate "
                "(`huggingface-cli login` or set HF_TOKEN), then retry."
            ) from exc

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
        instance of `concept`, with masks at the proxy resolution we feed in (resolve.py upsamples
        + feathers later). Open-vocab tolerance: a concept with no matches just returns []."""
        import torch
        from PIL import Image as PILImage

        # The processor expects uint8 PIL (0-255); our proxy rgb is float [0,1].
        pil = PILImage.fromarray((np.clip(image_rgb, 0.0, 1.0) * 255 + 0.5).astype(np.uint8), "RGB")
        inputs = self._processor(images=pil, text=concept, return_tensors="pt").to(self.device)
        with torch.no_grad():
            outputs = self._model(**inputs)

        # Run mask post-processing on CPU. torch's MPS backend has aborted (SIGABRT in the
        # Metal driver) inside bitwise/binary ops on bool masks for some inputs; the heavy
        # forward stays on the GPU, only the lightweight thresholding/union moves to CPU.
        items = getattr(outputs, "items", None)
        if callable(items):
            for k, v in list(items()):
                if isinstance(v, torch.Tensor):
                    outputs[k] = v.to("cpu")
        sizes = inputs.get("original_sizes")
        sizes = sizes.to("cpu").tolist() if hasattr(sizes, "to") else sizes

        # post_process returns one dict per image; masks are resized to target (== proxy) size.
        results = self._processor.post_process_instance_segmentation(
            outputs,
            threshold=self.score_threshold,
            mask_threshold=self.mask_threshold,
            target_sizes=sizes,
        )[0]

        instances: list[tuple[np.ndarray, float]] = []
        for mask, score in zip(results["masks"], results["scores"]):
            m = mask.detach().to("cpu").numpy() if hasattr(mask, "detach") else np.asarray(mask)
            m = np.squeeze(m).astype(bool)
            if m.ndim == 2 and m.any():
                instances.append((m, float(score)))
        return instances
