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

from .. import batch, curate, emit, geometry, history, instructions, lessons, looks as looks_mod, paths, teach, variants
from ..concepts import load_concepts
from ..contract import Adjustment, RunConfig
from ..imageio import downscale_long_edge, is_raw, is_supported, load_image, raw_opts_from
from ..pipeline import Pipeline
from ..segment.base import parse_mark
from ..styles import load_styles

# UI-settable RunConfig fields (everything else stays at defaults / config-file values).
_ALLOWED = (
    "style", "prompt", "edit", "preset", "look", "intensity", "palette", "palette_strength",
    "reference", "reference_strength",
    "decider", "recipe_model", "allow_cloud", "api_key", "backend", "device", "out_format", "quality", "metadata",
    "emit", "consistency", "compose", "vignette", "sharpen", "levels", "denoise", "night_sky", "raw_mode",
    "highlight_hold", "light_shaping", "snap_edges", "out_long_edge", "out_name", "out_subdir",
    "translator", "grain", "grain_size", "lut", "lut_amount", "history", "straighten", "out_aspect",
    "neutral", "wb", "backdrop", "consistency_wb", "colour_report", "reveal_check",
)


def make_cfg(opts: dict, configs_dir: str | None = None, profile: str | None = None) -> RunConfig:
    kw: dict = paths.config_paths(configs_dir, profile)
    if opts.get("lut"):     # a file path from a request: inside a folder the user picked, like every photo
        ensure_allowed(str(opts["lut"]))
    if opts.get("genre"):   # a genre first (F30): the export preset and explicit options win over it
        from .. import genres as _genres
        kw.update(_genres.run_kwargs(str(opts["genre"]), kw, style_named=opts.get("style") is not None))
        kw["genre"] = str(opts["genre"])
    if opts.get("export"):
        from .. import exports as _exports   # preset first: explicit options win over it
        kw.update(_exports.preset_kwargs(opts["export"], kw.get("exports_path")
                                         or paths.config_path("exports.toml", packaged_fallback=True)))
        kw["export"] = opts["export"]
    kw["history"] = history.wanted(configs_dir, profile)
    for k in _ALLOWED:
        if opts.get(k) is not None:
            kw[k] = opts[k]
    kw["profile_dir"] = profile
    concepts = opts.get("concepts")
    if isinstance(concepts, str) and concepts.strip():
        kw["concepts"] = [c.strip() for c in concepts.split(",") if c.strip()]
    elif isinstance(concepts, list):
        kw["concepts"] = concepts
    cfg = RunConfig(**kw)
    if cfg.look == variants.SURPRISE and cfg.look not in looks_mod.load(cfg.looks_path):
        # F26: the word is not a look, unless the library has one of that name. It never reaches a sidecar.
        raise ValueError('"surprise" is not a look: POST /api/v1/variants with "surprise": true, '
                         'then send the name of the look that was picked')
    if cfg.backend == "auto":   # the service builds its own segmenter, so it needs the real name
        from .. import segment
        cfg = cfg.model_copy(update={"backend": segment.resolve_backend("auto")})
    return cfg


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
    """Raise PermissionError if any given path is outside every approved workspace root.

    `None` means "optional / unset, resolved & checked elsewhere" and is skipped. An empty
    string is NOT skipped — it's a concrete path that resolves to the server's CWD, so treating
    it as "unset" is the fail-open bug that let default outputs escape the boundary. Callers that
    legitimately have no path (a benign empty listing) must short-circuit before calling this."""
    for p in candidate_paths:
        if p is None:
            continue
        if not p or not path_allowed(p):
            raise PermissionError(
                "path is outside an approved folder — choose it with the folder/file picker "
                f"first (or set AUTOEDIT_ALLOW_ANY_PATH=1 to disable this check): {p!r}")


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


