"""Shoot-level consistency (Codex review F5): brightness clustering + bounded exposure anchor."""

import numpy as np
from PIL import Image as PILImage

from autoedit import batch
from autoedit.consistency import exposure_bias, plan_groups, quick_lum
from autoedit.contract import RunConfig


def test_plan_groups_splits_on_lighting_gap():
    lums = [("a", 0.20), ("b", 0.22), ("c", 0.70), ("d", 0.72)]  # two clusters
    g = plan_groups(lums, gap=0.08)
    assert g["a"][0] == g["b"][0]            # dark pair share a group
    assert g["c"][0] == g["d"][0]            # bright pair share a group
    assert g["a"][0] != g["c"][0]            # different groups
    assert abs(g["a"][1] - 0.21) < 0.02      # group target ~ median brightness


def test_exposure_bias_is_bounded_and_directional():
    assert exposure_bias(0.30, 0.50) > 0     # darker-than-target → brighten
    assert exposure_bias(0.80, 0.50) < 0     # brighter-than-target → darken
    assert abs(exposure_bias(0.0, 1.0)) <= 0.15  # bounded


def test_batch_emits_groups_and_normalizes(tmp_path):
    # three dark + one bright frame → at least two groups; sidecars carry batch_group
    corpus = tmp_path / "shoot"
    corpus.mkdir()
    for i, lvl in enumerate([40, 45, 50, 210]):
        a = np.full((48, 48, 3), lvl, np.uint8)
        PILImage.fromarray(a, "RGB").save(str(corpus / f"img{i}.jpg"))
    out = tmp_path / "out"
    manifest = batch.run(RunConfig(backend="stub"), str(corpus), str(out), do_apply=True)
    assert manifest["batch_groups"]                      # groups recorded
    assert len(manifest["batch_groups"]) >= 2            # dark cluster vs the bright outlier

    import json
    sidecars = list(out.glob("*.autoedit.json"))
    groups = {json.load(open(s))["batch_group"] for s in sidecars}
    assert "" not in groups and len(groups) >= 2         # every image got a real group id


# ── F9: shoot consistency v2 (bursts) ────────────────────────────────────────

import json
import os
from types import SimpleNamespace

import pytest

from autoedit import adjust, consistency
from autoedit.cli import load_run_config
from autoedit.consistency import Bias, burst_biases, find_bursts, plan
from autoedit.contract import Result
from autoedit.pipeline import Pipeline


def _picture(kind=0):
    """A picture with structure (so thumbnails correlate); `kind` picks one of two framings."""
    a = np.zeros((96, 128, 3), np.float32)
    a[:40] = [100, 140, 200]
    a[40:] = [60, 110, 70]
    yy, xx = np.mgrid[0:96, 0:128]
    if kind == 0:
        a[(((xx - 40) / 20) ** 2 + ((yy - 60) / 26) ** 2) <= 1.0] = [200, 150, 120]
    else:
        a[(((xx - 95) / 14) ** 2 + ((yy - 25) / 12) ** 2) <= 1.0] = [240, 230, 120]
        a[70:, :60] = [30, 30, 40]
    return a


def _save(path, kind=0, gain=1.0, when=None, cast=(1.0, 1.0, 1.0)):
    exif = PILImage.Exif()
    if when is not None:
        exif.get_ifd(0x8769)[0x9003] = when
    PILImage.fromarray(np.clip(_picture(kind) * gain * np.array(cast, np.float32), 0, 255).astype(np.uint8), "RGB").save(
        str(path), quality=95, exif=exif)


def _stamp(sec):
    return f"2026:05:01 10:{sec // 60:02d}:{sec % 60:02d}"


def _burst_folder(d, gains=(0.6, 1.0, 1.5), casts=None):
    d.mkdir()
    for i, g in enumerate(gains):
        _save(d / f"f{i}.jpg", 0, g, _stamp(3 * i), casts[i] if casts else (1.0, 1.0, 1.0))
    return str(d)


_CASTS = ((1.0, 1.0, 1.0), (1.25, 1.0, 0.8), (0.8, 1.0, 1.25))   # neutral, warm, cool


def _mean_luma(path):
    return float(np.asarray(PILImage.open(path).convert("L"), np.float32).mean())


