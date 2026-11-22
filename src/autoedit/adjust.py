"""Re-render one photo from its sidecar, with per-photo adjustments (F3)."""

from __future__ import annotations

import json
import math
import os
import pathlib

from . import emit, history, instructions, lessons, paths
from .contract import Adjustment, Result, RunConfig
from .edit.ops import OP_NAMES
from .paths import config_paths
from .pipeline import Pipeline
from .segment.base import MARK, mark_name, parse_mark

_SIDECAR = ".autoedit.json"


def parse_set(text: str) -> Adjustment:
    """"sky.exposure=-0.3" -> a photo adjustment. The target is `global` or a concept."""
    left, eq, value = text.partition("=")
    target, dot, op = left.strip().rpartition(".")
    target = " ".join(target.lower().split())
    if target.startswith(MARK):
        mark = parse_mark(target)
        if mark is None:
            raise ValueError(f"--set {text!r}: a mark is mark:area:X0,Y0,X1,Y1 or "
                             "mark:thing:X0,Y0,X1,Y1:WORD")
        target = mark_name(*mark)
    if not eq or not dot or not target or op not in OP_NAMES:
        raise ValueError(f"--set expects TARGET.OP=VALUE (for example sky.exposure=-0.3), got {text!r}")
    try:
        delta = float(value)
    except ValueError:
        raise ValueError(f"--set {text!r}: {value!r} is not a number") from None
    if not math.isfinite(delta):
        raise ValueError(f"--set {text!r}: the value must be a finite number")
    if op in instructions.WHOLE_PHOTO_OPS and target != instructions.GLOBAL:
        raise ValueError(f"--set {text!r}: {op} is for the whole photo, use global.{op}")
    return Adjustment(target=target, ops={op: delta}, scope="photo")


def _source_from_manifest(out_dir: str, image: str) -> str | None:
    try:
        with open(os.path.join(out_dir, "run.json")) as fh:
            path = os.path.join(json.load(fh)["input_dir"], image)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return path if os.path.isfile(path) else None


