"""F27 T1: the history of your own edits (off by default), where it is written, and `autoedit history`.
Every test that writes a history uses a profile folder under its own tmp_path."""

import contextlib
import io
import json
import os
import time
import tomllib
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image as PILImage

from autoedit import adjust, batch, cli, emit, history, paths, recipe
from autoedit.contract import RunConfig
from autoedit.pipeline import Pipeline
from autoedit.segment.base import RegionMask
from autoedit.web import jobs, service


def _photo(path, k=0):
    """A synthetic photo; every k is a different photo (the history tells photos apart by their numbers)."""
    a = np.zeros((64, 64, 3), np.float32)
    a[:32] = [100, 140, 200]
    a[32:] = [60, 110, 70]
    a[24:48, 20:44] = [200, 150, 120]
    rng = np.random.default_rng(k)
    a = a * (0.7 + 0.04 * k) + rng.random((64, 64, 3)) * 6
    PILImage.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB").save(str(path), quality=95)


def _folder(tmp_path, n=3, name="in"):
    d = tmp_path / name
    d.mkdir()
    for k in range(n):
        _photo(d / f"p{k}.jpg", k)
    return str(d)


def _profile(tmp_path, on=True):
    prof = tmp_path / "profile"
    prof.mkdir(exist_ok=True)
    (prof / "defaults.toml").write_text(f"[run]\nhistory = {'true' if on else 'false'}\n")
    return str(prof)


def _rows(prof):
    path = os.path.join(prof, history.FILE)
    return [json.loads(x) for x in open(path).read().splitlines()] if os.path.exists(path) else []


def _wait(job, timeout=30):
    t0 = time.time()
    while job.status not in ("done", "cancelled", "error") and time.time() - t0 < timeout:
        time.sleep(0.05)
    return job


@contextlib.contextmanager
def _env(**kw):
    old = {k: os.environ.get(k) for k in kw}
    os.environ.update(kw)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _result(tmp_path, **cfg_kw):
    """(result, cfg, pixels) of one processed photo."""
    _photo(tmp_path / "one.jpg", 1)
    cfg = RunConfig(backend="stub", **cfg_kw)
    out, res = Pipeline.build(cfg).process_one(str(tmp_path / "one.jpg"), do_apply=True)
    return res, cfg, out


def test_off_by_default_writes_nothing(tmp_path):
    assert RunConfig().history is False
    with open(os.path.join(paths._packaged(), "defaults.toml"), "rb") as fh:
        assert tomllib.load(fh)["run"]["history"] is False
    src, prof = _folder(tmp_path), str(tmp_path / "never")
    off, on = str(tmp_path / "off"), str(tmp_path / "on")
    batch.run(RunConfig(backend="stub", profile_dir=prof), src, off)
    assert not os.path.exists(prof)
    os.makedirs(prof)
    batch.run(RunConfig(backend="stub", profile_dir=prof, history=True), src, on)
    assert len(_rows(prof)) == 3
    for n in ("p0", "p1", "p2"):
        assert open(f"{off}/{n}.jpg", "rb").read() == open(f"{on}/{n}.jpg", "rb").read()
        for out in (off, on):
            run_config = json.load(open(f"{out}/{n}.autoedit.json"))["audit"]["run_config"]
            assert "history" not in run_config and "profile_dir" not in run_config


def test_one_line_with_exactly_these_fields(tmp_path):
    prof = _profile(tmp_path)
    batch.run(RunConfig(backend="stub", history=True, profile_dir=prof), _folder(tmp_path), str(tmp_path / "o"))
    rows = _rows(prof)
    assert len(rows) == 3
    for row in rows:
        assert tuple(row) == history.KEYS
        assert set(row["features"]) == set(recipe.RECIPE_FEATURES)
        assert row["has"] == ["skin", "subject"]      # the run's own concepts that were found, sorted
    if os.name != "nt":
        assert os.stat(os.path.join(prof, history.FILE)).st_mode & 0o777 == 0o600


class _Finds:
    """Finds the subject and a 'jacket' (a word the configs do not define)."""
    name = "fake"

    def segment(self, img, concepts):
        h, w = img.rgb.shape[:2]
        out = []
        for c in ("subject", "jacket"):
            if c in concepts:
                m = np.zeros((h, w), bool)
                m[int(.4 * h):int(.6 * h), int(.4 * w):int(.6 * w)] = True
                out.append(RegionMask(c, m, 0.9, 0.04))
        return out


