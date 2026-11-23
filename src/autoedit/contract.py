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
    # The genre pack this config was built from (genres.toml, F30). Like `export` it is applied where the
    # config is built (cli.py, web/service.py): the name alone changes nothing.
    genre: str | None = Field(default=None, max_length=40)
    rule_sets: list[str] = Field(default_factory=list, max_length=20)  # opt-in rule sets of rules.toml a genre switched on
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
    # F20: a small model on this machine (Ollama) reads instruction clauses the word list did not
    # understand. "auto": used when Ollama answers with the model installed, else nothing happens.
    translator: Literal["auto", "local", "off"] = "auto"
    local_model: str = Field(default="qwen3.5:9b", max_length=100)
    local_url: str = Field(default="http://127.0.0.1:11434", max_length=200)
    local_timeout_s: float = Field(default=60.0, gt=0.0, le=600.0)
    llm_thumb_px: int = Field(default=512, ge=64, le=1024)

    # RAW development (imageio._decode_raw). "none" is today's libraw render and stays the
    # default; the other modes are benchmark candidates (scripts/raw_benchmark.py) — a benchmark
    # may recommend one, it never flips this default by itself.
    raw_mode: Literal["none", "libraw_auto", "preview_ev", "preview_ev_headroom", "tone_target", "linear_srgb"] = "none"
    raw_max_ev: float = Field(default=2.5, ge=0.0, le=5.0)          # never lift more than this
    raw_clip_budget: float = Field(default=0.005, ge=0.0, le=0.1)   # fraction allowed to reach white
    raw_tone_target_p50: float = Field(default=0.40, gt=0.0, lt=1.0)  # tone_target: median (gamma)
    raw_fallback_ev: float = Field(default=0.0, ge=0.0, le=5.0)     # no universal lift without a preview
    lens_ca: bool = False  # scale red and blue onto green about the centre (F46); off → decoded as it is
    lens_vignette: bool = False  # brighten RAW corners from a lens profile (F52); off → decoded as it is
    fast_analysis: bool = False  # decide on the half-size RAW development, apply at full size (F51); off → one full decode
    raw_recover: bool = False  # rebuild highlights the default decode clips, from LibRaw's blend decode (F44); off → decoded as before

    # segmentation
    backend: Literal["stub", "sam3", "auto"] = "stub"
    device: str = "auto"
    analysis_long_edge: int = Field(default=1536, ge=64, le=8192)
    mask_cache: bool = True  # keep raw masks under <out>/.autoedit-masks (not for the stub) so re-renders skip segmentation
    workers: int = Field(default=1, ge=0, le=64)  # render processes for a batch; 0 = half the cores, at most 4
    threads: int = Field(default=0, ge=0, le=256)  # threads for per-pixel work inside one render; 0 = one per core

    consistency: Literal["brightness", "bursts", "timelapse"] = "brightness"  # shoot consistency (F9); "bursts" evens out frames of one framing

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
    highlight_detail: bool = False  # the highlight knee keeps faint cloud and snow texture (F50); off → the knee as before
    levels: bool = False  # black and white point (F5); off → the rule-driven black_point is dropped after decide
    light_shaping: bool = False  # a soft warm glow where the sun is (F18); off → the sun glow move does not exist
    snap_edges: bool = False  # region masks that stop just short of the frame edge are extended to it (F39); off → masks as segmented
    denoise: bool = False  # noise reduction (F7); off → the rule set is not loaded and the op is dropped after decide
    night_sky: bool = False  # night sky tools (F31): night frames take the night rules; off → the rule set is not loaded and the ops are dropped after decide
    grain: float | None = Field(default=None, ge=0.0, le=100.0)  # film grain on the written picture (F23); None = the look's own, 0 = none
    grain_size: float = Field(default=1.0, ge=0.5, le=4.0)  # one grain clump in two-thousandths of the export's long edge
    lut: str | None = None  # path of a .cube 3D LUT laid over the finished photo (F25); None or "" = none
    lut_amount: float = Field(default=100.0, ge=0.0, le=100.0)  # how much of the LUT is blended in; not scaled by intensity
    neutral: list[float] | None = Field(default=None, min_length=2, max_length=2)  # a spot, X,Y as shares of the frame, that is grey or white: the white balance makes it neutral (F34)
    wb: list[float] | None = Field(default=None, min_length=2, max_length=2)  # [temp, tint] given for the whole run, e.g. measured once by --neutral-from (F34)
    backdrop: bool = False  # even out a light backdrop and take it to a set white, on the finished picture (F34)
    consistency_wb: bool = False  # a group of photos gets one white balance, the median cast of its frames (F34)
    colour_report: bool = False  # measure the subject colour of every photo and report the spread per group (F34)
    straighten: bool = False  # turn the export so a clear waterline or horizon is level (F32); off → no turn
    out_aspect: str | None = Field(default=None, max_length=7)  # crop the export to this W:H shape (F32); None or "" = the photo's own
    # Saved visual edits (F55), hidden from the general settings form. Offsets themselves are
    # photo Adjustments; these names/masks/framing make replay use the same regions and frame.
    selected_regions: list[str] = Field(default_factory=list)
    mask_refinements: dict[str, list[dict]] = Field(default_factory=dict)
    photo_geometry: dict | None = None
    # A watermark (F48): set only by an export preset (exports.toml); drawn on the export alone,
    # after the resize and the grain.
    watermark: str | None = Field(default=None, max_length=100)  # one line of text laid over a corner; None = no text
    watermark_logo: str | None = Field(default=None, max_length=100)  # file name of a PNG in the profile folder (else the configs folder); never a folder
    watermark_corner: Literal["bottom_right", "bottom_left", "top_right", "top_left"] = "bottom_right"
    watermark_size: float = Field(default=3.0, ge=1.0, le=20.0)  # height of the mark in percent of the export's short side
    watermark_opacity: float = Field(default=0.7, ge=0.1, le=1.0)
    reveal_check: bool = False  # report which photos give something away: plates, screens, people, file metadata (F21); never changes a pixel
    hide: list[str] = Field(default_factory=list)  # reveal kinds to hide in the export, on request only (F22); a kind named here also turns the check on
    hide_method: Literal["cover", "mosaic", "blur"] | None = None  # how a hidden region looks (F22); None = reveal.toml's [hide] method
    history: bool = False  # one line per exported photo in the profile (F27)
    profile_dir: str | None = None  # the profile folder this config was built with; set by the code

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
    reveal_path: str = Field(default_factory=lambda: config_path("reveal.toml", packaged_fallback=True))

    @field_validator("hide", mode="before")
    @classmethod
    def _check_hide(cls, v):
        if v is None:
            return []
        if isinstance(v, str):    # the settings sheet and the command line send "plate, face"
            v = [k for k in re.split(r"[,\s]+", v) if k]
        if not isinstance(v, (list, tuple)):
            raise ValueError("hide is a list of reveal kinds, e.g. plate, face")
        out = []
        for k in v:
            if not (isinstance(k, str) and re.fullmatch(r"[a-z0-9_-]{1,40}", k)):
                raise ValueError(f"hide: {k!r} is not a reveal kind name (lower-case letters, digits, - and _)")
            if k not in out:
                out.append(k)
        if len(out) > 40:
            raise ValueError("hide names more than 40 kinds")
        return out

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

    @field_validator("neutral")
    @classmethod
    def _check_neutral(cls, v: list[float] | None) -> list[float] | None:
        if v is not None and not all(0.0 <= n <= 1.0 for n in v):      # the comparison also refuses NaN
            raise ValueError(f"neutral {v!r} must be X,Y as shares of the frame, each from 0 to 1")
        return v

    @field_validator("wb")
    @classmethod
    def _check_wb(cls, v: list[float] | None) -> list[float] | None:
        if v is not None and not all(-100.0 <= n <= 100.0 for n in v):  # NaN and infinity fail it too
            raise ValueError(f"wb {v!r} must be [temp, tint], each from -100 to 100")
        return v

    @field_validator("out_aspect")
    @classmethod
    def _check_out_aspect(cls, v: str | None) -> str | None:
        if not v:
            return None
        if not re.fullmatch(r"[1-9][0-9]{0,2}:[1-9][0-9]{0,2}", v, re.ASCII):
            raise ValueError(f"out_aspect {v!r} must be W:H in whole numbers, for example 4:5")
        a, b = (int(n) for n in v.split(":"))
        if not 0.2 <= a / b <= 5:
            raise ValueError(f"out_aspect {v!r} is outside 1:5 to 5:1")
        return v

    @field_validator("selected_regions", mode="before")
    @classmethod
    def _check_selected_regions(cls, value):
        from .segment.refine import names
        return names(value)

    @field_validator("mask_refinements", mode="before")
    @classmethod
    def _check_mask_refinements(cls, value):
        from .segment.refine import check
        return check(value)

    @field_validator("photo_geometry", mode="before")
    @classmethod
    def _check_photo_geometry(cls, value):
        from .geometry import check_manual
        return check_manual(value)

    @field_validator("watermark")
    @classmethod
    def _check_watermark(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        if any(ord(c) < 32 or ord(c) == 127 for c in v):
            raise ValueError("watermark must be one line of text, without control characters")
        return v

    @field_validator("watermark_logo")
    @classmethod
    def _check_watermark_logo(cls, v: str | None) -> str | None:
        if not v:
            return None
        if v.startswith(".") or not re.fullmatch(r"[^/\\:]{1,96}\.png", v, re.IGNORECASE):
            raise ValueError(f"watermark_logo {v!r} must be the file name of a PNG in your profile folder, "
                             "without a folder (for example logo.png)")
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
    recovered_highlight: float = 0.0  # F44: share of the frame that was clipped before RAW recovery rebuilt it
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
    scope: Literal["run", "photo", "look"] = "run"   # "run": from the run's --edit; "photo": from `autoedit adjust`; "look": the look's own habit (F27)
    resolved_only: bool = Field(default=False, strict=True)  # visual object control: only its arbitrated, displayed region
    status: Literal["", "applied", "clamped", "blocked", "not_found"] = ""   # filled in per photo
    size: float = Field(default=1.0, ge=0.25, le=4.0)  # F20: scales a place's extent ("a bigger gradient"); 1 = as in instructions.toml
    via: Literal["", "local", "cloud"] = ""   # F20: "" = the word list understood it; else which model translated it

    @field_validator("resolved_only")
    @classmethod
    def _check_resolved_only(cls, value, info):
        if value:
            from .segment.refine import name
            name(info.data.get("target"))
            if info.data.get("scope") != "photo":
                raise ValueError("resolved_only is a photo-scope visual region adjustment")
        return value


class RevealFinding(BaseModel):
    kind: str
    label: str = ""                                         # what the kind is called to a person; "" = use the kind name
    word: str
    score: float
    box: list[float] = Field(min_length=4, max_length=4)   # x0, y0, x1, y1 as shares of the frame
    share: float                                            # of the frame's area
    subject: bool = False
    weak: bool = False                                      # the kind is marked "may miss"


class HiddenRegion(BaseModel):
    """One region hidden in the export because the user asked for its kind (F22)."""
    kind: str
    label: str = ""
    box: list[float] = Field(min_length=4, max_length=4)   # x0, y0, x1, y1 of the hidden area (margin included), shares of the frame
    method: Literal["cover", "mosaic", "blur"]
    trace: bool                                             # mosaic and blur keep a trace of what was there; cover copies nothing from under the region


class RevealReport(BaseModel):
    picture: Literal["checked", "not_run"]
    reason: str = ""                  # for not_run: why
    looked_for: list[str] = []        # kind names, so "nothing found" always says of what
    findings: list[RevealFinding] = []
    more: int = 0                     # findings left out by max_findings
    file: dict[str, dict[str, bool]] = {}
    hidden: list[HiddenRegion] = []   # regions hidden in the export on request (F22); [] = nothing was hidden
    hide_note: str = ""               # F22: why a hide request hid nothing ("" when it did, or none was made)


class Geometry(BaseModel):
    """How the finished picture is turned and cut on its way out (F32), in shares of the source photo."""

    angle: float = 0.0       # degrees the levelled line sloped; positive: it fell to the right
    line: str = ""           # "waterline" or "horizon"; "" = not turned
    confidence: float = 0.0  # share of the line's points that lie on one straight line
    aspect: str = ""         # the shape asked for; "" = the photo's own
    cx: float = 0.5          # middle of the kept frame
    cy: float = 0.5
    w: float = 1.0           # width and height of the kept frame
    h: float = 1.0
    kept: float = 1.0        # share of the photo's area that is kept


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
    reveal: RevealReport | None = None  # what the reveal check found (F21); None = the check was off
    geometry: Geometry | None = None  # straighten and crop, done to the export (F32); None = the photo as it is