def rerender(target: str, sets=(), edit: str | None = None, reset: bool = False,
             source: str | None = None, configs_dir: str | None = None,
             reference: str | None = None, recipe_model: str | None = None,
             allow_cloud: bool = False, profile: str | None = None,
             mark=None, mark_kind: str = "auto", explain: bool = False, lut: str | None = None,
             neutral=None, adjustments=(), exposure_bias: float | None = None,
             batch_group: str | None = None, wb=None, apply: bool = True) -> dict:
    """Render the photo behind `target` (its exported image or its sidecar) again. Stored
    per-photo adjustments are kept unless `reset`; `sets`, `edit` and `adjustments` (Adjustment
    objects, F47) add new ones. `exposure_bias`, `batch_group` and `wb` replace what the sidecar
    stored. `apply=False` decides without pixels, writes nothing and returns {"result", "problems"}."""
    if mark is not None and not (edit and edit.strip()):
        raise ValueError("--mark needs --edit: the sentence says what changes inside the mark")
    sidecar = target if target.endswith(_SIDECAR) else os.path.splitext(target)[0] + _SIDECAR
    if not os.path.isfile(sidecar):
        raise FileNotFoundError(f"no sidecar at {sidecar}")
    with open(sidecar) as fh:
        old = Result.model_validate_json(fh.read())
    stored = old.audit.get("run_config")
    if not stored:
        raise ValueError("this sidecar does not record its run settings; "
                         "render the photo again with `autoedit run` first")
    # Consent for the cloud is given per command: what the batch was allowed does not carry over.
    stored = {**stored, "allow_cloud": bool(allow_cloud)}
    if neutral is not None:     # the pick of this photo (F34): a run setting of its own, replayed from now on
        stored["neutral"] = None if neutral == "none" else list(neutral)
        if neutral == "none":   # also the numbers --neutral-from measured once for the whole run
            stored["wb"] = None
    if wb is not None:      # F47: a white balance as numbers (the reference's pick); it replaces this photo's pick
        stored["wb"], stored["neutral"] = [float(wb[0]), float(wb[1])], None
    if stored.get("decider") == "llm" and not allow_cloud:
        raise ValueError("the run used the LLM decider; a re-render calls it again (a thumbnail "
                         "leaves the machine and your key is billed). Pass --allow-cloud to allow it")
    here = os.path.dirname(os.path.abspath(sidecar))
    # With an export subfolder the manifest and the mask cache stay in the run folder above it: take
    # the subfolder from the recorded settings and strip exactly those parts. No guessing from the
    # parent (that picked another run's folder). No key (a sidecar from before F10): this folder.
    here_parts = pathlib.Path(here).parts
    sub = pathlib.PurePosixPath((stored.get("out_subdir") or "").replace("\\", "/")).parts
    out_dir = here
    if sub and len(sub) < len(here_parts) and here_parts[-len(sub):] == sub:
        out_dir = str(pathlib.Path(*here_parts[:-len(sub)]))
    source = source or _source_from_manifest(out_dir, old.image)
    if not source or not os.path.isfile(source):
        raise FileNotFoundError(f"source photo for {old.image} not found; pass --source")
    given = {"reference": reference, "recipe_model": recipe_model, "lut": lut}
    for key, name in (old.audit.get("run_inputs") or {}).items():
        if not name:
            continue
        flag = "--" + key.replace("_", "-")
        if not given.get(key):
            raise ValueError(f"the run used {flag} {name}; pass it again")
        if os.path.basename(given[key]) != name:
            raise ValueError(f"the run used {flag} {name}, not {os.path.basename(given[key])}")
    cfg = RunConfig(**{**config_paths(configs_dir, profile), **stored,
                       **{k: v for k, v in given.items() if v},
                       "history": history.wanted(configs_dir, profile), "profile_dir": profile})
    pipe = Pipeline.build(cfg)
    pipe.use_mask_cache(out_dir)
    kept = [] if reset else [a.model_copy(update={"status": ""})
                             for a in old.adjustments if a.scope == "photo"]
    new = [parse_set(s) for s in sets] + [a.model_copy(update={"scope": "photo", "status": ""})
                                          for a in adjustments]
    problems: list[str] = []
    if edit and edit.strip():
        if mark is not None:
            m = instructions.check_mark({"box": list(mark), "kind": mark_kind, "edit": edit}, pipe.vocab())
            kind = m["kind"] if getattr(pipe.segmenter, "open_vocabulary", False) else "area"
            parsed, problems = instructions.parse(m["edit"], pipe.vocab(), (), mark=(m["box"], kind))
        else:
            parsed, problems = instructions.parse(edit, pipe.vocab(), pipe.known_concepts())
        new += [a.model_copy(update={"scope": "photo"}) for a in parsed]
    walk: list = []

    def _planned(*args):        # made here: later steps may still change the settings it reads
        walk.extend(lessons.walk(*args, pipe.vocab(), lessons.load(
            paths.config_path("lessons.toml", packaged_fallback=True))))
    out_rgb, result = pipe.process_one(source, do_apply=apply, adjustments=kept + new,
                                       exposure_bias=old.exposure_bias if exposure_bias is None else float(exposure_bias),
                                       batch_group=old.batch_group if batch_group is None else batch_group,
                                       wb_bias=tuple(old.wb_bias), on_plan=_planned if explain else None)
    if not apply:           # a plan only: nothing is written
        return {"result": result, "problems": problems}
    if result.decider != old.decider:
        raise ValueError(f"the re-render used the {result.decider} decider, the run used "
                         f"{old.decider}; nothing was written")
    warnings: list[str] = []
    if old.audit.get("config_digest") and old.audit["config_digest"] != pipe.config_digest:
        warnings.append("the config files differ from the ones the batch used; "
                        "the re-render may differ from the batch output")
    if (old.audit.get("lut") or {}).get("digest") != (result.audit.get("lut") or {}).get("digest"):
        warnings.append("the LUT file differs from the one the batch used; "
                        "the re-render may differ from the batch output")
    result.notes += [f"re-render: {w}" for w in warnings]
    result.notes += [f"instruction {p}" for p in problems]
    image_out = os.path.join(here, os.path.basename(old.audit.get("output") or (
        os.path.basename(sidecar)[:-len(_SIDECAR)] + {"tiff": ".tif", "webp": ".webp", "png": ".png"}.get(
            cfg.out_format, ".jpg"))))
    emit.write_outputs(out_rgb, result, image_out, sidecar, cfg, source)
    return {"image": image_out, "sidecar": sidecar, "problems": problems, "warnings": warnings,
            "adjustments": [a.model_dump() for a in result.adjustments], "walkthrough": walk, "result": result}


def explain_lines(walkthrough) -> list[str]:
    """The walk-through as plain lines: the mask, then each tool and what held it back."""
    out = []
    for e in walkthrough:
        m = e["mask"]
        out.append(f"{e['label']}: {m.get('name') or m['kind']}. {m['line']}")
        for t in e["tools"]:
            done = "; ".join(f"{i['part']} {i['value']:+g} ({i['state']})" for i in t["applied"]) or "not applied"
            does = f" {t['does']}" if t.get("does") else ""
            out.append(f"  {t.get('name') or t['op']}: asked {t['asked']:+g}; {done}.{does}")
        out += [f"  held back: {h}" for h in e["held"]]
    return out
