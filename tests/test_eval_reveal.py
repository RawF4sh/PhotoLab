"""eval_reveal: the scoring and the labelling page (no model, no network)."""

import importlib.util
import json
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
    # 3 of 10 photos without the thing score 0.5, 0.5, 0.6: F21's rule gives 0.55 (1 alarm); the free
    # range (every hit scores 0.9) reaches no alarm at 0.65
    labels, results = _set("screen", 10, 10, fa=0)
    for i, s in enumerate([0.5, 0.5, 0.6]):
        results[f"n{i}.jpg"]["proxy"] = [_f("screen", s)]
    k = _mod().score(labels, results, _cfg())["kinds"]["screen"]
    assert k["min_score"] == 0.65 and k["proxy"]["t21"] == 0.55
    assert k["proxy"]["hits"] == 10 and k["proxy"]["false_alarms"] == 0


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
    for i in range(10):                      # "television" fires on the same photos
        results[f"w{i}.jpg"]["proxy"].append(_f("screen", 0.9, "television"))
    k = m.score(labels, results, cfg)["kinds"]["screen"]
    assert k["kept"] == ["computer screen"] and k["dropped"] == ["television", "phone screen"]
    assert k["proxy"]["hits"] == 10
    labels, results = _set("screen", 10, 10, word="computer screen")
    k = m.score(labels, results, cfg)["kinds"]["screen"]
    assert k["words"] == {"computer screen": 10} and k["dropped"] == ["television", "phone screen"]


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



def test_free_raise_cuts_alarms():
    m, cfg = _mod(), _cfg()
    labels, results = _set("screen", 20, 20, n_without=100, fa=3, score=0.9)
    for i in range(3):
        results[f"n{i}.jpg"]["proxy"] = [_f("screen", 0.5)]
    k = m.score(labels, results, cfg)["kinds"]["screen"]
    assert k["proxy"]["t21"] == 0.4                       # F21's rule: 3 of 100 is within 10%
    assert k["min_score"] == 0.55 and k["proxy"]["false_alarms"] == 0 and k["proxy"]["hits"] == 20


def test_cap_keeps_every_hit():
    m, cfg = _mod(), _cfg()
    labels, results = _set("screen", 20, 20, n_without=100, fa=0, score=0.6)
    for i in range(5):
        results[f"n{i}.jpg"]["proxy"] = [_f("screen", 0.8)]
    k = m.score(labels, results, cfg)["kinds"]["screen"]
    assert k["min_score"] == 0.4 and k["proxy"]["hits"] == 20 and k["proxy"]["cap"] == 0.55


def test_never_below_f21():
    m, cfg = _mod(), _cfg()
    labels, results = _set("screen", 10, 9, n_without=10, fa=0, score=0.9)
    results["w9.jpg"]["proxy"] = [_f("screen", 0.45)]        # the tenth hit is weak
    for i in range(3):
        results[f"n{i}.jpg"]["proxy"] = [_f("screen", 0.8)]
    k = m.score(labels, results, cfg)["kinds"]["screen"]
    assert k["proxy"]["t21"] == 0.85 and k["min_score"] == 0.85     # F21's, not lower, not higher
    assert k["proxy"]["hits"] == 9


def test_words_dropped_one_at_a_time():
    m, cfg = _mod(), _cfg()
    labels, results = _set("screen", 10, 10, word="computer screen")
    for i in range(10):
        results[f"w{i}.jpg"]["proxy"].append(_f("screen", 0.9, "phone screen"))   # same photos
    for i in range(3):                                                           # only alarms
        results[f"n{i}.jpg"]["proxy"] = [_f("screen", 0.9, "television")]
    k = m.score(labels, results, cfg)["kinds"]["screen"]
    assert k["kept"] == ["computer screen"] and k["dropped"] == ["television", "phone screen"]
    assert k["proxy"]["hits"] == 10 and k["min_score"] == 0.4 and k["proxy"]["false_alarms"] == 0


def _photos(root, names, size=(64, 48)):
    root.mkdir(exist_ok=True)
    for i, n in enumerate(names):
        PILImage.fromarray(np.full((size[1], size[0], 3), 40 + 30 * i, np.uint8)).save(root / n)


