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
from .edit import glow_alpha, gradient_alpha, radial_alpha
from .edit.ops import OP_BOUNDS, OP_NAMES
from .segment.base import MARK, RegionMask, mark_name, parse_mark
from .segment.resolve import ResolvedRegions, resolve

GLOBAL = "global"
PLACE = "place:"                # a target that is a part of the frame (F18), not an object
LIGHT_TARGET = PLACE + "light"  # the one place that needs a segmented concept: the sun
LIGHT_CONCEPT = "sun"
LAYER = "instruction:"          # name of the compositional layer a place becomes
SIZE_ON_OBJECT = "size only applies to a gradient, an oval or a glow"
WHOLE_PHOTO_OPS = frozenset({"grain", "lut"})   # finishing ops (F23, F25): one value for the whole photo, never for a part
WHOLE_PHOTO = "grain is for the whole photo"
_PLACE_SHAPES = ("gradient", "oval", "oval_outside", "glow")


def is_place(target: str) -> bool:
    return target.startswith(PLACE)


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


_MARK_DEFAULTS = {"feather": 0.35, "min_side": 0.01, "min_score": 0.45, "min_coverage": 0.0001,
                  "min_cell_coverage": 0.0001, "inside_share": 0.6, "guard_min_share": 0.03,
                  "area_words": ("area", "part", "spot", "patch", "corner", "region", "zone"),
                  "pointing_words": ("thing", "one", "object", "stuff", "bit", "here", "there",
                                     "circle", "oval", "mark", "selection",
                                     "give", "gives", "turn", "turns", "let", "get", "change", "paint",
                                     "colour", "color", "tint", "tone", "hue", "feel")}


@dataclass(frozen=True)
class Vocab:
    verbs: dict[str, Settings] = field(default_factory=dict)        # verb -> op deltas at amount 1
    verb_words: tuple = ()      # ((tokens, verb name), ...), longest phrase first
    verb_place: dict[str, str] = field(default_factory=dict)  # verb -> place of a clause with no target
    places: dict[str, dict] = field(default_factory=dict)     # place name -> shape and its numbers
    place_words: tuple = ()     # ((tokens, place name), ...), longest phrase first
    amount_words: tuple = ()    # ((tokens, scale), ...), longest phrase first
    size_words: tuple = ()      # ((tokens, size factor), ...), longest phrase first
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
    marks: dict = field(default_factory=lambda: dict(_MARK_DEFAULTS))   # [marks] (F37)
    shape_guard: bool = True                                           # guard under [shapes]


