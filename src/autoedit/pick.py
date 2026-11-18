"""Pick the keepers (F35): find the bursts of a folder and rank the frames of each by how sharp
the subject is, by exposure, and by the scorer when a model is given. `run` writes a ranking, a
list of the picks and a contact sheet into its own output folder, and copies only with `copy`.
No photo is moved, changed or deleted; no file is ever written over one."""

from __future__ import annotations

import csv
import html
import json
import math
import os
import shutil
from urllib.parse import quote

import numpy as np

from . import __version__, aesthetic, batch, consistency, curate, emit, paths, segment
from . import rules as rules_mod
from .edit.filters import box_blur
from .imageio import Image, camera_preview, is_raw, load_image, luma, raw_opts_from, resize_long_edge, save_image
from .stats import _HIGHLIGHT


def load_settings(configs_dir: str | None = None, profile: str | None = None) -> dict:
    """pick.toml (a profile's file of the same name merged over it)."""
    base = os.path.join(configs_dir or paths.configs_dir(), "pick.toml")
    if not os.path.exists(base):   # a configs folder that predates the file gets the packaged one
        base = os.path.join(paths._packaged(), "pick.toml")
    return _check(paths.read_config(base, profile))


_POSITIVE = {"measure": ("long_edge", "local_radius", "min_contrast", "min_coverage"),
             "exposure": ("clip_full", "dark_p50"), "sheet": ("thumb",)}


def _check(st: dict) -> dict:
    """pick.toml's values are numbers in range: a bad profile file stops the run with its name and key."""
    def num(sec, key, ok, what):
        v = st.get(sec, {}).get(key)
        if v is None:
            return
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not ok(v):
            raise ValueError(f"pick.toml: [{sec}] {key} must be {what}, not {v!r}")
    for sec, keys in _POSITIVE.items():
        for key in keys:
            num(sec, key, lambda v: v > 0, "a number above 0")
    num("measure", "min_score", lambda v: 0 <= v <= 1, "a number from 0 to 1")
    for key in ("exposure", "scorer"):
        num("weights", key, lambda v: v >= 0, "a number of 0 or more")
    crop = st.get("sheet", {}).get("crop")
    if crop is not None and not (isinstance(crop, list) and len(crop) == 2 and
                                 all(isinstance(c, int) and not isinstance(c, bool) and c > 0 for c in crop)):
        raise ValueError(f"pick.toml: [sheet] crop must be two whole numbers above 0, not {crop!r}")
    words = st.get("measure", {}).get("concepts")
    if words is not None and not (isinstance(words, list) and words and all(isinstance(w, str) and w for w in words)):
        raise ValueError(f"pick.toml: [measure] concepts must be a list of words, not {words!r}")
    return st


def _not_ours(out_dir: str) -> list[str]:
    """Files of `FILES` in `out_dir` that an earlier pick run did not write: they are never overwritten."""
    there = [f for f in FILES if os.path.exists(os.path.join(out_dir, f))]
    if not there:
        return []
    try:
        with open(os.path.join(out_dir, "ranking.json"), encoding="utf-8") as fh:
            old = json.load(fh)
        if isinstance(old, dict) and {"bursts", "singles", "picks"} <= set(old):
            return []
    except (OSError, ValueError):
        pass
    return there


def shots_of(files: list[str]) -> list[list[str]]:
    """Group files by lower-cased name without extension; a group of two or more with a RAW file
    is one shot (the judged file first, never the RAW file), every other file is its own shot."""
    groups: dict[str, list[str]] = {}
    for f in sorted(files):
        groups.setdefault(os.path.splitext(os.path.basename(f))[0].lower(), []).append(f)
    shots = []
    for g in groups.values():
        if len(g) > 1 and any(is_raw(p) for p in g):
            shots.append(sorted(g, key=lambda p: (is_raw(p), p)))
        else:
            shots.extend([p] for p in g)
    return sorted(shots)


