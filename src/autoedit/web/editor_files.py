"""Local, bounded working pictures for the browser editor."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
from urllib.parse import urlencode

from .. import imageio
from . import service

CACHE_BYTES = 4 * 1024**3
UPLOAD_BYTES = 512 * 1024**2
PREVIEW_EDGE = 2560
_LOCK = threading.RLock()
_DIRECT = {".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


def root():
    path = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "autoedit" / "working-pictures-v1"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_export_path(path):
    """Keep a dropped file's finished exports outside the evictable working cache."""
    service.ensure_allowed(path)
    source = Path(path).resolve()
    if re.fullmatch(r"[0-9a-f]{64}", source.parent.name) and source.parent.parent == root().resolve() / "drops":
        output = Path.home() / "Pictures" / "AutoEdit Exports" / (source.stem + "-" + source.parent.name[:12])
        output.mkdir(parents=True, exist_ok=True)
        service.approve_root(str(output))
        return str(output)
    return str(source.parent / "AutoEdit Exports")


def identity(path):
    stat = os.stat(path)
    return [os.path.realpath(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def prune(keep):
    """Only prune files created by the cache, never an export or an open staging file."""
    folder = root()
    files = list(folder.glob("*.tif")) + [p for p in folder.glob("drops/*/*") if p.is_file()]
    sizes = [(p.stat(), p) for p in files if p.exists()]
    total = sum(s.st_size for s, _ in sizes)
    for s, path in sorted(sizes, key=lambda pair: pair[0].st_mtime_ns):
        if total <= CACHE_BYTES:
            break
        if path in keep:
            continue
        path.unlink(missing_ok=True)
        total -= s.st_size
        if path.parent.parent.name == "drops":
            try:
                path.parent.rmdir()
            except OSError:
                pass
    if total > CACHE_BYTES:
        raise ValueError("This photo exceeds the editor working storage limit.")


def write_tiff(path, rgb):
    try:
        import tifffile  # noqa: F401 — refuse the optional eight-bit writer fallback
    except ImportError:
        raise ValueError("Install the AutoEdit app extra to open 16-bit working pictures.") from None
    imageio.save_image(rgb, str(path), fmt="tiff", icc_profile=imageio.srgb_profile())


def cached_picture(path, key, build):
    dest = root() / (key + ".tif")
    with _LOCK:
        if not dest.is_file():
            with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
                output = Path(tmp) / "working.tif"
                write_tiff(output, build())
                if output.stat().st_size > CACHE_BYTES:
                    raise ValueError("This photo exceeds the editor working storage limit.")
                output.replace(dest)
        dest.touch()
        prune({Path(path), dest})
    return {"path": str(path), "name": Path(path).name + ".tif", "id": key,
            "url": "/api/v1/editor/pictures/" + key, "working_copy": True}


def open_picture(key):
    if not re.fullmatch(r"[0-9a-f]{64}", key):
        raise ValueError("Invalid working picture identifier.")
    with _LOCK:
        path = root() / (key + ".tif")
        stream = path.open("rb")
        path.touch()
        return stream, os.fstat(stream.fileno()).st_size


def _direct_source(path):
    # Some cameras put a RAW sensor frame behind a small RGB preview in a .tif container.
    return Path(path).suffix.lower() in _DIRECT and not imageio.is_raw(path)


def source_preview(path, options, configs_dir=None, profile=None):
    service.ensure_allowed(path)
    service.check_image(path)
    # Match the before frame used by Run AI Edit, including the active RAW development.
    cfg = service.make_cfg({**options, "backend": "stub", "local_only": True}, configs_dir, profile)
    source, _ = service._preview_images(path, cfg, 1600)
    return {"before": service._img_b64(source.rgb, 1600, fmt="webp"),
            "source_bits": source.source_bits, "working_copy": not _direct_source(path)}


def _with_saved_hide(path, options, profile):
    """F22: this photo's own Hide (saved in the project) always reaches the picture made for the editor."""
    from . import projects
    try:
        photo = (projects.load(os.path.dirname(os.path.abspath(path)), profile) or {}).get("photos", {}).get(os.path.abspath(path)) or {}
    except (OSError, ValueError):
        return options
    saved = [k for k in photo.get("hide") or [] if isinstance(k, str)]
    method = photo.get("hideMethod") or None
    if not saved:
        return options      # a method alone hides nothing
    asked = options.get("hide")
    asked = re.split(r"[,\s]+", asked) if isinstance(asked, str) else list(asked or [])
    return {**options, "hide": list(dict.fromkeys(k for k in [*asked, *saved] if k)),
            **({"hide_method": method} if method and not options.get("hide_method") else {})}


def import_photo(path, options, configs_dir=None, profile=None, full_size=False):
    service.ensure_allowed(path)
    service.check_image(path)
    options = _with_saved_hide(path, options, profile)
    if options.get("hide"):
        return working_picture(path, options, configs_dir, profile, full_size)
    if _direct_source(path):
        return {"path": path, "name": Path(path).name, "url": "/api/v1/editor/original?" + urlencode({"path": path}),
                "working_copy": False, "id": digest(identity(path))}
    # Decoding does not require a region finder or a cloud decider.
    cfg = service.make_cfg({**options, "backend": "stub", "local_only": True}, configs_dir, profile)
    decode = imageio.raw_opts_from(cfg)
    lens_file = decode.get("lens_profiles")
    signature = identity(path)
    key = digest([signature, decode, identity(lens_file) if lens_file and os.path.isfile(lens_file) else None, full_size])

    def render():
        photo = imageio.load_image(path, decode)
        if identity(path) != signature:
            raise ValueError("The source changed while opening. Open it again.")
        return (photo if full_size else imageio.downscale_long_edge(photo, PREVIEW_EDGE)).rgb

    return cached_picture(path, key, render)


def store_drop(temp, name, sha):
    name = str(name)
    if not name or name in (".", "..") or any(c in name for c in "/\\\0") or len(name.encode()) > 240:
        raise ValueError("Supply a photo filename without a folder.")
    if not imageio.is_supported(name):
        raise ValueError("Choose a supported photo format.")
    directory = root() / "drops" / sha
    dest = directory / name
    with _LOCK:
        if Path(temp).stat().st_size > CACHE_BYTES:
            raise ValueError("This photo exceeds the editor working storage limit.")
        directory.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            Path(temp).replace(dest)
        prune({dest})
        service.approve_root(str(directory))
    return str(dest)


def native_crop(geo, shape):
    """Translate AutoEdit's source-centred frame into LightCraft's rotated-frame coordinates."""
    if geo is None:
        return None
    from ..geometry import size
    height, width = shape[:2]
    h, w = size(geo, (height, width))
    angle = -geo.angle
    a = math.radians(angle)
    x, y = (geo.cx - .5) * width, (geo.cy - .5) * height
    cx = .5 + (math.cos(a) * x - math.sin(a) * y) / width
    cy = .5 + (math.sin(a) * x + math.cos(a) * y) / height
    return {"angle": angle, "rect": {"x0": cx - w / (2 * width), "x1": cx + w / (2 * width),
                                    "y0": cy - h / (2 * height), "y1": cy + h / (2 * height)}}


def working_picture(path, options, configs_dir=None, profile=None, full_size=False):
    from . import edit_steps
    service.ensure_allowed(path)
    service.check_image(path)
    options = _with_saved_hide(path, options, profile)
    service._preview_request(options, register=True)
    if options.get("editor_original") is True and not options.get("hide"):
        from ..contract import Result
        from .manual_stage import issue
        signature = identity(path)
        cfg = service.make_cfg({**options, "backend": "stub", "local_only": True}, configs_dir, profile)
        photo = imageio.load_image(path, imageio.raw_opts_from(cfg))
        if not full_size:
            photo = imageio.downscale_long_edge(photo, PREVIEW_EDGE)
        answer = cached_picture(path, digest([signature, imageio.raw_opts_from(cfg), "neutral", full_size]), lambda: photo.rgb)
        result = Result(image=Path(path).name, scene="manual", confidence=0,
                        audit={"decode": photo.decode_info}, notes=["Local source development without an AI edit."])
        if cfg.grain:
            result.global_settings["grain"] = cfg.grain
        answer.update(result=result.model_dump(), source_overlays=[], walkthrough=[])
        if identity(path) != signature:
            raise ValueError("The source changed during development.")
        service._preview_request(options)
        if full_size:
            answer["ticket"] = issue(path, options, signature, result, photo.rgb.shape, profile)
        return answer
    opts, limits = edit_steps.fold(options, service.editor_sliders()[0])
    opts.update(local_only=True, decider="rules", allow_cloud=False, translator="off",
                preview_edge=PREVIEW_EDGE, preview_unframed=True, lut_amount=0)
    signature = identity(path)
    render = service._edit(path, opts, configs_dir, profile, native=full_size)
    service._preview_request(options)
    result = render["result"]
    result.notes.extend(edit_steps.notes(options.get("edit_steps", []), limits))
    rgb = render["out"]
    if not full_size and max(rgb.shape[:2]) > PREVIEW_EDGE:
        rgb = imageio.downscale_long_edge(imageio.Image(rgb), PREVIEW_EDGE).rgb
    # Include the actual pixels, so changed rules, masks or a replaced reference cannot serve a stale picture.
    key = hashlib.sha256(memoryview(rgb).cast("B")).hexdigest()
    if identity(path) != signature:
        raise ValueError("The source changed during this edit. Reopen it.")
    answer = cached_picture(path, digest([signature, rgb.shape, key]), lambda: rgb)
    service._preview_request(options)
    answer.update(result=result.model_dump(), native_crop=native_crop(result.geometry, rgb.shape),
                  source_overlays=render["source_overlays"],
                  walkthrough=render["walk"], editor={"clamped": limits, "totals": {
                      "global": opts.get("photo_settings") or {}, "regions": opts.get("region_settings") or {}}})
    if full_size:
        from . import manual_stage
        answer["ticket"] = manual_stage.issue(path, options, signature, result, rgb.shape, profile)
    return answer
