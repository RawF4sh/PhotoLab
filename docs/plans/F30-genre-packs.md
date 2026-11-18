# F30. Genre packs

Status: spec (2026-11-17). Size M. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F30;
the kinds are in [GENRES.md](../GENRES.md). Written in an unattended run
([UNATTENDED_RUN.md](../UNATTENDED_RUN.md)): nothing was asked, and the owner's decisions are in
the table below. The design was built once on a scratch copy of the tree, which is deleted again:
the reference code is from that build and every number is measured. On the copy both suites ran,
on both interpreters: 823 tests before, 835 with the 12 of this spec, and one that failed both
times (`test_lut.py::test_a_broken_file_stops_the_run`, from F25, which was being built).

## Goal

A genre pack is a name for data that exists already: the things to find, opt-in rule sets, a
default look, a few run settings, and the kinds the reveal check (F21) will look for. `--genre
NAME` selects one, `autoedit genres list` shows them, a `genres.toml` in the profile holds the
user's own. No code per genre, and no genre is applied unless the user names it. Two packs ship:
`landscape`, today's `--preset landscape` byte for byte, and `alpine`, which adds the highlight
hold. Default output stays byte-identical.

## What was found in the code

1. `pipeline.py::Pipeline.concepts_for`: the concept set is `cfg.concepts`, else the
   `[preset.NAME]` of `cfg.preset`, else the style's list. The look is `cfg.look or style.look`. A
   preset or look name that no file defines is taken without a word: no regions, the neutral look.
2. `exports.py::preset_kwargs` is the pattern for a named bundle: `cli.py::load_run_config` and
   `web/service.py::make_cfg` turn it into `RunConfig` arguments; `RunConfig.export` keeps the
   name. The CLI's order: packaged `defaults.toml`, the preset, the files the user wrote (`explicit`:
   the profile's `defaults.toml`, the `--config` file), the flags. `make_cfg` reads no
   `defaults.toml`; a request key that is `None` counts as not given.
3. `rules.py::load(path, enable)` merges `[opt_in.NAME.<scene>.<block>]` into `[<scene>.<block>]`
   with `dict.update`: a set adds or replaces rules of a block, or adds a block. A name the file
   lacks is skipped without a word. A set that names a threshold (`[guard]`, `[quality_gate]` and
   the like hold plain values) ends in an `AttributeError` or a `TypeError`.
4. `pipeline.py::process_one` stores the run settings under `audit.run_config`, and
   `adjust.py::rerender` builds its config from them. `_config_digest` hashes the file behind
   every `*_path` field of `RunConfig`.
5. `paths.py::config_paths` returns `NAME_path` for each name in `_CONFIG_FILES`: the merged copy
   when the profile has the file, nothing for a name in `_OPTIONAL` whose file the folder lacks.
   `RunConfig` ignores a key it does not know: `reveal_path` reaches it today and is not a field.
6. `segment/stub.py`: of the landscape set the stub finds sky and water; of the default style's
   set also skin and a subject oval. So with the stub the two sets give different pixels, and a
   gate run says nothing about mountain, snow, grass, trees and rock.
7. `web/static/studio.html::editOptions` sends `style`, `intensity` and `concepts` (always a list)
   with every request: the page's choices would win over a genre's concept set and look.
