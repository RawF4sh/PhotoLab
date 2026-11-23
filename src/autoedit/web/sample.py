"""Create local, generated demonstration images. No download or bundled photo rights needed."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from ..paths import default_profile_dir


def sample_folder(profile: str | None = None) -> str:
    folder = (Path(profile or default_profile_dir()) / "samples" / "landscapes-v1").resolve()
    folder.mkdir(parents=True, exist_ok=True)
    for index, (name, exposure, warmth) in enumerate((
            ("01-morning.jpg", 0.78, 0.0), ("02-warm-evening.jpg", 0.93, 0.12),
            ("03-cloudy-day.jpg", 0.64, -0.04))):
        path = folder / name
        if path.exists():
            continue  # reopening the sample does not replace originals or work
        w, h = 1200, 800
        y = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
        top = np.array([0.27, 0.49, 0.72], dtype=np.float32)
        bottom = np.array([0.86, 0.83, 0.69], dtype=np.float32)
        pixels = np.broadcast_to(top * (1-y) + bottom*y, (h, w, 3)).copy()
        scene = Image.fromarray(np.uint8(pixels * 255))
        cloud = Image.new("RGBA", (w, h))
        pen = ImageDraw.Draw(cloud)
        for x, cy, rx, ry in [(190, 130, 180, 35), (810, 200, 230, 45), (970, 110, 170, 28)]:
            pen.ellipse((x-rx, cy-ry, x+rx, cy+ry), fill=(250, 247, 238, 180))
        scene = Image.alpha_composite(scene.convert("RGBA"), cloud.filter(ImageFilter.GaussianBlur(14))).convert("RGB")
        pen = ImageDraw.Draw(scene)
        pen.polygon([(0, 470), (210, 290), (410, 455), (680, 190), (920, 430), (1200, 280),
                     (1200, 800), (0, 800)], fill=(106, 126, 130))
        pen.polygon([(510, 345), (680, 190), (800, 310), (725, 288), (695, 314), (645, 266),
                     (610, 329), (576, 308)], fill=(229, 230, 219))
        pen.polygon([(0, 545), (205, 470), (440, 560), (720, 440), (960, 530), (1200, 470),
                     (1200, 800), (0, 800)], fill=(76, 105, 66))
        pen.polygon([(400, 800), (430, 680), (665, 580), (1040, 555), (940, 650), (710, 800)],
                    fill=(81, 137, 154))
        for x, cy, size in [(90, 650, 65), (170, 615, 50), (1080, 690, 80), (1010, 625, 48)]:
            pen.rectangle((x-5, cy, x+5, cy+size), fill=(75, 64, 44))
            pen.polygon([(x, cy-size), (x-size//2, cy+30), (x+size//2, cy+30)], fill=(38, 71, 48))
        a = np.asarray(scene, dtype=np.float32) / 255
        noise = np.random.default_rng(54).normal(0, 0.004, (h, w, 1)).astype(np.float32)
        a = (a + noise) * exposure * np.array([1+warmth, 1, 1-warmth], dtype=np.float32)
        result = Image.fromarray(np.uint8(np.clip(a, 0, 1) * 255))
        exif = Image.Exif()
        exif[34665] = {36867: f"2026:01:01 10:00:{index:02d}"}
        exif[270] = "AutoEdit generated demo landscape; not a photograph"
        fd, temp = tempfile.mkstemp(dir=folder, suffix=".part")
        os.close(fd)
        try:
            result.save(temp, format="JPEG", quality=95, exif=exif)
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    return str(folder)