def groups_of(images: list[str], thr: dict) -> tuple[list[list[list[str]]], list[list[str]]]:
    """(bursts, singles). A burst is a list of two or more shots, a single is one shot."""
    shots = shots_of(images)
    shot_of = {f: i for i, shot in enumerate(shots) for f in shot}
    bursts, used = [], set()
    for files in consistency.find_bursts(images, thr["max_gap_s"], thr["min_similarity"], thr["max_span_s"]):
        mine = sorted({shot_of[f] for f in files} - used)
        if len(mine) > 1:
            used.update(mine)
            bursts.append([shots[i] for i in mine])
    return bursts, [s for i, s in enumerate(shots) if i not in used]


def look_picture(path: str, long_edge: int, decode: dict | None = None) -> np.ndarray:
    """Upright float RGB, long edge at most `long_edge`. A RAW file gives the camera's own preview
    when that is large enough, else a half-size development."""
    if is_raw(path):
        rgb = camera_preview(path, max_px=long_edge)
        if rgb is not None and max(rgb.shape[:2]) >= long_edge:
            return rgb
        return resize_long_edge(load_image(path, {**(decode or {}), "half_size": True}).rgb, long_edge)
    # ponytail: a full decode, then the shrink (0.4 s for a 22 megapixel JPEG). Decode at reduced
    # size, as consistency.thumb_signature does, when a shoot of thousands takes too long.
    return resize_long_edge(load_image(path).rgb, long_edge)


def sharpness(rgb, mask, radius: int = 16, min_contrast: float = 0.02) -> float:
    """RMS of the luma's Laplacian over RMS of the local contrast (a pixel against the mean of the
    2 * radius + 1 square around it), both inside `mask`. Under `min_contrast` an area is flat: it
    is not divided by less, so its noise cannot look sharp."""
    y = luma(rgb)
    lap = 4.0 * y[1:-1, 1:-1] - y[:-2, 1:-1] - y[2:, 1:-1] - y[1:-1, :-2] - y[1:-1, 2:]
    local = (y - box_blur(y, radius))[1:-1, 1:-1]
    m = mask[1:-1, 1:-1]
    if not m.any():
        return 0.0
    rms = lambda a: float(np.sqrt(np.mean(np.square(a[m], dtype=np.float64))))  # noqa: E731
    return rms(lap) / max(rms(local), min_contrast)


def exposure_penalty(rgb, mask, clip_full: float, dark_p50: float) -> tuple[float, dict]:
    """(0..1, its numbers), inside `mask`: blown share over `clip_full`, or the median under `dark_p50`."""
    blown = float((rgb.max(axis=2)[mask] > _HIGHLIGHT).mean())
    p50 = float(np.median(luma(rgb)[mask]))
    penalty = max(min(1.0, blown / clip_full), min(1.0, max(0.0, (dark_p50 - p50) / dark_p50)))
    return penalty, {"blown": blown, "p50": p50}


def judge(path, seg, st, concepts, decode=None, model=None) -> tuple[dict, np.ndarray, np.ndarray]:
    """(the numbers of one shot, the picture it was judged on, the judged area)."""
    m = st["measure"]
    rgb = look_picture(path, int(m["long_edge"]), decode)
    area = np.zeros(rgb.shape[:2], dtype=bool)
    for r in seg.segment(Image(rgb=rgb, path=path), list(concepts)):
        if r.score >= m["min_score"]:
            area |= r.mask
    on = "mask"
    if area.mean() < m["min_coverage"]:      # nothing found, or a speck: the whole frame
        area[:], on = True, "frame"
    penalty, numbers = exposure_penalty(rgb, area, st["exposure"]["clip_full"], st["exposure"]["dark_p50"])
    scorer = aesthetic.predict(curate.features_of(resize_long_edge(rgb, 256)), model) if model else None
    return ({"judged_on": on, "coverage": float(area.mean()),
             "sharpness": sharpness(rgb, area, int(m["local_radius"]), m["min_contrast"]),
             "exposure_penalty": penalty, **numbers, "scorer": scorer}, rgb, area)