def list_styles(configs_dir: str | None = None, profile: str | None = None) -> list[dict]:
    styles = load_styles(paths.config_paths(configs_dir, profile)["styles_path"])
    return [{"name": s.name, "look": s.look, "brief": s.brief, "concepts": s.concepts}
            for s in styles.values()]


def list_presets(configs_dir: str | None = None, profile: str | None = None) -> dict[str, list[str]]:
    presets, _ = load_concepts(paths.config_paths(configs_dir, profile)["concepts_path"])
    return presets


def list_genres(configs_dir: str | None = None, profile: str | None = None) -> list[dict]:
    from .. import genres
    return genres.listing(configs_dir, profile)


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
    shoot isn't processed twice.

    Only a genuine RAW+pixel set collapses. Same-stem files that are all pixel formats
    (e.g. photo.jpg + photo.png) are DISTINCT exports, not a camera pair — collapsing them would
    silently drop one from editing — so they're listed separately."""
    by_stem: dict[str, list[dict]] = {}
    order: list[str] = []
    for f in list_images(folder):
        key = os.path.splitext(f)[0].lower()
        if key not in by_stem:
            by_stem[key] = []
            order.append(key)
        by_stem[key].append({"name": f, "kind": "raw" if is_raw(f) else "pixel"})

    def _entry(members: list[dict]) -> dict:
        raw = next((m["name"] for m in members if m["kind"] == "raw"), None)
        pixel = next((m["name"] for m in members if m["kind"] == "pixel"), None)
        return {"stem": os.path.splitext(members[0]["name"])[0], "members": members,
                "raw": raw, "pixel": pixel}

    out: list[dict] = []
    for key in order:
        members = by_stem[key]
        if len(members) == 1 or any(m["kind"] == "raw" for m in members):
            out.append(_entry(members))          # single file, or a real RAW(+pixel) pair
        else:
            out.extend(_entry([m]) for m in members)   # multiple pixels, no RAW → keep separate
    return out


def _img_b64(rgb01: np.ndarray, max_px: int = 1600, fmt: str = "jpeg", quality: int = 90,
             grain: tuple[float, float, int] | None = None, geo=None) -> str:
    """Encode a working RGB array as a base64 data-URL. JPEG at q90 keeps a 1600px preview small
    (~200–500 KB) so we can show it sharp; PNG stays available for lossless needs."""
    from ..imageio import _to_u8
    rgb = np.clip(rgb01.astype(np.float32), 0.0, 1.0)
    rgb = geometry.warp(rgb, geo)       # F32: the export's frame first, as `emit.write_image` does
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
    if grain:
        from ..edit import ops
        rgb = ops.grain(rgb, *grain)
    pil = PILImage.fromarray(_to_u8(rgb), "RGB")  # final-size dither → no preview sky banding
    buf = io.BytesIO()
    if fmt in ("jpg", "jpeg"):
        pil.save(buf, format="JPEG", quality=quality, subsampling=0)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    if fmt == "webp":
        # Lossless WebP: pixel-exact like PNG (so the anti-banding dither survives) but typically
        # 30–45% smaller — the before/after preview ships two of these per keystroke.
        pil.save(buf, format="WEBP", lossless=True, quality=100, method=4)
        return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()
    pil.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def thumb_bytes(path: str, px: int = 360, quality: int = 82) -> bytes:
    """A small JPEG of any image on disk (by path) — for the UI's real thumbnails/previews.
    Cheap: decode + downscale, no pipeline/segmentation. `px` is clamped so a hostile/errant
    caller can't turn this into a full-resolution image server (px=100000 would skip downscale)."""
    check_image(path)
    px = max(32, min(int(px), 2048))
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


def _in_frame(alpha: np.ndarray, geo) -> np.ndarray:
    """A uint8 overlay in the export's frame (F32); as it is for `geo is None`."""
    if geo is None:
        return alpha
    return (geometry.warp(alpha[..., None].astype(np.float32) / 255.0, geo)[..., 0] * 255.0 + 0.5).astype(np.uint8)


