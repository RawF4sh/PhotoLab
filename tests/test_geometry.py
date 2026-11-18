"""F32 geometry: straighten and crop. Tests 1 to 11: the pure functions and the settings. Tests 12 to 17: from the
settings to the export."""
import argparse
import json
import math
import os
import shutil
import tomllib
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image as PILImage

from autoedit import adjust, batch, cli, emit, paths
from autoedit import geometry
from autoedit.contract import RunConfig
from autoedit.paths import config_path
from autoedit.pipeline import Pipeline
from autoedit.segment.base import RegionMask
from autoedit.styles import style_for
from autoedit.web import service

D = geometry.DEFAULTS
FRAMES = ((400, 600), (600, 400))


def _scene(hw, angle, at=0.4, boat=0.0):
    """(rgb, {"sky", "water"}): sky over water, the waterline crosses the middle column at `at` of the
    height and falls to the right by `angle` degrees; `boat`: a share of the width with a block on the water."""
    h, w = hw
    yy, xx = np.mgrid[0:h, 0:w]
    line = at * h + math.tan(math.radians(angle)) * (xx + 0.5 - w / 2)
    water = yy + 0.5 >= line
    if boat:
        x0 = int(0.3 * w)
        block = np.zeros((h, w), bool)
        block[:, x0:x0 + int(boat * w)] = (yy[:, x0:x0 + int(boat * w)] + 0.5 >= line[:, x0:x0 + int(boat * w)] - 0.1 * h)
        water = water | block
    sky = ~water
    rgb = np.where(sky[..., None], np.array([0.6, 0.7, 1.0]), np.array([0.05, 0.1, 0.3])).astype(np.float32)
    return rgb, {"sky": sky, "water": water}


def _slope(rgb):
    """Degrees the edge between the bright top and the dark bottom of `rgb` slopes (positive: falls to the right)."""
    mask = rgb[..., 0] > 0.3
    x, y = geometry._edge(mask, top=False)
    return geometry._fit(x, y, 1.0)[0]


def test_a_sloping_line_is_measured():
    for hw in FRAMES:
        for a in (-4, -1.5, 0.7, 3):
            _, m = _scene(hw, a)
            ang, line, conf, why = geometry.horizon({"water": m["water"]}, D)
            assert abs(ang - a) < 0.1 and line == "waterline" and conf > 0.95 and why == ""
            ang, line, conf, _ = geometry.horizon({"sky": m["sky"]}, D)
            assert abs(ang - a) < 0.1 and line == "horizon"
        _, m = _scene(hw, 2.0, boat=0.15)
        ang, line, conf, _ = geometry.horizon({"water": m["water"]}, D)
        assert abs(ang - 2.0) < 0.1 and 0.8 <= conf <= 0.9


def test_a_sloping_photo_is_levelled_within_a_fifth_of_a_degree():
    for hw in FRAMES:
        for a in (-4, 0.7, 3):
            rgb, m = _scene(hw, a)
            geo, notes = geometry.decide(m, hw, True, None, D)
            assert geo is not None and geo.kept < 1
            out = geometry.warp(rgb, geo)
            assert out.shape[:2] == geometry.size(geo, hw)
            assert abs(_slope(out)) < 0.2
            assert any("counter-clockwise" in n for n in notes) == (a > 0)


def test_a_level_photo_is_left_alone():
    hw = (400, 600)
    for a in (0, 0.2, -0.25):
        rgb, m = _scene(hw, a)
        geo, notes = geometry.decide(m, hw, True, None, D)
        assert geo is None
        assert notes == ["straighten: left alone, the waterline is level (within 0.3 degrees)"]
        assert geometry.warp(rgb, geo) is rgb
    water = np.zeros((200, 256), bool)
    water[100:, :128] = True
    water[101:, 128:] = True
    ang = geometry.horizon({"water": water}, D)[0]
    assert 0.2 < abs(ang) < 0.5
    geo, notes = geometry.decide({"water": water}, (200, 256), True, None, D)
    assert geo is None and notes == ["straighten: left alone, the waterline is level (within 0.45 degrees)"]


