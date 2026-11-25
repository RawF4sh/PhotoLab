"""Measure an F62 scene round and, after the checker panel, prepare its blind owner vote.

Use the existing blindtest comparison-manifest format (baseline = Simple, autoedit = Auto).
Include the development and held-out captures with their source SHA256 and shoot group. Each
held-out method needs its rendered image and decision sidecar. The development entries need
only provenance. A detached or renamed render/sidecar pair must pin both files with sha256
and sidecar_sha256 in its method entry. Panel JSON maps each Auto render's SHA256 to a list of visible faults (empty
after inspecting a clean photo). Files and individual results stay in the private round folder.

  python -I scripts/auto_release.py measure twilight comparisons.json report.json
  python -I scripts/auto_release.py gallery twilight comparisons.json panel.json gallery
  python -I scripts/auto_release.py vote gallery/manifest.csv gallery/results.csv
"""

import argparse
import csv
import re
import tempfile
import json
import statistics
import sys
from pathlib import Path

import numpy as np

SCRIPTS = Path(__file__).resolve().parent
sys.path[:0] = [str(SCRIPTS), str(SCRIPTS.parent / "src")]
from blindtest import _load_entries, render_comparison, score
from web_round import checksum, review_kinds
from autoedit.contract import Result, RunConfig
from autoedit.edit.oklab import linear_to_oklab
from autoedit.imageio import downscale_long_edge, load_image, srgb_to_linear
from autoedit.quality_runner import write_json
from autoedit.scene import LANDSCAPES


def visible_distance(before, after):
    """Mean per-pixel OKLab distance ×100 within one image; the round takes the median across images."""
    if before.shape != after.shape:
        raise ValueError("Simple and Auto must have identical framing and dimensions")
    labs = [linear_to_oklab(srgb_to_linear(pixels)) for pixels in (before, after)]
    return float(np.linalg.norm(labs[1] - labs[0], axis=-1).mean()) * 100


def image_pair(paths):
    # Use the gallery's decoder: ICC conversion, EXIF orientation and 16-bit TIFF
    # handling must agree with the photos the owner will actually compare.
    result = []
    shape = None
    for path in paths:
        im = load_image(str(path))
        if shape is not None and shape != im.rgb.shape:
            raise ValueError("Simple and Auto must have identical dimensions")
        shape = im.rgb.shape
        result.append(downscale_long_edge(im, 1024).rgb)
    return result


def held_out(entries, scene):
    prior = [r for r in entries if r.get("split") != "holdout"]
    if not prior:
        raise ValueError("include the development captures so shoot separation can be checked")
    for row in entries:
        group, digest = row.get("group"), row.get("source_sha256")
        if (not isinstance(group, str) or not group.strip() or group.strip().lower() == "unknown"
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)):
            raise ValueError("every capture needs its shoot and a source SHA256")
    entries = [dict(r, source_sha256=r["source_sha256"].lower()) for r in entries]
    rows = [r for r in entries if r.get("split") == "holdout" and r.get("scene") == scene]
    others = [r for r in entries if r.get("split") != "holdout" or r.get("scene") != scene]
    hashes = {r["source_sha256"] for r in rows}
    if (len(hashes) != len(rows) or hashes & {r["source_sha256"] for r in others}
            or {r["group"].strip() for r in rows} & {r["group"].strip() for r in prior}):
        raise ValueError("duplicate capture or development shoot in holdout")
    if len(rows) < 10:
        raise ValueError("a scene round needs at least ten held-out originals")
    return rows


# The two arms may use different output names and scheduling. Their processing
# settings otherwise have to agree, apart from selecting the two styles.
_RUN_DIFFERENCES = {"style", "intensity", "auto_unreleased", "out_name", "out_subdir", "workers",
                    "threads", "history", "profile_dir", "mask_cache"}