def load_vocab(path: str) -> Vocab:
    """Load instructions.toml. A missing file gives an empty vocabulary (instructions then do
    nothing and say so); an unknown op name is an error, because a typo would be silent."""
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except (FileNotFoundError, OSError):
        return Vocab()
    verbs: dict[str, Settings] = {}
    verb_place: dict[str, str] = {}
    words = []
    places: dict[str, dict] = {}
    place_words = []
    for name, body in data.get("places", {}).items():
        if body.get("shape") not in _PLACE_SHAPES:
            raise ValueError(f"instructions.toml: place {name!r} has no known shape {_PLACE_SHAPES}")
        places[name] = {k: v for k, v in body.items() if k != "words"}
        place_words += [(_toks(w), name) for w in body.get("words", [name])]
    for name, body in data.get("verbs", {}).items():
        if "place" in body:
            if body["place"] not in places:
                raise ValueError(f"instructions.toml: verb {name!r} aims at an unknown place {body['place']!r}")
            verb_place[name] = str(body["place"])
        ops = {k: float(v) for k, v in body.items() if k not in ("words", "place")}
        bad = sorted(k for k in ops if k not in OP_NAMES)
        if bad:
            raise ValueError(f"instructions.toml: verb {name!r} uses unknown ops {bad}")
        verbs[name] = ops
        words += [(_toks(w), name) for w in body.get("words", [name])]
    amounts = [(_toks(w), float(v)) for w, v in data.get("amounts", {}).items()]
    sizes = [(_toks(w), float(v)) for w, v in data.get("sizes", {}).items()]
    longest = lambda item: -len(item[0])  # noqa: E731
    parser, target = data.get("parser", {}), data.get("target", {})
    marks = dict(_MARK_DEFAULTS)
    for k, v in data.get("marks", {}).items():
        marks[k] = tuple(str(w).lower() for w in v) if isinstance(v, list) else float(v)

    def alternation(words):                 # a word needs \b on both sides, a symbol does not
        return "|".join(rf"\b{re.escape(w)}\b" if w[:1].isalnum() else re.escape(w) for w in words)

    seps = [str(w).lower() for w in parser.get("separators", [])]
    joins = [str(w).lower() for w in parser.get("joiners", [])]
    segment_split = re.compile(r"[.,;!?\n]+" + ("|" + alternation(seps) if seps else ""))
    part_split = re.compile(alternation(joins)) if joins else None
    return Vocab(
        verbs=verbs,
        verb_words=tuple(sorted(words, key=longest)),
        verb_place=verb_place,
        places=places,
        place_words=tuple(sorted(place_words, key=longest)),
        amount_words=tuple(sorted(amounts, key=longest)),
        size_words=tuple(sorted(sizes, key=longest)),
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
        marks=marks,
        shape_guard=bool(data.get("shapes", {}).get("guard", True)),
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
    # A size word on an object is the one reasoned rejection a model may read ("a bigger gradient
    # for pink" names a place by a different route); it asks for nothing forbidden.
    return problem.startswith('not understood: "') or problem.startswith(f"not understood ({SIZE_ON_OBJECT}): ")


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
        if target in vocab.places:          # "top" is the place, not an object called top
            target = PLACE + target
        try:
            size = float(item.get("size", 1.0))
        except (TypeError, ValueError):
            size = 1.0
        size = min(max(size, 0.25), 4.0) if math.isfinite(size) else 1.0
        out.append(Adjustment(target=target, ops=ops, source=clause, size=size if is_place(target) else 1.0))
    return out


_TRANSLATIONS: dict[tuple, list] = {}   # (who, model, clause texts) -> the model's answer, per process


def _doubtful(adj: Adjustment, known: set[str]) -> bool:
    """A target the word list made up from leftover words: not the whole photo, not a place, not a
    known concept, and more than one word ("can you sky", "shadows trees", "give hour feel")."""
    t = adj.target
    return (t != GLOBAL and not t.startswith(PLACE) and not t.startswith(MARK)
            and t not in known and len(t.split()) > 1)


def translate_problems(problems: list[str], vocab: Vocab, known, *, decider=None, cfg=None,
                       allow_cloud: bool = True, adjustments=None):
    """The clauses of `problems` that are plainly "not understood" (see `translatable`), read by
    the cloud decider when one is active and `allow_cloud`, else by the local model when
    `cfg.translator` allows it (F20). Returns (adjustments with `via` set, the problems that
    remain). Any failure leaves the problems as they were."""
    # With `adjustments` (what the word list made of the text), a clause whose target is doubtful is
    # handed to the model as well, and the return value is (all adjustments, problems). Without a
    # model nothing is taken away: the open vocabulary of F2 still applies to such a target.
    kept = None if adjustments is None else list(adjustments)
    def done(extra, rest):
        return (extra if kept is None else kept + extra), rest
    known = set(known)
    shaky = {a.source for a in (kept or []) if _doubtful(a, known)}
    asked_problems = problems + [f'not understood: "{s}"' for s in sorted(shaky)]
    clauses = [clause_of(p) for p in asked_problems if translatable(p)]
    if not clauses or cfg is None:
        return done([], problems)
    cloud = (allow_cloud and cfg.translator != "local" and cfg.llm_translate_instructions
             and hasattr(decider, "translate"))
    if cloud:
        who, model, ask = "cloud", getattr(decider, "model", None), lambda: decider.translate(clauses, sorted(vocab.limits))
    else:
        from . import translate_local
        if cfg.translator == "off" or not translate_local.available(cfg):
            return done([], problems)
        who, model = "local", cfg.local_model
        ask = lambda: translate_local.translate(clauses, sorted(vocab.limits), sorted(vocab.places), cfg)  # noqa: E731
    # One process asks once for the same text: a web preview builds a pipeline per request. An empty
    # answer (capped, error, nothing to translate) is not kept, so it can be retried.
    key = (who, model, tuple(clauses))
    reply = _TRANSLATIONS.get(key)
    if reply is None:
        reply = ask()
        if reply:
            _TRANSLATIONS[key] = reply
    extra = [a.model_copy(update={"via": "local" if who == "local" else "cloud"})
             for a in from_translation(reply, clauses, vocab, known)]
    read = {a.source for a in extra}
    if kept is not None:      # a doubtful clause the model read replaces the word list's guess
        kept = [a for a in kept if not (a.source in shaky and a.source in read)]
    return done(extra, [p for p in problems if clause_of(p) not in read])


def _mark_target(concept: str, area: bool, mark, vocab: Vocab) -> str | None:
    """The mark as the target of one part. `concept` is what the part names ("guy", "", "thing").
    `area`: the part says "area", or its verb brings a glow. None when the name is too long.
    The name stays as typed: "tree" is not turned into the known concept "trees", because the
    user pointed at one."""
    box, kind = mark
    m = vocab.marks
    drop = {*m["pointing_words"], *m["area_words"]}
    word = " ".join(t for t in concept.split() if t not in drop)
    if len(word) > vocab.max_target_chars or len(word.split()) > vocab.max_target_words:
        return None
    if word and word not in vocab.global_words and kind == "auto" and not area:
        return mark_name("thing", box, word)
    return mark_name("area", box)


def to_mark(adj: Adjustment, mark, vocab: Vocab) -> Adjustment:
    """An adjustment read by a model, made to aim at the mark: the model's target becomes the
    name when it is a thing ("person"), and is dropped when it is a place or the whole photo."""
    if adj.target.startswith(MARK):     # the word list read it: it aims at the mark already
        return adj
    concept = "" if adj.target == GLOBAL or is_place(adj.target) else adj.target
    target = _mark_target(concept, False, mark, vocab) or mark_name("area", mark[0])
    return adj.model_copy(update={"target": target, "size": 1.0})


def mark_spec(box, feather: float) -> dict:
    """The oval of an area mark as a place for `place_alpha`. The drawn line is where the change
    is at half strength, so the oval on the screen is a true picture of what happens."""
    x0, y0, x1, y1 = box
    k = 1.0 + feather / 2.0
    return {"shape": "oval", "cx": (x0 + x1) / 2, "cy": (y0 + y1) / 2,
            "rx": (x1 - x0) / 2 / k, "ry": (y1 - y0) / 2 / k, "feather": feather}


MAX_MARK_EDIT = 300     # characters of the sentence of one mark (F37)


def check_mark(mark, vocab: Vocab) -> dict:
    """One mark of a request, checked: {"box": (x0, y0, x1, y1), "kind": "auto" | "area", "edit": text}.
    Raises ValueError with a short plain message for anything else."""
    if not isinstance(mark, dict):
        raise ValueError("a mark must be an object")
    if set(mark) - {"box", "kind", "edit"}:
        raise ValueError("a mark has only box, kind and edit")
    box = mark.get("box")
    if (not isinstance(box, (list, tuple)) or len(box) != 4
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in box)):
        raise ValueError("a mark's box must be four numbers")
    if not all(0 <= v <= 1 for v in box):       # before float(): a huge integer overflows there; NaN fails here
        raise ValueError("a mark's box must hold numbers from 0 to 1")
    x0, y0, x1, y1 = (float(v) for v in box)
    side = vocab.marks["min_side"] - 1e-9
    if x1 - x0 < side or y1 - y0 < side or x0 >= x1 or y0 >= y1:
        raise ValueError("a mark's box must have x0 < x1 and y0 < y1, and no side shorter than "
                         f"{vocab.marks['min_side']:g}")
    kind = mark.get("kind", "auto")
    if kind not in ("auto", "area"):
        raise ValueError("a mark's kind must be auto or area")
    edit = mark.get("edit")
    if not isinstance(edit, str) or not edit.strip():
        raise ValueError("a mark needs text that says what changes")
    if len(edit.strip()) > MAX_MARK_EDIT:
        raise ValueError(f"a mark's text is longer than {MAX_MARK_EDIT} characters")
    return {"box": (x0, y0, x1, y1), "kind": kind, "edit": edit.strip()}


