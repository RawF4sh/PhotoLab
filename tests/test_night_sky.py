"""F31: night sky tools. The ops of edit/ops.py (stuck pixels, the sky's gradient, the stars the
noise reduction and the sharpener spare) and the fit of night.py, on made-up skies."""

import json
import tomllib
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image as PILImage

from autoedit import night
from autoedit.contract import RunConfig
from autoedit.edit import ops
from autoedit.imageio import luma
from autoedit.paths import config_path
from autoedit.pipeline import Pipeline
from autoedit.segment.base import RegionMask

_H, _W = 400, 600
_GLOW = np.array([20.0, 13.0, 5.0], np.float32) / 255.0   # an orange glow toward the bottom, per channel


def _glow(h=_H, w=_W):
    yn = (np.arange(h, dtype=np.float32) + 0.5)[:, None] / h
    xn = (np.arange(w, dtype=np.float32) + 0.5)[None, :] / w
    return ((0.6 * yn + 0.4 * yn * yn + 0.1 * xn)[..., None] * _GLOW).astype(np.float32)


def _sky(seed, noise=0.0, band=0.0):
    """(rgb, star centres): background 0.10 plus the glow, 60 white stars of 2.5 px with heights
    0.05 to 0.6, noise per channel, a Milky Way band of height `band`."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:_H, 0:_W].astype(np.float32)
    rgb = (0.10 + _glow()).astype(np.float32)
    if band:
        rgb = rgb + np.float32(band) * np.exp(-(((xx - 0.8 * yy - 150.0) / 30.0) ** 2))[..., None]
    stars = []
    while len(stars) < 60:
        y, x = int(rng.integers(14, _H - 14)), int(rng.integers(14, _W - 14))
        if all(max(abs(y - a), abs(x - b)) >= 20 for a, b in stars):
            stars.append((y, x))
    heights = np.linspace(0.05, 0.6, len(stars))
    rng.shuffle(heights)
    for (y, x), hgt in zip(stars, heights):
        rgb = rgb + np.float32(hgt) * np.clip(1.75 - np.hypot(yy - y, xx - x), 0.0, 1.0)[..., None]   # a disc of 2.5 px
    if noise:
        rgb = rgb + rng.normal(0.0, noise, rgb.shape)
    return rgb.astype(np.float32), stars


def _heights(rgb, stars):
    """A star's centre luma minus the median of the ring 5 to 9 px around it."""
    l = luma(rgb)
    out = []
    for y, x in stars:
        yy, xx = np.mgrid[y - 9:y + 10, x - 9:x + 10]
        d = np.hypot(yy - y, xx - x)
        ring = l[y - 9:y + 10, x - 9:x + 10][(d >= 5) & (d <= 9)]
        out.append(float(l[y, x] - np.median(ring)))
    return np.asarray(out)


def _far(stars, r):
    """True where a pixel is more than r px (a square) from every star."""
    m = np.ones((_H, _W), bool)
    for y, x in stars:
        m[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1] = False
    return m


def _take_off(rgb, sky):
    """The sky's gradient taken off as the pipeline does it: fit, the 5th percentile level, the mask as alpha."""
    h, w = rgb.shape[:2]
    coef, tiles = night.fit(rgb, sky, night.DEFAULTS)
    assert coef is not None and tiles >= 100
    surf = night.surface(coef, h, w, night.DEFAULTS["degree"])
    level = np.percentile(surf[sky], night.DEFAULTS["level"], axis=0).astype(np.float32)
    return ops.sky_gradient(rgb, 100.0, surf - level, sky.astype(np.float32)), surf


def test_zero_is_the_identity():
    x, _ = _sky(1, 0.01)
    off = np.zeros_like(x)
    a = np.ones(x.shape[:2], np.float32)
    assert ops.hot_pixels(x, 0.0) is x
    assert ops.sky_gradient(x, 0.0, off, a) is x
    assert np.array_equal(ops.denoise(x, 40.0, 0.0), ops.denoise(x, 40.0))
    assert np.array_equal(ops.sharpen(x, 30.0, 0.0), ops.sharpen(x, 30.0))
    for s in ({"hot_pixels": 100.0, "sky_gradient": 100.0}, {"denoise_stars": 100.0, "sharpen_stars": 100.0}):
        assert ops.apply_ops(x, s) is x or np.array_equal(ops.apply_ops(x, s), x)
    assert np.array_equal(ops.apply_ops(x, {}), x)