def matching_settings(simple, auto):
    base = RunConfig.model_validate(simple)
    goal = RunConfig.model_validate(auto)
    return (base.style in {"natural", "standard"} and base.intensity == RunConfig.model_fields["intensity"].default
            and goal.style == "auto" and goal.intensity == 1 and goal.scene is None
            and base.decider == "rules" and not goal.allow_cloud
            and base.model_dump(exclude=_RUN_DIFFERENCES) == goal.model_dump(exclude=_RUN_DIFFERENCES))


def measure(manifest, scene):
    photos = []
    config_digests = set()
    for row in held_out(_load_entries(str(manifest)), scene):
        if checksum(Path(row["source"])) != row["source_sha256"]:
            raise ValueError("held-out original differs from the frozen source")
        methods = [row["methods"][name] for name in ("baseline", "autoedit")]
        simple, auto = [Result.model_validate_json(Path(m["sidecar"]).read_text()) for m in methods]
        info = auto.audit.get("auto", {})
        images = [Path(m["path"]) for m in methods]
        for result, output, method in zip((simple, auto), images, methods):
            # Either use an adjacent, matching sidecar (normal batch exports) or pin
            # detached files explicitly in the manifest. A basename can repeat by shoot.
            sidecar = Path(method["sidecar"])
            pinned = method.get("sha256"), method.get("sidecar_sha256")
            if any(pinned):
                if pinned != (checksum(output), checksum(sidecar)):
                    raise ValueError("comparison render or sidecar differs from its frozen SHA256")
            elif (sidecar.parent != output.parent or sidecar.name not in
                  (output.with_suffix(".autoedit.json").name, output.with_suffix(".json").name)):
                raise ValueError("detached comparison files need both render and sidecar SHA256")
            if (result.image != Path(row["source"]).name
                    or result.audit.get("output") != output.name
                    or not result.audit.get("config_digest")):
                raise ValueError("sidecar does not identify this original, render and configuration")
            config_digests.add(result.audit["config_digest"])
        options = auto.audit.get("run_config", {})
        simple_options = simple.audit.get("run_config", {})
        count = info.get("photos", 0)
        if type(count) is not int or count < 0:
            raise ValueError("calibration pairs must be a nonnegative integer")
        manual = any(result.audit.get("manual") or result.audit.get("gemini_edit")
                     or any(a.scope == "photo" for a in result.adjustments) for result in (simple, auto))
        if (manual or simple.audit.get("auto") or simple.style != simple_options.get("style") or auto.style != "auto"
                or not matching_settings(simple_options, options) or auto.scene != scene
                or info.get("scene") != scene or info.get("target") not in {f"auto_{scene}", "auto_landscape"} or info.get("fallback")
                or not isinstance(info.get("fault_check", {}).get("failures"), list)):
            raise ValueError("each pair needs a Simple render and a checked Auto render of this scene")
        reasons = review_kinds(auto.review_reason) - review_kinds(simple.review_reason)
        photos.append({"capture": row["capture_id"], "render_sha256": checksum(images[1]),
                       "simple_sha256": checksum(images[0]), "target": info["target"],
                       "distance": visible_distance(*image_pair(images)),
                       "calibration_pairs": count,
                       "faults": info["fault_check"]["failures"],
                       "new_review": sorted(reasons) if auto.needs_review else [],
                       "new_review_mark": auto.needs_review and not simple.needs_review})
    if len({(p["target"], p["calibration_pairs"]) for p in photos}) != 1:
        raise ValueError("all photos of this scene must use the same frozen goal and calibration")
    if len(config_digests) != 1:
        raise ValueError("all renders must use the same frozen configuration")
    median = statistics.median(p["distance"] for p in photos)
    share = sum(p["distance"] >= 3 for p in photos) / len(photos)
    return {"scene": scene, "photos": photos, "median_distance": median, "share_at_least_3": share,
            "visible": median >= 5 and share >= .80,
            "automatic_checks": not any(p["faults"] or p["new_review"] or p["new_review_mark"] for p in photos)}


