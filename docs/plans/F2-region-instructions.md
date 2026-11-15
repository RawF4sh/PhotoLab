# F2. Region instructions

Status: spec (2026-11-11). Size L. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F2.

## Goal

`autoedit run in out --edit "brighten the jacket, darken the sky slightly"` edits the named
things. Any concept the segmenter can find may be named, including concepts that have no rule
block. The text is parsed locally, with no LLM. Every safety layer that exists today still applies.

Without `--edit`, a run renders exactly the pixels it renders today.

## Decisions taken without the owner

Written during the unattended run of 2026-11-11 ([UNATTENDED_RUN.md](../UNATTENDED_RUN.md)).

| Decision | Chosen | Reverse it by |
|---|---|---|
| Are instruction amounts scaled by `intensity`? | No. An instruction is explicit; its amount words are the scale | Multiply the deltas by `cfg.intensity` in `instructions.plan` |
| Does a "darken" instruction opt out of the darkness gate? | No. The photo is written and flagged for review, as today | Pass `preserve_exposure=True` when an applied adjustment has a negative exposure |
| An instruction without a target ("brighten") | Applies to the whole photo | Report it as not understood in `instructions.parse` |
| Strength of each verb | The values in `configs/instructions.toml` below | Edit the file |

## The arbitration rule (what the plan asked this spec to settle)

**An instruction is a delta on top of whatever each pixel would get anyway.**

- The base pipeline is unchanged: the run's concepts are segmented and arbitrated by priority, and
  the rules produce settings per region.
- Each instructed concept contributes a *layer*: its union mask (where the segmenter says the thing
  is) and a delta. A concept that is not in the run's concept list is segmented in addition; it does
  not take part in the base arbitration, in the statistics or in the scene classification, so an
  instruction never changes a base decision.
- Each base region the layer overlaps is split into two *cells*: the part inside the layer gets
  the region's settings plus the delta, the part outside keeps the region's settings. Layer pixels
  that belong to no base region form a cell with the delta alone.
- Cells are disjoint, so no pixel is edited by two regions (invariant 4 in `CLAUDE.md`).
- A jacket inside `subject` therefore keeps the subject's lift and gets the instruction on top.
  "Brighten the subject" also reaches the face that `skin` won in the arbitration, as
  `skin` settings plus the delta, under the `skin` policy.
- Overlaps smaller than `min_cell_coverage` of the frame are ignored, so mask noise at a border
  does not create slivers (each cell costs one full-frame pass).
- Region policies are enforced on the summed settings of each cell, with the policies of every
  concept that owns the cell: the base region and each instructed concept.

## Files

| File | Change |
|---|---|
| `src/autoedit/configs/instructions.toml` | new: the vocabulary |
| `src/autoedit/instructions.py` | new: vocabulary loader, parser, cell planner, report |
| `src/autoedit/contract.py` | `Adjustment` model; `RunConfig.edit`, `RunConfig.instructions_path`; `Result.adjustments` |
| `src/autoedit/policies.py` | `enforce(..., owners=None)` |
| `src/autoedit/pipeline.py` | load and parse once per run; segment extra concepts; plan cells before the policies; report |
| `src/autoedit/cli.py` | `--edit`, `[run] edit`, `instructions_path` |
| `src/autoedit/web/service.py` | `"edit"` in `_ALLOWED` |
| `tests/test_instructions.py` | new |

Not touched: `edit/ops.py`, `edit/apply.py`, `edit/gates.py`, `segment/resolve.py`,
`decide/llm_decider.py`, `configs/quality.toml`, `configs/rules.toml`.

## Config: `src/autoedit/configs/instructions.toml`

Create the file with exactly this content.

```toml
# AutoEdit instructions: plain-language region edits ("brighten the jacket"), parsed locally by
# src/autoedit/instructions.py. This is data: add a verb or a word here, not in code.
#
# [verbs.NAME]  words = every surface form that selects the verb. The other keys are op deltas at
#               amount 1.0, in the apply engine's units (exposure in EV, the rest about -100..100).
# [amounts]     words that scale the deltas of their clause.
# [limits]      ceiling (absolute value) for the SUM of instruction deltas on one target, per op.
# [target]      gating for an instructed concept that has no [concept.X] entry in concepts.toml,
#               and the smallest overlap, as a fraction of the frame, that becomes its own cell.
# [parser]      word lists: ignored words, words that reject a clause, words that mean the whole photo.
#
# An instruction is added on top of the rule baseline and is not scaled by --intensity. Region
# policies in concepts.toml (forbidden ops, caps) still apply to the sum.

[verbs.brighten]
words = ["brighten", "brighter", "lighten", "lighter", "lift"]
exposure = 0.30

[verbs.darken]
words = ["darken", "darker", "dim", "dimmer"]
exposure = -0.30

[verbs.warm]
words = ["warm", "warmer", "warm up"]
temp = 10

[verbs.cool]
words = ["cool", "cooler", "cool down"]
temp = -10

[verbs.saturate]
words = ["saturate", "more saturated", "more colorful", "more colourful", "more vivid"]
saturation = 12

[verbs.mute]
words = ["mute", "muted", "more muted", "desaturate", "less saturated", "less colorful", "less colourful"]
saturation = -14

[verbs.soften]
words = ["soften", "softer", "smooth", "smoother"]
clarity = -12
contrast = -6

[verbs.deepen]
words = ["deepen", "deeper", "richer"]
exposure = -0.12
contrast = 8
saturation = 6

[amounts]
"slightly" = 0.5
"subtly" = 0.5
"gently" = 0.5
"a little" = 0.5
"a bit" = 0.5
"a touch" = 0.5
"somewhat" = 0.75
"much" = 1.6
"a lot" = 1.6
"strongly" = 1.6
"way" = 1.6

[limits]
exposure = 0.75
temp = 20
saturation = 25
clarity = 20
contrast = 15

[target]
min_score = 0.45
min_coverage = 0.003
min_cell_coverage = 0.002

[parser]
stop = ["the", "a", "an", "my", "our", "his", "her", "their", "this", "that", "these", "those",
        "make", "makes", "please", "it", "its", "to", "of", "be", "is", "are", "look", "looks",
        "more", "less", "just", "up", "down", "in", "on", "at", "for", "bit", "little"]
negations = ["not", "no", "don't", "dont", "never", "without", "avoid", "stop"]
global_words = ["global", "everything", "all", "image", "photo", "picture", "whole image",
                "whole photo", "whole picture", "frame", "whole frame", "scene"]
```

## Contract (`contract.py`)

```python
class Adjustment(BaseModel):
    """One directed change: a delta on a target, on top of the rule baseline (F2, F3)."""
    target: str                      # "global" or a concept name
    ops: Settings                    # op name -> delta
    source: str = ""                 # the instruction clause it came from; "" for a direct adjustment
    status: Literal["", "applied", "clamped", "not_found"] = ""   # filled in per photo
```

