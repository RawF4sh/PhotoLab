# F19. Follow-up edits

Status: spec (2026-11-15). Size M. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F19.
Builds on [F2](F2-region-instructions.md) (instructions), [F3](F3-per-photo-adjustments.md)
(per-photo adjustments) and [F18](F18-shaped-light.md) (places as targets).

Written on Opus at the owner's request, because the Planner tier (Fable) had reached its usage
limit (see `docs/AGENT_ROUTING.md`).

## Goal

After the editor has done its edit, the user types what else should change ("a bit warmer",
"darken the top", "brighten the face") and sees it applied on top of the edit at once. They can
keep adding, undo the last change, remove any change, or start over. The batch export uses
each photo's changes.

Studio today sends no instruction at all (the `edit` setting exists only on the command line).

## How it works

A follow-up is an F2 instruction clause. The photo's follow-ups are a list of clauses; the
preview is rendered with all of them, joined, as `edit`. F2 already sums the deltas of several
clauses on one target and clamps the sum to `[limits]`, so "a bit warmer" twice is twice as warm
up to the limit. The base edit (the rules) is deterministic, so "on top of what was done" is
exactly: the same decision plus the listed clauses.

No chat and no model call by default. A clause the local parser does not understand is shown as
not understood and changes nothing; with the LLM decider on (F17) it is translated as any
`--edit` clause is.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Where the follow-ups of a photo live in Studio | In the page, per photo, and in the browser's local storage keyed by the photo's path (so a reload keeps them). Wrapped in try/catch: without storage they last for the session | `STATE.followups` in `studio.html` |
| An instruction for every photo | A second box, "For every photo", sends `edit` for the whole run; the per-photo list adds to it | Nothing to reverse |
| Undo | Removes the last clause of the photo; each clause also has a remove button; "Start over" clears the photo's list | Nothing to reverse |
| What the batch does | The job request carries `photo_edits: {path: "clause. clause"}`; each photo gets its clauses as photo-scope adjustments (F3), so the sidecar records them and `autoedit adjust` can change them later | Nothing to reverse |
| A clause that is not understood | Kept in the list, marked "not understood" with the parser's reason, never applied; the user can remove it | Nothing to reverse |

## Studio (`web/static/studio.html`)

Below the preview, a bar with:

- a text field "Change something… (e.g. a bit warmer, darken the top, brighten the face)" and an
  **Apply** button (Enter applies);
- the photo's list of clauses as chips, oldest first, each with a remove (×) button and, when the
  last preview reported it, a "not understood" mark with the reason in its title;
- **Undo** (removes the last chip) and **Start over** (clears the list), both disabled when the
  list is empty.

Applying or removing re-renders the preview at once (the segmentation of the photo is cached,
so a re-preview is quick). While it renders, the field stays usable; a newer request wins (the
page already keeps a preview token).

In the right panel, a section **Instructions for every photo** with a text field; its text is
sent as `edit` with every preview and with the batch. Changing it marks the preview stale, as
the other settings do.

The "What it did" window already lists `Result.adjustments` with their source clause and status;
nothing to change there beyond labelling run-wide versus this-photo clauses ("every photo" /
"this photo", from `Adjustment.scope`).

## Server

- `service.preview(..., opts)`: `opts` may carry `edit` (run-wide) and `photo_edit` (this photo's
  clauses). `edit` is already allowed. `photo_edit` is parsed with `instructions.parse` against
  the pipeline's vocabulary and known concepts and passed to `process_one(adjustments=...)` with
  `scope = "photo"`. The response gains `instruction_problems`: the problem lines of both texts,
  so the page can mark the chips.
- `jobs.start(..., photo_edits=None)` and `POST /api/jobs` (and `/api/v1/jobs`): a map from a
  photo path to its text. Every path must be one of the job's photos (anything else is a
  `ValueError` → 400). `_run` parses each photo's text once and passes the adjustments as above.
