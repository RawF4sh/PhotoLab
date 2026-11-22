# F47. Copy edits to similar photos

Status: spec (2026-11-19). Size M. Approved by the owner in chat on 2026-11-19. Default output
stays byte-identical: this is a new command, and every changed function keeps its old behaviour
when the new arguments are left out.

## Goal

`autoedit sync PHOTO [FOLDER]` takes the changes made to one photo after the run and applies them
to the other photos of the same scene and light. It then moves each photo's exposure toward the
reference photo, so the set matches.

## What exists, and what this feature adds

Most of the work exists. The feature adds the selection of similar photos and one loop.

| Exists | Used for |
|---|---|
| `adjust.rerender` (F3): re-renders one export from its sidecar, with its run settings and its own photo adjustments, and replays them on every later re-render | Rendering every similar photo. Five keyword arguments are added |
| Photo-scope `Adjustment`s in the sidecar: `autoedit adjust --set/--edit`, Studio follow-ups (F19), marks (F37) | What is copied (marks excepted) |
| `consistency.burst_biases` (F9): the exposure bias toward a burst's median, never a net cut, never into clipping | The bias toward the reference, through a new `anchor` argument |
| `RunConfig.wb` (F34): a white balance given as numbers | Copying the white balance of the reference's neutral pick |
| `consistency.capture_time`, `declare._source_dir` | The capture time of each photo's source |
| `rules.block` | Reading and checking `[sync]` in `rules.toml` |

Not used, and why:

- `edit/reference.py` (`--reference`) matches a look by pixel statistics. It does not carry the
  user's own changes.
- `consistency.find_bursts` finds repeats of one framing (F9, known limits). Another angle of the
  same scene is a similar photo too.
- `recipe.py`, `variants.py` and `workflows.py` predict, vary or reorder a look. None of them
  copies a decision.

## Design

### What is copied, and what never is

- **Copied:** the reference's photo-scope adjustments, global ones and region ones alike. A region
  adjustment works by concept name. Where the target has no such concept, the instruction planner
  marks it `not_found` and changes nothing. Places (`darken the top`, the glow on the light) are
  copied too: each photo finds its own top and its own light.
- **Copied as numbers:** the white balance of the reference's neutral pick (`audit.neutral`). The
  target gets `wb = [temp, tint]` and loses its own pick.
- **Never copied:** marks (`mark:area:...`, `mark:thing:...`): a mark is a place in the
  reference's frame. Straighten and crop: they are run settings, decided per photo from its own
  horizon and subject. The run's `--edit` and the look's habits: every photo of the run has them
  already. The rules' own decision: each photo keeps the decision the rules made from its own
  pixels.
- **Replaced:** a target's own photo adjustments. So a second sync gives the same result.

### What "similar" means

