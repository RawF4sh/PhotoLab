"""Rules evaluator: helpers, the sandbox, and apply()."""

import numpy as np
import pytest

from autoedit.contract import GlobalStatsModel, RegionStatsModel
from autoedit.rules import apply, eval_expr


def test_eval_helpers():
    assert eval_expr("clamp(5, 0, 3)", {}) == 3.0
    assert eval_expr("if(1 > 0, 2, 9)", {}) == 2.0
    assert eval_expr("max(min(0.7, 1), 0)", {}) == 0.7


def test_eval_reads_context():
    assert eval_expr("mean_lum * 2", {"mean_lum": 0.25}) == 0.5


def test_sandbox_blocks_attribute_access():
    with pytest.raises(Exception):
        eval_expr("(1).__class__", {})


def test_sandbox_blocks_unknown_call():
    with pytest.raises(Exception):
        eval_expr("__import__('os')", {})


def test_apply_lifts_buried_skin():
    rules = {"people": {
        "global": {"vibrance": "6"},
        "skin": {"exposure": "clamp(0.5 * max(0, mean_lum - region_lum), 0, 0.5)"},
    }}
    g = GlobalStatsModel(mean_lum=0.6, mean_sat=0.2, shadow_clip=0.0, highlight_clip=0.0, haze=0.0)
    region_stats = {"skin": RegionStatsModel(name="skin", lum=0.2, sat=0.3, clip_low=0, clip_high=0, coverage=0.1)}
    gset, rset = apply(rules, "people", g, region_stats, {"skin": "skin"}, intensity=1.0)
    assert gset["vibrance"] == 6.0
    # mean_lum 0.6, region 0.2 -> 0.5*0.4 = 0.2 lift
    assert abs(rset["skin"]["exposure"] - 0.2) < 1e-6