def test_brightness_mode_is_unchanged(tmp_path):
    folder = tmp_path / "shoot"
    folder.mkdir()
    paths = []
    for i, lvl in enumerate([40, 45, 120, 125, 210]):
        p = folder / f"img{i}.jpg"
        PILImage.fromarray(np.full((48, 48, 3), lvl, np.uint8), "RGB").save(str(p))
        paths.append(str(p))
    lums = {p: quick_lum(p) for p in paths}
    groups = plan_groups(list(lums.items()))
    expected = {p: (g, exposure_bias(lums[p], t)) for p, (g, t) in groups.items()}
    got = plan(paths, RunConfig(backend="stub"), None)
    assert {p: (b.group, b.exposure) for p, b in got.items()} == expected
    assert all(b.temp == 0.0 and b.tint == 0.0 for b in got.values())
    # and the batch writes those biases into the sidecars
    out = tmp_path / "out"
    batch.run(RunConfig(backend="stub"), str(folder), str(out))
    for p in paths:
        d = json.load(open(out / (os.path.splitext(os.path.basename(p))[0] + ".autoedit.json")))
        assert (d["batch_group"], d["exposure_bias"]) == expected[p]
        assert d["wb_bias"] == [0.0, 0.0]


def test_find_bursts(tmp_path):
    _save(tmp_path / "a1.jpg", 0, 0.6, _stamp(0))
    _save(tmp_path / "a2.jpg", 0, 1.0, _stamp(5))
    _save(tmp_path / "a3.jpg", 0, 1.4, _stamp(10))
    _save(tmp_path / "b1.jpg", 1, 1.0, _stamp(15))
    _save(tmp_path / "c1.jpg", 1, 1.0, _stamp(115))
    _save(tmp_path / "c2.jpg", 1, 0.8, _stamp(117))
    _save(tmp_path / "notime.jpg", 0, 1.0, None)
    paths = sorted(str(p) for p in tmp_path.glob("*.jpg"))
    bursts = find_bursts(paths, 30.0, 0.9)
    names = [[os.path.basename(p) for p in b] for b in bursts]
    assert names == [["a1.jpg", "a2.jpg", "a3.jpg"], ["c1.jpg", "c2.jpg"]]


def test_only_frames_with_a_near_neighbour_get_a_thumbnail(tmp_path):
    # A thumbnail costs a decode. Frames that are far from every other frame in time cannot be in
    # a burst and must not be decoded for one.
    from autoedit import consistency

    _save(tmp_path / "a1.jpg", 0, 0.6, _stamp(0))
    _save(tmp_path / "a2.jpg", 0, 1.0, _stamp(5))
    for i in range(6):                                          # six lone frames, minutes apart
        _save(tmp_path / f"lone{i}.jpg", 1, 1.0, _stamp(300 + 200 * i))
    paths = sorted(str(p) for p in tmp_path.glob("*.jpg"))
    seen, real = [], consistency.thumb_signature
    consistency.thumb_signature = lambda p: (seen.append(os.path.basename(p)), real(p))[1]
    try:
        bursts = find_bursts(paths, 30.0, 0.9, 120.0)
    finally:
        consistency.thumb_signature = real
    assert [[os.path.basename(p) for p in b] for b in bursts] == [["a1.jpg", "a2.jpg"]]
    assert sorted(seen) == ["a1.jpg", "a2.jpg"]