def gallery(manifest, scene, panel, out):
    report = measure(manifest, scene)
    if not report["visible"] or not report["automatic_checks"]:
        raise ValueError("visible difference or automatic checks missed the release bar")
    if any(p["calibration_pairs"] < 45 for p in report["photos"]):
        raise ValueError("the owner gallery requires measured goals backed by at least 45 screened pairs")
    hashes = {p["render_sha256"] for p in report["photos"]}
    if (not isinstance(panel, dict) or len(hashes) != len(report["photos"]) or set(panel) != hashes
            or any(faults != [] for faults in panel.values())):
        raise ValueError("the checker must inspect every distinct candidate and record zero visible faults")
    rows = held_out(_load_entries(str(manifest)), scene)
    # Limit the gallery to this scene. The manifest contains absolute resolved source and render paths.
    out.parent.mkdir(parents=True, exist_ok=True)
    # Never write over the source manifest or any existing sibling file. Keep the
    # filtered inputs inside the gallery once its empty-output check has succeeded.
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        selected = Path(tmp) / "comparisons.json"
        write_json(selected, {"entries": rows})
        result = render_comparison(str(selected), str(out), split="holdout")
        selected.replace(out / "comparisons.json")
    run = json.loads((out / "run.json").read_text())
    run["comparison_manifest"]["path"] = str((out / "comparisons.json").resolve())
    write_json(out / "run.json", run)
    checked = {p["capture"]: p for p in report["photos"]}
    for source in run["sources"]:
        inputs, photo = source["rendered_inputs"], checked[source["capture_id"]]
        if (inputs["autoedit"]["sha256"] != photo["render_sha256"]
                or inputs["baseline"]["sha256"] != photo["simple_sha256"]):
            raise ValueError("a checked render changed during gallery preparation")
    if result["failed"] or result["photos"] != len(rows):
        raise ValueError("blind gallery is incomplete")
    write_json(out / "scene-round.json", {"scene": scene, "gallery_id": result["gallery_id"],
                                         "manifest_sha256": checksum(out / "manifest.csv")})


def vote_passes(result):
    total, expected, wins = result["total"], result["expected"], result["wins"].get("autoedit", 0)
    return (type(total) is int and type(expected) is int and type(wins) is int
            and total == expected and expected >= 10 and 0 <= wins <= total and wins * 10 >= 7 * expected)


def score_round(manifest, results):
    record = json.loads((manifest.parent / "scene-round.json").read_text())
    if record["scene"] not in LANDSCAPES or record["manifest_sha256"] != checksum(manifest):
        raise ValueError("votes must use the manifest from this checked scene round")
    with manifest.open(newline="") as fh:
        for row in csv.DictReader(fh):
            photo, slot = row["photo"], row["slot"]
            if (not re.fullmatch(r"photo[0-9]+", photo) or slot not in {"0", "1"}
                    or row["gallery_id"] != record["gallery_id"] or row["scene"] != record["scene"]):
                raise ValueError("blind cards do not match this checked scene round")
            card = manifest.parent / photo / f"slot{slot}.jpg"
            if not card.is_file() or checksum(card) != row["sha256"]:
                raise ValueError("a blind card is missing or differs from the checked gallery")
    return vote_passes(score(str(results), str(manifest), pair=("autoedit", "baseline")))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="action", required=True)
    for command in ("measure", "gallery"):
        sub = commands.add_parser(command)
        sub.add_argument("scene", choices=LANDSCAPES)
        sub.add_argument("manifest", type=Path)
        if command == "gallery":
            sub.add_argument("panel", type=Path)
        sub.add_argument("out", type=Path)
    sub = commands.add_parser("vote")
    sub.add_argument("manifest", type=Path)
    sub.add_argument("results", type=Path)
    args = parser.parse_args()
    if args.action == "measure":
        write_json(args.out, measure(args.manifest, args.scene))
    elif args.action == "gallery":
        gallery(args.manifest, args.scene, json.loads(args.panel.read_text()), args.out)
    else:
        passed = score_round(args.manifest, args.results)
        print("Owner vote passed." if passed else "Owner vote has not passed.")
        raise SystemExit(0 if passed else 1)
