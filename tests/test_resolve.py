"""Mask resolution: priority arbitration (no double-edit) and absence gating."""

import numpy as np

from autoedit.concepts import ConceptMeta
from autoedit.segment.base import RegionMask
from autoedit.segment.resolve import resolve


def _meta(name, priority, min_cov=0.0):
    return ConceptMeta(name=name, priority=priority, min_score=0.0, min_coverage=min_cov, rule_region=name)


def test_priority_claims_overlap():
    a = np.zeros((10, 10), bool); a[:, 0:7] = True   # left, low priority
    b = np.zeros((10, 10), bool); b[:, 4:10] = True  # right, high priority (overlap cols 4-6)
    raw = [RegionMask("A", a, score=1.0, coverage=a.mean()),
           RegionMask("B", b, score=1.0, coverage=b.mean())]
    meta = {"A": _meta("A", 50), "B": _meta("B", 90)}

    res = resolve(raw, ["A", "B"], meta, (10, 10))
    by = {r.name: r.mask for r in res.regions}

    # higher-priority B keeps the overlap; A loses it; no pixel in both
    assert by["B"][:, 4:7].all()
    assert not by["A"][:, 4:7].any()
    assert not (by["A"] & by["B"]).any()
    # two non-overlapping regions; A∪B covers the frame so there's no background label here
    assert len(res.regions) == 2
    assert set(np.unique(res.label_map)) == {1, 2}


def test_absence_gating():
    tiny = np.zeros((10, 10), bool); tiny[0, 0] = True  # 1% coverage
    raw = [RegionMask("dress", tiny, score=1.0, coverage=tiny.mean())]
    meta = {"dress": _meta("dress", 70, min_cov=0.5)}  # require 50% -> absent

    res = resolve(raw, ["dress"], meta, (10, 10))
    assert res.regions == []
    assert "dress" not in res.union
    assert any(d.name == "dress" and not d.present for d in res.detected)
