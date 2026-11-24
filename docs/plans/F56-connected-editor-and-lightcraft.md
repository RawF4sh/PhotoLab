# F56. Editable changes, suggestions you can adjust, and recipes

Status: M1, M2, and M3 implemented (2026-11-23). The optional L0 measurement awaits the owner-installed tool. The first, larger version
of this plan is kept in [F56-full-vision.md](F56-full-vision.md); it is the long-term picture,
not the build order.

## Implementation record

The Photo screen now keeps each accepted change through undo, restart, single export and batch
export. The Recipes sheet uses the saved effective adjustments, can route ticked photos by
ordered conditions, and reports unresolved targets. Each planned recipe keeps the previous
look/strength and any separately applied copy of the same recipe. Replanning can therefore
replace or clear its previous assignment without removing independent photo edits.

- Chrome exercised selecting two of three suggestions, changing the amount, toggling, removing,
  undoing, reopening, and saving. The lossless export matched the equivalent manual adjustment.
- A second browser run created, imported and exported recipes, assigned two recipes and one
  fallback across three photos, changed their priority, reopened the project, and exported the
  three files. The export sidecars identify the active changes and their contributions.
- In Chrome, disabling a recipe cleared its missing-object warning while keeping a measured
  shadow-clipping warning. Undo restored the missing-object warning. Both survived a restart.
- The two default stub corpus outputs matched the JPEGs from the previous commit byte for byte.
  The 1,000-photo project fixture with three undo snapshots per photo occupied about 1.1 MiB.
- The repository test runner passed at each milestone, with 1,241 tests at M1, 1,245 at M2 and
  1,251 at M3. The publication check passed for each milestone and the final documentation.
- Gemini tests used a fake provider. The owner's five-photo live assessment and the three
  LightCraft measurements remain open. See the [LightCraft check](F56-lightcraft-check.md).

### Independent check (2026-11-23)

A second session read the whole diff against this plan, ran the acceptance checks again and
went through the demo in a browser with a stand-in for Gemini. M1, M2 and M3 do what the plan
says. Three gaps were found and fixed:

- **Recipe export in the desktop window.** Export is a file the page makes, and the desktop
  window blocked such downloads. `web/desktop.py` now allows them. A unit test covers the
  setting; the button has not yet been clicked in the real window.
- **Clearing a folder plan.** With every recipe removed from the order, **Plan** was offered,
  but the tool refused the empty list. An empty order now clears the earlier assignments, with
  no measuring.
- **Suggestions that repeat the request.** An answer whose label was the request itself was
  refused as invalid, which a short request would often cause. Such a label is now named from
  its own controls ("Whole photo: exposure"), and a reason that repeats the request is dropped.
  The request text is still never stored, and an answer that contains the key is still refused.

What was checked:

- All 1,251 tests passed before the fixes. After them the full suite passed again with 1,254
  tests; the three extra tests belong to later editor work that was being added at the same time.
- The two default stub outputs are byte-identical to the previous commit, and the publication
  check passes.
- In the browser: three suggested changes, two applied, one set to 50 %, one switched off. The
  picture equals the same value typed into the slider, and the list is the same after a reload.
  Two recipes planned over three photos gave one match each, one photo that matched none and
  kept its own changes, and one missing object with a review mark. The batch sidecars name each
  entry. Neither the key nor the request text is anywhere in the profile.

Still open, and both are the owner's steps: the five-photo live Gemini session, and L0, which
needs Rust or a LightCraft release on the machine.

## Goal

1. **Changes you can see and switch.** A photo's edit is the sliders plus a list of named
   changes. Each change can be switched off, toned down or removed, and all of it survives a
   restart.
2. **Suggestions as separate changes.** A Gemini suggestion arrives as a few named changes
   ("Lift the foreground", "Calm the sky"). You accept the ones you want. Reopening and
   exporting never calls the model again.
3. **Recipes.** A saved edit that works on other photos: look, strength, whole-photo
   adjustments and per-object adjustments. A recipe can have a condition, and a folder can use
   several recipes, each on the photos it fits.

The demo that proves it: ask for a suggestion, apply two of three changes, tone one down, switch
one off, restart, export. Save the result as a recipe, plan two recipes over a folder, fix one
photo by hand, run the batch.

## Why this is smaller than the first version

Most of what the first version wanted to build already exists in a simpler form.

