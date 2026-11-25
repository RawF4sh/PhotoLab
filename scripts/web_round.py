"""Score a frozen F60 iteration after blind judging, or publish its held-back owner gallery.

  python -I scripts/web_round.py score out-webloop/iter-01
  python -I scripts/web_round.py gallery out-webloop/iter-01/holdout \
      --list out-webloop/holdout-blue_hour.txt --manifest curated-web/manifest.csv \
      --out out-webloop/blind/blue_hour

The score expects sheets/, controls/, pairs/, tone-best.json, tone-candidate.json and
verdicts.txt in the iteration directory. Each panel photo needs one A/B/tie vote with
a reason. Prefix a reason with CLEAR to identify a clearly inferior side. The gallery
reuses actual held-back renders. It never reads, copies or publishes research pairs.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
import re
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blindtest import render_comparison
from web_corpus import same_author, write_atomic

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from autoedit import tonemetrics as tm
from autoedit.contract import Result

ROLES = ("best", "candidate")
BAR_DECIDED = 10  # photos that must be Better or Worse for the bar
BAR_SHARE = 0.7  # Better divided by decided
BAR_PAIRS = 5  # pairs both sides can be compared on before the distance is part of the bar
NATIVE_FLAGS = ("dark_made_darker", "bright_pushed_to_clip", "shadow_pushed_to_clip")


def read_json(path):
    return json.loads(path.read_text())


def votes(path):
    answers = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split(None, 2)
        if len(fields) != 3 or fields[1] not in ("A", "B", "=") or fields[0] in answers:
            raise ValueError("each verdict needs a unique photo ID, A/B/= and a reason")
        answers[fields[0]] = fields[1:]
    if not answers:
        raise ValueError("blind verdicts are required before unmasking")
    return answers


def native_flags(key):
    flagged = {role: set() for role in ROLES}
    for role in ROLES:
        for stem in key["rows"]:
            path = Path(key["renders"][role]) / f"{stem}.autoedit.json"
            result = Result.model_validate_json(path.read_text())
            measured = tm.compare(
                tm.from_global(result.global_stats),
                tm.from_global(result.post_edit_stats),
            )
            flagged[role].update(
                (stem, name) for name in NATIVE_FLAGS if measured[name]
            )
            if result.needs_review:
                flagged[role].add((stem, "needs_review"))
    return flagged


def review_kinds(reason):
    """The kinds of warning in a review reason, without their numbers."""
    return {re.sub(r"[-+]?\d+(?:\.\d+)?", "#", part).strip() for part in (reason or "").split(";") if part.strip()}


def checksum(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def frozen_inputs(directory, panel):
    """Locate the actual reservation; never infer completeness from the rendered outputs."""
    root = next(
        (p for p in (directory, *directory.parents) if (p / "split.json").is_file()),
        None,
    )
    if root is None:
        raise ValueError("the iteration must belong to a frozen web corpus split")
    split = read_json(root / "split.json")
    controls = split.get("control", {}).get("panel", [])
    if len(controls) != 16 or len(set(controls)) != 16:
        raise ValueError("the frozen Standard panel needs sixteen distinct photographs")
    judged = [
        kind
        for kind, roles in split.items()
        if kind != "control"
        and len(roles["panel"]) == 16
        and set(roles["panel"]) == set(panel)
    ]
    if len(panel) != 16 or not judged:
        raise ValueError(
            "blind judging must cover a complete sixteen-photo frozen style panel"
        )
    own = root / f"pairs-{judged[0]}.txt"  # round two keeps one pairs list per kind
    entries = [
        line.split("\t")
        for line in (own if own.is_file() else root / "pairs-evaluation.txt")
        .read_text()
        .splitlines()
        if line.strip()
    ]
    if not entries or any(len(entry) != 2 or not all(entry) for entry in entries):
        raise ValueError(
            "the frozen reference list must contain source and expert edit for each pair"
        )
    pairs = [Path(entry[0]).stem for entry in entries]
    if len(set(pairs)) != len(pairs) or len(
        {Path(entry[1]).stem for entry in entries}
    ) != len(pairs):
        raise ValueError("duplicate entry in frozen expert pairs")
    return set(controls), set(pairs)


def check_native_backend(key, expected, label):
    if set(key["rows"]) != expected:
        raise ValueError(f"{label} did not cover its complete frozen list")
    for role in ROLES:
        for stem in expected:
            doc = read_json(Path(key["renders"][role]) / f"{stem}.autoedit.json")
            evidence = doc.get("audit", {}).get("panel_segmentation", {})
            if evidence.get("requested") != "sam3" or evidence.get("actual") != "sam3":
                raise ValueError(
                    f"{label} {role} requires actual SAM segmentation for every frame"
                )


def check_same_arms(sheets, controls, pairs):
    """The judged sheets, the controls and the pairs must compare the same two config folders,
    and two different ones. Their render folders differ by design (controls run under Standard),
    so the recorded config folders are what is compared."""
    want = {role: sheets[role] for role in ROLES}
    for label, key in (("sheets", sheets), ("controls", controls), ("pairs", pairs)):
        arms = {role: key[role] for role in ROLES}
        if len(set(arms.values())) != 2:
            raise ValueError(f"the {label} key compares a config folder with itself")
        if arms != want:
            raise ValueError(f"the {label} key was built from other config folders than the sheets")


def check_tone(doc, panel):
    rows = doc["rows"]
    ids = [Path(row["name"]).stem for row in rows]
    if len(ids) != len(panel) or set(ids) != set(panel) or doc.get("errors"):
        raise ValueError("tone gate did not cover the complete panel")
    if any(
        row.get("requested_backend") != "sam3"
        or row.get("actual_backend") != "sam3"
        or row.get("backend_fallback")
        for row in rows
    ):
        raise ValueError("tone gate silently lacked real SAM segmentation")
    safe = sum(row["safety_fallback"] == 1 and row["audit_passed"] for row in rows)
    failed = [
        row["name"]
        for row in rows
        if not row["audit_passed"] or any(row[name] for name in NATIVE_FLAGS)
    ]
    return {"safe_as_rendered": safe, "failures": failed}


def reference_medians(distances, rows):
    """The median distance from the reference per arm, over the pairs both arms can be compared on.
    Also the pairs left out, those where only the candidate cannot be compared, and how many
    were compared."""
    measures = {role: [] for role in ROLES}
    excluded, candidate_lost = [], []
    for stem, values in distances.items():
        if not all(values[side]["comparable"] for side in "AB"):
            excluded.append(stem)
            if any(values[side]["comparable"] and rows[stem][side] == "best" for side in "AB"):
                candidate_lost.append(stem)
            continue
        for side in "AB":
            measures[rows[stem][side]].append(values[side]["cells_ev"])
    median = {role: statistics.median(x) for role, x in measures.items()} if measures["best"] else None
    return median, excluded, candidate_lost, len(measures["best"])


def outcomes(answers, key):
    """Each vote as Better, Same or Worse for the candidate, and the clear losses."""
    results, clearly_worse = {}, []
    for stem, (pick, reason) in answers.items():
        results[stem] = result = (
            "Same"
            if pick == "="
            else "Better"
            if key["rows"][stem][pick] == "candidate"
            else "Worse"
        )
        if result == "Worse" and (
            reason.upper().startswith("CLEAR")
            or re.search(r"(?<!not )(?<!n't )\bclear(ly)?\b", reason, re.I)
        ):
            clearly_worse.append(stem)
    return results, clearly_worse


def bar(directory):
    """The bar of F61 for one held-back comparison (best = Standard, candidate = the style)."""
    answers = votes(directory / "verdicts.txt")
    key = read_json(directory / "sheets/key.json")
    if set(answers) != set(key["rows"]):
        raise ValueError("judge every photo exactly once before the bar")
    check_native_backend(key, set(answers), "held-back comparison")
    results, clear_losses = outcomes(answers, key)
    counts = Counter(results.values())
    better, worse = counts["Better"], counts["Worse"]
    decided = better + worse
    share = better / decided if decided else None
    flags = native_flags(key)
    marks = sorted(
        stem
        for stem, name in flags["candidate"] - flags["best"]
        if name == "needs_review"
    )
    changed = sum(
        checksum(Path(key["renders"]["best"]) / f"{stem}.jpg")
        != checksum(Path(key["renders"]["candidate"]) / f"{stem}.jpg")
        for stem in key["rows"]
    )
    by_shoot = {}
    root = next(
        (p for p in (directory, *directory.parents) if (p / "split.json").is_file()),
        None,
    )
    if root is not None and (root / "shoots.json").is_file():
        shoots = read_json(root / "shoots.json")
        for stem, result in results.items():
            tally = by_shoot.setdefault(
                shoots.get(stem, "unknown"), dict(better=0, worse=0, same=0)
            )
            tally[result.lower()] += 1
    # The distance from the reference is part of the bar when at least BAR_PAIRS photos came with
    # a reference. Then as many must be comparable on both sides, and the style must be closer.
    median, candidate_lost, compared, distances = None, [], 0, {}
    measured = directory / "sheets/distances.json"
    if measured.is_file():
        distances = read_json(measured)
        if not set(distances) <= set(key["rows"]):
            raise ValueError("the reference distances name photos that were not judged")
        median, _, candidate_lost, compared = reference_medians(distances, key["rows"])
    closer = len(distances) < BAR_PAIRS or (
        compared >= BAR_PAIRS and median["candidate"] < median["best"]
    )
    return dict(
        better=better,
        worse=worse,
        same=counts["Same"],
        decided=decided,
        share=share,
        clear_losses=clear_losses,
        new_review_marks=marks,
        changed=changed,
        median_36_cell_ev=median,
        reference_pairs=compared,
        candidate_not_comparable=candidate_lost,
        by_shoot=by_shoot,
        passes=decided >= BAR_DECIDED
        and share >= BAR_SHARE
        and not clear_losses
        and not marks
        and closer
        and not candidate_lost,
    )


def score(directory):
    # Require the blind file before loading the roll's key.
    answers = votes(directory / "verdicts.txt")
    key = read_json(directory / "sheets/key.json")
    if set(answers) != set(key["rows"]):
        raise ValueError("judge every panel photo exactly once before scoring")
    expected_controls, expected_pairs = frozen_inputs(directory, answers)
    check_native_backend(key, set(answers), "style panel")
    results, clearly_worse = outcomes(answers, key)
    counts = Counter(results.values())
    flags = native_flags(key)
    controls = read_json(directory / "controls/key.json")
    pairs = read_json(directory / "pairs/key.json")
    check_same_arms(key, controls, pairs)
    check_native_backend(controls, expected_controls, "Standard controls")
    changed = [
        stem
        for stem in controls["rows"]
        if len(
            {
                checksum(Path(controls["renders"][role]) / f"{stem}.jpg")
                for role in ROLES
            }
        )
        != 1
    ]
    check_native_backend(pairs, expected_pairs, "expert pairs")
    distances = read_json(directory / "pairs/distances.json")
    if set(distances) != set(pairs["rows"]):
        raise ValueError("reference coverage missing")
    median, excluded, candidate_lost, compared = reference_medians(distances, pairs["rows"])
    if median is None:
        raise ValueError("no comparable expert reference pairs")
    tone = {
        r: check_tone(read_json(directory / f"tone-{r}.json"), answers) for r in ROLES
    }
    new_flags = sorted(flags["candidate"] - flags["best"])
    # O7: a review mark is new only when Standard does not give the same photo one. The key of a
    # run of the same panel with Standard as its best arm is optional.
    shared = None
    vs_path = directory / "vs-standard/key.json"
    if vs_path.is_file():
        vs = read_json(vs_path)
        if set(vs["rows"]) != set(answers):
            raise ValueError("the Standard comparison did not cover the judged panel")
        if vs["candidate"] != key["candidate"]:
            raise ValueError("the Standard comparison was built from another candidate")
        check_native_backend(vs, set(answers), "Standard on the style panel")
        docs = {s: read_json(Path(vs["renders"]["best"]) / f"{s}.autoedit.json") for s in answers}
        if any(d.get("style") not in ("natural", "standard") or d.get("audit", {}).get("local_style")
               for d in docs.values()):
            raise ValueError("the Standard comparison's best arm was not rendered under Standard")
        if vs["best"] not in (key["best"], key["candidate"]):
            raise ValueError("the Standard comparison's best arm came from another config folder")
        standard = {s for s, n in native_flags(vs)["best"] if n == "needs_review"}

        def excused(stem):
            # Same kinds of warning only: a kind Standard lacks on that photo is a new problem.
            mine = review_kinds(read_json(Path(key["renders"]["candidate"]) / f"{stem}.autoedit.json").get("review_reason"))
            return stem in standard and bool(mine) and mine <= review_kinds(docs[stem].get("review_reason"))

        shared = sorted(s for s, n in new_flags if n == "needs_review" and excused(s))
        new_flags = [f for f in new_flags if not (f[1] == "needs_review" and f[0] in shared)]
    gates = {
        "blind_preference": counts["Better"] > counts["Worse"] and not clearly_worse,
        "native": not new_flags,
        "tone": not tone["candidate"]["failures"],
        "safe_proposals": tone["candidate"]["safe_as_rendered"]
        >= tone["best"]["safe_as_rendered"],
        "standard": not changed,
        "expert_distance": median["candidate"] <= median["best"],
        "candidate_comparable": not candidate_lost,
    }
    summary = dict(
        keep=all(gates.values()),
        gates=gates,
        counts=dict(counts),
        clearly_worse=clearly_worse,
        tone=tone,
        new_native_flags=new_flags,
        changed_controls=changed,
        reference_pairs=compared,
        excluded_pairs=excluded,
        reasons=[
            f"pair {stem}: the candidate side cannot be compared with the expert edit"
            for stem in candidate_lost
        ],
        median_36_cell_ev=median,
    )
    if shared is not None:
        summary["review_marks_standard_gives_too"] = shared
    write_atomic(directory / "gates.json", json.dumps(summary, indent=2).encode())
    return summary


def gallery(sheets, listed, manifest, out, seed):
    # A line is a photo, optionally followed by a tab and a reference path (ignored here).
    sources = [
        Path(line.split("\t")[0])
        for line in listed.read_text().splitlines()
        if line.strip()
    ]
    with manifest.open(newline="") as fh:
        by_id = {r["id"]: r for r in csv.DictReader(fh)}
    key = read_json(sheets / "key.json")
    if not sources or set(key["rows"]) != {p.stem for p in sources}:
        raise ValueError(
            "the held-back comparison must cover the complete declared list"
        )
    split = read_json(listed.parent / "split.json")
    ids = [p.stem for p in sources]
    # Round two's control has a panel only, so a kind may have no held-back group.
    kind = next((k for k, roles in split.items() if ids == roles.get("holdout")), None)
    if len(set(ids)) != len(ids) or kind is None:
        raise ValueError("gallery list must match a frozen held-back group")
    shoots_file = listed.parent / "shoots.json"
    shoots = read_json(shoots_file) if shoots_file.is_file() else None
    entries = []
    for path in sources:
        row = by_id.get(path.stem)
        if row is None:
            # the owner's own original: no manifest row, in owner/originals/<kind>/
            parts = path.resolve().parent.parts[-3:]
            home = (manifest.parent / "owner" / "originals").resolve()
            if len(parts) < 3 or path.resolve().parent.parent != home:
                raise ValueError(
                    "owner gallery takes only licensed held-back style photographs"
                )
            row = dict(kind=parts[2], author="owner", sha256=checksum(path))
        else:
            if (row["source"], row["role"], row["track"]) not in (
                ("commons", "finished", "L"),
                ("fivek", "before", "L"),
            ):
                raise ValueError(
                    "owner gallery takes only licensed held-back style photographs"
                )
            if (manifest.parent / row["file"]).resolve() != path.resolve():
                raise ValueError("holdout list does not match corpus provenance")
        methods = {}
        for role, method in zip(ROLES, ("baseline", "autoedit")):
            root = Path(key["renders"][role]).resolve()
            methods[method] = {
                "path": str(root / f"{path.stem}.jpg"),
                "sidecar": str(root / f"{path.stem}.autoedit.json"),
            }
        group = (
            shoots.get(path.stem, "unknown")
            if shoots
            else hashlib.sha256(same_author(row["author"]).encode()).hexdigest()[:16]
        )
        entries.append(
            dict(
                capture_id=path.stem,
                source=str(path.resolve()),
                source_sha256=row["sha256"],
                group=group,
                scene=kind,
                split="holdout",
                methods=methods,
            )
        )
    comparison = sheets / "gallery-sources.json"
    write_atomic(comparison, json.dumps({"entries": entries}, indent=2).encode())
    return render_comparison(str(comparison), str(out), seed=seed, split="holdout")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("score")
    audit.add_argument("directory", type=Path)
    threshold = commands.add_parser("bar")
    threshold.add_argument("directory", type=Path)
    publish = commands.add_parser("gallery")
    publish.add_argument("sheets", type=Path)
    publish.add_argument("--list", type=Path, required=True)
    publish.add_argument("--manifest", type=Path, required=True)
    publish.add_argument("--out", type=Path, required=True)
    publish.add_argument("--seed", type=int, default=60)
    args = parser.parse_args()
    answer = (
        score(args.directory)
        if args.command == "score"
        else bar(args.directory)
        if args.command == "bar"
        else gallery(args.sheets, args.list, args.manifest, args.out, args.seed)
    )
    print(json.dumps(answer, indent=2))
