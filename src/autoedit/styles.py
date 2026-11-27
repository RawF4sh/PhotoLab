"""Named local development styles and their optional statistical targets."""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field, replace


@dataclass
class Style:
    name: str
    look: str = "natural"
    concepts: list[str] = field(default_factory=list)
    brief: str = ""
    label: str = ""
    target: str = ""
    targets: dict[str, str] = field(default_factory=dict)
    pull: float = 0.0
    aim: float = 0.0
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
    from .edit.ops import OP_BOUNDS             # here, not at the top: importing this module stays light
    from .edit.style_target import CONTROLS

    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    out = {}
    for name, body in data.items():
        targets = body.get("targets", {})
        if (not isinstance(targets, dict) or (targets and (name != "auto" or "landscape" not in targets))
                or any(not isinstance(v, str) or not v for v in targets.values())):
            raise ValueError(f"{name}: expected Auto target names indexed by scene")
        limits = dict(body.get("limits", {}))
        bounds = {"exposure": 3, "shadows_ev": 2, "chroma": .90, "lights_ev": 3}
        for control in CONTROLS.values():
            low, high = OP_BOUNDS[control]
            bounds.setdefault(control, max(abs(low), abs(high)))
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
            targets=dict(targets), pull=_number(body.get("pull", 0), 0, 1, "pull"), aim=_number(body.get("aim", 0), 0, .5, "aim"), limits=limits, intent=intent,
            tested=body.get("tested", False), available=body.get("available", True),
            run=run, moves=body.get("moves", {}),
        )
    return out


def style_for(name: str | None, styles: dict[str, Style]) -> Style:
    if name == "standard":
        name = "natural"   # same processing, including a user's existing natural style overrides
    if name == "auto" and name in styles and styles[name].targets:
        simple = styles.get("natural", Style("natural"))
        return replace(styles[name], brief=simple.brief, look=simple.look, concepts=simple.concepts)
    if name and name in styles:
        return styles[name]
    return Style(name=name or "natural")


def offered_styles(styles):
    """The styles of the normal lists. A specialist (it has a label or a target) joins them only
    once it is tested; until then it runs when named explicitly."""
    return {s.name: s for s in styles.values() if s.available and (s.targets or s.tested or not (s.label or s.target))}


def local_catalog(styles, candidates=False):
    rows = [{"id": "standard", "label": "Simple", "tested": True}]
    rows.extend({"id": s.name, "label": s.label, "tested": s.tested}
                for s in styles.values() if s.label and s.available and (s.targets or s.tested or candidates))
    return rows


def combine_brief(style_brief: str, prompt: str | None) -> str | None:
    """Merge the style's baked brief with the user's free-text prompt."""
    parts = [p.strip() for p in (style_brief, prompt) if p and p.strip()]
    return ". ".join(parts) if parts else None


def load_auto(path, style):
    """Validate every scene before starting a run, including scenes it may never encounter."""
    from .edit.ops import OP_BOUNDS
    from .scene import LANDSCAPES
    with open(path, "rb") as fh:
        cfg = tomllib.load(fh)
    for key in ("fallback_strength", "shoot_pull"):
        _number(cfg["auto"][key], 0, 1, key)
    shoot = cfg.get("shoot")
    if not isinstance(shoot, dict):
        raise ValueError("auto: the [shoot] table is missing")
    _number(shoot.get("window_min"), 1, 120, "window_min")
    if type(shoot.get("min_frames")) is not int or shoot["min_frames"] < 1:
        raise ValueError("auto: min_frames must be a whole number of 1 or more")
    _number(shoot.get("min_share"), 0.5, 1, "min_share")
    for key, value in cfg["scenes"].items():
        if key.endswith("_ev"):
            if type(value) not in (float, int) or not math.isfinite(value):
                raise ValueError(f"{key} must be a finite exposure value")
        else:
            _number(value, -1 if key == "cool_sky" else 0, 1, key)
    for name in ("landscape", *LANDSCAPES):
        options = cfg["scene"][name]
        if type(options.get("released")) is not bool:
            raise ValueError(f"auto: {name}.released must be true or false")
        for key in ("clip_budget", "shadow_clip_budget"):
            _number(options[key], 0, 1, key)
        for key, value in cfg["limits"].get(name, {}).items():
            if key == "chroma":
                bound = 0.90
            elif key in OP_BOUNDS:
                bound = max(abs(v) for v in OP_BOUNDS[key])
            else:
                raise ValueError(f"auto: unknown limit {key!r}")
            _number(value, 0, bound, key)
    if set(style.targets) - set(cfg["scene"]):
        raise ValueError("Auto names an unknown scene in its targets")
    return cfg


@dataclass
class AutoPhoto:
    scene: str
    reason: str
    confidence: float
    released: bool
    target: str
    requested_strength: float
    strength: float
    fallback: str
    direct: bool = False

    @classmethod
    def select(cls, detected, style, cfg, requested, preview, retry=None, shoot=None):
        scene = detected.finer or detected.name
        reason = detected.reason or f"detected {detected.name} subjects"
        from .scene import LANDSCAPES
        if detected.name == "landscape" and shoot and shoot.get("scene") in (*LANDSCAPES, "landscape") and shoot["scene"] != scene:
            scene = shoot["scene"]
            reason += f"; shoot agrees on {scene}"
        options = cfg["scene"].get(scene) if detected.name == "landscape" else None
        released = bool(options and options["released"])
        strength = requested if retry is None else min(requested, retry)
        if scene == "landscape":
            strength *= cfg["auto"]["fallback_strength"]
        fallback = ""
        target = ""
        if not options or not (released or preview):
            fallback = f"Auto is not ready for {scene}. Used Simple."
        elif strength <= 0:
            fallback = "Auto at zero strength uses Simple."
        else:
            target = style.targets.get(scene, style.targets["landscape"])
        return cls(scene, reason, detected.confidence, released, target, requested,
                   strength if target else 0.0, fallback, detected.direct)

    def audit(self):
        return {"scene": self.scene, "reason": self.reason, "confidence": self.confidence,
                "released": self.released, "target": self.target or None,
                "requested_strength": self.requested_strength, "strength": self.strength,
                "fallback": self.fallback, "direct": self.direct}
