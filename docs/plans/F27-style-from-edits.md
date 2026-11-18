# F27. Style from your edits

Status: spec (2026-11-17). Size M. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F27.
Written in an unattended run ([UNATTENDED_RUN.md](../UNATTENDED_RUN.md)): nothing was asked, and the
owner's decisions are in the table below. Build it after F24, F25 and F26 are closed: it edits
`contract.py`, `pipeline.py`, `cli.py`, `web/service.py` and `settings.toml`, which they edit too.
The design was built once on a scratch copy of the tree, which is deleted again: the reference code
is that code and every number is measured. On the copy `tests/run_all.py` gave 795 passed and 3
failed: the settings snapshot, a help text over 140 characters, and one F24 test newer than the copy.

## Goal

When the user switches it on, the engine keeps one small line per exported photo: what the photo was
like and what the user typed on top of the edit. `autoedit style suggest` names the changes the user
keeps making. A yes writes them into a new look of the profile, which repeats them. The history is
off by default, nothing is written without the yes, and default output stays byte-identical.

## What was found in the code

1. Every product export, and no preview, goes through `emit.py::write_outputs`: `batch.py::run` and
   `_render_with` (render workers), `adjust.py::rerender`, `web/service.py::run_one`, `web/jobs.py::_run`.
2. `pipeline.py::process_one` puts every directed change into `Result.adjustments`
   (`contract.py::Adjustment`): target, op deltas, `source` (the typed clause), `scope` (`run` from
   `--edit`; `photo` from a follow-up, `--set` or a mark), `status`. A target is `global`, `place:NAME`
   (a name of `instructions.toml`), a concept (a config name or any typed word), or a mark with its box.
3. `Result.region_stats` holds exactly the run's own concepts that were found; a thing only an
   instruction named is not there. `recipe.py::features_from_stats` is ten whole-frame numbers that
   do not depend on masks: the same photo gives the same ten.
4. A `RunConfig` is built in three places, each from `paths.config_paths(configs_dir, profile)`:
   `cli.py::load_run_config`, `web/service.py::make_cfg`, `adjust.py::rerender`. Only the first reads
   `defaults.toml`; `rerender` replays `audit.run_config`; render workers get the config pickled.
5. A look is flat numbers that scale global magnitudes (`looks.py::shape`); `paths.py::_check_looks`
   refuses an unknown key. A typed instruction can add exposure to a sky (`instructions.py::plan`,
   `_summed`). `process_one` builds that list before the segmenter runs; the scene is known only after.

## Measured

- **How:** stub backend and synthetic photos on the scratch copy. Nothing was run with SAM 3.
- **A line** is 346 bytes with one change; `history.record` takes 0.85 ms, where `process_one` of a
  128 px photo takes 56 ms. 8 processes with 5,000 appends each gave 40,000 readable lines, none torn
  (macOS, local disk). A cap of 200 kB was never passed. Loading 40,000 lines takes 0.8 s.
- **What a line holds:** a folder with a person's name, files `IMG_SECRET_n`, a typed sentence, a
  word the configs do not know, a mark with its box: none of it is in the file.
- **The rule on made-up histories:** one planted habit among other edits, other scenes and another
  look gives exactly that suggestion; 2,000 histories of random edits give none. A habit on 20
  photos is found in 92 of 100 histories when it is followed on 80 % of them, in 62 at 70 %, in 26
  at 60 %; on 12 photos, in 26 of 100 at 90 %. The edges are test 15.
- **The sidecars in the repository** (read only): 6,248 under `out*`, 1,430 different photos on 13
  days, a typed change on 3 of them (the checks of F36 and F37). No suggestion: no real follow-ups.
- **A look with a habit** (`sky exposure -0.30` for landscapes) writes the bytes of the same look
  without it and `--edit "darken the sky"` on 4 of 4 landscapes, and the bytes of the plain look on
  2 of 2 photos of people. Default exports are byte-identical between the committed code and the copy.
- **Not measured:** how often the owner repeats a change, so whether 12, 3 and 70 % fit; a blind test
  of a suggested look; appends on Windows or a network drive; SAM 3's time for a habit target.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Is the history on? | No: `history = false`; the owner's decision in `FEATURE_PLAN.md` stays open. `adjust` and the web app follow `[run] history` of today's files, not what the batch had | `history = true` under `[run]` in `defaults.toml` |
