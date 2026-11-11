"""Blind-test harness: renders 3 variants/photo, shuffles, and scores picks against the manifest."""

import csv
import hashlib
import importlib.util
import json
import pathlib
import shutil
import sys
from unittest.mock import patch
import pytest

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


def test_presentation_order_has_an_independent_reproducible_stream(tmp_path):
    bt = _load_blindtest()
    photos = [{"id": f"photo{i:03d}", "slots": ["a", "b"]} for i in range(9)]
    expected = list(photos)
    bt._rng(13, "presentation:baseline,autoedit").shuffle(expected)
    meta = {"gallery_id": "fixture", "expected": 9,
            "render": {"seed": 13, "methods": ["baseline", "autoedit"]}}
    for _ in range(2):
        # Consuming arbitrary sampling/slot streams must not change presentation.
        bt._rng(13, "selection:fixture").random()
        bt._rng(13, "slots:baseline,autoedit:capture").random()
        with patch.object(bt, "_write_manifest"), patch.object(bt, "_write_run_metadata"), \
                patch.object(bt, "_write_html") as html:
            bt._publish_gallery(str(tmp_path), dict(meta), photos,
                                [{"group": str(i)} for i in range(9)], [], [])
            assert html.call_args.args[1] == expected
    assert [p["id"] for p in photos] == [f"photo{i:03d}" for i in range(9)]


