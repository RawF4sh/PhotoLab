"""The data contract — run config + serializable Result (sidecar JSON).

Ported from the Rust scaffold's engine/src/io.rs. Two kinds of data live here:
- Pydantic models for everything that gets serialized (RunConfig, Result, sidecar).
- Plain dataclasses (in the modules that own them) for runtime objects holding numpy
  arrays (masks, image buffers) — those never get serialized directly.

`Settings` is a plain dict of edit-op name -> value, e.g. {"exposure": 0.35, "vibrance": 6}.
Op names and units are defined by the apply engine (edit/ops.py), NOT Adobe CRS names.
"""

from __future__ import annotations

import re
import string
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .paths import config_path

# A bundle of edit-op values. Keys are op names from edit/ops.py OP_NAMES.
Settings = dict[str, float]


class RunConfig(BaseModel):
    """Everything a batch run needs. Built from configs/defaults.toml + a --config file
    + CLI overrides (see cli.py::load_config)."""

    # selection / look
    style: str = "natural"  # styles.toml profile: look + default concepts + brief
    prompt: str | None = None  # free-text intent, combined with the style brief for the LLM
    edit: str | None = Field(default=None, max_length=400)
    # plain-language region instructions, e.g. "brighten the jacket" (instructions.toml)
    preset: str | None = None  # concept-set override (concepts.toml); wins over the style's set
    concepts: list[str] | None = None  # None uses style/preset; [] explicitly disables region editing
    look: str | None = None  # magnitude profile override; defaults to the style's look
    intensity: float = Field(default=0.8, ge=0.0, le=2.0)
    palette: str | None = None  # palettes.toml colour-grade ("cool_blue"…); None → infer from prompt / off
    palette_strength: float = Field(default=0.8, ge=0.0, le=1.0)  # 0 = off, 1 = full palette grade

    # reference-style color match (optional): grade the result toward an example image's look
    reference: str | None = None  # path to a reference photo whose palette/tone to match
    reference_strength: float = Field(default=0.7, ge=0.0, le=1.0)  # 0 = off, 1 = full match

    # decision
    decider: Literal["rules", "llm", "learned"] = "rules"
    recipe_model: str | None = None  # path to a trained recipe predictor (used when decider="learned")
    allow_cloud: bool = False  # explicit consent required to use the cloud LLM (BYO-key)
    # Anthropic API key (falls back to ANTHROPIC_API_KEY env). exclude=True/repr=False so no
    # model_dump()/repr/log path can ever serialize the secret — read it via cfg.api_key only.
    api_key: str | None = Field(default=None, exclude=True, repr=False)
    llm_mode: Literal["refine", "propose"] = "refine"
    llm_model: str = "claude-haiku-5-5"
    llm_temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    llm_max_calls: int = Field(default=0, ge=0)  # 0 = unlimited
    llm_budget_usd: float = Field(default=1.0, ge=0.0)  # per run; 0 = unlimited
    # prices of llm_model, for the budget cap; update them when you change the model
    llm_input_usd_per_mtok: float = Field(default=0.10, ge=0.0)
    llm_output_usd_per_mtok: float = Field(default=0.50, ge=0.0)
    llm_translate_instructions: bool = True
    llm_thumb_px: int = Field(default=512, ge=64, le=1024)

    # RAW development (imageio._decode_raw). "none" is today's libraw render and stays the
    # default; the other modes are benchmark candidates (scripts/raw_benchmark.py) — a benchmark
    # may recommend one, it never flips this default by itself.
    raw_mode: Literal["none", "libraw_auto", "preview_ev", "preview_ev_headroom", "tone_target", "linear_srgb"] = "none"
    raw_max_ev: float = Field(default=2.5, ge=0.0, le=5.0)          # never lift more than this
    raw_clip_budget: float = Field(default=0.005, ge=0.0, le=0.1)   # fraction allowed to reach white
    raw_tone_target_p50: float = Field(default=0.40, gt=0.0, lt=1.0)  # tone_target: median (gamma)
    raw_fallback_ev: float = Field(default=0.0, ge=0.0, le=5.0)     # no universal lift without a preview

    # segmentation
    backend: Literal["stub", "sam3", "auto"] = "stub"
    device: str = "auto"
    analysis_long_edge: int = Field(default=1536, ge=64, le=8192)
    mask_cache: bool = True  # keep raw masks under <out>/.autoedit-masks (not for the stub) so re-renders skip segmentation
    workers: int = Field(default=1, ge=0, le=64)  # render processes for a batch; 0 = half the cores, at most 4
    threads: int = Field(default=0, ge=0, le=256)  # threads for per-pixel work inside one render; 0 = one per core

    consistency: Literal["brightness", "bursts"] = "brightness"  # shoot consistency (F9); "bursts" evens out frames of one framing

    # output
    out_format: Literal["jpeg", "jpg", "tiff", "webp", "png"] = "jpeg"
    out_long_edge: int | None = Field(default=None, ge=64, le=20000)  # longest side of the export in pixels (F10); never upscales; None = full size
    out_name: str = "{stem}"  # export file name without extension; fields {stem} {style} {n}
    out_subdir: str = ""  # subfolder of the output folder
    export: str | None = None  # name of a preset in exports.toml (applied by the CLI and the web service)
    quality: int = Field(default=95, ge=1, le=100)  # higher q preserves the anti-banding dither (TIFF stays lossless)
    metadata: Literal["keep", "shareable", "none"] = "shareable"  # EXIF carried into exports (F1); "shareable" drops GPS and serial numbers; sRGB profile always embedded
    emit: Literal["baked"] = "baked"  # "editable" (XMP hand-off) was accepted but never implemented; removed until it exists
    compose: bool = True  # apply radial/linear compositional moves (vignette, sky grad)
    vignette: bool = True  # frame/subject vignette (subset of compose); off → no edge falloff
    sharpen: bool = True  # output sharpening (unsharp mask); off → no sharpen ops applied
    highlight_hold: bool = False  # keep source-blown highlights white (F6); off → the edit may grey them
    levels: bool = False  # black and white point (F5); off → the rule-driven black_point is dropped after decide
    denoise: bool = False  # noise reduction (F7); off → the rule set is not loaded and the op is dropped after decide

    # paths to config assets — resolved to ./configs or the packaged configs (see paths.py)
    rules_path: str = Field(default_factory=lambda: config_path("rules.toml"))
    looks_path: str = Field(default_factory=lambda: config_path("looks.toml"))
    concepts_path: str = Field(default_factory=lambda: config_path("concepts.toml"))
    styles_path: str = Field(default_factory=lambda: config_path("styles.toml"))
    compose_path: str = Field(default_factory=lambda: config_path("compose.toml"))
    intents_path: str = Field(default_factory=lambda: config_path("intents.toml"))
    palettes_path: str = Field(default_factory=lambda: config_path("palettes.toml"))
    instructions_path: str = Field(default_factory=lambda: config_path("instructions.toml", packaged_fallback=True))
    exports_path: str = Field(default_factory=lambda: config_path("exports.toml", packaged_fallback=True))

    @field_validator("out_name")
    @classmethod
    def _check_out_name(cls, v: str) -> str:
        # Only plain {stem} {style} {n}: attribute/index access ({n[0]}, {stem.x}), conversions and
        # nested fields in a spec could pull other text into the name. A spec is for {n} only.
        try:
            for _, field, spec, conv in string.Formatter().parse(v):
                if field is None:
                    continue
                if field not in ("stem", "style", "n") or conv is not None:
                    raise ValueError(f"field {{{field}}} is not allowed, use plain {{stem}}, {{style}}, {{n}}")
                if spec and (field != "n" or "{" in spec or "}" in spec):
                    raise ValueError(f"a format spec is only allowed on {{n}}, without braces (got {spec!r})")
                if spec and not re.fullmatch(r"(0?[1-9][0-9]?)?d?", spec):
                    # {n:c} writes a control character, {n:x} hex, {n:>8} pads with spaces
                    raise ValueError(f"the format spec of {{n}} is a width of 1 to 99, an optional 0 in front "
                                     f"and an optional d (got {spec!r})")
            name = v.format(stem="x", style="s", n=1)
        except (KeyError, IndexError, ValueError, AttributeError, TypeError) as e:
            raise ValueError(f"out_name {v!r} is not a valid template (fields: stem, style, n): {e}") from None
        if not name or "/" in name or "\\" in name or ".." in name:
            raise ValueError(f"out_name {v!r} must give a non-empty file name without '/', '\\' or '..'")
        return v

    @field_validator("out_subdir")
    @classmethod
    def _check_out_subdir(cls, v: str) -> str:
        if (len(v) > 100 or v.startswith(("/", "\\")) or ".." in v.replace("\\", "/").split("/")
                or (len(v) > 1 and v[1] == ":") or "{" in v or "}" in v):
            # Braces: the folder is not a template (only the file name is), and a folder literally
            # named "{style}" is never what was meant.
            raise ValueError(f"out_subdir {v!r} must be a relative folder name (no '..', no leading '/' "
                             "or drive, no {fields}, at most 100 characters)")
        return v


