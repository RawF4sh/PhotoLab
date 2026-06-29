"""M5 region-aware reference matching: per-concept feature extraction + dimension-independent
region nudges toward the reference's same-concept look (bounded)."""

import numpy as np

from autoedit.edit.reference import (extract_concept_features, reference_features,
                                     region_settings_from_features, _REGION_MATCH_BOUNDS)


def _gray(v, warm=0.0):
    a = np.zeros((40, 60, 3), np.float32)
    a[:] = [v + warm, v, v - warm]
    return np.clip(a, 0, 1)


def test_extract_concept_features_per_region():
    rgb = np.zeros((40, 60, 3), np.float32)
    rgb[:, :30] = 0.3            # left = sky
    rgb[:, 30:] = 0.6            # right = water
    masks = {"sky": np.zeros((40, 60), bool), "water": np.zeros((40, 60), bool)}
    masks["sky"][:, :30] = True
    masks["water"][:, 30:] = True
    feats = extract_concept_features(rgb, masks)
    assert set(feats) == {"sky", "water"}
    assert feats["water"]["p50"] > feats["sky"]["p50"]      # right region is brighter


def test_match_pushes_toward_reference_direction():
    src = {"sky": reference_features(_gray(0.3))}            # dark, neutral
    ref = {"sky": reference_features(_gray(0.6, warm=0.12))}  # brighter, warmer
    tuned, rep = region_settings_from_features({}, src, ref, strength=1.0)
    assert "sky" in tuned
    assert tuned["sky"]["exposure"] > 0                      # reference brighter → raise exposure
    assert tuned["sky"]["temp"] > 0                          # reference warmer → warm it
    assert any(r["name"] == "sky" for r in rep)


def test_only_shared_concepts_matched_and_bounded():
    src = {"sky": reference_features(_gray(0.05))}           # extreme: very dark
    ref = {"sky": reference_features(_gray(0.95)),           # extreme: very bright
           "snow": reference_features(_gray(0.8))}           # not in source → ignored
    tuned, _ = region_settings_from_features({}, src, ref, strength=1.0)
    assert "snow" not in tuned                               # concept absent in source
    assert tuned["sky"]["exposure"] <= _REGION_MATCH_BOUNDS["exposure"][1] + 1e-6   # clamped


def test_zero_strength_is_noop():
    src = {"sky": reference_features(_gray(0.3))}
    ref = {"sky": reference_features(_gray(0.7))}
    tuned, rep = region_settings_from_features({"sky": {"exposure": 0.1}}, src, ref, strength=0.0)
    assert tuned == {"sky": {"exposure": 0.1}} and rep == []
