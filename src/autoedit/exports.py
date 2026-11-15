"""Export presets (F10): named bundles of output settings in configs/exports.toml."""

from __future__ import annotations

import tomllib

_KEYS = {"format": "out_format", "quality": "quality", "long_edge": "out_long_edge",
         "name": "out_name", "subdir": "out_subdir", "metadata": "metadata"}


def load(path: str) -> dict[str, dict]:
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except (FileNotFoundError, OSError):
        return {}


def preset_kwargs(name: str, path: str) -> dict:
    """RunConfig arguments of the preset `name`. Unknown preset or key: ValueError."""
    presets = load(path)
    if name not in presets:
        raise ValueError(f"unknown export preset {name!r}; choose one of {sorted(presets)}")
    bad = sorted(k for k in presets[name] if k not in _KEYS)
    if bad:
        raise ValueError(f"exports.toml: preset {name!r} has unknown keys {bad}")
    return {_KEYS[k]: v for k, v in presets[name].items()}