class DetectedConcept(BaseModel):
    name: str
    present: bool
    score: float
    coverage: float
    instance_count: int = 0


class RegionStatsModel(BaseModel):
    name: str
    lum: float
    sat: float
    clip_low: float
    clip_high: float
    coverage: float
    r_shadow_clip: float = 0.0
    g_shadow_clip: float = 0.0
    b_shadow_clip: float = 0.0
    r_highlight_clip: float = 0.0
    g_highlight_clip: float = 0.0
    b_highlight_clip: float = 0.0
    r_highlight_recoverable: float = 0.0
    g_highlight_recoverable: float = 0.0
    b_highlight_recoverable: float = 0.0
    channel_shadow_clip: float = 0.0
    channel_highlight_clip: float = 0.0
    rgb_shadow_clip_any: float = 0.0
    rgb_shadow_clip_all: float = 0.0
    rgb_highlight_clip_any: float = 0.0
    rgb_highlight_clip_all: float = 0.0
    rgb_highlight_recoverable: float = 0.0


class GlobalStatsModel(BaseModel):
    mean_lum: float
    mean_sat: float
    shadow_clip: float
    highlight_clip: float
    haze: float
    sky_lum: float = 0.0
    sky_sat: float = 0.0
    blown_sky: float = 0.0
    subj_lum: float = 0.0
    subj_cov: float = 0.0
    bg_lum: float = 0.0
    # EXIF-derived priors (0.0 when the photo carries no usable metadata)
    iso: float = 0.0
    exposure_ev: float = 0.0
    # perceptual stats (default 0.0 so older sidecars/configs stay valid; rules can read them too):
    # luminance percentiles for tone targeting, and OKLab colourfulness/warmth.
    lum_p10: float = 0.0   # black-depth / crushed-shadow risk
    lum_p50: float = 0.0   # median brightness (use over mean for tone targets)
    lum_p90: float = 0.0   # highlight brightness / wash-out
    mean_chroma: float = 0.0  # mean OKLab chroma — how colourful the frame is
    warmth: float = 0.0       # mean OKLab b axis: + = warm/yellow, − = cool/blue
    # Explicit availability separates an older/hand-built zero-filled model from a newly computed
    # photo whose measured percentile/detail can legitimately be exactly zero (e.g. clipped black).
    pro_stats_valid: float = 0.0
    # Pro-tone signals (0.0 defaults preserve older sidecar validation; rules gate on
    # `pro_stats_valid`, never on the measured value itself):
    veil: float = 0.0         # TRUE atmospheric haze (bright+smooth+desat+not-white) — unlike
    #                           `haze` (dark-channel mean), snow/high-key scenes score ~0
    detail_rms: float = 0.0   # root-mean-square local luma detail — low = flat/soft frame
    dark_p10: float = 0.0     # dark-channel 10th percentile — black anchor
    fg_lum: float = 0.0       # sky-EXCLUDED mean luma (falls back to mean_lum without a sky mask)
    fg_p50: float = 0.0       # sky-EXCLUDED median luma — the pro exposure target
    snow_cov: float = 0.0     # region coverages for stats-conditional rules (iff(snow_cov>…))
    sky_cov: float = 0.0
    water_cov: float = 0.0
    # region-aware auto-WB (gray card = SAM snow region, else bright near-neutrals). Corrections
    # are in the white_balance op's units; rules apply a bounded partial correction scaled by
    # confidence (0 = no trustworthy reference → identity).
    wb_temp_correction: float = 0.0
    wb_tint_correction: float = 0.0
    wb_confidence: float = 0.0
    # Tone-report signals (dark-RAW diagnosis): highlight headroom, darkness coverage, and
    # spatial bands so a sidecar shows WHERE brightness moved, not just the global mean.
    lum_p99: float = 0.0
    headroom_ev: float = 0.0        # stops before p99.5 of per-pixel max(R,G,B) reaches white
    dark_cov: float = 0.0           # fraction of pixels with luma < 0.10 (dark, not clipped)
    band_top_p50: float = 0.0       # median luma of the top / middle / bottom thirds
    band_mid_p50: float = 0.0
    band_bottom_p50: float = 0.0
    # Histogram-style per-channel exposure stats. These catch color-channel clipping that luma
    # alone can miss, e.g. a red channel pinned in clouds while the overall luminance still looks OK.
    r_shadow_clip: float = 0.0
    g_shadow_clip: float = 0.0
    b_shadow_clip: float = 0.0
    r_highlight_clip: float = 0.0
    g_highlight_clip: float = 0.0
    b_highlight_clip: float = 0.0
    r_highlight_recoverable: float = 0.0
    g_highlight_recoverable: float = 0.0
    b_highlight_recoverable: float = 0.0
    channel_shadow_clip: float = 0.0
    channel_highlight_clip: float = 0.0
    rgb_shadow_clip_any: float = 0.0
    rgb_shadow_clip_all: float = 0.0
    rgb_highlight_clip_any: float = 0.0
    rgb_highlight_clip_all: float = 0.0
    rgb_highlight_recoverable: float = 0.0


