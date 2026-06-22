"""Shoot-level consistency (Codex review F5): brightness clustering + bounded exposure anchor."""

import numpy as np
from PIL import Image as PILImage

from autoedit import batch
from autoedit.consistency import exposure_bias, plan_groups, quick_lum
from autoedit.contract import RunConfig


def test_plan_groups_splits_on_lighting_gap():
    lums = [("a", 0.20), ("b", 0.22), ("c", 0.70), ("d", 0.72)]  # two clusters
    g = plan_groups(lums, gap=0.08)
    assert g["a"][0] == g["b"][0]            # dark pair share a group
    assert g["c"][0] == g["d"][0]            # bright pair share a group
    assert g["a"][0] != g["c"][0]            # different groups
    assert abs(g["a"][1] - 0.21) < 0.02      # group target ~ median brightness


def test_exposure_bias_is_bounded_and_directional():
    assert exposure_bias(0.30, 0.50) > 0     # darker-than-target → brighten
    assert exposure_bias(0.80, 0.50) < 0     # brighter-than-target → darken
    assert abs(exposure_bias(0.0, 1.0)) <= 0.15  # bounded


def test_batch_emits_groups_and_normalizes(tmp_path):
    # three dark + one bright frame → at least two groups; sidecars carry batch_group
    corpus = tmp_path / "shoot"
    corpus.mkdir()
    for i, lvl in enumerate([40, 45, 50, 210]):
        a = np.full((48, 48, 3), lvl, np.uint8)
        PILImage.fromarray(a, "RGB").save(str(corpus / f"img{i}.jpg"))
    out = tmp_path / "out"
    manifest = batch.run(RunConfig(backend="stub"), str(corpus), str(out), do_apply=True)
    assert manifest["batch_groups"]                      # groups recorded
    assert len(manifest["batch_groups"]) >= 2            # dark cluster vs the bright outlier

    import json
    sidecars = list(out.glob("*.autoedit.json"))
    groups = {json.load(open(s))["batch_group"] for s in sidecars}
    assert "" not in groups and len(groups) >= 2         # every image got a real group id
