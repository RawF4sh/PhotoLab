"""Point and tell (F37): the name of a mark, the reader bound to a mark, the oval of an area, a
thing mark in the segmenters and the caches, and marks in the plan and the pipeline."""

import base64
import contextlib
import io
import json
import math
import os
import time

import numpy as np
import pytest
from PIL import Image as PILImage

from autoedit import adjust, cli, instructions
from autoedit.concepts import load_concepts
from autoedit.contract import Adjustment, RunConfig
from autoedit.imageio import Image, load_image
from autoedit.paths import configs_dir
from autoedit.pipeline import Pipeline
from autoedit.segment.base import RegionMask, mark_name, parse_mark
from autoedit.segment.cache import MAX_MARKS, CachingSegmenter, DiskMaskCache
from autoedit.segment.sam3 import Sam3Segmenter
from autoedit.segment.stub import StubSegmenter
from autoedit.web import jobs, service

KNOWN = {"sky", "mountain", "grass", "skin", "subject"}
B = (0.58, 0.12, 0.92, 0.94)
THING = "mark:thing:0.5800,0.1200,0.9200,0.9400:"
AREA = "mark:area:0.5800,0.1200,0.9200,0.9400"
PURPLE = {"tint": -90.0, "temp": -10.0}


def _same(ops, want):   # the stand-alone runner's approx takes numbers only
    assert set(ops) == set(want), (ops, want)
    assert all(abs(ops[k] - want[k]) < 1e-9 for k in want), (ops, want)


def _vocab():
    return instructions.load_vocab(os.path.join(configs_dir(), "instructions.toml"))


def _m(text, kind="auto"):
    adjs, problems = instructions.parse(text, _vocab(), KNOWN, mark=(B, kind))
    return [(a.target, a.ops) for a in adjs], problems


def test_mark_name_round_trip():
    thing = "mark:thing:0.5800,0.1200,0.9200,0.9400:guy"
    area = "mark:area:0.3000,0.3000,0.7000,0.7000"
    assert mark_name("thing", B, "guy") == thing
    assert mark_name("area", (0.3, 0.3, 0.7, 0.7)) == area
    assert parse_mark(thing) == ("thing", B, "guy")
    assert parse_mark(area) == ("area", (0.3, 0.3, 0.7, 0.7), "")
    assert mark_name("thing", (0.58004, 0.12, 0.92, 0.94), "Guy ") == thing
    assert mark_name(*parse_mark("mark:area:0.3,0.3,0.7,0.7")) == area
    edge = mark_name("area", (-0.0, 0.0, 0.5, 1))       # a negative zero must not make a second spelling
    assert edge == "mark:area:0.0000,0.0000,0.5000,1.0000" and parse_mark(edge) == ("area", (0.0, 0.0, 0.5, 1.0), "")
    bad = [("thing", (0.1, 0.1, 0.5), "x"), ("area", (0.5, 0.1, 0.5, 0.9), ""), ("area", (False, 0.1, True, 0.9), ""),
           ("area", (0, 0, 10 ** 400, 1), ""),
           ("area", (0.6, 0.1, 0.5, 0.9), ""), ("area", (0.1, 0.1, 1.2, 0.9), ""),
           ("area", (0.1, 0.1, math.nan, 0.9), ""), ("area", (-0.1, 0.1, 0.5, 0.9), ""), ("blob", (0.1, 0.1, 0.5, 0.9), ""),
           ("thing", (0.1, 0.1, 0.5, 0.9), " ")]
    for kind, box, word in bad:
        with pytest.raises(ValueError):
            mark_name(kind, box, word)
    for name in ["mark:thing:0.1,0.1,0.5:x", "mark:area:0.5,0.1,0.5,0.9", "mark:area:0.1,0.1,1.2,0.9",
                 "mark:area:0.1,0.1,nan,0.9", "mark:blob:0.1,0.1,0.5,0.9", "mark:thing:0.1,0.1,0.5,0.9",
                 "mark:area:0.1,0.1,0.5,0.9:guy", "guy", "mark:", None, 5]:
        assert parse_mark(name) is None, name
    for text in ["mark thing guy", "mark:thing:guy", "make this guy more purple", "0.1,0.2,0.3,0.4"]:
        assert parse_mark(" ".join(instructions._toks(text))) is None


def test_parse_binds_the_sentence_to_the_mark():
    one = lambda text, kind="auto": _m(text, kind)[0]  # noqa: E731
    (t, o), = one("make this guy more purple")
    assert t == THING + "guy" and _same(o, PURPLE) is None
    (t, _o), = one("make him more purple")
    assert t == THING + "him"
    assert [t for t, _ in one("make this guy and his backpack more purple")] == [THING + "guy", THING + "backpack"]
    got = one("brighten and warm this jacket")
    assert [t for t, _ in got] == [THING + "jacket"] * 2
    assert _same(got[0][1], {"exposure": 0.30}) is None and _same(got[1][1], {"temp": 10.0}) is None
    assert one("brighten that tree there")[0][0] == THING + "tree"
    (t, o), = one("give him a violet tone")                 # filler words are no part of the name
    assert t == THING + "him" and _same(o, PURPLE) is None
    assert one("turn this jacket purple")[0][0] == THING + "jacket"
    assert one("brighten this face")[0][0] == THING + "face"
    for text in ["make this more purple", "more purple", "brighten this thing"]:
        assert [t for t, _ in one(text)] == [AREA], text
    (t, o), = one("darker here")
    assert t == AREA
    _same(o, {"exposure": -0.30})
    (t, o), = one("brighten this area a little")
    assert t == AREA
    _same(o, {"exposure": 0.15})
    assert [t for t, _ in one("brighten this part of the jacket")] == [AREA]
    glow = {"exposure": 0.15, "temp": 8.0}
    for text in ["add a glow here", "add a warm glow on the sun"]:
        (t, o), = one(text)
        assert t == AREA
        _same(o, glow)
    assert [t for t, _ in one("brighten this jacket", "area")] == [AREA]
    assert [t for t, _ in one("brighten and warm this part of the jacket")] == [AREA, AREA]
    assert _m("don't brighten this")[1] == ['not understood (negation or exclusion): "don\'t brighten this"']
    for text in ["darken the top", "make the whole photo warmer", "add a vignette", "brighten this and top"]:
        got, problems = _m(text)
        assert not got and problems == [
            f'not understood (the sentence names another place than the mark): "{text}"'], text
    assert _m("a bigger glow here") == (
        [], ['not understood (a mark has the size you drew): "a bigger glow here"'])
    assert _m("brighten this and make the grass warmer")[0]   # a clause about a thing is not another place


