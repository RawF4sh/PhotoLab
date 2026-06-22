"""The data contract — run config + serializable Result (sidecar JSON).

Ported from the Rust scaffold's engine/src/io.rs. Two kinds of data live here:
- Pydantic models for everything that gets serialized (RunConfig, Result, sidecar).
- Plain dataclasses (in the modules that own them) for runtime objects holding numpy
  arrays (masks, image buffers) — those never get serialized directly.

`Settings` is a plain dict of edit-op name -> value, e.g. {"exposure": 0.35, "vibrance": 6}.
Op names and units are defined by the apply engine (edit/ops.py), NOT Adobe CRS names.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .paths import config_path

# A bundle of edit-op values. Keys are op names from edit/ops.py OP_NAMES.
Settings = dict[str, float]


class RunConfig(BaseModel):
    """Everything a batch run needs. Built from configs/defaults.toml + a --config file
    + CLI overrides (see cli.py::load_config)."""

    # selection / look
    style: str = "natural"  # styles.toml profile: look + default concepts + brief
    prompt: str | None = None  # free-text intent, combined with the style brief for the LLM
    preset: str | None = None  # concept-set override (concepts.toml); wins over the style's set
    concepts: list[str] | None = None  # explicit concept override; wins over preset
    look: str | None = None  # magnitude profile override; defaults to the style's look
    intensity: float = 0.8

    # reference-style color match (optional): grade the result toward an example image's look
    reference: str | None = None  # path to a reference photo whose palette/tone to match
    reference_strength: float = 0.7  # 0 = off, 1 = full match

    # decision
    decider: str = "rules"  # "rules" | "llm"
    allow_cloud: bool = False  # explicit consent required to use the cloud LLM (BYO-key)
    api_key: str | None = None  # Anthropic API key; falls back to ANTHROPIC_API_KEY env
    llm_mode: str = "refine"  # "refine" | "propose"
    llm_model: str = "claude-haiku-4-5-20251001"
    llm_temperature: float = 0.0
    llm_max_calls: int = 0  # 0 = unlimited
    llm_budget_usd: float = 0.0
    llm_thumb_px: int = 512

    # segmentation
    backend: str = "stub"  # "stub" | "sam3"
    device: str = "auto"
    analysis_long_edge: int = 1536

    # output
    out_format: str = "jpeg"  # "jpeg" | "tiff"
    quality: int = 92
    emit: str = "baked"  # "baked" | "editable" | "both"
    compose: bool = True  # apply radial/linear compositional moves (vignette, sky grad)
    vignette: bool = True  # frame/subject vignette (subset of compose); off → no edge falloff
    sharpen: bool = True  # output sharpening (unsharp mask); off → no sharpen ops applied

    # paths to config assets — resolved to ./configs or the packaged configs (see paths.py)
    rules_path: str = Field(default_factory=lambda: config_path("rules.toml"))
    looks_path: str = Field(default_factory=lambda: config_path("looks.toml"))
    concepts_path: str = Field(default_factory=lambda: config_path("concepts.toml"))
    styles_path: str = Field(default_factory=lambda: config_path("styles.toml"))
    compose_path: str = Field(default_factory=lambda: config_path("compose.toml"))


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
    bg_lum: float = 0.0
    # EXIF-derived priors (0.0 when the photo carries no usable metadata)
    iso: float = 0.0
    exposure_ev: float = 0.0


class MaskOps(BaseModel):
    target: str  # region name, e.g. "sky" | "skin" | "subject"
    ops: Settings
    coverage: float = 0.0    # fraction of frame this (arbitrated) mask claims
    confidence: float = 0.0  # segmenter score for the concept


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
    # ── post-edit safety / explainability (measured on the EDITED output) ──
    post_edit_stats: GlobalStatsModel | None = None  # stats recomputed after apply (not source)
    quality_score: float = 1.0      # naturalness 0..1 from the post-edit gate
    needs_review: bool = False      # output tripped a safety threshold → human should look
    review_reason: str = ""         # why it needs review (clipping/oversaturation/…)
    warnings: list[str] = Field(default_factory=list)  # structured safety flags
    batch_group: str = ""           # shoot-consistency group id (batch normalization)
    decider: str = "rules"
    model: str | None = None
    engine_version: str = ""
    timing_ms: float = 0.0
