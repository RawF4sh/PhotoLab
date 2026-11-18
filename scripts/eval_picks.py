"""Check `autoedit pick` against your own keepers (F35).

Pick a keeper per burst in your own viewer first, list the file names in a text file (one per line,
extension optional, a line starting with `#` is a comment), then:

    python scripts/eval_picks.py out-picks/ranking.json my-picks.txt [--top 2]

Prints how often your keeper is among the tool's first N of each burst, and the misses. Writes nothing.
"""

from __future__ import annotations

import argparse
import json
import os


def _stem(name: str) -> str:
    return os.path.splitext(os.path.basename(name.strip()))[0].lower()


def agreement(ranking: dict, names: list[str], top: int = 2) -> dict:
    wanted = {_stem(n) for n in names if n.strip() and not n.strip().startswith("#")}
    agree = first = n_bursts = 0
    misses, skipped = [], []
    for g in ranking["bursts"]:
        hits = [(j["rank"], j) for j in g["shots"] if any(_stem(f) in wanted for f in j["files"])]
        if not hits:
            skipped.append(g["id"])
            continue
        rank, j = min(hits, key=lambda h: h[0])
        n_bursts += 1
        first += rank == 1
        if rank <= top:
            agree += 1
        else:
            misses.append((g["id"], j["files"][0], rank, g["shots"][0]["files"][0]))
    known = {_stem(f) for g in ranking["bursts"] for j in g["shots"] for f in j["files"]}
    unknown = sorted(wanted - known)
    return {"bursts": n_bursts, "agree": agree, "first": first, "misses": misses,
            "skipped": skipped, "unknown": unknown}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ranking")
    ap.add_argument("picks")
    ap.add_argument("--top", type=int, default=2)
    a = ap.parse_args(argv)
    with open(a.ranking, encoding="utf-8") as fh:
        ranking = json.load(fh)
    with open(a.picks, encoding="utf-8") as fh:
        names = fh.read().splitlines()
    r = agreement(ranking, names, a.top)
    print(f"your keeper is among the tool's first {a.top} in {r['agree']} of {r['bursts']} bursts "
          f"(the tool's pick in {r['first']})")
    for b, f, rank, pick in r["misses"]:
        print(f"  miss {b}: {f} is ranked {rank}; the tool picked {pick}")
    if r["skipped"]:
        print(f"  no name for: {', '.join(r['skipped'])}")
    if r["unknown"]:
        print(f"  in no burst: {', '.join(r['unknown'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
