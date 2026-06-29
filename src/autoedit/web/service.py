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
from ..imageio import downscale_long_edge, is_raw, is_supported, load_image
from ..pipeline import Pipeline
from ..styles import load_styles

# UI-settable RunConfig fields (everything else stays at defaults / config-file values).
_ALLOWED = (
    "style", "prompt", "preset", "look", "intensity", "palette", "palette_strength",
    "reference", "reference_strength",
    "decider", "recipe_model", "allow_cloud", "api_key", "backend", "device", "out_format", "quality",
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
        "intents_path": os.path.join(configs_dir, "intents.toml"),
        "palettes_path": os.path.join(configs_dir, "palettes.toml"),
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


def group_images(folder: str) -> list[dict]:
    """Group files that share a basename — a camera's RAW+JPEG pair (IMG_001.dng + IMG_001.jpg)
    is one logical photo. Returns, in listing order, one dict per group:
        {stem, members:[{name, kind}], raw, pixel}
    where `raw`/`pixel` name the preferred file of each kind (None if absent). The UI shows one
    row per group and edits a single chosen source — RAW preferred for latitude — so a paired
    shoot isn't processed twice."""
    groups: dict[str, dict] = {}
    order: list[str] = []
    for f in list_images(folder):
        stem = os.path.splitext(f)[0]
        key = stem.lower()
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"stem": stem, "members": []}
            order.append(key)
        g["members"].append({"name": f, "kind": "raw" if is_raw(f) else "pixel"})
    out: list[dict] = []
    for key in order:
        g = groups[key]
        raw = next((m["name"] for m in g["members"] if m["kind"] == "raw"), None)
        pixel = next((m["name"] for m in g["members"] if m["kind"] == "pixel"), None)
        out.append({"stem": g["stem"], "members": g["members"], "raw": raw, "pixel": pixel})
    return out


def _img_b64(rgb01: np.ndarray, max_px: int = 1600, fmt: str = "jpeg", quality: int = 90) -> str:
    """Encode a working RGB array as a base64 data-URL. JPEG at q90 keeps a 1600px preview small
    (~200–500 KB) so we can show it sharp; PNG stays available for lossless needs."""
    from ..imageio import _to_u8
    rgb = np.clip(rgb01.astype(np.float32), 0.0, 1.0)
    h, w = rgb.shape[:2]
    if max(h, w) > max_px:
        scale = max_px / max(h, w)
        new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
        chans = [
            np.asarray(
                PILImage.fromarray(rgb[..., c], "F").resize((new_w, new_h), PILImage.LANCZOS),
                dtype=np.float32,
            )
            for c in range(3)
        ]
        rgb = np.stack(chans, axis=-1)
    pil = PILImage.fromarray(_to_u8(rgb), "RGB")  # final-size dither → no preview sky banding
    buf = io.BytesIO()
    if fmt in ("jpg", "jpeg"):
        pil.save(buf, format="JPEG", quality=quality, subsampling=0)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
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


def preview(image_path: str, opts: dict, configs_dir: str | None = None, max_px: int = 2560) -> dict:
    """Render a single-image before/after preview + the decision sidecar.

    The edit is applied at FULL resolution (bounded to a 4096px long edge for monster files), so the
    preview shows the real result — no proxy-scaling softness. The before/after ship as **lossless
    PNG** (not JPEG): JPEG's DCT quantizes away the anti-banding dither in flat areas like sky and
    re-introduces contour banding, so a quality-critical preview must stay lossless. Capped at
    max_px (retina-sharp at the on-screen stage). Segmentation is cached, so re-previews are fast."""
    check_image(image_path)
    cfg = make_cfg(opts, configs_dir)
    pipe = build_pipeline(cfg)
    work = load_image(image_path)
    if work.long_edge > 4096:                       # bound apply cost on very large files
        work = downscale_long_edge(work, 4096)
    with INFERENCE_LOCK:
        out_rgb, result = pipe.process_one(image_path, do_apply=True, image=work)
    return {
        "before": _img_b64(work.rgb, max_px, fmt="png"),
        "after": _img_b64(out_rgb, max_px, fmt="png"),
        "result": result.model_dump(),
    }


def run_batch(input_dir: str, out_dir: str, opts: dict, configs_dir: str | None = None) -> dict:
    cfg = make_cfg(opts, configs_dir)
    return batch.run(cfg, input_dir, out_dir, do_apply=True)


