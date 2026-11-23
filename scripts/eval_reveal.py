"""Measure the reveal check (F21, task T0): does SAM 3 find the things on the list?

    PYTHONPATH=src python3 scripts/eval_reveal.py label FOLDER [--out DIR]
    PYTHONPATH=src .venv/bin/python scripts/eval_reveal.py run FOLDER --labels labels.csv [--tiles] [--limit N]

`label` writes a local page; tick what each photo holds and download labels.csv (`--labels` starts
from an earlier file, `--results` shows the disputed photos first, with crops). `run` measures SAM 3
on the labelled photos the way the engine does (every word on its own; `--config FILE` measures the
words of FILE), appends one line per photo to results.jsonl and writes report.md. The report is built by `score`, which knows nothing about files or models. Nothing leaves
this computer and no metadata value of a photo is read or stored.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import html
import json
import os
import statistics
import sys
import time

import numpy as np
from PIL import Image as PILImage

from autoedit import reveal
from autoedit.batch import list_images
from autoedit.imageio import Image, downscale_long_edge, load_image
from autoedit.paths import config_path
from autoedit.segment import MASK_CACHE_DIR, DiskMaskCache, make_segmenter, sam3_ready
from autoedit.segment.base import RegionMask

THRESHOLDS = [round(0.40 + 0.05 * i, 2) for i in range(12)]   # 0.40 ... 0.95
MAX_FALSE_ALARM = 0.10
TARGET = 0.02           # false alarms the rule aims for, as a share of the photos without the thing
MARGIN = 0.05           # how far under the weakest measured hit min_score stays
MIN_WITH = 10           # fewer photos with the thing: the kind is not measured
MAX_CROPS = 6           # crops per kind and photo on the labelling page
CROP_EDGE = 480
CROP_GROW = 3
CROP_MIN = 160
LABEL_FLOOR = THRESHOLDS[0]   # a finding at the floor already counts as "found" on the page
SEVEN = ("address", "screen", "document", "card", "badge", "key", "code")
PROXY_EDGE = 1536
PREVIEW_EDGE = 1280
TILE = 0.6


# --------------------------------------------------------------------------- label page

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reveal check labels</title>
<style>
body{font:15px/1.4 system-ui,sans-serif;margin:0 auto;max-width:960px;padding:16px;background:#fafafa;color:#222}
.top{position:sticky;top:0;background:#fafafa;padding:8px 0;border-bottom:1px solid #ccc;z-index:1}
.photo{display:flex;gap:16px;margin:16px 0;padding:12px;background:#fff;border:1px solid #ddd;border-radius:6px}
.photo img{width:320px;max-width:45vw;height:auto;display:block}
.name{font-weight:600;word-break:break-all;margin-bottom:6px}
label{display:block;margin:2px 0}
.seen{margin-top:8px;padding-top:6px;border-top:1px solid #eee}
button{font:inherit;padding:6px 14px}
.crops img{height:auto;margin:2px 6px 2px 0;border:1px solid #ccc;vertical-align:top}
.crops figcaption{font-size:12px;color:#555}
.crops figure{display:inline-block;margin:0 6px 6px 0}
.note{color:#a40;font-size:13px}
</style></head><body data-folder="__FOLDER__">
<div class="top">
<p>Tick a kind when the photo has at least one such thing that a person can read or recognise at full size. Tick 'looked at' for every photo you have judged, also when nothing is in it. Only photos marked 'looked at' are used.</p>
__MEANS__
<p><span id="count">looked at: 0 of __N__</span> <button id="dl" type="button">Download labels.csv</button></p>
</div>
__BLOCKS__
<script>
var KINDS = Array.prototype.map.call(document.querySelectorAll('#kinds span'), function (s) { return s.textContent; });
var KEY = 'reveal-eval:' + document.body.getAttribute('data-folder');
var blocks = Array.prototype.slice.call(document.querySelectorAll('.photo'));
function boxes(b) { return Array.prototype.slice.call(b.querySelectorAll('input[type=checkbox]')); }
function save() {
  var state = {};
  blocks.forEach(function (b) {
    state[b.getAttribute('data-name')] = boxes(b).filter(function (c) { return c.checked; })
      .map(function (c) { return c.value; });
  });
  try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {}
}
function count() {
  var n = blocks.filter(function (b) { return b.querySelector('input[value=seen]').checked; }).length;
  document.getElementById('count').textContent = 'looked at: ' + n + ' of ' + blocks.length;
}
function load() {
  var state = {};
  try { state = JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch (e) { state = {}; }
  blocks.forEach(function (b) {
    var on = state[b.getAttribute('data-name')] || [];
    boxes(b).forEach(function (c) { c.checked = on.indexOf(c.value) >= 0; });
  });
}
function q(s) { return /[",\\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; }
document.addEventListener('change', function () { save(); count(); });
document.getElementById('dl').addEventListener('click', function () {
  var rows = [['image', 'seen'].concat(KINDS).join(',')];
  blocks.forEach(function (b) {
    var on = boxes(b).filter(function (c) { return c.checked; }).map(function (c) { return c.value; });
    rows.push([q(b.getAttribute('data-name')), on.indexOf('seen') >= 0 ? 1 : 0]
      .concat(KINDS.map(function (k) { return on.indexOf(k) >= 0 ? 1 : 0; })).join(','));
  });
  var a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([rows.join('\\n') + '\\n'], {type: 'text/csv'}));
  a.download = 'labels.csv'; a.click();
});
load(); count();
</script></body></html>
"""


