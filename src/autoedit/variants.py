"""Variant engine (M7) — generate several non-generative recipes per photo, score each with the
post-edit safety gate + (optionally) the aesthetic critic, auto-pick the best, and flag uncertain
cases for human review.

Pure logic here: the variant specs, the per-variant scorer, and the pick/uncertainty rule. The
rendering loop lives in web/service.py (it owns the resident pipeline), so this module stays light
and unit-testable without building a pipeline.
"""

from __future__ import annotations

import hashlib
import random

import numpy as np

# A small, diverse set of recipes spanning the look space. Each spec's non-`name` keys are ordinary
# edit options (style / palette / prompt / intensity) — never anything generative.
DEFAULT_VARIANTS = [
    {"name": "Natural", "style": "natural"},
    {"name": "Dramatic cool", "style": "dramatic", "palette": "cool_blue"},
    {"name": "Warm sunset", "style": "natural", "prompt": "warm sunset, golden", "palette": "warm_gold"},
    {"name": "Editorial punch", "style": "editorial", "prompt": "vibrant", "intensity": 1.0},
    {"name": "Muted film", "style": "warm_film", "palette": "muted_film", "prompt": "muted"},
]

# How close the top two combined scores can be before we call it a toss-up and send it to review.
_UNCERTAIN_MARGIN = 0.04


SURPRISE = "surprise"     # F26: the word after --look; never a run setting, so never in a sidecar
SURPRISE_COUNT = 6
_SEED_END = 2 ** 32


def surprise_seed(seed=None) -> int:
    """The seed, checked. None draws one below a million, so every answer can be repeated."""
    if seed is None:
        return random.randrange(1_000_000)
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < _SEED_END:
        raise ValueError(f"seed must be a whole number from 0 to {_SEED_END - 1}")
    return seed


def surprise_looks(names, seed: int, count: int = SURPRISE_COUNT, skip=()) -> list[str]:
    """Up to `count` different names, in the order the seed gives them, without those in `skip`.
    A name's place is a digest of the seed and the name: the same on every machine and Python
    version, and a look that is added, removed or skipped leaves the order of the others alone."""
    def place(name: str):
        return hashlib.blake2b(f"{seed}:{name}".encode("utf-8"), digest_size=8).digest(), name
    return sorted((n for n in names if n not in skip), key=place)[:count]


def surprise_for(cfg, seed: int) -> list[str]:
    """The looks the seed picks for a run config: out of its library, without the look it has now."""
    from .looks import load as load_looks
    from .styles import load_styles, style_for

    library = load_looks(cfg.looks_path)
    now = cfg.look if cfg.look in library else style_for(cfg.style, load_styles(cfg.styles_path)).look
    return surprise_looks(library, seed, skip=(now,))


def spec_opts(spec: dict) -> dict:
    """The edit-option subset of a variant spec (drops the display name)."""
    return {k: v for k, v in spec.items() if k != "name"}


def resolve_specs(specs: list[dict] | None) -> list[dict]:
    """Caller-supplied specs, or the default set. Each must have a `name`."""
    out = []
    for i, s in enumerate(specs or DEFAULT_VARIANTS):
        s = dict(s)
        s.setdefault("name", f"Variant {i + 1}")
        out.append(s)
    return out


def score_variant(result, edited_rgb: np.ndarray, aesthetic_model=None) -> dict:
    """Combined score for one rendered variant: post-edit quality gate + optional aesthetic critic,
    with a penalty when the safety gate flags review. Higher is better."""
    quality = float(getattr(result, "quality_score", 1.0) or 1.0)
    needs_review = bool(getattr(result, "needs_review", False))
    aesthetic = None
    combined = quality

    if aesthetic_model is not None:
        try:
            import os
            import tempfile

            from . import aesthetic as aes, curate
            from .imageio import save_image

            fh = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
            fh.close()
            try:
                save_image(np.clip(edited_rgb, 0.0, 1.0), fh.name, "jpeg", 90)
                aesthetic = float(aes.predict(curate.image_features(fh.name), aesthetic_model))
            finally:
                os.unlink(fh.name)
            combined = 0.55 * aesthetic + 0.45 * quality
        except Exception:
            aesthetic = None

    if needs_review:
        combined -= 0.15
    return {"quality": round(quality, 4),
            "aesthetic": round(aesthetic, 4) if aesthetic is not None else None,
            "score": round(float(combined), 4), "needs_review": needs_review}


def is_uncertain(sorted_scored: list[dict]) -> bool:
    """True when the auto-pick should go to human review: the winner is gate-flagged, or the top two
    are within a hair of each other (no clear winner)."""
    if not sorted_scored:
        return False
    if sorted_scored[0].get("needs_review"):
        return True
    if len(sorted_scored) >= 2:
        return (sorted_scored[0]["score"] - sorted_scored[1]["score"]) < _UNCERTAIN_MARGIN
    return False