def rank_burst(judged: list[dict], weights: dict) -> list[dict]:
    """The shots of one burst, best first, each with `sharp_rel`, `score`, `rank` and `pick`.
    The best possible score is 1. Ties go to the sharper shot, then to the file name."""
    best = max(j["sharpness"] for j in judged) or 1.0
    top = max((j["scorer"] for j in judged if j["scorer"] is not None), default=None)
    for j in judged:
        j["sharp_rel"] = j["sharpness"] / best
        j["score"] = j["sharp_rel"] - weights["exposure"] * j["exposure_penalty"]
        if top is not None and j["scorer"] is not None:
            j["score"] -= weights["scorer"] * (top - j["scorer"])
    order = sorted(judged, key=lambda j: (-j["score"], -j["sharpness"], j["files"][0]))
    for n, j in enumerate(order, start=1):
        j["rank"], j["pick"] = n, n == 1
    return order


FILES = ("ranking.json", "ranking.csv", "picks.txt", "contact-sheet.html")


class OnAPhoto(ValueError):
    """A file would be written over one of the photos that are being read. It stops the run."""


def _why(j: dict, concepts: list[str]) -> list[str]:
    where = f"the mask of {', '.join(concepts)}" if j["judged_on"] == "mask" else "the whole frame (no mask found)"
    out = [f"sharpness {j['sharp_rel']:.2f} of the burst's sharpest, judged on {where}"]
    if j["exposure_penalty"] > 0:
        out.append(f"exposure penalty {j['exposure_penalty']:.2f}: {j['blown']:.1%} blown, median luma {j['p50']:.2f}")
    if j["scorer"] is not None:
        out.append(f"scorer {j['scorer']:.2f}")
    return out


def _target(path: str, sources: set) -> str:
    """`path` as a place to write. Never one of the photos that are being read."""
    if os.path.exists(path):
        st = os.stat(path)
        if (st.st_dev, st.st_ino) in sources:
            raise OnAPhoto(f"{path}: this is one of the photos; choose another output folder")
    return path


def _window(rgb, area, size):
    """A window of the judged picture at full scale, on the middle of the judged area."""
    w, h = min(int(size[0]), rgb.shape[1]), min(int(size[1]), rgb.shape[0])
    ys, xs = np.nonzero(area)
    x = int(min(max(xs.mean() - w / 2, 0), rgb.shape[1] - w))
    y = int(min(max(ys.mean() - h / 2, 0), rgb.shape[0] - h))
    return rgb[y:y + h, x:x + w]


