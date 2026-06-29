"""Decision layer — rules baseline and optional LLM refine, behind one protocol.

The LLM (BYO-key) path is gated: it requires *explicit consent* (`--allow-cloud` or
AUTOEDIT_ALLOW_CLOUD=1) AND an API key. Without either, it transparently falls back to the
local rules decider after printing a one-time disclosure. Photos (a small thumbnail) leave
to the AI provider only when the LLM path is actually active — see DISCLOSURE.
"""

from __future__ import annotations

import os
import sys

from .base import DecisionContext, Decider
from .rules_decider import RulesDecider

__all__ = ["DecisionContext", "Decider", "RulesDecider", "make_decider", "DISCLOSURE", "consented"]

DISCLOSURE = (
    "AutoEdit cloud (BYO-key): the LLM decider sends a SMALL THUMBNAIL + image stats to your "
    "AI provider (Anthropic) and bills YOUR API key per photo. Full-resolution pixels never "
    "leave your machine; segmentation stays local. Enable with --allow-cloud (or "
    "AUTOEDIT_ALLOW_CLOUD=1)."
)


def consented(cfg) -> bool:
    if getattr(cfg, "allow_cloud", False):
        return True
    return os.environ.get("AUTOEDIT_ALLOW_CLOUD", "").lower() in ("1", "true", "yes")


def _api_key(cfg) -> str | None:
    return getattr(cfg, "api_key", None) or os.environ.get("ANTHROPIC_API_KEY")


def make_decider(cfg) -> Decider:
    """Build a decider from a RunConfig. `learned` predicts the global recipe from a trained model
    (falls back to rules if the model is missing/unloadable). `llm` falls back to rules (with a
    disclosure) unless consent + an API key are both present."""
    if cfg.decider == "learned":
        from .learned_decider import LearnedDecider

        model = None
        path = getattr(cfg, "recipe_model", None)
        if path:
            try:
                from .. import recipe

                model = recipe.load_model(path)
            except Exception as e:
                print(f"[learned] could not load recipe model ({e}) -> falling back to rules.",
                      file=sys.stderr)
        else:
            print("[learned] no recipe model given -> using rules until one is trained.",
                  file=sys.stderr)
        return LearnedDecider(model, RulesDecider())

    if cfg.decider != "llm":
        return RulesDecider()

    if not consented(cfg):
        print(f"[cloud disabled] {DISCLOSURE}\n  -> falling back to local rules.", file=sys.stderr)
        return RulesDecider()
    key = _api_key(cfg)
    if not key:
        print("[cloud] no API key (--api-key or ANTHROPIC_API_KEY) -> falling back to rules.",
              file=sys.stderr)
        return RulesDecider()

    print(f"[cloud ENABLED] {DISCLOSURE}", file=sys.stderr)
    from .llm_decider import LlmDecider

    return LlmDecider(
        mode=cfg.llm_mode,
        model=cfg.llm_model,
        temperature=cfg.llm_temperature,
        max_calls=cfg.llm_max_calls,
        budget_usd=cfg.llm_budget_usd,
        api_key=key,
    )
