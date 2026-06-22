"""Region policies — hard rails drop forbidden ops and clamp magnitudes after planning."""

from autoedit.concepts import ConceptMeta, load_concepts
from autoedit.paths import config_path
from autoedit.policies import enforce


def _meta(**kw):
    return {kw["name"]: ConceptMeta(**kw)}


def test_forbidden_op_dropped():
    meta = _meta(name="snow", forbidden_ops=("temp", "saturation"))
    settings = {"snow": {"temp": -8.0, "exposure": 0.1, "saturation": 12.0}}
    notes = enforce(settings, meta)
    assert "temp" not in settings["snow"] and "saturation" not in settings["snow"]
    assert settings["snow"]["exposure"] == 0.1            # allowed op untouched
    assert any("dropped temp" in n for n in notes)


def test_max_clamps_both_signs():
    meta = _meta(name="foliage", max_ops={"saturation": 8.0})
    settings = {"foliage": {"saturation": 30.0}, "x": {"saturation": -50.0}}
    meta["x"] = ConceptMeta(name="x", max_ops={"saturation": 8.0})
    enforce(settings, meta)
    assert settings["foliage"]["saturation"] == 8.0
    assert settings["x"]["saturation"] == -8.0            # negative clamped too


def test_within_cap_is_untouched():
    meta = _meta(name="sky", max_ops={"saturation": 12.0}, forbidden_ops=("hsl_green_sat",))
    settings = {"sky": {"saturation": 6.0}}
    notes = enforce(settings, meta)
    assert settings["sky"]["saturation"] == 6.0 and notes == []


def test_unknown_region_passes_through():
    settings = {"mystery": {"saturation": 99.0}}
    assert enforce(settings, {}) == [] and settings["mystery"]["saturation"] == 99.0


def test_shipped_concepts_load_policies():
    _, meta = load_concepts(config_path("concepts.toml"))
    # snow must forbid warming and all colour ops; sky must forbid a green drift
    assert "temp" in meta["snow"].forbidden_ops
    assert "hsl_green_sat" in meta["sky"].forbidden_ops
    assert meta["foliage"].max_ops.get("saturation") == 8.0
