"""Compose region edits + global finishing into the final image.

Regions arrive in priority-descending order (from segment/resolve). Even though the
arbitrated masks are non-overlapping, feathering softens edges, so we still composite with
`a_eff = alpha * (1 - claimed)` to guarantee no pixel is double-edited. Region edits run
first (masked), then global finishing over the whole frame, then a final clip.
"""

from __future__ import annotations

import numpy as np

from ..contract import Settings
from . import gates, ops, par
from .palette import palette_grade


def global_steps(out, global_settings, guard_mask=None, guard_ops=None):
    """The global pass of `apply_image`, one step of `ops.STEPS` at a time (F29). Yields (the ops of the step that
    have a value, the picture after it) for every step that renders something. The last picture is bit for bit what
    the one-pass code gives; with a guard every picture is the blend of the two renders so far."""
    guard = guard_mask is not None and bool(guard_ops)
    full = kept = out
    shown = False
    for step in ops.STEPS:
        part = {k: global_settings[k] for k in step if global_settings.get(k)}
        if not part:
            continue
        was_full, was_kept = full, kept
        full = ops.apply_ops(was_full, part)
        keep = {k: v for k, v in part.items() if k not in guard_ops} if guard else part
        # until a guarded op has a value the two renders are one array
        kept = full if (was_kept is was_full and keep == part) else (ops.apply_ops(was_kept, keep) if keep else was_kept)
        if full is was_full and kept is was_kept:
            continue                # a value apply_ops renders nothing for (lut, grain), or an op with nothing to do
        shown = True
        yield tuple(part), (_guard_blend(full, kept, guard_mask) if guard else full)
    if guard and not shown:         # the one-pass code blends the input with itself
        yield (), _guard_blend(out, out, guard_mask)


def _guard_blend(full, guarded, guard_mask):
    return par.rows(lambda f, g, m: f * (1.0 - m[..., None]) + g * m[..., None], full, guarded, guard_mask)


