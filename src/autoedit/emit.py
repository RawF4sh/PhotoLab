"""Assemble the Result and write outputs (baked image + sidecar JSON)."""

from __future__ import annotations

import os

import numpy as np

from . import __version__
from .contract import DetectedConcept, GlobalStatsModel, MaskOps, RegionStatsModel, Result, Settings
from .imageio import save_image


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
    notes: list[str],
    decider: str,
    model: str | None,
    timing_ms: float,
) -> Result:
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
        masks=[MaskOps(target=name, ops=ops) for name, ops in region_settings.items()],
        notes=notes,
        decider=decider,
        model=model,
        engine_version=__version__,
        timing_ms=timing_ms,
    )


def write_sidecar(result: Result, path: str) -> None:
    with open(path, "w") as fh:
        fh.write(result.model_dump_json(indent=2))


def write_image(rgb: np.ndarray, path: str, fmt: str = "jpeg", quality: int = 92) -> None:
    save_image(rgb, path, fmt=fmt, quality=quality)


def output_paths(out_dir: str, image_name: str, fmt: str) -> tuple[str, str]:
    stem = os.path.splitext(os.path.basename(image_name))[0]
    ext = "tif" if fmt == "tiff" else "jpg"
    return (
        os.path.join(out_dir, f"{stem}.{ext}"),
        os.path.join(out_dir, f"{stem}.autoedit.json"),
    )
