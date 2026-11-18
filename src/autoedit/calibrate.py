"""Taste calibration (F12): blind pairs that differ along one dimension of the look, read the
votes, move the look, repeat; the fitted look is saved into the user's profile.

A calibration folder holds `state.json` and one `round-N/` folder per round (a voting page,
`pairs.json`, the rendered images, and the `results.csv` the page downloads). Nothing here
touches the pipeline's stage order: a variant is just a different entry in `pipe.looks`.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import random
from collections.abc import Callable
from dataclasses import asdict
from types import SimpleNamespace

from . import curate, paths
from .imageio import downscale_long_edge, load_image, raw_opts_from, save_image
from .looks import Look

_DEFAULT = asdict(Look())
_UNBOUNDED = (-1e9, 1e9)
STATE = "state.json"
_CHOICES = ("1", "2", "tie")


def load_config(profile: str | None = None, configs_dir: str | None = None) -> dict:
    """calibrate.toml (a profile file of the same name merged over it) as
    {"run": {...}, "steps": {dim: {field: size}}, "labels": {dim: text}, "bounds": {field: (lo, hi)}}."""
    base = os.path.join(configs_dir or paths.configs_dir(), "calibrate.toml")
    if not os.path.exists(base):   # a configs folder that predates the file gets the packaged one
        base = os.path.join(paths._packaged(), "calibrate.toml")
    data = paths.read_config(base, profile)
    dims = data.get("dimension", {})
    return {
        "run": dict(data["run"]),
        "steps": {d: {k: float(v) for k, v in body.items() if k != "label"} for d, body in dims.items()},
        "labels": {d: str(body.get("label", d)) for d, body in dims.items()},
        "bounds": {k: (float(v[0]), float(v[1])) for k, v in data.get("bounds", {}).items()},
    }


def update(base: dict[str, float], steps: dict[str, dict[str, float]],
           votes: dict[str, tuple[int, int]], cfg: dict, bounds: dict) -> tuple[dict, dict]:
    """One round's update. votes[dimension] = (plus, minus). Returns (new base, new steps)."""
    new = dict(base)
    for dim, step in steps.items():
        plus, minus = votes.get(dim, (0, 0))
        n = plus + minus
        d = (plus - minus) / n if n else 0.0
        if n >= cfg["min_votes"] and abs(d) >= cfg["dead_zone"]:
            for field, size in step.items():
                lo, hi = bounds.get(field, _UNBOUNDED)
                new[field] = min(hi, max(lo, new.get(field, _DEFAULT[field]) + d * size))
    shrunk = {dim: {f: s * cfg["shrink"] for f, s in step.items()} for dim, step in steps.items()}
    return new, shrunk


def variant(base: dict[str, float], step: dict[str, float], sign: int, bounds: dict) -> Look:
    """The look at base + sign * step, inside the bounds."""
    vals = {**_DEFAULT, **base}
    for field, size in step.items():
        lo, hi = bounds.get(field, _UNBOUNDED)
        vals[field] = min(hi, max(lo, vals[field] + sign * size))
    return Look(**vals)


# ---- votes -------------------------------------------------------------------------------------

def read_votes(round_dir: str, pairs: list[dict], gallery_id: str) -> dict[str, tuple[int, int]]:
    """results.csv of a round as {dimension: (plus, minus)}; ties are left out. ValueError names
    whatever is wrong with the file."""
    path = os.path.join(round_dir, "results.csv")
    if not os.path.isfile(path):
        raise ValueError(f"no results.csv yet: open {os.path.join(round_dir, 'index.html')}, vote, "
                         f"and save the downloaded results.csv into {round_dir}")
    by_id = {p["id"]: p for p in pairs}
    seen: set[str] = set()
    tally = {p["dimension"]: [0, 0] for p in pairs}
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if not {"gallery_id", "pair", "choice"} <= set(reader.fieldnames or ()):
            raise ValueError(f"{path}: expected the columns gallery_id,pair,choice")
        for row in reader:
            gid, pid, choice = (row["gallery_id"] or "").strip(), (row["pair"] or "").strip(), (row["choice"] or "").strip()
            if gid != gallery_id:
                raise ValueError(f"{path} is from another round (gallery_id {gid!r}, this round is "
                                 f"{gallery_id!r}); save the file of this round's page here")
            if pid not in by_id:
                raise ValueError(f"{path}: unknown pair {pid!r}")
            if pid in seen:
                raise ValueError(f"{path}: pair {pid!r} is voted twice")
            if choice not in _CHOICES:
                raise ValueError(f"{path}: choice {choice!r} for pair {pid!r} must be 1, 2 or tie")
            seen.add(pid)
            if choice != "tie":
                tally[by_id[pid]["dimension"]][0 if int(choice) == by_id[pid]["plus_slot"] else 1] += 1
    return {d: (p, m) for d, (p, m) in tally.items()}