def apply_image(
    full_rgb: np.ndarray,
    regions: list[tuple[np.ndarray, Settings] | list[tuple[np.ndarray, Settings]]],
    global_settings: Settings,
    compositional: list[tuple[np.ndarray, Settings]] | None = None,
    guard_mask: np.ndarray | None = None,
    guard_ops: set[str] | None = None,
    chroma_max_increase: float | None = None,
    chroma_neutral_allowance: float = 0.02,
    protect_chroma: np.ndarray | None = None,
    palette: tuple | None = None,
    report: dict | None = None,
    on_stage=None,
    highlight_hold: tuple[float, float] | None = None,
    chroma_reference: np.ndarray | None = None,
    on_step=None,
) -> np.ndarray:
    """full_rgb: float HxWx3 gamma [0,1].
    regions: [(alpha_full, settings), ...] priority desc — arbitrated, claim-composited. An entry
    may be a list of such pairs: the cells of one region, blended in parallel (F2).
    compositional: [(alpha_full, settings), ...] — radial/linear moves blended independently
    (they intentionally overlap regions, e.g. a vignette over the background).
    guard_mask / guard_ops: skin-safe guard. Over `guard_mask` (float HxW [0,1]) the final
    GLOBAL pass skips the high-risk ops in `guard_ops` (e.g. clarity/saturation/grade) so faces
    keep global exposure/tone but not the aggressive look. Both None → byte-identical to before.
    chroma_max_increase: if set, a final oversaturation gate caps each pixel's chroma increase
    vs the source by this fraction (snow/skin in `protect_chroma` held tighter). None → off.
    report: optional dict the gate fills with {'chroma_limited': frac} for the sidecar.
    highlight_hold: (start, full) of the F6 highlight hold, or None (default) for no new code.
    chroma_reference: what the oversaturation gate compares against, when the caller has it
    already (the pipeline computes the denoised source once per photo). None: the source,
    denoised here when `denoise` is active.
    on_step: a read-only observer, `on_step(ops of a step, picture after it)` for every step of the global pass
    that renders something (`global_steps`, F29). None: no new code runs."""
    out = full_rgb.astype(np.float32).copy()
    claimed = np.zeros(out.shape[:2], dtype=np.float32)

    for entry in regions:
        if isinstance(entry, list):   # the cells of one region (F2): blended in parallel against
            free = 1.0 - claimed      # the same input, so the cut between them loses no edit
            total = np.zeros_like(claimed)
            mix = None
            for alpha, settings in entry:
                w = np.clip(np.minimum(alpha * free, free - total), 0.0, None)
                if float(w.max()) <= 1e-4:
                    continue
                # A cell without settings still claims its pixels, as an unchanged copy, so the
                # part of a layer outside the regions cannot spill into it and bypass its policy.
                part = (ops.apply_ops(out, settings) if settings else out) * w[..., None]
                mix = part if mix is None else mix + part
                total = total + w
            if mix is not None:
                out = out * (1.0 - total[..., None]) + mix
                claimed = np.clip(claimed + total, 0.0, 1.0)
            continue
        alpha, settings = entry
        if not settings:
            continue
        a_eff = alpha * (1.0 - claimed)
        if float(a_eff.max()) <= 1e-4:
            continue
        edited = ops.apply_ops(out, settings)
        out = par.rows(lambda o, e, a: o * (1.0 - a[..., None]) + e * a[..., None], out, edited, a_eff)
        claimed = np.clip(claimed + a_eff, 0.0, 1.0)

    if on_stage is not None:
        on_stage("after_regions", out)

    for alpha, settings in compositional or []:
        if not settings or float(alpha.max()) <= 1e-4:
            continue
        edited = ops.apply_ops(out, settings)
        out = par.rows(lambda o, e, a: o * (1.0 - a[..., None]) + e * a[..., None], out, edited, alpha)

    if on_stage is not None:
        on_stage("after_composition", out)

    if global_settings and on_step is not None:
        for keys, out in global_steps(out, global_settings, guard_mask, guard_ops):
            on_step(keys, out)
    elif global_settings:
        if guard_mask is not None and guard_ops:
            # Two renders by design: the kept ops include ones whose result depends on the whole
            # frame, so the guarded pass cannot run on a crop. Every step before the first guarded
            # op is the same in both renders, so it runs once (exact: a bundle cut between two
            # steps of ops.STEPS and applied in two calls is the same sequence of calls).
            cut = next((i for i, step in enumerate(ops.STEPS)
                        if any(k in guard_ops and global_settings.get(k) for k in step)),
                       len(ops.STEPS))
            first = {k for step in ops.STEPS[:cut] for k in step}
            pre = {k: v for k, v in global_settings.items() if k in first}
            post = {k: v for k, v in global_settings.items() if k not in first}
            base = ops.apply_ops(out, pre) if pre else out
            full = ops.apply_ops(base, post)
            kept = {k: v for k, v in post.items() if k not in guard_ops}
            guarded = ops.apply_ops(base, kept) if kept else base
            out = par.rows(lambda f, g, m: f * (1.0 - m[..., None]) + g * m[..., None],
                           full, guarded, guard_mask)  # protected pixels get global minus guarded ops
        else:
            out = ops.apply_ops(out, global_settings)

    if on_stage is not None:
        on_stage("after_global", out)

    # Regional palette grade (OKLab split-tone) as colour finishing — luminance-preserving, with the
    # skin-safe guard mask held neutral. Runs BEFORE the chroma gate so the gate still backstops it.
    if palette is not None:
        pal, pstrength = palette
        if pal is not None and pstrength > 0.0:
            out = palette_grade(out, pal, pstrength, protect=guard_mask)

    if on_stage is not None:
        on_stage("after_palette", out)

    out = np.clip(out, 0.0, 1.0)

    # Keep blown highlights white: a clipped source area has no detail for the pulls to recover.
    if highlight_hold is not None:
        out, held = gates.highlight_hold(full_rgb.astype(np.float32), out, *highlight_hold)
        if report is not None:
            report["highlight_held"] = held
            report["highlight_blown"] = float((full_rgb.min(axis=-1) >= highlight_hold[1]).mean())

    # Final safety pass: never let the edit be more saturated than the source allows.
    if chroma_max_increase is not None:
        reference = chroma_reference if chroma_reference is not None else full_rgb.astype(np.float32)
        if chroma_reference is None and global_settings.get("denoise", 0.0):
            # The gate compares colourfulness pixel by pixel. Against a noisy source that stamps
            # the source's colour noise back onto the denoised result (measured at ISO 12800:
            # 7 levels of colour grain with the raw source as reference, 0.4 with this). So the
            # reference gets the same noise reduction as the edit.
            reference = ops.denoise(reference, global_settings["denoise"], global_settings.get("denoise_stars", 0.0))
        out, limited = gates.chroma_guard(
            reference, out,
            max_increase=chroma_max_increase,
            neutral_allowance=chroma_neutral_allowance,
            protect=protect_chroma,
        )
        if report is not None:
            report["chroma_limited"] = limited

    if on_stage is not None:
        on_stage("after_chroma_guard", out)
    return out
