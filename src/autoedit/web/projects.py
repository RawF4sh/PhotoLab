"""Small durable editing workspaces, independent of the desktop server's changing port.

Only editable state is stored, never credentials or pixels. A canonical folder identifies a
project; atomic replacement keeps the previous version intact if a save is interrupted.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
import json
import math
import os
import tempfile
import threading

from .. import paths
from . import edit_steps

MAX_BYTES = 8 * 1024 * 1024
MAX_PHOTOS = 10_000
MAX_HISTORY = 50
_LOCK = threading.RLock()
_ACTIVE_FILES = set()
_PHOTO = {"look", "intensity", "neutral", "sliders", "entries", "reference", "reference_strength", "hide", "hideMethod", "base",
          "region_settings", "mask_refinements", "photo_geometry", "local_only", "edit_steps", "manual", "ai_enabled", "style"}
_FOLDER = {"look", "genre", "strength", "runText", "exportPreset", "settings", "finder", "folderSliders", "recipes"}
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


def _photo(value, table, history=True):
    from .photo_base import check as check_base
    from ..segment.refine import check as check_refinements
    from ..geometry import check_manual
    _object(value, _PHOTO | ({"history", "future"} if history else set()), "photo")
    if "ai_enabled" in value and type(value["ai_enabled"]) is not bool:
        raise ValueError("ai_enabled must be true or false")
    for key in ("style", "look", "reference", "hideMethod"):
        if key in value:
            _text(value[key], key, 4096 if key == "reference" else 200)
    if "intensity" in value:
        _number(value["intensity"], "intensity", 0, 2)
    if "reference_strength" in value:
        _number(value["reference_strength"], "reference_strength", 0, 1)
    if "manual" in value:
        from .manual_stage import check_pointer
        check_pointer(value["manual"])
    if "neutral" in value and value["neutral"] is not None:
        n = value["neutral"]
        if not isinstance(n, list) or len(n) != 2:
            raise ValueError("neutral must be a pair of coordinates")
        for x in n:
            _number(x, "neutral", 0, 1)
    if value.get("sliders") is not None:
        edit_steps.controls(value["sliders"], table)
    if "base" in value:
        check_base(value["base"])
    if value.get("region_settings") is not None:
        edit_steps.regions(value["region_settings"], table, legacy=True)
    if "edit_steps" in value:
        options = {"edit_steps": value["edit_steps"], "photo_settings": value.get("sliders"),
                   "region_settings": value.get("region_settings")}
        folded, _ = edit_steps.fold(options, table)
        from ..segment.refine import names
        base_regions = (value.get("base") or {}).get("selected_regions", [])
        names(list(dict.fromkeys([*base_regions, *(folded.get("region_settings") or {})])))
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
                _photo(snapshot, table, history=False)


def check_project(folder, project):
    from .service import _ALLOWED, check_photo_settings, editor_sliders
    from ..settings_schema import described
    _object(project, {"version", "folder", "photos", "files", "last_index"}, "project")
    if type(project.get("version")) is not int or project["version"] != 1:
        raise ValueError("unsupported project version")
    opts = project.get("folder", {})
    _object(opts, _FOLDER, "folder")
    for key in ("look", "genre", "runText", "exportPreset", "finder"):
        if key in opts:
            _text(opts[key], "folder." + key)
    if "recipes" in opts:
        from .edit_presets import check_order
        check_order(opts["recipes"])
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
    table, _ = editor_sliders()
    root = os.path.realpath(folder)
    for key in ("photos", "files"):
        mapping = project.get(key, {})
        if not isinstance(mapping, dict) or len(mapping) > MAX_PHOTOS:
            raise ValueError(f"{key} must contain at most {MAX_PHOTOS} photos")
        for path, value in mapping.items():
            if not isinstance(path, str) or not os.path.isabs(path) or os.path.commonpath([root, os.path.realpath(path)]) != root:
                raise ValueError(f"{key} names a photo outside the project folder")
            if key == "photos":
                _photo(value, table)
            else:
                _object(value, {"checked", "status", "out", "dirty", "recipeReview", "qualityReason", "objectChecks"}, "file")
                if "qualityReason" in value:
                    _text(value["qualityReason"], "qualityReason", 2048)
                if "objectChecks" in value:
                    from ..segment.refine import MAX_CONCEPTS, name
                    checks = value["objectChecks"]
                    if not isinstance(checks, dict) or len(checks) > MAX_CONCEPTS:
                        raise ValueError("Invalid number of checked objects")
                    for target, found in checks.items():
                        name(target)
                        if type(found) is not bool:
                            raise ValueError("A checked object must be present or absent")
                if "recipeReview" in value:
                    _text(value["recipeReview"], "recipeReview", 2048)
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
    return _project_file(folder, profile or paths.profile_dir() or paths.default_profile_dir())


def _project_file(folder, profile):
    key = hashlib.sha256(os.path.normcase(os.path.realpath(folder)).encode()).hexdigest()
    return os.path.join(profile, "projects", key + ".json")


@contextmanager
def transaction(folder, profile):
    """Serialize compare-and-save across the desktop and browser servers on one profile."""
    file = _location(folder, profile)
    with _LOCK:
        if file in _ACTIVE_FILES:
            yield
            return
        os.makedirs(os.path.dirname(file), exist_ok=True)
        with open(file + ".lock", "a+b") as lock:
            if os.name == "nt":
                import msvcrt
                if lock.tell() == 0:
                    lock.write(b"0")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX)
            _ACTIVE_FILES.add(file)
            try:
                yield
            finally:
                _ACTIVE_FILES.remove(file)
                # Closing the descriptor releases the advisory lock on both platforms.


def manual_sources(folder, profile=None):
    """Read only this folder's saved manual markers, including for command-line exports."""
    profile = profile or paths.profile_dir()
    if not profile:
        return set()
    file = _project_file(folder, profile)
    try:
        with open(file, "rb") as stream:
            data = stream.read(MAX_BYTES + 1)
    except (FileNotFoundError, NotADirectoryError):
        return set()
    if len(data) > MAX_BYTES:
        raise ValueError("Saved photo project exceeds its size limit.")
    return {os.path.realpath(p) for p, state in json.loads(data).get("photos", {}).items() if state.get("manual")}


