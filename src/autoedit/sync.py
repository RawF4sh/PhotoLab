"""Copy the changes of one photo to the similar photos of its run (F47).

Copied: the reference's photo adjustments without its marks, and the white balance of its neutral
pick. Each similar photo keeps the rules' own decision. Its exposure moves toward the reference's
level, with the bounds of the bursts mode (F9). Every photo is re-rendered through
adjust.rerender, so a later `autoedit adjust` replays it."""

from __future__ import annotations

import glob
import os

from . import adjust, consistency, declare, paths, rules as rules_mod
from .contract import Result
from .segment.base import MARK
from .edit.ops import OP_NAMES, _TONE_KEYS, _COLOUR_KEYS

DEFAULTS = {"minutes": 10.0, "max_ev": 1.0}
RANGES = {"minutes": (0.0, 1440.0), "max_ev": (0.05, 3.0)}
CATEGORIES = ("tone", "color", "white_balance", "detail")


def _selected_ops(categories) -> set[str]:
    if categories is None:
        return set(OP_NAMES)
    if not isinstance(categories, list) or any(c not in CATEGORIES for c in categories):
        raise ValueError("categories must list tone, color, white_balance or detail")
    wb = {"temp", "tint"}
    tone = set(_TONE_KEYS) - wb
    color = set(_COLOUR_KEYS) | {"lut"}
    groups = {"tone": tone, "color": color, "white_balance": wb,
              "detail": set(OP_NAMES) - tone - color - wb}
    return set().union(*(groups[c] for c in categories))


def _subset(changes, allowed, keep_marks=False):
    result = []
    for a in changes:
        if a.target.startswith(MARK):
            if keep_marks:
                result.append(a.model_copy(update={"status": ""}))
            continue
        ops = {k: v for k, v in a.ops.items() if k in allowed}
        if ops:
            result.append(a.model_copy(update={"ops": ops, "status": ""}))
    return result


def settings(configs_dir: str | None = None, profile: str | None = None) -> dict:
    """[sync] of rules.toml, checked."""
    rules = paths.read_config(paths.config_paths(configs_dir, profile)["rules_path"])
    return rules_mod.block(rules, "sync", DEFAULTS, RANGES)


def _sidecar(target: str) -> str:
    return target if target.endswith(adjust._SIDECAR) else os.path.splitext(target)[0] + adjust._SIDECAR


def _load(sidecar: str) -> Result:
    with open(sidecar, encoding="utf-8") as fh:
        return Result.model_validate_json(fh.read())


def _taken(sidecar: str, r: Result) -> float | None:
    """Capture time of the source photo behind a sidecar (found through run.json), or None."""
    src_dir = declare._source_dir(os.path.dirname(os.path.abspath(sidecar)))
    src = os.path.join(src_dir, r.image) if src_dir else None
    return consistency.capture_time(src) if src and os.path.isfile(src) else None


def similar(ref_sidecar: str, folder: str, minutes: float) -> tuple[list[str], dict[str, int]]:
    """Sidecars in `folder` (not below it) of photos like the reference: the same scene, taken at most
    `minutes` before or after it. Returns them, and how many were left out for each reason."""
    ref = _load(ref_sidecar)
    when = _taken(ref_sidecar, ref)
    if when is None:
        raise ValueError(f"{ref.image}: its source photo has no capture time, or it was not found through "
                         "run.json; without it no photo can be told to be of the same light")
    found, left = [], {}
    for sc in sorted(glob.glob(os.path.join(glob.escape(folder), "*" + adjust._SIDECAR))):
        if os.path.samefile(sc, ref_sidecar):
            continue
        try:
            r = _load(sc)
        except (OSError, ValueError):
            why = "not readable"
        else:
            stored = r.audit.get("run_config")
            t = _taken(sc, r) if stored and r.scene == ref.scene else None
            why = ("not re-renderable" if not stored else
                   "another scene" if r.scene != ref.scene else
                   "no capture time" if t is None else
                   f"more than {minutes:g} min apart" if abs(t - when) > minutes * 60 else "")
        if why:
            left[why] = left.get(why, 0) + 1
        else:
            found.append(sc)
    return found, left


def copied(ref: Result) -> tuple[list, int]:
    """The reference's own changes: its photo adjustments without the marks; and how many marks were left out."""
    own = [a for a in ref.adjustments if a.scope == "photo"]
    keep = [a.model_copy(update={"status": ""}) for a in own if not a.target.startswith(MARK)]
    return keep, len(own) - len(keep)


