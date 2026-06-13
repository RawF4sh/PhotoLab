"""Decision layer — rules baseline and optional LLM refine, behind one protocol."""

from .base import DecisionContext, Decider
from .rules_decider import RulesDecider

__all__ = ["DecisionContext", "Decider", "RulesDecider", "make_decider"]


def make_decider(cfg) -> Decider:
    """Build a decider from a RunConfig. `llm` falls back to rules if unavailable."""
    if cfg.decider == "llm":
        from .llm_decider import LlmDecider

        return LlmDecider(
            mode=cfg.llm_mode,
            model=cfg.llm_model,
            temperature=cfg.llm_temperature,
            max_calls=cfg.llm_max_calls,
            budget_usd=cfg.llm_budget_usd,
        )
    return RulesDecider()