def _mask_previews(resolved, geo=None) -> list[dict]:
    """Lossless transparent proxy masks, after overlap arbitration, from this render's resolve.

    White RGB with an alpha channel lets the UI tint each named region in its legend color.
    These show the region footprints before full-resolution edge feathering.
    """
    overlays = []
    for region in resolved.regions:
        alpha = PILImage.fromarray(_in_frame(region.mask.astype(np.uint8) * 255, geo))
        mask = PILImage.new("RGBA", alpha.size, (255, 255, 255, 0))
        mask.putalpha(alpha)
        buf = io.BytesIO()
        mask.save(buf, format="PNG")
        overlays.append({"name": region.name, "coverage": region.coverage,
                         "alpha": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()})
    return overlays


MAX_PHOTO_EDIT = 2000        # characters of follow-up text for one photo (F19)


def photo_adjustments(pipe: Pipeline, text) -> tuple[list, list[str]]:
    """Parse one photo's follow-up text into photo-scope adjustments + the problem lines (F19)."""
    if text is None or text == "":
        return [], []
    if not isinstance(text, str):
        raise ValueError("photo_edit must be text")
    if len(text) > MAX_PHOTO_EDIT:
        raise ValueError(f"photo_edit is longer than {MAX_PHOTO_EDIT} characters")
    adjs, problems = instructions.parse(text, pipe.vocab(), pipe.known_concepts())
    # A follow-up never goes to the cloud (F19 keeps it on this machine); the local model may read it.
    adjs, problems = instructions.translate_problems(
        problems, pipe.vocab(), pipe.known_concepts(), cfg=pipe.cfg, allow_cloud=False, adjustments=adjs)
    return [a.model_copy(update={"scope": "photo"}) for a in adjs], problems


MAX_PHOTO_MARKS = 20         # marks on one photo (F37)
PREVIEW_LONG_EDGE = 4096     # a preview applies on a copy of at most this size; the export applies at full size
_RANK = ["not_found", "blocked", "clamped", "applied"]


def check_marks(marks, vocab) -> list[dict]:
    """One photo's marks, checked one by one (F37). None is no marks."""
    if marks is None:
        return []
    if not isinstance(marks, list):
        raise ValueError("photo_marks must be a list of marks")
    if len(marks) > MAX_PHOTO_MARKS:
        raise ValueError(f"a photo takes at most {MAX_PHOTO_MARKS} marks")
    return [instructions.check_mark(m, vocab) for m in marks]


def mark_limits(configs_dir: str | None = None, profile: str | None = None) -> dict:
    """What `check_marks` and `check_mark` enforce, for the page to repeat (F37)."""
    vocab = instructions.load_vocab(make_cfg({}, configs_dir, profile).instructions_path)
    return {"min_side": vocab.marks["min_side"], "max_chars": instructions.MAX_MARK_EDIT,
            "max_marks": MAX_PHOTO_MARKS}


def mark_adjustments(pipe: Pipeline, marks: list[dict]) -> tuple[list, list[dict]]:
    """Checked marks -> photo-scope adjustments + one fact per mark: {index, targets, problems}.
    Without SAM 3 every mark is an area. A mark's sentence never goes to the cloud."""
    vocab = pipe.vocab()
    adjs, facts = [], []
    for i, m in enumerate(marks):
        kind = m["kind"] if getattr(pipe.segmenter, "open_vocabulary", False) else "area"
        mark = (m["box"], kind)
        got, problems = instructions.parse(m["edit"], vocab, (), mark=mark)
        got, problems = instructions.translate_problems(
            problems, vocab, (), cfg=pipe.cfg, allow_cloud=False, adjustments=got)
        got = [instructions.to_mark(a, mark, vocab).model_copy(update={"scope": "photo"}) for a in got]
        adjs += got
        facts.append({"index": i, "targets": list(dict.fromkeys(a.target for a in got)), "problems": problems})
    return adjs, facts


