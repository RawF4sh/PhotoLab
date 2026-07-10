"""Blind-test harness: renders 3 variants/photo, shuffles, and scores picks against the manifest."""

import csv
import importlib.util
import pathlib
import sys

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
    slot_orders = {
        tuple(method for method, _slot in sorted(methods.items(), key=lambda item: item[1]))
        for methods in by_photo.values()
    }
    assert len(slot_orders) > 1                              # shuffle is performed per photo

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
    assert res["p_value"] == 0.5                          # exact two-sided binomial for 2–0
    assert res["p_value_assumption"] == "independent-photo Bernoulli trials"


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
    before_landscape, marker, landscape_onward = rules_src.partition("[landscape.global]")
    assert marker
    rules_b = before_landscape + marker + landscape_onward.replace(
        'vibrance   = "4"', 'vibrance   = "90"', 1)
    ra = tmp_path / "rules_a.toml"; ra.write_text(rules_src)
    rb = tmp_path / "rules_b.toml"; rb.write_text(rules_b)
    assert ra.read_text() != rb.read_text()

    out = tmp_path / "ab"
    old_argv = sys.argv
    try:
        sys.argv = [
            "blindtest.py", str(corpus), str(out), "--backend", "stub", "--max-px", "160",
            "--rules-a", str(ra), "--rules-b", str(rb), "--seed", "3",
        ]
        assert bt.main() == 0
    finally:
        sys.argv = old_argv

    rows = list(csv.DictReader(open(out / "manifest.csv")))
    assert {r["method"] for r in rows} == {"rules_a", "rules_b"}
    s0 = np.asarray(PILImage.open(out / "photo001" / "slot0.jpg"), np.float32)
    s1 = np.asarray(PILImage.open(out / "photo001" / "slot1.jpg"), np.float32)
    assert np.abs(s0 - s1).mean() > 1.0                   # the configured vibrance delta is visible

    with open(out / "results.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["photo", "best_slot"]); w.writerow(["photo001", 0])
    res = bt.score(str(out / "results.csv"))
    assert set(res["pair"]) == {"rules_a", "rules_b"}
    assert res["total"] == 1 and res["p_value"] == 1.0    # n=1 can never be significant


def test_exact_binomial_values():
    bt = _load_blindtest()
    assert bt._binomial_p(3, 0) == 0.25
    assert bt._binomial_p(3, 1) == 0.625
    assert bt._binomial_p(8, 2) == 0.109375


def test_multi_method_score_requires_predeclared_pair(tmp_path):
    bt = _load_blindtest()
    manifest = tmp_path / "manifest.csv"
    results = tmp_path / "results.csv"
    with open(manifest, "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["photo", "slot", "method"])
        for photo in ("p1", "p2", "p3"):
            for slot, method in enumerate(("alpha", "beta", "gamma")):
                w.writerow([photo, slot, method])
    with open(results, "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["photo", "best_slot"])
        w.writerows([("p1", 0), ("p2", 0), ("p3", 0)])

    descriptive = bt.score(str(results), str(manifest))
    assert descriptive["pair"] == []
    assert descriptive["head_to_head"] is None and descriptive["p_value"] is None

    declared = bt.score(str(results), str(manifest), pair=("alpha", "beta"))
    assert declared["pair"] == ["alpha", "beta"]
    assert declared["p_value"] == 0.25
