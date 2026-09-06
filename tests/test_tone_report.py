"""Tone metrics + report: the flags fire on the right cases, summaries count them, and the
in-memory render path produces one row per photo with the camera arm absent for JPEGs."""

import importlib.util
import json
import pathlib
import subprocess
import sys

import numpy as np
from PIL import Image as PILImage

from autoedit import tonemetrics as tm
from autoedit.contract import GlobalStatsModel, RunConfig

_TR = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "tone_report.py"


def _report_module():
    spec = importlib.util.spec_from_file_location("tone_report", _TR)
    tr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tr)
    return tr


def _f(**kw):
    base = {k: 0.4 for k in tm.FIELDS}
    base.update(dark_cov=0.0, clip_lo=0.0, clip_hi=0.0, headroom_ev=1.0, p90=0.5, p99=0.6)
    base.update(kw)
    return base


def test_dark_made_darker_flag_is_source_relative():
    src = _f(fg_p50=0.17, band_bottom_p50=0.15, p50=0.17)
    worse = _f(fg_p50=0.15, band_bottom_p50=0.13, p50=0.15)
    better = _f(fg_p50=0.30, band_bottom_p50=0.28, p50=0.30)
    assert tm.compare(src, worse)["dark_made_darker"]
    assert not tm.compare(src, better)["dark_made_darker"]
    # a night frame left alone passes (relative, not absolute)
    assert not tm.compare(src, dict(src))["dark_made_darker"]
    # a bright source darkened a little is NOT a darkness regression
    bright = _f(fg_p50=0.6, band_bottom_p50=0.55, p50=0.6)
    dim = _f(fg_p50=0.55, band_bottom_p50=0.5, p50=0.55)
    assert not tm.compare(bright, dim)["dark_made_darker"]


def test_bright_pushed_to_clip_flag():
    src = _f(p90=0.85, clip_hi=0.002)
    out = _f(p90=0.9, clip_hi=0.04)
    assert tm.compare(src, out)["bright_pushed_to_clip"]
    assert not tm.compare(src, _f(p90=0.85, clip_hi=0.004))["bright_pushed_to_clip"]
    dark = _f(p90=0.3, clip_hi=0.0)
    assert not tm.compare(dark, _f(p90=0.35, clip_hi=0.01))["bright_pushed_to_clip"]


def test_ev_gap_is_logarithmic_in_linear_light():
    assert abs(tm.ev_gap(0.5, 0.5)) < 1e-9
    one_stop = tm.ev_gap(0.5, 0.36)   # sRGB 0.36 ≈ half the linear light of 0.5
    assert 0.85 < one_stop < 1.15
    assert tm.ev_gap(0.2, 0.5) < 0


def test_summary_counts_and_camera_stats():
    rows = [
        tm.row_for("0001_dark__A.cr2", _f(fg_p50=0.17, band_bottom_p50=0.15, p50=0.17),
                   _f(fg_p50=0.15, band_bottom_p50=0.13, p50=0.15), _f(p50=0.3)),
        tm.row_for("0002_snow__B.cr2", _f(p90=0.85), _f(p90=0.9, clip_hi=0.03), _f(p50=0.6)),
        tm.row_for("plain.jpg", _f(), _f(fg_p50=0.45), None),
    ]
    assert rows[0]["category"] == "dark" and rows[1]["category"] == "snow" and rows[2]["category"] == ""
    s = tm.summarize(rows)
    assert s["n"] == 3 and s["dark_made_darker"] == 1 and s["bright_pushed_to_clip"] == 1
    assert s["n_with_camera"] == 2 and "median_abs_ev_vs_camera" in s
    assert "ev_vs_camera" not in rows[2]


def test_describe_matches_stats_fields():
    rgb = np.full((30, 30, 3), 0.2, np.float32)
    rgb[20:] = 0.05
    d = tm.describe(rgb)
    assert set(d) == set(tm.FIELDS)
    assert d["band_bottom_p50"] < d["band_top_p50"]
    assert d["dark_cov"] > 0.3


def test_render_report_on_synthetic_folder(tmp_path):
    tr = _report_module()

    corpus = tmp_path / "c"
    corpus.mkdir()
    for i, fill in enumerate(([40, 40, 45], [180, 190, 210])):
        a = np.full((64, 96, 3), fill, np.uint8)
        a[:30] = [120, 150, 210]
        PILImage.fromarray(a, "RGB").save(str(corpus / f"p{i}.jpg"), quality=95)
    rows = tr.rows_by_render(str(corpus), RunConfig(backend="stub"), max_px=96, quiet=True)
    assert len(rows) == 2
    assert all("out_fg_p50" in r and "src_band_bottom_p50" in r for r in rows)
    assert all("d_rgb_highlight_clip_any" in r and "review_reason" in r for r in rows)
    csv_path = tmp_path / "r.csv"
    tm.write_csv(rows, str(csv_path))
    assert csv_path.read_text().count("\n") == 3