- `RunConfig.edit: str | None = Field(default=None, max_length=400)`, placed after `prompt`, with
  the comment `# plain-language region instructions, e.g. "brighten the jacket" (instructions.toml)`.
- `RunConfig.instructions_path: str = Field(default_factory=lambda: config_path("instructions.toml"))`
  with the other paths.
- `Result.adjustments: list[Adjustment] = Field(default_factory=list)`, placed after `notes`.

## Module `src/autoedit/instructions.py`

Reference implementation. Follow it; small style changes are fine, behaviour changes are not.

```python
"""Plain-language region instructions and directed adjustments (F2, F3).

`parse` turns text such as "brighten the jacket, darken the sky slightly" into `Adjustment`s
using the vocabulary in configs/instructions.toml. No LLM, no network. `plan` adds them to one
photo: every instructed concept is a layer (its union mask plus a delta) that splits the
arbitrated regions into cells, so no pixel is edited by two regions and each cell keeps the
settings of the region it came from. `report` says, per adjustment, what happened on this photo.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field

import numpy as np

from .concepts import ConceptMeta
from .contract import Adjustment, DetectedConcept, Settings
from .edit.ops import OP_BOUNDS, OP_NAMES
from .segment.base import RegionMask
from .segment.resolve import ResolvedRegions, resolve

GLOBAL = "global"
_SEGMENT_SPLIT = re.compile(r"[.,;!?\n]+|\bthen\b|\bbut\b")
_PART_SPLIT = re.compile(r"\band\b|&|\+")


def _toks(text: str) -> tuple[str, ...]:
    # a typographic apostrophe must not hide a negation ("don’t" would split into "don", "t")
    return tuple(re.findall(r"[a-z0-9'-]+", text.lower().replace("’", "'")))


@dataclass(frozen=True)
class Vocab:
    verbs: dict[str, Settings] = field(default_factory=dict)        # verb -> op deltas at amount 1
    verb_words: tuple = ()      # ((tokens, verb name), ...), longest phrase first
    amount_words: tuple = ()    # ((tokens, scale), ...), longest phrase first
    limits: Settings = field(default_factory=dict)
    stop: frozenset = frozenset()
    negations: frozenset = frozenset()
    global_words: frozenset = frozenset()
    min_score: float = 0.45
    min_coverage: float = 0.003
    min_cell_coverage: float = 0.002


def load_vocab(path: str) -> Vocab:
    """Load instructions.toml. A missing file gives an empty vocabulary (instructions then do
    nothing and say so); an unknown op name is an error, because a typo would be silent."""
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except (FileNotFoundError, OSError):
        return Vocab()
    verbs: dict[str, Settings] = {}
    words = []
    for name, body in data.get("verbs", {}).items():
        ops = {k: float(v) for k, v in body.items() if k != "words"}
        bad = sorted(k for k in ops if k not in OP_NAMES)
        if bad:
            raise ValueError(f"instructions.toml: verb {name!r} uses unknown ops {bad}")
        verbs[name] = ops
        words += [(_toks(w), name) for w in body.get("words", [name])]
    amounts = [(_toks(w), float(v)) for w, v in data.get("amounts", {}).items()]
    longest = lambda item: -len(item[0])  # noqa: E731
    parser, target = data.get("parser", {}), data.get("target", {})
    return Vocab(
        verbs=verbs,
        verb_words=tuple(sorted(words, key=longest)),
        amount_words=tuple(sorted(amounts, key=longest)),
        limits={k: float(v) for k, v in data.get("limits", {}).items()},
        stop=frozenset(parser.get("stop", [])),
        negations=frozenset(parser.get("negations", [])),
        global_words=frozenset(parser.get("global_words", [GLOBAL])),
        min_score=float(target.get("min_score", 0.45)),
        min_coverage=float(target.get("min_coverage", 0.003)),
        min_cell_coverage=float(target.get("min_cell_coverage", 0.002)),
    )


def _take(tokens: list, table) -> list:
    """Blank out every phrase of `table` found in `tokens` and return the matched values in
    text order. Matched tokens become None, so two words that were not neighbours never join."""
    found = []
    for phrase, value in table:
        n = len(phrase)
        for i in range(len(tokens) - n + 1):
            if tuple(tokens[i:i + n]) == phrase:
                found.append((i, value))
                tokens[i:i + n] = [None] * n
    return [value for _i, value in sorted(found, key=lambda item: item[0])]


def _canonical(concept: str, known: set[str], vocab: Vocab) -> str | None:
    if not concept or concept in vocab.global_words:
        return GLOBAL
    if len(concept) > 40 or len(concept.split()) > 4:
        return None
    variants = [concept, concept + "s"]
    if concept.endswith("es"):
        variants.append(concept[:-2])
    if concept.endswith("s"):
        variants.append(concept[:-1])
    return next((v for v in variants if v in known), concept)


def parse(text: str | None, vocab: Vocab, known=()) -> tuple[list[Adjustment], list[str]]:
    """Text -> (adjustments, problems). `known` are the concept names the configs define; a
    plural or singular form of one maps onto it ("mountains" -> "mountain"). `problems` are
    human-readable lines for the clauses that were not turned into an adjustment."""
    if not text or not text.strip():
        return [], []
    known = set(known)
    out: list[Adjustment] = []
    problems: list[str] = []
    for raw_segment in _SEGMENT_SPLIT.split(text.lower()):
        segment = " ".join(raw_segment.split())
        if not segment:
            continue
        if vocab.negations & set(_toks(segment)):
            problems.append(f'not understood (negation): "{segment}"')
            continue
        parts = []
        for chunk in _PART_SPLIT.split(segment):
            tokens = list(_toks(chunk))
            verbs = _take(tokens, vocab.verb_words)
            amounts = _take(tokens, vocab.amount_words)
            concept = " ".join(t for t in tokens if t is not None and t not in vocab.stop)
            if verbs or concept:
                parts.append({"verbs": verbs, "amount": amounts[0] if amounts else None,
                              "concept": concept})
        with_verb = [i for i, p in enumerate(parts) if p["verbs"]]
        with_concept = [i for i, p in enumerate(parts) if p["concept"]]
        if not with_verb:
            problems.append(f'not understood: "{segment}"')
            continue
        for i, part in enumerate(parts):
            verbs, amount, concept = part["verbs"], part["amount"], part["concept"]
            if not verbs:   # "brighten the jacket and the sky": the second part borrows the verb
                before = [j for j in with_verb if j < i]
                donor = parts[before[-1] if before else with_verb[0]]
                verbs = donor["verbs"]
                amount = donor["amount"] if amount is None else amount
            if not concept:  # "brighten and warm the jacket": the first part borrows the target
                after = [j for j in with_concept if j > i]
                before = [j for j in with_concept if j < i]
                concept = parts[after[0]]["concept"] if after else (
                    parts[before[-1]]["concept"] if before else "")
            target = _canonical(concept, known, vocab)
            if target is None:
                problems.append(f'not understood (target too long): "{segment}"')
                continue
            scale = 1.0 if amount is None else amount
            for verb in verbs:
                out.append(Adjustment(
                    target=target, source=segment,
                    ops={op: delta * scale for op, delta in vocab.verbs[verb].items()}))
    return out, problems


def extra_concepts(adjustments: list[Adjustment], concepts: list[str]) -> list[str]:
    """Instructed concepts the run does not segment yet, in first-mention order."""
    out: list[str] = []
    for a in adjustments:
        if a.target != GLOBAL and a.target not in concepts and a.target not in out:
            out.append(a.target)
    return out


@dataclass
class Plan:
    cells: list[RegionMask]                 # disjoint, in apply order
    settings: dict[str, Settings]           # cell name -> settings (base + deltas)
    owners: dict[str, list[str]]            # cell name -> concepts whose policy applies
    feather: dict[str, str]                 # cell name -> concept whose feather applies
    detected: list[DetectedConcept]         # the extra concepts, present or not
    hit: dict[str, list[str]]               # target -> cell names it reached ([] = not found)
    limited: set[tuple[str, str]]           # (target, op) clamped by [limits] or OP_BOUNDS
    before: dict[str, Settings]             # settings as planned, before the policies ran


def _summed(adjustments: list[Adjustment], vocab: Vocab) -> tuple[dict[str, Settings], set]:
    """Per target: instruction deltas summed and clamped to [limits], then direct adjustments
    added, then the total clamped to the op's own bounds."""
    asked: dict[str, Settings] = {}
    limited: set[tuple[str, str]] = set()
    for from_text in (True, False):
        for a in adjustments:
            if bool(a.source) != from_text:
                continue
            acc = asked.setdefault(a.target, {})
            for op, delta in a.ops.items():
                acc[op] = acc.get(op, 0.0) + float(delta)
        if from_text:
            for target, acc in asked.items():
                for op, value in acc.items():
                    lim = vocab.limits.get(op)
                    if lim is not None and abs(value) > lim:
                        acc[op] = lim if value > 0 else -lim
                        limited.add((target, op))
    for target, acc in asked.items():
        for op, value in acc.items():
            lo, hi = OP_BOUNDS.get(op, (-100.0, 100.0))
            if not lo <= value <= hi:
                acc[op] = min(max(value, lo), hi)
                limited.add((target, op))
    return asked, limited


def _add(base: Settings, delta: Settings) -> Settings:
    out = dict(base)
    for op, d in delta.items():
        out[op] = out.get(op, 0.0) + d
    return out


def _cell(src: RegionMask, name: str, mask: np.ndarray) -> RegionMask:
    return RegionMask(name=name, mask=mask, score=src.score, coverage=float(mask.mean()),
                      instance_count=src.instance_count)


def plan(adjustments: list[Adjustment], vocab: Vocab, raw: list[RegionMask], concepts: list[str],
         resolved: ResolvedRegions, region_settings: dict[str, Settings],
         global_settings: Settings, meta: dict[str, ConceptMeta]) -> Plan:
    """Add the adjustments to one photo. `global_settings` is changed in place; the region side
    comes back as cells with their own settings. `raw` must hold the masks for the run's
    concepts and for `extra_concepts(adjustments, concepts)`."""
    asked, limited = _summed(adjustments, vocab)
    hit: dict[str, list[str]] = {}
    if GLOBAL in asked:
        for op, delta in asked[GLOBAL].items():
            global_settings[op] = global_settings.get(op, 0.0) + delta
        hit[GLOBAL] = [GLOBAL]

    extra = extra_concepts(adjustments, concepts)
    gate = {**meta, **{c: ConceptMeta(name=c, rule_region=c, min_score=vocab.min_score,
                                      min_coverage=vocab.min_coverage)
                       for c in extra if c not in meta}}
    ext = resolve(raw, extra, gate, resolved.shape) if extra else None
    union = {**resolved.union, **(ext.union if ext else {})}
    by_name = {rm.name: rm for rm in raw}

    h, w = resolved.shape
    min_px = max(1, int(vocab.min_cell_coverage * h * w))
    cells: list[tuple[RegionMask, list[str]]] = [(r, [r.name]) for r in resolved.regions]
    settings = {name: dict(s) for name, s in region_settings.items()}
    feather: dict[str, str] = {}
    for target, delta in asked.items():
        if target == GLOBAL:
            continue
        hit[target] = []
        mask = union.get(target)
        if mask is None:
            continue
        nxt: list[tuple[RegionMask, list[str]]] = []
        rest = mask.copy()
        for rm, own in cells:
            inside = rm.mask & mask
            rest &= ~rm.mask
            if int(inside.sum()) < min_px:
                nxt.append((rm, own))
                continue
            outside = rm.mask & ~mask
            if int(outside.sum()) < min_px:      # the layer covers the whole cell: keep its name
                settings[rm.name] = _add(settings.get(rm.name, {}), delta)
                nxt.append((rm, own + [target]))
                hit[target].append(rm.name)
                continue
            name = f"{target}@{rm.name}"
            settings[name] = _add(settings.get(rm.name, {}), delta)
            feather[name] = target
            nxt.append((_cell(rm, name, inside), own + [target]))
            nxt.append((_cell(rm, rm.name, outside), own))
            hit[target].append(name)
        if int(rest.sum()) >= min_px:            # the part of the layer no base region owns
            name = target if all(rm.name != target for rm, _own in nxt) else f"{target}@background"
            settings[name] = dict(delta)
            feather[name] = target
            src = by_name.get(target)
            nxt.append((RegionMask(name=name, mask=rest, score=src.score if src else 0.0,
                                   coverage=float(rest.mean()),
                                   instance_count=src.instance_count if src else 1), [target]))
            hit[target].append(name)
        cells = nxt
    return Plan(cells=[c for c, _own in cells], settings=settings,
                owners={c.name: own for c, own in cells}, feather=feather,
                detected=list(ext.detected) if ext else [], hit=hit, limited=limited,
                before={name: dict(s) for name, s in settings.items()})


def report(adjustments: list[Adjustment], p: Plan, settings: dict[str, Settings]) -> list[str]:
    """Set each adjustment's `status` for this photo and return one sidecar note per adjustment.
    Call it after the region policies ran on `settings`."""
    notes: list[str] = []
    for a in adjustments:
        label = f'instruction "{a.source}"' if a.source else "adjustment"
        asked = ", ".join(f"{op} {delta:+.2f}" for op, delta in a.ops.items())
        cells = p.hit.get(a.target, [])
        if not cells:
            a.status = "not_found"
            notes.append(f"{label}: {a.target} not found in this photo ({asked} not applied)")
            continue
        clamped = any((a.target, op) in p.limited for op in a.ops) or any(
            settings.get(c, {}).get(op, 0.0) != p.before.get(c, {}).get(op, 0.0)
            for c in cells if c != GLOBAL for op in a.ops)
        a.status = "clamped" if clamped else "applied"
        where = "the whole photo" if a.target == GLOBAL else a.target
        tail = ", held back by a limit or the region policy" if clamped else ""
        notes.append(f"{label}: applied to {where} ({asked}){tail}")
    return notes
```