def test_a_ridge_is_not_a_horizon():
    h, w = 400, 600
    yy, xx = np.mgrid[0:h, 0:w]
    peaks = 200 + 100 * np.abs(((xx / (w / 3)) % 1.0) - 0.5) * 2     # three peaks
    ang, line, _, why = geometry.horizon({"sky": yy < peaks}, D)
    assert line == "" and "the horizon is not one straight line" in why
    _, m = _scene((h, w), 6.0)
    line, why = geometry.horizon({"water": m["water"]}, D)[1::2]
    assert line == "" and "more than the limit of 5" in why
    _, m = _scene((h, w), 12.0)
    assert geometry.horizon({"water": m["water"]}, D)[1] == ""
    part = np.zeros((h, w), bool)
    part[200:, :int(0.3 * w)] = True
    line, why = geometry.horizon({"water": part}, D)[1::2]
    assert line == "" and "covers too little of the width" in why
    lake = ((xx - 300) / 200.0) ** 2 + ((yy - 250) / 80.0) ** 2 < 1
    assert geometry.horizon({"water": lake}, D)[1] == ""
    assert geometry.horizon({}, D)[3] == "no water and no sky found"


def _corners(fr, angle, hw):
    cx, cy, w, h = fr
    H, W = hw
    a, b = w * W / 2, h * H / 2
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    return [(cx * W + sx * a * c - sy * b * s, cy * H + sx * a * s + sy * b * c) for sx in (-1, 1) for sy in (-1, 1)]


def test_the_frame_is_the_largest_that_fits():
    for hw in ((400, 600), (600, 400), (500, 500)):
        H, W = hw
        for angle in (0, 0.5, -3, 5):
            for ratio in (None, 0.8, 1, 16 / 9):
                for centre in (None, (0.1, 0.1), (0.9, 0.5), (0.5, 0.95)):
                    fr = geometry.frame(angle, ratio, hw, centre)
                    for x, y in _corners(fr, angle, hw):
                        assert -1e-6 <= x <= W + 1e-6 and -1e-6 <= y <= H + 1e-6
                    assert abs(fr[2] * W / (fr[3] * H) - (ratio or W / H)) < 1e-9
                    if centre is None:
                        big = (fr[0], fr[1], fr[2] * 1.002, fr[3] * 1.002)
                        assert any(not (0 <= x <= W and 0 <= y <= H) for x, y in _corners(big, angle, hw))
    assert abs(geometry.frame(5, None, (400, 600))[2] * geometry.frame(5, None, (400, 600))[3] - 0.787) < 0.002
    assert abs(geometry.frame(1, None, (400, 600))[2] * geometry.frame(1, None, (400, 600))[3] - 0.950) < 0.002
    assert geometry.frame(0, None, (400, 600)) == (0.5, 0.5, 1.0, 1.0)


def test_a_turn_adds_nothing():
    rng = np.random.default_rng(1)
    for hw in ((100, 150), (150, 100), (120, 120)):
        for angle in (-5, -1, 0.5, 2, 5):
            fr = geometry.frame(angle, None, hw)
            geo = geometry.Geometry(angle=angle, line="waterline", cx=fr[0], cy=fr[1], w=fr[2], h=fr[3], kept=fr[2] * fr[3])
            ones = np.ones(hw + (3,), np.float32)
            assert geometry.warp(ones, geo).min() >= 1 - 1e-6
            assert np.abs(geometry.warp(ones * 0.37, geo) - 0.37).max() < 1e-6
            noise = rng.random(hw + (3,)).astype(np.float32)
            a, b = geometry.warp(noise, geo), geometry.warp(noise, geo)
            assert a.min() >= 0 and a.max() <= 1 and np.array_equal(a, b)
            assert geometry.warp(noise[..., :1], geo).shape[2] == 1


def test_a_crop_alone_is_a_plain_cut():
    x = np.random.default_rng(2).random((400, 600, 3)).astype(np.float32)
    geo, notes = geometry.decide({}, (400, 600), False, "4:5", D)
    assert np.array_equal(geometry.warp(x, geo), x[:, 140:460])
    assert geo.kept == 0.5333
    assert notes == ["crop: to 4:5, placed in the middle",
                     "geometry: 53.3 % of the photo is kept, 46.7 % is cut; done to the export after the checks, nothing is added"]
    geo, _ = geometry.decide({}, (4000, 6000), False, "4:5", D)
    assert geometry.size(geo, (4000, 6000)) == (4000, 3200)
    geo, _ = geometry.decide({}, (600, 400), False, "16:9", D)
    assert geometry.size(geo, (600, 400)) == (225, 400)


