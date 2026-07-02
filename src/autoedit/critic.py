"""Aesthetic critic (M4) — a reusable finished-photo SCORER, never an editor.

Wraps the lightweight aesthetic model (aesthetic.py) for the jobs the roadmap calls for: rank a
folder of photos (culling / variant ranking), train a model from an AVA/Flickr-style labels CSV,
and flag uncertain images for human review. Pure measurement on image features — no pixels are
changed. Degrades safely: a missing/invalid model just means callers fall back to heuristics.
"""

from __future__ import annotations

import csv
import os

from . import aesthetic, curate


def _row_path(row: dict, images_dir: str, path_column: str | None) -> str | None:
    cols = [path_column] if path_column else [
        "path", "filepath", "file_path", "file", "filename", "image", "image_name", "image_id", "id"]
    for col in cols:
        if not col or col not in row or not row[col]:
            continue
        value = row[col].strip()
        cands = [value]
        if not os.path.splitext(value)[1]:
            cands += [f"{value}.jpg", f"{value}.jpeg", f"{value}.JPG"]
        for c in cands:
            p = c if os.path.isabs(c) else os.path.join(images_dir, c)
            if os.path.exists(p):
                return p
    return None


def _row_score(row: dict, score_column: str | None) -> float | None:
    if score_column:
        try:
            return float(row[score_column])
        except (KeyError, TypeError, ValueError):
            return None
    for col in ("score", "mean_score", "rating", "aesthetic_score", "mos"):
        if row.get(col):
            try:
                return float(row[col])
            except ValueError:
                pass
    if all(row.get(str(i)) for i in range(1, 11)):          # AVA rating distribution → mean score
        try:
            counts = [float(row[str(i)]) for i in range(1, 11)]
        except ValueError:
            return None
        total = sum(counts)
        if total > 0:
            return sum((i + 1) * counts[i] for i in range(10)) / total
    return None


def train_from_csv(labels_csv: str, images_dir: str, out_model: str, *, path_column: str | None = None,
                   score_column: str | None = None, ridge: float = 1e-3, long_edge: int = 256,
                   limit: int | None = None) -> dict:
    """Train + save an aesthetic model JSON from a labels CSV (AVA/Flickr or simple path,score)."""
    samples, skipped = [], 0
    with open(labels_csv, newline="") as fh:
        for i, row in enumerate(csv.DictReader(fh)):
            if limit is not None and i >= limit:
                break
            p, s = _row_path(row, images_dir, path_column), _row_score(row, score_column)
            if not p or s is None:
                skipped += 1
                continue
            try:
                samples.append((curate.image_features(p, long_edge=long_edge), s))
            except Exception:
                skipped += 1
    if not samples:
        raise ValueError("no training samples matched labels to readable images")
    model = aesthetic.train(samples, ridge=ridge)
    aesthetic.save_model(model, out_model, metadata={
        "labels_csv": os.path.abspath(labels_csv), "images_dir": os.path.abspath(images_dir),
        "samples": len(samples), "skipped": skipped})
    return {"model_path": os.path.abspath(out_model), "samples": len(samples), "skipped": skipped}


def review_priority(score: float) -> float:
    """0..1 — highest near the 0.5 decision boundary (most uncertain → send to human review),
    lowest for clearly good or clearly bad photos."""
    return max(0.0, 1.0 - abs(float(score) - 0.5) * 2.0)


def score_folder(folder: str, model_path: str, recursive: bool = True, limit: int | None = None) -> dict:
    """Score + rank every image in a folder with a trained model. Returns items sorted best-first,
    each with score (0..1) and review priority."""
    model = aesthetic.load_model(model_path)
    paths = curate.list_images(folder, recursive=recursive)
    if limit:
        paths = paths[:limit]
    items = []
    skipped = 0
    for p in paths:
        try:
            s = aesthetic.predict(curate.image_features(p), model)
        except Exception:
            skipped += 1                     # unreadable image — counted, not silently invisible
            continue
        items.append({"path": os.path.abspath(p), "score": round(float(s), 4),
                      "priority": round(review_priority(s), 4)})
    items.sort(key=lambda d: d["score"], reverse=True)
    for i, it in enumerate(items):
        it["rank"] = i + 1
    return {"model": os.path.abspath(model_path), "count": len(items), "skipped": skipped,
            "items": items}
