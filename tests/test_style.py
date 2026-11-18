"""F27 T2: a look that carries a habit (a `habit` table, replayed per scene as the same typed
instruction). Every test that writes a look writes it into a profile under its own tmp_path."""

import contextlib
import csv
import io
import json
import os
import tomllib
from dataclasses import asdict

import numpy as np
import pytest
from PIL import Image as PILImage

from autoedit import adjust, batch, calibrate, cli, history, instructions, looks, paths
from autoedit.contract import Adjustment, RunConfig
from autoedit.looks import load_habits
from autoedit.pipeline import Pipeline

HABIT = {"landscape": {"sky": {"exposure": -0.3}}}
NOTE = "look mine: applied to sky (exposure -0.30)"


def _make(path, k=0):
    a = np.zeros((128, 128, 3), np.float32)
    a[:70] = [100, 140, 200]
    a[70:] = [60, 110, 70]
    yy, xx = np.mgrid[0:128, 0:128]
    a[(((xx - 64) / 28) ** 2 + ((yy - 80) / 36) ** 2) <= 1.0] = [200, 150, 120]
    a = np.clip(a * (1.0 + 0.04 * k), 0, 255).astype(np.uint8)
    PILImage.fromarray(a, "RGB").save(str(path), quality=95)


def _folder(tmp_path, n=3, name="in"):
    d = tmp_path / name
    d.mkdir()
    for k in range(n):
        _make(d / f"p{k}.jpg", k)
    return str(d)


def _profile(tmp_path, habit=HABIT, name="mine", folder="profile"):
    """A profile whose looks.toml holds `name` = the packaged `natural` plus the habit."""
    prof = str(tmp_path / folder)
    natural = looks.load(os.path.join(paths.configs_dir(), "looks.toml"))["natural"]
    cli.write_look(prof, name, asdict(natural), habit=habit)
    return prof


def _cfg(prof=None, **kw):
    return RunConfig(**{"backend": "stub", **(paths.config_paths(None, prof) if prof else {}), **kw})


def _one(tmp_path, cfg, adjustments=None, on_plan=None):
    img = tmp_path / "one.jpg"
    if not img.exists():
        _make(img)
    return Pipeline.build(cfg).process_one(str(img), do_apply=True, adjustments=adjustments, on_plan=on_plan)


def _files(root):
    return {os.path.relpath(os.path.join(d, f), root): open(os.path.join(d, f), "rb").read()
            for d, _dirs, fs in os.walk(root) for f in fs}


def test_no_habit_by_default_and_byte_identical(tmp_path):
    assert load_habits(os.path.join(paths.configs_dir(), "looks.toml")) == {}
    assert Pipeline.build(RunConfig(backend="stub")).habits == {}
    prof = _profile(tmp_path)                    # a habit on a look that no run uses
    src = _folder(tmp_path)
    batch.run(RunConfig(backend="stub"), src, str(tmp_path / "a"))
    batch.run(_cfg(prof), src, str(tmp_path / "b"))
    a, b = _files(str(tmp_path / "a")), _files(str(tmp_path / "b"))
    jpgs = sorted(k for k in a if k.endswith(".jpg"))
    assert jpgs and jpgs == sorted(k for k in b if k.endswith(".jpg"))
    assert all(a[k] == b[k] for k in jpgs)
    assert not Pipeline.build(_cfg(prof)).habits   # the look in use is natural: nothing to replay


def test_a_habit_is_the_same_typed_instruction(tmp_path):
    prof = _profile(tmp_path)
    land = dict(concepts=["sky", "subject"])
    mine, res = _one(tmp_path, _cfg(prof, look="mine", **land))
    nat, nat_res = _one(tmp_path, _cfg(prof, look="natural", edit="darken the sky", **land))
    assert res.scene == "landscape" and np.array_equal(mine, nat)
    (a,) = res.adjustments
    assert (a.scope, a.status, a.source, a.target) == ("look", "applied", "look mine", "sky")
    assert NOTE in res.notes
    # a photo of people: the pixels of natural, and no adjustment
    people, p_res = _one(tmp_path, _cfg(prof, look="mine"))
    plain, _ = _one(tmp_path, _cfg(prof, look="natural"))
    assert p_res.scene == "people" and p_res.adjustments == [] and np.array_equal(people, plain)
    # typed changes add to the look's and the sum stays inside [limits]
    seen = {}
    _out, res = _one(tmp_path, _cfg(prof, look="mine", edit="darken the sky a lot, darken the sky a lot", **land),
                     on_plan=lambda adjs, plan, before, settings: seen.update(asked=plan.asked))
    assert seen["asked"]["sky"]["exposure"] == pytest.approx(-0.75)
    assert [a.status for a in res.adjustments] == ["clamped"] * 3


