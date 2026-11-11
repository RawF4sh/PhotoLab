"""Local autonomous orchestration with explicit source selection and export verification.

The existing Pipeline remains the control. This wrapper accepts only measured safe
outputs and records every fallback, including no-edit, without changing originals.
"""
from __future__ import annotations

import json
import importlib.metadata
import os
import platform
import re
import tempfile
import time
from pathlib import Path

import numpy as np

from . import emit, stats
from .captures import assert_separate_output, discover_captures, sha256_file, stable_hash, verify_sources
from .contract import Result, RunConfig
from .imageio import Image, downscale_long_edge, load_image
from .quality import assess_pixels, load_quality_config


def write_json(path: str | Path, data) -> None:
    path = Path(path)
    fd, temp = tempfile.mkstemp(prefix=path.name + ".", suffix=".part", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(data, indent=2, allow_nan=False) + "\n")
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def build_safe_engine(cfg: RunConfig, workflow="current", options=None):
    """Catch optional-backend initialization failures as well as inference failures."""
    from .workflows import WorkflowEngine
    try:
        return WorkflowEngine(cfg, workflow=workflow, options=options)
    except Exception as exc:
        if cfg.backend != "sam3":
            raise
        fallback = WorkflowEngine(cfg.model_copy(update={"backend": "stub", "concepts": []}),
                                  workflow=workflow, options=options)
        fallback._quality_backend_error = f"{type(exc).__name__}: {exc}"
        return fallback


