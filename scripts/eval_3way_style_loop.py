"""Build a visual calibration loop for eval_3way.

This is not neural training. It applies a fixed, ordinary AutoEdit slider recipe to
the original images, then places that proxy result next to the generated references.
The contact sheet is the screenshot loop: inspect it, adjust the recipe/style, rerun.

Usage:
  PYTHONPATH=src python3 scripts/eval_3way_style_loop.py eval_3way
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from autoedit.edit.ops import apply_ops


CALIBRATED_PROXY_SETTINGS = {
    # Calibrated from eval_3way old-cinematic references, then pulled back to avoid
    # teal/cyan cast, crushed shadows, neon greens, and generated-looking drama.
    "exposure": -0.03,
    "contrast": 5.0,
    "shadows": -3.0,
    "whites": -3.0,
    "blacks": -3.0,
    "dehaze": 8.0,
    "clarity": 9.0,
    "vibrance": 11.0,
    "saturation": 4.0,
    "temp": -1.0,
    "tint": -2.0,
    "hsl_green_sat": 4.0,
    "hsl_green_lum": -2.0,
    "hsl_blue_sat": 3.0,
    "curve": 10.0,
}


def _open_rgb(path: Path, long_edge: int) -> Image.Image:
    image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    image.thumbnail((long_edge, long_edge), Image.Resampling.LANCZOS)
    return image


def _apply_proxy(image: Image.Image) -> Image.Image:
    rgb = np.asarray(image).astype(np.float32) / 255.0
    edited = apply_ops(rgb, CALIBRATED_PROXY_SETTINGS)
    out = np.clip(np.round(edited * 255.0), 0, 255).astype(np.uint8)
    return Image.fromarray(out, "RGB")


def _image_path(folder: Path, stem: str) -> Path:
    for suffix in (".JPG", ".jpg", ".JPEG", ".jpeg", ".png", ".tif", ".tiff", ".webp"):
        path = folder / f"{stem}{suffix}"
        if path.exists():
            return path
    raise FileNotFoundError(f"no image for {stem} in {folder}")


def _load_rows(root: Path, labels_path: Path | None) -> list[dict[str, str]]:
    if labels_path and labels_path.exists():
        with labels_path.open(newline="") as handle:
            return list(csv.DictReader(handle))
    manifest = json.loads((root / "_review" / "eval_manifest.json").read_text(encoding="utf-8"))
    return [{"id": item["id"], "codex_final_target": ""} for item in manifest["items"]]


def _select_rows(rows: list[dict[str, str]], limit: int, target: str) -> list[dict[str, str]]:
    if target != "all":
        rows = [row for row in rows if row.get("codex_final_target") == target]
    return rows[:limit]


def build_sheet(root: Path, rows: list[dict[str, str]], out_path: Path, long_edge: int = 520) -> None:
    columns = [
        ("Original", root / "original"),
        ("AutoEdit calibrated", None),
        ("New Lightroom", root / "gemini_new_lightroom"),
        ("Old Cinematic", root / "gemini_old_cinematic"),
    ]
    tile_w, tile_h = 300, 210
    label_h = 34
    row_h = tile_h + label_h + 14
    sheet = Image.new("RGB", (tile_w * len(columns), row_h * len(rows)), (18, 18, 18))
    draw = ImageDraw.Draw(sheet)

    for row_i, row in enumerate(rows):
        stem = row["id"]
        original = _open_rgb(_image_path(root / "original", stem), long_edge)
        images = [
            original,
            _apply_proxy(original.copy()),
            _open_rgb(_image_path(root / "gemini_new_lightroom", stem), long_edge),
            _open_rgb(_image_path(root / "gemini_old_cinematic", stem), long_edge),
        ]
        for col_i, ((label, _folder), image) in enumerate(zip(columns, images)):
            image.thumbnail((tile_w, tile_h), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (tile_w, tile_h), (0, 0, 0))
            canvas.paste(image, ((tile_w - image.width) // 2, (tile_h - image.height) // 2))
            x = col_i * tile_w
            y = row_i * row_h + label_h
            sheet.paste(canvas, (x, y))
            final = row.get("codex_final_target", "")
            header = f"{label} - {stem}" if col_i == 0 else label
            if col_i == 1 and final:
                header += f" ({final})"
            draw.text((x + 8, row_i * row_h + 8), header, fill=(238, 238, 238))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, quality=92)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build eval_3way calibrated style contact sheets")
    parser.add_argument("eval_dir", type=Path)
    parser.add_argument("--labels", type=Path, default=None,
                        help="assessment CSV; defaults to eval_dir/_review/codex_assessment.csv")
    parser.add_argument("--target", default="hybrid_old_style_new_preservation",
                        choices=["all", "hybrid_old_style_new_preservation", "old_cinematic", "new_lightroom", "needs_regen"])
    parser.add_argument("--limit", type=int, default=36)
    parser.add_argument("--long-edge", type=int, default=520)
    args = parser.parse_args()

    root = args.eval_dir
    labels = args.labels or root / "_review" / "codex_assessment.csv"
    rows = _select_rows(_load_rows(root, labels), args.limit, args.target)
    out = root / "_review" / f"autoedit_cinematic_preserved_{args.target}_sheet.jpg"
    build_sheet(root, rows, out, args.long_edge)
    settings_path = root / "_review" / "autoedit_cinematic_preserved_settings.json"
    settings_path.write_text(json.dumps(CALIBRATED_PROXY_SETTINGS, indent=2) + "\n", encoding="utf-8")
    print(out)
    print(settings_path)
    print(f"rows={len(rows)} target={args.target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
