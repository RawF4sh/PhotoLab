"""Plain-language region instructions and directed adjustments (F2, F3).

`parse` turns text such as "brighten the jacket, darken the sky slightly" into `Adjustment`s
using the vocabulary in configs/instructions.toml. No LLM, no network. `plan` adds them to one
photo: every instructed concept is a layer (its union mask plus a delta) that splits the
arbitrated regions into cells, so no pixel is edited by two regions and each cell keeps the
settings of the region it came from. `report` says, per adjustment, what happened on this photo.
"""

from __future__ import annotations

import math
import re
import tomllib
import unicodedata
from dataclasses import dataclass, field

import numpy as np

from .concepts import ConceptMeta
from .contract import Adjustment, DetectedConcept, Settings
from .edit.ops import OP_BOUNDS, OP_NAMES
from .segment.base import RegionMask
from .segment.resolve import ResolvedRegions, resolve

GLOBAL = "global"


def _toks(text: str) -> tuple[str, ...]:
    """Words of a text: letters, marks and digits of any script, with an inner apostrophe or
    hyphen ("don't", "t-shirt"). NFC first, and a typographic apostrophe must not hide a negation."""
    text = unicodedata.normalize("NFC", text.lower().replace("’", "'"))
    out, cur = [], []
    for ch in text + " ":
        if unicodedata.category(ch)[0] in "LMN" or (ch in "'-" and cur):
            cur.append(ch)
        elif cur:
            out.append("".join(cur).rstrip("'-"))
            cur = []
    return tuple(t for t in out if t)


