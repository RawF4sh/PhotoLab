"""Run the local editor on every input and inspect the written pixels.

  .venv/bin/python -I scripts/opens_report.py curated-web/opens out-webloop/opens
  .venv/bin/python -I scripts/opens_report.py curated-web/opens out-webloop/opens --resume

One image failure is a report row. An unsupported file is counted as a failure.
RAW orientation is measured against the embedded camera preview when one exists.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time

import numpy as np
from PIL import Image as PILImage

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from autoedit import emit, paths
from autoedit.cli import load_run_config
from autoedit.imageio import (
    camera_preview,
    downscale_long_edge,
    is_raw,
    is_supported,
    load_image,
    raw_opts_from,
    srgb_to_linear,
)
from autoedit.pipeline import Pipeline

COLUMNS = (
    "file",
    "sha256",
    "status",
    "reason",
    "width",
    "height",
    "bits",
    "output_width",
    "output_height",
    "orientation",
    "camera_ev",
    "export_camera_ev",
    "reference",
    "encoding",
    "seconds",
    "export",
    "sidecar",
)


@contextmanager
def unlimited_pixels():
    """Lift Pillow's pixel limit for one header read, then put it back."""
    old = PILImage.MAX_IMAGE_PIXELS
    PILImage.MAX_IMAGE_PIXELS = None
    try:
        yield
    finally:
        PILImage.MAX_IMAGE_PIXELS = old


def header_size(path):
    """Size from the file header only; no pixels are decoded."""
    with unlimited_pixels(), PILImage.open(path) as image:
        return image.size


def grid(image):
    gray = PILImage.fromarray(
        np.asarray(
            np.clip(image @ [0.2126, 0.7152, 0.0722], 0, 1) * 255, dtype=np.uint8
        )
    )
    return np.asarray(gray.resize((24, 24), PILImage.Resampling.BOX), dtype=float)


def camera_check(source, camera):
    if camera is None:
        return "no embedded camera preview", ""
    image, reference = grid(source), grid(camera)
    n0 = image - image.mean()
    if min(image.std(), reference.std()) < 2:
        verdict = "indeterminate (flat preview)"
    else:
        corr = []
        for flip in (False, True):
            for k in range(4):
                b = np.rot90(np.fliplr(reference) if flip else reference, k)
                b = b - b.mean()
                corr.append(
                    float((n0 * b).sum() / (np.linalg.norm(n0) * np.linalg.norm(b)))
                )
        if max(corr) < 0.5 or (max(corr) - corr[0] < 0.12 and np.argmax(corr) != 0):
            verdict = "indeterminate (different framing)"
        else:
            verdict = (
                "agrees" if np.argmax(corr) == 0 else "possible rotation or reflection"
            )
    lum = lambda rgb: float(np.mean(srgb_to_linear(rgb) @ [0.2126, 0.7152, 0.0722]))
    return verdict, round(
        float(np.log2((lum(source) + 1e-5) / (lum(camera) + 1e-5))), 3
    )


def reference_for(path):
    """Return an independent upright raster grid, or the RAW camera's embedded image."""
    if is_raw(str(path)):
        frame = camera_preview(str(path), 400)
        return (
            frame,
            "embedded camera preview" if frame is not None else "unavailable",
            "camera RAW",
        )
    with PILImage.open(path) as source:
        # Some TIFF writers store XMP as text. Pillow expects the XMP payload as bytes
        # when it reads fallback orientation, including during its own TIFF load.
        if isinstance(source.info.get("xmp"), str):
            source.info["xmp"] = source.info["xmp"].encode("utf-8")
        tag = source.getexif().get(274, 1)
        encoding = f"{source.format} {source.mode}, EXIF {tag}, {source.width * source.height / 1e6:.1f} MP"
        if source.info.get("progressive") or source.info.get("progression"):
            encoding += ", progressive"
        if source.info.get("icc_profile"):
            encoding += ", embedded ICC"
        source.thumbnail((400, 400))
        # Pillow applies the TIFF orientation during load. Other formats need this transform.
        transforms = {
            2: PILImage.Transpose.FLIP_LEFT_RIGHT,
            3: PILImage.Transpose.ROTATE_180,
            4: PILImage.Transpose.FLIP_TOP_BOTTOM,
            5: PILImage.Transpose.TRANSPOSE,
            6: PILImage.Transpose.ROTATE_270,
            7: PILImage.Transpose.TRANSVERSE,
            8: PILImage.Transpose.ROTATE_90,
        }
        transformed = (
            source.transpose(transforms[tag])
            if source.format != "TIFF" and tag in transforms
            else source
        )
        upright = transformed.convert("RGB")
        return (
            np.asarray(upright, dtype=np.float32) / 255,
            "display-oriented input",
            encoding,
        )


def check_written(row, source, image_path):
    """Read existing emitted pixels too, so interrupted passes can resume without a re-edit."""
    try:
        reference, kind, encoding = reference_for(source)
    except (
        OSError,
        ValueError,
        RuntimeError,
        TypeError,
        PILImage.DecompressionBombError,
    ) as exc:
        row["reference"] = f"unavailable ({type(exc).__name__}: {exc})"
        row["orientation"] = "indeterminate (no usable reference)"
        return
    row["reference"], row["encoding"] = kind, encoding
    try:
        with PILImage.open(image_path) as written:
            written.thumbnail((400, 400))
            pixels = np.asarray(written.convert("RGB"), dtype=np.float32) / 255
    except PILImage.DecompressionBombError:
        row["orientation"] = "indeterminate (export too large to inspect)"
        return
    orientation, ev = camera_check(pixels, reference)
    if kind == "embedded camera preview":
        row["export_camera_ev"] = ev
    if kind == "display-oriented input" or orientation.startswith("possible"):
        row["orientation"] = orientation
    if orientation.startswith("possible"):
        row["status"] = "review"


