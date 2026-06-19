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
           max_px: int = 1600, quality: int = 88) -> dict:
    """Render all three variants per photo, shuffle slots, write JPEGs + manifest.csv + index.html.
    Returns a summary dict. Quiet enough to call from a test."""
    images = batch.list_images(corpus)
    if limit:
        images = images[:limit]
    if not images:
        raise SystemExit(f"no supported images in {corpus}")

    os.makedirs(out, exist_ok=True)
    pipe = Pipeline.build(cfg)
    rng = random.Random(seed)

    manifest: list[tuple[str, int, str]] = []   # (photo_id, slot_index, method)
    photos_json: list[dict] = []

    for i, path in enumerate(images, start=1):
        pid = f"photo{i:03d}"
        pdir = os.path.join(out, pid)
        os.makedirs(pdir, exist_ok=True)

        img = load_image(path)
        work = downscale_long_edge(img, max_px)          # all three variants at one resolution
        g, _ = stats.compute(work, {})

        variants = {
            "unedited": work.rgb,
            "baseline": apply_ops(work.rgb, baseline_settings(g)),
            "autoedit": pipe.process_one(path, do_apply=True, image=work)[0],
        }

        order = list(METHODS)
        rng.shuffle(order)                                # per-photo blind shuffle
        slots = []
        for slot, method in enumerate(order):
            fname = f"slot{slot}.jpg"
            save_image(variants[method], os.path.join(pdir, fname), "jpeg", quality)
            manifest.append((pid, slot, method))
            slots.append(f"{pid}/{fname}")
        photos_json.append({"id": pid, "name": os.path.basename(path), "slots": slots})
        print(f"[{i}/{len(images)}] {os.path.basename(path)} → {pid} (shuffled)")

    with open(os.path.join(out, "manifest.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["photo", "slot", "method"])
        w.writerows(manifest)

    _write_html(os.path.join(out, "index.html"), photos_json)
    print(f"\nWrote {len(photos_json)} photos × 3 variants to {out}/")
    print(f"Open {os.path.join(out, 'index.html')} to rank blind, save results.csv, then:")
    print(f"  python3 scripts/blindtest.py --score {os.path.join(out, 'results.csv')}")
    return {"photos": len(photos_json), "out": out}


def score(results_path: str, manifest_path: str | None = None) -> dict:
    """Join blind picks against the hidden manifest and report win-rates (the gate number)."""
    manifest_path = manifest_path or os.path.join(os.path.dirname(results_path) or ".", "manifest.csv")
    slot_method: dict[tuple[str, int], str] = {}
    with open(manifest_path, newline="") as fh:
        for row in csv.DictReader(fh):
            slot_method[(row["photo"], int(row["slot"]))] = row["method"]

    wins: Counter = Counter()
    total = 0
    with open(results_path, newline="") as fh:
        for row in csv.DictReader(fh):
            method = slot_method.get((row["photo"], int(row["best_slot"])))
            if method:
                wins[method] += 1
                total += 1

    print(f"\nBlind-test results ({total} photos ranked):")
    for m in METHODS:
        pct = 100.0 * wins[m] / total if total else 0.0
        print(f"  {m:9s} {pct:5.1f}%  ({wins[m]}/{total})")
    ae, bl = wins["autoedit"], wins["baseline"]
    h2h = 100.0 * ae / (ae + bl) if (ae + bl) else 0.0
    print(f"\nGate — AutoEdit vs baseline head-to-head: {h2h:.1f}% "
          f"({'PASS' if ae > bl else 'MISS'})")
    return {"wins": dict(wins), "total": total, "head_to_head": h2h}


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
  :root{--bg:#15171c;--panel:#1d2027;--line:#2b2f38;--ink:#e8eaed;--mut:#9aa3af;--accent:#5b8cff}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);
    font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
  header{padding:14px 20px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:16px}
  header b{font-weight:650} .mut{color:var(--mut)}
  #prog{margin-left:auto;color:var(--mut)}
  .wrap{max-width:1500px;margin:0 auto;padding:20px}
  .grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
  .card{background:var(--panel);border:2px solid var(--line);border-radius:12px;overflow:hidden;
    cursor:pointer;transition:border-color .12s,transform .12s}
  .card:hover{border-color:var(--accent);transform:translateY(-2px)}
  .card img{width:100%;display:block;aspect-ratio:3/2;object-fit:contain;background:#0d0f13}
  .card .lab{padding:10px 12px;display:flex;justify-content:space-between;align-items:center}
  .key{width:22px;height:22px;border:1px solid var(--line);border-radius:6px;display:grid;
    place-items:center;color:var(--mut);font-size:12px}
  .bar{margin-top:18px;display:flex;gap:12px;align-items:center}
  button{background:var(--accent);color:#fff;border:0;border-radius:9px;padding:10px 18px;
    font-weight:600;cursor:pointer} button.ghost{background:#262a33;color:var(--ink)}
  button:disabled{opacity:.4;cursor:default}
  #done{display:none;text-align:center;padding:60px 20px}
  #done h2{font-size:22px} a.dl{display:inline-block;margin-top:14px}
</style></head><body>
<header><b>AutoEdit · blind test</b><span class="mut">Pick the photo you'd keep. No labels — trust your eye.</span>
  <span id="prog"></span></header>
<div class="wrap">
  <div id="stage">
    <div class="mut" id="pname"></div>
    <div class="grid" id="grid"></div>
    <div class="bar"><button class="ghost" id="back" disabled>← Back</button>
      <span class="mut" id="hint">Click a photo (or press 1 / 2 / 3)</span></div>
  </div>
  <div id="done">
    <h2>All done — thank you.</h2>
    <p class="mut">Download your picks and run the scorer to reveal the winner.</p>
    <a class="dl"><button id="dl">Download results.csv</button></a>
  </div>
</div>
<script>
const PHOTOS = /*__DATA__*/;
let i = 0; const picks = {};            // photo id -> chosen slot index
const grid = document.getElementById('grid'), prog = document.getElementById('prog');
const pname = document.getElementById('pname'), back = document.getElementById('back');

function render(){
  const p = PHOTOS[i];
  prog.textContent = (i+1) + ' / ' + PHOTOS.length;
  pname.textContent = p.name;
  grid.innerHTML = '';
  p.slots.forEach((src, slot) => {
    const c = document.createElement('div'); c.className = 'card';
    c.innerHTML = '<img src="'+src+'" loading="lazy"><div class="lab"><span class="mut">'
      +String.fromCharCode(65+slot)+'</span><span class="key">'+(slot+1)+'</span></div>';
    c.onclick = () => choose(slot);
    grid.appendChild(c);
  });
  back.disabled = i === 0;
}
function choose(slot){
  picks[PHOTOS[i].id] = slot;
  if (i < PHOTOS.length - 1){ i++; render(); } else { finish(); }
}
back.onclick = () => { if (i>0){ i--; render(); } };
document.addEventListener('keydown', e => {
  if (['1','2','3'].includes(e.key)) choose(+e.key - 1);
  else if (e.key === 'Backspace') back.click();
});
function finish(){
  document.getElementById('stage').style.display = 'none';
  document.getElementById('done').style.display = 'block';
}
document.getElementById('dl').onclick = () => {
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
    ap.add_argument("--configs-dir", dest="configs_dir", default=None)
    ap.add_argument("--backend", choices=["stub", "sam3"], default=None)
    ap.add_argument("--device", default=None)
    ap.add_argument("--style", default=None)
    ap.add_argument("--intensity", type=float, default=None)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--max-px", dest="max_px", type=int, default=1600)
    args = ap.parse_args()

    if args.score:
        score(args.score)
        return 0
    if not args.corpus:
        ap.error("give a corpus folder to render, or --score results.csv")

    cfg = make_config(args.configs_dir or paths.configs_dir(), backend=args.backend,
                      device=args.device, style=args.style, intensity=args.intensity)
    render(args.corpus, args.out, cfg, seed=args.seed, limit=args.limit, max_px=args.max_px)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