def test_parse_without_a_mark_is_as_before():
    # Recorded from the code before F37 touched it (targets, ops, problems).
    v, p = _vocab(), instructions.parse
    pur = {"tint": -90.0, "temp": -10.0}
    want = {
        "make this guy more purple": ([("guy", pur)], []),
        "make him more purple": ([("him", pur)], []),
        "make this guy and his backpack more purple": ([("guy", pur), ("backpack", pur)], []),
        "brighten and warm this jacket": ([("jacket", {"exposure": 0.3}), ("jacket", {"temp": 10.0})], []),
        "brighten that tree there": ([("tree there", {"exposure": 0.3})], []),
        "brighten this face": ([("face", {"exposure": 0.3})], []),
        "make this more purple": ([], ['not understood (no target): "make this more purple"']),
        "more purple": ([("global", pur)], []),
        "brighten this thing": ([("thing", {"exposure": 0.3})], []),
        "darker here": ([("here", {"exposure": -0.3})], []),
        "brighten this area a little": ([], ['not understood (no target): "brighten this area a little"']),
        "brighten this part of the jacket": ([("jacket", {"exposure": 0.3})], []),
        "add a glow here": ([("here", {"exposure": 0.15, "temp": 8.0})], []),
        "add a warm glow on the sun": ([("place:light", {"exposure": 0.15, "temp": 8.0})], []),
        "brighten this jacket": ([("jacket", {"exposure": 0.3})], []),
        "don't brighten this": ([], ['not understood (negation or exclusion): "don\'t brighten this"']),
        "darken the top": ([("place:top", {"exposure": -0.3})], []),
        "make the whole photo warmer": ([("global", {"temp": 10.0})], []),
        "add a vignette": ([("place:edges", {"exposure": -0.25})], []),
        "a bigger glow here": ([], ['not understood (size only applies to a gradient, an oval or a glow): '
                                    '"a bigger glow here"']),
    }
    for text, (adjs, problems) in want.items():
        got, got_problems = p(text, v, KNOWN)
        assert got_problems == problems, text
        assert [a.target for a in got] == [t for t, _ in adjs], text
        for a, (_t, ops) in zip(got, adjs):
            _same(a.ops, ops)


def test_a_model_reading_stays_on_the_mark():
    v = _vocab()
    ask = lambda target, kind="auto": instructions.to_mark(  # noqa: E731
        Adjustment(target=target, ops={"tint": -60.0}, source="give him a violet tone", via="local"),
        (B, kind), v)
    a = ask("person")
    assert a.target == THING + "person" and a.via == "local" and a.ops == {"tint": -60.0} and a.size == 1.0
    assert a.source == "give him a violet tone"
    assert ask("place:top").target == AREA and ask("global").target == AREA
    assert ask("person", "area").target == AREA
    assert ask(THING + "guy").target == THING + "guy"      # read by the word list: left as it is
    pipe = Pipeline.build(RunConfig(backend="stub"))

    class _Cloud:
        def translate(self, *a, **k):
            raise AssertionError("a mark's sentence went to the cloud")
    pipe.decider = _Cloud()
    adjs, facts = service.mark_adjustments(
        pipe, service.check_marks([{"box": list(B), "edit": "make it pop like a movie"}], pipe.vocab()))
    assert adjs == [] and facts[0]["targets"] == [] and "make it pop like a movie" in facts[0]["problems"][0]


def test_area_oval():
    spec = instructions.mark_spec((0.3, 0.3, 0.7, 0.7), 0.35)
    hw = (400, 400)
    a = instructions.place_alpha(spec, hw)
    assert a[200, 200] == pytest.approx(1.0, abs=1e-3)
    for y, x in [(200, 120), (200, 280), (120, 200), (280, 200)]:   # the drawn line at 0.2 of the frame
        assert abs(a[y, x] - 0.5) < 0.03, (y, x, a[y, x])
    assert a[200, 0] == 0 and a[0, 200] == 0 and a[200, 399] == 0
    for y, x in [(200, 297), (200, 103), (297, 200), (103, 200)]:   # just beyond 1.2 times the radius of 80
        assert a[y, x] == 0, (y, x, a[y, x])
    edge =instructions.place_alpha(instructions.mark_spec((0.0, 0.0, 0.4, 0.4), 0.35), hw)
    assert np.isfinite(edge).all() and edge[80, 80] > 0.9
    assert instructions.layer_spec(AREA, _vocab())["shape"] == "oval"
    assert instructions.layer_spec("top", _vocab())["shape"] == "gradient"