def layer_spec(name: str, vocab: Vocab) -> dict:
    """The shape of an instruction layer: a named place, or the oval of an area mark."""
    mark = parse_mark(name)
    return mark_spec(mark[1], vocab.marks["feather"]) if mark else vocab.places[name]


def parse(text: str | None, vocab: Vocab, known=(), mark=None) -> tuple[list[Adjustment], list[str]]:
    """Text -> (adjustments, problems). A clause is taken whole or not at all: anything the
    grammar does not cover is reported in `problems` and changes nothing. With `mark`
    (`(box, kind)`, kind auto or area) every clause is about the oval the user drew (F37)."""
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
        rejected = many_amounts = many_places = place_and_object = False
        n_sizes = 0
        for chunk in (vocab.part_split.split(segment) if vocab.part_split else [segment]):
            tokens = list(_toks(chunk))
            verbs = _take(tokens, vocab.verb_words)
            amounts = _take(tokens, vocab.amount_words)
            many_amounts = many_amounts or len(amounts) > 1
            sizes = _take(tokens, vocab.size_words)
            n_sizes += len(sizes)
            places = _take(tokens, vocab.place_words)   # after the verbs: "sun glow" is a verb
            many_places = many_places or len(places) > 1
            rest = [t for t in tokens if t is not None]
            # After the verb phrases are taken: "less saturated" is a verb, a stray "less" is not.
            rejected = rejected or bool(vocab.reject & set(rest))
            words = [t for t in rest if t not in vocab.stop and t not in vocab.determiners]
            det = any(t in vocab.determiners for t in rest)
            concept = " ".join(words)
            area_word = bool(mark and set(vocab.marks["area_words"]) & set(rest))
            if places and not mark:
                # One target per clause: "the top of the frame" is a place, "the top of the
                # mountain" is two targets and is not understood.
                place_and_object = place_and_object or bool(concept and concept not in vocab.global_words)
                concept = PLACE + places[0]
            # A part of filler words only is dropped. With a mark a place is not a concept, and a
            # part that is only a place ("... and top") must stay, to be refused below.
            if verbs or concept or det or (mark and places):
                parts.append({"verbs": verbs, "amount": amounts[0] if amounts else None,
                              "size": sizes[0] if sizes else None, "concept": concept, "det": det,
                              "places": places, "area": area_word or "light" in places})
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
        elif mark and n_sizes:
            why = "a mark has the size you drew"
        elif mark and any(
                any(pl != "light" for pl in p["places"]) or p["concept"] in vocab.global_words
                or (p["verbs"] and vocab.verb_place.get(p["verbs"][0], "light") != "light") for p in parts):
            why = "the sentence names another place than the mark"
        elif n_sizes > 1:
            why = "more than one size"
        elif many_places and not mark:
            why = "more than one place"
        elif place_and_object and not mark:
            why = "a place and an object"
        elif any(ch.isdigit() for p in parts for ch in p["concept"]):
            why = "numbers are not supported"
        found: list[Adjustment] = []
        for i, part in enumerate(parts):
            if why:
                break
            verbs, amount, concept, size = part["verbs"], part["amount"], part["concept"], part["size"]
            if not verbs:   # "brighten the jacket and the sky": the second part borrows the verb
                before = [j for j in with_verb if j < i]
                donor = parts[before[-1] if before else with_verb[0]]
                verbs = donor["verbs"]
                amount = donor["amount"] if amount is None else amount
                size = donor["size"] if size is None else size
            whole = bool(WHOLE_PHOTO_OPS & set(vocab.verbs[verbs[0]]))
            if mark:
                if whole:
                    why = WHOLE_PHOTO
                    break
                area = part["area"] or vocab.verb_place.get(verbs[0]) == "light"
                if not concept and not part["det"] and not area:   # borrow the donor's name
                    after = [j for j in with_concept if j > i]
                    before = [j for j in with_concept if j < i]
                    donor = parts[after[0] if after else before[-1]] if after or before else None
                    if donor:
                        concept, area = donor["concept"], donor["area"]
                target = _mark_target(concept, area, mark, vocab)
                if target is None:
                    why = "target too long"
                    break
                adj = Adjustment(target=target, source=segment, size=1.0,
                                 ops={op: delta * (1.0 if amount is None else amount)
                                      for op, delta in vocab.verbs[verbs[0]].items()})
                if not any(f.target == adj.target and f.ops == adj.ops for f in found):
                    found.append(adj)
                continue
            if not concept and part["verbs"] and vocab.verb_place.get(part["verbs"][0]):
                concept = PLACE + vocab.verb_place[part["verbs"][0]]   # "a wider glow": the verb's place
            elif not concept and part["det"]:    # "darken the": a target was announced, none came
                why = "no target"
                break
            elif not concept and not whole:  # "brighten and warm the jacket": the first part borrows the target
                after = [j for j in with_concept if j > i]
                before = [j for j in with_concept if j < i]
                concept = parts[after[0]]["concept"] if after else (
                    parts[before[-1]]["concept"] if before else "")
            target = _canonical(concept, known, vocab)
            if target is None:
                why = "target too long"
                break
            if whole and target != GLOBAL:
                why = WHOLE_PHOTO
                break
            if size is not None and not is_place(target):
                why = SIZE_ON_OBJECT
                break
            scale = 1.0 if amount is None else amount
            adj = Adjustment(target=target, source=segment, size=1.0 if size is None else size,
                             ops={op: delta * scale for op, delta in vocab.verbs[verbs[0]].items()})
            if not any(f.target == adj.target and f.ops == adj.ops and f.size == adj.size for f in found):
                found.append(adj)
        if why:
            problems.append(f'not understood ({why}): "{segment}"')
        else:
            out += found
    return out, problems


