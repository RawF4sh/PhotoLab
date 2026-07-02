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
import threading
import time
from dataclasses import dataclass, field

# Serializes append_labels' read-modify-write of the JSON file: two concurrent saves from the UI
# would otherwise each read the same "existing" list and the second write would drop the first's
# records (lost update). Single-process web server, so a process lock suffices.
_LOCK = threading.Lock()

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


def _labels_from_csv(csv_path: str) -> list[dict]:
    """Rebuild label records from the append-only CSV — the recovery source when the JSON is
    missing or corrupt. The CSV is only ever appended to, so it survives a crash mid-JSON-write."""
    if not os.path.isfile(csv_path):
        return []
    out: list[dict] = []
    try:
        with open(csv_path, newline="") as fh:
            for row in csv.DictReader(fh):
                image = str(row.get("image", "")).strip()
                if not image:
                    continue
                try:
                    ts = float(row.get("timestamp") or 0.0)
                except ValueError:
                    ts = 0.0
                out.append({"image": image, "decision": row.get("decision", "") or "",
                            "tags": [t for t in (row.get("tags", "") or "").split(";") if t],
                            "source": row.get("source", "") or "", "note": row.get("note", "") or "",
                            "timestamp": ts})
    except OSError:
        return []
    return out


def append_labels(records: list[dict], out_dir: str, ts: float | None = None) -> dict:
    """Append label records under `out_dir` as review-labels.csv + review-labels.json. Append-only:
    existing labels are preserved. Returns {added, total, csv, json}.

    Durable: the JSON rewrite is atomic (temp + os.replace, never a truncated file), the whole
    read-modify-write holds a lock (concurrent saves can't drop each other's records), and a
    corrupt/missing JSON is rebuilt from the append-only CSV instead of silently starting empty."""
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "review-labels.csv")
    json_path = os.path.join(out_dir, "review-labels.json")

    clean = [normalize_record(r, ts) for r in records if str(r.get("image", "")).strip()]

    with _LOCK:
        existing = _load_labels_locked(json_path, csv_path)
        merged = existing + [{"image": r.image, "decision": r.decision, "tags": r.tags,
                              "source": r.source, "note": r.note, "timestamp": r.timestamp}
                             for r in clean]
        tmp = json_path + ".part"
        with open(tmp, "w") as fh:
            json.dump(merged, fh, indent=2)
        os.replace(tmp, json_path)

        new_file = not os.path.isfile(csv_path)
        with open(csv_path, "a", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=_FIELDS)
            if new_file:
                w.writeheader()
            for r in clean:
                w.writerow(r.row())

    return {"added": len(clean), "total": len(merged), "csv": csv_path, "json": json_path}


def _load_labels_locked(json_path: str, csv_path: str) -> list[dict]:
    """JSON if readable, else recover from the CSV (crash-corrupted JSON must never mean the
    labeling history silently restarts from zero)."""
    if os.path.isfile(json_path):
        try:
            with open(json_path) as fh:
                return json.load(fh)
        except (ValueError, OSError):
            return _labels_from_csv(csv_path)
    return _labels_from_csv(csv_path)


def load_labels(out_dir: str) -> list[dict]:
    """Read back the label list — JSON first, rebuilt from the append-only CSV if it's damaged."""
    return _load_labels_locked(os.path.join(out_dir, "review-labels.json"),
                               os.path.join(out_dir, "review-labels.csv"))


def latest_by_image(records: list[dict]) -> dict[str, dict]:
    """Collapse append-only records to the newest label per image."""
    latest: dict[str, dict] = {}
    for rec in records:
        image = str(rec.get("image", "")).strip()
        if not image:
            continue
        ts = float(rec.get("timestamp") or 0.0)
        old = latest.get(image)
        if old is None or ts >= float(old.get("timestamp") or 0.0):
            latest[image] = rec
    return latest


def summary(out_dir: str) -> dict:
    """Return persisted review labels plus newest-per-image state for UI hydration."""
    records = load_labels(out_dir)
    latest = latest_by_image(records)
    keep = sum(1 for r in latest.values() if r.get("decision") == "keep")
    reject = sum(1 for r in latest.values() if r.get("decision") == "reject")
    return {
        "records": records,
        "latest": latest,
        "total_records": len(records),
        "labeled_images": len(latest),
        "keep": keep,
        "reject": reject,
        "csv": os.path.join(out_dir, "review-labels.csv"),
        "json": os.path.join(out_dir, "review-labels.json"),
    }
