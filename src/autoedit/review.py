"""Review labels (M2) — human verdicts on culled originals and edited outputs.

Labels are the training signal for the aesthetic critic (M4) and the recipe predictor (M6): a
keep/reject decision plus tags describing *why* (too dark, fake-looking, banding, good preservation,
…). Persisted append-only as both CSV (for spreadsheets / quick training loads) and JSON (for
structured tools). Nothing is moved or deleted — labels only annotate.
"""

from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import dataclass, field

# The controlled tag vocabulary the UI exposes. `keep`/`reject` are the decision; the rest are
# descriptive tags that can co-occur. Training code should treat unknown tags as ignorable.
LABELS = (
    "keep", "reject",
    "too_dark", "too_blue", "too_warm",
    "fake_looking", "changed_content", "halos", "banding",
    "good_style", "good_preservation",
)
_DECISIONS = ("keep", "reject", "")
_FIELDS = ("image", "decision", "tags", "source", "note", "timestamp")


@dataclass
class LabelRecord:
    image: str
    decision: str = ""               # keep | reject | "" (tags-only)
    tags: list[str] = field(default_factory=list)
    source: str = ""                 # cull | edit | "" — where the image came from
    note: str = ""
    timestamp: float = 0.0

    def row(self) -> dict:
        return {"image": self.image, "decision": self.decision, "tags": ";".join(self.tags),
                "source": self.source, "note": self.note, "timestamp": f"{self.timestamp:.0f}"}


def normalize_record(d: dict, ts: float | None = None) -> LabelRecord:
    """Coerce a loose dict (from the API) into a clean LabelRecord; drops unknown tags."""
    decision = str(d.get("decision", "") or "").strip().lower()
    if decision not in _DECISIONS:
        decision = ""
    tags = [t for t in (d.get("tags") or []) if t in LABELS and t not in ("keep", "reject")]
    return LabelRecord(
        image=str(d.get("image", "")).strip(),
        decision=decision,
        tags=list(dict.fromkeys(tags)),                      # de-dup, preserve order
        source=str(d.get("source", "") or "").strip().lower(),
        note=str(d.get("note", "") or "").strip(),
        timestamp=float(d.get("timestamp") or (ts if ts is not None else time.time())),
    )


def append_labels(records: list[dict], out_dir: str, ts: float | None = None) -> dict:
    """Append label records under `out_dir` as review-labels.csv + review-labels.json. Append-only:
    existing labels are preserved. Returns {added, total, csv, json}."""
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "review-labels.csv")
    json_path = os.path.join(out_dir, "review-labels.json")

    clean = [normalize_record(r, ts) for r in records if str(r.get("image", "")).strip()]

    existing: list[dict] = []
    if os.path.isfile(json_path):
        try:
            with open(json_path) as fh:
                existing = json.load(fh)
        except (ValueError, OSError):
            existing = []

    merged = existing + [{"image": r.image, "decision": r.decision, "tags": r.tags,
                          "source": r.source, "note": r.note, "timestamp": r.timestamp}
                         for r in clean]
    with open(json_path, "w") as fh:
        json.dump(merged, fh, indent=2)

    new_file = not os.path.isfile(csv_path)
    with open(csv_path, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_FIELDS)
        if new_file:
            w.writeheader()
        for r in clean:
            w.writerow(r.row())

    return {"added": len(clean), "total": len(merged), "csv": csv_path, "json": json_path}


def load_labels(out_dir: str) -> list[dict]:
    """Read back the JSON label list (empty if none)."""
    json_path = os.path.join(out_dir, "review-labels.json")
    if not os.path.isfile(json_path):
        return []
    try:
        with open(json_path) as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return []