def test_render_failure_is_recorded_and_later_photo_is_still_assessed(tmp_path):
    (tmp_path / "a-broken.png").write_bytes(b"not an image")
    PILImage.new("RGB", (32, 32), (100, 100, 100)).save(tmp_path / "b-good.png")
    errors = []
    rows = _report_module().rows_by_render(str(tmp_path), RunConfig(backend="stub"),
                                           max_px=32, camera=False, quiet=True, errors=errors)
    assert [r["name"] for r in rows] == ["b-good.png"]
    assert [e["name"] for e in errors] == ["a-broken.png"]
    assert "cannot identify image" in errors[0]["error"]


def test_render_cli_gate_fails_partial_report_even_when_successes_have_no_regressions(tmp_path):
    (tmp_path / "a-broken.png").write_bytes(b"not an image")
    PILImage.new("RGB", (32, 32), (100, 100, 100)).save(tmp_path / "b-good.png")
    report = tmp_path / "report.json"
    result = subprocess.run([sys.executable, str(_TR), str(tmp_path), "--render", "--backend", "stub",
                             "--max-px", "32", "--no-camera", "--fail-on-regression", "--json", str(report)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 1, result.stderr
    data = json.loads(report.read_text())
    assert data["summary"]["n"] == 1 and data["summary"]["expected"] == 2
    assert data["summary"]["errors"] == 1 and data["summary"]["complete"] is False
    assert data["summary"]["dark_made_darker"] == data["summary"]["bright_pushed_to_clip"] == 0
    assert data["errors"][0]["name"] == "a-broken.png"
    assert "1 of 2 sources could not be assessed" in result.stderr


def _sidecar_stats(**kw):
    return GlobalStatsModel(mean_lum=0.4, mean_sat=0.1, shadow_clip=0.0, highlight_clip=0.0,
                            haze=0.2, fg_p50=0.4, band_bottom_p50=0.4, lum_p50=0.4,
                            lum_p90=0.7, **kw).model_dump()


def test_sidecar_failures_are_isolated_and_make_cli_gate_fail(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir()
    out.mkdir()
    for name in ("a-missing", "b-invalid", "c-no-post", "d-good"):
        (src / f"{name}.png").write_bytes(b"source pixels are not needed for sidecar reports")
    (out / "b-invalid.autoedit.json").write_text("{invalid")
    (out / "c-no-post.autoedit.json").write_text(json.dumps({"global_stats": _sidecar_stats()}))
    (out / "d-good.autoedit.json").write_text(json.dumps({"global_stats": _sidecar_stats(),
                                                         "post_edit_stats": _sidecar_stats()}))
    report = tmp_path / "report.json"
    result = subprocess.run([sys.executable, str(_TR), str(src), "--out", str(out), "--no-camera",
                             "--fail-on-regression", "--json", str(report)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 1, result.stderr
    data = json.loads(report.read_text())
    assert [r["name"] for r in data["rows"]] == ["d-good.png"]
    assert {e["name"] for e in data["errors"]} == {"a-missing.png", "b-invalid.png", "c-no-post.png"}
    assert data["summary"]["expected"] == 4 and data["summary"]["errors"] == 3
    assert data["summary"]["complete"] is False


def test_rgb_review_diagnostics_survive_reports_without_changing_luminance_gate(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir()
    out.mkdir()
    (src / "p.png").write_bytes(b"source pixels are not needed for sidecar reports")
    reason = "RGB highlight clipping increased +1.00%"
    (out / "p.autoedit.json").write_text(json.dumps({
        "global_stats": _sidecar_stats(rgb_highlight_clip_any=0.001),
        "post_edit_stats": _sidecar_stats(rgb_highlight_clip_any=0.011),
        "quality_score": 0.75, "needs_review": True, "review_reason": reason,
    }))
    report, csv_path = tmp_path / "report.json", tmp_path / "report.csv"
    result = subprocess.run([sys.executable, str(_TR), str(src), "--out", str(out), "--no-camera",
                             "--fail-on-regression", "--json", str(report), "--csv", str(csv_path)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(report.read_text())
    row = data["rows"][0]
    assert row["src_rgb_highlight_clip_any"] == 0.001 and row["out_rgb_highlight_clip_any"] == 0.011
    assert abs(row["d_rgb_highlight_clip_any"] - 0.01) < 1e-12
    assert row["needs_review"] is True and row["review_reason"] == reason
    assert not row["bright_pushed_to_clip"] and not row["dark_made_darker"]
    assert data["summary"]["complete"] is True and data["errors"] == []
    assert reason in csv_path.read_text() and reason in result.stdout


def test_empty_failed_report_still_writes_error_artifact(tmp_path):
    (tmp_path / "broken.png").write_bytes(b"not an image")
    report = tmp_path / "report.json"
    result = subprocess.run([sys.executable, str(_TR), str(tmp_path), "--render", "--backend", "stub",
                             "--no-camera", "--fail-on-regression", "--json", str(report)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    data = json.loads(report.read_text())
    assert data["rows"] == [] and data["summary"]["n"] == 0
    assert data["summary"]["expected"] == data["summary"]["errors"] == 1
    assert data["errors"][0]["name"] == "broken.png"