def configuration_evidence(cfg: RunConfig, workflow: str, options: dict | None = None) -> dict:
    config = cfg.model_dump()
    files = {}
    for key, value in config.items():
        if key.endswith("_path") and value and Path(value).is_file():
            files[key] = sha256_file(value)
    for name in ("quality.toml", "workflows.toml"):
        p = Path(__file__).parent / "configs" / name
        if p.exists():
            files[name] = sha256_file(p)
    for name in ("quality_config", "config_path"):
        if (options or {}).get(name):
            files[name] = sha256_file(options[name])
    package = Path(__file__).parent
    code = {str(p.relative_to(package)): sha256_file(p) for p in sorted(package.rglob("*.py"))}
    versions = {}
    for name in ("numpy", "Pillow", "pydantic", "rawpy", "tifffile", "opencv-python", "opencv-python-headless", "opencv-contrib-python", "opencv-contrib-python-headless", "torch", "torchvision", "transformers", "scipy"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return {"run_config": config, "workflow": workflow, "options": options or {}, "config_files": files,
            "code_hashes": code, "runtime": {"python": platform.python_version(),
            "platform": platform.platform(), "versions": versions}}


def _image(value) -> Image:
    return value if isinstance(value, Image) else Image(rgb=np.asarray(value, dtype=np.float32))


def _finalize_stats(result: Result, reference: Image, rgb: np.ndarray, masks=None) -> None:
    # Keep the reported foreground definition consistent before and after fallback.
    pre = downscale_long_edge(reference, 1024)
    post = downscale_long_edge(Image(rgb=rgb), 1024)
    usable = {}
    for name, mask in (masks or {}).items():
        yy = np.minimum(np.arange(pre.height) * mask.shape[0] // pre.height, mask.shape[0] - 1)
        xx = np.minimum(np.arange(pre.width) * mask.shape[1] // pre.width, mask.shape[1] - 1)
        usable[name] = np.asarray(mask)[np.ix_(yy, xx)].astype(bool)
    result.global_stats, rs = stats.compute(pre, usable)
    result.region_stats = list(rs.values())
    result.post_edit_stats, ps = stats.compute(post, usable)
    result.post_region_stats = list(ps.values())


def render_safe(path: str, cfg: RunConfig, workflow: str = "current", image: Image | None = None,
                common_source: Image | np.ndarray | None = None, options: dict | None = None,
                engine=None, proposal_sink=None) -> tuple[np.ndarray, Result]:
    from .workflows import WorkflowEngine

    if cfg.decider == "llm" or cfg.allow_cloud:
        raise ValueError("the autonomous quality workflow is local; cloud deciders are not supported")
    start = time.perf_counter()
    options = dict(options or {})
    quality_config = options.pop("quality_config", None)
    quality_thresholds = options.pop("quality_thresholds", None)
    reference = _image(common_source) if common_source is not None else (
        image if image is not None and cfg.raw_mode == "none" else load_image(path, decode={"raw_mode": "none"}))
    holder = engine or build_safe_engine(cfg, workflow=workflow, options=options)
    engine = getattr(holder, "_quality_fallback_engine", holder)
    backend_error = getattr(engine, "_quality_backend_error", None)
    try:
        proposal, result = engine.process(path, image=image, common_source=reference)
    except Exception as exc:
        if cfg.backend != "sam3":
            raise
        # Record this as a fallback, never as a completed SAM experiment.
        backend_error = f"{type(exc).__name__}: {exc}"
        fallback_cfg = cfg.model_copy(update={"backend": "stub", "concepts": []})
        engine = WorkflowEngine(fallback_cfg, workflow=workflow, options=options)
        proposal, result = engine.process(path, image=image, common_source=reference)
        engine._quality_backend_error = backend_error
        holder._quality_fallback_engine = engine
    # Requested configuration does not establish that segmentation was invoked.
    actual_backend = getattr(engine, "last_backend", "unknown")
    thresholds = {**load_quality_config(quality_config), **(quality_thresholds or {})}
    masks = getattr(engine, "last_masks", {})
    holder.last_masks = masks  # export/review must use the masks of the actual fallback engine
    assessment = assess_pixels(reference.rgb, proposal, masks=masks, thresholds=thresholds)
    proposed = assessment
    attempts = [{"factor": 1.0, "passed": assessment["passed"], "failures": assessment["failures"]}]
    factor = 1.0
    if not assessment["passed"]:
        if proposal_sink is not None and proposal.shape == reference.rgb.shape and np.isfinite(proposal).all():
            proposal_sink(proposal, result, assessment)
        # Finite malformed values cannot be rehabilitated by multiplication with zero.
        finite = proposal.shape == reference.rgb.shape and np.isfinite(proposal).all()
        factors = thresholds.get("remediation_factors", [0.75, 0.5, 0.25, 0.125, 0.0])
        for factor in factors:
            if factor and not finite:
                continue
            candidate = reference.rgb if factor == 0 else (
                reference.rgb * np.float32(1.0 - factor) + proposal * np.float32(factor))
            assessment = assess_pixels(reference.rgb, candidate, masks=masks, thresholds=thresholds)
            attempts.append({"factor": factor, "passed": assessment["passed"], "failures": assessment["failures"]})
            if assessment["passed"]:
                proposal = candidate
                break
        if not assessment["passed"]:
            raise ValueError("no safe output, including identity: " + "; ".join(assessment["failures"]))
        result.audit["proposed_settings"] = {"global": result.global_settings,
                                             "masks": [m.model_dump() for m in result.masks],
                                             "composition": [m.model_dump() for m in result.composition]}
        if factor == 0:
            result.global_settings, result.masks, result.composition = {}, [], []
        result.notes.append(f"Source-relative safety accepted blend factor {factor:g}; rejected proposal: "
                            + "; ".join(proposed["failures"]))
    _finalize_stats(result, reference, proposal, masks)
    result.audit.update({"workflow": workflow, "requested_backend": cfg.backend,
                         "actual_backend": actual_backend, "backend_fallback": backend_error,
                         "proposal_assessment": proposed, "assessment": assessment,
                         "remediation_factor": factor, "remediation_attempts": attempts,
                         "common_reference_decode": "none", "source_format": Path(path).suffix.lower()[1:]})
    result.needs_review = factor != 1.0 or backend_error is not None
    result.review_reason = ("Safety fallback applied; original proposal retained in audit" if factor != 1 else
                           f"Local backend fallback: {backend_error}" if backend_error else "")
    result.quality_score = 1.0 if assessment["passed"] else 0.0
    result.stage_timings_ms["safe_render_total"] = (time.perf_counter() - start) * 1000
    return proposal, result


def _flatten_record(entry: dict) -> dict:
    row = {k: entry.get(k) for k in ("capture_id", "source", "source_format", "split", "category",
                                    "scene", "group", "status", "fallback", "error", "timing_ms")}
    a = entry.get("assessment", {})
    row["passed"] = a.get("passed", False)
    row["failures"] = "; ".join(a.get("failures", []))
    for k, value in a.get("metrics", {}).items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            row[k] = value
        elif isinstance(value, dict):
            row[k] = json.dumps(value, sort_keys=True, allow_nan=False)
            for subkey, subvalue in value.items():
                if isinstance(subvalue, (str, int, float, bool)) or subvalue is None:
                    row[f"{k}_{subkey}"] = subvalue
        elif isinstance(value, list):
            row[k] = json.dumps(value, allow_nan=False)
    row["metric_scopes"] = json.dumps(a.get("scopes", {}), sort_keys=True)
    row["stage_timings_ms"] = json.dumps(entry.get("stage_timings_ms", {}), sort_keys=True)
    return row


def run_quality(input_dir: str, output_dir: str, cfg: RunConfig | None = None, *,
                workflow: str = "current", options: dict | None = None, resume: bool = False,
                source_manifest: str | None = None, split: str | None = None,
                max_px: int | None = None, limit: int | None = None) -> dict:
    """Process a frozen selection. max_px/limit are explicitly diagnostic, never native validation."""
    from .tonemetrics import write_csv
    from .workflows import WorkflowEngine

    cfg = cfg or RunConfig()
    options = dict(options or {})
    assert_separate_output(input_dir, output_dir)
    out = Path(output_dir).resolve()
    if source_manifest:
        selected = json.loads(Path(source_manifest).read_text())
    else:
        selected = discover_captures(input_dir)
    entries = [e for e in selected["entries"] if split is None or e.get("split") == split]
    if limit is not None:
        entries = entries[:limit]
    if not entries:
        raise ValueError("no selected captures; an empty run cannot pass")
    ids = [e.get("capture_id", "") for e in entries]
    if any(not isinstance(cid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", cid)
           for cid in ids) or len(set(ids)) != len(ids):
        raise ValueError("capture IDs must be unique safe output names")
    for e in entries:
        assert_separate_output(Path(e["source"]).parent, out)
        for companion in e.get("companions", []):
            assert_separate_output(Path(companion["path"]).parent, out)
        if e.get("camera_reference"):
            assert_separate_output(Path(e["camera_reference"]).parent, out)
    evidence = configuration_evidence(cfg, workflow, options)
    config_hash = stable_hash({**evidence, "max_px": max_px, "captures": [
        {key: e.get(key) for key in ("capture_id", "source", "source_sha256", "companions",
                                    "camera_reference", "camera_reference_sha256")}
        for e in entries]})
    previous = None
    if out.exists() and any(out.iterdir()):
        if not resume or not (out / "run.json").is_file():
            raise FileExistsError("output is nonempty; use a new directory or verified --resume")
        previous = json.loads((out / "run.json").read_text())
        if previous.get("config_hash") != config_hash:
            raise ValueError("resume configuration or capture selection differs")
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": 1, "input_dir": str(Path(input_dir).resolve()), "out_dir": str(out),
                "config_hash": config_hash, "configuration": evidence, "selected_inputs": entries,
                "entries": [], "expected": len(entries), "complete": False, "status": "running",
                "scope": "native" if max_px is None else f"diagnostic-{max_px}px", "source_errors": []}
    start = time.perf_counter()
    build = time.perf_counter()
    engine_options = {k: v for k, v in options.items() if k not in ("quality_config", "quality_thresholds")}
    engine = build_safe_engine(cfg, workflow=workflow, options=engine_options)
    manifest["cold_start_ms"] = (time.perf_counter() - build) * 1000
    thresholds = {**load_quality_config(options.get("quality_config")), **options.get("quality_thresholds", {})}
    # Do not replace accepted provenance until setup succeeds. Carry every prior
    # selected record through intermediate checkpoints, including unvisited items.
    records = {e["capture_id"]: e for e in (previous or {}).get("entries", [])
               if e["capture_id"] in ids}
    prior = {}
    for cid, record in records.items():
        accepted = record if record.get("status") == "accepted" else record.get("previous_accepted")
        if (isinstance(accepted, dict) and accepted.get("status") == "accepted"
                and accepted.get("capture_id") == cid):
            prior[cid] = accepted

    def checkpoint(item=None):
        if item is not None:
            records[item["capture_id"]] = item
        manifest["entries"] = [records[cid] for cid in ids if cid in records]
        write_json(out / "run.json", manifest)

    checkpoint()
    for index, e in enumerate(entries, 1):
        item = {**e, "status": "failed"}
        tick = time.perf_counter()
        cid = e["capture_id"]
        suffix = ".tif" if cfg.out_format == "tiff" else ".jpg"
        output = out / (cid + suffix)
        sidecar = out / (cid + ".autoedit.json")
        old = prior.get(cid)
        try:
            if sha256_file(e["source"]) != e.get("source_sha256"):
                raise ValueError("source hash changed or is missing from manifest")
            if old and old.get("status") == "accepted":
                if (sha256_file(output) != old.get("output_sha256") or
                        sha256_file(sidecar) != old.get("sidecar_sha256")):
                    raise ValueError("resume artifacts changed; refusing to overwrite")
                saved_audit = json.loads(sidecar.read_text()).get("audit", {})
                if saved_audit.get("masks_path") and (
                        sha256_file(saved_audit["masks_path"]) != saved_audit.get("masks_sha256")):
                    raise ValueError("resume mask artifact changed; refusing to reuse")
                checkpoint(old)
                print(f"[{index}/{len(entries)}] {cid} verified resume", flush=True)
                continue
            if output.exists() or sidecar.exists():
                raise FileExistsError("unverified existing output; refusing to overwrite")
            decode_start = time.perf_counter()
            reference = load_image(e["source"], decode={"raw_mode": "none"})
            if max_px is not None:
                reference = downscale_long_edge(reference, max_px)
            decode_ms = (time.perf_counter() - decode_start) * 1000
            decode_opts = engine.decode_options() if hasattr(engine, "decode_options") else {"raw_mode": cfg.raw_mode}
            if decode_opts.get("raw_mode", cfg.raw_mode) == "none":
                image = reference
                candidate_decode_ms = 0.0
            else:
                candidate_decode_start = time.perf_counter()
                image = load_image(e["source"], decode=decode_opts)
                if max_px is not None:
                    image = downscale_long_edge(image, max_px)
                candidate_decode_ms = (time.perf_counter() - candidate_decode_start) * 1000
            def sink(rgb, _result, assessment):
                alt = out / "alternatives"
                alt.mkdir(exist_ok=True)
                fd, name = tempfile.mkstemp(prefix=cid + "-rejected-", suffix=".jpg", dir=alt)
                os.close(fd)
                emit.write_image(rgb, name, "jpeg", cfg.quality)
                write_json(Path(name).with_suffix(".json"), assessment)
            rgb, result = render_safe(e["source"], cfg, workflow, image, reference,
                                      options, engine=engine, proposal_sink=sink)
            result.stage_timings_ms["reference_decode"] = decode_ms
            result.stage_timings_ms["candidate_decode"] = candidate_decode_ms
            enc_start = time.perf_counter()
            # Never overwrite the published output while trying encoding fallbacks.
            fd, name = tempfile.mkstemp(prefix=cid + ".checking-", suffix=suffix, dir=out)
            os.close(fd)
            temp = Path(name)
            encoded_attempts = []
            proposal = rgb
            accepted = None
            factors = [1.0] + list(thresholds.get("remediation_factors", [0.75, 0.5, 0.25, 0.125, 0.0]))
            for factor in factors:
                trial = proposal if factor == 1 else reference.rgb if factor == 0 else (
                    reference.rgb * np.float32(1 - factor) + proposal * np.float32(factor))
                emit.write_image(trial, str(temp), cfg.out_format, cfg.quality)
                exported = load_image(str(temp))
                assessment = assess_pixels(reference.rgb, exported.rgb,
                                           masks=getattr(engine, "last_masks", {}), thresholds=thresholds)
                encoded_attempts.append({"factor": factor, "passed": assessment["passed"],
                                         "failures": assessment["failures"]})
                if assessment["passed"]:
                    accepted = factor
                    rgb = exported.rgb
                    break
            if accepted is None:
                # Retain evidence under its explicit diagnostic name; never publish as accepted.
                raise ValueError("encoded identity failed safety: " + "; ".join(assessment["failures"]))
            if output.exists():
                raise FileExistsError("output appeared during processing")
            os.link(temp, output)  # atomic no-clobber publish on the same filesystem
            temp.unlink()
            result.stage_timings_ms["encode_and_verify"] = (time.perf_counter() - enc_start) * 1000
            _finalize_stats(result, reference, rgb, getattr(engine, "last_masks", {}))
            result.audit.update({"capture_id": cid, "source": e["source"], "source_sha256": e["source_sha256"],
                                 "output": str(output), "output_sha256": sha256_file(output),
                                 "config_hash": config_hash, "assessment": assessment,
                                 "export_assessment": assessment, "encoded_attempts": encoded_attempts,
                                 "encoded_remediation_factor": accepted,
                                 "source_format": e["source_format"], "decode_info": getattr(image, "decode_info", {})})
            if getattr(engine, "last_masks", {}):
                masks_path = out / (cid + ".masks.npz")
                if masks_path.exists():
                    raise FileExistsError("unverified existing mask artifact")
                with masks_path.open("xb") as fh:
                    np.savez_compressed(fh, **engine.last_masks)
                result.audit.update(masks_path=str(masks_path), masks_sha256=sha256_file(masks_path))
            if accepted != 1:
                result.notes.append(f"Exported-pixel safety accepted additional source blend {accepted:g}")
                result.needs_review = True
                export_reason = (f"Exported-pixel safety accepted additional source blend {accepted:g}; "
                                 "rejected encoded proposal: " + "; ".join(encoded_attempts[0]["failures"]))
                result.review_reason = "; ".join(reason for reason in (result.review_reason, export_reason) if reason)
                if accepted == 0:
                    result.audit["pre_export_settings"] = result.global_settings
                    result.global_settings, result.masks, result.composition = {}, [], []
            if sha256_file(e["source"]) != e["source_sha256"]:
                raise ValueError("source modified during processing")
            fd, name = tempfile.mkstemp(prefix=cid + ".sidecar-", suffix=".json", dir=out)
            with os.fdopen(fd, "w") as fh:
                fh.write(result.model_dump_json(indent=2))
            try:
                os.link(name, sidecar)  # no-clobber even if a sidecar appeared during processing
            finally:
                os.unlink(name)
            item.update(status="accepted", output=str(output), sidecar=str(sidecar),
                        output_sha256=sha256_file(output), sidecar_sha256=sha256_file(sidecar),
                        assessment=assessment, stage_timings_ms=result.stage_timings_ms,
                        fallback=(result.audit.get("remediation_factor", 1) != 1 or accepted != 1
                                  or bool(result.audit.get("backend_fallback"))),
                        actual_backend=result.audit.get("actual_backend"),
                        backend_fallback=result.audit.get("backend_fallback"))
        except Exception as exc:
            item["error"] = f"{type(exc).__name__}: {exc}"
            if old is not None:
                # Preserve original hashes through transient verification failures.
                # A later resume must recheck them; this never permits overwriting.
                item["previous_accepted"] = old
        item["timing_ms"] = (time.perf_counter() - tick) * 1000
        checkpoint(item)
        print(f"[{index}/{len(entries)}] {Path(e['source']).name}: {item['status']} "
              f"{item.get('error', '')}", flush=True)
    manifest["source_errors"] = verify_sources(entries)
    manifest["failed"] = sum(e["status"] != "accepted" for e in manifest["entries"])
    manifest["processed"] = len(entries) - manifest["failed"]
    manifest["fallbacks"] = sum(bool(e.get("fallback")) for e in manifest["entries"])
    manifest["duration_s"] = time.perf_counter() - start
    manifest["complete"] = not manifest["failed"] and not manifest["source_errors"]
    manifest["status"] = "complete" if manifest["complete"] else "failed"
    write_json(out / "run.json", manifest)
    write_json(out / "quality.json", manifest)
    write_csv([_flatten_record(e) for e in manifest["entries"]], str(out / "quality.csv"))
    return manifest
