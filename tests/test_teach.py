"""F29: teaching mode. Tests 1 to 9 (T1): the global pass one step at a time, the steps of a preview, the collector,
the checking of `steps`. Tests 11 to 18 (T2): try first, the compare, the checks of `try`. Stub backend, so no GPU or network."""

import os
from dataclasses import asdict
from unittest import mock

import numpy as np
import pytest
from PIL import Image as PILImage

from autoedit import cli, lessons, looks, lut, paths, teach
from autoedit.edit import banding
from autoedit.imageio import Image, downscale_long_edge
from autoedit.edit import apply_image, ops, par
from autoedit.pipeline import Pipeline
from autoedit.web import service

EXACT = {"px": 2560, "lossless": True}
FOUR = ("name", "does", "when", "watch")
TYPED = "brighten the sky, warm the photo, darken the top"


def _photo(tmp_path, flat=False, name="p.png", noise=3):
    """Sky, foliage and a skin-coloured oval, 256 px. `flat`: no noise, so the banding gate renders again."""
    a = np.zeros((256, 256, 3), np.int16)
    a[:140] = [100, 140, 200]
    a[140:] = [60, 110, 70]
    yy, xx = np.mgrid[0:256, 0:256]
    a[(((xx - 128) / 56) ** 2 + ((yy - 160) / 72) ** 2) <= 1.0] = [200, 150, 120]
    if not flat:
        a += np.random.default_rng(7).integers(-noise, noise + 1, a.shape, dtype=np.int16)
    path = tmp_path / name
    PILImage.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB").save(str(path))
    return str(path)


def _bare(answer):
    """An answer without what differs between two runs of one request: its timings."""
    r = dict(answer["result"])
    for k in ("timing_ms", "stage_timings_ms"):
        r.pop(k, None)
    return {**answer, "result": r}


def _flat_ops(bundle):
    return [k for step in ops.STEPS for k in step if bundle.get(k) and k not in ("lut", "grain")]


class _Threads:
    def __init__(self, threads):
        self.threads = threads

    def __enter__(self):
        self.old = (par._threads, par.MIN_PIXELS)
        par.MIN_PIXELS = 0
        par.set_threads(self.threads)

    def __exit__(self, *exc):
        par.MIN_PIXELS = self.old[1]
        par.set_threads(self.old[0])


# ---- 1 ---------------------------------------------------------------------------------------

BUNDLE19 = {"temp": 12.0, "exposure": 0.4, "highlight_rolloff": 30.0, "shadows_ev": 0.5, "highlights": -20.0,
            "contrast": 15.0, "curve": 20.0, "dehaze": 15.0, "clarity": 25.0, "whites": 10.0, "vibrance": 20.0,
            "saturation": 10.0, "hsl_skin_sat": 10.0, "grade_shadows_temp": 10.0, "denoise": 40.0, "sharpen": 30.0,
            "bw": 40.0, "lut": 50.0, "grain": 30.0}
BUNDLES = [BUNDLE19, {"saturation": 12.0}, {"clarity": 0, "grain": 20.0}]
GUARDS = [None, {"saturation"}, {"clarity", "vibrance", "grade_shadows_temp"}, {"temp", "bw", "sharpen"},
          {"hsl_blue_sat"}]          # the last names an op that no bundle has a value for


def _guarded_pass(x, bundle, guard_ops, mask):
    calls = []
    plain = apply_image(x, [], dict(bundle), guard_mask=mask if guard_ops else None, guard_ops=guard_ops)
    seen = apply_image(x, [], dict(bundle), guard_mask=mask if guard_ops else None, guard_ops=guard_ops,
                       on_step=lambda keys, pic: calls.append((keys, pic)))
    assert np.array_equal(plain, seen), (bundle, guard_ops)
    assert [k for keys, _ in calls for k in keys] == _flat_ops(bundle), (bundle, guard_ops)
    if calls:
        assert np.array_equal(np.clip(calls[-1][1], 0.0, 1.0), plain)
    else:
        assert np.array_equal(np.clip(x, 0.0, 1.0), plain)


