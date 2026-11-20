"""F41: search a run's sidecars by what is in the photo, and write the same words as XMP keywords."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from xml.sax.saxutils import escape

from . import paths, reveal

def index(folder: str) -> tuple[list[dict], int]:
    """(entries, number of sidecars that did not parse)."""
    entries, skipped = [], 0
    for p in sorted(Path(folder).rglob("*.autoedit.json")):
        try:
            d = json.loads(p.read_text())
            rev = d.get("reveal") or {}
            finds = rev.get("findings") or []
            entries.append({
                "sidecar": str(p),
                "image": (os.path.join(str(p.parent), os.path.basename(out)) if (out := (d.get("audit") or {}).get("output")) else None),
                "scene": d.get("scene") or "",
                "concepts": {c["name"]: float(c.get("coverage", 0.0))
                             for c in d.get("concepts", []) if c.get("present")},
                "reveal": sorted({f["kind"] for f in finds}),
                "reveal_labels": sorted({f.get("label") or f["kind"] for f in finds}),
            })
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            skipped += 1
    return entries, skipped


def _words_in(name: str) -> set[str]:
    return set(re.findall(r"\w+", name.lower()))


def _match(entry: dict, word: str, min_cov: float) -> tuple[bool, float]:
    w = word.lower().strip()
    hit, cov = False, 0.0
    if w == entry["scene"].lower():
        hit = True
    for name, c in entry["concepts"].items():
        if c >= min_cov and (w == name.lower() or w in _words_in(name)):
            hit, cov = True, cov + c
    if w in {k.lower() for k in entry["reveal"] + entry.get("reveal_labels", [])}:
        hit = True
    return hit, cov


def find(entries: list[dict], words: list[str], min_coverage: float) -> list[dict]:
    out = []
    for e in entries:
        ms = [_match(e, w, min_coverage) for w in words]
        if ms and all(h for h, _ in ms):
            out.append((sum(c for _, c in ms), e))
    out.sort(key=lambda t: -t[0])  # stable: ties keep folder order
    return [e for _, e in out]


def skip_words(configs_dir: str | None = None, profile: str | None = None) -> frozenset[str]:
    """Words that are no keyword: every reveal kind's name, label and words (a photo of a person is not
    tagged "person": that is for the reveal check), and rules.toml [search] exclude (engine guards).
    Read from the configs folder and profile the run uses."""
    cp = paths.config_paths(configs_dir, profile)
    cfg = reveal.load(cp["reveal_path"])
    rules = paths.read_config(cp["rules_path"])
    bad = [w for k in cfg.kinds for w in (k.name, k.label, *k.words)] + list((rules.get("search") or {}).get("exclude", []))
    return frozenset(w.lower() for w in bad)


def keywords(entry: dict, min_coverage: float, skip: frozenset[str] | None = None) -> list[str]:
    skip = skip_words() if skip is None else skip
    ws = {entry["scene"]} if entry["scene"] else set()
    ws |= {n for n, c in entry["concepts"].items() if c >= min_coverage}
    return sorted(w for w in ws if w.lower() not in skip)


# ponytail: envelope written here, not shared with curate._write_xmp_sidecar (task forbade touching curate.py)
def write_xmp(image_path: str, words: list[str]) -> str:
    xmp_path = str(Path(image_path).with_suffix(".xmp"))
    items = "\n".join(f"          <rdf:li>{escape(w)}</rdf:li>" for w in words)
    doc = f"""<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description rdf:about=""
      xmlns:xmp="http://ns.adobe.com/xap/1.0/"
      xmlns:dc="http://purl.org/dc/elements/1.1/"
      xmp:CreatorTool="AutoEdit">
      <dc:subject>
        <rdf:Bag>
{items}
        </rdf:Bag>
      </dc:subject>
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>
"""
    with open(xmp_path, "w") as fh:
        fh.write(doc)
    return os.path.abspath(xmp_path)


def _shown(e: dict) -> str:
    return e["image"] if e["image"] and os.path.exists(e["image"]) else e["sidecar"]


def cmd_find(folder: str, words: list[str], min_coverage: float, as_json: bool) -> tuple[str, int]:
    entries, _ = index(folder)
    hits = find(entries, words, min_coverage)
    if as_json:
        return json.dumps(hits, indent=2), (0 if hits else 1)
    lines = [_shown(e) for e in hits] + [f"{len(hits)} of {len(entries)} photos"]
    return "\n".join(lines), (0 if hits else 1)


def _is_ours(xmp: str) -> bool:
    try:
        return 'xmp:CreatorTool="AutoEdit"' in Path(xmp).read_text()
    except OSError:
        return False


def cmd_keywords(folder: str, min_coverage: float, dry_run: bool,
                 configs_dir: str | None = None, profile: str | None = None) -> str:
    lines, skip = [], skip_words(configs_dir, profile)
    for e in index(folder)[0]:
        words = keywords(e, min_coverage, skip)
        if not e["image"] or not os.path.exists(e["image"]):   # a dry run has no export: never an .xmp beside a source photo
            lines.append(f"{e['sidecar']}: no export image, skipped")
            continue
        xmp = str(Path(e["image"]).with_suffix(".xmp"))
        if os.path.exists(xmp) and not _is_ours(xmp):
            lines.append(f"{xmp}: exists and was not written by AutoEdit, skipped")
        elif dry_run:
            lines.append(f"{e['image']}: {', '.join(words)}")
        else:
            lines.append(f"{write_xmp(e['image'], words)}: {', '.join(words)}")
    return "\n".join(lines)
