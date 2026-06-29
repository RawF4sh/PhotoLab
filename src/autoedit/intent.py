"""Free-text look intent → bounded global edit nudges (local, offline).

Type what you want ("warm sunset, moody, vibrant") and we scan it for known photographic terms,
accumulating a small set of GLOBAL adjustments on top of the rule baseline — so intent works with
no cloud/LLM. The vocabulary is data (configs/intents.toml); add a term there, not here. The LLM
decider (when enabled) is the richer path; this is the always-available one.

Each op is clamped to a sane bound so stacking many terms can't drive an unnatural result; the
caller scales the whole nudge by intensity, and the oversaturation gate + skin-safe guard still
backstop the output.
"""

from __future__ import annotations

import tomllib

# Per-op ceiling on the accumulated nudge (EV for exposure; ~[-100,100] otherwise). Saturation is
# uncapped so "black and white" (−100) can fully desaturate.
_BOUNDS = {
    "exposure": 0.18, "temp": 24, "tint": 14, "contrast": 24, "vibrance": 22, "saturation": 100.0,
    "clarity": 20, "dehaze": 32, "shadows": 28, "highlights": 28, "whites": 18, "blacks": 18,
    "grade_shadows_temp": 16, "grade_shadows_tint": 12,
    "grade_highlights_temp": 16, "grade_highlights_tint": 12, "sharpen": 32,
}


def load_intents(path: str) -> dict[str, dict[str, float]]:
    """Load the term → {op: delta} vocabulary. Missing file → {} (intent simply does nothing)."""
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except (FileNotFoundError, OSError):
        return {}
    return {term.lower(): {k: float(v) for k, v in body.items()} for term, body in data.items()}


def parse(prompt: str | None, vocab: dict[str, dict[str, float]]) -> tuple[dict[str, float], list[str]]:
    """Scan `prompt` for vocabulary terms (substring match) and accumulate their deltas, clamped
    per op. Returns (global-nudge settings, matched term names). Empty when nothing matches."""
    if not prompt or not vocab:
        return {}, []
    text = prompt.lower()
    acc: dict[str, float] = {}
    matched: list[str] = []
    for term, deltas in vocab.items():
        if term in text:
            matched.append(term)
            for k, v in deltas.items():
                acc[k] = acc.get(k, 0.0) + v
    for k, b in ((k, _BOUNDS.get(k)) for k in list(acc)):
        if b is not None:
            acc[k] = max(-b, min(b, acc[k]))
    return acc, matched