| The first version wanted | What the code already has | So this plan does |
|---|---|---|
| A new edit document, per-photo files, an asset store, migration | A project file with atomic saves, per-photo undo and redo (`web/projects.py`, `app/project.js`) | Adds one optional key to it. No migration |
| Suggested steps you can edit one by one | A Gemini suggestion that is added into the sliders and then forgotten (`photo_tools.js`, `applyProposal`) | Keeps each suggested change as its own entry |
| A new recipe system with a rule builder | Presets (look, strength, sliders), rules that already adapt to each photo, per-photo options in batch jobs | Grows presets into recipes |
| A batch planner, run plans, an exception queue | Jobs that take different settings per photo, a "Needs review" filter with reasons | Adds one "Plan" step for recipes |
| A Learn area | "How it was done" and "Try it yourself" on the Photo screen | Nothing new |
| Five top-level areas | Start, Folder, Photo, Tools, Settings ([UI-minimal.md](UI-minimal.md)) | Uses the existing screens |

No task below touches a protected file (`pipeline.py`, `edit/ops.py`, the gates, `quality.toml`,
`llm_decider.py`), and no default pixel changes.

## Decisions (owner, 2026-11-23)

| Decision | Answer |
|---|---|
| LightCraft | A one-day check that blocks nothing (see L0) |
| Layout | Keep the minimal layout of F49 ([UI-minimal.md](UI-minimal.md)): no new top-level areas |
| Scope | The three milestones below, in order. Everything else is in "Left out" with its trigger |

## Design

### M1. The Changes list

A photo's saved record gets one optional key, `edit_steps`: a list of at most 24 entries.

| Field | Meaning | Limit |
|---|---|---|
| `id` | Unique in the photo | `[a-z0-9-]`, 1 to 80 characters |
| `label` | What the user reads | 1 to 60 characters |
| `origin` | `suggested` or `recipe` | fixed list |
| `on` | Switched on or off | true or false |
| `amount` | Scales this entry's values | 0 to 2 |
| `global` | Whole-photo offsets | the slider ops and ranges of `lessons.toml [try.sliders]` |
| `regions` | Offsets per object name | at most 32 names, checked like `region_settings` |
| `note` | Why (the checked reason) | up to 240 characters, optional |
| `ref` | Model name or recipe id | up to 100 characters, optional |

The rule, per target and op: **total = slider + the sum of (amount × value) over entries that
are on, limited to the slider's range.** A suggestion of +0.4 EV and a slider at −0.1 EV give
+0.3 EV; switching the suggestion off leaves −0.1 EV.

Two properties are tested:

- A photo whose entries add up to X renders exactly like the same X typed into the sliders.
- A photo with no entries renders byte-identically to today.

How it is built:

- New `src/autoedit/web/edit_steps.py` with two pure functions: `check(value, table)` and
  `fold(opts, table)`. `fold` returns the options with `photo_settings` and `region_settings`
  replaced by the totals and `edit_steps` removed, plus a report of every total that hit its
  limit.
- `fold` is called once at each entry point, before anything reads the offsets:
  `service.preview`, `service.detail`, `service.make_variants`, `service.run_one`,
  `assist.suggest`, and `jobs.start` (job options and each photo's options in
  `_check_photo_options`). Everything after that sees the two dictionaries it sees today.
- `web/projects.py`: `_PHOTO` gains `edit_steps`, checked by `edit_steps.check`. Undo and redo
  already snapshot the whole photo record, so they cover the list with no new code.
- The preview answer keeps `editor.sliders[].value` as the slider's own value and adds
  `editor.totals` and `editor.clamped`.
- The sidecar names each entry that was on, with its share, so "why" stays answerable (hint:
  `Adjustment.source`, `contract.py`).
- Interface, on the Photo screen: a "Changes" list in a new `app/edit_steps.js`. Per entry: a
  tick box, the label, "Suggested" or "Recipe", an amount slider, the note, Remove. One line
  when a total is at its limit, and "not found in this photo" for an object that is missing.
  Nothing is drawn while the list is empty.
- Small edits: `project.js` (`PHOTO_KEYS`), `photo.js` (`previewBody`, `resetPhoto`,
  `rememberCopied`), `folder.js` (forward `edit_steps` in `photo_options`).

Known ceiling: the project file is capped at 8 MB and every undo snapshot copies a photo's whole
record. M1 adds a test that builds a 1,000-photo project with typical edits and fails above
4 MB. If it fails, the first fix is a shorter history for photos that are not open.

### M2. Suggestions as separate changes

The suggestions themselves were built in [F55](F55-guided-photo-workflow.md). This milestone
changes their shape and what happens when they are applied.