def test_two_pictures_with_the_same_sky_over_ground_are_not_a_burst(tmp_path):
    # Found on a real shoot: a hut, then a meadow, six seconds apart. Both are bright sky over
    # dark ground, and a plain thumbnail correlation took them for one burst (0.93). So did an
    # upright and a level frame of one scene.
    def photo(name, sec, cx, upright=False):
        h, w = (128, 96) if upright else (96, 128)
        a = np.zeros((h, w, 3), np.float32)
        a[: h // 2] = [200, 210, 230]                         # sky
        a[h // 2:] = [50, 80, 45]                             # ground
        yy, xx = np.mgrid[0:h, 0:w]
        a[(((xx - cx * w) / (0.10 * w)) ** 2 + ((yy - 0.55 * h) / (0.22 * h)) ** 2) <= 1.0] = [120, 70, 50]
        exif = PILImage.Exif()
        exif.get_ifd(0x8769)[0x9003] = _stamp(sec)
        PILImage.fromarray(a.astype(np.uint8), "RGB").save(str(tmp_path / name), quality=95, exif=exif)

    photo("hut_left.jpg", 0, 0.25)
    photo("hut_right.jpg", 6, 0.75)                           # another picture, same layout
    photo("upright.jpg", 12, 0.75, upright=True)
    paths = sorted(str(p) for p in tmp_path.glob("*.jpg"))
    assert find_bursts(paths, 30.0, 0.8, 120.0) == []
    photo("hut_right_again.jpg", 9, 0.76)                     # the same framing, a hair to the side
    paths = sorted(str(p) for p in tmp_path.glob("*.jpg"))
    names = [[os.path.basename(p) for p in b] for b in find_bursts(paths, 30.0, 0.8, 120.0)]
    assert names == [["hut_right.jpg", "hut_right_again.jpg"]]


def test_raw_files_and_jpegs_of_a_burst_are_evened_out_separately():
    # Two shots, each as RAW and JPEG. The RAW decodes are far darker than the JPEGs. One median
    # for all four would push both JPEGs down and both RAW files up by the full bound; each kind
    # among its own only closes the small drift between the two shots.
    from autoedit.consistency import burst_biases, kind_biases

    d = lambda fg, ev: {"fg_p50": fg, "exposure": ev, "shadows_ev": 0.0, "temp": 0.0, "tint": 0.0, "headroom_ev": 3.0}
    paths = ["a.dng", "a.jpg", "b.dng", "b.jpg"]
    decided = [d(0.12, 0.8), d(0.40, 0.2), d(0.13, 0.8), d(0.44, 0.2)]
    mixed = burst_biases(decided, 0.5, 4.0)
    assert max(abs(b[0]) for b in mixed) > 0.19          # one median: it fights the RAW/JPEG gap
    split = kind_biases(paths, decided, 0.5, 4.0)
    assert max(abs(b[0]) for b in split) < 0.16          # each kind alone: only the burst's own drift
    assert split[0][0] > 0 > split[2][0] and split[1][0] > 0 > split[3][0]
    # a kind with one file in the burst is left alone
    lone = kind_biases(["a.dng", "a.jpg", "b.jpg"], decided[:2] + decided[3:], 0.5, 4.0)
    assert lone[0] == (0.0, 0.0, 0.0) and lone[1][0] > 0 > lone[2][0]


def test_a_raw_file_and_its_jpeg_are_one_shot(tmp_path):
    # The same second, one RAW and one JPEG: one shot. Alone they are no burst; inside a burst
    # both files belong to it. (The ".dng" files here are JPEGs by content: only their names and
    # capture times matter, the thumbnail of a shot comes from its JPEG.)
    def twin(name, kind, gain, sec):
        _save(tmp_path / f"{name}.jpg", kind, gain, _stamp(sec))
        _save(tmp_path / f"{name}_raw.jpg", kind, gain, _stamp(sec))
        os.replace(tmp_path / f"{name}_raw.jpg", tmp_path / f"{name}.dng")

    twin("a", 0, 1.0, 0)
    twin("far", 1, 1.0, 600)
    paths = sorted(str(p) for p in tmp_path.glob("*.*"))
    assert find_bursts(paths, 30.0, 0.9, 120.0) == []
    twin("b", 0, 1.3, 4)
    paths = sorted(str(p) for p in tmp_path.glob("*.*"))
    bursts = find_bursts(paths, 30.0, 0.9, 120.0)
    assert [sorted(os.path.basename(p) for p in b) for b in bursts] == [["a.dng", "a.jpg", "b.dng", "b.jpg"]]


def test_a_burst_is_cut_after_max_span(tmp_path):
    paths = []
    for i in range(12):                    # twelve identical frames, 20 s apart
        p = tmp_path / f"f{i:02d}.jpg"
        _save(p, 0, 1.0, _stamp(20 * i))
        paths.append(str(p))
    bursts = find_bursts(paths, 30.0, 0.9, 120.0)
    assert [len(b) for b in bursts] == [7, 5]
    assert len(find_bursts(paths, 30.0, 0.9)) == 1          # no limit given: the old chain
    thr = consistency.BURST_DEFAULTS
    assert thr["max_span_s"] == 120.0
    import tomllib
    with open(os.path.join(os.path.dirname(consistency.__file__), "configs", "rules.toml"), "rb") as fh:
        assert tomllib.load(fh)["consistency"]["max_span_s"] == 120


def test_the_plan_decides_with_the_rules_only(tmp_path):
    class Boom:
        name = "llm"

        def decide(self, ctx):
            raise AssertionError("the LLM decider was called by the plan")

    folder = _burst_folder(tmp_path / "shoot", casts=_CASTS)
    paths = sorted(os.path.join(folder, n) for n in os.listdir(folder))
    cfg = RunConfig(backend="stub", consistency="bursts")
    pipe = Pipeline.build(cfg)
    boom = Boom()
    pipe.decider = boom
    got = plan(paths, cfg, pipe)
    assert len(got) == 3 and pipe.decider is boom        # biases found, decider put back


def test_bursts_mode_with_two_workers_and_through_the_web_job(tmp_path):
    folder = _burst_folder(tmp_path / "shoot", casts=_CASTS)
    one, two = tmp_path / "one", tmp_path / "two"
    batch.run(RunConfig(backend="stub", consistency="bursts"), folder, str(one))
    batch.run(RunConfig(backend="stub", consistency="bursts", workers=2), folder, str(two))
    for i in range(3):
        assert open(one / f"f{i}.jpg", "rb").read() == open(two / f"f{i}.jpg", "rb").read()
    assert any(json.load(open(two / f"f{i}.autoedit.json"))["wb_bias"] != [0.0, 0.0] for i in range(3))

    import time

    from autoedit.web import jobs
    out = tmp_path / "web"
    job = jobs.start(folder, None, str(out), {"backend": "stub", "consistency": "bursts"})
    t0 = time.time()
    while job.status not in ("done", "cancelled", "error") and time.time() - t0 < 60:
        time.sleep(0.05)
    assert job.status == "done" and jobs.snapshot(job)["failed"] == 0
    side = [json.load(open(out / f"f{i}.autoedit.json")) for i in range(3)]
    assert all(s["batch_group"].startswith("b") for s in side)


def _d(**kw):
    return {"fg_p50": 0.4, "exposure": 0.0, "shadows_ev": 0.0, "temp": 0.0, "tint": 0.0,
            "headroom_ev": 3.0, **kw}


def test_burst_biases_are_bounded():
    far = [_d(fg_p50=0.1, temp=-10, tint=8), _d(fg_p50=0.4), _d(fg_p50=0.8, temp=10, tint=-8)]
    for ev, t, n in burst_biases(far, 0.5, 4.0):
        assert abs(ev) <= 0.5 and abs(t) <= 4.0 and abs(n) <= 4.0
    # a small lift can be cancelled but never turned into a cut
    lift = [_d(fg_p50=0.9, exposure=0.1), _d(fg_p50=0.2), _d(fg_p50=0.2)]
    assert burst_biases(lift, 0.5, 4.0)[0][0] >= -0.1 - 1e-9
    # no headroom, no push
    tight = [_d(fg_p50=0.1, exposure=0.05, headroom_ev=0.2), _d(fg_p50=0.5), _d(fg_p50=0.5)]
    assert burst_biases(tight, 0.5, 4.0)[0][0] <= 1e-9
    same = burst_biases([_d(), _d(), _d()], 0.5, 4.0)
    assert same == [(0.0, 0.0, 0.0)] * 3


def test_bursts_mode_brings_a_burst_together(tmp_path):
    folder = _burst_folder(tmp_path / "shoot")
    plain, bursts = tmp_path / "plain", tmp_path / "bursts"
    batch.run(RunConfig(backend="stub", consistency="bursts"), folder, str(plain), consistent=False)
    batch.run(RunConfig(backend="stub", consistency="bursts"), folder, str(bursts))
    spread = lambda d: np.ptp([_mean_luma(str(d / f"f{i}.jpg")) for i in range(3)])
    assert spread(bursts) < spread(plain)
    side = [json.load(open(bursts / f"f{i}.autoedit.json")) for i in range(3)]
    assert all(s["batch_group"].startswith("b") for s in side)
    assert any(s["exposure_bias"] != 0 or s["wb_bias"] != [0.0, 0.0] for s in side)
    assert any("batch normalize" in n for s in side for n in s["notes"])


def test_wb_bias_is_applied_and_recorded(tmp_path):
    p = tmp_path / "x.jpg"
    _save(p, 0, 1.0, _stamp(0))
    pipe = Pipeline.build(RunConfig(backend="stub"))
    _, base = pipe.process_one(str(p), do_apply=False)
    _, moved = pipe.process_one(str(p), do_apply=False, wb_bias=(3.0, -2.0))
    assert moved.global_settings.get("temp", 0.0) == pytest.approx(base.global_settings.get("temp", 0.0) + 3.0)
    assert moved.global_settings.get("tint", 0.0) == pytest.approx(base.global_settings.get("tint", 0.0) - 2.0)
    assert any("white balance" in n for n in moved.notes)
    assert moved.wb_bias == [3.0, -2.0] and base.wb_bias == [0.0, 0.0]

    folder = _burst_folder(tmp_path / "shoot", gains=(0.6, 1.0, 1.5), casts=_CASTS)
    out = tmp_path / "out"
    batch.run(RunConfig(backend="stub", consistency="bursts"), folder, str(out))
    side = [json.load(open(out / f"f{i}.autoedit.json")) for i in range(3)]
    # the re-render check below means nothing unless the white balance was really moved
    assert any(s["wb_bias"] != [0.0, 0.0] for s in side)
    for i in range(3):
        before = open(out / f"f{i}.jpg", "rb").read()
        adjust.rerender(str(out / f"f{i}.jpg"))
        assert open(out / f"f{i}.jpg", "rb").read() == before
    with pytest.raises(ValueError):       # a hand-edited sidecar with one value
        Result.model_validate({**side[0], "wb_bias": [1.0]})


def test_cli_and_config(tmp_path):
    assert load_run_config(SimpleNamespace(configs_dir=None, config=None)).consistency == "brightness"
    assert load_run_config(SimpleNamespace(configs_dir=None, config=None, consistency="bursts")).consistency == "bursts"
    run = tmp_path / "run.toml"
    run.write_text('[run]\nconsistency = "bursts"\n')
    assert load_run_config(SimpleNamespace(configs_dir=None, config=str(run))).consistency == "bursts"
    with pytest.raises(Exception):
        RunConfig(consistency="nope")
