"""Phase-2 blind-test harness (skeleton).

Renders each corpus photo three ways — AutoEdit / a global-auto baseline / unedited — into
a hash-labeled, shuffled gallery + CSV so photographers can score blind. This number is the
Phase-2 go/kill gate (must clearly beat the baseline; low halo/banding; ~0 catastrophic).

TODO(Phase 2): implement the global-auto baseline renderer and the shuffled-gallery export.
For now this scaffolds the output layout and runs AutoEdit over the corpus.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autoedit import batch  # noqa: E402
from autoedit.cli import load_run_config  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus")
    ap.add_argument("out", nargs="?", default="dist/blindtest")
    ap.add_argument("--configs-dir", dest="configs_dir", default=None)
    args = ap.parse_args()
    args.config = None
    for k in ("preset", "concepts", "look", "intensity", "decider", "llm_mode",
              "backend", "device", "format", "quality", "analysis_long_edge", "emit"):
        setattr(args, k, None)

    cfg = load_run_config(args)
    os.makedirs(args.out, exist_ok=True)
    batch.run(cfg, args.corpus, os.path.join(args.out, "autoedit"), do_apply=True)
    print("TODO(Phase 2): add global-auto baseline + shuffled hash-labeled gallery + CSV")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
