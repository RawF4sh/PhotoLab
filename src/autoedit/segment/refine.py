"""Bounded, source-coordinate brush corrections, before mask arbitration.

Corrections copy masks returned by the segmenter. They never mutate cached masks, generate a
missing object or bypass the ordinary confidence/coverage gates and overlap resolver.
"""

import math
from dataclasses import replace

import numpy as np
from PIL import Image as PILImage, ImageDraw

from ..concepts import meta_for

MAX_CONCEPTS, MAX_STROKES, MAX_TOTAL_STROKES, MAX_POINTS = 32, 100, 256, 8192
RADIUS_MIN, RADIUS_MAX = .002, .2


def name(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 120 or value != value.strip()
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or value in {"global", "__proto__", "constructor", "prototype"} or value.startswith(("mark:", "place:"))):
        raise ValueError("region name must be a concept of 1 to 120 characters, without a mark or place")
    return value


def names(value):
    if not isinstance(value, list) or len(value) > MAX_CONCEPTS:
        raise ValueError(f"selected_regions must contain at most {MAX_CONCEPTS} concepts")
    return list(dict.fromkeys(name(v) for v in value))


def _number(value, lo, hi, label):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError(f"{label} must be a finite number from {lo:g} to {hi:g}")
    return float(value)


def check(value):
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > MAX_CONCEPTS:
        raise ValueError(f"mask_refinements must contain at most {MAX_CONCEPTS} concepts")
    out, count, strokes = {}, 0, 0
    for concept, items in value.items():
        name(concept)
        if not isinstance(items, list) or len(items) > MAX_STROKES:
            raise ValueError(f"a mask takes at most {MAX_STROKES} strokes")
        strokes += len(items)
        if strokes > MAX_TOTAL_STROKES:
            raise ValueError(f"a photo takes at most {MAX_TOTAL_STROKES} refinement strokes")
        checked = []
        for item in items:
            if not isinstance(item, dict) or set(item) != {"mode", "radius", "points"}:
                raise ValueError("a refinement stroke requires mode, radius and points")
            if item["mode"] not in ("add", "subtract"):
                raise ValueError("refinement mode must be add or subtract")
            radius = _number(item["radius"], RADIUS_MIN, RADIUS_MAX, "brush radius")
            points = item["points"]
            if not isinstance(points, list) or not 1 <= len(points) <= 512:
                raise ValueError("a refinement stroke requires 1 to 512 points")
            count += len(points)
            if count > MAX_POINTS:
                raise ValueError(f"mask refinements take at most {MAX_POINTS} points per photo")
            pts = []
            for point in points:
                if not isinstance(point, (list, tuple)) or len(point) != 2:
                    raise ValueError("brush points must be [x, y] pairs")
                pts.append([_number(v, 0, 1, "brush coordinate") for v in point])
            checked.append({"mode": item["mode"], "radius": radius, "points": pts})
        if checked:
            out[concept] = checked
    return out


def apply(raw, refinements, meta, shape):
    """Checked strokes -> copied raw masks and human-readable decisions. Empty is a no-op."""
    if not refinements:
        return raw, []
    by_name = {mask.name: mask for mask in raw}
    replacements, notes = {}, []
    h, w = shape
    for concept, strokes in refinements.items():
        original, cm = by_name.get(concept), meta_for(concept, meta)
        if (original is None or not original.mask.any() or original.score < cm.min_score
                or float(original.mask.mean()) < cm.min_coverage):
            notes.append(f"selection correction: {concept} was not found; no mask was invented")
            continue
        mask = PILImage.fromarray(original.mask.astype(np.uint8) * 255, mode="L")
        draw = ImageDraw.Draw(mask)
        for stroke in strokes:
            radius = max(.5, stroke["radius"] * min(h, w))
            points = [(x * (w - 1), y * (h - 1)) for x, y in stroke["points"]]
            fill = 255 if stroke["mode"] == "add" else 0
            if len(points) > 1:
                draw.line(points, fill=fill, width=max(1, round(radius * 2)), joint="curve")
            for x, y in (points[0], points[-1]):
                draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=fill)
        arr = np.array(mask) > 0
        replacements[concept] = replace(original, mask=arr, coverage=float(arr.mean()))
        changed = float(np.mean(arr != original.mask))
        notes.append(f"selection correction: {concept}, {len(strokes)} brush strokes changed {changed * 100:.2f}% "
                     "of the source frame before overlap arbitration")
    return [replacements.get(mask.name, mask) for mask in raw], notes
