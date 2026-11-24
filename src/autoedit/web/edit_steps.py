"""Validate named photo changes and fold them into the existing editor offsets."""
from __future__ import annotations

import re
from decimal import Decimal

from .. import teach
from ..segment import refine

MAX_STEPS = 24
_ID = re.compile(r"[a-z0-9-]{1,80}")
_REQUIRED = {"id", "label", "origin", "on", "amount", "global", "regions"}


def _text(value, limit, *, required=False):
    return (isinstance(value, str) and len(value) <= limit
            and (not required or bool(value.strip()))
            and all(ord(c) >= 32 and ord(c) != 127 for c in value))


def controls(value, table):
    """Use the ordinary slider validation and drop zero offsets."""
    try:
        return teach.check_try({"sliders": value}, table)["sliders"]
    except ValueError as exc:
        raise ValueError(str(exc).replace("try.sliders", "photo_settings")) from None


def regions(value, table, *, legacy=False):
    if not isinstance(value, dict) or len(value) > refine.MAX_CONCEPTS:
        raise ValueError(f"region_settings must contain at most {refine.MAX_CONCEPTS} concepts")
    return {refine.name(name): controls({} if legacy and ops is None else ops, table)
            for name, ops in value.items()}


def _check_plan(entry, table):
    plan = entry["plan"]
    reference = entry.get("ref")
    if (entry["origin"] != "recipe" or not isinstance(reference, str)
            or not re.fullmatch(r"[0-9a-f]{32}", reference) or entry["id"] != "recipe-" + reference):
        raise ValueError("a planned change must identify its saved recipe")
    if (not isinstance(plan, dict) or not {"prior", "applied"}.issubset(plan)
            or plan.keys() - {"prior", "applied", "previous", "missing"}):
        raise ValueError("a planned change needs prior and applied photo settings")
    for key in ("prior", "applied"):
        settings = plan[key]
        if not isinstance(settings, dict) or settings.keys() - {"look", "intensity"}:
            raise ValueError("a planned change may remember only a look and strength")
        if "look" in settings and not _text(settings["look"], 200):
            raise ValueError("invalid planned look")
        if "intensity" in settings:
            n = settings["intensity"]
            if type(n) not in (int, float) or not 0 <= n <= 2:
                raise ValueError("planned strength must be between 0 and 2")
    if "previous" in plan:
        previous = plan["previous"]
        if (not isinstance(previous, dict) or "plan" in previous
                or any(previous.get(k) != entry[k] for k in ("id", "ref", "origin"))):
            raise ValueError("a planned change may restore only the same unplanned recipe")
        check([previous], table)
    if "missing" in plan:
        missing = plan["missing"]
        if not isinstance(missing, list) or len(refine.names(missing)) != len(missing):
            raise ValueError("missing recipe objects must be a list of unique concept names")


def check(value, table):
    if not isinstance(value, list) or len(value) > MAX_STEPS:
        raise ValueError(f"edit_steps must be a list of at most {MAX_STEPS} changes")
    seen, checked = set(), []
    found_plan = False
    for entry in value:
        if (not isinstance(entry, dict) or not _REQUIRED.issubset(entry)
                or entry.keys() - (_REQUIRED | {"note", "ref", "plan"})):
            raise ValueError("edit_steps entry has missing or unsupported fields")
        identifier = entry["id"]
        if not isinstance(identifier, str) or not _ID.fullmatch(identifier) or identifier in seen:
            raise ValueError("each edit_steps id must be unique and contain 1 to 80 lowercase letters, digits or hyphens")
        seen.add(identifier)
        if not _text(entry["label"], 60, required=True):
            raise ValueError("an edit_steps label requires 1 to 60 characters")
        if entry["origin"] not in ("suggested", "recipe") or type(entry["on"]) is not bool:
            raise ValueError("a change needs a suggested or recipe origin and an on/off state")
        amount = entry["amount"]
        if type(amount) not in (int, float) or not 0 <= amount <= 2:
            raise ValueError("a change amount must be between 0 and 2")
        for key, limit in (("note", 240), ("ref", 100)):
            if key in entry and not _text(entry[key], limit):
                raise ValueError(f"a change {key} must be text of at most {limit} characters")
        if "plan" in entry:
            if found_plan:
                raise ValueError("a photo can have only one planned recipe")
            _check_plan(entry, table)
            found_plan = True
        checked.append({**entry, "global": controls(entry["global"], table),
                        "regions": regions(entry["regions"], table)})
    return checked


def fold(opts, table):
    """Return plain renderer options and where enabled contributions exceeded a slider bound."""
    if "edit_steps" not in opts:
        return opts, []
    output = dict(opts)
    entries = check(output.pop("edit_steps"), table)
    if not entries:
        return output, []

    manual = controls(opts["photo_settings"], table) if opts.get("photo_settings") is not None else {}
    manual_regions = regions(opts["region_settings"], table, legacy=True) if opts.get("region_settings") is not None else {}
    sums = {}

    def add(target, offsets, amount):
        values = sums.setdefault(target, {})
        for op, offset in offsets.items():
            values[op] = values.get(op, Decimal(0)) + Decimal(str(offset)) * amount

    add("global", manual, 1)
    for target, offsets in manual_regions.items():
        add(target, offsets, 1)
    for entry in entries:
        if entry["on"] and entry["amount"]:
            amount = Decimal(str(entry["amount"]))
            add("global", entry["global"], amount)
            for target, offsets in entry["regions"].items():
                add(target, offsets, amount)

    folded, clamped = {}, []
    for target, offsets in sums.items():
        values = {}
        for op, total in offsets.items():
            low, high, _ = table[op]
            requested = float(total)
            value = min(high, max(low, requested))
            if value != requested:
                clamped.append({"target": target, "op": op, "requested": requested, "value": value})
            if value:
                values[op] = value
        if values or target in manual_regions or target == "global":
            folded[target] = values
    output["photo_settings"] = folded.pop("global")
    if len(folded) > refine.MAX_CONCEPTS:
        raise ValueError(f"combined changes exceed {refine.MAX_CONCEPTS} object targets")
    output["region_settings"] = folded
    return output, clamped


def notes(entries, clamped):
    """Describe scaled contributions separately from the replayable, folded adjustments."""
    lines = []
    for entry in entries:
        if not entry["on"]:
            continue
        amount = entry["amount"]
        targets = {"global": entry["global"], **entry["regions"]}
        shares = [f"{target}.{op} {amount * value:+g}"
                  for target, offsets in targets.items() for op, value in offsets.items() if value]
        detail = ", ".join(shares) or "no offsets"
        lines.append(f"Change: {entry['label']} ({entry['origin']}, {amount:g}×): {detail}")
    for row in clamped:
        lines.append(f"Changes: {row['target']}.{row['op']} limited from {row['requested']:g} to {row['value']:g}")
    return lines
