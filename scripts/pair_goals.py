"""Measure aligned before/after pairs. One photograph contributes one observation per region."""
from __future__ import annotations

import math

import numpy as np
from PIL import Image as PILImage

from autoedit.edit import style_target
from autoedit.edit.oklab import linear_to_oklab
from autoedit.edit.ops import OP_BOUNDS, apply_ops
from autoedit.imageio import Image, srgb_to_linear

SCENES = ("sunset", "twilight", "daylight", "mist")
REGIONS = ("sky", "land", "water", "mountain")
CONCEPTS = ("sky", "cloud", "water", "mountain", "grass", "trees", "rock", "snow", "skin", "face", "person")
MIN_PAIRS = 45


def split_pair(rgb, layout):
    """The centre pixel of an odd-width/height composite may be its divider."""
    if layout == "horizontal":
        n = rgb.shape[1] // 2
        return rgb[:, :n], rgb[:, -n:]
    if layout == "vertical":
        n = rgb.shape[0] // 2
        return rgb[:n], rgb[-n:]
    raise ValueError("a composite needs a horizontal or vertical layout")


def resize(rgb, size):
    if rgb.shape[1::-1] == size:
        return rgb
    # Keep 16-bit/float precision when measuring RAW and TIFF pairs.
    return np.stack([np.asarray(PILImage.fromarray(channel).resize(size, PILImage.Resampling.LANCZOS))
                     for channel in np.moveaxis(rgb, -1, 0)], axis=-1).clip(0, 1)


