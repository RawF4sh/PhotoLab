# F20. Free-text instructions: more words, and a small local model

Status: spec (2026-11-15). Size M. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F20.
Builds on [F2](F2-region-instructions.md), [F17](F17-llm-path-refresh.md),
[F18](F18-shaped-light.md) and [F19](F19-follow-up-edits.md).

Written on Opus at the owner's request (the Planner tier, Fable, was at its usage limit).

## Goal

The owner typed "make the sky have a bigger gradient for pink" and got "not understood". The
reader of instructions is a word list (`configs/instructions.toml`): ten verbs (brighten, darken,
warm, cool, saturate, mute, soften, deepen, glow, vignette), no colour but warm and cool, and no
way to say how big a shape is. This feature:

1. **Grows the word list:** colour verbs (pink, purple, golden, teal, green, blue) and size words
   for places ("a bigger gradient", "a smaller vignette", "a wider glow").
2. **Adds a small model on this computer** that reads what the word list did not understand and
   fills in the same bounded form: target, ops, amounts, size. It runs through Ollama, which is
   already installed on the owner's Mac with `qwen3.5:9b` pulled (6.6 GB; an M1 Max with 64 GB).
   No text leaves the machine.

Common phrasings stay instant and need no model; the model is the fallback.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Is the local model on by default? | `translator = "auto"`: used when Ollama answers at `local_url` and the model is installed; otherwise nothing changes (the clause stays "not understood"). It only ever runs on text the user typed and the word list missed, so a run without instructions is untouched | `[run] translator = "off"` in `defaults.toml` |
| Which model | `qwen3.5:9b`, the model already on the machine. Chosen by measurement only among installed models; no download in this feature | `local_model` |
| Cloud versus local | Unchanged: with `--decider llm --allow-cloud` and a key, the cloud model translates (F17). Otherwise the local model does | Nothing to reverse |
| Follow-ups ("Edit this version") | Translated by the local model too. Never by the cloud model (F19 kept follow-ups on the machine) | `translator = "off"` |
| What the model may return | Exactly F17's form plus `size`: a clause number, a target (an object name or one of the places), ops from the instruction limits, values, a size factor. Everything is validated and clamped by `instructions.from_translation` and `[limits]`; a clause rejected for a reason (negation, exclusion, ...) is never sent | Nothing to reverse |
| Determinism | Temperature 0, a fixed seed, and a cache per process keyed by (model, clauses). The sidecar records which model translated each adjustment (`Adjustment.via`) | Nothing to reverse |

## Part 1: the word list

### Colour verbs (`[verbs.*]` in `instructions.toml`)

The white balance op's `tint` is **positive toward green** (`edit/ops.py::light`: the green
channel is multiplied by `1 + 0.1 * tint / 100`), so pink and purple are negative tint.

| Verb | Words | Ops (amount 1.0) |
|---|---|---|
| `pink` | pink, pinker, rosy, rosier, magenta, more pink | tint -60, temp +6 |
| `purple` | purple, purpler, violet, more purple | tint -50, temp -8 |
| `golden` | golden, gold, more golden | temp +16, tint +6 |
| `teal` | teal, cyan, more teal | temp -10, tint +20 |
| `green` | greener, more green | tint +40 |
| `blue` | bluer, more blue | temp -14 |

`[limits]` gains `tint = 80`. These values are a starting point: the session measures the colour
change on a blue sky and a grey frame and adjusts them once, so that amount 1.0 is a clear but
not garish step (a mean shift of about 6 to 12 levels of 255 in the channel that moves most).

### Size words (`[sizes]` in `instructions.toml`)

```toml
[sizes]
"bigger" = 1.5
"larger" = 1.5
"wider" = 1.5
"longer" = 1.5
"taller" = 1.5
"huge" = 2.0
"smaller" = 0.67
"narrower" = 0.67
"shorter" = 0.67
"tighter" = 0.67
"tiny" = 0.5
```

