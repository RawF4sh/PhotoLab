"""Measure what a thing mark finds (F37): one segmenter call per box of a boxes file.

    PYTHONPATH=src python scripts/eval_marks.py BOXES.toml --photos DIR [--out DIR]
                                                [--backend sam3] [--edge 1536]

BOXES.toml holds `[[box]]` tables: `photo` (a file name in --photos), `box` (x0, y0, x1, y1 as
shares of the frame), `word`, and an optional `kind` that is copied to the output. Writes
`marks.jsonl` (one line per box) and `marks-sheet-NN.jpg` (one panel per box) into --out.
Goes through the shipped segmenter only. Do not run in CI.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import tomllib

import numpy as np
from PIL import Image as PILImage, ImageDraw

from autoedit.imageio import downscale_long_edge, load_image
from autoedit.segment import make_segmenter
from autoedit.segment.base import mark_name

PANEL, COLS, ROWS = 360, 4, 3


def _panel(rgb, box, mask, label):
    h, w = rgb.shape[:2]
    x0, y0, x1, y1 = box
    out = np.clip(rgb, 0, 1) * 255
    if mask is not None:
        out = np.where(mask[..., None], out * 0.5 + np.array([0, 255, 0]) * 0.5, out)
    # a crop round the box: the box plus half its size on every side
    px, py = (x1 - x0) / 2, (y1 - y0) / 2
    cx0, cx1 = int(max(0, x0 - px) * w), int(min(1, x1 + px) * w)
    cy0, cy1 = int(max(0, y0 - py) * h), int(min(1, y1 + py) * h)
    crop = PILImage.fromarray(out.astype(np.uint8)[cy0:cy1, cx0:cx1])
    sx, sy = PANEL / crop.width, (PANEL - 40) / crop.height
    s = min(sx, sy)
    crop = crop.resize((max(1, int(crop.width * s)), max(1, int(crop.height * s))))
    d = ImageDraw.Draw(crop)
    d.rectangle([(x0 * w - cx0) * s, (y0 * h - cy0) * s, (x1 * w - cx0) * s, (y1 * h - cy0) * s],
                outline=(255, 255, 0), width=2)
    tile = PILImage.new("RGB", (PANEL, PANEL), (24, 24, 24))
    tile.paste(crop, (0, 0))
    ImageDraw.Draw(tile).multiline_text((4, PANEL - 36), label, fill=(255, 255, 255))
    return tile


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("boxes")
    ap.add_argument("--photos", required=True)
    ap.add_argument("--out")
    ap.add_argument("--backend", default="sam3")
    ap.add_argument("--edge", type=int, default=1536)
    a = ap.parse_args(argv)
    with open(a.boxes, "rb") as fh:
        boxes = tomllib.load(fh)["box"]
    out_dir = a.out or os.path.dirname(os.path.abspath(a.boxes))
    os.makedirs(out_dir, exist_ok=True)
    seg = make_segmenter(a.backend)

    rows, panels, seen, cache = [], [], set(), {}
    for n, b in enumerate(boxes):
        photo = b["photo"]
        if photo not in cache:
            cache = {photo: downscale_long_edge(load_image(os.path.join(a.photos, photo)), a.edge)}
        proxy = cache[photo]
        box = tuple(b["box"])
        first = photo not in seen
        seen.add(photo)
        t0 = time.perf_counter()
        got = seg.segment(proxy, [mark_name("thing", box, b["word"])])
        secs = time.perf_counter() - t0
        rm = got[0] if got else None
        h, w = proxy.rgb.shape[:2]
        inside = 0.0
        if rm is not None:
            x0, y0, x1, y1 = int(box[0] * w), int(box[1] * h), int(box[2] * w) + 1, int(box[3] * h) + 1
            inside = float(rm.mask[y0:y1, x0:x1].sum() / rm.mask.sum())
        rows.append({"n": n, "photo": photo, "box": list(box), "word": b["word"], "kind": b.get("kind"),
                     "kept": rm.instance_count if rm else 0, "score": rm.score if rm else 0.0,
                     "coverage": rm.coverage if rm else 0.0, "inside": inside, "seconds": secs,
                     "first_on_photo": first, "load": os.getloadavg()[0]})
        label = f"#{n} {photo}\n{b['word']}  kept {rows[-1]['kept']}"
        panels.append(_panel(proxy.rgb, box, rm.mask if rm else None, label))

    with open(os.path.join(out_dir, "marks.jsonl"), "w") as fh:
        fh.writelines(json.dumps(r) + "\n" for r in rows)
    per = COLS * ROWS
    for i in range(0, len(panels), per):
        sheet = PILImage.new("RGB", (COLS * PANEL, ROWS * PANEL), (0, 0, 0))
        for j, p in enumerate(panels[i:i + per]):
            sheet.paste(p, ((j % COLS) * PANEL, (j // COLS) * PANEL))
        sheet.save(os.path.join(out_dir, f"marks-sheet-{i // per + 1:02d}.jpg"), quality=88)

    warm = [r["seconds"] for r in rows if not r["first_on_photo"]]
    med = f"{statistics.median(warm):.2f}s" if warm else "n/a"
    print(f"{len(rows)} boxes, {sum(r['kept'] > 0 for r in rows)} with a mask, "
          f"median {med} per call after the first on a photo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
