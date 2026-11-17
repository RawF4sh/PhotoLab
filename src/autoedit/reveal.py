"""F21 reveal check: which photos give something away. Measurement only; it never edits a pixel.

`findings` turns the named masks of a photo into boxes (shares of the frame); `report` adds the
file part (which metadata the source has and the export carries, never a value)."""

from __future__ import annotations

from dataclasses import dataclass

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
        out += [RevealFinding(kind=kind.name, word=c["word"], score=round(c["score"], 4), box=c["box"],
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