## `policies.py`

`enforce(region_settings, meta, owners=None)`. For each entry, apply the policy of every concept
in `(owners or {}).get(name, [name])`, in order. Notes keep the entry's name:
`policy[<name>]: ...`. With `owners=None` the behaviour and the notes are exactly as today.

## `pipeline.py`

`Pipeline` gets three fields with defaults, at the end of the dataclass:
`instr: Vocab | None = None`, `adjustments: list = field(default_factory=list)`,
`adjust_problems: list = field(default_factory=list)`.

`Pipeline.build`: `vocab = instructions.load_vocab(cfg.instructions_path)`. When `cfg.edit` has
text: `adjustments, problems = instructions.parse(cfg.edit, vocab, known)` with
`known = set(meta) | {c for cs in presets.values() for c in cs}`. Otherwise both lists are empty.

`process_one` gets a keyword parameter `adjustments: list[Adjustment] | None = None` (F3 will pass
per-photo adjustments through it). Changes, in pipeline order:

1. After `concepts = self.concepts_for(style)`:
   `adjs = [a.model_copy() for a in (*self.adjustments, *(adjustments or []))]` and
   `extra = instructions.extra_concepts(adjs, concepts)`.
2. Segment `concepts + extra` (when both are empty, no call, as today). `resolve` is still called
   with `concepts` only. Statistics, scene and the decider see no extra concept.
