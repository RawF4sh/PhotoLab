import csv
import json
import os

import numpy as np
from PIL import Image

from autoedit import aesthetic, curate
from autoedit.cli import main


def _write_rgb(path, rgb):
    arr = np.zeros((40, 54, 3), dtype=np.uint8)
    arr[:, :] = rgb
    Image.fromarray(arr, mode="RGB").save(path)


def test_aesthetic_model_predicts_higher_for_trained_positive():
    good = {
        "lum_mean": 0.48,
        "lum_std": 0.22,
        "lum_p10": 0.18,
        "lum_p90": 0.82,
        "warmth": 0.05,
        "green": 0.02,
        "blue_top": 0.08,
        "top_lum": 0.58,
        "dark_frac": 0.04,
        "bright_frac": 0.05,
        "snow_frac": 0.0,
        "aspect": 1.5,
        "edge_mean": 0.05,
        "edge_p90": 0.16,
    }
    bad = dict(good, lum_mean=0.10, lum_std=0.01, edge_p90=0.01, dark_frac=0.85)
    model = aesthetic.train([(good, 9.0), (bad, 2.0), (dict(good, warmth=-0.03), 8.0)])

    assert aesthetic.predict(good, model) > aesthetic.predict(bad, model)


def test_train_aesthetic_cli_and_curate_mode(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    _write_rgb(images / "bad.jpg", (15, 15, 15))
    textured = np.zeros((48, 64, 3), dtype=np.uint8)
    textured[:, :32] = (40, 90, 160)
    textured[:, 32:] = (230, 205, 150)
    Image.fromarray(textured, mode="RGB").save(images / "good.jpg")

    labels = tmp_path / "labels.csv"
    with open(labels, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["filename", "score"])
        writer.writeheader()
        writer.writerow({"filename": "bad.jpg", "score": "2"})
        writer.writerow({"filename": "good.jpg", "score": "9"})

    model = tmp_path / "aesthetic.json"
    rc = main(["train-aesthetic", str(labels), str(images), str(model)])

    assert rc == 0
    payload = json.loads(model.read_text())
    assert payload["metadata"]["samples"] == 2

    out = tmp_path / "curated"
    manifest = curate.curate(str(images), str(out), count=1, mode="aesthetic", aesthetic_model_path=str(model))

    assert manifest["mode"] == "aesthetic"
    assert manifest["items"][0]["source_path"].endswith("good.jpg")
    assert manifest["aesthetic_model_path"] == os.path.abspath(model)
