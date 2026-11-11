"""Freeze and run reproducible tuning/holdout and component experiments."""
from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from autoedit.experiments import evaluate, lock_experiment, prepare_experiment, merge_evaluations


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=["prepare","freeze","tune","evaluate","merge"])
    ap.add_argument("run_dir")
    ap.add_argument("--tuning", help="explicit local tuning input directory for prepare")
    ap.add_argument("--holdout", help="explicit local holdout input directory for prepare")
    ap.add_argument("--split", choices=["tuning", "holdout"], default="holdout", help="split to merge")
    ap.add_argument("--shard", help="zero-based INDEX/COUNT; shards render all selected candidates")
    ap.add_argument("--shard-count", "--shards", type=int, help="number of completed shards to merge")
    ap.add_argument("--suite",choices=["primary","ablations"],default="primary")
    ap.add_argument("--max-px",type=int,default=1024)
    ap.add_argument("--half-size",action="store_true",help="different demosaic; screening evidence only")
    ap.add_argument("--resume",action="store_true")
    ap.add_argument("--candidates",help="comma-separated registered candidate names")
    ap.add_argument("--ablation-groups",type=int,default=24)
    args=ap.parse_args(argv)
    os.environ.setdefault("HF_HUB_OFFLINE","1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE","1")
    shard = None
    if args.shard:
        try:
            index, count = map(int, args.shard.split("/"))
            if count < 1 or not 0 <= index < count:
                raise ValueError
            shard = (index, count)
        except ValueError:
            ap.error("--shard must be INDEX/COUNT, with zero-based INDEX < COUNT")
    if args.action == "prepare":
        if not args.tuning or not args.holdout:
            ap.error("prepare requires both --tuning and --holdout local directories")
        report = prepare_experiment(args.run_dir, args.tuning, args.holdout)
        import json
        print(json.dumps(report["sources"], indent=2))
        return int(bool(report["source_errors"]))
    if args.action=="freeze":
        report=lock_experiment(args.run_dir,ablation_groups=args.ablation_groups)
        print("Frozen",report["lock_hash"])
        return 0
    if args.action == "merge":
        if args.shard_count is None:
            ap.error("merge requires --shard-count")
        report = merge_evaluations(args.run_dir, args.split, suite=args.suite, max_px=args.max_px,
                                   half_size=args.half_size, shard_count=args.shard_count)
    else:
        report=evaluate(args.run_dir,"tuning" if args.action=="tune" else "holdout",suite=args.suite,
                        max_px=args.max_px,half_size=args.half_size,resume=args.resume,
                        names=[name.strip() for name in args.candidates.split(",")] if args.candidates else None,
                        shard=shard)
    import json
    print(json.dumps(report["summary"],indent=2))
    # Unsafe measured candidates are experiment results, not accepted production outputs.
    return 0 if report["complete"] and not any(s["errors"] for s in report["summary"].values()) else 1


if __name__=="__main__":
    raise SystemExit(main())
