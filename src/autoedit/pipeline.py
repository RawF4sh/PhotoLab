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

from . import compose, emit, looks as looks_mod, rules as rules_mod, scene as scene_mod, stats as stats_mod
from .concepts import load_concepts, meta_for
from .contract import Result, RunConfig
from .decide import DecisionContext, make_decider
from .decide.base import Decider
from .edit import apply_image, build_alpha
from .imageio import Image, downscale_long_edge, load_image
from .notes import build_notes
from .segment import make_segmenter, resolve
from .segment.base import Segmenter
from .styles import Style, combine_brief, load_styles, style_for


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

    @classmethod
    def build(cls, cfg: RunConfig) -> "Pipeline":
        presets, meta = load_concepts(cfg.concepts_path)
        return cls(
            cfg=cfg,
            rules=rules_mod.load(cfg.rules_path),
            looks=looks_mod.load(cfg.looks_path),
            presets=presets,
            meta=meta,
            styles=load_styles(cfg.styles_path),
            compose_moves=compose.load_compose(cfg.compose_path) if cfg.compose else [],
            segmenter=make_segmenter(cfg.backend, cfg.device),
            decider=make_decider(cfg),
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

    def process_one(self, path: str, do_apply: bool = True) -> tuple[np.ndarray | None, Result]:
        t0 = time.perf_counter()
        cfg = self.cfg

        style = style_for(cfg.style, self.styles)
        look = cfg.look or style.look
        brief = combine_brief(style.brief, cfg.prompt)

        img: Image = load_image(path)
        proxy = downscale_long_edge(img, cfg.analysis_long_edge)
        concepts = self.concepts_for(style)

        raw = self.segmenter.segment(proxy, concepts)
        resolved = resolve(raw, concepts, self.meta, proxy.rgb.shape[:2])

        gstats, region_stats = stats_mod.compute(proxy, resolved.union)
        present = set(region_stats.keys())
        sc = scene_mod.classify(gstats, present)
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

        full_hw = (img.height, img.width)
        comp_settings = compose.settings_for(self.compose_moves, resolved.union, cfg.intensity)

        out_rgb = None
        if do_apply:
            regions_apply = []
            for r in resolved.regions:  # priority-descending
                s = region_settings.get(r.name)
                if not s:
                    continue
                cm = meta_for(r.name, self.meta)
                alpha = build_alpha(r.mask, full_hw, cm.feather_px)
                regions_apply.append((alpha, s))
            comp = compose.plan(self.compose_moves, resolved.union, full_hw, cfg.intensity)
            out_rgb = apply_image(
                img.rgb, regions_apply, global_settings,
                compositional=[(a, s) for _n, a, s in comp],
            )

        notes = build_notes(gstats, region_stats, resolved.detected)
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
            decider=self.decider.name,
            model=cfg.llm_model if cfg.decider == "llm" else None,
            timing_ms=(time.perf_counter() - t0) * 1000.0,
        )
        return out_rgb, result
