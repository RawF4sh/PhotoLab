"""Framework-agnostic web service logic (no FastAPI import here, so it's unit-testable).

Builds RunConfigs from UI options, lists styles/presets/images, renders single-image
before/after previews as base64 PNGs, and runs batches — all over the existing engine.
"""

from __future__ import annotations

import base64
import io
import os
import subprocess
import sys
import threading

import numpy as np
from PIL import Image as PILImage

from .. import batch, emit, paths
from ..concepts import load_concepts
from ..contract import RunConfig
from ..imageio import downscale_long_edge, is_supported, load_image
from ..pipeline import Pipeline
from ..styles import load_styles

# UI-settable RunConfig fields (everything else stays at defaults / config-file values).
_ALLOWED = (
    "style", "prompt", "preset", "look", "intensity", "reference", "reference_strength",
    "decider", "allow_cloud", "api_key", "backend", "device", "out_format", "quality",
    "emit", "compose", "vignette", "sharpen",
)


def make_cfg(opts: dict, configs_dir: str | None = None) -> RunConfig:
    configs_dir = configs_dir or paths.configs_dir()
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


# A resident segmenter per (backend, device) so the UI doesn't reload the 848M model on every
# request, and a lock that serializes inference (one GPU model isn't concurrency-safe).
_SEG_CACHE: dict = {}
_SEG_LOCK = threading.Lock()
INFERENCE_LOCK = threading.Lock()

# ── filesystem trust boundary (Codex review F4) ───────────────────────────────
# The web API takes file/dir paths from HTTP requests. To stop a reachable server from reading
# or writing arbitrary local paths, every request path must descend from a folder the user
# actually chose via the native picker this session. Set AUTOEDIT_ALLOW_ANY_PATH=1 to opt out.
_ROOTS_LOCK = threading.Lock()
_ALLOWED_ROOTS: set[str] = set()


def approve_root(path: str) -> None:
    """Mark a picked folder (or a picked file's folder) as an allowed workspace root."""
    if not path:
        return
    rp = os.path.realpath(path)
    root = rp if os.path.isdir(rp) else os.path.dirname(rp)
    with _ROOTS_LOCK:
        _ALLOWED_ROOTS.add(root)


def path_allowed(path: str) -> bool:
    if os.environ.get("AUTOEDIT_ALLOW_ANY_PATH", "").lower() in ("1", "true", "yes"):
        return True
    if not path:
        return False
    rp = os.path.realpath(path)
    with _ROOTS_LOCK:
        roots = list(_ALLOWED_ROOTS)
    for root in roots:
        try:
            if os.path.commonpath([rp, root]) == root:
                return True
        except ValueError:  # different drives on Windows, etc.
            continue
    return False


def ensure_allowed(*candidate_paths: str | None) -> None:
    """Raise PermissionError if any given path is outside every approved workspace root."""
    for p in candidate_paths:
        if p and not path_allowed(p):
            raise PermissionError(
                "path is outside an approved folder — choose it with the folder/file picker "
                f"first (or set AUTOEDIT_ALLOW_ANY_PATH=1 to disable this check): {p}")


def shared_segmenter(cfg: RunConfig):
    key = (cfg.backend, cfg.device)
    with _SEG_LOCK:
        seg = _SEG_CACHE.get(key)
        if seg is None:
            from ..segment import CachingSegmenter, make_segmenter
            # Wrap in the per-concept cache so re-previewing the same image under a different
            # style/intensity only segments the *new* concepts (style switches feel instant).
            seg = CachingSegmenter(make_segmenter(cfg.backend, cfg.device))
            _SEG_CACHE[key] = seg
        return seg


def build_pipeline(cfg: RunConfig) -> Pipeline:
    """A Pipeline using the resident (cached) segmenter — one model load per process."""
    pipe = Pipeline.build(cfg)
    pipe.segmenter = shared_segmenter(cfg)
    return pipe


def list_styles(configs_dir: str | None = None) -> list[dict]:
    configs_dir = configs_dir or paths.configs_dir()
    styles = load_styles(os.path.join(configs_dir, "styles.toml"))
    return [{"name": s.name, "look": s.look, "brief": s.brief, "concepts": s.concepts}
            for s in styles.values()]


def list_presets(configs_dir: str | None = None) -> dict[str, list[str]]:
    configs_dir = configs_dir or paths.configs_dir()
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


