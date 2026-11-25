"""Locate config assets so the tool works both from the repo and pip-installed.

Resolution order for the configs directory:
  1. $AUTOEDIT_CONFIGS_DIR (explicit override)
  2. ./configs in the current working directory (a user's local tuning copy)
  3. the configs bundled inside the installed package (the default)
"""

from __future__ import annotations

import atexit
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import threading
import tomllib


def _packaged() -> str:
    return os.path.join(os.path.dirname(__file__), "configs")


def configs_dir() -> str:
    env = os.environ.get("AUTOEDIT_CONFIGS_DIR")
    if env:
        return env
    if os.path.isdir("configs"):
        return "configs"
    return _packaged()


def config_path(name: str, packaged_fallback: bool = False) -> str:
    """The config file `name` in the active configs folder. With `packaged_fallback`, a folder
    that predates the file (a local tuning copy made before a feature added it) gets the
    packaged one; every other file keeps the path in the active folder, existing or not."""
    path = os.path.join(configs_dir(), name)
    if packaged_fallback and not os.path.exists(path):
        return os.path.join(_packaged(), name)
    return path


_CONFIG_FILES = ("rules", "looks", "concepts", "styles", "compose", "intents", "palettes", "instructions", "exports", "reveal", "genres", "targets", "auto")


_OPTIONAL = ("instructions", "exports", "reveal", "genres", "targets", "auto")


def _app_dir(create: bool) -> str | None:
    base = os.environ.get("APPDATA") if os.name == "nt" else (
        os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config"))
    default = os.path.join(base, "autoedit") if base else ""
    return default if default and (create or os.path.isdir(default)) else None


def profile_dir(explicit: str | None = None) -> str | None:
    """The user's profile folder, or None. `explicit` (--profile) must exist. Otherwise
    AUTOEDIT_PROFILE (an empty value switches the profile off), otherwise the default location
    when that folder exists."""
    if explicit:
        if not os.path.isdir(explicit):
            raise ValueError(f"profile folder not found: {explicit}")
        return explicit
    env = os.environ.get("AUTOEDIT_PROFILE")
    if env is not None:
        return env if env and os.path.isdir(env) else None
    return _app_dir(False)


def default_profile_dir() -> str:
    """Where `autoedit looks save` creates a profile when none exists. AUTOEDIT_PROFILE wins when
    it names a folder, so a profile that does not exist yet can still be created there. An empty value
    means profiles are off: nothing would read what is written, so that is an error."""
    env = os.environ.get("AUTOEDIT_PROFILE")
    if env == "":
        raise ValueError("profiles are switched off (AUTOEDIT_PROFILE is empty); pass --profile DIR")
    if env:
        return env
    return _app_dir(True) or os.path.join(os.path.expanduser("~"), ".config", "autoedit")


def merge(base: dict, over: dict, where: str = "") -> dict:
    """Deep merge of `over` into a copy of `base`. Tables merge, everything else is replaced.
    A table over a non-table (or the reverse) is a ValueError naming the key."""
    out = dict(base)
    for key, val in over.items():
        name = f"{where}.{key}" if where else str(key)
        if key in out and isinstance(out[key], dict) != isinstance(val, dict):
            raise ValueError(f"{name}: a table cannot be replaced by a value, nor a value by a table")
        out[key] = merge(out[key], val, name) if isinstance(val, dict) and key in out else val
    return out


_BARE = re.compile(r"[A-Za-z0-9_-]+")


def _key(k: str) -> str:
    return k if _BARE.fullmatch(k) else _str(k)


def _str(s: str) -> str:
    # json's escapes are valid TOML; DEL is the one control character json leaves raw.
    return json.dumps(s, ensure_ascii=False).replace("\x7f", "\\u007f")


def _val(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, str):
        return _str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_val(x) for x in v) + "]"
    raise TypeError(f"cannot write {type(v).__name__} as TOML")


def dump_toml(data: dict) -> str:
    """Write a dict as TOML: tables (nested), strings, numbers, booleans and lists of those.
    Enough for the config files of this project; anything else is a TypeError."""
    lines: list[str] = []

    def table(d: dict, path: list[str]) -> None:
        plain = [(k, v) for k, v in d.items() if not isinstance(v, dict)]
        subs = [(k, v) for k, v in d.items() if isinstance(v, dict)]
        if path and (plain or not subs):
            if lines:
                lines.append("")
            lines.append("[" + ".".join(_key(k) for k in path) + "]")
        lines.extend(f"{_key(k)} = {_val(v)}" for k, v in plain)
        for k, v in subs:
            table(v, path + [k])

    table(data, [])
    return "\n".join(lines) + "\n"


def _load(path: str) -> dict:
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{path}: not valid TOML ({e})") from None


