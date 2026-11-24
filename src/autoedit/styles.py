"""Named local development styles and their optional statistical targets."""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field


@dataclass
class Style:
    name: str
    look: str = "natural"
    concepts: list[str] = field(default_factory=list)
    brief: str = ""
    label: str = ""
    target: str = ""
    pull: float = 0.0
    limits: dict = field(default_factory=dict)
    intent: tuple = ()
    tested: bool = False
    available: bool = True
    run: dict = field(default_factory=dict)
    moves: dict = field(default_factory=dict)


def _number(value, low, high, key):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{key} must be between {low} and {high}")
    return float(value)


def load_styles(path: str) -> dict[str, Style]:
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    out = {}
    for name, body in data.items():
        limits = dict(body.get("limits", {}))
        bounds = {"exposure": 3, "shadows_ev": 2, "chroma": .90, "lights_ev": 3}
        if limits.keys() - bounds.keys():
            raise ValueError(f"{name}: unsupported style limit")
        limits = {key: _number(val, 0, bounds[key], key) for key, val in limits.items()}
        intent = tuple(body.get("intent", ()))
        if set(intent) - {"richer", "darker"}:
            raise ValueError(f"{name}: unsupported style intent")
        if intent and (not body.get("target") or name in ("natural", "standard")):
            raise ValueError(f"{name}: intent requires an explicitly chosen specialist target")
        if name in ("natural", "standard") and any(body.get(key) for key in ("target", "moves", "run")):
            # Standard is what a default run renders: it changes by a blind vote, never by a target.
            raise ValueError(f"{name}: the standard style takes no target, moves or run settings")
        run = body.get("run", {})
        if set(run) - {"denoise", "palette", "palette_strength"}:
            raise ValueError(f"{name}: unsupported style run options")
        if "denoise" in run and not isinstance(run["denoise"], bool):
            raise ValueError(f"{name}: denoise must be true or false")
        if "palette_strength" in run:
            _number(run["palette_strength"], 0, 1, "palette_strength")
        for flag in ("available", "tested"):
            if flag in body and not isinstance(body[flag], bool):
                raise ValueError(f"{name}: {flag} must be true or false")
        out[name] = Style(
            name=name, look=str(body.get("look", "natural")), concepts=list(body.get("concepts", [])),
            brief=str(body.get("brief", "")), label=str(body.get("label", "")), target=str(body.get("target", "")),
            pull=_number(body.get("pull", 0), 0, 1, "pull"), limits=limits, intent=intent,
            tested=body.get("tested", False), available=body.get("available", True),
            run=run, moves=body.get("moves", {}),
        )
    return out


def style_for(name: str | None, styles: dict[str, Style]) -> Style:
    if name == "standard":
        name = "natural"   # same processing, including a user's existing natural style overrides
    if name and name in styles:
        return styles[name]
    return Style(name=name or "natural")


def offered_styles(styles):
    """The styles of the normal lists. A specialist (it has a label or a target) joins them only
    once it is tested; until then it runs when named explicitly."""
    return {s.name: s for s in styles.values() if s.available and (s.tested or not (s.label or s.target))}


def local_catalog(styles, candidates=False):
    rows = [{"id": "standard", "label": "Standard", "tested": True}]
    rows.extend({"id": s.name, "label": s.label, "tested": s.tested}
                for s in styles.values() if s.label and s.available and (s.tested or candidates))
    return rows


def combine_brief(style_brief: str, prompt: str | None) -> str | None:
    """Merge the style's baked brief with the user's free-text prompt."""
    parts = [p.strip() for p in (style_brief, prompt) if p and p.strip()]
    return ". ".join(parts) if parts else None
