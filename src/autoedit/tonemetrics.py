"""Objective tone metrics — the numbers behind the dark-RAW gate.

`describe()` reduces an image (plus optional region masks) to a flat dict of luminance facts;
`compare()` judges an output against its source (and, for RAW, against the camera's own embedded
preview) and raises the two flags the product must never trip silently:

  dark_made_darker        a dark source (foreground / bottom-band median < dark_source) came out
                          materially darker
  bright_pushed_to_clip   a bright source gained more than `clip_budget` of clipped highlights

Used by scripts/tone_report.py, scripts/raw_benchmark.py and the blind-test harness so every gate
reads one definition. numpy + stats.py only (no torch, no extras).
"""

from __future__ import annotations

import csv
import math
import os
import re

import numpy as np

from . import stats as stats_mod
from .imageio import Image, srgb_to_linear

# The per-image facts every report row carries (prefixed src_/out_/cam_ by the caller).
FIELDS = ("mean", "p10", "p50", "p90", "p99", "fg_p50", "band_top_p50", "band_mid_p50",
          "band_bottom_p50", "dark_cov", "clip_lo", "clip_hi", "headroom_ev")


def from_global(g) -> dict:
    """Flatten a GlobalStatsModel (source, post-edit, or sidecar-loaded) to the FIELDS dict."""
    return {
        "mean": float(g.mean_lum), "p10": float(g.lum_p10), "p50": float(g.lum_p50),
        "p90": float(g.lum_p90), "p99": float(g.lum_p99), "fg_p50": float(g.fg_p50),
        "band_top_p50": float(g.band_top_p50), "band_mid_p50": float(g.band_mid_p50),
        "band_bottom_p50": float(g.band_bottom_p50), "dark_cov": float(g.dark_cov),
        "clip_lo": float(g.shadow_clip), "clip_hi": float(g.highlight_clip),
        "headroom_ev": float(g.headroom_ev),
    }


def describe(rgb: np.ndarray, masks: dict | None = None) -> dict:
    """FIELDS for a float gamma RGB array; `masks` (name → bool HxW) enables fg_p50 (sky-excluded)."""
    g, _ = stats_mod.compute(Image(rgb=np.asarray(rgb, dtype=np.float32)), masks or {})
    return from_global(g)


def ev_gap(a_p50: float, b_p50: float) -> float:
    """Stops between two gamma-encoded medians: log2(lin(a) / lin(b)); positive = a is brighter."""
    la = float(srgb_to_linear(np.float32(a_p50)))
    lb = float(srgb_to_linear(np.float32(b_p50)))
    return math.log2(max(la, 1e-6) / max(lb, 1e-6))


def compare(src: dict, out: dict, cam: dict | None = None, dark_source: float = 0.30,
            dark_regress: float = 0.01, clip_budget: float = 0.005) -> dict:
    """Deltas + flags for one photo. `src`/`out`/`cam` are FIELDS dicts (cam may be None)."""
    d = {
        "d_mean": out["mean"] - src["mean"],
        "d_fg_p50": out["fg_p50"] - src["fg_p50"],
        "d_bottom": out["band_bottom_p50"] - src["band_bottom_p50"],
        "d_dark_cov": out["dark_cov"] - src["dark_cov"],
        "d_clip_hi": out["clip_hi"] - src["clip_hi"],
        "ev_vs_src": ev_gap(out["p50"], src["p50"]),
    }
    d["dark_made_darker"] = bool(
        (src["fg_p50"] < dark_source and out["fg_p50"] < src["fg_p50"] - dark_regress)
        or (src["band_bottom_p50"] < dark_source
            and out["band_bottom_p50"] < src["band_bottom_p50"] - dark_regress))
    d["bright_pushed_to_clip"] = bool(src["p90"] >= 0.60 and d["d_clip_hi"] > clip_budget)
    if cam is not None:
        d["ev_vs_camera"] = ev_gap(out["p50"], cam["p50"])
        d["cam_d_bottom"] = out["band_bottom_p50"] - cam["band_bottom_p50"]
        d["cam_d_clip_hi"] = out["clip_hi"] - cam["clip_hi"]
    return d


def row_for(name: str, src: dict, out: dict, cam: dict | None = None, **thr) -> dict:
    """One flat CSV/JSON row: name, category, src_*, out_*, cam_*, then compare() keys."""
    row = {"name": name, "category": category_of(name)}
    row.update({f"src_{k}": v for k, v in src.items()})
    row.update({f"out_{k}": v for k, v in out.items()})
    if cam is not None:
        row.update({f"cam_{k}": v for k, v in cam.items()})
    row.update(compare(src, out, cam, **thr))
    return row


def summarize(rows: list[dict]) -> dict:
    """Folder-level counts — the pass/fail numbers a change is judged by."""
    n = len(rows)
    if not n:
        return {"n": 0}
    s = {
        "n": n,
        "dark_made_darker": sum(1 for r in rows if r.get("dark_made_darker")),
        "bright_pushed_to_clip": sum(1 for r in rows if r.get("bright_pushed_to_clip")),
        "darker_than_source_mean": sum(1 for r in rows if r.get("d_mean", 0.0) < 0),
        "out_bottom_below_020": sum(1 for r in rows if r.get("out_band_bottom_p50", 1.0) < 0.20),
        "out_dark_cov_above_020": sum(1 for r in rows if r.get("out_dark_cov", 0.0) > 0.20),
        "median_ev_vs_src": _median([r["ev_vs_src"] for r in rows if "ev_vs_src" in r]),
    }
    cam = [r["ev_vs_camera"] for r in rows if "ev_vs_camera" in r]
    if cam:
        s["n_with_camera"] = len(cam)
        s["median_ev_vs_camera"] = _median(cam)
        s["median_abs_ev_vs_camera"] = _median([abs(v) for v in cam])
        s["iqr_ev_vs_camera"] = [float(np.percentile(cam, 25)), float(np.percentile(cam, 75))]
        s["cam_outliers_gt_075"] = sum(1 for v in cam if abs(v) > 0.75)
    return s


def _median(xs: list[float]) -> float | None:
    return float(np.median(xs)) if xs else None


_CAT = re.compile(r"^\d+_([a-z\-]+)__")


def category_of(name: str) -> str:
    """Scene tag from the curated-corpus naming (`0006_dark__3D6A4941.cr2` → `dark`), else ''."""
    m = _CAT.match(os.path.basename(name))
    return m.group(1) if m else ""


def write_csv(rows: list[dict], path: str) -> None:
    if not rows:
        return
    keys: list[str] = []
    for r in rows:  # union of keys, first-seen order (camera columns may be absent for JPEGs)
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
