"""Per-image pipeline (port of the Rust main.rs::process), end to end.

decode → segment → resolve → stats → scene → decide → apply → result.
A `Pipeline` is built once per run (configs + resident segmenter + decider) and reused
across the batch so the model load cost is paid once.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import numpy as np

from . import (compose, emit, intent as intent_mod, looks as looks_mod, palette as palette_mod,
               policies, rules as rules_mod, scene as scene_mod, stats as stats_mod)
from .concepts import load_concepts, meta_for
from .contract import Result, RunConfig
from .decide import DecisionContext, make_decider
from .decide.base import Decider
from .edit import apply_image, banding, build_alpha, gates, reference as ref_match, linear_alpha
from .imageio import Image, downscale_long_edge, load_image, luma
from .notes import build_notes
from .segment import make_segmenter, resolve
from .segment.base import Segmenter
from .styles import Style, combine_brief, load_styles, style_for

# High-risk global ops the skin-safe guard holds off protected (skin/face) regions when no
# [guard] block is present in rules.toml. Exposure/tone/contrast are intentionally NOT guarded —
# faces should still get global brightness/tonal correction, just not the aggressive look.
_DEFAULT_GUARD_OPS = {
    "clarity", "saturation", "vibrance", "hsl_skin_sat",
    "grade_shadows_temp", "grade_shadows_tint", "grade_highlights_temp", "grade_highlights_tint",
}
# Default oversaturation gate: a pixel's chroma may grow by at most +30% over the source.
_DEFAULT_CHROMA_MAX = 0.30


def _vertical_band_alpha(full_hw: tuple[int, int], top: float, peak: float, bottom: float) -> np.ndarray:
    h, w = full_hw
    y = np.linspace(0.0, 1.0, h, dtype=np.float32)
    up = np.clip((y - top) / max(peak - top, 1e-6), 0.0, 1.0)
    down = np.clip((bottom - y) / max(bottom - peak, 1e-6), 0.0, 1.0)
    a = np.minimum(up, down)
    a = a * a * (3.0 - 2.0 * a)
    return np.repeat(a[:, None], w, axis=1).astype(np.float32)


@dataclass
class Pipeline:
    cfg: RunConfig
    rules: dict
    looks: dict
    presets: dict
    meta: dict
    styles: dict
    compose_moves: list
    segmenter: Segmenter
    decider: Decider
    guard_ops: set
    chroma_max: float | None
    intents: dict
    palettes: dict
    banding: dict
    quality_gate: dict | None = None  # post-edit gate thresholds from rules.toml (None → defaults)
    ref_feats: dict | None = None    # cached per-concept reference look (M5); computed once per run
    ref_proxy: Image | None = None   # cached decoded reference proxy — one decode per run, not per photo

    @classmethod
    def build(cls, cfg: RunConfig) -> "Pipeline":
        presets, meta = load_concepts(cfg.concepts_path)
        moves = compose.load_compose(cfg.compose_path) if cfg.compose else []
        if not cfg.vignette:  # vignette is a toggleable subset of the compositional moves
            moves = [m for m in moves if "vignette" not in m.name]
        rules = rules_mod.load(cfg.rules_path)
        # which global ops the skin-safe guard holds off protected regions (rules-as-data; the
        # [guard] block in rules.toml, with a sane default so older config files still work).
        guard_ops = set(rules.get("guard", {}).get("ops", []) or _DEFAULT_GUARD_OPS)
        # oversaturation gate: cap each pixel's chroma increase vs the source (None disables).
        cm = rules.get("guard", {}).get("max_chroma_increase", _DEFAULT_CHROMA_MAX)
        chroma_max = None if cm in (None, "", "off") else float(cm)
        return cls(
            cfg=cfg,
            rules=rules,
            looks=looks_mod.load(cfg.looks_path),
            presets=presets,
            meta=meta,
            styles=load_styles(cfg.styles_path),
            compose_moves=moves,
            segmenter=make_segmenter(cfg.backend, cfg.device),
            decider=make_decider(cfg),
            guard_ops=guard_ops,
            chroma_max=chroma_max,
            intents=intent_mod.load_intents(cfg.intents_path),
            palettes=palette_mod.load_palettes(cfg.palettes_path),
            banding={k: float(v) for k, v in (rules.get("banding", {}) or {}).items()
                     if isinstance(v, (int, float))},
            quality_gate={k: float(v) for k, v in (rules.get("quality_gate", {}) or {}).items()
                          if isinstance(v, (int, float))} or None,
        )

    def concepts_for(self, style: Style) -> list[str]:
        """Resolution: explicit --concepts > --preset > the style's set > the generic preset."""
        if self.cfg.concepts:
            return list(self.cfg.concepts)
        if self.cfg.preset:
            return self.presets.get(self.cfg.preset) or []
        if style.concepts:
            return list(style.concepts)
        return self.presets.get("generic") or ["subject", "sky", "skin"]

    def process_one(self, path: str, do_apply: bool = True,
                    image: Image | None = None, exposure_bias: float = 0.0,
                    batch_group: str = "") -> tuple[np.ndarray | None, Result]:
        t0 = time.perf_counter()
        cfg = self.cfg

        style = style_for(cfg.style, self.styles)
        look = cfg.look or style.look
        brief = combine_brief(style.brief, cfg.prompt)

        # `image` lets callers (e.g. the UI preview) pass a pre-downscaled proxy so the full-res
        # apply stays fast; the batch/export path passes nothing → full resolution.
        img: Image = image if image is not None else load_image(path)
        proxy = downscale_long_edge(img, cfg.analysis_long_edge)
        concepts = self.concepts_for(style)

        raw = self.segmenter.segment(proxy, concepts)
        resolved = resolve(raw, concepts, self.meta, proxy.rgb.shape[:2])

        gstats, region_stats = stats_mod.compute(proxy, resolved.union)
        present = set(region_stats.keys())
        sc = scene_mod.classify(gstats, present, region_stats)
        region_rule = {name: meta_for(name, self.meta).region() for name in present}

        thumb = None
        if cfg.decider == "llm":
            thumb = downscale_long_edge(proxy, cfg.llm_thumb_px).rgb

        ctx = DecisionContext(
            scene=sc.name,
            gstats=gstats,
            region_stats=region_stats,
            region_rule=region_rule,
            intensity=cfg.intensity,
            rules=self.rules,
            looks=self.looks,
            look=look,
            brief=brief,
            thumb=thumb,
        )
        global_settings, region_settings = self.decider.decide(ctx)

        if not cfg.sharpen:  # strip the sharpen op when output sharpening is disabled
            global_settings.pop("sharpen", None)
            for s in region_settings.values():
                s.pop("sharpen", None)

        # Free-text look intent (e.g. "warm sunset, moody") → bounded GLOBAL nudges on top of the
        # rule baseline, so typing what you want works fully offline. Scaled by intensity; the
        # oversaturation gate + skin-safe guard still backstop. Skipped under the LLM decider, which
        # already consumes the prompt via the brief (avoids double-applying the same intent).
        intent_note = None
        if cfg.prompt and self.decider.name != "llm":
            nudge, terms = intent_mod.parse(cfg.prompt, self.intents)
            if nudge:
                for k, v in nudge.items():
                    global_settings[k] = global_settings.get(k, 0.0) + v * cfg.intensity
                intent_note = "look intent: " + ", ".join(terms)

        # Regional palette grade (the universal "make it blue/sunset" colour layer). Explicit
        # cfg.palette wins; "none"/"off" disables; cue words in the prompt win next; otherwise
        # an image-stats auto picker chooses a conservative colour story and strength.
        palette_scale = 1.0
        palette_auto_reason = ""
        if cfg.palette and cfg.palette.lower() not in ("auto", ""):
            pal_name = None if cfg.palette.lower() in ("none", "off") else cfg.palette
        else:
            pal_name = palette_mod.palette_for_prompt(cfg.prompt, self.palettes)
            if pal_name is None and cfg.reference:
                # A reference image is already a color story. Do not stack the generic auto
                # cool/warm palette on top of it, or reference-matched landscapes can come out
                # too dark/cool compared with the chosen hero edit.
                pal_name = None
            if pal_name is None and not cfg.reference:
                pal_name, palette_scale, palette_auto_reason = palette_mod.auto_palette_for_image(
                    sc.name, gstats, present, self.palettes)
        palette_obj = self.palettes.get(pal_name) if pal_name else None
        palette_arg = ((palette_obj, cfg.palette_strength * cfg.intensity * palette_scale)
                       if palette_obj is not None else None)
        chroma_neutral_allowance = (
            0.02 + 0.10 * min(1.0, max(0.0, palette_arg[1])) if palette_arg is not None else 0.02
        )
        if palette_obj is not None and palette_auto_reason:
            palette_note = f"palette: auto {pal_name} ({palette_arg[1]:.0%}) — {palette_auto_reason}"
        else:
            palette_note = (f"palette: {pal_name} ({cfg.palette_strength:.0%})"
                            if palette_obj is not None else None)

        # Region-aware reference matching (M5): push each source region toward the SAME concept's
        # look in the reference (sky→sky, water→water, mountain→mountain), dimension-independent so
        # an arbitrary alpine reference works. Bounded; merged into region_settings BEFORE policies
        # clamp them. The global color_transfer below still finishes the overall palette.
        region_ref_note = None
        if cfg.reference:
            ref_feats = self._reference_concept_features(concepts)
            if ref_feats:
                src_feats = ref_match.extract_concept_features(proxy.rgb, resolved.union)
                region_settings, rep = ref_match.region_settings_from_features(
                    region_settings, src_feats, ref_feats, cfg.reference_strength)
                if rep:
                    region_ref_note = "reference match (region): " + ", ".join(r["name"] for r in rep)

        # Region policies: hard rails enforced on whatever the rules/LLM proposed (drop forbidden
        # ops, clamp magnitudes) so a region can't be over-driven into an unnatural colour.
        policy_notes = policies.enforce(region_settings, self.meta)

        # Shoot-consistency: a bounded exposure nudge toward this image's batch group target,
        # so a folder doesn't drift in brightness photo-to-photo (batch normalization, F5).
        if exposure_bias:
            base = global_settings.get("exposure", 0.0)
            global_settings["exposure"] = float(np.clip(base + exposure_bias, -3.0, 3.0))
            policy_notes.append(
                f"batch normalize: {exposure_bias:+.2f} EV toward group {batch_group or '?'}")

        notes_extra: list[str] = list(policy_notes)

        # Reference feedback: before touching full-res pixels, learn a bounded set of ordinary
        # edit-slider nudges from the reference look. This captures "make this image feel like
        # that one" as reusable settings rather than a one-off color-transfer result.
        ref_feedback_note = None
        ref_proxy = None
        reference_comp_settings: dict[str, dict[str, float]] = {}
        if cfg.reference:
            try:
                from .edit.ops import apply_ops
                from .edit.reference import (
                    bottom_gradient_to_reference,
                    midground_lift_to_reference,
                    mountain_highlight_to_reference,
                    top_gradient_to_reference,
                    tune_regions_to_reference,
                    tune_settings_to_reference,
                )

                ref_proxy = self._get_ref_proxy()
                globally_rendered = apply_ops(proxy.rgb, global_settings)
                tuned, feedback = tune_settings_to_reference(
                    proxy.rgb, ref_proxy.rgb, global_settings,
                    strength=cfg.reference_strength, passes=5,
                )
                global_settings = tuned
                globally_rendered = apply_ops(proxy.rgb, global_settings)
                moves = feedback.get("moves", [])
                before_loss = float(feedback.get("before_loss", 0.0))
                after_loss = float(feedback.get("after_loss", before_loss))
                if moves and after_loss < before_loss:
                    preview = ", ".join(str(m) for m in moves[:5])
                    if len(moves) > 5:
                        preview += ", ..."
                    ref_feedback_note = (
                        f"reference feedback → {os.path.basename(cfg.reference)} "
                        f"({before_loss:.3f}→{after_loss:.3f}; {preview})")
                else:
                    ref_feedback_note = (
                        f"reference feedback → {os.path.basename(cfg.reference)} "
                        f"({before_loss:.3f}→{after_loss:.3f}; no slider change needed)")
                region_settings, region_feedback = tune_regions_to_reference(
                    globally_rendered, ref_proxy.rgb, region_settings, resolved.union,
                    strength=cfg.reference_strength, passes=3,
                )
                # Re-enforce region policies: the tuner can re-introduce ops the rails already
                # dropped (e.g. temp on snow). Hard rails must win over reference matching.
                notes_extra.extend(policies.enforce(region_settings, self.meta))
                if region_feedback:
                    parts = []
                    for item in region_feedback[:3]:
                        moves = ", ".join(str(m) for m in item.get("moves", [])[:3])
                        parts.append(f"{item.get('name')} {item.get('before_loss', 0):.3f}→"
                                     f"{item.get('after_loss', 0):.3f} ({moves})")
                    if len(region_feedback) > 3:
                        parts.append("...")
                    notes_extra.append("reference regional feedback: " + "; ".join(parts))
                if "sky" in resolved.union:
                    top_settings, top_report = top_gradient_to_reference(
                        globally_rendered, ref_proxy.rgb, strength=cfg.reference_strength)
                    if top_settings:
                        reference_comp_settings["reference_sky_grad"] = top_settings
                        moves = ", ".join(f"{k} {fmt:+.1f}" for k, fmt in top_settings.items())
                        notes_extra.append(
                            "reference sky gradient: "
                            f"top luminance {top_report['before_top']:.2f}→"
                            f"{top_report['reference_top']:.2f} ({moves})")
                bottom_settings, bottom_report = bottom_gradient_to_reference(
                    globally_rendered, ref_proxy.rgb, strength=cfg.reference_strength)
                if bottom_settings:
                    reference_comp_settings["reference_foreground_lift"] = bottom_settings
                    moves = ", ".join(f"{k} {fmt:+.1f}" for k, fmt in bottom_settings.items())
                    notes_extra.append(
                        "reference foreground lift: "
                        f"lower luminance {bottom_report['before_bottom']:.2f}→"
                        f"{bottom_report['reference_bottom']:.2f} ({moves})")
                mid_settings, mid_report = midground_lift_to_reference(
                    globally_rendered, ref_proxy.rgb, strength=cfg.reference_strength)
                if mid_settings:
                    reference_comp_settings["reference_midground_lift"] = mid_settings
                    moves = ", ".join(f"{k} {fmt:+.1f}" for k, fmt in mid_settings.items())
                    notes_extra.append(
                        "reference midground lift: "
                        f"mid luminance {mid_report['before_mid']:.2f}→"
                        f"{mid_report['reference_mid']:.2f} ({moves})")
                mountain_settings, mountain_report = mountain_highlight_to_reference(
                    globally_rendered, ref_proxy.rgb, strength=cfg.reference_strength)
                if mountain_settings:
                    reference_comp_settings["reference_mountain_highlight"] = mountain_settings
                    moves = ", ".join(f"{k} {fmt:+.1f}" for k, fmt in mountain_settings.items())
                    notes_extra.append(
                        "reference mountain highlight: "
                        f"snow p90 {mountain_report['before_p90']:.2f}→"
                        f"{mountain_report['reference_p90']:.2f} ({moves})")
            except Exception as e:
                ref_feedback_note = f"reference feedback skipped ({e})"

        full_hw = (img.height, img.width)
        comp_settings = compose.settings_for(self.compose_moves, resolved.union, cfg.intensity)
        comp_settings.update(reference_comp_settings)

        out_rgb = None
        guard_note = None
        if do_apply:
            guide = luma(img.rgb)  # full-res structure reference for edge-aware mask feathering
            regions_apply = []
            for r in resolved.regions:  # priority-descending
                s = region_settings.get(r.name)
                if not s:
                    continue
                cm = meta_for(r.name, self.meta)
                alpha = build_alpha(r.mask, full_hw, cm.feather_px, guide=guide)
                regions_apply.append((alpha, s))
            comp = compose.plan(self.compose_moves, resolved.union, full_hw, cfg.intensity)
            if "reference_sky_grad" in reference_comp_settings:
                comp.append((
                    "reference_sky_grad",
                    linear_alpha(full_hw, frm="top", coverage=0.58),
                    reference_comp_settings["reference_sky_grad"],
                ))
            if "reference_foreground_lift" in reference_comp_settings:
                comp.append((
                    "reference_foreground_lift",
                    linear_alpha(full_hw, frm="bottom", coverage=0.62),
                    reference_comp_settings["reference_foreground_lift"],
                ))
            if "reference_midground_lift" in reference_comp_settings:
                comp.append((
                    "reference_midground_lift",
                    _vertical_band_alpha(full_hw, top=0.48, peak=0.70, bottom=0.93),
                    reference_comp_settings["reference_midground_lift"],
                ))
            if "reference_mountain_highlight" in reference_comp_settings:
                comp.append((
                    "reference_mountain_highlight",
                    _vertical_band_alpha(full_hw, top=0.30, peak=0.47, bottom=0.66),
                    reference_comp_settings["reference_mountain_highlight"],
                ))
            guard_mask, guard_pct, held = self._build_guard(resolved.union, full_hw, global_settings, guide)
            gate_report: dict = {}
            out_rgb = apply_image(
                img.rgb, regions_apply, global_settings,
                compositional=[(a, s) for _n, a, s in comp],
                guard_mask=guard_mask, guard_ops=self.guard_ops,
                chroma_max_increase=self.chroma_max,
                chroma_neutral_allowance=chroma_neutral_allowance,
                protect_chroma=guard_mask,
                palette=palette_arg, report=gate_report,
            )
            if guard_mask is not None:
                guard_note = f"skin-safe guard: held {'/'.join(held)} off {guard_pct * 100:.0f}% of frame"
            limited = gate_report.get("chroma_limited", 0.0)
            if limited > 0.005:
                notes_extra.append(f"oversaturation gate: pulled chroma back on {limited * 100:.0f}% of frame")

            # Banding gate (spec Phase 1): detect posterization in smooth regions of the EDITED
            # output; if present, reduce the stretch ops (dehaze/contrast/curve/clarity) and re-apply
            # ONCE. Measured on downscaled copies so it's cheap. Off when [banding] is absent.
            if self.banding:
                s_small = downscale_long_edge(Image(rgb=img.rgb), 1280).rgb
                e_small = downscale_long_edge(Image(rgb=out_rgb), 1280).rgb
                bscore, baction, _cov = banding.assess(s_small, e_small, self.banding)
                if baction in ("reduce", "fail"):
                    red = 0.35 if baction == "fail" else 0.55

                    def damp(settings: dict) -> dict:
                        out = dict(settings)
                        for k in ("dehaze", "contrast", "curve", "clarity", "sharpen"):
                            if out.get(k):
                                out[k] *= red
                        return out

                    gs2 = damp(global_settings)
                    regions2 = [(a, damp(s)) for a, s in regions_apply]
                    out_rgb = apply_image(
                        img.rgb, regions2, gs2,
                        compositional=[(a, s) for _n, a, s in comp],
                        guard_mask=guard_mask, guard_ops=self.guard_ops,
                        chroma_max_increase=self.chroma_max,
                        chroma_neutral_allowance=chroma_neutral_allowance,
                        protect_chroma=guard_mask,
                        palette=palette_arg, report={},
                    )
                    # The sidecar must report the settings that actually rendered the pixels —
                    # swap in the damped values so the JSON matches the output, not the plan.
                    global_settings = gs2
                    region_settings = {k: damp(s) for k, s in region_settings.items()}
                    notes_extra.append(
                        f"banding gate: smooth-region banding {bscore:.2f} → reduced "
                        f"dehaze/contrast/curve/clarity/sharpen {int((1 - red) * 100)}% and re-applied")
                elif baction == "warn":
                    notes_extra.append(f"banding watch: smooth-region banding {bscore:.2f} (dither applied)")

        # Optional finishing grade: match the look of a reference photo (degrades safely).
        ref_note = None
        if do_apply and out_rgb is not None and cfg.reference:
            try:
                from .edit.ops import apply_ops
                from .edit.reference import (
                    bottom_gradient_to_reference,
                    color_transfer,
                    midground_lift_to_reference,
                    mountain_highlight_to_reference,
                )

                if ref_proxy is None:
                    ref_proxy = self._get_ref_proxy()
                finish_strength = min(float(cfg.reference_strength), 0.72)
                out_rgb = color_transfer(out_rgb, ref_proxy.rgb, finish_strength)
                ref_note = (f"reference match → {os.path.basename(cfg.reference)} "
                            f"(strength {finish_strength:.2f})")
                edit_proxy = downscale_long_edge(Image(rgb=out_rgb), ref_proxy.long_edge)
                post_lift, post_lift_report = bottom_gradient_to_reference(
                    edit_proxy.rgb, ref_proxy.rgb, strength=cfg.reference_strength * 0.85)
                if post_lift:
                    alpha = linear_alpha(out_rgb.shape[:2], frm="bottom", coverage=0.62)
                    lifted = apply_ops(out_rgb, post_lift)
                    out_rgb = out_rgb * (1.0 - alpha[..., None]) + lifted * alpha[..., None]
                    moves = ", ".join(f"{k} {v:+.1f}" for k, v in post_lift.items())
                    notes_extra.append(
                        "post-reference foreground lift: "
                        f"lower luminance {post_lift_report['before_bottom']:.2f}→"
                        f"{post_lift_report['reference_bottom']:.2f} ({moves})")
                post_mid, post_mid_report = midground_lift_to_reference(
                    edit_proxy.rgb, ref_proxy.rgb, strength=cfg.reference_strength * 0.85)
                if post_mid:
                    alpha = _vertical_band_alpha(out_rgb.shape[:2], top=0.48, peak=0.70, bottom=0.93)
                    lifted = apply_ops(out_rgb, post_mid)
                    out_rgb = out_rgb * (1.0 - alpha[..., None]) + lifted * alpha[..., None]
                    moves = ", ".join(f"{k} {v:+.1f}" for k, v in post_mid.items())
                    notes_extra.append(
                        "post-reference midground lift: "
                        f"mid luminance {post_mid_report['before_mid']:.2f}→"
                        f"{post_mid_report['reference_mid']:.2f} ({moves})")
                post_mountain, post_mountain_report = mountain_highlight_to_reference(
                    edit_proxy.rgb, ref_proxy.rgb, strength=cfg.reference_strength * 0.85)
                if post_mountain:
                    alpha = _vertical_band_alpha(out_rgb.shape[:2], top=0.30, peak=0.47, bottom=0.66)
                    lifted = apply_ops(out_rgb, post_mountain)
                    out_rgb = out_rgb * (1.0 - alpha[..., None]) + lifted * alpha[..., None]
                    moves = ", ".join(f"{k} {v:+.1f}" for k, v in post_mountain.items())
                    notes_extra.append(
                        "post-reference mountain highlight: "
                        f"snow p90 {post_mountain_report['before_p90']:.2f}→"
                        f"{post_mountain_report['reference_p90']:.2f} ({moves})")
                # Reference transfer runs AFTER apply, so re-run the safety gates over it — a
                # match can re-introduce oversaturation/cast that the first gate already cleared.
                if self.chroma_max is not None:
                    out_rgb, rlim = gates.chroma_guard(
                        img.rgb.astype(np.float32), out_rgb,
                        max_increase=self.chroma_max, protect=guard_mask)
                    out_rgb = np.clip(out_rgb, 0.0, 1.0)
                    if rlim > 0.005:
                        notes_extra.append(
                            f"post-reference gate: pulled chroma back on {rlim * 100:.0f}% of frame")
            except Exception as e:
                ref_note = f"reference match skipped ({e})"

        # Post-edit safety assessment — measured on the EDITED output, not the source. Drives
        # the review queue + quality score (Codex review: review gate must read edited output).
        post_stats = None
        quality_score, needs_review, review_reason, warnings = 1.0, False, "", []
        if do_apply and out_rgb is not None:
            post_proxy = downscale_long_edge(Image(rgb=out_rgb), cfg.analysis_long_edge)
            post_stats, _ = stats_mod.compute(post_proxy, {})
            quality_score, needs_review, review_reason, warnings = gates.assess(
                gstats, post_stats, gate_report.get("chroma_limited", 0.0), thr=self.quality_gate)

        notes = build_notes(gstats, region_stats, resolved.detected)
        if intent_note:
            notes.append(intent_note)
        if region_ref_note:
            notes.append(region_ref_note)
        if palette_note:
            notes.append(palette_note)
        if guard_note:
            notes.append(guard_note)
        if ref_feedback_note:
            notes.append(ref_feedback_note)
        notes.extend(notes_extra)
        if ref_note:
            notes.append(ref_note)
        warnings = list(warnings)
        if needs_review:
            notes.append(f"⚠ needs review: {review_reason}")
        # arbitrated coverage + segmenter confidence per region, for the sidecar audit trail
        region_meta = {r.name: (float(r.coverage), float(r.score)) for r in resolved.regions}
        result = emit.build_result(
            image_name=os.path.basename(path),
            scene=sc.name,
            confidence=sc.confidence,
            style=cfg.style,
            brief=brief,
            detected=resolved.detected,
            gstats=gstats,
            region_stats=region_stats,
            global_settings=global_settings,
            region_settings=region_settings,
            compositional=comp_settings,
            notes=notes,
            region_meta=region_meta,
            post_edit_stats=post_stats,
            quality_score=quality_score,
            needs_review=needs_review,
            review_reason=review_reason,
            warnings=warnings,
            batch_group=batch_group,
            decider=self.decider.name,
            model=cfg.llm_model if cfg.decider == "llm" else None,
            timing_ms=(time.perf_counter() - t0) * 1000.0,
        )
        return out_rgb, result

    def _get_ref_proxy(self) -> Image | None:
        """Decode the reference image ONCE per run (downscaled to analysis size) and cache it.
        Reference-matched batches previously reloaded the same file 2-3x per photo."""
        if not self.cfg.reference:
            return None
        if self.ref_proxy is None:
            self.ref_proxy = downscale_long_edge(
                load_image(self.cfg.reference), self.cfg.analysis_long_edge)
        return self.ref_proxy

    def _reference_concept_features(self, concepts: list[str]) -> dict:
        """Per-concept look of the reference image (M5), segmented + measured ONCE per run and
        cached. Empty dict when no reference / segmentation fails (degrades to global transfer)."""
        if self.ref_feats is not None:
            return self.ref_feats
        self.ref_feats = {}
        if not self.cfg.reference:
            return self.ref_feats
        try:
            ref = self._get_ref_proxy()
            raw = self.segmenter.segment(ref, concepts)
            rr = resolve(raw, concepts, self.meta, ref.rgb.shape[:2])
            self.ref_feats = ref_match.extract_concept_features(ref.rgb, rr.union)
        except Exception:
            self.ref_feats = {}
        return self.ref_feats

    def _build_guard(self, union: dict, full_hw: tuple[int, int], global_settings: dict,
                     guide: np.ndarray | None = None):
        """Feathered alpha over the union of guard concepts (skin/face), upsampled to full res.
        Returns (mask | None, coverage_fraction, held_op_names). None when nothing to guard —
        no guard concept present, or the global pass touches none of the guarded ops."""
        held = sorted(self.guard_ops & set(global_settings))
        if not held:
            return None, 0.0, []
        mask = None
        for name, m in union.items():
            if not meta_for(name, self.meta).guard_global or m is None or not m.any():
                continue
            alpha = build_alpha(m, full_hw, meta_for(name, self.meta).feather_px, guide=guide)
            mask = alpha if mask is None else np.maximum(mask, alpha)
        if mask is None:
            return None, 0.0, []
        return mask, float(mask.mean()), held
