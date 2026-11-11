# Prompt: make the local AutoEdit editor good, with a screenshot loop

Start a fresh agent session **in this checkout, not a worktree**: the photo corpora are
gitignored and exist only here. Paste everything below the line, or say
"Read docs/IMPROVEMENT_LOOP_PROMPT.md and do it." To keep it iterating, run it under `/loop`.

---

You're improving **AutoEdit's local editor**, the rules pipeline in this repo
(`decode → segment → resolve → stats → scene → decide → apply → emit`). This is not the Gemini
workflow on Photo Lab, which is separate. Here, Gemini outputs are only references.

**Goal:** edits a photographer would keep. Check them by eye every iteration, and stay inside
the existing safety gates. The final call is my blind vote, not your judgment.

Read `CLAUDE.md` first. Its invariants and definition of done apply throughout. Keep the rules
decider (the default); the LLM path is out of scope.

## What's true now (checked 2026-11-09; re-verify before relying on it)

- `PYTHONPATH=src python3 tests/run_all.py` → 440 passed. The stub pipeline runs.
- **SAM 3 doesn't run.** On macOS 27.0.1, the system Python 3.13 (`/usr/local/bin/python3`) has
  scipy 1.15.2. Its `_spropack` extension fails to dlopen (a `__thread_bss` zero-fill error), so
  `autoedit doctor` reports the sam3 backend unavailable. The
  `out-quality-2026-09-29/export-runtime` venv inherits system site-packages and fails the same
  way. SAM worked during the Sep 29 evaluation, so this probably broke with the OS update. The
  default backend (`defaults.toml`) and that evaluation's primary arm are both the heuristic
  stub. Open-vocabulary region editing, the actual product, has barely been judged.
- A 4-photo sheet showed four problems: eval_3way `3D6A1382 1624 2191 1736`, with columns
  original | AE natural | AE cinematic_preserved | Gemini LR | Gemini cinematic.
  - **Flat, hazy output.** Rules lift exposure (+0.2 to +0.8 EV) and shadows (up to +36), while
    contrast stays below 1 on a ±100 scale. The contrast rule maxes at 8 and fades with
    `headroom_ev` (`configs/rules.toml` ~line 120; rationale at lines 42–48). Gemini's
    Lightroom-style edits look deeper and more finished.
  - **Blue drift.** On 1736 the auto palette picked `cool_blue` at 20% ("cool low-chroma
    landscape"), and the result is bluer than the original. "Too blue" is my most frequent
    complaint.
  - **Styles look the same.** `natural` and `cinematic_preserved` can't be told apart at review
    size. Look multipliers act on near-zero baselines: contrast 0.47→0.63, curve 4→8.8, grades ±3.
  - **Stub regions hallucinate.** On the 1736 field (no water, no person), it brightened "water"
    0.25→0.35 and "subject" 0.34→0.47. Mountain, snow and grass came back "not detected".
- From `docs/QUALITY_EVALUATION_2026-09-29.md`: the safety wrapper blended back 203 of 532 holdout
  proposals. Native proposals raised 148 mask-boundary flags and 93 texture flags. Snow loses
  texture at global finishing (`0080_snow`). No human votes have ever been collected.
- **My taste, in my words:** before judging anything, read every non-empty `user_notes` in
  `eval_3way/_review/codex_user_comparison.csv`. In short: I want the old-cinematic mood with
  new-Lightroom preservation. I dislike images that are too blue or too saturated, added
  sharpness, fake or changed content, and treating the subject and background inconsistently.

## Ground rules

- The working tree has a large uncommitted change set (the Sep 29 quality work). If
  `git status` still shows it, ask me whether to commit it before your first edit. Never reset,
  checkout, or stash files you didn't change. Don't commit. At the end, give me a copy-paste
  commit + push block.
- **Experiments live in copied config dirs.** Snapshot `src/autoedit/configs/` as
  `out-loop/iter-00/configs/`; that's the starting baseline. Each candidate copies the current
  best dir, adds one change, and runs with `--configs-dir`. A rejected idea never touches
  `src/`.
- Change code only when TOML can't express the idea (a new stat, palette-selection logic, mask
  feathering). Put new code behind a TOML key so the baseline dir keeps the old behavior and
  A/B still works. Each code change needs a test, per `CLAUDE.md`.
- Reuse what exists: `scripts/contact_sheet.py`, `tone_report.py`, `blindtest.py` (its
  `--configs-a/--configs-b` A/B mode shares one SAM pass between arms), `quality_workflow.py`,
  and Studio (`autoedit ui`). `scripts/eval_3way_style_loop.py` calls itself "the screenshot
  loop", but it applies a fixed `apply_ops` recipe and bypasses the pipeline, so it can't
  measure the product. Extend `contact_sheet.py` rather than building a framework.
- Gemini outputs are references for tone, color and depth only. Gemini sometimes invents
  content (clouds, a different bear, a whale fin). AutoEdit must never change content.
- Never loosen `configs/quality.toml` thresholds to make something pass.

## Phase 0: make it work

1. **Fix SAM 3 without touching system Python.** The frozen Sep 29 runtimes inherit system
   packages, so a global upgrade would change their locked dependencies. Create `.venv`
   (gitignored, no system site-packages) and run `pip install -e '.[sam,accel]'`. Make sure
   `import scipy.sparse.linalg` works. Use the cached `facebook/sam3` weights; if they're
   missing, stop and tell me. Done when `autoedit doctor` reports sam3 OK on `mps` and one
   photo run with `--backend sam3` writes a sidecar with real detected concepts.
