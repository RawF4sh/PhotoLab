"""Run the unattended, source-preserving quality workflow. Never prompts for input."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autoedit.contract import RunConfig
from autoedit.quality_runner import run_quality


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--backend", choices=["stub", "sam3"], default="stub")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--workflow", choices=["current", "global-first", "optimized"], default="current")
    ap.add_argument("--config", help="JSON or TOML with run_config and options blocks")
    ap.add_argument("--manifest", help="frozen capture selection")
    ap.add_argument("--split", choices=["tuning", "holdout"])
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--max-px", type=int, help="diagnostic resized export, not native validation")
    ap.add_argument("--limit", type=int, help="diagnostic subset, not a complete corpus evaluation")
    ap.add_argument("--raw-mode", default=None)
    args = ap.parse_args(argv)
    # The autonomous workflow never requires network access for SAM model discovery.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    data = {}
    if args.config:
        p = Path(args.config)
        data = tomllib.loads(p.read_text()) if p.suffix == ".toml" else json.loads(p.read_text())
    kw = {**data.get("run_config", {}), "backend": args.backend, "device": args.device}
    options = dict(data.get("options", {}))
    if args.raw_mode:
        kw["raw_mode"] = args.raw_mode
        # B/C distinguish their implicit default from an explicitly selected mode.
        if args.workflow != "current":
            options["raw_mode"] = args.raw_mode
    try:
        report = run_quality(args.input, args.output, RunConfig(**kw), workflow=args.workflow,
                             options=options, resume=args.resume,
                             source_manifest=args.manifest, split=args.split,
                             max_px=args.max_px, limit=args.limit)
    except Exception as exc:
        print(f"QUALITY WORKFLOW FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"Accepted {report['processed']}/{report['expected']}; failures={report['failed']}; "
          f"fallbacks={report['fallbacks']}; source errors={len(report['source_errors'])}")
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