def test_stepwise_global_pass_is_bit_identical():
    rng = np.random.default_rng(3)
    x = rng.random((96, 96, 3), dtype=np.float32)
    mask = rng.random((96, 96), dtype=np.float32)
    with _Threads(1):
        for bundle in BUNDLES:
            for guard_ops in GUARDS:
                _guarded_pass(x, bundle, guard_ops, mask)
    calls = []      # an op that finds nothing to do (a white point on a frame that has no bright tail) is no step
    apply_image(x, [], {"white_point": 40.0, "exposure": 0.2}, on_step=lambda keys, pic: calls.append(keys))
    assert calls == [("exposure",)]
    big = np.random.default_rng(4).random((1301, 1203, 3), dtype=np.float32)
    big_mask = np.random.default_rng(5).random((1301, 1203), dtype=np.float32)
    with _Threads(4):
        for guard_ops in (None, {"clarity", "vibrance", "grade_shadows_temp"}):
            _guarded_pass(big, BUNDLE19, guard_ops, big_mask)


# ---- 2, 3 ------------------------------------------------------------------------------------

OPTION_SETS = [
    {}, {"style": "dramatic"}, {"grain": 40}, {"photo_edit": TYPED}, {"look": "bw_classic"},
    {"prompt": "cool blue moody mountains", "palette": "cool_blue"}, {"concepts": []}, {"intensity": 0},
]


def _opts(**extra):
    return {"backend": "stub", **extra}


def test_steps_end_at_the_preview(tmp_path):
    p = _photo(tmp_path)
    for extra in OPTION_SETS:
        plain = service.preview(p, _opts(**extra))
        exact = service.preview(p, _opts(**extra, steps=EXACT))
        assert exact["before"] == plain["before"] and exact["after"] == plain["after"], extra
        assert exact["steps"][0]["after"] == plain["before"] and exact["steps"][-1]["after"] == plain["after"], extra
        assert _bare({k: v for k, v in exact.items() if k != "steps"}) == _bare(plain), extra
        cheap = service.preview(p, _opts(**extra, steps=True))
        assert cheap["after"] == plain["after"]
        assert all(s["after"].startswith("data:image/jpeg;base64,") for s in cheap["steps"])
        assert [(s["kind"], s["title"]) for s in cheap["steps"]] == [(s["kind"], s["title"]) for s in exact["steps"]]


def test_a_plain_preview_takes_no_new_path(tmp_path):
    p = _photo(tmp_path)
    calls = []
    real = Pipeline.process_one

    def spy(self, *a, **kw):
        calls.append(kw)
        return real(self, *a, **kw)
    with mock.patch.object(Pipeline, "process_one", spy):
        answer = service.preview(p, _opts())
    (kw,) = calls
    assert kw.get("on_stage") is None and kw.get("on_step") is None
    assert not {"steps", "try", "compare"} & set(answer)
    assert not {"steps", "try", "compare"} & set(service.preview(p, _opts(steps=False)))


# ---- 4 --------------------------------------------------------------------------------------

ORDER = ["source", "regions", "shapes", "tools", "palette", "checks", "finish", "grain"]


def test_steps_follow_the_editing_order(tmp_path):
    p = _photo(tmp_path)
    answer = service.preview(p, _opts(photo_edit=TYPED, steps=True))
    steps, result, walk = answer["steps"], answer["result"], answer["walkthrough"]
    kinds = [s["kind"] for s in steps]
    assert kinds[0] == "source" and kinds.count("checks") == 1 and kinds[-1] == "checks"
    assert [ORDER.index(k) for k in kinds] == sorted(ORDER.index(k) for k in kinds)
    shown = [t["op"] for s in steps if s["kind"] == "tools" for t in s["tools"]]
    gs = result["global_settings"]
    assert shown == _flat_ops(gs) and shown
    for s in steps:
        for t in s["tools"]:
            assert t["value"] == round(gs[t["op"]], 4) and all(t.get(k) for k in FOUR), t
        if s["kind"] not in ("tools", "grain"):
            assert all(s.get(k) for k in ("does", "when", "watch")), s["kind"]
    regions = next(s for s in steps if s["kind"] == "regions")
    assert [(w["target"], w["coverage"]) for w in regions["where"]] == \
        [(m["target"], m["coverage"]) for m in result["masks"] if any(m["ops"].values())]
    shapes = next(s for s in steps if s["kind"] == "shapes")
    assert "instruction:top" in [w["target"] for w in shapes["where"]]
    label = {e["label"]: i for i, e in enumerate(walk)}
    assert label["the sky"] in regions["typed"] and label["the top"] in shapes["typed"]
    whole = [s for s in steps if label["the whole photo"] in s["typed"]]
    assert len(whole) == 1 and whole[0]["kind"] == "tools"
    guard_ops = service.build_pipeline(service.make_cfg(_opts())).guard_ops
    for s in steps:
        has = any(n.startswith(teach.GUARD_NOTE) for n in s["notes"])
        assert not has or (s["kind"] == "tools" and {t["op"] for t in s["tools"]} & guard_ops), s["kind"]
    if any(n.startswith(teach.GUARD_NOTE) for n in result["notes"]):
        assert any(n.startswith(teach.GUARD_NOTE) for s in steps for n in s["notes"])