A size word is taken like an amount word (phrases first, at most one per clause, a second one
rejects the clause as "more than one size"). It sets `Adjustment.size`, which scales the place's
extent in `instructions.place_alpha`: the gradient's `end` (capped at 1.0), an oval's `rx`/`ry`,
the edges' oval inversely (a "bigger vignette" reaches further in, so its radii shrink), the
glow's radius. On an object target (not a place) a size word rejects the clause ("size only
applies to a gradient, an oval or a glow").

"gradient" and "graduated" stay filler words. So:

| Text | Result |
|---|---|
| `make the sky pinker` | `sky`, tint -60, temp +6 |
| `make the top pink with a bigger gradient` | `place:top`, tint -60, temp +6, size 1.5 |
| `a smaller vignette` | `place:edges`, exposure -0.25, size 0.67 |
| `add a wider glow` | `place:light`, the glow deltas, size 1.5 |
| `golden light on the left` | `place:left`, temp +16, tint +6 |
| `make the sky have a bigger gradient for pink` | still not understood by the word list (object and size): the local model's job |

## Part 2: the local model

### `src/autoedit/translate_local.py` (new)

```python
def available(cfg) -> bool:
    """True when Ollama answers at cfg.local_url and cfg.local_model is installed (GET /api/tags,
    timeout 1 s). Cached per (url, model) for the process; never raises."""

def translate(clauses: list[str], ops: list[str], places: list[str], cfg) -> list[dict]:
    """Ask the local model for [{clause, target, edits: [{op, value}], size}]. Returns [] when
    the model is not available, on a timeout, on invalid output or any error; the caller
    validates and clamps everything."""
```

- Standard library only (`urllib.request`, `json`). POST `/api/chat` with `stream: false`,
  `think: false` (qwen3.5 is a thinking model; thinking would make it slow), `format` set to the
  JSON schema of the form (Ollama's structured output: the answer must parse against it), and
  `options: {temperature: 0, seed: 7}`, timeout `cfg.local_timeout_s` (the first call loads the
  model into memory; later calls take about a second). `keep_alive` "10m".
- The schema: `{"adjustments": [{"clause": int, "target": string, "edits": [{"op": enum(ops),
  "value": number}], "size": number}]}`. The prompt is F17's, extended with the place names (top,
  bottom, left, right, center, edges, light), the size factor, the colour conventions (tint
  negative is pink/magenta, positive is green; temp positive is warm), and two worked examples.
  The prompt says to leave out a clause that asks for nothing or forbids something.
- Only clause text goes to the model: no image, no path, no statistics.
- Every failure prints one line to stderr and returns []. A missing Ollama is not a failure:
  `available` is false, no request is made, no line is printed.

### One translation helper for every caller

Today `Pipeline.build` translates the run-wide `--edit` through the cloud decider, and Studio's
follow-ups (`web/service.py::photo_adjustments`) are not translated at all. A single function
does both:

```python
# instructions.py
def translate_problems(problems, vocab, known, *, decider=None, cfg=None, allow_cloud=True)
        -> tuple[list[Adjustment], list[str]]:
    """The clauses of `problems` that are plainly "not understood" (translatable), translated by
    the cloud decider when one is active and allow_cloud, else by the local model when
    cfg.translator allows it. Returns (adjustments with via set, the problems that remain)."""
```

- `Pipeline.build` calls it for the run-wide text (cloud allowed, as before).
- `service.photo_adjustments` calls it for a photo's follow-ups with `allow_cloud=False`.
- The process cache (`pipeline._TRANSLATIONS` today) moves next to it, keyed by (who, model,
  clauses), so a re-preview never asks twice.
- `from_translation` accepts and validates `size` (a number in [0.25, 4.0], else 1.0) and keeps
  every existing check (clause index, known ops, finite values, target checks; place names map to
  `place:<name>`).

### Studio and sidecar

- A chip whose clause was translated shows a small "read by the local model" mark (title text
  names the model). The "What it did" window shows `via` in "Your instructions".
- The settings panel gets one switch, "Understand free text on this computer", bound to
  `translator` (auto / off), with a line that says whether Ollama is running (from
  `GET /api/v1/translator/status`, which returns `{available, model}`; behind the same checks as
  the other routes; route snapshot regenerated on purpose).