- `decide/gemini.py`: the answer becomes `{"changes": [{"label", "why", "target", "ops"}],
  "explanation"}`. At most 6 changes; `target` is `"photo"` or one of the object names sent;
  `ops` are checked as today (known op, in range, zeros dropped). The prompt asks for one change
  per intent. An answer in any other shape is refused and the edit stays as it was.
- `web/assist.py`: the answer gains an `id`. The "current controls" sent to the model are the
  folded totals, which M1 already provides.
- `photo_tools.js`: each change is a row with a tick box. "Apply selected" appends entries with
  the ids `<id>-<n>`, so applying twice cannot double. Sliders are no longer overwritten. The
  existing "your edit changed, ask again" check stays.
- What leaves the computer does not change: the 512-pixel thumbnail, the request text and the
  listed numbers. The key, the request text and the raw answer are never written to the
  project, a log or a sidecar.

### M3. Recipes

Recipes are the existing presets (`web/edit_presets.py`, `app/presets.js`) with more in them.
Existing presets stay valid. In code they keep the name `edit_presets`, because
`src/autoedit/recipe.py` is already the learned predictor of F27.

**M3a. Recipes with object settings**

- A recipe stores look, strength, whole-photo totals and per-object totals by object name.
  It never stores masks, brush strokes, crops, marks, a reference photo or hide settings.
- Applying a recipe to a photo is one undoable step: it sets look and strength if the recipe has
  them, and adds one Changes entry with the id `recipe-<id>`. Applying it again replaces that
  entry. The entry is a copy, so changing the recipe later leaves earlier photos alone.