def save_report(path, rows):
    stream = io.StringIO()
    w = csv.DictWriter(stream, fieldnames=COLUMNS)
    w.writeheader()
    w.writerows(rows)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(stream.getvalue())
    os.replace(tmp, path)


def report(source, out, *, resume=False, limit=None):
    source, out = Path(source).resolve(), Path(out).resolve()
    if not source.is_dir() or source == out or source in out.parents:
        raise ValueError("choose an existing source and an output folder outside it")
    out.mkdir(parents=True, exist_ok=True)
    export_dir = out / "run"
    export_dir.mkdir(exist_ok=True)
    csvpath = out / "coverage.csv"
    old = {}
    if resume and csvpath.exists():
        with csvpath.open(newline="") as f:
            old = {r["file"]: r for r in csv.DictReader(f)}
    cfg = load_run_config(
        argparse.Namespace(backend="stub", configs_dir=paths.configs_dir())
    )
    pipe = Pipeline.build(cfg)
    inputs = sorted(
        p
        for p in source.iterdir()
        if p.is_file() and not p.name.startswith(".") and not p.name.endswith(".part")
    )
    if limit:
        inputs = inputs[:limit]
    if not inputs:
        raise ValueError("no input files")
    taken = set()
    sources = emit.source_ids(map(str, inputs))
    rows = []
    try:
        for i, p in enumerate(inputs):
            row = dict.fromkeys(COLUMNS, "")
            row.update(file=p.name, status="failed")
            start = time.monotonic()
            try:
                with p.open("rb") as f:
                    row["sha256"] = hashlib.file_digest(f, "sha256").hexdigest()
            except OSError as exc:
                row.update(reason=str(exc), seconds=round(time.monotonic() - start, 3))
                rows.append(row)
                continue
            try:
                image_path, sidecar = emit.output_paths(
                    str(export_dir), str(p), cfg.out_format, taken, sources=sources
                )
                cached = old.get(p.name, {})
                if (
                    cached.get("status") == "ok"
                    and cached.get("sha256") == row["sha256"]
                    and Path(image_path).is_file()
                    and Path(sidecar).is_file()
                    and not (
                        p.suffix.lower() in (".tif", ".tiff")
                        and is_raw(str(p))
                        and cached.get("encoding") != "camera RAW"
                    )
                ):
                    if not cached.get("reference"):
                        try:
                            check_written(cached, p, image_path)
                        except Exception:
                            # A damaged cached export gets rendered again from the verified source.
                            pass
                        else:
                            rows.append(cached)
                            continue
                    else:
                        rows.append(cached)
                        continue
                try:
                    if not is_supported(str(p)):
                        raise ValueError(
                            f"unsupported photo format ({p.suffix.lower() or 'no extension'})"
                        )
                    photo = load_image(str(p), raw_opts_from(cfg))
                    row.update(
                        width=photo.width, height=photo.height, bits=photo.source_bits
                    )
                    proxy = downscale_long_edge(photo, 400).rgb
                    row["orientation"], row["camera_ev"] = camera_check(
                        proxy, camera_preview(str(p), 400)
                    )
                    rgb, result = pipe.process_one(str(p), image=photo)
                    emit.write_outputs(rgb, result, image_path, sidecar, cfg, str(p))
                    size = header_size(image_path)
                    row["output_width"], row["output_height"] = size
                    if size[0] * size[1] <= (PILImage.MAX_IMAGE_PIXELS or size[0] * size[1]):
                        with PILImage.open(image_path) as written:
                            written.load()      # the export must decode, not only exist
                    if result.geometry is None and size != (photo.width, photo.height):
                        raise ValueError(
                            "export dimensions differ from the developed photo"
                        )
                    row.update(
                        status="review"
                        if row["orientation"].startswith("possible")
                        else "ok",
                        export=Path(image_path).name,
                        sidecar=Path(sidecar).name,
                    )
                    check_written(row, p, image_path)
                except Exception as exc:
                    row.update(status="failed", reason=str(exc) or type(exc).__name__)
            except Exception as exc:  # one file never stops the run
                row.update(status="failed", reason=str(exc) or type(exc).__name__)
            row["seconds"] = round(time.monotonic() - start, 3)
            rows.append(row)
            seen = {r["file"] for r in rows}
            remaining = [
                r
                for name, r in old.items()
                if name not in seen and (source / name).is_file()
            ]
            save_report(csvpath, rows + remaining)
            print(
                f"[{i + 1}/{len(inputs)}] {p.name} {row['status']} {row['reason']}",
                flush=True,
            )
    finally:
        if rows:
            # Keep undispatched rows on an interrupted resumed pass.
            remaining = [
                r
                for name, r in old.items()
                if name not in {x["file"] for x in rows} and (source / name).is_file()
            ]
            save_report(csvpath, rows + remaining)
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    rows = report(args.source, args.out, resume=args.resume, limit=args.limit)
    print(
        json.dumps(
            {
                key: sum(r["status"] == key for r in rows)
                for key in ("ok", "review", "failed")
            }
        )
    )
    raise SystemExit(any(r["status"] != "ok" for r in rows))
