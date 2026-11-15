# F11. User config layer

Status: spec (2026-11-11). Size M. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F11.

## Goal

A user changes one value, or adds one look, without copying a whole config file. A *profile
folder* holds TOML files with only the differences; they are merged key by key over the packaged
files. `autoedit looks list | save | delete` manages custom looks in the profile.

With no profile, nothing changes: the packaged files are read as today.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Where is the profile? | `--profile DIR`, else `AUTOEDIT_PROFILE`, else `~/.config/autoedit` when that folder exists (`$XDG_CONFIG_HOME/autoedit`; `%APPDATA%\\autoedit` on Windows). An empty `AUTOEDIT_PROFILE` switches the profile off | `paths.profile_dir` |
| Which files can be overlaid? | Every config file a run reads (rules, looks, concepts, styles, compose, intents, palettes, instructions, exports) and `defaults.toml` | `paths._CONFIG_FILES` |
| Can an overlay delete a key? | No. It can only set or add | Nothing to reverse |
| How do the loaders see the overlay? | They do not. The merged result is written to a temporary folder and the run's config paths point there | See "Design" |

## Design

**Merge at the path level.** Ten loaders read config files, from five call sites each. Instead of
teaching each of them about overlays, the place that builds a `RunConfig` (the CLI, the web
service, `adjust`) asks `paths.config_paths(configs_dir, profile)` for the paths. For a config file
that has an overlay in the profile, that function merges base and overlay, writes the result into
a per-process temporary folder, and returns that path. Loaders, the pipeline, `workflows.py`, the
render workers and the config digest then work on plain files, unchanged.

- The merge is deep for tables and replaces everything else (scalars, lists).
- The merged file is written by a small TOML writer (`paths.dump_toml`). The same writer saves
  custom looks. Property test: every packaged config file survives `tomllib.loads(dump_toml(x))`.
- The temporary folder is created once per process (`tempfile.mkdtemp`), removed at exit, and its
  files are rewritten only when the base file or the overlay changed (compared by modification
  time and size).
- The config digest that F3 stores in each sidecar is computed over the merged files, so
  `autoedit adjust` warns when the profile changed since the batch.

**Validation at merge time**, with the overlay's own path in the message:

- the overlay must be valid TOML;
- a key that is a table in the base must be a table in the overlay, and the reverse;
- `looks.toml`: every key of a look must be a field of `looks.Look`, and its value a number (a
  boolean for `preserve_exposure`).

A file in the profile that is not a known config file is ignored; `autoedit doctor` lists it.

## `paths.py`

```python
def profile_dir(explicit: str | None = None) -> str | None:
    """The user's profile folder, or None. `explicit` (--profile) must exist. Otherwise
    AUTOEDIT_PROFILE (an empty value switches the profile off), otherwise the default location
    when that folder exists."""
    if explicit:
        if not os.path.isdir(explicit):
            raise ValueError(f"profile folder not found: {explicit}")
        return explicit
    env = os.environ.get("AUTOEDIT_PROFILE")
    if env is not None:
        return env if env and os.path.isdir(env) else None
    base = os.environ.get("APPDATA") if os.name == "nt" else (
        os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config"))
    default = os.path.join(base, "autoedit") if base else ""
    return default if default and os.path.isdir(default) else None


def default_profile_dir() -> str:
    """Where `autoedit looks save` creates a profile when none exists."""


def merge(base: dict, over: dict, where: str = "") -> dict:
    """Deep merge of `over` into a copy of `base`. Tables merge, everything else is replaced.
    A table over a non-table (or the reverse) is a ValueError naming the key."""


def dump_toml(data: dict) -> str:
    """Write a dict as TOML: tables (nested), strings, numbers, booleans and lists of those.
    Enough for the config files of this project; anything else is a TypeError."""


def read_config(path: str, profile: str | None = None) -> dict:
    """The config file at `path`, with the profile's file of the same name merged over it."""


def config_paths(configs_dir: str | None = None, profile: str | None = None) -> dict[str, str]:
    """As before; a file that has an overlay in `profile` is returned as the path of the merged
    copy."""
```

