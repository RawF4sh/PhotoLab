"""M4 aesthetic critic: train a scorer from a labels CSV, score/rank a folder, review priority."""

import csv
import os
import tempfile

import numpy as np
from PIL import Image

from autoedit import critic


def _corpus(n=8):
    d = tempfile.mkdtemp()
    rng = np.random.default_rng(0)
    names = []
    for i in range(n):
        a = (rng.uniform(0.1, 0.9, (48, 64, 3)) * 255).astype("uint8")
        nm = f"img_{i:02d}.jpg"
        Image.fromarray(a, "RGB").save(os.path.join(d, nm), quality=90)
        names.append(nm)
    return d, names


def _labels_csv(d, names):
    p = os.path.join(d, "labels.csv")
    with open(p, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["image", "score"])
        for i, nm in enumerate(names):                 # alternating high/low aesthetic scores (1..10)
            w.writerow([nm, 8.0 if i % 2 == 0 else 3.0])
    return p


def test_review_priority_peaks_at_boundary():
    assert critic.review_priority(0.5) == 1.0
    assert critic.review_priority(0.0) == 0.0
    assert critic.review_priority(1.0) == 0.0
    assert critic.review_priority(0.5) > critic.review_priority(0.8)


def test_train_from_csv_and_score_folder():
    d, names = _corpus(8)
    labels = _labels_csv(d, names)
    model_path = os.path.join(d, "model.json")
    info = critic.train_from_csv(labels, d, model_path)
    assert info["samples"] == 8 and os.path.isfile(model_path)

    ranked = critic.score_folder(d, model_path, recursive=False)
    assert ranked["count"] == 8
    scores = [it["score"] for it in ranked["items"]]
    assert scores == sorted(scores, reverse=True)        # best-first
    assert all(0.0 <= s <= 1.0 for s in scores)
    assert ranked["items"][0]["rank"] == 1
    assert all("priority" in it for it in ranked["items"])


def test_train_skips_unmatched_rows():
    d, names = _corpus(4)
    p = os.path.join(d, "labels.csv")
    with open(p, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["image", "score"])
        w.writerow([names[0], 7.0])
        w.writerow(["missing.jpg", 9.0])                 # no such file → skipped
        w.writerow([names[1], ""])                       # no score → skipped
    info = critic.train_from_csv(p, d, os.path.join(d, "m.json"))
    assert info["samples"] == 1 and info["skipped"] == 2
