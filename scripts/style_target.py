"""Measure local style target ranges from a frozen set of finished photographs."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import web_corpus
from autoedit.contract import RunConfig
from autoedit.edit.reference import reference_features
from autoedit.edit.style_target import CONTROLS
from autoedit.imageio import downscale_long_edge, load_image
from autoedit.paths import dump_toml
from autoedit.pipeline import Pipeline
from autoedit.styles import load_styles

KINDS = {"blue_hour": "blue_hour", "sunset_glow": "sunset", "moody_landscape": "moody"}


def finished_files(list_path, manifest_path, style):
    """Verify the frozen role, provenance and bytes before using a target photo."""
    freeze = list_path.parent / "split.json"
    if not freeze.exists():
        freeze = list_path.parent / "planned-partitions.json"
    frozen = json.loads(freeze.read_text())
    eligible = set(frozen[KINDS[style]]["target"])
    reserved = {photo for roles in frozen.values() for role in ("panel", "holdout") for photo in roles[role]}
    if eligible & reserved:
        raise ValueError("a target belongs to a tuning or held-back set")
    paths = [Path(p).resolve() for p in list_path.read_text().splitlines() if p.strip()]
    with manifest_path.open(newline="", encoding="utf8") as stream:
        records = {r["id"]: r for r in csv.DictReader(stream)}
    other = [records[key] for key in reserved if key in records]
    excluded_hashes = {r.get("sha256") for r in other}
    excluded_authors = {r["author"].casefold() for r in other if r.get("author")}
    near_authors = {web_corpus.same_author(r["author"]) for r in other if r.get("author")}
    near = 0
    by_path = {}
    cfg = web_corpus.config()
    for photo in eligible:
        row = records[photo]
        if (row["source"], row["role"], row["kind"]) != ("commons", "finished", KINDS[style]):
            raise ValueError("target requires an eligible finished photo")
        if row["sha256"] in excluded_hashes or row["author"].casefold() in excluded_authors:
            raise ValueError("a tuning or held-back photo or photographer overlaps the target set")
        near += web_corpus.same_author(row["author"]) in near_authors
        path = web_corpus.check_row(row, cfg, manifest_path.parent)
        by_path[path] = row
    if not paths or len(paths) != len(set(paths)) or set(paths) != by_path.keys():
        raise ValueError("target list differs from the frozen target group")
    seen_hashes = set()
    for path in paths:
        actual = web_corpus.digest(path)
        if actual != by_path[path]["sha256"] or actual in seen_hashes:
            raise ValueError("duplicate or changed target photo")
        seen_hashes.add(actual)
    if near:
        # A split drawn before credits were normalised. `web_corpus.py split` warns the same way.
        print(f"warning: {near} target photos are by an author who is in a tuning or held-back set "
              "under another spelling of the credit")
    return paths


CORPUS_ID = re.compile(rf"(?:{'|'.join(web_corpus.SOURCES)})-[0-9a-f]{{8,}}")


def owner_files(list_path):
    """Only the owner's own photographs: never a corpus file, wherever it was copied."""
    photos = [Path(line.strip()).expanduser().resolve() for line in list_path.read_text().splitlines() if line.strip()]
    for path in photos:
        if path.suffix.lower() == ".toml":
            raise ValueError("expected an owner photograph")
        if CORPUS_ID.match(path.stem):
            raise ValueError("a web corpus file cannot be marked as an owner target")
        for folder in path.parents:
            if _is_corpus(folder) and path.relative_to(folder).parts[:2] != ("owner", "photos"):
                raise ValueError("inside a web corpus only owner/photos holds the owner's photographs")
    return photos


def _is_corpus(folder):
    """A folder of this tool: its manifest.csv has the licence and checksum columns. Any other
    manifest.csv (a blind gallery has one too) says nothing about where a photo came from."""
    try:
        with (folder / "manifest.csv").open(newline="") as stream:
            return {"licence", "sha256"} <= set(next(csv.reader(stream), []))
    except OSError:
        return False


def ranges_from_samples(samples, fields):
    """One observation per photograph per region, irrespective of its pixel count."""
    tables = {}
    for region, measures in fields.items():
        if measures.get("hold"):
            tables[region] = {"hold": True}
            continue
        observations = [s[region] for s in samples if region in s]
        if observations:
            tables[region] = {key: [round(float(n), 6) for n in np.percentile([s[key] for s in observations], [25, 75])]
                              for key in measures if key in CONTROLS}
    tables["meta"] = {"photos": len(samples),
                      "region_counts": {r: sum(r in s for s in samples) for r in fields if r != "global"}}
    return tables


def extract(files, pipe, fields):
    samples = []
    for index, path in enumerate(files, 1):
        photo = downscale_long_edge(load_image(str(path)), pipe.cfg.analysis_long_edge)
        sample = {"global": reference_features(photo.rgb)}
        def measure(resolved):
            for region in resolved.regions:
                if region.name in fields and region.mask.sum() >= max(16, region.mask.size * .002):
                    sample[region.name] = reference_features(photo.rgb, region.mask)
        pipe.process_one(str(path), image=photo, proxy=photo, do_apply=False, on_resolved=measure)
        samples.append(sample)
        print(f"Measured {index}/{len(files)}", flush=True)
    return ranges_from_samples(samples, fields)


def main():
    import tomllib
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--manifest", type=Path, help="F60 corpus manifest, with split.json beside the list")
    group.add_argument("--owner-files", action="store_true", help="the listed photos are the owner's finished photos")
    parser.add_argument("--style", choices=[*KINDS, "natural_portrait"], required=True)
    parser.add_argument("--backend", choices=["sam3","stub"], default="sam3")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    cfg = RunConfig(backend=args.backend)
    style = load_styles(cfg.styles_path)[args.style]
    with open(cfg.targets_path, "rb") as f:
        table = tomllib.load(f)[style.target]
    fields = {k: v for k, v in table.items() if k != "meta"}
    for region, keys in table.get("meta", {}).get("measure", {}).items():
        fields.setdefault(region, dict.fromkeys(keys))
    if args.style == "natural_portrait" and args.manifest:
        parser.error("Natural portrait needs owner-supplied finished portraits")
    paths = finished_files(args.list, args.manifest, args.style) if args.manifest else owner_files(args.list)
    if args.out.suffix.lower() != ".toml" or args.out.resolve() in paths:
        parser.error("--out must name a separate TOML target table")
    if not paths or len(set(paths)) != len(paths):
        parser.error("the list must contain distinct finished photos")
    pipe = Pipeline.build(RunConfig(backend=args.backend, concepts=style.concepts, intensity=0))
    report = extract(paths, pipe, fields)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(dump_toml({style.target: report}), encoding="utf8")
    print(f"{style.label}: measured {len(paths)} photos")


if __name__ == "__main__":
    main()
