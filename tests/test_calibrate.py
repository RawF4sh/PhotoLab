"""F12: taste calibration (blind pairs along one dimension of the look, votes move the look)."""

import csv
import json
import os
import random
import re
import tomllib

import numpy as np
import pytest
from PIL import Image as PILImage

from autoedit import calibrate, cli, paths
from autoedit.contract import RunConfig
from autoedit.looks import Look
from autoedit.pipeline import Pipeline

CFG = {"min_votes": 4, "dead_zone": 0.2, "shrink": 0.6}
BOUNDS = {"exposure_mul": (0.4, 1.4), "contrast_mul": (0.5, 1.8), "curve_add": (-20.0, 30.0)}


def _photos(folder, n=6):
    """Dim, mildly textured scenes: the rules lift them, so every dimension changes the render."""
    os.makedirs(folder, exist_ok=True)
    rng = np.random.default_rng(11)
    for i in range(n):
        a = np.zeros((72, 96, 3), np.float32)
        a[:36] = [60 + 8 * i, 80 + 6 * i, 120 + 5 * i]
        a[36:] = [40 + 5 * i, 70 + 4 * i, 45 + 3 * i]
        a = np.clip(a + rng.integers(0, 24, a.shape), 0, 255).astype(np.uint8)
        PILImage.fromarray(a, "RGB").save(os.path.join(folder, f"p{i}.jpg"), quality=95)