def _png_alpha(alpha: np.ndarray) -> str:
    img = PILImage.new("RGBA", (alpha.shape[1], alpha.shape[0]), (255, 255, 255, 0))
    img.putalpha(PILImage.fromarray(alpha))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _mark_answer(facts, seen, shape, vocab, geo=None) -> tuple[list[dict], list[dict]]:
    """The preview's `marks` and `mark_overlays` from the mark facts and what the render reported."""
    if not facts:
        return [], []
    adjs, plan, _before, _after = seen if seen else ([], None, None, None)
    cells = {c.name: c for c in plan.cells} if plan else {}
    marks, overlays = [], []
    for f in facts:
        things = [t for t in f["targets"] if (parse_mark(t) or ("",))[0] == "thing"]
        found = bool(plan) and any(plan.hit.get(t) for t in f["targets"])
        why = ""
        if plan and not found:
            why = next((plan.why[t] for t in things if plan.why.get(t)), "")
        det = next((d for t in things for d in plan.detected if d.name == t), None) if plan else None
        got = [a.status for a in adjs if a.target in f["targets"] and a.status in _RANK]
        marks.append({
            "index": f["index"], "kind": "thing" if things else "area" if f["targets"] else "",
            "word": parse_mark(things[0])[2] if things else "", "targets": f["targets"],
            "found": found, "why": why,
            "score": float(det.score) if det else None, "coverage": float(det.coverage) if det else None,
            "status": min(got, key=_RANK.index) if got else "", "problems": f["problems"]})
        for t in f["targets"]:
            if t in things:
                hit = [cells[c].mask for c in plan.hit.get(t, []) if c in cells]
                if not hit:
                    continue
                alpha = np.logical_or.reduce(hit).astype(np.uint8) * 255
            else:
                alpha = (np.clip(instructions.place_alpha(instructions.layer_spec(t, vocab), shape), 0, 1)
                         * 255 + 0.5).astype(np.uint8)
            overlays.append({"target": t, "alpha": _png_alpha(_in_frame(alpha, geo))})
    return marks, overlays


def translator_status(configs_dir: str | None = None, profile: str | None = None) -> dict:
    """Whether the local instruction reader can run now: {available, model} (F20)."""
    from .. import translate_local
    cfg = make_cfg({}, configs_dir, profile)
    return {"available": cfg.translator != "off" and translate_local.available(cfg), "model": cfg.local_model}


_PENDING = "\0pending"      # a step picture that waits for the export's frame (F32)
TRY_DROPS = ("edit", "prompt", "reference", "lut", "neutral", "wb", "backdrop")            # run options that edit: a try starts without them
TRY_FIXED = {"decider": "rules", "allow_cloud": False, "compose": False, "palette": "none", "grain": 0.0}
NOT_THE_ENGINE = ("try", "steps", "photo_edit", "photo_marks")   # request keys that are the user's own


