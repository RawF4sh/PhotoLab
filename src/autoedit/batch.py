"""Batch orchestrator: enumerate a folder, build the pipeline once, process with per-image
error isolation (one bad photo never aborts the batch), resume, and write a run manifest.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool

from . import consistency, emit, reveal
from .contract import RevealReport, RunConfig
from .edit import par
from .imageio import is_supported
from .pipeline import Pipeline
from .segment import DiskMaskCache


def list_images(input_dir: str) -> list[str]:
    out = []
    for name in sorted(os.listdir(input_dir)):
        p = os.path.join(input_dir, name)
        if os.path.isfile(p) and is_supported(p):
            out.append(p)
    return out


def _effective_workers(cfg: RunConfig, workers: int) -> tuple[int, str]:
    """(count, reason it was reduced or "")."""
    n = workers if workers > 0 else min(4, max(1, (os.cpu_count() or 2) // 2))
    if n > 1 and cfg.decider == "llm":
        return 1, "the LLM decider counts its calls and budget per process"
    if n > 1 and cfg.backend != "stub" and not cfg.mask_cache:
        return 1, "render workers need the mask cache with a model backend"
    return n, ""


class _NoModel:
    """Stands in for the segmenter inside a render worker: the parent fills the mask cache. It carries
    the parent backend's `open_vocabulary`, or the worker would skip the reveal words (F21)."""

    def __init__(self, open_vocabulary: bool = False):
        self.open_vocabulary = open_vocabulary

    def segment(self, img, concepts):
        raise RuntimeError("mask cache miss in a render worker: " + ", ".join(concepts))


_WORKER: dict = {}


def _worker_init(cfg: RunConfig, out_dir: str, threads: int, open_vocabulary: bool = False) -> None:
    # Build with the stub backend: a render worker reads masks from the cache and must not load a
    # model, and building the real backend would import torch in every worker.
    pipe = Pipeline.build(cfg.model_copy(update={"backend": "stub"}))
    pipe.cfg = cfg
    par.set_threads(threads)
    try:
        import cv2
        cv2.setNumThreads(threads)
    except Exception:
        pass
    pipe.use_mask_cache(out_dir)
    if isinstance(pipe.segmenter, DiskMaskCache):
        pipe.segmenter.inner = _NoModel(open_vocabulary)
    _WORKER["pipe"] = pipe


def _render_with(pipe, path: str, image_out: str, sidecar: str, bias: float, gid: str,
                 do_apply: bool, wb: tuple[float, float] = (0.0, 0.0)) -> dict:
    out_rgb, result = pipe.process_one(path, do_apply=do_apply, exposure_bias=bias, batch_group=gid,
                                       wb_bias=wb)
    emit.write_outputs(out_rgb if do_apply else None, result, image_out, sidecar, pipe.cfg, path)
    return {"scene": result.scene, "group": result.batch_group, "ms": result.timing_ms,
            "regions": [m.target for m in result.masks],
            "present": [c.name for c in result.concepts if c.present]}


def _render(path: str, image_out: str, sidecar: str, bias: float, gid: str, do_apply: bool,
            wb: tuple[float, float] = (0.0, 0.0)) -> dict:
    return _render_with(_WORKER["pipe"], path, image_out, sidecar, bias, gid, do_apply, wb)


def _render_here(pipe, jobs, do_apply: bool, record, fail) -> None:
    """Render `jobs` ((index, path, image_out, sidecar, bias, group[, (temp, tint)]) each) one
    after the other in this process. One bad photo calls `fail(index, path, error)` and the rest go on."""
    for i, path, image_out, sidecar, bias, gid, *wb in jobs:
        try:
            r = _render_with(pipe, path, image_out, sidecar, bias, gid, do_apply, *wb)
        except Exception as e:
            fail(i, path, e)
        else:
            record(i, path, r)


def reveal_line(block: dict) -> str:
    """The last line of a run with the reveal check on."""
    if block["picture"] == "not_run":
        line = "Reveal check: the pictures were not checked (needs SAM 3); file metadata was."
    else:
        kinds = ", ".join(f"{k} {n}" for k, n in sorted(block["by_kind"].items(), key=lambda t: -t[1]))
        line = (f"Reveal check: {block['with_findings']} of {block['photos']} photos have findings"
                + (f" ({kinds})" if kinds else "") + ". See reveal.html.")
    if block.get("hide_asked"):      # F22: a hide request always says what it did
        h = block.get("hidden")
        asked = ", ".join(block["hide_asked"])
        if h:
            kinds = ", ".join(f"{k} {n}" for k, n in sorted(h["by_kind"].items(), key=lambda t: -t[1]))
            line += f" Hidden on request: {h['regions']} regions on {h['photos']} photos ({kinds})."
        else:
            line += f" Hide asked for {asked}: nothing was hidden."
        if block.get("not_hidden"):
            line += (f" {block['not_hidden']} photos were skipped as already made with another hide setting; "
                     "run again without --resume to hide them.")
    n = block.get("unchecked", 0)
    return line + (f" {n} photos not checked (made before the check was on)." if n else "")


