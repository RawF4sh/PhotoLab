#!/usr/bin/env bash
# Run AutoEdit over a folder without installing the package. Fast dev loop.
#   scripts/dev-run.sh [INPUT_DIR] [OUT_DIR] [extra autoedit flags...]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IN="${1:-$ROOT/test-corpus}"
OUT="${2:-$ROOT/out}"
PYTHONPATH="$ROOT/src" python3 -m autoedit.cli run "$IN" "$OUT" --backend stub "${@:3}"
