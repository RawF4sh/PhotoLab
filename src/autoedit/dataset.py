"""Dataset builder (M3) — pair originals with edited targets and validate that each edit is a
*real* photographic edit, not a generated/content-changed image.

Workflow: pair by normalized filename stem → run preservation checks on each pair → copy passing
pairs into a training workspace (originals/ + targets/), failures into rejected/, and write
labels.csv + manifest.json. Copy-only: source files are never moved/renamed/deleted.

Preservation checks (the non-generative gate): the target must keep the original's framing and
structure — same aspect, high edge/structure correlation (low content drift), and no runaway
clipping / saturation / banding. Generated or heavily-cropped/relit edits fail and are excluded
from training unless a human approves them.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field

import numpy as np

from .imageio import downscale_long_edge, is_supported, load_image, luma

# Suffixes commonly appended to an edited copy — stripped so "IMG_1.jpg" pairs with "IMG_1_edit.jpg".
_EDIT_SUFFIXES = ("_edit", "_edited", "-edit", "_after", "-after", "_final", "_retouched",
                  "_v2", "_v3", "-2", "-3", "copy", "_copy", "_out")

# Default preservation thresholds (a target failing any HARD gate is rejected for training).
DEFAULTS = {
    "aspect_tol": 0.04,         # |aspect_target - aspect_src| / aspect_src
    "edge_corr_min": 0.45,      # structure correlation below this → content drift (generative)
    "clip_increase_max": 0.06,  # new highlight/shadow clipping the edit may add
    "sat_increase_max": 0.30,   # mean-saturation increase cap (neon / fake colour)
    "banding_max": 0.25,        # introduced smooth-region banding
}


def normalize_stem(name: str) -> str:
    """Filename → comparable key: lowercase, drop extension + edit suffixes, collapse separators."""
    stem = os.path.splitext(os.path.basename(name))[0].lower()
    changed = True
    while changed:
        changed = False
        for suf in _EDIT_SUFFIXES:
            if stem.endswith(suf) and len(stem) > len(suf):
                stem = stem[: -len(suf)]
                changed = True
    return re.sub(r"[\s_\-.]+", "", stem)


def _list(folder: str, recursive: bool = False) -> list[str]:
    if not folder or not os.path.isdir(folder):
        return []
    out = []
    if recursive:
        for root, _d, files in os.walk(folder):
            out += [os.path.join(root, f) for f in files if is_supported(f)]
    else:
        out = [os.path.join(folder, f) for f in sorted(os.listdir(folder))
               if os.path.isfile(os.path.join(folder, f)) and is_supported(f)]
    return sorted(out)


def pair_by_stem(originals: list[str], targets: list[str]):
    """Return (pairs, unmatched_originals, unmatched_targets), pairing by normalized stem."""
    tmap: dict[str, list[str]] = {}
    for t in targets:
        tmap.setdefault(normalize_stem(t), []).append(t)
    pairs, unmatched_o = [], []
    used: set[str] = set()
    for o in originals:
        k = normalize_stem(o)
        if tmap.get(k):
            pairs.append((o, tmap[k][0]))
            used.add(k)
        else:
            unmatched_o.append(o)
    unmatched_t = [t for t in targets if normalize_stem(t) not in used]
    return pairs, unmatched_o, unmatched_t


# ── preservation measurement ──────────────────────────────────────────────────

def _edge_map(lum: np.ndarray) -> np.ndarray:
    gy, gx = np.gradient(lum.astype(np.float32))
    m = np.sqrt(gx * gx + gy * gy)
    mx = float(m.max())
    return m / mx if mx > 1e-6 else m


def _clip_sat(rgb: np.ndarray) -> tuple[float, float, float]:
    lum = luma(rgb)
    mx, mn = rgb.max(2), rgb.min(2)
    sat = np.where(mx > 1e-6, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
    return float((lum > 0.98).mean()), float((lum < 0.02).mean()), float(sat.mean())


def validate_pair(orig_path: str, target_path: str, thr: dict | None = None) -> dict:
    """Preservation verdict for one (original, target) pair. Returns metrics + reasons + ok flag."""
    t = {**DEFAULTS, **(thr or {})}
    reasons: list[str] = []
    try:
        s_img, tg_img = load_image(orig_path), load_image(target_path)
        s_rgb = downscale_long_edge(s_img, 256).rgb
        t_rgb_native = downscale_long_edge(tg_img, 256).rgb
    except Exception as e:
        return {"ok": False, "reasons": [f"unreadable ({e})"], "metrics": {}}

    a_src, a_tgt = s_img.width / max(s_img.height, 1), tg_img.width / max(tg_img.height, 1)
    aspect_diff = abs(a_tgt - a_src) / max(a_src, 1e-6)
    if aspect_diff > t["aspect_tol"]:
        reasons.append("aspect/crop changed")

    # target resampled onto the source grid so we compare the same framing
    t_rgb = _resize_to(t_rgb_native, s_rgb.shape[:2])

    # structure correlation: Pearson-correlate the two edge maps (low = content drift / generated)
    edge_corr = _corr(_edge_map(luma(s_rgb)), _edge_map(luma(t_rgb)))
    if edge_corr < t["edge_corr_min"]:
        reasons.append("content drift (structure changed)")

    sc_hi, sc_lo, s_sat = _clip_sat(s_rgb)
    tc_hi, tc_lo, t_sat = _clip_sat(t_rgb)
    if (tc_hi - sc_hi) > t["clip_increase_max"] or (tc_lo - sc_lo) > t["clip_increase_max"]:
        reasons.append("added clipping")
    if (t_sat - s_sat) > t["sat_increase_max"]:
        reasons.append("oversaturated")

    from .edit import banding
    b_score, _act, _cov = banding.assess(s_rgb, t_rgb)
    if b_score > t["banding_max"]:
        reasons.append("banding introduced")

    metrics = {"aspect_diff": round(aspect_diff, 4), "edge_corr": round(edge_corr, 4),
               "clip_hi_delta": round(tc_hi - sc_hi, 4), "clip_lo_delta": round(tc_lo - sc_lo, 4),
               "sat_delta": round(t_sat - s_sat, 4), "banding": round(b_score, 4)}
    return {"ok": not reasons, "reasons": reasons, "metrics": metrics}


def _resize_to(rgb: np.ndarray, shape_hw) -> np.ndarray:
    from PIL import Image as PILImage
    h, w = shape_hw[0], shape_hw[1]
    pil = PILImage.fromarray(np.clip(rgb * 255 + 0.5, 0, 255).astype(np.uint8), "RGB").resize((w, h), PILImage.BILINEAR)
    return np.asarray(pil).astype(np.float32) / 255.0


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a.ravel(), b.ravel()
    a, b = a - a.mean(), b - b.mean()
    d = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / d) if d > 1e-9 else 0.0


# ── workspace build ───────────────────────────────────────────────────────────

@dataclass
class PairResult:
    stem: str
    original: str
    target: str
    ok: bool
    reasons: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    approved: bool = False           # manual override → keep even if a gate failed


def validate(originals_dir: str, targets_dir: str, recursive: bool = False,
             thr: dict | None = None) -> dict:
    """Pair + validate without copying anything (report only). Returns pairs + verdicts."""
    pairs, unmatched_o, unmatched_t = pair_by_stem(_list(originals_dir, recursive),
                                                   _list(targets_dir, recursive))
    results = []
    for o, tg in pairs:
        v = validate_pair(o, tg, thr)
        results.append(PairResult(stem=normalize_stem(o), original=os.path.abspath(o),
                                   target=os.path.abspath(tg), ok=v["ok"],
                                   reasons=v["reasons"], metrics=v["metrics"]))
    return {
        "pairs": len(results),
        "passed": sum(1 for r in results if r.ok),
        "rejected": sum(1 for r in results if not r.ok),
        "unmatched_originals": len(unmatched_o),
        "unmatched_targets": len(unmatched_t),
        "results": [r.__dict__ for r in results],
    }


def build_dataset(originals_dir: str, targets_dir: str, out_dir: str, recursive: bool = False,
                  do_validate: bool = True, approve_all: bool = False, thr: dict | None = None) -> dict:
    """Build a training workspace (copy-only): originals/, targets/, rejected/, labels.csv,
    manifest.json. Passing pairs go to originals/+targets/; failing pairs' targets go to rejected/
    (unless approve_all). Returns the manifest."""
    rep = validate(originals_dir, targets_dir, recursive, thr) if do_validate else None
    pairs, _uo, _ut = pair_by_stem(_list(originals_dir, recursive), _list(targets_dir, recursive))

    o_dir = os.path.join(out_dir, "originals")
    t_dir = os.path.join(out_dir, "targets")
    r_dir = os.path.join(out_dir, "rejected")
    for d in (o_dir, t_dir, r_dir):
        os.makedirs(d, exist_ok=True)

    verdicts = {r["target"]: r for r in (rep["results"] if rep else [])}
    rows, items = [], []
    kept = rejected = 0
    for o, tg in pairs:
        v = verdicts.get(os.path.abspath(tg), {"ok": True, "reasons": [], "metrics": {}})
        ok = bool(v["ok"]) or approve_all
        stem = normalize_stem(o)
        base = f"{stem}{os.path.splitext(tg)[1].lower()}"
        if ok:
            shutil.copy2(o, os.path.join(o_dir, f"{stem}{os.path.splitext(o)[1].lower()}"))
            shutil.copy2(tg, os.path.join(t_dir, base))
            kept += 1
        else:
            shutil.copy2(tg, os.path.join(r_dir, base))
            rejected += 1
        rows.append({"stem": stem, "original": os.path.abspath(o), "target": os.path.abspath(tg),
                     "status": "kept" if ok else "rejected", "reasons": ";".join(v["reasons"]),
                     **{k: v["metrics"].get(k, "") for k in ("edge_corr", "aspect_diff", "sat_delta", "banding")}})
        items.append({"stem": stem, "original": os.path.abspath(o), "target": os.path.abspath(tg),
                      "status": "kept" if ok else "rejected", "reasons": v["reasons"], "metrics": v["metrics"]})

    manifest = {"originals_dir": os.path.abspath(originals_dir),
                "targets_dir": os.path.abspath(targets_dir), "out_dir": os.path.abspath(out_dir),
                "pairs": len(pairs), "kept": kept, "rejected": rejected,
                "validated": do_validate, "approve_all": approve_all, "items": items}
    with open(os.path.join(out_dir, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
    _write_labels_csv(rows, os.path.join(out_dir, "labels.csv"))
    return manifest


def _write_labels_csv(rows: list[dict], path: str) -> None:
    import csv
    cols = ["stem", "original", "target", "status", "reasons", "edge_corr", "aspect_diff", "sat_delta", "banding"]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})