def _round(x):
    if isinstance(x, float):
        return round(x, 4)
    if isinstance(x, dict):
        return {k: _round(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_round(v) for v in x]
    return x


def _write(result: dict, out_dir: str, sources: set) -> dict:
    """The four files. Returns `result` with its floats rounded to four places: what `ranking.json` holds."""
    result = _round(result)
    inp = result["input_dir"]
    where = {f: _target(os.path.join(out_dir, f), sources) for f in FILES}
    with open(where["ranking.json"], "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    cols = ["burst", "rank", "pick", "file", "other_files", "score", "sharp_rel", "sharpness", "exposure_penalty",
            "blown", "p50", "scorer", "judged_on", "why"]
    with open(where["ranking.csv"], "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(cols)
        for g in result["bursts"]:
            for j in g["shots"]:
                w.writerow([g["id"], j["rank"], "yes" if j["pick"] else "", j["files"][0], "; ".join(j["files"][1:]),
                            j["score"], j["sharp_rel"], j["sharpness"], j["exposure_penalty"], j["blown"], j["p50"],
                            "" if j["scorer"] is None else j["scorer"], j["judged_on"], " | ".join(j["why"])])
        for x in result["failed"]:
            w.writerow([x["burst"], "", "yes", x["files"][0], "; ".join(x["files"][1:]), "", "", "", "", "", "", "", "",
                        f"could not be judged and is kept: {x['error']}"])
        for s in result["singles"]:
            w.writerow(["", 1, "yes", s[0], "; ".join(s[1:]), "", "", "", "", "", "", "", "", "single frame"])
    with open(where["picks.txt"], "w", encoding="utf-8") as fh:
        fh.write("".join(os.path.join(inp, f) + "\n" for f in result["picks"]))
    with open(where["contact-sheet.html"], "w", encoding="utf-8") as fh:
        fh.write(_sheet(result))
    return result


def _sheet(result: dict) -> str:
    e = html.escape
    sections = []
    for g in result["bursts"]:
        n = len(g["shots"])
        head = f"{g['id']} · {n} shot{'s' if n != 1 else ''}"
        if g["margin"] is not None:
            head += f" · the pick is ahead by {g['margin']:.2f}"
        cards = []
        for j in g["shots"]:
            f = j["files"][0]
            pics = "".join(f'<img src="{quote(d + "/" + f + ".jpg")}" alt="{e(f)}" loading="lazy">'
                           for d in ("thumbs", "crops"))
            cards.append(f'<article{" class=" + chr(34) + "pick" + chr(34) if j["pick"] else ""}>'
                         f'<div class="pics">{pics}</div>'
                         f'<h3>{j["rank"]}{" · PICK" if j["pick"] else ""} · {j["score"]:.2f}</h3>'
                         f'<p>{e(" · ".join(j["files"]))}</p><code>{e("; ".join(j["why"]))}</code></article>')
        sections.append(f"<section><h2>{e(head)}</h2><div class=\"row\">{''.join(cards)}</div></section>")
    failed = ""
    if result["failed"]:
        items = "".join(f"<li>{e(', '.join(x['files']))} ({e(x['burst'])}): {e(x['error'])}</li>" for x in result["failed"])
        failed = f"<section><h2>Could not be judged, kept as picks</h2><ul>{items}</ul></section>"
    singles = sum(len(s) for s in result["singles"])
    stub = (" The stub backend judged an oval in the middle of every frame; --backend sam3 judges the subject's own outline."
            if result["backend"] == "stub" and result["bursts"] else "")
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>AutoEdit picks</title>
  <style>
    body {{ margin: 24px; font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #101114; color: #f3f4f6; }}
    h1 {{ font-size: 22px; margin: 0 0 6px; }}
    h2 {{ font-size: 15px; margin: 22px 0 8px; color: #cbd5e1; }}
    h3 {{ font-size: 13px; letter-spacing: .08em; text-transform: uppercase; margin: 10px 12px 4px; color: #cbd5e1; }}
    .row {{ display: flex; flex-wrap: wrap; gap: 14px; }}
    article {{ background: #181a20; border: 2px solid #30333d; border-radius: 8px; overflow: hidden; width: 380px; }}
    article.pick {{ border-color: #22c55e; }}
    img {{ width: 100%; display: block; background: #252832; }}
    p {{ margin: 0 12px 8px; font-size: 13px; overflow-wrap: anywhere; }}
    code {{ display: block; margin: 0 12px 12px; font-size: 11px; color: #9ca3af; overflow-wrap: anywhere; }}
  </style>
</head>
<body>
  <header>
    <h1>AutoEdit picks</h1>
    <p>{len(result['bursts'])} bursts; {singles} single frames are not shown (each is kept). No photo was moved or changed.{e(stub)}</p>
  </header>
  {''.join(sections)}
  {failed}
</body>
</html>
"""


def run(folder, out_dir, cfg, model_path=None, copy=False, configs_dir=None, profile=None, log=None) -> dict:
    say = log or (lambda _msg: None)
    st = load_settings(configs_dir, profile)
    concepts = list(cfg.concepts or st["measure"]["concepts"])
    picked = os.path.join(out_dir, "picked")
    if copy and os.path.isdir(picked) and os.listdir(picked):
        raise ValueError(f"{picked} already holds files; empty it or choose another output folder")
    if _not_ours(out_dir):
        raise ValueError(f"{out_dir} already holds {', '.join(_not_ours(out_dir))} not written by pick; "
                         "choose another output folder")
    try:
        model = aesthetic.load_model(model_path) if model_path else None
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise ValueError(f"{model_path}: not a scorer model ({e})") from None
    images = batch.list_images(folder)
    sources = emit.source_ids(images)
    thr = {**consistency.BURST_DEFAULTS, **rules_mod.load(cfg.rules_path).get("consistency", {}), **st.get("burst", {})}
    say(f"looking for bursts in {len(images)} photos")
    bursts, singles = groups_of(images, thr)
    say(f"{len(bursts)} bursts with {sum(len(b) for b in bursts)} shots; {len(singles)} single frames")
    backend = segment.resolve_backend(cfg.backend)
    if bursts and backend == "stub":
        say("backend stub: every frame is judged in an oval in the middle of the frame; "
            "--backend sam3 judges the subject's own outline")
    seg = segment.make_segmenter(backend, cfg.device) if bursts else None
    if seg is not None and backend != "stub" and cfg.mask_cache:
        seg = segment.DiskMaskCache(seg, os.path.join(out_dir, segment.MASK_CACHE_DIR, backend))
    for sub in ("thumbs", "crops"):
        os.makedirs(os.path.join(out_dir, sub), exist_ok=True)
    name = os.path.basename
    groups, failed = [], []
    for n, burst in enumerate(bursts, start=1):
        judged = []
        for shot in burst:
            try:
                numbers, rgb, area = judge(shot[0], seg, st, concepts, raw_opts_from(cfg), model)
                for sub, pic in (("thumbs", resize_long_edge(rgb, int(st["sheet"]["thumb"]))),
                                 ("crops", _window(rgb, area, st["sheet"]["crop"]))):
                    save_image(pic, _target(os.path.join(out_dir, sub, name(shot[0]) + ".jpg"), sources),
                               fmt="jpeg", quality=90)
            except OnAPhoto:
                raise
            except Exception as e:
                failed.append({"files": [name(f) for f in shot], "burst": f"b{n}", "error": str(e)})
                continue
            judged.append({"files": [name(f) for f in shot], **numbers})
        order = rank_burst(judged, st["weights"]) if judged else []
        for j in order:
            j["why"] = _why(j, concepts)
        groups.append({"id": f"b{n}", "margin": order[0]["score"] - order[1]["score"] if len(order) > 1 else None,
                       "shots": order})
        if order:
            say(f"[b{n}] {len(order)} shots, pick {order[0]['files'][0]}"
                + (f" (ahead of {order[1]['files'][0]} by {groups[-1]['margin']:.2f})" if len(order) > 1 else ""))
    picks = sorted([f for g in groups for j in g["shots"] if j["pick"] for f in j["files"]]
                   + [name(f) for s in singles for f in s] + [f for x in failed for f in x["files"]])
    result = {
        "engine_version": __version__, "input_dir": os.path.abspath(folder), "backend": backend,
        "concepts": concepts, "scorer": os.path.abspath(model_path) if model_path else "",
        "settings": st, "burst_finder": {k: thr[k] for k in ("max_gap_s", "max_span_s", "min_similarity")},
        "photos": len(images), "bursts": groups, "singles": [[name(f) for f in s] for s in singles],
        "failed": failed, "picks": picks,
    }
    result = _write(result, out_dir, sources)
    if copy:
        os.makedirs(picked, exist_ok=True)
        for f in picks:
            shutil.copy2(os.path.join(folder, f), _target(os.path.join(picked, f), sources))
    return result
