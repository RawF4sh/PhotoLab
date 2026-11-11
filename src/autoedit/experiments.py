"""Frozen, split-aware experiments. Raw proposals and safe deliveries stay separate.

This module does not choose a winner from camera brightness. A candidate cannot be
promoted from proxy evidence or from a holdout containing unresolved hard failures.
"""
from __future__ import annotations

import json
import hashlib
import importlib.metadata
import platform
import select
import os
from pathlib import Path
import resource
import subprocess
import sys
import time
import shutil
import tempfile

import numpy as np

from .captures import sha256_file, stable_hash, verify_sources, discover_captures, assert_separate_output
from .contract import RunConfig
from .imageio import Image, load_image, downscale_long_edge, camera_preview, save_image
from .quality import assess_pixels, load_quality_config
from .quality_runner import write_json, _flatten_record
from .tonemetrics import write_csv
from . import tonemetrics as tm
from .workflows import WorkflowEngine


def candidates() -> list[dict]:
    """Predeclared component substitutions; every ablation names its matched control."""
    base = [{"name": "A-current", "workflow": "current", "config": {}, "options": {}, "primary": True},
            {"name": "B-global", "workflow": "global-first", "config": {}, "options": {}, "primary": True},
            {"name": "C-optimized", "workflow": "optimized", "config": {}, "options": {}, "primary": True}]
    variations = {
        "decode-none": {"raw_mode": "none"}, "decode-libraw-auto": {"raw_mode": "libraw_auto"},
        "decode-preview": {"raw_mode": "preview_ev"}, "decode-preview-headroom": {"raw_mode": "preview_ev_headroom"},
        "decode-tone-target": {"raw_mode": "tone_target"}, "gamma-stats": {"stats_space": "gamma"},
        "shared-format": {"format_policy": "shared"}, "fixed-contrast": {"contrast_mode": "current"},
        "current-curve": {"tone_mapping": "current_curve"}, "reinhard": {"tone_mapping": "reinhard"}, "no-tone-map": {"tone_mapping": "none"},
        "scene-before": {"scene_order": "before_global"}, "no-scene": {"scene_order": "none"},
        "stub-analysis": {"segmentation": True},
        "stub-regions": {"segmentation": True, "regional": True},
        "region-first": {"segmentation": True, "regional": True, "order": "region_first"},
        "vignette": {"segmentation": True, "composition": True, "vignette": True},
        "sky-gradient": {"segmentation": True, "composition": True, "sky_gradient": True},
        "palette": {"segmentation": True, "palette": True},
        "consistency": {"consistency": True},
    }
    for name, options in variations.items():
        control = "B-stub-analysis" if name in ("stub-regions", "vignette", "sky-gradient", "palette") else (
            "B-stub-regions" if name == "region-first" else "B-global")
        base.append({"name": "B-"+name, "workflow": "global-first", "config": {},
                     "options": options, "primary": False, "control": control,
                     "mask_policy": "fixed-source" if options.get("segmentation") else "none"})
    base.extend([
        {"name": "B-stub-resegment-analysis", "workflow": "global-first", "config": {},
         "options": {"segmentation": True}, "primary": False,
         "control": "B-stub-analysis", "mask_policy": "resegment"},
        {"name": "B-stub-resegment-regions", "workflow": "global-first", "config": {},
         "options": {"segmentation": True, "regional": True}, "primary": False,
         "control": "B-stub-regions", "mask_policy": "resegment"},
        {"name": "B-global-jpeg", "workflow": "global-first", "config": {}, "options": {},
         "primary": False, "control": "B-global", "source_variant": "paired_jpeg", "mask_policy": "none"},
        {"name": "B-shared-format-jpeg", "workflow": "global-first", "config": {},
         "options": {"format_policy": "shared"}, "primary": False,
         "control": "B-global-jpeg", "source_variant": "paired_jpeg", "mask_policy": "none"},
        {"name": "A-no-segmentation", "workflow": "current", "config": {"concepts": []}, "options": {}, "primary": False, "control": "A-current"},
        {"name": "B-sam-analysis", "workflow": "global-first", "config": {"backend": "sam3", "device": "mps"}, "options": {"segmentation": True}, "primary": False, "control": "B-stub-resegment-analysis", "mask_policy": "resegment"},
        {"name": "B-sam-regions", "workflow": "global-first", "config": {"backend": "sam3", "device": "mps"}, "options": {"segmentation": True, "regional": True}, "primary": False, "control": "B-sam-analysis", "mask_policy": "resegment"},
    ])
    return base


def code_hashes() -> dict:
    package = Path(__file__).parent
    files = sorted([*package.rglob("*.py"), *package.rglob("*.toml")])
    result = {"package/" + str(p.relative_to(package)): sha256_file(p) for p in files}
    scripts = package.parents[1] / "scripts"
    for name in ("quality_benchmark.py", "quality_workflow.py", "tone_report.py", "blindtest.py"):
        path = scripts / name
        if path.is_file():
            result["scripts/" + name] = sha256_file(path)
    return result


def baseline_hashes(root: Path) -> dict:
    baseline = root / "baseline"
    if not (baseline / "src" / "autoedit" / "pipeline.py").is_file():
        raise ValueError("a frozen baseline snapshot is required before locking")
    return {str(path.relative_to(baseline)): sha256_file(path)
            for path in sorted(baseline.rglob("*"))
            if path.is_file() and path.suffix in {".py", ".toml"}}


