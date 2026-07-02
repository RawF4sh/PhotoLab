"""LLM decider merge safety: bounded deltas, absolute clamps, no invented regions, no unknown
ops. This is the layer that keeps a misbehaving model output inside the editing contract —
tested directly on _merge (no client / network involved)."""

from autoedit.decide.llm_decider import LlmDecider


def _decider(mode="refine"):
    return LlmDecider(mode=mode)


def test_refine_deltas_are_bounded():
    d = _decider()
    base_g = {"exposure": 0.2, "contrast": 10.0}
    g, regions = d._merge(base_g, {"sky": {"contrast": 5.0}},
                          {"global": {"exposure": 99.0, "contrast": -500.0}, "regions": {}})
    assert g["exposure"] == 0.2 + 0.5          # delta clamped to ±0.5 EV off the baseline
    assert g["contrast"] == 10.0 - 25.0        # delta clamped to ±25 (default)
    assert regions["sky"] == {"contrast": 5.0}  # untouched


def test_absolute_bounds_still_apply_after_delta():
    d = _decider()
    g, _ = d._merge({"exposure": 2.8}, {}, {"global": {"exposure": 0.5}, "regions": {}})
    assert g["exposure"] == 3.0                # 2.8 + 0.5 → clamped to OP_BOUNDS (±3 EV)


def test_unknown_region_is_never_invented():
    d = _decider()
    _, regions = d._merge({}, {"sky": {}},
                          {"global": {}, "regions": {"dragon": {"exposure": 1.0}}})
    assert "dragon" not in regions


def test_unknown_ops_and_non_numeric_values_are_dropped():
    d = _decider()
    g, _ = d._merge({}, {}, {"global": {"sky_replace": 50.0, "exposure": "bright", "contrast": 5.0},
                             "regions": {}})
    assert "sky_replace" not in g              # hallucinated op never reaches settings/sidecar
    assert "exposure" not in g                 # non-numeric skipped
    assert g["contrast"] == 5.0


def test_propose_mode_uses_absolute_clamp_only():
    d = _decider(mode="propose")
    g, _ = d._merge({"contrast": 10.0}, {}, {"global": {"contrast": 250.0}, "regions": {}})
    assert g["contrast"] == 100.0              # absolute OP_BOUNDS clamp, no baseline delta