3. Directly before the first `policies.enforce(...)`:
   ```python
   apply_regions, owners, adj_plan = resolved.regions, None, None
   if adjs:
       adj_plan = instructions.plan(adjs, self.instr, raw, concepts, resolved,
                                    region_settings, global_settings, self.meta)
       apply_regions, region_settings, owners = adj_plan.cells, adj_plan.settings, adj_plan.owners
   policy_notes = policies.enforce(region_settings, self.meta, owners=owners)
   if adj_plan is not None:
       policy_notes += instructions.report(adjs, adj_plan, region_settings)
   policy_notes += [f"instruction {p}" for p in self.adjust_problems]
   ```
4. The second `policies.enforce` call (inside the reference block) also passes `owners=owners`.
5. The apply loop iterates `apply_regions` and takes the feather from
   `meta_for((adj_plan.feather if adj_plan else {}).get(r.name, r.name), self.meta)`.
6. `region_meta` is built from `apply_regions`.
7. `emit.build_result(... detected=resolved.detected + (adj_plan.detected if adj_plan else []))`,
   and after it `result.adjustments = adjs`. `build_notes` keeps using `resolved.detected`.

With no adjustment, none of the new branches runs and `apply_regions is resolved.regions`.

## `cli.py` and `web/service.py`

- `_flatten`: add `"edit"` to the `[run]` keys.
- `load_run_config`: `kw["instructions_path"]` is set to `<configs_dir>/instructions.toml` only
  when that file exists (an older custom configs folder then falls back to the packaged file);
  add `"edit": getattr(args, "edit", None)` to the overrides.
- `_add_run_flags`: `--edit`, help
  `'plain-language region instructions, e.g. "brighten the jacket, darken the sky slightly"'`.
- `service._ALLOWED`: add `"edit"`. `make_cfg` sets `instructions_path` the same way as the CLI.

## Tasks

One Builder (Sonnet, medium), in this order. Tier for both: Builder.

| Task | Content | Proving command |
|---|---|---|
| T1 | `instructions.toml`, `instructions.py`, the `Adjustment` model, `policies.enforce(owners=)`, tests 1 to 7 | `PYTHONPATH=src python3 -m pytest tests/test_instructions.py tests/test_policies.py -q` |
| T2 | `RunConfig` and `Result` fields, pipeline, CLI, API, tests 8 to 13 | `PYTHONPATH=src python3 tests/run_all.py` |

## Tests: `tests/test_instructions.py`

Follow the style of `tests/test_intent.py` and `tests/test_pipeline.py` (plain functions,
`tmp_path`, no fixtures). Load the packaged vocabulary with
`instructions.load_vocab(os.path.join(configs_dir(), "instructions.toml"))`.

