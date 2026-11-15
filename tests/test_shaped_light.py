"""F18: shaped light. Shapes (edit/mask.py), the light finder (light.py), the automatic sun glow
(compose.toml [sun_glow], opt-in `light_shaping`)."""

import os
import pathlib

import numpy as np
from PIL import Image as PILImage

from autoedit import compose
from autoedit.contract import RunConfig
from autoedit.edit import glow_alpha, gradient_alpha, linear_alpha
from autoedit.light import find_light
from autoedit.paths import config_path
from autoedit.pipeline import Pipeline

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIGHT_CFG = {**compose.load_light_config(config_path("compose.toml")), "brightness_fallback": True}
PACKAGED_LIGHT = compose.load_light_config(config_path("compose.toml"))


def test_gradient_alpha_math():
    a = gradient_alpha((100, 100), 0, 0.2, 0.6)
    assert a.shape == (100, 100) and a.dtype == np.float32
    col = a[:, 0]
    assert np.all(col[:19] == 1.0) and np.all(col[61:] == 0.0)
    assert np.all(np.diff(col) <= 1e-7)                         # monotonic down the ramp
    assert 0.0 < col[40] < 1.0 and abs(col[40] - 0.5) < 0.02    # the midpoint is half
    assert np.all(a == a[:, :1])                                # nothing varies along x at angle 0
    # the other axis angles are rotations of angle 0 on a square frame
    assert np.allclose(gradient_alpha((100, 100), 180, 0.2, 0.6), np.rot90(a, 2), atol=1e-6)
    assert np.allclose(gradient_alpha((100, 100), 90, 0.2, 0.6), np.rot90(a, 3), atol=1e-6)
    assert np.allclose(gradient_alpha((100, 100), 270, 0.2, 0.6), np.rot90(a, 1), atol=1e-6)
    # 90 starts at the right edge, 270 at the left
    assert gradient_alpha((100, 100), 90, 0.2, 0.6)[50, 99] == 1.0
    assert gradient_alpha((100, 100), 270, 0.2, 0.6)[50, 0] == 1.0
    # an oblique ramp has its half line on the diagonal (angle 45 starts at the top right corner)
    d = gradient_alpha((100, 100), 45, 0.0, 1.0)
    assert abs(d[50, 50] - 0.5) < 0.02 and abs(d[20, 20] - 0.5) < 0.02
    assert d[0, 99] > 0.99 and d[99, 0] < 0.01
    # a rectangle: the fractions are along the direction, so angle 0 uses the height
    r = gradient_alpha((50, 200), 0, 0.0, 0.5)
    assert r[0, 0] > 0.99 and r[25, 0] < 0.01 and r[10, 0] > r[20, 0]
    # start == end is a hard step, with no NaN
    s = gradient_alpha((40, 40), 30, 0.5, 0.5)
    assert np.isfinite(s).all() and set(np.unique(s)) <= {0.0, 1.0}
    # linear_alpha agrees at both ends and the midpoint (the ramp itself is a smoothstep there)
    lin = linear_alpha((100, 10), frm="top", coverage=0.5)
    g = gradient_alpha((100, 10), 0, 0.0, 0.5)
    assert abs(lin[0, 0] - g[0, 0]) < 0.02 and lin[60, 0] == g[60, 0] == 0.0
    assert abs(lin[25, 0] - g[25, 0]) < 0.02


def test_glow_alpha_math():
    a = glow_alpha((200, 300), 0.5, 0.5, 0.2, falloff=2.5)   # radius 60 px (long side 300)
    assert a.dtype == np.float32 and np.isfinite(a).all()
    assert a[100, 150] > 0.99 and a.max() <= 1.0 and a.min() >= 0.0
    assert abs(a[100, 150 + 60] - 0.5) < 0.02 and abs(a[100, 150 - 60] - 0.5) < 0.02
    assert abs(a[100 + 60, 150] - 0.5) < 0.02
    wide = glow_alpha((400, 1200), 0.25, 0.5, 0.1)            # centre x 300, 120 px radius, falloff reaches 300 px
    assert wide[200, 300 + 301:].max() < 0.01 and wide[0:1, 300 + 301:].max() < 0.01
    assert wide[200, 300 + 250] > 0.0                         # still fading, not cut, short of the limit
    assert np.all(np.diff(a[100, 150:]) <= 1e-6)             # only ever falls away from the centre
    sq = glow_alpha((101, 101), 0.5, 0.5, 0.2)                # symmetric about the centre pixel
    assert np.allclose(sq, sq[::-1], atol=1e-6) and np.allclose(sq, sq[:, ::-1], atol=1e-6)
    assert np.allclose(sq, sq.T, atol=1e-6)
    assert np.allclose(sq[50, 50 + 20], sq[50 + 20, 50], atol=1e-6)   # radially symmetric
    off = glow_alpha((100, 100), 1.4, -0.3, 0.3)             # centre outside the frame
    assert np.isfinite(off).all() and off.max() <= 1.0
    tiny = glow_alpha((50, 50), 0.5, 0.5, 0.0)               # a zero radius must not divide by zero
    assert np.isfinite(tiny).all()