2. **Stub smoke still passes:** `autoedit run ./test-corpus ./out-loop/smoke --backend stub`.
3. **Studio works end to end.** Run `autoedit ui --no-open` (port 8765) and open it in your
   browser tool. (In Claude Code desktop, add a `studio` entry to `.claude/launch.json`.)
   Process one photo with `--backend sam3`, then screenshot the before/after split, the mask
   overlay and the "why" list. Fix anything broken before tuning.

## Phase 1: build the screenshot loop (keep it small)

- **Tuning panel, frozen once:** `out-loop/panel.txt`, about 16 photos:
  - eval_3way originals that have my notes, at least 3D6A1382, 1613, 1624, 1639, 1736, 2036,
    2191 and 2299
  - 4 RAWs from `curated-dolomites-summer-2026/selected_raw`: dark, `0080_snow`, bright,
    blue sky
  - 2–3 pairs from `split_before_after_landscapes`, where the human "after" is the reference
- **Holdout:** the independent Switzerland event, through the existing manifest
  `out-quality-2026-09-29/holdout-sources.json` (split `holdout`). Don't look at it until the end.
- **Sheets:** extend `contact_sheet.py`. Each row is a panel photo; the columns are
  Original | AE A | AE B | Reference (Gemini LR, the human "after", or the camera preview).
  - Render through the real `Pipeline` with `--backend sam3`, from two config dirs: the current
    best and the candidate.
  - **Randomize which side gets A and B in each row.** Write the key to a separate JSON and read
    it only after recording your verdicts.
  - Captions: top settings, clip %, foreground change, dark→darker flag.
  - At most 4 rows per sheet image, so detail survives the image viewer's downscaling.
- **Crops sheet:** 100% crops of the risky areas: snow and highlights, mask edges, skin, smooth
  sky. Thumbnails hide halos, banding and texture loss.

## Phase 2: the loop

Each iteration:

1. **Hypothesis:** one change, in one sentence, tied to something you saw on a sheet.
2. **Change:** in `out-loop/iter-NN/configs/`, or code + test behind a TOML key.
3. **Render:** the panel sheets and the crops sheet, current best vs. candidate.
4. **Look:** open every sheet image. For each photo, write Better / Same / Worse for the
   candidate vs. the current best, with a one-line reason in rubric terms. Then read the key.
5. **Gates:** `tone_report.py --render --fail-on-regression` on the panel must show
   0 dark→darker and 0 pushed into clipping.
6. **Keep** only if Better > Worse, no photo is clearly worse, and the gates pass. Otherwise,
   discard the iteration dir.
7. **Log** a row in `out-loop/LEDGER.md`: iteration, hypothesis, change, B/S/W counts, gates,
   kept or not, and sheet paths.
8. **After every 3 kept changes:**
   - Promote them into `src/autoedit/configs/` and run the full tests.
   - Run the definition-of-done tone report on the 110 Dolomites RAWs.
   - Re-check 2 photos in Studio, with screenshots.

Stop after 3 iterations in a row with nothing kept, after 12 iterations total, or when a
decision is mine to make. To resume (in a new session or under `/loop`), read `LEDGER.md` and
continue from the last row. Don't start over.

## Rubric (in order: a failure higher up outweighs any win lower down)

1. **Preservation:** no content change, no halos at mask edges, no sky banding, snow and cloud
   texture intact, natural skin.
2. **Tone:** the subject is readable. Dark scenes are lifted without going grey. Real blacks and
   depth. Highlights are neither clipped nor flattened into a plateau.
3. **Color:** neutral whites. Never bluer than the original unless the scene really is blue. No
   neon greens, no oversaturation.
4. **Finish:** reads as a deliberate edit (depth, separation), not a filter. Restraint beats
   drama.
5. **Style:** a requested style looks visibly different from `natural`.

## Starting hypotheses (verify each on a sheet before acting)

1. **Flatness:** contrast, curve and blacks don't balance the exposure and shadow lift, and
   contrast fades exactly where exposure spends headroom.
2. **Blue drift:** the auto `cool_blue` palette plus temp on cool scenes. Try neutral or off for
   cool low-chroma scenes, or a warm-neutral bias.
3. **Look-alike styles:** multipliers on near-zero baselines. Make `cinematic_preserved` actually
   hit "old-cinematic mood, new-Lightroom preservation".
4. **Real SAM:** do regional edits help, or do they create mask-edge halos (the 148 boundary
   flags)?
5. **Snow:** texture loss at global finishing (`0080_snow`).
6. **Sharpening:** the default sharpen is about 11. Check the crops for crunch.

## Finish with

- **Holdout check:** one holdout sheet comparing the final config with `iter-00`. Report it
  honestly, even if the result is worse.
- **Blind gallery for my vote.** Build it with `blindtest.py out-loop/blind --manifest
  out-quality-2026-09-29/holdout-sources.json --split holdout --configs-a out-loop/iter-00/configs
  --configs-b <final> --backend sam3 --stratify 8`. Give me the `index.html` path and the
  `--score` command. Your verdicts are a proxy for my taste; my vote decides what ships.
- **Summary:**
  - what changed
  - what got better or worse, with sheet paths
  - what's unresolved
  - what needs my decision
  - the commit + push block (not run)