def _edit(image_path: str, opts: dict, configs_dir: str | None, profile: str | None, trial=None, steps=None,
          geo_box: list | None = None) -> dict:
    """One preview render: what `preview` answers from. `trial`: a checked `try` (F29), edited by nothing but
    its sliders and sentences. `steps`: a `teach.Steps` that listens to it (F29). `geo_box` (F32): a list that gets the
    export's geometry as soon as the render has decided it, so the step pictures can be turned before it ends."""
    cfg = make_cfg({**{k: v for k, v in opts.items() if k not in TRY_DROPS}, **TRY_FIXED} if trial is not None else opts,
                   configs_dir, profile)
    pipe = build_pipeline(cfg)
    if trial is not None:
        pipe.decider, pipe.looks, pipe.habits = teach.NoEdit(), {}, {}
    photo_adjs, photo_problems = photo_adjustments(pipe, opts.get("photo_edit"))
    vocab = pipe.vocab()
    mark_adjs, facts = mark_adjustments(pipe, check_marks(opts.get("photo_marks"), vocab))
    photo_adjs = photo_adjs + mark_adjs
    if trial and trial["sliders"]:      # a direct adjustment, as `autoedit adjust --set global.OP=VALUE` makes it
        photo_adjs.insert(0, Adjustment(target=instructions.GLOBAL, ops=dict(trial["sliders"]), scope="photo"))
    work = load_image(image_path, decode=raw_opts_from(cfg))
    proxy = None
    if work.long_edge > PREVIEW_LONG_EDGE:          # bound apply cost on very large files
        proxy = downscale_long_edge(work, cfg.analysis_long_edge)   # as the export analyses it
        work = downscale_long_edge(work, PREVIEW_LONG_EDGE)
    seen, walk, shape, union, regions = [], [], [(0, 0)], {}, []

    def _resolved(resolved):
        regions.append(resolved)        # drawn after the render: they follow the export's frame (F32)
        shape[0] = resolved.shape
        union.update(resolved.union)

    def _planned(*args):        # made here: later steps may still change the settings it reads
        seen.append(args)
        walk.extend(lessons.walk(*args, vocab, lessons.load(
            paths.config_path("lessons.toml", packaged_fallback=True))))
    with INFERENCE_LOCK:
        out_rgb, result = pipe.process_one(
            image_path, do_apply=True, image=work, proxy=proxy, adjustments=photo_adjs or None,
            on_resolved=_resolved, on_plan=_planned,
            on_stage=steps.stage if steps else None, on_step=steps.step if steps else None,
            on_geometry=(lambda g: geo_box.__setitem__(slice(None), [g])) if geo_box is not None else None)
    geo = result.geometry
    overlays = [o for resolved in regions for o in _mask_previews(resolved, geo)]
    marks, mark_overlays = _mark_answer(facts, seen[0] if seen else None, shape[0], vocab, geo)
    return {"cfg": cfg, "pipe": pipe, "work": work, "proxy": proxy, "out": out_rgb, "result": result,
            "union": union, "walk": walk, "overlays": overlays,
            "problems": [*pipe.adjust_problems, *photo_problems], "marks": marks, "mark_overlays": mark_overlays}


