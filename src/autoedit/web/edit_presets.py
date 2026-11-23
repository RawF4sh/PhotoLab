"""Portable, local creative presets, separate from concept and export presets.

Only a look, its intensity and whole-photo control offsets travel between photos.
Coordinates, image paths, instructions, keys and privacy choices never enter this file.
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
import threading
import time
import uuid

from .. import paths

_LOCK = threading.Lock()
_ID = re.compile(r"[a-f0-9]{32}")
_LOOK = re.compile(r"[A-Za-z0-9_-]{1,100}")
MAX_PRESETS = 100
MAX_BYTES = 512 * 1024


def check_settings(value):
    from .service import check_photo_settings
    if not isinstance(value, dict) or set(value) - {"look", "intensity", "sliders"}:
        raise ValueError("A preset contains only a look, intensity and whole-photo controls")
    result = {}
    if "look" in value:
        look = value["look"]
        if not isinstance(look, str) or (look and not _LOOK.fullmatch(look)):
            raise ValueError("Preset look must be a look name")
        result["look"] = look
    if "intensity" in value:
        number = value["intensity"]
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or not 0 <= number <= 2:
            raise ValueError("Preset intensity must be between 0 and 2")
        result["intensity"] = float(number)
    result["sliders"] = check_photo_settings(value.get("sliders", {}))
    return result


def _name(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 80 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Give the preset a name of 1 to 80 characters")
    return value.strip()


def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("Invalid preset identifier")
    return value


def _file(profile):
    base = profile or paths.profile_dir() or paths.default_profile_dir()
    return os.path.join(base, "edit-presets.json")


def _read(path):
    if not os.path.exists(path):
        return []
    if os.path.getsize(path) > MAX_BYTES:
        raise ValueError("Saved presets exceed the size limit")
    with open(path, encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict) or data.get("version") != 1 or set(data) != {"version", "presets"}:
        raise ValueError("Unsupported saved preset file")
    items = data["presets"]
    if not isinstance(items, list) or len(items) > MAX_PRESETS:
        raise ValueError("Invalid saved preset list")
    seen = set()
    checked = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {"id", "name", "settings", "updated_at"}:
            raise ValueError("Invalid saved preset")
        key = _id(item["id"])
        stamp = item["updated_at"]
        if key in seen or isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not math.isfinite(stamp) or stamp < 0:
            raise ValueError("Invalid saved preset identifier or timestamp")
        seen.add(key)
        checked.append({"id": key, "name": _name(item["name"]), "settings": check_settings(item["settings"]), "updated_at": stamp})
    return checked


def _write(path, items):
    encoded = json.dumps({"version": 1, "presets": items}, allow_nan=False, ensure_ascii=False, indent=2)
    if len(encoded.encode("utf-8")) > MAX_BYTES:
        raise ValueError("Saved presets exceed the size limit")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".edit-presets-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(encoded + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def listing(profile=None):
    with _LOCK:
        return sorted(_read(_file(profile)), key=lambda item: item["name"].casefold())


def save(name, settings, preset_id=None, profile=None):
    name, settings = _name(name), check_settings(settings)
    if preset_id is not None:
        _id(preset_id)
    with _LOCK:
        path = _file(profile)
        items = _read(path)
        previous = next((item for item in items if item["id"] == preset_id), None)
        if preset_id is not None and previous is None:
            raise ValueError("This preset no longer exists; save it as a new preset")
        if any(item["id"] != preset_id and item["name"].casefold() == name.casefold() for item in items):
            raise ValueError("A preset with this name exists; choose another name or update it")
        if previous is None and len(items) >= MAX_PRESETS:
            raise ValueError("You have 100 presets; remove one before saving another")
        result = {"id": preset_id or uuid.uuid4().hex, "name": name, "settings": settings, "updated_at": time.time()}
        items = [result if item["id"] == preset_id else item for item in items]
        if previous is None:
            items.append(result)
        _write(path, items)
        return result


def remove(preset_id, profile=None):
    _id(preset_id)
    with _LOCK:
        path = _file(profile)
        items = _read(path)
        kept = [item for item in items if item["id"] != preset_id]
        if len(kept) == len(items):
            return False
        _write(path, kept)
        return True
