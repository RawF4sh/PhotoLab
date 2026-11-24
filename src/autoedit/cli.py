"""AutoEdit CLI (argparse — no third-party CLI dep so the core runs anywhere).

Commands:
  autoedit run INPUT_DIR [OUT_DIR] [flags]   batch-edit a folder
  autoedit adjust PHOTO [--set T.OP=V] [--edit TEXT] [--mark X0,Y0,X1,Y1 [--mark-kind K]] [--explain] [--reset]
                                             re-render one photo
  autoedit sync PHOTO [FOLDER] [--dry-run]   copy one photo's own changes to the similar photos of its run
  autoedit curate INPUT_DIR OUT_DIR          copy a diverse subset for review/training
  autoedit pick FOLDER [-o DIR] [--copy]     rank the frames of each burst and pick the keepers
  autoedit find FOLDER WORD [WORD ...]     list photos by what is in them
  autoedit keywords FOLDER [--dry-run]     write XMP keywords beside the photos
  autoedit declare FOLDER [-o DIR]         write the edit declaration of a run
  autoedit gallery FOLDER [-o DIR]         write a static page of a run's exports
  autoedit merge FOLDER [-o DIR] [--mode M]  merge brackets, stacks, focus series or star trails into TIFFs
  autoedit doctor                            report device + backend availability
  autoedit models status|download|remove     SAM 3 weights
  autoedit presets                           list concept presets
  autoedit genres list                       list genre packs

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
                "tone_target_p50": "raw_tone_target_p50", "fallback_ev": "raw_fallback_ev", "recover": "raw_recover", "lens_ca": "lens_ca", "lens_vignette": "lens_vignette", "fast_analysis": "fast_analysis"}
    for src, dst in _dec_map.items():
        if src in dec:
            kw[dst] = dec[src]
    for k in ("style", "prompt", "edit", "preset", "look", "intensity", "palette", "palette_strength",
              "decider", "emit", "consistency", "levels", "denoise", "night_sky", "highlight_hold", "highlight_detail", "light_shaping", "snap_edges", "reveal_check", "hide", "hide_method",
              "translator", "grain", "grain_size", "lut", "lut_amount", "history", "genre", "straighten", "neutral", "wb", "backdrop",
              "consistency_wb", "colour_report"):
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
                     ("subdir", "out_subdir"), ("export", "export"), ("aspect", "out_aspect")):
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
    # A genre (F30) sits directly above the packaged defaults: the export preset, the files and the
    # flags win over it. `--genre ""` switches off a genre that a file names.
    genre = getattr(args, "genre", None)
    if genre is None:
        genre = explicit.get("genre") or kw.get("genre")
    if genre:
        from . import genres

        kw.update(genres.run_kwargs(
            genre, kw, style_named="style" in explicit or getattr(args, "style", None) is not None))
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
    kw["genre"] = genre or None

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
        "raw_recover": getattr(args, "raw_recover", None),
        "lens_ca": getattr(args, "lens_ca", None),
        "lens_vignette": getattr(args, "lens_vignette", None),
        "fast_analysis": getattr(args, "fast_analysis", None),
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
        "straighten": getattr(args, "straighten", None),
        "neutral": getattr(args, "neutral", None),
        "backdrop": getattr(args, "backdrop", None),
        "consistency_wb": getattr(args, "consistency_wb", None),
        "colour_report": getattr(args, "colour_report", None),
        "out_aspect": getattr(args, "aspect", None),
        "denoise": getattr(args, "denoise", None),
        "night_sky": getattr(args, "night_sky", None),
        "grain": getattr(args, "grain", None),
        "grain_size": getattr(args, "grain_size", None),
        "lut": getattr(args, "lut", None),
        "lut_amount": getattr(args, "lut_amount", None),
        "history": getattr(args, "history", None),
        "highlight_hold": getattr(args, "highlight_hold", None),
        "highlight_detail": getattr(args, "highlight_detail", None),
        "light_shaping": getattr(args, "light_shaping", None),
        "snap_edges": getattr(args, "snap_edges", None),
        "reveal_check": getattr(args, "reveal_check", None),
        "hide": getattr(args, "hide", None),
        "hide_method": getattr(args, "hide_method", None),
        "translator": getattr(args, "translator", None),
    }
    for k, v in overrides.items():
        if v is not None:
            kw[k] = v
    concepts = getattr(args, "concepts", None)
    if concepts:
        kw["concepts"] = [c.strip() for c in concepts.split(",") if c.strip()]
    kw["profile_dir"] = profile
    return RunConfig(**kw)


def _spot(text: str) -> list[float]:
    from . import neutral

    try:
        return neutral.parse(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def _add_run_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("input_dir")
    p.add_argument("out_dir", nargs="?", default="out")
    p.add_argument("--style", help="style profile from styles.toml (look + concepts + brief)")
    p.add_argument("--genre", help="genre pack from genres.toml (`autoedit genres list`): the things to find, "
                   "a look and run settings for one kind of photo; other flags win over it")
    p.add_argument("--prompt", help='free-text intent, layered on the style brief (e.g. "moody, warm, lift shadows")')
    p.add_argument("--edit", help='plain-language region instructions, e.g. "brighten the jacket, darken the sky slightly"')
    p.add_argument("--translator", choices=["auto", "local", "off"],
                   help="a small model on this computer (Ollama) reads --edit text the word list misses: "
                        "auto (when it is running) | local (also when --decider llm is on) | off")
    p.add_argument("--preset", help="concept-set override from concepts.toml")
    p.add_argument("--concepts", help='comma-separated, overrides --preset (e.g. "sky,skin,dress")')
    p.add_argument("--look", help="magnitude profile override (defaults to the style's look); "
                                  '"surprise" with --seed N takes a look the seed picks and names it')
    p.add_argument("--seed", type=int, help="with --look surprise: a whole number that picks the look (the same number, the same look)")
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
    p.add_argument("--raw-recover", dest="raw_recover", action="store_true", default=None,
                   help="rebuild snow and sky that the RAW decode clips to flat white, from the channels "
                        "that did not clip (opt-in, RAW only)")
    p.add_argument("--lens-ca", dest="lens_ca", action="store_true", default=None,
                   help="line up red and blue with green to remove colour fringes toward the corners (opt-in)")
    p.add_argument("--lens-vignette", dest="lens_vignette", action="store_true", default=None,
                   help="brighten RAW corners as much as the camera does in its own JPEGs, from a profile "
                        "(autoedit lens-profile FOLDER; opt-in)")
    p.add_argument("--fast-analysis", dest="fast_analysis", action="store_true", default=None,
                   help="decide the edit on a half-size development of a RAW file (faster previews; opt-in)")
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
    p.add_argument("--straighten", dest="straighten", action="store_true", default=None,
                   help="turn the export so a clear waterline or horizon is level, at most 5 degrees; the edges are cut (opt-in)")
    p.add_argument("--neutral", type=_spot, metavar="X,Y",
                   help="a spot that is grey or white, as shares of the frame from the top left (0.42,0.61): the white "
                        "balance makes it neutral. A spot it cannot use is reported and left")
    p.add_argument("--neutral-from", dest="neutral_from", metavar="FILE",
                   help="measure the --neutral spot once, on this photo, and give every photo of the run that white balance")
    p.add_argument("--backdrop", dest="backdrop", action="store_true", default=None,
                   help="even out a light background and take it to a set white, 245 of 255 (opt-in; nothing is cut out)")
    p.add_argument("--consistency-wb", dest="consistency_wb", action="store_true", default=None,
                   help="bring the photos of a group to one white balance, the median cast of its frames (opt-in)")
    p.add_argument("--colour-report", dest="colour_report", action="store_true", default=None,
                   help="measure the subject colour of every photo and report how far each group spreads (opt-in)")
    p.add_argument("--aspect", help='crop the export to a shape W:H, e.g. 4:5, placed on the subject; "" keeps the photo\'s shape')
    p.add_argument("--denoise", dest="denoise", action="store_true", default=None,
                   help="reduce colour and luma noise on high-ISO frames, chroma first (opt-in)")
    p.add_argument("--night-sky", dest="night_sky", action="store_true", default=None,
                   help="night sky tools: night frames keep their darkness, a town's glow comes off the sky, stuck pixels are repaired, "
                        "and noise reduction and sharpening spare the stars (opt-in; the astro pack sets it)")
    p.add_argument("--grain", type=float,
                   help="film grain on the finished photo, 0 to 100 (25 is light); added to the export after the resize. "
                        "Not scaled by --intensity; 0 switches a look's grain off")
    p.add_argument("--grain-size", dest="grain_size", type=float,
                   help="how coarse the grain is, 0.5 to 4 (1 is fine; it follows the size of the export)")
    p.add_argument("--lut", help="a .cube 3D LUT file laid over the finished photo")
    p.add_argument("--lut-amount", dest="lut_amount", type=float,
                   help="how much of the LUT is blended in, 0 to 100 (default 100). Not scaled by --intensity")
    p.add_argument("--history", dest="history", action="store_true", default=None,
                   help="keep one line per exported photo in your profile, for `autoedit style suggest` (opt-in)")
    p.add_argument("--no-history", dest="history", action="store_false", default=None,
                   help="do not keep a history, whatever the config files say")
    p.add_argument("--highlight-hold", dest="highlight_hold", action="store_true", default=None,
                   help="keep highlights that are blown in the source white instead of letting the edit grey them (opt-in)")
    p.add_argument("--highlight-detail", dest="highlight_detail", action="store_true", default=None,
                   help="keep faint cloud and snow texture where the edit holds back the brightest tones (opt-in)")
    p.add_argument("--light-shaping", dest="light_shaping", action="store_true", default=None,
                   help="add a soft warm glow where the sun is, when the photo has a clear light source (opt-in)")
    p.add_argument("--snap-edges", dest="snap_edges", action="store_true", default=None,
                   help="extend region masks that stop just short of the frame edge to it (opt-in)")
    p.add_argument("--reveal-check", dest="reveal_check", action="store_true", default=None,
                   help="report which photos show a plate, screen, document, person or file metadata "
                        "(opt-in; reveal.html and run.json, no picture changes)")
    p.add_argument("--hide", dest="hide", metavar="KIND[,KIND]",
                   help="hide every finding of these reveal kinds in the export, e.g. plate,face (turns the check on). "
                        "Nothing is hidden unless you ask; every hidden region is listed in the sidecar")
    p.add_argument("--hide-method", dest="hide_method", choices=("cover", "mosaic", "blur"),
                   help="how a hidden region looks: cover (default, one flat colour, nothing left), "
                        "mosaic or blur (softer, keep a trace of what was there)")
    p.add_argument("--workers", type=int,
                   help="render processes for a batch (0 = half the cores, at most 4)")
    p.add_argument("--threads", type=int,
                   help="threads for per-pixel work inside one render (0 = one per core)")
    p.add_argument("--no-mask-cache", dest="mask_cache", action="store_false", default=None,
                   help="do not keep raw masks under <out>/.autoedit-masks")
    p.add_argument("--no-consistent", dest="consistent", action="store_false", default=None,
                   help="disable shoot-consistency (the bounded per-group brightness normalization)")
    p.add_argument("--consistency", choices=["brightness", "bursts", "timelapse"],
                   help="shoot consistency: brightness (default) | bursts (even out frames of one framing) | timelapse (even out flicker, keep the slow change)")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="segment + decide + sidecar, no baked pixels")
    p.add_argument("--config", help="run.toml overriding defaults")
    p.add_argument("--configs-dir", dest="configs_dir", help="directory with rules/looks/concepts/defaults")
    p.add_argument("--profile", help="profile folder with your own config differences (default: $AUTOEDIT_PROFILE, else ~/.config/autoedit if it exists)")


def _surprise(cfg: RunConfig, seed: int | None) -> RunConfig:
    """`--look surprise --seed N` (F26): the run gets the first of the looks the seed picks, by
    name. The config that comes back never holds the word, so no sidecar does."""
    from . import looks as looks_mod, variants

    if cfg.look != variants.SURPRISE or variants.SURPRISE in looks_mod.load(cfg.looks_path):
        return cfg   # not asked for, or a look of your own has that name
    if seed is None:
        raise ValueError("--look surprise needs --seed N (any whole number from 0 up): "
                         "the seed picks the look, so the run can be repeated")
    picks = variants.surprise_for(cfg, variants.surprise_seed(seed))
    if not picks:
        raise ValueError("--look surprise: the look library holds no other look")
    print(f"surprise, seed {seed}: {', '.join(picks)}\nthis run uses {picks[0]}, the same as --look {picks[0]}")
    return cfg.model_copy(update={"look": picks[0]})


def _cmd_run(args) -> int:
    from . import batch

    try:
        cfg = _surprise(load_run_config(args), getattr(args, "seed", None))
        if getattr(args, "neutral_from", None):
            from . import neutral

            cfg, line = neutral.for_run(cfg, args.neutral_from)
            print(line)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    if not os.path.isdir(args.input_dir):
        print(f"input_dir not found: {args.input_dir}", file=sys.stderr)
        return 2
    try:
        m = batch.run(cfg, args.input_dir, args.out_dir, do_apply=not args.dry_run, resume=args.resume,
                      consistent=args.consistent is not False, workers=cfg.workers)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    return 1 if getattr(args, "strict", False) and (m or {}).get("failed") else 0


def _cmd_check(args) -> int:
    """A dry run with the reveal check on: sidecars, mask cache and reveal.html, no image."""
    from . import batch

    if not os.path.isdir(args.folder):
        print(f"folder not found: {args.folder}", file=sys.stderr)
        return 2
    args.reveal_check = True
    out = args.out or os.path.join(args.folder, "autoedit-check")
    try:
        cfg = load_run_config(args)
        m = batch.run(cfg, args.folder, out, do_apply=False, consistent=False, workers=cfg.workers)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(f"Page: {os.path.join(out, 'reveal.html')}")
    if not args.strict:
        return 0
    if m["reveal"]["picture"] == "not_run":     # no photo was looked at: a clean exit would say nothing was found
        print("--strict: the picture part did not run for any photo (it needs SAM 3; the stub cannot find "
              "named things), so nothing was looked for in the pictures.", file=sys.stderr)
        return 2
    return 2 if m["reveal"]["with_findings"] else 0


def _cmd_sync(args) -> int:
    from . import sync

    try:
        r = sync.run(args.target, args.folder, dry_run=args.dry_run, minutes=args.minutes,
                     configs_dir=args.configs_dir, profile=args.profile,
                     reference=args.reference, recipe_model=args.recipe_model, lut=args.lut)
    except (FileNotFoundError, ValueError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 2
    print("\n".join(sync.lines(r)))
    return 1 if any(t["status"] == "failed" for t in r["targets"]) else 0


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
                            allow_cloud=args.allow_cloud, profile=args.profile,
                            lut=args.lut, neutral=args.neutral)
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


def _cmd_pick(args) -> int:
    from . import pick

    if not os.path.isdir(args.folder):
        print(f"folder not found: {args.folder}", file=sys.stderr)
        return 2
    try:
        r = pick.run(args.folder, args.out, load_run_config(args), model_path=args.aesthetic_model,
                     copy=args.copy, configs_dir=args.configs_dir, profile=args.profile, log=print)
    except (ValueError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 2
    print(f"\n{len(r['bursts'])} bursts, {len(r['singles'])} single frames, {len(r['picks'])} picked files"
          + (f", {len(r['failed'])} could not be judged" if r["failed"] else ""))
    print("No photo was moved or changed.")
    for f in pick.FILES:
        print(os.path.abspath(os.path.join(args.out, f)))
    if args.copy:
        print(os.path.abspath(os.path.join(args.out, "picked")))
    return 0


def _min_coverage(args) -> float:
    if args.min_coverage is not None:
        return args.min_coverage
    rules = paths.read_config(paths.config_paths(args.configs_dir, args.profile)["rules_path"])
    return float((rules.get("search") or {}).get("min_coverage", 0.02))


def _cmd_find(args) -> int:
    from . import search

    if not os.path.isdir(args.folder):
        print(f"folder not found: {args.folder}", file=sys.stderr)
        return 2
    text, code = search.cmd_find(args.folder, args.words, _min_coverage(args), args.json)
    print(text)
    return code


def _cmd_keywords(args) -> int:
    from . import search

    if not os.path.isdir(args.folder):
        print(f"folder not found: {args.folder}", file=sys.stderr)
        return 2
    print(search.cmd_keywords(args.folder, _min_coverage(args), args.dry_run, args.configs_dir, args.profile))
    return 0


def _cmd_gallery(args) -> int:
    from . import gallery, search

    if not os.path.isdir(args.folder):
        print(f"folder not found: {args.folder}", file=sys.stderr)
        return 2
    try:
        r = gallery.write(args.folder, args.out, _min_coverage(args),
                          search.skip_words(args.configs_dir, args.profile))
    except (OSError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 2
    print(r["page"])
    print(f"{r['photos']} photo{'s' * (r['photos'] != 1)}" +(f", {r['warned']} with a reveal warning on the tile" if r["warned"] else "")
          + (f"; {r['skipped']} sidecars without an export skipped" if r["skipped"] else ""))
    return 0


def _cmd_declare(args) -> int:
    from . import declare

    if not os.path.isdir(args.folder):
        print(f"folder not found: {args.folder}", file=sys.stderr)
        return 2
    print(declare.cmd_declare(args.folder, args.out))
    return 0


def _cmd_merge(args) -> int:
    from . import merge

    if not os.path.isdir(args.folder):
        print(f"folder not found: {args.folder}", file=sys.stderr)
        return 2
    try:
        r = merge.run(args.folder, args.out, mode=args.mode, everything=args.all, align=not args.no_align,
                      dry_run=args.dry_run, configs_dir=args.configs_dir, profile=args.profile)
    except (ValueError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 2
    if args.dry_run:
        print(f"\n{len(r['groups'])} groups found; nothing was written")
        return 0
    print(f"\n{len(r['merged'])} of {len(r['groups'])} groups merged into {os.path.abspath(args.out)}"
          + (f"; {len(r['flagged'])} flagged" if r["flagged"] else "")
          + (f"; {len(r['failed'])} not merged" if r["failed"] else ""))
    print("No photo was moved or changed.")
    return 1 if r["failed"] else 0


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
        known = {f"{n}.toml" for n in paths._CONFIG_FILES} | {"defaults.toml", "style.toml", "history.jsonl", "calibrate.toml", "pick.toml", "merge.toml", "lens_profiles.toml"}
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


def _kb(n: int) -> str:
    return f"{n / 1000:.0f} kB"


def _days(n: int) -> str:
    return f"{n} day" if n == 1 else f"{n} days"


def _cmd_history(args) -> int:
    from . import history

    try:
        st = history.status(args.profile)
        if args.history_cmd == "status":
            if history.wanted(args.configs_dir, args.profile):
                print("history: on")
            else:
                print("history: off (turn it on with `history = true` under [run] in your profile's "
                      "defaults.toml, or --history on a run)")
            if not os.path.isfile(st["path"]):
                print(f"file: none yet ({st['path']})")
                return 0
            print(f"file: {st['path']}")
            if st["unreadable"]:
                print(f"unreadable lines: {st['unreadable']} (skipped)")
            span = f" ({st['first']} to {st['last']})" if st["first"] else ""
            print(f"photos: {st['photos']} on {_days(st['days'])}{span}, {_kb(st['bytes'])}")
            return 0
        if not os.path.isfile(st["path"]):
            print("no history to clear")
            return 0
        if not args.yes:
            print(f"would delete the history of {st['photos']} photos ({_kb(st['bytes'])}) at {st['path']}; "
                  "run again with --yes to delete")
            return 1
        history.clear(args.profile)
        print(f"deleted the history of {st['photos']} photos")
        return 0
    except (ValueError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 2


def _cmd_style(args) -> int:
    from . import style

    try:
        ov = style.overview(args.profile, args.configs_dir, args.look)
        by_id = {s["id"]: s for s in ov["suggestions"]}
        unknown = [i for i in args.only if i not in by_id]
        if unknown:
            print(f"no such suggestion: {', '.join(unknown)} (known: {', '.join(by_id) or 'none'})", file=sys.stderr)
            return 2
        if args.yes:
            r = style.add_to_look(args.profile, args.yes, args.only or None, args.look, args.configs_dir)
            print(f"saved look {r['name']} in {r['path']}: the look {r['base']} plus {', '.join(r['added'])}")
            if r["replaced"]:
                print(f"  it replaced the look '{r['name']}' that was in the profile")
            print(f"Nothing uses it yet. Try it with: autoedit run <in> <out> --look {r['name']}")
            return 0
        if not ov["photos"]:
            print("No history yet." + ("" if ov["on"] else " It is off: turn it on with `history = true` under "
                  "[run] in your profile's defaults.toml, or --history on a run."))
            return 0
        pl = lambda n, w: f"{n} {w}" if n == 1 else f"{n} {w}s"  # noqa: E731
        line = f"History: {pl(ov['photos'], 'photo')} on {_days(ov['days'])}."
        if ov["look"]:
            line += f" Look {ov['look']}: {pl(ov['looks'][ov['look']], 'photo')}."
        others = [f"{n} ({c})" for n, c in ov["looks"].items() if n != ov["look"]]
        if others:
            line += f" Other looks: {', '.join(others)}; see them with --look NAME."
        print(line)
        lo = ov["left_out"]
        if lo["mark"] or lo["word"]:
            print(f"Not counted: {pl(lo['mark'], 'change')} made with a mark, {lo['word']} on things your "
                  "config files do not name.")
        shown = [by_id[i] for i in args.only] if args.only else ov["suggestions"]
        if not shown:
            r = ov["suggest"]
            print(f"No suggestion yet. A change shows up here when you made it in the same direction on at least "
                  f"{r['min_photos']} photos, on at least {r['min_days']} days, and on at least "
                  f"{r['min_share'] * 100:.0f} % of the photos it could have been made on.")
            return 0
        for s in shown:
            print(f"\n{s['id']}\n  {s['ask']}\n  {s['fact']}")
        print("\nTo add them to a new look: autoedit style suggest --yes NAME   (--only ID for some)")
        print("Nothing has changed.")
        return 0
    except (ValueError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 2


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
    from .styles import load_styles, offered_styles

    styles = load_styles(paths.config_paths(args.configs_dir, args.profile)["styles_path"])
    for name, s in offered_styles(styles).items():
        print(f"{name}  (look={s.look})\n    {s.brief}")
    return 0


def _cmd_exports(args) -> int:
    from . import exports

    path = paths.config_paths(args.configs_dir, args.profile).get("exports_path") or paths.config_path(
        "exports.toml", packaged_fallback=True)
    for name, preset in exports.load(path).items():
        print(f"{name}: " + ", ".join(f"{k}={v}" for k, v in preset.items()))
    return 0


def _cmd_genres(args) -> int:
    import json

    from . import genres

    try:
        rows = genres.listing(args.configs_dir, args.profile)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    for g in rows:
        bits = [f"{k}={g[k]}" for k in ("preset", "look") if g[k]]
        bits += [f"{k}={','.join(g[k])}" for k in ("rule_sets", "reveal") if g[k]]
        bits += [f"{k}={json.dumps(v)}" for k, v in g["run"].items()]
        print(f"{g['name']}  ({g['origin']}, {'tested' if g['tested'] else 'untested'})  " + "  ".join(bits))
        print("    " + g["description"])
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


def write_look(profile: str, name: str, fields: dict, habit: dict | None = None) -> tuple[str, bool]:
    """Write look `name` with ALL its fields into <profile>/looks.toml, keeping the other looks in
    the file. All fields, because a profile table is merged over a packaged look of the same name:
    a table with only the changed fields would let the packaged values come back.
    `habit` (F27, scene -> target -> op -> value) is written as the look's habit table.
    Returns (path, whether the file existed)."""
    from dataclasses import asdict

    from . import looks as looks_mod

    defaults = asdict(looks_mod.Look())
    os.makedirs(profile, exist_ok=True)
    path = os.path.join(profile, "looks.toml")
    existed = os.path.isfile(path)
    data = paths.read_config(path) if existed else {}
    data[name] = {k: fields.get(k, defaults[k]) for k in looks_mod.FIELDS}
    if habit:
        data[name][looks_mod.HABIT] = habit
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(paths.dump_toml(data))
    return path, existed


def _cmd_looks(args) -> int:
    from dataclasses import asdict

    from . import lookcard, lookfile
    from . import looks as looks_mod

    base_path = os.path.join(args.configs_dir or paths.configs_dir(), "looks.toml")
    try:
        packaged = _read_toml(base_path)
        merged = looks_mod.load(paths.config_paths(args.configs_dir, args.profile)["looks_path"])
        own = _profile_looks(args.profile)
        habits = looks_mod.load_habits(paths.config_paths(args.configs_dir, args.profile)["looks_path"])
        if args.looks_cmd == "list":
            defaults = asdict(looks_mod.Look())
            for name, look in merged.items():
                origin = ("packaged, changed by the profile" if name in packaged else "profile") if name in own \
                    else "packaged"
                diff = ", ".join(f"{k}={v}" for k, v in asdict(look).items() if v != defaults[k])
                print(f"{name}  ({origin})  {diff}")
                if name in habits:
                    print("  habit: " + "; ".join(f"{scene} {target} {op} {v:+g}" for scene, ts in habits[name].items()
                                                  for target, ops in ts.items() for op, v in ops.items()))
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
        if args.looks_cmd == "export":
            text, notes = lookfile.export(args.name, args.configs_dir, args.profile, args.palette,
                                          args.author, args.licence)
            out = args.out or args.name + lookfile.EXT
            lookfile.write(out, text)
            print(f"wrote {out}: look {args.name}" + (f" with palette {args.palette}" if args.palette else ""))
            for note in notes:
                print(note)
            return 0
        if args.looks_cmd == "import":
            lf = lookfile.read(args.file)
            r = lookfile.install(lf, args.profile or paths.default_profile_dir(), args.new_name, args.force,
                                 args.configs_dir)
            print(f"imported look {r['name']} into {r['look_path']}")
            if lf.author:
                print(f"  author: {lf.author}")
            if lf.licence:
                print(f"  licence: {lf.licence}")
            if lf.habit:
                print("  it repeats changes of its own on some scenes (habits); `autoedit looks list` shows them")
            if r["palette_path"]:
                print(f"  with the palette {r['name']} in {r['palette_path']}")
            for kind in r["replaced"]:
                print(f"  it replaced the {kind} {r['name']!r} that was in the profile")
            if r["rewritten"]:
                print("note: the file was rewritten, so comments in it are gone")
            print(f"use it with: autoedit run <in> <out> --look {r['name']}"
                  + (f" --palette {r['name']}" if r["palette_path"] else ""))
            return 0
        if args.looks_cmd == "card":
            cfg = load_run_config(argparse.Namespace(configs_dir=args.configs_dir, profile=args.profile,
                                                     backend=args.backend, look=args.name, palette=args.palette))
            r = lookcard.render(args.name, args.photos, args.out, cfg, log=print)
            if not r["photos"]:
                print("no photo was named: the card shows a drawn sample frame")
            for n, reason in r["flagged"]:
                print(f"photo {n} is flagged for review with this look: {reason}")
            print(f"wrote {r['png']}")
            print(f"wrote {r['html']}  ({max(r['photos'], 1)} before-and-after, segmenter: {r['backend']})")
            return 0
        if args.looks_cmd == "lut":
            from . import lut as lut_mod

            if args.name not in merged:
                print(f"look {args.name!r} not found (known: {', '.join(merged)})", file=sys.stderr)
                return 2
            table, comments = lut_mod.look_cube(args.name, merged[args.name])
            lut_mod.write_cube(args.out, table, f"AutoEdit {args.name}", comments)
            print(f"wrote {args.out}: the whole-picture colour part of look {args.name}, {table.shape[0]} points")
            print(comments[1].strip())
            print("not in the file: region edits, what the rules decide per photo, clarity, sharpening, grain")
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
        path, existed = write_look(args.profile or paths.default_profile_dir(), args.name, fields,
                                   habit=habits.get(args.base))
        print(f"saved look {args.name} in {path}")
        if existed:
            print("note: the file was rewritten, so comments in it are gone")
        return 0
    except (ValueError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 2


def _cmd_lens_profile(args) -> int:
    from . import lens
    from .imageio import is_raw

    files = []
    for d in args.folders:
        if not os.path.isdir(d):
            print(f"folder not found: {d}", file=sys.stderr)
            return 2
        files += sorted(os.path.join(d, n) for n in os.listdir(d) if os.path.isfile(os.path.join(d, n)) and is_raw(os.path.join(d, n)))
    if not files:
        print("no RAW files in the folders", file=sys.stderr)
        return 1
    try:
        target = os.path.join(args.profile or paths.default_profile_dir(), lens._VIG_FILE)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    # ponytail: one process; a pool if catalogues of thousands are common
    profiles, rep = lens.build_profiles(files)
    for p, why in rep["skipped"]:
        print(f"skipped {os.path.basename(p)}: {why}")
    for cam, lens_name, row, ev in rep["rows"]:
        print(f"{cam} / {lens_name}: {row[0]:.0f}-{row[1]:.0f} mm f/{row[2]:.1f}-{row[3]:.1f}, "
              f"{row[4]} photos, corners +{ev:.2f} EV")
    for cam, lens_name, e, n in rep["short"]:
        print(f"not enough photos ({n} of {lens._VIG_MIN_FRAMES}): {cam} / {lens_name}, "
              f"{e[0]:.0f}-{e[1]:.0f} mm f/{e[2]:.1f}-{e[3]:.1f}")
    if not rep["rows"]:
        print("no bucket reached the minimum; nothing written", file=sys.stderr)
        return 1
    if args.dry_run:
        print(f"dry run: would write {target}")
    else:
        try:
            lens.write_profiles(target, profiles)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        print(f"wrote {target}")
    return 0


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


def _cmd_app(args) -> int:
    from .web.desktop import run

    try:
        run(args.configs_dir or paths.configs_dir(), args.profile, debug=args.debug)
    except ImportError as e:
        print(e if "[app] extra" in str(e) else
              f"autoedit app needs the [app] extra:  pip install 'autoedit[app]'   ({e})", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autoedit", description="Open-vocabulary batch photo auto-editor")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="batch-edit a folder")
    _add_run_flags(run_p)
    run_p.add_argument("--strict", action="store_true", help="end with 1 when any photo failed (default: 0)")
    run_p.set_defaults(func=_cmd_run)

    chk_p = sub.add_parser("check", help="check a folder for what gives something away (reveal.html); changes no picture")
    chk_p.add_argument("folder")
    chk_p.add_argument("--out", help="where the sidecars and reveal.html go (default: FOLDER/autoedit-check)")
    chk_p.add_argument("--strict", action="store_true", help="end with 2 when any photo has a finding")
    chk_p.add_argument("--configs-dir", dest="configs_dir")
    chk_p.add_argument("--profile")
    chk_p.add_argument("--backend", choices=["auto", "sam3", "stub"])
    chk_p.add_argument("--device")
    chk_p.add_argument("--metadata", choices=["keep", "shareable", "none"])
    chk_p.set_defaults(func=_cmd_check)

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
    adj_p.add_argument("--lut", help="the LUT file, if the run used one")
    adj_p.add_argument("--neutral", type=lambda t: t if t == "none" else _spot(t), metavar="X,Y|none",
                       help="make the spot X,Y (shares of the frame) of this photo neutral; none drops the run's pick, also one taken with --neutral-from")
    adj_p.add_argument("--allow-cloud", dest="allow_cloud", action="store_true",
                       help="allow the LLM decider again, if the run used it")
    adj_p.add_argument("--configs-dir", dest="configs_dir", help="directory with rules/looks/concepts/defaults")
    adj_p.add_argument("--profile", help="profile folder with your own config differences (default: $AUTOEDIT_PROFILE, else ~/.config/autoedit if it exists)")
    adj_p.set_defaults(func=_cmd_adjust)

    sync_p = sub.add_parser("sync", help="copy one photo's own changes to the photos of the same scene taken "
                                         "near it, and match their exposure to it")
    sync_p.add_argument("target", help="the photo to copy from: an exported photo or its .autoedit.json")
    sync_p.add_argument("folder", nargs="?", help="where the other exports are (default: that photo's folder)")
    sync_p.add_argument("--minutes", type=float,
                        help="how far apart in time a similar photo may be taken (default: rules.toml [sync])")
    sync_p.add_argument("--dry-run", dest="dry_run", action="store_true", help="list the similar photos, change nothing")
    sync_p.add_argument("--reference", help="the --reference photo of the run, if it used one")
    sync_p.add_argument("--recipe-model", dest="recipe_model", help="recipe model, if the run used one")
    sync_p.add_argument("--lut", help="the LUT file, if the run used one")
    sync_p.add_argument("--configs-dir", dest="configs_dir")
    sync_p.add_argument("--profile", help="profile folder with your own config differences")
    sync_p.set_defaults(func=_cmd_sync)

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

    pick_p = sub.add_parser("pick", help="find the bursts of a folder and pick the keeper of each")
    pick_p.add_argument("folder")
    pick_p.add_argument("-o", "--out", default="out-picks", help="where the results go (default: out-picks)")
    pick_p.add_argument("--copy", action="store_true", help="also copy the picks into OUT/picked")
    pick_p.add_argument("--aesthetic-model", dest="aesthetic_model", help="JSON model from train-aesthetic; adds its score")
    pick_p.add_argument("--concepts", help='comma-separated, what the sharpness is judged on (default: "subject")')
    pick_p.add_argument("--backend", choices=["stub", "sam3", "auto"])
    pick_p.add_argument("--device")
    pick_p.add_argument("--configs-dir", dest="configs_dir")
    pick_p.add_argument("--profile", help="profile folder with your own config differences (default: $AUTOEDIT_PROFILE, else ~/.config/autoedit if it exists)")
    pick_p.set_defaults(func=_cmd_pick)

    merge_p = sub.add_parser("merge", help="merge the frames of a folder (brackets, stacks, focus series, star trails) into TIFFs")
    merge_p.add_argument("folder")
    merge_p.add_argument("-o", "--out", default="out-merge", help="where the results go (default: out-merge)")
    merge_p.add_argument("--mode", choices=["auto", "average", "brackets", "focus", "trails"], default="auto")
    merge_p.add_argument("--all", action="store_true", help="treat every photo of the folder as one group")
    merge_p.add_argument("--no-align", dest="no_align", action="store_true", help="frames are already aligned")
    merge_p.add_argument("--dry-run", dest="dry_run", action="store_true", help="list the groups, write nothing")
    merge_p.add_argument("--configs-dir", dest="configs_dir")
    merge_p.add_argument("--profile", help="profile folder with your own config differences")
    merge_p.set_defaults(func=_cmd_merge)

    find_p = sub.add_parser("find", help="list the photos of a run that show every given word (scene, concept or reveal kind)")
    find_p.add_argument("folder")
    find_p.add_argument("words", nargs="+", metavar="WORD")
    find_p.add_argument("--min-coverage", dest="min_coverage", type=float, help="smallest share of the frame (default: rules.toml [search])")
    find_p.add_argument("--json", action="store_true")
    kw_p = sub.add_parser("keywords", help="write each exported photo's scene and concepts as XMP keywords (.xmp beside it)")
    kw_p.add_argument("folder")
    kw_p.add_argument("--min-coverage", dest="min_coverage", type=float, help="smallest share of the frame (default: rules.toml [search])")
    kw_p.add_argument("--dry-run", dest="dry_run", action="store_true", help="print the keywords, write nothing")
    for q in (find_p, kw_p):
        q.add_argument("--configs-dir", dest="configs_dir")
        q.add_argument("--profile", help="profile folder with your own config differences")
    find_p.set_defaults(func=_cmd_find)
    kw_p.set_defaults(func=_cmd_keywords)
    decl_p = sub.add_parser("declare", help="write a page and a JSON file stating what AutoEdit did to each photo of a run")
    decl_p.add_argument("folder")
    decl_p.add_argument("-o", "--out", help="where the two files go (default: the folder)")
    decl_p.set_defaults(func=_cmd_declare)
    gal_p = sub.add_parser("gallery", help="write gallery.html: thumbnails of a run's exports that link to the full photos")
    gal_p.add_argument("folder")
    gal_p.add_argument("-o", "--out", help="where gallery.html and gallery/ go (default: the folder)")
    gal_p.add_argument("--configs-dir", dest="configs_dir")
    gal_p.add_argument("--profile", help="profile folder with your own config differences")
    gal_p.set_defaults(func=_cmd_gallery, min_coverage=None)

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

    lp_p = sub.add_parser("lens-profile", help="measure how much your camera brightens lens corners, from your own RAW files (F52)")
    lp_p.add_argument("folders", nargs="+", metavar="FOLDER", help="folders of RAW files (no subfolders)")
    lp_p.add_argument("--profile", help="profile folder to write lens_profiles.toml into (created if missing; default: the default profile folder)")
    lp_p.add_argument("--dry-run", dest="dry_run", action="store_true", help="measure and print, write nothing")
    lp_p.set_defaults(func=_cmd_lens_profile)

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

    gen_p = sub.add_parser("genres", help="list genre packs")
    gen_p.add_argument("genres_cmd", choices=["list"])
    gen_p.add_argument("--configs-dir", dest="configs_dir")
    gen_p.add_argument("--profile", help="profile folder with your own config differences (default: $AUTOEDIT_PROFILE, else ~/.config/autoedit if it exists)")
    gen_p.set_defaults(func=_cmd_genres)

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
    lu_p = look_sub.add_parser("lut", help="write the whole-picture colour part of a look as a .cube LUT file")
    lu_p.add_argument("name")
    lu_p.add_argument("-o", "--out", required=True, metavar="FILE.cube")
    le_p = look_sub.add_parser("export", help="write a look (and optionally a palette) as a look file")
    le_p.add_argument("name")
    le_p.add_argument("-o", "--out", metavar="FILE.aelook", help="file to write (default: NAME.aelook)")
    le_p.add_argument("--palette", help="a palette to put in the file")
    le_p.add_argument("--author", default="")
    le_p.add_argument("--licence", default="")
    li_p = look_sub.add_parser("import", help="check a look file and put its look into the profile")
    li_p.add_argument("file", metavar="FILE.aelook")
    li_p.add_argument("--name", dest="new_name", help="keep the look under this name")
    li_p.add_argument("--force", action="store_true", help="replace a look or palette of the profile with that name")
    lc_p = look_sub.add_parser("card", help="render a before-and-after sheet (PNG and HTML) for a look")
    lc_p.add_argument("name")
    lc_p.add_argument("photos", nargs="*", metavar="PHOTO_OR_FOLDER", help="at most 12 are shown (default: a drawn frame)")
    lc_p.add_argument("-o", "--out", required=True, metavar="DIR")
    lc_p.add_argument("--palette", help="a palette to show with the look")
    lc_p.add_argument("--backend", choices=["stub", "sam3", "auto"])
    for action_p in (ll_p, ls_p, ld_p, lu_p, le_p, li_p, lc_p):
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

    hist_p = sub.add_parser("history", help="the history of your own edits (off by default): status, clear")
    hist_sub = hist_p.add_subparsers(dest="history_cmd", required=True)
    hs_p = hist_sub.add_parser("status", help="is it on, where it lives, how much it holds")
    hc_p = hist_sub.add_parser("clear", help="delete the history")
    hc_p.add_argument("--yes", action="store_true", help="delete without the dry-run stop")
    for p_ in (hs_p, hc_p):
        p_.add_argument("--configs-dir", dest="configs_dir")
        p_.add_argument("--profile", help="profile folder that holds the history (default: $AUTOEDIT_PROFILE, else ~/.config/autoedit)")
    hist_p.set_defaults(func=_cmd_history)

    sty2_p = sub.add_parser("style", help="what your own edits show you keep doing (needs the history)")
    sty2_sub = sty2_p.add_subparsers(dest="style_cmd", required=True)
    ss_p = sty2_sub.add_parser("suggest", help="name the changes you keep making; --yes NAME adds them to a new look")
    ss_p.add_argument("--look", help="the look your history was made with (default: the most used)")
    ss_p.add_argument("--yes", metavar="NAME", help="write the suggestions into a new look NAME of the profile")
    ss_p.add_argument("--only", action="append", default=[], metavar="ID", help="only this suggestion (repeatable)")
    ss_p.add_argument("--configs-dir", dest="configs_dir")
    ss_p.add_argument("--profile", help="profile folder that holds the history (default: $AUTOEDIT_PROFILE, else ~/.config/autoedit)")
    sty2_p.set_defaults(func=_cmd_style)

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

    app_p = sub.add_parser("app", help="open the local UI in a native window (needs the [app] extra)")
    app_p.add_argument("--configs-dir", dest="configs_dir")
    app_p.add_argument("--profile", help="profile folder with your own config differences (default: $AUTOEDIT_PROFILE, else ~/.config/autoedit if it exists)")
    app_p.add_argument("--debug", action="store_true", help="enable the webview dev tools")
    app_p.set_defaults(func=_cmd_app)

    args = parser.parse_args(argv)
    if hasattr(args, "profile"):
        try:
            # `looks save` and `calibrate` may create the profile folder, so a --profile that does not exist yet is fine there.
            if args.profile and (args.cmd in ("calibrate", "lens-profile") or (args.cmd == "looks" and args.looks_cmd in ("save", "import"))):
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