def preview(image_path: str, opts: dict, configs_dir: str | None = None, max_px: int = 2560,
            profile: str | None = None) -> dict:
    """Render a single-image before/after preview + the decision sidecar.

    The edit is applied at FULL resolution (bounded to a 4096px long edge for monster files), so the
    preview shows the real result — no proxy-scaling softness. Above that bound the analysis picture
    is still made from the original, as the export makes it, so both see the same masks, stats and
    decisions. The before/after ship as **lossless
    WebP** (not JPEG): JPEG's DCT quantizes away the anti-banding dither in flat areas like sky and
    re-introduces contour banding, so a quality-critical preview must stay lossless — WebP is
    pixel-exact like PNG but a much smaller payload. Capped at max_px (retina-sharp at the on-screen
    stage). Segmentation is cached, so re-previews are fast.

    `opts["steps"]` (F29): true, or {"px": 256 to 2560, "lossless": bool}, adds the answer's `steps`: the
    render again, one step at a time, with a lesson on each. It changes no pixel of the other keys.

    `opts["try"]` (F29): {"sliders": {op: value}, "compare": bool}. The photo is edited by those sliders and the
    typed sentences alone, and the answer's `before`, `after`, `result` describe that try; `try` gives the sliders,
    `compare` (on request) the engine's edit beside it. Only this function reads `try`: an export never does."""
    check_image(image_path)
    trial = table = les = None
    px, lossless = teach.check_steps(opts.get("steps"))     # first: {} is a valid steps value, and asks for steps
    if opts.get("try") is not None or px:
        les = lessons.load(paths.config_path("lessons.toml", packaged_fallback=True))
    if opts.get("try") is not None:
        table = teach.sliders(les)
        trial = teach.check_try(opts["try"], table)
    px = min(px, max_px)

    geo_box, held = [], []      # F32: the export's frame, once the render has decided it; the source picture, held for it

    def small(rgb, grain=None):
        return _img_b64(rgb, px, fmt="webp" if lossless else "jpeg", grain=grain, geo=geo_box[0] if geo_box else None)

    def encode(rgb):
        if not held:            # the first picture is shown before the frame is decided: it is encoded at the end
            held.append(rgb)
            return _PENDING
        return small(rgb)
    walk = teach.Steps(encode) if px else None
    r = _edit(image_path, opts, configs_dir, profile, trial, walk, geo_box)
    geo = r["result"].geometry
    grain = emit.grain_args(r["result"], r["cfg"])
    answer = {
        "before": _img_b64(r["work"].rgb, max_px, fmt="webp", geo=geo),
        "after": _img_b64(r["out"], max_px, fmt="webp", grain=grain, geo=geo),
        "mask_overlays": r["overlays"],
        "result": r["result"].model_dump(),
        "instruction_problems": r["problems"],
        "walkthrough": r["walk"],
        "mark_overlays": r["mark_overlays"],
        "marks": r["marks"],
        "reveal": r["result"].reveal.model_dump() if r["result"].reveal else None,     # F21: null = the check was off
    }
    if walk is not None:
        answer["steps"] = walk.done(r["result"], les, r["pipe"].guard_ops, r["walk"],
                                    small(r["out"], grain) if grain else None)
        if held:
            source = small(held[0])
            for step in answer["steps"]:
                if step["after"] == _PENDING:
                    step["after"] = source
    if trial is not None:
        answer["try"] = {"sliders": [{"op": op, "min": lo, "max": hi, "step": st,
                                      "value": trial["sliders"].get(op, 0.0), **lessons._lines(les, "op", op)}
                                     for op, (lo, hi, st) in table.items()]}
    if trial and trial["compare"]:
        e = _edit(image_path, {k: v for k, v in opts.items() if k not in NOT_THE_ENGINE}, configs_dir, profile)
        m = [teach.measure(x["work"].rgb, x["out"], x["proxy"] or downscale_long_edge(x["work"], x["cfg"].analysis_long_edge),
                           x["union"], x["pipe"]) for x in (r, e)]
        answer["compare"] = {"engine": _img_b64(e["out"], max_px, fmt="webp", grain=emit.grain_args(e["result"], e["cfg"]),
                                           geo=e["result"].geometry),
                             **teach.compare(r["result"], e["result"], m[0], m[1], les, trial["sliders"],
                                             r["pipe"].quality_gate)}
    return answer


def run_batch(input_dir: str, out_dir: str, opts: dict, configs_dir: str | None = None,
              profile: str | None = None) -> dict:
    cfg = make_cfg(opts, configs_dir, profile)
    return batch.run(cfg, input_dir, out_dir, do_apply=True)


def _curate_kwargs(input_dir: str, opts: dict) -> dict:
    """Validate + normalize cull options once, so the sync (`curate_folder`) and background
    (`curate_run`) entry points can't drift in defaults or validation. Copy-only is hard-wired."""
    if not input_dir or not os.path.isdir(input_dir):
        raise ValueError(f"input_dir not found: {input_dir}")
    count = int(opts["count"]) if opts.get("count") not in (None, "") else 200
    if count < 1:
        raise ValueError(f"count must be >= 1: {count}")
    raw_limit = str(opts.get("scan_limit") or "").strip()
    scan_limit = int(raw_limit) if raw_limit and raw_limit not in ("0", "None") else None
    mode = opts.get("mode") or "best"
    if mode not in ("best", "diverse", "aesthetic"):
        raise ValueError(f"unknown curation mode: {mode}")
    aesthetic_model = opts.get("aesthetic_model") or None
    if mode == "aesthetic" and not aesthetic_model:
        raise ValueError("aesthetic mode requires an aesthetic model")
    if aesthetic_model:
        check_file_exists(aesthetic_model, ".json", "aesthetic model")
    return dict(
        count=count,
        recursive=bool(opts.get("recursive", True)),
        proxy_long_edge=int(opts.get("analysis_long_edge") or 256),
        scan_limit=scan_limit,
        copy_originals=True,                              # roadmap: copy-only, always
        mode=mode,
        emit_xmp=bool(opts.get("emit_xmp", False)),
        aesthetic_model_path=aesthetic_model,
    )