def _make(path):
    a = np.zeros((128, 128, 3), np.uint8)
    a[:70] = [100, 140, 200]   # sky
    a[70:] = [60, 110, 70]     # foliage
    yy, xx = np.mgrid[0:128, 0:128]
    a[(((xx - 64) / 28) ** 2 + ((yy - 80) / 36) ** 2) <= 1.0] = [200, 150, 120]   # skin subject
    PILImage.fromarray(a, "RGB").save(path, quality=95)


def test_without_sam3_a_mark_is_an_area(tmp_path):
    assert StubSegmenter.open_vocabulary is False and Sam3Segmenter.open_vocabulary is True
    assert CachingSegmenter(StubSegmenter()).open_vocabulary is False
    assert CachingSegmenter(Sam3Segmenter.__new__(Sam3Segmenter)).open_vocabulary is True
    path = str(tmp_path / "p.jpg")
    _make(path)
    pipe = Pipeline.build(RunConfig(backend="stub"))
    plain, _ = pipe.process_one(path)
    adj = Adjustment(target=mark_name("thing", (0.3, 0.3, 0.7, 0.7), "guy"),
                     ops={"exposure": 0.3}, scope="photo")
    marked, result = pipe.process_one(path, adjustments=[adj])
    assert [a.status for a in result.adjustments] == ["not_found"]
    assert np.array_equal(plain, marked)
    adjs, facts = service.mark_adjustments(
        pipe, service.check_marks([{"box": list(B), "edit": "make this guy more purple"}], pipe.vocab()))
    assert [a.target for a in adjs] == [AREA] and facts[0]["targets"] == [AREA]


class _Counting:
    name = "counting"

    def __init__(self):
        self.asked = []

    def segment(self, img, concepts):
        self.asked.append(list(concepts))
        return [RegionMask(name=c, mask=np.ones((4, 4), bool), coverage=1.0)
                for c in concepts if c != "nothing"]


def _photo():
    return Image(rgb=np.full((4, 4, 3), 0.5, np.float32), source_bits=8, path="x.jpg")


def test_caches_take_mark_names(tmp_path):
    thing = mark_name("thing", B, "guy")
    disk_inner = _Counting()
    disk = DiskMaskCache(disk_inner, str(tmp_path))
    assert [r.name for r in disk.segment(_photo(), [thing])] == [thing]
    assert [r.name for r in disk.segment(_photo(), [thing])] == [thing]
    assert disk_inner.asked == [[thing]]

    inner = _Counting()
    cache = CachingSegmenter(inner)
    names = [mark_name("thing", (0.0, 0.0, 0.1 + i / 1000, 0.5), "guy") for i in range(MAX_MARKS + 1)]
    cache.segment(_photo(), ["sky", "nothing"])          # not marks: never dropped
    for n in names:
        assert [r.name for r in cache.segment(_photo(), [n])] == [n]
    inner.asked.clear()
    cache.segment(_photo(), [names[-1], names[1], "sky", "nothing"])
    assert inner.asked == []                              # the 65th, the 2nd, and the plain names stay
    cache.segment(_photo(), [names[0]])
    assert inner.asked == [[names[0]]]                    # the first was dropped


# ── marks in the plan and the pipeline (T3) ─────────────────────────────────────────────────

SKIN_MAX = load_concepts(os.path.join(configs_dir(), "concepts.toml"))[1]["skin"].max_ops
PURPLE_OPS = {"tint": -90.0, "temp": -10.0}


def _rect(r0, r1, c0, c1, n=128):
    m = np.zeros((n, n), bool)
    m[r0:r1, c0:c1] = True
    return m


class _Masks:
    """A segmenter that returns the given masks by name and records what it was asked."""
    name = "masks"
    open_vocabulary = True

    def __init__(self, masks, score=0.9):
        self.masks, self.score, self.asked, self.stages = masks, score, [], {}

    def segment(self, img, concepts):
        self.asked.append(list(concepts))
        return [RegionMask(name=c, mask=self.masks[c], score=self.score, coverage=float(self.masks[c].mean()))
                for c in concepts if c in self.masks]


def _textured(tmp_path, name="n.png"):
    """Noise keeps the banding gate quiet, so the edit changes only what it should."""
    path = tmp_path / name
    if not path.exists():
        a = np.random.default_rng(3).integers(60, 190, (128, 128, 3), dtype=np.uint8)
        PILImage.fromarray(a, "RGB").save(str(path))
    return str(path)


def _run(tmp_path, masks, adjs, concepts, **kw):
    """(output, result, plan, segmenter) of one photo with a fake segmenter."""
    pipe = Pipeline.build(RunConfig(backend="stub", concepts=concepts, **kw))
    pipe.segmenter = _Masks(masks)
    seen = {}
    out, result = pipe.process_one(_textured(tmp_path), adjustments=adjs,
                                   on_plan=lambda _a, plan, _b, _s: seen.update(plan=plan),
                                   on_stage=lambda name, rgb: pipe.segmenter.stages.update({name: rgb.copy()}))
    return out, result, seen.get("plan"), pipe.segmenter


def _mark(box, word, **ops):
    return Adjustment(target=mark_name("thing", box, word), ops=ops, source="x", scope="photo")


def _disjoint(cells):
    for i, c in enumerate(cells):
        for d in cells[i + 1:]:
            assert not (c.mask & d.mask).any(), (c.name, d.name)


def _grow(mask, px):
    out = mask.copy()
    for _ in range(px):
        out[1:] |= out[:-1].copy()
        out[:-1] |= out[1:].copy()
        out[:, 1:] |= out[:, :-1].copy()
        out[:, :-1] |= out[:, 1:].copy()
    return out


# A frame of 128 px: shares that round back to the pixel they were made from.
BOX = (0.25, 0.20, 0.75, 0.75)          # rows 26..96, columns 32..96
REGIONS = {"sky": _rect(0, 50, 0, 128), "subject": _rect(40, 70, 30, 100)}