def align(before, after, max_edge=512, radius=14):
    """Match normalised edge maps, as in the Auto prototype. Return only shared pixels.

    Both views are resized to the same proxy without enlarging the smaller image. If
    a truly flat image gives the same score at several shifts, keep the smallest.
    """
    bh, bw = before.shape[:2]
    ah, aw = after.shape[:2]
    if min(bh, bw, ah, aw) < 8 or abs((bw * ah) / (aw * bh) - 1) > .025:
        raise ValueError("pair is too small or its aspect ratios differ; check crop and layout")
    factor = min(1., max_edge / max(bh, bw), ah / bh, aw / bw)
    size = (round(bw * factor), round(bh * factor))
    before, after = resize(before, size), resize(after, size)

    def edges(rgb):
        lum = rgb.mean(axis=2)
        return abs(np.diff(lum, axis=0)[:, :-1]) + abs(np.diff(lum, axis=1)[:-1])

    left, right = edges(before), edges(after)
    h, w = left.shape
    r = min(radius, min(h, w) // 6)
    if not r:
        return before, after, (0, 0)
    # Stride only the score samples. Offsets and the returned overlap are pixel-accurate.
    stride = max(1, min(h, w) // 100)
    ref = right[r:h-r:stride, r:w-r:stride]
    ref = ref / (ref.mean() + 1e-6)
    best = None
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            patch = left[r+dy:h-r+dy:stride, r+dx:w-r+dx:stride]
            error = float(abs(patch / (patch.mean() + 1e-6) - ref).mean())
            key = (error, dx*dx + dy*dy, dy, dx)
            if best is None or key < best:
                best = key
    _, _, dy, dx = best
    h, w = before.shape[:2]
    # Coordinates of the shift are relative to 'after'.
    y, x = max(-dy, 0), max(-dx, 0)
    rows, cols = h - abs(dy), w - abs(dx)
    return (before[y+dy:y+dy+rows, x+dx:x+dx+cols],
            after[y:y+rows, x:x+cols], (dy, dx))


def infer_controls(before, after, mask=None):
    """Estimate magnitudes in actual slider units by replaying bounded controls on a proxy.

    A change in median luma is not an EV value. Fit directly to matching pixels, keeping
    controls at zero when they make no improvement. This estimates an equivalent edit;
    exported web images do not identify an editor's original settings uniquely.
    """
    stride = max(1, math.ceil(max(before.shape[:2]) / 72))
    source, target = before[::stride, ::stride], after[::stride, ::stride]
    sample = mask[::stride, ::stride] if mask is not None else None
    if sample is not None and not sample.any():
        return {}

    def error(settings):
        diff = np.clip(apply_ops(source, settings), 0, 1) - target
        return float(np.mean(np.square(diff if sample is None else diff[sample])))

    controls = tuple(dict.fromkeys(("exposure", *style_target.CONTROLS.values())))
    def solve_one(settings, best, op):
        lo, hi = OP_BOUNDS[op]
        for fraction in (.5, .25, .125, .0625, .03125, .015625, .0078125):
            previous = settings.get(op, 0.)
            step = (hi-lo) * fraction
            for sign in (1, -1):
                value = float(np.clip(previous + sign * step, lo, hi))
                if value == previous:
                    continue
                candidate = {**settings, op: value}
                loss = error(candidate)
                if loss + 1e-10 < best:
                    settings, best = candidate, loss
                    break
        return settings, best

    # Find the best single-control explanation first. A pure tint or exposure
    # move should recover that control, rather than a chain of compensating ops.
    initial = error({})
    if initial <= 1e-10:
        return {}
    settings, best = min((solve_one({}, initial, op) for op in controls), key=lambda fit: fit[1])
    for _ in range(3):
        start = best
        if best < 1e-10:
            break
        for op in controls:
            settings, best = solve_one(settings, best, op)
        if best == start:
            break
    return settings


def measure_pair(before, after, masks):
    """Use the same mask on both sides. Never substitute whole-image stats for a missing region."""
    distance = np.linalg.norm(linear_to_oklab(srgb_to_linear(after)) -
                              linear_to_oklab(srgb_to_linear(before)), axis=2)
    regions = {}
    for region, mask in {"global": None, **masks}.items():
        if mask is not None and mask.sum() < max(16, mask.size * .002):
            continue
        a, b = style_target.features(before, mask), style_target.features(after, mask)
        regions[region] = {"before": a, "after": b, "change": {k: b[k] - a[k] for k in a},
                           "controls": infer_controls(before, after, mask)}
    return {"distance": float(distance.mean() * 100), "regions": regions}


def masks_for(before, pipe):
    image = Image(rgb=before)
    masks = {}

    def collect(resolved):
        for region in resolved.regions:
            if region.name in ("water", "mountain"):
                masks[region.name] = region.mask
        skies = [resolved.union[k] for k in ("sky", "cloud") if k in resolved.union]
        protected = [resolved.union[k] for k in ("water", "snow", "skin", "face", "person") if k in resolved.union]
        if skies:
            masks["sky"] = np.logical_or.reduce(skies)
            masks["land"] = ~np.logical_or.reduce([masks["sky"], *protected])
        else:
            terrestrial = [r.mask for r in resolved.regions if r.name in ("mountain", "grass", "trees", "rock")]
            if terrestrial:
                land = np.logical_or.reduce(terrestrial)
                masks["land"] = land & ~np.logical_or.reduce(protected) if protected else land

    pipe.process_one("pair-proxy", image=image, proxy=image, do_apply=False, on_resolved=collect)
    return masks


def summarize(samples, scene, *, middle=False):
    if len(samples) < MIN_PAIRS:
        raise ValueError(f"{scene} needs at least {MIN_PAIRS} distinct screened pairs, got {len(samples)}")
    ordered = sorted(samples, key=lambda s: s["distance"])
    third = len(ordered) // 3
    selected = ordered[third:2*third] if middle else ordered[-third:]
    target = {"meta": {"photos": len(samples), "selected": third, "region_counts": {}}}
    limits = {}
    for region in ("global", *REGIONS):
        values = [s["regions"][region] for s in selected if region in s["regions"]]
        if not values:
            continue
        target["meta"]["region_counts"][region] = len(values)
        target[region] = {feature: [round(float(n), 6) for n in np.percentile(
            [value["after"][feature] for value in values], [10, 90])]
            for feature in values[0]["after"] if feature in style_target.CONTROLS}
    # Limits are magnitudes, one vote per photograph across all its regions, not
    # one vote per mask (which would overweight photos with many detections).
    for op in dict.fromkeys(style_target.CONTROLS.values()):
        amounts = [max(abs(r["controls"].get(op, 0.)) for r in s["regions"].values()) for s in selected]
        limits[op] = round(float(np.percentile(amounts, 90)), 6)
    return target, limits
