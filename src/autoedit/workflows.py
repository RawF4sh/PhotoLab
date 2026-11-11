"""Isolated, deterministic alternatives to the production region-first pipeline.

`current` delegates without changing pixels. `global-first` performs bounded display
normalization followed by optional regional refinement. `optimized` uses the same
renderer with a finite coordinate search. All photographic tuning lives in workflows.toml.
No candidate claims aesthetic superiority; common-source/encoded safety is also checked
by the caller responsible for writing the output.
"""
from __future__ import annotations

import os
import re
import time
import tomllib
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from . import emit, stats, scene
from .contract import RunConfig
from .edit import ops
from .edit.gates import chroma_guard
from .imageio import Image, downscale_long_edge, is_raw, load_image, luma, raw_opts_from, srgb_to_linear, linear_to_srgb
from .quality import assess_pixels, prepare_quality_source

WORKFLOWS = ("current", "global-first", "optimized")
_SEARCH_KEYS = ("exposure", "shadows", "highlights", "whites", "blacks", "contrast",
                "temp", "tint", "vibrance", "dehaze")


def summarize_stage_recovery(checkpoints) -> dict:
    """Continuous proposal-only diagnostic; never changes pixels or a safety decision.

    Compare each recorded candidate-source-relative foreground delta with the final
    recorded delta. This exposes erased intermediate recovery even when the result
    remains brighter than its source and correctly passes the source-relative gate.
    """
    rows = ([{"stage": name, **values} for name, values in checkpoints.items()]
            if isinstance(checkpoints, dict) else list(checkpoints or []))
    values = []
    for row in rows:
        delta = row.get("assessment", {}).get("metrics", {}).get("comparison", {}).get("d_fg_p50")
        if isinstance(delta, (int, float)) and np.isfinite(delta):
            values.append((row.get("stage", "unknown"), float(delta)))
    base = {"available": bool(values), "scope": "proposal stages; candidate-source-relative proxy foreground",
            "hard_gate_applied": False, "stage_count": len(values)}
    if not values:
        return base
    peak_stage, peak = max(values, key=lambda item: item[1])
    final_stage, final = next((item for item in reversed(values) if item[0] == "final_float"), values[-1])
    recovered = max(0.0, peak)
    lost = max(0.0, recovered-final) if recovered > 0 else 0.0
    return {**base, "peak_stage": peak_stage, "final_stage": final_stage,
            "peak_foreground_delta": peak, "final_foreground_delta": final,
            "recovered_then_lost": lost,
            "fraction_of_recovery_lost": lost/recovered if recovered > 0 else None}


def load_workflow_options(options: dict | None = None) -> dict:
    """Load flat option names; nested section overrides are accepted for TOML parity."""
    supplied = dict(options or {})
    path = supplied.pop("config_path", None)
    default = Path(__file__).with_name("configs") / "workflows.toml"
    with default.open("rb") as fh:
        sections = tomllib.load(fh)
    if path:
        with Path(path).open("rb") as fh:
            for section, values in tomllib.load(fh).items():
                sections.setdefault(section, {}).update(values)
    result = {"workflow": dict(sections["workflow"]), "global": dict(sections["global"]),
              "regional": dict(sections["regional"]), "search": dict(sections["search"]),
              "objective": dict(sections["objective"])}
    for key, value in supplied.items():
        if key in result and isinstance(value, dict):
            unknown = set(value) - set(result[key])
            if unknown:
                raise ValueError(f"unknown {key} workflow options: {sorted(unknown)}")
            result[key].update(value)
        elif key in result["workflow"]:
            result["workflow"][key] = value
        elif key in result["search"]:
            result["search"][key] = value
        else:
            raise ValueError(f"unknown workflow option {key!r}")
    for key, choices in {
        "stats_space": ("linear", "gamma"), "format_policy": ("shared", "separate"),
        "order": ("global_first", "region_first"),
        "segment_order": ("before_global", "after_global"),
        "scene_order": ("none", "before_global", "after_global"),
        "contrast_mode": ("adaptive", "current", "none"),
        "tone_mapping": ("shoulder", "current_curve", "reinhard", "none"),
    }.items():
        if result["workflow"][key] not in choices:
            raise ValueError(f"{key} must be one of {choices}")
    from .imageio import RAW_MODES
    if result["workflow"]["raw_mode"] not in RAW_MODES:
        raise ValueError("unknown experimental RAW mode")
    for section, values in result.items():
        for key, value in values.items():
            if isinstance(value, (int, float)) and not np.isfinite(value):
                raise ValueError(f"{section}.{key} must be finite")
    search = result["search"]
    for key in _SEARCH_KEYS:
        lo, hi = search[key+"_min"], search[key+"_max"]
        op_lo, op_hi = ops.OP_BOUNDS[key]
        if not op_lo <= lo <= hi <= op_hi or search[key+"_step"] < 0:
            raise ValueError(f"invalid search bounds/step for {key}")
    if not 0 < search["step_decay"] <= 1:
        raise ValueError("search.step_decay must be in (0, 1]")
    if any(value < 0 for value in result["objective"].values()):
        raise ValueError("objective weights must be nonnegative")
    if not 0 <= result["global"]["reinhard_strength"] <= 1 or result["global"]["reinhard_white_point"] < 1:
        raise ValueError("Reinhard strength must be [0, 1] and white point at least 1")
    # These are resource ceilings, not tunable photographic parameters.
    for key, upper in (("max_passes", 6), ("max_evaluations", 144), ("proxy_edge", 512)):
        search[key] = max(1, min(upper, int(search[key])))
    search["proxy_edge"] = max(8, search["proxy_edge"])
    search["max_seconds"] = max(0.0, min(30.0, float(search["max_seconds"])))
    return result


