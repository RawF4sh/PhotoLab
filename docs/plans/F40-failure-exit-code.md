# F40. An exit code for failed photos

Status: spec (2026-11-18). Size S.

## Goal

`autoedit run` ends with 0 even when photos failed, so a script cannot tell. Give it a way to.

## Design

- `autoedit run ... --strict`: ends with 1 when any photo failed (the `failures` list of
  `batch.run`, `"failed"` in `run.json`), else 0. A bad command line keeps its 2.
- `batch.run` already returns or writes the count; `_cmd_run` (`cli.py`) reads it. If `batch.run`
  returns nothing usable, it returns the summary dict it writes to `run.json`.
- Without `--strict`, nothing changes: 0 as before.

## Tests (`tests/test_cli.py` or a new `tests/test_exit_code.py`)

1. A folder with one good photo and one file that fails to decode: `cli.main(["run", ...,
   "--strict"])` returns 1; without `--strict` returns 0; the good photo is exported in both.
2. A folder of good photos with `--strict`: 0.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Default | Unchanged: 0. A failure code only with `--strict` | make 1 the default in `_cmd_run` |
| Flag name | `--strict`, as on `autoedit check` (F21) | rename |

## Result

Built on 2026-11-18; the Checker found it correct (0 without `--strict`, 1 with it only when a photo failed, 2 for bad input).