def curate_run(input_dir: str, out_dir: str, opts: dict) -> dict:
    """Cull a folder into `out_dir/selected` + thumbs + contact-sheet (M1). Copy-only — originals
    are never moved/renamed/deleted. Modes: diverse | best | aesthetic. Returns the manifest."""
    from .. import curate  # light deps (numpy/PIL); imported here to keep service import lean

    mode = str(opts.get("mode", "diverse"))
    scan_limit = opts.get("scan_limit")
    return curate.curate(
        input_dir, out_dir,
        count=int(opts.get("count", 200)),
        recursive=bool(opts.get("recursive", True)),
        scan_limit=int(scan_limit) if scan_limit else None,
        copy_originals=True,                              # roadmap: copy-only, always
        mode=mode,
        emit_xmp=bool(opts.get("emit_xmp", False)),
        aesthetic_model_path=opts.get("aesthetic_model"),
    )


def save_review_labels(out_dir: str, records: list[dict]) -> dict:
    """Append human review labels (M2) under out_dir as review-labels.csv/json. Annotation only —
    no files moved/deleted."""
    from .. import review

    return review.append_labels(records, out_dir)


def dataset_validate(originals_dir: str, targets_dir: str, recursive: bool = False) -> dict:
    """Pair + preservation-validate originals↔targets (M3), report only — nothing copied."""
    from .. import dataset

    return dataset.validate(originals_dir, targets_dir, recursive=recursive)


def dataset_build(originals_dir: str, targets_dir: str, out_dir: str, opts: dict) -> dict:
    """Build a copy-only training workspace from originals↔target pairs (M3)."""
    from .. import dataset

    return dataset.build_dataset(
        originals_dir, targets_dir, out_dir,
        recursive=bool(opts.get("recursive", False)),
        do_validate=bool(opts.get("validate", True)),
        approve_all=bool(opts.get("approve_all", False)),
    )


def aesthetic_train(opts: dict) -> dict:
    """Train + save an aesthetic-critic model JSON from a labels CSV + images dir (M4)."""
    from .. import critic

    limit = opts.get("limit")
    return critic.train_from_csv(
        opts["labels_csv"], opts["images_dir"], opts["out_model"],
        path_column=opts.get("path_column"), score_column=opts.get("score_column"),
        ridge=float(opts.get("ridge", 1e-3)), limit=int(limit) if limit else None)


def aesthetic_score(opts: dict) -> dict:
    """Score + rank a folder of photos with a trained critic model (M4)."""
    from .. import critic

    limit = opts.get("limit")
    return critic.score_folder(opts["folder"], opts["model"],
                               recursive=bool(opts.get("recursive", True)),
                               limit=int(limit) if limit else None)


def make_variants(image_path: str, opts: dict, configs_dir: str | None = None,
                  with_images: bool = True) -> dict:
    """Render several recipe variants for one photo, score each (safety gate + optional aesthetic
    critic), rank, auto-pick the best, and flag uncertain cases for review (M7). Non-generative —
    every variant is an ordinary bounded edit."""
    from .. import variants

    check_image(image_path)
    specs = variants.resolve_specs(opts.get("variants"))
    model = None
    if opts.get("aesthetic_model"):
        from .. import aesthetic

        try:
            model = aesthetic.load_model(opts["aesthetic_model"])
        except Exception:
            model = None

    work = downscale_long_edge(load_image(image_path), 1400)   # shared proxy → fast, seg cached
    scored = []
    for spec in specs:
        o = {k: v for k, v in opts.items() if k not in ("variants", "aesthetic_model")}
        o.update(variants.spec_opts(spec))
        cfg = make_cfg(o, configs_dir)
        pipe = build_pipeline(cfg)
        with INFERENCE_LOCK:
            out_rgb, result = pipe.process_one(image_path, do_apply=True, image=work)
        item = {"name": spec["name"], "opts": variants.spec_opts(spec), "scene": result.scene,
                **variants.score_variant(result, out_rgb, model)}
        if with_images:
            item["after"] = _img_b64(out_rgb, 1100, fmt="jpeg", quality=88)
        scored.append(item)

    scored.sort(key=lambda d: d["score"], reverse=True)
    review = variants.is_uncertain(scored)
    return {"image": os.path.abspath(image_path), "best": scored[0]["name"] if scored else None,
            "review": review, "variants": scored}


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