def test_the_slope_goes_and_the_stars_stay():
    x, stars = _sky(2)
    sky = np.ones((_H, _W), bool)
    out, _ = _take_off(x, sky)
    free = _far(stars, 6)
    rng_before = x[free].max(axis=0) - x[free].min(axis=0)
    rng_after = out[free].max(axis=0) - out[free].min(axis=0)
    assert (rng_before > 5.0 / 255.0).all()
    assert (rng_after <= 1.0 / 255.0).all(), rng_after * 255
    before, after = _heights(x, stars), _heights(out, stars)
    assert (np.abs(after - before) <= 0.02 * before).all()
    lift = out - x
    assert lift.max() <= 3.0 / 255.0
    assert (lift > 1e-6).any(axis=2).mean() <= 0.05


def test_noise_a_band_and_the_ground():
    x, stars = _sky(3, 0.01, 0.04)
    sky = np.zeros((_H, _W), bool)
    sky[:280] = True
    x[280:] = 0.05 + 0.1 * np.linspace(0.0, 1.0, _W, dtype=np.float32)[None, :, None]   # a ground
    out, surf = _take_off(x, sky)
    true = (0.10 + _glow()).astype(np.float32)
    diff = (surf - true)[sky]
    assert np.abs(diff - diff.mean(axis=0)).max() <= 1.0 / 255.0
    assert np.array_equal(out[280:], x[280:])
    on_sky = [s for s in stars if s[0] < 270]
    before, after = _heights(x, on_sky), _heights(out, on_sky)
    assert (np.abs(after - before) <= 0.02 * np.abs(before)).all()


def test_stars_are_found_and_noise_is_not():
    x, stars = _sky(4, 0.01, 0.04)
    w = ops.star_weight(luma(x))
    assert w.dtype == np.float32 and w.shape == (_H, _W) and w.min() >= 0.0 and w.max() <= 1.0
    assert all(w[y, x_] > 0.0 for y, x_ in stars)
    assert (w[_far(stars, 6)] == 0.0).all()


def test_stuck_pixels_go_and_stars_stay():
    rng = np.random.default_rng(5)
    h, w = 200, 300
    x = (0.10 + rng.normal(0.0, 1.0 / 255.0, (h, w, 3))).astype(np.float32)
    spots = []
    while len(spots) < 80:
        y, c = int(rng.integers(6, h - 6)), int(rng.integers(6, w - 6))
        if all(max(abs(y - a), abs(c - b)) >= 6 for a, b in spots):
            spots.append((y, c))
    spikes, whites = spots[:50], spots[50:]
    chans = rng.integers(0, 3, len(spikes))
    clean = x.copy()
    for (y, c), k in zip(spikes, chans):
        x[y, c, k] = 0.6
    for y, c in whites:
        x[y, c] = 0.6
    out = ops.hot_pixels(x, 100.0)
    for (y, c), k in zip(spikes, chans):
        near = np.delete(x[y - 1:y + 2, c - 1:c + 2, k].ravel(), 4)
        assert abs(float(out[y, c, k]) - float(np.median(near))) <= 1.0 / 255.0
    for y, c in whites:
        assert np.array_equal(out[y, c], x[y, c])
    half = ops.hot_pixels(x, 50.0)
    y, c = spikes[0]
    k = chans[0]
    assert abs(float(half[y, c, k]) - (0.6 + float(out[y, c, k])) / 2.0) <= 1e-5
    sky, _ = _sky(6, 0.01)
    assert np.array_equal(ops.hot_pixels(sky, 100.0), sky)
    assert clean.shape == x.shape


def test_denoise_and_sharpen_spare_star_cores():
    x, stars = _sky(7, 0.02)
    before = _heights(x, stars)
    keep = [s for s, hgt in zip(stars, before) if hgt >= 0.15]
    assert len(keep) >= 15
    h0 = _heights(x, keep)
    for plain, spared in ((ops.denoise(x, 100.0), ops.denoise(x, 100.0, 100.0)),
                          (ops.sharpen(x, 30.0), ops.sharpen(x, 30.0, 100.0))):
        assert (np.abs(_heights(spared, keep) - h0) <= 0.02 * h0).all()
        assert (np.abs(_heights(plain, keep) - h0) > 0.20 * h0).any()


def test_registered_in_the_frozen_order():
    new = ("hot_pixels", "sky_gradient", "denoise_stars", "sharpen_stars")
    for n in new:
        assert n in ops.OP_NAMES and ops.OP_BOUNDS[n] == (0.0, 100.0) and n in ops.SPATIAL_OPS
    assert ops.OP_NAMES[:2] == ("hot_pixels", "sky_gradient")
    assert ops.STEPS[0] == ("hot_pixels", "sky_gradient")
    assert ops.STEPS[ops.STEPS.index(("denoise", "denoise_stars")) + 1] == ("sharpen", "sharpen_stars")
    assert ops._TONE_KEYS == {k for step in ops.STEPS[1:6] for k in step}
    assert ops._TONE_KEYS.isdisjoint(new)
    assert ops._COLOUR_KEYS.isdisjoint(new)
    x, _ = _sky(8, 0.02)
    want = ops.sharpen(ops.denoise(x, 60.0, 100.0), 20.0, 100.0)
    got = ops.apply_ops(x, {"denoise": 60.0, "sharpen": 20.0, "denoise_stars": 100.0, "sharpen_stars": 100.0})
    assert np.array_equal(got, want)