| What is a run? | A day of exporting: the line has a date, no run id. A command per photo (`adjust`, Studio's export of one) would make "3 runs" mean nothing, and a folder id would come from a path | `min_days` in `style.toml`; a run field in the line |
| 70 % of what, and which value? | Of the photos the change could have been made on (scene, look, the thing found), not only of those it was made on: "14 of 17 landscapes" is then true as written. The value is the middle one over all of them (0 where untouched), always one that was typed | `min_share`; `style.py::_could`, `suggest` |
| Marks and typed words | Left out of the line, and counted. A mark is one spot on one photo; a word the configs do not define could be a name | `history.py::line` |
| How does a look carry a change to one region? | A `habit` table in the look, replayed per scene as the same typed instruction, for ops with a limit under `[limits]` in `instructions.toml`. Not a rules overlay: that would change every look, under no name | Not meant to be reversed |
| The yes | `autoedit style suggest --yes NAME`, a new name, no prompt. The look is never applied by itself | A profile `defaults.toml` with `look = "NAME"` |
| Clearing, and size | `history clear` says what it would delete, `--yes` deletes (as `models remove`). At 8 MB, about 23,000 photos, the oldest half goes | `cli.py::_cmd_history`; `history.MAX_BYTES` |
| Blind check, Studio card | Deferred (Design 6); the API has two routes | A task of its own |

## Design

### 1. The history line (`history.py`, new)

One JSON object per line in `<profile>/history.jsonl`, ASCII, file mode 600:

| Field | Content |
|---|---|
| `v`, `day` | 1; the local date of the export, `YYYY-MM-DD` |
| `scene`, `look` | `Result.scene`; `cfg.look` or the style's look. Empty unless `[a-z0-9_]{1,40}` |
| `features`, `has` | the ten numbers of `recipe.features_from_stats`, 4 decimals; the run's own concepts that were found (fact 3), config names only, sorted |
| `changes`, `left_out` | target, op, summed delta: only adjustments of scope `run` or `photo` with status `applied` or `clamped`, only the targets `global`, `place:NAME` and a concept the configs define; `{"mark": n, "word": n}`, the applied changes that were not written down |

Never in a line: a path, a file or folder name, pixels, EXIF, the typed sentence, the prompt, a mark
or its box, a word the configs do not define, a style name.

```python
# from .instructions import GLOBAL, PLACE; from .segment.base import MARK; load_concepts, load_styles, style_for
FILE, VERSION = "history.jsonl", 1
MAX_BYTES = 8_000_000        # about 23,000 photos; above it the oldest half is dropped
KEYS = ("v", "day", "scene", "look", "features", "has", "changes", "left_out")
_NAME = re.compile(r"[a-z0-9_]{1,40}")
_TYPED, _MADE = ("run", "photo"), ("applied", "clamped")   # scope "look" is the look's own habit
def line(result, cfg, day: str) -> dict:
    presets, meta = load_concepts(cfg.concepts_path)
    known = set(meta) | {c for cs in presets.values() for c in cs}
    look = cfg.look or style_for(cfg.style, load_styles(cfg.styles_path)).look
    changes, left = {}, {"mark": 0, "word": 0}
    for a in result.adjustments:
        if a.scope not in _TYPED or a.status not in _MADE:
            continue
        t = a.target
        if t.startswith(MARK):
            left["mark"] += 1          # one spot on one photo: no look can repeat it
        elif t == GLOBAL or t in known or (t.startswith(PLACE) and _NAME.fullmatch(t[len(PLACE):])):
            acc = changes.setdefault(t, {})
            for op, delta in a.ops.items():
                acc[op] = acc.get(op, 0.0) + float(delta)
        else:
            left["word"] += 1          # a word the user typed: never written down
    changes = {t: {op: round(v, 4) for op, v in ops.items() if abs(v) >= 1e-9} for t, ops in changes.items()}
    return {"v": VERSION, "day": day,
            "scene": result.scene if _NAME.fullmatch(result.scene or "") else "",
            "look": look if _NAME.fullmatch(look or "") else "",
            "features": {k: round(v, 4) for k, v in recipe.features_from_stats(result.global_stats).items()},
            "has": sorted(r.name for r in result.region_stats if r.name in known),
            "changes": {t: ops for t, ops in changes.items() if ops}, "left_out": left}
def record(result, cfg, day: str | None = None) -> None:     # the part inside its try block
    where = folder(getattr(cfg, "profile_dir", None))
    row = line(result, cfg, day or datetime.date.today().isoformat())
    os.makedirs(where, exist_ok=True)
    fd = os.open(os.path.join(where, FILE), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(row, separators=(",", ":")) + "\n").encode("ascii"))
        big = os.fstat(fd).st_size > MAX_BYTES
    finally:
        os.close(fd)
```

`record` returns at once unless `cfg.history` is set and the result has statistics. It never raises:
`OSError` and `ValueError` are caught and said once on stderr (`history: not written (...)`), so a
history that cannot be written never fails an export. One write of one short line does not mix with
another process's on a local disk: a `ponytail:` comment says so (way out: a lock file). Then, when
`big`, `_trim` runs on the file.

| Function | Does |
|---|---|
| `folder(profile)`, `wanted(configs_dir=None, profile=None)` | `profile or paths.default_profile_dir()` (a `ValueError` with profiles off). `[run] history` of `defaults.toml` in `configs_dir or paths.configs_dir()`, read with `paths.read_config(path, profile)` |
| `load(profile) -> (lines, unreadable)` | The lines `_ok` accepts, in file order, one per photo: the key is `json.dumps(row["features"], sort_keys=True)` and a later line replaces an earlier one (`photos.pop(key, None); photos[key] = row`). No file: `([], 0)` |
| `_ok(row)` | A dict with `v == 1`, `day` as `YYYY-MM-DD`, `scene` and `look` empty or `_NAME`, finite numbers in `features` and in every op of `changes`, strings in `has`. Anything else is unreadable |
| `_trim(path)`, `_warn(msg)` | Keep the newest lines up to `MAX_BYTES // 2`, write them to `PATH.<pid>.part` with mode 600, `os.replace`. Print once per message and process, to stderr |
| `status(profile)`, `clear(profile)` | `{path, bytes, photos, days, first, last, unreadable}` from `load`. Remove the file; true when there was one |

### 2. The switch, and where the file lives

Two run settings carry the switch to every export, also into render workers: `history` (in the
settings schema) and `profile_dir` (hidden). Neither is stored in a sidecar, so no sidecar changes
and no path is written down. The CLI reads `[run] history` like every run setting; the web service
and `adjust` do not read `defaults.toml` (fact 4) and ask `history.wanted`. `profile_dir` is set only
by the three places that build a config, after everything a file, a request or a sidecar can supply.
`emit.write_outputs` records after the sidecar is written, and only when there are pixels. History on
with profiles off writes nothing and says so once. The edit per file is under "Files".

### 3. A look that carries a habit

```toml
[my_style]                           # every look field, as `looks save` writes them
curve_add = 5.0
[my_style.habit.landscape.sky]       # habit.<scene>.<target>: what the look repeats
exposure = -0.3
```

`habit` is a sub-table of a look and no field of `Look`. `looks.check_habit(table, where) -> dict`
(scene, target, `{op: float}`) raises a `ValueError` that starts with `where` unless: every level is
a table; a scene is `[a-z0-9_]{1,40}`; a target is `global`, `place:[a-z0-9_]{1,40}` or
`[a-z][a-z0-9 _-]{0,39}` (never a mark); an op is in `ops.OP_NAMES` (imported inside the function,
because `paths.py` imports `looks`); a value is a finite number, no boolean; a look has at most
`MAX_HABIT_TARGETS = 12` targets. Empty tables are dropped. `looks.load_habits(path)` maps a look
name to `check_habit(body[HABIT], f"{path}: look {name}")`, with `HABIT = "habit"`.

`instructions.habit_adjustments(habit, look, vocab) -> dict[str, list[Adjustment]]` maps a scene to
`Adjustment(target=target, ops=dict(ops), source=f"look {look}", scope="look")`. A `ValueError` for
an op without an entry in `vocab.limits` ("a look can only repeat ops that have a limit under
[limits] in instructions.toml") and for a place not in `vocab.places`. The source makes `_summed`
hold the habit and the typed changes together inside `[limits]`. `pipeline.py` (protected; the stage
order does not change) gets a field `habits: dict = field(default_factory=dict)` after
`place_light_cfg`, a helper and four lines:

```python
    # build, after `decider = make_decider(cfg)`; then pass `styles=styles, habits=habits` to cls(...)
        styles = load_styles(cfg.styles_path)
        look_name = cfg.look or style_for(cfg.style, styles).look
        habit = looks_mod.load_habits(cfg.looks_path).get(look_name)
        habits = instructions.habit_adjustments(
            habit, look_name, vocab or instructions.load_vocab(cfg.instructions_path)) if habit else {}
    def _all_habits(self) -> list[Adjustment]:
        """The look's habits of every scene: what to segment for before the scene is known."""
        return [a for scene in self.habits.values() for a in scene]
    # presegment:   instructions.extra_concepts(self.adjustments + self._all_habits(), concepts)
    # process_one:  instructions.extra_concepts(adjs + self._all_habits(), concepts)
    # process_one, directly after `region_rule = {...}`: the look first, typed changes add to it
        adjs = [a.model_copy() for a in self.habits.get(sc.name, [])] + adjs
```

A habit is not scaled by `--intensity`, like every instruction. `adjust` keeps only `photo`
adjustments, so a re-render builds the habit again from the look. The history skips scope `look`.

### 4. Suggestions and the yes (`style.py` and `configs/style.toml`, both new)

`style.toml` is read by `load_config(profile=None, configs_dir=None)` as `calibrate.load_config`
reads its file. Every key gets a comment line; a `[words]` entry is a list of two strings:

| Table | Keys |
|---|---|
| `[suggest]` | `min_photos = 12` (the change was made, in one direction, on at least this many photos), `min_days = 3` (exported on at least this many days; a day of exporting is one run), `min_share = 0.70` (and on at least this share of the photos it could have been made on) |
| `[words]`: lower value, higher value. No entry, no suggestion | `exposure`: darker, brighter. `temp`: cooler, warmer. `tint`: more magenta, greener. `saturation`: more muted, more colourful. `clarity`: softer, crisper. `contrast`: flatter, more contrasty |
| `[scenes]`. No entry reads "SCENE photos" | `landscape = "landscapes"`, `people = "photos of people"`, `other = "other photos"` |

```python
def _could(photos: list[dict], target: str) -> tuple[list[dict], bool]:
    """(the photos the change could have been made on, whether the run looks for the target itself)."""
    if target == GLOBAL or target.startswith(PLACE):
        return photos, True
    if not any(target in r["has"] for r in photos):
        return photos, False       # only known where an instruction named it: the whole scene counts
    return [r for r in photos if target in r["has"] or target in r["changes"]], True
def suggest(rows: list[dict], look: str, conf: dict, limits: dict[str, float]) -> list[dict]:
    rule, words = conf["suggest"], conf["words"]
    scenes: dict[str, list[dict]] = {}
    for r in rows:
        if r["look"] == look and r["scene"]:
            scenes.setdefault(r["scene"], []).append(r)
    out = []
    for scene, photos in sorted(scenes.items()):
        keys = sorted({(t, op) for r in photos for t, ops in r["changes"].items() for op in ops
                       if op in words and op in limits})
        for target, op in keys:
            could, found = _could(photos, target)
            vals = [(r["changes"].get(target, {}).get(op, 0.0), r["day"]) for r in could]
            down, up = sum(v < 0 for v, _ in vals), sum(v > 0 for v, _ in vals)
            if down == up:
                continue
            sign, same = (-1 if down > up else 1), max(down, up)
            days = {d for v, d in vals if v * sign > 0}
            if same < rule["min_photos"] or len(days) < rule["min_days"] or same < rule["min_share"] * len(could):
                continue
            size = statistics.median_low([v * sign for v, _ in vals])   # a value that was typed
            if size <= 0:               # only with min_share at or below a half
                continue
            out.append({"id": f"{scene}/{target}/{op}", "look": look, "scene": scene, "target": target,
                        "op": op, "value": round(sign * min(size, limits[op]), 4), "photos": same,
                        "of": len(could), "days": len(days), "found": found})
    return out
```

`sentence(s, conf) -> (ask, fact)`; `what` is `the photo`, `the top` (a place) or `the sky`:

- ask: `You made {what} {word} on {photos} of {of} {kind}. Add it to your look?` (`word` is the entry of `[words]` for the sign of the value, `kind` the one of `[scenes]`)
- fact: `Counted: {counted}; {photos} of them on {days} days. The look would add {op} {value:+g} to {what} on {kind}.`
- `counted`: `the {of} {kind} where {what} was found`; for the photo or a place
  `all {of} {kind} edited with the look {look}`; with `found` false
  `all {of} {kind}, because {what} is only looked for where an instruction names it`.

`overview(profile, configs_dir=None, look=None) -> dict` writes nothing. Keys: `on` (`history.wanted`),
`photos`, `days`, `unreadable`, `looks` (name to photos), `look` (the one given, else the most used),
`suggestions` (with `ask` and `fact`), `left_out`. The limits are `instructions.load_vocab(P).limits`,
P being `paths.config_paths(configs_dir, profile).get("instructions_path")` or the packaged file.

`add_to_look(profile, name, ids=None, look=None, configs_dir=None) -> dict` is the yes. It takes the
suggestions of `overview` (all, or those in `ids`), copies the base look's habit from
`looks.load_habits`, adds each value (rounded to 4 decimals), runs `looks.check_habit` on the result
and calls `cli.write_look(history.folder(profile), name, asdict(base look), habit=...)`. A
`ValueError`, nothing written, for: a name that is not `[a-z0-9_]+`; an unknown id; no suggestion;
the base look's own name ("is the look your history was made with; choose a new name": its lines
would suggest the change again); a packaged look's name; a base look that no longer exists. Returns
`{name, base, path, replaced, added, habit}`. A second yes writes the same bytes.

### 5. Commands and API

`autoedit history status|clear [--yes] [--profile DIR] [--configs-dir DIR]` and
`autoedit style suggest [--look NAME] [--yes NAME] [--only ID]... [--profile DIR] [--configs-dir DIR]`.
A `ValueError` or `OSError` is printed and gives exit code 2. Counts read `1 day`, `3 days`.

```
$ autoedit history status       (off: "history: off (turn it on with `history = true` under [run] in your profile's defaults.toml, or --history on a run)")
history: on
file: <path>                    (no file: "file: none yet (<path>)" and nothing more; if any: "unreadable lines: 2 (skipped)")
photos: 212 on 9 days (2026-11-02 to 2026-11-17), 74 kB
$ autoedit history clear        (exit 1; with --yes "deleted the history of 212 photos"; no file: "no history to clear", exit 0)
would delete the history of 212 photos (74 kB) at <path>; run again with --yes to delete
$ autoedit style suggest
History: 21 photos on 5 days. Look natural: 17 photos. Other looks: punchy (4); see them with --look NAME.
Not counted: 4 changes made with a mark, 0 on things your config files do not name.
(an empty line before each suggestion, and one after the last)
landscape/sky/exposure
  You made the sky darker on 14 of 17 landscapes. Add it to your look?
  Counted: the 17 landscapes where the sky was found; 14 of them on 5 days. The look would add exposure -0.3 to the sky on landscapes.
To add them to a new look: autoedit style suggest --yes NAME   (--only ID for some)
Nothing has changed.
```

`--yes my_style` prints `saved look my_style in <path>: the look natural plus landscape/sky/exposure`
and `Nothing uses it yet. Try it with: autoedit run <in> <out> --look my_style`; a replaced look adds
`  it replaced the look 'my_style' that was in the profile`. Without a suggestion the listing ends
`No suggestion yet. A change shows up here when you made it in the same direction on at least 12
photos, on at least 3 days, and on at least 70 % of the photos it could have been made on.` (the
numbers of `[suggest]`). An empty history prints `No history yet.`, plus ` It is off: turn it on
with ...` when it is off. The "Not counted" line is left out at 0 and 0.

API, on the router of `web/app.py`, through `guard`: `GET /style/suggestions?look=` returns
`style.overview(profile, configs_dir, look or None)`; `POST /style/accept` with `{"name", "ids"?,
"look"?}` returns `style.add_to_look(...)` (`ids`: a list of strings). The profile is the server's own.

### 6. Deferred

- **Blind check.** It needs a real suggestion and the owner's votes; this run has neither, and
  nothing waits for it, because a yes only writes a look. How it reuses F12: render each photo with
  both looks (two pipelines: habits are bound at build) as `qNN_a.jpg` and `qNN_b.jpg`, write
  `pairs.json` with `dimension: "look"` and a random `plus_slot`, the page from `calibrate._PAGE`,
  read the votes with `calibrate.read_votes`. A pair that differs by under 2 of 255 is left out.
- **Studio card:** none until the test UI; the routes give it all it shows. The recipe predictor: not now.

## Files

| File | Change | Task |
|---|---|---|
| `src/autoedit/history.py`; `src/autoedit/style.py`, `src/autoedit/configs/style.toml` | new: Design 1; new: Design 4 | T1; T3 |
| `src/autoedit/contract.py` | After `grain_size` (after F25's `lut_amount` when it is there): `history: bool = False` ("one line per exported photo in the profile (F27)") and `profile_dir: str \| None = None` ("the profile folder this config was built with; set by the code"). T2: `Adjustment.scope` is `Literal["run", "photo", "look"]` | T1, T2 |
| `src/autoedit/pipeline.py` | `"history"` and `"profile_dir"` join `_NOT_STORED`. T2: the field, the helper and the four lines of Design 3 | T1, T2 |
| `src/autoedit/emit.py`, `src/autoedit/web/service.py`, `src/autoedit/adjust.py` | Import `history` in each. In `write_outputs`, after `write_sidecar(result, sidecar)`: `if out_rgb is not None: history.record(result, cfg)`. In `make_cfg`: `"history"` joins `_ALLOWED`; before the loop over `_ALLOWED`, `kw["history"] = history.wanted(configs_dir, profile)`; after it, `kw["profile_dir"] = profile`. In `rerender`, the dict of `RunConfig(**{...})` ends with `"history": history.wanted(configs_dir, profile), "profile_dir": profile`: last, so a hand-edited sidecar cannot set them | T1 |
| `src/autoedit/cli.py` | T1: `"history"` in the `[run]` list of `_flatten` and in `overrides`; `--history` and `--no-history` in `_add_run_flags` (`dest="history"`, `store_true` and `store_false`, default `None`); `kw["profile_dir"] = profile` directly before `return RunConfig(**kw)`; the command `history`; `"style.toml"` and `"history.jsonl"` join `known` in `_cmd_doctor`. T2: `write_look(profile, name, fields, habit=None)` sets `data[name][looks_mod.HABIT] = habit` when given; `_cmd_looks` loads the habits of the merged looks file, `save` passes `habit=habits.get(args.base)`, `list` appends `  habit: landscape sky exposure -0.3` (`{scene} {target} {op} {value:+g}`, joined by `; `). T3: the command `style` | T1 to T3 |
| `src/autoedit/configs/defaults.toml`, `.gitignore`, `scripts/check_public_repo.py`, `src/autoedit/configs/settings.toml`, `tests/snapshots/settings_schema.json` | Under `[run]`: `history = false`, with the comment "true: one line per exported photo in your profile, for `autoedit style suggest` (F27, opt-in)". The line `history.jsonl` (and `"history.jsonl"` in `PRIVATE_NAMES` of `scripts/check_public_repo.py`). `"profile_dir"` joins `hidden`; at the end of the Output group `[setting.history]`: `label = "Keep a history of your edits"`, `group = "output"`, `help = "One line per exported photo in your profile: what you changed, no picture, no file name. It never leaves this computer."` (119 characters; the schema test allows 140). The snapshot regenerated | T1 |
| `src/autoedit/looks.py`, `src/autoedit/paths.py` | `HABIT`, `MAX_HABIT_TARGETS`, `check_habit`, `load_habits`; `Look`, `FIELDS`, `load` and `shape` do not change. In `_check_looks`, before the unknown-field check: for the key `HABIT`, `check_habit(val, f"{path}: look {name}")` and `continue` | T2 |
| `src/autoedit/instructions.py`, `src/autoedit/calibrate.py` | `habit_adjustments`. In `report`: `label = a.source if a.scope == "look" else ...` (the rest of the line as it is), so the note reads `look my_style: applied to sky (exposure -0.30)`. `advance` passes the habit of `state["look"]` to `write_look`: its pairs were rendered with it | T2 |
| `src/autoedit/web/app.py`, `tests/snapshots/api_v1.json` | The two routes of Design 5; regenerated | T3 |
| `tests/test_history.py`, `tests/test_style.py` | new: tests 1 to 8; tests 9 to 13 (T2) and 14 to 18 (T3) | T1 to T3 |

## Tasks

One after the other: they share three files. `.venv` has no pytest; there `tests/run_all.py` proves.

| Task | Tier | Content | Proving command |
|---|---|---|---|
| T1 | Builder, Sonnet, medium | Designs 1 and 2, `autoedit history`; tests 1 to 8; the snapshot (`AUTOEDIT_UPDATE_SNAPSHOTS=1 PYTHONPATH=src python3 -m pytest tests/test_settings_schema.py -q`) | `PYTHONPATH=src python3 -m pytest tests/test_history.py tests/test_emit.py tests/test_pipeline.py tests/test_cli.py tests/test_adjust.py tests/test_follow_up.py tests/test_jobs.py tests/test_web_service.py tests/test_parallel.py tests/test_profile.py tests/test_settings_schema.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` |
| T2 | Builder, Sonnet, high (`pipeline.py`) | Design 3; tests 9 to 13 | `PYTHONPATH=src python3 -m pytest tests/test_style.py tests/test_history.py tests/test_profile.py tests/test_calibrate.py tests/test_instructions.py tests/test_follow_up.py tests/test_marks.py tests/test_adjust.py tests/test_parallel.py tests/test_pipeline.py tests/test_lessons.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` |
| T3 | Builder, Sonnet, medium | Designs 4 and 5 without `history`; tests 14 to 18; the API snapshot (`AUTOEDIT_UPDATE_SNAPSHOTS=1 PYTHONPATH=src python3 -m pytest tests/test_api_contract.py -q`) | `PYTHONPATH=src python3 -m pytest tests/test_style.py tests/test_history.py tests/test_cli.py tests/test_api_contract.py tests/test_security.py tests/test_profile.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` and `PYTHONPATH=src .venv/bin/python tests/run_all.py` |
| Check | Checker, Opus, high | The diff against this spec (`pipeline.py` is protected). With care: what a line can hold, who can set `profile_dir`, what a habit table from someone else can do | none |
| Docs | The session, inline | `README.md` (the two commands, where the history lives and how to clear it, `style.toml`), `SECURITY.md`, `APP_GUIDE.md`, `ARCHITECTURE.md`, `CHANGELOG.md`, the plan rows; if F25 is built, "habits" in the list of what a look LUT leaves out | none |

## Tests

The runner has `tmp_path` only: environment and module values are set and restored in `finally`. With
`AUTOEDIT_PROFILE=""` every test passes its own profile folder. A made-up history is a list of dicts.

| # | Test | Asserts |
|---|---|---|
| 1 | `test_off_by_default_writes_nothing` | `RunConfig().history is False`; packaged `defaults.toml` has `history = false`; a stub batch with `profile_dir` set and history off creates no profile folder; with history on the exports have the same bytes; no sidecar has `history` or `profile_dir` in `audit.run_config` |
| 2 | `test_one_line_with_exactly_these_fields` | Three photos, three lines; the keys are `history.KEYS`; `features` has the keys of `recipe.RECIPE_FEATURES`; `has == ["sky", "subject"]`; the file mode is 600 (not on Windows) |
| 3 | `test_a_line_holds_nothing_that_identifies` | Photos `IMG_PRIVATE_n.jpg` in a folder `Client Folder 2026`, `prompt="for the client"`, `edit="brighten the jacket"` with a stand-in segmenter that finds `jacket` (as `tests/test_instructions.py::_Fake`), a mark with the box 0.35, 0.35, 0.65, 0.65: the file has none of `IMG_PRIVATE`, `Client`, `jacket`, `brighten`, `0.3500,0.3500`, the folder path; `left_out == {"mark": 1, "word": 1}`; a look named `My Look` is written as `""` |
| 4 | `test_changes_are_summed_and_only_what_was_made` | Two follow-ups on the sky give one sum; a run's `--edit` counts; `not_found` and `blocked` changes are neither in `changes` nor in `left_out` |
| 5 | `test_every_export_writes_a_line`, `test_a_photo_exported_again_counts_once` | A batch, a batch with `workers=2` (8 photos, 8 readable lines), `service.run_one`, a job, `adjust.rerender` each add lines; a dry run and `service.preview` add none. Export, then `adjust` twice: two more raw lines, `history.load` gives one line per photo with the latest changes |
| 6 | `test_the_size_cap_keeps_the_newest`, `test_unreadable_lines_are_skipped` | With `history.MAX_BYTES = 4000`: after 40 records the file is not larger, the last line is there, `load` reads every line. A cut line, `{"v": 2}`, a list, plain text, a scene `../x`: 5 unreadable, the good lines stay |
| 7 | `test_a_failed_write_never_fails_the_export` | `profile_dir` is a file: the export and its sidecar are written. History on, `profile_dir=None`, profiles off: the same, and nothing is written anywhere |
| 8 | `test_the_setting_reaches_every_builder`, `test_cli_history` | `--history`, `[run] history` in a `--config` file and in a profile `defaults.toml` set it; `--no-history` wins; `profile_dir` is the profile; `make_cfg` follows the profile's file, `{"history": False}` wins, `{"profile_dir": "x"}` is ignored; `adjust` on a sidecar whose `run_config` was given `history: true` and a `profile_dir` writes nothing there. `status` prints off, then on with the photos and days; `clear` returns 1 and keeps the file; `--yes` deletes; again 0; with profiles off and no `--profile`, 2 |
| 9 | `test_no_habit_by_default_and_byte_identical` | `load_habits` of the packaged file is `{}`; a default pipeline has no habits; a profile with a habit on a look that is not used writes the bytes of a default batch |
| 10 | `test_a_habit_is_the_same_typed_instruction` | Look `mine` = `natural`'s values plus `habit.landscape.sky exposure -0.30`: on a landscape `process_one` is `array_equal` to `natural` with `edit="darken the sky"`; one adjustment with scope `look`, status `applied`, source `look mine`; the note, word for word. On a photo of people: the pixels of `natural`, no adjustment. With "darken the sky a lot" twice on top: the sky's exposure delta is -0.75, every status `clamped` |
| 11 | `test_rerender_and_workers` | `adjust.rerender` and `workers=2` give the bytes of the batch. With a habit on `foliage` (not in the style) and the pattern of `tests/test_parallel.py::test_workers_keep_the_reference_match`: after `presegment` a worker with `_NoModel` equals the parent |
| 12 | `test_what_a_habit_table_may_not_hold` | An unknown op, a mark as target, `nan`, a scene `../x`, a name of 50 letters, 13 targets, `habit = 3`: each a `ValueError` from `paths.config_paths` that names the file and the look. `dehaze` and `place:nowhere` raise at `Pipeline.build` with that look, not with another |
| 13 | `test_look_commands_keep_the_habit`, `test_the_looks_own_habit_is_not_history` | `write_look(..., habit=H)` writes it; `looks save b --from a` copies it; `looks list` shows it; `looks delete` removes it; a calibration finished from a habit look saves the habit. An export with `mine` and the follow-up "warm the sky": the line's changes are `{"sky": {"temp": 10.0}}` |
| 14 | `test_one_planted_habit_gives_exactly_that_suggestion`, `test_no_habit_gives_no_suggestion` | 60 landscapes on 6 days, the sky darker by 0.3 on 48, other edits on a few, 30 photos of people, 20 lines of another look: exactly `landscape/sky/exposure`, value -0.3, 48 of 60; the question word for word. 200 seeded histories of random edits, and an empty one: none |
| 15 | `test_the_rule_at_its_edges` | 12 of 12 on 3 days yes, 11 no, 2 days no, 14 of 20 yes, 13 of 20 no, 14 of 17 with 3 the other way yes, 12 against 12 no, 14 of 120 no; 12 typed at -2.0 give -0.75; six at -0.15, eight at -0.30 and three untouched give -0.15; a `dress` never in `has`: 14 of 14 yes, 14 of 30 no; `dehaze` never; a profile `style.toml` with `min_photos = 2` changes the answer |
| 16 | `test_a_yes_changes_the_look_file_and_nothing_else` | A profile with a history, a `defaults.toml` and a look `other`: `overview` changes no file. After the yes only `looks.toml` differs in the whole `tmp_path` tree; it holds `other` unchanged and `mine` = `natural`'s fields plus the habit; the packaged files keep their bytes; a second yes writes the same bytes. The base look's name, a packaged name, `Bad Name`, an unknown id, no suggestion: `ValueError`, no file changed. A landscape with `look="mine"` then has the pixels of `natural` with `edit="darken the sky"`, and `overview(look="mine")` after that export has no suggestion |
| 17 | `test_cli_style_suggest`, `test_api_style_routes` | The listing returns 0 and ends with "Nothing has changed."; `--yes mine` returns 0 and the file exists; `--yes natural` returns 2; `--only` with an unknown id returns 2; no history returns 0. `GET /api/v1/style/suggestions` gives the overview; `POST /api/v1/style/accept` writes the look; a bad name is 400 `bad_request`; `ids` that is no list is 400; the route snapshot holds both |
| 18 | `test_nothing_opens_a_connection`, `test_words_have_limits` | With `socket.socket` and `socket.create_connection` replaced by functions that raise: a batch with history on, `load`, `overview` and `add_to_look` work. Every op of `[words]` has a packaged `[limits]` entry |

## Acceptance checks (the session runs these)

1. Both suites are green (`PYTHONPATH=src python3 tests/run_all.py`, and with `.venv/bin/python`). The
   stub smoke run of `CLAUDE.md` completes; default exports are byte-identical to the baseline
   renders, and a default sidecar has no new key. With `--history --profile` on a scratch folder:
   one line per photo, no file name of the corpus in it; `history status`, then `clear --yes`.
2. The tone gate of `CLAUDE.md`, default settings: 0 dark to darker, 0 pushed into clipping, and
   every row equal to the run before F27 (`pipeline.py` was touched).
3. With SAM 3 (`.venv/bin/python`, one job): 12 photos of the curated corpus with a scratch profile
   look that holds `habit.landscape.sky exposure -0.30`, against `natural` with `--edit "darken the
   sky"`: the landscapes are byte-identical. Then a habit on `trees` with `--workers 2`: it
   completes, and the time per photo goes into the Result.
4. The commands on a made-up history of 17 landscapes (the text of Design 5, the yes, `looks list`, a
   run with `--look`), both routes with `curl` against `autoedit ui`, and the Checker's review.

## Known limits

- A look repeats one fixed change. It cannot carry a mark, a thing the configs do not name, the size
  of a place, an op without a limit (grain, anything set only with `--set`), a change the region
  policy blocked, or a change that depends on the photo (the plan's "later").
- A day is a run: two shoots on one day count once, a batch that runs past midnight twice. The same
  ten numbers are the same photo: a RAW file and its JPEG count as two.
- For a thing the style does not look for, every photo of the scene counts, so that habit shows up
  only when it is named on 70 % of them. Adding the thing to the style's concepts changes that.
- A line keeps the look's name, not its values. The oldest half of the history goes at 8 MB, and a
  line appended in that moment is lost. A LUT (F25) cannot hold a habit; F28's look file must.
- The web app now reads `defaults.toml` for this one key: a broken one in the profile fails a preview.

## Proposed plan row

`| F27 | Style from your edits | M | done, off by default (`history = true` under `[run]`, `autoedit style suggest`); the blind check and the Studio card are open, and no real history exists yet |`

For the tables at the end of `FEATURE_PLAN.md`. For the owner: the row "Keep a local history of your
own edits, on by default (F27)?" stays, with "Measured" as its evidence; new: "Are 12 photos, 3 days
and 70 % the right bar, and is a day the right run?" (`[suggest]` in `style.toml`). Changed against
the plan: "3 runs" is "3 days"; "70 %" counts every photo the change could have been made on; a look
can hold a `habit` table; the blind check is not built.

## Result

Built on 2026-11-17 in the unattended run. Status: done, off by default.

- **Built** by three Builders (Sonnet; T2 at high effort) from this spec. The Checker (Opus,
  high) found two things that had to be fixed, both privacy holes in the history line, fixed by a
  Builder the same night with tests: a `--look` word no look file defines (`--look
  jane_doe_client`, or `"look"` in an API request) was written into the line, and an op name from
  a hand-edited sidecar (`{"JaneDoe": 0.3}`) went into `changes`. Now `look` is written only when
  the library defines it, and only op names of `ops.OP_NAMES` are kept. Also fixed: a planted line
  with a bad `left_out` crashed `style suggest` (lines are now checked field by field on read and
  skipped); a habit value had no upper bound when read (now refused outside the op's bounds);
  `history.record` caught only two error types (now any error leaves the export intact with a
  warning). Notes kept: with history on and no profile, the default profile folder is created (as
  `looks save` does); an unknown place in `--set` raises a bare `KeyError` in `adjust` (older,
  outside F27).
- **What held** in the review: nothing smuggled into the file through folder names, file names,
  typed sentences, concept words, a mark box, the prompt, `--concepts` or the profile path;
  history off writes nothing; `profile_dir` cannot be set by a request, a run file or a sidecar;
  6 processes × 1,500 appends gave 9,000 readable lines; a suggestion changes nothing without
  `--yes NAME`; habits from a profile are clamped to `[limits]` and refuse marks; default output
  byte-identical to the committed code.
- **Checks:** 885 tests on both interpreters after the fixes; the stub smoke run is
  byte-identical and writes no history.
- **Open:** the owner's history does not exist yet, so whether 12 photos, 3 days and 70 % fit is
  unmeasured; the blind check and the Studio card are not built.
