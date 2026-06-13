"""Scene classification (port of the Rust scene.rs) — pick which rule block set runs.

v1 classifies from the concepts SAM actually detected + global stats (a cheap proxy for
the anchor-probe described in the plan). Returns the scene name used to index rules.toml.
"""

from __future__ import annotations

from dataclasses import dataclass

from .contract import GlobalStatsModel

_PEOPLE = {"skin", "person", "face"}


@dataclass
class Scene:
    name: str
    confidence: float


def classify(g: GlobalStatsModel, present: set[str], override: str | None = None) -> Scene:
    if override:
        return Scene(name=override, confidence=1.0)
    if present & _PEOPLE:
        return Scene(name="people", confidence=0.7)
    if "sky" in present and g.sky_lum > 0.0:
        return Scene(name="landscape", confidence=0.6)
    return Scene(name="other", confidence=0.5)
