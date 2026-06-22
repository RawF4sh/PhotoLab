"""Region policies — hard safety rails enforced AFTER the planner (rules or LLM), BEFORE apply.

Each region (via its ConceptMeta) may declare:
  - forbidden_ops: ops that must never run on it (e.g. snow must not be warmed → forbid `temp`)
  - max_ops:       per-op magnitude caps (abs value), e.g. foliage saturation ≤ 8

No proposal — from the rule baseline or the LLM — can exceed these. This is the layer that
prevents yellow snow, neon foliage, and green skin regardless of what upstream decided. It is
pure data (configs/concepts.toml) + this deterministic enforcement; see AutoEdit Pipeline Fixes
v0.3 §5 / the color-correction plan §5.
"""

from __future__ import annotations

from .concepts import ConceptMeta


def enforce(region_settings: dict[str, dict], meta: dict[str, ConceptMeta]) -> list[str]:
    """Mutate `region_settings` ({concept_name: settings}) in place to satisfy each region's
    policy. Returns human-readable notes for the sidecar (what was dropped/clamped)."""
    notes: list[str] = []
    for concept, settings in region_settings.items():
        cm = meta.get(concept)
        if cm is None:
            continue
        for op in cm.forbidden_ops:
            if settings.get(op, 0.0):
                settings.pop(op, None)
                notes.append(f"policy[{concept}]: dropped {op} (forbidden)")
        for op, cap in cm.max_ops.items():
            v = settings.get(op)
            if v is None:
                continue
            if abs(v) > cap:
                settings[op] = cap if v > 0 else -cap
                notes.append(f"policy[{concept}]: clamped {op} {v:+.0f}→{settings[op]:+.0f}")
    return notes
