"""Genre packs (F30): named bundles of data that exists elsewhere, in configs/genres.toml. A genre
is turned into RunConfig arguments where a config is built (cli.py, web/service.py), below what the
user wrote, like an export preset. The pipeline never reads this file; there is no code per genre."""

from __future__ import annotations

import os
import re

from pydantic import ValidationError

from . import paths
from .contract import RunConfig

KEYS = ("description", "tested", "preset", "look", "rule_sets", "reveal", "run")
# What [NAME.run] may set: how the photo is edited, nothing else. No file, no network, no backend,
# no output folder: a genre file can come from someone else.
RUN_KEYS = ("intensity", "palette", "palette_strength", "consistency", "compose", "vignette", "sharpen",
            "highlight_hold", "levels", "light_shaping", "denoise", "grain", "grain_size", "backdrop",
            "consistency_wb", "colour_report", "night_sky")
_NAME = re.compile(r"[a-z0-9_]{1,40}")
_MAX = {"description": 200, "preset": 40, "look": 40}


def _file(kw: dict, name: str) -> str:
    return kw.get(f"{name}_path") or paths.config_path(f"{name}.toml", packaged_fallback=True)


def check(data: dict, path: str) -> None:
    """Refuse a genres file that is malformed. Every error is a ValueError starting with the path
    and the genre's name."""
    for name, g in data.items():
        where = f"{path}: genre {name}"
        if not _NAME.fullmatch(str(name)) or not isinstance(g, dict):
            raise ValueError(f"{where}: the name must be 1 to 40 of a-z, 0-9 or _, and the genre a table")
        for key in g:
            if key not in KEYS:
                raise ValueError(f"{where}: unknown key {key!r}; valid keys: {', '.join(KEYS)}")
        if "tested" in g and not isinstance(g["tested"], bool):
            raise ValueError(f"{where}: tested must be true or false")
        for key, limit in _MAX.items():
            if key in g and not (isinstance(g[key], str) and len(g[key]) <= limit):
                raise ValueError(f"{where}: {key} must be a string of at most {limit} characters")
        for key in ("rule_sets", "reveal"):
            val = g.get(key, [])
            if not (isinstance(val, list) and len(val) <= 20
                    and all(isinstance(v, str) and _NAME.fullmatch(v) for v in val)):
                raise ValueError(f"{where}: {key} must be a list of at most 20 names (a-z, 0-9, _)")
        run = g.get("run", {})
        if not isinstance(run, dict):
            raise ValueError(f"{where}: run must be a table")
        for key, val in run.items():
            if key not in RUN_KEYS:
                raise ValueError(f"{where}: run.{key} is not allowed; valid keys: {', '.join(RUN_KEYS)}")
            try:
                RunConfig.model_validate({key: val}, strict=True)
            except ValidationError as e:
                raise ValueError(f"{where}: run.{key}: {e.errors()[0]['msg']}") from None


def load(path: str) -> dict[str, dict]:
    """Every genre of the file at `path` (the packaged file or a merged copy), checked. No file: none."""
    data = paths._load(path) if path and os.path.exists(path) else {}
    check(data, path)
    return data


def run_kwargs(name: str, kw: dict, style_named: bool = False) -> dict:
    """RunConfig arguments of the genre `name`. `kw` holds the run's config file paths
    (`paths.config_paths`); a missing one is found the usual way. `style_named`: the user named a
    style, which keeps its own look. Unknown genre, or a preset or look no file defines: ValueError."""
    from . import looks
    from .concepts import load_concepts

    genres = load(_file(kw, "genres"))
    if not isinstance(name, str) or name not in genres:
        raise ValueError(f"unknown genre {name!r}; choose one of {sorted(genres)}")
    g = genres[name]
    preset, look = g.get("preset", ""), g.get("look", "")
    if preset and preset not in load_concepts(_file(kw, "concepts"))[0]:
        raise ValueError(f"genre {name}: preset {preset!r} is not a [preset] of concepts.toml")
    if look and look not in looks.load(_file(kw, "looks")):
        raise ValueError(f"genre {name}: look {look!r} is not a look of looks.toml")
    out = dict(g.get("run", {}))
    if preset:
        out["preset"] = preset
    if look and not style_named:
        out["look"] = look
    if g.get("rule_sets"):
        out["rule_sets"] = list(g["rule_sets"])
    return out


def listing(configs_dir: str | None = None, profile: str | None = None) -> list[dict]:
    """One dict per genre of the merged file, in file order, for the list, the API and the schema."""
    from .concepts import load_concepts

    p = paths.config_paths(configs_dir, profile)
    merged = load(_file(p, "genres"))
    own = (paths._overlay("genres.toml", profile) or ("", {}))[1]
    presets = load_concepts(_file(p, "concepts"))[0]
    base = load(_file(paths.config_paths(configs_dir), "genres"))   # the genres without the profile
    out = []
    for name, g in merged.items():
        tested = bool(g.get("tested", False))
        origin = ("packaged" if name not in own
                  else "packaged, changed by the profile" if name in base
                  else "profile")
        out.append({"name": name, "description": g.get("description", ""), "tested": tested,
                    "origin": origin, "offered": tested or name in own,
                    "preset": g.get("preset", ""), "concepts": list(presets.get(g.get("preset", ""), [])),
                    "look": g.get("look", ""), "rule_sets": list(g.get("rule_sets", [])),
                    "reveal": list(g.get("reveal", [])), "run": dict(g.get("run", {}))})
    return out
