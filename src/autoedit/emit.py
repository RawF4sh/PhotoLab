"""Assemble the Result and write outputs (baked image + sidecar JSON)."""

from __future__ import annotations

import hashlib
import os

import numpy as np

from . import __version__, geometry as geometry_mod, history
from .contract import DetectedConcept, GlobalStatsModel, MaskOps, RegionStatsModel, Result, Settings
from .edit import ops
from .imageio import export_exif, resize_long_edge, save_image, srgb_profile


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


def grain_args(result: Result, cfg) -> tuple[float, float, int] | None:
    """(amount, size, seed) for `ops.grain` on a picture of this result, or None for no grain.
    The seed is a digest of the photo's file name: the same photo gets the same pattern in every
    run and in every folder."""
    amount = float(result.global_settings.get("grain", 0.0))
    if amount <= 0.0:
        return None
    seed = int.from_bytes(hashlib.blake2b(result.image.encode("utf-8"), digest_size=4).digest(), "big")
    return amount, float(cfg.grain_size), seed


def write_image(rgb: np.ndarray, path: str, fmt: str = "jpeg", quality: int = 92,
                source: str | None = None, metadata: str = "none", long_edge: int | None = None,
                grain: tuple[float, float, int] | None = None, geometry=None) -> str:
    """Write the baked image and return a one-line note for the sidecar ("" when there is
    nothing to say).

    With `source` (the photo this export was made from) the file is a product export: it gets
    the sRGB profile and, as `metadata` says, the source's EXIF. Without `source` the bytes are
    what they were before F1; measurement and contact-sheet exports rely on that.

    `long_edge` shrinks the export first (never enlarges); the EXIF pixel size is the size written.
    `grain` (see `grain_args`) is laid over the picture after that resize, directly before the encoder.
    `geometry` (the result's `Geometry`, F32) turns and cuts the finished picture first, before the resize."""
    if geometry is not None:
        rgb = geometry_mod.warp(rgb, geometry)
    if long_edge:
        rgb = resize_long_edge(rgb, long_edge)
    if grain:
        rgb = ops.grain(rgb, *grain)
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


def write_outputs(out_rgb, result: Result, image_out: str, sidecar: str, cfg, source: str) -> None:
    """Write the baked image (when there are pixels) and the sidecar that explains it."""
    if out_rgb is not None:
        grain = grain_args(result, cfg)
        note = write_image(out_rgb, image_out, cfg.out_format, cfg.quality,
                           source=source, metadata=cfg.metadata, long_edge=cfg.out_long_edge, grain=grain,
                           geometry=result.geometry)
        if note:
            result.notes.append(note)
        if grain:
            result.notes.append(f"grain: {grain[0]:.3g} of 100, size {grain[1]:g}, "
                                "added to the export after the checks and the resize")
        result.audit["output"] = os.path.basename(image_out)
    write_sidecar(result, sidecar)
    if out_rgb is not None:
        history.record(result, cfg)


_EXT = {"tiff": "tif", "webp": "webp", "png": "png"}


def source_ids(paths) -> set[tuple[int, int]]:
    """(device, inode) of each readable input of a run. Names are not enough to tell two paths
    apart: on a case-insensitive volume IMG_1.jpg and IMG_1.JPG are one file."""
    ids = set()
    for p in paths:
        try:
            st = os.stat(p)
        except OSError:
            continue                     # a photo that cannot be read fails on its own
        ids.add((st.st_dev, st.st_ino))
    return ids


def output_paths(out_dir: str, image_name: str, fmt: str,
                 taken: set[str] | None = None, name: str = "{stem}", subdir: str = "",
                 fields: dict | None = None,
                 sources: set[tuple[int, int]] | None = None) -> tuple[str, str]:
    """Map a source path to its (image_out, sidecar) targets under out_dir.

    `name` is the file-name template ({stem}, plus the `fields`: style, n) and `subdir` a folder
    under out_dir (created); the sidecar sits next to its image. Defaults give the plain
    `<stem>.jpg` in out_dir.

    Two sources sharing a stem (a camera's IMG_001.dng + IMG_001.jpg, or same-named files from
    different folders) would otherwise write the SAME output and silently overwrite each other
    (image + explanation sidecar). Pass a persistent `taken` set across a batch: on collision the
    stem gets a -2/-3 suffix. Deterministic given a stable iteration order, so resume reproduces
    the same assignment. `taken=None` keeps the legacy single-source behavior unchanged.

    `sources` (see `source_ids`) are all the inputs of the run: an export that would land on any of
    them raises, not only on its own source. Without it only the own source is checked."""
    src_stem = os.path.splitext(os.path.basename(image_name))[0]
    stem = name.format(stem=src_stem, **(fields or {"style": "", "n": 0}))
    # The template is checked when the config is built, but {style} is filled in here from a value
    # nobody checked ("../../x"), so the filled name must be a plain file name. A leading dot is
    # fine only when it is the source's own (a hidden photo keeps the name it always had).
    if (not stem or stem in (".", "..") or any(c in stem for c in "/\\\0") or os.path.splitdrive(stem)[0]
            or (stem.startswith(".") and not (src_stem.startswith(".") and stem.startswith(src_stem)))):
        raise ValueError(f"export file name {stem!r} (from the template {name!r}) is not a plain file name")
    ext = _EXT.get(fmt, "jpg")
    folder = os.path.join(out_dir, subdir) if subdir else out_dir
    image_out = os.path.join(folder, f"{stem}.{ext}")
    if subdir:
        # The web layer approves the output folder only; a symlink inside it must not lead out.
        real_out = os.path.realpath(out_dir)
        real_folder = os.path.realpath(folder)
        if real_folder != real_out and not real_folder.startswith(real_out + os.sep):
            raise ValueError(f"{folder}: the sub-folder {subdir!r} leads outside the output folder")
    if taken is not None:
        n = 2
        while image_out in taken:
            image_out = os.path.join(folder, f"{stem}-{n}.{ext}")
            n += 1
        taken.add(image_out)
    if os.path.exists(image_out):
        st = os.stat(image_out)
        if (st.st_dev, st.st_ino) in (sources or ()) or (
                os.path.exists(image_name) and os.path.samefile(image_out, image_name)):
            raise ValueError(f"{image_out}: this export would overwrite a source photo; "
                             "choose another output folder, --subdir or --name")
    os.makedirs(folder, exist_ok=True)
    out_stem = os.path.splitext(os.path.basename(image_out))[0]
    return image_out, os.path.join(folder, f"{out_stem}.autoedit.json")