# ---- state -------------------------------------------------------------------------------------

def _read_state(out: str) -> dict:
    path = os.path.join(out, STATE)
    if not os.path.isfile(path):
        raise ValueError(f"{out} holds no calibration (no {STATE}); start one with: "
                         f"autoedit calibrate <photos> --out {out}")
    with open(path, encoding="utf-8") as fh:
        state = json.load(fh)
    # A hand-edited state must not name photos outside the photo folder.
    for photo in state.get("photos", []):
        parts = str(photo).split("/")
        if "\\" in str(photo) or any(p in ("", ".", "..") for p in parts) or os.path.splitdrive(str(photo))[0]:
            raise ValueError(f"{path}: the photo {photo!r} is not a path inside the photo folder")
    return state


def _fingerprint(profile: str) -> str:
    """Which profile folder a calibration belongs to, without storing the path itself."""
    return hashlib.sha256(os.path.realpath(profile).encode()).hexdigest()[:12]


def _write_state(out: str, state: dict) -> None:
    tmp = os.path.join(out, STATE + ".part")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
    os.replace(tmp, os.path.join(out, STATE))


def _run_config(state: dict, profile: str | None, configs_dir: str | None, backend: str | None = None):
    from .cli import load_run_config   # lazy: cli imports this module

    return load_run_config(SimpleNamespace(
        configs_dir=configs_dir, profile=profile, backend=backend or state["backend"],
        style=state["style"], look=state["look"]))


def _photo_dir(out: str, state: dict, photos: str | None) -> str:
    folder = photos or (os.path.normpath(os.path.join(out, state["photo_dir"])) if state.get("photo_dir") else "")
    if not folder or not os.path.isdir(folder):
        raise ValueError("the photo folder was not found" + (f": {folder}" if folder else "")
                         + "; give it again: autoedit calibrate <photos> --continue " + out)
    return folder


# ---- one round ---------------------------------------------------------------------------------

def _plan(state: dict, number: int) -> list[dict]:
    """The round's pairs: every photo against every dimension, shuffled so the pair id and the
    left/right slot say nothing about what differs."""
    rng = random.Random(f"{state['seed']}-{number}")
    pairs = [{"photo": p, "dimension": d, "plus_slot": rng.choice((1, 2))}
             for p in state["photos"] for d in state["steps"]]
    rng.shuffle(pairs)
    for i, pair in enumerate(pairs, 1):
        pair["id"] = f"q{i:02d}"
    return pairs


def _gallery_id(state: dict, number: int, pairs: list[dict]) -> str:
    key = json.dumps([state["seed"], number, [[p["id"], p["photo"], p["dimension"], p["plus_slot"]] for p in pairs]])
    return hashlib.blake2b(key.encode(), digest_size=6).hexdigest()