def _sky_frame(disc=True, disc_in_sky=True):
    """A 200x300 frame: sky blue on the top 60 %, dark ground below, a white disc at (60, 40)."""
    h, w = 200, 300
    rgb = np.zeros((h, w, 3), np.float32)
    rgb[:120] = (0.45, 0.62, 0.85)
    rgb[120:] = (0.12, 0.2, 0.1)
    sky = np.zeros((h, w), bool)
    sky[:120] = True
    if disc:
        yy, xx = np.mgrid[0:h, 0:w]
        cy, cx = (40, 60) if disc_in_sky else (160, 60)
        rgb[(xx - cx) ** 2 + (yy - cy) ** 2 <= 36] = 1.0
    return rgb, sky


def test_find_light_brightness_fallback():
    rgb, sky = _sky_frame()
    light = find_light(rgb, {"sky": sky}, LIGHT_CFG)
    assert light is not None and light.source == "brightness"
    assert abs(light.cx - 60 / 300) < 0.02 and abs(light.cy - 40 / 200) < 0.02
    assert 0.0 < light.radius < 0.05 and light.score > 0.97
    rgb, sky = _sky_frame(disc_in_sky=False)               # the same disc, on the ground
    assert find_light(rgb, {"sky": sky}, LIGHT_CFG) is None
    assert find_light(rgb, {}, LIGHT_CFG) is None          # no sky: a bright speck is not a sun
    bright_sky = np.full((200, 300, 3), 0.99, np.float32)  # a whole bright sky has no core
    assert find_light(bright_sky, {"sky": np.ones((200, 300), bool)}, LIGHT_CFG) is None
    assert find_light(np.zeros((200, 300, 3), np.float32), {}, LIGHT_CFG) is None
    rgb, sky = _sky_frame(disc=False)                      # sky and ground, nothing brighter than 0.85
    assert find_light(rgb, {"sky": sky}, LIGHT_CFG) is None
    cloud = rgb.copy()                                     # a white patch in a blown, white sky
    cloud[:120] = 0.93
    yy, xx = np.mgrid[0:200, 0:300]
    cloud[(xx - 60) ** 2 + (yy - 40) ** 2 <= 36] = 1.0
    assert find_light(cloud, {"sky": sky}, LIGHT_CFG) is None
    wide = rgb.copy()                                      # a bright band is not a compact core
    wide[30:34, 20:200] = 1.0
    assert find_light(wide, {"sky": sky}, LIGHT_CFG) is None


def test_brightness_fallback_is_off_by_default():
    # measured on a real shoot: it marked snow and cloud edges as suns; SAM 3's concept did not
    rgb, sky = _sky_frame()
    assert PACKAGED_LIGHT["brightness_fallback"] is False
    assert find_light(rgb, {"sky": sky}, PACKAGED_LIGHT) is None


def test_find_light_prefers_the_concept():
    rgb, sky = _sky_frame()
    sun = np.zeros(sky.shape, bool)
    sun[100:112, 200:212] = True                           # not where the bright disc is
    light = find_light(rgb, {"sky": sky, "sun": sun}, LIGHT_CFG)
    assert light is not None and light.source == "concept"
    assert abs(light.cx - 206.0 / 300) < 0.01 and abs(light.cy - 106.0 / 200) < 0.01
    big = np.zeros(sky.shape, bool)
    big[:100] = True                                       # a sun mask covering half the frame is no light
    light = find_light(rgb, {"sky": sky, "sun": big}, LIGHT_CFG)
    assert light is not None and light.source == "brightness"