- `service.run_one` takes `photo_edit` the same way.
- Length: one photo's text at most 2,000 characters, the map at most 10,000 entries (400
  otherwise).
- The route snapshot `tests/snapshots/api_v1.json` changes only if a route or its parameters
  change; regenerate it on purpose if so.

## Files

| File | Change |
|---|---|
| `src/autoedit/web/static/studio.html` | the follow-up bar, the run-wide field, local storage, `edit`/`photo_edit` in the requests, chips from `instruction_problems` |
| `src/autoedit/web/service.py` | `photo_edit` in `preview` and `run_one`, `instruction_problems` |
| `src/autoedit/web/jobs.py` | `photo_edits` |
| `src/autoedit/web/app.py` | pass `photo_edit` / `photo_edits` through |
| `tests/test_follow_up.py` | new |

## Task

One Builder (Sonnet, medium). It does not touch the files of [F18](F18-shaped-light.md) and can
run at the same time as F18's first task. Proving command:
`PYTHONPATH=src python3 -m pytest tests/test_follow_up.py tests/test_web_service.py tests/test_jobs.py tests/test_api_contract.py tests/test_security.py -q`,
then `PYTHONPATH=src python3 tests/run_all.py`, then `PYTHONPATH=src .venv/bin/python tests/run_all.py`.

## Tests: `tests/test_follow_up.py`

| # | Test | Asserts |
|---|---|---|
| 1 | `test_preview_applies_a_follow_up` | `service.preview` on a test photo with `photo_edit = "brighten the photo"` is brighter than without; the result's adjustments carry the clause with scope `photo` and status `applied` |
| 2 | `test_follow_ups_add_up` | `"brighten the photo. brighten the photo"` is brighter than one clause; a long run of the same clause stops at the limit (status `clamped`) |
| 3 | `test_run_wide_and_photo_edits_combine` | `edit = "warm the photo"` with `photo_edit = "brighten the photo"`: both applied, scopes `run` and `photo` |
| 4 | `test_not_understood_is_reported` | `photo_edit = "make it pop like a movie"`: `instruction_problems` names the clause; the pixels equal the preview without it |
| 5 | `test_batch_uses_photo_edits` | A job on two photos with `photo_edits` for one of them: that photo's sidecar has the photo-scope adjustment and its image differs from a run without; the other photo is byte-identical to a run without |
| 6 | `test_photo_edits_are_validated` | A path that is not in the job, a text over 2,000 characters, a non-string value: 400 through the API (and `ValueError` from `jobs.start`) |
| 7 | `test_adjust_keeps_the_follow_ups` | After a job with a follow-up, `adjust.rerender(<image>)` without changes is byte-identical (the photo-scope adjustment is replayed from the sidecar) |

## Acceptance checks (the session runs these)

1. Both suites green; default renders byte-identical (no follow-up, no run-wide text).
2. In Studio, in the browser, on a real photo: type three follow-ups one after another, see each
   applied; undo one; remove one from the middle; reload the page and see the list kept; one
   not-understood clause marked; run a batch of two photos and check the sidecar of the photo
   with follow-ups.
3. With F18 built: "darken the top with a gradient" and "add a warm glow where the sun is" as
   follow-ups.

## Result

Built on 2026-11-15. Status: done.

- **Built** by one Builder (Sonnet, medium), at the same time as F18's first task.
- **Checked by the session in the browser:** typed three follow-ups into the field (a glow, a
  gradient, and "make it pop like a movie"); each re-rendered the preview; the third was marked
  "not understood" on its chip and changed nothing; Undo removed the last one, × removed one from
  the middle; after a page reload the list of the photo was still there; the "What it did"
  window lists each follow-up as "this photo".
- **Deviation accepted:** a follow-up is never sent to the LLM translation (only the run-wide
  instruction can be), so a follow-up never leaves the machine.
- **Default output:** byte-identical; no request changes when the fields are empty.