Writer rules: scalars and lists of a table first, then its sub-tables under dotted headers
(`[a.b]`); keys that are not bare (`[A-Za-z0-9_-]+`) are quoted; strings are written with
`json.dumps` (a valid TOML basic string); floats with `repr`; an empty table still gets its
header. Key order is the dict's order, so the output is deterministic.

## CLI and service

- `--profile DIR` on `run`, `adjust`, `ui`, `presets`, `styles`, `exports`, `looks`.
- `cli.main` resolves `args.profile = paths.profile_dir(getattr(args, "profile", None))` once, for
  the commands that have the option, and prints `profile: <dir>` when one is used.
- `load_run_config`: `defaults.toml` is read with `paths.read_config(path, args.profile)`; the
  config paths come from `paths.config_paths(configs_dir, args.profile)`.
- `service.make_cfg(opts, configs_dir, profile=None)`; `web/app.py::create_app(..., profile=None)`
  passes it on; `autoedit ui` hands in the resolved profile.
- `adjust.rerender(..., profile=None)` uses the same call.

### `autoedit looks`

- `looks list`: every look with its origin (`packaged`, `profile`, or `packaged, changed by the
  profile`) and the fields that differ from the dataclass defaults.
- `looks save NAME [--from BASE] [--set FIELD=VALUE ...]`: the look `BASE` (default `natural`,
  as it is after the merge) with the given fields changed, written as table `NAME` into
  `<profile>/looks.toml`. The profile folder is created when it does not exist (the `--profile`
  folder, else the default location). Other tables of the file are kept; the file is rewritten by
  the TOML writer, so comments in it are lost (the command says so once).
- `looks delete NAME`: removes the table from the profile's file. A packaged look that the profile
  does not define cannot be deleted (exit code 2, a clear message).
- Unknown field, a value that is not a number, a name that is not `[a-z0-9_]+`: exit code 2.

## Tests stay hermetic

A developer's own `~/.config/autoedit` must not change test results: `tests/run_all.py` sets
`os.environ.setdefault("AUTOEDIT_PROFILE", "")` before it imports anything, and a new
`tests/conftest.py` does the same for pytest.

## Files

`src/autoedit/paths.py`, `src/autoedit/cli.py`, `src/autoedit/web/service.py`,
`src/autoedit/web/app.py`, `src/autoedit/adjust.py`, `src/autoedit/looks.py` (a `FIELDS` constant
for the validation), `tests/run_all.py`, `tests/conftest.py` (new), `tests/test_profile.py` (new).

## Task

One Builder (Sonnet, medium). Proving command:
`PYTHONPATH=src python3 -m pytest tests/test_profile.py tests/test_cli.py tests/test_config_hardening.py tests/test_adjust.py tests/test_web_service.py -q`,
then `PYTHONPATH=src python3 tests/run_all.py`.

## Tests: `tests/test_profile.py`