# ---- 5, 6 ------------------------------------------------------------------------------------

def test_a_second_render_replaces_the_first(tmp_path):
    p = _photo(tmp_path, flat=True)
    opts = _opts(style="dramatic")
    plain = service.preview(p, opts)
    assert any(n.startswith("banding gate:") for n in plain["result"]["notes"])
    answer = service.preview(p, {**opts, "steps": EXACT})
    kinds = [s["kind"] for s in answer["steps"]]
    assert kinds.count("source") == 1 and kinds.count("regions") <= 1 and kinds.count("checks") == 1
    gs = answer["result"]["global_settings"]
    for s in answer["steps"]:
        for t in s["tools"]:
            assert t["value"] == round(gs[t["op"]], 4)
    assert answer["steps"][-1]["after"] == plain["after"]
    checks = next(s for s in answer["steps"] if s["kind"] == "checks")
    assert any(n.startswith("banding gate:") for n in checks["notes"])


def test_grain_and_a_lut_are_steps(tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    p = _photo(folder)
    cube = str(folder / "warm.cube")
    lut.write_cube(cube, np.clip(lut.identity(5).astype(np.float64) ** 0.7, 0, 1).astype(np.float32), "t")
    os.environ.pop("AUTOEDIT_ALLOW_ANY_PATH", None)
    service._ALLOWED_ROOTS.clear()
    try:
        service.approve_root(str(folder))
        opts = _opts(lut=cube, grain=40)
        plain = service.preview(p, opts)
        answer = service.preview(p, {**opts, "steps": EXACT})
    finally:
        service._ALLOWED_ROOTS.clear()
    steps = answer["steps"]
    assert [s["kind"] for s in steps][-2:] == ["finish", "grain"]
    finish, grain = steps[-2:]
    assert [t["op"] for t in finish["tools"]] == ["lut"] and any(n.startswith("lut:") for n in finish["notes"])
    assert [(t["op"], t["value"]) for t in grain["tools"]] == [("grain", 40.0)] and grain["moved"] is None
    assert grain["after"] != finish["after"] and steps[-1]["after"] == plain["after"]
    bw = service.preview(p, _opts(look="bw_classic", steps=EXACT))
    assert "finish" not in [s["kind"] for s in bw["steps"]]
    assert bw["steps"][-1]["after"] == service.preview(p, _opts(look="bw_classic"))["after"]


# ---- 7 --------------------------------------------------------------------------------------

class _Result:
    def __init__(self, **kw):
        self.global_settings, self.masks, self.composition, self.notes = {"exposure": 0.5}, [], [], []
        self.__dict__.update(kw)


def test_the_collector_keeps_the_last_picture():
    base = np.full((8, 8, 3), 0.4, np.float32)
    s = teach.Steps(lambda rgb, grain=None: float(rgb.mean()))
    s.stage("candidate_decode", base)
    for lift in (0.1, 0.2):         # the second render replaces the first
        s.stage("after_regions", base.copy())                           # equal values after the source
        s.stage("after_composition", base + 0.25 / 255)                 # a quarter of a level
        tool = base + lift
        s.step(("exposure",), tool)                                     # a tool
        s.step((), tool + 1e-6)                                         # a blend without ops: no step
        s.stage("after_palette", tool)
        s.stage("after_chroma_guard", tool)
    s.stage("final_float", base + 0.2 + 0.25 / 255)                     # under half a level
    les = {"op": {"exposure": {k: k for k in FOUR}}}
    steps = s.done(_Result(), les)
    assert [x["kind"] for x in steps] == ["source", "tools", "checks"]
    assert [t["op"] for t in steps[1]["tools"]] == ["exposure"] and steps[1]["tools"][0]["value"] == 0.5
    assert steps[1]["moved"] == pytest.approx(0.2 * 255 - 0.25, abs=1e-3)   # from the picture before it
    assert steps[2]["after"] == pytest.approx(0.6 + 0.25 / 255) and steps[2]["moved"] == 0.0
    assert steps[0]["after"] == pytest.approx(0.4)
    steps = s.done(_Result(), les, grained=0.9)
    assert [x["kind"] for x in steps][-1] == "grain" and steps[-1]["after"] == 0.9 and steps[-1]["moved"] is None


# ---- 8, 9 -----------------------------------------------------------------------------------

def test_step_notes_are_the_pipelines_own_words():
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "..", "src", "autoedit", "pipeline.py"), encoding="utf-8") as f:
        source = f.read()
    for prefix in [*(p for ps in teach.NOTES.values() for p in ps), teach.GUARD_NOTE]:
        assert prefix in source, prefix


