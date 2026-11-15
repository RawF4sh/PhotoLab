"""Measure the instruction reader (F20): the word list first, the local model for what it misses.

    PYTHONPATH=src python3 scripts/eval_translator.py [--phrasings FILE] [--model NAME] [--url URL]

Each phrasing of scripts/data/phrasings.toml is read the way a run reads it. The script reports
who understood it, whether the target and the signs of the ops match what was written down before
the model was run, and the time of every model call. Needs Ollama running locally for the model
part; without it only the word list is measured. Nothing leaves this computer.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import time
import tomllib

from autoedit import instructions, translate_local
from autoedit.concepts import load_concepts
from autoedit.contract import RunConfig
from autoedit.paths import config_path

HERE = pathlib.Path(__file__).resolve().parent


def right(expect: dict, adjs) -> bool:
    """Does some adjustment hit an accepted target with the expected sign on every named op?"""
    targets = expect.get("targets", [])
    if not targets:
        return not adjs                     # must be left out
    for a in adjs:
        if a.target not in targets:
            continue
        if any((a.ops.get(op, 0.0) > 0) != (sign == "+") or a.ops.get(op, 0.0) == 0.0
               for op, sign in expect.get("ops", {}).items()):
            continue
        size = expect.get("size")
        if size and not (a.size > 1 if size == ">1" else a.size < 1):
            continue
        return True
    return False


def show(adjs) -> str:
    return "; ".join(f"{a.target} " + " ".join(f"{k}{v:+g}" for k, v in a.ops.items())
                     + (f" size {a.size:g}" if a.size != 1.0 else "") for a in adjs) or "nothing"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--phrasings", default=str(HERE / "data" / "phrasings.toml"))
    ap.add_argument("--model")
    ap.add_argument("--url")
    args = ap.parse_args()
    with open(args.phrasings, "rb") as fh:
        phrases = tomllib.load(fh)["phrase"]
    kw = {k: v for k, v in (("local_model", args.model), ("local_url", args.url)) if v}
    cfg = RunConfig(backend="stub", translator="local", **kw)
    os.environ.pop("AUTOEDIT_NO_LOCAL_MODEL", None)
    vocab = instructions.load_vocab(config_path("instructions.toml", packaged_fallback=True))
    presets, meta = load_concepts(config_path("concepts.toml", packaged_fallback=True))
    known = set(meta) | {c for cs in presets.values() for c in cs}
    up = translate_local.available(cfg)
    print(f"model {cfg.local_model} at {cfg.local_url}: {'ready' if up else 'NOT available, word list only'}\n")

    words_seen = words_right = model_given = model_right = 0
    wrong, left_out_made_edit, times = [], [], []
    for ph in phrases:
        text = ph["text"]
        adjs, problems = instructions.parse(text, vocab, known)
        # A clause whose target the word list made up from leftover words goes to the model too
        # (instructions._doubtful), exactly as the pipeline does it.
        if up and any(instructions._doubtful(a, set(known)) for a in adjs):
            problems = problems + [f'not understood: "{a.source}"' for a in adjs if instructions._doubtful(a, set(known))]
            adjs = [a for a in adjs if not instructions._doubtful(a, set(known))]
        if adjs and not problems:
            words_seen += 1
            ok = right(ph, adjs)
            words_right += ok
            print(f"[words ] {'ok  ' if ok else 'WRONG'} {text!r} -> {show(adjs)}")
            if not ok:
                wrong.append((text, "word list", show(adjs)))
            continue
        sent = [p for p in problems if instructions.translatable(p)]
        if not sent or not up:
            made = not ph.get("targets") and bool(adjs)
            print(f"[{'kept ' if not sent else 'no model'}] {'ok  ' if not made else 'WRONG'} {text!r} -> not sent"
                  f" ({problems[0] if problems else 'nothing'})")
            if made:
                left_out_made_edit.append(text)
            continue
        instructions._TRANSLATIONS.clear()
        t0 = time.perf_counter()
        extra, rest = instructions.translate_problems(problems, vocab, known, cfg=cfg)
        times.append(time.perf_counter() - t0)
        got = adjs + extra
        ok = right(ph, got)
        model_given += 1
        model_right += ok
        print(f"[model ] {'ok  ' if ok else 'WRONG'} {text!r} -> {show(got)}  ({times[-1]:.1f}s)")
        if not ok:
            wrong.append((text, "model", show(got)))
        if not ph.get("targets") and got:
            left_out_made_edit.append(text)

    print("\n== summary ==")
    print(f"phrasings: {len(phrases)}")
    print(f"word list understood: {words_seen}, right on target and signs: {words_right}")
    pct = 100 * model_right / model_given if model_given else 0.0
    print(f"given to the model: {model_given}, right on target and signs: {model_right} ({pct:.0f} %)")
    print("wrong answers:")
    for text, who, got in wrong:
        print(f"  [{who}] {text!r} -> {got}")
    print(f"phrasings that must be left out but became an edit: {left_out_made_edit or 'none'}")
    if times:
        later = times[1:]
        print(f"model calls: {len(times)}, first {times[0]:.1f} s"
              + (f", later mean {sum(later) / len(later):.1f} s, max {max(later):.1f} s" if later else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