| # | Test | Asserts |
|---|---|---|
| 1 | `test_no_profile_changes_nothing` | With no profile, `config_paths(dir)` returns the plain paths of the folder and creates no temporary file; `load_run_config` gives the same `RunConfig` as before (compare with the paths built by hand) |
| 2 | `test_override_one_key` | A profile `looks.toml` with `[natural]\ncontrast_mul = 1.3`: the merged look `natural` has `contrast_mul == 1.3` and every other field of the packaged look; the other looks are unchanged; the base file on disk is unchanged |
| 3 | `test_add_one_look` | A profile `looks.toml` with a new table `mine`: `looks.load(merged path)` has the packaged looks plus `mine`; a run with `look="mine"` works |
| 4 | `test_reject_a_bad_file` | Invalid TOML in the overlay raises `ValueError` naming the overlay path; a look with the field `contrast_mull` raises naming the field and the valid fields; a string where a table is expected raises naming the key |
| 5 | `test_dump_toml_roundtrip` | For every `*.toml` in the packaged configs folder: `tomllib.loads(dump_toml(tomllib.load(f)))` equals the loaded dict. Also for a dict with a quoted key, a string with quotes, a backslash and a non-ASCII letter, a nested empty table, and a list of lists |
| 6 | `test_profile_defaults` | A profile `defaults.toml` with `[run]\nstyle = "dramatic"` changes `load_run_config(...).style`; a command-line `--style natural` still wins |
| 7 | `test_profile_resolution` | `profile_dir("x")` for a missing folder raises; with `AUTOEDIT_PROFILE` set to an existing folder it is returned; set to `""` gives `None` even when the default location exists (point `XDG_CONFIG_HOME` at a `tmp_path` that contains `autoedit/`); unset gives that default location. Environment restored in `finally` |
| 8 | `test_merged_files_follow_changes` | After the overlay file is rewritten with another value (and its modification time moved forward), `config_paths` returns a merged file with the new value; when nothing changed the merged file is not rewritten |
| 9 | `test_looks_commands` | `looks save warm2 --from natural --set vibrance_add=6` creates the profile folder and file; `looks list` shows `warm2` with origin `profile`; a second `save` of another look keeps `warm2`; `looks delete warm2` removes it; `looks delete natural` returns 2; `looks save bad --set nope=1` returns 2; `looks save "Bad Name"` returns 2 |
| 10 | `test_digest_sees_the_profile` | The config digest of a pipeline built with a profile overlay differs from the one without, and changes when the overlay changes |
| 11 | `test_workers_and_adjust_use_the_profile` | `batch.run` with `workers=2` and a profile look gives the same image bytes as with one worker; `adjust.rerender(image, profile=<dir>)` is byte-identical to the batch output, and without the profile it warns that the config files differ |

## Review round 1 (F8 to F11)

A Checker (Opus, high) reviewed F8, F9, F10 and F11 on one snapshot. One finding is blocking, the
rest are fixes worth making. Each row is one fix with its test. Rows marked "no change" record a
decision.

