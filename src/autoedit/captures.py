"""Deterministic, copy-free capture discovery and RAW/JPEG source selection.

Curation manifests describe renamed local copies. Original paths in those manifests
are identifiers only: discovery never opens the original Desktop photographs.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from PIL import Image as PILImage

from .imageio import _SUPPORTED, is_raw

EXTENSIONS = frozenset(_SUPPORTED)


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def stable_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def metadata(path: Path) -> dict:
    out = {"camera": "", "captured_at": "", "exposure_bias": None,
           "camera_serial": "", "capture_unique_id": "", "capture_subseconds": ""}
    try:
        with PILImage.open(path) as im:
            exif = im.getexif()
            sub = exif.get_ifd(34665)
            out["camera"] = str(exif.get(272, ""))
            out["camera_serial"] = str(sub.get(42033, "")).strip()
            out["capture_unique_id"] = str(sub.get(42016, "")).strip()
            subseconds = str(sub.get(37521, "")).strip()
            if subseconds.isdigit():
                out["capture_subseconds"] = subseconds
            stamp = sub.get(36867, exif.get(306, ""))
            if stamp:
                out["captured_at"] = datetime.strptime(str(stamp), "%Y:%m:%d %H:%M:%S").isoformat()
            bias = sub.get(37380)
            if bias is not None:
                out["exposure_bias"] = float(bias)
    except Exception:
        pass  # corrupt photographs remain selected and fail individually during decode
    return out


def _provenance(root: Path, manifest: str | None) -> tuple[dict, Path | None]:
    candidates = [Path(manifest)] if manifest else [
        base / name for base in (root, root.parent)
        for name in ("paired-versions.json", "candidates.json")]
    for path in candidates:
        if not path.is_file():
            continue
        data = json.loads(path.read_text())
        mapping = {}
        for item in data.get("items", []):
            copies = item.get("raw_copies", []) + item.get("jpeg_copies", [])
            if item.get("copied_path"):
                copies.append(item["copied_path"])
            original = Path(item.get("source_path", ""))
            if not original.name:
                continue
            identity = str(original.with_suffix("")).casefold()
            for local in copies:
                p = Path(local)
                if not p.is_absolute():
                    p = path.parent / p
                mapping[str(p.resolve())] = {
                    "identity": identity, "scene": str(original.parent),
                    "category": item.get("bucket", ""), "original_name": original.stem,
                }
        if any(Path(p).is_relative_to(root) for p in mapping):
            return mapping, path
    return {}, None


def _merge_generic_duplicates(groups: dict, digest) -> tuple[dict, dict]:
    """Coalesce proven duplicate files or unambiguous, precise RAW/JPEG identities.

    A directory is the conservative event boundary. Distinct curated identities remain
    authoritative even when their bytes happen to match. Camera model + whole-second
    timestamps alone never merge burst frames. Metadata pairing requires a capture ID,
    or body serial + subsecond time, with exactly one unpaired RAW and JPEG group.
    """
    parents = {key: key for key in groups}
    reasons = {}

    def find(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    def authoritative(key):
        return any(info.get("identity") for _, info, _ in groups[key])

    def merge(a, b, reason):
        a, b = find(a), find(b)
        if a == b:
            return
        aa, ba = authoritative(a), authoritative(b)
        if aa and ba:
            return
        winner = a if aa else b if ba else min(a, b)
        loser = b if winner == a else a
        parents[loser] = winner
        for path, _, _ in groups[loser]:
            reasons[str(path)] = reason
        groups[winner].extend(groups[loser])

    # Size buckets avoid hashing every file when callers only need a quick inventory.
    buckets = defaultdict(list)
    for key, members in groups.items():
        for path, _, _ in members:
            buckets[(str(path.parent), path.stat().st_size)].append((key, path))
    for members in buckets.values():
        if len(members) < 2:
            continue
        hashes = {}
        for key, path in members:
            value = digest(path)
            if value in hashes:
                merge(hashes[value], key, "exact-content duplicate")
            else:
                hashes[value] = key

    precise = defaultdict(lambda: {"raw": set(), "jpeg": set()})
    for key in groups:
        if find(key) != key or authoritative(key):
            continue
        members = groups[key]
        kinds = {"raw" if is_raw(str(p)) else "jpeg" if p.suffix.lower() in {".jpg", ".jpeg"}
                 else "other" for p, _, _ in members}
        if len(kinds) != 1 or "other" in kinds:
            continue
        kind = next(iter(kinds))
        keys = set()
        for path, _, meta in members:
            if not meta["camera"] or not meta["captured_at"]:
                continue
            uid = meta.get("capture_unique_id", "").casefold()
            serial, subsec = meta.get("camera_serial", ""), meta.get("capture_subseconds", "")
            if re.fullmatch(r"[0-9a-f]{32}", uid) and set(uid) != {"0"}:
                extra = ("capture-id", uid)
            elif serial.casefold() not in ("", "none", "unknown", "0") and len(subsec) >= 2:
                # Numerically equivalent fractional timestamps have the same key.
                extra = ("subsecond", serial, subsec.rstrip("0") or "0")
            else:
                continue
            keys.add((str(path.parent), meta["camera"], meta["captured_at"], extra))
        if len(keys) == 1:
            precise[next(iter(keys))][kind].add(key)
    for candidates in precise.values():
        if len(candidates["raw"]) == 1 and len(candidates["jpeg"]) == 1:
            merge(next(iter(candidates["raw"])), next(iter(candidates["jpeg"])),
                  "unambiguous capture metadata; RAW preferred")
    return {key: members for key, members in groups.items() if find(key) == key}, reasons


def discover_captures(input_dir: str | Path, *, split: str = "", provenance: str | None = None,
                      hash_sources: bool = True, burst_seconds: float = 30.0) -> dict:
    root = Path(input_dir).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("input must be a directory")
    mapping, manifest = _provenance(root, provenance)
    # Provenance annotates discovery; it must not silently suppress newly added captures.
    # Do not follow directory symlinks or escape the supplied input directory.
    paths = sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink()
                   and p.suffix.lower() in EXTENSIONS)
    groups = defaultdict(list)
    for p in paths:
        if p.suffix.lower() not in EXTENSIONS:
            continue
        info = mapping.get(str(p), {})
        ident = info.get("identity", str(p.with_suffix("")).casefold())
        meta = metadata(p)
        # A reused same stem with conflicting timestamps cannot be confidently paired.
        groups[ident].append((p, info, meta))
    separated = {}
    for ident, members in groups.items():
        known = {(m[2]["camera"], m[2]["captured_at"]) for m in members if m[2]["captured_at"]}
        if len(known) > 1 and not members[0][1]:
            for member in members:
                key = ident + "|" + member[2]["camera"] + "|" + member[2]["captured_at"]
                separated.setdefault(key, []).append(member)
        else:
            separated[ident] = members
    groups = separated
    hashes = {}
    def digest(path):
        key = str(path)
        if key not in hashes:
            hashes[key] = sha256_file(path)
        return hashes[key]
    groups, merge_reasons = _merge_generic_duplicates(groups, digest)
    entries = []
    for ident, members in sorted(groups.items()):
        members.sort(key=lambda x: (not is_raw(str(x[0])), str(x[0]).casefold(), str(x[0])))
        source, info, meta = members[0]
        companions = []
        for p, _, _ in members[1:]:
            companions.append({"path": str(p), "format": p.suffix.lower()[1:],
                               "reason": merge_reasons.get(str(p),
                                   "RAW preferred" if not is_raw(str(p)) and is_raw(str(source)) else "duplicate capture"),
                               "sha256": digest(p) if hash_sources and p.exists() else None})
        # References outside selected_raw are allowed only when explicitly recorded in its
        # local curation manifest. They are never chosen as additional edit sources.
        jpgs = sorted(p for p, pi in mapping.items() if pi["identity"] == ident
                      and Path(p).suffix.lower() in {".jpg", ".jpeg"} and Path(p).is_file())
        if not jpgs:
            jpgs = [str(p) for p, _, _ in members if p.suffix.lower() in {".jpg", ".jpeg"}]
        entry = {"capture_id": stable_hash(ident)[:16], "identity": ident,
                 "source": str(source), "source_sha256": digest(source) if hash_sources and source.exists() else None,
                 "source_format": source.suffix.lower()[1:], "companions": companions,
                 "camera_reference": jpgs[0] if jpgs and is_raw(str(source)) else None,
                 "camera_reference_sha256": digest(jpgs[0]) if jpgs and is_raw(str(source)) and hash_sources else None,
                 "scene": info.get("scene", str(source.parent)), "category": info.get("category", ""),
                 "split": split, **meta}
        entries.append(entry)
    # Group before any gallery sampling; never group frame numbers across cameras/events.
    ordered = sorted(entries, key=lambda e: (e["scene"], e["camera"], e["captured_at"], e["capture_id"]))
    previous = None
    group = ""
    for entry in ordered:
        stamp = datetime.fromisoformat(entry["captured_at"]) if entry["captured_at"] else None
        same = (previous is not None and stamp is not None and previous[2] is not None
                and (entry["scene"], entry["camera"]) == previous[:2]
                and 0 <= (stamp - previous[2]).total_seconds() <= burst_seconds)
        if not same:
            group = "g-" + entry["capture_id"]
        entry["group"] = group
        previous = (entry["scene"], entry["camera"], stamp)
    return {"schema_version": 1, "input_dir": str(root), "provenance": str(manifest) if manifest else None,
            "burst_seconds": burst_seconds, "entries": entries,
            "counts": {"files": len(paths), "captures": len(entries),
                       "raw": sum(is_raw(e["source"]) for e in entries),
                       "jpeg_only": sum(e["source_format"] in {"jpg", "jpeg"} for e in entries),
                       "other_pixel": sum(not is_raw(e["source"]) and e["source_format"] not in {"jpg", "jpeg"} for e in entries),
                       "groups": len({e["group"] for e in entries})}}


def assert_separate_output(input_dir: str | Path, output_dir: str | Path) -> None:
    source, target = Path(input_dir).resolve(), Path(output_dir).resolve()
    if source == target or source.is_relative_to(target) or target.is_relative_to(source):
        raise ValueError("output must be separate from the input tree (including resolved symlinks)")


def verify_sources(entries: list[dict]) -> list[dict]:
    errors = []
    for entry in entries:
        files = [(entry["source"], entry.get("source_sha256"))]
        files.extend((c["path"], c.get("sha256")) for c in entry.get("companions", []))
        if entry.get("camera_reference") and entry.get("camera_reference_sha256"):
            files.append((entry["camera_reference"], entry["camera_reference_sha256"]))
        for path, expected in files:
            try:
                if expected is None or sha256_file(path) != expected:
                    errors.append({"path": path, "error": "source hash changed or unavailable"})
            except OSError as exc:
                errors.append({"path": path, "error": str(exc)})
    return errors
