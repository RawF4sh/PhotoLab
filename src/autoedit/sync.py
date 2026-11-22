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

DEFAULTS = {"minutes": 10.0, "max_ev": 1.0}
RANGES = {"minutes": (0.0, 1440.0), "max_ev": (0.05, 3.0)}


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
        configs_dir: str | None = None, profile: str | None = None, **inputs) -> dict:
    """Copy the reference's changes to every similar photo and match its exposure. `inputs` are
    `reference`, `recipe_model` and `lut`, as `autoedit adjust` takes them."""
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
    changes, marks = copied(ref)
    pick = ref.audit.get("neutral")
    wb = [pick["temp"], pick["tint"]] if pick else None
    report = {"reference": ref.image, "copied": len(changes), "marks_left": marks, "white_balance": wb,
              "left_out": left, "dry_run": dry_run, "targets": []}
    if dry_run:
        report["targets"] = [{"sidecar": t, "status": "would sync"} for t in targets]
        return report
    kw = {"configs_dir": configs_dir, "profile": profile, **inputs}
    # A plan is compared with a plan: the banding gate can damp what a render records.
    anchor = consistency.decided_from(adjust.rerender(ref_sidecar, apply=False, **kw)["result"])
    group = "sync:" + os.path.splitext(ref.image)[0]
    for t in targets:
        try:
            plan = adjust.rerender(t, adjustments=changes, reset=True, wb=wb, exposure_bias=0.0,
                                   apply=False, **kw)
            d = consistency.decided_from(plan["result"])
            bias = consistency.burst_biases([anchor, d], st["max_ev"], 0.0, anchor=0)[1][0]
            r = adjust.rerender(t, adjustments=changes, reset=True, wb=wb, exposure_bias=bias,
                                batch_group=group, **kw)
        except Exception as e:    # one photo fails (a corrupt RAW too), the others go on
            report["targets"].append({"sidecar": t, "status": "failed", "error": str(e)})
            continue
        report["targets"].append({"sidecar": t, "image": r["image"], "status": "synced",
                                  "exposure_bias": bias,
                                  "gap_ev": consistency.level(anchor) - consistency.level(d) - bias,
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
            s = f"  {name}: synced, exposure {t['exposure_bias']:+.2f} EV toward the reference"
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
