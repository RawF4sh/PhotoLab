"""Per-image pipeline (port of the Rust main.rs::process), end to end.

decode → segment → resolve → stats → scene → decide → apply → result.
A `Pipeline` is built once per run (configs + resident segmenter + decider) and reused
across the batch so the model load cost is paid once.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace

import numpy as np

from . import (backdrop as backdrop_mod, hide as hide_mod, reveal as reveal_mod, compose, consistency, emit, geometry, instructions, intent as intent_mod, light as light_mod, looks as looks_mod,
               lut as lut_mod, neutral as neutral_mod, night, palette as palette_mod, policies, rules as rules_mod, scene as scene_mod, stats as stats_mod)
from .concepts import load_concepts, meta_for
from .contract import Adjustment, HiddenRegion, Result, RunConfig
from .decide import DecisionContext, make_decider
from .decide.base import Decider
from .edit import style_target
from .edit.mask import snap_to_edges
from .edit import apply_image, banding, build_alpha, gates, ops, par, reference as ref_match, linear_alpha
from .imageio import Image, is_raw, analysis_opts, downscale_long_edge, load_image, luma, raw_opts_from, resize_to
from .notes import build_notes
from .segment import ResolvedRegions, make_segmenter, resolve
from . import segment as segment_mod
from .segment import MASK_CACHE_DIR, DiskMaskCache
from .segment.base import Segmenter, parse_mark
from .segment import refine as refine_masks
from .styles import Style, combine_brief, load_styles, style_for

def _config_digest(cfg: RunConfig) -> str:
    """A short hash of the config files a run reads, so a re-render can tell that they changed."""
    h = hashlib.blake2b(digest_size=8)
    for name in sorted(n for n in RunConfig.model_fields if n.endswith("_path")):
        try:
            with open(getattr(cfg, name), "rb") as fh:
                h.update(fh.read())
        except (OSError, TypeError):
            h.update(b"missing")
    return h.hexdigest()


# Run settings that switch on an opt-in rule set of the same name in rules.toml.
_OPT_IN_RULES = ("levels", "denoise", "night_sky", "highlight_detail")
_TRANSLATIONS = instructions._TRANSLATIONS   # the process cache of translated clauses lives next to its helper

# Never written into a sidecar: local paths and the secret. `adjust` takes the config paths from
# the current environment and asks for the two input files again.
_NOT_STORED = {name for name in RunConfig.model_fields if name.endswith("_path")} | {
    "api_key", "reference", "recipe_model", "workers", "threads", "lut", "history", "profile_dir"}

# High-risk global ops the skin-safe guard holds off protected (skin/face) regions when no
# [guard] block is present in rules.toml. Exposure/tone/contrast are intentionally NOT guarded —
# faces should still get global brightness/tonal correction, just not the aggressive look.
_DEFAULT_GUARD_OPS = {
    "clarity", "saturation", "vibrance", "hsl_skin_sat",
    "grade_shadows_temp", "grade_shadows_tint", "grade_highlights_temp", "grade_highlights_tint",
}
# Default oversaturation gate: a pixel's chroma may grow by at most +30% over the source.
_DEFAULT_CHROMA_MAX = 0.30
# Prompt words that mean "I want it dark" — they opt the photo out of the darkness-regression
# gate (rules.toml [quality_gate].preserve_terms overrides this list).
_DEFAULT_PRESERVE_TERMS = ("moody", "dark", "night", "low key", "low-key", "silhouette",
                          "preserve exposure", "keep exposure")


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
    auto_palette: dict  # [auto.<palette>] thresholds/caps from palettes.toml (rules-as-data)
    banding: dict
    quality_gate: dict | None = None  # post-edit gate thresholds from rules.toml (None → defaults)
    style_target: dict = field(default_factory=dict)
    style_safety: dict = field(default_factory=dict)
    ref_feats: dict | None = None    # cached per-concept reference look (M5); computed once per run
    ref_proxy: Image | None = None   # cached decoded reference proxy — one decode per run, not per photo
    instr: instructions.Vocab | None = None  # instructions.toml vocabulary (F2)
    adjustments: list = field(default_factory=list)      # parsed from cfg.edit, once per run
    adjust_problems: list = field(default_factory=list)  # clauses of cfg.edit that were not understood
    config_digest: str = ""   # hash of the config files, stored in the sidecar for `adjust`
    highlight_hold: tuple[float, float] | None = None  # (start, full) of the F6 hold; None → off
    light_cfg: dict = field(default_factory=dict)  # compose.toml [light]: thresholds of the light finder (F18)
    place_light_cfg: dict = field(default_factory=dict)  # the same, loaded on the first instruction that asks for a light
    lut: lut_mod.Lut | None = None  # the .cube file of cfg.lut (F25), read once per run
    habits: dict = field(default_factory=dict)  # scene -> the look's habit as adjustments of scope "look" (F27)
    geometry_cfg: dict = field(default_factory=dict)  # [geometry] of rules.toml; {} when neither straighten nor a crop is on (F32)
    neutral_cfg: dict = field(default_factory=lambda: dict(neutral_mod.DEFAULTS))  # [neutral] of rules.toml, checked (F34)
    backdrop_cfg: dict | None = None  # [backdrop] of rules.toml, checked; None when the clean backdrop is off (F34)
    reveal: "reveal_mod.Config | None" = None  # the list of things the reveal check looks for; None when the check is off (F21)
    night_cfg: dict = field(default_factory=dict)  # [night_sky] of rules.toml, checked (F31)

    @classmethod
    def build(cls, cfg: RunConfig) -> "Pipeline":
        par.set_threads(cfg.threads)
        lut = lut_mod.read_cube(cfg.lut) if cfg.lut else None   # a broken file stops the run here (F25)
        if cfg.backend == "auto":
            cfg = cfg.model_copy(update={"backend": segment_mod.resolve_backend(cfg.backend)})
        styles = load_styles(cfg.styles_path)
        style = style_for(cfg.style, styles)
        defaults = {k: v for k, v in style.run.items() if k not in cfg.model_fields_set}
        if defaults:
            cfg = cfg.model_copy(update=defaults)
        presets, meta = load_concepts(cfg.concepts_path)
        moves = compose.load_compose(cfg.compose_path, cfg, style.moves) if cfg.compose else []
        if not cfg.vignette:  # vignette is a toggleable subset of the compositional moves
            moves = [m for m in moves if "vignette" not in m.name]
        # Opt-in rule sets ([opt_in.<name>] in rules.toml) join the rules only when their run
        # setting is on; a default run never sees them.
        rules = rules_mod.load(cfg.rules_path, enable=[n for n in _OPT_IN_RULES if getattr(cfg, n, False)],
                               require=[*cfg.rule_sets, *(["lights"] if "lights" in style.concepts else [])])
        # which global ops the skin-safe guard holds off protected regions (rules-as-data; the
        # [guard] block in rules.toml, with a sane default so older config files still work).
        guard_ops = set(rules.get("guard", {}).get("ops", []) or _DEFAULT_GUARD_OPS)
        # oversaturation gate: cap each pixel's chroma increase vs the source (None disables).
        cm = rules.get("guard", {}).get("max_chroma_increase", _DEFAULT_CHROMA_MAX)
        chroma_max = None if cm in (None, "", "off") else float(cm)
        if "richer" in style.intent:
            # A richer specialist still uses its declared ceiling when a profile disables the general guard.
            chroma_max = min(0.90, max(chroma_max or 0.0, style.limits.get("chroma", _DEFAULT_CHROMA_MAX)))
        from .quality import load_quality_config
        hh = None
        if cfg.highlight_hold:
            blk = rules.get("highlight_hold", {})
            hh = (float(blk.get("start", 0.80)), float(blk.get("full", 0.985)))
        vocab = None
        adjustments, problems = [], []
        if cfg.edit and cfg.edit.strip():
            vocab = instructions.load_vocab(cfg.instructions_path)
            known = set(meta) | {c for cs in presets.values() for c in cs}
            adjustments, problems = instructions.parse(cfg.edit, vocab, known)
        decider = make_decider(cfg)
        if vocab is not None:   # clauses the word list missed or guessed at go to a model (F17, F20)
            adjustments, problems = instructions.translate_problems(
                problems, vocab, known, decider=decider, cfg=cfg, adjustments=adjustments)
        look_name = cfg.look or style_for(cfg.style, styles).look
        habit = looks_mod.load_habits(cfg.looks_path).get(look_name)
        habits = instructions.habit_adjustments(
            habit, look_name, vocab or instructions.load_vocab(cfg.instructions_path)) if habit else {}
        pipe_reveal = reveal_mod.load(cfg.reveal_path) if (cfg.reveal_check or cfg.hide) else None   # asking to hide runs the check
        if cfg.hide:
            reveal_mod.check_hide(pipe_reveal, cfg.hide)
        target = style_target.load(cfg.targets_path, style.target) if style.target else {}
        style_target.check_limits(style.name, target, style.limits)
        return cls(
            cfg=cfg,
            rules=rules,
            looks=looks_mod.load(cfg.looks_path),
            presets=presets,
            meta=meta,
            styles=styles,
            style_target=target,
            style_safety=load_quality_config() if style.target else {},
            compose_moves=moves,
            light_cfg=compose.load_light_config(cfg.compose_path) if any(m.at == "light" for m in moves) else {},
            segmenter=make_segmenter(cfg.backend, cfg.device),
            decider=decider,
            guard_ops=guard_ops,
            chroma_max=chroma_max,
            highlight_hold=hh,
            intents=intent_mod.load_intents(cfg.intents_path),
            palettes=palette_mod.load_palettes(cfg.palettes_path),
            auto_palette=palette_mod.load_auto_palette_config(cfg.palettes_path),
            banding={k: float(v) for k, v in (rules.get("banding", {}) or {}).items()
                     if isinstance(v, (int, float))},
            quality_gate={k: float(v) for k, v in (rules.get("quality_gate", {}) or {}).items()
                          if isinstance(v, (int, float))} or None,
            instr=vocab,
            adjustments=adjustments,
            config_digest=_config_digest(cfg),
            adjust_problems=problems,
            lut=lut,
            habits=habits,
            geometry_cfg=geometry.config(rules) if (cfg.straighten or cfg.out_aspect) else {},
            neutral_cfg=rules_mod.block(rules, "neutral", neutral_mod.DEFAULTS, neutral_mod.RANGES),
            backdrop_cfg=(rules_mod.block(rules, "backdrop", backdrop_mod.DEFAULTS, backdrop_mod.RANGES)
                          if cfg.backdrop else None),
            night_cfg=rules_mod.block(rules, "night_sky", night.DEFAULTS, night.RANGES),
            reveal=pipe_reveal,
        )

    def vocab(self) -> instructions.Vocab:
        """The instructions vocabulary, loaded on first use (a run without --edit never reads it)."""
        if self.instr is None:
            self.instr = instructions.load_vocab(self.cfg.instructions_path)
        return self.instr

    def known_concepts(self) -> set[str]:
        """Concept names the configs define; the instruction parser maps plurals onto them."""
        return set(self.meta) | {c for cs in self.presets.values() for c in cs}

    def use_mask_cache(self, out_dir: str) -> None:
        """Keep raw masks under `out_dir` so `autoedit adjust` and later runs skip segmentation.
        Not for the stub: its masks are instant and deterministic."""
        if (self.cfg.mask_cache and self.cfg.backend != "stub"
                and not isinstance(self.segmenter, DiskMaskCache)):
            self.segmenter = DiskMaskCache(
                self.segmenter, os.path.join(out_dir, MASK_CACHE_DIR, self.cfg.backend))

    def presegment(self, path: str) -> None:
        """Decode and segment one photo, nothing else: fills the mask cache for a render worker."""
        concepts = list(dict.fromkeys([*self.concepts_for(style_for(self.cfg.style, self.styles)),
                                      *self.cfg.selected_regions, *self.cfg.mask_refinements,
                                      *(a.target for a in self.adjustments if a.resolved_only)]))
        words = self._segment_words(concepts, self.adjustments + self._all_habits(),
                                    self._wants_guard(self.adjustments))
        if words:
            img = load_image(path, decode=analysis_opts(self.cfg, path) or raw_opts_from(self.cfg))
            self.segmenter.segment(downscale_long_edge(img, self.cfg.analysis_long_edge), words)
        if self.cfg.reference:
            # the reference's masks too, once per run: a render worker reads them from the cache
            self._reference_concept_features(concepts)

    def _wants_guard(self, adjs: list) -> bool:
        """A thing mark is in `adjs` and the segmenter can find named things (F37): the guard words are asked for."""
        return getattr(self.segmenter, "open_vocabulary", False) and any(
            (parse_mark(a.target) or ("",))[0] == "thing" for a in adjs)

    def _segment_words(self, concepts: list[str], adjs: list, guard: bool = False) -> list[str]:
        """Every word to segment for a photo; `presegment` and `process_one` both use it, so a render
        worker's cache has the reveal words too (F21)."""
        extra = self._with_light_concept(instructions.extra_concepts(adjs, concepts), concepts)
        if guard:
            # A thing mark keeps the limits of skin and snow inside it, also where the run found none (F37).
            extra += [n for n, cm in self.meta.items() if cm.guard_global and n not in concepts and n not in extra]
        extra = self._with_geometry_concepts(extra, concepts)
        words = concepts + extra
        words = list(dict.fromkeys([*words, *(w for name in concepts for w in meta_for(name, self.meta).words)]))
        if self.reveal is not None and getattr(self.segmenter, "open_vocabulary", False):
            words += [w for w in reveal_mod.words(self.reveal) if w not in words]
        return words

    def _segment(self, image, concepts, words):
        raw = self.segmenter.segment(image, words)
        for name in concepts:
            meta = meta_for(name, self.meta)
            if not meta.words:
                continue
            group = [r for r in raw if r.name in (name, *meta.words) and r.score >= meta.min_score]
            if group:
                merged = np.logical_or.reduce([r.mask for r in group])
                raw = [r for r in raw if r.name != name]
                raw.append(replace(group[0], name=name, mask=merged, score=max(r.score for r in group),
                                   coverage=float(merged.mean())))
        return raw

    def _all_habits(self) -> list[Adjustment]:
        """The look's habits of every scene: what to segment for before the scene is known."""
        return [a for scene in self.habits.values() for a in scene]

    def _with_light_concept(self, extra: list[str], concepts: list[str]) -> list[str]:
        """A move placed on the light (`at = "light"`) makes SAM look for the concept `sun` too."""
        if self.light_cfg and "sun" not in extra and "sun" not in concepts:
            return extra + ["sun"]
        return extra

    def _with_geometry_concepts(self, extra: list[str], concepts: list[str]) -> list[str]:
        """Straighten reads the water and the sky, a crop is placed on the subject: the segmenter looks for
        them too (F32). They never become regions. The stub's masks are brightness guesses: none is asked for."""
        if not self.geometry_cfg or self.cfg.backend == "stub":
            return extra
        want = (geometry.LEVEL if self.cfg.straighten else ()) + (self.geometry_cfg["subject"] if self.cfg.out_aspect else ())
        return extra + [n for n in want if n not in concepts and n not in extra]

    def _find_light(self, proxy_rgb: np.ndarray, raw: list, union: dict,
                    asked: bool = False) -> light_mod.Light | None:
        """The main light of the frame for the moves placed on it, or None (also when no move
        wants one). `asked`: an instruction wants the light ("add a glow"), whether or not the
        automatic glow is on. The `sun` mask joins the finder's input but not the regions."""
        if asked and not (self.light_cfg or self.place_light_cfg):
            self.place_light_cfg = compose.load_light_config(self.cfg.compose_path)
        light_cfg = self.light_cfg or self.place_light_cfg
        if not light_cfg:
            return None
        sun = next((r for r in raw if r.name == "sun" and r.mask.any()
                    and r.score >= meta_for("sun", self.meta).min_score), None)
        return light_mod.find_light(proxy_rgb, {**union, **({"sun": sun.mask} if sun else {})}, light_cfg)

    def concepts_for(self, style: Style) -> list[str]:
        """Resolution: explicit --concepts > --preset > the style's set > the generic preset."""
        if self.cfg.concepts is not None:
            return list(self.cfg.concepts)
        if self.cfg.preset:
            return self.presets.get(self.cfg.preset) or []
        if style.concepts:
            return list(style.concepts)
        return self.presets.get("generic") or ["subject", "sky", "skin"]

    def process_one(self, path: str, do_apply: bool = True,
                    image: Image | None = None, exposure_bias: float = 0.0,
                    batch_group: str = "",
                    wb_bias: tuple[float, float] = (0.0, 0.0),
                    on_resolved: Callable[[ResolvedRegions], None] | None = None,
                    on_stage=None,
                    adjustments: list[Adjustment] | None = None,
                    on_plan=None,
                    proxy: Image | None = None,
                    on_step=None,
                    on_geometry=None,
                    ) -> tuple[np.ndarray | None, Result]:
        t0 = time.perf_counter()
        cfg = self.cfg
        stage_timings = {}
        stage_started = time.perf_counter()

        style = style_for(cfg.style, self.styles)
        look = cfg.look or style.look
        brief = combine_brief(style.brief, cfg.prompt)

        # `image` lets callers (e.g. the UI preview) pass a pre-downscaled proxy so the full-res
        # apply stays fast; the batch/export path passes nothing → full resolution.
        # F51: a RAW file's analysis picture is its half-size development. With nothing to apply that is
        # the whole decode; otherwise the full development is decoded as well, and the edit applied to it.
        fast = analysis_opts(cfg, path) if image is None and proxy is None else None
        fast_img = load_image(path, decode=fast) if fast is not None else None
        if fast_img is not None and not fast_img.decode_info.get("half_size"):
            fast = None   # LibRaw returned the full size (a linear DNG): that already is the full development
        img: Image = image if image is not None else (
            fast_img if fast_img is not None and (fast is None or not do_apply) else load_image(path, decode=raw_opts_from(cfg)))
        if on_stage is not None:
            on_stage("candidate_decode", img.rgb)
        # `proxy`: the analysis picture, when the caller made it from a larger original than `image`
        # (a preview applies on a smaller copy).
        given_proxy = proxy is not None
        if fast is not None and do_apply:
            proxy, given_proxy = downscale_long_edge(fast_img, cfg.analysis_long_edge), True
        if proxy is None:
            proxy = downscale_long_edge(img, cfg.analysis_long_edge)
        adjs = [a.model_copy() for a in (*self.adjustments, *(adjustments or []))]
        concepts = list(dict.fromkeys([*self.concepts_for(style), *cfg.selected_regions, *cfg.mask_refinements,
                                      *(a.target for a in adjs if a.resolved_only)]))
        words = self._segment_words(concepts, adjs + self._all_habits(), self._wants_guard(adjs))
        stage_timings["decode_proxy"] = (time.perf_counter() - stage_started) * 1000

        stage_started = time.perf_counter()
        raw = self._segment(proxy, concepts, words) if words else []
        px = self._snap_px()
        if px:   # once, at the root: regions, instruction cells and marks all see the same masks. New arrays: cached masks are shared and never written into.
            snap = set(concepts) | set(instructions.extra_concepts(adjs + self._all_habits(), concepts))
            raw = [replace(rm, mask=snap_to_edges(rm.mask, px)) if rm.name in snap else rm for rm in raw]
        raw, refinement_notes = refine_masks.apply(raw, cfg.mask_refinements, self.meta, proxy.rgb.shape[:2])
        stage_timings["segment"] = (time.perf_counter() - stage_started) * 1000
        stage_started = time.perf_counter()
        resolved = resolve(raw, concepts, self.meta, proxy.rgb.shape[:2])
        stage_timings["resolve"] = (time.perf_counter() - stage_started) * 1000
        # Optional read-only observer for diagnostics such as preview mask overlays. Reuse the
        # exact arbitration used by apply; arrays stay out of the serializable sidecar.
        if on_resolved is not None:
            on_resolved(resolved)

        resolved_coverages = {r.name: float(r.coverage) for r in resolved.regions}
        stage_started = time.perf_counter()
        gstats, region_stats = stats_mod.compute(proxy, resolved.union, resolved_coverages)
        stage_timings["stats"] = (time.perf_counter() - stage_started) * 1000
        present = set(region_stats.keys())
        stage_started = time.perf_counter()
        # Night sky (F31): with the run setting on, a night frame takes the scene "night", whose rules
        # are the [opt_in.night_sky.night.*] blocks; any other frame keeps the usual scene.
        night_frame = cfg.night_sky and night.is_night(gstats, self.night_cfg)
        sc = scene_mod.classify(gstats, present, region_stats, override="night" if night_frame else None)
        stage_timings["scene"] = (time.perf_counter() - stage_started) * 1000
        reveal_rep = None
        to_hide: list = []
        if self.reveal is not None:   # beside stats and scene: a reveal mask is never a region, so nothing is edited by it (F21)
            stage_started = time.perf_counter()
            reveal_rep = reveal_mod.report(raw, self.reveal, sc.name, getattr(self.segmenter, "open_vocabulary", False),
                                    path, cfg.metadata, cfg.out_format)
            stage_timings["reveal"] = (time.perf_counter() - stage_started) * 1000
            if cfg.hide:
                if reveal_rep.picture == "checked":
                    to_hide = reveal_mod.hide_candidates(raw, self.reveal, sc.name, cfg.hide)
                    if not to_hide:
                        reveal_rep.hide_note = f"hide asked for {', '.join(cfg.hide)}: none was found, nothing was hidden"
                else:
                    reveal_rep.hide_note = (f"hide asked for {', '.join(cfg.hide)}: nothing was hidden, "
                                            "the picture part of the check did not run (it needs SAM 3)")
        region_rule = {name: meta_for(name, self.meta).region() for name in present}
        adjs = [a.model_copy() for a in self.habits.get(sc.name, [])] + adjs   # the look first, typed changes add to it

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
        stage_started = time.perf_counter()
        global_settings, region_settings = self.decider.decide(ctx)
        stage_timings["decide"] = (time.perf_counter() - stage_started) * 1000

        if not cfg.sharpen:  # strip the sharpen op when output sharpening is disabled
            global_settings.pop("sharpen", None)
            global_settings.pop("sharpen_stars", None)
            for s in region_settings.values():
                s.pop("sharpen", None)
                s.pop("sharpen_stars", None)
        if not cfg.levels:  # black and white point is opt-in until it has passed a blind vote (F5)
            for s in (global_settings, *region_settings.values()):
                s.pop("black_point", None)
                s.pop("white_point", None)
        if not cfg.denoise:  # noise reduction is opt-in until it has passed a blind vote (F7)
            for s in (global_settings, *region_settings.values()):
                s.pop("denoise", None)
                s.pop("denoise_stars", None)
        if not cfg.highlight_detail:  # the detail-keeping knee is opt-in (F50): no rule set, look or genre turns it on alone
            for s in (global_settings, *region_settings.values()):
                s.pop("highlight_detail", None)
        if not night_frame:  # the night tools are opt-in (F31), and only a night frame takes them, whoever proposed them
            for s in (global_settings, *region_settings.values()):
                for k in night.OPS:
                    s.pop(k, None)
        # Grain (F23) is asked for by the run setting, the look or an instruction, never by a
        # decider. Here it is a value in the global settings and no pixel: emit adds it to the
        # picture it writes, after the checks below and after the export resize.
        for s in (global_settings, *region_settings.values()):
            s.pop("grain", None)
        grain = cfg.grain if cfg.grain is not None else self.looks.get(look, looks_mod.Look()).grain * cfg.intensity
        if grain > 0.0:
            global_settings["grain"] = grain
        # Black and white (F24) comes from the look, an instruction or --set, never from a decider.
        # It is taken as written, not scaled by intensity: a black and white look is black and
        # white at every strength.
        for s in (global_settings, *region_settings.values()):
            for k in ops.BW_KEYS:
                s.pop(k, None)
        bw_look = self.looks.get(look, looks_mod.Look())
        if bw_look.bw > 0.0:
            global_settings.update({k: getattr(bw_look, k) for k in ops.BW_KEYS if getattr(bw_look, k)})
        # A LUT (F25) is asked for by the run setting, never by a decider or a look. Here it is a
        # value in the global settings and no pixel: it is laid over the finished picture below.
        for s in (global_settings, *region_settings.values()):
            s.pop("lut", None)
        if self.lut is not None and cfg.lut_amount > 0.0:
            global_settings["lut"] = cfg.lut_amount

        # A neutral pick (F34) replaces the white balance of the baseline: the spot the user says is grey or white is
        # measured on the analysis picture, and the temp and tint that make it neutral become the global ones. Regions
        # lose theirs: one white balance for the photo. What the user types or sets afterwards still adds to it.
        pick, pick_notes = None, []
        if cfg.neutral is not None:
            pick, why = neutral_mod.measure(proxy.rgb, cfg.neutral, self.neutral_cfg)
            if pick is None:
                pick_notes.append(f"neutral pick at {cfg.neutral[0]:.3f}, {cfg.neutral[1]:.3f}: not used, {why}")
        if pick is None and cfg.wb is not None:
            pick = neutral_mod.Pick(float(cfg.wb[0]), float(cfg.wb[1]))
        if pick is not None:
            was = (global_settings.get("temp", 0.0), global_settings.get("tint", 0.0))
            for name, s in region_settings.items():
                gone = [k for k in ("temp", "tint") if s.pop(k, None)]
                if gone:
                    pick_notes.append(f"neutral pick: {' and '.join(gone)} of {name} dropped, the pick sets the white balance")
            global_settings["temp"], global_settings["tint"] = pick.temp, pick.tint

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
                    sc.name, gstats, present, self.palettes, self.auto_palette)
        palette_obj = self.palettes.get(pal_name) if pal_name else None
        palette_strength = cfg.palette_strength * cfg.intensity * palette_scale
        if palette_auto_reason:
            # `palette_scale` is an absolute restraint cap, not merely a multiplier. Intensity may
            # make an explicit/requested palette stronger, but must never push an inferred grade
            # past the auto picker's ceiling.
            palette_strength = min(palette_scale, palette_strength)
        palette_arg = ((palette_obj, palette_strength) if palette_obj is not None else None)
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

        style_notes = []
        style_cap = None    # the style's lift budget in EV, when one applies to this photo
        if self.style_target:
            strength = min(1.0, style.pull * cfg.intensity)
            limits = dict(style.limits)
            if not is_raw(path) or img.source_bits <= 8 or not cfg.denoise:
                limits["exposure"] = min(2.0, limits.get("exposure", 2.0))
            held_regions = [(build_alpha(r.mask, proxy.rgb.shape[:2], meta_for(r.name, self.meta).feather_px),
                             region_settings.get(r.name, {})) for r in resolved.regions
                            if self.style_target.get(r.name, {}).get("hold")]
            before_ev = global_settings.get("exposure", 0.0)
            global_settings, moves = style_target.fit(proxy.rgb, global_settings,
                self.style_target.get("global", {}), strength, limits, self.style_safety, held_regions=held_regions,
                aim=style.aim)
            if moves:
                style_notes.append(f"{style.label}, frame: " + ", ".join(moves))
            elif self.style_target.get("global"):
                style_notes.append(f"{style.label}: retained frame settings after checking target ranges and clipping")
            for region in resolved.regions:
                ranges = self.style_target.get(region.name, {})
                current = region_settings.get(region.name, {})
                if ranges.get("hold"):
                    held = style_target.held_settings(current, global_settings.get("exposure", 0.0),
                                                       before_ev, limits.get("lights_ev"))
                    if held != current:
                        region_settings[region.name] = held
                        reduction = current.get("exposure", 0.0) - held["exposure"]
                        style_notes.append(f"{style.label}: held {region.name} back from the frame lift by {reduction:.2f} EV")
                    continue
                rails = meta_for(region.name, self.meta)
                fitted, changes = style_target.fit(proxy.rgb, current, ranges, strength, limits, self.style_safety,
                                                   mask=region.mask, global_after=global_settings,
                                                   caps=rails.max_ops, forbidden=rails.forbidden_ops, aim=style.aim)
                if changes:
                    region_settings[region.name] = fitted
                    style_notes.append(f"{style.label}, {region.name}: " + ", ".join(changes))
            # One budget for the whole lift (F59: a JPEG or 8-bit preview is capped at 2 EV).
            style_cap = limits.get("exposure")
            held_names = [n for n, ranges in self.style_target.items() if ranges.get("hold")]
            for name, (ev, allowed) in style_target.over_budget(
                    region_settings, global_settings.get("exposure", 0.0), before_ev, style_cap, held_names).items():
                region_settings[name] = {**region_settings[name], "exposure": allowed}
                style_notes.append(f"{style.label}: {name} lift reduced by {ev - allowed:.2f} EV, "
                                   f"frame and region together stay within {style_cap:g} EV")

        # Region policies: hard rails enforced on whatever the rules/LLM proposed (drop forbidden
        # ops, clamp magnitudes) so a region can't be over-driven into an unnatural colour.
        apply_regions, owners, adj_plan = resolved.regions, None, None
        light = self._find_light(proxy.rgb, raw, resolved.union,
                                 asked=any(a.target == instructions.LIGHT_TARGET for a in adjs))
        if adjs:
            adj_plan = instructions.plan(adjs, self.vocab(), raw, concepts, resolved,
                                         region_settings, global_settings, self.meta, light=light)
            apply_regions, region_settings, owners = adj_plan.cells, adj_plan.settings, adj_plan.owners
        policy_notes = refinement_notes + policies.enforce(region_settings, self.meta, owners=owners)
        if adj_plan is not None:
            policy_notes += self._hold_layers(adj_plan, resolved, light)
            policy_notes += instructions.report(adjs, adj_plan, region_settings, local_model=cfg.local_model)
            if on_plan is not None:
                on_plan(adjs, adj_plan, adj_plan.before, region_settings)
        policy_notes += [f"instruction {p}" for p in self.adjust_problems]
        for rs in region_settings.values():     # stuck pixels and the sky's gradient are the whole photo's (F31)
            rs.pop("hot_pixels", None)
            rs.pop("sky_gradient", None)
        # The grain that is added: the base above plus what instructions asked for, inside 0..100.
        grain = min(max(global_settings.pop("grain", 0.0), 0.0), ops.OP_BOUNDS["grain"][1])
        for rs in region_settings.values():     # grain is for the whole photo: a stored region value is dropped
            rs.pop("grain", None)
        if grain > 0.0:
            global_settings["grain"] = grain
        # Black and white (F24): the look's amount plus what instructions asked for, inside 0..100.
        # At 100 the photo ends neutral, so no palette grade is laid over it.
        bw_amount = min(max(global_settings.pop("bw", 0.0), 0.0), ops.OP_BOUNDS["bw"][1])
        bw_full = bw_amount >= ops.OP_BOUNDS["bw"][1]
        if bw_amount > 0.0:
            global_settings["bw"] = bw_amount
            weights = ", ".join(f"{k[3:]} {global_settings[k]:+g}" for k in ops.BW_KEYS[1:] if global_settings.get(k))
            policy_notes.append(f"black and white: {bw_amount:g} of 100" + (f" ({weights})" if weights else ""))
        else:   # the colour weights act only inside black and white: alone they are dropped, and said so
            idle = [k for k in ops.BW_KEYS[1:] if global_settings.pop(k, None)]
            if idle:
                policy_notes.append(f"black and white: {', '.join(idle)} dropped, they act only with bw above 0")
        if bw_full and palette_arg is not None:
            palette_arg, palette_note = None, "palette: none, the photo is black and white"

        # The LUT amount (F25): the run setting plus what this photo's own --set asked for, inside
        # 0..100. A LUT is for the whole photo, and without a LUT file there is nothing to blend.
        lut_amount = min(max(global_settings.pop("lut", 0.0), 0.0), ops.OP_BOUNDS["lut"][1])
        for rs in region_settings.values():
            rs.pop("lut", None)
        if lut_amount > 0.0 and self.lut is None:
            policy_notes.append("lut: this run has no LUT file, the amount is ignored")
        elif lut_amount > 0.0:
            global_settings["lut"] = lut_amount
            policy_notes.append(
                f"lut: {self.lut.name} ({self.lut.points} points, digest {self.lut.digest}), {lut_amount:g} of 100, "
                "laid over the finished picture after the oversaturation gate; the review check reads the result")

        # Geometry (F32): how the finished picture is turned and cut on its way out. Decided here,
        # from the masks; no pixel moves in this function. emit.py and the preview do it, after
        # every check below, so the gates and the stats compare two pictures of the source's shape.
        geo = None
        if cfg.photo_geometry is not None:
            geo, geo_notes = geometry.manual(cfg.photo_geometry, (img.height, img.width))
            policy_notes += geo_notes
            if on_geometry is not None:
                on_geometry(geo)
        elif self.geometry_cfg:
            stub = cfg.backend == "stub"
            wanted = set(geometry.LEVEL) | set(self.geometry_cfg["subject"])
            geo_masks = {} if stub else {r.name: r.mask for r in raw if r.name in wanted and r.mask.any()
                                         and r.score >= meta_for(r.name, self.meta).min_score}
            geo, geo_notes = geometry.decide(geo_masks, (img.height, img.width), cfg.straighten and not stub,
                                             cfg.out_aspect, self.geometry_cfg)
            if stub and cfg.straighten:
                geo_notes.insert(0, "straighten: not done, the stub backend cannot find water or sky (use --backend sam3)")
            policy_notes += geo_notes
            if on_geometry is not None:      # the preview turns every picture it shows the same way (F32)
                on_geometry(geo)

        # Shoot-consistency: a bounded exposure nudge toward this image's batch group target,
        # so a folder doesn't drift in brightness photo-to-photo (batch normalization, F5).
        if exposure_bias:
            base = global_settings.get("exposure", 0.0)
            top = 3.0
            if style_cap is not None and exposure_bias > 0:     # the style's lift budget binds this nudge too
                most = max((s.get("exposure", 0.0) for s in region_settings.values()), default=0.0)
                top = max(base, style_cap - max(0.0, most))
            moved = float(np.clip(base + exposure_bias, -3.0, top))
            global_settings["exposure"] = moved
            if top < 3.0 and moved - base < exposure_bias - 1e-9:
                policy_notes.append(
                    f"batch normalize: {moved - base:+.2f} EV of {exposure_bias:+.2f} EV toward group "
                    f"{batch_group or '?'}, limited by the style's lift budget")
            else:
                policy_notes.append(
                    f"batch normalize: {exposure_bias:+.2f} EV toward group {batch_group or '?'}")

        if pick is not None and (wb_bias[0] or wb_bias[1]):
            wb_bias = (0.0, 0.0)
            policy_notes.append("batch normalize: white balance not moved, the neutral pick sets it")
        if wb_bias[0] or wb_bias[1]:
            global_settings["temp"] = global_settings.get("temp", 0.0) + wb_bias[0]
            global_settings["tint"] = global_settings.get("tint", 0.0) + wb_bias[1]
            policy_notes.append(f"batch normalize: white balance {wb_bias[0]:+.1f} temp, "
                                f"{wb_bias[1]:+.1f} tint toward group {batch_group or '?'}")

        notes_extra: list[str] = list(policy_notes) + pick_notes + style_notes
        if cfg.night_sky:
            notes_extra.append(
                f"night sky: {'a' if night_frame else 'not a'} night frame (median {gstats.lum_p50:.2f}, ISO "
                f"{gstats.iso:.0f}): {'the night rules' if night_frame else 'the usual rules'}")

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
                notes_extra.extend(policies.enforce(region_settings, self.meta, owners=owners))
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
        if self.light_cfg:
            notes_extra.append(
                f"sun glow: light at {light.cx:.2f}, {light.cy:.2f} from the {light.source}" if light
                else "sun glow: no light source found, none added")
        line = waterline = None
        if cfg.backend != "stub" and any(move.at == "horizon" for move in self.compose_moves):
            line = geometry.horizon_position(resolved.union, geometry.config(self.rules))
        if cfg.backend != "stub" and any(move.at == "waterline" for move in self.compose_moves):
            waterline = geometry.horizon_position(resolved.union, geometry.config(self.rules), only="waterline")
        shape_masks = {r.name: r.mask for r in resolved.regions} if any(m.within for m in self.compose_moves) else None
        comp_settings = compose.settings_for(self.compose_moves, resolved.union, cfg.intensity, light, line, shape_masks,
                                             waterline)
        comp_settings.update(reference_comp_settings)
        if adj_plan is not None:    # a place the user named is a layer of its own, like a vignette (F18)
            comp_settings.update({instructions.LAYER + name: delta for name, delta in adj_plan.layers.items()})
            comp_settings.update({instructions.LAYER + name + "/guarded": g[0] for name, g in adj_plan.guarded.items()})

        out_rgb = None
        gate_src = None   # the oversaturation gate's reference, when it is not the plain source
        guard_note = None
        # Banding-gate state, threaded from the remediation decision (inside `if do_apply:`) to
        # the final post-reference-match reassessment (outside it) — must exist even when
        # do_apply is False, since that final block runs unconditionally on this path.
        band_smooth = None
        band_s_small = None
        band_remediated = False
        band_pre_score = 0.0
        band_reduce_factor = None
        residual_banding: tuple[float, str, bool] | None = None
        stage_started = time.perf_counter()
        if do_apply:
            # Night sky (F31): stuck pixels and the sky's gradient are corrections of the capture. They
            # are made once, to the decoded photo, before every region, the global pass and the checks.
            if global_settings.get("hot_pixels") or global_settings.get("sky_gradient"):
                img, night_notes = night.correct(img, proxy, resolved.regions, global_settings, self.night_cfg, self.meta)
                notes_extra += night_notes
            guide = luma(img.rgb)  # full-res structure reference for edge-aware mask feathering
            regions_apply = []
            if adj_plan is not None:
                for group, concept, touched in zip(adj_plan.groups, adj_plan.feather, adj_plan.touched):
                    cells = [(fmask, region_settings.get(cell.name) or {}) for cell, fmask in group]
                    if not touched:
                        cells = [(fmask, s) for fmask, s in cells if s]
                        if not cells:
                            continue
                    feather_px = meta_for(concept, self.meta).feather_px
                    entry = [(build_alpha(fmask, full_hw, feather_px, guide=guide), s) for fmask, s in cells]
                    regions_apply.append(entry if touched else entry[0])
            else:
                for r in apply_regions:  # priority-descending
                    s = region_settings.get(r.name)
                    if not s:
                        continue
                    cm = meta_for(r.name, self.meta)
                    alpha = build_alpha(r.mask, full_hw, cm.feather_px, guide=guide)
                    regions_apply.append((alpha, s))
            comp = compose.plan(self.compose_moves, resolved.union, full_hw, cfg.intensity, light, line,
                                {name: cm.feather_px for name, cm in self.meta.items()}, guide, shape_masks, waterline)
            guard_mask, guard_pct, held = self._build_guard(
                resolved.union, full_hw, global_settings, guide,
                always=bool(adj_plan is not None and adj_plan.guarded) or "richer" in style.intent)
            chroma_mask = guard_mask if held or "richer" in style.intent else None
            if adj_plan is not None:
                for name, delta in adj_plan.layers.items():
                    alpha = instructions.place_alpha(instructions.layer_spec(name, self.vocab()), full_hw, light,
                                                     adj_plan.sizes.get(name, 1.0))
                    if name in adj_plan.guarded and guard_mask is not None:   # the guarded part holds the limits
                        comp += [(instructions.LAYER + name, alpha * (1.0 - guard_mask), delta),
                                 (instructions.LAYER + name + "/guarded", alpha * guard_mask, adj_plan.guarded[name][0])]
                    else:
                        comp.append((instructions.LAYER + name, alpha, delta))
            if not held:        # the mask was made only for the layers: the global pass gets none
                guard_mask = None
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
            gate_report: dict = {}
            # What the oversaturation gate compares against. With noise reduction on, that is the
            # denoised source (see apply_image). It is the same for every render of this photo
            # (the banding re-render, the gate after a reference match), so it is made once.
            gate_src = None
            if self.chroma_max is not None and global_settings.get("denoise", 0.0):
                gate_src = ops.denoise(img.rgb.astype(np.float32), global_settings["denoise"],
                                       global_settings.get("denoise_stars", 0.0))
            if self.chroma_max is not None and pick is not None:
                # The gate compares with the source AS THE PICK SHOWS IT: against the plain source it would take
                # the correction back on every colour the cast had made grey (up to 15 of 255).
                gate_src = ops.light(gate_src if gate_src is not None else img.rgb.astype(np.float32),
                                     pick.temp, pick.tint)
            out_rgb = apply_image(
                img.rgb, regions_apply, global_settings,
                compositional=[(a, s) for _n, a, s in comp],
                guard_mask=guard_mask, guard_ops=self.guard_ops,
                chroma_max_increase=self.chroma_max,
                chroma_neutral_allowance=chroma_neutral_allowance,
                protect_chroma=chroma_mask,
                palette=palette_arg, report=gate_report,
                on_stage=on_stage, highlight_hold=self.highlight_hold,
                chroma_reference=gate_src, on_step=on_step,
            )
            if guard_mask is not None:
                guard_note = f"skin-safe guard: held {'/'.join(held)} off {guard_pct * 100:.0f}% of frame"
            if global_settings.get("highlight_detail", 0.0) and global_settings.get("highlight_rolloff", 0.0):
                notes_extra.append("highlight detail: the roll-off held the brightest tones by the level around them, "
                                   "so faint cloud and snow texture keep their contrast")
            held_white = gate_report.get("highlight_held", 0.0)
            if held_white > 0.005:
                # Two numbers: what was blown, and how much of the frame the soft shoulder below
                # it eased back. The second is much larger on a bright frame.
                blown = gate_report.get("highlight_blown", 0.0)
                what = (f"{blown * 100:.0f}% of the frame is blown in the source and stays white"
                        if blown >= 0.005 else "nothing in the frame is blown in the source")
                notes_extra.append(
                    f"highlight hold: {what}; {held_white * 100:.0f}% of the frame was eased back toward its "
                    "source brightness")
            limited = gate_report.get("chroma_limited", 0.0)
            if limited > 0.005:
                notes_extra.append(f"oversaturation gate: pulled chroma back on {limited * 100:.0f}% of frame")

            # Banding gate (spec Phase 1): detect posterization in smooth regions of the EDITED
            # output; if present, reduce every luma-stretch op and re-apply ONCE. The remediation
            # decision must happen HERE (before any reference-match finishing runs, since it needs
            # to re-render with damped settings) — but the FINAL verdict is deferred until after
            # reference matching (below), which is the last thing to touch out_rgb and can
            # reintroduce banding in the same smooth regions just remediated (mirrors why
            # chroma_guard is re-run post-reference-match, see that block's comment). Measured on
            # downscaled copies so it's cheap; the source smoothness mask is computed once and
            # reused across every assess() call this photo (it depends only on the unchanged
            # source), never recomputed per candidate.
            if self.banding:
                band_s_small = downscale_long_edge(Image(rgb=img.rgb), 1280).rgb
                band_smooth = banding.smooth_region_mask(luma(band_s_small))
                e_small = downscale_long_edge(Image(rgb=out_rgb), 1280).rgb
                bscore, baction, _cov = banding.assess(
                    band_s_small, e_small, self.banding, smooth=band_smooth)
                if baction in ("reduce", "fail"):
                    red = 0.35 if baction == "fail" else 0.55

                    gs2 = banding.damp_settings(global_settings, red)
                    regions2 = [[(a, banding.damp_settings(s, red)) for a, s in e]
                                if isinstance(e, list) else (e[0], banding.damp_settings(e[1], red))
                                for e in regions_apply]
                    out_rgb = apply_image(
                        img.rgb, regions2, gs2,
                        compositional=[(a, s) for _n, a, s in comp],
                        guard_mask=guard_mask, guard_ops=self.guard_ops,
                        chroma_max_increase=self.chroma_max,
                        chroma_neutral_allowance=chroma_neutral_allowance,
                        protect_chroma=chroma_mask,
                        palette=palette_arg, report={},
                        on_stage=on_stage, highlight_hold=self.highlight_hold,
                        chroma_reference=gate_src, on_step=on_step,
                    )
                    # The sidecar must report the settings that actually rendered the pixels —
                    # swap in the damped values so the JSON matches the output, not the plan.
                    global_settings = gs2
                    region_settings = {
                        k: banding.damp_settings(s, red) for k, s in region_settings.items()
                    }
                    band_remediated = True
                    band_pre_score = bscore
                    band_reduce_factor = red
                elif baction == "warn" and not (cfg.reference or global_settings.get("lut")):
                    # No remediation to attempt and nothing left that could still change out_rgb
                    # (no reference match queued) — this IS the final verdict; report it now. When
                    # cfg.reference IS set, defer: reference matching could still change the final
                    # banding state, so let the post-reference reassessment below decide.
                    notes_extra.append(f"banding watch: smooth-region banding {bscore:.2f} (dither applied)")
                    residual_banding = (bscore, baction, False)

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
                if self.highlight_hold is not None:   # the match can grey the held whites again
                    out_rgb, _ = gates.highlight_hold(img.rgb.astype(np.float32), out_rgb, *self.highlight_hold)
                if self.chroma_max is not None:
                    out_rgb, rlim = gates.chroma_guard(
                        gate_src if gate_src is not None else img.rgb.astype(np.float32), out_rgb,
                        max_increase=self.chroma_max, protect=chroma_mask)
                    out_rgb = np.clip(out_rgb, 0.0, 1.0)
                    if rlim > 0.005:
                        notes_extra.append(
                            f"post-reference gate: pulled chroma back on {rlim * 100:.0f}% of frame")
            except Exception as e:
                ref_note = f"reference match skipped ({e})"

        # A full black and white ends neutral (F24). Two steps after the global pass work against
        # the colour source and can hand a little of its colour back: the highlight hold and a
        # reference match. Every pixel is set to its own luma, so the tone they made stays.
        if bw_full and out_rgb is not None:
            out_rgb = np.repeat(luma(out_rgb)[..., None], 3, axis=-1)

        # Clean backdrop (F34): the light background of the finished picture is evened out and taken to the set white,
        # after the gates and a reference match, before a LUT. Outside the soft edge of the backdrop no pixel changes.
        # The review check below reads the result; the banding verdict does not (it reads a flat backdrop as a plateau).
        backdrop_report = None
        if do_apply and out_rgb is not None and self.backdrop_cfg is not None:
            small = downscale_long_edge(Image(rgb=out_rgb), cfg.analysis_long_edge)
            if given_proxy:
                small = resize_to(small, *proxy.rgb.shape[:2])
            keep = None
            if cfg.backend != "stub" and small.rgb.shape[:2] == resolved.shape:
                # What a model found is never backdrop. The stub's subject is an oval in the middle of every frame.
                found = [resolved.union[n] for n in self.backdrop_cfg["keep"] if n in resolved.union]
                keep = np.logical_or.reduce(found) if found else None
            out_rgb, backdrop_report, _weight = backdrop_mod.even(out_rgb, self.backdrop_cfg, keep=keep, small=small.rgb)
            if backdrop_report["applied"]:
                lo, hi = backdrop_report["level"]
                glo, ghi = backdrop_report["gain"]
                notes_extra.append(
                    f"backdrop: {backdrop_report['coverage'] * 100:.0f}% of the frame evened out and taken to "
                    f"{backdrop_report['white'] * 255:.0f} of 255; it was {lo * 255:.0f} to {hi * 255:.0f} "
                    f"(gain {glo:.2f} to {ghi:.2f})"
                    + ("; where it needed more than the limit it stays darker" if backdrop_report["limited"] else ""))
            else:
                notes_extra.append(f"backdrop: nothing changed, {backdrop_report['why']}")

        # A LUT file (F25) is laid over the finished picture: after the gates and after a reference
        # match, which judged and shaped the edit, not the LUT. The banding verdict and the review
        # check below read the result.
        if do_apply and out_rgb is not None and global_settings.get("lut", 0.0):
            shift = self.lut.shift      # made on first use, here and not in a strip thread
            out_rgb = par.rows(lambda a: ops.lut_map(a, shift, global_settings["lut"], self.lut.lo, self.lut.hi),
                               out_rgb)

        # Final banding verdict — deliberately AFTER reference matching (if any), on whatever
        # out_rgb actually is now. Runs whenever the gate is on and either remediation touched
        # the pixels or a reference match could have (cfg.reference): those are the only ways
        # out_rgb can differ from what the FIRST assess() already judged, so a "pass"-and-untouched
        # photo doesn't pay for a second identical assessment. Reuses band_smooth/band_s_small
        # (the source doesn't change) — never recomputes the smoothness mask.
        if self.banding and band_smooth is not None and (band_remediated or cfg.reference or global_settings.get("lut")):
            e_final_small = downscale_long_edge(Image(rgb=out_rgb), 1280).rgb
            final_score, final_action, _ = banding.assess(
                band_s_small, e_final_small, self.banding, smooth=band_smooth)
            if band_remediated:
                notes_extra.append(
                    f"banding gate: smooth-region banding {band_pre_score:.2f}→{final_score:.2f}; "
                    f"reduced luma-stretch ops {int((1 - band_reduce_factor) * 100)}% and re-applied")
            elif final_action == "warn":
                notes_extra.append(
                    f"banding watch: smooth-region banding {final_score:.2f} (dither applied)")
            if final_action != "pass":
                residual_banding = (final_score, final_action, band_remediated)

        # Hide on request (F22): the last pixel step of apply, on the full-size picture, only for the kinds the user
        # named. Every hidden region goes into the sidecar. Nothing here generates content.
        if do_apply and out_rgb is not None and to_hide:
            method = cfg.hide_method or self.reveal.hide.method
            out_rgb, boxes = hide_mod.apply(out_rgb, [m for _f, m in to_hide], self.reveal.hide, method)
            reveal_rep.hidden = [HiddenRegion(kind=f.kind, label=f.label, box=list(b), method=method, trace=hide_mod.TRACE[method])
                                 for (f, _m), b in zip(to_hide, boxes)]
            what = ", ".join(f"{n} {k}" for k, n in Counter((f.label or f.kind) for f, _m in to_hide).items())
            notes_extra.append(f"hidden on request: {what}, by {method}"
                               + ("; it keeps a trace of what was there" if hide_mod.TRACE[method] else "; nothing under it is copied into the fill")
                               + ". The area is the finding grown by a margin; nothing is generated, and the source is unchanged")
        if reveal_rep is not None and cfg.hide and not do_apply and to_hide:   # a dry run has no picture to hide in
            reveal_rep.hide_note = (f"hide asked for {', '.join(cfg.hide)}: {len(to_hide)} found, nothing was hidden "
                                    "because this was a dry run (no picture was made)")
        if reveal_rep is not None and reveal_rep.hide_note:
            notes_extra.append(reveal_rep.hide_note)
        stage_timings["apply_finish"] = (time.perf_counter() - stage_started) * 1000
        if on_stage is not None and out_rgb is not None:
            on_stage("final_float", out_rgb)
        stage_started = time.perf_counter()
        # Post-edit safety assessment — measured on the EDITED output, not the source. Drives
        # the review queue + quality score (Codex review: review gate must read edited output).
        post_stats = None
        post_regions: dict = {}
        quality_score, needs_review, review_reason, warnings = 1.0, False, "", []
        if do_apply and out_rgb is not None:
            post_proxy = downscale_long_edge(Image(rgb=out_rgb), cfg.analysis_long_edge)
            if given_proxy:
                post_proxy = resize_to(post_proxy, *proxy.rgb.shape[:2])
            # Same masks as the source stats, so the sidecar shows per-region pre→post luma
            # (the diagnostics the dark-RAW investigation lacked). Masks are proxy-res; the
            # post proxy has the proxy's shape by construction — guard anyway.
            same_shape = post_proxy.rgb.shape[:2] == proxy.rgb.shape[:2]
            post_stats, post_regions = stats_mod.compute(
                post_proxy, resolved.union if same_shape else {},
                resolved_coverages if same_shape else None)
            preserve_terms = (self.rules.get("quality_gate", {}) or {}).get(
                "preserve_terms", _DEFAULT_PRESERVE_TERMS)
            preserve = (bool(cfg.prompt) and any(t in cfg.prompt.lower() for t in preserve_terms)) or \
                self.looks.get(look, looks_mod.Look()).preserve_exposure
            quality_score, needs_review, review_reason, warnings = gates.assess(
                gstats, post_stats, gate_report.get("chroma_limited", 0.0), thr=self.quality_gate,
                preserve_exposure=preserve or "darker" in style.intent, richer="richer" in style.intent)
        subject_colour = None
        if cfg.colour_report and post_stats is not None and same_shape:     # F34: the colour report reads this
            subject_colour = consistency.subject_colour(
                post_proxy.rgb, resolved.union, (self.rules.get("consistency") or {}).get("colour_of", ["subject", "person"]))
        neutral_audit = None
        if pick is not None:       # what the pick did, and where the spot ended (F34)
            neutral_audit = {"temp": round(pick.temp, 3), "tint": round(pick.tint, 3),
                             "rules": [round(was[0], 3), round(was[1], 3)]}
            said = (f"white balance temp {pick.temp:+.1f}, tint {pick.tint:+.1f} "
                    f"(the rules: {was[0]:+.1f}, {was[1]:+.1f})")
            if pick.xy is None:
                notes_extra.append(f"neutral pick: {said}, given for the run")
            else:
                neutral_audit.update(x=round(pick.xy[0], 4), y=round(pick.xy[1], 4), source=list(pick.source))
                line = f"neutral pick at {pick.xy[0]:.3f}, {pick.xy[1]:.3f}: {said}; the spot was " + \
                       ", ".join(f"{v:.0f}" for v in pick.source) + " of 255"
                if post_stats is not None and same_shape:
                    after = [round(float(v) * 255.0, 1)
                             for v in neutral_mod.spot_mean(post_proxy.rgb, pick.xy, self.neutral_cfg["patch"])]
                    off = round(max(after) - min(after), 1)
                    neutral_audit.update(result=after, off=off)
                    line += " and is " + ", ".join(f"{v:.0f}" for v in after)
                    if off > 1:
                        line += f", {off:.1f} from neutral: something after the white balance adds colour there"
                notes_extra.append(line)
        if residual_banding is not None:
            bscore, baction, was_remediated = residual_banding
            warnings = list(warnings) + [f"smooth-region banding {bscore:.2f} ({baction})"]
            if baction in ("reduce", "fail"):
                quality_score = min(quality_score, 0.80 if baction == "reduce" else 0.70)
                needs_review = True
                band_reason = f"residual smooth-region banding {bscore:.2f}"
                review_reason = f"{review_reason}; {band_reason}" if review_reason else band_reason
            elif baction == "warn" and was_remediated:
                # The gate already damped+reapplied once and STILL lands in "warn" — it worked
                # hard, same as gates.py's "oversaturation gate worked hard" pattern, which also
                # applies a small penalty rather than failing hard. A first-pass "warn" (no
                # remediation attempted) stays a pure heads-up with no score impact.
                quality_score = max(0.0, quality_score - 0.05)

        notes = build_notes(gstats, region_stats, resolved.detected)
        if cfg.genre:
            notes.append(f"genre: {cfg.genre}")
        if img.decode_ev:
            notes.append(f"raw develop: {cfg.raw_mode} {img.decode_ev:+.2f} EV at decode")
        if proxy.decode_info.get("half_size"):
            notes.append("fast analysis: the edit was decided on the half-size development of the RAW file")
        lens_rep = (img.decode_info or {}).get("lens")
        if lens_rep:
            parts = [f"{c} {r['corner_px']:+.1f} px" + ("" if r["applied"] else " (left, below 0.3 px)")
                     for c, r in lens_rep.items()]
            notes.append("lens: colour fringe at the frame corner " + ", ".join(parts))
        di = img.decode_info or {}
        vg = di.get("vignette")
        if vg:
            if vg["applied"]:
                b = vg["bucket"]
                notes.append(f"lens: corners brightened +{vg['corner_ev']:.2f} EV (profile of {vg['frames']} photos, "
                             f"{b[0]:.0f}-{b[1]:.0f} mm f/{b[2]:.1f}-{b[3]:.0f})")
            else:
                notes.append(f"lens: vignette left as decoded ({vg['reason']})")
        if di.get("highlight") == "recover":
            notes.append(f"raw recover: {di['recover_clipped'] * 100:.1f}% of the frame had a clipped channel; "
                         f"rebuilt from LibRaw's blend decode, {di['recover_top']:.2f} times the old white "
                         "mapped to white")
        elif di.get("recover_skipped"):
            notes.append(f"raw recover: not applied, {di['recover_skipped']}")
        for name, pre_r in region_stats.items():  # where did the brightness actually move?
            post_r = post_regions.get(name)
            if post_r is not None:
                notes.append(f"{name}: luma {pre_r.lum:.2f} → {post_r.lum:.2f}")
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
        region_meta = {r.name: (float(r.coverage), float(r.score)) for r in apply_regions}
        result = emit.build_result(
            image_name=os.path.basename(path),
            scene=sc.name,
            confidence=sc.confidence,
            style=cfg.style,
            brief=brief,
            detected=resolved.detected + (adj_plan.detected if adj_plan else []),
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
            post_region_stats=list(post_regions.values()),
            decode_ev=float(img.decode_ev),
            reveal=reveal_rep,
            decider=self.decider.name,
            model=cfg.llm_model if cfg.decider == "llm" else None,
            timing_ms=(time.perf_counter() - t0) * 1000.0,
        )
        stage_timings["assess_result"] = (time.perf_counter() - stage_started) * 1000
        result.adjustments = adjs
        result.geometry = geo
        result.stage_timings_ms.update(stage_timings)
        result.audit.update(decode_info=getattr(img, "decode_info", {}),
                            source_format=os.path.splitext(path)[1].lower().lstrip("."),
                            decode_injected=image is not None)
        if style.target:
            result.audit["local_style"] = {"name": style.name, "target": style.target, "pull": style.pull,
                                           "intent": list(style.intent), "tested": style.tested}
        if neutral_audit is not None:
            result.audit["neutral"] = neutral_audit
        if backdrop_report is not None:
            result.audit["backdrop"] = backdrop_report
        if subject_colour is not None:
            result.audit["subject_colour"] = subject_colour
        result.exposure_bias = float(exposure_bias)
        result.wb_bias = [float(wb_bias[0]), float(wb_bias[1])]
        result.audit.update(
            run_config=cfg.model_dump(mode="json", exclude=_NOT_STORED),
            run_inputs={k: (os.path.basename(v) if v else None)
                        for k, v in (("reference", cfg.reference),
                                     ("recipe_model", cfg.recipe_model if cfg.decider == "learned" else None))},
            config_digest=self.config_digest)
        if self.lut is not None:   # the file's name and digest, never its folder; `adjust` asks for the file again
            result.audit["run_inputs"]["lut"] = self.lut.name
            result.audit["lut"] = {"name": self.lut.name, "digest": self.lut.digest, "points": self.lut.points}
        return out_rgb, result

    def _snap_px(self) -> int:
        return int((self.rules.get("masks") or {}).get("snap_edge_px", 12)) if self.cfg.snap_edges else 0

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
            words = list(dict.fromkeys([*concepts, *(w for name in concepts for w in meta_for(name, self.meta).words)]))
            raw = self._segment(ref, concepts, words)
            rr = resolve(raw, concepts, self.meta, ref.rgb.shape[:2])
            self.ref_feats = ref_match.extract_concept_features(ref.rgb, rr.union)
        except Exception:
            self.ref_feats = {}
        return self.ref_feats

    def _hold_layers(self, adj_plan, resolved: ResolvedRegions, light) -> list[str]:
        """The part of every instruction layer (an area mark or a named place) that lies on a guarded
        region the run found (skin, snow) gets the change after that region's policy (F37). Fills
        `adj_plan.guarded`; returns the policy notes. A layer the policies leave alone stays one layer."""
        vocab, notes = self.vocab(), []
        names = [n for n, m in resolved.union.items() if meta_for(n, self.meta).guard_global and m.any()]
        if not vocab.shape_guard or not names:
            return notes
        for name, delta in adj_plan.layers.items():
            alpha = instructions.place_alpha(instructions.layer_spec(name, vocab), resolved.shape, light,
                                             adj_plan.sizes.get(name, 1.0))
            # Only the regions the layer reaches: a face keeps the skin limits, not those of snow elsewhere.
            # A weight of 1e-4 is what apply_image treats as nothing for a whole layer; the same floor is used
            # here per region, so the far tail of a glow does not count as reaching it.
            under = [n for n in names if alpha[resolved.union[n]].max() > 1e-4]
            if not under:
                continue
            held, key = dict(delta), instructions.LAYER + name + "/guarded"
            enforced = policies.enforce({key: held}, self.meta, owners={key: under})
            if enforced:
                on_guarded = np.logical_or.reduce([resolved.union[n] for n in under])
                adj_plan.guarded[name] = (held, float(alpha[on_guarded].sum() / alpha.sum()), under)
                notes += enforced
        return notes

    def _build_guard(self, union: dict, full_hw: tuple[int, int], global_settings: dict,
                     guide: np.ndarray | None = None, always: bool = False):
        """Feathered alpha over the union of guard concepts (skin/face), upsampled to full res.
        Returns (mask | None, coverage_fraction, held_op_names). None when nothing to guard —
        no guard concept present, or the global pass touches none of the guarded ops (`always`:
        the mask is returned anyway, with no held ops, for the guarded part of a layer)."""
        held = sorted(self.guard_ops & set(global_settings))
        if not held and not always:
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