def test_steps_are_checked(tmp_path):
    for bad in (5, "yes", {"px": 100}, {"px": 99999}, {"px": 1280.5}, {"px": True}, {"px": "1280"},
                {"other": 1}, {"lossless": 1}, {"px": 1280, "extra": 0}, [], 0.0):
        with pytest.raises(ValueError):
            teach.check_steps(bad)
    assert teach.check_steps(None) == (0, False) and teach.check_steps(False) == (0, False)
    assert teach.check_steps(True) == (1280, False) and teach.check_steps({}) == (1280, False)
    assert teach.check_steps({"px": 256, "lossless": True}) == (256, True)
    assert teach.check_steps({"px": 2560}) == (2560, False)
    p = _photo(tmp_path)
    for bad in (5, "yes", {"px": 100}):
        with pytest.raises(ValueError):
            service.preview(p, _opts(steps=bad))
    assert service.preview(p, _opts(steps={}))["steps"]          # {} asks for steps with the defaults


# ---- 11 to 18 (T2) ---------------------------------------------------------------------------

def _les():
    return lessons.load(paths.config_path("lessons.toml", packaged_fallback=True))


def _table():
    return teach.sliders(_les())


def _ops_of(entries):
    return [(m["target"], {k: v for k, v in m["ops"].items() if v}) for m in entries]


def test_an_untouched_try_is_the_source(tmp_path):
    p = _photo(tmp_path)
    busy = _opts(style="dramatic", look="bw_classic", prompt="cool blue moody mountains", edit="brighten the sky",
                 palette="cool_blue", grain=30, decider="llm", allow_cloud=True)
    answer = service.preview(p, {**busy, "try": {}})
    assert answer["after"] == answer["before"]
    r = answer["result"]
    assert not r["global_settings"] and not r["masks"] and not r["composition"] and not r["adjustments"]
    assert r["decider"] == "none" and r["model"] is None and "compare" not in answer
    assert [s["op"] for s in answer["try"]["sliders"]] == list(_table()) and all(
        s["value"] == 0.0 for s in answer["try"]["sliders"])
    prof = str(tmp_path / "prof")
    natural = looks.load(os.path.join(paths.configs_dir(), "looks.toml"))["natural"]
    cli.write_look(prof, "mine", asdict(natural), habit={"people": {"sky": {"exposure": -0.3}}})
    plain = service.preview(p, _opts(look="mine"), profile=prof)
    tried = service.preview(p, _opts(look="mine", **{"try": {}}), profile=prof)
    assert plain["result"]["adjustments"] and not tried["result"]["adjustments"]
    assert tried["after"] == tried["before"]


def test_a_try_applies_what_the_user_set_and_nothing_else(tmp_path):
    p = _photo(tmp_path)
    opts = _opts(concepts=["sky"], photo_edit="brighten the sky, darken the top", style="dramatic", grain=30)
    answer = service.preview(p, {**opts, "try": {"sliders": {"exposure": 0.5, "contrast": 20, "saturation": 0}},
                                 "steps": EXACT})
    r = answer["result"]
    assert r["global_settings"] == {"exposure": 0.5, "contrast": 20.0}
    assert _ops_of(r["masks"]) == [("sky", {"exposure": 0.3})]
    assert _ops_of(r["composition"]) == [("instruction:top", {"exposure": -0.3})]
    first = r["adjustments"][0]
    assert first["target"] == "global" and first["source"] == "" and first["status"] == "applied"
    assert [s["kind"] for s in answer["steps"]] == ["source", "regions", "shapes", "tools", "tools", "checks"]
    tools = [t["op"] for s in answer["steps"] if s["kind"] == "tools" for t in s["tools"]]
    assert tools == ["exposure", "contrast"] and answer["steps"][-1]["after"] == answer["after"]