def curate_run(input_dir: str, out_dir: str, opts: dict) -> dict:
    """Cull a folder into `out_dir/selected` + thumbs + contact-sheet (M1). Copy-only — originals
    are never moved/renamed/deleted. Modes: best | diverse | aesthetic. Returns the manifest.

    Background (curate_jobs) entry point; shares validation/defaults with `curate_folder`."""
    out_dir = out_dir or os.path.join(input_dir, "autoedit-curated")
    return curate.curate(input_dir, out_dir, **_curate_kwargs(input_dir, opts))


def save_review_labels(out_dir: str, records: list[dict]) -> dict:
    """Append human review labels (M2) under out_dir as review-labels.csv/json. Annotation only —
    no files moved/deleted."""
    from .. import review

    return review.append_labels(records, out_dir)


def load_review_labels(out_dir: str) -> dict:
    """Load persisted review-label state for a review folder."""
    from .. import review

    return review.summary(out_dir)


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
                  with_images: bool = True, profile: str | None = None) -> dict:
    """Render several recipe variants for one photo, score each (safety gate + optional aesthetic
    critic), rank, auto-pick the best, and flag uncertain cases for review (M7). Non-generative —
    every variant is an ordinary bounded edit."""
    check_image(image_path)
    seed = picks = None
    if opts.get("surprise"):   # F26: the specs are looks of the library, picked by the seed
        if opts.get("variants"):
            raise ValueError("give surprise or variants, not both")
        seed = variants.surprise_seed(opts.get("seed"))
        picks = variants.surprise_for(make_cfg(opts, configs_dir, profile), seed)
        if not picks:
            raise ValueError("surprise: the look library holds no other look")
        specs = [{"name": n, "look": n} for n in picks]
    else:
        specs = variants.resolve_specs(opts.get("variants"))
    model = None
    if opts.get("aesthetic_model"):
        from .. import aesthetic

        try:
            model = aesthetic.load_model(opts["aesthetic_model"])
        except Exception:
            model = None

    work = None
    decode_opts = None
    scored = []
    for spec in specs:
        o = {k: v for k, v in opts.items() if k not in ("variants", "aesthetic_model")}
        o.update(variants.spec_opts(spec))
        cfg = make_cfg(o, configs_dir, profile)
        raw_opts = raw_opts_from(cfg)
        if work is None or raw_opts != decode_opts:
            work = downscale_long_edge(load_image(image_path, decode=raw_opts), 1400)
            decode_opts = raw_opts
        pipe = build_pipeline(cfg)
        with INFERENCE_LOCK:
            out_rgb, result = pipe.process_one(image_path, do_apply=True, image=work)
        item = {"name": spec["name"], "opts": variants.spec_opts(spec), "scene": result.scene,
                **variants.score_variant(result, out_rgb, model)}
        if with_images:
            item["after"] = _img_b64(out_rgb, 1100, fmt="jpeg", quality=88, grain=emit.grain_args(result, cfg),
                                     geo=result.geometry)
        scored.append(item)

    scored.sort(key=lambda d: d["score"], reverse=True)
    review = variants.is_uncertain(scored)
    out = {"image": os.path.abspath(image_path), "best": scored[0]["name"] if scored else None,
           "review": review, "variants": scored}
    if picks is not None:
        out.update(seed=seed, looks=picks)
    return out