def run(cfg: RunConfig, input_dir: str, out_dir: str, do_apply: bool = True, resume: bool = False,
        consistent: bool = True, workers: int | None = None) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    images = list_images(input_dir)
    pipeline = Pipeline.build(cfg)
    cfg = pipeline.cfg   # "auto" is resolved to the backend that actually runs
    n_workers, why = _effective_workers(cfg, cfg.workers if workers is None else workers)
    if why:
        print(f"Using 1 render worker: {why}.")
    pipeline.use_mask_cache(out_dir)

    # Shoot consistency (see consistency.py): `consistent=False` (--no-consistent) skips it, each
    # photo is then judged in isolation. With several workers the plan is made here, in the parent.
    say = None
    if consistent and cfg.consistency == "bursts":
        # The plan reads every capture time and decides each burst photo once before the first
        # photo renders: say so, or a large folder looks hung.
        say = lambda msg: print(f"Bursts mode: {msg}", flush=True)  # noqa: E731
        say(f"looking for bursts in {len(images)} photos")
    elif consistent and cfg.consistency_wb:
        say = lambda msg: print(f"Consistency: {msg}", flush=True)  # noqa: E731
    biases = consistency.plan(images, cfg, pipeline, progress=say) if consistent else {}

    processed = 0
    skipped = 0
    failures: list[dict] = []
    scene_mix: Counter = Counter()
    concept_hits: Counter = Counter()
    group_mix: Counter = Counter()
    taken: set[str] = set()          # de-dupe same-stem outputs (IMG_001.dng + IMG_001.jpg)
    sources = emit.source_ids(images)   # no export may land on any input of this run
    t0 = time.perf_counter()

    par_fail: list[tuple[int, dict]] = []
    sidecars: list[str] = []         # every photo's sidecar, also of a photo a resume skips (the colour report)
    made: list[tuple[str, str, str]] = []   # (source, export, sidecar) of every photo, for the reveal page
    pool = None
    pending: dict = {}               # future -> (index, path, image_out, sidecar, bias, group)

    lost: list[tuple] = []           # jobs a dead worker left without a result
    broken = False

    def _record(i: int, path: str, r: dict) -> None:
        nonlocal processed
        processed += 1
        scene_mix[r["scene"]] += 1
        if r["group"]:
            group_mix[r["group"]] += 1
        for c in r["present"]:
            concept_hits[c] += 1
        print(f"[{i}/{len(images)}] {os.path.basename(path)}  scene={r['scene']}  "
              f"regions={r['regions']}  {r['ms']:.0f}ms")

    def _fail(i: int, path: str, e: Exception) -> None:
        par_fail.append((i, {"image": os.path.basename(path), "error": str(e)}))
        print(f"[{i}/{len(images)}] {os.path.basename(path)}  ERROR: {e}")

    def _collect(done) -> None:
        for fut in done:
            job = pending.pop(fut)
            try:
                r = fut.result()
            except BrokenProcessPool:
                lost.append(job)
            except Exception as e:  # a worker error fails that photo only
                _fail(job[0], job[1], e)
            else:
                _record(job[0], job[1], r)

    def _fall_back() -> None:
        """A worker died: every photo without a result is rendered here, one after the other."""
        nonlocal broken
        broken = True
        while pending:
            _collect(wait(list(pending), return_when=FIRST_COMPLETED).done)
        print(f"a render worker died; finishing {len(lost)} photos in this process")
        todo = sorted(lost, key=lambda j: j[0])
        lost.clear()
        pool.shutdown(wait=False, cancel_futures=True)
        _render_here(pipeline, todo, do_apply, _record, _fail)

    if n_workers > 1:
        pool = ProcessPoolExecutor(max_workers=n_workers, mp_context=multiprocessing.get_context("spawn"),
                                   initializer=_worker_init,
                                   initargs=(cfg, out_dir, max(1, par.threads() // n_workers),
                                             bool(getattr(pipeline.segmenter, "open_vocabulary", False))))

    try:
        for i, path in enumerate(images, start=1):
            try:
                image_out, sidecar = emit.output_paths(
                    out_dir, path, cfg.out_format, taken, name=cfg.out_name, subdir=cfg.out_subdir,
                    fields={"style": cfg.style, "n": i}, sources=sources)
            except ValueError as e:      # a name problem fails this photo, not the run
                if pool is not None:
                    _fail(i, path, e)
                else:
                    failures.append({"image": os.path.basename(path), "error": str(e)})
                    print(f"[{i}/{len(images)}] {os.path.basename(path)}  ERROR: {e}")
                continue
            sidecars.append(sidecar)
            made.append((path, image_out, sidecar))
            if resume and os.path.exists(sidecar):
                skipped += 1
                continue
            b = biases.get(path, consistency.Bias())
            bias, gid, wb = b.exposure, b.group, (b.temp, b.tint)
            if pool is not None:
                job = (i, path, image_out, sidecar, bias, gid, wb)
                if broken:
                    _render_here(pipeline, [job], do_apply, _record, _fail)
                    continue
                while len(pending) >= 2 * n_workers:   # bounded: a cancelled run leaves few photos behind
                    _collect(wait(list(pending), return_when=FIRST_COMPLETED).done)
                try:
                    if cfg.backend != "stub":
                        pipeline.presegment(path)   # fills the mask cache; the worker never loads the model
                    pending[pool.submit(_render, path, image_out, sidecar, bias, gid, do_apply, wb)] = job
                except BrokenProcessPool:
                    lost.append(job)
                except Exception as e:
                    _fail(i, path, e)
                if pending:
                    _collect(wait(list(pending), timeout=0, return_when=FIRST_COMPLETED).done)
                if lost:
                    _fall_back()
                continue
            try:
                out_rgb, result = pipeline.process_one(path, do_apply=do_apply,
                                                       exposure_bias=bias, batch_group=gid,
                                                       wb_bias=wb)
                emit.write_outputs(out_rgb if do_apply else None, result, image_out, sidecar, cfg, path)
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

        if pool is not None and not broken:
            while pending:
                _collect(wait(list(pending), return_when=FIRST_COMPLETED).done)
            if lost:
                _fall_back()
            else:
                pool.shutdown(wait=True)
    except BaseException:
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
        raise
    if pool is not None:
        failures = [f for _, f in sorted(par_fail, key=lambda t: t[0])]

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
        "workers": n_workers,
    }
    if cfg.colour_report and do_apply:
        manifest["colour_report"] = consistency.colour_report(
            sidecars, ((pipeline.rules or {}).get("consistency") or {}).get(
                "wb_min_confidence", consistency.BURST_DEFAULTS["wb_min_confidence"]))
    items: list = []
    unchecked: list[str] = []          # sidecars made with the check off (a resumed run keeps them)
    not_hidden = 0                     # F22: photos a resume skipped that were made without, or with another kind or method of, the hide that is asked for now
    warped: set[str] = set()           # photos whose export is straightened or cropped: boxes would not fit
    if pipeline.reveal is not None:    # read back from the sidecars, so a resumed run counts the earlier photos too
        for src, img_out, sc in made:
            try:
                with open(sc) as fh:
                    doc = json.load(fh)
                name = os.path.basename(src)
                if cfg.hide and (sorted(((doc.get("audit") or {}).get("run_config") or {}).get("hide") or []) != sorted(cfg.hide)
                                 or any(h.get("method") != (cfg.hide_method or pipeline.reveal.hide.method)
                                        for h in (doc.get("reveal") or {}).get("hidden") or [])):
                    not_hidden += 1
                if doc.get("reveal"):
                    items.append((name, img_out if os.path.exists(img_out) else src,
                                  RevealReport.model_validate(doc["reveal"])))
                    if doc.get("geometry") and os.path.exists(img_out):
                        warped.add(name)
                else:
                    unchecked.append(name)
            except (OSError, ValueError):
                pass
        manifest["reveal"] = {**reveal.summary([r for _, _, r in items]), "unchecked": len(unchecked)}
        if cfg.hide:
            manifest["reveal"].update(hide_asked=list(cfg.hide), not_hidden=not_hidden)
    with open(os.path.join(out_dir, "run.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)

    print(f"\nDone: {processed} edited, {skipped} skipped, {len(failures)} failed, "
          f"{manifest['duration_s']}s. Scene mix: {dict(scene_mix)}")
    if pipeline.reveal is not None:
        reveal.write_page(out_dir, items, pipeline.reveal, unchecked, warped)
    if "colour_report" in manifest:
        print(f"Colour report ({consistency.DELTA_E}):")
        for line in consistency.colour_lines(manifest["colour_report"]) or \
                ["  no group has two photos with a measured subject"]:
            print(line)
    if "reveal" in manifest:
        print(reveal_line(manifest["reveal"]))
    return manifest