def test_a_line_holds_nothing_that_identifies(tmp_path):
    folder = tmp_path / "Client Folder 2026"
    folder.mkdir()
    prof = _profile(tmp_path)
    cfg = RunConfig(backend="stub", concepts=["subject"], prompt="for the client", edit="brighten the jacket",
                    history=True, profile_dir=prof)
    pipe = Pipeline.build(cfg)
    pipe.segmenter = _Finds()
    marks = service.check_marks([{"box": [0.35, 0.35, 0.65, 0.65], "kind": "area", "edit": "brighten this"}],
                                pipe.vocab())
    madj = service.mark_adjustments(pipe, marks)[0]
    for k in range(2):
        src = folder / f"IMG_PRIVATE_{k}.jpg"
        _photo(src, k)
        out, res = pipe.process_one(str(src), do_apply=True, adjustments=madj)
        emit.write_outputs(out, res, str(tmp_path / f"o{k}.jpg"), str(tmp_path / f"o{k}.autoedit.json"), cfg, str(src))
    text = open(os.path.join(prof, history.FILE)).read()
    for secret in ("IMG_PRIVATE", "Client", "jacket", "brighten", "0.3500,0.3500", "client", str(folder), str(tmp_path)):
        assert secret not in text, secret
    assert [r["left_out"] for r in _rows(prof)] == [{"mark": 1, "word": 1}] * 2
    res2 = res.model_copy(deep=True)
    assert history.line(res2, cfg.model_copy(update={"look": "My Look"}), "2026-01-01")["look"] == ""


def test_changes_are_summed_and_only_what_was_made(tmp_path):
    from autoedit.contract import Adjustment

    res, cfg, _ = _result(tmp_path, history=True)
    res.adjustments = [
        Adjustment(target="sky", ops={"exposure": -0.2}, scope="photo", status="applied"),
        Adjustment(target="sky", ops={"exposure": -0.1}, scope="photo", status="clamped"),
        Adjustment(target="global", ops={"temp": 5.0}, scope="run", status="applied"),
        Adjustment(target="sky", ops={"exposure": 0.9}, scope="photo", status="not_found"),
        Adjustment(target="skin", ops={"exposure": 0.9}, scope="photo", status="blocked"),
        Adjustment(target="jacket", ops={"exposure": 0.9}, scope="photo", status="not_found"),
    ]
    row = history.line(res, cfg, "2026-01-02")
    assert row["changes"] == {"sky": {"exposure": -0.3}, "global": {"temp": 5.0}}
    assert row["left_out"] == {"mark": 0, "word": 0}


def test_every_export_writes_a_line(tmp_path):
    prof = _profile(tmp_path)
    src = _folder(tmp_path, 8)
    cfg = RunConfig(backend="stub", history=True, profile_dir=prof)
    batch.run(cfg, src, str(tmp_path / "a"))
    assert len(_rows(prof)) == 8
    batch.run(cfg.model_copy(update={"workers": 2}), src, str(tmp_path / "b"), workers=2)
    assert len(_rows(prof)) == 16 and history.load(prof) == (history.load(prof)[0], 0)
    one = str(tmp_path / "one.jpg")
    _photo(one, 20)
    service.run_one(one, str(tmp_path / "c"), {"backend": "stub"}, profile=prof)
    assert len(_rows(prof)) == 17
    d = tmp_path / "jin"
    d.mkdir()
    _photo(d / "j.jpg", 21)
    job = _wait(jobs.start(str(d), None, str(tmp_path / "d"), {"backend": "stub"}, profile=prof))
    assert job.status == "done" and len(_rows(prof)) == 18
    adjust.rerender(os.path.join(str(tmp_path / "c"), "one.jpg"), sets=["global.exposure=0.1"], source=one, profile=prof)
    assert len(_rows(prof)) == 19
    batch.run(cfg, src, str(tmp_path / "e"), do_apply=False)       # a dry run
    service.preview(one, {"backend": "stub"}, profile=prof)
    assert len(_rows(prof)) == 19


def test_a_photo_exported_again_counts_once(tmp_path):
    prof = _profile(tmp_path)
    one = str(tmp_path / "one.jpg")
    _photo(one, 3)
    out = str(tmp_path / "out")
    service.run_one(one, out, {"backend": "stub"}, profile=prof)
    img = os.path.join(out, "one.jpg")
    adjust.rerender(img, sets=["global.exposure=0.1"], source=one, profile=prof)
    adjust.rerender(img, sets=["global.exposure=0.2"], source=one, reset=True, profile=prof)
    assert len(_rows(prof)) == 3
    rows, bad = history.load(prof)
    assert bad == 0 and len(rows) == 1 and rows[0]["changes"] == {"global": {"exposure": 0.2}}


