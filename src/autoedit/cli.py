"""AutoEdit CLI (argparse — no third-party CLI dep so the core runs anywhere).

Commands:
  autoedit run INPUT_DIR [OUT_DIR] [flags]   batch-edit a folder
  autoedit adjust PHOTO [--set T.OP=V] [--edit TEXT] [--mark X0,Y0,X1,Y1 [--mark-kind K]] [--explain] [--reset]
                                             re-render one photo
  autoedit curate INPUT_DIR OUT_DIR          copy a diverse subset for review/training
  autoedit doctor                            report device + backend availability
  autoedit models status|download|remove     SAM 3 weights
  autoedit presets                           list concept presets

Config layering: CLI flags > --config run.toml > configs/defaults.toml.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tomllib

from . import paths
from .contract import RunConfig


def _flatten(d: dict) -> dict:
    """Map a defaults-shaped TOML ([run]/[segment]/[output]/[llm]) to RunConfig kwargs."""
    run, seg, out, llm, dec, perf = (d.get(k, {}) for k in ("run", "segment", "output", "llm", "decode", "performance"))
    kw: dict = {}
    _dec_map = {"raw_mode": "raw_mode", "max_ev": "raw_max_ev", "clip_budget": "raw_clip_budget",
                "tone_target_p50": "raw_tone_target_p50", "fallback_ev": "raw_fallback_ev"}
    for src, dst in _dec_map.items():
        if src in dec:
            kw[dst] = dec[src]
    for k in ("style", "prompt", "edit", "preset", "look", "intensity", "palette", "palette_strength",
              "decider", "emit", "consistency", "levels", "denoise", "highlight_hold", "light_shaping",
              "translator"):
        if k in run:
            kw[k] = run[k]
    for k in ("backend", "device", "analysis_long_edge", "mask_cache"):
        if k in seg:
            kw[k] = seg[k]
    for k in ("workers", "threads"):
        if k in perf:
            kw[k] = perf[k]
    if "format" in out:
        kw["out_format"] = out["format"]
    if "quality" in out:
        kw["quality"] = out["quality"]
    for src, dst in (("metadata", "metadata"), ("long_edge", "out_long_edge"), ("name", "out_name"),
                     ("subdir", "out_subdir"), ("export", "export")):
        if src in out:
            kw[dst] = out[src]
    _llm_map = {"mode": "llm_mode", "model": "llm_model", "temperature": "llm_temperature",
                "max_calls": "llm_max_calls", "budget_usd": "llm_budget_usd", "thumb_px": "llm_thumb_px",
                "input_usd_per_mtok": "llm_input_usd_per_mtok",
                "output_usd_per_mtok": "llm_output_usd_per_mtok",
                "translate_instructions": "llm_translate_instructions"}
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
    profile = getattr(args, "profile", None)
    defaults_path = os.path.join(configs_dir, "defaults.toml")
    kw = _flatten(paths.read_config(defaults_path))
    kw.update(paths.config_paths(configs_dir, profile))
    # What the user wrote down: the profile's defaults and the run file.
    explicit = _flatten((paths._overlay("defaults.toml", profile) or ("", {}))[1])
    if profile:
        paths.read_config(defaults_path, profile)   # only to reject an overlay that cannot merge
    if getattr(args, "config", None):
        explicit.update(_flatten(_read_toml(args.config)))
    # Order, lowest to highest: the packaged defaults, the preset, what the files say, the flags.
    # A preset named by a flag or a file can be switched off with `--export ""`.
    preset = getattr(args, "export", None)
    if preset is None:
        preset = explicit.get("export") or kw.get("export")
    if preset:
        from . import exports

        kw.update(exports.preset_kwargs(
            preset, kw.get("exports_path") or paths.config_path("exports.toml", packaged_fallback=True)))
    kw.update(explicit)
    kw["export"] = preset or None

    # CLI overrides (only when explicitly provided)
    overrides = {
        "style": getattr(args, "style", None),
        "prompt": getattr(args, "prompt", None),
        "edit": getattr(args, "edit", None),
        "preset": getattr(args, "preset", None),
        "look": getattr(args, "look", None),
        "intensity": getattr(args, "intensity", None),
        "palette": getattr(args, "palette", None),
        "palette_strength": getattr(args, "palette_strength", None),
        "reference": getattr(args, "reference", None),
        "reference_strength": getattr(args, "reference_strength", None),
        "decider": getattr(args, "decider", None),
        "recipe_model": getattr(args, "recipe_model", None),
        "allow_cloud": getattr(args, "allow_cloud", None),
        "api_key": getattr(args, "api_key", None),
        "llm_mode": getattr(args, "llm_mode", None),
        "backend": getattr(args, "backend", None),
        "device": getattr(args, "device", None),
        "raw_mode": getattr(args, "raw_mode", None),
        "out_format": getattr(args, "format", None),
        "quality": getattr(args, "quality", None),
        "metadata": getattr(args, "metadata", None),
        "out_long_edge": getattr(args, "long_edge", None),
        "out_name": getattr(args, "name", None),
        "out_subdir": getattr(args, "subdir", None),
        "analysis_long_edge": getattr(args, "analysis_long_edge", None),
        "mask_cache": getattr(args, "mask_cache", None),
        "workers": getattr(args, "workers", None),
        "threads": getattr(args, "threads", None),
        "emit": getattr(args, "emit", None),
        "consistency": getattr(args, "consistency", None),
        "compose": getattr(args, "compose", None),
        "vignette": getattr(args, "vignette", None),
        "sharpen": getattr(args, "sharpen", None),
        "levels": getattr(args, "levels", None),
        "denoise": getattr(args, "denoise", None),
        "highlight_hold": getattr(args, "highlight_hold", None),
        "light_shaping": getattr(args, "light_shaping", None),
        "translator": getattr(args, "translator", None),
    }
    for k, v in overrides.items():
        if v is not None:
            kw[k] = v
    concepts = getattr(args, "concepts", None)
    if concepts:
        kw["concepts"] = [c.strip() for c in concepts.split(",") if c.strip()]
    return RunConfig(**kw)


def _add_run_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("input_dir")
    p.add_argument("out_dir", nargs="?", default="out")
    p.add_argument("--style", help="style profile from styles.toml (look + concepts + brief)")
    p.add_argument("--prompt", help='free-text intent, layered on the style brief (e.g. "moody, warm, lift shadows")')
    p.add_argument("--edit", help='plain-language region instructions, e.g. "brighten the jacket, darken the sky slightly"')
    p.add_argument("--translator", choices=["auto", "local", "off"],
                   help="a small model on this computer (Ollama) reads --edit text the word list misses: "
                        "auto (when it is running) | local (also when --decider llm is on) | off")
    p.add_argument("--preset", help="concept-set override from concepts.toml")
    p.add_argument("--concepts", help='comma-separated, overrides --preset (e.g. "sky,skin,dress")')
    p.add_argument("--look", help="magnitude profile override (defaults to the style's look)")
    p.add_argument("--palette", help='palette grade from palettes.toml ("auto", "cool_blue", "teal_orange"; "off" disables)')
    p.add_argument("--palette-strength", dest="palette_strength", type=float,
                   help="palette strength 0..1 (default 0.8)")
    p.add_argument("--reference", help="reference photo whose look (palette/tone) to match onto the batch")
    p.add_argument("--reference-strength", dest="reference_strength", type=float,
                   help="reference-match strength 0..1 (default 0.7)")
    p.add_argument("--intensity", type=float)
    p.add_argument("--decider", choices=["rules", "llm", "learned"])
    p.add_argument("--recipe-model", dest="recipe_model",
                   help="trained recipe predictor JSON (used with --decider learned)")
    p.add_argument("--allow-cloud", dest="allow_cloud", action="store_true", default=None,
                   help="consent to the BYO-key cloud LLM (sends a thumbnail to your AI provider)")
    p.add_argument("--api-key", dest="api_key", help="Anthropic API key (else ANTHROPIC_API_KEY env)")
    p.add_argument("--llm-mode", dest="llm_mode", choices=["refine", "propose"])
    p.add_argument("--backend", choices=["stub", "sam3", "auto"])
    p.add_argument("--device")
    p.add_argument("--raw-mode", dest="raw_mode",
                   choices=["none", "linear_srgb", "libraw_auto", "preview_ev", "preview_ev_headroom", "tone_target"],
                   help="RAW development: none (default libraw render) | libraw_auto | preview_ev | "
                        "preview_ev_headroom | tone_target | linear_srgb (see scripts/raw_benchmark.py)")
    p.add_argument("--format", choices=["jpeg", "tiff", "webp", "png"])
    p.add_argument("--quality", type=int)
    p.add_argument("--metadata", choices=["keep", "shareable", "none"],
                   help="EXIF carried into exports: keep (all, with GPS and serial numbers) | "
                        "shareable (default: without them) | none")
    p.add_argument("--long-edge", dest="long_edge", type=int,
                   help="longest side of the export in pixels (never upscales; default full size)")
    p.add_argument("--name", help="export file name template without extension: {stem} {style} {n}, e.g. {stem}_web")
    p.add_argument("--subdir", help="subfolder of the output folder for the exports")
    p.add_argument("--export", help="export preset from exports.toml (full, web, social, archive, webp); "
                                    "other flags win over it")
    p.add_argument("--analysis-long-edge", dest="analysis_long_edge", type=int)
    p.add_argument("--emit", choices=["baked"], help="output kind (only baked pixels exist today; an XMP hand-off is not implemented)")
    p.add_argument("--no-compose", dest="compose", action="store_false", default=None,
                   help="disable radial/linear compositional moves (vignette, sky grad)")
    p.add_argument("--no-vignette", dest="vignette", action="store_false", default=None,
                   help="disable the frame/subject vignette")
    p.add_argument("--no-sharpen", dest="sharpen", action="store_false", default=None,
                   help="disable output sharpening")
    p.add_argument("--levels", dest="levels", action="store_true", default=None,
                   help="pull the measured black (and white) point of each frame to a true black (opt-in)")
    p.add_argument("--denoise", dest="denoise", action="store_true", default=None,
                   help="reduce colour and luma noise on high-ISO frames, chroma first (opt-in)")
    p.add_argument("--highlight-hold", dest="highlight_hold", action="store_true", default=None,
                   help="keep highlights that are blown in the source white instead of letting the edit grey them (opt-in)")
    p.add_argument("--light-shaping", dest="light_shaping", action="store_true", default=None,
                   help="add a soft warm glow where the sun is, when the photo has a clear light source (opt-in)")
    p.add_argument("--workers", type=int,
                   help="render processes for a batch (0 = half the cores, at most 4)")
    p.add_argument("--threads", type=int,
                   help="threads for per-pixel work inside one render (0 = one per core)")
    p.add_argument("--no-mask-cache", dest="mask_cache", action="store_false", default=None,
                   help="do not keep raw masks under <out>/.autoedit-masks")
    p.add_argument("--no-consistent", dest="consistent", action="store_false", default=None,
                   help="disable shoot-consistency (the bounded per-group brightness normalization)")
    p.add_argument("--consistency", choices=["brightness", "bursts"],
                   help="shoot consistency: brightness (default) | bursts (even out frames of one framing)")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="segment + decide + sidecar, no baked pixels")
    p.add_argument("--config", help="run.toml overriding defaults")
    p.add_argument("--configs-dir", dest="configs_dir", help="directory with rules/looks/concepts/defaults")
    p.add_argument("--profile", help="profile folder with your own config differences (default: $AUTOEDIT_PROFILE, else ~/.config/autoedit if it exists)")


def _cmd_run(args) -> int:
    from . import batch

    try:
        cfg = load_run_config(args)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    if not os.path.isdir(args.input_dir):
        print(f"input_dir not found: {args.input_dir}", file=sys.stderr)
        return 2
    try:
        batch.run(cfg, args.input_dir, args.out_dir, do_apply=not args.dry_run, resume=args.resume,
                  consistent=args.consistent is not False, workers=cfg.workers)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    return 0


def _cmd_adjust(args) -> int:
    from . import adjust

    mark = None
    if args.mark is not None:
        try:
            mark = [float(v) for v in args.mark.split(",")]
            if len(mark) != 4:
                raise ValueError
        except ValueError:
            print("--mark needs four numbers: X0,Y0,X1,Y1 (shares of the frame)", file=sys.stderr)
            return 2
    try:
        r = adjust.rerender(args.target, sets=args.set, edit=args.edit, reset=args.reset,
                            mark=mark, mark_kind=args.mark_kind, explain=args.explain,
                            source=args.source, configs_dir=args.configs_dir,
                            reference=args.reference, recipe_model=args.recipe_model,
                            allow_cloud=args.allow_cloud, profile=args.profile)
    except (FileNotFoundError, ValueError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 2
    adjs = r["adjustments"]
    count = lambda s: sum(1 for a in adjs if a["status"] == s)  # noqa: E731
    print(f"Re-rendered {os.path.basename(r['image'])}: {len(adjs)} adjustments "
          f"({count('applied')} applied, {count('clamped')} clamped, "
          f"{count('blocked')} blocked, {count('not_found')} not found)")
    for problem in r["problems"]:
        print(f"  instruction {problem}")
    for warning in r.get("warnings", []):
        print(f"warning: {warning}")
    if args.explain:
        print("\n".join(adjust.explain_lines(r["walkthrough"])))
    return 0


def _cmd_rank_variants(args) -> int:
    import csv

    from . import batch
    from .web import service

    images = batch.list_images(args.input) if os.path.isdir(args.input) else [args.input]
    if not images:
        print(f"no images at {args.input}", file=sys.stderr)
        return 2
    opts = {"aesthetic_model": args.aesthetic_model} if args.aesthetic_model else {}
    rows = []
    for p in images:
        try:
            r = service.make_variants(p, opts, with_images=False)
        except Exception as e:
            print(f"  {os.path.basename(p)}: {e}", file=sys.stderr)
            continue
        best, review = r["best"], r["review"]
        print(f"  {os.path.basename(p)} → best: {best}{'  (needs review)' if review else ''}")
        for v in r["variants"]:
            rows.append({"image": os.path.basename(p), "variant": v["name"], "score": v["score"],
                         "quality": v["quality"], "aesthetic": v["aesthetic"],
                         "best": "yes" if v["name"] == best else "", "review": "yes" if review else ""})
    base = args.input if os.path.isdir(args.input) else os.path.dirname(args.input)
    out = args.out or os.path.join(base, "variant-ranking.csv")
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["image", "variant", "score", "quality", "aesthetic", "best", "review"])
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {out}")
    return 0


def _cmd_train_recipe(args) -> int:
    from . import recipe

    if not os.path.isdir(args.dataset):
        print(f"dataset not found: {args.dataset}", file=sys.stderr)
        return 2
    try:
        info = recipe.train_from_dataset(args.dataset, args.out_model, ridge=args.ridge)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(f"Trained recipe predictor on {info['samples']} pairs (skipped {info['skipped']}).")
    print(f"Model: {info['model_path']}  —  run with: --decider learned --recipe-model {info['model_path']}")
    return 0


def _cmd_dataset_validate(args) -> int:
    from . import dataset

    rep = dataset.validate(args.originals, args.targets, recursive=args.recursive)
    print(f"Pairs: {rep['pairs']}  passed: {rep['passed']}  rejected: {rep['rejected']}  "
          f"(unmatched: {rep['unmatched_originals']} orig / {rep['unmatched_targets']} target)")
    for r in rep["results"]:
        flag = "OK  " if r["ok"] else "REJ "
        reasons = "" if r["ok"] else "  — " + ", ".join(r["reasons"])
        print(f"  {flag}{os.path.basename(r['target'])}  edge_corr={r['metrics'].get('edge_corr','?')}{reasons}")
    return 0


def _cmd_dataset_build(args) -> int:
    from . import dataset

    m = dataset.build_dataset(args.originals, args.targets, args.out, recursive=args.recursive,
                              do_validate=args.validate, approve_all=args.approve_all)
    print(f"Built dataset at {m['out_dir']}: {m['kept']} kept, {m['rejected']} rejected "
          f"of {m['pairs']} pairs. (originals/ targets/ rejected/ labels.csv manifest.json)")
    return 0


def _cmd_curate(args) -> int:
    from . import curate

    if not os.path.isdir(args.input_dir):
        print(f"input_dir not found: {args.input_dir}", file=sys.stderr)
        return 2
    manifest = curate.curate(
        args.input_dir,
        args.out_dir,
        count=args.count,
        recursive=not args.no_recursive,
        proxy_long_edge=args.analysis_long_edge,
        scan_limit=args.scan_limit,
        copy_originals=True,
        mode=args.mode,
        emit_xmp=args.emit_xmp,
        aesthetic_model_path=args.aesthetic_model,
    )
    print(
        f"\nCopied {manifest['selected']} of {manifest['total_scanned']} scanned photos to "
        f"{os.path.abspath(os.path.join(args.out_dir, 'selected'))}"
    )
    print(f"Review: {os.path.abspath(os.path.join(args.out_dir, 'contact-sheet.html'))}")
    print(f"Manifest: {os.path.abspath(os.path.join(args.out_dir, 'candidates.json'))}")
    return 0


def _cmd_train_aesthetic(args) -> int:
    # Thin wrapper over critic.train_from_csv — the web API uses the same function, so the CSV
    # column detection / AVA parsing / feature extraction can't drift between CLI and UI.
    from . import critic

    if not os.path.isdir(args.images_dir):
        print(f"images_dir not found: {args.images_dir}", file=sys.stderr)
        return 2
    if not os.path.exists(args.labels_csv):
        print(f"labels_csv not found: {args.labels_csv}", file=sys.stderr)
        return 2
    try:
        info = critic.train_from_csv(
            args.labels_csv, args.images_dir, args.out_model,
            path_column=args.path_column, score_column=args.score_column,
            ridge=args.ridge, long_edge=args.analysis_long_edge, limit=args.limit)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(f"Trained aesthetic scorer on {info['samples']} images; skipped {info['skipped']}.")
    print(f"Model: {info['model_path']}")
    return 0


def _cmd_doctor(args) -> int:
    from .decide import DISCLOSURE

    print("AutoEdit doctor")
    # a stray ./configs in the CWD silently overrides packaged tuning — always show which won
    print(f"  configs: {os.path.abspath(args.configs_dir or paths.configs_dir())}")
    if args.profile:
        known = {f"{n}.toml" for n in paths._CONFIG_FILES} | {"defaults.toml"}
        print(f"  profile: {args.profile}")
        for f in sorted(os.listdir(args.profile)):
            print(f"    {f}" + ("" if f in known else "  (not a config file, ignored)"))
    print("  segmentation:")
    try:
        import torch  # noqa: F401
        from .segment.sam3 import pick_device

        dev = pick_device("auto")
        print(f"    torch: available, device(auto) -> {dev}")
        try:
            from transformers import Sam3Model, Sam3Processor  # noqa: F401

            print("    sam3 backend: ready (Transformers has SAM 3).")
            print("      weights are gated — accept the license at "
                  "https://huggingface.co/facebook/sam3 then `huggingface-cli login` "
                  "(Meta custom SAM License — not MIT; check terms before commercial use).")
            if dev == "mps":
                print("      apple silicon: runs on mps; PYTORCH_ENABLE_MPS_FALLBACK is on for "
                      "ops MPS lacks.")
        except Exception as e:
            print(f"    sam3 backend: torch OK but Transformers>=5.12 w/ SAM 3 missing ({e})")
            print("    -> `pip install -U '.[sam]'`")
    except Exception as e:
        print(f"    torch/sam3: NOT available ({e})")
        print("    -> stub backend works now; `pip install '.[sam]'` for real SAM 3")
    from .segment import resolve_backend
    print(f'    backend "auto" would use: {resolve_backend("auto")}')
    print("    weights and packages: `autoedit models status`")
    print("  input formats:")
    try:
        from PIL import features
        avif = bool(features.check("avif"))
    except Exception:
        avif = False
    import importlib.util
    heif = importlib.util.find_spec("pillow_heif") is not None
    print("    avif: " + ("readable" if avif else "NOT readable, needs Pillow 11.3 or later"))
    print("    heic/heif: " + ("readable" if heif else "needs `pip install '.[heif]'`"))
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


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB"


def _cmd_models(args) -> int:
    from . import models

    if args.models_cmd == "status":
        st = models.status()
        if st["cached"]:
            print(f"weights: in the local cache, {_gb(st['size_bytes'])} "
                  f"({st['revisions']} revision(s)) at {st['path']}")
        else:
            print("weights: not downloaded")
        for k in ("torch", "transformers", "hub"):
            print(f"{k if k != 'hub' else 'huggingface_hub'}: {'installed' if st[k] else 'missing'}")
        print(f"licence: {st['licence_url']}")
        from .segment import resolve_backend
        print(f'backend "auto" would use: {resolve_backend("auto")}')
        return 0
    try:
        if args.models_cmd == "download":
            print(f"SAM 3 weights are gated. Licence (Meta's SAM licence): {models.LICENCE_URL}")
            print(f"downloaded to: {models.download()}")
            return 0
        models._hub()  # fail early with the install hint, before any size or prompt
        size = models.status()["size_bytes"]
        if not args.yes:
            print(f"would free {_gb(size)}; run again with --yes to delete" if size
                  else "nothing to remove")
            return 1 if size else 0
        print(f"freed {_gb(models.remove())}")
        return 0
    except (PermissionError, RuntimeError) as e:
        print(str(e), file=sys.stderr)
        return 2


def _cmd_presets(args) -> int:
    from .concepts import load_concepts

    presets, _ = load_concepts(paths.config_paths(args.configs_dir, args.profile)["concepts_path"])
    for name, concepts in presets.items():
        print(f"{name}: {', '.join(concepts)}")
    return 0


def _cmd_styles(args) -> int:
    from .styles import load_styles

    styles = load_styles(paths.config_paths(args.configs_dir, args.profile)["styles_path"])
    for name, s in styles.items():
        print(f"{name}  (look={s.look})\n    {s.brief}")
    return 0


def _cmd_exports(args) -> int:
    from . import exports

    path = paths.config_paths(args.configs_dir, args.profile).get("exports_path") or paths.config_path(
        "exports.toml", packaged_fallback=True)
    for name, preset in exports.load(path).items():
        print(f"{name}: " + ", ".join(f"{k}={v}" for k, v in preset.items()))
    return 0


def _cmd_settings(args) -> int:
    import json

    from . import settings_schema

    data = settings_schema.schema(args.configs_dir, args.profile)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    for s in data["settings"]:
        rng = s["choices"] if "choices" in s else (f"{s.get('min', '')}..{s.get('max', '')}" if "min" in s or "max" in s else "")
        print(f"{s['group']:<13}{s['key']:<30}{s['type']:<8}{json.dumps(s['default']):<24}{rng if rng else ''}")
    return 0


_LOOK_NAME = re.compile(r"[a-z0-9_]+")


def _profile_looks(profile: str | None) -> dict:
    path = os.path.join(profile, "looks.toml") if profile else ""
    return paths.read_config(path) if path else {}


def write_look(profile: str, name: str, fields: dict) -> tuple[str, bool]:
    """Write look `name` with ALL its fields into <profile>/looks.toml, keeping the other looks in
    the file. All fields, because a profile table is merged over a packaged look of the same name:
    a table with only the changed fields would let the packaged values come back.
    Returns (path, whether the file existed)."""
    from dataclasses import asdict

    from . import looks as looks_mod

    defaults = asdict(looks_mod.Look())
    os.makedirs(profile, exist_ok=True)
    path = os.path.join(profile, "looks.toml")
    existed = os.path.isfile(path)
    data = paths.read_config(path) if existed else {}
    data[name] = {k: fields.get(k, defaults[k]) for k in looks_mod.FIELDS}
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(paths.dump_toml(data))
    return path, existed


def _cmd_looks(args) -> int:
    from dataclasses import asdict

    from . import looks as looks_mod

    base_path = os.path.join(args.configs_dir or paths.configs_dir(), "looks.toml")
    try:
        packaged = _read_toml(base_path)
        merged = looks_mod.load(paths.config_paths(args.configs_dir, args.profile)["looks_path"])
        own = _profile_looks(args.profile)
        if args.looks_cmd == "list":
            defaults = asdict(looks_mod.Look())
            for name, look in merged.items():
                origin = ("packaged, changed by the profile" if name in packaged else "profile") if name in own \
                    else "packaged"
                diff = ", ".join(f"{k}={v}" for k, v in asdict(look).items() if v != defaults[k])
                print(f"{name}  ({origin})  {diff}")
            return 0
        if args.looks_cmd == "delete":
            if args.name not in own:
                print(f"look {args.name!r} is " + ("packaged and the profile does not define it; "
                      "it cannot be deleted" if args.name in packaged else "not in the profile"), file=sys.stderr)
                return 2
            del own[args.name]
            with open(os.path.join(args.profile, "looks.toml"), "w", encoding="utf-8") as fh:
                fh.write(paths.dump_toml(own))
            return 0
        # save
        if not _LOOK_NAME.fullmatch(args.name):
            print(f"look name {args.name!r}: use lowercase letters, digits and underscores", file=sys.stderr)
            return 2
        if args.base not in merged:
            print(f"look {args.base!r} not found (known: {', '.join(merged)})", file=sys.stderr)
            return 2
        fields = asdict(merged[args.base])
        for item in args.set:
            field, eq, raw = item.partition("=")
            if not eq or field not in looks_mod.FIELDS:
                print(f"--set {item!r}: expected FIELD=VALUE with a field of: {', '.join(looks_mod.FIELDS)}",
                      file=sys.stderr)
                return 2
            if field == "preserve_exposure" and raw in ("true", "false"):
                fields[field] = raw == "true"
                continue
            try:
                value = float(raw)
            except ValueError:
                value = float("nan")
            if field == "preserve_exposure" or value != value or value in (float("inf"), float("-inf")):
                print(f"--set {item!r}: the value must be a finite number"
                      + (" (true or false for preserve_exposure)" if field == "preserve_exposure" else ""),
                      file=sys.stderr)
                return 2
            fields[field] = value
        path, existed = write_look(args.profile or paths.default_profile_dir(), args.name, fields)
        print(f"saved look {args.name} in {path}")
        if existed:
            print("note: the file was rewritten, so comments in it are gone")
        return 0
    except (ValueError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 2


def _cmd_calibrate(args) -> int:
    from . import calibrate

    log = lambda m: print(m, flush=True)  # noqa: E731
    try:
        if args.cont:
            r = calibrate.advance(args.cont, photos=args.photos, finish=args.finish, profile=args.profile,
                                  configs_dir=args.configs_dir, log=log)
        else:
            if not args.photos or not args.out:
                print("usage: autoedit calibrate PHOTOS --out DIR   (or: autoedit calibrate --continue DIR)",
                      file=sys.stderr)
                return 2
            if not _LOOK_NAME.fullmatch(args.name):
                print(f"look name {args.name!r}: use lowercase letters, digits and underscores", file=sys.stderr)
                return 2
            r = calibrate.start(args.photos, args.out, count=args.count, rounds=args.rounds, seed=args.seed,
                                backend=args.backend, style=args.style, look=args.look, name=args.name,
                                profile=args.profile, configs_dir=args.configs_dir, log=log)
            print(f"round 1: {r['pairs']} pairs rendered.\nOpen {os.path.join(r['folder'], 'index.html')}, vote, "
                  f"and save the downloaded results.csv into {r['folder']}\nthen run: "
                  f"autoedit calibrate --continue {args.out}")
            return 0
    except (ValueError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 2
    for dim, (plus, minus) in r["votes"].items():
        print(f"  {dim}: {plus} votes for higher values, {minus} for lower")
    if not r["done"]:
        print(f"round {r['round']}: open {os.path.join(r['folder'], 'index.html')}, vote, and save the "
              f"downloaded results.csv into {r['folder']}\nthen run: autoedit calibrate --continue {args.cont}")
        return 0
    from dataclasses import asdict

    from . import looks as looks_mod

    defaults = asdict(looks_mod.Look())
    print(f"look {r['name']} saved in {r['look_path']}")
    if r.get("replaced"):
        print(f"  it replaced the look {r['name']!r} that was in the profile")
    print("  " + ", ".join(f"{k}={v:.4g}" for k, v in r["base"].items()
                           if not isinstance(v, bool) and abs(v - defaults[k]) > 1e-9))
    print(f"use it with: autoedit run <in> <out> --look {r['name']}")
    return 0


def _cmd_ui(args) -> int:
    from .web.app import serve

    try:
        serve(host=args.host, port=args.port, configs_dir=args.configs_dir or paths.configs_dir(),
              open_browser=not args.no_open, unsafe_network=args.unsafe_network, profile=args.profile)
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

    adj_p = sub.add_parser("adjust", help="re-render one photo with its own adjustments")
    adj_p.add_argument("target", help="an exported photo or its .autoedit.json")
    adj_p.add_argument("--set", action="append", default=[], metavar="TARGET.OP=VALUE",
                       help="direct delta, e.g. sky.exposure=-0.3 (repeatable)")
    adj_p.add_argument("--edit", help='plain-language instruction, e.g. "warm the grass a little"')
    adj_p.add_argument("--mark", metavar="X0,Y0,X1,Y1",
                       help="an oval on the photo, as shares of the frame; --edit is then about the mark")
    adj_p.add_argument("--mark-kind", dest="mark_kind", choices=["auto", "area"], default="auto",
                       help="auto: a named thing follows its outline; area: the oval itself (default: auto)")
    adj_p.add_argument("--explain", action="store_true",
                       help="print how this photo's own changes were made")
    adj_p.add_argument("--reset", action="store_true", help="drop this photo's earlier adjustments")
    adj_p.add_argument("--source", help="source photo (default: found through run.json)")
    adj_p.add_argument("--reference", help="reference photo, if the run used one")
    adj_p.add_argument("--recipe-model", dest="recipe_model", help="recipe model, if the run used one")
    adj_p.add_argument("--allow-cloud", dest="allow_cloud", action="store_true",
                       help="allow the LLM decider again, if the run used it")
    adj_p.add_argument("--configs-dir", dest="configs_dir", help="directory with rules/looks/concepts/defaults")
    adj_p.add_argument("--profile", help="profile folder with your own config differences (default: $AUTOEDIT_PROFILE, else ~/.config/autoedit if it exists)")
    adj_p.set_defaults(func=_cmd_adjust)

    cur_p = sub.add_parser("curate", help="copy a photo subset for culling, style training, or review")
    cur_p.add_argument("input_dir")
    cur_p.add_argument("out_dir")
    cur_p.add_argument("--count", type=int, default=200,
                       help="number of photos to copy into OUT_DIR/selected (default: 200)")
    cur_p.add_argument("--no-recursive", dest="no_recursive", action="store_true",
                       help="scan only the top-level input folder")
    cur_p.add_argument("--scan-limit", dest="scan_limit", type=int,
                       help="only analyze the first N supported files, useful for quick trials")
    cur_p.add_argument("--analysis-long-edge", dest="analysis_long_edge", type=int, default=256,
                       help="proxy size used for curation features (default: 256)")
    cur_p.add_argument("--mode", choices=["diverse", "best", "aesthetic"], default="diverse",
                       help="diverse = cover looks; best = heuristic keepers; aesthetic = learned scorer")
    cur_p.add_argument("--emit-xmp", dest="emit_xmp", action="store_true",
                       help="write XMP sidecars next to copied selections with rating/label metadata")
    cur_p.add_argument("--aesthetic-model", dest="aesthetic_model",
                       help="JSON model from train-aesthetic; required for --mode aesthetic")
    cur_p.set_defaults(func=_cmd_curate)

    ds_p = sub.add_parser("dataset", help="build/validate a training dataset from originals + edited targets")
    ds_sub = ds_p.add_subparsers(dest="ds_cmd", required=True)
    dsb = ds_sub.add_parser("build", help="pair, preservation-validate, and copy into a training workspace")
    dsb.add_argument("originals"); dsb.add_argument("targets"); dsb.add_argument("out")
    dsb.add_argument("--recursive", action="store_true")
    dsb.add_argument("--no-validate", dest="validate", action="store_false",
                     help="skip preservation checks (keep every pair)")
    dsb.add_argument("--approve-all", dest="approve_all", action="store_true",
                     help="keep pairs even if a preservation gate fails")
    dsb.set_defaults(func=_cmd_dataset_build)
    dsv = ds_sub.add_parser("validate", help="report pairing + preservation verdicts, copy nothing")
    dsv.add_argument("originals"); dsv.add_argument("targets")
    dsv.add_argument("--recursive", action="store_true")
    dsv.set_defaults(func=_cmd_dataset_validate)

    rv_p = sub.add_parser("rank-variants", help="render + score recipe variants per photo; write a ranking CSV")
    rv_p.add_argument("input", help="an image or a folder of images")
    rv_p.add_argument("--out", help="ranking CSV path (default: <input>/variant-ranking.csv)")
    rv_p.add_argument("--aesthetic-model", dest="aesthetic_model", help="critic JSON to score variants")
    rv_p.set_defaults(func=_cmd_rank_variants)

    rec_p = sub.add_parser("train-recipe", help="train the learned recipe predictor from an M3 dataset")
    rec_p.add_argument("dataset", help="dataset workspace with originals/ + targets/ (from `dataset build`)")
    rec_p.add_argument("out_model")
    rec_p.add_argument("--ridge", type=float, default=1.0)
    rec_p.set_defaults(func=_cmd_train_recipe)

    aes_p = sub.add_parser("train-aesthetic", help="train a lightweight aesthetic scorer from labels CSV")
    aes_p.add_argument("labels_csv")
    aes_p.add_argument("images_dir")
    aes_p.add_argument("out_model")
    aes_p.add_argument("--path-column", dest="path_column",
                       help="CSV column containing image path/name; auto-detected when omitted")
    aes_p.add_argument("--score-column", dest="score_column",
                       help="CSV column containing score; auto-detected or AVA 1..10 votes when omitted")
    aes_p.add_argument("--limit", type=int, help="only train on the first N rows")
    aes_p.add_argument("--analysis-long-edge", dest="analysis_long_edge", type=int, default=256,
                       help="proxy size used for training features (default: 256)")
    aes_p.add_argument("--ridge", type=float, default=1e-3,
                       help="ridge regularization strength (default: 1e-3)")
    aes_p.set_defaults(func=_cmd_train_aesthetic)

    doc_p = sub.add_parser("doctor", help="report device + backend availability")
    doc_p.add_argument("--configs-dir", dest="configs_dir")
    doc_p.add_argument("--profile", help="profile folder with your own config differences (default: $AUTOEDIT_PROFILE, else ~/.config/autoedit if it exists)")
    doc_p.set_defaults(func=_cmd_doctor)

    pre_p = sub.add_parser("presets", help="list concept presets")
    pre_p.add_argument("--configs-dir", dest="configs_dir")
    pre_p.add_argument("--profile", help="profile folder with your own config differences (default: $AUTOEDIT_PROFILE, else ~/.config/autoedit if it exists)")
    pre_p.set_defaults(func=_cmd_presets)

    sty_p = sub.add_parser("styles", help="list style profiles")
    sty_p.add_argument("--configs-dir", dest="configs_dir")
    sty_p.add_argument("--profile", help="profile folder with your own config differences (default: $AUTOEDIT_PROFILE, else ~/.config/autoedit if it exists)")
    sty_p.set_defaults(func=_cmd_styles)

    exp_p = sub.add_parser("exports", help="list export presets")
    exp_p.add_argument("--configs-dir", dest="configs_dir")
    exp_p.add_argument("--profile", help="profile folder with your own config differences (default: $AUTOEDIT_PROFILE, else ~/.config/autoedit if it exists)")
    exp_p.set_defaults(func=_cmd_exports)

    set_p = sub.add_parser("settings", help="describe every run setting (type, default, range)")
    set_p.add_argument("--configs-dir", dest="configs_dir")
    set_p.add_argument("--profile", help="profile folder with your own config differences (default: $AUTOEDIT_PROFILE, else ~/.config/autoedit if it exists)")
    set_p.add_argument("--json", action="store_true", help="print the schema as JSON")
    set_p.set_defaults(func=_cmd_settings)

    look_p = sub.add_parser("looks", help="list, save or delete looks in your profile")
    look_p.add_argument("--configs-dir", dest="configs_dir")
    look_p.add_argument("--profile", help="profile folder with your own config differences (default: $AUTOEDIT_PROFILE, else ~/.config/autoedit if it exists)")
    look_sub = look_p.add_subparsers(dest="looks_cmd", required=True)
    ll_p = look_sub.add_parser("list", help="every look, where it comes from, and what it changes")
    ls_p = look_sub.add_parser("save", help="save a look into the profile")
    ls_p.add_argument("name", help="lowercase letters, digits and underscores")
    ls_p.add_argument("--from", dest="base", default="natural", help="look to start from (default: natural)")
    ls_p.add_argument("--set", action="append", default=[], metavar="FIELD=VALUE", help="field to change (repeatable)")
    ld_p = look_sub.add_parser("delete", help="remove a look from the profile")
    ld_p.add_argument("name")
    for action_p in (ll_p, ls_p, ld_p):
        # Also after the action (`looks save NAME --profile DIR`), which is where people write it.
        # SUPPRESS: when it is not given here, the value given before the action stays.
        action_p.add_argument("--profile", default=argparse.SUPPRESS, help="profile folder (as for `looks`)")
    look_p.set_defaults(func=_cmd_looks)

    cal_p = sub.add_parser("calibrate", help="fit a personal look from blind pairs you vote on")
    cal_p.add_argument("photos", nargs="?", help="folder of your photos (with --continue: only when it moved)")
    cal_p.add_argument("--out", help="calibration folder to create")
    cal_p.add_argument("--continue", dest="cont", metavar="DIR", help="read the votes in DIR and go on")
    cal_p.add_argument("--finish", action="store_true", help="with --continue: stop here and save the look")
    cal_p.add_argument("--count", type=int, help="photos per round (calibrate.toml: 8)")
    cal_p.add_argument("--rounds", type=int, help="rounds (calibrate.toml: 3)")
    cal_p.add_argument("--seed", type=int)
    cal_p.add_argument("--name", default="calibrated", help="name of the fitted look (default: calibrated)")
    cal_p.add_argument("--backend", choices=["auto", "stub", "sam3"])
    cal_p.add_argument("--style")
    cal_p.add_argument("--look", help="look to start from (default: the style's look)")
    cal_p.add_argument("--configs-dir", dest="configs_dir")
    cal_p.add_argument("--profile", help="profile folder the look is saved into (default: $AUTOEDIT_PROFILE, else ~/.config/autoedit)")
    cal_p.set_defaults(func=_cmd_calibrate)

    mod_p = sub.add_parser("models", help="SAM 3 weights: status, download, remove")
    mod_sub = mod_p.add_subparsers(dest="models_cmd", required=True)
    mod_sub.add_parser("status", help="what is installed (reads the local cache only)")
    mod_sub.add_parser("download", help="download the weights (resumes; needs the licence accepted)")
    mr_p = mod_sub.add_parser("remove", help="delete the cached weights")
    mr_p.add_argument("--yes", action="store_true", help="delete without the dry-run stop")
    mod_p.set_defaults(func=_cmd_models)

    ui_p = sub.add_parser("ui", help="launch the local web app")
    ui_p.add_argument("--host", default="127.0.0.1")
    ui_p.add_argument("--port", type=int, default=8765)
    ui_p.add_argument("--no-open", dest="no_open", action="store_true", help="don't open the browser")
    ui_p.add_argument("--unsafe-network", dest="unsafe_network", action="store_true",
                      help="allow binding a non-loopback host (exposes the local-file API off-machine)")
    ui_p.add_argument("--configs-dir", dest="configs_dir")
    ui_p.add_argument("--profile", help="profile folder with your own config differences (default: $AUTOEDIT_PROFILE, else ~/.config/autoedit if it exists)")
    ui_p.set_defaults(func=_cmd_ui)

    args = parser.parse_args(argv)
    if hasattr(args, "profile"):
        try:
            # `looks save` and `calibrate` may create the profile folder, so a --profile that does not exist yet is fine there.
            if args.profile and (args.cmd == "calibrate" or (args.cmd == "looks" and args.looks_cmd == "save")):
                pass
            else:
                args.profile = paths.profile_dir(args.profile)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        if args.profile:
            print(f"profile: {args.profile}")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