def test_rerender_and_workers(tmp_path):
    prof = _profile(tmp_path)
    src = _folder(tmp_path, n=4)
    cfg = _cfg(prof, look="mine", concepts=["sky", "subject"])
    batch.run(cfg, src, str(tmp_path / "a"))
    batch.run(cfg.model_copy(update={"workers": 2}), src, str(tmp_path / "b"), workers=2)
    a, b = _files(str(tmp_path / "a")), _files(str(tmp_path / "b"))
    assert all(a[k] == b[k] for k in a if k.endswith(".jpg"))
    one = os.path.join(src, "p1.jpg")
    out = os.path.join(str(tmp_path / "a"), "p1.jpg")
    adjust.rerender(out, source=one, profile=prof)      # nothing new: the habit is built again from the look
    assert open(out, "rb").read() == a["p1.jpg"]
    # a habit on `foliage`, which the style does not look for: the parent asks for it before a worker needs it
    prof2 = _profile(tmp_path, habit={"landscape": {"foliage": {"exposure": -0.3}}}, folder="profile2")
    cfg2 = _cfg(prof2, look="mine", backend="sam3", concepts=["sky", "subject"])
    out_dir = str(tmp_path / "cache")

    def stub_pipe(inner=None):
        pipe = Pipeline.build(cfg2.model_copy(update={"backend": "stub"}))
        pipe.cfg = cfg2
        pipe.use_mask_cache(out_dir)
        if inner is not None:
            pipe.segmenter.inner = inner
        return pipe

    photo = os.path.join(src, "p0.jpg")
    parent = stub_pipe()
    parent.presegment(photo)
    worker = stub_pipe(inner=batch._NoModel())
    got, got_result = worker.process_one(photo)
    want, want_result = parent.process_one(photo)
    assert [a.status for a in want_result.adjustments] == ["applied"] and want_result.adjustments[0].target == "foliage"
    assert np.array_equal(got, want) and got_result.adjustments == want_result.adjustments


def _bad(tmp_path, body):
    prof = tmp_path / "bad"
    prof.mkdir(exist_ok=True)
    (prof / "looks.toml").write_text(body)
    return str(prof)


def test_what_a_habit_table_may_not_hold(tmp_path):
    many = "".join(f'[mine.habit.landscape.t{i}]\nexposure = -0.1\n' for i in range(13))
    cases = ["[mine.habit.landscape.sky]\nbogus = 1.0\n",
             '[mine.habit.landscape."mark:thing:x"]\nexposure = -0.1\n',
             "[mine.habit.landscape.sky]\nexposure = nan\n",
             "[mine.habit.landscape.sky]\nexposure = true\n",
             '[mine.habit."../x".sky]\nexposure = -0.1\n',
             f'[mine.habit.landscape.{"a" * 50}]\nexposure = -0.1\n',
             many,
             "[mine]\nhabit = 3\n"]
    for body in cases:
        prof = _bad(tmp_path, body)
        with pytest.raises(ValueError) as e:
            paths.config_paths(None, prof)
        assert os.path.join(prof, "looks.toml") in str(e.value) and "look mine" in str(e.value), body
    with pytest.raises(ValueError) as e:
        paths.config_paths(None, _bad(tmp_path, "[mine.habit.landscape.sky]\nexposure = 1e300\n"))
    assert "look mine" in str(e.value) and "exposure must be between -3 and 3" in str(e.value)
    # 12 targets are fine
    paths.config_paths(None, _bad(tmp_path, "".join(f'[mine.habit.landscape.t{i}]\nexposure = -0.1\n' for i in range(12))))
    # an op with no limit, and a place that does not exist, are refused when the pipeline is built, for that look only
    for habit, what in (({"landscape": {"sky": {"dehaze": 10.0}}}, "dehaze"),
                        ({"landscape": {"place:nowhere": {"exposure": -0.2}}}, "nowhere")):
        prof = _bad(tmp_path, "")
        os.remove(os.path.join(prof, "looks.toml"))
        natural = looks.load(os.path.join(paths.configs_dir(), "looks.toml"))["natural"]
        cli.write_look(prof, "other", asdict(natural))
        cli.write_look(prof, "mine", asdict(natural), habit=habit)
        with pytest.raises(ValueError) as e:
            Pipeline.build(_cfg(prof, look="mine"))
        assert "look mine" in str(e.value) and what in str(e.value)
        Pipeline.build(_cfg(prof, look="other"))
        Pipeline.build(_cfg(prof))


