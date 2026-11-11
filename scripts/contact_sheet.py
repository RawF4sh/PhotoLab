"""Contact sheet — one row per photo: camera preview | AutoEdit (raw_mode A) | AutoEdit (raw_mode B).

The human-eyes companion to `tone_report.py`: pick a handful of RAWs (dark / normal / snow /
highlight-risk), render them with two decode candidates through the same pipeline, and lay the
results next to the camera's own render with the objective numbers printed under each cell.

  PYTHONPATH=src python3 scripts/contact_sheet.py RAW_DIR out-tonegate/contact_sheet.jpg \
      --modes none,preview_ev_headroom --pick 0001_dark 0009_neutral 0010_snow 0039_bright --backend stub

Panel mode — the blind A/B screenshot loop for tuning configs. Each row is one panel photo:
Original | AE A | AE B | Reference, where A/B are the current-best and the candidate configs dir
in a per-row random order. Both render at full resolution through the real `Pipeline` (cached on
disk per configs-dir content, so an unchanged arm is never re-rendered; SAM masks are cached too).
The blind sheets carry no settings; `key.json` and the `*_captioned` sheets do — open those only
after writing the verdicts. `facts.txt` is safe to read first: per row it says how far apart A and
B are and which side is darker / more saturated / warmer (and, per crop, which is crisper), never
which one is the candidate (a subtle pair is otherwise easy to mislabel). `--crops` adds 100%
crops of the risky areas.

  PYTHONPATH=src python3 scripts/contact_sheet.py --panel out-loop/panel.txt \
      --best out-loop/iter-00/configs --candidate out-loop/iter-01/configs \
      --out-dir out-loop/iter-01/sheets --crops out-loop/crops.txt --backend sam3
  PYTHONPATH=src python3 scripts/contact_sheet.py --tally out-loop/iter-01/sheets/verdicts.txt

  panel.txt    SOURCE<TAB>REFERENCE   (REFERENCE: an image path, or `camera` for the RAW preview)
  crops.txt    STEM<TAB>CX<TAB>CY<TAB>LABEL   (crop centre as fractions of width/height)
  verdicts.txt STEM A|B|= REASON      (which side is better, judged before reading the key)
"""

from __future__ import annotations

import argparse
import glob
import gzip
import hashlib
import json
import os
import pickle
import random
import sys
import textwrap
from collections import Counter

import numpy as np
from PIL import Image as PILImage, ImageDraw, ImageFont, ImageOps

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autoedit import batch, emit, tonemetrics as tm  # noqa: E402
from autoedit.cli import load_run_config  # noqa: E402
from autoedit.contract import Result, RunConfig  # noqa: E402
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


# ── panel mode: blind A/B of two configs dirs through the real Pipeline ──────────────────────

# The image viewer scales a sheet to a 2000 px long edge: 4 × 500 px cells and 3 × 660 px crops
# arrive unscaled, and a sheet taller than 2000 px would shrink everything on it.
_CELL_W, _CELL_H, _CROP, _SHEET_H = 500, 700, (660, 440), 2000
try:
    _FONT = ImageFont.load_default(size=14)
except TypeError:  # Pillow < 10.1 has only the bitmap font
    _FONT = ImageFont.load_default()


class DiskSegmenter:
    """Persist masks per proxy image, so both arms and every later run share one SAM pass.
    ponytail: gzip-pickle keyed by proxy pixels; delete the folder after changing SAM weights."""

    def __init__(self, inner, root: str):
        self.inner, self.root = inner, root
        os.makedirs(root, exist_ok=True)

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def segment(self, img, concepts):
        pixels = np.ascontiguousarray(img.rgb)
        path = os.path.join(self.root, hashlib.blake2b(pixels.tobytes(), digest_size=16).hexdigest() + ".pkl.gz")
        bucket = {}
        if os.path.exists(path):
            with gzip.open(path, "rb") as fh:
                bucket = pickle.load(fh)
        missing = [c for c in concepts if c not in bucket]
        if missing:
            fresh = {m.name: m for m in self.inner.segment(img, missing)}
            bucket.update({c: fresh.get(c) for c in missing})  # None = tried, found nothing
            with gzip.open(path + ".part", "wb") as fh:
                pickle.dump(bucket, fh)
            os.replace(path + ".part", path)
        return [bucket[c] for c in concepts if bucket[c] is not None]


