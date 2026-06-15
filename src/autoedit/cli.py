"""AutoEdit CLI (argparse — no third-party CLI dep so the core runs anywhere).

Commands:
  autoedit run INPUT_DIR [OUT_DIR] [flags]   batch-edit a folder
  autoedit doctor                            report device + backend availability
  autoedit presets                           list concept presets

Config layering: CLI flags > --config run.toml > configs/defaults.toml.
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib

from . import paths
from .contract import RunConfig


def _flatten(d: dict) -> dict:
    """Map a defaults-shaped TOML ([run]/[segment]/[output]/[llm]) to RunConfig kwargs."""
    run, seg, out, llm = (d.get(k, {}) for k in ("run", "segment", "output", "llm"))
    kw: dict = {}
    for k in ("style", "prompt", "preset", "look", "intensity", "decider", "emit"):
        if k in run:
            kw[k] = run[k]
    for k in ("backend", "device", "analysis_long_edge"):
        if k in seg:
            kw[k] = seg[k]
    if "format" in out:
        kw["out_format"] = out["format"]
    if "quality" in out:
        kw["quality"] = out["quality"]
    _llm_map = {"mode": "llm_mode", "model": "llm_model", "temperature": "llm_temperature",
                "max_calls": "llm_max_calls", "budget_usd": "llm_budget_usd", "thumb_px": "llm_thumb_px"}
    for src, dst in _llm_map.items():
        if src in llm:
            kw[dst] = llm[src]
    return kw


def _read_toml(path: str) -> dict:
    if not path or not os.path.exists(path):
        return {}
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def load_run_config(args) -> RunConfig:
    configs_dir = args.configs_dir or paths.configs_dir()
    kw = _flatten(_read_toml(os.path.join(configs_dir, "defaults.toml")))
    kw["rules_path"] = os.path.join(configs_dir, "rules.toml")
    kw["looks_path"] = os.path.join(configs_dir, "looks.toml")
    kw["concepts_path"] = os.path.join(configs_dir, "concepts.toml")
    if getattr(args, "config", None):
        kw.update(_flatten(_read_toml(args.config)))

    # CLI overrides (only when explicitly provided)
    overrides = {
        "style": args.style,
        "prompt": args.prompt,
        "preset": args.preset,
        "look": args.look,
        "intensity": args.intensity,
        "decider": args.decider,
        "allow_cloud": args.allow_cloud,
        "api_key": args.api_key,
        "llm_mode": args.llm_mode,
        "backend": args.backend,
        "device": args.device,
        "out_format": args.format,
        "quality": args.quality,
        "analysis_long_edge": args.analysis_long_edge,
        "emit": args.emit,
        "compose": args.compose,
    }
    for k, v in overrides.items():
        if v is not None:
            kw[k] = v
    if args.concepts:
        kw["concepts"] = [c.strip() for c in args.concepts.split(",") if c.strip()]
    return RunConfig(**kw)


def _add_run_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("input_dir")
    p.add_argument("out_dir", nargs="?", default="out")
    p.add_argument("--style", help="style profile from styles.toml (look + concepts + brief)")
    p.add_argument("--prompt", help='free-text intent, layered on the style brief (e.g. "moody, warm, lift shadows")')
    p.add_argument("--preset", help="concept-set override from concepts.toml")
    p.add_argument("--concepts", help='comma-separated, overrides --preset (e.g. "sky,skin,dress")')
    p.add_argument("--look", help="magnitude profile override (defaults to the style's look)")
    p.add_argument("--intensity", type=float)
    p.add_argument("--decider", choices=["rules", "llm"])
    p.add_argument("--allow-cloud", dest="allow_cloud", action="store_true", default=None,
                   help="consent to the BYO-key cloud LLM (sends a thumbnail to your AI provider)")
    p.add_argument("--api-key", dest="api_key", help="Anthropic API key (else ANTHROPIC_API_KEY env)")
    p.add_argument("--llm-mode", dest="llm_mode", choices=["refine", "propose"])
    p.add_argument("--backend", choices=["stub", "sam3"])
    p.add_argument("--device")
    p.add_argument("--format", choices=["jpeg", "tiff"])
    p.add_argument("--quality", type=int)
    p.add_argument("--analysis-long-edge", dest="analysis_long_edge", type=int)
    p.add_argument("--emit", choices=["baked", "editable", "both"])
    p.add_argument("--no-compose", dest="compose", action="store_false", default=None,
                   help="disable radial/linear compositional moves (vignette, sky grad)")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="segment + decide + sidecar, no baked pixels")
    p.add_argument("--config", help="run.toml overriding defaults")
    p.add_argument("--configs-dir", dest="configs_dir", help="directory with rules/looks/concepts/defaults")


def _cmd_run(args) -> int:
    from . import batch

    cfg = load_run_config(args)
    if not os.path.isdir(args.input_dir):
        print(f"input_dir not found: {args.input_dir}", file=sys.stderr)
        return 2
    batch.run(cfg, args.input_dir, args.out_dir, do_apply=not args.dry_run, resume=args.resume)
    return 0


def _cmd_doctor(args) -> int:
    from .decide import DISCLOSURE

    print("AutoEdit doctor")
    print("  segmentation:")
    try:
        import torch  # noqa: F401
        from .segment.sam3 import pick_device

        print(f"    torch: available, device(auto) -> {pick_device('auto')}")
        print("    sam3 backend: usable. Weights fetch from Hugging Face on first run "
              "(Meta custom SAM License — not MIT; check terms before commercial use).")
    except Exception as e:
        print(f"    torch/sam3: NOT available ({e})")
        print("    -> stub backend works now; `pip install '.[sam]'` for real SAM 3")
    print("  cloud LLM (BYO-key, optional):")
    try:
        import anthropic  # noqa: F401

        has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        consent = os.environ.get("AUTOEDIT_ALLOW_CLOUD", "").lower() in ("1", "true", "yes")
        print(f"    anthropic: available | ANTHROPIC_API_KEY {'set' if has_key else 'MISSING'} "
              f"| consent {'ON' if consent else 'OFF (use --allow-cloud)'}")
    except Exception:
        print("    anthropic: not installed (`pip install '.[llm]'` for the LLM decider)")
    print(f"    disclosure: {DISCLOSURE}")
    return 0


def _cmd_presets(args) -> int:
    from .concepts import load_concepts

    configs_dir = args.configs_dir or paths.configs_dir()
    presets, _ = load_concepts(os.path.join(configs_dir, "concepts.toml"))
    for name, concepts in presets.items():
        print(f"{name}: {', '.join(concepts)}")
    return 0


def _cmd_styles(args) -> int:
    from .styles import load_styles

    configs_dir = args.configs_dir or paths.configs_dir()
    styles = load_styles(os.path.join(configs_dir, "styles.toml"))
    for name, s in styles.items():
        print(f"{name}  (look={s.look})\n    {s.brief}")
    return 0


def _cmd_ui(args) -> int:
    from .web.app import serve

    try:
        serve(host=args.host, port=args.port, configs_dir=args.configs_dir or paths.configs_dir(),
              open_browser=not args.no_open)
    except ImportError as e:
        print(f"The UI needs the [ui] extra:  pip install '.[ui]'   ({e})", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autoedit", description="Open-vocabulary batch photo auto-editor")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="batch-edit a folder")
    _add_run_flags(run_p)
    run_p.set_defaults(func=_cmd_run)

    doc_p = sub.add_parser("doctor", help="report device + backend availability")
    doc_p.add_argument("--configs-dir", dest="configs_dir")
    doc_p.set_defaults(func=_cmd_doctor)

    pre_p = sub.add_parser("presets", help="list concept presets")
    pre_p.add_argument("--configs-dir", dest="configs_dir")
    pre_p.set_defaults(func=_cmd_presets)

    sty_p = sub.add_parser("styles", help="list style profiles")
    sty_p.add_argument("--configs-dir", dest="configs_dir")
    sty_p.set_defaults(func=_cmd_styles)

    ui_p = sub.add_parser("ui", help="launch the local web app")
    ui_p.add_argument("--host", default="127.0.0.1")
    ui_p.add_argument("--port", type=int, default=8765)
    ui_p.add_argument("--no-open", dest="no_open", action="store_true", help="don't open the browser")
    ui_p.add_argument("--configs-dir", dest="configs_dir")
    ui_p.set_defaults(func=_cmd_ui)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
