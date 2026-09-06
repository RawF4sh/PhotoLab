"""Rules-as-data (port of the Rust rules.rs).

`configs/rules.toml` maps per-region stats to edit-op values via expressions. Tuning is a
TOML edit, never a code change. Expressions are evaluated by a small **sandboxed AST
evaluator** (no `eval()`, no attribute access, no arbitrary calls) so an edited/untrusted
rules file can't execute code.

Rule keys ARE edit-op names (exposure, contrast, ...) consumed directly by edit/ops.py.
"""

from __future__ import annotations

import ast
import math
import re
import tomllib

from .contract import GlobalStatsModel, RegionStatsModel, Settings

# scene -> region -> op -> expression string
RuleSet = dict[str, dict[str, dict[str, str]]]


def load(path: str) -> RuleSet:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


# ── sandboxed expression evaluator ───────────────────────────────────────────

def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _iff(cond, a, b):
    return a if cond else b


def _log2(x: float) -> float:
    """Safe log2 for EV arithmetic in rules: exposure in stops is log2(target/current) — a
    linear luma difference is NOT stops (that mistake capped dark-frame lifts at ~0.3 EV).
    Floored at 1e-6 so a black region yields a large finite number the rule then clamps,
    instead of a ValueError that would drop the whole rule."""
    return math.log2(max(float(x), 1e-6))


_FUNCS = {"clamp": _clamp, "min": min, "max": max, "abs": abs, "iff": _iff,
          "log2": _log2, "log": lambda x: math.log(max(float(x), 1e-6))}

_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a**b,
}
_CMPOPS = {
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
}


def _ev(node, ctx):
    if isinstance(node, ast.Expression):
        return _ev(node.body, ctx)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError(f"disallowed constant: {node.value!r}")
    if isinstance(node, ast.Name):
        if node.id in ctx:
            return ctx[node.id]
        raise NameError(f"unknown variable in rule: {node.id!r}")
    if isinstance(node, ast.UnaryOp):
        v = _ev(node.operand, ctx)
        if isinstance(node.op, ast.USub):
            return -v
        if isinstance(node.op, ast.UAdd):
            return +v
        if isinstance(node.op, ast.Not):
            return not v
        raise ValueError("disallowed unary op")
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_ev(node.left, ctx), _ev(node.right, ctx))
    if isinstance(node, ast.BoolOp):
        vals = [_ev(v, ctx) for v in node.values]
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.Compare):
        left = _ev(node.left, ctx)
        for op, comp in zip(node.ops, node.comparators):
            right = _ev(comp, ctx)
            if type(op) not in _CMPOPS or not _CMPOPS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        return _ev(node.body, ctx) if _ev(node.test, ctx) else _ev(node.orelse, ctx)
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise ValueError("disallowed function call in rule")
        if node.func.id == "iff":
            if len(node.args) != 3:
                raise ValueError("if() requires exactly three arguments")
            return _ev(node.args[1], ctx) if _ev(node.args[0], ctx) else _ev(node.args[2], ctx)
        args = [_ev(a, ctx) for a in node.args]
        return _FUNCS[node.func.id](*args)
    raise ValueError(f"disallowed expression element: {type(node).__name__}")


def eval_expr(expr: str, ctx: dict) -> float:
    # `if(...)` -> `iff(...)` since `if` is a Python keyword and can't be a call target.
    # Rewrite only call syntax; leave Python's ternary `a if cond else b` intact.
    expr = re.sub(r"\bif\s*(?=\()", "iff", expr)
    tree = ast.parse(expr, mode="eval")
    return float(_ev(tree, ctx))


# ── apply ────────────────────────────────────────────────────────────────────

def _base_ctx(g: GlobalStatsModel, intensity: float) -> dict:
    ctx = g.model_dump()
    ctx["intensity"] = float(intensity)
    return ctx


def _region_ctx(g: GlobalStatsModel, r: RegionStatsModel, intensity: float) -> dict:
    ctx = _base_ctx(g, intensity)
    ctx.update(
        region_lum=r.lum,
        region_sat=r.sat,
        region_clip_low=r.clip_low,
        region_clip_high=r.clip_high,
        region_coverage=r.coverage,
        region_vs_global_lum=r.lum - g.mean_lum,
    )
    for key, value in r.model_dump().items():
        if key not in {"name", "lum", "sat", "clip_low", "clip_high", "coverage"}:
            ctx[f"region_{key}"] = value
    return ctx


def _eval_block(block: dict[str, str], ctx: dict) -> Settings:
    out: Settings = {}
    # Exposure spends highlight headroom before the tone/finishing ops run. Evaluate it first
    # regardless of TOML key order, and expose its intensity-scaled baseline value to later
    # rules. Looks do not change exposure beyond this scaling; later decider/intent changes
    # remain subject to the post-edit quality gate. Keep this context local to each block.
    ctx = {**ctx, "planned_exposure": 0.0}
    order = (["exposure"] if "exposure" in block else []) + [k for k in block if k != "exposure"]
    for op in order:
        expr = block[op]
        try:
            out[op] = eval_expr(expr, ctx)
            if op == "exposure":
                ctx["planned_exposure"] = out[op] * ctx["intensity"]
        except Exception as e:  # a bad single rule must not kill the photo
            import sys

            print(f"rule eval failed [{op} = {expr}]: {e}", file=sys.stderr)
    return out


def apply(
    rules: RuleSet,
    scene: str,
    g: GlobalStatsModel,
    region_stats: dict[str, RegionStatsModel],
    region_rule: dict[str, str],
    intensity: float,
) -> tuple[Settings, dict[str, Settings]]:
    """Return (global_settings, {concept_name: settings}).

    `region_rule` maps each detected concept -> the rule-block name it uses (its rule_region).
    """
    scene_rules = rules.get(scene) or rules.get("other") or {}

    global_settings = _eval_block(scene_rules.get("global", {}), _base_ctx(g, intensity))
    for op in rules.get("compat", {}).get("omit_zero_global_ops", []):
        if global_settings.get(op) == 0.0:
            global_settings.pop(op, None)

    region_settings: dict[str, Settings] = {}
    for concept, rstat in region_stats.items():
        block_name = region_rule.get(concept, concept)
        block = scene_rules.get(block_name)
        if not block:
            continue
        settings = _eval_block(block, _region_ctx(g, rstat, intensity))
        if settings:
            region_settings[concept] = settings
    return global_settings, region_settings