def test_thing_mark_edits_no_pixel_twice(tmp_path):
    mark = _rect(30, 90, 40, 90)                 # across sky, subject and the background below
    adj = _mark(BOX, "jacket", exposure=0.5)
    target = adj.target
    _, _, plan, _ = _run(tmp_path, {**REGIONS, target: mark}, [adj], ["sky", "subject"])
    cells = {c.name: c for c in plan.cells}
    _disjoint(plan.cells)
    union = np.zeros_like(mark)
    for name in plan.hit[target]:
        union |= cells[name].mask
    assert np.array_equal(union, mark)
    assert len(plan.hit[target]) == 3            # one cell in each of sky, subject, and outside them

    _, _, _, base = _run(tmp_path, {**REGIONS, target: mark}, [], ["sky", "subject"])
    _, result, _, seg = _run(tmp_path, {**REGIONS, target: mark}, [adj], ["sky", "subject"])
    # through apply_image, the region stage alone (the global stage reads the whole frame)
    diff = np.abs(seg.stages["after_regions"] - base.stages["after_regions"]).max(axis=-1)
    assert diff[~_grow(mark, 14)].max() < 1e-4        # 14 px: the reach of the guided feather, as for a typed thing
    assert diff[mark].max() > 1e-2
    assert result.adjustments[0].status == "applied"


def test_two_marks_that_overlap(tmp_path):
    box_a, box_b = (0.2, 0.2, 0.7, 0.7), (0.4, 0.4, 0.9, 0.9)
    a_mask, b_mask = _rect(30, 80, 30, 80), _rect(50, 100, 50, 100)
    both = _rect(50, 80, 50, 80)
    a, b = _mark(box_a, "jacket", exposure=0.3), _mark(box_b, "hat", exposure=0.2, temp=5.0)
    masks = {"subject": _rect(20, 110, 20, 110), a.target: a_mask, b.target: b_mask}
    out, _, plan, _ = _run(tmp_path, masks, [a, b], ["subject"])
    _disjoint(plan.cells)
    overlap = [c for c in plan.cells if np.array_equal(c.mask, both)]
    assert len(overlap) == 1
    base = plan.before["subject"]
    got = plan.settings[overlap[0].name]
    assert abs(got["exposure"] - (base.get("exposure", 0.0) + 0.5)) < 1e-9
    assert abs(got["temp"] - (base.get("temp", 0.0) + 5.0)) < 1e-9
    assert plan.owners[overlap[0].name].count(a.target) == 1 and plan.owners[overlap[0].name].count(b.target) == 1
    # applied once: the same pixels as one mark with both changes on the overlap alone
    c = _mark((0.4, 0.4, 0.625, 0.625), "both", exposure=0.5, temp=5.0)
    one, _, _, _ = _run(tmp_path, {"subject": masks["subject"], c.target: both}, [c], ["subject"])
    assert np.abs(out - one)[62:68, 62:68].max() < 5e-4   # twice would be 0.1 and more


def test_bigger_than_the_mark(tmp_path):
    mask = _rect(20, 100, 20, 100)
    adj = _mark((20 / 128, 20 / 128, 44 / 128, 100 / 128), "sky", exposure=0.5)   # 24 of 80 columns inside
    base, _, _, _ = _run(tmp_path, {"subject": REGIONS["subject"]}, [], ["subject"])
    out, result, plan, _ = _run(tmp_path, {"subject": REGIONS["subject"], adj.target: mask}, [adj], ["subject"])
    assert plan.why == {adj.target: "bigger"} and plan.hit[adj.target] == []
    assert result.adjustments[0].status == "not_found"
    assert any("bigger than the mark" in n for n in result.notes)
    assert np.array_equal(out, base)


def test_a_small_thing_is_edited(tmp_path):
    speck = _rect(50, 52, 60, 62)                # 4 px: 0.02 % of the frame, inside the subject
    adj = _mark(BOX, "lamp", exposure=0.5)
    _, result, plan, _ = _run(tmp_path, {"subject": REGIONS["subject"], adj.target: speck}, [adj], ["subject"])
    assert result.adjustments[0].status == "applied" and plan.hit[adj.target]
    assert any(np.array_equal(c.mask, speck) for c in plan.cells)
    typed = Adjustment(target="lamp", ops={"exposure": 0.5}, source="x", scope="photo")
    _, result, plan, _ = _run(tmp_path, {"subject": REGIONS["subject"], "lamp": speck}, [typed], ["subject"])
    assert result.adjustments[0].status == "not_found" and plan.hit["lamp"] == []


def _skin_run(tmp_path, skin, word="guy"):
    adj = _mark((0.3, 0.3, 0.8, 0.8), word, **PURPLE_OPS)
    masks = {"sky": _rect(0, 30, 0, 128), adj.target: _rect(40, 100, 40, 100), "skin": skin}
    return _run(tmp_path, masks, [adj], ["sky"]) + (adj.target,)


def test_skin_inside_a_thing_mark_keeps_its_limits(tmp_path):
    _, result, plan, seg, target = _skin_run(tmp_path, _rect(40, 52, 40, 100))   # 20 % of the mark
    assert "skin" in seg.asked[0]
    part = f"{target}/skin"
    assert "skin" in plan.owners[part] and plan.owners[part][-1] == target
    assert abs(plan.inside[target]["skin"] - 0.2) < 1e-9
    ops = {m.target: m.ops for m in result.masks}
    assert ops[part]["tint"] == -SKIN_MAX["tint"] and ops[part]["temp"] == -SKIN_MAX["temp"]
    assert ops[target]["tint"] == -90.0 and ops[target]["temp"] == -10.0     # the rest: the full change
    assert result.adjustments[0].status == "clamped"
    assert "mark: skin inside it (20 % of the mark) keeps the skin limits" in result.notes
    assert "skin" not in {c.name for c in result.concepts} and "skin" not in ops   # never a region