- The sidecar note of a translated adjustment says so, for example
  `instruction "…": applied to the sky (tint -40) — read by qwen3.5:9b on this computer`.

## Files

| File | Change | Task |
|---|---|---|
| `src/autoedit/contract.py` | `Adjustment.size`, `Adjustment.via`, `RunConfig.translator`, `local_model`, `local_url`, `local_timeout_s` | done by the session before the tasks |
| `src/autoedit/configs/settings.toml`, `tests/snapshots/settings_schema.json` | the four settings | done by the session |
| `src/autoedit/configs/instructions.toml` | colour verbs, `tint` limit, `[sizes]` | 1 |
| `src/autoedit/instructions.py` | size words in `parse`, `size` in `place_alpha` and `from_translation`, `translate_problems` | 1 (size), 2 (`translate_problems`) |
| `src/autoedit/translate_local.py` | new | 2 |
| `src/autoedit/pipeline.py` | `build` calls `translate_problems` | 2 |
| `src/autoedit/web/service.py`, `web/app.py` | follow-ups translated; the status route | 2 |
| `src/autoedit/web/static/studio.html` | the chip mark, the switch | 2 |
| `src/autoedit/cli.py`, `configs/defaults.toml` | `--translator`, `[run] translator` | 2 |
| `scripts/eval_translator.py`, `scripts/data/phrasings.toml` | the measurement below | 2 |
| `tests/test_instruction_words.py` | new | 1 |
| `tests/test_local_translator.py` | new | 2 |

## Tasks

Task 2 needs task 1's `size` in `from_translation`; the two run one after the other. In task 2,
`instructions.py` changes only by adding `translate_problems` and moving the cache.

1. **The word list** (Sonnet, medium). Proving command:
   `PYTHONPATH=src python3 -m pytest tests/test_instruction_words.py tests/test_instructions.py tests/test_shaped_light.py tests/test_follow_up.py -q`.
2. **The local model** (Sonnet, high). Proving command:
   `PYTHONPATH=src python3 -m pytest tests/test_local_translator.py tests/test_llm_refresh.py tests/test_follow_up.py tests/test_api_contract.py tests/test_security.py tests/test_settings_schema.py -q`.

Both then `PYTHONPATH=src python3 tests/run_all.py` and `PYTHONPATH=src .venv/bin/python tests/run_all.py`.

## Tests

`tests/test_instruction_words.py` (task 1):

| # | Test | Asserts |
|---|---|---|
| 1 | `test_colour_parses` | Each colour verb parses with the ops of the table on `sky` and on `place:top`; "pinker" and "more pink" are the same verb |
| 2 | `test_colour_moves_the_right_channels` | On a grey frame, `pink` raises R and B against G; `golden` raises R against B; `teal` raises G and B against R (mean over the frame, applied through the pipeline) |
| 3 | `test_size_parses` | The size table above, exactly; two size words reject the clause; a size word on an object rejects it with the reason |
| 4 | `test_size_scales_the_shape` | `place_alpha` for `top` with size 1.5 reaches 1.5 times further down (the 0.5 line moves), capped at the frame; `edges` with size 1.5 covers more of the frame than with 1.0; `light` glow radius scales |
| 5 | `test_tint_limit` | Pink four times on one target is clamped at the tint limit (status `clamped`) |

`tests/test_local_translator.py` (task 2). No test reaches a real model or the network: each
replaces `urllib.request.urlopen` inside `translate_local` with a stand-in, by hand, restored in
`finally`.

