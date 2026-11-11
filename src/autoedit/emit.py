"""Assemble the Result and write outputs (baked image + sidecar JSON)."""

from __future__ import annotations

import os

import numpy as np

from . import __version__
from .contract import DetectedConcept, GlobalStatsModel, MaskOps, RegionStatsModel, Result, Settings
from .imageio import export_exif, save_image, srgb_profile


def build_result(
    image_name: str,
    scene: str,
    confidence: float,
    style: str | None,
    brief: str | None,
    detected: list[DetectedConcept],
    gstats: GlobalStatsModel,
    region_stats: dict[str, RegionStatsModel],
    global_settings: Settings,
    region_settings: dict[str, Settings],
    compositional: dict[str, Settings],
    notes: list[str],
    decider: str,
    model: str | None,
    timing_ms: float,
    region_meta: dict[str, tuple[float, float]] | None = None,
    post_edit_stats: GlobalStatsModel | None = None,
    quality_score: float = 1.0,
    needs_review: bool = False,
    review_reason: str = "",
    warnings: list[str] | None = None,
    batch_group: str = "",
    post_region_stats: list[RegionStatsModel] | None = None,
    decode_ev: float = 0.0,
) -> Result:
    rm = region_meta or {}
    return Result(
        image=image_name,
        scene=scene,
        confidence=confidence,
        style=style,
        brief=brief,
        concepts=detected,
        region_stats=list(region_stats.values()),
        global_stats=gstats,
        global_settings=global_settings,
        masks=[MaskOps(target=name, ops=ops,
                       coverage=rm.get(name, (0.0, 0.0))[0], confidence=rm.get(name, (0.0, 0.0))[1])
               for name, ops in region_settings.items()],
        composition=[MaskOps(target=name, ops=ops) for name, ops in compositional.items()],
        notes=notes,
        post_edit_stats=post_edit_stats,
        post_region_stats=list(post_region_stats or []),
        decode_ev=float(decode_ev),
        quality_score=quality_score,
        needs_review=needs_review,
        review_reason=review_reason,
        warnings=warnings or [],
        batch_group=batch_group,
        decider=decider,
        model=model,
        engine_version=__version__,
        timing_ms=timing_ms,
    )


def write_sidecar(result: Result, path: str) -> None:
    tmp = path + ".part"                       # atomic: full JSON or nothing (never truncated)
    with open(tmp, "w") as fh:
        fh.write(result.model_dump_json(indent=2))
    os.replace(tmp, path)


def write_image(rgb: np.ndarray, path: str, fmt: str = "jpeg", quality: int = 92,
                source: str | None = None, metadata: str = "none") -> str:
    """Write the baked image and return a one-line note for the sidecar ("" when there is
    nothing to say).

    With `source` (the photo this export was made from) the file is a product export: it gets
    the sRGB profile and, as `metadata` says, the source's EXIF. Without `source` the bytes are
    what they were before F1; measurement and contact-sheet exports rely on that."""
    if source is None:
        save_image(rgb, path, fmt=fmt, quality=quality)
        return ""
    exif = export_exif(source, metadata, (rgb.shape[1], rgb.shape[0]),
                       software=f"AutoEdit {__version__}")
    save_image(rgb, path, fmt=fmt, quality=quality, exif=exif, icc_profile=srgb_profile())
    if metadata == "none":
        return ""
    if exif:
        limit = "; a 16-bit TIFF holds the basic tags only" if fmt == "tiff" else ""
        return f"export: EXIF kept{limit} (metadata = {metadata})"
    return (f"export: no EXIF written, the source has none or it could not be read "
            f"(metadata = {metadata})")


def output_paths(out_dir: str, image_name: str, fmt: str,
                 taken: set[str] | None = None) -> tuple[str, str]:
    """Map a source path to its (image_out, sidecar) targets under out_dir.

    Two sources sharing a stem (a camera's IMG_001.dng + IMG_001.jpg, or same-named files from
    different folders) would otherwise write the SAME output and silently overwrite each other
    (image + explanation sidecar). Pass a persistent `taken` set across a batch: on collision the
    stem gets a -2/-3 suffix. Deterministic given a stable iteration order, so resume reproduces
    the same assignment. `taken=None` keeps the legacy single-source behavior unchanged."""
    stem = os.path.splitext(os.path.basename(image_name))[0]
    ext = "tif" if fmt == "tiff" else "jpg"
    image_out = os.path.join(out_dir, f"{stem}.{ext}")
    if taken is not None:
        n = 2
        while image_out in taken:
            image_out = os.path.join(out_dir, f"{stem}-{n}.{ext}")
            n += 1
        taken.add(image_out)
    out_stem = os.path.splitext(os.path.basename(image_out))[0]
    return image_out, os.path.join(out_dir, f"{out_stem}.autoedit.json")