def _write_votes(round_dir, rows):
    pairs = json.load(open(os.path.join(round_dir, "pairs.json")))
    with open(os.path.join(round_dir, "results.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["gallery_id", "pair", "choice"])
        for pair in pairs["pairs"]:
            w.writerow([pairs["gallery_id"], pair["id"], rows(pair)])
    return pairs


def _lift_up(pair):
    return str(pair["plus_slot"]) if pair["dimension"] == "lift" else "tie"


def _start(tmp_path, **kw):
    photos, out = str(tmp_path / "photos"), str(tmp_path / "calib")
    if not os.path.isdir(photos):
        _photos(photos)
    r = calibrate.start(photos, out, backend="stub", profile=str(tmp_path / "profile"), **kw)
    return photos, out, r


def test_update_math():
    steps = {"lift": {"exposure_mul": 0.25}}
    base = {"exposure_mul": 1.0}
    for votes, want in [((8, 0), 0.25), ((6, 2), 0.125), ((5, 3), 0.0625), ((4, 4), 0.0), ((1, 0), 0.0),
                        ((0, 8), -0.25)]:
        new, shrunk = calibrate.update(base, steps, {"lift": votes}, CFG, BOUNDS)
        assert new["exposure_mul"] - 1.0 == pytest.approx(want)
        assert shrunk["lift"]["exposure_mul"] == pytest.approx(0.15)
    new, _ = calibrate.update({"exposure_mul": 1.3}, steps, {"lift": (8, 0)}, CFG, BOUNDS)
    assert new["exposure_mul"] == pytest.approx(1.4)
    new, _ = calibrate.update({"exposure_mul": 0.5}, steps, {"lift": (0, 8)}, CFG, BOUNDS)
    assert new["exposure_mul"] == pytest.approx(0.4)
    new, _ = calibrate.update({}, steps, {"lift": (8, 0)}, CFG, BOUNDS)   # a missing field starts at the Look default
    assert new["exposure_mul"] == pytest.approx(1.25)


def test_variant_stays_in_bounds():
    v = calibrate.variant({"exposure_mul": 1.3, "curve_add": 5.0}, {"exposure_mul": 0.25, "curve_add": 6.0}, 1, BOUNDS)
    assert v.exposure_mul == pytest.approx(1.4) and v.curve_add == pytest.approx(11.0)
    assert calibrate.variant({}, {"curve_add": 6.0}, -1, BOUNDS).curve_add == pytest.approx(-6.0)


def test_simulated_voter_recovers_a_preference():
    conf = calibrate.load_config()
    steps, bounds, run = conf["steps"], conf["bounds"], dict(conf["run"])
    base = {"exposure_mul": 1.0, "vibrance_add": 0.0, "saturation_add": 0.0, "contrast_mul": 1.0,
            "curve_add": 5.0, "grade_highlights_temp": 0.0, "grade_shadows_temp": 0.0}
    target = {**base, "exposure_mul": 1.3, "vibrance_add": 12.0, "grade_highlights_temp": -10.0}
    first = {d: s for d, s in steps.items()}
    rng = random.Random(5)
    for _ in range(4):
        votes = {}
        for dim, step in steps.items():
            # where the target sits along the dimension, in steps from the base: the voter
            # prefers the variant on that side
            t = sum((target[f] - base[f]) * s for f, s in step.items()) / sum(s * s for s in step.values())
            plus = minus = 0
            for _photo in range(8):
                choice = "plus" if t > 1e-9 else "minus" if t < -1e-9 else "tie"
                if rng.random() < 0.10:
                    choice = rng.choice(("plus", "minus"))
                plus += choice == "plus"
                minus += choice == "minus"
            votes[dim] = (plus, minus)
        base, steps = calibrate.update(base, steps, votes, run, bounds)
    for dim, field in (("lift", "exposure_mul"), ("colour", "vibrance_add"), ("warmth", "grade_highlights_temp")):
        assert abs(base[field] - target[field]) <= 0.4 * first[dim][field]
    # the voter had no preference about depth: no move beyond the last (shrunk) step
    assert abs(base["contrast_mul"] - 1.0) <= steps["depth"]["contrast_mul"]
    assert abs(base["curve_add"] - 5.0) <= steps["depth"]["curve_add"]


def test_start_writes_a_round(tmp_path):
    photos, out, r = _start(tmp_path, count=3)
    assert r["round"] == 1 and r["pairs"] == 12
    folder = os.path.join(out, "round-1")
    assert os.path.isfile(os.path.join(out, "state.json")) and os.path.isfile(os.path.join(folder, "index.html"))
    meta = json.load(open(os.path.join(folder, "pairs.json")))
    pairs = meta["pairs"]
    assert len(pairs) == 12 and len({p["photo"] for p in pairs}) == 3
    assert {p["dimension"] for p in pairs} == {"lift", "depth", "colour", "warmth"}
    for p in pairs:
        a = open(os.path.join(folder, p["id"] + "_a.jpg"), "rb").read()
        b = open(os.path.join(folder, p["id"] + "_b.jpg"), "rb").read()
        assert a != b
    page = open(os.path.join(folder, "index.html"), encoding="utf-8").read()
    low = page.lower()
    conf = calibrate.load_config()
    assert all(p["id"] in page for p in pairs) and meta["gallery_id"] in page
    forbidden = [*conf["steps"], *conf["labels"].values(), "plus", "minus",
                 *{f for s in conf["steps"].values() for f in s}]
    assert not [w for w in forbidden if w.lower() in low]
    assert "http://" not in low and "https://" not in low and "<link" not in low and "src=\"http" not in low
    state_text = open(os.path.join(out, "state.json"), encoding="utf-8").read()
    assert str(tmp_path) not in state_text
    assert not any(isinstance(v, str) and os.path.isabs(v) for v in json.loads(state_text).values())
    assert not any(os.path.isabs(p) for p in json.loads(state_text)["photos"])


def test_sample_is_deterministic(tmp_path):
    photos = str(tmp_path / "photos")
    _photos(photos, n=6)
    chosen = []
    for name in ("a", "b"):
        calibrate.start(photos, str(tmp_path / name), count=3, seed=4, backend="stub", profile=str(tmp_path / "profile"))
        chosen.append(json.load(open(str(tmp_path / name / "state.json")))["photos"])
    assert chosen[0] == chosen[1] and len(chosen[0]) == 3


def test_continue_reads_votes_and_advances(tmp_path):
    photos, out, _ = _start(tmp_path, count=4)
    before = json.load(open(os.path.join(out, "state.json")))
    _write_votes(os.path.join(out, "round-1"), _lift_up)
    r = calibrate.advance(out, profile=str(tmp_path / "profile"))
    after = json.load(open(os.path.join(out, "state.json")))
    assert not r["done"] and after["round"] == 2
    conf = calibrate.load_config()          # the packaged numbers are data: read them, do not pin them
    step, shrink = conf["steps"]["lift"]["exposure_mul"], conf["run"]["shrink"]
    assert after["base"]["exposure_mul"] == pytest.approx(min(before["base"]["exposure_mul"] + step, 1.4))
    for field in ("contrast_mul", "curve_add", "vibrance_add", "grade_highlights_temp"):
        assert after["base"][field] == before["base"][field]
    assert after["steps"]["lift"]["exposure_mul"] == pytest.approx(step * shrink)
    assert after["votes"]["lift"] == [4, 0]
    assert os.path.isfile(os.path.join(out, "round-2", "index.html"))
    # the next round has no results.csv yet
    with pytest.raises(ValueError) as e:
        calibrate.advance(out, profile=str(tmp_path / "profile"))
    assert "round-2" in str(e.value)


def test_results_are_validated(tmp_path):
    photos, out, _ = _start(tmp_path, count=3)
    folder = os.path.join(out, "round-1")
    prof = str(tmp_path / "profile")
    with pytest.raises(ValueError) as e:
        calibrate.advance(out, profile=prof)
    assert "results.csv" in str(e.value) and folder in str(e.value)
    pairs = json.load(open(os.path.join(folder, "pairs.json")))
    gid, first = pairs["gallery_id"], pairs["pairs"][0]["id"]

    def write(rows):
        with open(os.path.join(folder, "results.csv"), "w", newline="") as fh:
            csv.writer(fh).writerows([["gallery_id", "pair", "choice"], *rows])

    def fails(rows, word):
        write(rows)
        with pytest.raises(ValueError) as err:
            calibrate.advance(out, profile=prof)
        assert word in str(err.value), str(err.value)

    fails([["other", first, "1"]], "another round")
    fails([[gid, "q99", "1"]], "unknown pair")
    fails([[gid, first, "1"], [gid, first, "2"]], "twice")
    fails([[gid, first, "3"]], "must be 1, 2 or tie")
    assert json.load(open(os.path.join(out, "state.json")))["round"] == 1   # nothing moved


def test_finish_writes_the_look_into_the_profile(tmp_path):
    prof = tmp_path / "profile"
    prof.mkdir()
    (prof / "looks.toml").write_text("[mine]\ncontrast_mul = 1.2\n")
    photos = str(tmp_path / "photos")
    _photos(photos)
    out = str(tmp_path / "calib")
    calibrate.start(photos, out, count=4, rounds=1, backend="stub", profile=str(prof))
    _write_votes(os.path.join(out, "round-1"), _lift_up)
    r = calibrate.advance(out, profile=str(prof))
    assert r["done"] and r["look_path"] == str(prof / "looks.toml")
    data = tomllib.loads((prof / "looks.toml").read_text())
    assert data["mine"] == {"contrast_mul": 1.2}
    want = min(1.0 + calibrate.load_config()["steps"]["lift"]["exposure_mul"], 1.4)
    assert data["calibrated"]["exposure_mul"] == pytest.approx(want)
    with pytest.raises(ValueError):
        calibrate.advance(out, profile=str(prof))   # already finished
    cfg = RunConfig(**paths.config_paths(None, str(prof)), look="calibrated", backend="stub")
    assert Pipeline.build(cfg).looks["calibrated"].exposure_mul == pytest.approx(want)


def test_finish_flag_stops_after_a_round(tmp_path):
    photos, out, _ = _start(tmp_path, count=4)
    _write_votes(os.path.join(out, "round-1"), _lift_up)
    r = calibrate.advance(out, finish=True, profile=str(tmp_path / "profile"))
    assert r["done"] and not os.path.exists(os.path.join(out, "round-2"))
    assert "calibrated" in tomllib.loads((tmp_path / "profile" / "looks.toml").read_text())


def test_moved_photo_folder_is_given_again(tmp_path):
    photos, out, _ = _start(tmp_path, count=4)
    moved = str(tmp_path / "moved")
    os.rename(photos, moved)
    _write_votes(os.path.join(out, "round-1"), _lift_up)
    with pytest.raises(ValueError) as e:
        calibrate.advance(out, profile=str(tmp_path / "profile"))
    assert "photo folder" in str(e.value)
    assert not calibrate.advance(out, photos=moved, profile=str(tmp_path / "profile"))["done"]


def test_cli_calibrate(tmp_path):
    photos, out = str(tmp_path / "photos"), str(tmp_path / "calib")
    prof = str(tmp_path / "profile")
    _photos(photos)
    assert cli.main(["calibrate", photos, "--out", out, "--count", "3", "--backend", "stub", "--profile", prof]) == 0
    assert os.path.isfile(os.path.join(out, "round-1", "index.html"))
    assert cli.main(["calibrate", "--continue", out, "--profile", prof]) == 2   # no results.csv yet
    _write_votes(os.path.join(out, "round-1"), lambda p: "tie")
    assert cli.main(["calibrate", "--continue", out, "--profile", prof]) == 0
    assert os.path.isfile(os.path.join(out, "round-2", "index.html"))
    assert cli.main(["calibrate", "--continue", out, "--finish", "--profile", prof]) == 2   # round 2 has no votes
    assert cli.main(["calibrate", "--out", out, "--profile", prof]) == 2                    # no photo folder


def test_continue_with_another_profile_is_refused(tmp_path):
    photos, out, _ = _start(tmp_path, count=3)
    _write_votes(os.path.join(out, "round-1"), _lift_up)
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError) as e:
        calibrate.advance(out, profile=str(other))
    assert "--profile" in str(e.value)
    assert json.load(open(os.path.join(out, "state.json")))["round"] == 1
    assert not (other / "looks.toml").exists()
    state = json.load(open(os.path.join(out, "state.json")))
    assert len(state["profile"]) == 12 and str(tmp_path) not in state["profile"]
    # a state file from before the fingerprint existed is accepted
    del state["profile"]
    with open(os.path.join(out, "state.json"), "w") as fh:
        json.dump(state, fh)
    assert calibrate.advance(out, finish=True, profile=str(other))["done"]


def test_a_tampered_state_or_pairs_file_is_refused(tmp_path):
    photos, out, _ = _start(tmp_path, count=3)
    prof = str(tmp_path / "profile")
    _write_votes(os.path.join(out, "round-1"), _lift_up)
    state_path = os.path.join(out, "state.json")
    good = json.load(open(state_path))
    for bad in ("../x.jpg", "/etc/hosts", "a//b.jpg", "a/./b.jpg", "a\\b.jpg", ".."):
        tampered = dict(good, photos=[bad, *good["photos"][1:]])
        with open(state_path, "w") as fh:
            json.dump(tampered, fh)
        with pytest.raises(ValueError) as e:
            calibrate.advance(out, profile=prof)
        assert "inside the photo folder" in str(e.value), bad
    with open(state_path, "w") as fh:
        json.dump(good, fh)
    pairs_path = os.path.join(out, "round-1", "pairs.json")
    meta = json.load(open(pairs_path))
    meta["pairs"][0]["dimension"] = "sparkle"
    with open(pairs_path, "w") as fh:
        json.dump(meta, fh)
    with pytest.raises(ValueError) as e:
        calibrate.advance(out, profile=prof)
    assert "sparkle" in str(e.value)
    assert json.load(open(state_path))["round"] == 1


def test_a_packaged_look_name_is_refused_and_a_replaced_look_is_reported(tmp_path):
    photos = str(tmp_path / "photos")
    _photos(photos)
    prof = str(tmp_path / "profile")
    with pytest.raises(ValueError) as e:
        calibrate.start(photos, str(tmp_path / "c0"), count=3, backend="stub", profile=prof, name="natural")
    assert "choose another name" in str(e.value) and not os.path.exists(str(tmp_path / "c0"))
    replaced = []
    for n in (1, 2):
        out = str(tmp_path / f"c{n}")
        calibrate.start(photos, out, count=3, rounds=1, backend="stub", profile=prof)
        _write_votes(os.path.join(out, "round-1"), _lift_up)
        replaced.append(calibrate.advance(out, profile=prof)["replaced"])
    assert replaced == [False, True]