def _check_looks(data: dict, path: str) -> None:
    from .looks import FIELDS, HABIT, check_habit

    for name, body in data.items():
        if not isinstance(body, dict):
            raise ValueError(f"{path}: {name} must be a table of look fields")
        for field, val in body.items():
            if field == HABIT:
                check_habit(val, f"{path}: look {name}")
                continue
            if field not in FIELDS:
                raise ValueError(f"{path}: look {name} has unknown field {field!r}; "
                                 f"valid fields: {', '.join(FIELDS)}")
            ok = isinstance(val, bool) if field == "preserve_exposure" else (
                isinstance(val, (int, float)) and not isinstance(val, bool))
            if not ok:
                raise ValueError(f"{path}: look {name}.{field} must be "
                                 f"{'true or false' if field == 'preserve_exposure' else 'a number'}")
            if field != "preserve_exposure" and not math.isfinite(val):
                # nan passes every comparison and renders a black picture
                raise ValueError(f"{path}: look {name}.{field} must be a finite number, not {val}")


_CHECK_RANGES = {"min_score": (0, 1), "min_side": (0, 0.2), "merge_iou": (0, 1), "subject_ratio": (0, 1)}
_KIND_KEYS = ("label", "words", "min_score", "weak", "off")
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9 -]{0,39}")   # starts with a letter or digit: never blank


def _number(val, lo: float, hi: float, name: str, path: str) -> None:
    if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
        raise ValueError(f"{path}: {name} must be a finite number")
    if not lo <= val <= hi:
        raise ValueError(f"{path}: {name} must be from {lo} to {hi}, not {val}")


# The hide methods (F22) and the numbers each takes; the methods themselves are code (hide.py).
_HIDE_SCALARS = {"margin": (0, 1), "margin_min_px": (0, 50)}
_HIDE_METHODS = {"cover": {"ring": (0.02, 2)}, "mosaic": {"blocks": (2, 40)}, "blur": {"sigma": (0.02, 2)}}


def _check_hide(hide, path: str) -> None:
    if not isinstance(hide, dict):
        raise ValueError(f"{path}: hide must be a table")
    for key, val in hide.items():
        if key == "method":
            if val not in _HIDE_METHODS:
                raise ValueError(f"{path}: hide.method must be one of {', '.join(_HIDE_METHODS)}, not {val!r}")
        elif key in _HIDE_SCALARS:
            _number(val, *_HIDE_SCALARS[key], f"hide.{key}", path)
        elif key in _HIDE_METHODS:
            if not isinstance(val, dict):
                raise ValueError(f"{path}: hide.{key} must be a table")
            for k, x in val.items():
                if k not in _HIDE_METHODS[key]:
                    raise ValueError(f"{path}: unknown key hide.{key}.{k}; valid keys: {', '.join(_HIDE_METHODS[key])}")
                _number(x, *_HIDE_METHODS[key][k], f"hide.{key}.{k}", path)
        else:
            raise ValueError(f"{path}: unknown key hide.{key}; valid keys: "
                             f"{', '.join(['method', *_HIDE_SCALARS, *_HIDE_METHODS])}")


def _check_reveal(data: dict, path: str) -> None:
    """Refuse a reveal.toml that is malformed or would make the check slow (each word is a model call)."""
    for key in data:
        if key not in ("check", "kind", "hide"):
            raise ValueError(f"{path}: unknown key {key!r}; valid keys: check, kind, hide")
    check = data.get("check", {})
    if not isinstance(check, dict):
        raise ValueError(f"{path}: check must be a table")
    for key, val in check.items():
        if key in _CHECK_RANGES:
            _number(val, *_CHECK_RANGES[key], f"check.{key}", path)
        elif key == "max_findings":
            _number(val, 1, 200, "check.max_findings", path)
            if not isinstance(val, int):
                raise ValueError(f"{path}: check.max_findings must be a whole number")
        elif key == "tiles":
            if isinstance(val, bool) or val not in (1, 2):
                raise ValueError(f"{path}: check.tiles must be 1 or 2")
        else:
            raise ValueError(f"{path}: unknown key check.{key}; valid keys: "
                             f"{', '.join(list(_CHECK_RANGES) + ['max_findings', 'tiles'])}")
    _check_hide(data.get("hide", {}), path)
    kinds = data.get("kind", {})
    if not isinstance(kinds, dict):
        raise ValueError(f"{path}: kind must be a table of tables")
    total = 0
    for name, body in kinds.items():
        if not isinstance(body, dict):
            raise ValueError(f"{path}: kind.{name} must be a table")
        for key, val in body.items():
            where = f"kind.{name}.{key}"
            if key not in _KIND_KEYS:
                raise ValueError(f"{path}: unknown key {where}; valid keys: {', '.join(_KIND_KEYS)}")
            if key == "label" and not (isinstance(val, str) and val.strip()):
                raise ValueError(f"{path}: {where} must be a non-empty string")
            elif key in ("weak", "off") and not isinstance(val, bool):
                raise ValueError(f"{path}: {where} must be true or false")
            elif key == "min_score":
                _number(val, 0, 1, where, path)
            elif key == "words":
                if not (isinstance(val, list) and 1 <= len(val) <= 8):
                    raise ValueError(f"{path}: {where} must be a list of 1 to 8 words")
                for w in val:
                    if not (isinstance(w, str) and _WORD.fullmatch(w)):
                        raise ValueError(f"{path}: {where} has a bad word {w!r}: "
                                         "1 to 40 letters, digits, spaces or hyphens")
                total += len(val)
    if total > 40:
        raise ValueError(f"{path}: {total} words in all; at most 40 (each word is a model call)")


