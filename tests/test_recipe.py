"""M6 learned recipe predictor: stats→settings train/predict round-trip, bounded output, dataset
training, and the learned decider's safe fallback to rules."""

import os
import tempfile
from types import SimpleNamespace

import numpy as np
from PIL import Image

from autoedit import recipe
from autoedit.decide.learned_decider import LearnedDecider
from autoedit.edit.ops import OP_BOUNDS


def _stats(**kw):
    base = dict(mean_lum=0.5, mean_sat=0.1, shadow_clip=0.0, highlight_clip=0.0, haze=0.3,
                lum_p10=0.2, lum_p50=0.5, lum_p90=0.8, mean_chroma=0.04, warmth=0.0)
    base.update(kw)
    return SimpleNamespace(**base)


def test_features_from_stats():
    f = recipe.features_from_stats(_stats(mean_lum=0.42, warmth=0.05))
    assert set(f) == set(recipe.RECIPE_FEATURES)
    assert f["mean_lum"] == 0.42 and f["warmth"] == 0.05


def test_train_predict_learns_a_relationship():
    # dark images want +exposure, bright want -exposure → a learnable monotone signal
    samples = []
    for lum in np.linspace(0.2, 0.8, 14):
        feats = recipe.features_from_stats(_stats(mean_lum=lum, lum_p50=lum))
        settings = {"exposure": float((0.5 - lum) * 1.2), "contrast": 6.0}
        samples.append((feats, settings))
    model = recipe.train(samples, ridge=0.05)
    dark = recipe.predict(recipe.features_from_stats(_stats(mean_lum=0.25, lum_p50=0.25)), model)
    bright = recipe.predict(recipe.features_from_stats(_stats(mean_lum=0.75, lum_p50=0.75)), model)
    assert dark["exposure"] > bright["exposure"]          # learned the direction
    assert abs(dark.get("contrast", 0) - 6.0) < 2.0       # learned the ~constant contrast


def test_predict_is_bounded():
    samples = [(recipe.features_from_stats(_stats(mean_lum=0.5)), {"exposure": 99.0, "temp": 999.0})]
    model = recipe.train(samples, ridge=0.0)
    out = recipe.predict(recipe.features_from_stats(_stats(mean_lum=0.5)), model)
    assert out["exposure"] <= OP_BOUNDS["exposure"][1]
    assert out["temp"] <= OP_BOUNDS["temp"][1]


def test_save_load_roundtrip():
    samples = [(recipe.features_from_stats(_stats(mean_lum=l)), {"exposure": l}) for l in (0.3, 0.6)]
    model = recipe.train(samples)
    d = tempfile.mkdtemp()
    p = os.path.join(d, "recipe.json")
    recipe.save_model(model, p)
    loaded = recipe.load_model(p)
    f = recipe.features_from_stats(_stats(mean_lum=0.45))
    assert recipe.predict(f, model) == recipe.predict(f, loaded)


def test_learned_decider_falls_back_without_model():
    calls = {"n": 0}

    class FakeRules:
        name = "rules"
        def decide(self, ctx):
            calls["n"] += 1
            return {"exposure": 0.1}, {"sky": {"contrast": 5}}

    dec = LearnedDecider(model=None, fallback=FakeRules())
    g, regions = dec.decide(SimpleNamespace(gstats=_stats(), looks={}, look="natural", intensity=0.8))
    assert g == {"exposure": 0.1} and regions == {"sky": {"contrast": 5}}
    assert calls["n"] == 1                                 # used the rules fallback


def test_train_from_dataset(tmp_path):
    from autoedit.edit.ops import apply_ops
    ds = tmp_path / "ds"
    od, td = ds / "originals", ds / "targets"
    od.mkdir(parents=True); td.mkdir(parents=True)
    rng = np.random.default_rng(0)
    for i in range(4):
        a = np.clip(np.linspace(0.2, 0.7, 120)[None, :, None].repeat(90, 0).repeat(3, 2)
                    + rng.uniform(-0.05, 0.05, (90, 120, 3)), 0, 1).astype(np.float32)
        Image.fromarray((a * 255).astype("uint8"), "RGB").save(od / f"p{i}.jpg")
        Image.fromarray((np.clip(apply_ops(a, {"exposure": -0.2, "contrast": 8}), 0, 1) * 255).astype("uint8"), "RGB").save(td / f"p{i}.jpg")
    info = recipe.train_from_dataset(str(ds), str(tmp_path / "m.json"))
    assert info["samples"] == 4 and os.path.isfile(info["model_path"])
