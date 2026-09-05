"""Phase-2 blind-test harness — the go/kill gate from PLAN.md.

Renders every corpus photo several ways — **unedited** / a **global-auto baseline** / the
**camera**'s own render (the JPEG preview embedded in a RAW) / **AutoEdit** — into a per-photo,
shuffled, hash-labeled gallery plus a self-contained `index.html` you open in a browser to rank
blind (forced choice: pick the best). The page exports `results.csv`; `--score` then reveals the
win-rates with an exact binomial p (per photo AND per burst group). AutoEdit clearly beating the
baseline is the Phase-A gate; beating the camera is the honest pro bar for RAW corpora.

    # AutoEdit vs the global baseline, 8 photos per exposure stratum (dark / mid / bright)
    PYTHONPATH=src python3 scripts/blindtest.py RAW_DIR out-bt-baseline --backend sam3 --two-way --stratify 8
    # AutoEdit vs the camera render, same photos (same --seed → same stratified pick)
    PYTHONPATH=src python3 scripts/blindtest.py RAW_DIR out-bt-camera --backend sam3 --vs-camera --stratify 8
    open out-bt-baseline/index.html      # rank blind, then "Download results.csv" into that folder
    python3 scripts/blindtest.py --score out-bt-baseline/results.csv

Every gallery also writes `metrics.csv` — the objective tone numbers (tonemetrics) for every
variant next to the blind picks — so a win can be checked against what actually moved.

The baseline is a deliberately decent region-blind auto-correction — the bar AutoEdit must clear.
It lives here (eval scaffolding), NOT in the product rules.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import re
import sys
from collections import Counter

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autoedit import batch, paths, stats, tonemetrics as tm  # noqa: E402
from autoedit.cli import load_run_config  # noqa: E402
from autoedit.edit.ops import apply_ops  # noqa: E402
from autoedit.imageio import (camera_preview, downscale_long_edge, load_image, luma,  # noqa: E402
                              raw_opts_from, save_image)
from autoedit.pipeline import Pipeline  # noqa: E402

METHODS = ("unedited", "baseline", "autoedit")  # the classic 3-way labels (hidden behind slots)
ALL_METHODS = ("unedited", "baseline", "camera", "autoedit")
STRATA = ("dark", "mid", "bright")


def make_config(configs_dir=None, backend=None, device=None, style=None, intensity=None,
                raw_mode=None):
    """Build a RunConfig via the normal CLI loader, with everything defaulting to None so we only
    override what the harness exposes (the loader broke on the old stub's partial namespace)."""
    ns = argparse.Namespace()
    for k in ("config", "style", "prompt", "preset", "concepts", "look", "intensity",
              "reference", "reference_strength", "decider", "allow_cloud", "api_key", "llm_mode",
              "backend", "device", "raw_mode", "format", "quality", "analysis_long_edge", "emit",
              "compose", "vignette", "sharpen", "configs_dir"):
        setattr(ns, k, None)
    ns.configs_dir = configs_dir
    ns.backend = backend
    ns.device = device
    ns.style = style
    ns.intensity = intensity
    ns.raw_mode = raw_mode
    return load_run_config(ns)


_TRAIL = re.compile(r"(\d+)(?=\.[^.]+$)")


def burst_groups(paths_: list[str], gap: int = 2) -> dict[str, str]:
    """Group consecutive frame numbers (…3405, …3406, …3407) into one burst id, so `--score`
    can report significance with one vote per burst instead of treating near-duplicates as
    independent trials. Files without a trailing number are their own group."""
    nums = []
    for p in paths_:
        m = _TRAIL.search(os.path.basename(p))
        nums.append(int(m.group(1)) if m else None)
    order = sorted(range(len(paths_)), key=lambda i: (nums[i] is None, nums[i] or 0, paths_[i]))
    groups: dict[str, str] = {}
    gid, prev = 0, None
    for i in order:
        n = nums[i]
        if n is None or prev is None or n - prev > gap:
            gid += 1
        groups[paths_[i]] = f"b{gid:03d}"
        prev = n
    return groups


def stratify(images: list[str], per_stratum: int, raw_opts: dict, rng: random.Random) -> list[tuple[str, str]]:
    """Pick `per_stratum` photos from each source-brightness tercile (dark / mid / bright) by the
    quarter-res median luma, so the gate always sees dark, normal and highlight-risk frames.
    Returns [(path, stratum)] in corpus order."""
    lums = []
    for p in images:
        try:
            img = load_image(p, decode={**raw_opts, "half_size": True})
            lums.append((p, float(np.percentile(luma(downscale_long_edge(img, 256).rgb), 50))))
        except Exception as e:
            print(f"stratify: skipping {os.path.basename(p)} ({e})", file=sys.stderr)
    lums.sort(key=lambda t: t[1])
    n = len(lums)
    cuts = [lums[: n // 3], lums[n // 3: 2 * n // 3], lums[2 * n // 3:]]
    picked: dict[str, str] = {}
    for name, bucket in zip(STRATA, cuts):
        for p, _l in rng.sample(bucket, min(per_stratum, len(bucket))):
            picked[p] = name
    return [(p, picked[p]) for p in images if p in picked]


def baseline_settings(g) -> dict:
    """A 'decent one-slider auto': region-blind global correction from global stats. This is the
    bar AutoEdit must beat — intentionally competent, intentionally not clever."""
    s: dict = {"contrast": 10.0}
    if abs(g.mean_lum - 0.45) > 0.02:
        s["exposure"] = max(-0.4, min(0.4, -(g.mean_lum - 0.45) * 1.1))
    if g.mean_sat < 0.35:
        s["vibrance"] = 14.0
    if g.highlight_clip > 0.02:
        s["highlights"] = max(-50.0, -g.highlight_clip * 120.0 - 8.0)
    if g.shadow_clip > 0.05:
        s["shadows"] = min(35.0, g.shadow_clip * 80.0 + 6.0)
    return s


def render(corpus: str, out: str, cfg, seed: int = 7, limit: int | None = None,
           max_px: int = 1600, quality: int = 88, methods: tuple[str, ...] = METHODS,
           pipelines: dict | None = None, per_stratum: int | None = None, burst_gap: int = 2,
           metrics: bool = True) -> dict:
    """Render each photo's variants (`methods`), shuffle slots, write JPEGs + manifest.csv +
    index.html (+ metrics.csv). `methods` defaults to the classic three; ("baseline","autoedit")
    isolates regional-vs-global, ("camera","autoedit") is the honest RAW bar, ("rules_a","rules_b")
    with a `pipelines` map is the config-A-vs-B tuning gate. `per_stratum` picks that many photos
    from each source-brightness tercile instead of the first `limit`. Returns a summary dict."""
    images = batch.list_images(corpus)
    if not images:
        raise SystemExit(f"no supported images in {corpus}")
    rng = random.Random(seed)
    raw_opts = raw_opts_from(cfg)
    strata: dict[str, str] = {}
    if per_stratum:
        chosen = stratify(images, per_stratum, raw_opts, rng)
        images = [p for p, _s in chosen]
        strata = dict(chosen)
    elif limit:
        images = images[:limit]
    groups = burst_groups(images, burst_gap)

    os.makedirs(out, exist_ok=True)
    pipes: dict = dict(pipelines or {})
    if "autoedit" in methods and "autoedit" not in pipes:
        pipes["autoedit"] = Pipeline.build(cfg)

    manifest: list[tuple] = []   # (photo_id, slot_index, method, name, group, stratum)
    photos_json: list[dict] = []
    metric_rows: list[dict] = []
    camera_missing = 0

    for i, path in enumerate(images, start=1):
        pid = f"photo{i:03d}"
        pdir = os.path.join(out, pid)
        os.makedirs(pdir, exist_ok=True)

        img = load_image(path, decode=raw_opts)
        work = downscale_long_edge(img, max_px)          # all variants at one resolution
        g, _ = stats.compute(work, {})
        cam_rgb = camera_preview(path, max_px, like=work.rgb) if ("camera" in methods or metrics) else None

        def variant(method):                             # build only the methods we need
            nonlocal camera_missing
            if method == "unedited":
                return work.rgb
            if method == "baseline":
                return apply_ops(work.rgb, baseline_settings(g))
            if method == "camera":
                if cam_rgb is None:                      # JPEG source / no preview → the source itself
                    camera_missing += 1
                    return work.rgb
                return cam_rgb
            return pipes[method].process_one(path, do_apply=True, image=work)[0]

        order = list(methods)
        rng.shuffle(order)                                # per-photo blind shuffle
        slots = []
        src_d = tm.describe(work.rgb) if metrics else None
        cam_d = tm.describe(cam_rgb) if (metrics and cam_rgb is not None) else None
        for slot, method in enumerate(order):
            rgb = variant(method)
            fname = f"slot{slot}.jpg"
            save_image(rgb, os.path.join(pdir, fname), "jpeg", quality)
            manifest.append((pid, slot, method, os.path.basename(path), groups[path], strata.get(path, "")))
            slots.append(f"{pid}/{fname}")
            if metrics:
                row = tm.row_for(os.path.basename(path), src_d, tm.describe(rgb), cam_d)
                row.update(photo=pid, method=method, group=groups[path], stratum=strata.get(path, ""))
                metric_rows.append(row)
        photos_json.append({"id": pid, "name": os.path.basename(path), "slots": slots})
        tag = f" [{strata[path]}]" if path in strata else ""
        print(f"[{i}/{len(images)}] {os.path.basename(path)}{tag} → {pid} (shuffled)")

    with open(os.path.join(out, "manifest.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["photo", "slot", "method", "name", "group", "stratum"])
        w.writerows(manifest)
    if metric_rows:
        tm.write_csv(metric_rows, os.path.join(out, "metrics.csv"))
        print("\nObjective tone metrics per method (source-relative):")
        for m in methods:
            s = tm.summarize([r for r in metric_rows if r["method"] == m])
            cam = f"  median EV vs camera {s['median_ev_vs_camera']:+.2f}" if "median_ev_vs_camera" in s else ""
            print(f"  {m:9s} dark→darker {s['dark_made_darker']:3d}  pushed-to-clip {s['bright_pushed_to_clip']:3d}"
                  f"  bottom<0.20 {s['out_bottom_below_020']:3d}  darker-than-source {s['darker_than_source_mean']:3d}{cam}")
    if "camera" in methods and camera_missing:
        print(f"\nNOTE: {camera_missing} photo(s) had no camera preview (JPEG source?) — their "
              f"'camera' slot is the unedited source.", file=sys.stderr)

    _write_html(os.path.join(out, "index.html"), photos_json)
    print(f"\nWrote {len(photos_json)} photos × {len(methods)} variants ({'+'.join(methods)}) to {out}/"
          f" ({len(set(groups[p] for p in images))} burst groups)")
    print(f"Open {os.path.join(out, 'index.html')} to rank blind, save results.csv, then:")
    print(f"  python3 scripts/blindtest.py --score {os.path.join(out, 'results.csv')}")
    return {"photos": len(photos_json), "out": out, "groups": len(set(groups[p] for p in images)),
            "camera_missing": camera_missing}


def _binomial_p(wins_a: int, wins_b: int) -> float:
    """Two-sided exact binomial p-value for an A/B head-to-head under H0 p=0.5 (stdlib only).
    Small n is the norm here — report exact significance, not just a percentage."""
    import math

    n = wins_a + wins_b
    if n == 0:
        return 1.0
    k = max(wins_a, wins_b)
    tail = sum(math.comb(n, i) for i in range(k, n + 1)) / 2.0 ** n
    return min(1.0, 2.0 * tail)


def n_for_power(p_alt: float, alpha: float = 0.05, power: float = 0.80, n_max: int = 400) -> int | None:
    """Smallest number of independent head-to-head trials at which a true win-rate `p_alt` is
    detected (two-sided exact binomial, p < alpha) with probability ≥ `power`. Tells the owner how
    many *independent* photos a gate needs — a real 70% edge takes ~50, 65% ~90, 60% ~200; the
    12-photo galleries so far could only ever detect a landslide."""
    for n in range(5, n_max + 1):
        k_crit = next((k for k in range(n // 2, n + 1) if _binomial_p(k, n - k) < alpha), None)
        if k_crit is None:
            continue
        pw = sum(math.comb(n, i) * p_alt ** i * (1 - p_alt) ** (n - i) for i in range(k_crit, n + 1))
        if pw >= power:
            return n
    return None


def score(results_path: str, manifest_path: str | None = None,
          pair: tuple[str, str] | None = None) -> dict:
    """Join blind picks against the hidden manifest and report win-rates (the gate number).
    Method names come from the manifest, so 3-way, 2-way, camera and rules-A/B galleries all
    score. When the manifest carries burst groups, significance is ALSO reported with one
    majority vote per group (near-duplicate frames are not independent trials)."""
    manifest_path = manifest_path or os.path.join(os.path.dirname(results_path) or ".", "manifest.csv")
    slot_method: dict[tuple[str, int], str] = {}
    photo_group: dict[str, str] = {}
    photo_stratum: dict[str, str] = {}
    methods_seen: list[str] = []
    with open(manifest_path, newline="") as fh:
        for row in csv.DictReader(fh):
            slot_method[(row["photo"], int(row["slot"]))] = row["method"]
            if row.get("group"):
                photo_group[row["photo"]] = row["group"]
            if row.get("stratum"):
                photo_stratum[row["photo"]] = row["stratum"]
            if row["method"] not in methods_seen:
                methods_seen.append(row["method"])

    wins: Counter = Counter()
    per_photo: dict[str, str] = {}
    total = 0
    with open(results_path, newline="") as fh:
        for row in csv.DictReader(fh):
            method = slot_method.get((row["photo"], int(row["best_slot"])))
            if method:
                wins[method] += 1
                per_photo[row["photo"]] = method
                total += 1

    # Print order must be stable across runs/seeds — `methods_seen` is manifest row-insertion
    # order, which is the first photo's per-photo RANDOM shuffle order and so varies by --seed.
    # Sort by each method's position in the canonical METHODS tuple when it's one of the three
    # named methods, else alphabetically (covers rules_a/rules_b and any future custom pair) —
    # fully deterministic regardless of seed, unlike the raw first-seen order.
    print_order = sorted(
        methods_seen,
        key=lambda m: (METHODS.index(m) if m in METHODS else len(METHODS), m),
    )
    print(f"\nBlind-test results ({total} photos ranked):")
    for m in print_order:
        pct = 100.0 * wins[m] / total if total else 0.0
        print(f"  {m:9s} {pct:5.1f}%  ({wins[m]}/{total})")

    # Head-to-head pair must be declared independently of the observed wins. Picking the two most
    # common winners after scoring is selection-biased and can even yield a one-element tuple.
    if pair is not None:
        if len(pair) != 2 or pair[0] == pair[1] or any(m not in methods_seen for m in pair):
            raise ValueError(f"invalid comparison pair {pair!r}; methods are {methods_seen!r}")
        selected_pair = pair
    elif "autoedit" in methods_seen and "baseline" in methods_seen:
        selected_pair = ("autoedit", "baseline")
    elif len(methods_seen) == 2:
        # AutoEdit is always the challenger (first) so PASS/MISS reads the same in every gallery.
        other = [m for m in methods_seen if m != "autoedit"]
        selected_pair = ("autoedit", other[0]) if "autoedit" in methods_seen else (methods_seen[0], methods_seen[1])
    else:
        print("\nNo head-to-head significance test: gallery has more than two methods. "
              "Pass --pair METHOD_A METHOD_B to declare a comparison before scoring.")
        return {
            "wins": dict(wins), "total": total, "head_to_head": None, "p_value": None,
            "pair": [], "p_value_assumption": "independent-photo Bernoulli trials",
        }
    a, b = wins[selected_pair[0]], wins[selected_pair[1]]
    h2h = 100.0 * a / (a + b) if (a + b) else 0.0
    p = _binomial_p(a, b)
    print(f"\nGate — {selected_pair[0]} vs {selected_pair[1]} head-to-head: {h2h:.1f}% "
          f"({'PASS' if a > b else 'MISS'})  |  exact binomial p = {p:.3f}"
          f"{'  (significant under independence)' if p < 0.05 else '  (not significant)'}")
    out = {"wins": dict(wins), "total": total, "head_to_head": h2h, "p_value": p,
           "pair": list(selected_pair),
           "p_value_assumption": "independent-photo Bernoulli trials"}

    # Per-burst vote: near-duplicate frames share one vote (majority of the pair's picks; ties drop).
    if photo_group:
        votes: dict[str, Counter] = {}
        for pid, m in per_photo.items():
            if m in selected_pair:
                votes.setdefault(photo_group.get(pid, pid), Counter())[m] += 1
        ga = gb = 0
        for c in votes.values():
            if c[selected_pair[0]] > c[selected_pair[1]]:
                ga += 1
            elif c[selected_pair[1]] > c[selected_pair[0]]:
                gb += 1
        gp = _binomial_p(ga, gb)
        print(f"  Per burst group ({len(votes)} groups, one majority vote each): "
              f"{selected_pair[0]} {ga} – {gb} {selected_pair[1]}  |  p = {gp:.3f}"
              f"{'  (significant)' if gp < 0.05 else '  (not significant)'}")
        out.update(group_wins=[ga, gb], group_p_value=gp, groups=len(votes))
    else:
        print("  Assumption: each ranked photo is an independent trial; deduplicate or block burst/"
              "near-duplicate frames before interpreting significance.")
    if photo_stratum:
        print("  Per stratum:")
        for s in STRATA:
            pids = [pid for pid, st in photo_stratum.items() if st == s and pid in per_photo]
            if pids:
                sa = sum(1 for pid in pids if per_photo[pid] == selected_pair[0])
                sb = sum(1 for pid in pids if per_photo[pid] == selected_pair[1])
                print(f"    {s:7s} {selected_pair[0]} {sa} – {sb} {selected_pair[1]}")
        out["strata"] = {s: sum(1 for pid, st in photo_stratum.items() if st == s) for s in STRATA}
    need = {f"{int(100 * q)}%": n_for_power(q) for q in (0.60, 0.65, 0.70, 0.80)}
    print("  Sample size: independent trials needed to detect a true win-rate at 80% power, p<0.05 → "
          + ", ".join(f"{k}: {v}" for k, v in need.items()))
    out["n_for_power"] = need
    return out


def _write_html(path: str, photos: list[dict]) -> None:
    """A dependency-free single-file ranker. Inlines the photo/slot list; exports results.csv."""
    data = json.dumps(photos)
    html = _HTML_TEMPLATE.replace("/*__DATA__*/", data)
    with open(path, "w") as fh:
        fh.write(html)


_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AutoEdit — blind test</title>
<style>
  :root{
    --bg:#0e1013; --panel:#16191f; --line:#262b34; --ink:#eef1f5; --mut:#8b93a1;
    --accent:#6a97ff; --accent-soft:rgba(106,151,255,.16); --ok:#43c98b;
    --shadow:0 8px 30px rgba(0,0,0,.45);
  }
  *{box-sizing:border-box}
  html,body{height:100%}
  body{margin:0;background:var(--bg);color:var(--ink);
    font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    display:flex;flex-direction:column;overflow:hidden}

  /* ── header + progress ── */
  header{flex:0 0 auto;padding:16px 26px 0}
  .top{display:flex;align-items:baseline;gap:14px}
  .brand{font-weight:650;letter-spacing:.2px}
  .brand .dot{color:var(--accent)}
  .task{color:var(--mut);font-size:13.5px}
  .count{margin-left:auto;color:var(--mut);font-variant-numeric:tabular-nums;font-size:13.5px}
  .track{height:3px;background:var(--line);border-radius:3px;margin-top:14px;overflow:hidden}
  .fill{height:100%;width:0;background:var(--accent);border-radius:3px;transition:width .28s ease}

  /* ── stage: three-up comparison, sized to fill the viewport ── */
  main{flex:1 1 auto;min-height:0;display:flex;flex-direction:column;padding:14px 26px 8px}
  .name{color:var(--mut);font-size:12.5px;text-align:center;letter-spacing:.3px;margin-bottom:10px}
  .row{flex:1 1 auto;min-height:0;display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
  .opt{position:relative;min-width:0;background:var(--panel);border:1.5px solid var(--line);
    border-radius:14px;overflow:hidden;cursor:pointer;display:flex;align-items:center;
    justify-content:center;transition:border-color .14s,transform .14s,box-shadow .14s}
  .opt:hover{border-color:var(--accent);transform:translateY(-3px);box-shadow:var(--shadow)}
  .opt.pick{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
  .opt img{max-width:100%;max-height:100%;display:block;object-fit:contain}
  .num{position:absolute;top:12px;left:12px;width:26px;height:26px;border-radius:8px;
    background:rgba(10,12,16,.66);backdrop-filter:blur(4px);color:var(--ink);
    display:grid;place-items:center;font-size:13px;font-weight:600;opacity:.8;
    transition:opacity .14s,background .14s}
  .opt:hover .num{opacity:1;background:var(--accent)}

  /* ── footer hint ── */
  footer{flex:0 0 auto;display:flex;align-items:center;gap:16px;padding:12px 26px 18px;
    color:var(--mut);font-size:13px}
  .kbd{display:inline-block;min-width:18px;padding:1px 6px;border:1px solid var(--line);
    border-bottom-width:2px;border-radius:5px;background:#1b1f27;color:var(--ink);
    font:12px/1.4 ui-monospace,Menlo,monospace;text-align:center}
  .ghost{margin-left:auto;background:#1b1f27;color:var(--ink);border:1px solid var(--line);
    border-radius:9px;padding:8px 14px;font-weight:600;cursor:pointer;transition:opacity .14s}
  .ghost:disabled{opacity:.35;cursor:default}

  /* ── done ── */
  #done{flex:1 1 auto;display:none;flex-direction:column;align-items:center;justify-content:center;
    text-align:center;padding:40px;gap:6px}
  #done.show{display:flex}
  .check{width:56px;height:56px;border-radius:50%;background:var(--accent-soft);color:var(--ok);
    display:grid;place-items:center;font-size:28px;margin-bottom:8px}
  #done h2{margin:0;font-size:22px;font-weight:650}
  #done p{margin:0;color:var(--mut);max-width:440px}
  .cta{margin-top:18px;background:var(--accent);color:#fff;border:0;border-radius:10px;
    padding:12px 22px;font-weight:650;font-size:15px;cursor:pointer}
  .cmd{margin-top:20px;font:12.5px/1.5 ui-monospace,Menlo,monospace;color:var(--mut);
    background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 14px}
</style></head><body>
<header>
  <div class="top">
    <span class="brand">AutoEdit<span class="dot"> ·</span> blind test</span>
    <span class="task">Which edit is strongest? Trust your eye — the versions are unlabeled and shuffled.</span>
    <span class="count" id="count"></span>
  </div>
  <div class="track"><div class="fill" id="fill"></div></div>
</header>

<main id="stage">
  <div class="name" id="name"></div>
  <div class="row" id="row"></div>
</main>

<footer id="foot">
  <span>Click the one you'd keep</span>
  <span>·</span>
  <span id="keys"></span>
  <span>·</span>
  <span><span class="kbd">←</span> back</span>
  <button class="ghost" id="back" disabled>← Back</button>
</footer>

<div id="done">
  <div class="check">✓</div>
  <h2>All ranked — thank you.</h2>
  <p>Download your picks, drop <b>results.csv</b> into this folder, then run the scorer to reveal which edit won.</p>
  <button class="cta" id="dl">Download results.csv</button>
  <div class="cmd">python3 scripts/blindtest.py --score results.csv</div>
</div>

<script>
const PHOTOS = /*__DATA__*/;
let i = 0, busy = false;
const picks = {};                       // photo id -> chosen slot index
const $ = id => document.getElementById(id);
const row = $('row'), fill = $('fill'), count = $('count'), name = $('name'), back = $('back');

function render(){
  const p = PHOTOS[i], n = p.slots.length;
  count.textContent = (i + 1) + ' / ' + PHOTOS.length;
  fill.style.width = (i / PHOTOS.length * 100) + '%';
  name.textContent = p.name;
  row.style.gridTemplateColumns = 'repeat(' + n + ', 1fr)';   // adapts to 2- or 3-way
  $('keys').innerHTML = 'keys ' +
    p.slots.map((_, k) => '<span class="kbd">' + (k + 1) + '</span>').join(' ');
  row.innerHTML = '';
  p.slots.forEach((src, slot) => {
    const o = document.createElement('div');
    o.className = 'opt';
    o.innerHTML = '<img src="' + src + '" loading="lazy" alt="option ' + (slot+1) + '">' +
                  '<div class="num">' + (slot + 1) + '</div>';
    o.onclick = () => choose(slot, o);
    row.appendChild(o);
  });
  back.disabled = i === 0;
}
function choose(slot, el){
  if (busy) return;
  busy = true;
  picks[PHOTOS[i].id] = slot;
  el = el || row.children[slot];
  if (el) el.classList.add('pick');     // brief confirmation before advancing
  setTimeout(() => {
    busy = false;
    if (i < PHOTOS.length - 1){ i++; render(); } else { finish(); }
  }, 160);
}
back.onclick = () => { if (i > 0 && !busy){ i--; render(); } };
document.addEventListener('keydown', e => {
  const n = PHOTOS[i].slots.length;
  if (e.key >= '1' && e.key <= String(n)) choose(+e.key - 1);
  else if (e.key === 'ArrowLeft' || e.key === 'Backspace'){ e.preventDefault(); back.click(); }
});
function finish(){
  fill.style.width = '100%';
  $('stage').style.display = 'none';
  $('foot').style.display = 'none';
  $('done').classList.add('show');
}
$('dl').onclick = () => {
  let csv = 'photo,best_slot\n';
  for (const id in picks) csv += id + ',' + picks[id] + '\n';
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], {type:'text/csv'}));
  a.download = 'results.csv'; a.click();
};
render();
</script></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description="AutoEdit blind-test gate")
    ap.add_argument("corpus", nargs="?", help="folder of photos to render")
    ap.add_argument("out", nargs="?", default="dist/blindtest")
    ap.add_argument("--score", metavar="results.csv", help="reveal win-rates from a ranked results.csv")
    ap.add_argument("--pair", nargs=2, metavar=("METHOD_A", "METHOD_B"),
                    help="predeclare the head-to-head pair when scoring a gallery with >2 methods")
    ap.add_argument("--configs-dir", dest="configs_dir", default=None)
    ap.add_argument("--backend", choices=["stub", "sam3"], default=None)
    ap.add_argument("--device", default=None)
    ap.add_argument("--style", default=None)
    ap.add_argument("--intensity", type=float, default=None)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--max-px", dest="max_px", type=int, default=1600)
    ap.add_argument("--two-way", dest="two_way", action="store_true",
                    help="AutoEdit vs baseline only (drop 'unedited') — the sharpest regional-vs-global gate")
    ap.add_argument("--vs-camera", dest="vs_camera", action="store_true",
                    help="AutoEdit vs the camera's own render (embedded RAW preview) — the honest pro bar")
    ap.add_argument("--methods", help="explicit comma list from unedited,baseline,camera,autoedit")
    ap.add_argument("--stratify", type=int, metavar="N",
                    help="pick N photos from each source-brightness tercile (dark/mid/bright) instead of --limit")
    ap.add_argument("--burst-gap", dest="burst_gap", type=int, default=2,
                    help="frame-number gap that separates bursts for per-group significance (default 2)")
    ap.add_argument("--raw-mode", dest="raw_mode", default=None,
                    help="RAW development mode for every variant (none|libraw_auto|preview_ev|preview_ev_headroom|tone_target)")
    ap.add_argument("--no-metrics", dest="metrics", action="store_false", help="skip metrics.csv")
    ap.add_argument("--rules-a", dest="rules_a", help="rules.toml for config A (A/B tuning gate)")
    ap.add_argument("--rules-b", dest="rules_b", help="rules.toml for config B")
    ap.add_argument("--looks-a", dest="looks_a", help="optional looks.toml for config A")
    ap.add_argument("--looks-b", dest="looks_b", help="optional looks.toml for config B")
    ap.add_argument("--configs-a", dest="configs_a", help="whole configs dir for config A (rules+looks+defaults incl. [decode])")
    ap.add_argument("--configs-b", dest="configs_b", help="whole configs dir for config B")
    args = ap.parse_args()

    if args.score:
        score(args.score, pair=tuple(args.pair) if args.pair else None)
        return 0
    if not args.corpus:
        ap.error("give a corpus folder to render, or --score results.csv")
    if bool(args.rules_a) != bool(args.rules_b):
        ap.error("--rules-a and --rules-b must be given together")
    if bool(args.configs_a) != bool(args.configs_b):
        ap.error("--configs-a and --configs-b must be given together")

    cfg = make_config(args.configs_dir or paths.configs_dir(), backend=args.backend,
                      device=args.device, style=args.style, intensity=args.intensity,
                      raw_mode=args.raw_mode)
    common = dict(seed=args.seed, limit=args.limit, max_px=args.max_px, per_stratum=args.stratify,
                  burst_gap=args.burst_gap, metrics=args.metrics)

    if args.rules_a or args.configs_a:
        # Config-A-vs-config-B gate: same engine, two rule sets (or two whole configs dirs, which
        # also covers [decode] raw_mode). Segmentation is identical either way, so B shares A's
        # (cached) segmenter — one SAM pass per photo.
        from autoedit.segment import CachingSegmenter  # noqa: E402

        if args.configs_a:
            cfg_a = make_config(args.configs_a, backend=args.backend, device=args.device,
                                style=args.style, intensity=args.intensity, raw_mode=args.raw_mode)
            cfg_b = make_config(args.configs_b, backend=args.backend, device=args.device,
                                style=args.style, intensity=args.intensity, raw_mode=args.raw_mode)
        else:
            cfg_a = cfg.model_copy(update={"rules_path": args.rules_a,
                                           **({"looks_path": args.looks_a} if args.looks_a else {})})
            cfg_b = cfg.model_copy(update={"rules_path": args.rules_b,
                                           **({"looks_path": args.looks_b} if args.looks_b else {})})
        pipe_a, pipe_b = Pipeline.build(cfg_a), Pipeline.build(cfg_b)
        pipe_a.segmenter = CachingSegmenter(pipe_a.segmenter)
        pipe_b.segmenter = pipe_a.segmenter
        render(args.corpus, args.out, cfg_a, methods=("rules_a", "rules_b"),
               pipelines={"rules_a": pipe_a, "rules_b": pipe_b}, **common)
        return 0

    if args.methods:
        methods = tuple(m.strip() for m in args.methods.split(",") if m.strip())
        bad = [m for m in methods if m not in ALL_METHODS]
        if bad or len(methods) < 2:
            ap.error(f"--methods must name ≥2 of {ALL_METHODS}; got {methods}")
    elif args.vs_camera:
        methods = ("camera", "autoedit")
    elif args.two_way:
        methods = ("baseline", "autoedit")
    else:
        methods = METHODS
    render(args.corpus, args.out, cfg, methods=methods, **common)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
