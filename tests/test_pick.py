"""F35: pick the keepers of a burst (T1: settings, shots, sharpness, exposure, ranking)."""

import numpy as np
import pytest
from PIL import Image as PILImage
from PIL import ImageFilter

from autoedit import pick
from autoedit.segment import stub
from autoedit.segment.base import RegionMask

WEIGHTS = {"exposure": 0.25, "scorer": 0.15}


def _texture(seed=0, h=160, w=240):
    rng = np.random.RandomState(seed)   # large shapes, patches of local contrast, fine detail; inside 30..225
    yy, xx = np.mgrid[0:h, 0:w]
    a = np.zeros((h, w, 3), np.float32)
    a[: h // 3] = [120, 150, 200]
    a[h // 3:] = [70, 110, 70]
    a[(((xx - 0.3 * w) / (0.15 * w)) ** 2 + ((yy - 0.6 * h) / (0.25 * h)) ** 2) <= 1.0] = [190, 150, 120]
    coarse = PILImage.fromarray(rng.uniform(-30, 30, (h // 8, w // 8)).astype(np.float32), "F").resize((w, h), PILImage.BICUBIC)
    return np.clip(a + np.asarray(coarse)[..., None] + rng.uniform(-12, 12, (h, w, 1)), 30, 225)


def _blur(x, radius):
    """Pillow's GaussianBlur on a float picture of 0..1 or 0..255 (kept in 8 bits)."""
    scale = 255.0 if x.max() <= 1.0 else 1.0
    img = PILImage.fromarray(np.clip(x * scale, 0, 255).round().astype(np.uint8))
    return np.asarray(img.filter(ImageFilter.GaussianBlur(radius)), np.float32) / scale


def _full(x):
    return np.ones(x.shape[:2], bool)


def _sharp(x, mask=None, **kw):
    return pick.sharpness(x, _full(x) if mask is None else mask, **kw)


def test_settings_file():
    st = pick.load_settings()
    assert st["measure"] == {"long_edge": 2048, "local_radius": 16, "min_contrast": 0.02, "concepts": ["subject"],
                             "min_score": 0.4, "min_coverage": 0.002}
    assert st["exposure"] == {"clip_full": 0.1, "dark_p50": 0.25}
    assert st["weights"] == {"exposure": 0.3, "scorer": 0.15}


def test_profile_file_is_merged(tmp_path):
    (tmp_path / "pick.toml").write_text("[weights]\nexposure = 0.0\n")
    st = pick.load_settings(profile=str(tmp_path))
    assert st["weights"]["exposure"] == 0.0
    assert st["weights"]["scorer"] == 0.15
    assert st["measure"]["long_edge"] == 2048


def test_sharpness_falls_with_blur_and_ignores_exposure():
    x = _texture() / 255
    s0 = _sharp(x)
    s = [_sharp(_blur(x, r)) for r in (0.5, 1.0, 2.0)]
    assert s0 > s[0] > s[1] > s[2]
    assert s[0] < 0.8 * s0
    for gain, lift in ((0.4, 0), (0.7, 0), (0.8, 0.1), (0.5, 0.4)):
        assert abs(_sharp(x * gain + lift) - s0) < 1e-3
    assert _sharp(x * 0.1) < 0.5 * s0
    assert _sharp(np.zeros_like(x)) == 0.0
    assert _sharp(np.ones_like(x)) == 0.0
    assert _sharp(x, np.zeros(x.shape[:2], bool)) == 0.0


def test_noise_on_a_flat_area_is_not_sharpness():
    rng = np.random.RandomState(1)
    tex = _sharp(_texture() / 255)
    for level in (0.5, 0.01):
        flat = np.full((160, 240, 3), level, np.float32) + rng.normal(0, 0.002, (160, 240, 3)).astype(np.float32)
        assert _sharp(flat) < 0.5 * tex
        assert _sharp(flat, min_contrast=0.0) > tex


def test_sharpness_is_measured_inside_the_mask():
    sharp = _texture() / 255
    soft = _blur(sharp, 2.0)
    oval = stub._subject(sharp)
    a = np.where(oval[..., None], sharp, soft)
    b = np.where(oval[..., None], soft, sharp)
    assert _sharp(a, oval) > 2 * _sharp(b, oval)
    assert _sharp(b) > _sharp(a)


def test_exposure_penalty():
    def pen(x, mask=None):
        x = np.asarray(x, np.float32)
        return pick.exposure_penalty(x, _full(x) if mask is None else mask, 0.05, 0.25)

    def flat(v):
        return np.full((40, 40, 3), v, np.float32)

    assert pen(flat(0.5))[0] == 0 and pen(flat(0.25))[0] == 0
    assert pen(flat(0.99))[0] == 1 and pen(flat(0.0))[0] == 1
    p, nums = pen(flat(0.1))
    assert abs(p - 0.6) < 1e-6 and nums["blown"] == 0
    row = flat(0.5)
    row[0] = 1.0
    assert abs(pen(row)[0] - 0.5) < 1e-6
    chan = flat(0.5)
    chan[0, :, 0] = 1.0
    assert abs(pen(chan)[0] - 0.5) < 1e-6
    out = _full(row)
    out[0] = False
    assert pen(row, out)[0] == 0
    quarter = flat(0.5)
    quarter[:10] = 0.0
    assert pen(quarter)[0] == 0


def _shot(name, sharp, penalty=0.0, scorer=None):
    return {"files": [name], "sharpness": sharp, "exposure_penalty": penalty, "scorer": scorer}


def test_rank_burst_scores_and_ties():
    r = pick.rank_burst([_shot("c", 1.0), _shot("a", 1.0), _shot("b", 1.0)], WEIGHTS)
    assert [j["files"][0] for j in r] == ["a", "b", "c"]
    assert [j["rank"] for j in r] == [1, 2, 3] and [j["pick"] for j in r] == [True, False, False]
    assert abs(pick.rank_burst([_shot("a", 2.0), _shot("b", 1.0)], WEIGHTS)[1]["score"] - 0.5) < 1e-9
    r = pick.rank_burst([_shot("a", 1.0, penalty=1.0), _shot("b", 0.9), _shot("c", 0.7)], WEIGHTS)
    assert [j["files"][0] for j in r] == ["b", "a", "c"]
    r = pick.rank_burst([_shot("a", 1.0, scorer=0.2), _shot("b", 0.95, scorer=0.9)], WEIGHTS)
    assert r[0]["files"][0] == "b"
    assert abs(r[1]["score"] - (1.0 - 0.105)) < 1e-9
    r = pick.rank_burst([_shot("a", 0.0), _shot("b", 0.0)], WEIGHTS)
    assert [j["score"] for j in r] == [0.0, 0.0] and r[0]["files"][0] == "a"


def test_shots_pair_a_raw_file_with_its_jpeg():
    got = pick.shots_of(["b.jpg", "a.JPG", "a.cr2", "c.jpg", "c.png", "d.dng"])
    assert got == [["a.JPG", "a.cr2"], ["b.jpg"], ["c.jpg"], ["c.png"], ["d.dng"]]


def test_the_mask_of_the_segmenter_is_what_is_judged():
    tex = _texture() / 255
    quarter = np.zeros(tex.shape[:2], bool)
    quarter[:80, :120] = True
    asked = []

    class Fake:
        def __init__(self, masks):
            self.masks = masks

        def segment(self, img, concepts):
            asked.append(list(concepts))
            return self.masks

    st = pick.load_settings()
    real = pick.look_picture
    pick.look_picture = lambda path, long_edge, decode=None: tex
    try:
        j, _, area = pick.judge("x.jpg", Fake([RegionMask("bird", quarter, 0.9)]), st, ["bird"])
        assert asked == [["bird"]] and j["judged_on"] == "mask" and abs(j["coverage"] - 0.25) < 1e-9
        assert abs(j["sharpness"] - pick.sharpness(tex, quarter, 16, 0.02)) < 1e-9 and area.sum() == quarter.sum()
        for masks in ([RegionMask("bird", quarter, 0.2)], []):
            j, _, _ = pick.judge("x.jpg", Fake(masks), st, ["bird"])
            assert j["judged_on"] == "frame" and j["coverage"] == 1.0
    finally:
        pick.look_picture = real


# ---- T2: the run, what it writes, the command, the owner's check -------------------------------

import csv  # noqa: E402
import hashlib  # noqa: E402
import importlib.util  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402

from autoedit import cli  # noqa: E402
from autoedit.contract import RunConfig  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _save(path, pixels, sec=None):
    """Pixels are floats of 0..255. JPEG at quality 95 (PNG when the file name says so)."""
    img = PILImage.fromarray(np.clip(np.asarray(pixels), 0, 255).round().astype(np.uint8))
    kw = {}
    if sec is not None:
        exif = PILImage.Exif()
        exif.get_ifd(0x8769)[0x9003] = f"2026:05:01 10:{sec // 60:02d}:{sec % 60:02d}"
        kw["exif"] = exif.tobytes()
    if str(path).lower().endswith(".png"):
        img.save(path, **kw)
    else:
        img.save(path, quality=95, **kw)


def _burst(folder, frames, start=0, prefix="f", ext="jpg"):
    os.makedirs(folder, exist_ok=True)
    for i, px in enumerate(frames):
        _save(os.path.join(folder, f"{prefix}{i}.{ext}"), px, sec=start + 2 * i)


def _run(folder, out, **kw):
    return pick.run(str(folder), str(out), RunConfig(backend="stub"), **kw)


def _hashes(folder):
    out = {}
    for root, _, files in os.walk(folder):
        for f in files:
            p = os.path.join(root, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, folder)] = (hashlib.sha256(fh.read()).hexdigest(), os.stat(p).st_mtime_ns)
    return out


def _profile(tmp_path, text, name="prof"):
    d = tmp_path / name
    d.mkdir()
    (d / "pick.toml").write_text(text)
    return str(d)


def _picked_files(out):
    return sorted(os.listdir(os.path.join(out, "picked")))


def _shift_mean(x, n=4):
    return sum(np.roll(x, k, axis=1) for k in range(n)) / n


def test_the_sharp_frame_is_always_picked(tmp_path):
    for seed in range(3):
        base = _texture(seed)
        for kind, bad in (("blur05", lambda x: _blur(x, 0.5)), ("blur2", lambda x: _blur(x, 2.0)), ("shift", _shift_mean)):
            for at in range(4):
                frames = [base if i == at else bad(base) for i in range(4)]
                folder = tmp_path / f"{seed}_{kind}_{at}"
                _burst(folder, frames)
                r = _run(folder, tmp_path / f"o{seed}_{kind}_{at}")
                assert len(r["bursts"]) == 1 and r["singles"] == [], (seed, kind, at)
                shots = r["bursts"][0]["shots"]
                assert shots[0]["files"] == [f"f{at}.jpg"] and shots[0]["score"] == 1.0, (seed, kind, at)
                assert shots[1]["sharp_rel"] < 0.9, (seed, kind, at, shots[1]["sharp_rel"])
                assert r["picks"] == [f"f{at}.jpg"]


def test_exposure_decides_between_equally_sharp_frames(tmp_path):
    x = _texture()
    _burst(tmp_path / "in", [x * 0.4, x, np.clip(x * 2.4, 0, 255)], ext="png")
    r = _run(tmp_path / "in", tmp_path / "out")
    by = {j["files"][0]: j for j in r["bursts"][0]["shots"]}
    assert by["f1.png"]["pick"] and by["f1.png"]["exposure_penalty"] == 0
    dark, blown = by["f0.png"], by["f2.png"]
    assert abs(dark["sharpness"] / by["f1.png"]["sharpness"] - 1.0) < 0.01
    assert 0.15 < dark["exposure_penalty"] < 0.35
    assert abs(dark["score"] - (dark["sharp_rel"] - 0.3 * dark["exposure_penalty"])) < 1e-3
    assert blown["exposure_penalty"] == 1.0
    assert any("exposure penalty" in w for w in dark["why"])
    assert not any("exposure penalty" in w for w in by["f1.png"]["why"])
    prof = _profile(tmp_path, "[weights]\nexposure = 0.0\n")
    r = _run(tmp_path / "in", tmp_path / "out2", profile=prof)
    assert all(j["score"] == j["sharp_rel"] for j in r["bursts"][0]["shots"])


def test_frame_size_does_not_decide(tmp_path):
    big = _blur(_texture(0, 320, 480), 1.2)
    small = np.asarray(PILImage.fromarray(big.round().astype(np.uint8)).resize((240, 160), PILImage.LANCZOS), np.float32)
    _burst(tmp_path / "in", [big, small], ext="png")
    prof = _profile(tmp_path, "[measure]\nlong_edge = 240\n")
    r = _run(tmp_path / "in", tmp_path / "out", profile=prof)
    assert len(r["bursts"]) == 1
    assert all(j["sharp_rel"] > 0.9 for j in r["bursts"][0]["shots"])
    s_big, s_small = _sharp(big / 255), _sharp(small / 255)
    assert abs(s_big - s_small) > 0.25 * max(s_big, s_small)


def _mixed_folder(folder):
    _burst(folder, [_blur(_texture(), 1.0), _texture()])
    _save(os.path.join(folder, "lone.jpg"), _texture(1), sec=900)
    with open(os.path.join(folder, "notes.txt"), "w") as fh:
        fh.write("not a photo\n")
    shutil.copy2(os.path.join(folder, "f1.jpg"), os.path.join(folder, "f1.dng"))


def test_no_source_file_changes(tmp_path):
    folder = tmp_path / "in"
    _mixed_folder(folder)
    before, listing = _hashes(folder), sorted(os.listdir(folder))
    r = _run(folder, tmp_path / "out", copy=True)
    assert _hashes(folder) == before and sorted(os.listdir(folder)) == listing
    assert _picked_files(str(tmp_path / "out")) == ["f1.dng", "f1.jpg", "lone.jpg"]
    assert r["picks"] == ["f1.dng", "f1.jpg", "lone.jpg"]
    for f in ("f1.dng", "f1.jpg", "lone.jpg"):
        assert (tmp_path / "out" / "picked" / f).read_bytes() == (folder / f).read_bytes()
    _run(folder, folder / "o2", copy=True)
    after = _hashes(folder)
    assert {k: after[k] for k in before} == before


def test_nothing_is_written_over_a_photo(tmp_path):
    held = tmp_path / "out" / "picked"
    _burst(held, [_texture(), _blur(_texture(), 1.0)])
    before = _hashes(held)
    with pytest.raises(ValueError):
        _run(held, tmp_path / "out", copy=True)
    assert _hashes(held) == before
    other = tmp_path / "other" / "thumbs"
    _burst(other, [_texture(), _blur(_texture(), 1.0)])
    _save(str(other / "f0.jpg.jpg"), _texture(1), sec=900)
    before = _hashes(other)
    with pytest.raises(pick.OnAPhoto):
        _run(other, tmp_path / "other")
    assert _hashes(other) == before


def test_the_same_folder_gives_the_same_bytes(tmp_path):
    _burst(tmp_path / "in", [_texture(), _blur(_texture(), 1.0), _blur(_texture(), 2.0)])
    _run(tmp_path / "in", tmp_path / "a")
    _run(tmp_path / "in", tmp_path / "b")
    for f in pick.FILES:
        assert (tmp_path / "a" / f).read_bytes() == (tmp_path / "b" / f).read_bytes(), f
    assert sorted(os.listdir(tmp_path / "a" / "thumbs")) == ["f0.jpg.jpg", "f1.jpg.jpg", "f2.jpg.jpg"]
    assert not (tmp_path / "a" / "picked").exists()


def _spy_look_picture(fail=None):
    real, seen = pick.look_picture, []

    def spy(path, long_edge, decode=None):
        seen.append(os.path.basename(path))
        if fail and os.path.basename(path) == fail:
            raise ValueError("cannot read")
        return real(path, long_edge, decode)
    return real, seen, spy


def test_singles_are_picks_and_are_not_opened(tmp_path):
    folder = tmp_path / "in"
    _burst(folder, [_texture(), _blur(_texture(), 1.0)])
    _save(str(folder / "late.jpg"), _texture(1), sec=900)
    _save(str(folder / "nodate.jpg"), _texture(2))
    real, seen, spy = _spy_look_picture()
    pick.look_picture = spy
    try:
        r = _run(folder, tmp_path / "out")
    finally:
        pick.look_picture = real
    assert sorted(seen) == ["f0.jpg", "f1.jpg"]
    assert r["singles"] == [["late.jpg"], ["nodate.jpg"]]
    assert r["picks"] == ["f0.jpg", "late.jpg", "nodate.jpg"]
    rows = (tmp_path / "out" / "ranking.csv").read_text().splitlines()
    assert len(rows) == 5 and rows[-1].endswith("single frame")
    lines = (tmp_path / "out" / "picks.txt").read_text().splitlines()
    assert lines == [os.path.join(os.path.abspath(folder), n) for n in r["picks"]]


def test_one_bad_frame_does_not_stop_the_run(tmp_path):
    folder = tmp_path / "in"
    _burst(folder, [_texture(), _blur(_texture(), 1.0), _texture()])
    real, _, spy = _spy_look_picture(fail="f0.jpg")
    pick.look_picture = spy
    try:
        r = _run(folder, tmp_path / "out", copy=True)
    finally:
        pick.look_picture = real
    assert r["failed"] == [{"files": ["f0.jpg"], "burst": "b1", "error": "cannot read"}]
    assert [j["files"][0] for j in r["bursts"][0]["shots"]] == ["f2.jpg", "f1.jpg"]
    assert r["picks"] == ["f0.jpg", "f2.jpg"]
    assert _picked_files(str(tmp_path / "out")) == ["f0.jpg", "f2.jpg"]
    assert "cannot read" in (tmp_path / "out" / "contact-sheet.html").read_text()
    rows = list(csv.reader(io.StringIO((tmp_path / "out" / "ranking.csv").read_text())))
    bad = [x for x in rows if x[3] == "f0.jpg"][0]
    assert bad[1] == "" and bad[2] == "yes" and bad[-1] == "could not be judged and is kept: cannot read"


def test_the_scorer_joins_only_when_a_model_is_given(tmp_path):
    x = _texture()
    _burst(tmp_path / "in", [x * 0.75, x], ext="png")
    r = _run(tmp_path / "in", tmp_path / "o1")
    shots = r["bursts"][0]["shots"]
    assert all(j["scorer"] is None for j in shots)
    assert abs(shots[0]["score"] - shots[1]["score"]) < 0.005
    model = tmp_path / "model.json"
    model.write_text(json.dumps({"feature_names": ["lum_mean"], "mean": [0.0], "scale": [1.0],
                                 "weights": [-2.0], "bias": 1.3}))
    r = _run(tmp_path / "in", tmp_path / "o2", model_path=str(model))
    first, second = r["bursts"][0]["shots"]
    assert first["files"] == ["f0.png"] and first["pick"]
    assert abs(first["scorer"] - 0.60) < 0.1 and abs(second["scorer"] - 0.36) < 0.1
    assert abs(second["score"] - (second["sharp_rel"] - 0.15 * (first["scorer"] - second["scorer"]))) < 1e-3
    assert any(w.startswith("scorer ") for w in first["why"])
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    with pytest.raises(ValueError):
        _run(tmp_path / "in", tmp_path / "o3", model_path=str(bad))


def test_contact_sheet_marks_the_pick(tmp_path):
    folder = tmp_path / "in"
    _burst(folder, [_blur(_texture(), 1.0), _texture()], prefix="a #1 f")
    _burst(folder, [_texture(1), _blur(_texture(1), 1.0)], start=900)
    r = _run(folder, tmp_path / "out")
    sheet = (tmp_path / "out" / "contact-sheet.html").read_text()
    assert sheet.count('class="pick"') == 2
    assert "oval" in sheet and 'src="thumbs/a%20%231%20f1.jpg.jpg"' in sheet
    for g in r["bursts"]:
        for j in g["shots"]:
            for d in ("thumbs", "crops"):
                assert (tmp_path / "out" / d / (j["files"][0] + ".jpg")).exists()
    b1 = r["bursts"][0]
    assert b1["shots"][0]["files"] == ["a #1 f1.jpg"] and "a #1 f1.jpg" in sheet
    assert abs(b1["margin"] - (b1["shots"][0]["score"] - b1["shots"][1]["score"])) < 2e-4
    assert json.loads((tmp_path / "out" / "ranking.json").read_text()) == r
    assert r["burst_finder"] == {"max_gap_s": 30, "max_span_s": 120, "min_similarity": 0.8}


def test_pick_can_have_its_own_idea_of_a_burst(tmp_path):
    _burst(tmp_path / "in", [_texture(), _blur(_texture(), 1.0)])
    prof = _profile(tmp_path, "[burst]\nmax_gap_s = 1\n")
    r = _run(tmp_path / "in", tmp_path / "o1", profile=prof)
    assert r["bursts"] == [] and r["singles"] == [["f0.jpg"], ["f1.jpg"]]
    assert len(_run(tmp_path / "in", tmp_path / "o2")["bursts"]) == 1


def test_cli_pick(tmp_path):
    _burst(tmp_path / "in", [_texture(), _blur(_texture(), 1.0)])
    out = str(tmp_path / "out")
    args = ["pick", str(tmp_path / "in"), "-o", out, "--copy", "--backend", "stub"]
    assert cli.main(args) == 0
    assert all(os.path.exists(os.path.join(out, f)) for f in pick.FILES)
    assert _picked_files(out) == ["f0.jpg"]
    assert cli.main(args) == 2
    assert cli.main(["pick", str(tmp_path / "nope"), "-o", out]) == 2
    assert cli.main(["pick", str(tmp_path / "in"), "-o", str(tmp_path / "o2"), "--backend", "stub",
                     "--concepts", "person"]) == 0
    assert json.loads((tmp_path / "o2" / "ranking.json").read_text())["concepts"] == ["person"]


def test_your_keepers_against_the_ranking():
    spec = importlib.util.spec_from_file_location("eval_picks", os.path.join(_ROOT, "scripts", "eval_picks.py"))
    ev = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ev)

    def shot(*files, rank):
        return {"files": list(files), "rank": rank, "pick": rank == 1}
    ranking = {"bursts": [
        {"id": "b1", "shots": [shot("A1.JPG", "a1.cr2", rank=1), shot("A2.JPG", rank=2)]},
        {"id": "b2", "shots": [shot("B1.JPG", rank=1), shot("B2.JPG", rank=2), shot("B3.JPG", rank=3)]},
        {"id": "b3", "shots": [shot("C1.JPG", rank=1), shot("C2.JPG", rank=2)]},
        {"id": "b4", "shots": [shot("D1.JPG", rank=1), shot("D2.JPG", rank=2)]},
    ]}
    names = ["a1.cr2", "# a comment", "", "B3", "c2.jpg", "lone.jpg"]
    r = ev.agreement(ranking, names)
    assert (r["bursts"], r["agree"], r["first"]) == (3, 2, 1)
    assert r["misses"] == [("b2", "B3.JPG", 3, "B1.JPG")]
    assert r["skipped"] == ["b4"] and r["unknown"] == ["lone"]
    assert ev.agreement(ranking, names, top=3)["misses"] == []
    assert ev.agreement(ranking, ["/some/folder/" + n for n in names])["agree"] == 2   # lines of picks.txt


def test_a_bad_pick_file_is_refused(tmp_path):
    _burst(tmp_path / "in", [_texture(), _blur(_texture(), 1.0)])
    for bad in ('[weights]\nexposure = "0.3"\n', "[measure]\nmin_coverage = 0.0\n", "[exposure]\ndark_p50 = 0\n",
                "[measure]\nmin_score = nan\n", "[sheet]\ncrop = [360]\n", "[measure]\nconcepts = []\n",
                "[measure]\nlong_edge = true\n"):
        with pytest.raises(ValueError) as e:
            _run(tmp_path / "in", tmp_path / "o", profile=_profile(tmp_path, bad, name=f"p{len(os.listdir(tmp_path))}"))
        assert "pick.toml" in str(e.value)
    assert not (tmp_path / "o").exists() or not os.listdir(tmp_path / "o")


def test_files_pick_did_not_write_are_kept(tmp_path):
    _burst(tmp_path / "in", [_texture(), _blur(_texture(), 1.0)])
    out = tmp_path / "out"
    out.mkdir()
    (out / "contact-sheet.html").write_text("the owner's own sheet")
    with pytest.raises(ValueError) as e:
        _run(tmp_path / "in", out)
    assert "contact-sheet.html" in str(e.value)
    assert (out / "contact-sheet.html").read_text() == "the owner's own sheet"
    (out / "contact-sheet.html").unlink()
    _run(tmp_path / "in", out)
    _run(tmp_path / "in", out)          # a second run over its own files is fine