8. On the 110 landscape photos F21 measured true findings of four kinds of `reveal.toml`:
   `people` (10 photos), `face` (1), `writing` (a summit sign), `address` (a trail signpost).

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Which packs ship | `landscape` and `alpine`. No pack for a kind without photos, not even one marked untested: the plan keeps such a pack `planned`, the words a pack gives SAM 3 have to be measured first (F21: "face" found animals, "child" found distant adults), and every name in the packaged file is one a user can pick | a table in `genres.toml`; [GENRES.md](../GENRES.md) has the plan per kind |
| `alpine` has the highlight hold on while the vote on the hold is open | Yes. A genre is applied only when it is asked for, so no default changes. Known from F6: with the hold, 12 of the 110 corpus photos are flagged for review | delete `highlight_hold = true` under `[alpine.run]`, or the whole pack |
| Where a genre is applied | Where the config is built, like an export preset (fact 2). `RunConfig.genre` is the name; the sidecar holds what the genre set, and a re-render replays that without reading `genres.toml` | not meant to be reversed: Design 1 |
| The order | The packaged defaults, the genre, the export preset, the files the user wrote (profile defaults, run file), the flags or request fields. Key by key | the block in `load_run_config` |
| A style against the genre's look | A style the user names (flag, file or request) keeps its own look. The genre's concept set still applies, as `--preset` wins over a style's list today | `style_named` in `genres.run_kwargs` |
| What a genre may set | A concept set, a look, rule sets and 13 run settings (`RUN_KEYS`). Nothing that names a file, reaches the network, picks a backend or moves the exports | `RUN_KEYS` in `genres.py` |
| What `tested = false` does | The pack is listed as untested and is not among the choices of the settings schema, which is what a menu offers. `--genre NAME` still takes it. A genre of the profile is always offered | `offered` in `genres.listing` |
| The suggestion for a folder | Not built. The repo has one kind of photo, so a suggester could only be checked on saying "landscape", and telling `alpine` from `landscape` needs SAM 3's snow mask. Nothing applies a genre without a name the user gave | a task of its own when a second kind has photos |
| Studio | No menu (fact 7). The API has the field, the list and the schema entry (Design 5) | the Studio rebuild, or the test UI |
| What the reveal check gets | Per genre a list of kinds of `reveal.toml`, read by nothing yet. Kinds, not words: a kind carries the label and the score. F21's task T2 adds the run setting for them and one line in `run_kwargs` | F21 decides when it is wired |

## Design

### 1. A genre becomes run settings where the config is built

Read inside the pipeline, a genre would have to be read again by every re-render, and a change to
`genres.toml` would change photos that are already edited. So one function, `genres.run_kwargs`,
turns a genre into `RunConfig` arguments, and the three places that build a config from names
call it: `cli.py::load_run_config`, `web/service.py::make_cfg`, `scripts/tone_report.py`.
`preset` becomes `RunConfig.preset`, `look` becomes `RunConfig.look`, `[NAME.run]` the run
settings of those names, and `rule_sets` the new `RunConfig.rule_sets`, which `Pipeline.build`
hands to `rules.load`. The other keys change nothing in a run. `RunConfig.genre` is the name and
nothing else, as `RunConfig.export` is: a config that no builder made is not changed by it (test 2).

### 2. `genres.py` (new)

```python
"""Genre packs (F30): named bundles of data that exists elsewhere, in configs/genres.toml. A genre
is turned into RunConfig arguments where a config is built (cli.py, web/service.py), below what the
user wrote, like an export preset. The pipeline never reads this file; there is no code per genre."""

# Imports: annotations from __future__; os, re; pydantic's ValidationError; paths; contract.RunConfig.

KEYS = ("description", "tested", "preset", "look", "rule_sets", "reveal", "run")
# What [NAME.run] may set: how the photo is edited, nothing else. No file, no network, no backend,
# no output folder: a genre file can come from someone else.
RUN_KEYS = ("intensity", "palette", "palette_strength", "consistency", "compose", "vignette", "sharpen",
            "highlight_hold", "levels", "light_shaping", "denoise", "grain", "grain_size")
_NAME = re.compile(r"[a-z0-9_]{1,40}")
_MAX = {"description": 200, "preset": 40, "look": 40}


def _file(kw: dict, name: str) -> str:
    return kw.get(f"{name}_path") or paths.config_path(f"{name}.toml", packaged_fallback=True)


def load(path: str) -> dict[str, dict]:
    """Every genre of the file at `path` (the packaged file or a merged copy), checked. No file: none."""
    data = paths._load(path) if path and os.path.exists(path) else {}
    check(data, path)
    return data


def run_kwargs(name: str, kw: dict, style_named: bool = False) -> dict:
    """RunConfig arguments of the genre `name`. `kw` holds the run's config file paths
    (`paths.config_paths`); a missing one is found the usual way. `style_named`: the user named a
    style, which keeps its own look. Unknown genre, or a preset or look no file defines: ValueError."""
    from . import looks
    from .concepts import load_concepts

    genres = load(_file(kw, "genres"))
    if name not in genres:
        raise ValueError(f"unknown genre {name!r}; choose one of {sorted(genres)}")
    g = genres[name]
    preset, look = g.get("preset", ""), g.get("look", "")
    if preset and preset not in load_concepts(_file(kw, "concepts"))[0]:
        raise ValueError(f"genre {name}: preset {preset!r} is not a [preset] of concepts.toml")
    if look and look not in looks.load(_file(kw, "looks")):
        raise ValueError(f"genre {name}: look {look!r} is not a look of looks.toml")
    out = dict(g.get("run", {}))
    if preset:
        out["preset"] = preset
    if look and not style_named:
        out["look"] = look
    if g.get("rule_sets"):
        out["rule_sets"] = list(g["rule_sets"])
    return out
```