def run_one(image_path: str, out_dir: str, opts: dict, configs_dir: str | None = None,
            profile: str | None = None) -> dict:
    """Edit a single photo to out_dir (baked image + sidecar)."""
    check_image(image_path)
    cfg = make_cfg(opts, configs_dir, profile)
    pipe = build_pipeline(cfg)
    pipe.use_mask_cache(out_dir)
    photo_adjs, _ = photo_adjustments(pipe, opts.get("photo_edit"))
    photo_adjs += mark_adjustments(pipe, check_marks(opts.get("photo_marks"), pipe.vocab()))[0]
    with INFERENCE_LOCK:
        out_rgb, result = pipe.process_one(image_path, do_apply=True, adjustments=photo_adjs or None)
    os.makedirs(out_dir, exist_ok=True)
    image_out, sidecar = emit.output_paths(out_dir, image_path, cfg.out_format, name=cfg.out_name,
                                           subdir=cfg.out_subdir, fields={"style": cfg.style, "n": 1},
                                           sources=emit.source_ids([image_path]))
    emit.write_outputs(out_rgb, result, image_out, sidecar, cfg, image_path)
    return {"image_out": image_out, "sidecar": sidecar, "result": result.model_dump()}


def curate_folder(input_dir: str, out_dir: str, opts: dict) -> dict:
    """Copy a curated subset of a folder for review/training, returning UI-friendly output paths.

    Deliberately copy-only; sources are never moved or renamed. Sync entry point; shares
    validation/defaults with `curate_run` via `_curate_kwargs`.
    """
    out_dir = out_dir or os.path.join(input_dir, "autoedit-curated")
    manifest = curate.curate(input_dir, out_dir, **_curate_kwargs(input_dir, opts))
    return {
        "manifest": manifest,
        "out_dir": os.path.abspath(out_dir),
        "selected_dir": os.path.abspath(os.path.join(out_dir, "selected")),
        "contact_sheet": os.path.abspath(os.path.join(out_dir, "contact-sheet.html")),
        "selected_csv": os.path.abspath(os.path.join(out_dir, "selected.csv")),
        "candidates_json": os.path.abspath(os.path.join(out_dir, "candidates.json")),
    }


def check_file_exists(path: str, suffix: str, label: str) -> None:
    if not path or not os.path.isfile(path):
        raise ValueError(f"{label} not found: {path}")
    if suffix and not path.lower().endswith(suffix.lower()):
        raise ValueError(f"{label} must be a {suffix} file: {path}")


def pick_path(kind: str = "folder") -> str | None:
    """Open a native picker and return the chosen path, registering it as an approved workspace
    root (so subsequent API calls for paths under it pass the filesystem trust boundary)."""
    path = _pick_native(kind)
    if path:
        approve_root(path)
    return path


def _pick_native(kind: str = "folder") -> str | None:
    """Open a NATIVE picker on the machine running the server (the user's own Mac/PC) and
    return the chosen absolute path. kind: 'folder' | 'file' | 'json'. Returns None on cancel.

    macOS uses Finder via osascript; other platforms fall back to a tkinter dialog. This is
    why AutoEdit's UI is a *local* app — the server can read local paths and pop a real dialog,
    which a sandboxed browser cannot do on its own."""
    if sys.platform == "darwin":
        if kind == "folder":
            verb = 'choose folder with prompt "Select a folder for AutoEdit"'
        elif kind == "json":
            verb = 'choose file with prompt "Select an AutoEdit aesthetic model JSON"'
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
        elif kind == "json":
            path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("All", "*.*")])
        else:
            path = filedialog.askopenfilename(
                filetypes=[("Images", "*.jpg *.jpeg *.png *.tif *.tiff *.webp *.bmp"), ("All", "*.*")])
        root.destroy()
        return path or None
    except Exception:
        return None