def test_a_speck_of_skin_is_ignored(tmp_path):
    _, result, plan, seg, target = _skin_run(tmp_path, _rect(40, 41, 40, 76))     # 1 % of the mark
    assert "skin" in seg.asked[0]
    ops = {m.target: m.ops for m in result.masks}
    assert plan.inside == {} and not any("/skin" in t for t in ops)
    assert ops[target]["tint"] == -90.0 and ops[target]["temp"] == -10.0
    assert result.adjustments[0].status == "applied"


def test_snow_inside_a_mark_takes_no_colour(tmp_path):
    mark = _rect(30, 90, 40, 90)                 # rows 30..39 lie above the snow
    snow = {"snow": _rect(40, 100, 20, 110)}
    purple, brighter = _mark(BOX, "jacket", **PURPLE_OPS), _mark(BOX, "jacket", exposure=0.4)
    base, _, _, _ = _run(tmp_path, snow, [], ["snow"])
    out, result, plan, _ = _run(tmp_path, {**snow, purple.target: mark}, [purple], ["snow"])
    cell = f"{purple.target}@snow"
    live = lambda s: {op: v for op, v in s.items() if v}  # noqa: E731
    assert plan.owners[cell][0] == "snow" and live(plan.settings[cell]) == live(plan.settings["snow"])   # as the region's own
    ops = {m.target: m.ops for m in result.masks}
    assert "tint" not in ops[cell] and "temp" not in ops[cell]
    assert ops[purple.target]["tint"] == -90.0                       # above the snow: the full change
    assert np.abs(out - base)[50:80, 50:80].max() < 1e-4             # the snow part is as without the mark
    assert result.adjustments[0].status == "clamped"
    out, result, _, _ = _run(tmp_path, {**snow, brighter.target: mark}, [brighter], ["snow"])
    assert np.abs(out - base)[50:80, 50:80].max() > 1e-2 and result.adjustments[0].status == "applied"


def _scene(tmp_path):
    """The synthetic photo of test_pipeline, with a little noise so the banding gate stays quiet."""
    path = tmp_path / "scene.png"
    _make(str(tmp_path / "flat.jpg"))
    a = np.asarray(PILImage.open(tmp_path / "flat.jpg").convert("RGB")).astype(np.int16)
    a += np.random.default_rng(7).integers(-3, 4, a.shape, dtype=np.int16)
    PILImage.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB").save(str(path))
    return str(path)


def _instructions_without_guard(tmp_path):
    text = open(os.path.join(configs_dir(), "instructions.toml")).read()
    assert "\nguard = true" in text
    path = tmp_path / "instructions.toml"
    path.write_text(text.replace("\nguard = true", "\nguard = false"))
    return str(path)


def test_area_mark_holds_guarded_regions(tmp_path):
    path = _scene(tmp_path)
    box = (0.3, 0.35, 0.7, 0.9)                  # over the skin ellipse
    area = mark_name("area", box)
    layer = instructions.LAYER + area

    def run(ops, **cfg):
        pipe = Pipeline.build(RunConfig(backend="stub", **cfg))
        return pipe.process_one(path, adjustments=[Adjustment(target=area, ops=ops, scope="photo")])

    out, result = run(PURPLE_OPS)
    comp = {c.target: c.ops for c in result.composition}
    held = {"tint": -SKIN_MAX["tint"], "temp": -SKIN_MAX["temp"]}
    assert comp[layer] == PURPLE_OPS and comp[layer + "/guarded"] == held
    assert result.adjustments[0].status == "clamped"
    ref, ref_result = run(held)                                # the held change alone: one layer
    assert [c.target for c in ref_result.composition if c.target.startswith(layer)] == [layer]
    assert np.abs(out - ref)[75:90, 58:70].max() < 1e-3        # in the middle of the skin: the held change
    assert np.abs(out - ref).max() > 3e-3                      # and elsewhere in the oval: the full one
    result = run({"exposure": 0.3})[1]                         # "brighten": the policies leave it alone
    assert [c.target for c in result.composition if c.target.startswith(layer)] == [layer]
    off = _instructions_without_guard(tmp_path)                # guard = false: one layer, the full change
    result = run(PURPLE_OPS, instructions_path=off)[1]
    assert {c.target: c.ops for c in result.composition if c.target.startswith(layer)} == {layer: PURPLE_OPS}


def test_area_mark_obeys_only_the_regions_it_reaches(tmp_path):
    masks = {"skin": _rect(40, 70, 40, 90), "snow": _rect(100, 128, 0, 128)}

    def guarded(box):
        adj = Adjustment(target=mark_name("area", box), ops=PURPLE_OPS, scope="photo")
        _, result, plan, _ = _run(tmp_path, masks, [adj], ["skin", "snow"])
        comp = {c.target: c.ops for c in result.composition}
        return comp[instructions.LAYER + adj.target + "/guarded"], plan.guarded[adj.target][2]

    held, under = guarded((0.3, 0.3, 0.72, 0.56))            # over the face: the snow lies far below
    assert under == ["skin"] and held == {"tint": -SKIN_MAX["tint"], "temp": -SKIN_MAX["temp"]}
    held, under = guarded((0.3, 0.3, 0.72, 0.95))            # over both: snow takes no colour at all
    assert under == ["skin", "snow"] and held == {}