def _person(hw, at, width=40):
    m = np.zeros(hw, bool)
    x = int(at * hw[1])
    m[150:250, x - width // 2:x + width // 2] = True
    return m


def test_a_crop_keeps_the_subject():
    hw = (400, 600)
    for at in (0.2, 0.5, 0.8):
        m = _person(hw, at)
        geo, notes = geometry.decide({"person": m}, hw, False, "4:5", D)
        xs = np.nonzero(m.any(axis=0))[0]
        x0 = round((geo.cx - geo.w / 2) * 600)
        assert x0 <= xs.min() and xs.max() < x0 + round(geo.w * 600)
        assert any("placed on the subject" in n for n in notes)
        if at == 0.2:
            assert abs(geo.cx - geo.w / 2) < 1e-6
    geo, _ = geometry.decide({"person": _person(hw, 0.4)}, hw, False, "4:5", D)
    assert abs(geo.cx - 0.4) < 0.002
    two = _person(hw, 0.2) | _person(hw, 0.8)
    assert abs(geometry.decide({"person": two}, hw, False, "4:5", D)[0].cx - 0.5) < 0.002
    for masks in ({"person": np.zeros(hw, bool)}, {"water": _person(hw, 0.2)}):
        geo, notes = geometry.decide(masks, hw, False, "4:5", D)
        assert geo.cx == 0.5 and any("in the middle" in n for n in notes)


def test_straighten_and_crop_together():
    hw = (400, 600)
    rgb, m = _scene(hw, 3.0)
    masks = {"water": m["water"], "person": _person(hw, 0.2)}
    geo, notes = geometry.decide(masks, hw, True, "1:1", D)
    assert abs(geo.angle - 3.0) < 0.1
    for x, y in _corners((geo.cx, geo.cy, geo.w, geo.h), geo.angle, hw):
        assert -1e-3 <= x <= 600 + 1e-3 and -1e-3 <= y <= 400 + 1e-3   # the record is rounded to 6 places
    out = geometry.warp(rgb, geo)
    assert abs(out.shape[0] - out.shape[1]) <= 1 and abs(_slope(out)) < 0.2
    assert [n.split(":")[0] for n in notes] == ["straighten", "crop", "geometry"]


def test_the_photo_s_own_shape_is_no_crop():
    for hw in ((400, 600), (3840, 5760), (400, 601)):
        geo, notes = geometry.decide({}, hw, False, "3:2", D)
        assert geo is None and notes == ["crop: left alone, the photo already has the shape 3:2"]
    _, m = _scene((400, 600), 3.0)
    geo, _ = geometry.decide(m, (400, 600), True, "3:2", D)
    assert geo.aspect == "3:2" and abs(geo.w * 600 / (geo.h * 400) - 1.5) < 1e-6


def test_settings_and_thresholds():
    cfg = RunConfig()
    assert cfg.straighten is False and cfg.out_aspect is None
    assert RunConfig(out_aspect="").out_aspect is None
    assert RunConfig(out_aspect="4:5").out_aspect == "4:5"
    for bad in ("4x5", "0:5", "4:0", "1:9", "9:1", "-4:5", "4:5:6", "٤:٥", " 4:5", "4:5\n", "1000:999", "4.5:5"):
        with pytest.raises(ValueError) as e:
            RunConfig(out_aspect=bad)
        assert "out_aspect" in str(e.value)
    assert geometry.config({}) == D
    with open(config_path("rules.toml"), "rb") as fh:
        assert geometry.config(tomllib.load(fh)) == D
    assert all(isinstance(geometry.config({"geometry": {"max_angle": 3}})[k], float) for k in ("max_angle", "tolerance"))
    _, m = _scene((400, 600), 4.0)
    assert geometry.horizon({"water": m["water"]}, geometry.config({"geometry": {"max_angle": 3}}))[1] == ""
    assert geometry.horizon({"water": m["water"]}, D)[1] == "waterline"


# ---- tests 12 to 17: from the settings to the export ---------------------------------------------

HW = (400, 600)


class Fake:
    """A segmenter that returns the given masks by name (score 0.9, or `scores[name]`) and lists what it was asked."""
    name = "fake"

    def __init__(self, masks, scores=None):
        self.masks, self.scores, self.asked = masks, scores or {}, []

    def segment(self, img, concepts):
        self.asked.append(list(concepts))
        return [RegionMask(name=c, mask=self.masks[c], score=self.scores.get(c, 0.9),
                           coverage=float(self.masks[c].mean()), instance_count=1)
                for c in concepts if c in self.masks]


def _pipe(masks, scores=None, **settings):
    """A stub pipeline that answers with `masks`, as a render worker does (batch._worker_init): backend sam3."""
    pipe = Pipeline.build(RunConfig(backend="stub", **settings))
    pipe.cfg = pipe.cfg.model_copy(update={"backend": "sam3"})
    pipe.segmenter = Fake(masks, scores)
    return pipe


def _photo(path, angle=3.0, hw=HW):
    rgb, m = _scene(hw, angle)
    PILImage.fromarray((rgb * 255 + 0.5).astype(np.uint8), "RGB").save(path, quality=98)
    return m


def _edge_slope(arr):
    """Degrees the edge between the bright top and the dark bottom of `arr` (0..1) slopes."""
    r = arr[..., 0]
    x, y = geometry._edge(r > (r.min() + r.max()) / 2, top=False)
    return geometry._fit(x, y, 1.0)[0]


def _png(path):
    return np.asarray(PILImage.open(path).convert("RGB"), np.float32) / 255.0


def test_a_default_run_is_untouched(tmp_path):
    src = str(tmp_path / "a.jpg")
    masks = _photo(src)
    pipe = _pipe(masks)
    assert pipe.geometry_cfg == {}
    out, res = pipe.process_one(src)
    assert pipe.segmenter.asked == [pipe.concepts_for(style_for(pipe.cfg.style, pipe.styles))]
    assert res.geometry is None
    assert not [n for n in res.notes if n.split(":")[0] in ("straighten", "crop", "geometry")]
    a, b = str(tmp_path / "a.png"), str(tmp_path / "b.png")
    emit.write_image(out, a, "png", long_edge=300)
    emit.write_image(out, b, "png", long_edge=300, geometry=None)
    assert open(a, "rb").read() == open(b, "rb").read()
    emit.write_outputs(out, res, str(tmp_path / "o.jpg"), str(tmp_path / "o.autoedit.json"), pipe.cfg, src)
    side = json.load(open(tmp_path / "o.autoedit.json"))
    assert "geometry" in side and side["geometry"] is None


def test_the_pipeline_decides_and_the_export_is_turned(tmp_path):
    src = str(tmp_path / "a.jpg")
    masks = _photo(src, 3.0)
    plain, _ = _pipe(masks).process_one(src)
    pipe = _pipe(masks, straighten=True, out_format="png")
    out, res = pipe.process_one(src)
    assert out.shape == plain.shape and np.array_equal(out, plain)       # the checks see the whole photo, unturned
    geo = res.geometry
    assert geo is not None and abs(geo.angle - 3.0) < 0.2 and geo.line == "waterline"
    assert any(n.startswith("straighten: turned") for n in res.notes)
    img, side = str(tmp_path / "o.png"), str(tmp_path / "o.autoedit.json")
    emit.write_outputs(out, res, img, side, pipe.cfg, src)
    h, w = geometry.size(geo, HW)
    assert PILImage.open(img).size == (w, h)
    assert abs(_edge_slope(_png(img))) < 0.2
    assert json.load(open(side))["geometry"] == json.loads(geo.model_dump_json())
    dry, dry_res = _pipe(masks, straighten=True).process_one(src, do_apply=False)
    assert dry is None and dry_res.geometry == geo


def test_the_stub_backend_does_not_straighten(tmp_path):
    src = str(tmp_path / "a.jpg")
    _photo(src)

    class Spy:
        def __init__(self, inner):
            self.inner, self.asked = inner, []

        def segment(self, img, concepts):
            self.asked.append(list(concepts))
            return self.inner.segment(img, concepts)

    pipe = Pipeline.build(RunConfig(backend="stub", straighten=True, out_aspect="1:1"))
    pipe.segmenter = spy = Spy(pipe.segmenter)
    _, res = pipe.process_one(src)
    geo = res.geometry
    assert geo.angle == 0.0 and geo.line == "" and (geo.cx, geo.cy) == (0.5, 0.5) and geo.aspect == "1:1"
    assert any(n.startswith("straighten: not done, the stub backend") for n in res.notes)
    assert any("placed in the middle" in n for n in res.notes)
    assert not [c for call in spy.asked for c in call if c == "person"]
    only = Pipeline.build(RunConfig(backend="stub", straighten=True))
    _, res = only.process_one(src)
    assert res.geometry is None and any("stub backend" in n for n in res.notes)


def test_extra_masks_are_asked_for_only_when_needed(tmp_path):
    src = str(tmp_path / "a.jpg")
    m = _photo(src)
    masks = {**m, "subject": _person(HW, 0.3), "person": _person(HW, 0.3)}
    for settings, want in (({}, ["sky"]),
                           ({"straighten": True}, ["sky", "water"]),
                           ({"out_aspect": "4:5"}, ["sky", "subject", "person"]),
                           ({"straighten": True, "out_aspect": "4:5"}, ["sky", "water", "subject", "person"])):
        pipe = _pipe(masks, concepts=["sky"], **settings)
        _, res = pipe.process_one(src)
        assert pipe.segmenter.asked == [want], settings
        pipe.presegment(src)
        assert pipe.segmenter.asked[-1] == want, settings
        assert {x.target for x in res.masks} <= {"sky"} and {c.name for c in res.concepts} <= {"sky"}
    # a mask under its concept's score is not read: the crop is placed in the middle
    for score, text in ((0.9, "placed on the subject"), (0.2, "placed in the middle")):
        _, res = _pipe(masks, scores={"person": score, "subject": score},
                       concepts=["sky"], out_aspect="4:5").process_one(src)
        assert any(text in n for n in res.notes), score


def test_presets_flags_and_requests(tmp_path):
    # a preset with `aspect`
    assert exports_kwargs(tmp_path, '[sq]\naspect = "1:1"\n') == {"out_aspect": "1:1"}
    cdir = tmp_path / "configs"
    shutil.copytree(os.path.dirname(config_path("defaults.toml")), cdir)
    (cdir / "exports.toml").write_text('[sq]\nformat = "jpeg"\naspect = "1:1"\n')
    cfg = cli.load_run_config(SimpleNamespace(configs_dir=str(cdir), config=None, export="sq"))
    assert cfg.out_aspect == "1:1" and cfg.straighten is False
    assert service.make_cfg({"export": "sq"}, configs_dir=str(cdir)).out_aspect == "1:1"
    # the flags
    parser = argparse.ArgumentParser()
    cli._add_run_flags(parser)
    cfg = cli.load_run_config(parser.parse_args(["in", "out", "--straighten", "--aspect", "4:5"]))
    assert cfg.straighten is True and cfg.out_aspect == "4:5"
    plain = cli.load_run_config(parser.parse_args(["in", "out"]))
    assert plain.straighten is False and plain.out_aspect is None
    # a run file, and `--aspect ""` over it
    run = tmp_path / "run.toml"
    run.write_text('[run]\nstraighten = true\n[output]\naspect = "4:5"\n')
    cfg = cli.load_run_config(parser.parse_args(["in", "out", "--config", str(run)]))
    assert cfg.straighten is True and cfg.out_aspect == "4:5"
    cfg = cli.load_run_config(parser.parse_args(["in", "out", "--config", str(run), "--aspect", ""]))
    assert cfg.out_aspect is None and cfg.straighten is True
    with pytest.raises(ValueError):
        cli.load_run_config(parser.parse_args(["in", "out", "--aspect", "4x5"]))
    # a request
    cfg = service.make_cfg({"straighten": True, "out_aspect": "1:1"})
    assert cfg.straighten is True and cfg.out_aspect == "1:1"
    with pytest.raises(ValueError):
        service.make_cfg({"out_aspect": "wide"})


def exports_kwargs(tmp_path, text):
    from autoedit import exports
    p = tmp_path / "e.toml"
    p.write_text(text)
    return exports.preset_kwargs("sq", str(p))


def _bytes(folder):
    return {n: open(os.path.join(folder, n), "rb").read() for n in sorted(os.listdir(folder)) if n.endswith(".jpg")}


def test_a_batch_crops_resizes_and_renders_again(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    for i in range(2):
        a = np.random.default_rng(i).integers(60, 200, (300, 400, 3), dtype=np.uint8)
        PILImage.fromarray(a, "RGB").save(str(src / f"p{i}.jpg"), quality=95)
    cfg = RunConfig(backend="stub", out_aspect="1:1", out_long_edge=120)
    one, two = str(tmp_path / "one"), str(tmp_path / "two")
    batch.run(cfg, str(src), one, workers=1)
    for n in ("p0.jpg", "p1.jpg"):
        assert PILImage.open(os.path.join(one, n)).size == (120, 120)
    side = json.load(open(os.path.join(one, "p0.autoedit.json")))
    assert side["geometry"]["kept"] == 0.75 and side["geometry"]["aspect"] == "1:1"
    before = _bytes(one)
    adjust.rerender(os.path.join(one, "p0.jpg"))
    assert _bytes(one) == before
    batch.run(cfg, str(src), two, workers=2)
    assert _bytes(two) == before
    plain = str(tmp_path / "plain")
    batch.run(RunConfig(backend="stub"), str(src), plain)
    assert PILImage.open(os.path.join(plain, "p0.jpg")).size == (400, 300)
    assert json.load(open(os.path.join(plain, "p0.autoedit.json")))["geometry"] is None


# ---- Tests 18 to 20 (T3): the preview and the tools ---------------------------------------

def _data(url):
    import base64
    import io
    return PILImage.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))