def runtime_versions() -> dict:
    """Distribution metadata only: discovering optional versions never imports torch/rawpy."""
    versions = {"python": platform.python_version(), "platform": platform.platform(),
                "machine": platform.machine()}
    for name in ("numpy", "Pillow", "pydantic", "rawpy", "tifffile", "torch", "torchvision", "opencv-python", "opencv-python-headless", "opencv-contrib-python", "opencv-contrib-python-headless", "transformers", "huggingface-hub", "scipy"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def matched_candidates(registered: list[dict], suite: str, names: list[str] | None = None) -> list[dict]:
    """Resolve every transitive control on the same selected captures, control-first."""
    by_name = {s["name"]: s for s in registered}
    if len(by_name) != len(registered):
        raise ValueError("duplicate candidate name")
    if suite not in {"primary", "ablations"}:
        raise ValueError("unknown experiment suite")
    requested = names if names is not None else [s["name"] for s in registered if s["primary"] == (suite == "primary")]
    if not requested or len(requested) != len(set(requested)):
        raise ValueError("select at least one distinct registered candidate")
    result, visiting, done = [], set(), set()
    def add(name):
        if name not in by_name:
            raise ValueError(f"unknown candidate or control: {name}")
        if name in visiting:
            raise ValueError(f"cyclic candidate controls: {name}")
        if name in done:
            return
        visiting.add(name)
        spec = by_name[name]
        if spec.get("control"):
            add(spec["control"])
        visiting.remove(name)
        done.add(name)
        result.append(spec)
    for name in requested:
        add(name)
    return result


def _rss_bytes(value: int) -> int:
    return int(value if sys.platform == "darwin" else value * 1024)


def _validate_partition(manifests: dict) -> None:
    ownership = {}
    for split, manifest in manifests.items():
        seen = set()
        for entry in manifest["entries"]:
            if entry.get("split") != split:
                raise ValueError("entry split disagrees with source manifest")
            if entry["capture_id"] in seen:
                raise ValueError("duplicate capture within partition")
            seen.add(entry["capture_id"])
            identities = [("capture", entry["capture_id"]), ("identity", entry.get("identity")),
                          ("hash", entry.get("source_sha256")), ("hash", entry.get("camera_reference_sha256")),
                          ("group", (entry.get("scene"), entry.get("camera"), entry.get("group")))]
            identities.extend(("hash", c.get("sha256")) for c in entry.get("companions", []))
            for kind, value in identities:
                if not value or (kind == "group" and not value[-1]):
                    continue
                key = (kind, value)
                if key in ownership and ownership[key] != split:
                    raise ValueError(f"{kind} leakage across partitions")
                ownership[key] = split
            if not entry.get("source_sha256"):
                raise ValueError("source hashes are required before locking")


def prepare_experiment(run_dir: str | Path, tuning: str | Path, holdout: str | Path) -> dict:
    """Snapshot the currently installed control and selected local sources, without rendering."""
    root = Path(run_dir).resolve()
    for source in (tuning, holdout):
        assert_separate_output(source, root)
    if root.exists() and any(root.iterdir()):
        raise FileExistsError("prepare requires a new or empty run directory; existing experiments are preserved")
    manifests = {split: discover_captures(source, split=split)
                 for split, source in (("tuning", tuning), ("holdout", holdout))}
    if any(not manifest["entries"] for manifest in manifests.values()):
        raise ValueError("both partitions must contain captures")
    _validate_partition(manifests)
    root.mkdir(parents=True, exist_ok=True)
    package = Path(__file__).parent
    shutil.copytree(package, root/"baseline"/"src"/"autoedit",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    repository = package.parents[1]
    for name in ("pyproject.toml", "AGENTS.md", "README.md", "ARCHITECTURE.md", "APP_GUIDE.md"):
        if (repository/name).is_file():
            shutil.copy2(repository/name, root/"baseline"/name)
    for split, manifest in manifests.items():
        write_json(root/f"{split}-sources.json", manifest)
    report = {"schema_version": 1, "snapshot": "currently installed current pipeline, before this experiment",
              "baseline_hashes": baseline_hashes(root),
              "sources": {split: manifest["counts"] for split, manifest in manifests.items()},
              "source_errors": verify_sources([e for m in manifests.values() for e in m["entries"]])}
    write_json(root/"prepared.json", report)
    return report


def lock_experiment(run_dir: str | Path, *, ablation_groups: int = 24) -> dict:
    root = Path(run_dir)
    path = root / "locked-experiment.json"
    if path.exists():
        raise FileExistsError("experiment already locked; refusing to rewrite holdout protocol")
    manifests = {s: json.loads((root / f"{s}-sources.json").read_text()) for s in ("tuning", "holdout")}
    if ablation_groups < 1:
        raise ValueError("ablation_groups must be positive")
    _validate_partition(manifests)
    all_entries = [e for m in manifests.values() for e in m["entries"]]
    source_errors = verify_sources(all_entries)
    if source_errors:
        raise ValueError(f"source integrity errors before lock: {source_errors[:3]}")
    # Deterministic scene/category/format round-robin, with one capture per burst.
    selections = {}
    for split, manifest in manifests.items():
        pools = {}
        for e in sorted(manifest["entries"], key=lambda e: stable_hash(e["capture_id"])):
            key = (e["scene"], e["category"], e["source_format"])
            pools.setdefault(key, []).append(e)
        selected, seen = [], set()
        while len(selected) < ablation_groups and any(pools.values()):
            for key in sorted(pools):
                while pools[key]:
                    e = pools[key].pop(0)
                    if e["group"] not in seen:
                        selected.append(e["capture_id"]); seen.add(e["group"])
                        break
                if len(selected) >= ablation_groups:
                    break
        selections[split] = selected
    lock = {"schema_version": 2, "candidates": candidates(), "code_hashes": code_hashes(),
            "baseline_hashes": baseline_hashes(root), "runtime_versions": runtime_versions(),
            "source_manifests": {s: sha256_file(root/f"{s}-sources.json") for s in manifests},
            "ablation_captures": selections,
            "winner_policy": "zero hard failures; native accepted outputs required; Pareto comparisons by metric; no aesthetic winner from numbers",
            "unmeasured": ["cross-camera generalization", "human preference", "intentional low-key real corpus",
                           "learned recipe without independent training provenance", "cloud LLM disabled", "missing RAW previews: synthetic coverage; not simulated on real usable previews"]}
    lock["lock_hash"] = stable_hash(lock)
    write_json(path, lock)
    return lock


def _decode_key(entry: dict, opts: dict, max_px: int, decoder: dict) -> str:
    return stable_hash({"source": entry["source_sha256"], "decode": opts, "max_px": max_px,
                        "decoder": decoder, "dependencies": runtime_versions()})


def _read_cache(file: Path, source: str) -> Image:
    with np.load(file, allow_pickle=False) as data:
        info = json.loads(str(data["metadata"].item()))
        rgb = data["rgb"]
        if rgb.ndim != 3 or rgb.shape[-1] != 3 or not np.isfinite(rgb).all():
            raise ValueError("invalid cached decode pixels")
        image = Image(rgb=rgb, path=source, **info)
    image.decode_info = {**image.decode_info, "benchmark_cache_hit": True}
    return image


def _write_cache(file: Path, image: Image) -> None:
    info = {"source_bits": image.source_bits, "exif": image.exif, "decode_ev": image.decode_ev,
            "decode_info": image.decode_info}
    # Shards share the decode cache. Unique temporary names prevent cross-process rename races.
    with tempfile.NamedTemporaryFile(dir=file.parent, suffix=".npz", delete=False) as fh:
        temporary = Path(fh.name)
        np.savez_compressed(fh, rgb=image.rgb, metadata=json.dumps(info))
    try:
        os.replace(temporary, file)
    finally:
        temporary.unlink(missing_ok=True)


def cached_decode(entry: dict, opts: dict, cache: Path, max_px: int, *, half_size: bool = False) -> Image:
    """Decode cache; the caller verifies source bytes before looking up any row."""
    options = {**opts, "half_size": half_size}
    key = _decode_key(entry, options, max_px,
                      {"imageio": sha256_file(Path(__file__).with_name("imageio.py"))})
    cache.mkdir(parents=True, exist_ok=True)
    file = cache / (key + ".npz")
    if file.exists():
        return _read_cache(file, entry["source"])
    image = downscale_long_edge(load_image(entry["source"], decode=options), max_px)
    _write_cache(file, image)
    image.decode_info = {**image.decode_info, "benchmark_cache_hit": False}
    return image


_CONTROL_WORKER = r'''
import json,os,sys,time,resource,numpy as np
from autoedit.contract import RunConfig
from autoedit.pipeline import Pipeline
from autoedit.imageio import Image,load_image,downscale_long_edge
protocol=os.fdopen(int(os.environ["AUTOEDIT_CONTROL_PROTOCOL_FD"]),"w",buffering=1)
pipes={}
for line in sys.stdin:
 tick=time.perf_counter(); q={}
 try:
  q=json.loads(line)
  if q.get("action")=="decode":
   image=downscale_long_edge(load_image(q["source"],decode=q["decode"]),q["max_px"])
   np.save(q["output"],image.rgb)
   record={"metadata":{"source_bits":image.source_bits,"exif":image.exif,"decode_ev":image.decode_ev}}
  else:
   cfg=RunConfig(**q["config"])
   key=json.dumps(cfg.model_dump(),sort_keys=True)
   if key not in pipes: pipes[key]=Pipeline.build(cfg)
   a=np.load(q["input"],allow_pickle=False)
   masks={}
   def observed(r): masks.update(r.union)
   metadata=q["metadata"]
   image=Image(rgb=a,path=q["source"],source_bits=metadata["source_bits"],exif=metadata.get("exif"),decode_ev=metadata.get("decode_ev",0.0))
   rgb,result=pipes[key].process_one(q["source"],image=image,on_resolved=observed)
   np.save(q["output"],rgb); np.savez_compressed(q["masks"],**masks)
   record={"result":result.model_dump(),"resolved_config":cfg.model_dump()}
  record.update(id=q["id"],worker_ms=(time.perf_counter()-tick)*1000,
                worker_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=="darwin" else 1024))
  print(json.dumps(record),file=protocol,flush=True)
 except Exception as e:
  print(json.dumps({"id":q.get("id"),"error":type(e).__name__+": "+str(e)}),file=protocol,flush=True)
'''


class FrozenControl:
    def __init__(self, root: Path, scratch: Path):
        self.root, self.scratch = root.resolve(), scratch.resolve()
        self.scratch.mkdir(parents=True, exist_ok=True)
        self.snapshot_hashes = baseline_hashes(self.root)
        read_fd, write_fd = os.pipe()
        env = {**os.environ, "PYTHONPATH": str(self.root/"baseline"/"src"),
               "AUTOEDIT_CONTROL_PROTOCOL_FD": str(write_fd)}
        try:
            self.proc = subprocess.Popen([sys.executable, "-u", "-c", _CONTROL_WORKER],
                                         cwd=str(self.root/"baseline"), stdin=subprocess.PIPE,
                                         stdout=2, stderr=2, pass_fds=(write_fd,), text=True, env=env)
        except Exception:
            os.close(read_fd)
            raise
        finally:
            os.close(write_fd)
        self.protocol = os.fdopen(read_fd)
        self.sequence = 0
        self.last_worker_stats = {}

    def _request(self, query: dict) -> dict:
        self.sequence += 1
        query = {**query, "id": self.sequence}
        self.proc.stdin.write(json.dumps(query) + "\n")
        self.proc.stdin.flush()
        if not select.select([self.protocol], [], [], 300)[0]:
            raise TimeoutError("frozen control did not respond within 300 seconds")
        line = self.protocol.readline()
        if not line:
            raise RuntimeError("frozen control worker exited")
        record = json.loads(line)
        if record.get("id") != self.sequence:
            raise RuntimeError("frozen control response does not match request")
        if "error" in record:
            raise RuntimeError(record["error"])
        self.last_worker_stats = {k: record[k] for k in ("worker_ms", "worker_peak_rss_bytes") if k in record}
        return record

    def decode(self, entry: dict, max_px: int, half_size: bool, cache: Path) -> Image:
        opts = {"raw_mode": "none", "half_size": half_size}
        key = _decode_key(entry, opts, max_px, {"frozen_baseline": self.snapshot_hashes})
        cache.mkdir(parents=True, exist_ok=True)
        file = cache / (key + ".npz")
        if file.exists():
            return _read_cache(file, entry["source"])
        output = self.scratch / "decode.npy"
        answer = self._request({"action": "decode", "source": entry["source"],
                                "decode": opts, "max_px": max_px, "output": str(output)})
        image = Image(rgb=np.load(output, allow_pickle=False), path=entry["source"], **answer["metadata"],
                      decode_info={"decoder": "frozen_baseline", "raw_mode": "none"})
        _write_cache(file, image)
        image.decode_info["benchmark_cache_hit"] = False
        return image

    def process(self, entry, image, cfg):
        source, output, masks = (self.scratch/name for name in ("input.npy", "output.npy", "masks.npz"))
        np.save(source, image.rgb)
        config = dict(cfg) if isinstance(cfg, dict) else cfg.model_dump()
        for key, value in list(config.items()):
            if key.endswith("_path") and value:
                config[key] = str(self.root/"baseline"/"src"/"autoedit"/"configs"/Path(value).name)
        query = {"action": "process", "source": entry["source"], "input": str(source),
                 "output": str(output), "masks": str(masks), "config": config,
                 "metadata": {"source_bits": image.source_bits, "exif": image.exif,
                              "decode_ev": image.decode_ev}}
        record = self._request(query)
        from .contract import Result
        with np.load(masks, allow_pickle=False) as data:
            mask_data = {key: data[key] for key in data.files}
        result = Result(**record["result"])
        result.audit.update(frozen_baseline=True, resolved_config=record["resolved_config"],
                            decode_info=image.decode_info, **self.last_worker_stats)
        return np.load(output, allow_pickle=False), result, mask_data

    def close(self):
        if self.proc.stdin:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        finally:
            self.protocol.close()


def _mask_digest(masks: dict) -> str:
    return stable_hash({name: {"shape": list(np.asarray(mask).shape),
                              "sha256": hashlib.sha256(np.asarray(mask).tobytes()).hexdigest()}
                        for name, mask in sorted(masks.items())})


def _candidate_source(entry: dict, spec: dict) -> dict | None:
    """Keep capture identity while substituting its explicitly recorded camera JPEG."""
    if spec.get("source_variant") != "paired_jpeg":
        return entry
    if entry.get("source_format", "").casefold() not in {"cr2", "raw"}:
        return None
    path = entry.get("camera_reference")
    digest = entry.get("camera_reference_sha256")
    if not path or not digest:
        return None
    if Path(path).suffix.casefold() not in {".jpg", ".jpeg"}:
        raise ValueError("paired JPEG experiment requires a recorded JPG/JPEG reference")
    return {**entry, "source": path, "source_sha256": digest,
            "source_format": Path(path).suffix.casefold()[1:], "source_variant": "paired_jpeg",
            "primary_source": entry["source"]}


def _evaluation_id(lock: dict, split: str, suite: str, specs: list[dict], entries: list[dict],
                   max_px: int, half_size: bool) -> str:
    return stable_hash({"lock_hash": lock["lock_hash"], "split": split, "suite": suite,
                        "candidates": specs, "captures": [e["capture_id"] for e in entries],
                        "max_px": max_px, "half_size": half_size})


def _resume_rows(record: dict, evaluation_id: str, expected: set) -> dict:
    if record.get("evaluation_id") != evaluation_id:
        raise ValueError("resume candidate/sample/config identity differs from saved evaluation")
    done = {}
    for row in record["rows"]:
        key = (row["capture_id"], row["candidate"])
        if key not in expected or key in done:
            raise ValueError("unexpected or duplicated resumed experiment row")
        if row.get("status") == "measured":
            for field in ("output", "sidecar"):
                path = row.get(field)
                if not path or sha256_file(path) != row.get(field + "_sha256"):
                    raise ValueError(f"prior experiment {field} changed or unavailable")
            json.loads(Path(row["sidecar"]).read_text())
        elif row.get("status") not in {"failed", "not_applicable"}:
            raise ValueError("unknown resumed row status")
        done[key] = row
    return done


def consistency_biases(entries: list[dict], measurements: dict[str, float], maximum: float) -> dict:
    """Exact log2 ratios on source linear-light medians; singleton groups are unavailable."""
    groups = {}
    for entry in entries:
        if entry["capture_id"] in measurements:
            key = (entry.get("scene"), entry.get("camera"), entry["group"])
            groups.setdefault(key, []).append(entry)
    result = {}
    for members in groups.values():
        target = float(np.median([measurements[e["capture_id"]] for e in members]))
        for entry in members:
            measured = measurements[entry["capture_id"]]
            available = len(members) > 1 and measured > 0 and target > 0
            bias = float(np.clip(np.log2(target / measured), -maximum, maximum)) if available else 0.0
            result[entry["capture_id"]] = {"bias_ev": bias, "available": available,
                                             "group_members": len(members), "target_linear_luma": target}
    return result


def evaluate(run_dir: str | Path, split: str, *, suite: str = "primary", max_px: int = 1024,
             half_size: bool = False, resume: bool = False, names: list[str] | None = None,
             shard: tuple[int, int] | None = None) -> dict:
    root = Path(run_dir).resolve()
    lock = json.loads((root/"locked-experiment.json").read_text())
    if stable_hash({k: v for k, v in lock.items() if k != "lock_hash"}) != lock["lock_hash"]:
        raise ValueError("experiment lock contents changed")
    if lock["code_hashes"] != code_hashes() or lock["baseline_hashes"] != baseline_hashes(root):
        raise ValueError("code/config/baseline changed after locking; refusing contaminated evaluation")
    if lock["runtime_versions"] != runtime_versions():
        raise ValueError("dependency/runtime versions changed after locking")
    if split not in ("tuning", "holdout") or max_px < 16:
        raise ValueError("invalid split or image size")
    source_file = root/f"{split}-sources.json"
    if sha256_file(source_file) != lock["source_manifests"][split]:
        raise ValueError("source partition changed after locking")
    entries = json.loads(source_file.read_text())["entries"]
    specs = matched_candidates(lock["candidates"], suite, names)
    consistency_context = entries
    if suite != "primary":
        chosen = set(lock["ablation_captures"][split])
        consistency_requested = any(s["options"].get("consistency") for s in specs)
        if consistency_requested:
            groups = {e["group"] for e in entries if e["capture_id"] in chosen}
            consistency_context = [e for e in entries if e["group"] in groups]
        # Rendering remains on exactly the predeclared representatives. Additional group
        # members supply only source medians, never extra candidate outputs or votes.
        entries = [e for e in entries if e["capture_id"] in chosen]
    if not entries:
        raise ValueError("no captures selected for evaluation")
    all_entries = entries
    whole_id = _evaluation_id(lock, split, suite, specs, all_entries, max_px, half_size)
    tag = f"{split}-{suite}-{max_px}px" + ("-half" if half_size else "")
    if shard is not None:
        index, count = shard
        if count < 1 or not 0 <= index < count:
            raise ValueError("shard must be zero-based INDEX/COUNT with INDEX < COUNT")
        entries = all_entries[index::count]
        tag += f"-shard-{index}-of-{count}"
    dest = root/tag
    dest.mkdir(parents=True, exist_ok=True)
    report_path = dest/"report.json"
    if report_path.exists() and not resume:
        raise FileExistsError("report exists; --resume only reuses the exact verified evaluation")
    evaluation_id = stable_hash({"whole_evaluation_id": whole_id, "shard": shard,
                                 "captures": [e["capture_id"] for e in entries]})
    expected = {(e["capture_id"], spec["name"]) for e in entries for spec in specs}
    record = json.loads(report_path.read_text()) if report_path.exists() else {
        "schema_version": 2, "lock_hash": lock["lock_hash"], "evaluation_id": evaluation_id,
        "whole_evaluation_id": whole_id, "shard": list(shard) if shard is not None else None,
        "whole_capture_ids": [e["capture_id"] for e in all_entries],
        "split": split, "suite": suite, "max_px": max_px, "half_size": half_size,
        "scope": "proxy, not native acceptance", "expected": len(expected),
        "capture_ids": [e["capture_id"] for e in entries], "candidate_specs": specs,
        "evaluation_masks": "frozen-current source masks, identical for every candidate",
        "timing_scope": "reference/masks shared; render separated from decode/cache/encode/audit",
        "memory_scope": "parent/worker process high-water RSS, not isolated per-candidate allocation",
        "rows": [], "complete": False}
    done = _resume_rows(record, evaluation_id, expected)
    engines = {s["name"]: WorkflowEngine(RunConfig(**s["config"]), s["workflow"], s["options"])
               for s in specs if s["workflow"] != "current"}
    control = FrozenControl(root, dest/"scratch")
    cache = root/"decode-cache"
    thresholds = load_quality_config()
    shared_segmenters = {}
    started = time.perf_counter()
    biases = {}
    try:
        if any(s["options"].get("consistency") for s in specs):
            from .imageio import srgb_to_linear, luma
            preprocess_tick = time.perf_counter()
            measurements = {}
            record["consistency_context_errors"] = []
            for entry in consistency_context:
                try:
                    errors = verify_sources([entry])
                    if errors:
                        raise ValueError(f"consistency context source integrity errors: {errors}")
                    image = control.decode(entry, max_px, half_size, cache)
                    measurements[entry["capture_id"]] = float(np.median(luma(srgb_to_linear(image.rgb))))
                except Exception as exc:
                    record["consistency_context_errors"].append({"capture_id": entry["capture_id"],
                                                                 "error": f"{type(exc).__name__}: {exc}"})
            maximum = next(engine.options["global"]["consistency_max_ev"]
                           for name, engine in engines.items() if engine.options["workflow"]["consistency"])
            biases = consistency_biases(consistency_context, measurements, maximum)
            record["consistency_biases"] = biases
            record["consistency_context_capture_ids"] = [e["capture_id"] for e in consistency_context]
            record["consistency_preprocessing_ms"] = (time.perf_counter()-preprocess_tick)*1000
        for i, entry in enumerate(entries, 1):
            remaining = [spec for spec in specs if (entry["capture_id"], spec["name"]) not in done]
            if not remaining:
                continue
            setup_error = None
            reference_ms = mask_ms = 0.0
            try:
                source_errors = verify_sources([entry])
                if source_errors:
                    raise ValueError(f"source integrity errors: {source_errors}")
                tick = time.perf_counter()
                reference = control.decode(entry, max_px, half_size, cache)
                reference_ms = (time.perf_counter()-tick)*1000
                tick = time.perf_counter()
                frozen_rgb, frozen_result, common_masks = control.process(entry, reference, {})
                mask_ms = (time.perf_counter()-tick)*1000
                common_digest = _mask_digest(common_masks)
            except Exception as exc:
                setup_error = f"{type(exc).__name__}: {exc}"
            camera_stats, camera_info = None, {"available": False, "reason": "no real camera reference"}
            source_whole = None
            if not setup_error:
                source_whole = tm.describe(reference.rgb)
                try:
                    if entry.get("camera_reference"):
                        camera_image = downscale_long_edge(load_image(entry["camera_reference"]), max_px).rgb
                        camera_info = {"available": True, "kind": "paired_camera_jpeg",
                                       "path": entry["camera_reference"],
                                       "sha256": sha256_file(entry["camera_reference"])}
                    else:
                        camera_image = camera_preview(entry["source"], max_px, like=reference.rgb)
                        if camera_image is not None:
                            camera_info = {"available": True, "kind": "embedded_camera_preview"}
                    if camera_image is not None:
                        camera_stats = tm.describe(camera_image)
                except Exception as exc:
                    camera_info = {"available": False, "reason": f"camera reference unavailable: {exc}"}
            for spec in remaining:
                name = spec["name"]
                tick = time.perf_counter()
                variant_error = None
                try:
                    processing_entry = _candidate_source(entry, spec)
                except Exception as exc:
                    processing_entry, variant_error = entry, f"{type(exc).__name__}: {exc}"
                row = {**{k: entry.get(k) for k in ("capture_id", "source", "source_format", "split", "category", "scene", "group")},
                       "source_variant": spec.get("source_variant", "selected"),
                       "mask_policy": spec.get("mask_policy", "native"),
                       "candidate": name, "candidate_spec": spec, "control": spec.get("control"),
                       "status": "failed", "reference_decode_ms": reference_ms,
                       "shared_mask_setup_ms": mask_ms}
                if processing_entry is None:
                    row.update(status="not_applicable", reason="no recorded RAW/JPEG pair", timing_ms=0.0)
                    record["rows"].append(row)
                    write_json(report_path, record)
                    continue
                row.update(source=processing_entry["source"], source_format=processing_entry["source_format"],
                           primary_source=entry["source"])
                try:
                    if setup_error or variant_error:
                        raise RuntimeError(setup_error or variant_error)
                    cfg = RunConfig(**spec["config"])
                    decode_tick = time.perf_counter()
                    analysis_reference = (control.decode(processing_entry, max_px, half_size, cache)
                                          if spec.get("source_variant") == "paired_jpeg" else reference)
                    if spec["workflow"] == "current":
                        image = analysis_reference
                        decode_ms = 0.0
                        render_tick = time.perf_counter()
                        if not spec["config"]:
                            rgb, result, masks = frozen_rgb, frozen_result.model_copy(deep=True), common_masks
                            render_ms = mask_ms
                        else:
                            rgb, result, masks = control.process(entry, image, spec["config"])
                            render_ms = (time.perf_counter()-render_tick)*1000
                    else:
                        engine = engines[name]
                        # The correction masks, not merely assessment masks, stay identical
                        # when isolating region/palette/composition/order effects.
                        engine.fixed_masks = common_masks if spec.get("mask_policy") == "fixed-source" else None
                        if spec["options"].get("consistency"):
                            info = biases.get(entry["capture_id"], {"bias_ev": 0.0, "available": False})
                            engine.options["workflow"]["consistency_bias"] = info["bias_ev"]
                            row["consistency"] = info
                        image = cached_decode(processing_entry, engine.decode_options(), cache, max_px, half_size=half_size)
                        decode_ms = (time.perf_counter()-decode_tick)*1000
                        segkey = (cfg.backend, cfg.device)
                        if segkey in shared_segmenters:
                            engine.segmenter = shared_segmenters[segkey]
                        render_tick = time.perf_counter()
                        rgb, result = engine.process(processing_entry["source"], image=image, common_source=analysis_reference)
                        render_ms = (time.perf_counter()-render_tick)*1000
                        masks = engine.last_masks
                        if engine.segmenter is not None:
                            shared_segmenters[segkey] = engine.segmenter
                    audit_tick = time.perf_counter()
                    float_assessment = assess_pixels(analysis_reference.rgb, rgb, masks=common_masks, thresholds=thresholds)
                    float_audit_ms = (time.perf_counter()-audit_tick)*1000
                    folder = dest/name
                    folder.mkdir(exist_ok=True)
                    output = folder/(entry["capture_id"]+".jpg")
                    sidecar = folder/(entry["capture_id"]+".autoedit.json")
                    encode_tick = time.perf_counter()
                    save_image(rgb, str(output), quality=95)
                    exported = load_image(str(output))
                    encode_ms = (time.perf_counter()-encode_tick)*1000
                    audit_tick = time.perf_counter()
                    assessment = assess_pixels(analysis_reference.rgb, exported.rgb, masks=common_masks, thresholds=thresholds)
                    # Preserve the controlled result before conservative eligibility also
                    # incorporates failures measured using candidate-specific masks.
                    fixed_mask_safety = {"passed": assessment["passed"],
                                         "failures": list(assessment["failures"]),
                                         "scope": "exported pixels; common fixed-source assessment masks"}
                    own_assessment = None
                    if masks and _mask_digest(masks) != common_digest:
                        own_assessment = assess_pixels(analysis_reference.rgb, exported.rgb, masks=masks, thresholds=thresholds)
                        for failure in own_assessment["failures"]:
                            if failure not in assessment["failures"]:
                                assessment["failures"].append("candidate_region:"+failure)
                        assessment["passed"] = not assessment["failures"]
                    audit_ms = (time.perf_counter()-audit_tick)*1000 + float_audit_ms
                    camera_comparison = (tm.compare(tm.describe(analysis_reference.rgb), tm.describe(exported.rgb), camera_stats)
                                         if camera_stats is not None else None)
                    output_hash = sha256_file(output)
                    result.audit.update(source=processing_entry["source"], source_sha256=processing_entry["source_sha256"],
                        capture_id=entry["capture_id"], primary_source=entry["source"],
                        source_variant=spec.get("source_variant", "selected"),
                        mask_policy=spec.get("mask_policy", "native"),
                        safety_reference="the candidate's selected source format; fixed neutral-source spatial masks",
                        output=str(output), output_sha256=output_hash, assessment=assessment,
                        candidate=name, candidate_spec=spec, scope=record["scope"],
                        common_mask_digest=common_digest, fixed_mask_safety=fixed_mask_safety,
                        float_assessment=float_assessment,
                        candidate_region_assessment=own_assessment, camera_reference=camera_info,
                        camera_comparison=camera_comparison,
                        camera_scope="whole-frame diagnostic only; not an objective or safety target")
                    sidecar.write_text(result.model_dump_json(indent=2))
                    row.update(status="measured", output=str(output), sidecar=str(sidecar),
                        output_sha256=output_hash, sidecar_sha256=sha256_file(sidecar), assessment=assessment,
                        fixed_mask_safety=fixed_mask_safety,
                        float_failures=float_assessment["failures"], candidate_region_assessment=own_assessment,
                        camera_reference=camera_info, camera_comparison=camera_comparison,
                        decode_info=image.decode_info, source_sha256=processing_entry["source_sha256"],
                        common_mask_digest=common_digest, candidate_mask_digest=_mask_digest(masks),
                        candidate_decode_ms=decode_ms, candidate_render_ms=render_ms,
                        encode_reload_ms=encode_ms, assessment_ms=audit_ms,
                        stage_timings_ms=result.stage_timings_ms or {"frozen_pipeline": result.timing_ms},
                        search=result.audit.get("search", {}), resolved_audit=result.audit,
                        settings=result.global_settings, masks_available=sorted(masks))
                except Exception as exc:
                    row["error"] = f"{type(exc).__name__}: {exc}"
                row["timing_ms"] = (time.perf_counter()-tick)*1000
                row["peak_process_rss_bytes"] = _rss_bytes(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
                row["peak_frozen_worker_rss_bytes"] = control.last_worker_stats.get("worker_peak_rss_bytes")
                record["rows"].append(row)
                write_json(report_path, record)
                print(f"[{split} {i}/{len(entries)}] {name} {Path(entry['source']).name}: "
                      f"{row['status']} {row.get('assessment',{}).get('failures',row.get('error',''))}", flush=True)
    finally:
        control.close()
    integrity_entries = ({e["capture_id"]: e for e in [*entries, *consistency_context]}.values()
                         if biases else entries)
    record["source_errors"] = verify_sources(list(integrity_entries))
    record["integrity_errors"] = []
    if lock["code_hashes"] != code_hashes() or lock["baseline_hashes"] != baseline_hashes(root):
        record["integrity_errors"].append("code/config/baseline changed during evaluation")
    if lock["runtime_versions"] != runtime_versions():
        record["integrity_errors"].append("runtime dependencies changed during evaluation")
    if sha256_file(source_file) != lock["source_manifests"][split]:
        record["integrity_errors"].append("source manifest changed during evaluation")
    record["duration_s"] = record.get("duration_s", 0.0) + time.perf_counter()-started
    measured = sum(row["status"] == "measured" for row in record["rows"])
    record["completed"] = measured
    record["failed"] = sum(row["status"] == "failed" for row in record["rows"])
    record["not_applicable"] = sum(row["status"] == "not_applicable" for row in record["rows"])
    record["execution_complete"] = len(record["rows"]) == record["expected"]
    record["complete"] = (record["execution_complete"] and not record["source_errors"]
                           and not record["integrity_errors"] and not record["failed"]
                           and not record.get("consistency_context_errors"))
    record["summary"] = summarize(record["rows"])
    write_json(report_path, record)
    write_csv([{**_flatten_record(r), "candidate": r["candidate"]} for r in record["rows"]], str(dest/"metrics.csv"))
    return record

def merge_evaluations(run_dir: str | Path, split: str, *, suite="primary", max_px=1024,
                      half_size=False, shard_count: int) -> dict:
    """Combine all finished shards only after checking the complete capture × candidate grid."""
    root = Path(run_dir).resolve()
    lock = json.loads((root/"locked-experiment.json").read_text())
    if stable_hash({k: v for k, v in lock.items() if k != "lock_hash"}) != lock["lock_hash"]:
        raise ValueError("experiment lock contents changed")
    if lock["code_hashes"] != code_hashes() or lock["baseline_hashes"] != baseline_hashes(root):
        raise ValueError("code/config/baseline differs from locked experiment")
    if lock["runtime_versions"] != runtime_versions():
        raise ValueError("runtime differs from locked experiment")
    if shard_count < 1:
        raise ValueError("shard count must be positive")
    tag = f"{split}-{suite}-{max_px}px" + ("-half" if half_size else "")
    reports = [json.loads((root/f"{tag}-shard-{i}-of-{shard_count}"/"report.json").read_text())
               for i in range(shard_count)]
    first = reports[0]
    registered = {spec["name"]: spec for spec in lock["candidates"]}
    if any(registered.get(spec["name"]) != spec for spec in first["candidate_specs"]):
        raise ValueError("shard candidates differ from the locked registry")
    rows = []
    keys = set()
    for index, report in enumerate(reports):
        if report.get("schema_version") != 2 or report.get("shard") != [index, shard_count]:
            raise ValueError("wrong shard identity or schema")
        for field in ("lock_hash", "whole_evaluation_id", "whole_capture_ids", "candidate_specs",
                      "split", "suite", "max_px", "half_size"):
            if report.get(field) != first.get(field):
                raise ValueError(f"shard {field} disagreement")
        if (report["lock_hash"] != lock["lock_hash"] or not report.get("execution_complete")
                or report.get("integrity_errors")):
            raise ValueError("all shards must finish under the same code/runtime protocol")
        expected_local = {(capture, spec["name"]) for capture in report["capture_ids"]
                          for spec in report["candidate_specs"]}
        verified = _resume_rows(report, report["evaluation_id"], expected_local)
        if set(verified) != expected_local or report["expected"] != len(expected_local):
            raise ValueError("incomplete shard capture/candidate matrix")
        if keys.intersection(verified):
            raise ValueError("duplicate rows across shards")
        keys.update(verified)
        rows.extend(report["rows"])
    source_file = root/f"{split}-sources.json"
    if sha256_file(source_file) != lock["source_manifests"][split]:
        raise ValueError("source partition changed")
    all_manifest_entries = json.loads(source_file.read_text())["entries"]
    entries = [e for e in all_manifest_entries if e["capture_id"] in first["whole_capture_ids"]]
    actual_whole = _evaluation_id(lock, split, suite, first["candidate_specs"], entries, max_px, half_size)
    if actual_whole != first["whole_evaluation_id"]:
        raise ValueError("whole evaluation identity mismatch")
    expected = {(capture, spec["name"]) for capture in first["whole_capture_ids"]
                for spec in first["candidate_specs"]}
    if keys != expected:
        raise ValueError("merged shards do not cover expected capture/candidate matrix")
    integrity_ids = set(first["whole_capture_ids"]) | set(first.get("consistency_context_capture_ids", []))
    source_errors = verify_sources([e for e in all_manifest_entries if e["capture_id"] in integrity_ids])
    context_errors = [error for report in reports for error in report.get("consistency_context_errors", [])]
    result = {**first, "shard": None, "merged_shards": shard_count, "expected": len(expected),
              "capture_ids": first["whole_capture_ids"], "rows": sorted(rows, key=lambda row: (row["capture_id"], row["candidate"])),
              "evaluation_id": stable_hash({"whole_evaluation_id": actual_whole, "shard": None,
                                            "captures": first["whole_capture_ids"]}),
              "source_errors": source_errors,
              "execution_complete": True,
              "consistency_context_errors": context_errors,
              "complete": not source_errors and not context_errors and all(row["status"] in {"measured", "not_applicable"} for row in rows),
              "completed": sum(row["status"] == "measured" for row in rows),
              "failed": sum(row["status"] == "failed" for row in rows),
              "not_applicable": sum(row["status"] == "not_applicable" for row in rows),
              "duration_s": sum(report.get("duration_s", 0) for report in reports),
              "duration_scope": "sum of shard process durations; not elapsed wall time"}
    result["summary"] = summarize(result["rows"])
    destination = root/tag
    destination.mkdir(exist_ok=True)
    if (destination/"report.json").exists():
        raise FileExistsError("combined report already exists; refusing overwrite")
    write_json(destination/"report.json", result)
    write_csv([{**_flatten_record(row), "candidate": row["candidate"]} for row in result["rows"]], str(destination/"metrics.csv"))
    return result


def summarize(rows: list[dict]) -> dict:
    summary = {}
    for name in sorted({r["candidate"] for r in rows}):
        rr = [r for r in rows if r["candidate"] == name]
        good = [r for r in rr if r.get("assessment",{}).get("passed")]
        failures = {}
        for r in rr:
            for reason in r.get("assessment",{}).get("failures", []):
                failures[reason] = failures.get(reason,0)+1
        def metric(path):
            out=[]
            for r in rr:
                v=r.get("assessment",{}).get("metrics",{})
                for key in path:
                    v = v.get(key) if isinstance(v,dict) else None
                if isinstance(v,(float,int)): out.append(v)
            return float(np.median(out)) if out else None
        camera_gaps = [r["camera_comparison"]["ev_vs_camera"] for r in rr if r.get("camera_comparison")]
        measured = [r for r in rr if r["status"] == "measured"]
        summary[name] = {"n":len(measured), "scheduled":len(rr),
                         "not_applicable":sum(r["status"] == "not_applicable" for r in rr),
                         "source_variant":rr[0].get("source_variant", "selected"),
                         "camera_reference_count": len(camera_gaps),
                         "median_ev_vs_camera": float(np.median(camera_gaps)) if camera_gaps else None,"safe":len(good),"errors":sum(r["status"]=="failed" for r in rr),
                         "failures":failures,"median_ms":float(np.median([r["timing_ms"] for r in rr])),
                         "median_render_ms":float(np.median([r["candidate_render_ms"] for r in rr if "candidate_render_ms" in r])) if any("candidate_render_ms" in r for r in rr) else None,
                         "median_decode_cache_ms":float(np.median([r["candidate_decode_ms"] for r in rr if "candidate_decode_ms" in r])) if any("candidate_decode_ms" in r for r in rr) else None,
                         "median_fg_change":metric(["comparison","d_fg_p50"]),
                         "median_dark_coverage_change":metric(["comparison","d_dark_cov"]),
                         "median_color_change":metric(["chroma_delta"]),
                         "median_highlight_texture_retention":metric(["highlight_texture","minimum_retention"]),
                         "eligible_proxy_safety":bool(measured) and len(good)==len(measured) and not any(r["status"]=="failed" for r in rr),"promotion_eligible":False}
    return summary