| # | Test | Asserts |
|---|---|---|
| 1 | `test_parse_golden` | With `known={"sky", "mountain", "grass", "skin", "subject"}`: `"brighten the jacket"` gives one adjustment, target `jacket`, ops `{"exposure": 0.30}`. `"darken the sky slightly"` gives `sky`, exposure -0.15. `"slightly brighten the jacket and the sky"` gives jacket +0.15 and sky +0.15. `"brighten and warm the jacket"` gives two adjustments on `jacket`: exposure +0.30 and temp +10. `"make the mountains much darker, warm the grass a little"` gives `mountain` exposure -0.48 and `grass` temp +5. `"soften the skin; deepen the sky"` gives `skin` `{clarity: -12, contrast: -6}` and `sky` `{exposure: -0.12, contrast: 8, saturation: 6}`. Compare floats with a tolerance of 1e-9 |
| 2 | `test_parse_global_target` | `"brighten"`, `"make it darker"` and `"darken the whole photo"` each give one adjustment with target `global` |
| 3 | `test_parse_rejects` | `"don't brighten the sky"` gives no adjustment and one problem containing `negation`; the same text with a typographic apostrophe (`’`) does too. `"do something nice"` gives no adjustment and one problem starting with `not understood`. `None`, `""` and `"   "` give `([], [])`. A target of six words gives a `target too long` problem |
| 4 | `test_vocab_validation` | A TOML file whose verb uses the op `exposur` raises `ValueError` naming the op. A missing path gives an empty `Vocab`, and parsing `"brighten the sky"` with it gives no adjustment and one problem |
| 5 | `test_plan_splits_a_region` | Hand-made 100 by 100 masks: base region `subject` (rows 20 to 80, columns 20 to 80) with settings `{"exposure": 0.2}`; raw mask `jacket` (rows 40 to 60, columns 40 to 60). One adjustment `jacket` `{"exposure": 0.3}`. The plan has the cells `jacket@subject` (exposure 0.5) and `subject` (exposure 0.2); the two masks are disjoint and their union is the old subject mask; `owners["jacket@subject"] == ["subject", "jacket"]`; `hit["jacket"] == ["jacket@subject"]` |
| 6 | `test_plan_background_and_sliver` | A `jacket` mask that lies half on the subject and half on background gives the cells `jacket@subject`, `subject` and `jacket` (settings are the delta alone). A layer that overlaps a second region by fewer pixels than `min_cell_coverage` leaves that region as one cell with unchanged settings. A target with no mask gives `hit[target] == []` and `report` sets `not_found` |
| 7 | `test_policy_applies_to_cells` | `policies.enforce({"x@sky": {"temp": 14.0}}, meta, owners={"x@sky": ["sky", "x"]})` with the packaged concepts clamps `temp` to 4 and returns a note starting with `policy[x@sky]`. Without `owners` the existing behaviour is unchanged (the tests in `tests/test_policies.py` still pass) |
| 8 | `test_limits_and_direct_adjustments` | Three text adjustments of exposure +0.30 on one target sum to 0.75 (the limit), and `report` marks them `clamped`. A direct adjustment (`source=""`) of exposure +2.0 is not clamped by `[limits]`; one of +9.0 is clamped to 3.0 |
| 9 | `test_pipeline_instruction_on_sky_and_absent` | Stub backend, the image from `tests/test_pipeline.py::_make`, `RunConfig(backend="stub", concepts=["sky", "subject"], edit="darken the sky, brighten the unicorn")`. The result has two adjustments with status `applied` and `not_found`, one note per adjustment, and the mean of the output over the top 40 rows is lower than in the same run without `edit` |
| 10 | `test_pipeline_without_instruction_is_identical` | `edit=None`, `edit=""` and `edit="   "` give arrays equal with `np.array_equal`, and `result.adjustments == []` |
| 11 | `test_pipeline_overlay_on_a_custom_concept` | Build the pipeline, then replace `pipe.segmenter` with a fake whose `segment(img, concepts)` returns a `subject` rectangle and, only when asked for it, a `jacket` rectangle inside it. With `edit="brighten the jacket"`: the fake was asked for `jacket`; the sidecar masks contain the target `jacket@subject`; the centre of the jacket is brighter than in the run without `edit`; a subject pixel far from the jacket is unchanged (difference below 1e-6). Without `edit` the fake is never asked for `jacket` |
| 12 | `test_cli_and_api_carry_edit` | `cli.load_run_config` with `--edit "brighten the sky"` sets `cfg.edit`; a `--config` TOML with `[run] edit = "..."` does too; `service.make_cfg({"edit": "..."})` does too. A text longer than 400 characters is rejected by `RunConfig` |
| 13 | `test_sidecar_carries_adjustments` | `Result.model_dump_json` of the run from test 9 contains `adjustments` with `target`, `ops`, `source` and `status`, and `Result.model_validate_json` reads it back |

## Acceptance checks (the session runs these)

1. Both test suites green: system Python and `.venv`.
2. The stub smoke run completes with sidecars and a manifest.
3. Default renders are pixel-identical to the baseline taken before F2 (reference set A, B and the
   smoke corpus; maximum difference 0).
4. SAM 3 contact sheet: six panel photos, three instructions, rendered with `.venv/bin/python`.
5. Checker review (Opus, high): the diff against this spec and the invariants in `CLAUDE.md`.
   `pipeline.py` is a protected area.

The tone gate does not apply: with no instruction no pixel changes (check 3), and an instruction is
an explicit request, not a default.

## Known limits

- The grammar is small on purpose: verb, optional amount, target, joined by "and" or commas. No
  numbers ("by 20 %"), no positions ("the left mountain" is passed to the segmenter as typed).
- Each cell costs one full-frame pass at apply time. Three instructions on a 22 MP photo add
  several seconds until F4 lands.
- The stub segmenter knows a handful of concepts; everything else is "not found" without SAM 3.
- `saturate` on skin is capped at +4 by the skin policy. That is intended.

## Result

Closed on 2026-11-11, in the unattended run.

- **Built** by one Builder (Sonnet, medium) from this spec. Two reviews by a Checker (Opus, high)
  and two fix rounds followed; their findings and fixes are in "Review round 1" above and in
  "Review round 2" of the [F3 spec](F3-per-photo-adjustments.md).
- **What the reviews changed:** the parser takes a clause whole or not at all (negations,
  exclusions, numbers, two verbs, a missing target are rejected and reported); the cells of one
  region are blended in parallel, which removed a visible ring around an instructed object; a
  target inherits the policy of the protected concept it names; a cell without settings still
  claims its pixels.
- **Tests:** `tests/test_instructions.py` and `tests/test_apply_groups.py`. The tests of the seam
  and of the policy spill were shown to fail when their fix is undone.
- **Default output:** byte-identical to the renders made before the feature, on the reference
  photos (two RAW files and two JPEGs, two styles) and the smoke corpus, after every round.
- **With SAM 3,** six panel photos and `--edit "darken the sky, brighten the trees, brighten the
  bridge"`: the sky instruction applied on all six, the trees on four, the bridge on one; where a
  target does not exist the sidecar says so. Between 21 % and 54 % of the pixels moved, matching
  the instructed regions. The bridge, a concept without any rule block, was edited.
- **Found on the way, not fixed:** SAM 3 masks stop 1 to 12 proxy pixels short of the frame edge
  on most photos (the top row of a sky mask was sky in 27 % to 69 % of the columns). A region edit
  therefore fades along the frame edge. A fix would snap masks to the frame edge behind a setting.


## Review round 1 (2026-11-11)

The Checker (Opus, high) reviewed the first build and found two blocking parser errors, a visible
seam, and several smaller points. This section replaces the parts of the spec above that it
contradicts. It also corrects one mistake in the F3 spec (`paths.config_paths`).

### Findings and what changes

| # | Finding | Change |
|---|---|---|
| 1 | A target in another script was dropped and the edit went to the whole photo: `"brighten the 天空"` became a global edit. So did `"darken the ;; sky"` | Tokens are Unicode words. A clause with a determiner ("the") but no target is rejected |
| 2 | `"brighten everything but the sky"` brightened the sky | "but" no longer splits; it and other exclusion words reject the clause |
| 3 | Two verbs in one part went to a made-up target: `"the cool sky brighter"`, `"brighten the sky darken the grass"` | A part with more than one verb is rejected |
| 4 | `"darken the sky less"` darkened fully; `"brighten the sky by 20%"` got the target `sky by 20` | "less", "hardly", "undo" and similar reject the clause; a target with a digit is rejected |
| 5 | Splitting a region left a ring where a quarter of the region's own edit was lost (6.6 of 255 measured) | The cells of one region are blended in parallel against the same input, with no claim between them |
| 6 | A broken `instructions.toml` in a custom configs folder stopped runs that have no `--edit` | The vocabulary is loaded on first use |
| 7 | The `config_path` fallback applied to every config file, so a folder without `palettes.toml` silently got the packaged palettes (a pixel change for that setup). `config_paths` in the F3 spec had the same effect | Only `instructions.toml` falls back to the packaged file |
| 8 | A look-alike of a protected concept ("face" when `skin` is not in the run) got none of its policy | A target inherits the policy of the concept it is a kind of: a `[policy]` table, and the head noun of the target |
| 9 | "applied" was reported for an op the policy dropped completely; "not found" for a target that was found but too small | New status `blocked`; the note says "too small" |
| 10 | Separators, joiners and target length limits were in code | They are data in `[parser]` |
| 11 | Four tests would pass with the feature broken | Strengthened, see the test list |

