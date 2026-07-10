"""Blind-test harness: renders 3 variants/photo, shuffles, and scores picks against the manifest."""

import csv
import importlib.util
import pathlib

import numpy as np
from PIL import Image as PILImage

_BT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "blindtest.py"


def _load_blindtest():
    spec = importlib.util.spec_from_file_location("blindtest", _BT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make(path, tint):
    a = np.full((96, 128, 3), 40, np.uint8)
    a[:48] = [90, 130, 190]
    a[48:] = tint
    PILImage.fromarray(a, "RGB").save(path, quality=92)


def test_render_then_score(tmp_path):
    bt = _load_blindtest()
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _make(str(corpus / "a.jpg"), [70, 120, 80])
    _make(str(corpus / "b.jpg"), [150, 120, 90])

    out = tmp_path / "bt"
    cfg = bt.make_config(backend="stub")
    summary = bt.render(str(corpus), str(out), cfg, seed=1, max_px=160, quality=85)
    assert summary["photos"] == 2

    # three variants per photo on disk
    for pid in ("photo001", "photo002"):
        for slot in range(3):
            assert (out / pid / f"slot{slot}.jpg").exists()

    # manifest: 2 photos × 3 slots, every method present once per photo
    rows = list(csv.DictReader(open(out / "manifest.csv")))
    assert len(rows) == 6
    by_photo: dict = {}
    for r in rows:
        by_photo.setdefault(r["photo"], {})[r["method"]] = int(r["slot"])
    assert all(set(m) == {"unedited", "baseline", "autoedit"} for m in by_photo.values())

    # self-contained gallery written
    html = (out / "index.html").read_text()
    assert "photo001" in html and "AutoEdit" in html and len(html) > 1000

    # simulate a human who always picks AutoEdit → it should win the gate 100%
    with open(out / "results.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["photo", "best_slot"])
        for pid, methods in by_photo.items():
            w.writerow([pid, methods["autoedit"]])

    res = bt.score(str(out / "results.csv"))
    assert res["total"] == 2
    assert res["wins"]["autoedit"] == 2
    assert res["head_to_head"] == 100.0
    assert 0.0 < res["p_value"] <= 1.0                    # exact binomial reported


def test_rules_ab_mode(tmp_path):
    """Config-A-vs-config-B gate: two rules files differing in one constant must produce two
    differing variants per photo, manifest methods rules_a/rules_b, and a scoreable gallery."""
    import numpy as np
    from PIL import Image as PILImage

    bt = _load_blindtest()
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _make(str(corpus / "a.jpg"), [70, 120, 80])

    rules_src = (pathlib.Path(__file__).resolve().parent.parent
                 / "src" / "autoedit" / "configs" / "rules.toml").read_text()
    ra = tmp_path / "rules_a.toml"; ra.write_text(rules_src)
    rb = tmp_path / "rules_b.toml"; rb.write_text(rules_src.replace('vibrance   = "4"',
                                                                    'vibrance   = "90"'))
    assert ra.read_text() != rb.read_text()

    from autoedit.pipeline import Pipeline
    from autoedit.segment import CachingSegmenter

    cfg = bt.make_config(backend="stub")
    cfg_a = cfg.model_copy(update={"rules_path": str(ra)})
    cfg_b = cfg.model_copy(update={"rules_path": str(rb)})
    pipe_a, pipe_b = Pipeline.build(cfg_a), Pipeline.build(cfg_b)
    pipe_a.segmenter = CachingSegmenter(pipe_a.segmenter)
    pipe_b.segmenter = pipe_a.segmenter

    out = tmp_path / "ab"
    bt.render(str(corpus), str(out), cfg, seed=3, max_px=160,
              methods=("rules_a", "rules_b"), pipelines={"rules_a": pipe_a, "rules_b": pipe_b})

    rows = list(csv.DictReader(open(out / "manifest.csv")))
    assert {r["method"] for r in rows} == {"rules_a", "rules_b"}
    s0 = np.asarray(PILImage.open(out / "photo001" / "slot0.jpg"), np.float32)
    s1 = np.asarray(PILImage.open(out / "photo001" / "slot1.jpg"), np.float32)
    assert np.abs(s0 - s1).mean() > 1.0                   # the contrast delta is visible

    with open(out / "results.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["photo", "best_slot"]); w.writerow(["photo001", 0])
    res = bt.score(str(out / "results.csv"))
    assert set(res["pair"]) == {"rules_a", "rules_b"}
    assert res["total"] == 1 and res["p_value"] == 1.0    # n=1 can never be significant
