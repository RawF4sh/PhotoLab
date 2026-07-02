"""Learned recipe decider (M6) — predicts the GLOBAL edit recipe from image stats, with the rule
baseline supplying per-region edits and acting as a safe fallback.

Non-generative: it only emits the same bounded slider settings the rules engine uses, then the
look/intensity shaping + all safety gates apply downstream exactly as before. If no model is
loaded (or prediction fails), it transparently returns the pure rules decision.
"""

from __future__ import annotations

from ..contract import Settings
from ..looks import Look, shape
from .base import DecisionContext


class LearnedDecider:
    name = "learned"

    def __init__(self, model, fallback):
        self.model = model            # recipe.RecipeModel | None
        self.fallback = fallback      # RulesDecider — supplies regions + the fallback global

    def decide(self, ctx: DecisionContext) -> tuple[Settings, dict[str, Settings]]:
        g_rules, regions = self.fallback.decide(ctx)   # shaped regions (+ rules global as fallback)
        if self.model is None:
            return g_rules, regions
        try:
            from .. import recipe

            g = recipe.predict(recipe.features_from_stats(ctx.gstats), self.model)
        except Exception:
            return g_rules, regions                    # any failure → safe rules baseline
        # shape the learned global by look + intensity, just like the rules path does
        shape(g, ctx.looks.get(ctx.look, Look()), ctx.intensity, is_global=True)
        return g, regions