def test_no_mark_changes_nothing(tmp_path):
    masks = {**REGIONS, "skin": _rect(40, 60, 50, 80), "snow": _rect(90, 120, 0, 60)}
    concepts = ["sky", "subject"]
    first, _, _, seg = _run(tmp_path, masks, None, concepts)
    second, _, _, seg2 = _run(tmp_path, masks, [], concepts)
    assert np.array_equal(first, second)
    assert seg.asked == seg2.asked == [concepts]          # the run's concepts only
    typed = Adjustment(target="jacket", ops={"exposure": 0.3}, source="x", scope="photo")
    _, _, _, seg3 = _run(tmp_path, {**masks, "jacket": _rect(45, 60, 50, 70)}, [typed], concepts)
    assert seg3.asked == [concepts + ["jacket"]]          # a typed thing asks for no guarded concept


def test_a_mark_leaves_the_rest_alone(tmp_path):
    adj = _mark(BOX, "jacket", exposure=0.4, tint=-30.0)
    mark = _rect(45, 85, 40, 90)
    masks = {**REGIONS, "skin": _rect(45, 60, 40, 90), "snow": _rect(80, 100, 0, 128), adj.target: mark}
    concepts = ["sky", "subject"]
    base, plain, _, _ = _run(tmp_path, masks, [], concepts)
    out, result, plan, seg = _run(tmp_path, masks, [adj], concepts)
    assert set(seg.asked[0]) == {*concepts, adj.target, "skin", "snow"} and plan.inside[adj.target].keys() == {"skin", "snow"}
    assert result.scene == plain.scene and result.confidence == plain.confidence
    assert [c for c in result.concepts if c.name != adj.target] == plain.concepts
    ops, was = {m.target: m.ops for m in result.masks}, {m.target: m.ops for m in plain.masks}
    assert all(ops[t] == o for t, o in was.items()) and "skin" not in ops and "snow" not in ops
    assert result.global_settings == plain.global_settings
    assert np.abs(out - base)[100:].max() * 255 < 1.0         # the bottom rows: unchanged within 1 level
    assert np.abs(out - base).max() > 1e-2                    # and the mark did edit something


def test_typed_colour_on_skin_is_limited(tmp_path):
    path = str(tmp_path / "p.jpg")
    _make(path)
    limit = SKIN_MAX["tint"]
    _, result = Pipeline.build(RunConfig(backend="stub", edit="make the skin pinker")).process_one(path)
    skin = {m.target: m.ops for m in result.masks}["skin"]
    assert abs(skin["tint"]) == limit == 6
    assert any(n.startswith("policy[skin]: clamped tint") for n in result.notes)
    assert result.adjustments[0].status == "clamped" and result.adjustments[0].ops["tint"] == -100.0
    text = open(os.path.join(configs_dir(), "concepts.toml")).read()
    assert "temp = 6, tint = 6," in text
    loose = tmp_path / "concepts.toml"
    loose.write_text(text.replace("temp = 6, tint = 6,", "temp = 6,"))
    free = Pipeline.build(RunConfig(backend="stub", edit="make the skin pinker", concepts_path=str(loose)))
    assert {m.target: m.ops for m in free.process_one(path)[1].masks}["skin"]["tint"] == -100.0
    plain, _ = Pipeline.build(RunConfig(backend="stub")).process_one(path)
    again, _ = Pipeline.build(RunConfig(backend="stub", concepts_path=str(loose))).process_one(path)
    assert np.array_equal(plain, again)                       # no instruction: the limit changes no pixel


def _img(p, gain=1.0):
    a = np.zeros((64, 64, 3), np.float32)
    a[:32] = [100, 140, 200]
    a[32:] = [90, 110, 80]
    PILImage.fromarray(np.clip(a * gain, 0, 255).astype(np.uint8), "RGB").save(str(p), quality=95)


def _arr(url):
    raw = base64.b64decode(url.split(",", 1)[1])
    return np.asarray(PILImage.open(io.BytesIO(raw)).convert("RGB"), np.float32)


def _wait(job, timeout=30):
    t0 = time.time()
    while job.status not in ("done", "cancelled", "error") and time.time() - t0 < timeout:
        time.sleep(0.05)
    return job


STUB = {"backend": "stub", "style": "natural"}
MID = (0.3, 0.3, 0.7, 0.7)


