"""F52: lens vignette profiles from the camera's own preview (lens.py, T1: the lens code)."""

import math
import os
import tomllib

import numpy as np
from PIL import Image as PILImage

from autoedit import lens
from autoedit.imageio import Image, linear_to_srgb, srgb_to_linear


def _scene(h=1920, w=2880, seed=0):
    rng = np.random.default_rng(seed)
    a = np.full((h, w), 0.05, np.float32)
    a[: int(h * 0.45)] = 0.4
    for _ in range(400):
        y, x = rng.integers(0, h - 80), rng.integers(0, w - 80)
        a[y:y + rng.integers(10, 80), x:x + rng.integers(10, 80)] = rng.random() * 0.5 + 0.02
    return a


def _pair(g_fn, h=1920, w=2880, ph=480, pw=720):
    sc = _scene(h, w)
    rho = lens._rho(h, w)
    fall = (2.0 ** -(0.3 * rho ** 2)).astype(np.float32)
    lin = np.repeat((sc * fall)[..., None], 3, -1)
    gain = (2.0 ** g_fn(lens._rho(ph, pw))).astype(np.float32)
    small = np.asarray(PILImage.fromarray(sc * fall).resize((pw, ph), PILImage.BOX))
    lg = np.log2(np.maximum(small * gain, 1e-6))
    t = 1 / (1 + 2.0 ** (-1.2 * (lg - 0.25 + 3)))        # logistic tone curve in log2 light
    t = (t - t.min()) / (t.max() - t.min()) * 0.97
    pv = np.repeat(t[..., None], 3, -1).astype(np.float32)
    lin_small = np.asarray(PILImage.fromarray(lin[..., 0]).resize((pw * 2, ph * 2), PILImage.BOX))
    return np.repeat(lin_small[..., None], 3, -1), pv