# ---- tests 7 to 9: from the settings to the picture -------------------------------------------------

_NIGHT_OPS = ("hot_pixels", "sky_gradient", "denoise_stars", "sharpen_stars")
_SKY_ROWS = int(_H * 0.7)


def _photo(path, base=0.063, iso=6400):
    """A night frame: the sky of Measured 2 (a little noise, a Milky Way band) scaled to a background of
    `base`, a flat ground in the bottom 30 %, the ISO in its EXIF. base 0.063: median luma 0.0709."""
    rgb, _ = _sky(9, 0.004, 0.03)
    rgb = np.clip(rgb * np.float32(base / 0.10), 0.0, 1.0)
    rgb[_SKY_ROWS:] = 0.4 * base
    ex = PILImage.Exif()
    ex[0x8769] = {0x8827: iso}
    PILImage.fromarray((rgb * 255 + 0.5).astype(np.uint8), "RGB").save(path, quality=98, exif=ex.tobytes())


class _SkyTop:
    """A segmenter whose sky is the top 70 % of the frame, and which finds nothing else."""
    name = "fake"

    def segment(self, img, concepts):
        m = np.zeros(img.rgb.shape[:2], bool)
        m[:int(m.shape[0] * 0.7)] = True
        return [RegionMask(name=c, mask=m, score=0.9, coverage=float(m.mean()), instance_count=1)
                for c in concepts if c == "sky"]


def _astro(**kw):
    """The astro pack's settings written out (stub), so no genre is needed."""
    return dict(backend="stub", preset="landscape", look="astro", night_sky=True, denoise=True, **kw)


def _pipe(sky=False, **settings):
    pipe = Pipeline.build(RunConfig(**({"backend": "stub"} | settings)))
    if sky:      # as a render worker has it (batch._worker_init): backend sam3
        pipe.cfg = pipe.cfg.model_copy(update={"backend": "sam3"})
        pipe.segmenter = _SkyTop()
    return pipe


def _night_notes(res):
    return [n for n in res.notes if n.startswith("night sky:")]


def test_night_detection_and_the_rules_golden(tmp_path):
    pipe = _pipe()
    with open(config_path("rules.toml"), "rb") as fh:
        assert pipe.night_cfg == night.DEFAULTS == tomllib.load(fh)["night_sky"]
    text = open(config_path("rules.toml"), encoding="utf-8").read()
    assert "\nlevel      = 5\n" in text
    bad = tmp_path / "rules.toml"
    bad.write_text(text.replace("\nlevel      = 5\n", "\nlevel      = 500\n"), encoding="utf-8")
    with pytest.raises(ValueError) as e:
        Pipeline.build(RunConfig(backend="stub", rules_path=str(bad)))
    assert "[night_sky] level" in str(e.value)
    for med, iso, want in ((0.18, 12800, True), (0.25, 1600, True), (0.26, 12800, False),
                           (0.05, 800, False), (0.05, 0, False)):
        assert night.is_night(SimpleNamespace(lum_p50=med, iso=iso), night.DEFAULTS) is want, (med, iso)

    src = str(tmp_path / "n.jpg")
    _photo(src)
    out, res = _pipe(sky=True, **_astro()).process_one(src)
    assert res.scene == "night"
    want = {"hot_pixels": 100.0, "sky_gradient": 100.0, "exposure": 0.5249, "shadows_ev": 0.24,
            "denoise": 63.4927, "denoise_stars": 100.0, "sharpen": 2.8, "sharpen_stars": 100.0}
    assert set(res.global_settings) == set(want)
    for k, v in want.items():
        assert round(res.global_settings[k], 4) == v, k
    assert [m.target for m in res.masks] == ["sky"] and round(res.masks[0].ops["shadows_ev"], 4) == 0.32
    notes = _night_notes(res)
    assert notes[0] == "night sky: a night frame (median 0.07, ISO 6400): the night rules"
    assert "night sky: no stuck pixel found" in notes
    assert notes[-1].startswith("night sky: the sky's gradient taken off, ") and "tiles)" in notes[-1]
    assert len(notes) == 3


