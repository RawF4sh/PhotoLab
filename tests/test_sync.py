"""F47 (copy edits to similar photos): the T1 pieces. T2 adds the rest of this file."""
import json
import os

import numpy as np
import pytest
from PIL import Image

from autoedit import adjust, batch, cli, sync
from autoedit.consistency import burst_biases
from autoedit.contract import Adjustment, Result, RunConfig

from test_consistency import _d, _save, _stamp


def _run(tmp_path, extra=()):
    src, out = tmp_path / "in", tmp_path / "out"
    src.mkdir()
    for i, g in enumerate((0.6, 1.0, 1.5)):
        _save(src / f"f{i}.jpg", 0, g, _stamp(3 * i))
    for name, g, stamp in extra:
        _save(src / name, 0, g, stamp)
    batch.run(RunConfig(backend="stub"), str(src), str(out))
    return out


def _lv(ev):    # fg_p50 that gives `ev` stops of level (exposure 0)
    return 2.0 ** (ev / 2.2)


def test_burst_biases_toward_an_anchor():
    ds = [_d(fg_p50=_lv(0.0)),
          _d(fg_p50=_lv(-0.7), exposure=0.3, headroom_ev=2.0),
          _d(fg_p50=_lv(0.4), exposure=0.2),
          _d(fg_p50=_lv(-1.5)),
          _d(fg_p50=_lv(-0.5), exposure=0.1, headroom_ev=0.3)]
    b = burst_biases(ds, 1.0, 0.0, anchor=0)
    assert b[0][0] == 0
    assert abs(b[1][0] - 0.4) < 1e-6
    assert abs(b[2][0] + 0.2) < 1e-6
    assert abs(b[3][0] - 1.0) < 1e-6
    assert b[4][0] <= 0.05 + 1e-6
    assert all(t == 0 and n == 0 for _e, t, n in b)
    # Without an anchor the result is the median's, as before.
    assert burst_biases(ds, 1.0, 4.0) == burst_biases(ds, 1.0, 4.0, anchor=None)


def test_rerender_overrides(tmp_path):
    out = _run(tmp_path)
    f0 = str(out / "f0.jpg")
    adjust.rerender(f0, adjustments=[Adjustment(target="global", ops={"temp": 5.0})],
                    exposure_bias=0.2, batch_group="x", wb=[3.0, -1.0])
    r = Result.model_validate_json((out / "f0.autoedit.json").read_text())
    assert [(a.scope, a.target, a.status) for a in r.adjustments if a.scope == "photo"] == [("photo", "global", "applied")]
    assert r.exposure_bias == 0.2 and r.batch_group == "x"
    assert r.audit["run_config"]["wb"] == [3.0, -1.0] and r.audit["run_config"]["neutral"] is None
    assert r.audit["neutral"]["temp"] == 3.0
    paths = [f0, str(out / "f0.autoedit.json")]
    before = [(open(p, "rb").read(), os.stat(p).st_mtime_ns) for p in paths]
    got = adjust.rerender(f0, apply=False, exposure_bias=0.0)
    assert isinstance(got["result"], Result)
    assert before == [(open(p, "rb").read(), os.stat(p).st_mtime_ns) for p in paths]
    img = open(f0, "rb").read()
    adjust.rerender(f0)
    assert open(f0, "rb").read() == img


def _sc(out, name):
    return str(out / f"{name}.autoedit.json")


def _edit_json(path, fn):
    d = json.loads(open(path).read())
    fn(d)
    open(path, "w").write(json.dumps(d))


def _bytes(out):
    return {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in out.iterdir() if p.is_file()}


def _luma(path):
    return float(np.asarray(Image.open(path).convert("L"), np.float32).mean())


def test_similar(tmp_path):
    out = _run(tmp_path, [("late.jpg", 1.0, _stamp(1800)), ("notime.jpg", 1.0, None)])
    found, left = sync.similar(_sc(out, "f1"), str(out), 10)
    assert sorted(os.path.basename(f) for f in found) == ["f0.autoedit.json", "f2.autoedit.json"]
    assert left == {"more than 10 min apart": 1, "no capture time": 1}
    _edit_json(_sc(out, "f2"), lambda d: d.update(scene="other"))
    found, left = sync.similar(_sc(out, "f1"), str(out), 10)
    assert [os.path.basename(f) for f in found] == ["f0.autoedit.json"] and left["another scene"] == 1
    assert sync.similar(_sc(out, "f1"), str(out), 0)[0] == []
    with pytest.raises(ValueError):
        sync.similar(_sc(out, "notime"), str(out), 10)