A sidecar directly in FOLDER (default: the reference's folder), other than the reference, is
similar when all of these hold:

1. It records its run settings (`audit.run_config`), so it can be re-rendered.
2. Its `scene` equals the reference's.
3. Its source photo (found through `run.json`) has a capture time, and that time is at most
   `[sync] minutes` (default 10) before or after the reference's.

Brightness is not tested. It is matched instead. With auto exposure the source brightness does not
show the light, and a RAW file decodes about 1.5 EV below its JPEG (F9).

### How exposure is matched

Compare a plan with a plan, as F9 does. The banding gate can damp the settings a render records,
so a level read from a written sidecar is not comparable with a planned one (measured on F9's
synthetic burst: the reference's sidecar level was -2.94, its plan -2.81).

1. Plan the reference as it is: `adjust.rerender(ref, apply=False)`. This keeps its own changes and
   its stored bias.
2. Plan each target with the copied changes and no bias: `apply=False, exposure_bias=0.0`.
3. The bias is `burst_biases([anchor, target], max_ev, 0.0, anchor=0)[1][0]`. That is the level gap,
   clamped to `max_ev`, never a net cut, never into clipping.
4. Render the target with that bias and `batch_group = "sync:<reference stem>"`. The bias is stored
   in `exposure_bias`, so `autoedit adjust` replays it.

White balance is not evened, apart from a copied pick.

## Files

| File | Change |
|---|---|
| `src/autoedit/consistency.py` | `level`, `decided_from`, `burst_biases(..., anchor=None)` |
| `src/autoedit/adjust.py` | `rerender(..., adjustments=(), exposure_bias=None, batch_group=None, wb=None, apply=True)` |
| `src/autoedit/sync.py` | new: `settings`, `similar`, `copied`, `run`, `lines` |
| `src/autoedit/cli.py` | `sync` command, one docstring line |
| `src/autoedit/configs/rules.toml` | `[sync]` |
| `tests/test_sync.py` | new |

Not touched: `pipeline.py`, `edit/`, `decide/`, `emit.py`, `batch.py`, the web layer.

### `consistency.py`

Add `level` and `decided_from`, and give `burst_biases` an `anchor`. `_decided` keeps its
behaviour.

```python
def level(d: dict) -> float:
    """The foreground median after the decided lift, in stops: what bursts and sync even out."""
    return 2.2 * math.log2(max(d["fg_p50"], 1e-3)) + d["exposure"] + 0.5 * d["shadows_ev"]


def decided_from(r) -> dict:
    """The numbers the evening reads, from a Result (a planned decision or a written sidecar)."""
    gs, st = r.global_settings, r.global_stats
    return {"fg_p50": st.fg_p50, "headroom_ev": st.headroom_ev,
            "exposure": gs.get("exposure", 0.0), "shadows_ev": gs.get("shadows_ev", 0.0),
            "temp": gs.get("temp", 0.0), "tint": gs.get("tint", 0.0),
            "wb_temp": st.wb_temp_correction, "wb_tint": st.wb_tint_correction, "wb_conf": st.wb_confidence}


def _decided(pipeline, path: str) -> dict | None:
    """What the rules decided for one photo (one `process_one`, no pixels), or None when it cannot be measured."""
    try:
        return decided_from(pipeline.process_one(path, do_apply=False)[1])
    except Exception:
        return None


def burst_biases(decided: list[dict], max_ev: float, max_wb: float,
                 anchor: int | None = None) -> list[tuple[float, float, float]]:
    """Per frame of one burst: (exposure, temp, tint) bias toward the burst's median, or with `anchor`
    toward that frame (F47: the reference of `autoedit sync`). `decided` holds, per frame: fg_p50,
    exposure, shadows_ev, temp, tint, headroom_ev."""
    levels = [level(d) for d in decided]
    if anchor is None:
        mid, t_mid, n_mid = (_median(levels), _median([d["temp"] for d in decided]),
                             _median([d["tint"] for d in decided]))
    else:
        mid, t_mid, n_mid = levels[anchor], decided[anchor]["temp"], decided[anchor]["tint"]
    out = []
    for d, lv in zip(decided, levels):
        ...   # the loop body stays exactly as it is
    return out
```

Leave `_timelapse_plan` alone.

### `adjust.py`

`rerender` gets five keyword arguments. Their defaults keep today's behaviour.

```python
def rerender(target: str, sets=(), edit: str | None = None, reset: bool = False,
             source: str | None = None, configs_dir: str | None = None,
             reference: str | None = None, recipe_model: str | None = None,
             allow_cloud: bool = False, profile: str | None = None,
             mark=None, mark_kind: str = "auto", explain: bool = False, lut: str | None = None,
             neutral=None, adjustments=(), exposure_bias: float | None = None,
             batch_group: str | None = None, wb=None, apply: bool = True) -> dict:
    """Render the photo behind `target` (its exported image or its sidecar) again. Stored
    per-photo adjustments are kept unless `reset`; `sets`, `edit` and `adjustments` (Adjustment
    objects, F47) add new ones. `exposure_bias`, `batch_group` and `wb` replace what the sidecar
    stored. `apply=False` decides without pixels, writes nothing and returns {"result", "problems"}."""
```

Inside, three places change:

```python
    # directly after the `if neutral is not None:` block
    if wb is not None:      # F47: a white balance as numbers (the reference's pick); it replaces this photo's pick
        stored["wb"], stored["neutral"] = [float(wb[0]), float(wb[1])], None

    # where `new` is made
    new = [parse_set(s) for s in sets] + [a.model_copy(update={"scope": "photo", "status": ""})
                                          for a in adjustments]

    # the render
    out_rgb, result = pipe.process_one(source, do_apply=apply, adjustments=kept + new,
                                       exposure_bias=old.exposure_bias if exposure_bias is None else float(exposure_bias),
                                       batch_group=old.batch_group if batch_group is None else batch_group,
                                       wb_bias=tuple(old.wb_bias), on_plan=_planned if explain else None)
    if not apply:           # a plan only: nothing is written
        return {"result": result, "problems": problems}
```

The final return dict also gets `"result": result`.

### `sync.py` (new)

```python
"""Copy the changes of one photo to the similar photos of its run (F47).

Copied: the reference's photo adjustments without its marks, and the white balance of its neutral
pick. Each similar photo keeps the rules' own decision. Its exposure moves toward the reference's
level, with the bounds of the bursts mode (F9). Every photo is re-rendered through
adjust.rerender, so a later `autoedit adjust` replays it."""

from __future__ import annotations

import glob
import os

from . import adjust, consistency, declare, paths, rules as rules_mod
from .contract import Result
from .segment.base import MARK

DEFAULTS = {"minutes": 10.0, "max_ev": 1.0}
RANGES = {"minutes": (0.0, 1440.0), "max_ev": (0.05, 3.0)}


def settings(configs_dir: str | None = None, profile: str | None = None) -> dict:
    """[sync] of rules.toml, checked."""
    rules = paths.read_config(paths.config_paths(configs_dir, profile)["rules_path"])
    return rules_mod.block(rules, "sync", DEFAULTS, RANGES)


def _sidecar(target: str) -> str:
    return target if target.endswith(adjust._SIDECAR) else os.path.splitext(target)[0] + adjust._SIDECAR


def _load(sidecar: str) -> Result:
    with open(sidecar, encoding="utf-8") as fh:
        return Result.model_validate_json(fh.read())


def _taken(sidecar: str, r: Result) -> float | None:
    """Capture time of the source photo behind a sidecar (found through run.json), or None."""
    src_dir = declare._source_dir(os.path.dirname(os.path.abspath(sidecar)))
    src = os.path.join(src_dir, r.image) if src_dir else None
    return consistency.capture_time(src) if src and os.path.isfile(src) else None


def similar(ref_sidecar: str, folder: str, minutes: float) -> tuple[list[str], dict[str, int]]:
    """Sidecars in `folder` (not below it) of photos like the reference: the same scene, taken at most
    `minutes` before or after it. Returns them, and how many were left out for each reason."""
    ref = _load(ref_sidecar)
    when = _taken(ref_sidecar, ref)
    if when is None:
        raise ValueError(f"{ref.image}: its source photo has no capture time, or it was not found through "
                         "run.json; without it no photo can be told to be of the same light")
    found, left = [], {}
    for sc in sorted(glob.glob(os.path.join(glob.escape(folder), "*" + adjust._SIDECAR))):
        if os.path.samefile(sc, ref_sidecar):
            continue
        try:
            r = _load(sc)
        except (OSError, ValueError):
            why = "not readable"
        else:
            stored = r.audit.get("run_config")
            t = _taken(sc, r) if stored and r.scene == ref.scene else None
            why = ("not re-renderable" if not stored else
                   "another scene" if r.scene != ref.scene else
                   "no capture time" if t is None else
                   f"more than {minutes:g} min apart" if abs(t - when) > minutes * 60 else "")
        if why:
            left[why] = left.get(why, 0) + 1
        else:
            found.append(sc)
    return found, left


def copied(ref: Result) -> tuple[list, int]:
    """The reference's own changes: its photo adjustments without the marks; and how many marks were left out."""
    own = [a for a in ref.adjustments if a.scope == "photo"]
    keep = [a.model_copy(update={"status": ""}) for a in own if not a.target.startswith(MARK)]
    return keep, len(own) - len(keep)


def run(target: str, folder: str | None = None, dry_run: bool = False, minutes: float | None = None,
        configs_dir: str | None = None, profile: str | None = None, **inputs) -> dict:
    """Copy the reference's changes to every similar photo and match its exposure. `inputs` are
    `reference`, `recipe_model` and `lut`, as `autoedit adjust` takes them."""
    ref_sidecar = _sidecar(target)
    if not os.path.isfile(ref_sidecar):
        raise FileNotFoundError(f"no sidecar at {ref_sidecar}")
    if folder and not os.path.isdir(folder):
        raise FileNotFoundError(f"folder not found: {folder}")
    if minutes is not None and not RANGES["minutes"][0] <= minutes <= RANGES["minutes"][1]:
        raise ValueError(f"--minutes must be from 0 to {RANGES['minutes'][1]:g}")
    st = settings(configs_dir, profile)
    ref = _load(ref_sidecar)
    targets, left = similar(ref_sidecar, folder or os.path.dirname(os.path.abspath(ref_sidecar)),
                            st["minutes"] if minutes is None else minutes)
    changes, marks = copied(ref)
    pick = ref.audit.get("neutral")
    wb = [pick["temp"], pick["tint"]] if pick else None
    report = {"reference": ref.image, "copied": len(changes), "marks_left": marks, "white_balance": wb,
              "left_out": left, "dry_run": dry_run, "targets": []}
    if dry_run:
        report["targets"] = [{"sidecar": t, "status": "would sync"} for t in targets]
        return report
    kw = {"configs_dir": configs_dir, "profile": profile, **inputs}
    # A plan is compared with a plan: the banding gate can damp what a render records.
    anchor = consistency.decided_from(adjust.rerender(ref_sidecar, apply=False, **kw)["result"])
    group = "sync:" + os.path.splitext(ref.image)[0]
    for t in targets:
        try:
            plan = adjust.rerender(t, adjustments=changes, reset=True, wb=wb, exposure_bias=0.0,
                                   apply=False, **kw)
            d = consistency.decided_from(plan["result"])
            bias = consistency.burst_biases([anchor, d], st["max_ev"], 0.0, anchor=0)[1][0]
            r = adjust.rerender(t, adjustments=changes, reset=True, wb=wb, exposure_bias=bias,
                                batch_group=group, **kw)
        except (FileNotFoundError, ValueError, OSError) as e:    # one photo fails, the others go on
            report["targets"].append({"sidecar": t, "status": "failed", "error": str(e)})
            continue
        report["targets"].append({"sidecar": t, "image": r["image"], "status": "synced",
                                  "exposure_bias": bias,
                                  "gap_ev": consistency.level(anchor) - consistency.level(d) - bias,
                                  "adjustments": r["adjustments"], "warnings": r["warnings"]})
    return report
```

`lines(report) -> list[str]` turns the report into terminal text:

- A head line: `Reference IMG_1.jpg: 2 changes to copy`, then `; 1 mark not copied (a mark is a
  place in that photo)` when marks were left out, then `; its white balance (temp +3.0, tint -1.0)`
  when a pick is copied.
- One line per target, with the export's file name (the sidecar's for a dry run or a failure):
  `  IMG_2.jpg: similar` (dry run), `  IMG_2.jpg: not synced: <error>`, or
  `  IMG_2.jpg: synced, exposure +0.26 EV toward the reference`. When `abs(gap_ev) > 0.05`, add
  `; still 0.40 EV brighter (a photo is not cut below its own exposure, pushed into clipping or
  moved more than [sync] max_ev)`. Positive `gap_ev` means darker.
- A summary line: `2 of 3 similar photos synced, 1 failed`, or for a dry run
  `3 similar photos; nothing was changed (dry run)`. Then `; left out: 4 another scene,
  2 more than 10 min apart` when any were left out.

### `cli.py`

```python
def _cmd_sync(args) -> int:
    from . import sync

    try:
        r = sync.run(args.target, args.folder, dry_run=args.dry_run, minutes=args.minutes,
                     configs_dir=args.configs_dir, profile=args.profile,
                     reference=args.reference, recipe_model=args.recipe_model, lut=args.lut)
    except (FileNotFoundError, ValueError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 2
    print("\n".join(sync.lines(r)))
    return 1 if any(t["status"] == "failed" for t in r["targets"]) else 0
```

Register it after `adjust`:

```python
    sync_p = sub.add_parser("sync", help="copy one photo's own changes to the photos of the same scene taken "
                                         "near it, and match their exposure to it")
    sync_p.add_argument("target", help="the photo to copy from: an exported photo or its .autoedit.json")
    sync_p.add_argument("folder", nargs="?", help="where the other exports are (default: that photo's folder)")
    sync_p.add_argument("--minutes", type=float,
                        help="how far apart in time a similar photo may be taken (default: rules.toml [sync])")
    sync_p.add_argument("--dry-run", dest="dry_run", action="store_true", help="list the similar photos, change nothing")
    sync_p.add_argument("--reference", help="the --reference photo of the run, if it used one")
    sync_p.add_argument("--recipe-model", dest="recipe_model", help="recipe model, if the run used one")
    sync_p.add_argument("--lut", help="the LUT file, if the run used one")
    sync_p.add_argument("--configs-dir", dest="configs_dir")
    sync_p.add_argument("--profile", help="profile folder with your own config differences")
    sync_p.set_defaults(func=_cmd_sync)
```

Module docstring, after the `adjust` lines:

```
  autoedit sync PHOTO [FOLDER] [--dry-run]   copy one photo's own changes to the similar photos of its run
```

### `configs/rules.toml`

Add directly after the `[search]` block:

```toml
# ─────────────────────────── copy edits to similar photos (F47) ───────────────────────────
# `autoedit sync PHOTO`: a photo of the same scene taken at most `minutes` before or after PHOTO
# gets PHOTO's own changes. Its exposure moves toward PHOTO's by at most `max_ev` stops, never
# below its own exposure and never into clipping (the bounds of the bursts mode, F9).
[sync]
minutes = 10
max_ev = 1.0
```

## Tasks

| Task | Tier | Content | Proving command |
|---|---|---|---|
| T1 | Builder (Sonnet, medium) | `consistency.py` and `adjust.py` as above; tests 1 and 2 | `PYTHONPATH=src python3 -m pytest tests/test_consistency.py tests/test_adjust.py tests/test_sync.py -q` |
| T2 | Builder (Sonnet, medium) | `sync.py`, the CLI, `[sync]` in `rules.toml`; tests 3 to 8 | `PYTHONPATH=src python3 -m pytest tests/test_sync.py tests/test_cli.py -q` |
| T3 | The session, inline | The README and CHANGELOG lines below | none |

T2 starts after T1. T2 edits `cli.py`, so it does not run at the same time as F48's T2.

README, in the "Use" block after the `autoedit declare` line:

```
autoedit sync ./out/IMG_0001.jpg                                # copy that photo's own changes to the photos taken near it, and match their exposure
```

CHANGELOG, under "Added":

```
- **`autoedit sync PHOTO [FOLDER]`** (F47): copies the changes made to one photo after the run
  (`adjust` settings, follow-ups, the white balance of its neutral pick; never a mark) to the
  photos of the same scene taken within 10 minutes of it, and moves their exposure toward it (at
  most 1 EV, never below a photo's own exposure, never into clipping). `--dry-run` lists them first.
```

## Tests (`tests/test_sync.py`)

Plain functions with `tmp_path`. Copy `_picture` (kind 0), `_save` and `_stamp` from
`tests/test_consistency.py`. A helper `_run(tmp_path, extra=())` saves three frames of the picture
3 s apart (gains 0.6, 1.0, 1.5, names `f0.jpg` to `f2.jpg`) plus each `(name, gain, stamp or None)`
of `extra` into `in/`, then runs `batch.run(RunConfig(backend="stub"), in, out)`. The reference is
`f1`.

| # | Test | Task | Asserts |
|---|---|---|---|
| 1 | `test_burst_biases_toward_an_anchor` | T1 | Dicts made as in `test_consistency.py::_d`, levels set through `fg_p50`. With `anchor=0`: the anchor gets 0; a frame 0.4 EV under it (exposure 0.3, headroom 2.0) gets +0.4; a frame 0.6 EV over it with exposure 0.2 gets -0.2 (never a net cut); a frame 1.5 EV under gets +1.0 with `max_ev=1.0`; a frame 0.4 EV under with `headroom_ev=0.3, exposure=0.1` gets at most +0.05 (never into clipping); temp and tint are 0 with `max_wb=0`. Without `anchor`, the result equals today's (median) for the same input |
| 2 | `test_rerender_overrides` | T1 | After `_run`: `rerender(f0, adjustments=[Adjustment(target="global", ops={"temp": 5.0})], exposure_bias=0.2, batch_group="x", wb=[3.0, -1.0])` writes a sidecar with one adjustment (scope `photo`, target `global`, status `applied`), `exposure_bias == 0.2`, `batch_group == "x"`, `audit.run_config.wb == [3.0, -1.0]`, `audit.run_config.neutral is None`, `audit.neutral.temp == 3.0`. Then `rerender(f0, apply=False, exposure_bias=0.0)` returns a `Result` under `"result"` and leaves the image's and the sidecar's bytes and `st_mtime_ns` unchanged. `rerender(f0)` with no new argument rewrites the same image bytes (the stored values are replayed) |
| 3 | `test_similar` | T2 | `_run` with extra `("late.jpg", 1.0, 30 minutes after f0)` and `("notime.jpg", 1.0, None)`: `similar(f1 sidecar, out, 10)` gives the sidecars of `f0` and `f2`, and `left_out == {"more than 10 min apart": 1, "no capture time": 1}`. With `f2`'s sidecar JSON edited to `"scene": "other"`, `f2` is left out as `another scene`. `minutes=0` gives none. `notime` as the reference raises `ValueError` |
| 4 | `test_copied` | T2 | A hand-made `Result` with adjustments of scope `photo` (targets `global`, `sky`, and `mark:area:0.1000,0.1000,0.4000,0.4000`), one of scope `run` and one of scope `look`: `copied` gives the `global` and `sky` ones with status `""` and reports 1 mark |
| 5 | `test_sync_end_to_end` | T2 | `_run`, then `adjust.rerender(f1, sets=["global.temp=8", "sky.exposure=-0.3"])`. `run(f1)`: both targets `synced`. Each target's sidecar holds exactly two photo adjustments, with targets `global` and `sky`, and `batch_group == "sync:f1"`. `f0` (the darkest) has `exposure_bias > 0`, and the mean luma of its export is closer to `f1`'s than before the sync. A second `run(f1)` leaves every export byte-identical. `adjust.rerender(f0)` with no change is byte-identical. No sidecar contains `str(tmp_path)` |
| 6 | `test_dry_run_changes_nothing` | T2 | `run(f1, dry_run=True)` lists `f0` and `f2` as `would sync`; every file in `out` keeps its bytes and `st_mtime_ns` |
| 7 | `test_white_balance_of_the_pick_is_copied` | T2 | `adjust.rerender(f1, wb=[3.0, -1.0])` stands in for a pick (it writes `audit.neutral`). After `run(f1)`, `f0` and `f2` have `run_config.wb == [3.0, -1.0]` and `run_config.neutral is None` |
| 8 | `test_failures_are_isolated_and_cli` | T2 | With `f2`'s sidecar JSON edited to `audit.run_config.decider = "llm"`: `run(f1)` reports `f2` failed with `LLM` in the error and `f0` synced; `cli.main(["sync", f1])` returns 1. `cli.main(["sync", f1, "--dry-run"])` returns 0. `cli.main(["sync", str(tmp_path / "nope.jpg")])` returns 2. A `--configs-dir` copy whose `rules.toml` has `[sync] max_ev = 9` returns 2 |

## Acceptance checks (the session runs these)

1. Both suites green: `PYTHONPATH=src python3 tests/run_all.py` and
   `PYTHONPATH=src .venv/bin/python tests/run_all.py`.
2. The stub smoke run from `CLAUDE.md` completes with sidecars and a manifest.
3. A default render of the reference photos is byte-identical to the baseline.
4. On a real shoot (`curated-dolomites-summer-2026/selected_raw` when present): run it, then
   `autoedit adjust` one photo with `--set global.temp=6 --edit "darken the sky slightly"`, then
   `autoedit sync` it with `--dry-run`, then without. The list holds only photos of its scene within
   10 minutes. Every synced sidecar has the two changes and `batch_group` `sync:<stem>`. No
   negative bias is larger than that photo's own planned exposure. A second sync is
   byte-identical.
5. The Checker reviews F47 (size M).

## Known limits

- A photo brighter than the reference comes down only as far as its own lift (F9's bound). The
  summary line says how far it stays off.
- Similar means the same scene class and close in time. Two places shot minutes apart in the same
  light count as similar. `--dry-run` shows the list, and `--minutes` narrows it.
- The level is planned without the banding gate. A photo whose render trips the gate ends a little
  darker than planned, as in F9.
- Only the exports of a run (sidecars and `run.json`), like `adjust`. A full re-run without
  `--resume` drops the copied changes, as it drops every `adjust` (F3).
- `[sync]` changes `rules.toml`, so a re-render of an export made before F47 warns that the config
  files differ, as after any config change.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| What "the edit of the reference" is | The changes made to it after the run: its photo adjustments and the white balance of its neutral pick. Not the rules' own decision: every photo keeps the decision made from its own pixels | A decider that replays the reference's decision, stored in the sidecar so `adjust` can replay it |
| What is never copied | Marks (a place in the reference's frame); straighten and crop (run settings, decided per photo); the run's `--edit` and the look's habits (every photo has them already) | `sync.copied` |
| What "similar" means | Same scene, and taken at most 10 minutes before or after the reference. No capture time: not similar. Brightness is matched, not tested. `find_bursts` is not used (one framing only) | `[sync] minutes` in `rules.toml`, `--minutes`; `sync.similar` |
| A target's own changes | Replaced by the reference's, so a second sync gives the same result | `reset=True` in `sync.run` |
| How far exposure moves | Toward the reference's planned level, at most 1.0 EV, with F9's bounds. White balance is not evened | `[sync] max_ev`; the `anchor` call in `sync.run` |
| Where it works | On the exports of a run, like `adjust`. CLI only, no Studio button | a later feature |
| Runs with the LLM decider | Not synced: every re-render would call the cloud again | pass `allow_cloud` through `sync.run` to `rerender` |
| The command word | `autoedit sync` (the owner's working name) | `cli.py` |

## Result

Built on 2026-11-19 by Sonnet Builders, reviewed by the Checker (Opus, high): no must-fix. Fixed after the review: one bad target (a corrupt RAW, a missing HEIC decoder) no longer stops the others. Known limits found in the review: with `apply=False` a SAM 3 plan can still write mask-cache files; when the reference has no neutral pick a target keeps its own; the reference keeps its own `batch_group` in the colour report. `sync` needs a capture time, so it refuses photos without EXIF time.
