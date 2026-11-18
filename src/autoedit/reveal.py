"""F21 reveal check: which photos give something away. Measurement only; it never edits a pixel.

`findings` turns the named masks of a photo into boxes (shares of the frame); `report` adds the
file part (which metadata the source has and the export carries, never a value)."""

from __future__ import annotations

import html
import os
import re
from collections import Counter
from dataclasses import dataclass
from urllib.parse import quote

import numpy as np

from . import paths
from .contract import RevealFinding, RevealReport
from .edit.mask import blobs
from .imageio import metadata_report
from .segment.base import RegionMask

_GRID = 192   # two things closer than 8 pixels on the 1536 proxy count as one


@dataclass(frozen=True)
class Kind:
    name: str
    label: str
    words: tuple[str, ...]
    min_score: float
    weak: bool


@dataclass(frozen=True)
class Config:
    kinds: tuple[Kind, ...]          # kinds with off = true are left out
    min_side: float
    max_findings: int
    merge_iou: float
    subject_ratio: float
    tiles: int


def load(path: str) -> Config:
    """The reveal config at `path` (the packaged file or a merged copy), checked."""
    data = paths._load(path)
    paths._check_reveal(data, path)
    chk = data.get("check", {})
    default = float(chk.get("min_score", 0.5))
    kinds = []
    for name, body in data.get("kind", {}).items():
        if body.get("off"):
            continue
        if not body.get("words") or not body.get("label"):
            raise ValueError(f"{path}: kind.{name} needs a label and words")
        kinds.append(Kind(name, body["label"], tuple(body["words"]),
                          float(body.get("min_score", default)), bool(body.get("weak", False))))
    cfg = Config(tuple(kinds), float(chk.get("min_side", 0.006)), int(chk.get("max_findings", 30)),
                 float(chk.get("merge_iou", 0.5)), float(chk.get("subject_ratio", 0.6)),
                 int(chk.get("tiles", 1)))
    if len(words(cfg)) > 40:
        raise ValueError(f"{path}: more than 40 words in all (each word is a model call)")
    return cfg


def words(cfg: Config) -> list[str]:
    """Every word once, in file order."""
    return list(dict.fromkeys(w for k in cfg.kinds for w in k.words))


def _iou(a: list[float], b: list[float]) -> float:
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def findings(raw: list[RegionMask], cfg: Config, scene: str) -> tuple[list[RevealFinding], int]:
    by_name = {r.name: r for r in raw}
    out: list[RevealFinding] = []
    for kind in cfg.kinds:
        cands: list[dict] = []
        for word in kind.words:
            rm = by_name.get(word)
            if rm is None or rm.score < kind.min_score or not rm.mask.any():
                continue
            h, w = rm.mask.shape
            for area in blobs(rm.mask, _GRID):
                ys, xs = np.nonzero(area)
                x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
                if min(x1 - x0, y1 - y0) < cfg.min_side * max(h, w):
                    continue
                cands.append({"word": word, "score": float(rm.score), "share": float(area.sum()) / (h * w),
                              "box": [round(float(v), 4) for v in (x0 / w, y0 / h, x1 / w, y1 / h)]})
        merged: list[dict] = []
        for c in cands:
            for m in merged:
                if _iou(c["box"], m["box"]) >= cfg.merge_iou:
                    if (c["box"][2] - c["box"][0]) * (c["box"][3] - c["box"][1]) > \
                            (m["box"][2] - m["box"][0]) * (m["box"][3] - m["box"][1]):
                        m["box"], m["share"] = c["box"], c["share"]
                    if c["score"] > m["score"]:
                        m["score"], m["word"] = c["score"], c["word"]
                    break
            else:
                merged.append(dict(c))
        merged.sort(key=lambda c: -c["share"])
        subj = 0.0
        if scene == "people" and kind.name in ("people", "face") and merged:
            subj = merged[0]["share"] * cfg.subject_ratio
        out += [RevealFinding(kind=kind.name, label=kind.label, word=c["word"], score=round(c["score"], 4), box=c["box"],
                              share=round(c["share"], 6), weak=kind.weak,
                              subject=bool(subj) and c["share"] >= subj) for c in merged]
    return out[:cfg.max_findings], max(0, len(out) - cfg.max_findings)


def report(raw: list[RegionMask], cfg: Config, scene: str, can_find: bool, source: str | None,
           metadata: str, fmt: str) -> RevealReport:
    file = metadata_report(source, metadata, fmt)
    if not can_find:
        return RevealReport(picture="not_run", reason="needs SAM 3; the stub cannot find named things",
                            file=file)
    found, more = findings(raw, cfg, scene)
    return RevealReport(picture="checked", looked_for=[k.name for k in cfg.kinds], findings=found,
                        more=more, file=file)


def summary(reports: list[RevealReport]) -> dict:
    """The `reveal` block of run.json: counts only, never a value."""
    by_kind: Counter = Counter()
    file: dict[str, dict[str, int]] = {}
    for r in reports:
        for k in {f.kind for f in r.findings}:
            by_kind[k] += 1
        for group, where in r.file.items():
            c = file.setdefault(group, {"source": 0, "export": 0})
            c["source"] += bool(where.get("source"))
            c["export"] += bool(where.get("export"))
    return {"picture": "checked" if any(r.picture == "checked" for r in reports) else "not_run",
            "photos": len(reports), "with_findings": sum(1 for r in reports if r.findings),
            "by_kind": dict(by_kind), "file": file}


_GROUPS = {"gps": "GPS position", "serial": "serial numbers", "names": "names", "time": "capture time",
           "camera": "camera make and model", "description": "description text"}