def test_copied():
    mk = Adjustment(target="mark:area:0.1000,0.1000,0.4000,0.4000", ops={"exposure": 0.2}, scope="photo")
    r = Result(image="a.jpg", scene="x", confidence=1.0, adjustments=[
        Adjustment(target="global", ops={"temp": 5.0}, scope="photo", status="applied"),
        Adjustment(target="sky", ops={"exposure": -0.3}, scope="photo", status="applied"), mk,
        Adjustment(target="global", ops={"contrast": 1.0}, scope="run"),
        Adjustment(target="global", ops={"contrast": 1.0}, scope="look")])
    keep, marks = sync.copied(r)
    assert [(a.target, a.status) for a in keep] == [("global", ""), ("sky", "")] and marks == 1


def test_sync_end_to_end(tmp_path):
    out = _run(tmp_path)
    adjust.rerender(str(out / "f1.jpg"), sets=["global.temp=8", "sky.exposure=-0.3"])
    before0 = _luma(out / "f0.jpg")
    rep = sync.run(str(out / "f1.jpg"))
    assert [t["status"] for t in rep["targets"]] == ["synced", "synced"]
    for n in ("f0", "f2"):
        r = Result.model_validate_json(open(_sc(out, n)).read())
        assert sorted(a.target for a in r.adjustments if a.scope == "photo") == ["global", "sky"]
        assert r.batch_group == "sync:f1"
    assert Result.model_validate_json(open(_sc(out, "f0")).read()).exposure_bias > 0
    assert abs(_luma(out / "f0.jpg") - _luma(out / "f1.jpg")) < abs(before0 - _luma(out / "f1.jpg"))
    snap = _bytes(out)
    sync.run(str(out / "f1.jpg"))
    assert all(snap[k][0] == v[0] for k, v in _bytes(out).items() if k.endswith(".jpg"))
    adjust.rerender(str(out / "f0.jpg"))
    assert all(snap[k][0] == v[0] for k, v in _bytes(out).items() if k.endswith(".jpg"))
    for p in out.glob("*.autoedit.json"):
        assert str(tmp_path) not in p.read_text()


def test_dry_run_changes_nothing(tmp_path):
    out = _run(tmp_path)
    snap = _bytes(out)
    rep = sync.run(str(out / "f1.jpg"), dry_run=True)
    assert [t["status"] for t in rep["targets"]] == ["would sync"] * 2
    assert snap == _bytes(out)


def test_white_balance_of_the_pick_is_copied(tmp_path):
    out = _run(tmp_path)
    adjust.rerender(str(out / "f1.jpg"), wb=[3.0, -1.0])
    sync.run(str(out / "f1.jpg"))
    for n in ("f0", "f2"):
        rc = Result.model_validate_json(open(_sc(out, n)).read()).audit["run_config"]
        assert rc["wb"] == [3.0, -1.0] and rc["neutral"] is None


def test_failures_are_isolated_and_cli(tmp_path):
    out = _run(tmp_path)
    f1 = str(out / "f1.jpg")
    _edit_json(_sc(out, "f2"), lambda d: d["audit"]["run_config"].update(decider="llm"))
    rep = sync.run(f1)
    st = {os.path.basename(t["sidecar"]): t for t in rep["targets"]}
    assert st["f2.autoedit.json"]["status"] == "failed" and "LLM" in st["f2.autoedit.json"]["error"]
    assert st["f0.autoedit.json"]["status"] == "synced"
    assert cli.main(["sync", f1]) == 1
    assert cli.main(["sync", f1, "--dry-run"]) == 0
    assert cli.main(["sync", str(tmp_path / "nope.jpg")]) == 2
    from autoedit import paths
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    for k, v in paths.config_paths().items():
        (cfg / os.path.basename(v)).write_text(open(v).read())
    rp = cfg / "rules.toml"
    rp.write_text(rp.read_text().replace("max_ev = 1.0\n", "max_ev = 9\n", 1) if "[sync]" in rp.read_text() else "")
    assert "max_ev = 9" in rp.read_text()
    assert cli.main(["sync", f1, "--configs-dir", str(cfg)]) == 2
