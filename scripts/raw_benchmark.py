"""RAW-development benchmark — rank the decode candidates on real RAW files.

For every RAW in a folder and every `raw_mode` (imageio.RAW_MODES), decode (quarter-res by
default for speed), measure, and compare against the camera's own embedded JPEG preview — the
free reference every RAW carries. Writes a per-file×mode CSV and a JSON summary that RANKS the
modes and names a recommendation. It never changes the product default (defaults.toml
`[decode] raw_mode`); flipping it is a human decision.

  PYTHONPATH=src python3 scripts/raw_benchmark.py curated-dolomites-summer-2026/selected_raw --out-dir out-rawbench
  # also run the (stub) editing pipeline on each candidate to count dark→darker regressions (slow):
  PYTHONPATH=src python3 scripts/raw_benchmark.py RAW_DIR --edit --limit 24

Ranking (per mode):
  median |EV gap| to camera   how far the decode sits from the camera render (lower = better)
  clip violation rate         files whose highlight clipping exceeds the camera's by > clip_budget
  dark recovery               median bottom-band gap to camera on the `dark` category
  consistency                 spread of the median EV gap across scene categories (lower = better)
  outliers                    files with |EV gap| > 0.75
  regressions (--edit)        dark_made_darker after the pipeline ran on that decode
A mode is eligible when its clip violation rate ≤ 5% and it decoded every file; the eligible mode
with the smallest median |EV gap| is recommended.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autoedit import batch, tonemetrics as tm  # noqa: E402
from autoedit.contract import RunConfig  # noqa: E402
from autoedit.imageio import RAW_MODES, camera_preview, downscale_long_edge, is_raw, load_image  # noqa: E402


def bench(raw_dir: str, modes: tuple[str, ...], limit: int | None = None, half: bool = True,
          clip_budget: float = 0.005, edit: bool = False, backend: str = "stub", max_px: int = 1024,
          quiet: bool = False) -> tuple[list[dict], dict]:
    files = [p for p in batch.list_images(raw_dir) if is_raw(p)]
    if limit:
        files = files[:limit]
    if not files:
        raise SystemExit(f"no RAW files in {raw_dir}")

    pipes: dict = {}
    if edit:
        from autoedit.pipeline import Pipeline

        for m in modes:
            pipes[m] = Pipeline.build(RunConfig(backend=backend, raw_mode=m))

    rows: list[dict] = []
    failures: dict[str, int] = {m: 0 for m in modes}
    t0 = time.perf_counter()
    for i, p in enumerate(files, start=1):
        name = os.path.basename(p)
        cam_rgb = camera_preview(p, max_px=1024)
        cam = tm.describe(cam_rgb) if cam_rgb is not None else None
        none_clip = None
        for m in modes:
            try:
                img = load_image(p, decode={"raw_mode": m, "half_size": half})
            except Exception as e:
                failures[m] += 1
                print(f"[{i}/{len(files)}] {name} {m}: DECODE ERROR {e}", file=sys.stderr)
                continue
            dev = tm.describe(img.rgb)
            if m == "none":
                none_clip = dev["clip_hi"]
            row = {"name": name, "category": tm.category_of(name), "mode": m, "decode_ev": img.decode_ev}
            row.update({f"dev_{k}": v for k, v in dev.items()})
            if cam is not None:
                row.update({f"cam_{k}": v for k, v in cam.items()})
                row["ev_vs_camera"] = tm.ev_gap(dev["p50"], cam["p50"])
                row["cam_d_bottom"] = dev["band_bottom_p50"] - cam["band_bottom_p50"]
                row["cam_d_clip_hi"] = dev["clip_hi"] - cam["clip_hi"]
                row["clip_violation"] = bool(row["cam_d_clip_hi"] > clip_budget)
            row["mean_below_025"] = bool(dev["mean"] < 0.25)
            if none_clip is not None:
                row["d_clip_vs_none"] = dev["clip_hi"] - none_clip
            if edit:
                try:
                    _, res = pipes[m].process_one(p, do_apply=True, image=downscale_long_edge(img, max_px))
                    out = tm.from_global(res.post_edit_stats)
                    cmp = tm.compare(tm.from_global(res.global_stats), out, cam, clip_budget=clip_budget)
                    row.update({f"out_{k}": v for k, v in out.items()})
                    row.update({k: cmp[k] for k in ("dark_made_darker", "bright_pushed_to_clip", "d_mean")})
                    row["exposure"] = float(res.global_settings.get("exposure", 0.0))
                except Exception as e:
                    row["edit_error"] = str(e)
            rows.append(row)
        if not quiet:
            got = [r for r in rows if r["name"] == name]
            evs = " ".join(f"{r['mode']}={r.get('ev_vs_camera', float('nan')):+.2f}" for r in got)
            print(f"[{i}/{len(files)}] {name}  EV gap to camera: {evs}")
    summary = rank(rows, modes, failures, clip_budget)
    summary["files"] = len(files)
    summary["duration_s"] = round(time.perf_counter() - t0, 1)
    return rows, summary


def rank(rows: list[dict], modes: tuple[str, ...], failures: dict[str, int], clip_budget: float) -> dict:
    per_mode: dict[str, dict] = {}
    for m in modes:
        rs = [r for r in rows if r["mode"] == m]
        evs = [r["ev_vs_camera"] for r in rs if "ev_vs_camera" in r]
        cats: dict[str, list[float]] = {}
        for r in rs:
            if "ev_vs_camera" in r and r["category"]:
                cats.setdefault(r["category"], []).append(r["ev_vs_camera"])
        cat_medians = {c: float(np.median(v)) for c, v in cats.items() if len(v) >= 2}
        dark_bottom = [r["cam_d_bottom"] for r in rs if r["category"] == "dark" and "cam_d_bottom" in r]
        entry = {
            "n": len(rs),
            "decode_failures": failures.get(m, 0),
            "median_abs_ev_vs_camera": float(np.median([abs(v) for v in evs])) if evs else None,
            "median_ev_vs_camera": float(np.median(evs)) if evs else None,
            "iqr_ev_vs_camera": [float(np.percentile(evs, 25)), float(np.percentile(evs, 75))] if evs else None,
            "clip_violation_rate": (sum(1 for r in rs if r.get("clip_violation")) / len(rs)) if rs else None,
            "mean_below_025": sum(1 for r in rs if r.get("mean_below_025")),
            "dark_recovery_bottom_vs_camera": float(np.median(dark_bottom)) if dark_bottom else None,
            "consistency_spread": (max(cat_medians.values()) - min(cat_medians.values())) if len(cat_medians) >= 2 else None,
            "category_median_ev": cat_medians,
            "outliers_gt_075": sum(1 for v in evs if abs(v) > 0.75),
            "median_decode_ev": float(np.median([r["decode_ev"] for r in rs])) if rs else None,
        }
        if any("dark_made_darker" in r for r in rs):
            entry["dark_made_darker"] = sum(1 for r in rs if r.get("dark_made_darker"))
            entry["bright_pushed_to_clip"] = sum(1 for r in rs if r.get("bright_pushed_to_clip"))
            entry["edit_errors"] = sum(1 for r in rs if r.get("edit_error"))
        per_mode[m] = entry

    eligible = [m for m, e in per_mode.items()
                if e["n"] and e["decode_failures"] == 0
                and e["clip_violation_rate"] is not None and e["clip_violation_rate"] <= 0.05
                and e["median_abs_ev_vs_camera"] is not None]
    eligible.sort(key=lambda m: (per_mode[m]["median_abs_ev_vs_camera"], per_mode[m]["outliers_gt_075"]))
    ranking = sorted(per_mode, key=lambda m: (m not in eligible,
                                              per_mode[m]["median_abs_ev_vs_camera"] or 9.0))
    rec = eligible[0] if eligible else None
    return {
        "clip_budget": clip_budget,
        "per_mode": per_mode,
        "ranking": ranking,
        "eligible": eligible,
        "recommendation": rec,
        "note": ("Recommendation is advisory: defaults.toml [decode] raw_mode is NOT changed by this "
                 "script. Eligibility = decoded every file AND clip violation rate ≤ 5%."),
    }


def print_summary(summary: dict) -> None:
    print(f"\nRAW development benchmark — {summary['files']} files, {summary['duration_s']}s")
    print(f"{'mode':22s} {'med|EV|':8s} {'medEV':7s} {'IQR':16s} {'clip>':6s} {'<0.25':6s} "
          f"{'darkΔbot':9s} {'spread':7s} {'outl':5s} {'fail':5s} {'dark→darker':12s}")
    for m in summary["ranking"]:
        e = summary["per_mode"][m]
        iqr = f"[{e['iqr_ev_vs_camera'][0]:+.2f},{e['iqr_ev_vs_camera'][1]:+.2f}]" if e["iqr_ev_vs_camera"] else "-"
        f = lambda v, fmt: (fmt % v) if v is not None else "-"  # noqa: E731
        print(f"{m:22s} {f(e['median_abs_ev_vs_camera'], '%6.2f'):8s} {f(e['median_ev_vs_camera'], '%+6.2f'):7s} "
              f"{iqr:16s} {f(e['clip_violation_rate'], '%5.1f%%') if e['clip_violation_rate'] is None else f'{100 * e['clip_violation_rate']:5.1f}%':6s} "
              f"{e['mean_below_025']:<6d} {f(e['dark_recovery_bottom_vs_camera'], '%+8.3f'):9s} "
              f"{f(e['consistency_spread'], '%6.2f'):7s} {e['outliers_gt_075']:<5d} {e['decode_failures']:<5d} "
              f"{str(e.get('dark_made_darker', '-')):12s}")
    print(f"\neligible (clip ≤ {100 * summary['clip_budget']:.1f}% budget violated on ≤ 5% of files): {summary['eligible']}")
    print(f"RECOMMENDATION (advisory, default unchanged): {summary['recommendation']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Benchmark RAW development candidates against the camera preview")
    ap.add_argument("raw_dir")
    ap.add_argument("--modes", default="all", help="comma list of raw modes, or 'all'")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--full", action="store_true", help="full-resolution demosaic (default: quarter-res)")
    ap.add_argument("--clip-budget", dest="clip_budget", type=float, default=0.005)
    ap.add_argument("--edit", action="store_true", help="also run the pipeline per mode (slow)")
    ap.add_argument("--backend", choices=["stub", "sam3"], default="stub")
    ap.add_argument("--max-px", dest="max_px", type=int, default=1024)
    ap.add_argument("--out-dir", dest="out_dir", default="out-rawbench")
    args = ap.parse_args()

    modes = tuple(RAW_MODES) if args.modes == "all" else tuple(m.strip() for m in args.modes.split(","))
    rows, summary = bench(args.raw_dir, modes, limit=args.limit, half=not args.full,
                          clip_budget=args.clip_budget, edit=args.edit, backend=args.backend,
                          max_px=args.max_px)
    os.makedirs(args.out_dir, exist_ok=True)
    tm.write_csv(rows, os.path.join(args.out_dir, "raw_benchmark.csv"))
    with open(os.path.join(args.out_dir, "raw_benchmark.json"), "w") as fh:
        json.dump({"summary": summary, "rows": rows}, fh, indent=1, default=float)
    print_summary(summary)
    print(f"wrote {args.out_dir}/raw_benchmark.csv + raw_benchmark.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
