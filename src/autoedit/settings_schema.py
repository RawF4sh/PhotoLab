"""The settings schema: every user-facing RunConfig setting with its type, default, limits and
words (docs/plans/F13-settings-schema.md). Types, defaults and limits come from RunConfig; the
label, group, help and advanced flag from configs/settings.toml."""

from __future__ import annotations

import os
import tomllib
import types
from typing import Literal, Union, get_args, get_origin

from . import paths
from .contract import RunConfig

VERSION = 1


def _kind(name: str, ann) -> tuple[str, list | None]:
    """(type name, choices) of one annotation. A kind of field nobody mapped is an error."""
    if get_origin(ann) in (Union, types.UnionType):
        rest = [a for a in get_args(ann) if a is not type(None)]
        if len(rest) == 1:
            return _kind(name, rest[0])
    elif get_origin(ann) is Literal:
        return "choice", list(get_args(ann))
    elif get_origin(ann) is list:
        return "list", None
    elif ann in (bool, int, float, str):
        return ann.__name__ if ann is not str else "string", None
    raise TypeError(f"settings schema: field {name!r} has an annotation that is not mapped: {ann!r}")


def _limits(info) -> dict:
    # Read by attribute name so the code does not depend on annotated_types classes.
    # ponytail: gt/lt (raw_tone_target_p50) are reported as min/max too; the open end is not marked.
    out: dict = {}
    for m in info.metadata:
        for attr, key in (("ge", "min"), ("gt", "min"), ("le", "max"), ("lt", "max"), ("max_length", "max_length")):
            if getattr(m, attr, None) is not None:
                out[key] = getattr(m, attr)
    return out


def _choices(configs_dir: str | None, profile: str | None) -> dict[str, list[str]]:
    from . import exports, genres, palette
    from .concepts import load_concepts
    from .looks import load as load_looks
    from .styles import load_styles, offered_styles

    p = paths.config_paths(configs_dir, profile)
    return {
        "style": list(offered_styles(load_styles(p["styles_path"]))),
        "genre": [g["name"] for g in genres.listing(configs_dir, profile) if g["offered"]],
        "look": list(load_looks(p["looks_path"])),
        "palette": ["auto", "off", *palette.load_palettes(p["palettes_path"])],
        "preset": list(load_concepts(p["concepts_path"])[0]),
        "export": list(exports.load(p.get("exports_path") or paths.config_path("exports.toml", True))),
    }


def described(configs_dir: str | None = None) -> dict:
    """The parsed settings.toml: hidden, groups, setting. A folder that predates the file gets the
    packaged one."""
    own = os.path.join(configs_dir, "settings.toml") if configs_dir else ""
    path = own if own and os.path.exists(own) else paths.config_path("settings.toml", packaged_fallback=True)
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def schema(configs_dir: str | None = None, profile: str | None = None) -> dict:
    """The settings schema as a plain dict (see docs/plans/F13-settings-schema.md)."""
    doc = described(configs_dir)
    hidden, groups, words = set(doc.get("hidden", [])), doc.get("groups", {}), doc.get("setting", {})
    fields = RunConfig.model_fields
    unknown = [k for k in fields if k not in hidden and k not in words]
    stale = [k for k in [*words, *hidden] if k not in fields]
    if unknown or stale:
        raise ValueError(f"settings.toml does not match RunConfig: missing {unknown}, no longer a field {stale}")
    choices = _choices(configs_dir, profile)
    order = list(groups)
    out = []
    for key, w in words.items():
        info = fields[key]
        kind, opts = _kind(key, info.annotation)
        if key in choices:
            kind, opts = "choice", choices[key]
        default = info.get_default(call_default_factory=True)
        s = {"key": key, "label": w["label"], "group": w["group"], "type": kind, "default": default}
        if opts is not None:
            s["choices"] = opts
        s.update(_limits(info))
        s.update(help=w["help"], advanced=bool(w.get("advanced", False)))
        out.append(s)
    out.sort(key=lambda s: order.index(s["group"]))      # stable: file order inside a group
    return {"version": VERSION, "groups": [{"key": k, "label": v} for k, v in groups.items()], "settings": out}
