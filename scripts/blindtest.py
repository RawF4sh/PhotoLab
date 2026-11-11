"""Phase-2 blind-test harness — the go/kill gate from PLAN.md.

Renders every corpus photo several ways — **unedited** / a **global-auto baseline** / the
**camera**'s own render (the JPEG preview embedded in a RAW) / **AutoEdit** — into a per-photo,
shuffled, opaque-ID gallery plus a self-contained `index.html` you open in a browser to rank
blind (choose a preferred version or No preference). The page exports `results.csv`; `--score` then reveals the
win-rates with an exact binomial p (per photo AND per burst group). AutoEdit clearly beating the
baseline is the Phase-A preference gate; camera rendering is a separate reference competitor.

    # AutoEdit vs the global baseline, 8 photos per exposure stratum (dark / mid / bright)
    PYTHONPATH=src python3 scripts/blindtest.py RAW_DIR out-bt-baseline --backend sam3 --two-way --stratify 8
    # AutoEdit vs the camera render, same photos (same --seed → same stratified pick)
    PYTHONPATH=src python3 scripts/blindtest.py RAW_DIR out-bt-camera --backend sam3 --vs-camera --stratify 8
    open out-bt-baseline/index.html      # rank blind, then "Download results.csv" into that folder
    python3 scripts/blindtest.py --score out-bt-baseline/results.csv

Every newly rendered gallery also writes `metrics.csv` — the objective tone numbers (tonemetrics) for every
variant next to the blind picks — so a win can be checked against what actually moved.
`run.json` records the resolved config, config/source hashes and render settings. Pipeline
variants retain `photoNNN/slotN.autoedit.json` with mask-aware statistics and review decisions;
`metrics.csv` continues to compare all variants using the same whole-frame measurements.

The default baseline is the experimental global-first workflow. The old one-slider comparator
remains available as --baseline-workflow historical (or the historical method).

Pre-rendered benchmark outputs avoid decoding or editing originals again:
    python scripts/blindtest.py --comparison-manifest comparisons.json gallery-baseline \
        --methods baseline,autoedit --split holdout
    python scripts/blindtest.py --comparison-manifest comparisons.json gallery-camera \
        --methods camera,autoedit --split holdout

The HTML and encoded photos omit source filenames. Unlinked manifest.csv/run.json contain
method identities and provenance; inspect those only after ranking. New result CSVs carry a
content-bound gallery ID, and scoring rejects duplicates, invalid slots and mismatched IDs.
No-preference votes use best_slot='tie', count as answered photos and are excluded from
decisive binomial trials. Group majorities use split and actual group identity, never scene labels.
Camera comparisons exclude captures without a real camera reference.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import re
from pathlib import Path
import sys
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autoedit import __version__, batch, emit, paths, stats, tonemetrics as tm  # noqa: E402
from autoedit.cli import load_run_config  # noqa: E402
from autoedit.edit.ops import apply_ops  # noqa: E402
from autoedit.imageio import (Image, camera_preview, downscale_long_edge, load_image, luma,  # noqa: E402
                              raw_opts_from, save_image)
from autoedit.pipeline import Pipeline  # noqa: E402

METHODS = ("unedited", "baseline", "autoedit")  # the classic 3-way labels (hidden behind slots)
ALL_METHODS = ("unedited", "baseline", "historical", "camera", "autoedit")
STRATA = ("dark", "mid", "bright")


def make_config(configs_dir=None, backend=None, device=None, style=None, intensity=None,
                raw_mode=None, quality=None):
    """Build a RunConfig via the normal CLI loader, with everything defaulting to None so we only
    override what the harness exposes (the loader broke on the old stub's partial namespace)."""
    ns = argparse.Namespace()
    for k in ("config", "style", "prompt", "preset", "concepts", "look", "intensity",
              "reference", "reference_strength", "decider", "allow_cloud", "api_key", "llm_mode",
              "backend", "device", "raw_mode", "format", "quality", "analysis_long_edge", "emit",
              "compose", "vignette", "sharpen", "configs_dir"):
        setattr(ns, k, None)
    ns.configs_dir = configs_dir
    ns.backend = backend
    ns.device = device
    ns.style = style
    ns.intensity = intensity
    ns.raw_mode = raw_mode
    ns.quality = quality
    return load_run_config(ns)


_TRAIL = re.compile(r"(\d+)(?=\.[^.]+$)")


def burst_groups(paths_: list[str], gap: int = 2) -> dict[str, str]:
    """Conservative filename fallback; source directories and camera prefixes never mix.

    Prefer capture-manifest groups based on event and EXIF timestamps. This fallback cannot
    establish visual independence and is explicitly identified in the run metadata.
    """
    buckets: dict[tuple[str, str], list[tuple[int | None, str]]] = {}
    for path in paths_:
        name = os.path.basename(path)
        match = _TRAIL.search(name)
        prefix = name[:match.start()] if match else name
        # Curator rank/category prefixes are not camera namespaces.
        prefix = prefix.rsplit("__", 1)[-1].casefold()
        key = (os.path.realpath(os.path.dirname(path)), prefix)
        buckets.setdefault(key, []).append((int(match.group(1)) if match else None, path))
    groups: dict[str, str] = {}
    gid = 0
    for key in sorted(buckets):
        previous = None
        for number, path in sorted(buckets[key], key=lambda x: (x[0] is None, x[0] or 0, x[1])):
            if number is None or previous is None or number - previous > gap:
                gid += 1
            groups[path] = f"b{gid:03d}"
            previous = number
    return groups


def _rng(seed: int, domain: str) -> random.Random:
    """Sampling and each capture's slots have independent, stable random streams."""
    digest = hashlib.sha256(f"autoedit-blind-v2:{seed}:{domain}".encode()).digest()
    return random.Random(int.from_bytes(digest, "big"))


def _gallery_id(seed: int, methods: tuple[str, ...], sources: list[dict], settings=None) -> str:
    fingerprints = []
    for row in sources:
        names = [row["source"]] + [v["path"] for v in row.get("methods", {}).values()]
        values = {}
        for name in names:
            try:
                values[name] = _file_hash(name)
            except OSError:
                values[name] = None  # per-image validation records the actual failure
        fingerprints.append(values)
    payload = {"seed": seed, "methods": methods, "sources": sources, "settings": settings,
               "input_hashes": fingerprints}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]


def _prepare_output(out: str, protected: list[str]) -> None:
    destination = Path(out).resolve()
    for value in protected:
        source = Path(value).resolve()
        if source == destination or destination in source.parents:
            raise ValueError("gallery output must not contain its source files")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"gallery output must be empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)