def habit_adjustments(habit: dict, look: str, vocab: Vocab) -> dict[str, list[Adjustment]]:
    """A look's habit (F27) as typed instructions: scene -> adjustments of scope "look". Their source
    makes `_summed` clamp them together with the typed changes. A ValueError for an op without a
    limit and for a place that does not exist."""
    out: dict[str, list[Adjustment]] = {}
    for scene, targets in habit.items():
        for target, ops in targets.items():
            for op in ops:
                if op not in vocab.limits:
                    raise ValueError(f"look {look}: habit {scene}.{target} has {op}; a look can only repeat "
                                     "ops that have a limit under [limits] in instructions.toml")
            if is_place(target) and target[len(PLACE):] not in vocab.places:
                raise ValueError(f"look {look}: habit {scene}.{target}: there is no place "
                                 f"{target[len(PLACE):]!r} in instructions.toml")
            out.setdefault(scene, []).append(
                Adjustment(target=target, ops=dict(ops), source=f"look {look}", scope="look"))
    return out


def extra_concepts(adjustments: list[Adjustment], concepts: list[str]) -> list[str]:
    """Instructed concepts the run does not segment yet, in first-mention order."""
    out: list[str] = []
    for a in adjustments:
        target = LIGHT_CONCEPT if a.target == LIGHT_TARGET else a.target   # a glow needs the sun
        mark = parse_mark(target)
        if mark and mark[0] == "area":      # an area is a shape: it needs no mask
            continue
        if target != GLOBAL and not is_place(target) and target not in concepts and target not in out:
            out.append(target)
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
    layers: dict[str, Settings] = field(default_factory=dict)  # place name -> delta, one compositional layer each (F18)
    sizes: dict[str, float] = field(default_factory=dict)      # place name -> size factor of its layer (F20); the last adjustment on the place wins
    why: dict[str, str] = field(default_factory=dict)          # thing mark -> why nothing was applied: "nothing", "small" or "bigger" (F37)
    asked: dict[str, Settings] = field(default_factory=dict)   # target -> the summed change, after [limits] and the op bounds (F37)
    inside: dict[str, dict[str, float]] = field(default_factory=dict)   # thing mark -> {guarded concept: share of the mark with its own cells} (F37)
    guarded: dict[str, tuple] = field(default_factory=dict)    # layer -> (held settings, share on guarded regions, concepts); the pipeline fills it (F37)
    visual_hit: dict[str, list[str]] = field(default_factory=dict)  # displayed region -> its final cells only (F55)
    visual_asked: dict[str, Settings] = field(default_factory=dict)
    visual_limited: set[tuple[str, str]] = field(default_factory=set)

    @property
    def cells(self) -> list[RegionMask]:
        return [cell for group in self.groups for cell, _mask in group]


