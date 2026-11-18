"""F33 T1: the alignment and merge math of `autoedit merge` (frames move by whole pixels, nothing is resampled)."""

import copy

import numpy as np
import pytest

from autoedit import merge
from autoedit.edit.filters import gaussian_blur
from autoedit.imageio import linear_to_srgb, luma, srgb_to_linear

THR = merge.load_config()


def _thr(**over):
    t = copy.deepcopy(THR)
    for table, keys in over.items():
        t[table].update(keys)
    return t


def _frames(lins):
    return lambda k: linear_to_srgb(np.clip(lins[k], 0, 1)).copy()


def _scene(h=120, w=160, seed=0):                 # linear light, 0.08 to 0.6: soft structure and hard-edged patches
    rng = np.random.default_rng(seed)
    a = np.zeros((h, w), np.float32)
    for sigma in (1, 3, 9):
        g = gaussian_blur(rng.random((h, w)).astype(np.float32), sigma)
        a += (g - g.min()) / (g.max() - g.min())
    for _ in range(14):
        y, x = int(rng.integers(0, h - 8)), int(rng.integers(0, w - 8))
        a[y:y + int(rng.integers(6, h // 3)), x:x + int(rng.integers(6, w // 3))] += float(rng.uniform(-1.0, 1.0))
    a = (a - a.min()) / (a.max() - a.min())
    return np.stack([0.08 + 0.52 * a, 0.08 + 0.45 * a, 0.10 + 0.40 * a], axis=-1).astype(np.float32)


def _bracket(noise=0.002, seed=2):               # 13 stops of range; frames at 0, -2, +2 EV (a camera's order)
    h, w = 120, 160
    ramp = np.linspace(-9.0, 4.0, w, dtype=np.float32)[None, :].repeat(h, 0)
    radiance = (2.0 ** ramp)[..., None] * np.array([1.0, 0.9, 0.8], np.float32)
    radiance = radiance * (0.4 + 2.0 * _scene(h, w, seed)[..., :1])
    rng = np.random.default_rng(seed)
    frames = [np.clip(radiance * g, 0.0, 1.0) + rng.normal(0.0, noise, radiance.shape).astype(np.float32)
              for g in (1.0, 0.25, 4.0)]
    return radiance, frames


def _sky(h=120, w=160, seed=10, split=0.4):       # a bright upper part and a dark lower part
    s = _scene(h, w, seed)
    out = 0.03 + 0.28 * s
    cut = int(h * split)
    out[:cut] = 0.35 + 0.75 * s[:cut]
    return out.astype(np.float32)


def _blur(x, s):
    return np.stack([gaussian_blur(x[..., c], s) for c in range(3)], axis=-1)


def _subpixel(x, dy, dx):                         # Fourier shift: content moves down by dy and right by dx
    ky = np.fft.fftfreq(x.shape[0])[:, None]
    kx = np.fft.fftfreq(x.shape[1])[None, :]
    ph = np.exp(-2j * np.pi * (ky * dy + kx * dx))
    return np.stack([np.real(np.fft.ifft2(np.fft.fft2(x[..., c]) * ph)) for c in range(3)], axis=-1).astype(np.float32)


def _oval(base, cx, seed):                        # something that stands at x = cx in this frame
    yy, xx = np.mgrid[0:base.shape[0], 0:base.shape[1]]
    out = base.copy()
    out[(((xx - cx) / 28) ** 2 + ((yy - 120) / 40) ** 2) <= 1.0] = [0.75, 0.7, 0.6]
    return out + np.random.default_rng(seed).normal(0.0, 0.01, out.shape).astype(np.float32)


def _error(e_fn):
    try:
        e_fn()
    except ValueError as e:
        return str(e)
    raise AssertionError("did not raise ValueError")


def test_average_cuts_noise_by_root_n():
    scene = _scene()
    rng = np.random.default_rng(1)
    lins = [scene + rng.normal(0.0, 0.02, scene.shape).astype(np.float32) for _ in range(16)]
    out, found = merge.merge_frames(_frames(lins), 16, "average", THR)
    err = srgb_to_linear(out) - scene
    assert out.dtype == np.float32 and out.shape == scene.shape
    assert abs(float(err.std()) - 0.02 / 4) < 0.1 * 0.02 / 4
    assert abs(float(err.mean())) < 0.0005
    assert found["flags"] == [] and found["cut"] == [0, 0]
    assert all(f["shift"] == [0, 0] for f in found["frames"])
    out2, _ = merge.merge_frames(_frames(lins[:2]), 2, "average", THR)
    s2 = float((srgb_to_linear(out2) - scene).std())
    assert abs(s2 - 0.02 / np.sqrt(2)) < 0.1 * 0.02 / np.sqrt(2)


def test_brackets_keep_what_a_frame_holds():
    radiance, lins = _bracket()
    load = _frames(lins)
    out, found = merge.merge_frames(load, 3, "brackets", THR)
    disp = [load(k) for k in range(3)]
    top = np.min([d.max(axis=2) for d in disp], axis=0)
    safe = top < 0.98
    assert safe.mean() > 0.7 and (disp[0].max(axis=2) >= 0.98).mean() > 0.1
    assert float(out.max(axis=2)[safe].max()) <= 0.98
    assert [round(f["exposure_ev"], 1) for f in found["frames"]] == [2.0, 0.0, 4.0]
    lin, want = srgb_to_linear(out), radiance / 4
    mid = (want > 0.002) & (want < 0.9)
    assert float(np.median(np.abs(lin[mid] - want[mid]) / want[mid])) < 0.02
    dark = (want > 0.001) & (want < 0.01)
    one = srgb_to_linear(disp[1]) - want
    assert float((lin - want)[dark].std()) < 0.10 * float(one[dark].std())
    assert found["flags"] == [] and found["cut"] == [0, 0]


def test_brackets_trust_only_what_is_well_exposed():
    radiance, _ = _bracket()
    lo = np.clip(radiance * 0.5, 0, 1)
    dark = luma(linear_to_srgb(lo)) < 0.05
    assert dark.mean() > 0.03
    lo[dark] = 0.0
    out, found = merge.merge_frames(_frames([lo, np.clip(radiance, 0, 1)]), 2, "brackets", THR, align=False)
    want = (radiance * 0.5)[dark]
    got = srgb_to_linear(out)[dark]
    assert float(np.median(np.abs(got - want) / want)) < 0.02
    assert [round(f["exposure_ev"], 1) for f in found["frames"]] == [0.0, 1.0]


def test_brackets_fail_when_the_steps_are_too_far_apart():
    radiance, _ = _bracket()
    lins = [np.clip(radiance * 2.0 ** -9, 0, 1), np.clip(radiance * 2.0 ** 3, 0, 1)]
    msg = _error(lambda: merge.merge_frames(_frames(lins), 2, "brackets", THR, align=False))
    assert "too far apart" in msg


def test_focus_picks_the_sharp_frame():
    scene = _scene(160, 200, 3)
    soft = _blur(scene, 3.0)
    f0, f1 = scene.copy(), scene.copy()
    f0[:, 100:] = soft[:, 100:]          # frame 0 is soft from column 100 on
    f1[:, :100] = soft[:, :100]          # frame 1 is soft before it
    out, found = merge.merge_frames(_frames([f0, f1]), 2, "focus", THR)
    lin = srgb_to_linear(out)

    def err(x, cols):
        return float(np.abs(x[:, cols] - scene[:, cols]).mean())
    left, right, whole = slice(0, 90), slice(110, 200), slice(0, 200)
    assert err(lin, left) < 0.1 * err(f1, left)
    assert err(lin, right) < 0.1 * err(f0, right)
    assert err(lin, whole) < 0.25 * min(err(f0, whole), err(f1, whole))
    assert found["flags"] == [] and all(f["shift"] == [0, 0] for f in found["frames"])


def test_trails_take_the_brightest_frame():
    lins = []
    for k in range(6):
        f = 0.02 + 0.02 * _scene(80, 120, 4)
        f = f + np.random.default_rng(k).normal(0.0, 0.002, f.shape).astype(np.float32)
        f[20 + 5 * k, 30 + 10 * k] = [0.9, 0.85, 0.7]
        lins.append(f.astype(np.float32))
    load = _frames(lins)
    out, found = merge.merge_frames(load, 6, "trails", THR)
    lin = srgb_to_linear(out)
    for k in range(6):
        assert (lin[20 + 5 * k, 30 + 10 * k] > 0.69).all()
    disp = np.stack([load(k) for k in range(6)])
    best = np.argmax(luma(disp), axis=0)
    want = np.take_along_axis(srgb_to_linear(disp), best[None, ..., None], axis=0)[0]
    assert float(np.abs(lin - want).max()) < 1e-5
    assert found["flags"] == [] and all(f["moved"] is None and f["shift"] == [0, 0] for f in found["frames"])


def test_a_shifted_frame_is_aligned():
    a = luma(_scene(120, 160, 5))
    assert merge._phase(a, np.roll(a, (3, -5), axis=(0, 1))) == (3, -5)
    t = _thr(align={"max_shift": 0.05})
    big = _scene(280, 360, 5)
    ref = big[20:260, 20:340]

    def moved(dy, dx):                            # the content of ref, dy down and dx right
        return big[20 - dy:260 - dy, 20 - dx:340 - dx]
    for (dy, dx), gain in [((5, -3), 1.0), ((-7, 6), 2.0), ((0, 0), 1.0), ((1, 0), 0.25), ((-12, 14), 0.5)]:
        mode = "average" if gain == 1.0 else "brackets"
        _, found = merge.merge_frames(_frames([ref, moved(dy, dx) * gain]), 2, mode, t, ref=0)
        assert found["frames"][1]["shift"] == [dy, dx], (dy, dx, gain)
        assert found["cut"] == [abs(dy), abs(dx)] and found["flags"] == []
    two = [ref, big[15:255, 23:343]]
    out, found = merge.merge_frames(_frames(two), 2, "average", t, ref=0)
    assert found["frames"][1]["shift"] == [5, -3] and found["frames"][1]["moved"] < 0.002
    assert out.shape == (235, 317, 3)
    assert float(np.abs(out - linear_to_srgb(ref[0:235, 3:320])).max()) < 1e-4
    _, found = merge.merge_frames(_frames(two), 2, "average", t, align=False, ref=0)
    assert found["flags"] == ["moved"]
    for dy, dx in [(2.4, -1.7), (-0.3, 3.6)]:
        _, found = merge.merge_frames(_frames([ref, _subpixel(ref, dy, dx)]), 2, "average", t, ref=0)
        assert found["frames"][1]["shift"] == [int(round(dy)), int(round(dx))], (dy, dx)
    sky = _sky(260, 340, 16, split=0.6)
    _, found = merge.merge_frames(_frames([sky[10:250, 10:330], sky[13:253, 6:326] * 4.0]), 2, "brackets", t, ref=0)
    assert found["frames"][1]["shift"] == [-3, 4]
    soft = _blur(_scene(240, 320, 30), 10)
    rng = np.random.default_rng(7)
    lins = [soft + rng.normal(0.0, 0.01, soft.shape).astype(np.float32) for _ in range(2)]
    _, found = merge.merge_frames(_frames(lins), 2, "average", t, ref=0)
    assert found["frames"][1]["shift"] == [0, 0]


def test_a_frame_too_far_off_refuses_the_group():
    big = _scene(260, 340, 5)
    ref = big[10:250, 10:330]
    msg = _error(lambda: merge.merge_frames(_frames([ref, big[4:244, 10:330]]), 2, "average", THR, ref=0))
    assert "6 px off" in msg and "the limit is 4 px" in msg
    msg = _error(lambda: merge.merge_frames(_frames([ref, big[10:250, 0:320] * 0.5]), 2, "brackets", THR, ref=0))
    assert "10 px off" in msg
    other = _scene(240, 320, 99)
    _, found = merge.merge_frames(_frames([ref, other]), 2, "average", THR, align=False, ref=0)
    assert found["flags"] == ["moved"] and found["frames"][1]["moved"] > 0.05



def test_checker_cases_far_off_on_fine_texture_and_a_dark_channel(tmp_path):
    """A frame far off on fine texture is refused, not merged where the climb settled; a channel with no
    signal in every frame does not break the alignment; the brackets' ramps and the copy sizes are checked."""
    rng = np.random.default_rng(4)
    tex = gaussian_blur(rng.random((720, 960)).astype(np.float32), 1.5)
    tex = np.stack([0.15 + 0.5 * tex, 0.14 + 0.45 * tex, 0.12 + 0.4 * tex], axis=-1).astype(np.float32)
    ref = tex[60:660, 80:880]
    for dy, dx in ((30, 15), (60, 30)):
        moved = tex[60 + dy:660 + dy, 80 + dx:880 + dx]
        msg = _error(lambda: merge.merge_frames(_frames([ref, moved]), 2, "average", THR, ref=0))
        assert msg and "px off" in msg, (dy, dx, msg)
    red = _scene(240, 320, 3)
    red[..., 2] = 0.0                                      # deep red light: no blue anywhere
    out, found = merge.merge_frames(_frames([red, red.copy(), red.copy()]), 3, "average", THR)
    assert all(fr["shift"] == [0, 0] or tuple(fr["shift"]) == (0, 0) for fr in found["frames"])
    assert np.isfinite(out).all()
    for text in ("[brackets]\nlow = [0.1]\n", "[brackets]\nhigh = [0.98, 0.9]\n", "[align]\nproxy_px = 0.5\n"):
        d = tmp_path / f"c{len(list(tmp_path.iterdir()))}"
        d.mkdir()
        (d / "merge.toml").write_text(text)
        with pytest.raises(ValueError):
            merge.load_config(str(d))


def test_a_moved_group_is_flagged():
    base = _scene(240, 320, 6)
    still = [_oval(base, 100, s) for s in (1, 2, 3)]
    out, found = merge.merge_frames(_frames(still), 3, "average", THR)
    assert found["flags"] == [] and all((f["moved"] or 0.0) < 0.002 for f in found["frames"])
    walking = [_oval(base, cx, s) for cx, s in ((100, 4), (160, 5), (220, 6))]
    out, found = merge.merge_frames(_frames(walking), 3, "average", THR)
    assert found["flags"] == ["moved"]
    assert found["frames"][1]["moved"] is None and found["frames"][0]["moved"] > 0.03
    assert out.shape == (240, 320, 3)
    for mode, gains in (("average", (0.5, 1.0, 1.6)), ("brackets", (0.25, 1.0, 1.6))):
        _, found = merge.merge_frames(_frames([f * g for f, g in zip(still, gains)]), 3, mode, THR)
        assert found["flags"] == [], mode
    _, found = merge.merge_frames(_frames([f * g for f, g in zip(walking, (0.25, 1.0, 1.6))]), 3, "brackets", THR)
    assert found["flags"] == ["moved"]


def test_frames_of_another_size_and_bad_modes_are_refused():
    def load(k):
        return np.full((120, 160 + k, 3), 0.5, np.float32)
    assert "another size" in _error(lambda: merge.merge_frames(load, 2, "average", THR))
    assert "hdr" in _error(lambda: merge.merge_frames(load, 2, "hdr", THR))
    assert "at least two" in _error(lambda: merge.merge_frames(load, 1, "average", THR))


def test_config_is_layered_and_checked(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    pack = merge.load_config(str(empty))
    assert set(pack) == {"groups", "align", "moved", "brackets", "focus"}
    assert pack["groups"] == {"max_gap_s": 30, "min_similarity": 0.8, "bracket_min_ev": 0.5}
    assert pack["align"] == {"proxy_px": 512, "window_px": 1024, "max_shift": 0.01, "phase_wins": 0.8}
    assert pack["moved"] == {"blur": 1.0, "tolerance": 0.08, "max_share": 0.01}
    assert pack["brackets"]["high"] == [0.9, 0.98]
    conf, prof = tmp_path / "conf", tmp_path / "prof"
    conf.mkdir()
    prof.mkdir()
    (conf / "merge.toml").write_text("[align]\nmax_shift = 0.05\n")
    t = merge.load_config(str(conf))
    assert t["align"]["max_shift"] == 0.05 and t["align"]["window_px"] == 1024 and t["groups"] == pack["groups"]
    (prof / "merge.toml").write_text("[moved]\nmax_share = 0.05\n")
    t = merge.load_config(str(conf), str(prof))
    assert t["moved"]["max_share"] == 0.05 and t["align"]["max_shift"] == 0.05 and t["focus"] == pack["focus"]
    (prof / "merge.toml").write_text('[moved]\nmax_share = "a lot"\n')
    assert "[moved] max_share" in _error(lambda: merge.load_config(str(conf), str(prof)))


# --- T2: groups, files, the record, the command ----------------------------------------------

import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402

from PIL import Image as PILImage  # noqa: E402

from autoedit import batch, cli, consistency  # noqa: E402
from autoedit.contract import RunConfig  # noqa: E402
from autoedit.imageio import load_image  # noqa: E402


def _hashes(d):
    return {f: hashlib.sha256(open(os.path.join(d, f), "rb").read()).hexdigest() for f in sorted(os.listdir(d))}


def _save(path, lin, sec, seconds=0.01, fnum=8.0, iso=100):
    img = np.clip(linear_to_srgb(np.clip(lin, 0, 1)) * 255.0 + 0.5, 0, 255).astype(np.uint8)
    exif = PILImage.Exif()
    sub = exif.get_ifd(0x8769)
    sub[0x9003] = f"2026:05:01 10:{sec // 60:02d}:{sec % 60:02d}"
    sub[0x829A], sub[0x829D], sub[0x8827] = seconds, fnum, iso
    PILImage.fromarray(img).save(str(path), quality=95, subsampling=0, exif=exif.tobytes())
    return str(path)


def _shoot(d):
    d.mkdir(parents=True, exist_ok=True)
    for k, g in enumerate((0.25, 1.0, 4.0)):
        _save(d / f"a{k}.jpg", _sky() * g, k, seconds=0.01 * g)
    rng = np.random.default_rng(12)
    for k, sec in enumerate((100, 102)):
        _save(d / f"b{k}.jpg", _scene(seed=11) + rng.normal(0, 0.02, (120, 160, 3)).astype(np.float32), sec)
    (d / "b1_copy.jpg").write_bytes((d / "b1.jpg").read_bytes())
    _save(d / "lone.jpg", _scene(seed=13), 400)
    return d


def test_exposure_data_groups_and_modes(tmp_path):
    d = _shoot(tmp_path / "s")
    p = lambda n: str(d / n)  # noqa: E731
    st = [merge.exposure(p(f"a{k}.jpg")) for k in range(3)]
    assert [round(s[0] - st[0][0], 2) for s in st] == [0.0, 2.0, 4.0] and abs(st[2][1] - 0.04) < 1e-6
    assert merge.exposure(p("missing.jpg")) == (None, 0.0)
    gs = merge.find_groups(batch.list_images(str(d)), THR["groups"])
    assert [[os.path.basename(f) for f in g["frames"]] for g in gs] == [["a0.jpg", "a1.jpg", "a2.jpg"], ["b0.jpg", "b1.jpg"]]
    assert [os.path.basename(f) for f in gs[1]["copies"]] == ["b1_copy.jpg"]
    assert merge.pick_mode([st[0][0], st[1][0], st[2][0]], THR["groups"]) == "brackets"
    assert merge.pick_mode([1.0, 1.0], THR["groups"]) == "average"
    assert merge.pick_mode([None, 1.0], THR["groups"]) == "average"
    assert merge.pick_mode([0.0, 0.4], THR["groups"]) == "average"
    one = merge.find_groups(batch.list_images(str(d)), THR["groups"], everything=True)
    assert len(one) == 1 and len(one[0]["frames"]) == 6 and len(one[0]["copies"]) == 1
    e = tmp_path / "long"
    e.mkdir()
    for k in range(3):
        _save(e / f"n{k}.jpg", _scene(seed=21), 31 * k, seconds=30.0)
    assert len(merge.find_groups(batch.list_images(str(e)), THR["groups"])) == 1


def test_a_bracket_with_a_burnt_out_frame_stays_one_group(tmp_path):
    s = _scene(120, 160, 14)
    base = np.where(np.arange(120)[:, None, None] < 80, 0.25 + 1.2 * s, 0.04 + 0.05 * s).astype(np.float32)
    paths = [_save(tmp_path / f"f{k}.jpg", base * g, k, seconds=0.01 * g) for k, g in enumerate((1.0, 0.25, 4.0))]
    assert len(consistency.find_bursts(paths, 30.0, 0.8)) != 1 or len(consistency.find_bursts(paths, 30.0, 0.8)[0]) != 3
    gs = merge.find_groups(paths, THR["groups"])
    assert len(gs) == 1 and len(gs[0]["frames"]) == 3
    assert merge._alike(paths[1], paths[2]) > 0.9 and merge._alike(paths[2], paths[1]) > 0.9
    other = _save(tmp_path / "g.jpg", _sky(seed=40) * 4, 3, seconds=0.04)
    gs = merge.find_groups(paths[:2] + [other], THR["groups"])
    assert [len(g["frames"]) for g in gs] == [2]


def test_raw_files_and_the_rest_are_merged_apart(tmp_path):
    for k in range(3):
        j = _save(tmp_path / f"s{k}.jpg", _scene(seed=20) * 0.9, 10 * k)
        (tmp_path / f"s{k}.dng").write_bytes(open(j, "rb").read())
    imgs = batch.list_images(str(tmp_path))
    g = merge.find_groups(imgs, THR["groups"])
    assert len(g) == 1
    assert [os.path.basename(f) for f in g[0]["frames"]] == ["s0.dng", "s1.dng", "s2.dng"]
    assert sorted(os.path.basename(f) for f in g[0]["left_out"]) == ["s0.jpg", "s1.jpg", "s2.jpg"]
    os.remove(tmp_path / "s0.dng")
    os.remove(tmp_path / "s1.dng")
    g = merge.find_groups(batch.list_images(str(tmp_path)), THR["groups"])
    assert [os.path.basename(f) for f in g[0]["frames"]] == ["s0.jpg", "s1.jpg", "s2.jpg"]


def test_run_writes_a_tiff_and_a_record_and_leaves_the_sources(tmp_path):
    d = _shoot(tmp_path / "s")
    before = _hashes(d)
    out = tmp_path / "out"
    r = merge.run(str(d), str(out), say=lambda *a: None)
    assert _hashes(d) == before
    assert r["merged"] == ["a0_brackets.tif", "b0_average.tif"] and not r["failed"] and not r["flagged"]
    assert sorted(os.listdir(out)) == ["a0_brackets.merge.json", "a0_brackets.tif", "b0_average.merge.json", "b0_average.tif"]
    text = (out / "a0_brackets.merge.json").read_text()
    rec = json.loads(text)
    assert rec["mode"] == "brackets" and rec["output"] == "a0_brackets.tif" and rec["merge"] == 1
    assert [f["file"] for f in rec["frames"]] == ["a0.jpg", "a1.jpg", "a2.jpg"] and rec["reference"] == 1
    assert all({"file", "shift", "moved", "exposure_ev", "settings_ev"} <= set(f) for f in rec["frames"])
    assert [f["settings_ev"] for f in rec["frames"]] == [0.0, 2.0, 4.0]
    assert abs(rec["frames"][2]["exposure_ev"] - 4.0) < 0.3 and rec["frames"][0]["exposure_ev"] == 0.0
    assert os.sep not in text and str(tmp_path) not in text
    assert rec["bits"] == merge._bits()
    if rec["bits"] == 8:
        assert any("8-bit" in n for n in rec["notes"])
    img = load_image(str(out / "a0_brackets.tif"))
    assert img.rgb.shape[:2] == (120, 160) and img.source_bits == rec["bits"]
    assert json.loads((out / "b0_average.merge.json").read_text())["copies"] == ["b1_copy.jpg"]
    edited = tmp_path / "edited"
    res = batch.run(RunConfig(backend="stub"), str(out), str(edited))
    assert res["processed"] == 2 and res["failed"] == 0
    assert any(f.endswith(".autoedit.json") for f in os.listdir(edited))
    again = tmp_path / "again"
    merge.run(str(d), str(again), say=lambda *a: None)
    assert [(f, h) for f, h in _hashes(again).items() if f.endswith(".tif")] == \
           [(f, h) for f, h in _hashes(out).items() if f.endswith(".tif")]


def test_nothing_is_written_over_a_photo_or_a_file_merge_did_not_write(tmp_path):
    d = _shoot(tmp_path / "s")
    before = {f: h for f, h in _hashes(d).items()}
    r = merge.run(str(d), str(d), say=lambda *a: None)
    assert len(r["merged"]) == 2 and {f: h for f, h in _hashes(d).items() if f in before} == before
    r = merge.run(str(d), str(d), say=lambda *a: None)
    assert len(r["groups"]) == 2 and len(r["merged"]) == 2 and not r["failed"]
    d2 = _shoot(tmp_path / "t")
    out = tmp_path / "out"
    out.mkdir()
    (out / "a0_brackets.tif").write_bytes(b"not ours")
    r = merge.run(str(d2), str(out), say=lambda *a: None)
    assert r["merged"] == ["b0_average.tif"] and "not written by merge" in r["failed"][0]["error"]
    assert (out / "a0_brackets.tif").read_bytes() == b"not ours"
    e = tmp_path / "e"
    e.mkdir()
    _save(e / "b0.jpg", _scene(seed=11), 100)
    _save(e / "b1.jpg", _scene(seed=11), 102)
    _save(e / "b0_trails.tif", _scene(seed=12), 500)
    h = _hashes(e)
    r = merge.run(str(e), str(e), mode="trails", say=lambda *a: None)
    assert not r["merged"] and "overwrite a source" in r["failed"][0]["error"] and _hashes(e) == h


def _two_sizes(tmp_path):
    d = _shoot(tmp_path / "s")
    _save(d / "b0.jpg", np.kron(_scene(seed=11), np.ones((2, 2, 1), np.float32)), 100)
    return d


def test_one_bad_group_does_not_stop_the_run(tmp_path):
    d = _two_sizes(tmp_path)
    r = merge.run(str(d), str(tmp_path / "out"), say=lambda *a: None)
    assert r["merged"] == ["a0_brackets.tif"] and len(r["failed"]) == 1 and "another size" in r["failed"][0]["error"]


def test_cli(tmp_path):
    d = _shoot(tmp_path / "s")
    out = tmp_path / "out"
    assert cli.main(["merge", str(d), "-o", str(out), "--dry-run"]) == 0 and not out.exists()
    assert cli.main(["merge", str(d), "-o", str(out)]) == 0
    assert (out / "a0_brackets.tif").exists() and (out / "b0_average.tif").exists()
    o2 = tmp_path / "o2"
    assert cli.main(["merge", str(d), "-o", str(o2), "--all", "--mode", "trails"]) == 0
    assert (o2 / "a0_trails.tif").exists() and (o2 / "a0_trails.merge.json").exists()
    assert cli.main(["merge", str(tmp_path / "nope")]) == 2
    bad = _two_sizes(tmp_path / "x")
    assert cli.main(["merge", str(bad), "-o", str(tmp_path / "o3")]) == 1
    try:
        cli.main(["merge", str(d), "--mode", "hdr"])
    except SystemExit:
        pass
    else:
        raise AssertionError("--mode hdr was accepted")