def _gradient_photo(path, hw=(300, 400)):
    a = np.random.default_rng(5).integers(60, 200, (*hw, 3), dtype=np.uint8)
    PILImage.fromarray(a, "RGB").save(path, quality=95)


def test_the_preview_shows_the_export_s_frame(tmp_path):
    src = str(tmp_path / "a.jpg")
    _gradient_photo(src)
    base = {"backend": "stub", "style": "natural", "photo_marks": [{"box": [0.3, 0.3, 0.7, 0.7], "edit": "brighten this"}]}
    plain = service.preview(src, {**base, "steps": {"px": 2560, "lossless": True}})
    sq = service.preview(src, {**base, "out_aspect": "1:1", "steps": {"px": 2560, "lossless": True}})
    assert _data(plain["before"]).size == (400, 300) and _data(plain["after"]).size == (400, 300)
    assert _data(sq["before"]).size == (300, 300) and _data(sq["after"]).size == (300, 300)
    assert sq["result"]["geometry"]["aspect"] == "1:1"
    assert sq["mask_overlays"] and sq["mark_overlays"]
    for o in [*sq["mask_overlays"], *sq["mark_overlays"]]:
        assert _data(o["alpha"]).size == (300, 300)
    for o in plain["mark_overlays"]:
        assert _data(o["alpha"]).size == (400, 300)
    # `before` is the middle of the plain preview's: the dither follows the frame, so within 2 levels
    a = np.asarray(_data(plain["before"]).convert("RGB"), np.int16)[:, 50:350]
    b = np.asarray(_data(sq["before"]).convert("RGB"), np.int16)
    assert np.abs(a - b).max() <= 2
    # F29: every step picture is in the frame, and the last is the preview's `after` byte for byte
    assert sq["steps"] and all(_data(s["after"]).size == (300, 300) for s in sq["steps"])
    assert sq["steps"][-1]["after"] == sq["after"]
    assert [s["after"] for s in plain["steps"]][-1] == plain["after"]
    v = service.make_variants(src, {"backend": "stub", "out_aspect": "1:1", "variants": [{"name": "x"}]})
    assert _data(v["variants"][0]["after"]).size == (300, 300)


