"""Decider protocol + the context bundle both deciders consume."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from ..contract import GlobalStatsModel, RegionStatsModel, Settings
from ..looks import LookSet
from ..rules import RuleSet


@dataclass
class DecisionContext:
    scene: str
    gstats: GlobalStatsModel
    region_stats: dict[str, RegionStatsModel]
    region_rule: dict[str, str]  # concept -> rule-block name
    intensity: float
    rules: RuleSet
    looks: LookSet
    look: str = "natural"
    brief: str | None = None  # style brief + user --prompt, for the LLM path
    thumb: np.ndarray | None = None  # small RGB proxy for the LLM path
    extra: dict = field(default_factory=dict)


class Decider(Protocol):
    name: str

    def decide(self, ctx: DecisionContext) -> tuple[Settings, dict[str, Settings]]:
        """Return (global_settings, {concept_name: settings})."""
        ...
