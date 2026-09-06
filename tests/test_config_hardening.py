"""Config-layer hardening: chroma cap lives in the block the engine reads, quality-gate
thresholds are data, feather fractions are clamped, and RunConfig validates + never leaks
the API key."""

import os
import tomllib

import numpy as np
import pytest

from autoedit.contract import GlobalStatsModel, RunConfig
from autoedit.edit import gates

_RULES = os.path.join(os.path.dirname(__file__), "..", "src", "autoedit", "configs", "rules.toml")


def test_max_chroma_increase_lives_in_guard_block():
    # It was physically inside [banding], so the engine silently used the default and the
    # documented "set to off" knob was dead. Pin the correct placement.
    d = tomllib.load(open(_RULES, "rb"))
    assert "max_chroma_increase" in d.get("guard", {})
    assert "max_chroma_increase" not in d.get("banding", {})


def test_quality_gate_thresholds_are_overridable():
    pre = GlobalStatsModel(mean_lum=0.5, mean_sat=0.2, shadow_clip=0.0, highlight_clip=0.0, haze=0.0)
    post = GlobalStatsModel(mean_lum=0.5, mean_sat=0.2, shadow_clip=0.0, highlight_clip=0.04, haze=0.0)
    _, needs_default, _, warn_default = gates.assess(pre, post)
    assert warn_default and "highlight" in warn_default[0]      # 4% clip > 2% default → warned
    score, needs_loose, _, warn_loose = gates.assess(pre, post, thr={"highlight_clip": 0.10,
                                                                  "review_highlight": 0.20,
                                                                  "clip_budget": 0.10})
    assert warn_loose == [] and not needs_loose and score == 1.0  # loosened via data, no code change


def test_chroma_guard_feathered_protect_is_graduated():
    src = np.full((1, 3, 3), 0.5, np.float32)
    edited = src.copy()
    edited[..., 0] += 0.4                                        # strong chroma push everywhere
    protect = np.array([[0.0, 0.5, 1.0]], np.float32)            # feathered mask across 3 px
    out, _ = gates.chroma_guard(src, edited, max_increase=0.6, protect=protect)
    chroma = out.max(axis=-1) - out.min(axis=-1)
    assert chroma[0, 0] > chroma[0, 1] > chroma[0, 2]            # tightens smoothly with alpha


def test_feather_fraction_is_clamped(tmp_path):
    from autoedit.concepts import load_concepts

    p = tmp_path / "concepts.toml"
    p.write_text('[concept.sky]\nfeather_px = 12\n')             # "pixels" mistake → frame-sized blur
    _, meta = load_concepts(str(p))
    assert meta["sky"].feather_px <= 0.05


def test_runconfig_never_serializes_api_key():
    import json

    cfg = RunConfig(api_key="sk-secret", allow_cloud=True)
    assert cfg.api_key == "sk-secret"                            # still readable in-process
    blob = json.dumps(cfg.model_dump()) + cfg.model_dump_json() + repr(cfg)
    assert "sk-secret" not in blob and "api_key" not in blob


def test_compose_rejects_unknown_shape(tmp_path):
    from autoedit.compose import load_compose

    p = tmp_path / "compose.toml"
    p.write_text('[vig]\nwhen = "always"\nshape = "radiaal"\n')   # typo must fail loudly,
    with pytest.raises(ValueError):                               # not silently become linear
        load_compose(str(p))
    p.write_text('[grad]\nwhen = "always"\nshape = "linear"\nfrom = "diagonal"\n')
    with pytest.raises(ValueError):
        load_compose(str(p))


def test_batch_no_consistent_skips_grouping(tmp_path):
    import numpy as np
    from PIL import Image as PILImage

    from autoedit import batch
    from autoedit.contract import RunConfig

    corpus = tmp_path / "in"; corpus.mkdir()
    for name, base in (("dark.jpg", 40), ("bright.jpg", 200)):
        a = np.full((48, 48, 3), base, np.uint8); a[:24] = [100, 140, 200]
        PILImage.fromarray(a, "RGB").save(str(corpus / name))
    m = batch.run(RunConfig(backend="stub"), str(corpus), str(tmp_path / "o"), consistent=False)
    assert m["batch_groups"] == {}                                # pass 1 skipped entirely


def test_runconfig_rejects_invalid_enums_and_ranges():
    with pytest.raises(ValueError):
        RunConfig(decider="skynet")
    with pytest.raises(ValueError):
        RunConfig(out_format="bmp")
    with pytest.raises(ValueError):
        RunConfig(quality=0)
    with pytest.raises(ValueError):
        RunConfig(intensity=-1.0)