def test_camera_gain_synthetic():
    g = lambda r: 0.25 * r ** 2 + 0.2 * r ** 4
    lin, pv = _pair(g)
    out = lens.camera_gain(lin, pv)
    assert not isinstance(out, str), out
    for i, mid in enumerate(lens._VIG_MID):
        if mid >= 0.1:
            assert abs(out[i] - g(mid)) < 0.03, (mid, out[i], g(mid))
    lin0, pv0 = _pair(lambda r: 0 * r)
    out0 = lens.camera_gain(lin0, pv0)
    assert np.abs(out0[lens._VIG_MID >= 0.1]).max() < 0.03
    assert "framed" in lens.camera_gain(lin[:, : lin.shape[1] // 2], pv)
    assert "line up" in lens.camera_gain(lin, np.random.default_rng(1).random(pv.shape).astype(np.float32))
    assert "smaller" in lens.camera_gain(lin, pv[::2, ::2][:200, :300])


def test_bucket():
    assert lens.bucket(105, 11) == (76.11, 107.63, 9.51, 13.45)
    assert lens.bucket(105, 10)[2:] == lens.bucket(105, 11)[2:]
    assert lens.bucket(105, 8)[2:] != lens.bucket(105, 11)[2:]
    edge = 2 ** (12.5 / 2)
    assert lens._bin(edge) != lens._bin(edge * 0.999999) and lens._bin(edge) == lens._bin(edge * 1.0001)


def test_rolloff():
    ones = np.ones((4, 4), np.float32)
    lin = np.random.default_rng(0).random((4, 4, 3)).astype(np.float32) * 0.99
    assert np.array_equal(lens._rolloff(lin, ones), lin)
    for Gv in (1.1, 1.3, 1.6):
        G = ones * Gv
        v = np.linspace(0, 0.9999, 1000, dtype=np.float32)
        l = np.stack([v, v * 0.5, v * 0.25], -1)[None]
        out = lens._rolloff(l, np.full((1, 1000), Gv, np.float32))
        assert out.max() < 1.0 and (out >= l - 1e-7).all()
        one = lens._rolloff(np.ones((1, 1, 3), np.float32), np.full((1, 1), Gv, np.float32))
        assert (one == 1.0).all()
        lo = l[:, v * Gv <= lens._VIG_KNEE]
        assert np.allclose(lens._rolloff(l, np.full((1, 1000), Gv, np.float32))[:, : lo.shape[1]], lo * Gv, rtol=1e-5)


def _png(tmp_path, name="a.png", cam="Cam", lens_name="Lens", f=105.0, n=11.0, rgb=None):
    rgb = np.full((200, 300, 3), 0.2, np.float32) if rgb is None else rgb
    im = PILImage.fromarray((rgb * 255).astype(np.uint8))
    ex = PILImage.Exif()
    ex[0x0110] = cam
    ifd = ex.get_ifd(0x8769)
    if lens_name:
        ifd[0xA434] = lens_name
    ifd[0x920A], ifd[0x829D] = f, n
    p = str(tmp_path / name)
    im.save(p, exif=ex)
    return p


def _profile(tmp_path, k=(0.3, 0.0, 0.0), cam="Cam", lens_name="Lens"):
    f = str(tmp_path / "lens_profiles.toml")
    lens.write_profiles(f, {cam: {lens_name: {"buckets": [[*lens.bucket(105, 11), 9, *k]]}}})
    return f


def test_correct_vignette(tmp_path):
    p = _png(tmp_path)
    prof = _profile(tmp_path)
    rgb = np.asarray(PILImage.open(p).convert("RGB"), np.float32) / 255.0
    img = Image(rgb=rgb, path=p, decode_info={"transfer": "srgb"})
    out, rep = lens.correct_vignette(img, prof)
    assert rep["applied"] and rep["frames"] == 9 and rep["bucket"] == list(lens.bucket(105, 11))
    assert abs(rep["corner_ev"] - 0.3 * 0.86 ** 2) < 0.001
    assert np.abs(out[100, 150] - rgb[100, 150]).max() < 1e-4                       # centre
    r = float(lens._rho(*rgb.shape[:2])[5, 5])
    want = float(linear_to_srgb(srgb_to_linear(rgb[5, 5]) * 2 ** (0.3 * r * r))[0])
    assert abs(float(out[5, 5, 0]) - want) < 0.01
    for kw in (dict(f=24.0), dict(lens_name=""), dict(cam="Other")):
        q = _png(tmp_path, "b.png", **kw)
        o, rep = lens.correct_vignette(Image(rgb=rgb, path=q, decode_info={"transfer": "srgb"}), prof)
        assert np.array_equal(o, rgb) and not rep["applied"] and rep["reason"]
    o, rep = lens.correct_vignette(img, str(tmp_path / "missing.toml"))
    assert np.array_equal(o, rgb) and not rep["applied"]


def test_build_profiles(tmp_path):
    g = lambda r: 0.25 * r ** 2 + 0.1 * r ** 4
    curve = g(lens._VIG_MID)
    files, rng = [], np.random.default_rng(0)
    for i in range(7):
        files.append(_png(tmp_path, f"secret{i}.png", lens_name="LensA", f=105.0, n=11.0))
    for i in range(3):
        files.append(_png(tmp_path, f"other{i}.png", lens_name="LensA", f=24.0, n=4.0))
    old_measure, lens.measure = lens.measure, lambda p: curve + rng.normal(0, 0.005, curve.shape)
    try:
        profiles, rep = lens.build_profiles(files)
    finally:
        lens.measure = old_measure
    assert len(rep["rows"]) == 1 and len(rep["short"]) == 1 and rep["short"][0][3] == 3
    row = profiles["Cam"]["LensA"]["buckets"][0]
    assert row[4] == 7
    for rho in (0.3, 0.6, 0.86, 0.98):
        assert abs(lens._poly(rho, row[5:8]) - g(rho)) < 0.02
    f = str(tmp_path / "out" / "lens_profiles.toml")
    lens.write_profiles(f, profiles)
    text = open(f).read()
    assert "secret" not in text and "other" not in text and str(tmp_path) not in text
    lens.write_profiles(f, {"Cam": {"LensB": {"buckets": [[1.0, 2.0, 3.0, 4.0, 5, 0.1, 0.0, 0.0]]}}})
    d = tomllib.loads(open(f).read())
    assert set(d["Cam"]) == {"LensA", "LensB"}
    assert math.isclose(d["Cam"]["LensA"]["buckets"][0][5], row[5])
    open(f, "w").write("not [valid toml")   # a corrupt file is reported and left alone
    try:
        lens.write_profiles(f, profiles)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    assert open(f).read() == "not [valid toml"


def test_malformed_profiles(tmp_path):
    p = _png(tmp_path)
    rgb = np.asarray(PILImage.open(p).convert("RGB"), np.float32) / 255.0
    img = Image(rgb=rgb, path=p, decode_info={"transfer": "srgb"})
    lo, hi = lens.bucket(105, 11)[:2], lens.bucket(105, 11)[2:]
    good = [*lens.bucket(105, 11), 9, 0.3, 0.0, 0.0]
    cases = ['Cam = "x"', "[Cam.Lens]\nbuckets = 5", f"[Cam.Lens]\nbuckets = [[{lo[0]}, {lo[1]}, {hi[0]}, 9]]",
             "[Cam.Lens]\nbuckets = [[0, 0, 0, 0, 9, 0.3, 0, 0]]",
             f"[Cam.Lens]\nbuckets = [[{lo[0]}, {lo[1]}, {hi[0]}, {hi[1]}, 9, 1000, 0, 0]]",
             f"[Cam.Lens]\nbuckets = [[{lo[0]}, {lo[1]}, {hi[0]}, {hi[1]}, 9, inf, 0, 0]]"]
    for i, text in enumerate(cases):
        f = tmp_path / f"bad{i}.toml"
        f.write_text(text.replace("Cam.Lens", "Cam.\"Lens\""))
        out, rep = lens.correct_vignette(img, str(f))
        assert np.array_equal(out, rgb) and not rep["applied"] and rep["reason"], text
    # an in-range gain is capped, never NaN
    f = tmp_path / "wild.toml"
    f.write_text('[Cam."Lens"]\nbuckets = [[%s, %s, %s, %s, 9, 15.0, 0, 0]]' % (*lens.bucket(105, 11),))
    out, rep = lens.correct_vignette(img, str(f))
    assert np.isfinite(out).all() and out.max() <= 1.0


def test_measure_real_files():
    corpus = os.environ.get("AUTOEDIT_RAW_CORPUS")
    if not corpus:
        return
    files = sorted(os.path.join(corpus, n) for n in os.listdir(corpus)
                   if n.lower().endswith((".cr2", ".cr3", ".nef", ".arw", ".dng", ".raf", ".orf")))[:6]
    for p in files:
        c = lens.measure(p)
        assert isinstance(c, str) or (0 <= float(lens._poly(0.86, [0, 0, 0]) + np.interp(0.86, lens._VIG_MID, c)) <= 1.5)


def test_wiring(tmp_path):
    import argparse
    from autoedit import cli
    from autoedit.contract import RunConfig
    from autoedit.imageio import raw_opts_from
    from autoedit.web import service
    p = argparse.ArgumentParser()
    cli._add_run_flags(p)
    ns = p.parse_args(["in", "--lens-vignette"])
    ns.configs_dir = ns.config = None
    assert cli.load_run_config(ns).lens_vignette is True
    ns = p.parse_args(["in"])
    ns.configs_dir = ns.config = None
    assert cli.load_run_config(ns).lens_vignette is False
    conf = tmp_path / "run.toml"
    conf.write_text("[decode]\nlens_vignette = true\n")
    ns.config = str(conf)
    assert cli.load_run_config(ns).lens_vignette is True
    assert service.make_cfg({"lens_vignette": True}).lens_vignette is True
    assert "lens_profiles" not in raw_opts_from(RunConfig(profile_dir="X"))
    assert raw_opts_from(RunConfig(lens_vignette=True, profile_dir="X"))["lens_profiles"] == os.path.join("X", "lens_profiles.toml")
    assert raw_opts_from(RunConfig(lens_vignette=True))["lens_profiles"] is None


def test_png_untouched_and_half_size(tmp_path):
    from autoedit import imageio
    p = _png(tmp_path)
    plain = imageio.load_image(p)
    on = imageio.load_image(p, decode={"lens_vignette": True, "lens_profiles": _profile(tmp_path)})
    assert np.array_equal(on.rgb, plain.rgb) and "vignette" not in on.decode_info
    # a RAW-named path: the decode is faked. Half-size skips only lens_ca; the vignette is applied.
    raw = str(tmp_path / "a.cr2")
    info = {"transfer": "srgb"}
    old, imageio._load_image = imageio._load_image, lambda path, decode=None: Image(rgb=plain.rgb.copy(), path=p, decode_info=dict(info))
    try:
        opts = {"lens_vignette": True, "lens_profiles": _profile(tmp_path)}
        full = imageio.load_image(raw, decode=opts)
        assert full.decode_info["vignette"]["applied"] and not np.array_equal(full.rgb, plain.rgb)
        info["half_size"] = True
        half = imageio.load_image(raw, decode={**opts, "lens_ca": True, "half_size": True})
        assert half.decode_info["vignette"]["applied"] and half.decode_info["lens_skipped"] and "lens" not in half.decode_info
        # asked for half size but LibRaw returned full size (linear DNG): lens_ca runs, no skip note
        del info["half_size"]
        dng = imageio.load_image(raw, decode={**opts, "lens_ca": True, "half_size": True})
        assert "lens_skipped" not in dng.decode_info and "lens" in dng.decode_info
    finally:
        imageio._load_image = old


def test_cli_lens_profile(tmp_path):
    import contextlib
    import io
    from autoedit import cli
    empty = tmp_path / "empty"
    empty.mkdir()
    prof = tmp_path / "prof"
    assert cli.main(["lens-profile", str(empty), "--profile", str(prof)]) == 1
    d = tmp_path / "raws"
    d.mkdir()
    curve = 0.25 * lens._VIG_MID ** 2
    for i in range(6):
        _png(d, f"r{i}.png")
        os.replace(d / f"r{i}.png", d / f"r{i}.cr2")
    old = lens.lens_key, lens.measure
    lens.lens_key, lens.measure = (lambda p: ("Cam", "Lens", 105.0, 11.0)), (lambda p: curve)
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert cli.main(["lens-profile", str(d), "--profile", str(prof), "--dry-run"]) == 0
        assert "corners +" in buf.getvalue() and not (prof / "lens_profiles.toml").exists()
        assert cli.main(["lens-profile", str(d), "--profile", str(prof)]) == 0
        assert (prof / "lens_profiles.toml").exists()
        (prof / "lens_profiles.toml").write_text("not [valid")   # corrupt: exit non-zero, file untouched
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            assert cli.main(["lens-profile", str(d), "--profile", str(prof)]) == 1
        assert "cannot be read" in err.getvalue() and (prof / "lens_profiles.toml").read_text() == "not [valid"
    finally:
        lens.lens_key, lens.measure = old