Accepted without change, and recorded here: `--edit` works with `concepts=[]` (an explicit
instruction outranks "no region editing"); statuses describe the plan before the reference tuner
and the banding gate run; every sidecar now has an `adjustments` key, empty without instructions.

### `instructions.toml`

Replace the `[parser]` table, add `"more" = 1.3` to `[amounts]`, and add the `[policy]` table:

```toml
[policy]
# A target that is a kind of a protected concept obeys that concept's region policy too
# (concepts.toml: forbidden ops, caps). The head noun of a target is checked as well, so
# "blue sky" obeys the sky policy without an entry here.
face = "skin"
faces = "skin"
cheek = "skin"
cheeks = "skin"
forehead = "skin"
arm = "skin"
arms = "skin"
hand = "skin"
hands = "skin"
glacier = "snow"
ice = "snow"
cloud = "sky"
clouds = "sky"
forest = "trees"
bush = "foliage"
bushes = "foliage"
lake = "water"
river = "water"
sea = "water"
ocean = "water"
pond = "water"
waterfall = "water"
meadow = "grass"
lawn = "grass"
field = "grass"
peak = "mountain"
peaks = "mountain"
hill = "mountain"
hills = "mountain"

[parser]
# Words that end one instruction and start the next, besides . , ; ! ? and a line break.
separators = ["then"]
# What joins two parts of one instruction: "brighten the jacket and the sky".
joiners = ["and", "&", "+"]
# Words that announce a target. A clause that has one but no target is not understood.
determiners = ["the", "a", "an", "my", "our", "his", "her", "their", "this", "that", "these", "those"]
# Filler words, ignored.
stop = ["make", "makes", "please", "it", "its", "to", "of", "be", "is", "are", "look", "looks",
        "just", "up", "down", "in", "on", "at", "for", "bit", "little"]
# Words that reject the whole clause: a negation, an exclusion, or a word that turns the meaning
# around. A rejected clause is reported in the sidecar and changes nothing.
reject = ["not", "no", "don't", "dont", "never", "without", "avoid", "stop", "nor", "neither",
          "but", "except", "apart", "excluding", "besides", "unless",
          "less", "hardly", "barely", "undo", "revert", "reset", "remove"]
global_words = ["global", "everything", "all", "image", "photo", "picture", "whole image",
                "whole photo", "whole picture", "frame", "whole frame", "scene"]
max_target_words = 4
max_target_chars = 40
```

### `instructions.py`

`Vocab` gets the fields `policy: dict[str, str]`, `determiners: frozenset`, `reject: frozenset`
(replacing `negations`), `segment_split` and `part_split` (compiled patterns),
`max_target_words: int = 4`, `max_target_chars: int = 40`. In `load_vocab`:

```python
    def alternation(words):                 # a word needs \b on both sides, a symbol does not
        return "|".join(rf"\b{re.escape(w)}\b" if w[:1].isalnum() else re.escape(w) for w in words)

    seps = [str(w).lower() for w in parser.get("separators", [])]
    joins = [str(w).lower() for w in parser.get("joiners", [])]
    segment_split = re.compile(r"[.,;!?\n]+" + ("|" + alternation(seps) if seps else ""))
    part_split = re.compile(alternation(joins)) if joins else None
```

The module-level `_SEGMENT_SPLIT` and `_PART_SPLIT` go away. The default `Vocab()` (missing file)
has `segment_split = re.compile(r"[.,;!?\n]+")` and `part_split = None`.

```python
def _toks(text: str) -> tuple[str, ...]:
    # Unicode words, with an inner apostrophe or hyphen ("don't", "t-shirt"). A typographic
    # apostrophe must not hide a negation.
    return tuple(re.findall(r"[^\W_]+(?:['-][^\W_]+)*", text.lower().replace("’", "'")))


def _canonical(concept: str, known: set[str], vocab: Vocab) -> str | None:
    if not concept or concept in vocab.global_words:
        return GLOBAL
    if len(concept) > vocab.max_target_chars or len(concept.split()) > vocab.max_target_words:
        return None
    variants = [concept, concept + "s"]
    if concept.endswith("es"):
        variants.append(concept[:-2])
    if concept.endswith("s"):
        variants.append(concept[:-1])
    return next((v for v in variants if v in known), concept)


def parse(text: str | None, vocab: Vocab, known=()) -> tuple[list[Adjustment], list[str]]:
    """Text -> (adjustments, problems). A clause is taken whole or not at all: anything the
    grammar does not cover is reported in `problems` and changes nothing."""
    if not text or not text.strip():
        return [], []
    known = set(known)
    out: list[Adjustment] = []
    problems: list[str] = []
    clean = "".join(ch if ch.isprintable() or ch == "\n" else " "
                    for ch in text.lower().replace("’", "'"))
    for raw_segment in vocab.segment_split.split(clean):
        segment = " ".join(raw_segment.split())
        seg_tokens = _toks(segment)
        if not seg_tokens:
            continue
        if vocab.reject & set(seg_tokens):
            problems.append(f'not understood (negation or exclusion): "{segment}"')
            continue
        parts = []
        for chunk in (vocab.part_split.split(segment) if vocab.part_split else [segment]):
            tokens = list(_toks(chunk))
            verbs = _take(tokens, vocab.verb_words)
            amounts = _take(tokens, vocab.amount_words)
            rest = [t for t in tokens if t is not None]
            words = [t for t in rest if t not in vocab.stop and t not in vocab.determiners]
            det = any(t in vocab.determiners for t in rest)
            if verbs or words or det:       # a part of filler words only is dropped
                parts.append({"verbs": verbs, "amount": amounts[0] if amounts else None,
                              "concept": " ".join(words), "det": det})
        with_verb = [i for i, p in enumerate(parts) if p["verbs"]]
        with_concept = [i for i, p in enumerate(parts) if p["concept"]]
        why = None
        if not with_verb:
            problems.append(f'not understood: "{segment}"')
            continue
        if any(len(p["verbs"]) > 1 for p in parts):
            why = "more than one verb"
        elif any(ch.isdigit() for p in parts for ch in p["concept"]):
            why = "numbers are not supported"
        found: list[Adjustment] = []
        for i, part in enumerate(parts):
            if why:
                break
            verbs, amount, concept = part["verbs"], part["amount"], part["concept"]
            if not verbs:   # "brighten the jacket and the sky": the second part borrows the verb
                before = [j for j in with_verb if j < i]
                donor = parts[before[-1] if before else with_verb[0]]
                verbs = donor["verbs"]
                amount = donor["amount"] if amount is None else amount
            if not concept and part["det"]:      # "darken the": a target was announced, none came
                why = "no target"
                break
            if not concept:  # "brighten and warm the jacket": the first part borrows the target
                after = [j for j in with_concept if j > i]
                before = [j for j in with_concept if j < i]
                concept = parts[after[0]]["concept"] if after else (
                    parts[before[-1]]["concept"] if before else "")
            target = _canonical(concept, known, vocab)
            if target is None:
                why = "target too long"
                break
            scale = 1.0 if amount is None else amount
            found.append(Adjustment(
                target=target, source=segment,
                ops={op: delta * scale for op, delta in vocab.verbs[verbs[0]].items()}))
        if why:
            problems.append(f'not understood ({why}): "{segment}"')
        else:
            out += found
    return out, problems
```