class _Recorder:
    """Wraps a segmenter and remembers the concepts it was asked for."""

    def __init__(self, inner):
        self.inner, self.asked = inner, []

    def segment(self, img, concepts):
        self.asked.append(list(concepts))
        return self.inner.segment(img, concepts)


def _write(path, rgb):
    PILImage.fromarray((np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8), "RGB").save(path)


def test_light_shaping_is_off_by_default(tmp_path):
    assert RunConfig().light_shaping is False
    assert Pipeline.build(RunConfig(backend="stub")).compose_moves
    assert all(m.name != "sun_glow" for m in Pipeline.build(RunConfig(backend="stub")).compose_moves)
    # a compose.toml without [sun_glow] gives the very same render
    text = pathlib.Path(config_path("compose.toml")).read_text(encoding="utf-8")
    assert "[sun_glow]" in text
    without = tmp_path / "compose.toml"
    without.write_text(text.split("# A soft warm lift where the sun is")[0], encoding="utf-8")
    assert "[sun_glow]" not in without.read_text(encoding="utf-8")
    sunny, _ = _sky_frame()
    sunny_path = str(tmp_path / "sunny.png")
    _write(sunny_path, sunny)
    photos = [sunny_path] + sorted(str(p) for p in (ROOT / "test-corpus").glob("*.jp*g"))
    plain = Pipeline.build(RunConfig(backend="stub"))
    plain.segmenter = rec = _Recorder(plain.segmenter)
    other = Pipeline.build(RunConfig(backend="stub", compose_path=str(without)))
    for path in photos:
        a, ra = plain.process_one(path)
        b, rb = other.process_one(path)
        assert np.array_equal(a, b), path
        assert ra.notes == rb.notes and [m.target for m in ra.composition] == [m.target for m in rb.composition]
    assert rec.asked and all("sun" not in asked for asked in rec.asked)
    assert not any("sun glow" in n for n in ra.notes)


def test_light_shaping_on(tmp_path):
    sunny, _ = _sky_frame()
    sunny_path = str(tmp_path / "sunny.png")
    _write(sunny_path, sunny)
    off, r_off = Pipeline.build(RunConfig(backend="stub")).process_one(sunny_path)
    on_pipe = Pipeline.build(RunConfig(backend="stub", light_shaping=True, compose_path=_fallback_compose(tmp_path)))
    on_pipe.segmenter = rec = _Recorder(on_pipe.segmenter)
    on, r_on = on_pipe.process_one(sunny_path)
    assert all("sun" in asked for asked in rec.asked)               # SAM is asked for the concept
    assert "sun_glow" in [m.target for m in r_on.composition]
    assert "sun_glow" not in [m.target for m in r_off.composition]
    assert any("sun glow: light at" in n for n in r_on.notes)
    ys, xs = np.mgrid[0:on.shape[0], 0:on.shape[1]]
    ring = (((xs - 60 * on.shape[1] / 300) ** 2 + (ys - 40 * on.shape[0] / 200) ** 2) ** 0.5)
    ring = (ring > 14 * on.shape[1] / 300) & (ring < 30 * on.shape[1] / 300)
    # warmer = more red per blue. (R minus B is no good here: the exposure lift scales both
    # channels, so on a blue sky it widens the gap the other way.)
    warm_on, warm_off = float((on[..., 0] / on[..., 2])[ring].mean()), float((off[..., 0] / off[..., 2])[ring].mean())
    assert warm_on > warm_off + 0.002
    assert float(on[ring].mean()) > float(off[ring].mean())
    far = (xs > on.shape[1] * 0.8) & (ys > on.shape[0] * 0.8)       # far corner: the glow is gone
    assert np.abs(on[far] - off[far]).max() < 0.02
    # a photo with no light: no glow, and a note says so
    dull, _ = _sky_frame(disc=False)
    dull_path = str(tmp_path / "dull.png")
    _write(dull_path, dull)
    got, r = Pipeline.build(RunConfig(backend="stub", light_shaping=True)).process_one(dull_path)
    base, _ = Pipeline.build(RunConfig(backend="stub")).process_one(dull_path)
    assert "sun_glow" not in [m.target for m in r.composition]
    assert any("no light source found" in n for n in r.notes)
    assert np.array_equal(got, base)


