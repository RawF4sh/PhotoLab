"""Tone report — the objective pass/fail numbers for the dark-RAW problem.

Measures every source photo, its AutoEdit output and (for RAW) the camera's own embedded JPEG
preview, then counts the failures that matter: dark photos made darker, bright photos pushed into
clipping, outputs whose bottom band stays dark, and how far each result sits from the camera
render. Every later engine change is judged by this table, not by eye.

  # A) judge a finished batch from its sidecars (`autoedit run SRC OUT` wrote OUT/*.autoedit.json)
  PYTHONPATH=src python3 scripts/tone_report.py SRC --out OUT --csv report.csv --json report.json

  # B) render in memory (nothing written) — the quick engine gate while tuning rules
  PYTHONPATH=src python3 scripts/tone_report.py SRC --render --backend stub --raw-mode none --limit 24

`--fail-on-regression` exits 1 when any dark_made_darker / bright_pushed_to_clip row exists, so the
report doubles as an unattended gate.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autoedit import batch, tonemetrics as tm  # noqa: E402
from autoedit.contract import GlobalStatsModel, RunConfig  # noqa: E402
from autoedit.imageio import RAW_MODES, camera_preview, downscale_long_edge, load_image, raw_opts_from  # noqa: E402


def _cam(path: str, camera: bool) -> dict | None:
    if not camera:
        return None
    rgb = camera_preview(path, max_px=1024)
    return tm.describe(rgb) if rgb is not None else None


def rows_from_sidecars(src_dir: str, out_dir: str, camera: bool = True, thr: dict | None = None) -> list[dict]:
    """Rows for a batch already written by `autoedit run` (reads the sidecars' pre/post stats)."""
    rows: list[dict] = []
    for p in batch.list_images(src_dir):
        stem = os.path.splitext(os.path.basename(p))[0]
        sidecar = os.path.join(out_dir, f"{stem}.autoedit.json")
        if not os.path.exists(sidecar):
            continue
        with open(sidecar) as fh:
            d = json.load(fh)
        if not d.get("global_stats") or not d.get("post_edit_stats"):
            continue
        src = tm.from_global(GlobalStatsModel(**d["global_stats"]))
        out = tm.from_global(GlobalStatsModel(**d["post_edit_stats"]))
        row = tm.row_for(os.path.basename(p), src, out, _cam(p, camera), **(thr or {}))
        row.update(decode_ev=float(d.get("decode_ev", 0.0)), quality_score=d.get("quality_score"),
                   needs_review=d.get("needs_review"),
                   exposure=float((d.get("global_settings") or {}).get("exposure", 0.0)),
                   shadows=float((d.get("global_settings") or {}).get("shadows", 0.0)))
        rows.append(row)
    return rows


def rows_by_render(src_dir: str, cfg: RunConfig, limit: int | None = None, max_px: int = 1024,
                   camera: bool = True, thr: dict | None = None, quiet: bool = False) -> list[dict]:
    """Rows by rendering each photo in memory on a `max_px` proxy (no files written)."""
    from autoedit.pipeline import Pipeline

    pipe = Pipeline.build(cfg)
    images = batch.list_images(src_dir)
    if limit:
        images = images[:limit]
    rows: list[dict] = []
    for i, p in enumerate(images, start=1):
        try:
            img = load_image(p, decode=raw_opts_from(cfg))
            work = downscale_long_edge(img, max_px)
            _, res = pipe.process_one(p, do_apply=True, image=work)
        except Exception as e:  # one bad photo never aborts the report
            print(f"[{i}/{len(images)}] {os.path.basename(p)} ERROR: {e}", file=sys.stderr)
            continue
        src = tm.from_global(res.global_stats)
        out = tm.from_global(res.post_edit_stats)
        row = tm.row_for(os.path.basename(p), src, out, _cam(p, camera), **(thr or {}))
        row.update(decode_ev=float(res.decode_ev), quality_score=res.quality_score,
                   needs_review=res.needs_review,
                   exposure=float(res.global_settings.get("exposure", 0.0)),
                   shadows=float(res.global_settings.get("shadows", 0.0)))
        rows.append(row)
        if not quiet:
            print(f"[{i}/{len(images)}] {os.path.basename(p)}  fg {src['fg_p50']:.2f}→{out['fg_p50']:.2f}"
                  f"  bottom {src['band_bottom_p50']:.2f}→{out['band_bottom_p50']:.2f}"
                  f"{'  DARK→DARKER' if row['dark_made_darker'] else ''}"
                  f"{'  CLIPPED' if row['bright_pushed_to_clip'] else ''}")
    return rows


def print_table(rows: list[dict], summary: dict) -> None:
    hdr = (f"{'photo':30s} {'cat':9s} {'fg src→out':11s} {'bottom src→out':15s} {'dark% src→out':14s} "
           f"{'+clip':6s} {'EV vs cam':9s} flags")
    print(hdr)
    for r in rows:
        ev = f"{r['ev_vs_camera']:+.2f}" if "ev_vs_camera" in r else "   -  "
        flags = ("DARK→DARKER " if r["dark_made_darker"] else "") + ("CLIP" if r["bright_pushed_to_clip"] else "")
        print(f"{r['name'][:30]:30s} {r['category'][:9]:9s} "
              f"{r['src_fg_p50']:.2f}→{r['out_fg_p50']:.2f}   "
              f"{r['src_band_bottom_p50']:.2f}→{r['out_band_bottom_p50']:.2f}       "
              f"{100 * r['src_dark_cov']:4.0f}→{100 * r['out_dark_cov']:<4.0f}     "
              f"{100 * r['d_clip_hi']:+5.1f}% {ev:9s} {flags}")
    print("\nSummary:")
    for k, v in summary.items():
        print(f"  {k:28s} {v}")


def main() -> int:
    ap = argparse.ArgumentParser(description="AutoEdit tone report (objective dark-RAW gate)")
    ap.add_argument("src", help="folder of source photos (RAW or JPEG)")
    ap.add_argument("--out", help="output folder written by `autoedit run` (read its sidecars)")
    ap.add_argument("--render", action="store_true", help="render in memory instead of reading sidecars")
    ap.add_argument("--backend", choices=["stub", "sam3"], default="stub")
    ap.add_argument("--raw-mode", dest="raw_mode", choices=list(RAW_MODES), default=None)
    ap.add_argument("--style", default=None)
    ap.add_argument("--intensity", type=float, default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--max-px", dest="max_px", type=int, default=1024)
    ap.add_argument("--no-camera", dest="camera", action="store_false", help="skip the camera-preview arm")
    ap.add_argument("--dark-source", dest="dark_source", type=float, default=0.30)
    ap.add_argument("--dark-regress", dest="dark_regress", type=float, default=0.01)
    ap.add_argument("--clip-budget", dest="clip_budget", type=float, default=0.005)
    ap.add_argument("--csv", help="write per-photo rows here")
    ap.add_argument("--json", help="write {summary, rows} here")
    ap.add_argument("--fail-on-regression", dest="fail", action="store_true",
                    help="exit 1 if any dark_made_darker / bright_pushed_to_clip row exists")
    args = ap.parse_args()

    thr = dict(dark_source=args.dark_source, dark_regress=args.dark_regress, clip_budget=args.clip_budget)
    if args.render:
        kw = {"backend": args.backend}
        if args.raw_mode:
            kw["raw_mode"] = args.raw_mode
        if args.style:
            kw["style"] = args.style
        if args.intensity is not None:
            kw["intensity"] = args.intensity
        rows = rows_by_render(args.src, RunConfig(**kw), limit=args.limit, max_px=args.max_px,
                              camera=args.camera, thr=thr)
    elif args.out:
        rows = rows_from_sidecars(args.src, args.out, camera=args.camera, thr=thr)
    else:
        ap.error("give --out OUT_DIR (read sidecars) or --render")
    if not rows:
        print("no rows (no sidecars found / nothing rendered)", file=sys.stderr)
        return 2
    summary = tm.summarize(rows)
    print_table(rows, summary)
    if args.csv:
        tm.write_csv(rows, args.csv)
        print(f"wrote {args.csv}")
    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"summary": summary, "rows": rows}, fh, indent=1, default=float)
        print(f"wrote {args.json}")
    bad = summary["dark_made_darker"] + summary["bright_pushed_to_clip"]
    if args.fail and bad:
        print(f"\nGATE FAILED: {summary['dark_made_darker']} dark→darker, "
              f"{summary['bright_pushed_to_clip']} pushed into clipping", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