| # | Test | Asserts |
|---|---|---|
| 1 | `test_unavailable_does_nothing` | With `urlopen` raising (no Ollama), `available` is false, `translate` returns [] and prints nothing; a pipeline with `--edit` of a not-understood clause is byte-identical to `translator = "off"` |
| 2 | `test_request_shape` | The body sent to `/api/chat` has `stream` false, `think` false, a `format` schema whose op enum equals the instruction limits, temperature 0, the clause texts and nothing else from the photo (no path, no stats) |
| 3 | `test_answer_becomes_adjustments` | A stand-in answer for "make the sky have a bigger gradient for pink" (target `top`, tint -40, size 1.5) becomes `place:top` with `via` "local", size 1.5, applied; a target `sky` becomes the concept |
| 4 | `test_bad_answers_are_dropped` | Not JSON, wrong shape, an unknown op, a NaN, a clause index out of range, a size of 99: dropped or clamped as `from_translation` does; never an exception |
| 5 | `test_rejected_clauses_are_never_sent` | "don't brighten the face" is not in the request body |
| 6 | `test_follow_ups_are_translated_locally_never_in_the_cloud` | `service.photo_adjustments` with a cloud decider active still uses the local stand-in |
| 7 | `test_cache` | Two builds with the same text make one request |
| 8 | `test_status_route` | `GET /api/v1/translator/status` returns `available` and `model` (stand-in) |

## Measurement (the session runs it; `scripts/eval_translator.py`)

`scripts/data/phrasings.toml` holds 40 phrasings a photographer might type, each with the
expected target and the sign of each expected op (and size above or below 1 where it applies),
written before the model is run. Among them the owner's sentence, colour requests in other words
("give the clouds a rose tint"), size and place requests, vague ones ("make it moodier"),
negations that must be left out ("don't touch the face") and nonsense ("make it pop like a
movie"). The script runs the word list and, for what it misses, the local model, and reports per
phrasing: understood or not, by whom, and whether target and signs match; plus the time per call.

Acceptance: at least 80 % of the phrasings the model is given come back with the right target
and signs; no negation is ever turned into an edit; a call takes under 3 s after the first.

## Acceptance checks (the session runs these)

1. Both suites green; default renders byte-identical (no instruction).
2. The measurement above, with the real model, numbers in the Result.
3. In Studio, in the browser: the owner's sentence, typed into "Edit this version", produces a
   pink gradient at the top, its chip marked as read by the local model; with Ollama stopped the
   same sentence is "not understood" and nothing else changes.

## Result

Built on 2026-11-15. Status: done.

- **Built** by two Builders (Sonnet), one per part.
- **Colour amounts changed from the table above:** at the planned values the strongest channel
  moved only 2 to 4 levels of 255. Final values: pink tint -100 temp +8, purple tint -90 temp -10,
  golden temp +50 tint +6, teal temp -20 tint +100, green tint +100, blue temp -50, for about 6 to
  10 levels. The limits rose with them: `tint = 100`, and `temp` from 20 to 50, so "warm" typed
  several times on one target can now go further than before.
- **Added by the session:** a clause whose target the word list built from leftover words ("can
  you sky", "shadows trees", "give hour feel": more than one word, not a known concept, not a
  place) also goes to the model when one is available; without a model the word list's reading
  stays. Polite openings ("can you", "I want", "should") are filler words now.
- **Measurement** (`scripts/eval_translator.py` on `scripts/data/phrasings.toml`, the real
  `qwen3.5:9b`): of 40 phrasings the word list read 8, all right; the model was given 26 and got 24
  right on target and signs (92 %); 32 of the 34 that ask for an edit came out right. No phrasing
  that must be left out became an edit. A call takes 1.5 s on average after the first (2.6 s), at
  most 2.0 s. The two misses: "the grass looks too yellow, make it greener" (the comma splits it;
  the second half becomes a whole-photo green) and "skin looks too red" (cooled and greened the
  face instead of desaturating it).
- **Checked in Studio** (browser, SAM 3): the owner's sentence typed into "Edit this version"
  became a pink gradient over the top, 1.5 times the normal size, its chip marked "local model",
  the sidecar note "read by qwen3.5:9b on this computer". On that photo's near-white sky the pink
  is gentle: a white-balance shift has little to work with in a blown sky.
- **Safety:** only clause text goes to the model; `local_url` must be this computer (127.0.0.1,
  localhost or ::1); tests never reach the real model (`AUTOEDIT_NO_LOCAL_MODEL=1` in the test
  runners).
- **Not covered:** `autoedit adjust --edit` does not use the local model yet.
- **Default output:** byte-identical; no request changes without instructions.