def _overlay(base_name: str, profile: str | None) -> tuple[str, dict] | None:
    """(path, content) of the profile's file for `base_name`, validated; None without one."""
    if not profile:
        return None
    path = os.path.join(profile, base_name)
    if not os.path.isfile(path):
        return None
    data = _load(path)
    if base_name == "looks.toml":
        _check_looks(data, path)
    elif base_name == "reveal.toml":
        _check_reveal(data, path)
    elif base_name == "genres.toml":
        from . import genres   # inside the branch: genres imports contract, which imports paths

        genres.check(data, path)
    return path, data


def _merged(base_path: str, base_data: dict, over_path: str, over: dict) -> dict:
    try:
        return merge(base_data, over)
    except ValueError as e:
        raise ValueError(f"{over_path}: {e}") from None
    except TypeError as e:
        raise ValueError(f"{over_path}: {e}") from None


def read_config(path: str, profile: str | None = None) -> dict:
    """The config file at `path`, with the profile's file of the same name merged over it."""
    base = _load(path) if path and os.path.exists(path) else {}
    ov = _overlay(os.path.basename(path), profile)
    return _merged(path, base, *ov) if ov else base


_TMP: list[str] = []
_WRITE_LOCK = threading.Lock()


def _tmp_root() -> str:
    """One temporary folder per process for merged copies, made on first use, removed at exit."""
    if not _TMP:
        root = tempfile.mkdtemp(prefix="autoedit-profile-")
        _TMP.append(root)
        pid = os.getpid()
        atexit.register(lambda: os.getpid() == pid and shutil.rmtree(root, ignore_errors=True))
    return _TMP[0]


def _stamp(path: str) -> str:
    st = os.stat(path)
    return f"{path}:{st.st_mtime_ns}:{st.st_size}"


def _merged_file(name: str, base_path: str, over_path: str, over: dict) -> str:
    """Path of the merged copy of one config file, rewritten only when an input changed."""
    key = hashlib.blake2b(f"{base_path}|{over_path}".encode(), digest_size=6).hexdigest()
    folder = os.path.join(_tmp_root(), key)
    out = os.path.join(folder, f"{name}.toml")
    stamp = _stamp(over_path) + "|" + (_stamp(base_path) if os.path.exists(base_path) else "none")
    marker = out + ".stamp"
    try:
        with open(marker) as fh:
            if fh.read() == stamp and os.path.exists(out):
                return out
    except OSError:
        pass
    base = _load(base_path) if os.path.exists(base_path) else {}
    try:
        text = dump_toml(_merged(base_path, base, over_path, over))
    except TypeError as e:    # a date or an array of tables in the overlay: not a config value here
        raise ValueError(f"{over_path}: {e}") from None
    os.makedirs(folder, exist_ok=True)
    # Write beside the target and swap it in, so a reader (another thread, a render worker) sees
    # the old file or the new one, never half of it.
    with _WRITE_LOCK:
        for target, content in ((out, text), (marker, stamp)):
            tmp = f"{target}.{os.getpid()}.{threading.get_ident()}.part"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.replace(tmp, target)
    return out


def config_paths(configs_dir: str | None = None, profile: str | None = None) -> dict[str, str]:
    """RunConfig path arguments for a configs folder: every file's path in that folder, existing
    or not. `instructions_path` and `exports_path` are left out when the folder lacks the file, so
    RunConfig looks for it through the usual chain ($AUTOEDIT_CONFIGS_DIR, ./configs, then the
    packaged file), not only in the packaged folder. A file that has an overlay in `profile` is returned
    as the path of the merged copy (over the packaged file when the folder lacks it)."""
    base = configs_dir or globals()["configs_dir"]()
    out: dict[str, str] = {}
    for name in _CONFIG_FILES:
        path = os.path.join(base, f"{name}.toml")
        ov = _overlay(f"{name}.toml", profile)
        if ov and name in _OPTIONAL and not os.path.exists(path):
            path = os.path.join(_packaged(), f"{name}.toml")
        if ov:
            out[f"{name}_path"] = _merged_file(name, path, *ov)
        elif name not in _OPTIONAL or os.path.exists(path):
            out[f"{name}_path"] = path
    return out
