"""Calibrate an AutoEdit slider recipe against reference edits.

This is style calibration, not neural training: it searches normal AutoEdit ops
for a dataset-level recipe whose low-res color/tone/contrast features match a
reference folder. It is useful for turning a batch of example edits into a
repeatable preset without generating new image content.

Example:
  PYTHONPATH=src python3 scripts/train_style_to_reference.py eval_3way \
    --reference gemini_new_lightroom --samples 80 --iterations 500
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from autoedit.edit.ops import apply_ops
from autoedit.imageio import luma


SETTING_RANGES = {
    "exposure": (-0.35, 0.45),
    "contrast": (-12.0, 34.0),
    "highlights": (-34.0, 18.0),
    "shadows": (-22.0, 30.0),
    "whites": (-26.0, 18.0),
    "blacks": (-26.0, 14.0),
    "curve": (-8.0, 30.0),
    "dehaze": (-4.0, 22.0),
    "clarity": (-4.0, 28.0),
    "vibrance": (-6.0, 30.0),
    "saturation": (-10.0, 16.0),
    "temp": (-10.0, 10.0),
    "tint": (-8.0, 8.0),
    "hsl_green_sat": (-12.0, 18.0),
    "hsl_green_lum": (-12.0, 8.0),
    "hsl_blue_sat": (-10.0, 16.0),
    "sharpen": (0.0, 22.0),
}

STARTING_SETTINGS = {
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

FAST_SEARCH_KEYS = tuple(key for key in SETTING_RANGES if key != "dehaze")

FEATURE_WEIGHTS = {
    "rgb_mean": 2.6,
    "rgb_std": 1.4,
    "luma_quantiles": 3.0,
    "sat_stats": 1.4,
    "edge_stats": 1.1,
    "green_stats": 0.9,
    "blue_stats": 0.8,
}

IMAGE_SUFFIXES = (".JPG", ".jpg", ".JPEG", ".jpeg", ".png", ".tif", ".tiff", ".webp")


def _image_path(folder: Path, stem: str) -> Path | None:
    for suffix in IMAGE_SUFFIXES:
        path = folder / f"{stem}{suffix}"
        if path.exists():
            return path
    return None


def _open_array(path: Path, edge: int) -> np.ndarray:
    image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    image.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    return np.asarray(image).astype(np.float32) / 255.0


def _rgb_to_hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx = rgb.max(axis=2)
    mn = rgb.min(axis=2)
    d = mx - mn
    h = np.zeros_like(mx)
    nz = d > 1e-6
    i = (mx == r) & nz
    h[i] = (60.0 * ((g - b)[i] / d[i])) % 360.0
    i = (mx == g) & nz
    h[i] = (60.0 * ((b - r)[i] / d[i]) + 120.0) % 360.0
    i = (mx == b) & nz
    h[i] = (60.0 * ((r - g)[i] / d[i]) + 240.0) % 360.0
    s = np.where(mx > 1e-6, d / np.maximum(mx, 1e-6), 0.0)
    return h.astype(np.float32), s.astype(np.float32), mx.astype(np.float32)


def _mask_stats(values: np.ndarray, mask: np.ndarray) -> np.ndarray:
    if mask.mean() < 0.02:
        return np.zeros(3, dtype=np.float32)
    selected = values[mask]
    return np.array([selected.mean(), selected.std(), np.quantile(selected, 0.75)], dtype=np.float32)


def _features(rgb: np.ndarray) -> dict[str, np.ndarray]:
    lum = luma(rgb)
    h, sat, _v = _rgb_to_hsv(rgb)
    gy, gx = np.gradient(lum)
    edge = np.sqrt(gx * gx + gy * gy)
    green = (h > 70.0) & (h < 165.0) & (sat > 0.12)
    blue = (h > 175.0) & (h < 260.0) & (sat > 0.10)
    return {
        "rgb_mean": rgb.reshape(-1, 3).mean(axis=0),
        "rgb_std": rgb.reshape(-1, 3).std(axis=0),
        "luma_quantiles": np.quantile(lum, [0.02, 0.10, 0.25, 0.50, 0.75, 0.90, 0.98]).astype(np.float32),
        "sat_stats": np.array([sat.mean(), sat.std(), np.quantile(sat, 0.90)], dtype=np.float32),
        "edge_stats": np.array([edge.mean(), edge.std(), np.quantile(edge, 0.90)], dtype=np.float32),
        "green_stats": _mask_stats(np.stack([sat, lum, rgb[..., 1]], axis=2), green),
        "blue_stats": _mask_stats(np.stack([sat, lum, rgb[..., 2]], axis=2), blue),
    }


def _feature_loss(a: dict[str, np.ndarray], b: dict[str, np.ndarray]) -> float:
    loss = 0.0
    for key, weight in FEATURE_WEIGHTS.items():
        diff = a[key] - b[key]
        loss += weight * float(np.mean(diff * diff))
    return loss


def _load_label_targets(labels_path: Path | None, target: str) -> set[str] | None:
    if not labels_path or target == "all" or not labels_path.exists():
        return None
    ids: set[str] = set()
    with labels_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("codex_final_target") == target:
                ids.add(row["id"])
    return ids


def _load_pairs(root: Path, reference: str, edge: int, samples: int, target: str, seed: int) -> list[tuple[str, np.ndarray, dict[str, np.ndarray]]]:
    original_dir = root / "original"
    reference_dir = root / reference
    labels_path = root / "_review" / "codex_assessment.csv"
    allowed = _load_label_targets(labels_path, target)

    stems = []
    for path in sorted(original_dir.iterdir()):
        if path.suffix not in IMAGE_SUFFIXES:
            continue
        if allowed is not None and path.stem not in allowed:
            continue
        if _image_path(reference_dir, path.stem):
            stems.append(path.stem)

    rng = random.Random(seed)
    rng.shuffle(stems)
    if samples > 0:
        stems = stems[:samples]

    pairs = []
    for stem in stems:
        original_path = _image_path(original_dir, stem)
        reference_path = _image_path(reference_dir, stem)
        if original_path is None or reference_path is None:
            continue
        original = _open_array(original_path, edge)
        reference_arr = _open_array(reference_path, edge)
        pairs.append((stem, original, _features(reference_arr)))
    return pairs


def _sanitize(settings: dict[str, float]) -> dict[str, float]:
    out = {}
    for key, (lo, hi) in SETTING_RANGES.items():
        value = float(settings.get(key, 0.0))
        value = min(hi, max(lo, value))
        if abs(value) >= 0.005:
            out[key] = round(value, 4)
    return out


def _score(settings: dict[str, float], pairs: list[tuple[str, np.ndarray, dict[str, np.ndarray]]]) -> float:
    if not pairs:
        return math.inf
    total = 0.0
    clean = _sanitize(settings)
    for _stem, original, reference_features in pairs:
        edited = np.clip(apply_ops(original, clean), 0.0, 1.0)
        total += _feature_loss(_features(edited), reference_features)
    return total / len(pairs)


def _random_candidate(rng: random.Random, base: dict[str, float], temperature: float, keys: tuple[str, ...]) -> dict[str, float]:
    candidate = dict(base)
    for key in keys:
        lo, hi = SETTING_RANGES[key]
        span = hi - lo
        if rng.random() < 0.55:
            candidate[key] = candidate.get(key, 0.0) + rng.gauss(0.0, span * 0.11 * temperature)
        else:
            candidate[key] = rng.uniform(lo, hi)
    return _sanitize(candidate)


def train(
    pairs: list[tuple[str, np.ndarray, dict[str, np.ndarray]]],
    iterations: int,
    seed: int,
    search_dehaze: bool,
) -> tuple[dict[str, float], float]:
    rng = random.Random(seed)
    best = _sanitize(STARTING_SETTINGS)
    if not search_dehaze:
        best.pop("dehaze", None)
    best_score = _score(best, pairs)
    search_keys = tuple(SETTING_RANGES) if search_dehaze else FAST_SEARCH_KEYS
    starts = [
        {},
        STARTING_SETTINGS,
        {"exposure": 0.12, "contrast": 14, "highlights": -14, "shadows": 8, "whites": -9,
         "blacks": -10, "curve": 14, "dehaze": 8, "clarity": 12, "vibrance": 16,
         "saturation": 2, "temp": 0, "tint": 0, "hsl_green_sat": 0, "hsl_green_lum": -2,
         "hsl_blue_sat": 3, "sharpen": 8},
        {"exposure": 0.22, "contrast": 6, "highlights": -24, "shadows": 20, "whites": -18,
         "blacks": -6, "curve": 5, "dehaze": 2, "clarity": 5, "vibrance": 10,
         "saturation": -2, "temp": 1, "tint": 0, "hsl_green_sat": -2, "hsl_green_lum": 1,
         "hsl_blue_sat": 0, "sharpen": 4},
    ]
    for start in starts:
        if not search_dehaze:
            start = {k: v for k, v in start.items() if k != "dehaze"}
        score = _score(start, pairs)
        if score < best_score:
            best, best_score = _sanitize(start), score

    for i in range(max(0, iterations)):
        temperature = max(0.10, 1.0 - i / max(1, iterations))
        candidate = _random_candidate(rng, best, temperature, search_keys)
        score = _score(candidate, pairs)
        if score < best_score:
            best, best_score = candidate, score
        if iterations >= 50 and (i + 1) % 50 == 0:
            print(f"iteration={i + 1} score={best_score:.6f}", flush=True)

    # A final coordinate pass is cheap and usually tightens the result.
    step_fracs = (0.08, 0.04, 0.02)
    for frac in step_fracs:
        improved = True
        passes = 0
        while improved and passes < 3:
            passes += 1
            improved = False
            for key in search_keys:
                lo, hi = SETTING_RANGES[key]
                step = (hi - lo) * frac
                for direction in (-1.0, 1.0):
                    candidate = dict(best)
                    candidate[key] = candidate.get(key, 0.0) + direction * step
                    candidate = _sanitize(candidate)
                    score = _score(candidate, pairs)
                    if score < best_score:
                        best, best_score = candidate, score
                        improved = True

    if not search_dehaze:
        # Dehaze is much slower than the rest of the recipe, so choose it with
        # a tiny sweep after the cheap controls have settled.
        for value in (-2.0, 0.0, 3.0, 6.0, 9.0, 12.0, 16.0):
            candidate = dict(best)
            if value:
                candidate["dehaze"] = value
            score = _score(candidate, pairs)
            if score < best_score:
                best, best_score = _sanitize(candidate), score
    return best, best_score


def _tile(arr: np.ndarray, size: tuple[int, int]) -> Image.Image:
    image = Image.fromarray(np.clip(arr * 255.0 + 0.5, 0, 255).astype(np.uint8), "RGB")
    image.thumbnail(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, (0, 0, 0))
    canvas.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
    return canvas


def build_sheet(root: Path, reference: str, settings: dict[str, float], pairs: list[tuple[str, np.ndarray, dict[str, np.ndarray]]], out_path: Path, edge: int) -> None:
    columns = ("Original", "AutoEdit trained", "Reference")
    tile = (360, 250)
    label_h = 34
    rows = min(36, len(pairs))
    sheet = Image.new("RGB", (tile[0] * len(columns), (tile[1] + label_h) * rows), (18, 18, 18))
    draw = ImageDraw.Draw(sheet)
    for row_i, (stem, original, _features_ref) in enumerate(pairs[:rows]):
        reference_path = _image_path(root / reference, stem)
        if reference_path is None:
            continue
        reference_arr = _open_array(reference_path, edge)
        edited = np.clip(apply_ops(original, settings), 0.0, 1.0)
        images = (original, edited, reference_arr)
        y = row_i * (tile[1] + label_h)
        for col_i, (label, arr) in enumerate(zip(columns, images)):
            x = col_i * tile[0]
            draw.text((x + 8, y + 9), f"{label} - {stem}" if col_i == 0 else label, fill=(238, 238, 238))
            sheet.paste(_tile(arr, tile), (x, y + label_h))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, quality=92)


def main() -> int:
    parser = argparse.ArgumentParser(description="Train/calibrate AutoEdit settings to a reference edit folder")
    parser.add_argument("eval_dir", type=Path)
    parser.add_argument("--reference", default="gemini_new_lightroom",
                        help="reference folder inside eval_dir")
    parser.add_argument("--target", default="all",
                        choices=["all", "hybrid_old_style_new_preservation", "old_cinematic", "new_lightroom", "needs_regen"])
    parser.add_argument("--samples", type=int, default=80,
                        help="number of image pairs to train on; 0 means all")
    parser.add_argument("--iterations", type=int, default=500)
    parser.add_argument("--edge", type=int, default=260)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--search-dehaze", action="store_true",
                        help="include expensive dehaze in the full random search")
    args = parser.parse_args()

    pairs = _load_pairs(args.eval_dir, args.reference, args.edge, args.samples, args.target, args.seed)
    if not pairs:
        raise SystemExit("no complete image pairs found")

    best, best_score = train(pairs, args.iterations, args.seed, args.search_dehaze)
    out_dir = args.eval_dir / "_review"
    safe_ref = args.reference.replace("/", "_")
    settings_path = out_dir / f"trained_to_{safe_ref}_settings.json"
    sheet_path = out_dir / f"trained_to_{safe_ref}_sheet.jpg"
    payload = {
        "reference": args.reference,
        "target": args.target,
        "samples": len(pairs),
        "iterations": args.iterations,
        "edge": args.edge,
        "search_dehaze": args.search_dehaze,
        "score": best_score,
        "settings": best,
        "note": "Feature-matched AutoEdit ops; no generated pixels or neural model training.",
    }
    settings_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    build_sheet(args.eval_dir, args.reference, best, pairs, sheet_path, args.edge)
    print(settings_path)
    print(sheet_path)
    print(json.dumps({"score": best_score, "settings": best}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
