"""Small durable editing workspaces, independent of the desktop server's changing port.

Only editable state is stored, never credentials or pixels. A canonical folder identifies a
project; atomic replacement keeps the previous version intact if a save is interrupted.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import threading

from .. import paths

MAX_BYTES = 8 * 1024 * 1024
MAX_PHOTOS = 10_000
MAX_HISTORY = 50
_LOCK = threading.Lock()
_PHOTO = {"look", "intensity", "neutral", "sliders", "entries", "reference", "reference_strength", "hide", "hideMethod", "base",
          "region_settings", "mask_refinements", "photo_geometry", "local_only"}
_FOLDER = {"look", "genre", "strength", "runText", "exportPreset", "settings", "finder", "folderSliders"}
_STATUS = {"", "waiting", "editing", "finished", "review", "failed", "cancelled", "saved"}


def _object(value, keys, name):
    if not isinstance(value, dict) or set(value) - keys:
        raise ValueError(f"{name} must be an object with supported fields")


def _text(value, name, limit=2000):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"{name} must be text of at most {limit} characters")


def _number(value, name, lo, hi):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError(f"{name} must be a number from {lo} to {hi}")


def _photo(value, history=True):
    from .service import check_photo_settings, check_region_settings
    from .photo_base import check as check_base
    from ..segment.refine import check as check_refinements
    from ..geometry import check_manual
    _object(value, _PHOTO | ({"history", "future"} if history else set()), "photo")
    for key in ("look", "reference", "hideMethod"):
        if key in value:
            _text(value[key], key, 4096 if key == "reference" else 200)
    if "intensity" in value:
        _number(value["intensity"], "intensity", 0, 2)
    if "reference_strength" in value:
        _number(value["reference_strength"], "reference_strength", 0, 1)
    if "neutral" in value and value["neutral"] is not None:
        n = value["neutral"]
        if not isinstance(n, list) or len(n) != 2:
            raise ValueError("neutral must be a pair of coordinates")
        for x in n:
            _number(x, "neutral", 0, 1)
    if "sliders" in value:
        check_photo_settings(value["sliders"])
    if "base" in value:
        check_base(value["base"])
    if "region_settings" in value:
        check_region_settings(value["region_settings"])
    if "mask_refinements" in value:
        check_refinements(value["mask_refinements"])
    if "photo_geometry" in value:
        check_manual(value["photo_geometry"])
    if "local_only" in value and not isinstance(value["local_only"], bool):
        raise ValueError("local_only must be true or false")
    if "hide" in value and (not isinstance(value["hide"], list) or len(value["hide"]) > 100
                            or any(not isinstance(x, str) or len(x) > 100 for x in value["hide"])):
        raise ValueError("hide must be a list of names")
    if "entries" in value:
        entries = value["entries"]
        if not isinstance(entries, list) or len(entries) > 200:
            raise ValueError("entries must be a list of at most 200 instructions")
        for entry in entries:
            if isinstance(entry, str):
                _text(entry, "instruction")
            else:
                _object(entry, {"box", "kind", "edit"}, "mark")
                box = entry.get("box")
                if not isinstance(box, list) or len(box) != 4:
                    raise ValueError("mark box must have four coordinates")
                for x in box:
                    _number(x, "mark coordinate", 0, 1)
                if entry.get("kind") not in ("auto", "area"):
                    raise ValueError("mark kind must be auto or area")
                _text(entry.get("edit"), "mark edit", 300)
    for key in ("history", "future"):
        if key in value:
            if not isinstance(value[key], list) or len(value[key]) > MAX_HISTORY:
                raise ValueError(f"{key} must have at most {MAX_HISTORY} snapshots")
            for snapshot in value[key]:
                _photo(snapshot, history=False)


def check_project(folder, project):
    from .service import _ALLOWED, check_photo_settings
    from ..settings_schema import described
    _object(project, {"version", "folder", "photos", "files", "last_index"}, "project")
    if type(project.get("version")) is not int or project["version"] != 1:
        raise ValueError("unsupported project version")
    opts = project.get("folder", {})
    _object(opts, _FOLDER, "folder")
    for key in ("look", "genre", "runText", "exportPreset", "finder"):
        if key in opts:
            _text(opts[key], "folder." + key)
    if opts.get("strength") is not None:
        _number(opts["strength"], "folder.strength", 0, 2)
    if "folderSliders" in opts:
        check_photo_settings(opts["folderSliders"])
    # The whitelist intentionally excludes credentials, including future unknown settings.
    if "settings" in opts:
        allowed = set(_ALLOWED) | set(described().get("setting", {})) | {"consistent"}
        _object(opts["settings"], allowed - {"api_key"}, "settings")
        def safe(value):
            if isinstance(value, dict):
                raise ValueError("project settings cannot contain nested objects")
            if isinstance(value, list):
                if len(value) > 200:
                    raise ValueError("project setting list is too long")
                for item in value:
                    safe(item)
            elif value is not None and not isinstance(value, (str, bool, int, float)):
                raise ValueError("unsupported project setting")
            elif isinstance(value, str) and len(value) > 4096:
                raise ValueError("project setting text is too long")
        for value in opts["settings"].values():
            safe(value)
    root = os.path.realpath(folder)
    for key in ("photos", "files"):
        mapping = project.get(key, {})
        if not isinstance(mapping, dict) or len(mapping) > MAX_PHOTOS:
            raise ValueError(f"{key} must contain at most {MAX_PHOTOS} photos")
        for path, value in mapping.items():
            if not isinstance(path, str) or not os.path.isabs(path) or os.path.commonpath([root, os.path.realpath(path)]) != root:
                raise ValueError(f"{key} names a photo outside the project folder")
            if key == "photos":
                _photo(value)
            else:
                _object(value, {"checked", "status", "out", "dirty"}, "file")
                if "checked" in value and not isinstance(value["checked"], bool):
                    raise ValueError("checked must be true or false")
                if "dirty" in value and not isinstance(value["dirty"], bool):
                    raise ValueError("dirty must be true or false")
                if value.get("status", "") not in _STATUS:
                    raise ValueError("unsupported photo status")
                if value.get("out") is not None:
                    _text(value["out"], "output", 4096)
    if "last_index" in project and (type(project["last_index"]) is not int or not 0 <= project["last_index"] < MAX_PHOTOS):
        raise ValueError("last_index must be a photo index")
    try:
        encoded = json.dumps(project, allow_nan=False, ensure_ascii=False).encode("utf-8")
    except (ValueError, TypeError):
        raise ValueError("project must contain finite JSON values") from None
    if len(encoded) > MAX_BYTES:
        raise ValueError("project is larger than 8 MB")
    return encoded


def _location(folder, profile):
    from .service import ensure_allowed
    ensure_allowed(folder)
    if not os.path.isdir(folder):
        raise ValueError("project folder does not exist")
    key = hashlib.sha256(os.path.normcase(os.path.realpath(folder)).encode()).hexdigest()
    base = profile or paths.profile_dir() or paths.default_profile_dir()
    return os.path.join(base, "projects", key + ".json")


def _photo_paths(folder, project, *, canonical):
    """Store a photo once by real path; present keys using the folder the client opened.

    Old project files used the original picker spelling, so resolve their keys as well. Only
    the two lookup maps change: reference choices and exported-file paths remain untouched.
    """
    root = os.path.realpath(folder)
    requested = os.path.abspath(folder)
    result = dict(project)
    for field in ("photos", "files"):
        if field not in project:
            continue
        mapped = {}
        for path, value in project[field].items():
            real = os.path.realpath(path)
            key = real if canonical else os.path.join(requested, os.path.relpath(real, root))
            if key in mapped:
                raise ValueError(f"{field} names the same photo through more than one path")
            mapped[key] = value
        result[field] = mapped
    return result


def load(folder, profile=None):
    file = _location(folder, profile)
    with _LOCK:
        if not os.path.exists(file):
            return None
        if os.path.getsize(file) > MAX_BYTES:
            raise ValueError("saved project is larger than 8 MB")
        with open(file, encoding="utf-8") as fh:
            data = json.load(fh)
    check_project(folder, data)
    return _photo_paths(folder, data, canonical=False)


def save(folder, project, profile=None):
    file = _location(folder, profile)
    check_project(folder, project)
    encoded = check_project(folder, _photo_paths(folder, project, canonical=True))
    with _LOCK:
        os.makedirs(os.path.dirname(file), exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".project-", dir=os.path.dirname(file))
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(encoded)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temporary, file)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