| # | Where | Finding | Fix | Test |
|---|---|---|---|---|
| 1 | `emit.output_paths`, `contract.py` (`out_name` validator) | **Blocking.** The name template is validated with dummy values and filled with the real style name, which nobody checks: `style = "../../escaped"` with `out_name = "{style}-{stem}"` wrote images two folders above the output folder (also reachable through a web request or a profile). | (a) The validator parses the template with `string.Formatter().parse`: only the fields `stem`, `style` and `n`, written plainly (no attribute or index access, no conversion); a format spec is allowed for `n` only and may not contain a brace. Anything else is a `ValueError` (also catch `TypeError` and `KeyError` from a trial format). (b) `output_paths` checks the FILLED stem: not empty, not `.` or `..`, no `/`, no backslash, no NUL, no leading dot; otherwise `ValueError` naming the stem. | `test_export_presets.py`: the style scenario through `service.make_cfg` + `batch.run` raises and writes nothing outside the output folder; `{n[0]}`, `{stem.x}`, `{stem!r}`, `{stem:{style}}`, `{stem:>8}` are rejected by `RunConfig`; `{n:04d}_{stem}` still works |
| 2 | `emit.output_paths` | An export can replace its own source: `autoedit run X/photos X --subdir photos`, or an output folder equal to the input folder with the same format. | In `output_paths`, after the target is chosen: when `os.path.realpath(image_out) == os.path.realpath(image_name)` raise `ValueError("... would overwrite the source photo ...")`. `batch.run`, `jobs._run` and `service.run_one` call `output_paths` before they render, so nothing is written. The CLI prints the message and returns 2 (it already does for `ValueError` in `_cmd_run`). | A run into the parent of the photo folder with `out_subdir` = the photo folder's name raises and leaves the source bytes unchanged; the same for input folder = output folder with JPEG sources; a TIFF export next to JPEG sources still works |
| 3 | `adjust.py` (finding the run folder) | The run folder was guessed from "has a `run.json`, else the parent has one": with a missing `run.json` in a nested run, `adjust` re-rendered another run's photo over the export; `--subdir web/2026` was not found at all. | Take `out_subdir` from the sidecar's `audit.run_config` and strip exactly its path parts from the sidecar's folder (compare the parts; when they do not match, use the sidecar's folder). A sidecar without the key (made before F10): the sidecar's folder. No guessing from the parent. | `adjust.rerender` on an image under `out_subdir = "web/2026"` finds its source and stays byte-identical; a run into `out/` plus a second run into `out/b/` whose `run.json` is deleted: `adjust` on `out/b/x.jpg` does not touch it with the other run's photo (it works from the sidecar's own source, or fails with the "pass --source" message) |
| 4 | `segment/__init__.py` (`sam3_ready`, `make_segmenter`) | `sam3_ready` only looked for `torch`, `transformers` and a cached `config.json`. With a Transformers version that has no SAM 3, `--backend auto` chose `sam3` and every photo failed. A cache with the config but without the weights file would start a download. `workflows.py` and `quality_runner.py` hand `"auto"` to `make_segmenter`, which did not know it. | (a) `sam3_ready` also requires `importlib.util.find_spec("transformers.models.sam3")` to be found (inside a `try`: any exception means not ready) and a weights file (`*.safetensors` or `*.bin`) next to the cached `config.json`. Read `segment/sam3.py` for the model id and file layout; do not import torch. (b) `make_segmenter("auto", ...)` resolves through `resolve_backend` itself, so every caller is covered. `quality_runner.py`: where it compares `cfg.backend` with `"sam3"`, compare the resolved name. | `test_defaults.py`: with `find_spec` replaced so that `transformers.models.sam3` is missing, `sam3_ready()` is false and `resolve_backend("auto")` is `"stub"`; a cache folder with `config.json` only is not ready, with a weights file it is (build the fake cache under `tmp_path` and point the environment variable the code reads at it, restored in `finally`); `make_segmenter("auto")` returns the stub when not ready |
| 5 | `cli.py` (`write_look`, `looks save`), `calibrate.py` | `looks save` wrote only the fields that differ from the look defaults. Saved under a packaged look's name, the profile table is merged over the packaged one and the packaged values come back: `looks save natural --set curve_add=0` reported success and changed nothing. | A saved look is written with ALL its fields (every field in `looks.FIELDS`), so the profile table defines the look completely. `calibrate` passes all fields of the fitted look too. | `looks save natural --set curve_add=0` then loading the merged looks gives `curve_add == 0`; `looks save dramatic --from natural` gives a look equal to natural in every field |
| 6 | `paths.py` (`_check_looks`) | A profile look with `curve_add = nan` passed validation and rendered a black JPEG. | A look value in an overlay must be finite (`math.isfinite`); the error names the file, the look and the field. | A profile `looks.toml` with `nan` and one with `inf` raise `ValueError` |
| 7 | `paths.default_profile_dir`, `cli.py` | An empty `AUTOEDIT_PROFILE` switches profiles off for reading, but `looks save` without `--profile` still wrote into the default location, where nothing reads it. The test runners set exactly that value. | `default_profile_dir()` raises `ValueError("profiles are switched off (AUTOEDIT_PROFILE is empty); pass --profile DIR")` when the variable is set and empty. `looks save` and `calibrate` print it and return 2. | With `AUTOEDIT_PROFILE=""` and `XDG_CONFIG_HOME` pointed at `tmp_path`: `looks save x` returns 2 and writes nothing under `tmp_path` |
| 8 | `consistency.find_bursts`, `[consistency]` in `rules.toml` | A burst chains frame to frame with no limit: twelve frames 20 s apart over four minutes became one burst, and a real change of light was flattened. | New key `max_span_s = 120` in `[consistency]` (and in `BURST_DEFAULTS`): a frame that lies more than that after the burst's first frame starts a new burst. | Twelve similar frames 20 s apart give two bursts (seven and five frames), none longer than 120 s from its first frame |
| 9 | `consistency._bursts_plan` | With `--decider llm`, the plan's dry decisions called the LLM, so each burst frame was sent twice and the plan used up the call budget. | The plan decides with the rules only. Find how `Pipeline` holds its deciders; the dry decision of the plan must not reach the LLM decider (for example by swapping in the rules decider for the duration of the plan, restored in `finally`). | A pipeline whose decider is replaced by an object that raises when called: `consistency.plan(..., bursts)` still returns biases |
| 10 | `cli.py` (`load_run_config`), `web/service.py` (`make_cfg`) | Preset order: an `[output]` block with `export = "web"`, `quality = 70` and `subdir = "mine"` in one run file ended with the preset's quality and subfolder. A preset set in a file could not be switched off from the command line. | Order, lowest to highest: packaged defaults, the preset's values, the explicit values of the files, the command-line flags. `--export ""` (an empty name) means no preset. In `make_cfg` an explicit option already wins; keep that. | The run file above gives quality 70 and subfolder `mine` with the preset's long edge; `--export ""` over a file that sets a preset gives the plain defaults |
| 11 | `paths.py` (`merge`, `dump_toml`, merged copies) | An overlay with a date or an array of tables raised `TypeError` with a traceback. The merged copy was rewritten in place, so another thread or a worker could read half a file. | Wrap the merge and the write of one overlay: a `TypeError` becomes a `ValueError` that names the overlay file. Write the merged copy to a temporary file in the same folder and `os.replace` it, inside one module-level `threading.Lock`. | An overlay containing `[[x]]` and one containing a date raise `ValueError` naming the file |
| 12 | `tests/run_all.py`, `tests/conftest.py` | `setdefault` lets a developer's exported `AUTOEDIT_PROFILE` into the tests. | Plain assignment: `os.environ["AUTOEDIT_PROFILE"] = ""`. | none |
| 13 | `contract.py` (`Result.wb_bias`) | A hand-edited sidecar with one value made `adjust` fail with an `IndexError`. | `wb_bias` keeps its list type and gets `min_length=2, max_length=2`. | A `Result` with `wb_bias=[1.0]` is rejected |
| 14 | `tests/test_consistency.py`, `tests/test_defaults.py` | Two tests passed with their feature removed: the burst of the `adjust` test had no white-balance difference, and the `preserve_exposure` test compared looks that differ in other fields. No test ran bursts mode with two workers or through the web job runner. | (a) Give the burst frames different colour casts so that the planned `wb_bias` is not zero, and assert that at least one sidecar has a non-zero `wb_bias` before the re-render check. (b) Build the flagged look from natural's values plus the flag only. (c) Bursts mode with `workers=2` gives the same image bytes as with one worker; one run through `web/jobs` in bursts mode finishes and records a burst group. | the tests themselves; check (a) and (b) by removing the feature in a scratch copy of the tree and running the test function directly (not through pytest, whose config puts the checkout's own `src` first) |