def adaptive_contrast(rgb: np.ndarray, amount: float, pivot: float) -> np.ndarray:
    """Affine contrast around a measured pivot, bounded before any channel would clip.

    The common slope preserves channel ordering; an empty amount is an exact identity.
    The bound is derived from the actual extrema, not an arbitrary universal brightness.
    """
    if not amount:
        return rgb
    pivot = float(np.clip(pivot, 0.0, 1.0))
    factor = max(0.0, 1.0 + float(amount) / 100.0)
    lo, hi = float(np.min(rgb)), float(np.max(rgb))
    if hi > pivot:
        factor = min(factor, (1.0 - pivot) / (hi - pivot))
    if lo < pivot:
        factor = min(factor, pivot / (pivot - lo))
    return ((rgb - pivot) * factor + pivot).astype(np.float32)


def photographic_reinhard(linear_rgb: np.ndarray, strength: float, white_point: float,
                          ceiling: float = 1.0) -> np.ndarray:
    """Extended Reinhard luminance mapping in linear light, preserving RGB ratios.

    L' = L(1 + L/W²)/(1 + L). The strength blend is monotonic for W>=1.
    If chromatic pixels exceed the display ceiling, one source-dependent frame scale
    keeps every channel in range; per-pixel clipping would flatten highlight texture.
    Zero strength is a strict no-op, including black and HDR inputs.
    """
    if not strength:
        return linear_rgb
    if not 0 <= strength <= 1 or white_point < 1 or not 0 < ceiling <= 1:
        raise ValueError("invalid Reinhard strength, white point, or ceiling")
    lum = luma(linear_rgb)
    target = lum + strength*(lum*(1.0+lum/(white_point*white_point))/(1.0+lum)-lum)
    ratio = np.ones_like(lum)
    np.divide(target, lum, out=ratio, where=lum > 0)
    out = linear_rgb*ratio[..., None]
    peak = float(np.max(out))
    if peak > ceiling:
        out = out*(ceiling/peak)
    return out.astype(np.float32)


def _headroom(rgb: np.ndarray, level: float, percentile: float, margin: float) -> float:
    bright = float(np.percentile(srgb_to_linear(rgb).max(axis=-1), percentile))
    white = float(srgb_to_linear(np.float32(level)))
    return max(0.0, float(np.log2(white / max(bright, 1e-8))) - margin)


