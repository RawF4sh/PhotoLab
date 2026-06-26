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
from .edit import apply_image, banding, build_alpha, gates
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
        # cfg.palette wins; "none"/"off" disables; otherwise a cue word in the prompt selects one.
        # Strength scales by intensity; applied as colour finishing in apply_image.
        if cfg.palette:
            pal_name = None if cfg.palette.lower() in ("none", "off", "") else cfg.palette
        else:
            pal_name = palette_mod.palette_for_prompt(cfg.prompt, self.palettes)
        palette_obj = self.palettes.get(pal_name) if pal_name else None
        palette_arg = ((palette_obj, cfg.palette_strength * cfg.intensity)
                       if palette_obj is not None else None)
        palette_note = (f"palette: {pal_name} ({cfg.palette_strength:.0%})"
                        if palette_obj is not None else None)

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

        full_hw = (img.height, img.width)
        comp_settings = compose.settings_for(self.compose_moves, resolved.union, cfg.intensity)

        out_rgb = None
        guard_note = None
        notes_extra: list[str] = list(policy_notes)
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
            guard_mask, guard_pct, held = self._build_guard(resolved.union, full_hw, global_settings, guide)
            gate_report: dict = {}
            out_rgb = apply_image(
                img.rgb, regions_apply, global_settings,
                compositional=[(a, s) for _n, a, s in comp],
                guard_mask=guard_mask, guard_ops=self.guard_ops,
                chroma_max_increase=self.chroma_max, protect_chroma=guard_mask,
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
                    red = 0.5 if baction == "fail" else 0.65
                    gs2 = dict(global_settings)
                    for k in ("dehaze", "contrast", "curve", "clarity"):
                        if gs2.get(k):
                            gs2[k] *= red
                    out_rgb = apply_image(
                        img.rgb, regions_apply, gs2,
                        compositional=[(a, s) for _n, a, s in comp],
                        guard_mask=guard_mask, guard_ops=self.guard_ops,
                        chroma_max_increase=self.chroma_max, protect_chroma=guard_mask,
                        palette=palette_arg, report={},
                    )
                    notes_extra.append(
                        f"banding gate: smooth-region banding {bscore:.2f} → reduced "
                        f"dehaze/contrast/curve/clarity {int((1 - red) * 100)}% and re-applied")
                elif baction == "warn":
                    notes_extra.append(f"banding watch: smooth-region banding {bscore:.2f} (dither applied)")

        # Optional finishing grade: match the look of a reference photo (degrades safely).
        ref_note = None
        if do_apply and out_rgb is not None and cfg.reference:
            try:
                from .edit.reference import color_transfer

                ref_proxy = downscale_long_edge(load_image(cfg.reference), 512)
                out_rgb = color_transfer(out_rgb, ref_proxy.rgb, cfg.reference_strength)
                ref_note = (f"reference match → {os.path.basename(cfg.reference)} "
                            f"(strength {cfg.reference_strength:.2f})")
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
                gstats, post_stats, gate_report.get("chroma_limited", 0.0) if do_apply else 0.0)

        notes = build_notes(gstats, region_stats, resolved.detected)
        if intent_note:
            notes.append(intent_note)
        if palette_note:
            notes.append(palette_note)
        if guard_note:
            notes.append(guard_note)
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
