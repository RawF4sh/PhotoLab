"""Checked, versioned LightCraft settings stored alongside the AutoEdit project."""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import uuid

from . import projects, service
from .lightcraft import VERSION

MAX_BYTES = 2 * 1024**2
_FIELDS = set("""version process profile treatment wb light hdr curve color mixer point_colors bw_mix
grading effects vignette grain detail optics geometry crop orientation masks spots red_eye
lens_blur enhance calibration disabled_sections""".split())
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_TICKETS = OrderedDict()
_LOCK = threading.RLock()


def check_pointer(pointer):
    if not isinstance(pointer, dict) or set(pointer) != {"sha256", "version"}:
        raise ValueError("Invalid manual edit pointer.")
    if not isinstance(pointer["sha256"], str) or not _HASH.fullmatch(pointer["sha256"]):
        raise ValueError("Invalid manual edit checksum.")
    if not isinstance(pointer["version"], str) or not re.fullmatch(r"\d+\.\d+\.\d+", pointer["version"]):
        raise ValueError("Invalid manual editor version.")


def _check(value, depth=0):
    if depth > 24:
        raise ValueError("Manual settings nested too deeply.")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 80 or re.search("password|api.?key|credential|token|secret", key, re.I):
                raise ValueError("Unexpected manual settings key.")
            _check(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _check(item, depth + 1)
    elif isinstance(value, str):
        if len(value) > MAX_BYTES or "\x00" in value:
            raise ValueError("Invalid manual settings text.")
    elif value is not None and (not isinstance(value, (int, float)) or abs(value) > 1e100 or not math.isfinite(value)):
        raise ValueError("Invalid manual settings value.")


def _changed(before, after, prefix=""):
    if isinstance(after, dict) and isinstance(before, dict):
        for key in sorted(before.keys() | after.keys()):
            yield from _changed(before.get(key), after.get(key), f"{prefix}.{key}" if prefix else key)
    elif before != after:
        yield prefix


def _folder(path):
    service.ensure_allowed(path)
    service.check_image(path)
    return os.path.dirname(os.path.abspath(path))


def _file(folder, path, profile, checksum):
    project = Path(projects._location(folder, profile))
    key = hashlib.sha256(os.path.realpath(path).encode()).hexdigest()
    return project.with_suffix(".manual") / key / (checksum + ".json")


def _read(folder, path, pointer, profile):
    check_pointer(pointer)
    file = _file(folder, path, profile, pointer["sha256"])
    if not file.is_file() or file.stat().st_size > MAX_BYTES:
        raise ValueError("Saved manual edit missing or too large.")
    data = file.read_bytes()
    if hashlib.sha256(data).hexdigest() != pointer["sha256"]:
        raise ValueError("The saved manual edit failed its checksum.")
    return {"manual": pointer, **json.loads(data)}


def _baseline(photo, path, folder, profile):
    """The original manual settings of the newest readable snapshot; unreadable ones are skipped."""
    for snapshot in reversed([*photo.get("future", []), *photo.get("history", [])]):
        if snapshot.get("manual"):
            try:
                return {"baseline": _read(folder, path, snapshot["manual"], profile).get("baseline")}
            except ValueError:
                continue
    return {}


def load(path, profile=None):
    folder = _folder(path)
    with projects.transaction(folder, profile):
        project = projects.load(folder, profile) or {}
        photo = project.get("photos", {}).get(os.path.abspath(path), {})
        lost = None
        if photo.get("manual"):
            try:
                return _read(folder, path, photo["manual"], profile)
            except ValueError as error:     # a missing or damaged file must not leave the photo stuck
                lost = str(error)
                photo.pop("manual")
                projects.save(folder, project, profile, manual_update=True)
        # Undo can return to the untouched manual stage while LightCraft still caches an edit.
        return {"manual": None, **({"lost": lost} if lost else {}), **_baseline(photo, path, folder, profile)}


def save(path, settings, base, expected, profile=None, *, baseline=None):
    folder = _folder(path)
    path = os.path.abspath(path)
    if settings is not None and (not isinstance(settings, dict) or not settings or settings.keys() - _FIELDS):
        raise ValueError("Supply LightCraft develop settings.")
    if settings and any(not isinstance(settings.get(k, []), list) for k in ("spots", "masks")):
        raise ValueError("Manual masks and spots must be lists.")
    if not isinstance(base, str) or len(base) > 256:
        raise ValueError("Invalid working picture identity.")
    base_controls = baseline if isinstance(baseline, dict) else {}
    _check(settings)
    if baseline is not None:
        if not isinstance(baseline, dict) or baseline.keys() - _FIELDS:
            raise ValueError("Invalid original manual settings.")
        _check(baseline)
    record = {"base": base, "settings": settings, "baseline": baseline,
              "changed": list(_changed(base_controls, settings or {}))}
    data = json.dumps(record, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
    if len(data) > MAX_BYTES:
        raise ValueError("Manual edit exceeds the per-photo size limit.")
    sha = hashlib.sha256(data).hexdigest()
    with projects.transaction(folder, profile):
        project = projects.load(folder, profile) or {"version": 1}
        photo = project.setdefault("photos", {}).setdefault(path, {})
        old = photo.get("manual") or {}
        if old.get("sha256") == (sha if settings is not None else None):
            return {"manual": old or None}
        if old.get("sha256") != expected:
            raise ValueError("The manual edit changed in another window. Reopen before saving.")
        pointer = None
        if settings is not None:
            file = _file(folder, path, profile, sha)
            file.parent.mkdir(parents=True, exist_ok=True)
            if not file.exists():
                with tempfile.NamedTemporaryFile(dir=file.parent, delete=False) as out:
                    tmp = Path(out.name)
                    try:
                        out.write(data)
                        out.flush()
                        os.fsync(out.fileno())
                        out.close()
                        os.replace(tmp, file)
                    finally:
                        tmp.unlink(missing_ok=True)
            pointer = {"sha256": sha, "version": VERSION}
        snapshot = {k: v for k, v in photo.items() if k not in ("history", "future")}
        last = (photo.get("history") or [None])[-1]
        # A run of manual saves (the page saves about once a second) is one undo step, so it cannot
        # push the AI stage out of the history. A reset (no settings) is its own step.
        if not (pointer and old and last is not None
                and {k: v for k, v in last.items() if k != "manual"} == {k: v for k, v in snapshot.items() if k != "manual"}):
            photo["history"] = (photo.get("history", []) + [snapshot])[-projects.MAX_HISTORY:]
        photo["future"] = []
        if pointer:
            photo["manual"] = pointer
        else:
            photo.pop("manual", None)
        projects.save(folder, project, profile, manual_update=True)
        folder_on_disk = _file(folder, path, profile, sha).parent
        live = {p["manual"]["sha256"] for p in (photo, *photo["history"]) if p.get("manual")}
        for blob in folder_on_disk.glob("*.json"):
            if _HASH.fullmatch(blob.stem) and blob.stem not in live:
                blob.unlink(missing_ok=True)
        return {"manual": pointer}


def issue(path, options, signature, result, shape, profile):
    """A short-lived full-size render receipt, bound to its approved source and AI stage."""
    ticket = uuid.uuid4().hex
    now = time.monotonic()
    safe = {k: options[k] for k in (*service._ALLOWED, "genre", "export") if k in options
            and k not in ("api_key", "allow_cloud", "decider")}
    manual = (load(path, profile)["manual"] or {}).get("sha256", "")
    revision = {k: options[k] for k in ("preview_session", "preview_revision") if k in options}
    with _LOCK:
        while _TICKETS and (len(_TICKETS) >= 16 or next(iter(_TICKETS.values()))[0] < now - 1800):
            _TICKETS.popitem(last=False)
        _TICKETS[ticket] = (now, os.path.realpath(path), signature, safe, result, tuple(shape), manual, revision)
    return ticket


def _receipt(path, ticket):
    entry = _TICKETS.get(ticket)
    if not entry or entry[0] < time.monotonic() - 1800 or entry[1] != os.path.realpath(path):
        raise ValueError("Prepare a full-size export for this photo first.")
    return entry


def upload_limit(path, ticket):
    """Bound a native TIFF by the authorized full-resolution frame, including encoding overhead."""
    from . import editor_files
    with _LOCK:
        height, width = _receipt(path, ticket)[5][:2]
    rgb16_bytes = int(height) * int(width) * 6
    return min(editor_files.CACHE_BYTES, rgb16_bytes * 2 + 4 * 1024**2)


def export(path, ticket, checksum, rendered, out_dir, configs_dir, profile):
    from .. import emit, imageio, lut
    from ..edit import ops
    from . import editor_files, jobs

    service.ensure_allowed(path, out_dir)
    with _LOCK:
        _, _, signature, options, result, shape, manual, revision = _receipt(path, ticket)
        service._preview_request(revision)
        if editor_files.identity(path) != signature:
            raise ValueError("The source changed after rendering.")
        stage = load(path, profile)
        if manual != checksum or (stage["manual"] or {}).get("sha256", "") != checksum:
            raise ValueError("The manual edit changed. Render the current edit again.")
        import tifffile
        with tifffile.TiffFile(rendered) as tif:
            page = tif.pages[0]
            if len(tif.pages) != 1 or len(page.shape) != 3 or page.shape[2] != 3 or page.dtype.name != "uint16":
                raise ValueError("Manual export requires a full-size 16-bit RGB TIFF.")
            crop = (stage.get("settings") or {}).get("crop") or {}
            geometry = crop.get("geometry") or {}
            if result.geometry and crop == (stage.get("baseline") or {}).get("crop"):
                geometry = editor_files.native_crop(result.geometry, shape)
            rect = geometry.get("rect") or {"x0": 0, "x1": 1, "y0": 0, "y1": 1}
            w = float(rect["x1"]) - float(rect["x0"])
            h = float(rect["y1"]) - float(rect["y0"])
            # A 90-degree orientation exchanges the axes before the native crop. Allow a
            # pixel for the two renderers' endpoint rounding, never a preview-size export.
            dims = page.shape[:2]
            expected = [(max(1, round(h * y)), max(1, round(w * x))) for y, x in (shape[:2], shape[1::-1])]
            if not any(abs(dims[0]-y) <= 1 and abs(dims[1]-x) <= 1 for y, x in expected):
                raise ValueError("Manual render must use the full source dimensions and saved crop.")
        cfg = service.make_cfg({**options, "backend": "stub", "local_only": True}, configs_dir, profile)
        rgb = imageio.load_image(str(rendered)).rgb
        result = result.model_copy(deep=True)
        result.geometry = None  # already rasterized by the manual renderer
        if cfg.lut and cfg.lut_amount:
            table = lut.read_cube(cfg.lut)
            rgb = ops.lut_map(rgb, table.shift, cfg.lut_amount, table.lo, table.hi)
            result.global_settings["lut"] = cfg.lut_amount
            result.audit["lut"] = {"name": table.name, "digest": table.digest}
        settings = stage.get("settings", {})
        spots = settings.get("spots", [])
        modes = {re.sub(r"[^a-z0-9 _-]", "", s["mode"].lower())[:40].strip() if isinstance(s, dict) and isinstance(s.get("mode"), str) else ""
                 for s in spots}
        if stage["manual"]:
            result.audit["manual"] = {"engine": "LightCraft", "version": stage["manual"]["version"],
                                  "controls": stage["changed"], "masks": len(settings.get("masks", [])),
                                  "spots": len(spots), "spot_tools": sorted(m or "spot" for m in modes),
                                  "safety_checked": False}
            result.notes.append("Manual changes were applied after the local AI-stage checks.")
        image, sidecar = emit.single_output_paths(out_dir, path, cfg.out_format, name=cfg.out_name, subdir=cfg.out_subdir,
                                          fields={"style": cfg.style, "n": 1}, sources=emit.source_ids([path]))
        emit.write_outputs(rgb, result, image, sidecar, cfg, path)
        jobs._note_input_dir(out_dir, [path])
        del _TICKETS[ticket]
        return {"image_out": image, "sidecar": sidecar}
