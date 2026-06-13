"""Optional LLM decider (Claude, structured tool-use output).

Default mode is **refine**: the rules compute a baseline, the LLM returns *bounded deltas*
per region, we add them and re-clamp to op bounds. This is far more consistent and cheaper
to reason about than free 'propose'. Everything degrades safely to the rules baseline:
missing `anthropic`, no API key, budget/call cap exceeded, or any error → rules output.

The image never leaves as full-res: only a small thumbnail + region stats + the baseline
are sent. Off by default (cfg.decider == "llm" to enable).
"""

from __future__ import annotations

import base64
import io
import sys

import numpy as np

from ..contract import Settings
from .base import DecisionContext
from .rules_decider import RulesDecider

# Per-op absolute bounds (final clamp). Mirrors edit/ops.py ranges.
try:
    from ..edit.ops import OP_BOUNDS
except Exception:  # pragma: no cover
    OP_BOUNDS = {}

# How far the LLM may push a value off the rules baseline (refine mode).
_DELTA = {"exposure": 0.5}
_DELTA_DEFAULT = 25.0
_COST_PER_CALL_USD = 0.002  # rough Haiku-class estimate; for the soft budget cap only


def _clamp_abs(op: str, v: float) -> float:
    lo, hi = OP_BOUNDS.get(op, (-100.0, 100.0))
    return max(lo, min(hi, v))


def _delta_limit(op: str) -> float:
    return _DELTA.get(op, _DELTA_DEFAULT)


class LlmDecider:
    name = "llm"

    def __init__(self, mode="refine", model="claude-haiku-4-5-20251001", temperature=0.0,
                 max_calls=0, budget_usd=0.0):
        self.mode = mode
        self.model = model
        self.temperature = temperature
        self.max_calls = max_calls
        self.budget_usd = budget_usd
        self.calls = 0
        self.spent = 0.0
        self._base = RulesDecider()
        self._client = None  # lazy

    def decide(self, ctx: DecisionContext) -> tuple[Settings, dict[str, Settings]]:
        g, regions = self._base.decide(ctx)  # always have a safe baseline
        if self._capped():
            return g, regions
        try:
            proposal = self._call_llm(ctx, g, regions)
        except Exception as e:
            print(f"llm decider fell back to rules: {e}", file=sys.stderr)
            return g, regions
        return self._merge(g, regions, proposal)

    # ── internals ────────────────────────────────────────────────────────────

    def _capped(self) -> bool:
        if self.max_calls and self.calls >= self.max_calls:
            return True
        if self.budget_usd and self.spent >= self.budget_usd:
            return True
        return False

    def _client_or_none(self):
        if self._client is not None:
            return self._client
        from anthropic import Anthropic  # raises if extra missing → caught upstream

        self._client = Anthropic()  # reads ANTHROPIC_API_KEY
        return self._client

    def _call_llm(self, ctx, g, regions) -> dict:
        client = self._client_or_none()
        tool = {
            "name": "set_edits",
            "description": "Return per-region edit values for the photo.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "global": {"type": "object", "additionalProperties": {"type": "number"}},
                    "regions": {
                        "type": "object",
                        "additionalProperties": {
                            "type": "object",
                            "additionalProperties": {"type": "number"},
                        },
                    },
                },
                "required": ["global", "regions"],
            },
        }
        content = [{"type": "text", "text": self._prompt(ctx, g, regions)}]
        if ctx.thumb is not None:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png",
                           "data": _png_b64(ctx.thumb)},
            })
        msg = client.messages.create(
            model=self.model,
            max_tokens=1024,
            temperature=self.temperature,
            tools=[tool],
            tool_choice={"type": "tool", "name": "set_edits"},
            messages=[{"role": "user", "content": content}],
        )
        self.calls += 1
        self.spent += _COST_PER_CALL_USD
        for block in msg.content:
            if getattr(block, "type", None) == "tool_use":
                return block.input
        raise RuntimeError("LLM returned no tool_use block")

    def _prompt(self, ctx, g_settings, region_settings) -> str:
        what = "bounded DELTAS to ADD to the baseline" if self.mode == "refine" else "final VALUES"
        baseline = {
            "global": _round(g_settings),
            "regions": {k: _round(v) for k, v in region_settings.items()},
        }
        brief = ctx.brief or "clean, natural edit"
        return (
            f"You are a photo-editing assistant adjusting one image to achieve a creative brief.\n"
            f"CREATIVE BRIEF (the look the user wants): {brief}\n"
            f"Scene: {ctx.scene}. Global stats (0..1 unless noted): {_round(ctx.gstats.model_dump())}.\n"
            f"Rule baseline edits already computed: {baseline}.\n"
            f"Return {what} via the set_edits tool, using only the region names present in "
            f"the baseline, to move the image toward the brief. Edit ops: exposure (stops), "
            f"contrast, highlights, shadows, whites, blacks, clarity, vibrance, saturation, "
            f"dehaze, temp, tint (each ~[-100,100] except exposure). Keep edits tasteful and "
            f"photographic; only adjust what the brief and stats justify. Do not invent regions."
        )

    def _merge(self, g_base, regions_base, proposal) -> tuple[Settings, dict[str, Settings]]:
        g = dict(g_base)
        self._apply_proposal(g, proposal.get("global", {}))
        regions = {k: dict(v) for k, v in regions_base.items()}
        for name, ops in proposal.get("regions", {}).items():
            if name not in regions:
                continue  # never invent a region the segmenter didn't find
            self._apply_proposal(regions[name], ops)
        return g, regions

    def _apply_proposal(self, base: Settings, proposed: dict) -> None:
        for op, val in proposed.items():
            try:
                val = float(val)
            except (TypeError, ValueError):
                continue
            if self.mode == "refine":
                lim = _delta_limit(op)
                val = base.get(op, 0.0) + max(-lim, min(lim, val))
            base[op] = _clamp_abs(op, val)


def _png_b64(rgb01: np.ndarray) -> str:
    from PIL import Image as PILImage

    arr = np.clip(rgb01 * 255.0, 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    PILImage.fromarray(arr, mode="RGB").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _round(d: dict) -> dict:
    return {k: round(float(v), 3) for k, v in d.items()}