@dataclass(frozen=True)
class Vocab:
    verbs: dict[str, Settings] = field(default_factory=dict)        # verb -> op deltas at amount 1
    verb_words: tuple = ()      # ((tokens, verb name), ...), longest phrase first
    amount_words: tuple = ()    # ((tokens, scale), ...), longest phrase first
    limits: Settings = field(default_factory=dict)
    stop: frozenset = frozenset()
    determiners: frozenset = frozenset()
    reject: frozenset = frozenset()
    global_words: frozenset = frozenset()
    policy: dict[str, str] = field(default_factory=dict)
    segment_split: re.Pattern = re.compile(r"[.,;!?\n]+")
    part_split: re.Pattern | None = None
    max_target_words: int = 4
    max_target_chars: int = 40
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

    def alternation(words):                 # a word needs \b on both sides, a symbol does not
        return "|".join(rf"\b{re.escape(w)}\b" if w[:1].isalnum() else re.escape(w) for w in words)

    seps = [str(w).lower() for w in parser.get("separators", [])]
    joins = [str(w).lower() for w in parser.get("joiners", [])]
    segment_split = re.compile(r"[.,;!?\n]+" + ("|" + alternation(seps) if seps else ""))
    part_split = re.compile(alternation(joins)) if joins else None
    return Vocab(
        verbs=verbs,
        verb_words=tuple(sorted(words, key=longest)),
        amount_words=tuple(sorted(amounts, key=longest)),
        limits={k: float(v) for k, v in data.get("limits", {}).items()},
        stop=frozenset(parser.get("stop", [])),
        determiners=frozenset(parser.get("determiners", [])),
        reject=frozenset(parser.get("reject", [])),
        global_words=frozenset(parser.get("global_words", [GLOBAL])),
        policy={str(k): str(v) for k, v in data.get("policy", {}).items()},
        segment_split=segment_split,
        part_split=part_split,
        max_target_words=int(parser.get("max_target_words", 4)),
        max_target_chars=int(parser.get("max_target_chars", 40)),
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
    if len(concept) > vocab.max_target_chars or len(concept.split()) > vocab.max_target_words:
        return None
    variants = [concept, concept + "s"]
    if concept.endswith("es"):
        variants.append(concept[:-2])
    if concept.endswith("s"):
        variants.append(concept[:-1])
    return next((v for v in variants if v in known), concept)


def clause_of(problem: str) -> str:
    """The instruction clause inside a problem line (`not understood ...: "<clause>"`)."""
    start, end = problem.find('"'), problem.rfind('"')
    return problem[start + 1:end] if 0 <= start < end else ""


def translatable(problem: str) -> bool:
    """Only a clause the parser plainly did not understand (`not understood: "<clause>"`) may go to
    the LLM. One it rejected on purpose (negation, exclusion, two verbs, ...) carries a reason in
    brackets; a model must not turn "don't brighten the face" into "brighten the face"."""
    return problem.startswith('not understood: "')


def from_translation(items: list[dict], clauses: list[str], vocab: Vocab, known) -> list[Adjustment]:
    """Validate what the LLM returned for rejected clauses. Anything odd is dropped: an unknown
    clause number, a target that fails the same checks as a parsed one, an op without an
    instruction limit, a value that is not a finite number. The deltas are clamped later, with all
    other instruction deltas, by the limits in instructions.toml."""
    out: list[Adjustment] = []
    known = set(known)
    for item in items:
        try:
            index = item["clause"]
            if type(index) is not int or not 0 <= index < len(clauses):   # -1 would pick the last one
                continue
            clause = clauses[index]
            target = _canonical(" ".join(_toks(str(item["target"]))), known, vocab)
            ops = {str(e["op"]): float(e["value"]) for e in item["edits"]
                   if str(e["op"]) in vocab.limits and math.isfinite(float(e["value"]))}
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if target is None or not ops or any(ch.isdigit() for ch in target):
            continue
        out.append(Adjustment(target=target, ops=ops, source=clause))
    return out


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
        parts = []
        rejected = many_amounts = False
        for chunk in (vocab.part_split.split(segment) if vocab.part_split else [segment]):
            tokens = list(_toks(chunk))
            verbs = _take(tokens, vocab.verb_words)
            amounts = _take(tokens, vocab.amount_words)
            many_amounts = many_amounts or len(amounts) > 1
            rest = [t for t in tokens if t is not None]
            # After the verb phrases are taken: "less saturated" is a verb, a stray "less" is not.
            rejected = rejected or bool(vocab.reject & set(rest))
            words = [t for t in rest if t not in vocab.stop and t not in vocab.determiners]
            det = any(t in vocab.determiners for t in rest)
            if verbs or words or det:       # a part of filler words only is dropped
                parts.append({"verbs": verbs, "amount": amounts[0] if amounts else None,
                              "concept": " ".join(words), "det": det})
        if rejected:
            problems.append(f'not understood (negation or exclusion): "{segment}"')
            continue
        with_verb = [i for i, p in enumerate(parts) if p["verbs"]]
        with_concept = [i for i, p in enumerate(parts) if p["concept"]]
        why = None
        if not with_verb:
            problems.append(f'not understood: "{segment}"')
            continue
        if any(len(p["verbs"]) > 1 for p in parts):
            why = "more than one verb"
        elif many_amounts:
            why = "more than one amount"
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
            adj = Adjustment(target=target, source=segment,
                             ops={op: delta * scale for op, delta in vocab.verbs[verbs[0]].items()})
            if not any(f.target == adj.target and f.ops == adj.ops for f in found):
                found.append(adj)
        if why:
            problems.append(f'not understood ({why}): "{segment}"')
        else:
            out += found
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
    touched: list[bool]                     # per group: a layer reached one of its cells

    @property
    def cells(self) -> list[RegionMask]:
        return [cell for group in self.groups for cell, _mask in group]


def _policy_owners(target: str, meta: dict[str, ConceptMeta], vocab: Vocab) -> list[str]:
    """The concepts whose region policy a target must obey: itself, and before it every protected
    concept one of its words names ("skin tones" -> skin, "snow field" -> snow and grass)."""
    owners: list[str] = []
    for word in [target, *target.split()]:
        for cand in (word, word[:-1] if word.endswith("s") else word + "s"):
            like = vocab.policy.get(cand) or (cand if cand in meta else None)
            if like and like != target and like not in owners:
                owners.append(like)
                break
    return owners + [target]


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
                if not math.isfinite(float(delta)):
                    continue
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
    # One group per base region. The cells of a group partition the region; apply blends them in
    # parallel, so splitting a region never weakens the region's own edit along the cut.
    groups: list[list[tuple[RegionMask, np.ndarray, list[str]]]] = [
        [(r, r.mask, [r.name])] for r in resolved.regions]
    feather = [r.name for r in resolved.regions]
    touched = [False] * len(groups)
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
        for gi, group in enumerate(groups):
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
                    touched[gi] = True
                    continue
                name = f"{target}@{cell.name}"
                settings[name] = _add(settings.get(cell.name, {}), delta)
                nxt.append((_cell(cell, name, inside), fmask & mask, own + own_t))
                nxt.append((_cell(cell, cell.name, outside), fmask & ~mask, own))
                hit[target].append(name)
                touched[gi] = True
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
            touched.append(True)
            hit[target].append(name)
    return Plan(groups=[[(cell, fmask) for cell, fmask, _own in group] for group in groups],
                feather=feather, settings=settings,
                owners={cell.name: own for group in groups for cell, _f, own in group},
                detected=list(ext.detected) if ext else [], hit=hit, limited=limited,
                before={name: dict(s) for name, s in settings.items()},
                present={t for t in asked if t in union}, touched=touched)


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
