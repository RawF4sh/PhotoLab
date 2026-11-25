"""PROTOTYPE, throwaway (Auto design, 2026-11-25). Not part of AutoEdit.

Question: can the engine's existing controls turn the "before" of the owner's examples into their
"after"? For each same-photo pair it fits (A) whole-photo controls only, and (B) whole-photo plus
sky and land controls through a SAM 3 sky mask, by coordinate descent on a small proxy. It reports
how much of the edit each reproduces and the amounts it needed, and writes a page to flip between
before / fit A / fit B / after.

Run: .venv/bin/python -I scripts/prototype_auto_goal_fit.py <repo-root> <out-dir>
"""
import json, os, sys, time
import numpy as np
from PIL import Image as PI

root, out = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(root, "src"))
from autoedit.edit.ops import apply_ops, OP_BOUNDS          # noqa: E402
from autoedit.edit.oklab import linear_to_oklab             # noqa: E402

BRIEFS = os.path.join(root, "curated-web/owner/briefs")
# name: file, split axis (1 = side by side, 0 = stacked), (crop of each half: top, bottom) in px
PAIRS = {
    "bridge-twilight": ("bridge-blue-hour.webp", 1, (0, 60)),
    "dolomites-mist": ("dolomites-lake.webp", 1, (0, 75)),
    "tree-sunset": ("tree-and-valley-brief.png", 0, (42, 4)),
}
GLOBAL = ["exposure", "contrast", "curve", "highlights", "shadows", "whites", "blacks", "clarity",
          "dehaze", "vibrance", "saturation", "temp", "tint", "grade_shadows_temp",
          "grade_shadows_tint", "grade_highlights_temp", "grade_highlights_tint"]
REGION = ["exposure", "contrast", "saturation", "temp", "tint", "clarity"]


def halves(name):
    f, axis, (top, bot) = PAIRS[name]
    a = np.asarray(PI.open(os.path.join(BRIEFS, f)).convert("RGB"), np.float32) / 255
    if axis == 1:
        h, w = a.shape[:2]
        b, c = a[top:h - bot, : w // 2], a[top:h - bot, w - w // 2:]
    else:
        h = a.shape[0] // 2
        b, c = a[top:h - bot], a[h + top: 2 * h - bot]
    return align(b, c)


def edges(x):
    g = x.mean(2)
    return np.abs(np.diff(g, axis=0))[:, :-1] + np.abs(np.diff(g, axis=1))[:-1]


def align(b, c, r=14):
    """The before/after halves of a web composite may sit a few pixels apart: shift to match edges."""
    eb, ec = edges(b), edges(c)
    best = None
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            x = eb[r + dy: eb.shape[0] - r + dy, r + dx: eb.shape[1] - r + dx]
            y = ec[r: ec.shape[0] - r, r: ec.shape[1] - r]
            e = float(np.abs(x / (x.mean() + 1e-6) - y / (y.mean() + 1e-6)).mean())
            if best is None or e < best[0]:
                best = (e, dy, dx)
    _, dy, dx = best
    H, W = min(b.shape[0], c.shape[0]) - 2 * r, min(b.shape[1], c.shape[1]) - 2 * r
    return b[r + dy: r + dy + H, r + dx: r + dx + W], c[r: r + H, r: r + W], (dy, dx)


def lab(x):
    return linear_to_oklab(np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4))


def small(x, w=320):
    h = round(x.shape[0] * w / x.shape[1])
    return np.asarray(PI.fromarray((x * 255).round().astype(np.uint8)).resize((w, h), PI.LANCZOS), np.float32) / 255


LIGHT = {"vig_ev": "exposure", "grad_ev": "exposure", "grad_temp": "temp", "glow_ev": "exposure",
         "glow_temp": "temp", "glow_sat": "saturation"}


def light_masks(sky_a):
    """Shaped light from the engine's radial mask: a vignette, a top-down ramp inside the sky, and a
    glow around the brightest part of the sky (the sun)."""
    from autoedit.edit.mask import radial_alpha
    h, w = sky_a.shape
    vig = radial_alpha((h, w), 0.5, 0.5, 0.75, 0.75, feather=0.6, inside=False)
    rows = np.where(sky_a.mean(1) > 0.05)[0]
    bottom = rows.max() + 1 if len(rows) else h
    ramp = np.clip(1 - np.arange(h) / max(bottom, 1), 0, 1)[:, None] * sky_a
    return vig, ramp