def run(target: str, folder: str | None = None, dry_run: bool = False, minutes: float | None = None,
        configs_dir: str | None = None, profile: str | None = None, targets: list[str] | None = None,
        categories: list[str] | None = None, match_exposure: bool = True, **inputs) -> dict:
    """Copy the reference's changes to every similar photo and match its exposure. `inputs` are
    `reference`, `recipe_model` and `lut`, as `autoedit adjust` takes them."""
    allowed = _selected_ops(categories)
    if not isinstance(match_exposure, bool):
        raise ValueError("match_exposure must be true or false")
    selected = targets
    if selected is not None and (not isinstance(selected, list) or
                                 any(not isinstance(t, str) for t in selected)):
        raise ValueError("targets must be a list of candidate sidecar paths")
    ref_sidecar = _sidecar(target)
    if not os.path.isfile(ref_sidecar):
        raise FileNotFoundError(f"no sidecar at {ref_sidecar}")
    if folder and not os.path.isdir(folder):
        raise FileNotFoundError(f"folder not found: {folder}")
    if minutes is not None and not RANGES["minutes"][0] <= minutes <= RANGES["minutes"][1]:
        raise ValueError(f"--minutes must be from 0 to {RANGES['minutes'][1]:g}")
    st = settings(configs_dir, profile)
    ref = _load(ref_sidecar)
    targets, left = similar(ref_sidecar, folder or os.path.dirname(os.path.abspath(ref_sidecar)),
                            st["minutes"] if minutes is None else minutes)
    if selected is not None:
        candidates = {os.path.realpath(t) for t in targets}
        selected = {os.path.realpath(t) for t in selected}
        if selected - candidates:
            raise ValueError("targets includes a photo outside the similar-photo candidates; refresh the preview")
        targets = [t for t in targets if os.path.realpath(t) in selected]
    changes, marks = copied(ref)
    changes = _subset(changes, allowed)
    pick = ref.audit.get("neutral") if {"temp", "tint"} & allowed else None
    wb = [pick["temp"], pick["tint"]] if pick else None
    report = {"reference": ref.image, "copied": len(changes), "marks_left": marks, "white_balance": wb,
              "left_out": left, "dry_run": dry_run, "targets": [],
              "categories": list(CATEGORIES) if categories is None else categories,
              "match_exposure": match_exposure}
    if dry_run:
        report["targets"] = [{"sidecar": t, "image": _load(t).image, "status": "would sync"} for t in targets]
        return report
    if not targets or (not allowed and not match_exposure):
        return report
    kw = {"configs_dir": configs_dir, "profile": profile, **inputs}
    # A plan is compared with a plan: the banding gate can damp what a render records.
    anchor = (consistency.decided_from(adjust.rerender(ref_sidecar, apply=False, **kw)["result"])
              if match_exposure else None)
    group = "sync:" + os.path.splitext(ref.image)[0]
    for t in targets:
        try:
            old = _load(t)
            own = [a for a in old.adjustments if a.scope == "photo"]
            merged = _subset(own, set(OP_NAMES) - allowed, keep_marks=True) + changes
            bias, d = None, None
            if match_exposure:
                plan = adjust.rerender(t, adjustments=merged, reset=True, wb=wb, exposure_bias=0.0,
                                       apply=False, **kw)
                d = consistency.decided_from(plan["result"])
                bias = consistency.burst_biases([anchor, d], st["max_ev"], 0.0, anchor=0)[1][0]
            r = adjust.rerender(t, adjustments=merged, reset=True, wb=wb, exposure_bias=bias,
                                batch_group=group if match_exposure else None, **kw)
        except Exception as e:    # one photo fails (a corrupt RAW too), the others go on
            report["targets"].append({"sidecar": t, "status": "failed", "error": str(e)})
            continue
        from .web.photo_base import shoot_receipt
        group_receipt = shoot_receipt(r["result"])
        neutral = r["result"].audit.get("neutral")
        report["targets"].append({"sidecar": t, "image": r["image"], "status": "synced",
                                  "exposure_bias": bias if bias is not None else old.exposure_bias,
                                  "wb": [neutral["temp"], neutral["tint"]] if neutral else None,
                                  "selected_regions": r["result"].audit.get("run_config", {}).get("selected_regions", []),
                                  "wb_bias": list(r["result"].wb_bias),
                                  **({"auto_shoot": group_receipt} if group_receipt else {}),
                                  "needs_review": r["result"].needs_review,
                                  "gap_ev": consistency.level(anchor) - consistency.level(d) - bias if match_exposure else 0.0,
                                  "adjustments": r["adjustments"], "warnings": r["warnings"]})
    return report


def lines(report: dict) -> list[str]:
    """The report as terminal text."""
    n = report["copied"]
    head = f"Reference {report['reference']}: {n} change{'s' if n != 1 else ''} to copy"
    if report["marks_left"]:
        m = report["marks_left"]
        head += f"; {m} mark{'s' if m != 1 else ''} not copied (a mark is a place in that photo)"
    if report["white_balance"]:
        head += f"; its white balance (temp {report['white_balance'][0]:+.1f}, tint {report['white_balance'][1]:+.1f})"
    out = [head]
    for t in report["targets"]:
        name = os.path.basename(t.get("image") or t["sidecar"])
        if t["status"] == "would sync":
            out.append(f"  {name}: similar")
        elif t["status"] == "failed":
            out.append(f"  {name}: not synced: {t['error']}")
        else:
            s = (f"  {name}: synced, exposure {t['exposure_bias']:+.2f} EV toward the reference"
                 if report.get("match_exposure", True) else f"  {name}: selected adjustments synced; exposure matching off")
            g = t["gap_ev"]
            if abs(g) > 0.05:
                s += (f"; still {abs(g):.2f} EV {'darker' if g > 0 else 'brighter'} (a photo is not cut below "
                      "its own exposure, pushed into clipping or moved more than [sync] max_ev)")
            out.append(s)
    total, ts = len(report["targets"]), report["targets"]
    if report["dry_run"]:
        s = f"{total} similar photo{'s' if total != 1 else ''}; nothing was changed (dry run)"
    else:
        ok = sum(t["status"] == "synced" for t in ts)
        s = f"{ok} of {total} similar photos synced"
        if ok < total:
            s += f", {total - ok} failed"
    if report["left_out"]:
        s += "; left out: " + ", ".join(f"{c} {w}" for w, c in report["left_out"].items())
    out.append(s)
    return out
