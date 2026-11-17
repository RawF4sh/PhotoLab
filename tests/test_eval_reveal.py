"""eval_reveal: the scoring and the labelling page (no model, no network)."""

import importlib.util
import pathlib

import numpy as np
import pytest
from PIL import Image as PILImage

from autoedit import reveal
from autoedit.paths import config_path

_SRC = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "eval_reveal.py"


def _mod():
    spec = importlib.util.spec_from_file_location("eval_reveal", _SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _cfg():
    return reveal.load(config_path("reveal.toml", packaged_fallback=True))


def _f(kind, score, word=None):
    cfg = _cfg()
    w = word or next(k for k in cfg.kinds if k.name == kind).words[0]
    return {"kind": kind, "word": w, "score": score, "box": [0, 0, 1, 1]}


def _set(kind, n_with, hits, n_without=10, fa=0, score=0.9, word=None, tiles=None):
    """labels and results for one kind: n_with photos with it (hits found), n_without without."""
    labels, results = {}, {}
    for i in range(n_with):
        labels[f"w{i}.jpg"] = {"seen": 1, kind: 1}
        e = {"proxy": [_f(kind, score, word)] if i < hits else [], "proxy_s": 1.0}
        if tiles is not None:
            e["tiles"] = [_f(kind, score, word)] if i < tiles else []
            e["tiles_s"] = 2.0
        results[f"w{i}.jpg"] = e
    for i in range(n_without):
        labels[f"n{i}.jpg"] = {"seen": 1, kind: 0}
        e = {"proxy": [_f(kind, 0.6)] if i < fa else [], "proxy_s": 1.0}
        if tiles is not None:
            e["tiles"] = list(e["proxy"])
            e["tiles_s"] = 2.0
        results[f"n{i}.jpg"] = e
    return labels, results


def test_score_picks_the_lowest_passing_threshold():
    # 3 of 10 photos without the thing score 0.5, 2 of them also 0.55: false alarms drop to 1 at 0.55
    labels, results = _set("screen", 10, 10, fa=0)
    for i, s in enumerate([0.5, 0.5, 0.6]):
        results[f"n{i}.jpg"]["proxy"] = [_f("screen", s)]
    k = _mod().score(labels, results, _cfg())["kinds"]["screen"]
    assert k["min_score"] == 0.55
    assert k["proxy"]["hits"] == 10 and k["proxy"]["false_alarms"] == 1


def test_score_verdicts():
    m, cfg = _mod(), _cfg()
    labels, results = _set("screen", 10, 10, fa=0)
    for i in range(10):
        results[f"n{i}.jpg"]["proxy"] = [_f("screen", 0.95)]       # never passes
    assert m.score(labels, results, cfg)["kinds"]["screen"]["verdict"] == "leave off the default list"
    assert m.score(*_set("plate", 10, 8), cfg)["kinds"]["plate"]["verdict"] == "STOP: needs a second detector"
    assert m.score(*_set("screen", 10, 6), cfg)["kinds"]["screen"]["verdict"] == "weak"
    assert m.score(*_set("screen", 10, 9), cfg)["kinds"]["screen"]["verdict"] == "ok"
    assert m.score(*_set("screen", 0, 0), cfg)["kinds"]["screen"]["verdict"] == "no photos with it: not measured"


def test_score_tiles_gain_and_build_line():
    m, cfg = _mod(), _cfg()
    with_tiles = m.score(*_set("screen", 10, 5, tiles=8), cfg)
    assert with_tiles["kinds"]["screen"]["tiles_gain"] == pytest.approx(0.3)
    assert "Build tiles: yes" in m.render(with_tiles)
    plain = m.score(*_set("screen", 10, 5), cfg)
    assert "tiles_gain" not in plain["kinds"]["screen"]
    assert "Build tiles: no" in m.render(plain)


def test_score_words_that_add_nothing():
    m, cfg = _mod(), _cfg()
    labels, results = _set("screen", 10, 10, word="computer screen")
    for i in range(10):                      # "television" fires only together with "computer screen"
        results[f"w{i}.jpg"]["proxy"].append(_f("screen", 0.9, "television"))
    k = m.score(labels, results, cfg)["kinds"]["screen"]
    assert k["words"]["computer screen"] == 0 and k["words"]["television"] == 0
    labels, results = _set("screen", 10, 10, word="computer screen")
    k = m.score(labels, results, cfg)["kinds"]["screen"]
    assert k["words"]["computer screen"] == 10
    assert k["useless_words"] == ["phone screen", "television"]


def test_label_page(tmp_path):
    m, cfg = _mod(), _cfg()
    photos = tmp_path / "photos"
    photos.mkdir()
    for name in ("a<b>&.jpg", "b.jpg", "c.jpg"):
        PILImage.fromarray(np.full((20, 30, 3), 120, np.uint8)).save(photos / name)
    assert m.main(["label", str(photos)]) == 0
    out = tmp_path / "photos-reveal-eval"
    page = (out / "label.html").read_text(encoding="utf-8")
    assert page.count('value="seen"') == 3
    for k in cfg.kinds:
        assert page.count(f'value="{k.name}"') == 3
    assert "a&lt;b&gt;&amp;.jpg" in page and "a<b>" not in page
    assert "http" not in page and str(tmp_path) not in page
    assert len(list((out / "thumbs").glob("*.jpg"))) == 3
    assert not (photos / "label.html").exists()


def test_run_refuses_without_sam3(tmp_path):
    m = _mod()
    old = m.sam3_ready
    m.sam3_ready = lambda: False
    try:
        assert m.main(["run", str(tmp_path), "--labels", str(tmp_path / "labels.csv")]) == 2
    finally:
        m.sam3_ready = old


def test_only_seen_rows_count():
    m, cfg = _mod(), _cfg()
    labels, results = _set("screen", 10, 10)
    labels["x.jpg"] = {"seen": 0, "screen": 1}
    results["x.jpg"] = {"proxy": [], "proxy_s": 1.0}
    s = m.score(labels, results, cfg)
    assert s["used"] == 20 and s["kinds"]["screen"]["with"] == 10


def test_each_measurement_has_its_own_threshold():
    # two photos without the thing raise a false alarm at 0.8, but only in the tiles: the proxy
    # keeps its low threshold and its hits, and decides
    m, cfg = _mod(), _cfg()
    labels, results = _set("screen", 10, 10, score=0.6, tiles=10)
    for i in range(2):
        results[f"n{i}.jpg"]["tiles"] = [_f("screen", 0.8)]
    k = m.score(labels, results, cfg)["kinds"]["screen"]
    assert k["proxy"]["min_score"] == 0.4 and k["proxy"]["hits"] == 10
    assert k["tiles"]["min_score"] == 0.85 and k["tiles"]["hits"] == 0
    assert k["best"] == "proxy" and k["min_score"] == 0.4 and k["verdict"] == "ok"
    assert k["tiles_gain"] == pytest.approx(-1.0)


def test_label_says_when_the_folder_is_missing_or_empty(tmp_path):
    m = _mod()
    assert m.main(["label", str(tmp_path / "nope")]) == 2
    assert not (tmp_path / "nope-reveal-eval").exists()          # nothing is created for a wrong path
    (tmp_path / "empty").mkdir()
    assert m.main(["label", str(tmp_path / "empty")]) == 2
    assert not (tmp_path / "empty-reveal-eval").exists()