def require_automatic_export(path, manual):
    if os.path.realpath(path) in manual:
        raise ValueError("Finish in Photo editor to include the saved manual adjustments.")


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


def save(folder, project, profile=None, *, manual_update=False, manual_expected=None):
    file = _location(folder, profile)
    check_project(folder, project)
    canonical = _photo_paths(folder, project, canonical=True)
    with transaction(folder, profile):
        # The manual-stage endpoint uses a checksum precondition. A concurrent autosave
        # from Guided (including an older client) must not roll back that pointer.
        if not manual_update:
            from . import manual_stage
            current = load(folder, profile)
            incoming = canonical.get("photos", {})
            existing = _photo_paths(folder, current, canonical=True).get("photos", {}) if current else {}
            if manual_expected is None:
                manual_expected = {}
            if not isinstance(manual_expected, dict) or len(manual_expected) > MAX_PHOTOS:
                raise ValueError("Invalid manual undo request.")
            expected = {os.path.realpath(p): value for p, value in manual_expected.items()}
            if expected.keys() - incoming.keys():
                raise ValueError("Manual undo must name an edited project photo.")
            for path in incoming.keys() | existing.keys():
                old = existing.get(path, {})
                pointer = old.get("manual")
                wanted = incoming.get(path, {}).get("manual")
                if path in expected:
                    if (pointer or {}).get("sha256") != expected[path] and wanted != pointer:
                        raise ValueError("The manual edit changed in another window. Reopen before undoing.")
                    if wanted:
                        manual_stage._read(folder, path, wanted, profile)
                    continue
                if pointer != wanted:
                    # A Guided window opened before a Fine-tune save still has its old undo stack.
                    incoming[path] = {**incoming.get(path, {}), "history": old.get("history", []),
                                      "future": old.get("future", [])}
                if pointer:
                    incoming[path] = {**incoming.get(path, {}), "manual": pointer}
                elif path in incoming and "manual" in incoming[path]:
                    incoming[path] = {k: v for k, v in incoming[path].items() if k != "manual"}
            if incoming:
                canonical["photos"] = incoming
        encoded = check_project(folder, canonical)
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