Policy inheritance and the plan. `Plan.cells` and `Plan.feather` (the dict) are replaced:

```python
@dataclass
class Plan:
    groups: list[list[tuple[RegionMask, np.ndarray]]]   # apply order. One group per base region
    #   (or per layer part outside every region): its cells as (cell, mask to feather).
    feather: list[str]                      # per group: the concept whose feather applies
    settings: dict[str, Settings]           # cell name -> settings (base + deltas)
    owners: dict[str, list[str]]            # cell name -> concepts whose policy applies
    detected: list[DetectedConcept]         # the extra concepts, present or not
    hit: dict[str, list[str]]               # target -> cell names it reached ([] = not found)
    limited: set[tuple[str, str]]           # (target, op) clamped by [limits] or OP_BOUNDS
    before: dict[str, Settings]             # settings as planned, before the policies ran
    present: set[str]                       # targets that have a mask in this photo

    @property
    def cells(self) -> list[RegionMask]:
        return [cell for group in self.groups for cell, _mask in group]


def _policy_owners(target: str, meta: dict[str, ConceptMeta], vocab: Vocab) -> list[str]:
    """The concepts whose region policy a target must obey: itself, and before it the protected
    concept it is a kind of ("face" -> skin, "blue sky" -> sky)."""
    head = target.split()[-1]
    for cand in (target, head, head[:-1] if head.endswith("s") else head + "s"):
        like = vocab.policy.get(cand) or (cand if cand in meta else None)
        if like and like != target:
            return [like, target]
    return [target]
```

In `plan`, the part after `min_px = ...` becomes:

```python
    # One group per base region. The cells of a group partition the region; apply blends them in
    # parallel, so splitting a region never weakens the region's own edit along the cut.
    groups: list[list[tuple[RegionMask, np.ndarray, list[str]]]] = [
        [(r, r.mask, [r.name])] for r in resolved.regions]
    feather = [r.name for r in resolved.regions]
    settings = {name: dict(s) for name, s in region_settings.items()}
    for target, delta in asked.items():
        if target == GLOBAL:
            continue
        hit[target] = []
        mask = union.get(target)
        if mask is None:
            continue
        own_t = _policy_owners(target, meta, vocab)
        rest = mask.copy()
        for group in groups:
            nxt = []
            for cell, fmask, own in group:
                inside = cell.mask & mask
                rest &= ~cell.mask
                if int(inside.sum()) < min_px:
                    nxt.append((cell, fmask, own))
                    continue
                outside = cell.mask & ~mask
                if int(outside.sum()) < min_px:      # the layer covers the whole cell: keep its name
                    settings[cell.name] = _add(settings.get(cell.name, {}), delta)
                    nxt.append((cell, fmask, own + own_t))
                    hit[target].append(cell.name)
                    continue
                name = f"{target}@{cell.name}"
                settings[name] = _add(settings.get(cell.name, {}), delta)
                nxt.append((_cell(cell, name, inside), fmask & mask, own + own_t))
                nxt.append((_cell(cell, cell.name, outside), fmask & ~mask, own))
                hit[target].append(name)
            group[:] = nxt
        if int(rest.sum()) >= min_px:                # the part of the layer no region owns
            taken = {cell.name for group in groups for cell, _f, _o in group}
            name = target if target not in taken else f"{target}@background"
            settings[name] = dict(delta)
            src = by_name.get(target)
            cell = RegionMask(name=name, mask=rest, score=src.score if src else 0.0,
                              coverage=float(rest.mean()),
                              instance_count=src.instance_count if src else 1)
            # Feather the whole layer, not the leftover: claim compositing keeps it off the
            # regions, and the delta then has no dip where the layer crosses a region's outline.
            groups.append([(cell, mask, list(own_t))])
            feather.append(target)
            hit[target].append(name)
    return Plan(groups=[[(cell, fmask) for cell, fmask, _own in group] for group in groups],
                feather=feather, settings=settings,
                owners={cell.name: own for group in groups for cell, _f, own in group},
                detected=list(ext.detected) if ext else [], hit=hit, limited=limited,
                before={name: dict(s) for name, s in settings.items()},
                present={t for t in asked if t in union})
```

`report`, complete:

```python
def report(adjustments: list[Adjustment], p: Plan, settings: dict[str, Settings]) -> list[str]:
    """Set each adjustment's `status` for this photo and return one sidecar note per adjustment.
    Call it right after the region policies ran on `settings`."""
    notes: list[str] = []
    for a in adjustments:
        label = f'instruction "{a.source}"' if a.source else "adjustment"
        asked = ", ".join(f"{op} {delta:+.2f}" for op, delta in a.ops.items())
        where = "the whole photo" if a.target == GLOBAL else a.target
        cells = p.hit.get(a.target, [])
        if not cells:
            a.status = "not_found"
            why = "is too small in this photo" if a.target in p.present else "not found in this photo"
            notes.append(f"{label}: {a.target} {why} ({asked} not applied)")
            continue
        regions = [c for c in cells if c != GLOBAL]
        if regions and all(op in p.before.get(c, {}) and op not in settings.get(c, {})
                           for c in regions for op in a.ops):
            a.status = "blocked"
            notes.append(f"{label}: not applied to {where}, the region policy forbids "
                         f"{', '.join(a.ops)}")
            continue
        clamped = any((a.target, op) in p.limited for op in a.ops) or any(
            settings.get(c, {}).get(op, 0.0) != p.before.get(c, {}).get(op, 0.0)
            for c in regions for op in a.ops)
        a.status = "clamped" if clamped else "applied"
        tail = ", held back by a limit or the region policy" if clamped else ""
        notes.append(f"{label}: applied to {where} ({asked}){tail}")
    return notes
```

### `contract.py`, `paths.py`

- `Adjustment.status: Literal["", "applied", "clamped", "blocked", "not_found"]`.
- `paths.config_path(name, packaged_fallback=False)`: with the flag, a file missing from the
  active folder resolves to the packaged one; without it, the old behaviour (the path in the
  active folder, existing or not). `RunConfig.instructions_path` uses
  `config_path("instructions.toml", packaged_fallback=True)`. No other field uses the flag.