def place_alpha(spec: dict, full_hw: tuple[int, int], light=None, size: float = 1.0) -> np.ndarray:
    """The full-size alpha of a place (instructions.toml [places.*]). A glow needs the `light`.
    `size` scales the extent: a gradient's reach (capped at the far edge), an oval's radii, a
    glow's radius. A vignette reaches further in when bigger, so its radii shrink."""
    shape = spec["shape"]
    if shape == "gradient":
        end = min(float(spec["end"]) * size, 1.0)
        return gradient_alpha(full_hw, float(spec["angle"]), float(spec["start"]), end)
    if shape == "glow":
        return glow_alpha(full_hw, light.cx, light.cy,
                          size * max(float(spec["min_radius"]), float(spec["radius_scale"]) * light.radius))
    k = 1.0 / size if shape == "oval_outside" else size
    return radial_alpha(full_hw, float(spec["cx"]), float(spec["cy"]), float(spec["rx"]) * k, float(spec["ry"]) * k,
                        feather=float(spec["feather"]), inside=(shape == "oval"))


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


def mark_found(rm: RegionMask | None, box, shape: tuple[int, int], vocab: Vocab) -> str:
    """"" when the mask of a thing mark can be used, else why not: "nothing" (no mask, under the
    score, or empty), "small" (under the coverage), "bigger" (it lies mostly outside the box)."""
    m = vocab.marks
    if rm is None or rm.score < m["min_score"] or not rm.mask.any():
        return "nothing"
    if float(rm.mask.mean()) < m["min_coverage"]:
        return "small"
    h, w = shape
    x0, y0, x1, y1 = box
    inside = int(rm.mask[round(y0 * h):round(y1 * h), round(x0 * w):round(x1 * w)].sum())
    return "bigger" if inside < m["inside_share"] * int(rm.mask.sum()) else ""


