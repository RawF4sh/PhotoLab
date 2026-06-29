"""M7 variant engine: per-variant scoring (quality + review penalty), uncertainty rule, specs."""

from types import SimpleNamespace

import numpy as np

from autoedit import variants


def _rgb():
    return np.zeros((4, 4, 3), np.float32)


def test_score_variant_without_model_is_quality():
    r = SimpleNamespace(quality_score=0.9, needs_review=False)
    s = variants.score_variant(r, _rgb(), None)
    assert s["score"] == 0.9 and s["aesthetic"] is None and s["needs_review"] is False


def test_review_flag_penalizes_score():
    r = SimpleNamespace(quality_score=0.9, needs_review=True)
    s = variants.score_variant(r, _rgb(), None)
    assert s["needs_review"] is True
    assert s["score"] == round(0.9 - 0.15, 4)


def test_is_uncertain_rules():
    assert variants.is_uncertain([]) is False
    assert variants.is_uncertain([{"score": 0.9, "needs_review": True}]) is True
    # top two within the margin → toss-up → review
    assert variants.is_uncertain([{"score": 0.90, "needs_review": False},
                                  {"score": 0.88, "needs_review": False}]) is True
    # a clear winner → no review
    assert variants.is_uncertain([{"score": 0.90, "needs_review": False},
                                  {"score": 0.70, "needs_review": False}]) is False


def test_specs():
    assert len(variants.resolve_specs(None)) == len(variants.DEFAULT_VARIANTS)
    custom = variants.resolve_specs([{"style": "natural"}])         # missing name → auto-named
    assert custom[0]["name"] and variants.spec_opts(custom[0]) == {"style": "natural"}
    assert "name" not in variants.spec_opts({"name": "X", "style": "dramatic"})