def _render_round(out: str, state: dict, photo_dir: str, cfg, number: int,
                  log: Callable[[str], None] | None) -> str:
    from .pipeline import Pipeline
    from .segment import CachingSegmenter

    pipe = Pipeline.build(cfg)
    pipe.segmenter = CachingSegmenter(pipe.segmenter, max_images=2)   # the 8 variants of a photo share masks
    pairs = _plan(state, number)
    gid = _gallery_id(state, number, pairs)
    folder = os.path.join(out, f"round-{number}")
    os.makedirs(folder, exist_ok=True)
    stale = os.path.join(folder, "results.csv")
    if os.path.exists(stale):   # a re-run of this round must not inherit votes for other images
        os.remove(stale)
    bounds, run = state["bounds"], state["run"]
    for i, photo in enumerate(state["photos"], 1):
        path = os.path.join(photo_dir, photo)
        if not os.path.isfile(path):
            raise ValueError(f"photo not found: {photo} in {photo_dir}")
        if log:
            log(f"round {number}: rendering photo {i} of {len(state['photos'])}")
        img = downscale_long_edge(load_image(path, decode=raw_opts_from(cfg)), int(run["render_long_edge"]))
        for pair in (p for p in pairs if p["photo"] == photo):
            for sign in (1, -1):
                pipe.looks[state["look"]] = variant(state["base"], state["steps"][pair["dimension"]], sign, bounds)
                rgb, _ = pipe.process_one(path, image=img)
                slot = pair["plus_slot"] if sign == 1 else 3 - pair["plus_slot"]
                save_image(rgb, os.path.join(folder, f"{pair['id']}_{'ab'[slot - 1]}.jpg"), "jpeg", 90)
    with open(os.path.join(folder, "pairs.json"), "w", encoding="utf-8") as fh:
        json.dump({"gallery_id": gid, "round": number, "pairs": sorted(pairs, key=lambda p: p["id"])}, fh, indent=2)
    page = _PAGE.replace("__TITLE__", f"AutoEdit calibration, round {number} of {state['rounds']}") \
                .replace("__DATA__", json.dumps([p["id"] for p in sorted(pairs, key=lambda p: p["id"])])) \
                .replace("__GALLERY__", json.dumps(gid))
    with open(os.path.join(folder, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page)
    return folder


def start(photos: str, out: str, *, count: int | None = None, rounds: int | None = None,
          seed: int | None = None, backend: str | None = None, style: str | None = None,
          look: str | None = None, name: str = "calibrated", profile: str | None = None,
          configs_dir: str | None = None, log: Callable[[str], None] | None = None) -> dict:
    """Round 1: pick photos, render the pairs, write the page. Returns {"round", "folder", "pairs"}."""
    if not os.path.isdir(photos):
        raise ValueError(f"photo folder not found: {photos}")
    if os.path.exists(os.path.join(out, STATE)):
        raise ValueError(f"{out} already holds a calibration; continue it with --continue {out} or pick another --out")
    where = profile or paths.default_profile_dir()   # fails now when profiles are off, not after all the voting
    packaged = paths.read_config(os.path.join(configs_dir or paths.configs_dir(), "looks.toml"))
    if name in packaged:
        raise ValueError(f"{name!r} is a packaged look; choose another name for the fitted look")
    conf = load_config(profile, configs_dir)
    run = dict(conf["run"])
    for key, val in (("count", count), ("rounds", rounds), ("seed", seed)):
        if val is not None:
            run[key] = int(val)
    if run["count"] < 1 or run["rounds"] < 1:
        raise ValueError("count and rounds must be at least 1")
    from .pipeline import Pipeline
    from .segment import resolve_backend
    from .styles import style_for

    cfg = _run_config({"backend": backend, "style": style, "look": look}, profile, configs_dir, backend)
    cfg = cfg.model_copy(update={"backend": resolve_backend(cfg.backend)})
    pipe = Pipeline.build(cfg)
    look_name = cfg.look or style_for(cfg.style, pipe.styles).look
    base = asdict(pipe.looks.get(look_name, Look()))

    # select_diverse is deterministic for a folder, so the same folder gives the same sample.
    out_abs = os.path.abspath(out)
    cands = [c for c in curate.scan(photos) if not os.path.abspath(c.path).startswith(out_abs + os.sep)]
    if not cands:
        raise ValueError(f"no readable photos in {photos}")
    chosen = curate.select_diverse(cands, run["count"])
    os.makedirs(out, exist_ok=True)
    try:
        rel_dir = os.path.relpath(photos, out)
    except ValueError:   # another drive on Windows: no relative path exists, so --continue is given the folder
        rel_dir = None
    state = {"version": 1, "round": 1, "rounds": run["rounds"], "seed": run["seed"], "name": name,
             "profile": _fingerprint(where), "look": look_name, "style": cfg.style, "backend": cfg.backend, "photo_dir": rel_dir,
             "photos": [os.path.relpath(c.path, photos).replace(os.sep, "/") for c in chosen],
             "base": base, "steps": conf["steps"], "bounds": {k: list(v) for k, v in conf["bounds"].items()},
             "run": run, "votes": {d: [0, 0] for d in conf["steps"]}, "done": False}
    folder = _render_round(out, state, photos, cfg, 1, log)
    _write_state(out, state)
    return {"round": 1, "folder": folder, "pairs": len(state["photos"]) * len(state["steps"])}


def advance(out: str, *, photos: str | None = None, finish: bool = False, profile: str | None = None,
            configs_dir: str | None = None, log: Callable[[str], None] | None = None) -> dict:
    """Read this round's votes and move on: the next round's page, or (after the last round, or
    with `finish`) the fitted look written into the profile. Returns a dict with "done", "base",
    "votes" (this round's) and "folder" or "look_path"."""
    state = _read_state(out)
    if state.get("done"):
        raise ValueError(f"this calibration is finished; the look {state['name']!r} is in your profile")
    if "profile" in state and state["profile"] != _fingerprint(profile or paths.default_profile_dir()):
        raise ValueError("this calibration was started with another profile; "
                         "pass the same --profile as when you started it")
    number = state["round"]
    folder = os.path.join(out, f"round-{number}")
    with open(os.path.join(folder, "pairs.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    for pair in meta["pairs"]:
        if pair.get("dimension") not in state["steps"]:
            raise ValueError(f"{os.path.join(folder, 'pairs.json')}: unknown dimension {pair.get('dimension')!r}")
    votes = read_votes(folder, meta["pairs"], meta["gallery_id"])
    steps = {d: dict(s) for d, s in state["steps"].items()}
    bounds = {k: tuple(v) for k, v in state["bounds"].items()}
    state["base"], state["steps"] = update(state["base"], steps, votes, state["run"], bounds)
    for d, (p, m) in votes.items():
        state["votes"][d] = [state["votes"][d][0] + p, state["votes"][d][1] + m]
    result = {"base": state["base"], "votes": votes, "totals": state["votes"], "name": state["name"]}
    if finish or number >= state["rounds"]:
        from . import looks as looks_mod
        from .cli import write_look

        fitted = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in state["base"].items()}
        where = profile or paths.default_profile_dir()
        mine = os.path.join(where, "looks.toml")
        result["replaced"] = os.path.isfile(mine) and state["name"] in paths.read_config(mine)
        habit = looks_mod.load_habits(paths.config_paths(configs_dir, profile)["looks_path"]).get(state["look"])
        result["look_path"] = write_look(where, state["name"], fitted, habit=habit)[0]
        state["done"] = True
        result["done"] = True
        _write_state(out, state)
        return result
    state["round"] = number + 1
    cfg = _run_config(state, profile, configs_dir)
    result["folder"] = _render_round(out, state, _photo_dir(out, state, photos), cfg, number + 1, log)
    result["done"] = False
    result["round"] = number + 1
    _write_state(out, state)   # last, so a failed render leaves the round open and --continue repeats it
    return result


# ---- the voting page ---------------------------------------------------------------------------

# Self-contained: no network request, no external font or script. It does not say what differs.
_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
  :root{color-scheme:light dark;--bg:#fff;--ink:#16181d;--mut:#5d6572;--line:#d6d9df;--panel:#f3f4f6;--accent:#2d5fd6}
  @media (prefers-color-scheme:dark){:root{--bg:#0e1013;--ink:#eef1f5;--mut:#9aa3b0;--line:#2a2f38;--panel:#16191f;--accent:#6a97ff}}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
  header{position:sticky;top:0;z-index:2;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 16px;
    display:flex;gap:16px;align-items:center;flex-wrap:wrap}
  header h1{font-size:16px;margin:0;flex:1 1 auto}
  button{font:inherit;padding:6px 14px;border:1px solid var(--accent);border-radius:6px;background:var(--accent);color:#fff;cursor:pointer}
  button:disabled{opacity:.4;cursor:default}
  main{max-width:1500px;margin:0 auto;padding:12px 16px 48px}
  section{margin:0 0 28px;padding-bottom:20px;border-bottom:1px solid var(--line)}
  .q{font-weight:600;margin:0 0 8px}
  .pics{display:grid;grid-template-columns:1fr 1fr;gap:8px}
  .pics img{width:100%;height:auto;display:block;background:var(--panel)}
  .opts{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}
  .opts label{flex:1 1 0;text-align:center;padding:8px;border:1px solid var(--line);border-radius:6px;cursor:pointer;background:var(--panel)}
  .opts input{position:absolute;opacity:0}
  .opts label:has(input:checked){border-color:var(--accent);outline:2px solid var(--accent)}
  .opts label:has(input:focus-visible){outline:2px dashed var(--accent)}
</style></head>
<body>
<header><h1>__TITLE__</h1><span id="count"></span><button id="dl" disabled>Download results.csv</button></header>
<main id="list"></main>
<script>
const IDS = __DATA__, GALLERY = __GALLERY__, picks = {};
const list = document.getElementById('list'), dl = document.getElementById('dl'), count = document.getElementById('count');
const NAMES = [['1', 'Left'], ['2', 'Right'], ['tie', 'No difference']];
function el(tag, cls, text){ const e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; }
function refresh(){
  const n = Object.keys(picks).length;
  count.textContent = n + ' of ' + IDS.length + ' answered';
  dl.disabled = n < IDS.length;
}
IDS.forEach(function(id, i){
  const s = el('section'), pics = el('div', 'pics'), opts = el('div', 'opts');
  s.appendChild(el('p', 'q', (i + 1) + '. Which do you prefer?'));
  ['a', 'b'].forEach(function(k){ const im = el('img'); im.src = id + '_' + k + '.jpg'; im.alt = ''; im.loading = 'lazy'; pics.appendChild(im); });
  NAMES.forEach(function(nm){
    const l = el('label'), r = el('input');
    r.type = 'radio'; r.name = id; r.value = nm[0];
    r.onchange = function(){ picks[id] = nm[0]; refresh(); };
    l.appendChild(r); l.appendChild(document.createTextNode(' ' + nm[1])); opts.appendChild(l);
  });
  s.appendChild(pics); s.appendChild(opts); list.appendChild(s);
});
dl.onclick = function(){
  let csv = 'gallery_id,pair,choice\n';
  IDS.forEach(function(id){ csv += GALLERY + ',' + id + ',' + picks[id] + '\n'; });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], {type: 'text/csv'}));
  a.download = 'results.csv'; a.click();
};
refresh();
</script></body></html>
"""
