"""Reviewable Gemini suggestions. Applying a proposal remains a separate local UI action."""

from __future__ import annotations

import base64
import math
import os
import re
import uuid

from ..segment.refine import name as region_name

from ..decide import gemini

_CLIENT = gemini.Gemini()       # one bounded ledger per app process; never stores the key
_NAME = re.compile(r"[^\W\d_][\w .'-]{0,79}\Z", re.UNICODE)
_GLOBAL_METRICS = ("mean_lum", "mean_sat", "shadow_clip", "highlight_clip", "lum_p10", "lum_p50",
                   "lum_p90", "mean_chroma", "warmth", "detail_rms", "blown_sky")
_REGION_METRICS = ("lum", "sat", "clip_low", "clip_high", "coverage")
_PRIVATE_KEYS = {"gemini_api_key", "api_key", "assist_prompt", "preview_session", "preview_revision", "steps", "try"}


def _numbers(value, keys):
    if value is None:
        return {}
    values = value if isinstance(value, dict) else value.model_dump()
    return {key: round(float(values[key]), 6) for key in keys
            if type(values.get(key)) in (int, float) and math.isfinite(values[key]) and abs(values[key]) <= 1e9}


def _suggestible_region(name):
    if not isinstance(name, str) or not _NAME.fullmatch(name) or name == "photo":
        return False
    try:
        region_name(name)
    except ValueError:
        return False
    return True


def _context(render, opts, bounds):
    result = render["result"]
    # Only masks which actually claimed pixels can receive a proposed region adjustment. Marks
    # and generated instruction names are excluded: they aren't named object controls.
    masks = sorted((m for m in render.get("source_overlays", [])
                    if m.get("coverage", 0) > 0 and _suggestible_region(m.get("name"))),
                   key=lambda m: (-m["coverage"], m["name"]))[:gemini.MAX_REGIONS]
    names = list(dict.fromkeys(m["name"] for m in masks))
    stats = {r.name: r for r in (result.post_region_stats or result.region_stats)}
    rendered = {m.target: m.ops for m in result.masks}
    own = opts.get("region_settings") or {}
    region_context = {}
    for mask in masks:
        name = mask["name"]
        region_context[name] = {"coverage": float(mask["coverage"]),
                                "measurements": _numbers(stats.get(name), _REGION_METRICS),
                                "current_controls": _numbers(own.get(name, {}), bounds),
                                "rendered_settings": _numbers(rendered.get(name, {}), bounds)}
    return names, {"measurements": _numbers(result.post_edit_stats or result.global_stats, _GLOBAL_METRICS),
                   "current_controls": _numbers(opts.get("photo_settings") or {}, bounds),
                   "rendered_settings": _numbers(result.global_settings, bounds),
                   "regions": region_context}


def suggest(image_path, opts, configs_dir=None, profile=None):
    """Called only by POST /assist with explicit consent and a bounded assist_prompt.

    The provider receives a re-encoded 512px JPEG, the user's request, actual object labels and
    whitelisted numeric measurements/controls. No full result/config, EXIF, source or reference
    path, sidecar note, privacy finding, key or marks are put into the prompt.
    """
    from . import service                 # lazy: service does not import the provider

    if not isinstance(opts, dict):
        raise ValueError("assistant options must be an object")
    prompt = opts.get("assist_prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > gemini.MAX_PROMPT:
        raise ValueError("assist_prompt must contain 1 to 1200 characters")
    try:
        if opts.get("allow_cloud") is not True:
            raise gemini.Unavailable("consent", "Allow this request to send a small preview to Gemini first. Your local edit is unchanged.")
        key = opts.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY", "")
        if not isinstance(key, str) or not key:
            raise gemini.Unavailable("missing_key", "Add a Gemini API key for this session, or set GEMINI_API_KEY on this computer.")
        if len(key) > 512 or any(not 33 <= ord(c) <= 126 for c in key):
            raise gemini.Unavailable("missing_key", "The Gemini API key has an invalid format.")
        service.ensure_allowed(image_path, opts.get("reference") or None, opts.get("lut") or None,
                               opts.get("recipe_model") or None)
        service.check_image(image_path)
        bounds, _ = service.editor_sliders()
        from . import edit_steps
        opts, _ = edit_steps.fold(opts, bounds)
        service.check_photo_settings(opts.get("photo_settings"))
        local = {k: v for k, v in opts.items() if k not in _PRIVATE_KEYS}
        local.update(decider="rules", allow_cloud=False, translator="off", preview_edge=640)
        try:
            render = service._edit(image_path, local, configs_dir, profile)
            names, context = _context(render, local, bounds)
            # _img_b64 applies the same output geometry the preview shows. A fresh JPEG is
            # re-encoded in the provider again to enforce size and remove every metadata field.
            preview = service._img_b64(render["out"], gemini.MAX_THUMB_PX, fmt="jpeg", quality=85,
                                       geo=render["result"].geometry)
            thumbnail = base64.b64decode(preview.split(",", 1)[1], validate=True)
        except Exception:
            raise gemini.Unavailable("local_preview", "A local preview could not be prepared. Your edits are unchanged; try the photo again.") from None
        result = _CLIENT.suggest(thumbnail=thumbnail, prompt=prompt, context=context, bounds=bounds,
                                 names=names, api_key=key, allow_cloud=True)
        return {**result, "id": uuid.uuid4().hex}
    except gemini.Unavailable as error:
        return {"status": "unavailable", "reason": error.reason, "message": str(error),
                "proposal": None, "model": gemini.MODEL, "budget": _CLIENT.budget.status()}