def plan(adjustments: list[Adjustment], vocab: Vocab, raw: list[RegionMask], concepts: list[str],
         resolved: ResolvedRegions, region_settings: dict[str, Settings],
         global_settings: Settings, meta: dict[str, ConceptMeta], light=None) -> Plan:
    """Add the adjustments to one photo. `global_settings` is changed in place; the region side
    comes back as cells with their own settings. `raw` must hold the masks for the run's
    concepts and for `extra_concepts(adjustments, concepts)`. A place target is no region: it
    comes back in `layers` for the caller to blend on its own, and `light` (what the light finder
    found, or None) decides whether the place "light" exists in this photo."""
    asked, limited = _summed([a for a in adjustments if not a.resolved_only], vocab)
    visual_asked, visual_limited = _summed([a for a in adjustments if a.resolved_only], vocab)
    hit: dict[str, list[str]] = {}
    if GLOBAL in asked:
        for op, delta in asked[GLOBAL].items():
            value = global_settings.get(op, 0.0) + delta
            lo, hi = OP_BOUNDS.get(op, (-100.0, 100.0))
            global_settings[op] = min(max(value, lo), hi)
            if global_settings[op] != value:
                limited.add((GLOBAL, op))
        hit[GLOBAL] = [GLOBAL]

    extra = extra_concepts(adjustments, concepts)
    gate = {**meta, **{c: ConceptMeta(name=c, rule_region=c, min_score=vocab.min_score,
                                      min_coverage=vocab.min_coverage)
                       for c in extra if c not in meta}}
    for c in extra:                     # a thing the user pointed at may be far smaller than one found by name
        if parse_mark(c):
            gate[c] = ConceptMeta(name=c, rule_region=c, min_score=vocab.marks["min_score"],
                                  min_coverage=vocab.marks["min_coverage"])
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
    layers: dict[str, Settings] = {}
    sizes: dict[str, float] = {}
    why: dict[str, str] = {}
    inside: dict[str, dict[str, float]] = {}
    for target, delta in asked.items():
        if target == GLOBAL:
            continue
        mark = parse_mark(target)
        if mark and mark[0] == "area":       # an oval of the user's: a layer of its own, like a place
            hit[target] = [LAYER + target]
            layers[target] = dict(delta)
            sizes[target] = 1.0
            continue
        if is_place(target):
            name = target[len(PLACE):]
            hit[target] = [] if target == LIGHT_TARGET and light is None else [LAYER + name]
            if hit[target]:
                layers[name] = dict(delta)
                sizes[name] = [a.size for a in adjustments if a.target == target][-1]
            continue
        hit[target] = []
        px = min_px
        if mark:                             # a thing mark (F37): its word finds it, the oval picks which
            reason = mark_found(by_name.get(target), mark[1], (h, w), vocab)
            if reason:
                why[target] = reason
                continue
            mask = by_name[target].mask
            px = max(1, int(vocab.marks["min_cell_coverage"] * h * w))
            own_t = _policy_owners(mark[2], meta, vocab) + [target]
        else:
            mask = union.get(target)
            if mask is None:
                continue
            own_t = _policy_owners(target, meta, vocab)
        leftover: list = []     # the cells no region owns: one group, so the cuts between them lose no edit

        def lay(m, prefix, own, left):
            """Cut every cell of every group by `m`; the part of `m` no region owns joins `left`."""
            rest = m.copy()
            for gi, group in enumerate(groups):
                nxt = []
                for cell, fmask, own_c in group:
                    inside_px = cell.mask & m
                    rest &= ~cell.mask
                    if int(inside_px.sum()) < px:
                        nxt.append((cell, fmask, own_c))
                        continue
                    outside = cell.mask & ~m
                    if int(outside.sum()) < px:      # the layer covers the whole cell: keep its name
                        settings[cell.name] = _add(settings.get(cell.name, {}), delta)
                        nxt.append((cell, fmask, own_c + own))
                        hit[target].append(cell.name)
                        touched[gi] = True
                        continue
                    name = f"{prefix}@{cell.name}"
                    settings[name] = _add(settings.get(cell.name, {}), delta)
                    nxt.append((_cell(cell, name, inside_px), fmask & m, own_c + own))
                    nxt.append((_cell(cell, cell.name, outside), fmask & ~m, own_c))
                    hit[target].append(name)
                    touched[gi] = True
                group[:] = nxt
            if int(rest.sum()) >= px:                # the part of the layer no region owns
                taken = {c.name for g in groups for c, _f, _o in g} | {c.name for c, _f, _o in left}
                name = prefix if prefix not in taken else f"{prefix}@background"
                settings[name] = dict(delta)
                src = by_name.get(target)
                cell = RegionMask(name=name, mask=rest, score=src.score if src else 0.0,
                                  coverage=float(rest.mean()),
                                  instance_count=src.instance_count if src else 1)
                # Feather the whole layer, not the leftover: claim compositing keeps it off the
                # regions, and the delta then has no dip where the layer crosses a region's outline.
                left.append((cell, m, list(own)))
                hit[target].append(name)

        if mark:
            # Skin or snow the run did not find as a region keeps its limits inside the mark.
            left_of = mask.copy()
            for g, cm in meta.items():
                graw = by_name.get(g)
                if not cm.guard_global or g in resolved.union or g in own_t or graw is None or graw.score < cm.min_score:
                    continue
                part = left_of & graw.mask
                share = int(part.sum()) / int(mask.sum())
                if part.any() and share >= vocab.marks["guard_min_share"]:
                    lay(part, f"{target}/{g}", [g, *own_t], leftover)
                    left_of &= ~part
                    inside.setdefault(target, {})[g] = share
            lay(left_of, target, own_t, leftover)
            if not hit[target]:
                why[target] = "small"
        else:
            lay(mask, target, own_t, leftover)
        if leftover:
            groups.append(leftover)
            feather.append(target)
            touched.append(True)
    # A clicked object's alpha is its arbitration claim, not its original overlapping union.
    # The first groups still partition those exact resolved regions after all text/mark cuts.
    # Add visual offsets only to their own group's cells. This preserves text union semantics,
    # never creates a leftover group, and cannot cross into a higher-priority object's pixels.
    visual_hit = {target: [] for target in visual_asked}
    for gi, region in enumerate(resolved.regions):
        delta = visual_asked.get(region.name)
        if not delta:
            continue
        for cell, _mask, _owners in groups[gi]:
            settings[cell.name] = _add(settings.get(cell.name, {}), delta)
            visual_hit[region.name].append(cell.name)
        touched[gi] = True
    return Plan(groups=[[(cell, fmask) for cell, fmask, _own in group] for group in groups],
                feather=feather, settings=settings,
                owners={cell.name: own for group in groups for cell, _f, own in group},
                detected=list(ext.detected) if ext else [], hit=hit, limited=limited,
                before={name: dict(s) for name, s in settings.items()},
                present={t for t in asked if t in union}, touched=touched, layers=layers, sizes=sizes,
                why=why, asked=asked, inside=inside,
                visual_hit=visual_hit, visual_asked=visual_asked, visual_limited=visual_limited)


