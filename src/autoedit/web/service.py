"""Framework-agnostic web service logic (no FastAPI import here, so it's unit-testable).

Builds RunConfigs from UI options, lists styles/presets/images, renders single-image
before/after previews as base64 PNGs, and runs batches — all over the existing engine.
"""

from __future__ import annotations

import base64
import io
import os

import numpy as np
from PIL import Image as PILImage

from .. import batch
from ..concepts import load_concepts
from ..contract import RunConfig
from ..imageio import is_supported, load_image
from ..pipeline import Pipeline
from ..styles import load_styles

# UI-settable RunConfig fields (everything else stays at defaults / config-file values).
_ALLOWED = (
    "style", "prompt", "preset", "look", "intensity", "decider", "allow_cloud", "api_key",
    "backend", "device", "out_format", "quality", "emit", "compose",
)


def make_cfg(opts: dict, configs_dir: str = "configs") -> RunConfig:
    kw: dict = {
        "rules_path": os.path.join(configs_dir, "rules.toml"),
        "looks_path": os.path.join(configs_dir, "looks.toml"),
        "concepts_path": os.path.join(configs_dir, "concepts.toml"),
        "styles_path": os.path.join(configs_dir, "styles.toml"),
        "compose_path": os.path.join(configs_dir, "compose.toml"),
    }
    for k in _ALLOWED:
        if opts.get(k) is not None:
            kw[k] = opts[k]
    concepts = opts.get("concepts")
    if isinstance(concepts, str) and concepts.strip():
        kw["concepts"] = [c.strip() for c in concepts.split(",") if c.strip()]
    elif isinstance(concepts, list) and concepts:
        kw["concepts"] = concepts
    return RunConfig(**kw)


def list_styles(configs_dir: str = "configs") -> list[dict]:
    styles = load_styles(os.path.join(configs_dir, "styles.toml"))
    return [{"name": s.name, "look": s.look, "brief": s.brief, "concepts": s.concepts}
            for s in styles.values()]


def list_presets(configs_dir: str = "configs") -> dict[str, list[str]]:
    presets, _ = load_concepts(os.path.join(configs_dir, "concepts.toml"))
    return presets


def list_images(folder: str) -> list[str]:
    if not folder or not os.path.isdir(folder):
        return []
    return [n for n in sorted(os.listdir(folder))
            if os.path.isfile(os.path.join(folder, n)) and is_supported(n)]


def _png_b64(rgb01: np.ndarray, max_px: int = 900) -> str:
    arr = np.clip(rgb01, 0.0, 1.0)
    pil = PILImage.fromarray((arr * 255 + 0.5).astype(np.uint8), "RGB")
    if max(pil.size) > max_px:
        scale = max_px / max(pil.size)
        pil = pil.resize((max(1, int(pil.width * scale)), max(1, int(pil.height * scale))), PILImage.BILINEAR)
    buf = io.BytesIO()
    pil.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def preview(image_path: str, opts: dict, configs_dir: str = "configs", max_px: int = 900) -> dict:
    """Render a single-image before/after preview + the decision sidecar."""
    cfg = make_cfg(opts, configs_dir)
    pipe = Pipeline.build(cfg)
    out_rgb, result = pipe.process_one(image_path, do_apply=True)
    before = load_image(image_path).rgb
    return {
        "before": _png_b64(before, max_px),
        "after": _png_b64(out_rgb, max_px),
        "result": result.model_dump(),
    }


def run_batch(input_dir: str, out_dir: str, opts: dict, configs_dir: str = "configs") -> dict:
    cfg = make_cfg(opts, configs_dir)
    return batch.run(cfg, input_dir, out_dir, do_apply=True)