def thumb_bytes(path: str, px: int = 360, quality: int = 82) -> bytes:
    """A small JPEG of any image on disk (by path) — for the UI's real thumbnails/previews.
    Cheap: decode + downscale, no pipeline/segmentation."""
    check_image(path)
    proxy = downscale_long_edge(load_image(path), px)
    pil = PILImage.fromarray((np.clip(proxy.rgb, 0.0, 1.0) * 255 + 0.5).astype(np.uint8), "RGB")
    buf = io.BytesIO()
    pil.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def check_image(path: str) -> None:
    """Raise a clear ValueError if `path` isn't an openable supported image."""
    if not path:
        raise ValueError("no file selected")
    if not os.path.isfile(path):
        raise ValueError(f"file not found: {path}")
    if not is_supported(path):
        ext = os.path.splitext(path)[1] or "(none)"
        raise ValueError(f"not a supported image (extension {ext}); pick a JPG/PNG/TIFF, "
                         f"not a sidecar like .xmp")


def preview(image_path: str, opts: dict, configs_dir: str | None = None, max_px: int = 900) -> dict:
    """Render a single-image before/after preview + the decision sidecar."""
    check_image(image_path)
    cfg = make_cfg(opts, configs_dir)
    pipe = build_pipeline(cfg)
    work = downscale_long_edge(load_image(image_path), 1400)  # preview applies on a proxy → fast
    with INFERENCE_LOCK:
        out_rgb, result = pipe.process_one(image_path, do_apply=True, image=work)
    return {
        "before": _png_b64(work.rgb, max_px),
        "after": _png_b64(out_rgb, max_px),
        "result": result.model_dump(),
    }


def run_batch(input_dir: str, out_dir: str, opts: dict, configs_dir: str | None = None) -> dict:
    cfg = make_cfg(opts, configs_dir)
    return batch.run(cfg, input_dir, out_dir, do_apply=True)


def run_one(image_path: str, out_dir: str, opts: dict, configs_dir: str | None = None) -> dict:
    """Edit a single photo to out_dir (baked image + sidecar)."""
    check_image(image_path)
    cfg = make_cfg(opts, configs_dir)
    pipe = build_pipeline(cfg)
    with INFERENCE_LOCK:
        out_rgb, result = pipe.process_one(image_path, do_apply=True)
    os.makedirs(out_dir, exist_ok=True)
    image_out, sidecar = emit.output_paths(out_dir, image_path, cfg.out_format)
    emit.write_image(out_rgb, image_out, cfg.out_format, cfg.quality)
    emit.write_sidecar(result, sidecar)
    return {"image_out": image_out, "sidecar": sidecar, "result": result.model_dump()}


def pick_path(kind: str = "folder") -> str | None:
    """Open a native picker and return the chosen path, registering it as an approved workspace
    root (so subsequent API calls for paths under it pass the filesystem trust boundary)."""
    path = _pick_native(kind)
    if path:
        approve_root(path)
    return path


def _pick_native(kind: str = "folder") -> str | None:
    """Open a NATIVE picker on the machine running the server (the user's own Mac/PC) and
    return the chosen absolute path. kind: 'folder' | 'file'. Returns None on cancel.

    macOS uses Finder via osascript; other platforms fall back to a tkinter dialog. This is
    why AutoEdit's UI is a *local* app — the server can read local paths and pop a real dialog,
    which a sandboxed browser cannot do on its own."""
    if sys.platform == "darwin":
        if kind == "folder":
            verb = 'choose folder with prompt "Select a folder for AutoEdit"'
        else:  # restrict to images so users can't pick sidecars (.xmp) etc.
            verb = 'choose file of type {"public.image"} with prompt "Select a photo for AutoEdit"'
        try:
            r = subprocess.run(["osascript", "-e", f"POSIX path of ({verb})"],
                               capture_output=True, text=True)
            return r.stdout.strip() or None if r.returncode == 0 else None
        except FileNotFoundError:
            return None
    try:  # cross-platform fallback
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        if kind == "folder":
            path = filedialog.askdirectory()
        else:
            path = filedialog.askopenfilename(
                filetypes=[("Images", "*.jpg *.jpeg *.png *.tif *.tiff *.webp *.bmp"), ("All", "*.*")])
        root.destroy()
        return path or None
    except Exception:
        return None