_THUMB = 640


def _e(text) -> str:
    return html.escape(str(text), quote=True)


def _tell(r: RevealReport, label_of: dict[str, str]) -> list[str]:
    """One line per kind of a photo: the label, who is the subject, and 'may miss'."""
    lines = []
    for kind in dict.fromkeys(f.kind for f in r.findings):
        fs = [f for f in r.findings if f.kind == kind]
        parts = []
        if any(f.subject for f in fs):
            parts.append("the subject" + (", other people" if any(not f.subject for f in fs) else ""))
        if fs[0].weak:
            parts.append("may miss")
        n = f" ({len(fs)})" if len(fs) > 1 else ""
        lines.append((fs[0].label or label_of.get(kind, kind)) + n + (": " + ", ".join(parts) if parts else ""))
    return lines


def _thumb(src: str, dest: str) -> bool:
    try:
        from .imageio import downscale_long_edge, load_image, save_image
        save_image(downscale_long_edge(load_image(src), _THUMB).rgb, dest, "jpeg", 80)
        return True
    except Exception:
        return False


def write_page(out_dir: str, items: list[tuple[str, str | None, RevealReport]],
               cfg: Config | None = None, unchecked=(), warped=()) -> str:
    """Write reveal.html (no script, no outside resource, relative links) and the thumbnails
    under reveal/. `items` are (photo name, the picture to thumbnail, its report); `unchecked` names the
    photos made before the check was on; `warped` names the photos whose picture was straightened or cropped,
    where a box (a share of the source frame) would not fit, so none is drawn. Returns the path."""
    label_of = {k.name: k.label for k in cfg.kinds} if cfg else {}
    looked = [label_of.get(k, k) for k in dict.fromkeys(k for _, _, r in items for k in r.looked_for)]
    not_run = [n for n, _, r in items if r.picture == "not_run"]
    checked = [(n, p, r) for n, p, r in items if r.picture == "checked"]
    hits = sorted((i for i in checked if i[2].findings), key=lambda i: -len(i[2].findings))
    clean = len(checked) - len(hits)
    out = ["<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
           "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>Reveal check</title>",
           "<style>body{font:15px/1.5 system-ui,sans-serif;max-width:56rem;margin:2rem auto;padding:0 1rem}"
           ".pic{position:relative;display:inline-block;max-width:100%}.pic img{display:block;max-width:100%}"
           ".box{position:absolute;border:2px solid #e00;box-sizing:border-box}"
           "section{margin:2rem 0;border-top:1px solid #8884;padding-top:1rem}</style></head><body>",
           "<h1>Reveal check</h1>",
           f"<p>Looked for: {_e(', '.join(looked)) if looked else 'nothing (the pictures were not checked)'}. "
           "It does not read text in the picture.</p>"]
    if not_run:
        out.append(f"<p>Not checked: the picture part did not run for {len(not_run)} photos "
                   "(it needs SAM 3; the stub cannot find named things). File metadata was checked.</p>")
    if unchecked:
        out.append(f"<p>Not checked (made before the check was on): {len(unchecked)} photos: "
                   f"{_e(', '.join(unchecked))}.</p>")
    os.makedirs(os.path.join(out_dir, "reveal"), exist_ok=True)
    for n, (name, pic, r) in enumerate(hits, start=1):
        stem = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.splitext(name)[0])[:80]
        rel = f"reveal/{n:04d}-{stem}.jpg"
        out.append(f"<section><h2>{_e(name)}</h2>")
        if pic and _thumb(pic, os.path.join(out_dir, rel)):
            boxes = "" if name in warped else "".join(
                f'<div class="box" title="{_e(f.label or label_of.get(f.kind, f.kind))}" style="left:{f.box[0] * 100:.2f}%;'
                f'top:{f.box[1] * 100:.2f}%;width:{(f.box[2] - f.box[0]) * 100:.2f}%;'
                f'height:{(f.box[3] - f.box[1]) * 100:.2f}%"></div>' for f in r.findings)
            out.append(f'<div class="pic"><img src="{quote(rel)}" alt="{_e(name)}">{boxes}</div>')
            if name in warped:
                out.append("<p>Boxes not shown: the photo was straightened or cropped.</p>")
        out.append("<ul>" + "".join(f"<li>{_e(t)}</li>" for t in _tell(r, label_of)) + "</ul>")
        if r.more:
            out.append(f"<p>and {r.more} more</p>")
        out.append(_file_part(r))
        out.append("</section>")
    if checked:
        out.append(f"<p>{clean} photos: nothing on the list was found. That is not a statement that they are "
                   f"safe to share. The list: {_e(', '.join(looked))}.</p>")
    sums = summary([r for _, _, r in items])["file"]
    sent = [f"{_GROUPS.get(g, g)}: in the source of {c['source']} photos, in this export of {c['export']}"
            for g, c in sums.items() if c["source"]]
    if sent:
        out.append("<h2>File information</h2><ul>" + "".join(f"<li>{_e(s)}</li>" for s in sent) + "</ul>")
    elif items:
        out.append("<h2>File information</h2><p>" + _e("No file information was found in the source of any photo. "
                   "It looked for: " + ", ".join(_GROUPS.values()) + ".") + "</p>")
    out.append("</body></html>")
    path = os.path.join(out_dir, "reveal.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
    return path


def _file_part(r: RevealReport) -> str:
    lines = [f"{_GROUPS.get(g, g)}: in the source, " + ("in this export" if w.get("export") else "not in this export")
             for g, w in r.file.items() if w.get("source")]
    return "<p>" + _e("; ".join(lines)) + "</p>" if lines else ""