def test_the_size_cap_keeps_the_newest(tmp_path):
    prof = str(tmp_path / "prof")
    res, cfg, _ = _result(tmp_path, history=True, profile_dir=prof)
    old = history.MAX_BYTES
    history.MAX_BYTES = 4000
    try:
        for i in range(40):
            res.global_stats = res.global_stats.model_copy(update={"mean_lum": i / 100})
            history.record(res, cfg, "2026-01-03")
            assert os.path.getsize(os.path.join(prof, history.FILE)) <= 4000
    finally:
        history.MAX_BYTES = old
    rows = _rows(prof)
    assert rows[-1]["features"]["mean_lum"] == 0.39
    assert history.load(prof) == (rows, 0) and len(rows) < 40


def test_unreadable_lines_are_skipped(tmp_path):
    prof = str(tmp_path / "prof")
    os.makedirs(prof)
    res, cfg, _ = _result(tmp_path)
    good = [history.line(res.model_copy(update={"scene": "landscape"}), cfg, "2026-01-04")]
    other = res.model_copy(deep=True)
    other.global_stats = other.global_stats.model_copy(update={"mean_lum": 0.123})
    good.append(history.line(other, cfg, "2026-01-05"))
    odd = dict(good[0], scene="../x")
    text = [json.dumps(good[0]), json.dumps(good[0])[:40], '{"v": 2}', "[1, 2]", "plain text", json.dumps(odd),
            json.dumps(good[1])]
    open(os.path.join(prof, history.FILE), "w").write("\n".join(text) + "\n")
    rows, bad = history.load(prof)
    assert bad == 5 and len(rows) == 2
    assert history.status(prof)["unreadable"] == 5 and history.status(prof)["days"] == 2


def test_a_failed_write_never_fails_the_export(tmp_path):
    src = _folder(tmp_path, 2)
    afile = tmp_path / "afile"
    afile.write_text("x")
    err = io.StringIO()
    history._SAID.clear()
    with contextlib.redirect_stderr(err):
        m = batch.run(RunConfig(backend="stub", history=True, profile_dir=str(afile)), src, str(tmp_path / "a"))
    assert m["processed"] == 2 and m["failed"] == 0
    assert os.path.exists(tmp_path / "a" / "p0.jpg") and os.path.exists(tmp_path / "a" / "p0.autoedit.json")
    assert err.getvalue().count("history: not written") == 1
    history._SAID.clear()
    err = io.StringIO()
    with _env(AUTOEDIT_PROFILE=""), contextlib.redirect_stderr(err):
        m = batch.run(RunConfig(backend="stub", history=True, profile_dir=None), src, str(tmp_path / "b"))
    assert m["processed"] == 2 and "profiles are switched off" in err.getvalue()
    assert err.getvalue().count("history: not written") == 1
    assert not [f for f in os.listdir(tmp_path / "b") if "history" in f]
    assert not os.path.exists(tmp_path / "profile")


def test_the_setting_reaches_every_builder(tmp_path):
    prof = _profile(tmp_path)
    def cfg_of(**kw):
        return cli.load_run_config(SimpleNamespace(**{"configs_dir": None, "config": None, **kw}))

    assert cfg_of().history is False and cfg_of().profile_dir is None
    assert cfg_of(history=True).history is True
    assert cfg_of(profile=prof).history is True and cfg_of(profile=prof).profile_dir == prof
    assert cfg_of(profile=prof, history=False).history is False          # --no-history wins
    run_file = tmp_path / "run.toml"
    run_file.write_text("[run]\nhistory = true\n")
    assert cfg_of(config=str(run_file)).history is True
    assert cfg_of(config=str(run_file), history=False).history is False
    assert service.make_cfg({}, None, prof).history is True
    assert service.make_cfg({}, None, prof).profile_dir == prof
    assert service.make_cfg({"history": False}, None, prof).history is False
    assert service.make_cfg({"profile_dir": "x"}, None, prof).profile_dir == prof
    assert service.make_cfg({"backend": "stub"}).history is False
    # a sidecar that was hand-edited to switch it on writes nothing
    one = str(tmp_path / "one.jpg")
    _photo(one, 2)
    out = str(tmp_path / "out")
    service.run_one(one, out, {"backend": "stub"})
    side = os.path.join(out, "one.autoedit.json")
    data = json.load(open(side))
    elsewhere = str(tmp_path / "elsewhere")
    data["audit"]["run_config"].update(history=True, profile_dir=elsewhere)
    json.dump(data, open(side, "w"))
    adjust.rerender(os.path.join(out, "one.jpg"), sets=["global.exposure=0.1"], source=one)
    assert not os.path.exists(elsewhere)