Two more functions, pinned by tests 3, 7, 9 and 10:

- `check(data: dict, path: str) -> None`, for the packaged file, a merged copy and a profile's
  own part. Every error is a `ValueError` that starts with `f"{path}: genre {name}"`. It refuses:
  a name that does not match `_NAME`, or a genre that is not a table; a key outside `KEYS` (the
  message lists them); `tested` that is not a bool; `run` that is not a table; `description`,
  `preset` or `look` that is not a string of at most `_MAX[key]` characters; `rule_sets` or
  `reveal` that is not a list of at most 20 strings matching `_NAME`; a key under `run` outside
  `RUN_KEYS` (the message lists them); a value under `run` that `RunConfig.model_validate(run,
  strict=True)` refuses, as `run.KEY` plus the `msg` of the first entry of
  `ValidationError.errors()`. Strict, so that a TOML string is not a number or a switch.
- `listing(configs_dir=None, profile=None) -> list[dict]`, for the list, the API and the schema:
  one dict per genre of the merged file (`load(_file(paths.config_paths(configs_dir, profile),
  "genres"))`), in file order, with the keys `name`, `description`, `tested`, `origin`, `offered`,
  `preset`, `concepts` (that preset's list in the merged `concepts.toml`, `[]` without one),
  `look`, `rule_sets`, `reveal`, `run`. `own` is the profile's own part
  (`(paths._overlay("genres.toml", profile) or ("", {}))[1]`), `base` the genres without the
  profile. `origin`: `"packaged"` for a name not in `own`, `"packaged, changed by the profile"`
  for one in both, else `"profile"`. `offered`: `tested or name in own`.

`paths.py`: `"genres"` joins `_CONFIG_FILES` and `_OPTIONAL` (a configs folder from before F30
gets the packaged file). `_overlay` gains, after the `reveal.toml` branch, `elif base_name ==
"genres.toml":` with `from . import genres` and `genres.check(data, path)`; the import stays inside
the branch, because `genres` imports `contract`, which imports `paths`.

### 3. The order

`cli.py::load_run_config`, directly before the comment `# Order, lowest to highest: the packaged
defaults, the preset, ...` (`calibrate.py` and two scripts call this function with argument
objects of their own, hence `getattr`):

```python
    # A genre (F30) sits directly above the packaged defaults: the export preset, the files and the
    # flags win over it. `--genre ""` switches off a genre that a file names.
    genre = getattr(args, "genre", None)
    if genre is None:
        genre = explicit.get("genre") or kw.get("genre")
    if genre:
        from . import genres

        kw.update(genres.run_kwargs(
            genre, kw, style_named="style" in explicit or getattr(args, "style", None) is not None))
```

Directly after `kw["export"] = preset or None`: `kw["genre"] = genre or None`. `"genre"` joins the
`[run]` tuple of `_flatten` and stays out of `overrides`. `web/service.py::make_cfg`, directly
before `if opts.get("export"):` (`"genre"` and `"rule_sets"` stay out of `_ALLOWED`, so a request
cannot name rule sets):

```python
    if opts.get("genre"):   # a genre first (F30): the export preset and explicit options win over it
        from .. import genres as _genres
        kw.update(_genres.run_kwargs(str(opts["genre"]), kw, style_named=opts.get("style") is not None))
        kw["genre"] = str(opts["genre"])
```

### 4. Rule sets (`rules.py`, `pipeline.py`, `contract.py`)

`rules.py::load` becomes `load(path, enable=(), require=())`. Its docstring gains: "`require`
names the rule sets of a genre (F30): merged the same way, but a name the file does not define is
an error, because the pack would then run without its rules and say nothing." From the line
`opt_in = data.pop("opt_in", {})` on, the body is:

```python
    opt_in = data.pop("opt_in", {})
    missing = [n for n in require if n not in opt_in]
    if missing:
        raise ValueError(f"{path}: no opt-in rule set named {', '.join(missing)} "
                         f"(the file has: {', '.join(opt_in) or 'none'})")
    for name in (*enable, *require):
        try:
            for scene, blocks in opt_in.get(name, {}).items():
                for block, entries in blocks.items():
                    data.setdefault(scene, {}).setdefault(block, {}).update(entries)
        except (AttributeError, TypeError, ValueError):   # a value where a table of rules belongs
            raise ValueError(f"{path}: opt-in rule set {name} must hold [opt_in.{name}.<scene>.<block>] "
                             "tables of rules") from None
    return data
```

- `pipeline.py::Pipeline.build`: the call `rules_mod.load(...)` gains `require=cfg.rule_sets`.
- `pipeline.py::process_one`, directly after `notes = build_notes(gstats, region_stats,
  resolved.detected)`: `if cfg.genre:` with `notes.append(f"genre: {cfg.genre}")`. The stage order
  does not change.
- `contract.py`, directly after `style`:

```python
    # The genre pack this config was built from (genres.toml, F30). Like `export` it is applied where the
    # config is built (cli.py, web/service.py): the name alone changes nothing.
    genre: str | None = Field(default=None, max_length=40)
    rule_sets: list[str] = Field(default_factory=list, max_length=20)  # opt-in rule sets of rules.toml a genre switched on
```

A genre's rules pass the looks, the region policies and every gate like any rule, and cannot
reach a threshold (fact 3, now a clear error). A later pack with rules is `[opt_in.NAME...]` in
`rules.toml`, a `[preset.NAME]` in `concepts.toml` and a genre that names both: no Python.

### 5. The command line, the API and the schema

- `cli.py::_add_run_flags`, after `--style`: `--genre`, with the help ``"genre pack from
  genres.toml (`autoedit genres list`): the things to find, a look and run settings for one kind
  of photo; other flags win over it"``.
- `autoedit genres list` (`_cmd_genres`, next to `_cmd_exports`; the module docstring gains the
  command): a parser `genres` with `--configs-dir`, `--profile` and a positional `genres_cmd` with
  `choices=["list"]`. Per row of `genres.listing(args.configs_dir, args.profile)` it prints
  `NAME  (ORIGIN, tested|untested)  ` and, joined by two spaces, the non-empty ones of
  `preset=...`, `look=...`, `rule_sets=a,b`, `reveal=a,b` and each run setting as `key=<json
  value>`; then a line of four spaces and the description. A `ValueError`: stderr, exit code 2.
- `web/service.py::list_genres(configs_dir, profile)` returns `genres.listing(...)`; `web/app.py`
  gains `GET /genres` next to `/presets`. The request field `genre` works wherever `make_cfg`
  builds the config (`/preview`, `/run`, `/run-one`, `/jobs`, `/variants`); an unknown name is a 400.
- `settings_schema.py::_choices` gains `"genre": [g["name"] for g in genres.listing(configs_dir,
  profile) if g["offered"]]`. `settings.toml`: `"rule_sets"` joins `hidden`; before
  `[setting.look]` comes `[setting.genre]` with `label = "Kind of photo"`, `group = "look"`, `help
  = "A pack for one kind of photography: what to find, a look and which tools are on. Your own
  choices win over it."`
- `defaults.toml`, after the `style` line, one line: ``# genre = "landscape"      # a genre pack
  from genres.toml (F30, `autoedit genres list`); left out = none``.
- `scripts/tone_report.py` gains `--genre`. The lines of `main` that build `kw` move into
  `_render_kwargs(args, extra) -> dict`, which after `kw = {"backend": args.backend}` adds `if
  args.genre:` with `from autoedit import genres` and `kw.update(genres.run_kwargs(args.genre, kw,
  style_named=bool(args.style)), genre=args.genre)`. `_parse_sets` reports the key `genre` as an
  error and skips it: `f"--set {pair}: this names a genre without applying it, use --genre {raw}"`.

## Config: `src/autoedit/configs/genres.toml` (new, the whole file)

```toml
# AutoEdit genre packs (F30): `--genre NAME`, or `genre = "NAME"` under [run] in a run file.
# A genre selects data that lives in the other config files. There is no code per genre, and no
# genre is applied unless it is asked for. What you write in a file or on the command line wins.
# Your own genres go into a genres.toml in your profile folder, which is merged over this file.
# Keys of a genre, all optional:
#   description  one sentence for lists and menus
#   tested       true only after a golden test and the tone gate (0 dark photos made darker, 0
#                pushed into clipping) on at least 20 photos of its kind. Left out = false: a menu
#                does not offer the pack; `--genre NAME` still takes it.
#   preset       the things to find: a [preset.NAME] of concepts.toml
#   look         the default look, a look of looks.toml. A style you name yourself keeps its own.
#   rule_sets    opt-in rule sets: the [opt_in.NAME.<scene>.<block>] tables of rules.toml
#   reveal       the kinds of reveal.toml the reveal check looks for with this genre (F21 will
#                read it; nothing reads it yet). Left out = every kind.
#   [NAME.run]   run settings: intensity, palette, palette_strength, consistency, compose,
#                vignette, sharpen, highlight_hold, levels, light_shaping, denoise, grain, grain_size

[landscape]
description = "Wide outdoor scenes: finds sky, mountain, snow, grass, trees, water and rock."
tested = true
preset = "landscape"
reveal = ["people", "face", "writing", "address"]

[alpine]
description = "Mountains and snow: the landscape set, and what is blown white in the photo stays white."
tested = true
preset = "landscape"
reveal = ["people", "face", "writing", "address"]

[alpine.run]
highlight_hold = true
```

## Files

| File | Change | Task |
|---|---|---|
| `src/autoedit/configs/genres.toml`, `src/autoedit/genres.py`, `src/autoedit/paths.py` | new: Config, Design 2; the two tuples and the branch in `_overlay` | T1 |
| `src/autoedit/contract.py`, `src/autoedit/rules.py`, `src/autoedit/pipeline.py` | Design 4 | T1 |
| `src/autoedit/settings_schema.py`, `src/autoedit/configs/settings.toml`, `tests/snapshots/settings_schema.json` | the choices, the words, regenerated | T1 |
| `src/autoedit/cli.py` | `_flatten`, the block, the flag, `genres list` | T2 |
| `src/autoedit/web/service.py`, `src/autoedit/web/app.py`, `tests/snapshots/api_v1.json` | the block, `list_genres`, the route, regenerated | T2 |
| `src/autoedit/configs/defaults.toml`, `scripts/tone_report.py` | the comment line; `--genre` | T2 |
| `tests/test_genres.py` | new: tests 1 to 4 (T1), 5 to 12 (T2) | T1, T2 |

## Tasks

T2 needs T1. F25, F26 and F27 edit `load_run_config`, `_add_run_flags`, `make_cfg`, `contract.py`,
`settings.toml` and the schema snapshot too: every edit here is placed by its anchor text, the
snapshots are regenerated, never edited by hand, and one Builder at a time works in these files.

| Task | Tier | Content | Proving command |
|---|---|---|---|
| T1 | Builder, Sonnet, medium | Config; Design 2 and 4; the schema part of Design 5; tests 1 to 4; the snapshot (`AUTOEDIT_UPDATE_SNAPSHOTS=1 PYTHONPATH=src python3 -m pytest tests/test_settings_schema.py -q`) | `PYTHONPATH=src python3 -m pytest tests/test_genres.py tests/test_settings_schema.py tests/test_profile.py tests/test_rules.py tests/test_levels.py tests/test_denoise.py tests/test_pipeline.py tests/test_adjust.py -q` |
| T2 | Builder, Sonnet, medium | Design 3 and the rest of Design 5; tests 5 to 12; the snapshot (`AUTOEDIT_UPDATE_SNAPSHOTS=1 PYTHONPATH=src python3 -m pytest tests/test_api_contract.py -q`) | `PYTHONPATH=src python3 -m pytest tests/test_genres.py tests/test_cli.py tests/test_export_presets.py tests/test_web_service.py tests/test_api_contract.py tests/test_settings_schema.py tests/test_profile.py tests/test_tone_report.py tests/test_levels.py tests/test_calibrate.py tests/test_contact_sheet.py tests/test_blindtest.py tests/test_defaults.py -q` |
| Check | Checker, Opus, high | The diff against this spec (`pipeline.py` and `contract.py` are in it), the definition of done, checks 5 to 7 | none |
| Docs | The session, inline | `README.md` (one `--genre` example, `autoedit genres list`, a line for `genres.toml`); `ARCHITECTURE.md` sections 6, 15 and 16; `APP_GUIDE.md` sections 10 to 12; `GENRES.md` ("built" for kinds 7 and 8); `CHANGELOG.md`; the plan row | none |

## Tests

`tests/test_genres.py`; no existing test changes, the two snapshots gain one setting and one route.
`_configs(tmp_path)` copies the packaged configs and appends a genre `demo` to `genres.toml`
(`preset = "wedding"`, `look = "punchy"`, `rule_sets = ["demo"]`, `reveal = ["plate"]`, no
`tested`; under `[demo.run]`: `denoise = true`, `intensity = 0.5`, `consistency = "bursts"`) and to
`rules.toml` a table `[opt_in.demo.SCENE.global]` with `vibrance = "9"` for each of the three
scenes. `_photos` writes JPEGs of 96 by 128 px (top half `[100, 140, 200]`, bottom half `[170, 120,
90]`, seeded noise of 0 to 19): with the default set the stub finds skin and a subject in them,
with the landscape set neither. Sidecars are compared without the two timing fields.

| # | Test | Asserts |
|---|---|---|
| 1 | `test_packaged_packs_golden` | The golden table: the packaged genres are exactly `landscape` and `alpine`, in that order; `run_kwargs` gives `{"preset": "landscape"}` and `{"preset": "landscape", "highlight_hold": True}`; both are `tested` and have a description; every rule set is under `[opt_in]` of `rules.toml` and every `reveal` name a kind of `reveal.toml`; a stub pipeline built from each asks for `sky, mountain, snow, grass, trees, water, rock`; `Pipeline.highlight_hold` is `None` for `landscape` and `(0.80, 0.985)` for `alpine` |
| 2 | `test_default_is_unchanged` | `RunConfig().genre is None`, `rule_sets == []`; the CLI's default config has neither, nor a preset or a look; `make_cfg({})` equals `make_cfg({"genre": None})` and `make_cfg({"genre": ""})`; `rules.load(path) == rules.load(path, require=())`; a default stub batch stores `genre: null` and `rule_sets: []` and has no note that starts with "genre"; `RunConfig(genre="alpine")` equals the default config but for the name |
| 3 | `test_a_bad_file_is_refused` | `genres.load` raises a `ValueError` that names the file for: an unknown key (the message lists the valid keys); a value in place of a table; the name `Bad-Name`; `tested = "yes"`; `preset = 3`; `rule_sets = "a"`; `reveal = ["../x"]`; a description of 201 characters; `run = 1`; under `run`: `backend`, `api_key`, `allow_cloud`, `decider`, `out_subdir`, `reference`, `preset` (the message lists `RUN_KEYS`), `intensity = 7`, `intensity = nan`, `denoise = "yes"`, `intensity = "0.5"`, `consistency = "x"`. A missing file gives `{}`. `run_kwargs` raises for a genre whose preset or look no file defines and names both; `Pipeline.build` raises for a genre whose rule set `rules.toml` lacks, and the message lists the sets the file has |
| 4 | `test_rule_sets_join_the_rules` | `rules.load(path, require=["demo"])` has `vibrance = "9"` in `[landscape.global]`, `rules.load(path)` has not, and neither has a key `opt_in`; `require=["demo", "nope"]` raises and names `nope`, `demo` and `levels`; a set `[opt_in.loose.quality_gate]` with `dark_regress = 0.5` raises a `ValueError` that names `loose` and the file, and a plain load of that file is unchanged; a stub batch with `rule_sets=["demo"]` has a higher global `vibrance` and other bytes than one without, stores the list and has the same `config_digest`; `adjust.rerender` of it writes the same bytes without a warning |
| 5 | `test_genre_landscape_is_the_preset_byte_for_byte` | Three runs of `cli.main(["run", ...])` on two photos, stub: `--preset landscape`, `--genre landscape`, neither. The first two write the same bytes and the third other bytes; the sidecars of the first two are equal once `audit.run_config.genre` (`"landscape"` against `None`) and the one note `genre: landscape` are taken out; the two configs are equal but for `genre` |
| 6 | `test_what_the_user_wrote_wins` | `--genre demo` sets preset, look, rule sets, `denoise`, `intensity` and `consistency`; flags for `intensity`, `look`, `preset` and `consistency` win while `denoise` stays on; with `--style dramatic` the look stays `None` and the preset is the genre's; `--concepts sky` gives the pipeline `["sky"]`; a run file with `genre`, `intensity` and `denoise = false` wins on both keys and keeps the preset; a flag names another genre than the file; `--genre ""` on a file that names one gives exactly the default config; `style` in the run file keeps the look out; `--export web` and the genre both apply; a profile's `defaults.toml` names the genre and wins with `denoise = false`; an unknown name raises with the known names, and `cli.main` returns 2; a genre named in the configs folder's own `defaults.toml` applies, loses against a flag and is switched off by `--genre ""` |
| 7 | `test_api_field_and_listing` | `make_cfg({"genre": "alpine"})` has the preset and the hold; `highlight_hold: False` in the request wins; `demo` sets look, preset, rule sets and `denoise`; with `style`, `export` and `concepts` in the request the look is `None` and all three apply; `{"rule_sets": [...]}` in a request is ignored; an unknown genre raises; `list_genres` gives both packs with exactly the keys of `listing` (for `alpine`: tested, origin `packaged`, offered, the seven concepts, `run == {"highlight_hold": True}`); the schema's choices for `genre` are both names; with FastAPI, `GET /api/v1/genres` and `GET /api/genres` return that list |
| 8 | `test_a_genre_run_and_its_replay` | `--genre demo` against the same settings written out as flags without the rule set: a higher global `vibrance` and other bytes; the sidecar stores `genre`, `preset`, `look`, `rule_sets`, `denoise` and has the note `genre: demo`; with `genres.toml` deleted from the folder, `adjust.rerender` writes the same bytes without a warning |
| 9 | `test_profile_genres` | A profile `genres.toml` with a new genre `mine` and `[alpine] look = "dramatic"`: `config_paths` gives a merged copy that holds `landscape`, `alpine`, `mine`; `alpine` differs from the packaged one by that key only; the origins are `packaged`, `packaged, changed by the profile`, `profile`; `mine` is untested and offered; the CLI and `make_cfg` take `mine` with the profile; the schema's choices have `mine` with the profile and not without; the packaged file's bytes are unchanged; a profile file with `[mine.run] backend = "sam3"` makes `config_paths` raise, naming that file and the key |
| 10 | `test_an_untested_pack_is_listed_and_not_offered` | `demo` is `tested False` and `offered False`; the schema's choices are the two packaged names; `run_kwargs("demo", ...)` works; `genres list --configs-dir DIR` prints `demo  (packaged, untested)`, `landscape  (packaged, tested)`, `highlight_hold=true` and the description, with exit code 0; with a broken genre in the file the exit code is 2 |
| 11 | `test_a_configs_folder_from_before_f30_gets_the_packaged_genres` | A copy of the configs without `genres.toml`: `config_paths` has no `genres_path`, `--genre alpine` works, `listing` names both packs |
| 12 | `test_the_tone_report_takes_a_genre` | `_render_kwargs` with `genre="alpine"` gives `{"backend": "stub", "preset": "landscape", "highlight_hold": True, "genre": "alpine"}`; `--set highlight_hold=false` wins; without a genre nothing is added; `_parse_sets(["genre=alpine", "levels=true"], ...)` reports one error that says `--genre alpine` and returns `{"levels": True}` |

## Acceptance checks (the session runs these)

1. Both suites are green: `PYTHONPATH=src python3 tests/run_all.py`, and with `.venv/bin/python`.
2. The stub smoke run of `CLAUDE.md` completes with sidecars and a manifest. Once more with
   `--genre alpine` into a new folder: every sidecar has the note `genre: alpine`, and under
   `audit.run_config` the genre, `preset: "landscape"` and `highlight_hold: true`.
3. Default output is byte-identical to the baseline renders (maximum difference 0). Not pixels:
   sidecars gain `genre` and `rule_sets` under `audit.run_config`; `config_digest` stays.
4. The plan's bar on real photos: 24 JPEGs of `curated-dolomites-summer-2026/selected_jpeg`, stub,
   with `--preset landscape` and with `--genre landscape`: every pair of exports is identical
   (`cmp`). Measured on the scratch build for three of them at full size and the two smoke photos:
   identical, as were `--genre alpine` and `--preset landscape --highlight-hold`.
5. The tone gate, stub, all 110 RAW files, three runs (no genre, `landscape`, `alpine`):
   `PYTHONPATH=src python3 scripts/tone_report.py curated-dolomites-summer-2026/selected_raw --render --fail-on-regression --genre NAME --json out-features/F30/tone-NAME.json`.
   Each reports 0 dark to darker and 0 pushed into clipping, and no photo has a lower
   `safety_fallback` (1.0 = not pulled back) with a pack than without a genre. Measured on the
   first 10 photos: 0 and 0 in all three; the run without a genre pulled 2 back (to 0.75 and 0.25)
   and flagged them for review, the packs none: the stub's skin and subject shapes do that.
6. The same three runs with SAM 3 on the first 24 photos, one at a time (`.venv/bin/python`,
   `--backend sam3 --limit 24`): these edit mountain, snow, grass, trees, rock (fact 6). Same bar.
7. `tested` follows checks 5 and 6: a pack that fails one, or for which one could not be run,
   keeps its table and gets `tested = false` in `genres.toml` and in test 1; the Result says why.
8. A blind sheet per pack for the owner (`.venv`, SAM 3):
   `scripts/contact_sheet.py --panel out-loop/panel.txt --best src/autoedit/configs --candidate DIR --out-dir out-features/F30/sheets-NAME --backend sam3`,
   with `DIR` a copy of the configs under `.tmp/` that has `genre = "NAME"` under `[run]` of its
   `defaults.toml`. A pair that `facts.txt` shows about one 8-bit level apart is not put before
   the owner; the Result then gives the numbers.
9. By hand: `autoedit genres list`; a profile folder under `.tmp/` with a genre of its own is
   listed as `profile` and a stub run with it completes; `--genre nope` ends with exit code 2.

## Known limits

- Two packs, both for landscapes. Every other kind waits for photos ([GENRES.md](../GENRES.md));
  portrait, wedding, events, travel and phone photos need only data that exists, and a measurement.
- `RunConfig(genre=...)` alone applies nothing. `scripts/quality_workflow.py`,
  `scripts/raw_benchmark.py` and `experiments.py` build their own config and take no genre.
- Studio has no menu, and today's page would win over a genre's concept set and look (fact 7).
- The profile's `defaults.toml` counts as written by the user: `denoise = false` there wins over
  a genre's `denoise = true`. A profile can add a genre and change a key of a packaged one; it
  cannot remove a key, as with looks.
- A genre cannot set the scene, the style or the brief. `scene.py::classify` has an `override`
  parameter that nothing passes; F31 decides whether `astro` needs it.
- Only a genre's preset and look are checked: `--preset typo` and `--look typo` still pass
  without a word (F26's open question). `tested` is a word in a file that nothing verifies, and a
  run with an untested pack prints no warning. `reveal` is read by nothing.

## Proposed plan row

`| F30 | Genre packs | M | done for the mechanism and two packs (`--genre landscape`, `--genre alpine`, `autoedit genres list`, own genres in the profile); every other kind waits for photos; no suggestion for a folder and no Studio menu |`

For the two tables at the end of `FEATURE_PLAN.md`:

- For the owner: "Should the `alpine` pack keep the highlight hold?" Evidence: the blind gallery
  `out-features/galleries/highlight-hold/`, the sheets in `out-features/F30/`. The change:
  `[alpine.run]` in `genres.toml`. "Which kinds of photography first?" stays open; its change
  becomes a table in `genres.toml`, rules under `[opt_in.NAME...]` and 20 photos for the gate.
- Changed against the plan: "F30 has no Studio menu and no suggestion for a folder." Why: fact 7,
  and one kind of photo in the repo. Reverse it by: the Studio rebuild; a task of its own when a
  second kind has photos.

## Result

Built on 2026-11-17 in the unattended run. Status: done for the mechanism; both packs untested.

- **Built** by two Builders (Sonnet) from this spec. The T2 Builder widened the test helper that
  drops timing fields (the sidecar's own timing keys differ from the spec's guess).
- **The Checker (Opus, high)** found nothing that had to be fixed and seven notes. Fixed by the
  session, with tests: a genre given as a list in a run file crashed with `TypeError` (now
  "unknown genre"); `tone_report --set style=X` kept a pack's look where the CLI drops it; test 3
  never reached the look check (now its own case). Kept as known limits: a profile pack that names
  an unknown rule set is listed and fails only when run; tests 4 and 8 check that the bytes
  differ, not that vibrance is higher; test 6 has no `--concepts sky` case.
- **Checks:** both suites green after T2 (937 on each interpreter); the smoke run with
  `--genre alpine` has the note and `genre`, `preset`, `highlight_hold` in the sidecar;
  default output byte-identical; `--genre landscape` equals `--preset landscape` byte for byte on
  the smoke photos and on 24 JPEGs of the curated corpus (`out-features/F30/check4.txt`);
  `--genre nope` exits 2; a profile pack is listed as `profile` and runs.
- **The tone gate:** 0 dark photos made darker and 0 pushed into clipping in all nine runs (stub on
  110 RAW files and SAM 3 on 24, each without a genre, with `landscape` and with `alpine`;
  `out-features/F30/tone-*.json`). But the bar "no photo has a lower `safety_fallback` with a
  pack" fails once per backend: on the stub `0063_snow` is pulled back to 0.75 with either pack
  (8 photos pulled back in all, against 11 without a genre); with SAM 3 `0001_dark` is (3 against
  2). By check 7 both packs are `tested = false`: `--genre` takes them, no menu offers them. That
  is an owner question in the plan.
- **Not run:** the blind sheets of check 8 (SAM 3, one per pack), left for when the packs are
  reconsidered.
