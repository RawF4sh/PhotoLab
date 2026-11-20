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

import json
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
BURST_DEFAULTS = {"max_gap_s": 30.0, "max_span_s": 120.0, "min_similarity": 0.8, "max_ev": 0.5, "max_wb": 4.0,
                  "max_wb_cast": 8.0, "wb_min_confidence": 0.3, "colour_of": ["subject", "person"],
                  "timelapse_window": 9, "timelapse_max_ev": 0.5, "timelapse_step_ev": 0.75}


def capture_time(path: str) -> float | None:
    """EXIF DateTimeOriginal as seconds, or None."""
    try:
        register_reader(path)       # a HEIC photo has a capture time only with its reader registered
        with PILImage.open(path) as pil:
            stamp = pil.getexif().get_ifd(0x8769).get(0x9003)
        return datetime.strptime(str(stamp)[:19], "%Y:%m:%d %H:%M:%S").timestamp()
    except Exception:
        return None


def thumb_grey(path: str) -> np.ndarray | None:
    """The 24 by 16 (or 16 by 24) grey thumbnail of a frame, 0 to 255, or None.

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
    return g


def signature(g: np.ndarray) -> np.ndarray:
    """A grey thumbnail with every row's own mean removed, scaled to unit variance (why the row
    means go: see `thumb_signature`)."""
    g = g - g.mean(axis=1, keepdims=True)
    return g / (g.std() + 1e-6)


def thumb_signature(path: str) -> np.ndarray | None:
    """What a frame looks like, whatever its exposure: `signature(thumb_grey(path))`.

    The row means go because nearly every outdoor photo is bright sky over darker ground: with
    them in, different pictures shot seconds apart correlated above 0.9 and were taken for one
    burst (seen on a real shoot: a hut, then a meadow). Without them the same pair is at 0.2 to
    0.6 and a true repeat of one framing stays above 0.9."""
    g = thumb_grey(path)
    return None if g is None else signature(g)


def find_bursts(paths: list[str], max_gap_s: float, min_similarity: float,
                max_span_s: float | None = None, alike=None) -> list[list[str]]:
    """Runs of consecutive shots (by capture time) that are close in time and look alike. A shot
    more than `max_span_s` after the run's first shot starts a new run (without it a slow chain
    of near frames would flatten a real change of light). Only runs of two or more shots are
    returned, as lists of files.

    A RAW file and the JPEG the camera wrote with it are ONE shot (same capture second, one of
    each kind): by themselves they are not a burst. On a shoot kept as RAW plus JPEG, counting
    them as two frames made nearly every photo a burst photo (measured: 758 of 800).

    `alike(file_a, file_b)`, when given, is a second score tried for a pair whose thumbnails fall
    short (merge uses it to compare frames of a bracket at one exposure)."""
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
    seen = {i: sorted(shots[i][1], key=is_raw)[0] for i in near}     # the file a shot is judged by
    sigs = {i: thumb_signature(p) for i, p in seen.items()}

    def score(i: int, j: int) -> float:
        s = float((sigs[i] * sigs[j]).mean())
        return s if alike is None or s >= min_similarity else max(s, alike(seen[j], seen[i]))

    bursts, cur = [], []
    for i, (t, _files) in enumerate(shots):
        if cur and sigs.get(i) is not None and sigs.get(cur[-1]) is not None \
                and t - shots[cur[-1]][0] <= max_gap_s \
                and (max_span_s is None or t - shots[cur[0]][0] <= max_span_s) \
                and sigs[i].shape == sigs[cur[-1]].shape \
                and score(i, cur[-1]) >= min_similarity:
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


def timelapse_biases(lums: list[float], window: int, max_ev: float, step_ev: float = 0.75) -> list[float]:
    """Per frame, in order: the exposure bias in EV that moves its luminance to the running median of its neighbours
    (`window` frames centred on it, shrunk at the ends, along the window's slope), clamped to +-max_ev. Luminance is compared in log2 (EV).
    A real sudden change of light is kept: when inside a frame's window the median EV of the frames before it and of the
    frames after it differ by more than `step_ev`, only the frames on the frame's own side of the step are used."""
    ev = [math.log2(max(v, 1e-6)) for v in lums]
    half = max(window, 1) // 2
    # Each neighbour is carried to this frame along the window's own fitted slope, so a slow ramp is
    # not mistaken for flicker, also in the lopsided windows at the ends.
    out = []
    for i, e in enumerate(ev):
        lo, hi = max(0, i - half), min(len(ev), i + half + 1)
        if lo < i < hi - 1:
            before, after = _median(ev[lo:i]), _median(ev[i + 1:hi])
            if abs(after - before) > step_ev:      # a step inside the window: stay on this frame's side of it
                lo, hi = (lo, i + 1) if abs(e - before) <= abs(e - after) else (i, hi)
        # Theil-Sen slope (median of the pairwise slopes): a single frame across the edge of a step cannot tilt it.
        k = _median([(ev[b] - ev[a]) / (b - a) for a in range(lo, hi) for b in range(a + 1, hi)]) if hi - lo > 1 else 0.0
        out.append(max(-max_ev, min(max_ev, _median([ev[j] + k * (i - j) for j in range(lo, hi)]) - e)))
    return out


def _timelapse_plan(images: list[str], pipeline, thr: dict) -> dict[str, Bias]:
    """Frames in capture order (file name when any has no time), each evened on what it will look like once edited.
    RAW files and the other files are evened apart, as `kind_biases` does for bursts: a RAW decode sits about a stop and
    a half below the camera JPEG, which is no flicker."""
    times = [capture_time(p) for p in images]
    order = sorted(images, key=lambda p: (times[images.index(p)], p)) if None not in times else sorted(images)
    got = [(p, d) for p in order if (d := _decided(pipeline, p)) is not None]
    out = {}
    for raw in (True, False):
        part = [(p, d) for p, d in got if is_raw(p) == raw]
        if len(part) < 2:
            continue
        # The same level `burst_biases` compares (decided exposure included), as a luminance so the log2 gives it back.
        lums = [2.0 ** (2.2 * math.log2(max(d["fg_p50"], 1e-3)) + d["exposure"] + 0.5 * d["shadows_ev"]) for _p, d in part]
        for (p, d), ev in zip(part, timelapse_biases(lums, int(thr["timelapse_window"]), thr["timelapse_max_ev"],
                                                     float(thr["timelapse_step_ev"]))):
            ev = min(ev, max(0.0, d["headroom_ev"] - 0.15 - d["exposure"]))      # never into clipping, as bursts
            out[p] = Bias(group="t1", exposure=ev)
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


def _with_rules(pipeline, fn):
    # The plan decides with the rules only: the LLM decider would be called for every photo a second
    # time (the real run calls it again) and the plan would use up the call budget.
    decider, pipeline.decider = pipeline.decider, RulesDecider()
    try:
        return fn()
    finally:
        pipeline.decider = decider


def _decided(pipeline, path: str) -> dict | None:
    """What the rules decided for one photo (one `process_one`, no pixels), or None when it cannot be measured."""
    try:
        _rgb, r = pipeline.process_one(path, do_apply=False)
        gs, st = r.global_settings, r.global_stats
        return {"fg_p50": st.fg_p50, "headroom_ev": st.headroom_ev,
                "exposure": gs.get("exposure", 0.0), "shadows_ev": gs.get("shadows_ev", 0.0),
                "temp": gs.get("temp", 0.0), "tint": gs.get("tint", 0.0),
                "wb_temp": st.wb_temp_correction, "wb_tint": st.wb_tint_correction, "wb_conf": st.wb_confidence}
    except Exception:
        return None


def cast_biases(decided: list[dict], max_wb: float, min_confidence: float) -> list[tuple[float, float]]:
    """Per frame of one group: the (temp, tint) bias that brings its colour cast to the group's median. Frames whose
    reference is weaker than `min_confidence` are not moved and do not vote; fewer than two voters move nothing."""
    left = [(d["wb_temp"] - d["temp"], d["wb_tint"] - d["tint"]) if d["wb_conf"] >= min_confidence else None
            for d in decided]
    votes = [v for v in left if v is not None]
    if len(votes) < 2:
        return [(0.0, 0.0)] * len(decided)
    t_mid, n_mid = _median([v[0] for v in votes]), _median([v[1] for v in votes])
    return [(0.0, 0.0) if v is None else
            (max(-max_wb, min(max_wb, v[0] - t_mid)), max(-max_wb, min(max_wb, v[1] - n_mid))) for v in left]


def _cast_plan(out: dict[str, Bias], pipeline, thr: dict, progress=None) -> dict[str, Bias]:
    """Mode brightness with `consistency_wb`: each group (RAW files and the others apart) gets one white balance."""
    parts: dict[tuple[str, bool], list[str]] = {}
    for p, b in out.items():
        if b.group:
            parts.setdefault((b.group, is_raw(p)), []).append(p)
    parts = {k: v for k, v in parts.items() if len(v) >= 2}
    if progress and parts:
        progress(f"white balance: deciding {sum(len(v) for v in parts.values())} photos once before the first render")
    for paths in parts.values():
        got = [(p, d) for p in paths if (d := _decided(pipeline, p)) is not None]
        for (p, _d), (temp, tint) in zip(got, cast_biases([d for _p, d in got], thr["max_wb_cast"],
                                                          thr["wb_min_confidence"])):
            out[p].temp, out[p].tint = temp, tint
    return out


def _bursts_plan(images: list[str], pipeline, thr: dict, progress=None, by_cast: bool = False) -> dict[str, Bias]:
    return _with_rules(pipeline, lambda: _burst_plan(images, pipeline, thr, progress, by_cast))


def _burst_plan(images: list[str], pipeline, thr: dict, progress=None, by_cast: bool = False) -> dict[str, Bias]:
    out: dict[str, Bias] = {}
    n = 0
    bursts = find_bursts(images, thr["max_gap_s"], thr["min_similarity"], thr["max_span_s"])
    total, done = sum(len(b) for b in bursts), 0
    if progress:
        progress(f"{len(bursts)} bursts with {total} photos; deciding each of those photos once")
    cast = (thr["max_wb_cast"], thr["wb_min_confidence"]) if by_cast else None
    for burst in bursts:
        paths, decided = [], []
        for p in burst:
            done += 1
            if progress and done % 25 == 0:
                progress(f"decided {done} of {total}")
            d = _decided(pipeline, p)
            if d is not None:       # a frame that cannot be measured is left out of its burst
                decided.append(d)
                paths.append(p)
        if len(paths) < 2:
            continue
        n += 1
        for p, (ev, temp, tint) in zip(paths, kind_biases(paths, decided, thr["max_ev"], thr["max_wb"], cast)):
            out[p] = Bias(group=f"b{n}", exposure=ev, temp=temp, tint=tint)
    return out


def kind_biases(paths: list[str], decided: list[dict], max_ev: float, max_wb: float,
                cast: tuple[float, float] | None = None) -> list[tuple[float, float, float]]:
    """`burst_biases` for the RAW files of a burst among themselves and for the other files among
    themselves. A RAW decode starts about a stop and a half below the camera's JPEG of the same
    shot, so one median for both would spend the whole nudge on that gap (measured: it pulled
    JPEGs down and RAW files up by the full 0.5 EV) instead of on the drift inside the burst. A
    kind with a single file in the burst is not moved. With `cast` (bound, confidence) the temp and
    tint come from `cast_biases` instead; the exposure stays that of `burst_biases`."""
    out = [(0.0, 0.0, 0.0)] * len(paths)
    for raw in (True, False):
        idx = [k for k, p in enumerate(paths) if is_raw(p) == raw]
        if len(idx) >= 2:
            part = [decided[k] for k in idx]
            got = burst_biases(part, max_ev, max_wb)
            if cast is not None:
                got = [(ev, t, n) for (ev, _t, _n), (t, n) in zip(got, cast_biases(part, *cast))]
            for k, b in zip(idx, got):
                out[k] = b
    return out


def plan(images: list[str], cfg, pipeline, thr: dict | None = None, progress=None) -> dict[str, Bias]:
    """The consistency bias of every image, for the mode in `cfg.consistency`.
    "brightness": brightness clusters, a nudge of at most 0.15.
    "bursts": frames of one framing shot close together are brought to one level and one white
    balance.
    "timelapse": frames in order are moved to the running median of their neighbours (F43).
    A photo that fails to measure simply gets no bias. `progress`, when given, is called
    with a short line of text now and then: the bursts plan decodes and decides every burst
    photo once before the first render, which takes minutes on a large shoot."""
    if len(images) < 2:
        return {}
    thr = {**BURST_DEFAULTS, **((getattr(pipeline, "rules", None) or {}).get("consistency", {}) if thr is None else thr)}
    # A pick or a given white balance sets it itself, so the group has nothing to even out.
    by_cast = bool(getattr(cfg, "consistency_wb", False)) and cfg.neutral is None and cfg.wb is None
    words = thr.get("colour_of")
    if not (isinstance(words, list) and all(isinstance(w, str) and w for w in words)):
        raise ValueError(f"rules.toml [consistency] colour_of must be a list of words (got {words!r})")
    if cfg.consistency == "timelapse":
        return _with_rules(pipeline, lambda: _timelapse_plan(images, pipeline, thr))
    if cfg.consistency == "bursts":
        return _bursts_plan(images, pipeline, thr, progress, by_cast)
    out = _brightness_plan(images)
    if by_cast:
        _with_rules(pipeline, lambda: _cast_plan(out, pipeline, thr, progress))
    return out


def subject_colour(rgb: np.ndarray, union: dict, names) -> dict | None:
    """The mean OKLab colour of the first of `names` with a mask of the picture's shape, or None."""
    from .edit.oklab import linear_to_oklab
    from .imageio import srgb_to_linear
    for name in names:
        m = union.get(name)
        if m is not None and m.shape == rgb.shape[:2] and m.any():
            lab = linear_to_oklab(srgb_to_linear(rgb[m > 0.5]).astype(np.float64))
            return {"concept": name, "lab": [round(float(v), 5) for v in np.asarray(lab).reshape(-1, 3).mean(axis=0)]}
    return None


DELTA_E = ("OKLab distance times 100 from the group's median colour; about 2 is the smallest difference seen at a "
           "glance")


def colour_report(sidecars: list[str], min_confidence: float) -> dict:
    """Per group of photos, how far the subject colour (and the white balance left to do) spreads."""
    groups: dict[str, list[dict]] = {}
    for path in sidecars:
        try:
            with open(path) as fh:
                s = json.load(fh)
            name = s["image"]
            key = (s.get("batch_group") or "all") + (" raw" if is_raw(name) else "")
            pe = s.get("post_edit_stats") or {}
            groups.setdefault(key, []).append({
                "name": name, "lab": ((s.get("audit") or {}).get("subject_colour") or {}).get("lab"),
                "temp": pe.get("wb_temp_correction"), "tint": pe.get("wb_tint_correction"),
                "conf": pe.get("wb_confidence") or 0.0})
        except Exception:
            continue
    out: dict[str, dict] = {}
    for key, fr in groups.items():
        g: dict = {}
        lab = [f for f in fr if f["lab"]]
        if len(lab) >= 2:
            arr = np.array([f["lab"] for f in lab], dtype=np.float64)
            d = 100.0 * np.linalg.norm(arr - np.array([_median(list(arr[:, c])) for c in range(3)]), axis=1)
            g.update(photos=len(lab), delta_e_mean=round(float(d.mean()), 2), delta_e_max=round(float(d.max()), 2),
                     furthest=lab[int(d.argmax())]["name"])
        sure = [f for f in fr if f["conf"] >= min_confidence and f["temp"] is not None]
        if len(sure) >= 2:
            g["wb_spread"] = [round(max(f[k] for f in sure) - min(f[k] for f in sure), 2) for k in ("temp", "tint")]
        if g:
            out[key] = g
    return {"delta_e": DELTA_E, "groups": out}


def colour_lines(report: dict) -> list[str]:
    lines = []
    for key, g in report["groups"].items():
        parts = []
        if "photos" in g:
            parts.append(f"{g['photos']} photos, subject colour differs by {g['delta_e_mean']:.1f} on average and "
                         f"{g['delta_e_max']:.1f} at most ({g['furthest']})")
        if "wb_spread" in g:
            parts.append(f"white balance spread temp {g['wb_spread'][0]:.1f}, tint {g['wb_spread'][1]:.1f}")
        lines.append(f"  group {key}: " + "; ".join(parts))
    return lines
