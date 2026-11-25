# 02. Finer landscape scenes, confidence and fallback

Status: ready-for-agent
Blocked by: 01
Spec: [F62](../F62-auto.md), milestone M1
Who: Builder (Sonnet, medium)

## What to build

- `scene.py::classify` adds a finer landscape scene (`sunset`, `twilight`, `daylight`, `mist`)
  with a confidence, from measures the pipeline has: sky coverage, sky brightness and warmth,
  global brightness and contrast, the detected light (`light.py`), `night.py::is_night`, and
  `veil` (`contract.py`).
- All thresholds in a `[scenes]` table in `configs/auto.toml`. No number in Python.
- Below the confidence threshold: the broader `landscape` goal at `fallback_strength`.
- `--scene` passes the existing `override`. The scene and the reason go to `notes`.
- One shoot, one scene: within a `consistency.py::plan_groups` group every photo gets the most
  common scene, unless the group spans a change in time of day.
- `styles.toml [auto] targets` gains the four finer scenes, each pointing at `auto_landscape`
  until the measured tables exist (ticket 13).

## Done when

- A new `tests/test_scene.py` with synthetic stats for each scene, the fallback and the shoot rule.
- A golden test that today's three-way answer is unchanged for Simple.
- `PYTHONPATH=src python tests/run_all.py` is green and the tests stay pytest-collectable.
- `autoedit run ./test-corpus ./out --backend stub` completes, with images byte-identical to the
  previous commit (nothing chooses Auto by default).
- `python scripts/check_public_repo.py` passes. CHANGELOG and docs updated if behaviour changed.
- No git writes: the owner gets a commit block.

## Comments

2026-11-25 — Implemented fine scene labels with confidence, recorded reasons, a manual override, a lower-strength landscape fallback and per-shoot voting across time-of-day transitions. Simple keeps its original three-way decision. Ticket 03 reopened this ticket after 97 of 120 labels matched. Daylight and twilight never crossed; bright overcast scenes remain the main error.

A cloud mask is now measured alongside sky colour in bright overcast photos. The same evidence is used for a released scene and its fallback. In the fixed sample this recovered three additional mist/overcast scenes and called four additional daylight scenes overcast. The retained version recognises 10 of the 20 overcast scenes.
