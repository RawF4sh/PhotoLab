# 01. Auto end to end on provisional goals

Status: ready-for-agent
Spec: [F62](../F62-auto.md), milestone M3
Who: Builder (Sonnet, medium)

## What to build

The thinnest path through the whole of Auto, so every later ticket has something to plug into.

- A style `auto` in `configs/styles.toml` with `targets` per scene instead of one `target`
  (first only `landscape = "auto_landscape"`). The pipeline picks the table for the photo's scene
  (today's `scene.py::classify`) and calls `edit/style_target.py::fit` as for any style.
- A provisional `[auto_landscape]` table in `configs/targets.toml`, `photos = 0`, drawn from the
  owner's three examples (the prototype's fitted results in the private
  `out-webloop/prototype-auto/`, measured through `reference_features`; the script is on branch
  `prototype/auto-goal-fit`) and the F61 twilight originals.
- A new `configs/auto.toml`: per-scene `limits`, `released = false`, `fallback_strength`.
  Limits wide enough for the prototype's amounts (vibrance to 45, whites to −80, curve to 60),
  still inside `OP_BOUNDS`.
- Landscapes under Auto set `preserve_exposure` (ADR 0001) and use the scene's clipping budget
  from `auto.toml`. Fault checks (banding, blown light, skin) stay on.
- A photo of an unreleased scene gets Simple with a note in `notes`; `--auto-unreleased` runs the
  goal anyway. People and other scenes get Simple.
- The sidecar names the scene, the goal table and every control the fit moved.

## Done when

- A test: a synthetic landscape under `--style auto --auto-unreleased` moves toward the goal's
  ranges; without the flag it renders exactly like Simple.
- A sheet of 12 originals (FiveK and the in-repo corpus) with Simple and Auto side by side, in the
  private `out-webloop/` folder: the difference is visible on most photos.
- `PYTHONPATH=src python tests/run_all.py` is green and the tests stay pytest-collectable.
- `autoedit run ./test-corpus ./out --backend stub` completes, with images byte-identical to the
  previous commit (nothing chooses Auto by default).
- `python scripts/check_public_repo.py` passes. CHANGELOG and docs updated if behaviour changed.
- No git writes: the owner gets a commit block.

## Comments