def test_requires_names_a_run_setting(tmp_path):
    import pytest
    p = tmp_path / "c.toml"
    p.write_text('[m]\nwhen = "always"\nshape = "radial"\nrequires = "no_such_setting"\n', encoding="utf-8")
    with pytest.raises(ValueError):
        compose.load_compose(str(p), RunConfig())
    p.write_text('[m]\nwhen = "always"\nshape = "radial"\nat = "light"\n', encoding="utf-8")
    with pytest.raises(ValueError):                                  # `at` only goes with a glow
        compose.load_compose(str(p))
    assert os.path.exists(config_path("compose.toml"))


# ---- shapes by instruction (task 2) ----

from autoedit import instructions  # noqa: E402
from autoedit.paths import configs_dir  # noqa: E402

_KNOWN = {"sky", "mountain", "grass", "skin", "subject"}


def _vocab():
    return instructions.load_vocab(os.path.join(configs_dir(), "instructions.toml"))


def _grey(tmp_path, name="grey.png"):
    path = str(tmp_path / name)
    _write(path, np.full((200, 300, 3), 0.5, np.float32))
    return path


def _run(path, edit=None, **kw):
    return Pipeline.build(RunConfig(backend="stub", edit=edit, **kw)).process_one(path)


def _fallback_compose(tmp_path):
    """compose.toml with the brightness fallback on: the stub segmenter has no "sun" concept."""
    text = open(config_path("compose.toml")).read().replace("brightness_fallback = false", "brightness_fallback = true")
    assert "brightness_fallback = true" in text
    p = tmp_path / "compose_fallback.toml"
    p.write_text(text)
    return str(p)


def test_place_parse_golden():
    def one(text):
        adjs, problems = instructions.parse(text, _vocab(), _KNOWN)
        assert not problems and len(adjs) == 1, (text, problems)
        return adjs[0].target, adjs[0].ops

    def close(ops, want):
        assert set(ops) == set(want) and all(abs(ops[k] - v) < 1e-9 for k, v in want.items()), (ops, want)

    t, o = one("darken the top with a gradient")
    assert t == "place:top"
    close(o, {"exposure": -0.30})
    t, o = one("brighten the bottom a little")
    assert t == "place:bottom"
    close(o, {"exposure": 0.30 * 0.5})
    for text in ("add a warm glow where the sun is", "add a glow"):
        t, o = one(text)
        assert t == "place:light"
        close(o, {"exposure": 0.15, "temp": 8})
    t, o = one("brighten the center")
    assert t == "place:center"
    close(o, {"exposure": 0.30})
    t, o = one("add a vignette")
    assert t == "place:edges"
    close(o, {"exposure": -0.25})
    t, o = one("darken the edges")
    assert t == "place:edges"
    close(o, {"exposure": -0.30})
    t, o = one("warm the left side")
    assert t == "place:left"
    close(o, {"temp": 10})
    t, o = one("darken the sky")
    assert t == "sky"
    close(o, {"exposure": -0.30})
    adjs, problems = instructions.parse("don't darken the top", _vocab(), _KNOWN)
    assert adjs == [] and len(problems) == 1 and "negation" in problems[0]
    # one target per clause: a place or an object, and one place
    for text in ("darken the top of the mountain", "darken the top left"):
        adjs, problems = instructions.parse(text, _vocab(), _KNOWN)
        assert adjs == [] and len(problems) == 1, text
        assert not instructions.translatable(problems[0])      # a model must not guess at these
    # a verb with a default place keeps it next to another clause; "of the frame" is not an object
    assert [a.target for a in instructions.parse("darken the top and add a vignette", _vocab(), _KNOWN)[0]] \
        == ["place:top", "place:edges"]
    assert [a.target for a in instructions.parse("darken the top of the frame", _vocab(), _KNOWN)[0]] == ["place:top"]
    # places are no segmentation concepts, except that the light needs the sun
    adjs = instructions.parse("darken the top, add a glow, brighten the sky", _vocab(), _KNOWN)[0]
    assert instructions.extra_concepts(adjs, []) == ["sun", "sky"]
    assert instructions.extra_concepts(adjs, ["sun", "sky"]) == []


