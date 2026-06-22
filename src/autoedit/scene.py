"""Scene classification (port of the Rust scene.rs) — pick which rule block set runs.

v1 classifies from the concepts SAM actually detected + global stats (a cheap proxy for
the anchor-probe described in the plan). Returns the scene name used to index rules.toml.
"""

from __future__ import annotations

from dataclasses import dataclass

from .contract import GlobalStatsModel, RegionStatsModel

_PEOPLE = {"skin", "person", "face"}
_LANDSCAPE = {"sky", "snow", "mountain", "grass", "water", "foliage", "trees", "rock"}
# Min skin/face coverage to call a frame a portrait. Below this, a "skin" detection is almost
# always warm rock/grass/sand — it must NOT drag a landscape into the people ruleset (or warm
# scattered specks into red dots). A real face/half-body portrait is well above this.
_SKIN_PORTRAIT_COV = 0.08


@dataclass
class Scene:
    name: str
    confidence: float


def classify(g: GlobalStatsModel, present: set[str],
             region_stats: dict[str, RegionStatsModel] | None = None,
             override: str | None = None) -> Scene:
    if override:
        return Scene(name=override, confidence=1.0)
    rs = region_stats or {}

    def cov(name: str) -> float:
        r = rs.get(name)
        return r.coverage if r is not None else 0.0

    skin_cov = max(cov("skin"), cov("face"), cov("person"))
    # A genuine portrait: an explicit person/face concept, or skin covering a real fraction of
    # the frame. A tiny high-confidence skin blob on a landscape does NOT qualify.
    if (present & {"person", "face"}) or skin_cov >= _SKIN_PORTRAIT_COV:
        return Scene(name="people", confidence=0.7)
    if present & _LANDSCAPE:
        return Scene(name="landscape", confidence=0.6)
    if present & _PEOPLE:  # small skin, no landscape context → still a person-ish frame
        return Scene(name="people", confidence=0.55)
    return Scene(name="other", confidence=0.5)
