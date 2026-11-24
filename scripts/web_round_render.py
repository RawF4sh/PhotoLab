"""Prime one real SAM model for multiple frozen lists, or run a tone gate against its cached masks.

  python -I scripts/web_round_render.py prime --cache out-webloop/cache \
      --job blue_hour out-webloop/iter-01/configs out-webloop/panel-blue_hour.txt \
      --job natural out-webloop/iter-01/configs out-webloop/panel-control.txt
  python -I scripts/web_round_render.py tone --cache out-webloop/cache \
      --style blue_hour --configs out-webloop/iter-01/configs --list out-webloop/panel-blue_hour.txt \
      --out out-webloop/iter-01/tone-candidate.json

Use contact_sheet.py --require-cached-masks --backend sam3 for native render workers.
Specify the same --render-max-px on prime and contact_sheet for display-size comparisons.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import runpy
import sys
import tempfile
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent


def sources(listed):
    paths = [
        Path(line.split("\t")[0])
        for line in Path(listed).read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not paths or len(set(p.stem for p in paths)) != len(paths):
        raise ValueError("the list must contain distinct photo IDs")
    return paths


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="action", required=True)
    prime = commands.add_parser("prime")
    prime.add_argument(
        "--job",
        nargs=3,
        metavar=("STYLE", "CONFIGS", "LIST"),
        action="append",
        required=True,
    )
    prime.add_argument("--render-max-px", type=int)
    tone = commands.add_parser("tone")
    for arg in ("style", "configs", "list", "out"):
        tone.add_argument("--" + arg, required=True)
    for cmd in (prime, tone):
        cmd.add_argument("--cache", required=True, type=Path)
    args = parser.parse_args()
    if args.action == "tone":
        os.environ["AUTOEDIT_CONFIGS_DIR"] = str(Path(args.configs).resolve())
    sys.path[:0] = [str(SCRIPTS.parent / "src"), str(SCRIPTS)]
    from contact_sheet import DiskSegmenter
    from autoedit.cli import load_run_config
    from autoedit.imageio import downscale_long_edge, load_image, raw_opts_from
    from autoedit.pipeline import Pipeline

    root = str((args.cache / "masks-sam3").resolve())
    if args.action == "prime":
        segmenter = None
        if args.render_max_px is not None and args.render_max_px < 64:
            parser.error("render size must be at least 64 pixels")
        for style, config, listed in args.job:
            cfg = load_run_config(
                argparse.Namespace(configs_dir=config, backend="sam3", style=style)
            )
            pipe = Pipeline.build(cfg)
            if segmenter is None:
                segmenter = DiskSegmenter(pipe.segmenter, root)
            pipe.segmenter = segmenter
            paths = sources(listed)
            for i, path in enumerate(paths, 1):
                if args.render_max_px:
                    work = downscale_long_edge(
                        load_image(str(path), raw_opts_from(cfg)), args.render_max_px
                    )
                    pipe.process_one(str(path), image=work, do_apply=False)
                else:
                    pipe.presegment(str(path))
                print(f"{style}: masks ready {i}/{len(paths)}", flush=True)
        return
    from autoedit import quality_runner

    build = quality_runner.build_safe_engine

    def use_frozen(*a, **kw):
        engine = build(*a, **kw)
        engine.segmenter = DiskSegmenter(engine.segmenter, root, require_cached=True)
        return engine

    cfg = load_run_config(
        argparse.Namespace(configs_dir=args.configs, backend="sam3", style=args.style)
    )
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent) as directory:
        for path in sources(args.list):
            (Path(directory) / path.name).symlink_to(path.resolve())
        # The native proxy is the quality report's working size, so both use exactly the same SAM pixels.
        argv = [
            str(SCRIPTS / "tone_report.py"),
            directory,
            "--render",
            "--backend",
            "sam3",
            "--style",
            args.style,
            "--max-px",
            str(cfg.analysis_long_edge),
            "--fail-on-regression",
            "--json",
            str(out),
        ]
        with (
            patch.object(quality_runner, "build_safe_engine", use_frozen),
            patch.object(sys, "argv", argv),
        ):
            runpy.run_path(argv[0], run_name="__main__")


if __name__ == "__main__":
    main()