# What a tick means, shown on the page (full size, readable).
TICK_MEANS = {
    "address": "a house number, or a sign naming a street or a place",
    "screen": "switched on and facing the camera",
    "document": "paper with readable text (letter, envelope, parcel label, receipt, whiteboard with writing)",
    "card": "a bank, ID, membership or travel card, ticket or boarding pass, front side",
    "badge": "a name badge or lanyard card",
    "key": "a door or car key whose cut shows",
    "code": "a scannable barcode or QR code",
}


def default_out(folder: str) -> str:
    folder = os.path.abspath(folder)
    return os.path.join(os.path.dirname(folder), os.path.basename(folder) + "-reveal-eval")


def _preview(img: Image, dest: str, edge: int = PREVIEW_EDGE) -> None:
    proxy = downscale_long_edge(img, edge)
    px = (np.clip(proxy.rgb, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    PILImage.fromarray(px).save(dest, "JPEG", quality=85)


def _crop(img: Image, box: list[float], dest: str) -> None:
    """The finding's box grown CROP_GROW times (at least CROP_MIN pixels), from the full-size photo."""
    H, W = img.rgb.shape[:2]
    cx, cy = (box[0] + box[2]) / 2 * W, (box[1] + box[3]) / 2 * H
    w = int(min(W, max((box[2] - box[0]) * W * CROP_GROW, CROP_MIN)))
    h = int(min(H, max((box[3] - box[1]) * H * CROP_GROW, CROP_MIN)))
    x0 = int(min(max(cx - w / 2, 0), W - w))
    y0 = int(min(max(cy - h / 2, 0), H - h))
    _preview(Image(rgb=img.rgb[y0:y0 + h, x0:x0 + w]), dest, CROP_EDGE)


def _found(entry: dict | None, kind) -> list[dict]:
    """The findings of the kind's words at the floor, largest area first."""
    fs = [f for f in (entry or {}).get("proxy", [])
          if f["kind"] == kind.name and f["word"] in kind.words and f["score"] >= LABEL_FLOOR]
    return sorted(fs, key=lambda f: -(f["box"][2] - f["box"][0]) * (f["box"][3] - f["box"][1]))


def label_page(folder: str, out: str, labels: dict | None = None,
               results: dict | None = None) -> tuple[str, int, list[str]]:
    """Write DIR/label.html and the previews; (page path, photos in it, files that would not decode).

    With `labels` the kind ticks start from that file ("looked at" always starts empty). With
    `results` the photos where the findings and the ticks disagree come first, each with crops
    of what was found but not ticked."""
    cfg = reveal.load(config_path("reveal.toml", packaged_fallback=True))
    photos = list_images(folder)
    if not photos:
        return "", 0, []
    os.makedirs(os.path.join(out, "thumbs"), exist_ok=True)
    blocks, bad, first = [], [], 0
    for i, path in enumerate(photos, 1):
        name = os.path.basename(path)
        rel = f"thumbs/{i:04d}.jpg"
        try:
            img = load_image(path)
            _preview(img, os.path.join(out, rel))
        except Exception:
            bad.append(name)
            continue
        on = (labels or {}).get(name, {})
        extra, disputed = "", False
        if results is not None and name in results:
            figs, notes = [], []
            for k in cfg.kinds:
                fs, ticked = _found(results[name], k), on.get(k.name) == 1
                if fs and not ticked:
                    disputed = True
                    os.makedirs(os.path.join(out, "crops"), exist_ok=True)
                    for j, f in enumerate(fs[:MAX_CROPS], 1):
                        c = f"crops/{i:04d}-{k.name}-{j}.jpg"
                        _crop(img, f["box"], os.path.join(out, c))
                        figs.append(f'<figure><img src="{c}" alt=""><figcaption>found as: '
                                    f'{html.escape(k.label)}</figcaption></figure>')
                elif ticked and not fs:
                    disputed = True
                    notes.append(f'<div class="note">ticked, but the check found nothing: {html.escape(k.label)}</div>')
            extra = "".join(notes) + (f'<div class="crops">{"".join(figs)}</div>' if figs else "")
        e = html.escape(name, quote=True)
        ticks = "".join(f'<label><input type="checkbox" value="{html.escape(k.name, quote=True)}"'
                        f'{" checked" if on.get(k.name) == 1 else ""}> {html.escape(k.label)}</label>'
                        for k in cfg.kinds)
        block = (f'<div class="photo" data-name="{e}"><a href="{rel}" target="_blank">'
                 f'<img src="{rel}" alt="{e}" loading="lazy"></a><div><div class="name">{e}</div>{ticks}'
                 f'<label class="seen"><input type="checkbox" value="seen"> looked at</label>{extra}</div></div>')
        if disputed:
            blocks.insert(first, block)
            first += 1
        else:
            blocks.append(block)
    kinds = "".join(f"<span>{html.escape(k.name)}</span>" for k in cfg.kinds)
    means = "<ul>" + "".join(f"<li>{html.escape(k.label)}: {html.escape(TICK_MEANS[k.name])}</li>"
                             for k in cfg.kinds if k.name in TICK_MEANS) + "</ul>"
    key = os.path.basename(os.path.abspath(folder)) + (":prefilled" if labels else "")
    page = (_PAGE.replace("__FOLDER__", html.escape(key, quote=True))
            .replace("__N__", str(len(blocks)))
            .replace("__MEANS__", means)
            .replace("__BLOCKS__", f'<div id="kinds" hidden>{kinds}</div>\n' + "\n".join(blocks)))
    path = os.path.join(out, "label.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    return path, len(blocks), bad


def _no_folder(folder: str) -> bool:
    if os.path.isdir(folder):
        return False
    print(f"There is no folder {folder}. Put the photos into one folder and give its path.")
    return True


def cmd_label(args) -> int:
    if _no_folder(args.folder):
        return 2
    for f in (args.labels, args.results):
        if f and not os.path.isfile(f):
            print(f"There is no file {f}.")
            return 2
    cfg = reveal.load(config_path("reveal.toml", packaged_fallback=True))
    labels = read_labels(args.labels, [k.name for k in cfg.kinds]) if args.labels else None
    results = read_results(args.results) if args.results else None
    path, n, bad = label_page(args.folder, args.out or default_out(args.folder), labels, results)
    for name in bad:
        print(f"could not decode, left out: {name}")
    if not n:
        print(f"No photo that AutoEdit can read was found in {args.folder}. Nothing was written.")
        return 2
    print(f"labelling page: {path} ({n} photos)")
    return 0


# --------------------------------------------------------------------------- scoring

def _has(entry: dict, measure: str, kind: str, t: float, words) -> bool:
    return any(f["kind"] == kind and f["score"] >= t and f["word"] in words for f in entry.get(measure, []))


def _count(es: list[dict], measure: str, kind: str, t: float, words) -> int:
    return sum(_has(e, measure, kind, t, words) for e in es)


def _threshold(has: list[dict], no: list[dict], m: str, kind: str, words) -> dict | None:
    """min_score for one word set (rules 2 to 4 of F53); None when F21's rule finds no threshold."""
    curve = [(t, _count(has, m, kind, t, words), _count(no, m, kind, t, words)) for t in THRESHOLDS]
    t21 = next((t for t, _, a in curve if a <= MAX_FALSE_ALARM * len(no) + 1e-9), None)
    if t21 is None:
        return None
    keep = max(t for t, h, _ in curve if h == curve[0][1])     # the highest t that still finds every hit of 0.40
    cap = max(THRESHOLDS[0], round(keep - MARGIN, 2))
    free = [c for c in curve if t21 <= c[0] <= max(t21, cap)]
    goal = max(TARGET * len(no), min(a for _, _, a in free)) + 1e-9
    t, h, a = next(c for c in free if c[2] <= goal)
    return {"min_score": t, "hits": h, "hit_share": h / len(has), "false_alarms": a,
            "false_alarm_share": a / len(no) if no else 0.0, "t21": t21, "cap": cap,
            "curve": [{"t": c[0], "hits": c[1], "alarms": c[2]} for c in curve]}


def _decide(has: list[dict], no: list[dict], m: str, kind) -> tuple[dict | None, list[str], list[str]]:
    """(threshold, words kept, words dropped): while a word adds no photo, drop the one that leaves the fewest alarms."""
    words, dropped = list(kind.words), []
    r = _threshold(has, no, m, kind.name, words)
    while len(words) > 1:
        h0 = _count(has, m, kind.name, THRESHOLDS[0], words)
        tried = []
        for i, w in enumerate(words):
            rest = [x for x in words if x != w]
            if _count(has, m, kind.name, THRESHOLDS[0], rest) == h0:
                rr = _threshold(has, no, m, kind.name, rest)
                tried.append((rr["false_alarms"] if rr else float("inf"), -i, w, rr))
        if not tried:
            break
        _, _, w, r = min(tried, key=lambda c: c[:2])       # the later word in the file on a tie
        words.remove(w)
        dropped.append(w)
    return r, words, dropped


def score(labels: dict[str, dict[str, int]], results: dict[str, dict], cfg) -> dict:
    """The numbers of the report. Only photos that are labelled `seen` and measured count."""
    used = {n: results[n] for n, lab in labels.items() if lab.get("seen") == 1 and n in results}
    measures = ["proxy"] + (["tiles"] if used and all("tiles" in e for e in used.values()) else [])
    out: dict = {"used": len(used), "measures": measures, "kinds": {}}
    for kind in cfg.kinds:
        has = sorted(n for n in used if labels[n].get(kind.name) == 1)
        no = sorted(n for n in used if labels[n].get(kind.name) != 1)
        k: dict = {"label": kind.label, "with": len(has), "without": len(no), "min_score": None}
        out["kinds"][kind.name] = k
        if not has:
            k["verdict"] = "no photos with it: not measured"
            continue
        if len(has) < MIN_WITH:
            k["verdict"] = f"not measured: under {MIN_WITH} photos with it"
            continue
        # Each measurement gets its own words and threshold: a false alarm that only the tiles raise
        # must not lift the proxy's. The better of the two (the proxy on a tie: it is cheaper) decides.
        top = None
        for m in measures:
            r, kept, dropped = _decide([used[n] for n in has], [used[n] for n in no], m, kind)
            if r is None:
                k[m] = None
                continue
            k[m] = dict(r, kept=kept, dropped=dropped)
            if top is None or k[m]["hit_share"] > k[top]["hit_share"]:
                top = m
        if "tiles" in measures:
            k["tiles_gain"] = ((k["tiles"] or {}).get("hit_share", 0.0)
                               - (k["proxy"] or {}).get("hit_share", 0.0))
        if top is None:
            k["verdict"] = "leave off the default list"
            continue
        r = k[top]
        t, share, kept = r["min_score"], r["hit_share"], r["kept"]
        k["min_score"], k["best"], k["kept"], k["dropped"] = t, top, kept, r["dropped"]
        hit_names = {n for n in has if _has(used[n], top, kind.name, t, kept)}
        k["missed"] = [n for n in has if n not in hit_names]
        k["words"] = {w: sum(_has(used[n], top, kind.name, t, [w])
                             and not any(_has(used[n], top, kind.name, t, [o]) for o in kept if o != w)
                             for n in hit_names)
                      for w in kept}
        k["word_alarms"] = {w: sum(_has(used[n], top, kind.name, t, [w]) for n in no) for w in kept}
        if kind.name == "plate" and share < 0.9:
            k["verdict"] = "STOP: needs a second detector"
        elif share < 0.7:
            k["verdict"] = "weak"
        else:
            k["verdict"] = "ok"
    # Photos with an alarm (a finding of a kind the photo does not hold) at the recommended scores;
    # a kind without a recommendation counts as today (its own min_score, all its words).
    spec = {}
    for kind in cfg.kinds:
        k = out["kinds"][kind.name]
        if k["min_score"] is not None:
            spec[kind.name] = (k["best"], k["min_score"], k["kept"])
        elif k["verdict"] != "leave off the default list":
            spec[kind.name] = ("proxy", kind.min_score, kind.words)

    def alarmed(only):
        return sum(any(labels[n].get(kn) != 1 and _has(e, m, kn, t, w)
                       for kn, (m, t, w) in spec.items() if only is None or kn in only)
                   for n, e in used.items())
    out["alarm_any"], out["alarm_seven"] = alarmed(None), alarmed(SEVEN)
    out["median_seconds"] = {m: statistics.median(e[m + "_s"] for e in used.values())
                             for m in measures if used and all(m + "_s" in e for e in used.values())}
    out["build_tiles"] = any(k.get("tiles_gain", 0.0) >= 0.10 - 1e-9 for k in out["kinds"].values())
    return out


def render(s: dict) -> str:
    L = [f"# Reveal check measurement", "",
         f"Photos used: {s['used']}. The numbers describe these photos only.", ""]
    for name, k in s["kinds"].items():
        L.append(f"## {name} ({k['label']}): {k['verdict']}")
        L.append(f"- photos with it: {k['with']}, without: {k['without']}")
        for m in s["measures"]:
            r = k.get(m)
            if k["verdict"].startswith(("no photos", "not measured")):
                break
            if r is None:
                L.append(f"- {m}: no threshold from 0.40 to 0.95 keeps false alarms at 10% or less")
                continue
            L.append(f"- {m}: min_score {r['min_score']:.2f} (F21's rule {r['t21']:.2f}, cap {r['cap']:.2f}), "
                     f"hits {r['hits']} of {k['with']} ({r['hit_share']:.0%}), "
                     f"false alarms {r['false_alarms']} of {k['without']} ({r['false_alarm_share']:.0%})")
            L.append(f"  curve (score: hits/alarms): "
                     + ", ".join(f"{c['t']:.2f}: {c['hits']}/{c['alarms']}" for c in r["curve"]))
        if "tiles_gain" in k:
            L.append(f"- tiles gain: {k['tiles_gain']:+.0%}")
        if k["min_score"] is not None:
            L.append(f"- decided by: {k['best']} at min_score {k['min_score']:.2f}")
            L.append("- words dropped (they add no photo): " + (", ".join(k["dropped"]) or "none"))
            L.append("- words kept (hits only through this word): "
                     + ", ".join(f"{w} {a}" for w, a in k["words"].items()))
            L.append("- alarms through each word: "
                     + ", ".join(f"{w} {a}" for w, a in k["word_alarms"].items()))
            L.append("- missed: " + (", ".join(k["missed"]) or "none"))
        L.append("")
    L.append("## Overall")
    L.append(f"- photos with an alarm of any kind, at the recommended scores: {s['alarm_any']} of {s['used']}")
    L.append(f"- photos with an alarm of the seven ({', '.join(SEVEN)}): {s['alarm_seven']} of {s['used']}")
    for m, v in s["median_seconds"].items():
        what = "tiles, on top of the proxy pass" if m == "tiles" else m
        L.append(f"- median seconds per photo ({what}): {v:.1f}")
    L.append(f"- Build tiles: {'yes' if s['build_tiles'] else 'no'}")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- run

def read_labels(path: str, kinds: list[str]) -> dict[str, dict[str, int]]:
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            out[row["image"]] = {c: int(row.get(c) or 0) for c in ["seen"] + kinds}
    return out


def read_results(path: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    e = json.loads(line)
                    out[e["image"]] = e
    return out


def _flat(raw: list[RegionMask], cfg0) -> list[dict]:
    """Every word is its own kind (named as its kind), so two words on one area stay two findings."""
    found, _ = reveal.findings(raw, cfg0, "other")
    return [{"kind": f.kind, "word": f.word, "score": f.score, "box": f.box} for f in found]


def _tiles(img: Image, proxy: Image, raw: list[RegionMask], seg, wds: list[str]) -> list[RegionMask]:
    """The words on four overlapping quarters of the full-size image, joined onto the proxy's masks."""
    H, W = img.rgb.shape[:2]
    ph, pw = proxy.rgb.shape[:2]
    joined = {r.name: [r.mask.copy(), float(r.score)] for r in raw}
    th, tw = round(H * TILE), round(W * TILE)
    for y0 in (0, H - th):
        for x0 in (0, W - tw):
            quarter = downscale_long_edge(Image(rgb=img.rgb[y0:y0 + th, x0:x0 + tw]), PROXY_EDGE)
            px0, px1 = round(x0 * pw / W), round((x0 + tw) * pw / W)
            py0, py1 = round(y0 * ph / H), round((y0 + th) * ph / H)
            for r in seg.segment(quarter, wds):
                m = PILImage.fromarray(r.mask.astype(np.uint8) * 255).resize((px1 - px0, py1 - py0),
                                                                           PILImage.NEAREST)
                full, sc = joined.setdefault(r.name, [np.zeros((ph, pw), bool), 0.0])
                full[py0:py1, px0:px1] |= np.asarray(m) > 0
                joined[r.name][1] = max(sc, float(r.score))
    return [RegionMask(n, m, s) for n, (m, s) in joined.items()]


def cmd_run(args) -> int:
    if not sam3_ready():
        print("SAM 3 is not available here; run this with the project's virtual environment "
              "(.venv/bin/python scripts/eval_reveal.py run ...).")
        return 2
    if _no_folder(args.folder):
        return 2
    if not os.path.isfile(args.labels):
        print(f"There is no labels file {args.labels}. It is the labels.csv the labelling page downloads.")
        return 2
    cfg = reveal.load(args.config or config_path("reveal.toml", packaged_fallback=True))
    cfg0 = dataclasses.replace(cfg, max_findings=10_000,
                               kinds=tuple(dataclasses.replace(k, min_score=0.0, words=(w,))
                                           for k in cfg.kinds for w in k.words))
    wds = reveal.words(cfg)
    labels = read_labels(args.labels, [k.name for k in cfg.kinds])
    out = args.out or os.path.dirname(os.path.abspath(args.labels))
    os.makedirs(out, exist_ok=True)
    res_path = os.path.join(out, "results.jsonl")
    results = read_results(res_path)

    files = {os.path.basename(p): p for p in list_images(args.folder)}
    seen = [n for n, lab in labels.items() if lab["seen"] == 1]
    print(f"{len(labels) - len(seen)} rows skipped as not looked at")
    for n in seen:
        if n not in files:
            print(f"file missing, left out: {n}")
    todo = [n for n in seen if n in files]
    if args.limit:
        todo = todo[:args.limit]
    seg = DiskMaskCache(make_segmenter("sam3"), os.path.join(out, MASK_CACHE_DIR, "sam3"))
    for n in todo:
        e = results.get(n)
        if e and set(wds) <= set(e.get("words", [])) and (not args.tiles or "tiles" in e):
            continue
        try:
            img = load_image(files[n])
            proxy = downscale_long_edge(img, PROXY_EDGE)
            e = {"image": n, "words": wds}
            t0 = time.perf_counter()
            raw = seg.segment(proxy, wds)
            e["proxy_s"] = round(time.perf_counter() - t0, 3)
            e["proxy"] = _flat(raw, cfg0)
            if args.tiles:
                t0 = time.perf_counter()
                joined = _tiles(img, proxy, raw, seg, wds)
                e["tiles_s"] = round(time.perf_counter() - t0, 3)
                e["tiles"] = _flat(joined, cfg0)
        except Exception as exc:
            print(f"failed, left out: {n} ({type(exc).__name__})")
            continue
        results[n] = e
        with open(res_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(e) + "\n")
        print(f"measured {n}")
    text = render(score(labels, results, cfg))
    with open(os.path.join(out, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    lb = sub.add_parser("label", help="write the labelling page")
    lb.add_argument("folder")
    lb.add_argument("--out")
    lb.add_argument("--labels", help="start the kind ticks from this labels.csv (nothing is 'looked at')")
    lb.add_argument("--results", help="put the photos where the findings and the ticks disagree first")
    lb.set_defaults(fn=cmd_label)
    rn = sub.add_parser("run", help="measure SAM 3 on the labelled photos")
    rn.add_argument("folder")
    rn.add_argument("--labels", required=True)
    rn.add_argument("--out")
    rn.add_argument("--config", help="the reveal.toml whose words are measured (default: the packaged one)")
    rn.add_argument("--tiles", action="store_true")
    rn.add_argument("--limit", type=int, default=0)
    rn.set_defaults(fn=cmd_run)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