def test_each_word_keeps_its_areas(tmp_path):
    from autoedit.segment.base import RegionMask
    m = _mod()
    calls = []

    class Fake:
        def segment(self, img, concepts):
            calls.append(list(concepts))
            h, w = img.rgb.shape[:2]
            mask = np.zeros((h, w), bool)
            mask[10:30, 10:30] = True
            return [RegionMask(c, mask.copy(), 0.9, 0.1, 1) for c in concepts]

    def config(words):
        p = tmp_path / "r.toml"
        p.write_text('[kind.badge]\nlabel = "name badge"\nwords = [%s]\n'
                     % ", ".join(f'"{w}"' for w in words), encoding="utf-8")
        return str(p)

    _photos(tmp_path / "ph", ["a.jpg"])
    (tmp_path / "labels.csv").write_text("image,seen,badge\na.jpg,1,1\n", encoding="utf-8")
    old = m.make_segmenter, m.sam3_ready
    m.make_segmenter, m.sam3_ready = (lambda name: Fake()), (lambda: True)
    try:
        def run(words):
            assert m.main(["run", str(tmp_path / "ph"), "--labels", str(tmp_path / "labels.csv"),
                           "--out", str(tmp_path / "ev"), "--config", config(words)]) == 0
            return m.read_results(str(tmp_path / "ev" / "results.jsonl"))["a.jpg"]
        e = run(["name tag", "name badge"])
        assert sorted(f["word"] for f in e["proxy"]) == ["name badge", "name tag"]   # one area, two entries
        assert len(calls) == 1
        run(["name tag", "name badge"])                          # same words: not measured again
        assert len(calls) == 1
        e = run(["name tag", "name badge", "lanyard"])           # a new word: measured again (the cache holds the rest)
        assert len(calls) == 2 and calls[1] == ["lanyard"]
        assert sorted(f["word"] for f in e["proxy"]) == ["lanyard", "name badge", "name tag"]
        e = run(["name tag"])                                    # fewer words: kept as is
        assert len(calls) == 2 and len(e["proxy"]) == 3
    finally:
        m.make_segmenter, m.sam3_ready = old


def test_label_prefill_and_disputed(tmp_path):
    m = _mod()
    ph = tmp_path / "ph"
    _photos(ph, ["a.jpg", "m.jpg", "z.jpg"], size=(400, 300))
    (tmp_path / "labels.csv").write_text(
        "image,seen,badge,screen\na.jpg,1,1,0\nm.jpg,1,0,1\nz.jpg,1,0,0\n", encoding="utf-8")
    found = {"kind": "badge", "word": "name tag", "score": 0.7123, "box": [0.2, 0.2, 0.4, 0.4]}
    (tmp_path / "results.jsonl").write_text(
        "".join(json.dumps({"image": n, "proxy": p}) + "\n"
                for n, p in (("a.jpg", [found]), ("m.jpg", []), ("z.jpg", [found]))), encoding="utf-8")
    assert m.main(["label", str(ph), "--out", str(tmp_path / "ev"), "--labels", str(tmp_path / "labels.csv"),
                   "--results", str(tmp_path / "results.jsonl")]) == 0
    page = (tmp_path / "ev" / "label.html").read_text(encoding="utf-8")
    assert 'value="seen" checked' not in page                       # "looked at" starts empty
    assert 'value="badge" checked' in page and 'value="screen" checked' in page
    order = [page.index(f'data-name="{n}"') for n in ("z.jpg", "m.jpg", "a.jpg")]
    assert order[0] < order[2] and order[1] < order[2]               # disputed first, the agreed one last
    assert (tmp_path / "ev" / "crops" / "0003-badge-1.jpg").exists()
    assert not (tmp_path / "ev" / "crops" / "0001-badge-1.jpg").exists()
    assert "ticked, but the check found nothing: screen" in page
    assert "0.71" not in page and "http" not in page and str(tmp_path) not in page


def test_union_line():
    m, cfg = _mod(), _cfg()
    labels, results = _set("screen", 10, 10, n_without=10, fa=0)
    results["n0.jpg"]["proxy"] = [_f("screen", 0.95)]        # an alarm of a kind of the seven
    labels["n1.jpg"]["plate"] = 0
    results["n1.jpg"]["proxy"] = [_f("plate", 0.5)]          # an alarm of another kind (as today: 0.40)
    s = m.score(labels, results, cfg)
    assert s["alarm_seven"] == 1 and s["alarm_any"] == 2
    text = m.render(s)
    assert "any kind, at the recommended scores: 2 of 20" in text and "of the seven" in text and "1 of 20" in text