def test_the_safety_workflow_does_no_geometry(tmp_path):
    from autoedit.quality_runner import render_safe
    src = str(tmp_path / "a.jpg")
    _gradient_photo(src)
    rgb, res = render_safe(src, RunConfig(backend="stub", out_aspect="1:1"))
    assert rgb.shape[:2] == (300, 400) and res.geometry is None
    assert "geometry: not done by the safety workflow, which checks the pixels it writes" in res.notes


def test_contact_sheet_takes_a_turned_or_cropped_side(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "contact_sheet", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "contact_sheet.py"))
    cs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cs)
    a, b = PILImage.new("RGB", (600, 400), (90, 90, 90)), PILImage.new("RGB", (400, 400), (100, 100, 100))
    assert cs._facts(a, b).startswith("diff ")
    src = str(tmp_path / "a.jpg")
    _gradient_photo(src)
    pipe = Pipeline.build(RunConfig(backend="stub", out_aspect="1:1"))
    jpg, res = cs._render(pipe, src, str(tmp_path / "o"))
    assert PILImage.open(jpg).size == (300, 300) and res.geometry is not None


def test_the_geometry_block_is_checked():
    from autoedit import geometry
    assert geometry.config({}) == geometry.DEFAULTS
    assert geometry.config({"geometry": {"subject": ["person"], "max_angle": 3}})["subject"] == ("person",)
    for bad in ({"subject": "person"}, {"subject": [1]}, {"max_angel": 3}, {"max_angle": -1}, {"tolerance": 0},
                {"max_angle": True}, {"min_angle": 6.0}, {"max_angle": 9}, {"sky_span": 1.5}, {"tolerance": "x"}):
        with pytest.raises(ValueError):
            geometry.config({"geometry": bad})