def _resize_mask(mask: np.ndarray, hw: tuple[int, int]) -> np.ndarray:
    """Nearest sampling preserves categorical masks through proxy changes."""
    mask = np.asarray(mask, dtype=bool)
    if mask.ndim != 2 or not mask.size:
        raise ValueError("fixed masks must be nonempty 2D arrays")
    yy = np.minimum(np.arange(hw[0])*mask.shape[0]//hw[0], mask.shape[0]-1)
    xx = np.minimum(np.arange(hw[1])*mask.shape[1]//hw[1], mask.shape[1]-1)
    return mask[np.ix_(yy, xx)]


def _region_rgb(source: np.ndarray, alpha: np.ndarray, settings: dict,
                conf: dict) -> tuple[np.ndarray, dict]:
    """Apply local light only after bounding against all claimed highlight pixels.

    Unlike a final clipping check, this prevents a regional exposure multiply from
    destroying snow texture before later global rolloff can conceal the damage.
    """
    selected = source[alpha > 0]
    actual = dict(settings)
    if not selected.size:
        return source, {}
    room = _headroom(selected.reshape(1, -1, 3), conf["highlight_level"], 100.0,
                     conf["headroom_margin_ev"])
    actual["exposure"] = min(max(0.0, actual.get("exposure", 0.0)), room)
    rendered = ops.apply_ops(source, actual)
    return source + alpha[..., None] * (rendered - source), actual


class WorkflowEngine:
    """Resident candidate engine; callers own source selection, writes, and exported gates."""

    def __init__(self, cfg: RunConfig, workflow: str = "current", options: dict | None = None):
        if workflow not in WORKFLOWS:
            raise ValueError(f"workflow must be one of {WORKFLOWS}")
        self.cfg = cfg
        self.workflow = workflow
        options = dict(options or {})
        self.fixed_masks = options.pop("fixed_masks", None)
        self.options = load_workflow_options(options)
        w = self.options["workflow"]
        prompt = (cfg.prompt or "").lower()
        self.preserve_exposure = bool(w["preserve_exposure"] or any(
            re.search(r"(?<!\w)"+re.escape(str(term).lower())+r"(?!\w)", prompt)
            for term in w["preserve_terms"]))
        self._explicit_raw_mode = bool(options and ("raw_mode" in options or
                                        "raw_mode" in options.get("workflow", {})))
        self._pipe = None
        self._segmenter = None
        self.last_masks: dict = {}
        self.last_backend = "none"
        self.segmentation_events: list[dict] = []
        if workflow == "current":
            from .pipeline import Pipeline
            self._pipe = Pipeline.build(cfg)
            if self.fixed_masks is not None:
                fixed = self.fixed_masks
                class FixedSegmenter:
                    name = "fixed-masks"

                    def segment(self, image, concepts):
                        from .segment.base import RegionMask
                        return [RegionMask(name=name, mask=mask, score=1.0,
                                           coverage=float(mask.mean()), instance_count=1)
                                for name in concepts if name in fixed
                                for mask in [_resize_mask(fixed[name], image.rgb.shape[:2])]]
                self._pipe.segmenter = FixedSegmenter()

    @property
    def segmenter(self):
        return self._pipe.segmenter if self._pipe is not None else self._segmenter

    @segmenter.setter
    def segmenter(self, value):
        if self._pipe is not None:
            self._pipe.segmenter = value
        else:
            self._segmenter = value

    def decode_options(self) -> dict:
        options = raw_opts_from(self.cfg)
        if self.workflow != "current":
            if self._explicit_raw_mode or self.cfg.raw_mode == "none":
                options["raw_mode"] = self.options["workflow"]["raw_mode"]
            options["raw_fallback_ev"] = self.cfg.raw_fallback_ev
            if self.preserve_exposure and options["raw_mode"] not in ("none", "linear_srgb"):
                options["raw_mode"] = "linear_srgb"
        return options

    def _segment(self, proxy: Image, enabled: bool = True):
        from .concepts import load_concepts
        from .segment import make_segmenter, resolve
        from .styles import load_styles, style_for
        presets, meta = load_concepts(self.cfg.concepts_path)
        style = style_for(self.cfg.style, load_styles(self.cfg.styles_path))
        concepts = self.cfg.concepts
        if concepts is None:
            concepts = presets.get(self.cfg.preset, []) if self.cfg.preset else style.concepts
        concepts = list(concepts or []) if enabled and self.options["workflow"]["segmentation"] else []
        if concepts and self._segmenter is None:
            self._segmenter = make_segmenter(self.cfg.backend, self.cfg.device)
        if self.fixed_masks is not None and enabled:
            self.last_backend = "fixed-masks"
            self.segmentation_events.append({"backend": "fixed-masks", "concepts": sorted(self.fixed_masks)})
            from .segment.base import RegionMask
            concepts = list(self.fixed_masks)
            raw = []
            for name, mask in self.fixed_masks.items():
                mask = _resize_mask(mask, proxy.rgb.shape[:2])
                raw.append(RegionMask(name=name, mask=mask, score=1.0,
                                      coverage=float(mask.mean()), instance_count=1))
        else:
            raw = self._segmenter.segment(proxy, concepts) if concepts else []
            if concepts:
                self.last_backend = str(getattr(self._segmenter, "name", "unknown"))
                self.segmentation_events.append({"backend": self.last_backend, "concepts": list(concepts)})
        return resolve(raw, concepts, meta, proxy.rgb.shape[:2]), meta

    def _proposal(self, proxy: Image, g, masks: dict) -> dict:
        if self.preserve_exposure:
            return {}
        conf, w = self.options["global"], self.options["workflow"]
        raw_policy = is_raw(proxy.path or "") or w["format_policy"] == "shared"
        target = conf["raw_target"] if raw_policy else conf["jpeg_target"]
        fg = np.ones(proxy.rgb.shape[:2], bool)
        sky = masks.get("sky")
        if sky is not None and 0.05 < sky.mean() < 0.98:
            fg = ~sky
        if w["stats_space"] == "linear":
            measured = float(np.median(luma(srgb_to_linear(proxy.rgb))[fg]))
            wanted = float(srgb_to_linear(np.float32(target)))
        else:
            measured, wanted = float(np.median(luma(proxy.rgb)[fg])), target
        ev = max(0.0, np.log2(max(wanted, 1e-8) / max(measured, 1e-8)))
        if w["stats_space"] == "gamma":
            # This ablation deliberately uses gamma-space ratios, not claimed physical EV.
            ev = float(ev)
        room = _headroom(proxy.rgb, conf["highlight_level"], conf["headroom_percentile"],
                         conf["headroom_margin_ev"])
        settings = {
            "exposure": min(conf["max_exposure"], room, ev * conf["exposure_fraction"])
                        if g.fg_p50 < conf["dark_source"] else 0.0,
            "shadows": min(conf["max_shadows"], max(0.0, conf["dark_source"]-g.fg_p50)*conf["shadow_gain"]),
            "contrast": min(conf["max_contrast"], max(0.0, conf["target_spread"]-(g.lum_p90-g.lum_p10))*conf["contrast_gain"]),
            "temp": float(np.clip(g.wb_temp_correction*g.wb_confidence*conf["wb_fraction"] *
                           max(0.0, 1.0-max(0.0, g.warmth)/conf["warmth_preserve"]), conf["temp_min"], conf["temp_max"])),
            "tint": float(np.clip(g.wb_tint_correction*g.wb_confidence*conf["wb_fraction"], conf["tint_min"], conf["tint_max"])),
            "vibrance": conf["vibrance"],
        }
        if w["consistency"]:
            settings["exposure"] = float(np.clip(settings["exposure"]+w["consistency_bias"],
                                                  -conf["consistency_max_ev"], room))
        return {k: float(v) for k, v in settings.items()}

    def _render(self, rgb: np.ndarray, settings: dict, pivot: float,
                linear_source: np.ndarray | None = None) -> np.ndarray:
        w, conf = self.options["workflow"], self.options["global"]
        if not settings or not any(settings.values()):
            return rgb
        # Keep the linear shoulder in the same pass as WB/exposure: no earlier hard clip.
        s = dict(settings)
        contrast = s.pop("contrast", 0.0)
        if w["tone_mapping"] == "shoulder":
            s["highlight_rolloff"] = conf["rolloff"]
        elif w["tone_mapping"] == "current_curve":
            s["curve"] = conf["curve"]
        # Explicit stages permit a controlled contrast-position ablation without changing ops.
        color = {k: s.pop(k) for k in ("vibrance", "saturation", "dehaze") if k in s}
        if w["tone_mapping"] == "reinhard":
            # Preserve HDR values through light adjustment until the alternative shoulder;
            # calling ops.light here would clamp before Reinhard could protect them.
            linear = (srgb_to_linear(rgb) if linear_source is None else linear_source).copy()
            temp, tint, ev = (s.pop(k, 0.0) for k in ("temp", "tint", "exposure"))
            linear[..., 0] *= 1.0+0.002*temp
            linear[..., 1] *= 1.0+0.001*tint
            linear[..., 2] *= 1.0-0.002*temp
            linear *= 2.0**ev
            linear = photographic_reinhard(linear, conf["reinhard_strength"], conf["reinhard_white_point"],
                                            float(srgb_to_linear(np.float32(conf["highlight_level"]))))
            out = ops.apply_ops(linear_to_srgb(linear), s)
        elif linear_source is not None:
            # During search the source never changes. Reuse its transfer decode while
            # preserving ops.light's arithmetic and skipping exact-zero light operations.
            temp, tint, ev, rolloff = (s.pop(k, 0.0) for k in
                                      ("temp", "tint", "exposure", "highlight_rolloff"))
            if temp or tint or ev or rolloff:
                linear = linear_source
                if temp or tint:
                    linear = linear.copy()
                    linear[..., 0] *= 1.0+0.2*(temp/100.0)
                    linear[..., 1] *= 1.0+0.1*(tint/100.0)
                    linear[..., 2] *= 1.0-0.2*(temp/100.0)
                if ev:
                    linear = linear*(2.0**ev)
                out = linear_to_srgb(ops.highlight_rolloff(linear, rolloff))
            else:
                out = rgb
            out = ops.apply_ops(out, s)
        else:
            out = ops.apply_ops(rgb, s)
        if w["contrast_mode"] == "adaptive":
            out = adaptive_contrast(out, contrast, pivot)
        elif w["contrast_mode"] == "current":
            out = ops.contrast(out, contrast)
        out = ops.apply_ops(out, color)
        out = np.clip(out, 0.0, 1.0).astype(np.float32)
        return chroma_guard(rgb, out, max_increase=conf["chroma_max_increase"])[0]

    def _objective(self, pre, post, settings: dict, raw: bool) -> tuple[float, dict]:
        c, w, weights = self.options["global"], self.options["workflow"], self.options["objective"]
        target = c["raw_target"] if raw or w["format_policy"] == "shared" else c["jpeg_target"]
        # Normal/low-key inputs are not all driven to one mid-gray: only a source with a
        # measured dark foreground gains a recovery objective. Unchanged is always feasible.
        desired = target if pre.fg_p50 < c["dark_source"] and not self.preserve_exposure else pre.fg_p50
        vec = {
            "foreground_deficit": max(0.0, desired-post.fg_p50),
            "spread_deficit": max(0.0, min(c["target_spread"], pre.lum_p90-pre.lum_p10)-(post.lum_p90-post.lum_p10)),
            "dark_coverage": max(0.0, post.dark_cov),
            "highlight_change": abs(post.lum_p99-pre.lum_p99),
            "color_change": abs(post.mean_chroma-pre.mean_chroma)+abs(post.warmth-pre.warmth),
            "parameter_magnitude": sum(abs(settings.get(k, 0.0))/max(1.0, abs(self.options["search"][k+"_max"])) for k in _SEARCH_KEYS),
        }
        names = (("foreground_deficit", "foreground_weight"), ("spread_deficit", "spread_weight"),
                 ("dark_coverage", "dark_coverage_weight"), ("highlight_change", "highlight_change_weight"),
                 ("color_change", "color_change_weight"), ("parameter_magnitude", "parameter_weight"))
        return sum(vec[k]*weights[v] for k, v in names), vec

    def _optimize(self, image: Image, initial: dict, pivot: float, masks: dict) -> tuple[dict, dict]:
        conf = self.options["search"]
        work = downscale_long_edge(image, conf["proxy_edge"])
        # Source masks are sampled consistently when the optimizer uses a smaller proxy.
        small_masks = {name: _resize_mask(mask, work.rgb.shape[:2]) for name, mask in masks.items()}
        pre, _ = stats.compute(work, small_masks)
        linear_source = srgb_to_linear(work.rgb)
        prepared_safety = prepare_quality_source(work.rgb, masks=small_masks)
        initial = {k: float(np.clip(v, conf[k+"_min"], conf[k+"_max"])) if k in _SEARCH_KEYS else v
                   for k, v in initial.items()}
        start = time.perf_counter()
        evaluations, rejected, history = 0, 0, []
        best, best_score, best_vec = {}, *self._objective(pre, pre, {}, is_raw(image.path or ""))
        initial_vec = dict(best_vec)
        fallback = None
        pareto = [{"settings": {}, "objective_vector": dict(best_vec), "search_objective": best_score}]

        def retain_alternative(settings, score, vector):
            # The weighted search objective defines this candidate only. Preserve its
            # non-dominated feasible alternatives for later visual/multi-objective review;
            # it is never an architectural winner score.
            def dominates(a, b):
                return all(a[k] <= b[k] for k in a) and any(a[k] < b[k] for k in a)
            if any(row["objective_vector"] == vector or dominates(row["objective_vector"], vector)
                   for row in pareto):
                return
            pareto[:] = [row for row in pareto if not dominates(vector, row["objective_vector"])]
            pareto.append({"settings": dict(settings), "objective_vector": dict(vector), "search_objective": score})

        def watchdog_result(reason):
            return {}, {"evaluations": evaluations, "passes": completed,
                        "seconds": time.perf_counter()-start, "rejected": rejected,
                        "fallback_reason": reason, "objective_vector": initial_vec, "history": history,
                        "pareto_candidates": pareto}

        def evaluate(candidate):
            nonlocal evaluations, rejected
            evaluations += 1
            rgb = self._render(work.rgb, candidate, pivot, linear_source=linear_source)
            safety = assess_pixels(work.rgb, rgb, small_masks, prepared=prepared_safety)
            if not safety["passed"]:
                rejected += 1
                return None
            measured = safety["metrics"]["output"]
            # assess_pixels already computed these exact statistics on this <=512px
            # candidate. Reusing them avoids a third full stats pass per evaluation.
            post = SimpleNamespace(fg_p50=measured["fg_p50"], lum_p10=measured["p10"],
                                   lum_p90=measured["p90"], lum_p99=measured["p99"],
                                   dark_cov=measured["dark_cov"], mean_chroma=measured["mean_chroma"],
                                   warmth=measured["warmth"])
            score, vector = self._objective(pre, post, candidate, is_raw(image.path or ""))
            retain_alternative(candidate, score, vector)
            return score, vector

        completed = 0
        if self.preserve_exposure:
            return watchdog_result("explicit low-key/preserve intent; deterministic identity")
        if conf["max_seconds"] <= 0:
            return watchdog_result("watchdog disabled search; deterministic identity")
        value = evaluate(initial)
        if time.perf_counter()-start >= conf["max_seconds"]:
            return watchdog_result("time watchdog; discarded partial search for deterministic identity")
        if value and value[0] < best_score:
            best, (best_score, best_vec) = dict(initial), value
        for p in range(conf["max_passes"]):
            moved = False
            for key in _SEARCH_KEYS:
                for direction in (-1.0, 1.0):
                    if evaluations >= conf["max_evaluations"]:
                        fallback = "evaluation budget reached"
                        break
                    if time.perf_counter()-start >= conf["max_seconds"]:
                        # Partial best depends on machine speed. Discard it on timeout;
                        # identity is deterministic and leaves retry policy to the caller.
                        return watchdog_result("time watchdog; discarded partial search for deterministic identity")
                    trial = dict(best)
                    trial[key] = float(np.clip(trial.get(key, 0.0)+direction*conf[key+"_step"]*(conf["step_decay"]**p),
                                                conf[key+"_min"], conf[key+"_max"]))
                    if trial.get(key, 0.0) == best.get(key, 0.0):
                        continue
                    value = evaluate(trial)
                    if time.perf_counter()-start >= conf["max_seconds"]:
                        return watchdog_result("time watchdog; discarded partial search for deterministic identity")
                    if value and value[0] < best_score-conf["improvement_epsilon"]:
                        best, (best_score, best_vec) = trial, value
                        moved = True
                        history.append({"pass": p+1, "op": key, "value": trial[key], "objective": best_score})
                if evaluations >= conf["max_evaluations"]:
                    break
            completed += 1
            if not moved or evaluations >= conf["max_evaluations"]:
                break
        if evaluations >= conf["max_evaluations"]:
            fallback = "evaluation budget reached"
        return best, {"evaluations": evaluations, "passes": completed, "seconds": time.perf_counter()-start,
                      "rejected": rejected, "fallback_reason": fallback, "objective": best_score,
                      "objective_vector": best_vec, "source_objective_vector": initial_vec, "history": history,
                      "pareto_candidates": pareto}

    def process(self, path: str, image: Image | None = None, common_source=None):
        started = time.perf_counter()
        self.last_backend = "none"
        self.segmentation_events = []
        if self._pipe is not None:
            checkpoints = []
            self.last_masks = {}
            source_sample = None
            def observed(resolved):
                self.last_masks = resolved.union
            def stage_observed(label, values):
                nonlocal source_sample
                sample = downscale_long_edge(Image(rgb=values), self.cfg.analysis_long_edge).rgb
                if source_sample is None:
                    source_sample = sample.copy()
                assessment = assess_pixels(source_sample, sample, self.last_masks)
                checkpoints.append({"stage": label, "scope": "analysis proxy; final export assessed separately",
                                    "assessment": assessment})
            delegate, owner = self._pipe.segmenter, self
            class ObservedSegmenter:
                def segment(self, source, concepts):
                    raw = delegate.segment(source, concepts)
                    owner.last_backend = str(getattr(delegate, "name", "unknown"))
                    owner.segmentation_events.append({"backend": owner.last_backend, "concepts": list(concepts)})
                    return raw
            self._pipe.segmenter = ObservedSegmenter()
            try:
                rgb, result = self._pipe.process_one(path, image=image, on_resolved=observed,
                                                     on_stage=stage_observed)
            finally:
                self._pipe.segmenter = delegate
            result.audit.update(workflow="current", decode_options=self.decode_options(),
                                control_pixels_unchanged=True, checkpoints=checkpoints,
                                actual_backend=self.last_backend, segmentation_events=self.segmentation_events,
                                proposal_stage_recovery=summarize_stage_recovery(checkpoints))
            result.stage_timings_ms["current_pipeline"] = (time.perf_counter()-started)*1000
            return rgb, result
        timings = {}
        mark = time.perf_counter()
        img = image if image is not None else load_image(path, decode=self.decode_options())
        if img.path is None:
            img = Image(img.rgb, img.source_bits, path, img.exif, img.decode_ev, dict(img.decode_info))
        timings["decode"] = 0.0 if image is not None else (time.perf_counter()-mark)*1000
        if not np.isfinite(img.rgb).all():
            raise ValueError("source contains invalid pixels")
        proxy = downscale_long_edge(img, self.cfg.analysis_long_edge)
        w, conf = self.options["workflow"], self.options["global"]
        defer_segment = (w["segmentation"] and w["segment_order"] == "after_global"
                         and w["order"] == "global_first" and self.fixed_masks is None)
        mark = time.perf_counter()
        resolved, meta = self._segment(proxy, enabled=not defer_segment)
        masks = resolved.union
        self.last_masks = masks
        timings["segment_resolve"] = (time.perf_counter()-mark)*1000
        mark = time.perf_counter()
        pre, regions_pre = stats.compute(proxy, masks)
        timings["stats"] = (time.perf_counter()-mark)*1000
        decision_stats = pre.model_dump()
        sc = scene.classify(pre, set(masks), regions_pre) if w["scene_order"] == "before_global" else None
        settings = self._proposal(proxy, pre, masks)
        pivot = float(np.clip(pre.fg_p50, conf["contrast_pivot_min"], conf["contrast_pivot_max"]))
        proposed = dict(settings)
        search_report = {}
        mark = time.perf_counter()
        if self.workflow == "optimized":
            settings, search_report = self._optimize(proxy, settings, pivot, masks)
        timings["decide"] = (time.perf_counter()-mark)*1000
        mark = time.perf_counter()
        from .edit.mask import build_alpha
        from .concepts import meta_for
        region_settings = {}
        region_details = {}
        checkpoints = {}
        def checkpoint(label, values):
            sample = downscale_long_edge(Image(rgb=values), self.cfg.analysis_long_edge)
            measured, _ = stats.compute(sample, masks)
            checkpoints[label] = {"fg_p50": measured.fg_p50, "mean_lum": measured.mean_lum,
                                  "highlight_clip": measured.highlight_clip,
                                  "rgb_highlight_clip_any": measured.rgb_highlight_clip_any,
                                  "shadow_clip": measured.shadow_clip,
                                  "scope": "analysis proxy; final export assessed separately",
                                  "assessment": assess_pixels(proxy.rgb, sample.rgb, masks)}
        def refine(rgb):
            if not w["regional"] or self.preserve_exposure:
                return rgb
            rconf = self.options["regional"]
            source = rgb
            out = rgb.copy()
            claimed = np.zeros(rgb.shape[:2], np.float32)
            for region in resolved.regions:
                if region.coverage < rconf["min_coverage"]:
                    continue
                cm = meta_for(region.name, meta)
                alpha = build_alpha(region.mask, source.shape[:2], cm.feather_px, guide=luma(source))
                alpha = alpha*(1.0-claimed)
                covered = source[alpha > 0.5]
                if not covered.size:
                    continue
                median = float(np.median(luma(covered)))
                if median >= rconf["dark_source"]:
                    continue
                desired = min(rconf["max_exposure"], max(0.0, float(np.log2(
                    float(srgb_to_linear(np.float32(rconf["target"]))) /
                    max(float(srgb_to_linear(np.float32(median))), 1e-8))))*rconf["exposure_fraction"])
                request = {"exposure": desired, "shadows": min(rconf["max_shadows"],
                    max(0.0, rconf["dark_source"]-median)*rconf["shadow_gain"])}
                from .policies import enforce
                enforce({region.name: request}, meta)
                region_rgb, actual = _region_rgb(source, alpha, request, rconf)
                out += region_rgb-source
                claimed = np.clip(claimed+alpha, 0.0, 1.0)
                region_settings[region.name] = actual
                region_details[region.name] = {"requested": request, "applied": actual,
                                               "highlight_bound": "all feather-covered source pixels"}
            return out
        working = refine(img.rgb) if w["order"] == "region_first" else img.rgb
        if w["regional"] and w["order"] == "region_first":
            checkpoint("after_regions", working)
        rgb = self._render(working, settings, pivot)
        if defer_segment:
            seg_start = time.perf_counter()
            corrected_proxy = downscale_long_edge(Image(rgb=rgb), self.cfg.analysis_long_edge)
            resolved, meta = self._segment(corrected_proxy)
            masks = resolved.union
            self.last_masks = masks
            timings["segment_resolve"] += (time.perf_counter()-seg_start)*1000
            # Decisions used the unsegmented stats recorded separately. Audit source/output
            # with identical masks measured on the corrected image, without re-deciding.
            pre, regions_pre = stats.compute(proxy, masks)
        checkpoint("after_global", rgb)
        if w["order"] == "global_first":
            rgb = refine(rgb)
            if w["regional"]:
                checkpoint("after_regions", rgb)
        composition_settings = {}
        if w["composition"] and not self.preserve_exposure:
            from . import compose
            moves = compose.load_compose(self.cfg.compose_path)
            moves = [m for m in moves if (w["vignette"] or "vignette" not in m.name)
                     and (w["sky_gradient"] or m.name != "sky_grad")]
            for name, alpha, setting in compose.plan(moves, masks, rgb.shape[:2], self.cfg.intensity):
                rendered = ops.apply_ops(rgb, setting)
                rgb = rgb + alpha[..., None]*(rendered-rgb)
                composition_settings[name] = setting
        if w["palette"] and not self.preserve_exposure:
            from . import palette
            from .edit.palette import palette_grade
            palettes = palette.load_palettes(self.cfg.palettes_path)
            auto = palette.load_auto_palette_config(self.cfg.palettes_path)
            name, scale, reason = palette.auto_palette_for_image(sc.name if sc else "other", pre, set(masks), palettes, auto)
            if name:
                rgb = palette_grade(rgb, palettes[name], min(scale, self.cfg.palette_strength*self.cfg.intensity))
        rgb = np.clip(rgb, 0.0, 1.0).astype(np.float32)
        checkpoint("final_float", rgb)
        timings["apply"] = (time.perf_counter()-mark)*1000
        mark = time.perf_counter()
        safety = assess_pixels(img.rgb, rgb, masks)
        if common_source is not None:
            common = getattr(common_source, "rgb", common_source)
            if np.shape(common) != rgb.shape:
                raise ValueError("common_source and rendered image must have matching shapes")
            common_safety = assess_pixels(common, rgb, masks)
        else:
            common_safety = None
        post_proxy = downscale_long_edge(Image(rgb=rgb), self.cfg.analysis_long_edge)
        post, regions_post = stats.compute(post_proxy, masks)
        if sc is None:
            sc = scene.classify(post, set(masks), regions_post)
        timings["assess"] = (time.perf_counter()-mark)*1000
        notes = [f"experimental workflow: {self.workflow}; no aesthetic winner assumed",
                 f"RAW development: {self.decode_options()['raw_mode']}; {w['stats_space']}-space decision statistics",
                 f"global contrast: {w['contrast_mode']}; tone mapping: {w['tone_mapping']}"]
        if search_report.get("fallback_reason"):
            notes.append("search: "+search_report["fallback_reason"])
        if img.decode_info.get("fallback_reason"):
            notes.append("decode: "+img.decode_info["fallback_reason"])
        if self.preserve_exposure:
            notes.append("explicit low-key/preserve intent: no automatic normalization or edit")
        result = emit.build_result(
            image_name=os.path.basename(path), scene=sc.name if w["scene_order"] != "none" else "other",
            confidence=sc.confidence if w["scene_order"] != "none" else 0.0,
            style=self.cfg.style, brief=self.cfg.prompt, detected=resolved.detected,
            gstats=pre, region_stats=regions_pre, global_settings=settings, region_settings=region_settings,
            compositional=composition_settings, notes=notes, decider=self.workflow, model=None,
            timing_ms=(time.perf_counter()-started)*1000, post_edit_stats=post,
            post_region_stats=list(regions_post.values()), decode_ev=img.decode_ev,
            quality_score=1.0 if safety["passed"] else 0.0, needs_review=not safety["passed"],
            review_reason="; ".join(safety["failures"]), warnings=safety["failures"])
        result.audit.update(workflow=self.workflow, options=deepcopy(self.options), decode=img.decode_info,
                            decode_options=self.decode_options(), decode_injected=image is not None,
                            proposed_settings=proposed, applied_settings=settings, contrast_pivot=pivot,
                            search=search_report, regional=region_details, developed_source_safety=safety,
                            common_source_safety=common_safety, checkpoints=checkpoints,
                            decision_stats=decision_stats, actual_backend=self.last_backend,
                            segmentation_events=self.segmentation_events,
                            proposal_stage_recovery=summarize_stage_recovery(checkpoints),
                            preserve_exposure=self.preserve_exposure,
                            objective_vector=self._objective(pre, post, settings, is_raw(path))[1],
                            fixed_mask_names=sorted(self.fixed_masks or {}))
        result.stage_timings_ms.update(timings)
        return rgb, result
