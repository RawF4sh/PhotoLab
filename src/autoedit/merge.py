"""F33: merge the frames of a folder that were shot to become one picture (`autoedit merge`).

The alignment and merge math (frames move by whole pixels only; nothing is resampled or
generated, every pixel of the result is made from the user's own frames at the same place),
then the groups, the files and the command. Core imports only (numpy + the package).
"""

from __future__ import annotations

import filecmp
import json
import math
import os

import numpy as np
from PIL import Image as PILImage

from . import __version__, batch, consistency, emit, paths
from .edit.filters import gaussian_blur
from .imageio import is_raw, linear_to_srgb, load_image, luma, register_reader, srgb_to_linear

MODES = ("average", "brackets", "focus", "trails")
_RAW = {"raw_mode": "linear_srgb"}      # a RAW file is developed linear under the exact sRGB curve: light, not a tone curve

_WHY = {
    "average": "average: the frames are aligned and averaged in linear light; noise falls by the square root of their number",
    "brackets": "brackets: the frames are aligned, their exposures measured, and merged in linear light with weights "
                "that trust each frame only where it is well exposed",
    "focus": "focus: the frames are aligned and each pixel is taken from the frame that is sharpest there",
    "trails": "trails: the brightest frame at each pixel wins; the frames are neither moved nor checked",
}


# --- config -----------------------------------------------------------------------------------

def _ok(v) -> bool:
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) and v > 0


def load_config(configs_dir: str | None = None, profile: str | None = None) -> dict:
    """merge.toml: the packaged file, the active configs folder's file over it, a profile's over both,
    so a partial file never drops a key."""
    base = paths.read_config(os.path.join(paths._packaged(), "merge.toml"))
    local = paths.read_config(os.path.join(configs_dir or paths.configs_dir(), "merge.toml"), profile)
    thr = paths.merge(base, local)
    for sec, table in thr.items():
        if not isinstance(table, dict):
            raise ValueError(f"merge.toml: {sec} must be a table, not {table!r}")
        for key, v in table.items():
            if not (_ok(v) or (isinstance(v, list) and v and all(_ok(x) for x in v))):
                raise ValueError(f"merge.toml: [{sec}] {key} must be a number above 0, not {v!r}")
    for key in ("low", "high"):
        v = thr.get("brackets", {}).get(key)
        if v is not None and not (isinstance(v, list) and len(v) == 2 and v[0] < v[1] <= 1):
            raise ValueError(f"merge.toml: [brackets] {key} must be two numbers, rising, up to 1, not {v!r}")
    for key in ("proxy_px", "window_px"):
        v = thr.get("align", {}).get(key)
        if v is not None and (isinstance(v, list) or v < 16 or v != int(v)):
            raise ValueError(f"merge.toml: [align] {key} must be a whole number of 16 or more, not {v!r}")
    return thr


# --- alignment: whole pixels, against the reference frame --------------------------------------