class MaskOps(BaseModel):
    target: str  # region name, e.g. "sky" | "skin" | "subject"
    ops: Settings
    coverage: float = 0.0    # fraction of frame this (arbitrated) mask claims
    confidence: float = 0.0  # segmenter score for the concept


class Adjustment(BaseModel):
    """One directed change: a delta on a target, on top of the rule baseline (F2, F3)."""
    target: str                      # "global" or a concept name
    ops: Settings                    # op name -> delta
    source: str = ""                 # the instruction clause it came from; "" for a direct adjustment
    scope: Literal["run", "photo"] = "run"   # "run": from the run's --edit; "photo": from `autoedit adjust`
    status: Literal["", "applied", "clamped", "blocked", "not_found"] = ""   # filled in per photo


class Result(BaseModel):
    """The per-image sidecar (`<name>.autoedit.json`) — every decision, auditable."""

    image: str
    scene: str
    confidence: float
    style: str | None = None
    brief: str | None = None
    concepts: list[DetectedConcept] = Field(default_factory=list)
    region_stats: list[RegionStatsModel] = Field(default_factory=list)
    global_stats: GlobalStatsModel | None = None
    global_settings: Settings = Field(default_factory=dict)
    masks: list[MaskOps] = Field(default_factory=list)
    composition: list[MaskOps] = Field(default_factory=list)  # radial/linear moves
    notes: list[str] = Field(default_factory=list)
    adjustments: list[Adjustment] = Field(default_factory=list)
    # ── post-edit safety / explainability (measured on the EDITED output) ──
    post_edit_stats: GlobalStatsModel | None = None  # stats recomputed after apply (not source)
    post_region_stats: list[RegionStatsModel] = Field(default_factory=list)  # same masks, edited px
    decode_ev: float = 0.0          # RAW development lift applied at decode (0 for "none"/non-RAW)
    quality_score: float = 1.0      # naturalness 0..1 from the post-edit gate
    needs_review: bool = False      # output tripped a safety threshold → human should look
    review_reason: str = ""         # why it needs review (clipping/oversaturation/…)
    warnings: list[str] = Field(default_factory=list)  # structured safety flags
    batch_group: str = ""           # shoot-consistency group id (batch normalization)
    exposure_bias: float = 0.0      # shoot-consistency nudge this photo got (needed to re-render it alone)
    wb_bias: list[float] = Field(default_factory=lambda: [0.0, 0.0], min_length=2, max_length=2)  # burst white-balance nudge [temp, tint]
    decider: str = "rules"
    model: str | None = None
    engine_version: str = ""
    timing_ms: float = 0.0
    audit: dict = Field(default_factory=dict)
    stage_timings_ms: dict[str, float] = Field(default_factory=dict)
