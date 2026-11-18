"""Point and tell (F37, T4): the walk-through as data (lessons.py, configs/lessons.toml)."""

import json
import os

import numpy as np
from PIL import Image as PILImage

from autoedit import instructions, lessons
from autoedit.concepts import load_concepts
from autoedit.contract import Adjustment, RunConfig
from autoedit.edit import ops
from autoedit.paths import config_path, configs_dir
from autoedit.pipeline import Pipeline
from autoedit.segment.base import RegionMask, mark_name

SKIN_MAX = load_concepts(os.path.join(configs_dir(), "concepts.toml"))[1]["skin"].max_ops
PURPLE = {"tint": -90.0, "temp": -10.0}
FOUR = ("name", "does", "when", "watch")


def _vocab():
    return instructions.load_vocab(os.path.join(configs_dir(), "instructions.toml"))


def _rect(r0, r1, c0, c1, n=128):
    m = np.zeros((n, n), bool)
    m[r0:r1, c0:c1] = True
    return m


class _Masks:
    name = "masks"
    open_vocabulary = True

    def __init__(self, masks, score=0.9):
        self.masks, self.score = masks, score

    def segment(self, img, concepts):
        return [RegionMask(name=c, mask=self.masks[c], score=self.score, coverage=float(self.masks[c].mean()))
                for c in concepts if c in self.masks]


def _textured(tmp_path):
    path = tmp_path / "n.png"
    a = np.random.default_rng(3).integers(60, 190, (128, 128, 3), dtype=np.uint8)
    PILImage.fromarray(a, "RGB").save(str(path))
    return str(path)


def _make(path):
    a = np.zeros((128, 128, 3), np.uint8)
    a[:70] = [100, 140, 200]
    a[70:] = [60, 110, 70]
    yy, xx = np.mgrid[0:128, 0:128]
    a[(((xx - 64) / 28) ** 2 + ((yy - 80) / 36) ** 2) <= 1.0] = [200, 150, 120]
    PILImage.fromarray(a, "RGB").save(path, quality=95)


def _scene(tmp_path):
    path = tmp_path / "scene.png"
    _make(str(tmp_path / "flat.jpg"))
    a = np.asarray(PILImage.open(tmp_path / "flat.jpg").convert("RGB")).astype(np.int16)
    a += np.random.default_rng(7).integers(-3, 4, a.shape, dtype=np.int16)
    PILImage.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB").save(str(path))
    return str(path)


def _packaged():
    return lessons.load(config_path("lessons.toml", packaged_fallback=True))


def _walk(pipe, path, adjs):
    seen = {}
    pipe.process_one(path, adjustments=adjs, on_plan=lambda *a: seen.update(args=a))
    a, plan, before, after = seen["args"]
    return lessons.walk(a, plan, before, after, _vocab(), _packaged()), plan, before, after


def test_every_op_has_a_lesson(tmp_path):
    les = _packaged()
    assert set(les["op"]) == set(ops.OP_NAMES)
    keys = {"mask": ["thing", "area", "named", "place", "photo"], "guard": ["policy", "limit", "gate"],
            "step": ["source", "regions", "shapes", "palette", "finish"],
            "check": ["blown", "crushed", "channel", "banding", "colour"]}
    for table, want in keys.items():
        assert set(les[table]) == set(want), table
    for table in ("op", *keys):
        for k, entry in les[table].items():
            assert all(str(entry.get(f, "")).strip() for f in FOUR), (table, k)
    assert lessons.load(str(tmp_path / "nope.toml")) == {t: {} for t in lessons.TABLES}


def test_walk_matches_the_render(tmp_path):
    adj = Adjustment(target=mark_name("thing", (0.3, 0.3, 0.8, 0.8), "guy"), ops=dict(PURPLE),
                     source="x", scope="photo")
    masks = {"sky": _rect(0, 30, 0, 128), adj.target: _rect(40, 100, 40, 100), "skin": _rect(40, 52, 40, 100)}
    pipe = Pipeline.build(RunConfig(backend="stub", concepts=["sky"]))
    pipe.segmenter = _Masks(masks)
    entries, plan, before, after = _walk(pipe, _textured(tmp_path), [adj])
    assert json.loads(json.dumps(entries)) == entries
    (e,) = entries
    assert e["mask"]["kind"] == "thing" and e["mask"]["word"] == "guy" and e["mask"]["found"]
    assert abs(e["mask"]["coverage"] - masks[adj.target].mean()) < 1e-9
    assert e["status"] == "clamped" and e["sources"] == ["x"]
    assert abs(sum(p["share"] for p in e["parts"]) - 1) < 1e-9
    shares = {p["label"]: p["share"] for p in e["parts"]}
    assert abs(shares["skin"] - 0.2) < 1e-9
    skin_cell, rest_cell = f"{adj.target}/skin", adj.target
    labels = [p["label"] for p in e["parts"]]
    assert [t["op"] for t in e["tools"]] == list(PURPLE)
    for t in e["tools"]:
        op = t["op"]
        assert t["asked"] == PURPLE[op] and all(t.get(k) for k in FOUR)
        for item in t["applied"]:
            cell = skin_cell if item["part"] == "skin" else rest_cell
            want = after[cell].get(op, 0.0) - (before[cell].get(op, 0.0) - PURPLE[op])
            assert abs(item["value"] - want) < 1e-6, (op, item)
        by = {i["part"]: i for i in t["applied"]}
        assert [i["part"] for i in t["applied"]] == labels
        assert by["skin"]["state"] == "held" and abs(by["skin"]["value"] - -SKIN_MAX[op]) < 1e-6
        assert by["the rest of it"]["state"] == "full"
    assert any("skin" in h for h in e["held"])
    kinds = [g["kind"] for g in e["guards"]]
    assert "policy" in kinds and kinds[-1] == "gate"


def test_area_mark_walk_splits_at_the_skin(tmp_path):
    area = mark_name("area", (0.3, 0.35, 0.7, 0.9))
    adj = Adjustment(target=area, ops=dict(PURPLE), scope="photo")
    pipe = Pipeline.build(RunConfig(backend="stub"))
    (e,), _p, _b, _a = _walk(pipe, _scene(tmp_path), [adj])
    assert e["mask"]["kind"] == "area" and e["label"] == "the marked area"
    assert len(e["parts"]) == 2 and abs(sum(p["share"] for p in e["parts"]) - 1) < 1e-9
    assert e["parts"][0]["label"] == "skin" and e["parts"][1]["label"] == "the rest of it"
    tint = next(t for t in e["tools"] if t["op"] == "tint")
    first, second = tint["applied"]
    assert first["state"] == "held" and abs(first["value"] - -SKIN_MAX["tint"]) < 1e-6
    assert second["state"] == "full" and second["value"] == -90.0
    assert e["status"] == "clamped"


def test_a_thing_that_is_not_found(tmp_path):
    adj = Adjustment(target=mark_name("thing", (0.3, 0.3, 0.8, 0.8), "guy"), ops={"exposure": 0.3},
                     source="x", scope="photo")
    pipe = Pipeline.build(RunConfig(backend="stub"))
    (e,), _p, _b, _a = _walk(pipe, _textured(tmp_path), [adj])
    sentence = "No guy was found in the oval."
    assert e["mask"]["found"] is False and e["mask"]["why"] == sentence and e["mask"]["line"] == sentence
    assert e["parts"] == [] and e["held"] == [] and e["status"] == "not_found"
    assert e["tools"][0]["applied"] == []
