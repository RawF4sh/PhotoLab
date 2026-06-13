"""Human-readable "why" notes (port of the Rust emit.rs notes builder).

Built from day one — the blind-test debugging log and a future "explain this edit" feature.
"""

from __future__ import annotations

from .contract import DetectedConcept, GlobalStatsModel, RegionStatsModel


def build_notes(
    g: GlobalStatsModel,
    region_stats: dict[str, RegionStatsModel],
    detected: list[DetectedConcept],
) -> list[str]:
    notes: list[str] = []

    for r in region_stats.values():
        if r.name in ("skin", "subject", "person") and (g.mean_lum - r.lum) > 0.1:
            notes.append(f"{r.name} {((g.mean_lum - r.lum) * 5):.1f} EV below frame → lifted")

    if g.blown_sky > 0.05:
        notes.append(f"{g.blown_sky * 100:.0f}% of sky blown → recovered highlights")
    if g.haze > 0.4:
        notes.append("hazy scene → added dehaze")
    if g.shadow_clip > 0.1:
        notes.append(f"{g.shadow_clip * 100:.0f}% shadows crushed → lifted")
    if g.highlight_clip > 0.05:
        notes.append(f"{g.highlight_clip * 100:.0f}% highlights clipped → pulled back")

    for d in detected:
        if not d.present:
            notes.append(f"{d.name}: not detected → skipped")

    return notes