def _run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


def test_look_commands_keep_the_habit(tmp_path):
    prof = _profile(tmp_path)
    assert load_habits(os.path.join(prof, "looks.toml")) == {"mine": HABIT}
    assert _run_cli(["looks", "save", "b", "--from", "mine", "--profile", prof])[0] == 0
    assert load_habits(os.path.join(prof, "looks.toml")) == {"mine": HABIT, "b": HABIT}
    assert _run_cli(["looks", "save", "c", "--profile", prof])[0] == 0   # a look without one stays without
    code, text, _ = _run_cli(["looks", "list", "--profile", prof])
    assert code == 0 and "  habit: landscape sky exposure -0.3" in text.splitlines()
    assert text.count("habit:") == 2
    assert _run_cli(["looks", "delete", "b", "--profile", prof])[0] == 0
    data = tomllib.loads(open(os.path.join(prof, "looks.toml")).read())
    assert "b" not in data and data["mine"]["habit"] == HABIT
    # a calibration started from a habit look saves the habit with the fitted look
    photos = tmp_path / "photos"
    photos.mkdir()
    for k in range(6):
        _make(photos / f"p{k}.jpg", k)
    out = str(tmp_path / "calib")
    calibrate.start(str(photos), out, count=4, rounds=1, backend="stub", profile=prof, look="mine")
    folder = os.path.join(out, "round-1")
    pairs = json.load(open(os.path.join(folder, "pairs.json")))
    with open(os.path.join(folder, "results.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["gallery_id", "pair", "choice"])
        for pair in pairs["pairs"]:
            w.writerow([pairs["gallery_id"], pair["id"], "tie"])
    r = calibrate.advance(out, profile=prof)
    assert r["done"]
    assert load_habits(os.path.join(prof, "looks.toml"))["calibrated"] == HABIT
    # the look's own habit is not history; what is typed on top of it is
    cfg = _cfg(prof, look="mine", concepts=["sky", "subject"])
    (warm,), problems = instructions.parse("warm the sky", instructions.load_vocab(cfg.instructions_path), {"sky"})
    _out, res = _one(tmp_path, cfg, adjustments=[warm.model_copy(update={"scope": "photo"})])
    assert [a.scope for a in res.adjustments] == ["look", "photo"]
    assert history.line(res, cfg, "2026-11-17")["changes"] == {"sky": {"temp": 10.0}}


# ── T3: suggestions from the history, the yes, the commands and the routes ──────────────────

import random  # noqa: E402
import socket  # noqa: E402

from autoedit import style  # noqa: E402

LIMITS = {"exposure": 0.75, "temp": 50, "tint": 100, "saturation": 25, "clarity": 20, "contrast": 15}


def _row(i, scene="landscape", look="natural", day=1, changes=None, has=("sky",)):
    return {"v": 1, "day": f"2026-11-{day:02d}", "scene": scene, "look": look, "features": {"f": float(i)},
            "has": sorted(has), "changes": changes or {}, "left_out": {"mark": 0, "word": 0}}


def _habit_rows(vals, days=3, target="sky", op="exposure", scene="landscape", look="natural", has=("sky",), start=0):
    """One photo per value (0 = untouched), spread over `days` days."""
    return [_row(start + i, scene, look, 1 + i % days, {target: {op: v}} if v else {}, has)
            for i, v in enumerate(vals)]


def _sugg(rows, look="natural", conf=None):
    return style.suggest(rows, look, conf or style.load_config(), LIMITS)


def _write_history(prof, rows):
    os.makedirs(prof, exist_ok=True)
    with open(os.path.join(prof, history.FILE), "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def test_one_planted_habit_gives_exactly_that_suggestion_and_no_habit_none():
    rows = _habit_rows([-0.3] * 48 + [0] * 12, days=6)
    rows[50:53] = [dict(r, changes={"global": {"temp": 5.0}}) for r in rows[50:53]]      # other edits
    rows += _habit_rows([0] * 30, scene="people", has=("sky",), start=100)               # people
    rows += _habit_rows([-0.3] * 20, look="punchy", start=200)                           # another look
    (s,) = _sugg(rows)
    assert (s["id"], s["value"], s["photos"], s["of"], s["days"]) == ("landscape/sky/exposure", -0.3, 48, 60, 6)
    ask, fact = style.sentence(s, style.load_config())
    assert ask == "You made the sky darker on 48 of 60 landscapes. Add it to your look?"
    assert fact == ("Counted: the 60 landscapes where the sky was found; 48 of them on 6 days. "
                    "The look would add exposure -0.3 to the sky on landscapes.")
    rng = random.Random(7)
    for _ in range(200):
        n = rng.randint(20, 60)
        rows = [_row(i, rng.choice(["landscape", "people"]), "natural", rng.randint(1, 9),
                     {rng.choice(["sky", "global", "place:top"]): {rng.choice(["exposure", "temp"]):
                                                                      rng.choice([-0.3, 0.3, 5.0, -5.0])}}
                     if rng.random() < 0.3 else {}) for i in range(n)]
        assert _sugg(rows) == []
    assert _sugg([]) == []


def test_the_rule_at_its_edges(tmp_path):
    def got(vals, **kw):
        return _sugg(_habit_rows(vals, **kw))

    assert got([-0.3] * 12) and not got([-0.3] * 11)
    assert not got([-0.3] * 12, days=2)
    assert got([-0.3] * 14 + [0] * 6) and not got([-0.3] * 13 + [0] * 7)
    assert got([-0.3] * 14 + [0.3] * 3)
    assert not got([-0.3] * 12 + [0.3] * 12)
    assert not got([-0.3] * 14 + [0] * 106)
    assert got([-2.0] * 12)[0]["value"] == -0.75
    assert got([-0.15] * 6 + [-0.30] * 8 + [0] * 3)[0]["value"] == -0.15
    dress = dict(target="dress", has=("sky",))
    (s,) = got([-0.3] * 14, **dress)
    assert s["found"] is False and "only looked for where an instruction names it" in style.sentence(
        s, style.load_config())[1]
    assert not got([-0.3] * 14 + [0] * 16, **dress)
    assert not got([0.3] * 14, op="dehaze")
    five = _habit_rows([-0.3] * 5)
    prof = tmp_path / "profile"
    prof.mkdir()
    (prof / "style.toml").write_text("[suggest]\nmin_photos = 2\n")
    assert not _sugg(five) and _sugg(five, conf=style.load_config(str(prof)))


def _tree(prof, with_history=True):
    prof = str(prof)
    rows = _habit_rows([-0.3] * 14 + [0] * 3, days=5)
    if with_history:
        _write_history(prof, rows)
    other = looks.load(os.path.join(paths.configs_dir(), "looks.toml"))["punchy"]
    cli.write_look(prof, "other", asdict(other))
    return prof


def test_a_yes_changes_the_look_file_and_nothing_else(tmp_path):
    prof = _tree(tmp_path / "profile")
    (tmp_path / "profile" / "defaults.toml").write_text("[run]\nhistory = true\n")
    packaged = _files(paths.configs_dir())
    before = _files(str(tmp_path))
    ov = style.overview(prof)
    assert [s["id"] for s in ov["suggestions"]] == ["landscape/sky/exposure"] and ov["look"] == "natural"
    assert _files(str(tmp_path)) == before                       # overview writes nothing
    r = style.add_to_look(prof, "mine")
    after = _files(str(tmp_path))
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    assert changed == {os.path.join("profile", "looks.toml")}
    assert r["habit"] == HABIT and r["added"] == ["landscape/sky/exposure"] and r["replaced"] is False
    data = tomllib.loads(open(os.path.join(prof, "looks.toml")).read())
    natural = asdict(looks.load(os.path.join(paths.configs_dir(), "looks.toml"))["natural"])
    assert {k: v for k, v in data["mine"].items() if k != "habit"} == natural and data["mine"]["habit"] == HABIT
    assert data["other"] == asdict(looks.load(os.path.join(paths.configs_dir(), "looks.toml"))["punchy"])
    assert _files(paths.configs_dir()) == packaged
    again = style.add_to_look(prof, "mine")
    assert again["replaced"] is True and _files(str(tmp_path)) == after     # the same bytes
    for name, ids in (("natural", None), ("punchy", None), ("Bad Name", None), ("new", ["no/such/id"])):
        with pytest.raises(ValueError):
            style.add_to_look(prof, name, ids)
        assert _files(str(tmp_path)) == after
    empty = _tree(tmp_path / "empty", with_history=False)
    snap = _files(str(tmp_path / "empty"))
    with pytest.raises(ValueError):
        style.add_to_look(empty, "new")
    assert _files(str(tmp_path / "empty")) == snap
    # the new look repeats the habit; its own exports then suggest nothing more
    land = dict(concepts=["sky", "subject"])
    mine, _ = _one(tmp_path, _cfg(prof, look="mine", **land))
    nat, _ = _one(tmp_path, _cfg(prof, look="natural", edit="darken the sky", **land))
    assert np.array_equal(mine, nat)
    src = _folder(tmp_path)
    batch.run(_cfg(prof, look="mine", history=True, profile_dir=prof, **land), src, str(tmp_path / "out"))
    assert style.overview(prof, look="mine")["suggestions"] == []


def test_cli_style_suggest_and_api_style_routes(tmp_path):
    prof = _tree(tmp_path / "profile")
    code, text, _ = _run_cli(["style", "suggest", "--profile", prof])
    assert code == 0 and text.rstrip().endswith("Nothing has changed.")
    assert "landscape/sky/exposure\n  You made the sky darker on 14 of 17 landscapes." in text
    assert "History: 17 photos on 5 days. Look natural: 17 photos." in text
    assert not os.path.exists(os.path.join(prof, "mine"))
    assert _run_cli(["style", "suggest", "--profile", prof, "--only", "no/such/id"])[0] == 2
    assert _run_cli(["style", "suggest", "--profile", prof, "--yes", "natural"])[0] == 2
    assert _run_cli(["style", "suggest", "--profile", prof, "--yes", "mine", "--only", "no/such/id"])[0] == 2
    code, text, _ = _run_cli(["style", "suggest", "--profile", prof, "--yes", "mine"])
    assert code == 0 and "the look natural plus landscape/sky/exposure" in text
    assert load_habits(os.path.join(prof, "looks.toml"))["mine"] == HABIT
    (tmp_path / "none").mkdir()
    code, text, _ = _run_cli(["style", "suggest", "--profile", str(tmp_path / "none")])
    assert code == 0 and "No history yet." in text
    # the routes
    snap = json.load(open(os.path.join(os.path.dirname(__file__), "snapshots", "api_v1.json")))
    assert "GET /api/v1/style/suggestions" in snap and "POST /api/v1/style/accept" in snap
    try:
        from fastapi.testclient import TestClient

        from autoedit.web import app as webapp
    except Exception:
        return
    prof2 = _tree(tmp_path / "profile2")
    client = TestClient(webapp.create_app(paths.configs_dir(), local_only=True, profile=prof2),
                        base_url="http://localhost")
    got = client.get("/api/v1/style/suggestions").json()
    assert [s["id"] for s in got["suggestions"]] == ["landscape/sky/exposure"] and got["photos"] == 17
    r = client.post("/api/v1/style/accept", json={"name": "mine", "ids": ["landscape/sky/exposure"]})
    assert r.status_code == 200 and load_habits(os.path.join(prof2, "looks.toml"))["mine"] == HABIT
    for body in ({"name": "Bad Name"}, {"name": "x", "ids": "all"}, {"name": "natural"}):
        r = client.post("/api/v1/style/accept", json=body)
        assert r.status_code == 400 and r.json()["code"] == "bad_request"


def test_nothing_opens_a_connection_and_words_have_limits(tmp_path):
    def no(*a, **k):
        raise AssertionError("a connection was opened")

    saved = socket.socket, socket.create_connection
    socket.socket = socket.create_connection = no
    try:
        prof = _tree(tmp_path / "profile")
        batch.run(_cfg(prof, history=True, profile_dir=prof), _folder(tmp_path), str(tmp_path / "out"))
        rows, _bad = history.load(prof)
        assert len(rows) == 17 + 3
        assert style.overview(prof)["suggestions"]
        style.add_to_look(prof, "mine")
    finally:
        socket.socket, socket.create_connection = saved
    vocab = instructions.load_vocab(os.path.join(paths.configs_dir(), "instructions.toml"))
    assert all(op in vocab.limits for op in style.load_config()["words"])
