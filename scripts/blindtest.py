"""Phase-2 blind-test harness — the go/kill gate from PLAN.md.

Renders every corpus photo three ways — **unedited** / a **global-auto baseline** / **AutoEdit** —
into a per-photo, shuffled, hash-labeled gallery plus a self-contained `index.html` you open in a
browser to rank blind (forced choice: pick the best). The page exports `results.csv`; `--score`
then reveals the win-rates. AutoEdit clearly beating the baseline is the Phase-A gate.

    # render the gallery (real SAM 3, first 10 photos)
    PYTHONPATH=src python3 scripts/blindtest.py "/path/to/Best" out-blindtest --backend sam3 --limit 10
    open out-blindtest/index.html        # rank blind, then "Download results.csv" into out-blindtest/
    python3 scripts/blindtest.py --score out-blindtest/results.csv

The baseline (variant B) is a deliberately decent region-blind auto-correction — the bar AutoEdit
must clear. It lives here (eval scaffolding), NOT in the product rules.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autoedit import batch, paths, stats  # noqa: E402
from autoedit.cli import load_run_config  # noqa: E402
from autoedit.edit.ops import apply_ops  # noqa: E402
from autoedit.imageio import downscale_long_edge, load_image, save_image  # noqa: E402
from autoedit.pipeline import Pipeline  # noqa: E402

METHODS = ("unedited", "baseline", "autoedit")  # the labels we keep hidden behind slots


def make_config(configs_dir=None, backend=None, device=None, style=None, intensity=None):
    """Build a RunConfig via the normal CLI loader, with everything defaulting to None so we only
    override what the harness exposes (the loader broke on the old stub's partial namespace)."""
    ns = argparse.Namespace()
    for k in ("config", "style", "prompt", "preset", "concepts", "look", "intensity",
              "reference", "reference_strength", "decider", "allow_cloud", "api_key", "llm_mode",
              "backend", "device", "format", "quality", "analysis_long_edge", "emit",
              "compose", "vignette", "sharpen", "configs_dir"):
        setattr(ns, k, None)
    ns.configs_dir = configs_dir
    ns.backend = backend
    ns.device = device
    ns.style = style
    ns.intensity = intensity
    return load_run_config(ns)


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
           pipelines: dict | None = None) -> dict:
    """Render each photo's variants (`methods`), shuffle slots, write JPEGs + manifest.csv +
    index.html. `methods` defaults to all three; pass ("baseline","autoedit") for a 2-way test
    that isolates the real contest (regional vs global), or ("rules_a","rules_b") with a
    `pipelines` map for a config-A-vs-config-B tuning gate. Returns a summary dict."""
    images = batch.list_images(corpus)
    if limit:
        images = images[:limit]
    if not images:
        raise SystemExit(f"no supported images in {corpus}")

    os.makedirs(out, exist_ok=True)
    pipes: dict = dict(pipelines or {})
    if "autoedit" in methods and "autoedit" not in pipes:
        pipes["autoedit"] = Pipeline.build(cfg)
    rng = random.Random(seed)

    manifest: list[tuple[str, int, str]] = []   # (photo_id, slot_index, method)
    photos_json: list[dict] = []

    for i, path in enumerate(images, start=1):
        pid = f"photo{i:03d}"
        pdir = os.path.join(out, pid)
        os.makedirs(pdir, exist_ok=True)

        img = load_image(path)
        work = downscale_long_edge(img, max_px)          # all variants at one resolution
        g, _ = stats.compute(work, {})

        def variant(method):                             # build only the methods we need
            if method == "unedited":
                return work.rgb
            if method == "baseline":
                return apply_ops(work.rgb, baseline_settings(g))
            return pipes[method].process_one(path, do_apply=True, image=work)[0]

        order = list(methods)
        rng.shuffle(order)                                # per-photo blind shuffle
        slots = []
        for slot, method in enumerate(order):
            fname = f"slot{slot}.jpg"
            save_image(variant(method), os.path.join(pdir, fname), "jpeg", quality)
            manifest.append((pid, slot, method))
            slots.append(f"{pid}/{fname}")
        photos_json.append({"id": pid, "name": os.path.basename(path), "slots": slots})
        print(f"[{i}/{len(images)}] {os.path.basename(path)} → {pid} (shuffled)")

    with open(os.path.join(out, "manifest.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["photo", "slot", "method"])
        w.writerows(manifest)

    _write_html(os.path.join(out, "index.html"), photos_json)
    print(f"\nWrote {len(photos_json)} photos × {len(methods)} variants ({'+'.join(methods)}) to {out}/")
    print(f"Open {os.path.join(out, 'index.html')} to rank blind, save results.csv, then:")
    print(f"  python3 scripts/blindtest.py --score {os.path.join(out, 'results.csv')}")
    return {"photos": len(photos_json), "out": out}


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


def score(results_path: str, manifest_path: str | None = None,
          pair: tuple[str, str] | None = None) -> dict:
    """Join blind picks against the hidden manifest and report win-rates (the gate number).
    Method names come from the manifest, so 3-way, 2-way, and rules-A/B galleries all score."""
    manifest_path = manifest_path or os.path.join(os.path.dirname(results_path) or ".", "manifest.csv")
    slot_method: dict[tuple[str, int], str] = {}
    methods_seen: list[str] = []
    with open(manifest_path, newline="") as fh:
        for row in csv.DictReader(fh):
            slot_method[(row["photo"], int(row["slot"]))] = row["method"]
            if row["method"] not in methods_seen:
                methods_seen.append(row["method"])

    wins: Counter = Counter()
    total = 0
    with open(results_path, newline="") as fh:
        for row in csv.DictReader(fh):
            method = slot_method.get((row["photo"], int(row["best_slot"])))
            if method:
                wins[method] += 1
                total += 1

    print(f"\nBlind-test results ({total} photos ranked):")
    for m in methods_seen:
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
        selected_pair = (methods_seen[0], methods_seen[1])
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
    print("  Assumption: each ranked photo is an independent trial; deduplicate or block burst/"
          "near-duplicate frames before interpreting significance.")
    return {"wins": dict(wins), "total": total, "head_to_head": h2h, "p_value": p,
            "pair": list(selected_pair),
            "p_value_assumption": "independent-photo Bernoulli trials"}


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
    ap.add_argument("--rules-a", dest="rules_a", help="rules.toml for config A (A/B tuning gate)")
    ap.add_argument("--rules-b", dest="rules_b", help="rules.toml for config B")
    ap.add_argument("--looks-a", dest="looks_a", help="optional looks.toml for config A")
    ap.add_argument("--looks-b", dest="looks_b", help="optional looks.toml for config B")
    args = ap.parse_args()

    if args.score:
        score(args.score, pair=tuple(args.pair) if args.pair else None)
        return 0
    if not args.corpus:
        ap.error("give a corpus folder to render, or --score results.csv")
    if bool(args.rules_a) != bool(args.rules_b):
        ap.error("--rules-a and --rules-b must be given together")

    cfg = make_config(args.configs_dir or paths.configs_dir(), backend=args.backend,
                      device=args.device, style=args.style, intensity=args.intensity)

    if args.rules_a:
        # Config-A-vs-config-B gate: same engine, two rules/looks files. Segmentation is
        # identical either way, so B shares A's (cached) segmenter — one SAM pass per photo.
        from autoedit.segment import CachingSegmenter  # noqa: E402

        cfg_a = cfg.model_copy(update={"rules_path": args.rules_a,
                                       **({"looks_path": args.looks_a} if args.looks_a else {})})
        cfg_b = cfg.model_copy(update={"rules_path": args.rules_b,
                                       **({"looks_path": args.looks_b} if args.looks_b else {})})
        pipe_a, pipe_b = Pipeline.build(cfg_a), Pipeline.build(cfg_b)
        pipe_a.segmenter = CachingSegmenter(pipe_a.segmenter)
        pipe_b.segmenter = pipe_a.segmenter
        render(args.corpus, args.out, cfg, seed=args.seed, limit=args.limit, max_px=args.max_px,
               methods=("rules_a", "rules_b"), pipelines={"rules_a": pipe_a, "rules_b": pipe_b})
        return 0

    methods = ("baseline", "autoedit") if args.two_way else METHODS
    render(args.corpus, args.out, cfg, seed=args.seed, limit=args.limit, max_px=args.max_px,
           methods=methods)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