def report(adjustments: list[Adjustment], p: Plan, settings: dict[str, Settings],
           local_model: str = "") -> list[str]:
    """Set each adjustment's `status` for this photo and return one sidecar note per adjustment.
    Call it right after the region policies ran on `settings`. `local_model` names the model that
    read the clauses with via "local" (F20)."""
    notes: list[str] = []
    for a in adjustments:
        label = a.source if a.scope == "look" else (f'instruction "{a.source}"' if a.source else "adjustment")
        asked = ", ".join(f"{op} {delta:+.2f}" for op, delta in a.ops.items())
        where = "the whole photo" if a.target == GLOBAL else a.target
        if is_place(a.target):
            where = "the " + a.target[len(PLACE):]
        mark = parse_mark(a.target)
        if mark:
            where = "the marked area" if mark[0] == "area" else "the marked " + mark[2]
        cells = (p.visual_hit if a.resolved_only else p.hit).get(a.target, [])
        if a.resolved_only:
            where = "the selected " + a.target
        if not cells:
            a.status = "not_found"
            if a.target in p.why:
                word = mark[2]
                notes.append({
                    "nothing": f"{label}: no {word} found in the mark ({asked} not applied)",
                    "small": f"{label}: the {word} is too small in this photo ({asked} not applied)",
                    "bigger": f"{label}: the {word} is bigger than the mark; nothing was applied. "
                              "Use the oval, or type it without a mark",
                }[p.why[a.target]])
                continue
            if a.target == LIGHT_TARGET:
                notes.append(f"{label}: no light source found in this photo ({asked} not applied)")
                continue
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
        clamped = any((a.target, op) in (p.visual_limited if a.resolved_only else p.limited) for op in a.ops) or any(
            settings.get(c, {}).get(op, 0.0) != p.before.get(c, {}).get(op, 0.0)
            for c in regions for op in a.ops)
        layer = a.target[len(PLACE):] if is_place(a.target) else a.target
        if layer in p.guarded and cells == [LAYER + layer]:     # the part of the layer on guarded regions
            clamped = clamped or any(p.guarded[layer][0].get(op) != p.layers[layer].get(op) for op in a.ops)
        a.status = "clamped" if clamped else "applied"
        tail = ", held back by a limit or the region policy" if clamped else ""
        notes.append(f"{label}: applied to {where} ({asked}){tail}")
    for i, a in enumerate(adjustments):     # one note per adjustment, in order
        if is_place(a.target) and a.target[len(PLACE):] in p.sizes and a.size != p.sizes[a.target[len(PLACE):]]:
            notes[i] += f"; its size {a.size:g} is not used, the last instruction on this place sets {p.sizes[a.target[len(PLACE):]]:g}"
        if a.via:
            notes[i] += " \u2014 read by " + (f"{local_model or 'the local model'} on this computer" if a.via == "local"
                                              else "the cloud model")
    for target, parts in p.inside.items():      # a thing mark that holds skin or snow the run did not find
        notes += [f"mark: {g} inside it ({share * 100:.0f} % of the mark) keeps the {g} limits"
                  for g, share in parts.items()]
    return notes
