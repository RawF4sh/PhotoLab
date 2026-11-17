"""Sam3Segmenter — real open-vocabulary segmentation via Meta's SAM 3.

Isolated here so the SAM custom license + the torch dependency never leak into the rest
of the package (CLAUDE-style invariant). Requires the `sam` extra: `pip install '.[sam]'`.

SAM 3 ships *inside* Hugging Face Transformers (>=5.12) as `Sam3Model` + `Sam3Processor`
(model id `facebook/sam3`) — there is no standalone `sam3` PyPI package. It does Promptable
Concept Segmentation: given a short noun-phrase ("the dress", "snow", "sky") it returns a mask
+ score for *every* instance of that concept. The image encoder runs once per photo and each
concept is one light pass on its output, with the instances unioned; per-concept gating +
overlap arbitration happen downstream in resolve.py.

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
from .base import RegionMask, parse_mark
from .cache import pixel_digest

# Must be set before torch initialises the MPS backend; harmless elsewhere.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")


_IMPORTABLE: bool | None = None


def importable() -> bool:
    """Can the SAM 3 classes really be imported here? The packages can be installed and the import
    can still fail (seen: a broken binary in a dependency), and only the import tells. It costs
    the torch import once; the answer is kept for the process."""
    global _IMPORTABLE
    if _IMPORTABLE is None:
        try:
            from transformers import Sam3Model, Sam3Processor  # type: ignore  # noqa: F401
            _IMPORTABLE = True
        except Exception:
            _IMPORTABLE = False
    return _IMPORTABLE


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


def _centre_in(mask: np.ndarray, box) -> bool:
    """Does the middle of a mask (the mean position of its pixels) lie in a box given in shares
    of the frame?"""
    ys, xs = np.nonzero(mask)
    cx, cy = xs.mean() / mask.shape[1], ys.mean() / mask.shape[0]
    return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]


class Sam3Segmenter:
    name = "sam3"
    open_vocabulary = True

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
        # Digest, image features and sizes of the last photo, so that a new concept on the same
        # photo skips the encoder.
        self._kept = None

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

        def _load(offline: bool):
            model = Sam3Model.from_pretrained(self.model_id, local_files_only=offline)
            proc = Sam3Processor.from_pretrained(self.model_id, local_files_only=offline)
            return model.to(self.device).eval(), proc

        try:
            # Cached → load fully offline (instant, and never hangs on an HF network ping).
            self._model, self._processor = _load(True)
        except Exception:
            try:
                self._model, self._processor = _load(False)  # not cached → fetch from HF
            except Exception as exc:  # pragma: no cover - network/auth, only with the sam extra
                raise RuntimeError(
                    f"Could not load SAM 3 weights ('{self.model_id}'): {exc}. "
                    "If this is auth/403: the repo is gated — accept the license at "
                    "https://huggingface.co/facebook/sam3 and authenticate (`huggingface-cli "
                    "login` or HF_TOKEN)."
                ) from exc

    def segment(self, img: Image, concepts: list[str]) -> list[RegionMask]:
        if not concepts:
            return []
        self._ensure_model()
        h, w = img.height, img.width
        out: list[RegionMask] = []
        key = pixel_digest(img.rgb)
        if self._kept is not None and self._kept[0] == key:
            vision, sizes = self._kept[1], self._kept[2]
        else:
            self._kept = None                   # let go of the last photo before encoding the next
            vision, sizes = self._encode(img.rgb)   # the image encoder: once per photo
            self._kept = (key, vision, sizes)
        for concept in concepts:
            mark = parse_mark(concept)
            if mark is not None and mark[0] != "thing":
                continue                        # an area is a shape, not something to find
            instances = self._run_concept(vision, sizes, mark[2] if mark else concept)
            if mark is not None:                # of everything with that name: what was pointed at
                instances = [(m, s) for m, s in instances if _centre_in(m, mark[1])]
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
        if self.device == "mps":
            # Hand the allocator's unused GPU buffers back after every photo. Measured on 16
            # photos (F36): the Metal driver then holds 12.1 GB instead of 15.5 GB, and the time
            # per photo does not change.
            import torch

            torch.mps.empty_cache()
        return out

    def _encode(self, image_rgb):
        """The image encoder, once per photo. Every concept reuses its output: the full forward
        is this call followed by the per-concept part, so the masks are the same."""
        import torch
        from PIL import Image as PILImage

        # The processor expects uint8 PIL (0-255); our proxy rgb is float [0,1].
        pil = PILImage.fromarray((np.clip(image_rgb, 0.0, 1.0) * 255 + 0.5).astype(np.uint8), "RGB")
        inputs = self._processor(images=pil, return_tensors="pt").to(self.device)
        with torch.no_grad():
            vision = self._model.get_vision_features(pixel_values=inputs["pixel_values"])
        sizes = inputs.get("original_sizes")
        sizes = sizes.to("cpu").tolist() if hasattr(sizes, "to") else sizes
        return vision, sizes

    def _run_concept(self, vision, sizes, concept: str):
        """The per-concept SAM 3 pass on the encoded image. Returns [(bool HxW mask, float score),
        ...] for every instance of `concept`, with masks at the proxy resolution we feed in
        (resolve.py upsamples + feathers later). Open-vocab tolerance: a concept with no matches
        just returns []."""
        import torch

        text = self._processor(text=concept, return_tensors="pt").to(self.device)
        with torch.no_grad():
            outputs = self._model(vision_embeds=vision, input_ids=text["input_ids"],
                                  attention_mask=text.get("attention_mask"))

        # Run mask post-processing on CPU. torch's MPS backend has aborted (SIGABRT in the
        # Metal driver) inside bitwise/binary ops on bool masks for some inputs; the heavy
        # forward stays on the GPU, only the lightweight thresholding/union moves to CPU.
        items = getattr(outputs, "items", None)
        if callable(items):
            for k, v in list(items()):
                if isinstance(v, torch.Tensor):
                    outputs[k] = v.to("cpu")

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