def _main(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def test_cli_history(tmp_path):
    prof = _profile(tmp_path, on=False)
    code, out, _ = _main("history", "status", "--profile", prof)
    assert code == 0 and "history: off (turn it on with `history = true` under [run]" in out
    assert f"file: none yet ({os.path.join(prof, history.FILE)})" in out
    _profile(tmp_path, on=True)
    batch.run(RunConfig(backend="stub", history=True, profile_dir=prof), _folder(tmp_path), str(tmp_path / "o"))
    code, out, _ = _main("history", "status", "--profile", prof)
    assert code == 0 and "history: on" in out and "photos: 3 on 1 day (" in out
    code, out, _ = _main("history", "clear", "--profile", prof)
    assert code == 1 and "would delete the history of 3 photos" in out and os.path.exists(os.path.join(prof, history.FILE))
    code, out, _ = _main("history", "clear", "--yes", "--profile", prof)
    assert code == 0 and "deleted the history of 3 photos" in out and not os.path.exists(os.path.join(prof, history.FILE))
    code, out, _ = _main("history", "clear", "--yes", "--profile", prof)
    assert code == 0 and "no history to clear" in out
    with _env(AUTOEDIT_PROFILE=""):
        code, _, err = _main("history", "status")
    assert code == 2 and "profiles are switched off" in err


def test_a_name_the_configs_do_not_define_never_reaches_the_file(tmp_path):
    from autoedit.contract import Adjustment

    prof = _profile(tmp_path)
    src = _folder(tmp_path, 1)
    batch.run(RunConfig(backend="stub", look="jane_doe_client", history=True, profile_dir=prof), src, str(tmp_path / "a"))
    one = str(tmp_path / "one.jpg")
    _photo(one, 21)
    service.run_one(one, str(tmp_path / "b"), {"backend": "stub", "look": "smith_wedding"}, profile=prof)
    text = open(os.path.join(prof, history.FILE)).read()
    assert "jane_doe" not in text and "smith_wedding" not in text and [r["look"] for r in _rows(prof)] == ["", ""]
    res, cfg, _ = _result(tmp_path, history=True, look="natural")
    assert history.line(res, cfg, "2026-01-01")["look"] == "natural"
    res.adjustments = [Adjustment(target="global", ops={"JaneDoe": 0.3, "exposure": 0.1}, scope="photo", status="applied")]
    assert history.line(res, cfg, "2026-01-01")["changes"] == {"global": {"exposure": 0.1}}


def test_planted_bad_lines_are_skipped_and_style_suggest_works(tmp_path):
    prof = str(tmp_path / "prof")
    os.makedirs(prof)
    res, cfg, _ = _result(tmp_path)
    row = history.line(res, cfg, "2026-01-04")
    bad = [dict(row, left_out={"mark": "x"}), dict(row, has=["Jane Doe"]),
           dict(row, changes={"JaneDoe": {"exposure": 0.1}}), dict(row, changes={"sky": {"JaneDoe": 0.1}})]
    open(os.path.join(prof, history.FILE), "w").write("\n".join(json.dumps(r) for r in [row] + bad) + "\n")
    rows, n = history.load(prof)
    assert len(rows) == 1 and n == 4
    assert _main("style", "suggest", "--profile", prof)[0] == 0


def test_an_error_inside_line_never_fails_the_export(tmp_path):
    def boom(*a, **k):
        raise TypeError("unexpected")

    real, history.line = history.line, boom
    history._SAID.clear()
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            m = batch.run(RunConfig(backend="stub", history=True, profile_dir=_profile(tmp_path)),
                          _folder(tmp_path, 1), str(tmp_path / "a"))
    finally:
        history.line = real
    assert m["processed"] == 1 and m["failed"] == 0 and os.path.exists(tmp_path / "a" / "p0.autoedit.json")
    assert "history: not written (unexpected)" in err.getvalue()
    history._SAID.clear()
