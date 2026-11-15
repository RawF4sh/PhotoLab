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


def _clamp_abs(op: str, v: float) -> float:
    lo, hi = OP_BOUNDS.get(op, (-100.0, 100.0))
    return max(lo, min(hi, v))


def _edits_tool(region_names: list[str]) -> dict:
    """The strict tool the model answers with. Ops and region names are enums, so the model
    cannot invent either."""
    edit = {"type": "object", "additionalProperties": False, "required": ["op", "value"],
            "properties": {"op": {"type": "string", "enum": sorted(OP_BOUNDS)},
                           "value": {"type": "number"}}}
    region = {"type": "object", "additionalProperties": False, "required": ["name", "edits"],
              "properties": {"name": {"type": "string", "enum": sorted(region_names)},
                             "edits": {"type": "array", "items": edit}}}
    props = {"global": {"type": "array", "items": edit}}
    if region_names:
        props["regions"] = {"type": "array", "items": region}
    return {"name": "set_edits", "description": "Return the edit values for the photo.",
            "strict": True,
            "input_schema": {"type": "object", "additionalProperties": False,
                             "required": sorted(props), "properties": props}}


def _from_pairs(data: dict) -> dict:
    """The tool's lists of {op, value} pairs, back in the shape `_merge` reads."""
    import math

    def finite(v):    # nan would pass the clamp as the largest step
        try:
            return math.isfinite(float(v))
        except (TypeError, ValueError):
            return False

    def ops(pairs):
        return {p["op"]: p["value"] for p in pairs or []
                if isinstance(p, dict) and "op" in p and finite(p.get("value"))}
    return {"global": ops(data.get("global")),
            "regions": {r["name"]: ops(r.get("edits")) for r in data.get("regions") or []
                        if isinstance(r, dict) and "name" in r}}


def _delta_limit(op: str) -> float:
    return _DELTA.get(op, _DELTA_DEFAULT)


class LlmDecider:
    name = "llm"

    def __init__(self, mode="refine", model="claude-haiku-5-5", temperature=0.0,
                 max_calls=0, budget_usd=0.0, api_key=None,
                 input_usd_per_mtok=0.10, output_usd_per_mtok=0.50):
        self.mode = mode
        self.model = model
        self.temperature = temperature  # accepted for older run files; current models reject it
        self.input_usd_per_mtok = input_usd_per_mtok
        self.output_usd_per_mtok = output_usd_per_mtok
        self.max_calls = max_calls
        self.budget_usd = budget_usd
        self.api_key = api_key  # BYO-key; falls back to the SDK's env lookup if None
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

        # explicit BYO-key if provided, else the SDK reads ANTHROPIC_API_KEY
        self._client = Anthropic(api_key=self.api_key) if self.api_key else Anthropic()
        return self._client

    def _call_llm(self, ctx, g, regions) -> dict:
        client = self._client_or_none()
        content = [{"type": "text", "text": self._prompt(ctx, g, regions)}]
        if ctx.thumb is not None:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png",
                           "data": _png_b64(ctx.thumb)},
            })
        msg = client.messages.create(
            model=self.model,
            max_tokens=4096,           # thinking is on by default and counts toward the cap
            tools=[_edits_tool(list(regions))],
            tool_choice={"type": "auto"},   # a forced call is rejected by several current models
            messages=[{"role": "user", "content": content}],
        )
        self.calls += 1
        self.spent += self._cost(msg)
        if getattr(msg, "stop_reason", None) == "refusal":
            raise RuntimeError("the model declined the request")
        for block in msg.content:
            if getattr(block, "type", None) == "tool_use":
                return _from_pairs(block.input)
        raise RuntimeError("LLM returned no tool_use block")

    def _cost(self, msg) -> float:
        usage = getattr(msg, "usage", None)
        tokens_in = float(getattr(usage, "input_tokens", 0) or 0)
        tokens_out = float(getattr(usage, "output_tokens", 0) or 0)
        return (tokens_in * self.input_usd_per_mtok + tokens_out * self.output_usd_per_mtok) / 1e6

    def translate(self, clauses: list[str], ops: list[str]) -> list[dict]:
        """Ask the model to turn instruction clauses the local parser did not understand into
        [{clause, target, edits: [{op, value}]}]. Text only: no image is sent. Returns [] when
        capped or on any error. The caller validates and clamps everything."""
        if not clauses or self._capped():
            return []
        tool = {
            "name": "set_adjustments", "strict": True,
            "description": "Return the adjustments the instructions ask for.",
            "input_schema": {"type": "object", "additionalProperties": False, "required": ["adjustments"],
                             "properties": {"adjustments": {"type": "array", "items": {
                                 "type": "object", "additionalProperties": False,
                                 "required": ["clause", "target", "edits"],
                                 "properties": {
                                     "clause": {"type": "integer"},
                                     "target": {"type": "string"},
                                     "edits": {"type": "array", "items": {
                                         "type": "object", "additionalProperties": False,
                                         "required": ["op", "value"],
                                         "properties": {"op": {"type": "string", "enum": sorted(ops)},
                                                        "value": {"type": "number"}}}}}}}}}}
        numbered = "\n".join(f"{i}: {c}" for i, c in enumerate(clauses))
        prompt = (
            "A photo editor received instructions it could not parse. Turn each one into "
            "adjustments, or leave it out when it asks for nothing, forbids something, or cannot be "
            "expressed.\n"
            "An adjustment names a target (a short noun phrase for the thing in the photo, in "
            "lower case, or the word global for the whole photo) and edits. Edit ops and their "
            "usual size: exposure in stops (a normal step is 0.3), temp (warmer is positive, a "
            "normal step is 10), saturation (12), clarity (12), contrast (8). Use only the ops the "
            "tool allows. An instruction that excludes something (\"everything but the sky\") "
            "has no safe translation: leave it out.\n"
            f"Instructions, numbered:\n{numbered}\n"
            "Call the set_adjustments tool exactly once.")
        try:
            msg = self._client_or_none().messages.create(
                model=self.model, max_tokens=4096, tools=[tool], tool_choice={"type": "auto"},
                messages=[{"role": "user", "content": prompt}])
            self.calls += 1
            self.spent += self._cost(msg)
            if getattr(msg, "stop_reason", None) == "refusal":
                return []
            for block in msg.content:
                if getattr(block, "type", None) == "tool_use":
                    return list(block.input.get("adjustments") or [])
        except Exception as e:
            print(f"llm instruction translation skipped: {e}", file=sys.stderr)
        return []

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
            f"the baseline, to move the image toward the brief. Edit ops: "
            f"{', '.join(sorted(OP_BOUNDS))} (each ~[-100,100] except exposure, in stops). Keep edits "
            f"tasteful and photographic; only adjust what the brief and stats justify. Do not "
            f"invent regions. Call the set_edits tool exactly once."
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
            if OP_BOUNDS and op not in OP_BOUNDS:
                continue  # hallucinated/unknown op name — keep it out of settings AND the sidecar
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