def _blocks(a: np.ndarray, f: int, top: bool = False) -> np.ndarray:
    h, w = a.shape[0] // f * f, a.shape[1] // f * f
    a = a[:h, :w].reshape(h // f, f, w // f, f, *a.shape[2:])
    return a.max(axis=(1, 3)) if top else a.mean(axis=(1, 3))


def _well(lin: np.ndarray, top: np.ndarray, b: dict) -> np.ndarray:
    return (top < b["high"][0]) & (luma(linear_to_srgb(lin)) > b["low"][1])


def _gain(a: np.ndarray, b: np.ndarray, both: np.ndarray) -> np.ndarray:
    """b's exposure over a's, per channel: the median ratio where both expose well."""
    ma, mb = np.median(a[both], axis=0), np.median(b[both], axis=0)
    ok = (ma > 1e-4) & (mb > 1e-4)        # a channel with no signal (deep red light: no blue) has no ratio of its own
    if not ok.any():
        return np.ones(3, np.float32)
    g = np.where(ok, mb / np.where(ok, ma, 1.0), 0.0)
    return np.where(ok, g, g[ok].mean()).astype(np.float32)


def _lumas(a: np.ndarray, b: np.ndarray, g: np.ndarray):
    """The display lumas of a and b at one exposure: the darker is lifted and clips where the brighter clips."""
    if g.mean() >= 1.0:
        a = np.clip(a * g, 0, 1)
    else:
        b = np.clip(b / g, 0, 1)
    return luma(linear_to_srgb(a)), luma(linear_to_srgb(b))


def _slices(h: int, w: int, dy: int, dx: int):
    """(target, source) with out[target] = frame[source]."""
    return ((slice(max(0, -dy), h - max(0, dy)), slice(max(0, -dx), w - max(0, dx))),
            (slice(max(0, dy), h - max(0, -dy)), slice(max(0, dx), w - max(0, -dx))))


def _small(lin: np.ndarray, rgb: np.ndarray, m: int, dy: int, dx: int, f: int):
    """The small copy of a frame on the part of it that the reference's inner region shows."""
    h, w = lin.shape[:2]
    dy, dx = min(max(dy, -m), m), min(max(dx, -m), m)
    win = (slice(m + dy, h - m + dy), slice(m + dx, w - m + dx))
    return _blocks(lin[win], f), _blocks(rgb[win].max(axis=2), f, top=True)


def _phase(a, b) -> tuple[int, int]:
    """Whole pixels (dy, dx) by which b's content sits further down and right than a's.
    The cross spectrum is divided by the square root of its size: full whitening gave the fine
    detail, where a sharp and a soft frame disagree, as much say as the shapes."""
    win = np.outer(np.hanning(a.shape[0]), np.hanning(a.shape[1])).astype(np.float32)
    cross = np.fft.rfft2((b - b.mean()) * win) * np.conj(np.fft.rfft2((a - a.mean()) * win))
    c = np.fft.fftshift(np.fft.irfft2(cross / (np.sqrt(np.abs(cross)) + 1e-12), s=a.shape))
    y, x = divmod(int(np.argmax(c)), c.shape[1])
    return y - a.shape[0] // 2, x - a.shape[1] // 2           # zero shift sits in the middle


def _climb(a, b, dy: int, dx: int, reach: int, cap: float) -> tuple[float, int, int]:
    """(cost, dy, dx); b is a's place with a rim of `reach`: b[reach + y + dy, reach + x + dx] lies on a[y, x]."""
    h, w = a.shape
    seen: dict = {}

    def cost(y: int, x: int) -> float:
        if (y, x) not in seen:
            d = b[reach + y:reach + y + h, reach + x:reach + x + w] - a
            seen[(y, x)] = float(np.minimum(d * d, cap * cap).mean())
        return seen[(y, x)]
    while True:
        best = min((cost(dy + j, dx + i), dy + j, dx + i) for j in (-1, 0, 1) for i in (-1, 0, 1)
                   if max(abs(dy + j), abs(dx + i)) <= reach)
        if best[1:] == (dy, dx):
            return best
        dy, dx = best[1:]


def _cost_at(a, b, dy: int, dx: int, cap: float) -> float:
    """The capped squared difference of b moved by (dy, dx) against a, on their overlap (inf when under a quarter)."""
    t, s = _slices(*a.shape, dy, dx)
    d = b[s] - a[t]
    return float(np.minimum(d * d, cap * cap).mean()) if d.size >= a.size / 4 else math.inf


def _moved(a, b, both, thr: dict) -> float:       # b gets a's tones rank for rank: exposure and tone curve are out
    la, lb = (gaussian_blur(luma(linear_to_srgb(s)), thr["blur"]) for s in (a, b))
    av, bv = la[both], lb[both]
    matched = np.empty_like(bv)
    matched[np.argsort(bv, kind="stable")] = np.sort(av)
    return float((np.abs(matched - av) > thr["tolerance"]).mean())


class _Reference:
    def __init__(self, lin, rgb, f: int, m: int, thr: dict):
        self.f, self.m = f, m
        self.phase_wins = float(thr["align"].get("phase_wins", 0.8))
        self.small, self.top = _blocks(lin, f), _blocks(rgb.max(axis=2), f, top=True)
        h, w = lin.shape[:2]
        r = m + 1                              # every frame must hold the window plus this rim
        s = min(int(thr["align"]["window_px"]), h - 2 * r, w - 2 * r)
        self.win = None
        if s >= 16:
            d = luma(linear_to_srgb(self.small))
            detail = (d - gaussian_blur(d, 1.0)) ** 2
            spots = [(y, x) for y in np.linspace(r, h - r - s, 3).astype(int)
                     for x in np.linspace(r, w - r - s, 3).astype(int)]
            self.y0, self.x0 = max(spots, key=lambda p: float(
                detail[p[0] // f:(p[0] + s) // f + 1, p[1] // f:(p[1] + s) // f + 1].mean()))
            self.s, self.win = s, lin[self.y0:self.y0 + s, self.x0:self.x0 + s].copy()

    def shift(self, lin, rgb, b: dict, cap: float) -> tuple[int, int]:
        small, top = _blocks(lin, self.f), _blocks(rgb.max(axis=2), self.f, top=True)
        both = _well(self.small, self.top, b) & _well(small, top, b)
        g = _gain(self.small, small, both) if both.mean() >= b["min_overlap"] else np.ones(3, np.float32)
        la, lb = _lumas(self.small, small, g)
        dy, dx = (v * self.f for v in _phase(la, lb))
        if self.win is None:
            return dy, dx
        y, x, s, r = self.y0, self.x0, self.s, self.m + 1
        a, bb = (gaussian_blur(v, 1.0) for v in _lumas(self.win, lin[y - r:y + s + r, x - r:x + s + r], g))
        starts = {(0, 0)} | ({(dy, dx)} if max(abs(dy), abs(dx)) <= self.m else set())
        _, ry, rx = min(_climb(a, bb, *p, r, cap) for p in starts)
        if max(abs(ry), abs(rx)) > self.m and max(abs(dy), abs(dx)) > self.m:
            return dy, dx                      # too far off: say how far, as the small copies have it
        if max(abs(dy), abs(dx)) > self.m:     # the climb stayed near: on fine texture it can settle in a wrong dip
            la, lb = (gaussian_blur(v, 1.0) for v in (la, lb))
            far = _cost_at(la, lb, dy // self.f, dx // self.f, cap)
            near = _cost_at(la, lb, round(ry / self.f), round(rx / self.f), cap)
            if far < self.phase_wins * near:
                return dy, dx                  # the far shift fits clearly better: the frame is that far off
        return ry, rx


# --- the merge ----------------------------------------------------------------------------------

def merge_frames(load, n: int, mode: str, thr: dict, align: bool = True, ref: int | None = None):
    """Merge n frames. `load(k)` returns frame k as float32 RGB in 0..1 under the sRGB curve; each frame
    is loaded twice, one held at a time. Returns (rgb under the sRGB curve, found) with `found`
    {"reference", "frames": [{"shift", "moved", "exposure_ev" (brackets)}], "flags", "cut"}."""
    if mode not in MODES:
        raise ValueError(f"unknown merge mode {mode!r}; choose one of {MODES}")
    if n < 2:
        raise ValueError("a merge needs at least two frames")
    ref = n // 2 if ref is None else ref
    a, b, mv = thr["align"], thr["brackets"], thr["moved"]
    shape = None

    def frame(k: int) -> np.ndarray:
        nonlocal shape
        rgb = load(k)
        if shape is None:
            shape = rgb.shape
        elif rgb.shape != shape:
            raise ValueError(f"frame {k + 1} has another size than the others")
        return rgb

    check = mode != "trails"                  # trails: the sky moves by design; a search could follow it
    shifts, smalls = [(0, 0)] * n, [None] * n
    reference = sharpest = choice = None
    for k in [ref] + [k for k in range(n) if k != ref] if check else ():   # pass 1: where each frame sits
        rgb = frame(k)
        h, w = rgb.shape[:2]
        f = max(1, math.ceil(max(h, w) / a["proxy_px"]))
        m = max(1, math.ceil(a["max_shift"] * max(h, w)))
        lin = srgb_to_linear(rgb)
        if k == ref:
            reference = _Reference(lin, rgb, f, m, thr) if align else None
        elif align:
            dy, dx = reference.shift(lin, rgb, b, mv["tolerance"])
            if max(abs(dy), abs(dx)) > m:
                raise ValueError(f"frame {k + 1} sits {max(abs(dy), abs(dx))} px off frame {ref + 1}; the limit is "
                                 f"{m} px ({a['max_shift']:.1%} of the long edge): frames from a tripod only")
            shifts[k] = (dy, dx)
        smalls[k] = _small(lin, rgb, m, *shifts[k], f)
        del lin
        if mode == "focus":                   # per pixel of the reference grid: which frame is sharpest
            fo = thr["focus"]
            l = luma(rgb)
            sharp = gaussian_blur((l - gaussian_blur(l, fo["detail_sigma"])) ** 2, fo["window"] * max(h, w))
            if sharpest is None:
                sharpest, choice = np.full((h, w), -1.0, np.float32), np.zeros((h, w), np.int16)
            tgt, src = _slices(h, w, *shifts[k])
            better = sharp[src] > sharpest[tgt]
            sharpest[tgt][better] = sharp[src][better]
            choice[tgt][better] = k
    reference = sharpest = None
    info = [{"shift": list(shifts[k]), "moved": None} for k in range(n)]
    flags = []
    if check:
        well = [_well(*s, b) for s in smalls]
        for k in range(n):
            both = well[ref] & well[k]
            if k != ref and both.mean() >= b["min_overlap"]:
                info[k]["moved"] = round(_moved(smalls[ref][0], smalls[k][0], both, mv), 4)
        if any((i["moved"] or 0.0) > mv["max_share"] for i in info):
            flags.append("moved")
    gains = [np.ones(3, np.float32)] * n
    order = list(range(n))
    if mode == "brackets":                    # darkest first; each frame's exposure against the darkest
        order = sorted(range(n), key=lambda k: float(np.median(luma(smalls[k][0]))))
        for lo, hi in zip(order, order[1:]):
            both = well[lo] & well[hi]
            if both.mean() < b["min_overlap"]:
                raise ValueError("two neighbouring exposures share too little that both expose well: "
                                 "the steps of the bracket are too far apart")
            gains[hi] = gains[lo] * _gain(smalls[lo][0], smalls[hi][0], both)
        for k in range(n):
            info[k]["exposure_ev"] = round(float(np.log2(gains[k].mean())), 3)
    dys, dxs = [s[0] for s in shifts], [s[1] for s in shifts]
    acc = cnt = None
    for k in range(n):                        # pass 2: the picture, on the part every frame shows
        full = frame(k)
        if acc is None:
            h, w = full.shape[:2]
            y0, y1, x0, x1 = max(0, -min(dys)), h - max(0, max(dys)), max(0, -min(dxs)), w - max(0, max(dxs))
            acc = np.zeros((y1 - y0, x1 - x0, 3), np.float32)
            cnt = np.full(acc.shape[:2], -1.0 if mode == "trails" else 0.0, np.float32)
        dy, dx = shifts[k]
        rgb = full[y0 + dy:y1 + dy, x0 + dx:x1 + dx]
        del full
        lin = srgb_to_linear(rgb)
        if mode == "average":
            acc += lin
        elif mode == "trails":                # cnt holds the brightest luma so far; a tie keeps the earlier frame
            l = luma(rgb)
            brighter = l > cnt
            acc[brighter], cnt[brighter] = lin[brighter], l[brighter]
        elif mode == "focus":
            wgt = gaussian_blur((choice == k).astype(np.float32), thr["focus"]["soften"] * max(h, w))[y0:y1, x0:x1]
            acc += wgt[..., None] * lin
            cnt += wgt
        else:                                 # brackets
            wgt = np.full(lin.shape[:2], float(gains[k].mean()), np.float32)   # more light, less noise
            if k != order[0]:                 # near white in any channel: only the darkest frame may speak
                wgt *= np.clip((b["high"][1] - rgb.max(axis=2)) / (b["high"][1] - b["high"][0]), 0.0, 1.0)
            if k != order[-1]:                # lost in the dark: only the brightest frame may speak
                wgt *= np.clip((luma(rgb) - b["low"][0]) / (b["low"][1] - b["low"][0]), 0.0, 1.0)
            if k == order[0]:
                wgt = np.maximum(wgt, 1e-4)   # some frame always speaks
            acc += wgt[..., None] * (lin / gains[k])
            cnt += wgt
    if mode == "average":
        acc /= n
    elif mode != "trails":
        acc /= np.maximum(cnt, 1e-9)[..., None]
    return linear_to_srgb(acc), {"reference": ref, "frames": info, "flags": flags,
                                 "cut": [h - (y1 - y0), w - (x1 - x0)]}


# --- groups: which frames belong together --------------------------------------------------------

def _num(v) -> float | None:
    if isinstance(v, (tuple, list)):
        v = v[0] if v else None
    try:
        v = float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return v if math.isfinite(v) and v > 0 else None


def exposure(path: str) -> tuple[float | None, float]:
    """(exposure settings in stops: log2 of time * ISO / 100 / f-number squared, or None; seconds)."""
    try:
        register_reader(path)
        with PILImage.open(path) as pil:
            ifd = pil.getexif().get_ifd(0x8769)
        t, iso, n = _num(ifd.get(0x829A)), _num(ifd.get(0x8827)), _num(ifd.get(0x829D))
    except Exception:
        return None, 0.0
    if t is None or iso is None or n is None:
        return None, t or 0.0
    return math.log2(t * iso / 100.0 / n ** 2), t


def _alike(a: str, b: str) -> float:              # -1.0 when the settings are equal or unknown: the plain score stands
    (ea, _), (eb, _) = exposure(a), exposure(b)
    if ea is None or eb is None or abs(ea - eb) < 0.1:
        return -1.0
    if ea > eb:
        a, b, ea, eb = b, a, eb, ea
    ga, gb = consistency.thumb_grey(a), consistency.thumb_grey(b)
    if ga is None or gb is None or ga.shape != gb.shape:
        return -1.0
    lifted = linear_to_srgb(srgb_to_linear(ga / 255.0) * 2.0 ** (eb - ea)) * 255.0
    return float((consistency.signature(lifted) * consistency.signature(gb)).mean())


def _distinct(files: list[str]) -> tuple[list[str], list[str]]:
    """(files, copies): a file of the size and kind of a kept one and equal to it is a copy."""
    keep, copies = [], []
    for f in files:
        size = os.path.getsize(f)
        if any(is_raw(k) == is_raw(f) and os.path.getsize(k) == size and filecmp.cmp(k, f, shallow=False) for k in keep):
            copies.append(f)
        else:
            keep.append(f)
    return keep, copies


def find_groups(images: list[str], thr: dict, everything: bool = False) -> list[dict]:
    """[{"frames", "left_out", "copies"}] (paths, capture order). `thr` is the [groups] table."""
    longest = max((exposure(p)[1] for p in images), default=0.0)
    if everything:
        runs = [sorted(images, key=lambda p: (consistency.capture_time(p) or 0.0, p))]
    else:
        runs = consistency.find_bursts(images, thr["max_gap_s"] + longest, thr["min_similarity"], None, alike=_alike)
    out = []
    for files in runs:
        files, copies = _distinct(files)
        raw = [f for f in files if is_raw(f)]
        use_raw = len(raw) >= max(2, len(files) - len(raw))
        frames = raw if use_raw else [f for f in files if not is_raw(f)]
        if len(frames) >= 2:
            out.append({"frames": frames, "left_out": [f for f in files if f not in frames], "copies": copies})
    return out


def pick_mode(stops: list, thr: dict) -> str:
    known = all(s is not None for s in stops)
    return "brackets" if known and stops and max(stops) - min(stops) > thr["bracket_min_ev"] else "average"


# --- files, the record, the command ------------------------------------------------------------

def _bits() -> int:
    try:
        import tifffile  # noqa: F401
        return 16
    except ImportError:
        return 8


def _record_path(image: str) -> str:
    return (image[:-4] if image.lower().endswith(".tif") else image) + ".merge.json"


def _ours(image: str) -> bool:
    """True when a record beside `image` says merge wrote it."""
    try:
        with open(_record_path(image), encoding="utf-8") as fh:
            rec = json.load(fh)
        return isinstance(rec, dict) and rec.get("merge") == 1 and rec.get("output") == os.path.basename(image)
    except (OSError, ValueError):
        return False


def photos(folder: str) -> list[str]:
    return [p for p in batch.list_images(folder) if not _ours(p)]


def merge_group(group: dict, out_dir: str, mode: str, thr: dict, align: bool, taken: set, sources: set) -> dict:
    frames, n = group["frames"], len(group["frames"])
    image_out, _ = emit.output_paths(out_dir, frames[0], "tiff", taken, name="{stem}_" + mode, sources=sources)
    record = _record_path(image_out)
    for p in (image_out, record):
        if os.path.exists(p) and not _ours(image_out):
            raise ValueError(f"{p} exists and was not written by merge; it is left alone: choose another output folder")
    ev = [exposure(f)[0] for f in frames]
    known = all(e is not None for e in ev)
    ref = sorted(range(n), key=ev.__getitem__)[n // 2] if mode == "brackets" and known else None
    rgb, found = merge_frames(lambda k: load_image(frames[k], _RAW).rgb, n, mode, thr, align, ref)
    emit.write_image(rgb, image_out, "tiff", source=frames[found["reference"]], metadata="shareable")
    bits = _bits()
    notes = [_WHY[mode]]
    if "moved" in found["flags"]:
        notes.append("something moved between the frames; it is in the picture as a ghost, not repaired")
    if any(found["cut"]):
        notes.append(f"cut {found['cut'][0]} rows and {found['cut'][1]} columns that not every frame shows")
    if mode == "average" and max((exposure(f)[1] for f in frames), default=0.0) >= 1.0:
        notes.append("exposures of a second or longer: stars smear in an average; --mode trails keeps them")
    if group["left_out"] or group["copies"]:
        notes.append(f"{len(group['left_out'])} files left out, {len(group['copies'])} copies")
    if bits == 8:
        notes.append("8-bit TIFF: install the accel extra (tifffile) for 16 bits")
    infos = []
    for k, f in enumerate(frames):
        info = {"file": os.path.basename(f), **found["frames"][k]}
        if known:
            info["settings_ev"] = round(ev[k] - min(ev), 3)
        infos.append(info)
    rec = {"merge": 1, "engine_version": __version__, "output": os.path.basename(image_out), "mode": mode,
           "bits": bits, "reference": found["reference"], "frames": infos,
           "left_out": [os.path.basename(f) for f in group["left_out"]],
           "copies": [os.path.basename(f) for f in group["copies"]],
           "cut": found["cut"], "flags": found["flags"], "settings": thr, "notes": notes}
    with open(record + ".part", "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(record + ".part", record)
    return rec


def run(folder: str, out_dir: str, mode: str = "auto", everything: bool = False, align: bool = True,
        dry_run: bool = False, configs_dir: str | None = None, profile: str | None = None, say=print) -> dict:
    if mode != "auto" and mode not in MODES:
        raise ValueError(f"unknown merge mode {mode!r}; choose one of {('auto',) + MODES}")
    thr = load_config(configs_dir, profile)
    images = photos(folder)
    groups = find_groups(images, thr["groups"], everything)
    sources = emit.source_ids(images)
    taken: set = set()
    res = {"groups": groups, "merged": [], "flagged": [], "failed": []}
    say(f"searching {len(images)} photos for frames to merge")
    if not groups:
        say("nothing to merge" + ("" if everything else " (--all treats every photo of the folder as one group)"))
        return res
    if _bits() == 8:
        say("tifffile is not installed: the TIFFs will be 8-bit")
    for i, g in enumerate(groups, 1):
        names = [os.path.basename(f) for f in g["frames"]]
        stops = [exposure(f)[0] for f in g["frames"]]
        m = pick_mode(stops, thr["groups"]) if mode == "auto" else mode
        gap = (f"exposure settings {max(stops) - min(stops):.1f} EV apart"
               if all(s is not None for s in stops) else "no exposure settings")
        line = f"[{i}/{len(groups)}] {names[0]} to {names[-1]}: {len(names)} frames, {gap}, {m}"
        if dry_run:
            say(line)
            continue
        try:
            rec = merge_group(g, out_dir, m, thr, align, taken, sources)
        except Exception as e:
            res["failed"].append({"first": names[0], "error": str(e)})
            say(f"{line} -> NOT MERGED: {e}")
            continue
        res["merged"].append(rec["output"])
        if rec["flags"]:
            res["flagged"].append(rec["output"])
        say(f"{line} -> {rec['output']}" + ("  FLAGGED: " + ", ".join(rec["flags"]) if rec["flags"] else ""))
    return res
