"""Batch orchestrator: enumerate a folder, build the pipeline once, process with per-image
error isolation (one bad photo never aborts the batch), resume, and write a run manifest.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter

from . import consistency, emit
from .contract import RunConfig
from .imageio import is_supported
from .pipeline import Pipeline


def list_images(input_dir: str) -> list[str]:
    out = []
    for name in sorted(os.listdir(input_dir)):
        p = os.path.join(input_dir, name)
        if os.path.isfile(p) and is_supported(p):
            out.append(p)
    return out


def run(cfg: RunConfig, input_dir: str, out_dir: str, do_apply: bool = True, resume: bool = False,
        consistent: bool = True) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    images = list_images(input_dir)
    pipeline = Pipeline.build(cfg)

    # Pass 1 (shoot consistency): cheap brightness per image → lighting groups + per-group target.
    # `consistent=False` (--no-consistent) skips it — each photo is then judged in isolation.
    groups: dict[str, tuple[str, float]] = {}
    lums: dict[str, float] = {}
    if consistent and len(images) > 1:
        measured = []
        for path in images:
            try:
                lm = consistency.quick_lum(path)
                lums[path] = lm
                measured.append((path, lm))
            except Exception:
                continue
        groups = consistency.plan_groups(measured)

    processed = 0
    skipped = 0
    failures: list[dict] = []
    scene_mix: Counter = Counter()
    concept_hits: Counter = Counter()
    group_mix: Counter = Counter()
    taken: set[str] = set()          # de-dupe same-stem outputs (IMG_001.dng + IMG_001.jpg)
    t0 = time.perf_counter()

    for i, path in enumerate(images, start=1):
        image_out, sidecar = emit.output_paths(out_dir, path, cfg.out_format, taken)
        if resume and os.path.exists(sidecar):
            skipped += 1
            continue
        gid, target = groups.get(path, ("", None))
        bias = consistency.exposure_bias(lums[path], target) if target is not None and path in lums else 0.0
        try:
            out_rgb, result = pipeline.process_one(path, do_apply=do_apply,
                                                   exposure_bias=bias, batch_group=gid)
            if do_apply and out_rgb is not None:
                emit.write_image(out_rgb, image_out, cfg.out_format, cfg.quality)
            emit.write_sidecar(result, sidecar)
            processed += 1
            scene_mix[result.scene] += 1
            if result.batch_group:
                group_mix[result.batch_group] += 1
            for c in result.concepts:
                if c.present:
                    concept_hits[c.name] += 1
            print(f"[{i}/{len(images)}] {os.path.basename(path)}  scene={result.scene}  "
                  f"regions={[m.target for m in result.masks]}  {result.timing_ms:.0f}ms")
        except Exception as e:  # isolate per-image failures
            failures.append({"image": os.path.basename(path), "error": str(e)})
            print(f"[{i}/{len(images)}] {os.path.basename(path)}  ERROR: {e}")

    manifest = {
        "input_dir": os.path.abspath(input_dir),
        "out_dir": os.path.abspath(out_dir),
        "total": len(images),
        "processed": processed,
        "skipped": skipped,
        "failed": len(failures),
        "failures": failures,
        "scene_mix": dict(scene_mix),
        "concept_hits": dict(concept_hits),
        "batch_groups": dict(group_mix),
        "decider": cfg.decider,
        "backend": cfg.backend,
        "duration_s": round(time.perf_counter() - t0, 2),
        "dry_run": not do_apply,
    }
    with open(os.path.join(out_dir, "run.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)

    print(f"\nDone: {processed} edited, {skipped} skipped, {len(failures)} failed, "
          f"{manifest['duration_s']}s. Scene mix: {dict(scene_mix)}")
    return manifest