def _validate_methods(methods) -> tuple[str, ...]:
    methods = tuple(methods)
    if len(methods) < 2 or len(set(methods)) != len(methods):
        raise ValueError("a comparison requires at least two distinct methods")
    return methods


def _write_manifest(out: str, rows: list[dict]) -> None:
    fields = ["gallery_id", "photo", "slot", "method", "name", "group", "stratum",
              "capture_id", "scene", "category", "split", "sha256"]
    with open(os.path.join(out, "manifest.csv"), "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: row.get(key, "") for key in fields} for row in rows)


def stratify(images: list[str], per_stratum: int, raw_opts: dict, rng: random.Random,
             failures: list[dict] | None = None) -> list[tuple[str, str]]:
    """Pick `per_stratum` photos from each source-brightness tercile (dark / mid / bright) by the
    quarter-res median luma, so the gate always sees dark, normal and highlight-risk frames.
    Returns [(path, stratum)] in corpus order."""
    lums = []
    for p in images:
        try:
            img = load_image(p, decode={**raw_opts, "half_size": True})
            lums.append((p, float(np.percentile(luma(downscale_long_edge(img, 256).rgb), 50))))
        except Exception as e:
            if failures is not None:
                failures.append({"capture_id": p, "error": f"stratification decode: {e}"})
            print(f"stratify: skipping {os.path.basename(p)} ({e})", file=sys.stderr)
    lums.sort(key=lambda t: t[1])
    n = len(lums)
    cuts = [lums[: n // 3], lums[n // 3: 2 * n // 3], lums[2 * n // 3:]]
    picked: dict[str, str] = {}
    for name, bucket in zip(STRATA, cuts):
        for p, _l in rng.sample(bucket, min(per_stratum, len(bucket))):
            picked[p] = name
    return [(p, picked[p]) for p in images if p in picked]


def baseline_settings(g) -> dict:
    """Historical one-slider control, retained for reproducibility rather than claimed quality."""
    s: dict = {"contrast": 10.0}
    if abs(g.mean_lum - 0.45) > 0.02:
        s["exposure"] = max(-0.4, min(0.4, -(g.mean_lum - 0.45) * 1.1))
    if g.mean_sat < 0.35:
        s["vibrance"] = 14.0
    if g.highlight_clip > 0.02:
        s["highlights"] = max(-50.0, -g.highlight_clip * 120.0 - 8.0)
    if g.shadow_clip > 0.05:
        s["shadows"] = min(35.0, g.shadow_clip * 80.0 + 6.0)
    return s


def _file_hash(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def _config_metadata(cfg) -> dict:
    # RunConfig excludes api_key itself; explicitly exclude it here as well. Do not capture
    # environment variables or raw argv, which can contain credentials from other callers.
    public = cfg.model_dump(mode="json", exclude={"api_key"})
    files = {}
    for key, value in public.items():
        if key.endswith("_path") and value:
            path = os.path.abspath(value)
            files[key] = {"path": path, "sha256": _file_hash(path) if os.path.isfile(path) else None}
    return {"config": public, "config_files": files}


def _pipeline_metadata(pipe) -> dict:
    result = _config_metadata(pipe.cfg)
    if hasattr(pipe, "workflow"):
        result["workflow"] = pipe.workflow
        result["workflow_options"] = pipe.options
        result["decode_options"] = pipe.decode_options()
    return result


def _segmenter_metadata(pipe) -> dict:
    segmenter = pipe.segmenter
    while hasattr(segmenter, "inner"):
        segmenter = segmenter.inner
    result = {key: getattr(segmenter, key) for key in ("name", "device", "model_id")
              if hasattr(segmenter, key)}
    # Available after SAM's first inference; this does not load torch or contact Hugging Face.
    model_config = getattr(getattr(segmenter, "_model", None), "config", None)
    revision = getattr(model_config, "_commit_hash", None)
    if revision:
        result["revision"] = revision
    return result


def _write_run_metadata(out: str, metadata: dict) -> None:
    path = os.path.join(out, "run.json")
    with open(path + ".part", "w") as fh:
        json.dump(metadata, fh, indent=2)
        fh.write("\n")
    os.replace(path + ".part", path)


def _load_entries(manifest_path: str, split: str | None = None) -> list[dict]:
    root = Path(manifest_path).resolve().parent
    document = json.loads(Path(manifest_path).read_text())
    entries = document["entries"]
    ids: set[str] = set()
    result = []
    for item in entries:
        row = dict(item)
        capture = str(row["capture_id"])
        if not capture or capture in ids:
            raise ValueError(f"empty or duplicate capture_id: {capture!r}")
        ids.add(capture)
        if split is not None and row.get("split") != split:
            continue
        for key in ("source", "camera_reference"):
            if row.get(key):
                row[key] = str((root / row[key]).resolve())
        if "methods" in row:
            row["methods"] = {
                name: ({**value, "path": str((root / value["path"]).resolve()),
                        **({"sidecar": str((root / value["sidecar"]).resolve())}
                           if value.get("sidecar") else {})} if isinstance(value, dict)
                       else {"path": str((root / value).resolve())})
                for name, value in row["methods"].items() if value
            }
        result.append(row)
    return result


def _scene_sample(entries: list[dict], count: int, seed: int) -> list[dict]:
    """Round-robin scene/category/group strata, sampling groups before repeated frames."""
    buckets: dict[tuple[str, str], list[dict]] = {}
    for row in entries:
        buckets.setdefault((row.get("scene", ""), row.get("category", "")), []).append(row)
    for key, bucket in buckets.items():
        _rng(seed, "selection:" + repr(key)).shuffle(bucket)
    chosen, seen_groups = [], set()
    keys = sorted(buckets)
    while len(chosen) < count and any(buckets.values()):
        for key in keys:
            bucket = buckets[key]
            if bucket and len(chosen) < count:
                idx = next((i for i, row in enumerate(bucket)
                            if row.get("group", row["capture_id"]) not in seen_groups), 0)
                row = bucket.pop(idx)
                chosen.append(row)
                seen_groups.add(row.get("group", row["capture_id"]))
    return sorted(chosen, key=lambda row: row["capture_id"])


def _publish_gallery(out, metadata, photos, rows, failures, excluded):
    # Presentation order has its own stream, independent of capture sampling and
    # each comparison's per-capture slot assignment. Opaque IDs remain stable.
    photos = list(photos)
    _rng(metadata["render"].get("seed", 7),
         "presentation:" + ",".join(metadata["render"]["methods"])).shuffle(photos)
    _write_manifest(out, rows)
    _write_html(os.path.join(out, "index.html"), photos, metadata["gallery_id"])
    groups = {row["group"] for row in rows}
    summary = {"photos": len(photos), "out": out, "groups": len(groups),
               "camera_missing": sum(row.get("reason") == "missing camera reference" for row in excluded),
               "failed": len(failures), "excluded": len(excluded),
               "expected": metadata["expected"], "gallery_id": metadata["gallery_id"]}
    metadata.update(status="complete" if not failures else "partial", summary=summary,
                    failures=failures, exclusions=excluded,
                    completed_at=datetime.now(timezone.utc).isoformat())
    _write_run_metadata(out, metadata)
    if len(groups) < 6:
        print("WARNING: fewer than six independent groups cannot reach two-sided p < 0.05, "
              "even with unanimous preferences.", file=sys.stderr)
    print(f"Wrote {len(photos)} photos × {len(metadata['render']['methods'])} variants to {out}; "
          f"{len(failures)} failures, {len(excluded)} excluded.")
    return summary


def render_comparison(manifest_path: str, out: str, methods=("baseline", "autoedit"),
                      seed: int = 7, max_px: int = 1600, quality: int = 95,
                      split: str | None = None, limit: int | None = None) -> dict:
    """Publish pre-rendered comparison files without decoding any original photograph.

    Paths are relative to the comparison manifest or absolute. Exactly two methods are
    required; missing camera references are excluded, never replaced by the source.
    """
    methods = _validate_methods(methods)
    if len(methods) != 2:
        raise ValueError("pre-rendered blind comparisons require exactly two methods")
    entries = _load_entries(manifest_path, split)
    if limit is not None:
        entries = _scene_sample(entries, limit, seed)
    protected = [manifest_path] + [r["source"] for r in entries]
    protected += [v["path"] for row in entries for v in row["methods"].values()]
    _prepare_output(out, protected)
    gid = _gallery_id(seed, methods, entries, {"max_px": max_px, "quality": quality})
    metadata = {"schema_version": 2, "gallery_id": gid, "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(), "expected": len(entries),
                "comparison_manifest": {"path": str(Path(manifest_path).resolve()),
                                        "sha256": _file_hash(manifest_path)},
                "harness_sha256": _file_hash(__file__),
                "render": {"methods": list(methods), "seed": seed, "max_px": max_px,
                           "quality": quality, "source": "pre_rendered", "split": split,
                           "metrics_scope": "upstream benchmark; gallery files reencoded equally"},
                "sources": [], "sidecars": []}
    _write_run_metadata(out, metadata)
    rows, photos, failures, excluded = [], [], [], []
    for index, entry in enumerate(entries, 1):
        capture = entry["capture_id"]
        pid = f"photo{index:03d}"
        if "camera" in methods and not entry["methods"].get("camera"):
            excluded.append({"capture_id": capture, "reason": "missing camera reference"})
            continue
        try:
            source_hash = _file_hash(entry["source"])
            if entry.get("source_sha256") and source_hash != entry["source_sha256"]:
                raise ValueError("source hash differs from comparison manifest")
            variants = {}
            originals = {}
            sidecars = {}
            for method in methods:
                spec = entry["methods"][method]
                filename = spec["path"]
                if Path(filename).suffix.lower() not in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}:
                    raise ValueError("comparison methods must point to rendered pixel images")
                variants[method] = downscale_long_edge(load_image(filename), max_px).rgb
                originals[method] = {"path": filename, "sha256": _file_hash(filename)}
                if spec.get("sidecar"):
                    sidecars[method] = json.loads(Path(spec["sidecar"]).read_text())
            aspects = [rgb.shape[1] / rgb.shape[0] for rgb in variants.values()]
            if max(aspects) / min(aspects) > 1.01:
                raise ValueError("comparison variants have incompatible aspect ratios")
            # Do not give a higher-resolution camera render an accidental sharpness advantage.
            common_edge = min(max(rgb.shape[:2]) for rgb in variants.values())
            variants = {name: downscale_long_edge(Image(rgb), common_edge).rgb
                        for name, rgb in variants.items()}
            order = list(methods)
            _rng(seed, f"slots:{','.join(methods)}:{capture}").shuffle(order)
            directory = Path(out) / pid
            directory.mkdir()
            local_rows, slots, local_sidecars = [], [], []
            for slot, method in enumerate(order):
                relative = f"{pid}/slot{slot}.jpg"
                save_image(variants[method], str(Path(out) / relative), "jpeg", quality)
                local_rows.append({**entry, "gallery_id": gid, "photo": pid, "slot": slot,
                                   "method": method, "name": Path(entry["source"]).name,
                                   "group": entry.get("group") or capture,
                                   "stratum": entry.get("category", ""),
                                   "sha256": _file_hash(str(Path(out) / relative))})
                slots.append(relative)
                if method in sidecars:
                    relative_sc = f"{pid}/slot{slot}.autoedit.json"
                    (Path(out) / relative_sc).write_text(json.dumps(sidecars[method], indent=2))
                    local_sidecars.append({"photo": pid, "slot": slot, "method": method,
                                           "path": relative_sc})
            # Publish a capture only after every requested arm is complete.
            rows.extend(local_rows)
            metadata["sidecars"].extend(local_sidecars)
            metadata["sources"].append({**entry, "photo": pid, "sha256": source_hash,
                                         "rendered_inputs": originals})
            photos.append({"id": pid, "slots": slots})
        except Exception as exc:
            failures.append({"capture_id": capture, "error": str(exc)})
    return _publish_gallery(out, metadata, photos, rows, failures, excluded)


def render(corpus: str, out: str, cfg, seed: int = 7, limit: int | None = None,
           max_px: int = 1600, quality: int | None = None, methods: tuple[str, ...] = METHODS,
           pipelines: dict | None = None, per_stratum: int | None = None, burst_gap: int = 2,
           metrics: bool = True, manifest_path: str | None = None, split: str | None = None,
           workflow: str = "current", baseline_workflow: str = "global-first") -> dict:
    """Render independent candidate decodes; preserve only complete comparison pairs."""
    methods = _validate_methods(methods)
    quality = cfg.quality if quality is None else quality
    if manifest_path:
        entries = _load_entries(manifest_path, split)
        if limit is not None:
            entries = _scene_sample(entries, limit, seed)
    else:
        images = batch.list_images(corpus)
        groups = burst_groups(images, burst_gap)
        entries = [{"source": p, "capture_id": os.path.abspath(p), "group": groups[p]}
                   for p in images]
    images = [row["source"] for row in entries]
    if not images:
        raise ValueError("no supported source photographs selected")
    strata = {}
    selection_failures: list[dict] = []
    if per_stratum:
        chosen = stratify(images, per_stratum, raw_opts_from(cfg), _rng(seed, "selection:brightness"), selection_failures)
        strata = dict(chosen)
        entries = [row for row in entries if row["source"] in strata]
    elif limit is not None and not manifest_path:
        entries = entries[:limit]
    _prepare_output(out, [row["source"] for row in entries])
    pipes = dict(pipelines or {})
    if "autoedit" in methods and "autoedit" not in pipes:
        if workflow == "current":
            pipes["autoedit"] = Pipeline.build(cfg)
        else:
            from autoedit.workflows import WorkflowEngine
            pipes["autoedit"] = WorkflowEngine(cfg, workflow=workflow)
    if "baseline" in methods and baseline_workflow != "historical":
        from autoedit.workflows import WorkflowEngine
        pipes["baseline"] = WorkflowEngine(cfg, workflow=baseline_workflow)
    gid = _gallery_id(seed, methods, entries, {"config": _config_metadata(cfg),
                      "workflow": workflow, "baseline_workflow": baseline_workflow,
                      "max_px": max_px, "quality": quality,
                      "pipelines": {m: _pipeline_metadata(p) for m, p in pipes.items()}})
    metadata = {"schema_version": 2, "gallery_id": gid, "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(), "expected": len(entries),
                "engine_version": __version__, "harness_sha256": _file_hash(__file__),
                **_config_metadata(cfg),
                "render": {"methods": list(methods), "seed": seed, "limit": limit,
                           "max_px": max_px, "quality": quality, "format": "jpeg",
                           "per_stratum": per_stratum, "burst_gap": burst_gap, "metrics": metrics,
                           "batch_consistency": False, "metrics_scope": "whole_frame_before_jpeg_encoding",
                           "workflow": workflow, "baseline_workflow": baseline_workflow,
                           "grouping": "manifest" if manifest_path else "filename_fallback", "split": split},
                "pipelines": {m: {**_pipeline_metadata(p), "segmenter": _segmenter_metadata(p)}
                              for m, p in pipes.items() if m in methods},
                "sources": [], "sidecars": []}
    _write_run_metadata(out, metadata)
    rows, photos, metric_rows, failures, excluded = [], [], [], list(selection_failures), []
    metadata["expected"] += len(selection_failures)
    for index, entry in enumerate(entries, 1):
        path, capture, pid = entry["source"], entry["capture_id"], f"photo{index:03d}"
        try:
            source_hash = _file_hash(path)
            if entry.get("source_sha256") and source_hash != entry["source_sha256"]:
                raise ValueError("source hash differs from source manifest")
            decoded = {}
            def decode(options):
                key = json.dumps(options, sort_keys=True)
                if key not in decoded:
                    decoded[key] = downscale_long_edge(load_image(path, decode=options), max_px)
                return decoded[key]
            work = decode(raw_opts_from(cfg))
            cam_rgb = None
            if "camera" in methods or metrics:
                if entry.get("camera_reference"):
                    cam_rgb = downscale_long_edge(load_image(entry["camera_reference"]), max_px).rgb
                else:
                    cam_rgb = camera_preview(path, max_px, like=work.rgb)
            if "camera" in methods and cam_rgb is None:
                excluded.append({"capture_id": capture, "reason": "missing camera reference"})
                continue
            variants = {}
            for method in methods:
                if method in pipes:
                    pipe = pipes[method]
                    options = pipe.decode_options() if hasattr(pipe, "decode_options") else raw_opts_from(pipe.cfg)
                    candidate_work = decode(options)
                    if hasattr(pipe, "process"):
                        variants[method] = pipe.process(path, image=candidate_work, common_source=work)
                    else:
                        variants[method] = pipe.process_one(path, do_apply=True, image=candidate_work)
                elif method == "unedited":
                    variants[method] = (work.rgb, None)
                elif method in ("baseline", "historical"):
                    global_stats, _ = stats.compute(work, {})
                    variants[method] = (apply_ops(work.rgb, baseline_settings(global_stats)), None)
                elif method == "camera":
                    variants[method] = (cam_rgb, None)
                else:
                    raise ValueError(f"no renderer for {method}")
            directory = Path(out) / pid
            directory.mkdir()
            order = list(methods)
            _rng(seed, f"slots:{','.join(methods)}:{capture}").shuffle(order)
            slots, local_rows, local_metrics, local_sidecars = [], [], [], []
            src_d = tm.describe(work.rgb) if metrics else None
            cam_d = tm.describe(cam_rgb) if metrics and cam_rgb is not None else None
            for slot, method in enumerate(order):
                rgb, result = variants[method]
                if rgb is None or not np.isfinite(rgb).all():
                    raise ValueError(f"{method} produced missing or invalid pixels")
                relative = f"{pid}/slot{slot}.jpg"
                save_image(rgb, str(Path(out) / relative), "jpeg", quality)
                if result is not None:
                    sidecar = f"{pid}/slot{slot}.autoedit.json"
                    emit.write_sidecar(result, str(Path(out) / sidecar))
                    local_sidecars.append({"photo": pid, "slot": slot, "method": method, "path": sidecar})
                local_rows.append({**entry, "gallery_id": gid, "photo": pid, "slot": slot,
                                   "method": method, "name": os.path.basename(path),
                                   "stratum": strata.get(path, entry.get("category", "")),
                                   "sha256": _file_hash(str(Path(out) / relative))})
                slots.append(relative)
                if metrics:
                    row = tm.row_for(os.path.basename(path), src_d, tm.describe(rgb), cam_d)
                    row.update(photo=pid, method=method, group=entry.get("group", capture),
                               stratum=strata.get(path, entry.get("category", "")),
                               scene=entry.get("scene", ""), split=entry.get("split", ""))
                    local_metrics.append(row)
            if _file_hash(path) != source_hash:
                raise ValueError("source file changed during render")
            rows.extend(local_rows)
            metric_rows.extend(local_metrics)
            metadata["sidecars"].extend(local_sidecars)
            metadata["sources"].append({**entry, "photo": pid, "path": os.path.abspath(path),
                                         "sha256": source_hash, "stratum": strata.get(path, "")})
            photos.append({"id": pid, "slots": slots})
            print(f"[{index}/{len(entries)}] {pid} complete")
        except Exception as exc:
            failures.append({"capture_id": capture, "error": str(exc)})
            print(f"[{index}/{len(entries)}] {pid} failed: {exc}", file=sys.stderr)
    if metric_rows:
        tm.write_csv(metric_rows, os.path.join(out, "metrics.csv"))
    for method, pipe in pipes.items():
        if method in metadata["pipelines"]:
            metadata["pipelines"][method]["segmenter"] = _segmenter_metadata(pipe)
    return _publish_gallery(out, metadata, photos, rows, failures, excluded)


def _binomial_p(wins_a: int, wins_b: int) -> float:
    """Two-sided exact fair-binomial probability (including the observed tail)."""
    if min(wins_a, wins_b) < 0:
        raise ValueError("win counts must be nonnegative")
    n = wins_a + wins_b
    if n == 0:
        return 1.0
    # Integer division avoids the overflow of 2.0 ** n for large galleries.
    tail = sum(math.comb(n, i) for i in range(min(wins_a, wins_b) + 1)) / (1 << n)
    return min(1.0, 2 * tail)


@lru_cache(maxsize=64)
def n_for_power(p_alt: float, alpha: float = 0.05, power: float = 0.80, n_max: int = 400) -> int | None:
    """Smallest n reaching exact two-sided rejection power under a predeclared alternative."""
    if not 0 < p_alt < 1 or not 0 < alpha < 1 or not 0 < power < 1:
        raise ValueError("probabilities must be between zero and one")
    for n in range(1, n_max + 1):
        critical = next((k for k in range(n // 2 + 1, n + 1)
                         if _binomial_p(k, n - k) < alpha), None)
        if critical is None:
            continue
        rejecting = list(range(n - critical + 1)) + list(range(critical, n + 1))
        actual = sum(math.comb(n, k) * p_alt ** k * (1 - p_alt) ** (n - k) for k in rejecting)
        if actual >= power:
            return n
    return None


def score(results_path: str, manifest_path: str | None = None,
          pair: tuple[str, str] | None = None) -> dict:
    """Validate one vote per capture and reveal predeclared exact-binomial comparisons.

    New galleries require matching gallery_id values. Old manifests remain readable, with
    an explicit warning that their result files cannot be authenticated to a gallery.
    """
    manifest_path = manifest_path or os.path.join(os.path.dirname(results_path) or ".", "manifest.csv")
    mapping, photo_meta, per_photo_methods = {}, {}, {}
    gallery_ids = set()
    with open(manifest_path, newline="") as fh:
        for row in csv.DictReader(fh):
            pid, slot, method = row["photo"], int(row["slot"]), row["method"]
            if slot < 0 or (pid, slot) in mapping or method in per_photo_methods.setdefault(pid, set()):
                raise ValueError(f"duplicate or invalid manifest slot/method for {pid}")
            mapping[(pid, slot)] = method
            per_photo_methods[pid].add(method)
            if row.get("gallery_id"):
                gallery_ids.add(row["gallery_id"])
            if pid in photo_meta:
                for key in ("group", "split", "capture_id", "scene", "category", "stratum"):
                    if photo_meta[pid].get(key, "") != row.get(key, ""):
                        raise ValueError(f"inconsistent {key} across slots for {pid}")
            photo_meta[pid] = row
    if len(gallery_ids) > 1:
        raise ValueError("manifest contains multiple gallery IDs")
    if not mapping:
        raise ValueError("gallery has no complete comparisons to score")
    methods_seen = set(next(iter(per_photo_methods.values())))
    if len(methods_seen) < 2 or any(value != methods_seen for value in per_photo_methods.values()):
        raise ValueError("every photo must contain the same comparison methods exactly once")
    captures = [row.get("capture_id") or pid for pid, row in photo_meta.items()]
    if len(captures) != len(set(captures)):
        raise ValueError("manifest repeats a capture under different photo IDs")
    gallery_id = next(iter(gallery_ids), None)
    if gallery_id is None:
        print("WARNING: legacy manifest has no gallery ID; cross-gallery votes cannot be detected.", file=sys.stderr)
    wins, per_photo = Counter(), {}
    with open(results_path, newline="") as fh:
        for line, row in enumerate(csv.DictReader(fh), 2):
            if gallery_id and row.get("gallery_id") != gallery_id:
                raise ValueError(f"results line {line} belongs to a different or missing gallery ID")
            pid = row["photo"]
            if pid in per_photo:
                raise ValueError(f"duplicate vote for {pid}; retain exactly one vote per photo")
            if pid not in photo_meta:
                raise ValueError(f"unknown photo on results line {line}: {pid}")
            if row.get("best_slot") == "tie":
                per_photo[pid] = None
                continue
            try:
                slot = int(row.get("best_slot"))
            except (ValueError, TypeError) as exc:
                raise ValueError(f"invalid slot on results line {line}") from exc
            if (pid, slot) not in mapping:
                raise ValueError(f"unknown photo/slot on results line {line}: {pid}/{slot}")
            method = mapping[(pid, slot)]
            wins[method] += 1
            per_photo[pid] = method
    total = len(per_photo)
    photo_ties = sum(method is None for method in per_photo.values())
    decisive_votes = total - photo_ties
    print_order = sorted(methods_seen, key=lambda m: (METHODS.index(m) if m in METHODS else len(METHODS), m))
    print(f"\nBlind-test results ({total}/{len(photo_meta)} photos answered; "
          f"{decisive_votes} decisive preferences, {photo_ties} no-preference votes):")
    for method in print_order:
        pct = f"{100 * wins[method] / decisive_votes:5.1f}%" if decisive_votes else "   n/a"
        print(f"  {method:9s} {pct}  ({wins[method]}/{decisive_votes} decisive preferences)")
    out = {"wins": dict(wins), "total": total, "expected": len(photo_meta),
           "photo_ties": photo_ties, "decisive_votes": decisive_votes, "decisive_trials": None,
           "missing_votes": len(photo_meta) - total, "gallery_id": gallery_id,
           "head_to_head": None, "p_value": None, "pair": [],
           "p_value_assumption": "independent-photo Bernoulli trials"}
    if pair is not None:
        if len(pair) != 2 or pair[0] == pair[1] or not set(pair) <= methods_seen:
            raise ValueError(f"invalid comparison pair {pair!r}")
        selected = pair
    elif len(methods_seen) == 2:
        selected = (("autoedit", next(m for m in methods_seen if m != "autoedit"))
                    if "autoedit" in methods_seen else tuple(sorted(methods_seen)))
    else:
        print("No head-to-head test: this is a multi-arm gallery. Predeclare --pair for a "
              "conditional analysis, or use a separate two-arm gallery.")
        return out
    a, b = wins[selected[0]], wins[selected[1]]
    p = _binomial_p(a, b) if a + b else None
    h2h = 100 * a / (a + b) if a + b else None
    out.update(head_to_head=h2h, p_value=p, pair=list(selected),
               decisive_trials=a + b, other_arm_votes=decisive_votes - a - b,
               conditional_on_pair_winning=len(methods_seen) > 2)
    if p is not None:
        print(f"\n{selected[0]} vs {selected[1]}: {h2h:.1f}% | exact two-sided p = {p:.5f} "
              f"({a + b} decisive photo trials; {photo_ties} photo ties excluded)")
    else:
        print(f"\n{selected[0]} vs {selected[1]}: no decisive photo trials; exact test unavailable.")
    if len(methods_seen) > 2:
        print("Conditional on either selected arm winning; this is not a two-way preference study.")
    def group_key(pid):
        row = photo_meta[pid]
        # Group IDs already contain event/camera provenance. A changing scene label
        # must never turn one burst into several nominally independent observations.
        return (row.get("split", ""), "group" if row.get("group") else "photo", row.get("group") or pid)
    all_groups = {}
    for pid in photo_meta:
        all_groups.setdefault(group_key(pid), []).append(pid)
    groups = []
    for key, photos in sorted(all_groups.items()):
        answered = [pid for pid in photos if pid in per_photo]
        if not answered:
            continue
        count = Counter(per_photo[pid] for pid in answered)
        va, vb = count[selected[0]], count[selected[1]]
        if va > vb:
            outcome = "first_arm"
        elif vb > va:
            outcome = "second_arm"
        elif va + vb:
            outcome = "balanced_decisive_votes"
        elif count[None] == len(answered):
            outcome = "photo_ties_only"
        else:
            outcome = "no_selected_pair_preference"
        groups.append({"split": key[0], "group": key[2], "group_source": key[1],
                       "scenes": sorted({photo_meta[pid].get("scene", "") for pid in photos} - {""}),
                       "expected_photos": len(photos), "answered_photos": len(answered),
                       "missing_votes": len(photos) - len(answered), "photo_ties": count[None],
                       "pair_wins": [va, vb], "other_arm_votes": len(answered) - count[None] - va - vb,
                       "outcome": outcome})
    ga = sum(group["outcome"] == "first_arm" for group in groups)
    gb = sum(group["outcome"] == "second_arm" for group in groups)
    balanced = sum(group["outcome"] == "balanced_decisive_votes" for group in groups)
    tie_only = sum(group["outcome"] == "photo_ties_only" for group in groups)
    ties = balanced + tie_only
    gp = _binomial_p(ga, gb) if ga + gb else None
    out.update(group_wins=[ga, gb], group_p_value=gp, groups=len(groups), group_details=groups,
               expected_groups=len(all_groups), missing_groups=len(all_groups) - len(groups),
               partially_answered_groups=sum(group["missing_votes"] > 0 for group in groups),
               group_ties=ties, group_ties_balanced=balanced, group_ties_photo_only=tie_only,
               groups_without_pair_preference=sum(group["outcome"] == "no_selected_pair_preference" for group in groups),
               independent_trials=ga + gb)
    print(f"  Group majority votes: {ga}–{gb}; {balanced} balanced groups, {tie_only} photo-tie-only groups excluded")
    if gp is not None:
        print(f"  Group exact two-sided p = {gp:.5f}")
    else:
        print("  No decisive group trials; exact group test unavailable.")
    if ga + gb < 6:
        print("WARNING: this sample cannot reach two-sided p < 0.05, even with unanimous votes.")
    for field in ("stratum", "scene", "category", "split"):
        labels = sorted({row.get(field, "") for row in photo_meta.values()} - {""})
        if labels:
            out[field + "_results"] = {
                label: {method: sum(1 for pid, m in per_photo.items()
                                    if photo_meta[pid].get(field) == label and m == method)
                        for method in selected} for label in labels}
            out[field + "_photo_ties"] = {
                label: sum(photo_meta[pid].get(field) == label and method is None
                           for pid, method in per_photo.items()) for label in labels}
            out[field + "_missing_votes"] = {
                label: sum(row.get(field) == label and pid not in per_photo
                           for pid, row in photo_meta.items()) for label in labels}
            print(f"  By {field}: {out[field + '_results']}")
    if any(row.get("stratum") in STRATA for row in photo_meta.values()):
        out["strata"] = {label: sum(row.get("stratum") == label for row in photo_meta.values())
                         for label in STRATA}
    need = {f"{int(100 * probability)}%": n_for_power(probability) for probability in (0.60, 0.65, 0.70, 0.80)}
    out["n_for_power"] = need
    print("  Independent trials for 80% power: " + ", ".join(f"{k}: {v}" for k, v in need.items()))
    print("  Group independence remains an assumption; scene/burst blocking does not prove it.")
    return out


def _write_html(path: str, photos: list[dict], gallery_id: str = "legacy") -> None:
    """A dependency-free single-file ranker. Inlines the photo/slot list; exports results.csv."""
    data = json.dumps([{ "id": p["id"], "slots": p["slots"]} for p in photos]).replace("<", "\\u003c")
    html = _HTML_TEMPLATE.replace("/*__DATA__*/", data).replace("/*__GALLERY__*/", json.dumps(gallery_id))
    with open(path, "w") as fh:
        fh.write(html)


_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Photo comparison</title>
<style>
  :root{
    --bg:#0e1013; --panel:#16191f; --line:#262b34; --ink:#eef1f5; --mut:#8b93a1;
    --accent:#6a97ff; --accent-soft:rgba(106,151,255,.16); --ok:#43c98b;
    --shadow:0 8px 30px rgba(0,0,0,.45);
  }
  *{box-sizing:border-box}
  html,body{height:100%}
  body{margin:0;background:var(--bg);color:var(--ink);
    font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    display:flex;flex-direction:column;overflow:hidden}

  /* ── header + progress ── */
  header{flex:0 0 auto;padding:16px 26px 0}
  .top{display:flex;align-items:baseline;gap:14px}
  .brand{font-weight:650;letter-spacing:.2px}
  .brand .dot{color:var(--accent)}
  .task{color:var(--mut);font-size:13.5px}
  .count{margin-left:auto;color:var(--mut);font-variant-numeric:tabular-nums;font-size:13.5px}
  .track{height:3px;background:var(--line);border-radius:3px;margin-top:14px;overflow:hidden}
  .fill{height:100%;width:0;background:var(--accent);border-radius:3px;transition:width .28s ease}

  /* ── stage: three-up comparison, sized to fill the viewport ── */
  main{flex:1 1 auto;min-height:0;display:flex;flex-direction:column;padding:14px 26px 8px}
  .name{color:var(--mut);font-size:12.5px;text-align:center;letter-spacing:.3px;margin-bottom:10px}
  .row{flex:1 1 auto;min-height:0;display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
  .opt{position:relative;min-width:0;background:var(--panel);border:1.5px solid var(--line);
    border-radius:14px;overflow:hidden;cursor:pointer;display:flex;align-items:center;
    justify-content:center;transition:border-color .14s,transform .14s,box-shadow .14s}
  .opt:hover{border-color:var(--accent);transform:translateY(-3px);box-shadow:var(--shadow)}
  .opt.pick{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
  .opt img{max-width:100%;max-height:100%;display:block;object-fit:contain}
  .num{position:absolute;top:12px;left:12px;width:26px;height:26px;border-radius:8px;
    background:rgba(10,12,16,.66);backdrop-filter:blur(4px);color:var(--ink);
    display:grid;place-items:center;font-size:13px;font-weight:600;opacity:.8;
    transition:opacity .14s,background .14s}
  .opt:hover .num{opacity:1;background:var(--accent)}

  /* ── footer hint ── */
  footer{flex:0 0 auto;display:flex;align-items:center;gap:16px;padding:12px 26px 18px;
    color:var(--mut);font-size:13px}
  .kbd{display:inline-block;min-width:18px;padding:1px 6px;border:1px solid var(--line);
    border-bottom-width:2px;border-radius:5px;background:#1b1f27;color:var(--ink);
    font:12px/1.4 ui-monospace,Menlo,monospace;text-align:center}
  .ghost{margin-left:auto;background:#1b1f27;color:var(--ink);border:1px solid var(--line);
    border-radius:9px;padding:8px 14px;font-weight:600;cursor:pointer;transition:opacity .14s}
  .ghost:disabled{opacity:.35;cursor:default}
  #tie{margin-left:0}
  .ghost.pick{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}

  /* ── done ── */
  #done{flex:1 1 auto;display:none;flex-direction:column;align-items:center;justify-content:center;
    text-align:center;padding:40px;gap:6px}
  #done.show{display:flex}
  .check{width:56px;height:56px;border-radius:50%;background:var(--accent-soft);color:var(--ok);
    display:grid;place-items:center;font-size:28px;margin-bottom:8px}
  #done h2{margin:0;font-size:22px;font-weight:650}
  #done p{margin:0;color:var(--mut);max-width:440px}
  .cta{margin-top:18px;background:var(--accent);color:#fff;border:0;border-radius:10px;
    padding:12px 22px;font-weight:650;font-size:15px;cursor:pointer}
  .cmd{margin-top:20px;font:12.5px/1.5 ui-monospace,Menlo,monospace;color:var(--mut);
    background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 14px}
</style></head><body>
<header>
  <div class="top">
    <span class="brand">Photo comparison</span>
    <span class="task">Choose the version you prefer, or select No preference. Versions are unlabeled and shuffled.</span>
    <span class="count" id="count"></span>
  </div>
  <div class="track"><div class="fill" id="fill"></div></div>
</header>

<main id="stage">
  <div class="name" id="name"></div>
  <div class="row" id="row"></div>
</main>

<footer id="foot">
  <span>Choose a version or No preference</span>
  <span>·</span>
  <span id="keys"></span>
  <span>·</span>
  <span><span class="kbd">←</span> back</span>
  <button class="ghost" id="tie" type="button">No preference <span class="kbd">0</span></button>
  <button class="ghost" id="back" disabled>← Back</button>
</footer>

<div id="done">
  <div class="check">✓</div>
  <h2>All compared — thank you.</h2>
  <p>Download your choices, drop <b>results.csv</b> into this folder, then run the scorer to see the comparison results.</p>
  <button class="cta" id="dl">Download results.csv</button>
  <div class="cmd">python3 scripts/blindtest.py --score results.csv</div>
</div>

<script>
const PHOTOS = /*__DATA__*/;
const GALLERY_ID = /*__GALLERY__*/;
let i = 0, busy = false;
const picks = {};                       // photo id -> chosen slot index or canonical 'tie'
const $ = id => document.getElementById(id);
const row = $('row'), fill = $('fill'), count = $('count'), name = $('name'), back = $('back'), tie = $('tie');

function render(){
  const p = PHOTOS[i], n = p.slots.length;
  count.textContent = (i + 1) + ' / ' + PHOTOS.length;
  fill.style.width = (i / PHOTOS.length * 100) + '%';
  name.textContent = p.id;
  row.style.gridTemplateColumns = 'repeat(' + n + ', 1fr)';   // adapts to 2- or 3-way
  $('keys').innerHTML = 'keys ' +
    p.slots.map((_, k) => '<span class="kbd">' + (k + 1) + '</span>').join(' ');
  row.innerHTML = '';
  tie.classList.remove('pick');
  p.slots.forEach((src, slot) => {
    const o = document.createElement('div');
    o.className = 'opt';
    o.innerHTML = '<img src="' + src + '" loading="lazy" alt="option ' + (slot+1) + '">' +
                  '<div class="num">' + (slot + 1) + '</div>';
    o.onclick = () => choose(slot, o);
    row.appendChild(o);
  });
  back.disabled = i === 0;
}
function choose(slot, el){
  if (busy || !PHOTOS[i]) return;
  if (slot !== 'tie' && (!Number.isInteger(slot) || slot < 0 || slot >= PHOTOS[i].slots.length)) return;
  busy = true;
  picks[PHOTOS[i].id] = slot;
  el = el || (slot === 'tie' ? tie : row.children[slot]);
  if (el) el.classList.add('pick');     // brief confirmation before advancing
  setTimeout(() => {
    busy = false;
    if (i < PHOTOS.length - 1){ i++; render(); } else { finish(); }
  }, 160);
}
tie.onclick = () => choose('tie', tie);
back.onclick = () => { if (i > 0 && !busy){ i--; render(); } };
document.addEventListener('keydown', e => {
  if (!PHOTOS.length || $('done').classList.contains('show')) return;
  const n = PHOTOS[i].slots.length;
  if (e.key === '0') choose('tie', tie);
  else if (e.key >= '1' && e.key <= String(n)) choose(+e.key - 1);
  else if (e.key === 'ArrowLeft' || e.key === 'Backspace'){ e.preventDefault(); back.click(); }
});
function finish(){
  fill.style.width = '100%';
  $('stage').style.display = 'none';
  $('foot').style.display = 'none';
  $('done').classList.add('show');
}
$('dl').onclick = () => {
  let csv = 'gallery_id,photo,best_slot\n';
  for (const id in picks) csv += GALLERY_ID + ',' + id + ',' + picks[id] + '\n';
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], {type:'text/csv'}));
  a.download = 'results.csv'; a.click();
};
if (PHOTOS.length) render(); else { finish(); document.querySelector('#done h2').textContent = 'No eligible comparisons'; $('dl').disabled = true; }
</script></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description="AutoEdit blind-test gate")
    ap.add_argument("corpus", nargs="?", help="folder of photos to render")
    ap.add_argument("out", nargs="?", default="dist/blindtest")
    ap.add_argument("--comparison-manifest", help="publish a two-arm gallery from pre-rendered files; positional path is output")
    ap.add_argument("--manifest", help="capture source manifest (RAW-preferred selection and group metadata)")
    ap.add_argument("--split", help="select exactly this split from a source/comparison manifest")
    ap.add_argument("--workflow", choices=["current", "global-first", "optimized"], default="current")
    ap.add_argument("--baseline-workflow", choices=["global-first", "historical"], default="global-first",
                    help="competent global-first competitor; historical retains the old one-slider comparator")
    ap.add_argument("--score", metavar="results.csv", help="reveal win-rates from a ranked results.csv")
    ap.add_argument("--pair", nargs=2, metavar=("METHOD_A", "METHOD_B"),
                    help="predeclare the head-to-head pair when scoring a gallery with >2 methods")
    ap.add_argument("--configs-dir", dest="configs_dir", default=None)
    ap.add_argument("--backend", choices=["stub", "sam3"], default=None)
    ap.add_argument("--device", default=None)
    ap.add_argument("--style", default=None)
    ap.add_argument("--intensity", type=float, default=None)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--max-px", dest="max_px", type=int, default=1600)
    ap.add_argument("--quality", type=int, default=None,
                    help="JPEG quality for every variant (default: resolved output config, currently 95)")
    ap.add_argument("--two-way", dest="two_way", action="store_true",
                    help="AutoEdit vs baseline only (drop 'unedited') — the sharpest regional-vs-global gate")
    ap.add_argument("--vs-camera", dest="vs_camera", action="store_true",
                    help="AutoEdit vs the camera's own render (embedded RAW preview) — the honest pro bar")
    ap.add_argument("--methods", help="explicit comma list from unedited,baseline,camera,autoedit")
    ap.add_argument("--stratify", type=int, metavar="N",
                    help="pick N photos from each source-brightness tercile (dark/mid/bright) instead of --limit")
    ap.add_argument("--burst-gap", dest="burst_gap", type=int, default=2,
                    help="frame-number gap that separates bursts for per-group significance (default 2)")
    ap.add_argument("--raw-mode", dest="raw_mode", default=None,
                    help="RAW development mode for every variant (none|libraw_auto|preview_ev|preview_ev_headroom|tone_target)")
    ap.add_argument("--no-metrics", dest="metrics", action="store_false", help="skip metrics.csv")
    ap.add_argument("--rules-a", dest="rules_a", help="rules.toml for config A (A/B tuning gate)")
    ap.add_argument("--rules-b", dest="rules_b", help="rules.toml for config B")
    ap.add_argument("--looks-a", dest="looks_a", help="optional looks.toml for config A")
    ap.add_argument("--looks-b", dest="looks_b", help="optional looks.toml for config B")
    ap.add_argument("--configs-a", dest="configs_a", help="whole configs dir for config A (rules+looks+defaults incl. [decode])")
    ap.add_argument("--configs-b", dest="configs_b", help="whole configs dir for config B")
    args = ap.parse_args()

    if args.score:
        score(args.score, pair=tuple(args.pair) if args.pair else None)
        return 0
    if not args.corpus and not args.manifest and not args.comparison_manifest:
        ap.error("give a corpus, --manifest, --comparison-manifest, or --score")
    if args.manifest and args.comparison_manifest:
        ap.error("--manifest and --comparison-manifest are mutually exclusive")
    if (args.manifest or args.comparison_manifest) and args.out == "dist/blindtest" and args.corpus:
        args.out, args.corpus = args.corpus, None
    if bool(args.rules_a) != bool(args.rules_b):
        ap.error("--rules-a and --rules-b must be given together")
    if bool(args.configs_a) != bool(args.configs_b):
        ap.error("--configs-a and --configs-b must be given together")

    cfg = make_config(args.configs_dir or paths.configs_dir(), backend=args.backend,
                      device=args.device, style=args.style, intensity=args.intensity,
                      raw_mode=args.raw_mode, quality=args.quality)
    common = dict(seed=args.seed, limit=args.limit, max_px=args.max_px, per_stratum=args.stratify,
                  burst_gap=args.burst_gap, metrics=args.metrics, quality=args.quality,
                  manifest_path=args.manifest, split=args.split,
                  workflow=args.workflow, baseline_workflow=args.baseline_workflow)

    if args.rules_a or args.configs_a:
        # Config-A-vs-config-B gate: same engine, two rule sets (or two whole configs dirs, which
        # also covers [decode] raw_mode). Segmentation is identical either way, so B shares A's
        # (cached) segmenter — one SAM pass per photo.
        from autoedit.segment import CachingSegmenter  # noqa: E402

        if args.configs_a:
            cfg_a = make_config(args.configs_a, backend=args.backend, device=args.device,
                                style=args.style, intensity=args.intensity, raw_mode=args.raw_mode,
                                quality=args.quality)
            cfg_b = make_config(args.configs_b, backend=args.backend, device=args.device,
                                style=args.style, intensity=args.intensity, raw_mode=args.raw_mode,
                                quality=args.quality)
        else:
            cfg_a = cfg.model_copy(update={"rules_path": args.rules_a,
                                           **({"looks_path": args.looks_a} if args.looks_a else {})})
            cfg_b = cfg.model_copy(update={"rules_path": args.rules_b,
                                           **({"looks_path": args.looks_b} if args.looks_b else {})})
        pipe_a, pipe_b = Pipeline.build(cfg_a), Pipeline.build(cfg_b)
        pipe_a.segmenter = CachingSegmenter(pipe_a.segmenter)
        pipe_b.segmenter = pipe_a.segmenter
        summary = render(args.corpus, args.out, cfg_a, methods=("rules_a", "rules_b"),
                         pipelines={"rules_a": pipe_a, "rules_b": pipe_b}, **common)
        return int(bool(summary["failed"]) or not summary["photos"])

    if args.methods:
        methods = tuple(m.strip() for m in args.methods.split(",") if m.strip())
        bad = [m for m in methods if m not in ALL_METHODS]
        if bad or len(methods) < 2 or len(set(methods)) != len(methods):
            ap.error(f"--methods must name ≥2 of {ALL_METHODS}; got {methods}")
    elif args.vs_camera:
        methods = ("camera", "autoedit")
    elif args.two_way:
        methods = ("baseline", "autoedit")
    else:
        methods = METHODS
    if args.comparison_manifest:
        summary = render_comparison(args.comparison_manifest, args.out, methods=methods,
                                    seed=args.seed, max_px=args.max_px, quality=cfg.quality,
                                    split=args.split, limit=args.limit)
    else:
        summary = render(args.corpus, args.out, cfg, methods=methods, **common)
    return int(bool(summary["failed"]) or not summary["photos"])


if __name__ == "__main__":
    raise SystemExit(main())
