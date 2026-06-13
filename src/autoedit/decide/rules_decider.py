"""The default decider: rule baseline + look/intensity shaping. Deterministic, offline, free."""

from __future__ import annotations

from ..contract import Settings
from ..looks import Look, shape
from ..rules import apply as rules_apply
from .base import DecisionContext


class RulesDecider:
    name = "rules"

    def decide(self, ctx: DecisionContext) -> tuple[Settings, dict[str, Settings]]:
        g, regions = rules_apply(
            ctx.rules, ctx.scene, ctx.gstats, ctx.region_stats, ctx.region_rule, ctx.intensity
        )
        look = ctx.looks.get(ctx.look, Look())
        shape(g, look, ctx.intensity)
        for s in regions.values():
            shape(s, look, ctx.intensity)
        return g, regions