def test_the_compare_table_matches_both_sidecars(tmp_path):
    p = _photo(tmp_path)
    opts = _opts()
    plain = service.preview(p, opts)
    answer = service.preview(p, {**opts, "photo_edit": "brighten the sky, darken the top",
                                 "try": {"sliders": {"exposure": 0.5, "contrast": 20}, "compare": True}})
    c, mine, eng = answer["compare"], answer["result"], plain["result"]
    assert c["engine"] == plain["after"]

    def values(r):
        out = {("global", k): round(v, 4) for k, v in r["global_settings"].items() if v}
        for kind in ("masks", "composition"):
            out.update({(m["target"], k): round(v, 4) for m in r[kind] for k, v in m["ops"].items() if v})
        return out
    want_e, want_y = values(eng), values(mine)
    assert {(t["where"], t["op"]): t["engine"] for t in c["tools"] if t["engine"]} == want_e
    assert {(t["where"], t["op"]): t["yours"] for t in c["tools"] if t["yours"]} == want_y
    assert {t["op"]: t["set"] for t in c["tools"] if t["where"] == "global" and t["set"] is not None} == \
        {"exposure": 0.5, "contrast": 20.0}
    assert all(t["set"] is None for t in c["tools"] if t["where"] != "global")
    glob = [t["op"] for t in c["tools"] if t["where"] == "global"]
    flat = [k for st in ops.STEPS for k in st]
    assert glob == sorted(glob, key=flat.index) and c["tools"][0]["where"] == "global"
    assert c["said"]["engine"] == {"quality": eng["quality_score"], "needs_review": eng["needs_review"],
                                   "warnings": eng["warnings"], "notes": c["said"]["engine"]["notes"]}


def test_the_facts_are_what_the_checks_measure(tmp_path):
    p = _photo(tmp_path, noise=20)
    plain = service.preview(p, _opts())
    answer = service.preview(p, _opts(**{"try": {"sliders": {"exposure": 2.0, "saturation": 50}, "compare": True}}))
    rows = {(r["key"], r["where"]): r for r in answer["compare"]["checks"]}
    s, y, e = plain["result"]["global_stats"], answer["result"]["post_edit_stats"], plain["result"]["post_edit_stats"]
    blown = rows[("blown", "frame")]
    assert (blown["source"], blown["yours"], blown["engine"]) == (s["highlight_clip"], y["highlight_clip"], e["highlight_clip"])
    assert blown["flag"] and blown["line"].startswith("Your version clips")
    post = {r["name"]: r for r in answer["result"]["post_region_stats"]}
    skin = rows[("blown", "skin")]
    assert skin["yours"] == post["skin"]["clip_high"] and skin["flag"]
    assert skin["line"] == (f"Your version clips {lessons._pct(skin['yours'])} of the skin. "
                            f"The source: 0.0 %. AutoEdit: {lessons._pct(skin['engine'])}.")
    assert ("blown", "sky") not in rows
    ch = rows[("channel", "frame")]
    assert (ch["yours"], ch["engine"]) == (y["rgb_highlight_clip_any"], e["rgb_highlight_clip_any"])
    assert rows[("colour", "skin")]["yours"] > rows[("colour", "skin")]["engine"]
    assert all(all(r.get(k) for k in FOUR) for r in rows.values())
    assert answer["result"]["needs_review"]
    calm = service.preview(p, _opts(**{"try": {"compare": True}}))["compare"]["checks"]
    assert [r["key"] for r in calm if r["flag"]] in ([], ["banding"]) and not [r for r in calm if r["flag"] and r["key"] != "banding"]
    assert [r["yours"] for r in calm if r["key"] == "colour"] == [0.0]