def test_render_then_score(tmp_path):
    bt = _load_blindtest()
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _make(str(corpus / "a.jpg"), [70, 120, 80])
    _make(str(corpus / "b.jpg"), [150, 120, 90])

    out = tmp_path / "bt"
    cfg = bt.make_config(backend="stub")
    summary = bt.render(str(corpus), str(out), cfg, seed=1, max_px=160, quality=85, baseline_workflow="historical")
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
    # Independent shuffles may legitimately give two photographs the same slot order.

    # self-contained gallery written
    html = (out / "index.html").read_text()
    assert "photo001" in html and "Photo comparison" in html and len(html) > 1000
    assert "autoedit" not in html.casefold()

    # simulate a human who always picks AutoEdit → it should win the gate 100%
    with open(out / "results.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["gallery_id", "photo", "best_slot"])
        for pid, methods in by_photo.items():
            w.writerow([summary["gallery_id"], pid, methods["autoedit"]])

    res = bt.score(str(out / "results.csv"), pair=("autoedit", "baseline"))
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
    metadata = json.loads((out / "run.json").read_text())
    configs = metadata["pipelines"]
    assert configs["rules_a"]["config_files"]["rules_path"]["sha256"] == hashlib.sha256(ra.read_bytes()).hexdigest()
    assert configs["rules_b"]["config_files"]["rules_path"]["sha256"] == hashlib.sha256(rb.read_bytes()).hexdigest()
    assert {item["method"] for item in metadata["sidecars"]} == {"rules_a", "rules_b"}
    assert all((out / item["path"]).exists() for item in metadata["sidecars"])
    s0 = np.asarray(PILImage.open(out / "photo001" / "slot0.jpg"), np.float32)
    s1 = np.asarray(PILImage.open(out / "photo001" / "slot1.jpg"), np.float32)
    assert np.abs(s0 - s1).mean() > 1.0                   # the configured vibrance delta is visible

    with open(out / "results.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["gallery_id", "photo", "best_slot"]); w.writerow([metadata["gallery_id"], "photo001", 0])
    res = bt.score(str(out / "results.csv"))
    assert set(res["pair"]) == {"rules_a", "rules_b"}
    assert res["total"] == 1 and res["p_value"] == 1.0    # n=1 can never be significant


def test_score_print_order_is_stable_regardless_of_manifest_row_order(tmp_path):
    """Regression: the summary table's method row order must not depend on which method happens
    to appear first in manifest.csv (that first-occurrence order is itself the first photo's
    RANDOM per-photo shuffle, i.e. --seed-dependent) — otherwise scoring the same corpus across
    different seeds prints the table in a different, unstable order each time."""
    import contextlib
    import io

    bt = _load_blindtest()

    def _score_with_manifest(rows):
        manifest = tmp_path / f"manifest_{len(rows)}_{rows[0][2]}.csv"
        results = tmp_path / f"results_{len(rows)}_{rows[0][2]}.csv"
        with open(manifest, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["photo", "slot", "method"])
            w.writerows(rows)
        with open(results, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["photo", "best_slot"])
            w.writerow(["p1", 0])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            bt.score(str(results), str(manifest))
        return buf.getvalue()

    # same three methods, manifest rows written in two different first-seen orders
    out_a = _score_with_manifest([
        ("p1", 0, "autoedit"), ("p1", 1, "unedited"), ("p1", 2, "baseline"),
    ])
    out_b = _score_with_manifest([
        ("p1", 0, "baseline"), ("p1", 1, "unedited"), ("p1", 2, "autoedit"),
    ])

    def _row_order(text):
        return [line.split()[0] for line in text.splitlines()
                if line.strip().startswith(("unedited", "baseline", "autoedit"))]

    assert _row_order(out_a) == _row_order(out_b) == ["unedited", "baseline", "autoedit"]


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


def test_camera_arm_stratify_groups_and_metrics(tmp_path):
    """The camera method uses a real reference (mocked here),
    exposure stratification (N per dark/mid/bright tercile), burst grouping in the manifest,
    metrics.csv next to the picks, and per-group significance in --score."""
    bt = _load_blindtest()
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    # 6 frames: two bursts (…101/…102/…103 and …200/…201) + one lone frame, brightness spread
    for name, lvl in (("IMG_0101.jpg", 30), ("IMG_0102.jpg", 40), ("IMG_0103.jpg", 90),
                      ("IMG_0200.jpg", 120), ("IMG_0201.jpg", 170), ("IMG_0300.jpg", 220)):
        a = np.full((60, 80, 3), lvl, np.uint8)
        a[:24] = [120, 150, 210]
        PILImage.fromarray(a, "RGB").save(str(corpus / name), quality=92)

    groups = bt.burst_groups([str(corpus / n) for n in
                              ("IMG_0101.jpg", "IMG_0102.jpg", "IMG_0103.jpg", "IMG_0200.jpg", "IMG_0201.jpg", "IMG_0300.jpg")])
    assert len(set(groups.values())) == 3
    assert groups[str(corpus / "IMG_0101.jpg")] == groups[str(corpus / "IMG_0103.jpg")]
    assert groups[str(corpus / "IMG_0200.jpg")] != groups[str(corpus / "IMG_0300.jpg")]

    out = tmp_path / "bt"
    cfg = bt.make_config(backend="stub")
    with patch.object(bt, "camera_preview", side_effect=lambda path, *a, **kw: bt.load_image(path).rgb):
        summary = bt.render(str(corpus), str(out), cfg, seed=3, max_px=80, quality=85,
                            methods=("camera", "autoedit"), per_stratum=1)
    assert summary["photos"] == 3                            # one per tercile
    assert summary["camera_missing"] == 0                    # explicit camera reference fixture
    rows = list(csv.DictReader(open(out / "manifest.csv")))
    assert {r["method"] for r in rows} == {"camera", "autoedit"}
    assert {r["stratum"] for r in rows} == {"dark", "mid", "bright"}
    assert all(r["group"].startswith("b") for r in rows)
    metrics = list(csv.DictReader(open(out / "metrics.csv")))
    assert len(metrics) == 6 and {"out_fg_p50", "dark_made_darker", "method"} <= set(metrics[0])

    by_photo: dict = {}
    for r in rows:
        by_photo.setdefault(r["photo"], {})[r["method"]] = int(r["slot"])
    with open(out / "results.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["gallery_id", "photo", "best_slot"])
        for pid, methods in by_photo.items():
            w.writerow([summary["gallery_id"], pid, methods["autoedit"]])
    res = bt.score(str(out / "results.csv"))
    assert res["pair"] == ["autoedit", "camera"]             # AutoEdit is always the challenger
    assert res["group_wins"][0] == res["groups"] and res["group_wins"][1] == 0
    assert res["strata"] == {"dark": 1, "mid": 1, "bright": 1}
    assert res["n_for_power"]["80%"] < res["n_for_power"]["65%"] < res["n_for_power"]["60%"]


def test_n_for_power_is_sane():
    bt = _load_blindtest()
    n65 = bt.n_for_power(0.65)
    assert 75 <= n65 <= 100                                  # a real 65% edge needs ~90 independent photos
    assert 40 <= bt.n_for_power(0.70) <= 60
    assert bt.n_for_power(0.95) <= 10


def test_gallery_retains_pipeline_sidecars_and_public_run_metadata(tmp_path):
    bt = _load_blindtest()
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    source = corpus / "IMG_0101.jpg"
    _make(str(source), [70, 120, 80])
    original = source.read_bytes()
    cfg = bt.make_config(backend="stub").model_copy(update={"api_key": "test-secret-do-not-serialize"})
    out = tmp_path / "gallery"
    with patch.object(bt, "camera_preview", return_value=bt.load_image(str(source)).rgb):
        bt.render(str(corpus), str(out), cfg, seed=4, max_px=160,
                  methods=("baseline", "camera", "autoedit"), baseline_workflow="historical")

    metadata_text = (out / "run.json").read_text()
    metadata = json.loads(metadata_text)
    assert "test-secret-do-not-serialize" not in metadata_text and '"api_key"' not in metadata_text
    assert metadata["status"] == "complete" and metadata["summary"]["photos"] == 1
    assert metadata["config"]["quality"] == metadata["render"]["quality"] == 95
    assert metadata["render"]["methods"] == ["baseline", "camera", "autoedit"]
    assert metadata["render"]["metrics_scope"] == "whole_frame_before_jpeg_encoding"
    assert metadata["render"]["batch_consistency"] is False
    assert metadata["harness_sha256"] == hashlib.sha256(_BT.read_bytes()).hexdigest()
    assert metadata["sources"][0]["sha256"] == hashlib.sha256(original).hexdigest()
    assert source.read_bytes() == original
    rules_metadata = metadata["config_files"]["rules_path"]
    assert rules_metadata["sha256"] == hashlib.sha256(pathlib.Path(cfg.rules_path).read_bytes()).hexdigest()
    assert pathlib.Path(rules_metadata["path"]).is_absolute()
    assert metadata["pipelines"]["autoedit"]["segmenter"]["name"] == "stub"

    manifest = list(csv.DictReader((out / "manifest.csv").open()))
    autoedit_row = next(row for row in manifest if row["method"] == "autoedit")
    sidecar_path = f"{autoedit_row['photo']}/slot{autoedit_row['slot']}.autoedit.json"
    assert metadata["sidecars"] == [{"photo": "photo001", "slot": int(autoedit_row["slot"]),
                                      "method": "autoedit", "path": sidecar_path}]
    sidecar = json.loads((out / sidecar_path).read_text())
    assert sidecar["global_stats"] and sidecar["post_edit_stats"]
    assert sidecar["region_stats"] and sidecar["post_region_stats"]
    assert {"needs_review", "quality_score", "review_reason", "warnings", "decode_ev"} <= sidecar.keys()
    assert "rgb_highlight_clip_any" in sidecar["global_stats"]
    assert "rgb_highlight_clip_any" in sidecar["post_edit_stats"]

    # Fair comparison metrics remain whole-frame, while the sidecar retains sky-excluded stats.
    rows = list(csv.DictReader((out / "metrics.csv").open()))
    assert len(rows) == 3
    whole_frame = bt.tm.describe(bt.load_image(str(source)).rgb)
    assert all(abs(float(row["src_fg_p50"]) - whole_frame["fg_p50"]) < 1e-7 for row in rows)
    assert abs(sidecar["global_stats"]["fg_p50"] - whole_frame["fg_p50"]) > 0.01


def test_cli_quality_uses_config_default_and_explicit_override(tmp_path):
    bt = _load_blindtest()
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _make(str(corpus / "a.jpg"), [70, 120, 80])
    configs = tmp_path / "configs"
    shutil.copytree(pathlib.Path(bt.paths.configs_dir()), configs)
    defaults = configs / "defaults.toml"
    defaults.write_text(defaults.read_text().replace("quality = 95", "quality = 71"))
    assert bt.make_config(str(configs)).quality == 71

    for override, expected in ((None, 71), (83, 83)):
        out = tmp_path / f"quality-{expected}"
        argv = [str(_BT), str(corpus), str(out), "--configs-dir", str(configs),
                "--backend", "stub", "--max-px", "160", "--methods", "baseline,camera,autoedit"]
        if override is not None:
            argv += ["--quality", str(override)]
        with patch.object(sys, "argv", argv), patch.object(bt, "save_image", wraps=bt.save_image) as save, \
                patch.object(bt, "camera_preview", return_value=bt.load_image(str(corpus / "a.jpg")).rgb):
            assert bt.main() == 0
            assert save.call_count == 3
            assert all(call.args[3] == expected for call in save.call_args_list)
        metadata = json.loads((out / "run.json").read_text())
        assert metadata["config"]["quality"] == expected
        assert metadata["render"]["quality"] == expected
        assert metadata["pipelines"]["autoedit"]["config"]["quality"] == expected


def test_no_metrics_still_retains_pipeline_results_and_explicit_api_quality(tmp_path):
    bt = _load_blindtest()
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _make(str(corpus / "a.jpg"), [70, 120, 80])
    cfg = bt.make_config(backend="stub")
    out = tmp_path / "gallery"
    bt.render(str(corpus), str(out), cfg, max_px=160, quality=86, metrics=False, baseline_workflow="historical")
    metadata = json.loads((out / "run.json").read_text())
    assert metadata["config"]["quality"] == 95 and metadata["render"]["quality"] == 86
    assert metadata["render"]["metrics"] is False
    assert not (out / "metrics.csv").exists()
    assert len(metadata["sidecars"]) == 1
    assert (out / metadata["sidecars"][0]["path"]).exists()


def _comparison_fixture(tmp_path):
    """Rendered fixtures deliberately have informative names that must stay out of HTML."""
    source = tmp_path / "snow_warm_original.CR2"
    source.write_bytes(b"raw source: never decode this")
    baseline = tmp_path / "baseline_natural.jpg"
    edited = tmp_path / "autoedit_optimizer.jpg"
    _make(str(baseline), [100, 110, 120])
    _make(str(edited), [120, 130, 140])
    entry = {"capture_id": "capture-private", "source": str(source),
             "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
             "group": "burst-1", "scene": "private scene", "category": "snow", "split": "holdout",
             "methods": {"baseline": str(baseline), "autoedit": str(edited)}}
    manifest = tmp_path / "comparisons.json"
    manifest.write_text(json.dumps({"schema_version": 1, "entries": [entry]}))
    return manifest, entry


def test_prerendered_gallery_never_decodes_raw_and_hides_identifiers(tmp_path):
    bt = _load_blindtest()
    manifest, entry = _comparison_fixture(tmp_path)
    out = tmp_path / "gallery"
    real_load = bt.load_image
    def only_pixels(path, *args, **kwargs):
        assert not str(path).lower().endswith(".cr2")
        return real_load(path, *args, **kwargs)
    with patch.object(bt, "load_image", side_effect=only_pixels), \
            patch.object(bt.Pipeline, "build", side_effect=AssertionError("must not render")):
        summary = bt.render_comparison(str(manifest), str(out), split="holdout", quality=87)
    assert summary["photos"] == 1 and summary["failed"] == 0
    html = (out / "index.html").read_text()
    for secret in ("snow_warm_original", "capture-private", "private scene", "baseline_natural", "autoedit_optimizer"):
        assert secret not in html
    assert "gallery_id,photo,best_slot" in html and summary["gallery_id"] in html
    rows = list(csv.DictReader((out / "manifest.csv").open()))
    assert len(rows) == 2 and all(row["split"] == "holdout" for row in rows)
    assert all(row["sha256"] == hashlib.sha256((out / row["photo"] / f"slot{row['slot']}.jpg").read_bytes()).hexdigest()
               for row in rows)
    with (out / "results.csv").open("w", newline="") as fh:
        writer = csv.writer(fh); writer.writerow(["gallery_id", "photo", "best_slot"])
        writer.writerow([summary["gallery_id"], "photo001", 0])
    result = bt.score(str(out / "results.csv"))
    assert result["split_results"]["holdout"] and result["independent_trials"] == 1


def test_missing_camera_excludes_instead_of_substituting_source(tmp_path):
    bt = _load_blindtest()
    manifest, entry = _comparison_fixture(tmp_path)
    summary = bt.render_comparison(str(manifest), str(tmp_path / "camera"), methods=("camera", "autoedit"))
    assert summary["photos"] == 0 and summary["camera_missing"] == 1
    run = json.loads((tmp_path / "camera" / "run.json").read_text())
    assert run["exclusions"][0]["reason"] == "missing camera reference"
    assert list(csv.DictReader((tmp_path / "camera" / "manifest.csv").open())) == []
    corpus = tmp_path / "corpus"; corpus.mkdir()
    _make(str(corpus / "one.jpg"), [80, 90, 100])
    rendered = bt.render(str(corpus), str(tmp_path / "rendered"), bt.make_config(backend="stub"),
                         methods=("camera", "autoedit"))
    assert rendered["photos"] == 0 and rendered["camera_missing"] == 1


def test_prerendered_gallery_isolates_invalid_pairs_and_rejects_duplicate_captures(tmp_path):
    bt = _load_blindtest()
    manifest, entry = _comparison_fixture(tmp_path)
    broken = {**entry, "capture_id": "broken", "methods": {**entry["methods"], "autoedit": str(tmp_path / "missing.jpg")}}
    manifest.write_text(json.dumps({"entries": [broken, entry]}))
    summary = bt.render_comparison(str(manifest), str(tmp_path / "out"))
    assert summary["photos"] == 1 and summary["failed"] == 1 and summary["expected"] == 2
    assert {r["photo"] for r in csv.DictReader((tmp_path / "out" / "manifest.csv").open())} == {"photo002"}
    manifest.write_text(json.dumps({"entries": [entry, entry]}))
    with pytest.raises(ValueError):
        bt.render_comparison(str(manifest), str(tmp_path / "duplicates"))


def test_gallery_slots_are_stable_when_selection_or_another_photo_fails(tmp_path):
    bt = _load_blindtest()
    manifest, entry = _comparison_fixture(tmp_path)
    entries = [{**entry, "capture_id": f"capture-{i}", "group": f"group-{i}"} for i in range(8)]
    manifest.write_text(json.dumps({"entries": entries}))
    def orders(directory):
        result = {}
        for row in csv.DictReader((directory / "manifest.csv").open()):
            result.setdefault(row["capture_id"], {})[row["method"]] = row["slot"]
        return result
    bt.render_comparison(str(manifest), str(tmp_path / "full"), seed=17)
    bt.render_comparison(str(manifest), str(tmp_path / "sample"), seed=17, limit=4)
    whole, subset = orders(tmp_path / "full"), orders(tmp_path / "sample")
    assert len(subset) == 4 and all(whole[key] == value for key, value in subset.items())
    # Different arms use a different RNG stream; their assignment need not track one another.
    assert bt._rng(17, "slots:baseline,autoedit:capture-1").getstate() != bt._rng(17, "slots:camera,autoedit:capture-1").getstate()


def test_gallery_id_changes_with_rendered_bytes_and_refuses_overwrite(tmp_path):
    bt = _load_blindtest()
    manifest, entry = _comparison_fixture(tmp_path)
    before = bt.render_comparison(str(manifest), str(tmp_path / "one"))
    _make(entry["methods"]["autoedit"], [210, 140, 160])
    after = bt.render_comparison(str(manifest), str(tmp_path / "two"))
    assert before["gallery_id"] != after["gallery_id"]
    with pytest.raises(ValueError):
        bt.render_comparison(str(manifest), str(tmp_path / "one"))
    with pytest.raises(ValueError):
        bt.render_comparison(str(manifest), str(tmp_path))


def test_score_rejects_duplicate_invalid_and_cross_gallery_votes(tmp_path):
    bt = _load_blindtest()
    manifest, entry = _comparison_fixture(tmp_path)
    summary = bt.render_comparison(str(manifest), str(tmp_path / "out"))
    results = tmp_path / "out" / "results.csv"
    valid = [summary["gallery_id"], "photo001", 0]
    for votes in ([valid, valid], [[summary["gallery_id"], "photo001", 9]],
                  [[summary["gallery_id"], "unknown", 0]], [["other-gallery", "photo001", 0]],
                  [[summary["gallery_id"], "photo001", "NaN"]]):
        with results.open("w", newline="") as fh:
            writer = csv.writer(fh); writer.writerow(["gallery_id", "photo", "best_slot"]); writer.writerows(votes)
        with pytest.raises(ValueError):
            bt.score(str(results))


def test_grouped_votes_count_ties_and_report_split_separately(tmp_path):
    bt = _load_blindtest()
    manifest = tmp_path / "manifest.csv"
    with manifest.open("w", newline="") as fh:
        writer = csv.writer(fh); writer.writerow(["gallery_id", "photo", "slot", "method", "group", "split"])
        for pid, group, split in (("p1", "g1", "tuning"), ("p2", "g1", "tuning"), ("p3", "g2", "holdout")):
            for slot, method in enumerate(("autoedit", "baseline")):
                writer.writerow(["gid", pid, slot, method, group, split])
    results = tmp_path / "results.csv"
    with results.open("w", newline="") as fh:
        writer = csv.writer(fh); writer.writerow(["gallery_id", "photo", "best_slot"])
        writer.writerows([["gid", "p1", 0], ["gid", "p2", 1], ["gid", "p3", 0]])
    result = bt.score(str(results))
    assert result["groups"] == 2 and result["group_ties"] == 1 and result["independent_trials"] == 1
    assert result["group_wins"] == [1, 0] and result["group_p_value"] == 1
    assert result["split_results"] == {"holdout": {"autoedit": 1, "baseline": 0}, "tuning": {"autoedit": 1, "baseline": 1}}


def test_ab_decodes_each_config_independently(tmp_path):
    from autoedit.imageio import Image
    from types import SimpleNamespace
    bt = _load_blindtest()
    corpus = tmp_path / "corpus"; corpus.mkdir(); (corpus / "one.CR2").write_bytes(b"raw fixture")
    cfg = bt.make_config(backend="stub")
    seen = []
    def decode(path, decode=None):
        mode = decode.get("raw_mode", "none")
        return Image(np.full((24, 32, 3), .2 if mode == "none" else .7, np.float32), path=path)
    class FakePipeline:
        def __init__(self, config):
            self.cfg = config
            self.segmenter = SimpleNamespace(name="stub")
        def process_one(self, path, do_apply, image):
            seen.append((self.cfg.raw_mode, float(image.rgb.mean())))
            return image.rgb, None
    pipes = {"rules_a": FakePipeline(cfg), "rules_b": FakePipeline(cfg.model_copy(update={"raw_mode": "preview_ev"}))}
    with patch.object(bt, "load_image", side_effect=decode):
        summary = bt.render(str(corpus), str(tmp_path / "out"), cfg, methods=("rules_a", "rules_b"),
                            pipelines=pipes, metrics=False)
    assert summary["photos"] == 1 and seen[0][0] == "none" and seen[1][0] == "preview_ev"
    assert seen[0][1] < .3 and seen[1][1] > .6


def test_source_manifest_uses_camera_reference_and_split(tmp_path):
    bt = _load_blindtest()
    source = tmp_path / "source.jpg"; camera = tmp_path / "camera.jpg"
    _make(str(source), [80, 90, 100]); _make(str(camera), [130, 140, 150])
    entries = [{"capture_id": "capture-1", "source": str(source), "camera_reference": str(camera),
                "group": "known-burst", "scene": "known-scene", "category": "dark", "split": "holdout"},
               {"capture_id": "capture-2", "source": str(tmp_path / "must-not-read.CR2"), "split": "tuning"}]
    manifest = tmp_path / "sources.json"; manifest.write_text(json.dumps({"entries": entries}))
    with patch.object(bt, "camera_preview", side_effect=AssertionError("paired reference should win")):
        summary = bt.render("unused", str(tmp_path / "out"), bt.make_config(backend="stub"),
                            methods=("camera", "autoedit"), manifest_path=str(manifest), split="holdout")
    assert summary["photos"] == 1 and summary["failed"] == 0
    assert all(row["group"] == "known-burst" and row["split"] == "holdout"
               for row in csv.DictReader((tmp_path / "out" / "manifest.csv").open()))


def test_fallback_burst_groups_never_mix_directories_or_camera_prefixes():
    bt = _load_blindtest()
    paths = ["/shoot1/IMG_100.jpg", "/shoot1/IMG_101.jpg", "/shoot2/IMG_101.jpg", "/shoot1/OTHER_101.jpg"]
    grouped = bt.burst_groups(paths)
    assert grouped[paths[0]] == grouped[paths[1]]
    assert len(set(grouped.values())) == 3


def test_exact_binomial_large_counts_and_power_boundaries():
    bt = _load_blindtest()
    assert bt._binomial_p(0, 0) == 1
    assert bt._binomial_p(5, 0) == .0625 and bt._binomial_p(6, 0) == .03125
    assert bt._binomial_p(600, 600) == 1
    assert bt.n_for_power(.95, n_max=5) is None
    assert bt.n_for_power(.2) == bt.n_for_power(.8)
    with pytest.raises(ValueError):
        bt.n_for_power(1.0)


def _vote_fixture(tmp_path, photos, votes, methods=("autoedit", "baseline"), gallery_id="tie-test"):
    """Metadata-only fixture: photo tuples are (id, group, split, scene)."""
    manifest = tmp_path / "manifest.csv"
    results = tmp_path / "results.csv"
    with manifest.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["gallery_id", "photo", "slot", "method", "group", "split", "scene"])
        for pid, group, split, scene in photos:
            for slot, method in enumerate(methods):
                writer.writerow([gallery_id, pid, slot, method, group, split, scene])
    with results.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["gallery_id", "photo", "best_slot"])
        writer.writerows([gallery_id, pid, value] for pid, value in votes)
    return manifest, results


def test_photo_ties_are_answered_but_excluded_from_exact_trials(tmp_path):
    bt = _load_blindtest()
    photos = [(f"p{i}", f"g{i}", "holdout", "event") for i in range(8)]
    manifest, results = _vote_fixture(tmp_path, photos, [(f"p{i}", 0) for i in range(6)] + [("p6", "tie")])
    result = bt.score(str(results), str(manifest))
    assert result["total"] == 7 and result["expected"] == 8
    assert result["photo_ties"] == 1 and result["missing_votes"] == 1
    assert result["decisive_votes"] == result["decisive_trials"] == 6
    assert result["wins"] == {"autoedit": 6}
    assert result["head_to_head"] == 100 and result["p_value"] == .03125
    assert result["groups"] == 7 and result["expected_groups"] == 8 and result["missing_groups"] == 1
    assert result["group_ties"] == result["group_ties_photo_only"] == 1
    assert result["independent_trials"] == 6 and result["group_p_value"] == .03125
    assert result["split_photo_ties"] == {"holdout": 1}
    assert result["split_missing_votes"] == {"holdout": 1}


def test_all_photo_ties_keep_groups_without_claiming_a_preference(tmp_path):
    bt = _load_blindtest()
    photos = [("p1", "same-id", "tuning", "scene-a"), ("p2", "same-id", "tuning", "scene-b"),
              ("p3", "same-id", "holdout", "scene-c")]
    manifest, results = _vote_fixture(tmp_path, photos, [(pid, "tie") for pid, _, _, _ in photos])
    result = bt.score(str(results), str(manifest))
    assert result["total"] == result["photo_ties"] == 3 and result["missing_votes"] == 0
    assert result["decisive_votes"] == result["decisive_trials"] == 0
    assert result["head_to_head"] is None and result["p_value"] is None
    assert result["groups"] == 2 and result["group_ties"] == result["group_ties_photo_only"] == 2
    assert result["group_ties_balanced"] == 0 and result["group_wins"] == [0, 0]
    assert result["independent_trials"] == 0 and result["group_p_value"] is None
    assert all(group["outcome"] == "photo_ties_only" for group in result["group_details"])


def test_group_majority_uses_group_identity_not_scene_label(tmp_path):
    bt = _load_blindtest()
    photos = [("p1", "one-burst", "holdout", "snow"), ("p2", "one-burst", "holdout", "landscape"),
              ("p3", "one-burst", "holdout", "other")]
    manifest, results = _vote_fixture(tmp_path, photos, [("p1", 0), ("p2", 1), ("p3", "tie")])
    result = bt.score(str(results), str(manifest))
    assert result["groups"] == 1 and result["group_ties"] == result["group_ties_balanced"] == 1
    assert result["group_ties_photo_only"] == 0 and result["independent_trials"] == 0
    assert result["group_p_value"] is None
    assert result["group_details"][0]["scenes"] == ["landscape", "other", "snow"]
    assert result["scene_results"]["snow"] == {"autoedit": 1, "baseline": 0}
    assert result["scene_photo_ties"]["other"] == 1


def test_ties_preserve_vote_validation_and_reject_invalid_numeric_slots(tmp_path):
    bt = _load_blindtest()
    photos = [("p1", "g1", "holdout", "event")]
    invalid_votes = [[("p1", "tie"), ("p1", 0)], [("p1", "tie"), ("p1", "tie")],
                     [("unknown", "tie")], [("p1", -1)], [("p1", 2)], [("p1", "1.5")],
                     [("p1", "NaN")], [("p1", "")], [("p1", "Tie")]]
    for votes in invalid_votes:
        manifest, results = _vote_fixture(tmp_path, photos, votes)
        with pytest.raises(ValueError):
            bt.score(str(results), str(manifest))
    manifest, results = _vote_fixture(tmp_path, photos, [("p1", "tie")])
    results.write_text("gallery_id,photo,best_slot\nwrong-gallery,p1,tie\n")
    with pytest.raises(ValueError):
        bt.score(str(results), str(manifest))


def test_conditional_multi_arm_ties_are_distinct_from_other_arm_votes(tmp_path):
    bt = _load_blindtest()
    photos = [("p1", "tie-group", "holdout", "event"),
              ("p2", "other-group", "holdout", "event"), ("p3", "other-group", "holdout", "event"),
              ("p4", "winning-group", "holdout", "event"), ("p5", "winning-group", "holdout", "event")]
    votes = [("p1", "tie"), ("p2", 2), ("p3", "tie"), ("p4", 0), ("p5", "tie")]
    manifest, results = _vote_fixture(tmp_path, photos, votes, methods=("alpha", "beta", "gamma"))
    result = bt.score(str(results), str(manifest), pair=("alpha", "beta"))
    assert result["total"] == 5 and result["photo_ties"] == 3 and result["decisive_votes"] == 2
    assert result["decisive_trials"] == 1 and result["other_arm_votes"] == 1
    assert result["groups"] == 3 and result["group_ties_photo_only"] == 1
    assert result["groups_without_pair_preference"] == 1 and result["group_wins"] == [1, 0]
    assert result["independent_trials"] == 1


def test_neutral_gallery_presents_and_exports_canonical_no_preference(tmp_path):
    bt = _load_blindtest()
    output = tmp_path / "comparison.html"
    bt._write_html(str(output), [{"id": "photo001", "slots": ["photo001/slot0.jpg", "photo001/slot1.jpg"]}], "opaque-gallery-id")
    html = output.read_text()
    assert "<title>Photo comparison</title>" in html and '<span class="brand">Photo comparison</span>' in html
    public_markup = html.split("<body>", 1)[1].split("<script>", 1)[0].casefold()
    assert all(method not in public_markup for method in ("autoedit", "baseline", "camera"))
    assert 'id="tie"' in html and "No preference" in html
    assert "tie.onclick = () => choose('tie', tie)" in html
    assert "if (e.key === '0') choose('tie', tie)" in html
    assert "picks[PHOTOS[i].id] = slot" in html
    assert "gallery_id,photo,best_slot" in html and "picks[id]" in html
