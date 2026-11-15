"""Shoot-level consistency (Codex review F5).

A batch shouldn't edit every photo in a vacuum — adjacent shots of the same scene should land
at a coherent brightness instead of drifting per-image on segmentation/stat noise. This is a
cheap two-pass anchor: pass 1 measures each image's brightness, clusters images into lighting
groups, and computes a per-group target; pass 2 nudges each image a BOUNDED amount toward its
group target (the per-scene look still dominates — this only removes drift).

Kept deliberately light: grouping uses brightness only (no extra segmentation). Time/EXIF/WB
clustering is a future upgrade; the data structure already carries a group id per image.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from PIL import Image as PILImage
from PIL import ImageOps

from .decide import RulesDecider
from .imageio import camera_preview, downscale_long_edge, is_raw, load_image, luma, register_reader

# A pixel-brightness gap wider than this starts a new lighting group (0..1 luma).
_GROUP_GAP = 0.08
# Max brightness nudge toward the group. UNITS: this is a display-referred luma delta (0..1)
# fed to the exposure op as if it were EV — not true photometric stops. At this magnitude the
# approximation is fine (it only removes drift), but don't read the value as camera EV.
_MAX_BIAS_EV = 0.15


def quick_lum(path: str, px: int = 384) -> float:
    """Cheap mean luminance from a small proxy — no segmentation (pass-1 signal)."""
    proxy = downscale_long_edge(load_image(path), px)
    return float(luma(proxy.rgb).mean())


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def plan_groups(lums: list[tuple[str, float]], gap: float = _GROUP_GAP) -> dict[str, tuple[str, float]]:
    """Cluster images by brightness (1-D split on gaps). Returns path -> (group_id, target_lum)."""
    if not lums:
        return {}
    order = sorted(lums, key=lambda t: t[1])
    groups: list[list[tuple[str, float]]] = [[order[0]]]
    for item in order[1:]:
        if item[1] - groups[-1][-1][1] > gap:
            groups.append([item])
        else:
            groups[-1].append(item)
    out: dict[str, tuple[str, float]] = {}
    for gi, grp in enumerate(groups, start=1):
        target = _median([lm for _p, lm in grp])
        gid = f"g{gi}"
        for path, _lm in grp:
            out[path] = (gid, target)
    return out


def exposure_bias(image_lum: float, target_lum: float) -> float:
    """Bounded exposure nudge toward the group target — a luma delta used as an EV
    approximation (see _MAX_BIAS_EV note on units)."""
    return float(np.clip(target_lum - image_lum, -_MAX_BIAS_EV, _MAX_BIAS_EV))


@dataclass
class Bias:
    """What one image is moved by, and the group it belongs to."""
    group: str = ""
    exposure: float = 0.0
    temp: float = 0.0
    tint: float = 0.0


# Fallbacks when rules.toml has no [consistency] block; the tuning values live there.
BURST_DEFAULTS = {"max_gap_s": 30.0, "max_span_s": 120.0, "min_similarity": 0.8, "max_ev": 0.5, "max_wb": 4.0}


def capture_time(path: str) -> float | None:
    """EXIF DateTimeOriginal as seconds, or None."""
    try:
        register_reader(path)       # a HEIC photo has a capture time only with its reader registered
        with PILImage.open(path) as pil:
            stamp = pil.getexif().get_ifd(0x8769).get(0x9003)
        return datetime.strptime(str(stamp)[:19], "%Y:%m:%d %H:%M:%S").timestamp()
    except Exception:
        return None


def thumb_signature(path: str) -> np.ndarray | None:
    """What a frame looks like, whatever its exposure: a small grey thumbnail (24 by 16, or 16 by
    24 for an upright frame) from which every row's own mean is removed, scaled to unit variance.

    The row means go because nearly every outdoor photo is bright sky over darker ground: with
    them in, different pictures shot seconds apart correlated above 0.9 and were taken for one
    burst (seen on a real shoot: a hut, then a meadow). Without them the same pair is at 0.2 to
    0.6 and a true repeat of one framing stays above 0.9. An upright and a level frame get
    thumbnails of different shape and never match.

    Made the cheap way, because a shoot can need hundreds: a JPEG is decoded at reduced size, a
    RAW file gives its embedded preview, and only a RAW file without one is developed (at
    quarter size)."""
    try:
        grey = None
        if is_raw(path):
            preview = camera_preview(path, max_px=96)
            if preview is not None:
                grey = luma(preview)
        elif path.lower().endswith((".jpg", ".jpeg")):
            with PILImage.open(path) as pil:
                pil.draft("L", (192, 128))      # the JPEG decoder itself scales by 2, 4 or 8
                grey = np.asarray(ImageOps.exif_transpose(pil).convert("L"), dtype=np.float32) / 255.0
        if grey is None:
            grey = luma(downscale_long_edge(load_image(path, {"half_size": True}), 96).rgb)
        small = PILImage.fromarray((np.clip(grey, 0, 1) * 255).astype(np.uint8))
        size = (24, 16) if grey.shape[1] >= grey.shape[0] else (16, 24)
        g = np.asarray(small.resize(size, PILImage.BILINEAR), dtype=np.float32)
    except Exception:
        return None
    g = g - g.mean(axis=1, keepdims=True)
    return g / (g.std() + 1e-6)


def find_bursts(paths: list[str], max_gap_s: float, min_similarity: float,
                max_span_s: float | None = None) -> list[list[str]]:
    """Runs of consecutive shots (by capture time) that are close in time and look alike. A shot
    more than `max_span_s` after the run's first shot starts a new run (without it a slow chain
    of near frames would flatten a real change of light). Only runs of two or more shots are
    returned, as lists of files.

    A RAW file and the JPEG the camera wrote with it are ONE shot (same capture second, one of
    each kind): by themselves they are not a burst. On a shoot kept as RAW plus JPEG, counting
    them as two frames made nearly every photo a burst photo (measured: 758 of 800)."""
    timed = sorted(((t, p) for p in paths if (t := capture_time(p)) is not None))
    shots: list[tuple[float, list[str]]] = []
    for t, p in timed:
        last = shots[-1] if shots else None
        if last and last[0] == t and len(last[1]) == 1 and is_raw(last[1][0]) != is_raw(p):
            last[1].append(p)
        else:
            shots.append((t, [p]))
    # A thumbnail costs a decode, so only a shot with a neighbour close enough in time gets one,
    # and it is made from the shot's JPEG when there is one.
    near = [i for i in range(len(shots))
            if (i and shots[i][0] - shots[i - 1][0] <= max_gap_s)
            or (i + 1 < len(shots) and shots[i + 1][0] - shots[i][0] <= max_gap_s)]
    sigs = {i: thumb_signature(sorted(shots[i][1], key=is_raw)[0]) for i in near}
    bursts, cur = [], []
    for i, (t, _files) in enumerate(shots):
        if cur and sigs.get(i) is not None and sigs.get(cur[-1]) is not None \
                and t - shots[cur[-1]][0] <= max_gap_s \
                and (max_span_s is None or t - shots[cur[0]][0] <= max_span_s) \
                and sigs[i].shape == sigs[cur[-1]].shape \
                and float((sigs[i] * sigs[cur[-1]]).mean()) >= min_similarity:
            cur.append(i)
        else:
            if len(cur) > 1:
                bursts.append([f for j in cur for f in shots[j][1]])
            cur = [i]
    if len(cur) > 1:
        bursts.append([f for j in cur for f in shots[j][1]])
    return bursts


def burst_biases(decided: list[dict], max_ev: float, max_wb: float) -> list[tuple[float, float, float]]:
    """Per frame of one burst: (exposure, temp, tint) bias toward the burst's median. `decided`
    holds, per frame: fg_p50, exposure, shadows_ev, temp, tint, headroom_ev."""
    level = [2.2 * math.log2(max(d["fg_p50"], 1e-3)) + d["exposure"] + 0.5 * d["shadows_ev"] for d in decided]
    mid, t_mid, n_mid = _median(level), _median([d["temp"] for d in decided]), _median([d["tint"] for d in decided])
    out = []
    for d, lv in zip(decided, level):
        ev = max(-max_ev, min(max_ev, mid - lv))
        ev = max(ev, -max(d["exposure"], 0.0))                                   # cancel a lift, never cut
        ev = min(ev, max(0.0, d["headroom_ev"] - 0.15 - d["exposure"]))          # never into clipping
        out.append((ev, max(-max_wb, min(max_wb, t_mid - d["temp"])), max(-max_wb, min(max_wb, n_mid - d["tint"]))))
    return out


def _brightness_plan(images: list[str]) -> dict[str, Bias]:
    lums: dict[str, float] = {}
    for path in images:
        try:
            lums[path] = quick_lum(path)
        except Exception:
            continue
    groups = plan_groups(list(lums.items()))
    return {p: Bias(group=gid, exposure=exposure_bias(lums[p], target)) for p, (gid, target) in groups.items()}


def _bursts_plan(images: list[str], pipeline, thr: dict, progress=None) -> dict[str, Bias]:
    # The plan decides with the rules only: the LLM decider would be called for every burst frame a
    # second time (the real run calls it again) and the plan would use up the call budget.
    decider, pipeline.decider = pipeline.decider, RulesDecider()
    try:
        return _burst_plan(images, pipeline, thr, progress)
    finally:
        pipeline.decider = decider


def _burst_plan(images: list[str], pipeline, thr: dict, progress=None) -> dict[str, Bias]:
    out: dict[str, Bias] = {}
    n = 0
    bursts = find_bursts(images, thr["max_gap_s"], thr["min_similarity"], thr["max_span_s"])
    total, done = sum(len(b) for b in bursts), 0
    if progress:
        progress(f"{len(bursts)} bursts with {total} photos; deciding each of those photos once")
    for burst in bursts:
        paths, decided = [], []
        for p in burst:
            done += 1
            if progress and done % 25 == 0:
                progress(f"decided {done} of {total}")
            try:
                _rgb, r = pipeline.process_one(p, do_apply=False)
                gs, st = r.global_settings, r.global_stats
                decided.append({"fg_p50": st.fg_p50, "headroom_ev": st.headroom_ev,
                                "exposure": gs.get("exposure", 0.0), "shadows_ev": gs.get("shadows_ev", 0.0),
                                "temp": gs.get("temp", 0.0), "tint": gs.get("tint", 0.0)})
                paths.append(p)
            except Exception:
                continue    # a frame that cannot be measured is left out of its burst
        if len(paths) < 2:
            continue
        n += 1
        for p, (ev, temp, tint) in zip(paths, kind_biases(paths, decided, thr["max_ev"], thr["max_wb"])):
            out[p] = Bias(group=f"b{n}", exposure=ev, temp=temp, tint=tint)
    return out


def kind_biases(paths: list[str], decided: list[dict], max_ev: float,
                max_wb: float) -> list[tuple[float, float, float]]:
    """`burst_biases` for the RAW files of a burst among themselves and for the other files among
    themselves. A RAW decode starts about a stop and a half below the camera's JPEG of the same
    shot, so one median for both would spend the whole nudge on that gap (measured: it pulled
    JPEGs down and RAW files up by the full 0.5 EV) instead of on the drift inside the burst. A
    kind with a single file in the burst is not moved."""
    out = [(0.0, 0.0, 0.0)] * len(paths)
    for raw in (True, False):
        idx = [k for k, p in enumerate(paths) if is_raw(p) == raw]
        if len(idx) >= 2:
            for k, b in zip(idx, burst_biases([decided[k] for k in idx], max_ev, max_wb)):
                out[k] = b
    return out


def plan(images: list[str], cfg, pipeline, thr: dict | None = None, progress=None) -> dict[str, Bias]:
    """The consistency bias of every image, for the mode in `cfg.consistency`.
    "brightness": brightness clusters, a nudge of at most 0.15.
    "bursts": frames of one framing shot close together are brought to one level and one white
    balance. A photo that fails to measure simply gets no bias. `progress`, when given, is called
    with a short line of text now and then: the bursts plan decodes and decides every burst
    photo once before the first render, which takes minutes on a large shoot."""
    if len(images) < 2:
        return {}
    if cfg.consistency == "bursts":
        if thr is None:
            thr = (getattr(pipeline, "rules", None) or {}).get("consistency", {})
        return _bursts_plan(images, pipeline, {**BURST_DEFAULTS, **thr}, progress)
    return _brightness_plan(images)