def test_measure_reads_the_banding_gates_own_score(tmp_path):
    p = _photo(tmp_path, flat=True)
    r = service._edit(p, _opts(), None, None)
    m = teach.measure(r["work"].rgb, r["out"], r["proxy"] or r["work"], r["union"], r["pipe"])
    small = lambda a: downscale_long_edge(Image(rgb=a), 1280).rgb  # noqa: E731
    score, action = banding.assess(small(r["work"].rgb), small(r["out"]), r["pipe"].banding)[:2]
    assert (m["banding"], m["banding_action"]) == (float(score), action)
    assert set(m["colour"]) == {"skin"}
    assert teach.measure(r["work"].rgb, r["work"].rgb, r["proxy"] or r["work"], r["union"], r["pipe"])["colour"] == {"skin": 0.0}


def test_a_try_is_checked():
    table = _table()
    assert list(table) == ["exposure", "contrast", "highlights", "shadows", "temp", "tint", "saturation"]
    assert table["exposure"] == (-2.0, 2.0, 0.05) and table["saturation"] == (-50.0, 50.0, 1.0)
    for bad in ("x", [], {"other": 1}, {"sliders": []}, {"compare": 1}, {"sliders": {"clarity": 5}},
                {"sliders": {"exposure": 9}}, {"sliders": {"exposure": float("nan")}}, {"sliders": {"exposure": True}},
                {"sliders": {"exposure": "1"}}, {"sliders": {"exposure": 10 ** 400}}):
        with pytest.raises(ValueError):
            teach.check_try(bad, table)
    assert teach.check_try({"sliders": {"exposure": 0, "tint": 3}}, table) == {"sliders": {"tint": 3.0}, "compare": False}
    assert teach.check_try({}, table) == {"sliders": {}, "compare": False}
    with pytest.raises(ValueError):
        teach.check_try({}, {})
    ok = [-1.0, 1.0, 0.1]
    for bad in ({"nope": ok}, {"exposure": [-9.0, 1.0, 0.1]}, {"exposure": [-1.0, 1.0]}, {"exposure": [1.0, -1.0, 0.1]},
                {"exposure": [-1.0, 1.0, 0]}, {"exposure": [-1.0, float("inf"), 0.1]}, {"exposure": "x"},
                {"exposure": [True, 1.0, 0.1]}):
        with pytest.raises(ValueError):
            teach.sliders({"try": {"sliders": bad}})
    assert teach.sliders({"try": {"sliders": {"exposure": [-1, 1, 1]}}}) == {"exposure": (-1.0, 1.0, 1.0)}


def test_a_try_never_reaches_an_export(tmp_path):
    p = _photo(tmp_path)
    plain = service.run_one(p, str(tmp_path / "a"), _opts())
    kept = service.run_one(p, str(tmp_path / "b"), _opts(**{"try": {"sliders": {"exposure": 1.5}}, "steps": EXACT}))
    with open(plain["image_out"], "rb") as a, open(kept["image_out"], "rb") as b:
        assert a.read() == b.read()
    assert plain["result"]["global_settings"] == kept["result"]["global_settings"]


def test_api_takes_steps_and_try(tmp_path):
    try:
        from fastapi.testclient import TestClient
    except Exception:
        return
    from autoedit.web import app as webapp
    client = TestClient(webapp.create_app(paths.configs_dir(), local_only=True), base_url="http://localhost")
    p = _photo(tmp_path)
    service._ALLOWED_ROOTS.clear()
    os.environ.pop("AUTOEDIT_ALLOW_ANY_PATH", None)
    service.approve_root(str(tmp_path))
    try:
        def post(**extra):
            return client.post("/api/v1/preview", json={"backend": "stub", "file": p, **extra})
        plain, walk = post(), post(steps=EXACT)
        assert plain.status_code == 200 and walk.status_code == 200
        assert walk.json()["steps"][-1]["after"] == plain.json()["after"]
        tried = post(**{"try": {"sliders": {"exposure": 1.0}, "compare": True}})
        assert tried.status_code == 200 and tried.json()["compare"]["engine"] == plain.json()["after"]
        for bad in ({"steps": 5}, {"try": "x"}, {"try": {"sliders": {"exposure": 99}}}):
            r = post(**bad)
            assert r.status_code == 400 and r.json()["code"] == "bad_request", (bad, r.text)
    finally:
        service._ALLOWED_ROOTS.clear()