Decisions without a code change:

- **F9, units of the burst level** (`2.2 * log2(fg_p50) + exposure + 0.5 * shadows_ev`): consistent
  to first order with the exposure op; it overstates stops in the deepest shadows and ignores the
  exact weight of the shadow lift. The bias is bounded at 0.5 EV, so this stays.
- **F9, web jobs** hold the inference lock for the whole plan, so a preview waits and a cancel is
  seen only after the plan. Known limit of bursts mode in the web app.
- **F9, a RAW file and its JPEG** have the same time and picture, so they join one burst and
  count twice in its median. Harmless: they should end up alike.
- **F10, WebP** cannot store an image wider than 16,383 px; that photo fails with a clear message.
- **F11, the web app** does not read `defaults.toml` (it never did), so a profile's
  `defaults.toml` has no effect there. Looks, styles and the other files do.
- **F11, temporary folders** are removed at exit; a process that is killed leaves its folder
  (`autoedit-profile-*` in the system's temporary folder).
- **F10/F11, `exports.toml` lookup:** a `--configs-dir` without the file falls back through the
  usual chain (`$AUTOEDIT_CONFIGS_DIR`, `./configs`, the packaged file). The docstring of
  `config_paths` says so now (fix the docstring in this round).

## Review round 2 (final check of the whole run)

A second Checker (Opus, high) reviewed F12 to F17, the fixes of round 1 and the session's own
fixes on the complete tree. Two findings are blocking: both are ways an export can still destroy
a source photo. Each row is one fix with its test.

| # | Where | Finding | Fix | Test |
|---|---|---|---|---|
| 1 | `emit.output_paths`, `batch.run`, `web/jobs.py`, `web/service.py` (`run_one`) | **Blocking.** The check "is the export its own source" compared path strings. (a) On a case-insensitive volume (the macOS default) `IMG_1.jpg` and `IMG_1.JPG` are one file: `autoedit run X X` with camera-style upper-case extensions replaced every source. (b) An export could land on ANOTHER source of the run: with `b.jpg` and `b.png` in one folder, `autoedit run X X --format png` wrote `b.jpg`'s export over the source `b.png`. | New `emit.source_ids(paths)`: the set of `(st_dev, st_ino)` of the run's inputs (a path that cannot be read is skipped). `output_paths(..., sources=None)`: when the chosen target exists and its `(st_dev, st_ino)` is in `sources`, raise `ValueError("... would overwrite a source photo ...")`. With `sources=None` the own source is still checked, with `os.path.samefile` (when the target exists) instead of a string compare. `batch.run`, `jobs._run` and `service.run_one` build the set once from their inputs and pass it. | (a) on a case-insensitive volume (detect it in the test: write `Probe.TXT`, see whether `probe.txt` exists; return early otherwise): a folder with `IMG_1.JPG` run into itself fails that photo and leaves the source bytes unchanged; (b) the `b.jpg` + `b.png` folder with `out_format="png"`: `b.png` keeps its bytes; (c) a TIFF export next to JPEG sources still works; (d) a second run into the same output folder still replaces its own earlier outputs |
| 2 | `batch.run` | `output_paths` was called outside the per-photo failure handling, so a name problem at photo 47 ended the run with a traceback and no `run.json`. | Call `output_paths` inside the per-photo failure handling: the photo is recorded as failed with the message, the batch goes on and `run.json` is written. `cli._cmd_run` also catches a `ValueError` from `batch.run`, prints it and returns 2. | A three-photo run in which one export would land on a source: two photos are processed, one failure is recorded with "overwrite" in its message, `run.json` exists |
| 3 | `emit.output_paths` | `out_subdir` could follow a symlink that already exists inside the output folder and write outside it; the web layer only approves the output folder. | After the folder is chosen and before `makedirs`: when `subdir` is set, every existing part of `os.path.join(out_dir, subdir)` must resolve inside `os.path.realpath(out_dir)` (compare `os.path.realpath(folder)` with the real output folder plus separator); otherwise `ValueError`. | An output folder with a symlink `web` that points to a folder outside: `out_subdir="web"` raises and nothing is written outside (skip on a platform without symlinks) |
| 4 | `contract.py` (`out_name` validator) | The format spec of `{n}` allowed any presentation type: `{n:c}` wrote control characters into file names and a `/` at photo 47. | The spec of `{n}` must match `^(0?[1-9][0-9]?)?d?$` (a width of 1 to 99, optional zero padding, optional `d`). | `{n:c}`, `{n:x}`, `{n:>8}`, `{n:.2f}` are rejected; `{n}`, `{n:4}`, `{n:04d}`, `{n:d}` are accepted |
| 5 | `pipeline.py` (`build`, the translation of instruction clauses), `instructions.py` (`from_translation`), `decide/llm_decider.py` (`_from_pairs` only) | The LLM translation (F17) was given EVERY clause the local parser rejected, including the ones it rejects for safety (negation, exclusion): "don't brighten the face" could come back as "brighten the face". A negative clause index was accepted. A non-finite value from the model became the largest allowed step. The translation ran on every `Pipeline.build`, so a web preview repeated the call. | (a) Only clauses whose problem is a plain "not understood" are sent; a clause rejected as negation or exclusion (see how `instructions.parse` words its problems; add a machine-readable marker to the problem if the wording is all there is) is never sent and stays reported as a problem. (b) `from_translation` drops an item whose clause index is not an int in `0 .. len(clauses) - 1`. (c) `_from_pairs` drops a value that is not finite. (d) Translations are kept in a module-level dict keyed by (model, the exact clause texts), so one process asks once for the same text. | (a) with a stand-in client that answers for every clause it is shown: for `--edit "don't brighten the face, make the sky moodier"` the client is shown only the second clause and no adjustment for the face exists; (b) an item with clause `-1` is dropped; (c) a `nan` and an `inf` value are dropped and the other ops of that item survive; (d) two `Pipeline.build` calls with the same text call the stand-in once |
| 6 | `tests/test_defaults.py` (`test_sam3_needs_the_model_code_and_the_weights`) | The test passed with either check of `sam3_ready` removed: its "no SAM 3 module" case ran while the weights were also missing, and the weights case depended on whether SAM 3 imports on the machine. | Set `sam3._IMPORTABLE = True` for the whole test (restored in `finally`); order: weights present and module missing gives false; module present and config only gives false; both present gives true; then `_IMPORTABLE = False` gives false. | the test itself; check it by removing each of the two checks in a scratch copy |
| 7 | `tests/` (new tests in `test_highlight_hold.py` and `test_denoise.py`) | Two code paths had no test: the highlight hold applied again after a reference match, and the oversaturation gate after a reference match comparing against the denoised source. | One pipeline test each, with the stub backend, a small synthetic photo and a small synthetic reference image (`RunConfig(reference=...)`): (a) with `highlight_hold=True` the blown patch of the source is still white after the reference match, and is darker when the second hold is skipped; (b) with `denoise=True` on a noisy source the colour grain after the reference match is clearly lower than when the gate compares with the plain source. Check both by removing the code in a scratch copy. If a reference match on the stub backend cannot reach these lines, say so in the report instead of forcing a test. | the tests themselves |
| 8 | `consistency.capture_time` (and `captures.py` where it reads metadata the same way) | With the HEIC plugin installed, a HEIC photo still got no capture time (plain Pillow open without registering the plugin), so it could never be in a burst. | Register the plugin the way `imageio.load_image` does before the open, for those extensions only. Add a small public function in `imageio.py` for it if the existing one is private. | With a stand-in registration function (as `tests/test_input_formats.py` does it): `capture_time` on a `.heic` path calls it |
| 9 | `calibrate.py` | `--continue` without `--profile` continued with another profile than the calibration started with, and could write the fitted look into the default profile. A hand-edited `state.json` could name photos outside the photo folder, and a `pairs.json` with an unknown dimension ended in a traceback. `--name natural` replaced a look silently. | (a) `state.json` records a fingerprint of the profile folder (the first 12 hex characters of the SHA-256 of its real path; not the path). `advance` with another resolved profile raises `ValueError` that says to pass the same `--profile`. A state file without the fingerprint (older) is accepted. (b) Every entry of `photos` must be a plain file name (no separator, not `..`); otherwise `ValueError`. (c) An unknown dimension in `pairs.json` is a `ValueError` naming it. (d) A `--name` that is a packaged look is refused at `start` ("choose another name"); when the name already exists in the profile, `finish` prints one line saying it replaced it. | (a) start with profile A, `advance` with profile B raises; (b) a `state.json` with `../x.jpg` raises; (c) a tampered `pairs.json` raises `ValueError`; (d) `start(..., name="natural")` raises |

All nine rows were built by one Builder (Sonnet, high), each with its test, and each test was
shown to fail when its fix is removed in a copy of the tree. Three things came out differently
from the table, and stay that way:

- **Row 1:** in the `b.jpg` + `b.png` folder the photo `b.jpg` fails and `b.png` is exported as
  `b-2.png`. No source is touched.
- **Row 5:** only a clause reported as plainly "not understood" is translated. A clause the
  parser rejected for a stated reason is never sent: negation and exclusion, and also two verbs,
  two amounts, numbers, a missing target. That is stricter than the row asked for and safer;
  `instructions.translatable` is the one place to widen it.
- **Row 9:** a photo entry of the state file may be a relative path made of plain parts
  (`sub/IMG.jpg`), because a calibration scans subfolders; `..`, an absolute path, a backslash
  and a drive are refused.

The session then ran the reviewers' scenarios by hand on the final tree: a folder with
upper-case extensions exported into itself (both photos fail, both sources keep their bytes),
`b.jpg` + `b.png` exported as PNG into their own folder (no source changes), a style name
`../../escaped` in a name pattern (nothing is written outside the output folder), and
`--name "{stem}{n:c}"` (refused, exit code 2).

Decisions without a code change:

- **A JPEG-only shot and a RAW-only shot in the same second** are merged into one shot by the
  burst search (the pairing does not compare file names). They then cannot form a burst with
  each other. Rare, and harmless.
- **The job event stream** stays open while a job is paused, and each open stream uses a server
  thread for a quarter of a second per poll. Fine for a local app with one user; a limit belongs
  to the interface work.
- **The settings schema** reports an exclusive limit (`gt`, `lt`) as `min` or `max`, does not
  carry the rules of `out_subdir`, and lists `look` and `palette` with a default of null that is
  not among their choices. Known limits of the first schema version.
- **An old run file** with `[decode] highlight = "blend"` loads without a message: the key is
  ignored like any unknown key.
- **`autoedit adjust` on a photo decided with the LLM** asks the model again (it needs
  `--allow-cloud`), so a translated instruction can come back differently, like the decision
  itself.
- **A 500 response** of the local web app carries the exception text, which can contain a local
  path. It is sent to the local client only.

## Result

Built on 2026-11-11, in the unattended run. Status: done.

- **Built** by one Builder (Sonnet, medium) from this spec: the profile functions in `paths.py`
  (`profile_dir`, `merge`, `dump_toml`, `read_config`, the merged copies behind `config_paths`),
  `--profile` on the commands, `autoedit looks list | save | delete`, and the profile argument
  through `adjust`, the web service, the job runner and the app.
- **Deviations accepted:** `doctor` takes `--profile` too and lists profile files that are not
  config files; `looks save` accepts a profile folder that does not exist yet and writes only the
  fields that differ from the look defaults; `default_profile_dir()` follows `AUTOEDIT_PROFILE`
  when it is set, so a test or a user who points it at a new folder never writes into the home
  folder.
- **Checked by the session** with a scratch profile: a saved look appears in `looks list` with
  its origin, a run with `--look mine --profile DIR` uses it (contrast 5.34 became 6.94 with
  `contrast_mul = 1.3`), one worker and two workers give identical bytes, `autoedit adjust`
  without the profile warns that the config files differ, a misspelt field ends with the overlay's
  path, the field and the valid fields, and exit code 2. No temporary folder is left behind after
  a good run, a two-worker run or a failing run, and the home folder is never touched.
- **Review** (Checker, Opus, high), fixed in "Review round 1 (F8 to F11)" above: a look saved
  under a packaged look's name lost its changes in the merge (a saved look now carries all its
  fields); a profile look could hold `nan`; an empty `AUTOEDIT_PROFILE` switched reading off but
  `looks save` still wrote into the default folder; an overlay with a date or an array of tables
  ended in a traceback; the merged copy was rewritten in place; the test runners let a
  developer's exported profile in.
- **Known limits, not changed:** the web app does not read `defaults.toml`, so a profile's
  `defaults.toml` has no effect there; a process that is killed leaves its temporary folder.
- **Fixed by the session:** `--profile` is accepted after the `looks` action as well
  (`autoedit looks save NAME --profile DIR`), not only before it.
- **Default output:** byte-identical to the baseline renders with no profile; both test suites
  green on both interpreters.