def test_preview_takes_photo_marks(tmp_path):
    _img(tmp_path / "a.jpg")
    f = str(tmp_path / "a.jpg")
    base = service.preview(f, STUB)
    assert base["marks"] == [] and base["mark_overlays"] == [] and base["walkthrough"] == []
    res = service.preview(f, {**STUB, "photo_marks": [{"box": list(MID), "edit": "brighten this"}]})
    diff = (_arr(res["after"]) - _arr(base["after"])).mean(axis=2)
    h, w = diff.shape
    assert diff[h // 2, w // 2] > 8 and abs(diff[: h // 10]).max() < 3 and abs(diff[-h // 10:]).max() < 3
    adj, = res["result"]["adjustments"]
    assert adj["scope"] == "photo" and adj["target"] == "mark:area:0.3000,0.3000,0.7000,0.7000"
    mk, = res["marks"]
    assert (mk["index"], mk["kind"], mk["word"], mk["found"], mk["why"], mk["status"], mk["problems"]) == \
        (0, "area", "", True, "", "applied", [])
    assert mk["targets"] == [adj["target"]] and mk["score"] is None and mk["coverage"] is None
    ov, = res["mark_overlays"]
    assert ov["target"] == adj["target"]
    alpha = np.asarray(PILImage.open(io.BytesIO(base64.b64decode(ov["alpha"].split(",", 1)[1]))))[..., 3]
    assert alpha[alpha.shape[0] // 2, alpha.shape[1] // 2] == 255 and alpha[0, 0] == 0
    wt, = res["walkthrough"]
    assert wt["target"] == adj["target"] and wt["mask"]["kind"] == "area" and wt["tools"][0]["op"] == "exposure"
    assert res["instruction_problems"] == []
    said = service.preview(f, {**STUB, "photo_marks": [{"box": list(MID), "edit": "make it pop like a movie"}]})
    assert said["marks"][0]["kind"] == "" and "make it pop like a movie" in said["marks"][0]["problems"][0]
    assert said["instruction_problems"] == [] and said["mark_overlays"] == []
    out = service.run_one(f, str(tmp_path / "o"), {**STUB, "photo_marks": [{"box": list(MID), "edit": "brighten this"}]})
    assert [a["scope"] for a in out["result"]["adjustments"]] == ["photo"]


def test_photo_marks_are_checked(tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    _img(d / "a.jpg")
    f = str(d / "a.jpg")
    v = instructions.load_vocab(os.path.join(configs_dir(), "instructions.toml"))
    ok = {"box": [0.3, 0.3, 0.7, 0.7], "edit": "brighten this"}
    assert service.check_marks(None, v) == [] and service.check_marks([{**ok, "box": [0.30, 0.3, 0.31, 0.7]}], v)
    one = lambda **kw: {**ok, **kw}  # noqa: E731
    bad_marks = [one(box=[0.3, 0.3, 0.7]), one(box=[0.3, 0.3, 0.7, 0.7, 0.9]), one(box="0.3,0.3,0.7,0.7"),
                 one(box=[True, 0.3, 0.7, 0.7]), one(box=["0.3", 0.3, 0.7, 0.7]),
                 one(box=[0.3, 0.3, math.nan, 0.7]), one(box=[0.3, 0.3, math.inf, 0.7]),
                 one(box=[-0.1, 0.3, 0.7, 0.7]), one(box=[0.3, 0.3, 1.2, 0.7]),
                 one(box=[0.7, 0.3, 0.3, 0.7]), one(box=[0.3, 0.7, 0.7, 0.3]), one(box=[0.3, 0.3, 0.3, 0.7]),
                 one(box=[0.3, 0.3, 0.305, 0.7]), one(box=[0.3, 0.3, 0.7, 0.305]),
                 one(kind="thing"), one(kind=5), one(edit=""), one(edit="   "), one(edit=5), one(edit="x" * 301),
                 one(box=[0, 0, 10 ** 400, 1]),      # an integer too large for a float: refused, not a crash
                 {"box": ok["box"]}, {"edit": "brighten this"}, {**ok, "extra": 1}, "brighten", None, 5]
    for m in bad_marks:
        with pytest.raises(ValueError):
            service.check_marks([m], v)
    for bad in ({"box": ok["box"]}, "x", 5, [ok] * 21):
        with pytest.raises(ValueError):
            service.check_marks(bad, v)
    assert len(service.check_marks([ok] * 20, v)) == 20
    got, = service.check_marks([{"box": [0, 0, 1, 1], "kind": "area", "edit": "  brighten this "}], v)
    assert got == {"box": (0.0, 0.0, 1.0, 1.0), "kind": "area", "edit": "brighten this"}
    with pytest.raises(ValueError):
        service.preview(f, {**STUB, "photo_marks": [one(edit="")]})
    with pytest.raises(ValueError):
        service.preview(f, {**STUB, "photo_marks": "brighten"})
    bad_maps = [{str(d / "other.jpg"): [ok]}, {f: ok}, {f: [one(edit="")]}, {f: [ok] * 21}, {f: "x"}, [ok],
                {f + str(i): [ok] for i in range(10_001)}, {5: [ok]}]
    for pm in bad_maps:
        with pytest.raises(ValueError):
            jobs.start(str(d), [f], str(tmp_path / "o"), dict(STUB), photo_marks=pm)
    assert jobs.start(str(d), [f], str(tmp_path / "o2"), dict(STUB), photo_marks={f: []}).photo_marks == {}
    _wait(jobs.start(str(d), [f], str(tmp_path / "o3"), dict(STUB), photo_marks={}))

    try:
        from fastapi.testclient import TestClient
    except Exception:
        return                                   # no [ui] extra: the API half cannot run
    from autoedit import paths
    from autoedit.web import app as webapp
    client = TestClient(webapp.create_app(paths.configs_dir(), local_only=True), base_url="http://localhost")
    service._ALLOWED_ROOTS.clear()
    os.environ.pop("AUTOEDIT_ALLOW_ANY_PATH", None)
    service.approve_root(str(tmp_path))
    try:
        for pm in bad_maps:
            r = client.post("/api/v1/jobs", json={**STUB, "files": [f], "out_dir": str(tmp_path / "o"),
                                                  "photo_marks": pm})
            assert r.status_code == 400 and r.json()["code"] == "bad_request", (pm, r.text)
        for pm in ([one(box=[0.3, 0.3, 0.3, 0.7])], [one(box=[0.3, 0.3, 0.305, 0.7])], [one(box=[0.3, 0.3, 1.2, 0.7])],
                   [one(box=[0.7, 0.3, 0.3, 0.7])], [one(box=[0.3, 0.3, 0.7])], [one(box=[0, 0, 10 ** 400, 1])],
                   [one(kind="thing")], [one(edit="")], [one(edit="x" * 301)], [{**ok, "extra": 1}], [ok] * 21, ok):
            r = client.post("/api/v1/preview", json={**STUB, "file": f, "photo_marks": pm})
            assert r.status_code == 400 and r.json()["code"] == "bad_request", (pm, r.text)
        r = client.post("/api/v1/preview", json={**STUB, "file": f, "photo_marks": [ok]})
        assert r.status_code == 200 and r.json()["marks"][0]["found"] is True, r.text
    finally:
        service._ALLOWED_ROOTS.clear()


def test_batch_and_adjust_keep_marks(tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    for n in ("a.jpg", "b.jpg"):
        _img(d / n)
    files = [str(d / "a.jpg"), str(d / "b.jpg")]

    def run(out, **kw):
        job = _wait(jobs.start(str(d), files, str(tmp_path / out), dict(STUB), **kw))
        assert job.status == "done" and all(i.status != "failed" for i in job.items), [i.error for i in job.items]
        return [i.out for i in job.items]

    raw = lambda p: open(p, "rb").read()          # noqa: E731
    plain = run("plain")
    marked = run("marked", photo_marks={files[0]: [{"box": list(MID), "edit": "brighten this"}]})
    assert raw(plain[0]) != raw(marked[0]) and raw(plain[1]) == raw(marked[1])
    side = json.load(open(os.path.splitext(marked[0])[0] + ".autoedit.json"))
    assert [(a["scope"], a["target"], a["source"]) for a in side["adjustments"]] == \
        [("photo", "mark:area:0.3000,0.3000,0.7000,0.7000", "brighten this")]
    before = raw(marked[0])
    res = adjust.rerender(marked[0], source=files[0])
    assert raw(marked[0]) == before and [a["scope"] for a in res["adjustments"]] == ["photo"]
    adjust.rerender(marked[0], source=files[0], reset=True)
    assert raw(marked[0]) == raw(plain[0])
    assert adjust.parse_set("mark:area:0.3,0.3,0.7,0.7.exposure=0.3").target == "mark:area:0.3000,0.3000,0.7000,0.7000"
    with pytest.raises(ValueError):
        adjust.parse_set("mark:area:0.3,0.3,0.7.exposure=0.3")


def test_cli_adjust_mark(tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    _img(d / "a.jpg")
    src = str(d / "a.jpg")
    job = _wait(jobs.start(str(d), [src], str(tmp_path / "out"), dict(STUB)))
    out = job.items[0].out
    base = _arr("x," + base64.b64encode(open(out, "rb").read()).decode())

    def main(*argv):
        so, se = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
            code = cli.main(["adjust", out, "--source", src, *argv])
        return code, so.getvalue(), se.getvalue()

    code, text, _ = main("--mark", "0.3,0.3,0.7,0.7", "--edit", "brighten this", "--explain")
    assert code == 0, text
    now = _arr("x," + base64.b64encode(open(out, "rb").read()).decode())
    diff = (now - base).mean(axis=2)
    assert diff[32, 32] > 8 and abs(diff[:6]).max() < 4 and abs(diff[-6:]).max() < 4
    lines = text.splitlines()
    i = next(n for n, ln in enumerate(lines) if ln.startswith("the marked area: "))
    assert "The oval itself" in lines[i] and lines[i + 1].startswith("  ") and "asked +" in lines[i + 1]
    assert main("--mark", "0.3,0.3,0.2,0.7", "--edit", "brighten this")[0] == 2
    assert main("--mark", "0.3,0.3,0.7", "--edit", "brighten this")[0] == 2
    assert main("--mark", "a,b,c,d", "--edit", "brighten this")[0] == 2
    assert main("--mark", "0.3,0.3,0.7,0.7")[0] == 2
    assert main("--mark", "0.3,0.3,0.7,0.7", "--edit", "brighten this", "--mark-kind", "area")[0] == 0


def test_named_place_holds_guarded_regions(tmp_path):
    path = _scene(tmp_path)
    layer = instructions.LAYER + "center"
    no_guard = _instructions_without_guard(tmp_path)

    def run(edit=None, adjustments=None, banding=True, **cfg):
        pipe = Pipeline.build(RunConfig(backend="stub", edit=edit, **cfg))
        if not banding:     # the full tint bands the synthetic sky, and the gate then re-renders that run alone
            pipe.banding = {}
        return pipe.process_one(path, adjustments=adjustments)

    out, result = run("make the center pink", banding=False)
    comp = {c.target: c.ops for c in result.composition}
    held = comp[layer + "/guarded"]
    assert layer in comp and set(held) == {"tint", "temp"}
    assert abs(held["tint"]) == SKIN_MAX["tint"] and abs(held["temp"]) == SKIN_MAX["temp"]
    full = comp[layer]
    assert held["tint"] * full["tint"] > 0 and held["temp"] * full["temp"] > 0   # the sign of the change
    assert result.adjustments[0].status == "clamped"
    ref, _ = run(adjustments=[Adjustment(target="place:center", ops=held, scope="photo")], banding=False)
    assert np.abs(out - ref)[75:90, 58:70].max() < 1e-3        # in the middle of the skin: the held change
    # the rest of the oval gets the change in full: away from the skin it is the run without the guard
    skin = StubSegmenter().segment(load_image(path), ["skin"])[0].mask
    free, _ = run("make the center pink", banding=False, instructions_path=no_guard)
    assert np.array_equal(out[~_grow(skin, 12)], free[~_grow(skin, 12)])
    assert np.abs(out - free)[skin].max() > 3e-3               # and on the skin the guard holds it back

    out, result = run("brighten the center")                   # the policies leave it alone: one layer, as before
    assert [c.target for c in result.composition if c.target.startswith(layer)] == [layer]
    assert np.array_equal(out, run("brighten the center", instructions_path=no_guard)[0])

    assert np.array_equal(run()[0], run(instructions_path=no_guard)[0])