def test_off_and_day_frames_are_unchanged(tmp_path):
    day = str(tmp_path / "day.jpg")
    _photo(day, base=0.45, iso=100)
    a, ra = _pipe(sky=True, **_astro()).process_one(day)
    b, rb = _pipe(sky=True, **{**_astro(), "night_sky": False}).process_one(day)
    assert np.array_equal(a, b) and ra.scene == rb.scene != "night"
    assert _night_notes(ra)[0].startswith("night sky: not a night frame (median 0.") and "the usual rules" in _night_notes(ra)[0]
    assert len(_night_notes(ra)) == 1 and not _night_notes(rb)
    assert [n for n in ra.notes if not n.startswith("night sky:")] == rb.notes
    assert not any(k in ra.global_settings for k in _NIGHT_OPS)

    dark = str(tmp_path / "dark.jpg")
    _photo(dark)
    _, rd = _pipe(sky=True, **{**_astro(), "night_sky": False}).process_one(dark)
    assert rd.scene != "night" and not _night_notes(rd)
    assert not any(k in rd.global_settings or any(k in m.ops for m in rd.masks) for k in _NIGHT_OPS)

    class Proposes:
        name = "proposes"

        def decide(self, ctx):
            four = {k: 50.0 for k in _NIGHT_OPS}
            return {**four, "sharpen": 3.0}, {"sky": {**four, "shadows_ev": 0.2}}

    def run(**kw):
        pipe = _pipe(sky=True, **kw)
        pipe.decider = Proposes()
        _, res = pipe.process_one(dark)
        return res

    res = run(denoise=True)                    # the setting is off: none of the four is left
    assert not any(k in res.global_settings or any(k in m.ops for m in res.masks) for k in _NIGHT_OPS)
    res = run(night_sky=True)                  # denoise off: its star value goes with it
    assert {k for k in _NIGHT_OPS if k in res.global_settings} == {"hot_pixels", "sky_gradient", "sharpen_stars"}
    sky = [m for m in res.masks if m.target == "sky"]
    assert sky and not any(k in m.ops for m in sky for k in ("hot_pixels", "sky_gradient", "denoise_stars"))


def test_the_astro_pack_end_to_end(tmp_path):
    src = str(tmp_path / "n.jpg")
    _photo(src)

    def gap(rgb):
        y = luma(rgb)
        return float(np.median(y[240:270]) - np.median(y[10:40]))

    with_sky, r1 = _pipe(sky=True, **_astro()).process_one(src)
    without, r0 = _pipe(**_astro()).process_one(src)
    assert any("no sky found" in n for n in _night_notes(r0))
    assert gap(without) > 0.01
    assert gap(with_sky) < gap(without) / 3.0
    assert not r1.needs_review, r1.review_reason     # the look does not set preserve_exposure: the darkness check is on
    assert r1.audit["run_config"]["night_sky"] is True
    assert json.loads(r1.model_dump_json())["audit"]["run_config"]["night_sky"] is True


def test_the_switches(tmp_path):
    from autoedit import cli, teach
    from autoedit.web import service
    assert service.make_cfg({"night_sky": True}).night_sky is True and service.make_cfg({}).night_sky is False
    c = service.make_cfg({"genre": "astro"})
    assert (c.night_sky, c.denoise, c.look, c.preset, c.genre) == (True, True, "astro", "landscape", "astro")
    assert "night sky:" in teach.NOTES["regions"]
    src = tmp_path / "in"
    src.mkdir()
    _photo(str(src / "n.jpg"))
    assert cli.main(["run", str(src), str(tmp_path / "o1"), "--backend", "stub", "--workers", "1", "--night-sky"]) == 0
    assert cli.main(["run", str(src), str(tmp_path / "o2"), "--backend", "stub", "--workers", "1", "--genre", "astro"]) == 0
    with open(tmp_path / "o2" / "n.autoedit.json") as fh:
        side = json.load(fh)
    assert side["scene"] == "night" and side["audit"]["run_config"]["night_sky"] is True
    with open(tmp_path / "o1" / "n.autoedit.json") as fh:
        assert json.load(fh)["audit"]["run_config"]["night_sky"] is True




def test_a_day_frame_never_takes_the_night_tools(tmp_path):
    """With the setting on, a decider that proposes the night ops for a day frame is overruled (Checker, F31)."""
    day = str(tmp_path / "day.jpg")
    _photo(day, base=0.45, iso=100)
    pipe = _pipe(sky=True, **_astro())
    inner = pipe.decider

    class Eager:                     # an LLM decider that asks for every night op
        def __getattr__(self, k):
            return getattr(inner, k)

        def decide(self, ctx):
            g, r = inner.decide(ctx)
            return {**g, "sky_gradient": 25.0, "hot_pixels": 25.0, "denoise_stars": 50.0, "sharpen_stars": 50.0}, r
    pipe.decider = Eager()
    _, res = pipe.process_one(day)
    assert not any(k in res.global_settings for k in ("hot_pixels", "sky_gradient", "denoise_stars", "sharpen_stars"))
    assert not any(n.startswith("night sky: the sky") for n in res.notes)