- Objects are found again on each photo. If one is missing, that part is skipped and reported
  (the engine's existing `resolved_only` behaviour).
- Folder screen: "Apply recipe to ticked photos". Tiles show the recipe's name. The normal run
  renders them, because jobs already take different options per photo.
- One recipe exports to, and imports from, a small JSON file holding its name and settings only.

**M3b. Conditions and the folder plan**

- A recipe may have a condition: up to three tests of the form "measure is above/below a
  number", and an optional scene (people, landscape, other). The measures are a short list in a
  new `configs/recipes.toml`, each naming a field of `GlobalStatsModel` (for example `mean_lum`,
  `lum_p10`, `highlight_clip`, `sky_cov`, `snow_cov`, `iso`) with a label and a range. No
  free-form expression is read from a recipe.
- A folder keeps an ordered list of recipes (`folder.recipes` in the project, at most 20). The
  first recipe whose condition holds is used; otherwise the folder's own settings apply.
- New tool `plan-recipes` in `web/tools_jobs.py`: it runs the analysis half of the pipeline
  (`process_one(do_apply=False)`) per photo and returns the scene, the measured values, the
  objects found, the matching recipe and any missing object. It reuses the run's mask cache, and
  one bad photo does not stop the rest.
- Folder screen: "Plan" shows how many photos each recipe gets, how many match none, and how
  many miss an object. "Apply plan" writes the entries. Photos with a missing object get the
  existing review mark with the reason.
- A condition only chooses which recipe applies. Adapting the edit to each photo stays the job
  of `rules.toml`, and a recipe's values sit on top of that.

### L0. LightCraft check (one day, any time)

LightCraft is a separate open-source photo editor written in Rust
(https://github.com/storytold/lightcraft). Owner step first: install Rust, or download a
LightCraft release yourself. Neither is on the owner's machine, and the check installs nothing
on its own.

On copies of five corpus photos, with a pinned LightCraft commit, answer three questions:

1. Does it open this camera's RAW files, and how do its default renders compare with AutoEdit's
   decode in brightness, clipping and seconds per photo?
2. Can it apply a named edit to a TIFF we hand it and write a 16-bit result, with no window?
3. If AutoEdit's result is opened in LightCraft, finished by hand and exported, what is lost?

Result: one page, `docs/plans/F56-lightcraft-check.md`, ending in one of three outcomes: nothing;
an "Open in LightCraft" hand-off as its own small feature; or one named component worth its own
spec. No LightCraft code enters `src/`. Its NOTICE file lists parts with other licences
(a segmentation component, model weights, branding), so those are noted too.

## Tasks

Tiers as in [AGENT_ROUTING.md](../AGENT_ROUTING.md). Two Builders run together only where their
files do not overlap, which is true for each Python and interface pair below.

| Task | Tier | What | Proof |
|---|---|---|---|
| M1-T1 | Builder (Sonnet, medium) | `edit_steps.py`, the `fold` calls, the project check, `tests/test_edit_steps.py`, the size test | the new tests, then `PYTHONPATH=src python tests/run_all.py` |
| M1-T2 | Builder (Sonnet, medium) | The Changes list and the four small interface edits | browser check on the smoke corpus: toggle, tone down, remove, undo, restart |
| M1-T3 | Checker (Opus, high) | Review: the two tested properties, save and undo, the sidecar note | findings as `file:line` |
| M2-T1 | Builder (Sonnet, medium) | `gemini.py`, `assist.py`, `tests/test_gemini_assist.py` with the fake provider | the tests; `tests/test_api_contract.py` |
| M2-T2 | Builder (Sonnet, medium) | The suggestion rows and "Apply selected" | browser check with the fake provider |
| M2-T3 | Checker (Opus, high) | Review: what leaves the computer, nothing secret stored, bad answers leave the edit alone | findings as `file:line` |
| M3a-T1 | Builder (Sonnet, medium) | Recipe fields, export and import, `tests/test_edit_presets.py` | the tests |
| M3a-T2 | Builder (Sonnet, medium) | The Recipes sheet, apply to one photo and to ticked photos | browser check |
| M3b-T1 | Builder (Sonnet, medium) | `recipes.toml`, conditions, matching, the `plan-recipes` tool, tests | the tests; `tests/test_tools_api.py` |
| M3b-T2 | Builder (Sonnet, medium) | The folder's recipe list, Plan and Apply plan | browser check with two recipes |
| M3-T3 | Checker (Opus, high) | Review: exported file contents, the plan tool's failure handling, re-apply does not double | findings as `file:line` |
| L0 | Builder (Sonnet, medium), then the session writes the outcome | The three questions | the one-page result |
| Docs | Tweaker (Haiku, low), per milestone | CHANGELOG, the FEATURE_PLAN row, `APP_GUIDE.md` section 9 | none |

Order: M1, M2, M3a, M3b. L0 fits anywhere. Each milestone ends with a commit block for the owner.

## Acceptance

For every milestone, from the repository root:

- `PYTHONPATH=src python tests/run_all.py` is green, and the tests stay pytest-collectable.
- `autoedit run ./test-corpus ./out --backend stub` completes with sidecars and a manifest, and
  its images are byte-identical to the same run on the previous commit.
- `python scripts/check_public_repo.py` passes.
- No tone report is needed: nothing in exposure, tone or decode changes. A task that seems to
  need a protected file stops and reports.

Per milestone:

- **M1:** entries can be switched, toned down, removed and undone; the list and the picture are
  the same after a restart; the two tested properties hold; the sidecar names the entries.
- **M2:** the demo in Goal, with no model call after the first answer. Missing consent, a
  missing key, a bad answer and a used-up budget each leave the edit unchanged. No key in any
  project, log or sidecar.
- **M3:** two groups of photos get different recipes; a photo that matches none keeps the
  folder's settings; a missing object is skipped and reported; every applied recipe is an entry
  that can be changed per photo; an exported recipe holds no path, key, mask or EXIF.

## Owner steps and proposed wording

| Item | Proposed |
|---|---|
| The list's name on the Photo screen | "Changes" |
| "My presets" | Renamed "Recipes"; existing presets carry over |
| Labels for where an entry came from | "Suggested" and "Recipe: NAME" (no "AI", as in F49) |
| The request text you type | Not stored |
| A live Gemini session with your key (M2) | Five photos, to judge whether the split into changes is sensible. Tests use a fake provider |
| Rust for L0 | You install it when you want the check |

The open votes (F50, F52, `natural_clean`) and the F53 labels do not block this work.

## Left out, and what would bring each back

| Left out | Bring it back when |
|---|---|
| Five top-level areas | Not planned (owner, 2026-11-23) |
| More sliders (whites, blacks, vibrance, clarity, dehaze) | You miss one. Each is a line in `lessons.toml [try.sliders]` plus its lesson text |
| Histogram, point curves, a colour mixer | You ask after using M1 to M3, or L0 says LightCraft should do them. New op math needs a Planner spec, math tests and the tone gate |
| Per-photo files, an asset store, a new document format | The M1 size test fails after history trimming |
| A check against two windows saving the same folder | You use the desktop window and a browser tab on one folder |
| Batch jobs that survive a restart of the app | A long batch from the interface is lost to a restart (`autoedit run` already resumes) |
| Conditions on a single object inside a recipe | A recipe needs more than "found" or "not found" |
| Keeping a "Try it yourself" result as a Changes entry | You want it; it is a small follow-up to M1 |
| Engine fields in saved edits, a second renderer | L0 ends in adopting a LightCraft component |
| A hosted editor, accounts, payments | Unchanged: separate future projects |
