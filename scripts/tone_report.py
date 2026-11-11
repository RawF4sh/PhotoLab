"""Tone report — the objective pass/fail numbers for the dark-RAW problem.

Measures every source photo, its AutoEdit output and (for RAW) the camera's own embedded JPEG
preview, then counts the failures that matter: dark photos made darker, bright photos pushed into
clipping, outputs whose bottom band stays dark, and how far each result sits from the camera
render. Every later engine change is judged by this table, not by eye.

  # A) judge a finished batch from its sidecars (`autoedit run SRC OUT` wrote OUT/*.autoedit.json)
  PYTHONPATH=src python3 scripts/tone_report.py SRC --out OUT --csv report.csv --json report.json

  # B) render in memory (nothing written) — the quick engine gate while tuning rules
  PYTHONPATH=src python3 scripts/tone_report.py SRC --render --backend stub --raw-mode none --limit 24

`--fail-on-regression` fails closed for clipping, darkness, measurable artifact or integrity
failures and for incomplete reports. Export reports reopen pixels and verify source hashes.
Safe render mode records remediation; `--unsafe-control` measures the unchanged old pipeline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autoedit import batch, tonemetrics as tm  # noqa: E402
from autoedit.contract import GlobalStatsModel, RegionStatsModel, RunConfig  # noqa: E402
from autoedit.imageio import RAW_MODES, camera_preview, downscale_long_edge, load_image, raw_opts_from  # noqa: E402
from autoedit.quality import assess_pixels, load_quality_config  # noqa: E402


def _hash(path):
    with open(path, "rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def _finite(value):
    if isinstance(value, dict):
        return all(_finite(v) for v in value.values())
    if isinstance(value, list):
        return all(_finite(v) for v in value)
    return not isinstance(value, (int, float)) or np.isfinite(value)


def _records(src_dir, out_dir, errors=None, metadata=None):
    """Return explicit expected capture records; malformed manifests never hide omissions."""
    metadata = metadata if metadata is not None else {}
    manifest = os.path.join(out_dir, "run.json")
    if not os.path.isfile(manifest):
        try:
            records = [{"source": p} for p in batch.list_images(src_dir)]
        except Exception as exc:
            _record_error(src_dir, exc, errors)
            records = []
        metadata.update(mode="legacy", expected=len(records), manifest_status="absent")
        return records
    metadata.update(mode="manifest", manifest_path=manifest, manifest_status="invalid", expected=0)
    def invalid(message):
        _record_error(manifest, ValueError("manifest integrity: " + message), errors)
    try:
        with open(manifest) as fh:
            run = json.load(fh)
        if not isinstance(run, dict):
            raise ValueError("run manifest must be an object")
        if not _finite(run):
            raise ValueError("run manifest contains nonfinite numbers")
    except Exception as exc:
        _record_error(manifest, exc, errors)
        return []
    metadata.update(manifest_status=run.get("status", "complete_from_boolean" if run.get("complete") is True else "missing"),
                    manifest_declared_complete=run.get("complete"))
    expected = run.get("expected")
    expected_valid = type(expected) is int and expected > 0
    if not expected_valid:
        invalid("expected must be a positive capture count")
    # Merged native-run manifests declare completeness with a boolean and omit
    # status. Accept that schema only after the same full count/identity checks;
    # an explicitly unfinished status always contradicts complete=true.
    if run.get("complete") is not True or ("status" in run and run["status"] != "complete"):
        invalid("run is incomplete: complete=true and no contradictory status are required")
    if "exit_codes" in run and (not isinstance(run["exit_codes"], list) or
                                any(type(code) is not int or code != 0 for code in run["exit_codes"])):
        invalid("aggregate worker exit codes contain failure or invalid values")
    if run.get("source_errors"):
        invalid("run records source integrity errors")
    for field in ("failed", "processed"):
        if field in run and (type(run[field]) is not int or
                             run[field] != (0 if field == "failed" else expected)):
            invalid(f"{field} count contradicts an accepted complete run")
    selected = run.get("selected_inputs")
    records = next((run[key] for key in ("entries", "items", "outputs") if key in run), None)
    if not isinstance(selected, list):
        invalid("selected_inputs must be a list; expected capture coverage cannot be established")
        selected = []
    if not isinstance(records, list):
        invalid("entries must be a list")
        records = []
    metadata.update(expected=expected if expected_valid else len(selected),
                    selected_count=len(selected), record_count=len(records))
    if expected_valid and (len(selected) != expected or len(records) != expected):
        invalid(f"expected {expected} captures, selected {len(selected)}, recorded {len(records)}")

    def indexed(values, label):
        index, duplicate = {}, set()
        for position, value in enumerate(values):
            cid = value.get("capture_id") if isinstance(value, dict) else None
            if not isinstance(cid, str) or not cid.strip():
                invalid(f"{label}[{position}] has no valid capture_id")
                continue
            if cid in index:
                duplicate.add(cid)
            else:
                index[cid] = value
        if duplicate:
            invalid(f"duplicate capture IDs in {label}: {', '.join(sorted(duplicate))}")
        return index, duplicate

    selected_by_id, duplicate_selected = indexed(selected, "selected_inputs")
    records_by_id, duplicate_records = indexed(records, "entries")
    unexpected = set(records_by_id) - set(selected_by_id)
    if unexpected:
        invalid("unselected capture entries: " + ", ".join(sorted(unexpected)))
    for key in ("output", "sidecar"):
        owners = {}
        for cid, record in records_by_id.items():
            path = record.get(key)
            if isinstance(path, str) and path:
                path = os.path.realpath(_resolve(path, out_dir))
                if path in owners:
                    invalid(f"{key} artifact reused by {owners[path]} and {cid}")
                owners[path] = cid
    result = []
    for cid, selection in selected_by_id.items():
        record = dict(records_by_id.get(cid, {}))
        record["_selected_input"] = selection
        record["capture_id"] = cid
        if cid not in records_by_id:
            record["_record_error"] = "selected capture has no recorded output entry"
        elif cid in duplicate_selected or cid in duplicate_records:
            record["_record_error"] = "duplicate capture ID cannot identify a unique expected output"
        # Missing records retain their selected source identity for error reporting.
        record.setdefault("source", selection.get("source", ""))
        result.append(record)
    return result


def _verify_selection(record, src_dir, out_dir):
    """Verify immutable selected sources, companions, and explicit export mappings."""
    selected = record.get("_selected_input")
    if selected is None:  # no-manifest legacy reports still require the sidecar source hash
        return []
    if record.get("status") != "accepted":
        raise ValueError("capture status is not accepted: " + str(record.get("status", "missing")))
    for key in ("source", "source_sha256", "output", "output_sha256", "sidecar", "sidecar_sha256"):
        if not isinstance(record.get(key), str) or not record[key]:
            raise ValueError("manifest capture is missing " + key)
    if (not isinstance(selected.get("source"), str) or not selected["source"]
            or not isinstance(selected.get("source_sha256"), str) or not selected["source_sha256"]):
        raise ValueError("selected capture lacks source path or source hash")
    if (os.path.realpath(_resolve(record["source"], src_dir)) !=
            os.path.realpath(_resolve(selected["source"], src_dir))
            or record["source_sha256"] != selected["source_sha256"]):
        raise ValueError("entry source identity disagrees with selected capture")
    files = [(selected["source"], selected["source_sha256"])]
    companions = selected.get("companions", [])
    if not isinstance(companions, list):
        raise ValueError("selected companions must be a list")
    for companion in companions:
        if not isinstance(companion, dict):
            raise ValueError("invalid selected source companion")
        files.append((companion.get("path"), companion.get("sha256")))
    if selected.get("camera_reference"):
        files.append((selected["camera_reference"], selected.get("camera_reference_sha256")))
    verified = []
    for path, expected in files:
        if not isinstance(path, str) or not path or not isinstance(expected, str) or not expected:
            raise ValueError("selected source companion integrity unavailable: path/hash missing")
        path = _resolve(path, src_dir)
        if _hash(path) != expected:
            raise ValueError("selected source or companion hash changed: " + path)
        verified.append((path, expected))
    return verified


def _resolve(value, root):
    return value if os.path.isabs(value) else os.path.join(root, value)


def _cam(path: str, camera: bool) -> dict | None:
    if not camera:
        return None
    rgb = camera_preview(path, max_px=1024)
    return tm.describe(rgb) if rgb is not None else None


def _record_error(path: str, exc: Exception, errors: list[dict] | None) -> None:
    path = os.fspath(path) if isinstance(path, (str, os.PathLike)) else repr(path)
    error = {"name": os.path.basename(path), "path": path, "error": f"{type(exc).__name__}: {exc}"}
    if errors is not None:
        errors.append(error)
    print(f"{error['name']} ERROR: {error['error']}", file=sys.stderr)


def _review_fields(src: GlobalStatsModel, out: GlobalStatsModel, reason: str) -> dict:
    before, after = src.rgb_highlight_clip_any, out.rgb_highlight_clip_any
    return {"src_rgb_highlight_clip_any": float(before),
            "out_rgb_highlight_clip_any": float(after),
            "d_rgb_highlight_clip_any": float(after - before), "review_reason": reason}


def rows_from_sidecars(src_dir: str, out_dir: str, camera: bool = True, thr: dict | None = None,
                       errors: list[dict] | None = None, metadata: dict | None = None) -> list[dict]:
    """Rows for a batch already written by `autoedit run` (reads the sidecars' pre/post stats)."""
    rows: list[dict] = []
    for record in _records(src_dir, out_dir, errors=errors, metadata=metadata):
        p = os.path.join(out_dir, "run.json")
        try:
            started = time.perf_counter()
            p = record.get("source") or record.get("source_path") or record.get("path") or ""
            if not isinstance(p, str) or not p:
                raise ValueError("capture is missing a valid source path")
            p = _resolve(p, src_dir) if not os.path.isfile(p) else p
            if record.get("_record_error"):
                raise ValueError(record["_record_error"])
            selected_files = _verify_selection(record, src_dir, out_dir)
            stem = os.path.splitext(os.path.basename(p))[0]
            sidecar = record.get("sidecar") or record.get("sidecar_path") or f"{stem}.autoedit.json"
            sidecar = _resolve(sidecar, out_dir)
            before_hash = _hash(p)
            expected_hash = record.get("source_sha256") or record.get("sha256")
            if expected_hash and before_hash != expected_hash:
                raise ValueError("source-file modification: source hash differs from run manifest")
            with open(sidecar) as fh:
                d = json.load(fh)
            if not expected_hash:
                expected_hash = (d.get("audit") or {}).get("source_sha256")
            if not expected_hash:
                raise ValueError("source integrity unavailable: legacy sidecar has no recorded source hash")
            if before_hash != expected_hash:
                raise ValueError("source-file modification: source hash differs from sidecar")
            if record.get("sidecar_sha256") and _hash(sidecar) != record["sidecar_sha256"]:
                raise ValueError("corrupt sidecar: hash differs from run manifest")
            if not _finite(d):
                raise ValueError("sidecar contains invalid or nonfinite numbers")
            if not d.get("global_stats") or not d.get("post_edit_stats"):
                raise ValueError("sidecar is missing source or post-edit stats")
            pre = GlobalStatsModel(**d["global_stats"])
            post = GlobalStatsModel(**d["post_edit_stats"])
            models = [pre, post] + [RegionStatsModel(**v) for key in ("region_stats", "post_region_stats")
                                   for v in d.get(key, [])]
            if not all(_finite(model.model_dump()) for model in models):
                raise ValueError("sidecar statistics contain nonfinite values")
            output = record.get("output") or record.get("output_path") or record.get("image_out")
            if output:
                output = _resolve(output, out_dir)
            else:
                candidates = [os.path.join(out_dir, f"{stem}.{suffix}")
                              for suffix in ("jpg", "jpeg", "png", "tif", "tiff")]
                existing = [path for path in candidates if os.path.isfile(path)]
                if len(existing) != 1:
                    raise ValueError("missing or ambiguous expected image output")
                output = existing[0]
            if record.get("output_sha256") and _hash(output) != record["output_sha256"]:
                raise ValueError("output hash differs from run manifest")
            # Re-open the actual export; trusted-looking proxy stats cannot establish safety.
            source_rgb = load_image(p, decode={"raw_mode": "none"}).rgb
            exported = load_image(output).rgb
            saved_audit = d.get("audit") or {}
            masks_path = saved_audit.get("masks_path") or record.get("masks_path")
            masks = {}
            if masks_path:
                masks_path = _resolve(masks_path, out_dir)
                digest = saved_audit.get("masks_sha256") or record.get("masks_sha256")
                if not digest or _hash(masks_path) != digest:
                    raise ValueError("missing or corrupt mask provenance")
                with np.load(masks_path, allow_pickle=False) as archive:
                    masks = {name: archive[name] for name in archive.files}
            assessment = assess_pixels(source_rgb, exported, masks=masks, thresholds=thr)
            if _hash(p) != before_hash:
                raise ValueError("source-file modification during assessment")
            for selected_path, selected_hash in selected_files:
                if _hash(selected_path) != selected_hash:
                    raise ValueError("selected source or companion changed during assessment: " + selected_path)
            src = assessment["metrics"].get("source", tm.from_global(pre))
            out = assessment["metrics"].get("output", tm.from_global(post))
            row = tm.row_for(os.path.basename(p), src, out, _cam(p, camera), **(thr or {}))
            row.update(decode_ev=float(d.get("decode_ev", 0.0)), quality_score=d.get("quality_score"),
                       needs_review=d.get("needs_review"),
                       exposure=float((d.get("global_settings") or {}).get("exposure", 0.0)),
                       shadows=float((d.get("global_settings") or {}).get("shadows", 0.0)))
            row.update(_review_fields(pre, post, d.get("review_reason") or ""))
            row.update(capture_id=record.get("capture_id"), report_status="assessed",
                       audit_passed=assessment["passed"], audit_failures=assessment["failures"],
                       audit=assessment, source_sha256=before_hash,
                       source_integrity="verified_run_hash" if expected_hash else "verified_during_report_only",
                       source_format=os.path.splitext(p)[1].lower(), decode_method="none",
                       timing_ms=(time.perf_counter()-started)*1000,
                       stage_timings_ms=d.get("stage_timings_ms", {}),
                       region_stats=d.get("region_stats", []), post_region_stats=d.get("post_region_stats", []),
                       output_path=output, metrics_scope="sidecar proxy; audit reopens exported pixels")
        except Exception as exc:  # missing/invalid sidecars must not hide a partial corpus
            _record_error(p, exc, errors)
            continue
        rows.append(row)
    return rows


def rows_by_render(src_dir: str, cfg: RunConfig, limit: int | None = None, max_px: int = 1024,
                   camera: bool = True, thr: dict | None = None, quiet: bool = False,
                   errors: list[dict] | None = None, unsafe_control: bool = False) -> list[dict]:
    """Rows by rendering each photo in memory on a `max_px` proxy (no files written)."""
    from autoedit.pipeline import Pipeline

    pipe = Pipeline.build(cfg) if unsafe_control else None
    engine = None
    if not unsafe_control:
        from autoedit.quality_runner import build_safe_engine
        engine = build_safe_engine(cfg, workflow="current")
    images = batch.list_images(src_dir)
    if limit:
        images = images[:limit]
    rows: list[dict] = []
    for i, p in enumerate(images, start=1):
        try:
            started = time.perf_counter()
            before_hash = _hash(p)
            img = load_image(p, decode=raw_opts_from(cfg))
            work = downscale_long_edge(img, max_px)
            common = (work if cfg.raw_mode == "none" else
                      downscale_long_edge(load_image(p, decode={"raw_mode": "none"}), max_px))
            if unsafe_control:
                rgb, res = pipe.process_one(p, do_apply=True, image=work)
            else:
                from autoedit.quality_runner import render_safe
                rgb, res = render_safe(p, cfg, image=work, common_source=common, engine=engine)
            assessment = assess_pixels(common, rgb, masks=getattr(engine, "last_masks", {}), thresholds=thr)
            if _hash(p) != before_hash:
                raise ValueError("source-file modification during render")
            src = tm.from_global(res.global_stats)
            out = tm.from_global(res.post_edit_stats)
            row = tm.row_for(os.path.basename(p), src, out, _cam(p, camera), **(thr or {}))
            row.update(decode_ev=float(res.decode_ev), quality_score=res.quality_score,
                       needs_review=res.needs_review,
                       exposure=float(res.global_settings.get("exposure", 0.0)),
                       shadows=float(res.global_settings.get("shadows", 0.0)))
            row.update(_review_fields(res.global_stats, res.post_edit_stats, res.review_reason))
            row.update(audit_passed=assessment["passed"], audit_failures=assessment["failures"], audit=assessment,
                       source_sha256=before_hash, source_format=os.path.splitext(p)[1].lower(),
                       decode_method=cfg.raw_mode, timing_ms=(time.perf_counter()-started)*1000,
                       stage_timings_ms=res.stage_timings_ms, region_stats=[r.model_dump() for r in res.region_stats],
                       post_region_stats=[r.model_dump() for r in res.post_region_stats],
                       metrics_scope="floating render proxy; native exports require --out",
                       safety_fallback=res.audit.get("remediation_factor", 1.0))
        except Exception as e:  # one bad photo never aborts the report
            _record_error(p, e, errors)
            continue
        rows.append(row)
        if not quiet:
            print(f"[{i}/{len(images)}] {os.path.basename(p)}  fg {src['fg_p50']:.2f}→{out['fg_p50']:.2f}"
                  f"  bottom {src['band_bottom_p50']:.2f}→{out['band_bottom_p50']:.2f}"
                  f"{'  DARK→DARKER' if row['dark_made_darker'] else ''}"
                  f"{'  CLIPPED' if row['bright_pushed_to_clip'] else ''}")
    return rows


def print_table(rows: list[dict], summary: dict) -> None:
    hdr = (f"{'photo':30s} {'cat':9s} {'fg src→out':11s} {'bottom src→out':15s} {'dark% src→out':14s} "
           f"{'+clip':6s} {'+RGB':6s} {'EV vs cam':9s} flags / review reason")
    print(hdr)
    for r in rows:
        ev = f"{r['ev_vs_camera']:+.2f}" if "ev_vs_camera" in r else "   -  "
        flags = (("DARK→DARKER " if r["dark_made_darker"] else "")
                 + ("CLIP " if r["bright_pushed_to_clip"] else "")
                 + ",".join(r.get("audit_failures", [])))
        print(f"{r['name'][:30]:30s} {r['category'][:9]:9s} "
              f"{r['src_fg_p50']:.2f}→{r['out_fg_p50']:.2f}   "
              f"{r['src_band_bottom_p50']:.2f}→{r['out_band_bottom_p50']:.2f}       "
              f"{100 * r['src_dark_cov']:4.0f}→{100 * r['out_dark_cov']:<4.0f}     "
              f"{100 * r['d_clip_hi']:+5.1f}% {100 * r.get('d_rgb_highlight_clip_any', 0):+5.1f}% "
              f"{ev:9s} {flags} {r.get('review_reason', '')}")
    print("\nSummary:")
    for k, v in summary.items():
        print(f"  {k:28s} {v}")


def main() -> int:
    ap = argparse.ArgumentParser(description="AutoEdit tone report (objective dark-RAW gate)")
    ap.add_argument("src", help="folder of source photos (RAW or JPEG)")
    ap.add_argument("--out", help="output folder written by `autoedit run` (read its sidecars)")
    ap.add_argument("--render", action="store_true", help="render in memory instead of reading sidecars")
    ap.add_argument("--unsafe-control", action="store_true", help="measure unremediated historical pipeline")
    ap.add_argument("--backend", choices=["stub", "sam3"], default="stub")
    ap.add_argument("--raw-mode", dest="raw_mode", choices=list(RAW_MODES), default=None)
    ap.add_argument("--style", default=None)
    ap.add_argument("--intensity", type=float, default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--max-px", dest="max_px", type=int, default=1024)
    ap.add_argument("--no-camera", dest="camera", action="store_false", help="skip the camera-preview arm")
    defaults = load_quality_config()
    ap.add_argument("--dark-source", dest="dark_source", type=float, default=defaults["dark_source"])
    ap.add_argument("--dark-regress", dest="dark_regress", type=float, default=defaults["dark_regress"])
    ap.add_argument("--clip-budget", dest="clip_budget", type=float, default=defaults["clip_budget"])
    ap.add_argument("--shadow-clip-budget", type=float, default=defaults["shadow_clip_budget"])
    ap.add_argument("--csv", help="write per-photo rows here")
    ap.add_argument("--json", help="write {summary, rows, errors} here")
    ap.add_argument("--fail-on-regression", dest="fail", action="store_true",
                    help="exit nonzero for tone regressions or any source that cannot be assessed")
    args = ap.parse_args()

    thr = dict(dark_source=args.dark_source, dark_regress=args.dark_regress, clip_budget=args.clip_budget,
               shadow_clip_budget=args.shadow_clip_budget)
    errors: list[dict] = []
    metadata: dict = {}
    if args.render:
        kw = {"backend": args.backend}
        if args.raw_mode:
            kw["raw_mode"] = args.raw_mode
        if args.style:
            kw["style"] = args.style
        if args.intensity is not None:
            kw["intensity"] = args.intensity
        rows = rows_by_render(args.src, RunConfig(**kw), limit=args.limit, max_px=args.max_px,
                              camera=args.camera, thr=thr, errors=errors, unsafe_control=args.unsafe_control)
    elif args.out:
        rows = rows_from_sidecars(args.src, args.out, camera=args.camera, thr=thr, errors=errors, metadata=metadata)
    else:
        ap.error("give --out OUT_DIR (read sidecars) or --render")
    summary = tm.summarize(rows)
    summary.update(expected=metadata.get("expected", len(rows) + len(errors)),
                   errors=len(errors), complete=bool(rows) and not errors)
    if metadata.get("mode") == "manifest":
        metadata["manifest_integrity"] = "complete" if summary["complete"] else "incomplete"
        summary["manifest_status"] = metadata["manifest_integrity"]
    print_table(rows, summary)
    if args.csv:
        csv_rows = [dict(row, report_complete=summary["complete"]) for row in rows]
        csv_rows.extend(dict(error, report_status="error", report_complete=False) for error in errors)
        tm.write_csv(csv_rows or [{"report_status": "empty", "report_complete": False}], args.csv)
        print(f"wrote {args.csv}")
    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"summary": summary, "rows": rows, "errors": errors, "manifest": metadata}, fh, indent=1, default=float)
        print(f"wrote {args.json}")
    if not rows:
        print("no rows (no sidecars found / nothing rendered)", file=sys.stderr)
        return 2
    bad = (summary["dark_made_darker"] + summary["bright_pushed_to_clip"]
           + summary["shadow_pushed_to_clip"] + summary["hard_failures"])
    if args.fail and (bad or errors):
        print(f"\nGATE FAILED: {summary['dark_made_darker']} dark→darker, "
              f"{summary['bright_pushed_to_clip']} pushed into clipping, "
              f"{summary['shadow_pushed_to_clip']} shadow clipping, "
              f"{summary['hard_failures']} images failed pixel safety, "
              + (f"{len(errors)} integrity/assessment errors across {summary['expected']} selected captures"
               if metadata.get("mode") == "manifest" else
               f"{len(errors)} of {summary['expected']} sources could not be assessed"), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
