"""Contact sheet — one row per photo: camera preview | AutoEdit (raw_mode A) | AutoEdit (raw_mode B).

The human-eyes companion to `tone_report.py`: pick a handful of RAWs (dark / normal / snow /
highlight-risk), render them with two decode candidates through the same pipeline, and lay the
results next to the camera's own render with the objective numbers printed under each cell.

  PYTHONPATH=src python3 scripts/contact_sheet.py RAW_DIR out-tonegate/contact_sheet.jpg \
      --modes none,preview_ev_headroom --pick 0001_dark 0009_neutral 0010_snow 0039_bright --backend stub
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
from PIL import Image as PILImage, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autoedit import batch, tonemetrics as tm  # noqa: E402
from autoedit.contract import RunConfig  # noqa: E402
from autoedit.imageio import camera_preview, downscale_long_edge, load_image, raw_opts_from  # noqa: E402
from autoedit.pipeline import Pipeline  # noqa: E402


def _cell(rgb: np.ndarray, w: int, h: int, caption: str) -> PILImage.Image:
    pil = PILImage.fromarray((np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8), "RGB")
    pil.thumbnail((w, h - 34))
    cell = PILImage.new("RGB", (w, h), (18, 18, 20))
    cell.paste(pil, ((w - pil.width) // 2, 0))
    ImageDraw.Draw(cell).text((6, h - 30), caption, fill=(230, 230, 230))
    return cell


def build(raw_dir: str, out_path: str, modes: list[str], picks: list[str] | None, backend: str,
          max_px: int = 900, cell_w: int = 560, cell_h: int = 420) -> str:
    files = batch.list_images(raw_dir)
    if picks:
        files = [f for f in files if any(p in os.path.basename(f) for p in picks)]
    if not files:
        raise SystemExit("no matching files")
    pipes = {m: Pipeline.build(RunConfig(backend=backend, raw_mode=m)) for m in modes}
    cols = 1 + len(modes)
    sheet = PILImage.new("RGB", (cols * cell_w, len(files) * cell_h), (18, 18, 20))
    for r, path in enumerate(files):
        name = os.path.basename(path)
        cam = camera_preview(path, max_px)
        cells = []
        if cam is not None:
            d = tm.describe(cam)
            cells.append(_cell(cam, cell_w, cell_h, f"{name[:28]}  CAMERA  fg {d['fg_p50']:.2f} bottom {d['band_bottom_p50']:.2f} clip {100 * d['clip_hi']:.1f}%"))
        else:
            cells.append(_cell(np.zeros((8, 8, 3), np.float32), cell_w, cell_h, f"{name[:28]}  (no camera preview)"))
        for m in modes:
            img = load_image(path, decode=raw_opts_from(pipes[m].cfg))
            work = downscale_long_edge(img, max_px)
            out, res = pipes[m].process_one(path, do_apply=True, image=work)
            d = tm.from_global(res.post_edit_stats)
            s = tm.from_global(res.global_stats)
            flag = " DARK→DARKER" if tm.compare(s, d)["dark_made_darker"] else ""
            cells.append(_cell(out, cell_w, cell_h,
                               f"raw_mode={m} dec {res.decode_ev:+.2f}EV exp {res.global_settings.get('exposure', 0):+.2f} "
                               f"fg {s['fg_p50']:.2f}→{d['fg_p50']:.2f} bottom {s['band_bottom_p50']:.2f}→{d['band_bottom_p50']:.2f} "
                               f"clip {100 * d['clip_hi']:.1f}% q{res.quality_score:.2f}{flag}"))
        for c, cell in enumerate(cells):
            sheet.paste(cell, (c * cell_w, r * cell_h))
        print(f"[{r + 1}/{len(files)}] {name}")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    sheet.save(out_path, quality=88)
    print(f"wrote {out_path}")
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser(description="camera | AutoEdit(mode A) | AutoEdit(mode B) contact sheet")
    ap.add_argument("raw_dir")
    ap.add_argument("out", nargs="?", default="out-tonegate/contact_sheet.jpg")
    ap.add_argument("--modes", default="none,preview_ev_headroom")
    ap.add_argument("--pick", nargs="*", help="substrings selecting which files to include")
    ap.add_argument("--backend", choices=["stub", "sam3"], default="stub")
    ap.add_argument("--max-px", dest="max_px", type=int, default=900)
    args = ap.parse_args()
    build(args.raw_dir, args.out, [m.strip() for m in args.modes.split(",")], args.pick, args.backend, args.max_px)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
