"""Load concepts.toml — presets (concept sets) + per-concept metadata."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass


@dataclass
class ConceptMeta:
    name: str
    priority: int = 50
    min_score: float = 0.45
    min_coverage: float = 0.02
    feather_px: float = 0.004  # fraction of long edge
    rule_region: str = ""  # which [scene.region] block applies; defaults to name
    guard_global: bool = False  # protect this region from aggressive global ops (skin-safe guard)

    def region(self) -> str:
        return self.rule_region or self.name


def default_meta(name: str) -> ConceptMeta:
    return ConceptMeta(name=name, rule_region=name)


def load_concepts(path: str) -> tuple[dict[str, list[str]], dict[str, ConceptMeta]]:
    """Return (presets: name -> [concepts], meta: concept -> ConceptMeta)."""
    with open(path, "rb") as fh:
        data = tomllib.load(fh)

    presets: dict[str, list[str]] = {}
    for name, body in data.get("preset", {}).items():
        presets[name] = list(body.get("concepts", []))

    meta: dict[str, ConceptMeta] = {}
    for name, body in data.get("concept", {}).items():
        meta[name] = ConceptMeta(
            name=name,
            priority=int(body.get("priority", 50)),
            min_score=float(body.get("min_score", 0.45)),
            min_coverage=float(body.get("min_coverage", 0.02)),
            feather_px=float(body.get("feather_px", 0.004)),
            rule_region=str(body.get("rule_region", name)),
            guard_global=bool(body.get("guard_global", False)),
        )
    return presets, meta


def meta_for(name: str, meta: dict[str, ConceptMeta]) -> ConceptMeta:
    return meta.get(name, default_meta(name))