def test_place_layer_is_applied(tmp_path):
    path = _grey(tmp_path)
    base, _ = _run(path)
    out, r = _run(path, "darken the top with a gradient")
    assert out.shape == base.shape
    h = out.shape[0]
    diff = (out.astype(np.float64) - base.astype(np.float64)) * 255
    assert diff[:int(h * 0.1)].mean() < -10                       # the top is clearly darker
    assert np.abs(diff[int(h * 0.6):]).max() <= 1                 # the bottom 40 % is untouched
    assert diff[:int(h * 0.1)].mean() < diff[int(h * 0.3):int(h * 0.4)].mean() < 0.5   # and it fades
    (a,) = [a for a in r.adjustments if a.target == "place:top"]
    assert a.status == "applied"
    layer = [m for m in r.composition if m.target == "instruction:top"]
    assert len(layer) == 1 and abs(layer[0].ops["exposure"] + 0.30) < 1e-9
    # without an instruction nothing of it shows
    assert not any(m.target.startswith("instruction:") for m in _run(path)[1].composition)
    # a place works as a follow-up edit too (it goes through the same parser)
    adjs, problems = instructions.parse("darken the top with a gradient", _vocab(), _KNOWN)
    got, r2 = Pipeline.build(RunConfig(backend="stub")).process_one(
        path, adjustments=[a.model_copy(update={"scope": "photo"}) for a in adjs])
    assert np.array_equal(got, out) and "instruction:top" in [m.target for m in r2.composition]
    # an oval and the vignette leave the corners (or the middle) alone. The corner of the oval run
    # still moves a little: the global pass after the layers reads the whole frame's brightness.
    centre, _ = _run(path, "brighten the center")
    d = (centre.astype(np.float64) - base.astype(np.float64)) * 255
    assert d[100, 150].min() > 10 and np.abs(d[0, 0]).max() < 5
    vig, _ = _run(path, "add a vignette")
    d = (vig.astype(np.float64) - base.astype(np.float64)) * 255
    assert d[0, 0].max() < -5 and np.abs(d[100, 150]).max() <= 1


def test_glow_without_light_is_not_found(tmp_path):
    dull, _ = _sky_frame(disc=False)
    path = str(tmp_path / "dull.png")
    _write(path, dull)
    base, _ = _run(path)
    out, r = _run(path, "add a glow")
    assert np.array_equal(out, base)
    (a,) = r.adjustments
    assert a.target == "place:light" and a.status == "not_found"
    assert any("no light source found" in n for n in r.notes)
    assert not any(m.target.startswith("instruction:") for m in r.composition)
    # the sun is asked of the segmenter, even though the automatic glow is off
    pipe = Pipeline.build(RunConfig(backend="stub", edit="add a glow"))
    pipe.segmenter = rec = _Recorder(pipe.segmenter)
    pipe.process_one(path)
    assert rec.asked and all(asked.count("sun") == 1 for asked in rec.asked)
    # and with a sun in the frame the glow is there, around the sun, and the status says so
    sunny, _ = _sky_frame()
    sunny_path = str(tmp_path / "sunny.png")
    _write(sunny_path, sunny)
    base, _ = _run(sunny_path, compose_path=_fallback_compose(tmp_path))
    out, r = _run(sunny_path, "add a warm glow where the sun is", compose_path=_fallback_compose(tmp_path))
    assert [a.status for a in r.adjustments] == ["applied"]
    assert "instruction:light" in [m.target for m in r.composition]
    near = (slice(30, 60), slice(45, 80))
    assert out[near].mean() > base[near].mean()
    assert np.abs(out[-20:, -20:].astype(np.float64) - base[-20:, -20:]).max() * 255 <= 2
    # with the automatic glow on as well, the sun is still asked for once
    both = Pipeline.build(RunConfig(backend="stub", edit="add a glow", light_shaping=True))
    both.segmenter = rec = _Recorder(both.segmenter)
    both.process_one(sunny_path)
    assert rec.asked and all(asked.count("sun") == 1 for asked in rec.asked)


def test_place_limits_and_sum(tmp_path):
    path = _grey(tmp_path)
    _, r = _run(path, "darken the top. darken the top")
    layer = [m for m in r.composition if m.target == "instruction:top"]
    assert len(layer) == 1 and abs(layer[0].ops["exposure"] + 0.60) < 1e-9
    assert [a.status for a in r.adjustments] == ["applied", "applied"]
    _, r = _run(path, "darken the top. darken the top. darken the top")     # -0.90 is beyond the 0.75 limit
    layer = [m for m in r.composition if m.target == "instruction:top"]
    assert abs(layer[0].ops["exposure"] + 0.75) < 1e-9
    assert {a.status for a in r.adjustments} == {"clamped"}
    assert any("held back" in n for n in r.notes)