def _config_id(configs_dir: str, backend: str) -> str:
    """Content hash of a configs dir plus the engine source: a cached render is reused only
    while both are byte-identical."""
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "autoedit")
    h = hashlib.sha256(backend.encode())
    for p in sorted(glob.glob(os.path.join(configs_dir, "*.toml"))) + sorted(
            glob.glob(os.path.join(src, "**", "*.py"), recursive=True)):
        h.update(os.path.basename(p).encode())
        with open(p, "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()[:12]


def _stem(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def _render(pipe: Pipeline, path: str, out_dir: str) -> tuple[str, Result]:
    """Full-resolution render kept on disk as the product's own JPEG + sidecar."""
    jpg = os.path.join(out_dir, _stem(path) + ".jpg")
    side = os.path.join(out_dir, _stem(path) + ".autoedit.json")
    if not os.path.exists(side):  # the sidecar is written last, so it implies the image
        os.makedirs(out_dir, exist_ok=True)
        rgb, res = pipe.process_one(path)
        emit.write_image(rgb, jpg, "jpeg", pipe.cfg.quality)
        emit.write_sidecar(res, side)
    with open(side) as fh:
        return jpg, Result.model_validate_json(fh.read())


def _caption(res: Result) -> str:
    """Top settings, then palette, clip %, foreground change, the dark→darker flag, and the
    source→output warmth (OKLab b; lower = bluer) and saturation."""
    g = res.global_settings
    top = " ".join(f"{'sEV' if k == 'shadows_ev' else k[:4]} {g[k]:+.1f}" for k in (
        "exposure", "shadows_ev", "contrast", "curve", "shadows", "blacks", "whites", "highlights", "temp",
        "vibrance", "saturation", "dehaze", "clarity", "sharpen") if abs(g.get(k, 0.0)) >= 0.05)
    s, d = tm.from_global(res.global_stats), tm.from_global(res.post_edit_stats)
    palette = next((n.split(" — ")[0].replace("palette: ", "pal ") for n in res.notes
                    if n.startswith("palette:")), "pal none")
    flag = " DARK>DARKER" if tm.compare(s, d)["dark_made_darker"] else ""
    review = " REVIEW" if res.needs_review else ""
    pre, post = res.global_stats, res.post_edit_stats
    return (f"{top}\n{palette} | clip {100 * d['clip_hi']:.1f}% lo {100 * d['clip_lo']:.1f}% | "
            f"fg {s['fg_p50']:.2f}>{d['fg_p50']:.2f}{flag}{review}\n"
            f"warmth {pre.warmth:+.3f}>{post.warmth:+.3f} | sat {pre.mean_sat:.2f}>{post.mean_sat:.2f}")


def _facts(a: PILImage.Image, b: PILImage.Image) -> str:
    """Blind-safe facts about an A/B pair (8-bit levels): how far apart the two sides are and
    which one is darker, brighter, more saturated or warmer. They say nothing about which side
    is the candidate, and keep a verdict tied to what is actually in each cell."""
    x, y = (np.asarray(_fit(im), dtype=np.float32) for im in (a, b))
    lx, ly = x.mean(axis=2), y.mean(axis=2)

    def pair(name, fn):
        return f"{name} A {fn(x, lx):.0f} B {fn(y, ly):.0f}"

    return " | ".join([
        f"diff {np.abs(x - y).mean():.1f}",   # over R, G and B, so a pure colour shift counts
        pair("shadows", lambda rgb, lum: np.percentile(lum, 2)),
        pair("mid", lambda rgb, lum: np.median(lum)),
        pair("highlights", lambda rgb, lum: np.percentile(lum, 98)),
        pair("saturation", lambda rgb, lum: (rgb.max(axis=2) - rgb.min(axis=2)).mean()),
        pair("warmth(R-B)", lambda rgb, lum: (rgb[..., 0] - rgb[..., 2]).mean()),
    ])


def _detail(pil: PILImage.Image) -> float:
    """Fine-detail energy of a crop (mean absolute neighbour difference, 8-bit levels): which
    side is crisper. Thumbnails hide sharpening, so the crops get their own blind-safe fact."""
    a = np.asarray(pil.convert("L"), dtype=np.float32)
    return float(np.abs(np.diff(a, axis=0)).mean() + np.abs(np.diff(a, axis=1)).mean())


def _fit(pil: PILImage.Image) -> PILImage.Image:
    pil = pil.convert("RGB")
    pil.thumbnail((_CELL_W, _CELL_H))
    return pil


def _crop(pil: PILImage.Image, cx: float, cy: float) -> PILImage.Image:
    w, h = min(_CROP[0], pil.width), min(_CROP[1], pil.height)
    x = int(min(max(cx * pil.width - w / 2, 0), pil.width - w))
    y = int(min(max(cy * pil.height - h / 2, 0), pil.height - h))
    return pil.crop((x, y, x + w, y + h)).convert("RGB")


def _write_sheets(rows: list[list[tuple[PILImage.Image, str]]], prefix: str, col_w: int,
                  max_rows: int = 4) -> list[str]:
    """Stack rows of (image, caption) cells into sheets of ≤ max_rows rows and ≤ _SHEET_H px."""
    rows = [[(im, "\n".join(w for line in c.split("\n") for w in textwrap.wrap(line, col_w // 8) or [""]))
             for im, c in row] for row in rows]

    def height(row):
        return max(im.height for im, _ in row) + 6 + 18 * max(c.count("\n") + 1 for _, c in row)

    pages: list[list] = [[]]
    for row in rows:
        if pages[-1] and (len(pages[-1]) == max_rows
                          or sum(map(height, pages[-1])) + height(row) > _SHEET_H):
            pages.append([])
        pages[-1].append(row)
    out = []
    for n, page in enumerate(pages, 1):
        sheet = PILImage.new("RGB", (col_w * max(len(r) for r in page), sum(map(height, page))), (18, 18, 20))
        draw, y = ImageDraw.Draw(sheet), 0
        for row in page:
            for c, (im, caption) in enumerate(row):
                sheet.paste(im, (c * col_w + (col_w - im.width) // 2, y))
                draw.multiline_text((c * col_w + 6, y + max(i.height for i, _ in row) + 3), caption,
                                    fill=(230, 230, 230), font=_FONT, spacing=3)
            y += height(row)
        out.append(f"{prefix}_{n:02d}.jpg")
        sheet.save(out[-1], quality=92)
    return out


def build_panel(panel_path: str, best_dir: str, cand_dir: str, out_dir: str, backend: str = "stub",
                crops_path: str | None = None, seed: int = 1, cache: str | None = None,
                picks: list[str] | None = None, style: str | None = None,
                best_style: str | None = None) -> dict:
    with open(panel_path) as fh:
        panel = [line.rstrip("\n").split("\t") for line in fh if line.strip() and not line.startswith("#")]
    if picks:
        panel = [p for p in panel if any(k in os.path.basename(p[0]) for k in picks)]
    if len({_stem(p[0]) for p in panel}) != len(panel) or not panel:
        raise SystemExit("panel needs at least one photo and unique file stems")
    crops: dict[str, list] = {}
    if crops_path:
        with open(crops_path) as fh:
            for line in fh:
                if line.strip() and not line.startswith("#"):
                    stem, cx, cy, label = line.rstrip("\n").split("\t")
                    crops.setdefault(stem, []).append((float(cx), float(cy), label))
    cache = cache or os.path.join(os.path.dirname(os.path.abspath(panel_path)), "cache")
    os.makedirs(out_dir, exist_ok=True)

    pipes, render_dirs = {}, {}
    for role, d in (("best", best_dir), ("candidate", cand_dir)):
        look = (best_style or style) if role == "best" else style   # None → the dir's default style
        pipes[role] = Pipeline.build(load_run_config(
            argparse.Namespace(configs_dir=d, backend=backend, style=look)))
        pipes[role].segmenter = (pipes["best"].segmenter if role == "candidate" else
                                 DiskSegmenter(pipes[role].segmenter, os.path.join(cache, f"masks-{backend}")))
        render_dirs[role] = os.path.join(cache, "render-" + _config_id(d, f"{backend}:{look}" if look else backend))
    decode = raw_opts_from(pipes["best"].cfg)
    orig_dir = os.path.join(cache, "original-" + decode["raw_mode"])
    os.makedirs(orig_dir, exist_ok=True)

    rng = random.Random(seed)
    key = {"seed": seed, "best": os.path.abspath(best_dir), "candidate": os.path.abspath(cand_dir),
           "renders": render_dirs, "rows": {}}
    blind, shown, crop_rows, facts = [], [], [], []
    for i, entry in enumerate(panel, 1):
        path, ref = entry[0], (entry[1] if len(entry) > 1 else "")
        stem = _stem(path)
        orig_jpg = os.path.join(orig_dir, stem + ".jpg")
        if not os.path.exists(orig_jpg):
            emit.write_image(load_image(path, decode=decode).rgb, orig_jpg, "jpeg", 95)
        done = {role: _render(pipes[role], path, render_dirs[role]) for role in ("best", "candidate")}
        sides = ["best", "candidate"]
        rng.shuffle(sides)
        side = dict(zip("AB", sides))
        key["rows"][stem] = {**side, **{f"caption_{s}": _caption(done[r][1]) for s, r in side.items()}}

        orig = PILImage.open(orig_jpg)
        ae = {s: PILImage.open(done[r][0]) for s, r in side.items()}
        facts.append(f"{stem}\t{_facts(ae['A'], ae['B'])}")
        if ref == "camera":
            cam = camera_preview(path, 1600)
            ref_pil = None if cam is None else PILImage.fromarray((np.clip(cam, 0, 1) * 255 + 0.5).astype(np.uint8))
            ref_label = "camera preview"
        else:
            ref_pil = ImageOps.exif_transpose(PILImage.open(ref)) if ref else None
            ref_label = os.path.basename(os.path.dirname(ref)) if ref else ""
        ref_cell = (_fit(ref_pil) if ref_pil is not None else PILImage.new("RGB", (8, 8)),
                    f"reference: {ref_label}" if ref_pil is not None else "(no reference)")
        base = [(_fit(orig), f"{stem}  ORIGINAL")]
        blind.append(base + [(_fit(ae[s]), s) for s in "AB"] + [ref_cell])
        shown.append(base + [(_fit(ae[s]), f"{s} = {side[s]}  {key['rows'][stem]['caption_' + s]}")
                             for s in "AB"] + [ref_cell])
        for cx, cy, label in crops.get(stem, []):
            cut = {"ORIGINAL": _crop(orig, cx, cy), **{s: _crop(ae[s], cx, cy) for s in "AB"}}
            crop_rows.append([(cut["ORIGINAL"], f"{stem}  {label}  ORIGINAL 100%")]
                             + [(cut[s], s) for s in "AB"])
            facts.append(f"{stem}\tcrop {label}: detail "
                         + " ".join(f"{k} {_detail(v):.2f}" for k, v in cut.items()))
        print(f"[{i}/{len(panel)}] {stem}")

    files = {"blind": _write_sheets(blind, os.path.join(out_dir, "panel"), _CELL_W),
             "captioned": _write_sheets(shown, os.path.join(out_dir, "panel_captioned"), _CELL_W)}
    if crop_rows:
        files["crops"] = _write_sheets(crop_rows, os.path.join(out_dir, "crops"), _CROP[0])
    with open(os.path.join(out_dir, "facts.txt"), "w") as fh:  # safe to read before the verdicts
        fh.write("\n".join(facts) + "\n")
    with open(os.path.join(out_dir, "key.json"), "w") as fh:  # read only after the verdicts
        json.dump(key, fh, indent=1)
    for name in files["blind"] + files.get("crops", []):
        print(f"wrote {name}")
    return files


def tally(verdicts_path: str, key_path: str | None = None) -> Counter:
    """Translate blind `STEM A|B|= REASON` verdicts into Better/Same/Worse for the candidate."""
    with open(key_path or os.path.join(os.path.dirname(verdicts_path), "key.json")) as fh:
        rows = json.load(fh)["rows"]
    counts: Counter = Counter()
    with open(verdicts_path) as fh:
        for line in fh:
            parts = line.split(None, 2)
            if len(parts) < 2 or line.startswith("#"):
                continue
            stem, pick = parts[0], parts[1]
            verdict = "Same" if pick == "=" else "Better" if rows[stem][pick] == "candidate" else "Worse"
            counts[verdict] += 1
            print(f"{stem}\t{verdict}\t{parts[2].strip() if len(parts) > 2 else ''}")
    print(f"\ncandidate: {counts['Better']} better / {counts['Same']} same / {counts['Worse']} worse")
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description="camera | AutoEdit(mode A) | AutoEdit(mode B) contact sheet")
    ap.add_argument("raw_dir", nargs="?")
    ap.add_argument("out", nargs="?", default="out-tonegate/contact_sheet.jpg")
    ap.add_argument("--modes", default="none,preview_ev_headroom")
    ap.add_argument("--pick", nargs="*", help="substrings selecting which files to include")
    ap.add_argument("--backend", choices=["stub", "sam3"], default="stub")
    ap.add_argument("--max-px", dest="max_px", type=int, default=900)
    ap.add_argument("--panel", help="panel.txt: blind A/B sheets of two configs dirs (see module docstring)")
    ap.add_argument("--best", help="configs dir of the current best")
    ap.add_argument("--candidate", help="configs dir of the candidate")
    ap.add_argument("--out-dir", dest="out_dir", help="where panel-mode sheets and key.json go")
    ap.add_argument("--crops", help="crops.txt: 100%% crops of risky areas")
    ap.add_argument("--seed", type=int, default=1, help="A/B side assignment")
    ap.add_argument("--style", help="panel mode: render both arms with this style (default: the dirs' own)")
    ap.add_argument("--best-style", dest="best_style",
                    help="panel mode: style for the --best arm only (e.g. natural vs a style of one dir)")
    ap.add_argument("--cache", help="render/mask cache (default: cache/ next to the panel file)")
    ap.add_argument("--tally", metavar="VERDICTS", help="score a verdicts.txt against its key.json")
    args = ap.parse_args()
    if args.tally:
        tally(args.tally)
        return 0
    if args.panel:
        if not (args.best and args.candidate and args.out_dir):
            ap.error("--panel needs --best, --candidate and --out-dir")
        build_panel(args.panel, args.best, args.candidate, args.out_dir, args.backend, args.crops,
                    args.seed, args.cache, args.pick, args.style, args.best_style)
        return 0
    if not args.raw_dir:
        ap.error("give RAW_DIR, --panel or --tally")
    build(args.raw_dir, args.out, [m.strip() for m in args.modes.split(",")], args.pick, args.backend, args.max_px)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