- `paths.config_paths(configs_dir=None)`: returns the path for every file in `_CONFIG_FILES`,
  existing or not, exactly as `cli.load_run_config` built them before F3. Only
  `instructions_path` is left out when the folder has no `instructions.toml`.

### `edit/apply.py`

A region entry may now be a list of `(alpha, settings)` pairs: the cells of one region. The
existing tuple entries keep their exact code path.

```python
    for entry in regions:
        if isinstance(entry, list):   # the cells of one region (F2): blended in parallel against
            free = 1.0 - claimed      # the same input, so the cut between them loses no edit
            total = np.zeros_like(claimed)
            mix = None
            for alpha, settings in entry:
                if not settings:
                    continue
                w = np.minimum(alpha * free, free - total)
                if float(w.max()) <= 1e-4:
                    continue
                part = ops.apply_ops(out, settings) * w[..., None]
                mix = part if mix is None else mix + part
                total = total + w
            if mix is not None:
                out = out * (1.0 - total[..., None]) + mix
                claimed = np.clip(claimed + total, 0.0, 1.0)
            continue
        alpha, settings = entry
        ...  # unchanged
```

Update the docstring of `apply_image` in one sentence. The type hint of `regions` becomes
`list[tuple[np.ndarray, Settings] | list[tuple[np.ndarray, Settings]]]`.

### `pipeline.py`

- `Pipeline.vocab()` loads `instructions.load_vocab(self.cfg.instructions_path)` on first call and
  keeps it in `self.instr`. `build` loads the vocabulary only when `cfg.edit` has text (and
  passes it as `instr`); otherwise `instr=None`. `process_one` calls
  `instructions.plan(adjs, self.vocab(), ...)`.
- The apply loop, when `adj_plan is not None` (the loop for `adj_plan is None` stays as it is):

```python
                for group, concept in zip(adj_plan.groups, adj_plan.feather):
                    feather_px = meta_for(concept, self.meta).feather_px
                    cells = [(build_alpha(fmask, full_hw, feather_px, guide=guide), s)
                             for cell, fmask in group if (s := region_settings.get(cell.name))]
                    if cells:
                        regions_apply.append(cells[0] if len(cells) == 1 else cells)
```

- The banding re-render damps both kinds of entry:

```python
                    regions2 = [[(a, banding.damp_settings(s, red)) for a, s in e]
                                if isinstance(e, list) else (e[0], banding.damp_settings(e[1], red))
                                for e in regions_apply]
```

- `region_meta` and the `detected` list keep using `adj_plan.cells` and `adj_plan.detected`.

### `adjust.py`, `cli.py` (F3)

`adjust.rerender` uses `pipe.vocab()`. The summary line of `autoedit adjust` counts `blocked` too:
`(<a> applied, <c> clamped, <b> blocked, <f> not found)`.

### Tests

Change in `tests/test_instructions.py` (keep every other assertion):

| Test | Change |
|---|---|
| `test_parse_rejects` | Add: `"brighten the 天空"` gives one adjustment with target `天空` (not `global`). `"darken the ;; sky"` gives no adjustment. `"brighten everything but the sky"`, `"darken the sky less"`, `"hardly brighten the sky"`, `"undo brighten the sky"` give no adjustment and a problem. `"the cool sky brighter"`, `"brighten the sky darken the grass"` and `"lighten the warm tones"` give no adjustment and a problem containing `more than one verb`. `"brighten the sky by 20%"` gives no adjustment and a problem containing `numbers`. `"darken the"` gives a problem containing `no target`. A text with the control character `\x1b` has no `\x1b` in any problem line |
| `test_parse_golden` | Add: `"brighten the sky more"` gives sky exposure +0.39. `"brighten the sky and please"` gives exactly one adjustment |
| `test_plan_splits_a_region`, `test_plan_background_and_sliver` | Use `plan.cells` and `plan.groups`. Assert that the cell masks are pairwise disjoint, that the cells of the `subject` group are in one group, and that the background cell's feather mask is the whole layer mask |
| `test_policy_applies_to_cells` | Add the same call with `owners={"x@sky": ["x", "sky"]}` (the protected concept second): `temp` is still clamped to 4 |
| `test_pipeline_instruction_on_sky_and_absent` | Add: the bottom 20 rows differ from the run without `edit` by less than 0.01 on average |
| `test_pipeline_without_instruction_is_identical` | Add: with `instructions.plan` and `instructions.report` replaced by functions that raise (restore them in `finally`), a run without `edit` still completes. And a pipeline built with `instructions_path` pointing at a file with invalid TOML runs without `edit`; with `edit` it raises |
| `test_configs_folder_without_the_file_uses_the_packaged_vocabulary` | Add: with `palettes.toml` also missing from the folder, `RunConfig().palettes_path` still points into that folder, and `paths.config_paths(folder)` contains `palettes_path` and no `instructions_path` |

New tests:

| Test | Asserts |
|---|---|
| `test_policy_inheritance` | `_policy_owners("face", meta, vocab) == ["skin", "face"]`; `"blue sky"` gives `["sky", "blue sky"]`; `"jacket"` gives `["jacket"]`; `"sky"` gives `["sky"]`. In a plan with no base region, a `face` layer and the adjustment `saturation +19`: after `policies.enforce(..., owners=plan.owners)` the saturation is 4 |
| `test_blocked_and_too_small` | An adjustment `temp +10` on a `snow` layer ends with status `blocked` and a note containing `forbids`. A target whose mask is smaller than `min_cell_coverage` ends `not_found` with a note containing `too small` |
| `test_sibling_cells_blend_without_a_seam` (in `tests/test_ops.py` or a new `tests/test_apply_groups.py`) | For a random 96 by 96 image, an alpha `a` (a blurred rectangle) and a split of it `a1 + a2 == a`: `apply_image(img, [[(a1, s), (a2, s)]], {})` equals `apply_image(img, [(a, s)], {})` within 1e-5, with `s = {"exposure": 0.4, "contrast": 10}`. With two different settings the result lies between the two single-setting results at every pixel of the overlap, within 1e-5 |
| `test_split_region_leaves_no_ring` | Pipeline level, as the Checker measured it: a flat grey 160 by 160 image, a fake segmenter with a `subject` rectangle and a `jacket` rectangle inside it, `concepts=["subject"]`, and a direct adjustment `Adjustment(target="jacket", ops={"exposure": 1e-6})` passed through `process_one(adjustments=...)`. The output differs from the run without the adjustment by less than 0.003 everywhere |

### Proving commands

`PYTHONPATH=src python3 -m pytest tests/test_instructions.py tests/test_adjust.py tests/test_policies.py tests/test_ops.py -q`,
then `PYTHONPATH=src python3 tests/run_all.py` (488 tests before this round, 0 failed).