def glow_mask(src, sky_a):
    from autoedit.edit.mask import radial_alpha
    from PIL import ImageFilter
    h, w = sky_a.shape
    lum = np.asarray(PI.fromarray((src.mean(2) * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(w / 30)),
                     np.float32) * (sky_a > 0.3)
    y, x = np.unravel_index(np.argmax(lum), lum.shape)
    return radial_alpha((h, w), x / w, y / h, 0.35, 0.35 * w / h, feather=0.8, inside=True)


def layer(o, alpha, settings):
    if not any(settings.values()):
        return o
    a = alpha[..., None]
    return np.clip(apply_ops(o, settings), 0, 1) * a + o * (1 - a)


def render(src, g, sky_a, sky, land, light=None, masks=None):
    o = np.clip(apply_ops(src, g), 0, 1)
    if sky_a is None:
        return o
    a = sky_a[..., None]
    s = np.clip(apply_ops(o, sky), 0, 1) if any(sky.values()) else o
    l_ = np.clip(apply_ops(o, land), 0, 1) if any(land.values()) else o
    o = s * a + l_ * (1 - a)
    if light:
        vig, ramp, glow = masks
        o = layer(o, vig, {"exposure": light.get("vig_ev", 0)})
        o = layer(o, ramp, {"exposure": light.get("grad_ev", 0), "temp": light.get("grad_temp", 0)})
        o = layer(o, glow, {"exposure": light.get("glow_ev", 0), "temp": light.get("glow_temp", 0),
                            "saturation": light.get("glow_sat", 0)})
    return o


def loss(x, target_lab):
    return float(np.abs(lab(x) - target_lab).sum(2).mean() * 100)


def fit(src, tgt, sky_a, masks=None):
    tl = lab(tgt)
    params = [("g", k) for k in GLOBAL] + ([("sky", k) for k in REGION] + [("land", k) for k in REGION]
                                            if sky_a is not None else [])
    if masks is not None:
        params += [("light", k) for k in LIGHT]
    val = {p: 0.0 for p in params}

    def run(v):
        pick = lambda grp: {k: v[(g, k)] for g, k in params if g == grp}
        return render(src, pick("g"), sky_a, pick("sky"), pick("land"), pick("light"), masks)

    cur = loss(run(val), tl)
    for step in (40, 20, 10, 5, 2.5):
        for _ in range(3):
            moved = False
            for p in params:
                op = LIGHT.get(p[1], p[1])
                s = step / 40 if op == "exposure" else step
                lo, hi = OP_BOUNDS[op]
                for d in (s, -s):
                    v = dict(val); v[p] = float(np.clip(val[p] + d, lo, hi))
                    e = loss(run(v), tl)
                    if e < cur - 1e-4:
                        val, cur, moved = v, e, True
                        break
            if not moved:
                break
    return val, cur, run


def sky_mask(img):
    from autoedit.imageio import Image
    from autoedit.segment.sam3 import Sam3Segmenter
    seg = sky_mask.seg = getattr(sky_mask, "seg", None) or Sam3Segmenter()
    ms = [m for m in seg.segment(Image(rgb=img), ["sky"]) if m.name == "sky"]
    if not ms:
        return np.zeros(img.shape[:2], np.float32)
    m = PI.fromarray((ms[0].mask * 255).astype(np.uint8)).resize(img.shape[1::-1], PI.BILINEAR)
    from PIL import ImageFilter
    return np.asarray(m.filter(ImageFilter.GaussianBlur(3)), np.float32) / 255


def save(x, p):
    PI.fromarray((np.clip(x, 0, 1) * 255).round().astype(np.uint8)).save(p, quality=95)


report = {}
for name in PAIRS:
    t0 = time.time()
    b, a, shift = halves(name)
    d = os.path.join(out, name); os.makedirs(d, exist_ok=True)
    sky = sky_mask(b)
    bs, as_, ss = small(b), small(a), small(np.repeat(sky[..., None], 3, 2))[..., 0]
    edit = loss(bs, lab(as_))
    va, la, _ = fit(bs, as_, None)
    vb, lb, _ = fit(bs, as_, ss)
    ms = (*light_masks(ss), glow_mask(bs, ss))
    vc, lc, _ = fit(bs, as_, ss, ms)
    msf = (*light_masks(sky), glow_mask(b, sky))
    # full size renders with the fitted values
    pick = lambda v, grp: {k: v[(g, k)] for g, k in v if g == grp}
    save(b, f"{d}/before.jpg"); save(a, f"{d}/after.jpg")
    save(render(b, pick(va, "g"), None, {}, {}), f"{d}/fit_global.jpg")
    save(render(b, pick(vb, "g"), sky, pick(vb, "sky"), pick(vb, "land")), f"{d}/fit_regions.jpg")
    save(render(b, pick(vc, "g"), sky, pick(vc, "sky"), pick(vc, "land"), pick(vc, "light"), msf), f"{d}/fit_light.jpg")
    save(np.repeat(sky[..., None], 3, 2), f"{d}/sky_mask.jpg")
    nz = lambda v: {f"{g}.{k}": round(x, 2) for (g, k), x in v.items() if abs(x) > 1e-9}
    report[name] = {
        "shift": shift, "sky_share": round(float(sky.mean()), 3),
        "edit_size": round(edit, 2),
        "global_only": {"left": round(la, 2), "reproduced": round(1 - la / edit, 3), "values": nz(va)},
        "with_regions": {"left": round(lb, 2), "reproduced": round(1 - lb / edit, 3), "values": nz(vb)},
        "with_light": {"left": round(lc, 2), "reproduced": round(1 - lc / edit, 3), "values": nz(vc)},
        "seconds": round(time.time() - t0, 1),
    }
    print(name, json.dumps({k: v for k, v in report[name].items() if not k.startswith(("global", "with"))}),
          "| global only reproduces", report[name]["global_only"]["reproduced"],
          "| with sky/land", report[name]["with_regions"]["reproduced"],
          "| with shaped light", report[name]["with_light"]["reproduced"], flush=True)

json.dump(report, open(f"{out}/report.json", "w"), indent=1)
views = [("before", "Before (theirs)"), ("fit_global", "Engine: whole-photo controls"),
         ("fit_regions", "Engine: + sky and land"), ("fit_light", "Engine: + shaped light"),
         ("after", "After (their edit)")]
cards = []
for name, r in report.items():
    vals = "<br>".join(f"<b>{k}</b>: " + ", ".join(f"{p} {x:+g}" for p, x in r[k]["values"].items())
                       for k in ("global_only", "with_regions", "with_light"))
    cards.append(f"""<section data-n="{name}"><h2>{name}: their edit is {r['edit_size']} units;
 whole-photo controls reproduce {r['global_only']['reproduced']:.0%}, with sky and land {r['with_regions']['reproduced']:.0%}, with shaped light {r['with_light']['reproduced']:.0%}</h2>
<div class="btns">{''.join(f'<button data-v="{v}">{i+1}. {t}</button>' for i, (v, t) in enumerate(views))}</div>
<img src="{name}/before.jpg"><p class="cap">Before (theirs)</p><details><summary>Fitted amounts</summary><p>{vals}</p></details></section>""")
open(f"{out}/index.html", "w").write("""<!doctype html><meta charset="utf-8"><title>Auto prototype</title>
<style>body{background:#111;color:#ddd;font:14px system-ui;margin:16px;max-width:1100px}img{max-width:100%;display:block}
button{margin:0 6px 6px 0;padding:6px 10px;background:#333;color:#eee;border:1px solid #555;border-radius:6px}
button.on{background:#2a6;border-color:#2a6}h2{font-size:15px;margin-top:28px}.cap{color:#9c9}</style>
<h1>PROTOTYPE: can the engine reach your examples?</h1>
<p>Each picture flips in place. Click a button, or hover a picture and press 1 to 5.</p>""" + "\n".join(cards) + """
<script>let cur=null;document.querySelectorAll('section').forEach(s=>{const img=s.querySelector('img'),cap=s.querySelector('.cap');
const show=b=>{img.src=s.dataset.n+'/'+b.dataset.v+'.jpg';cap.textContent=b.textContent;s.querySelectorAll('button').forEach(x=>x.classList.toggle('on',x===b))};
s.querySelectorAll('button').forEach(b=>b.onclick=()=>show(b));s.onmouseenter=()=>cur=s;show(s.querySelector('button'))});
document.addEventListener('keydown',e=>{if(cur&&e.key>='1'&&e.key<='5')cur.querySelectorAll('button')[+e.key-1].click()});</script>""")
print("page:", f"{out}/index.html")
