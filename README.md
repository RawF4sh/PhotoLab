<h1 align="center">AutoEdit</h1>

<p align="center">
  <b>Open-vocabulary batch photo auto-editing.</b><br>
  Segment a folder of photos into <i>arbitrary named regions</i> with Meta's SAM 3,
  compute per-region + global edits (rule baseline, optional LLM refine), and bake
  good-looking results — locally.
</p>

<p align="center">
  <a href="../../actions/workflows/ci.yml"><img alt="CI" src="../../actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-blue">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-green">
</p>

<p align="center"><a href="https://photolab.fridshtand.ai">Photo Lab — demonstrations and editing comparisons</a></p>

Photo Lab presents several editing approaches. This repository contains AutoEdit's local
rules pipeline, optional Anthropic refinement, and a Gemini assistant that proposes editable
adjustments in the local app. The site's generated-image experiments remain a separate
workflow. The public source does not include the comparison photographs, private
evaluation datasets, API credentials, or model weights.

---

## Why this exists

Segmentation-driven auto-editing is already commoditized — Lightroom's Adaptive Presets,
Imagen, Aftershoot, and Evoto all do *bulk → segment → per-region edit → global finish*,
but against a **fixed** region set (subject / sky / skin / background). The one open lane is
**open-vocabulary** targeting: edit *"the dress"*, *"the foliage"*, *"the bouquet"* — any
concept you can name. SAM 3 makes that possible, and SAM masks can't live inside Lightroom,
so AutoEdit is a standalone tool that applies its own pixel edits.

> **Honest scope.** On baked 8-bit JPEGs, highlight/shadow *recovery* can't match a raw
> editor — that latitude is gone. AutoEdit is *consistent, good-looking, open-vocab batch
> editing*, not raw-grade recovery. Feed 16-bit TIFFs to raise the ceiling.

## How it works

```
folder ─► decode ─► SAM 3 segment(concepts) ─► resolve masks (priority, no double-edit)
       ─► per-region stats ─► scene ─► decide (rules baseline → optional LLM refine)
       ─► apply (feather + masked pixel ops + global finish) ─► baked image + sidecar JSON
```

The pipeline analyzes on a downscaled proxy and applies at full resolution. Every edit is
explained in a `<name>.autoedit.json` sidecar (detected concepts, stats, settings, "why" notes).

## Install

```bash
pip install -e .            # core: numpy, Pillow, pydantic (runs now with the stub segmenter)
pip install -e '.[sam]'     # + torch + SAM 3 (real open-vocab masks; GPU recommended)
pip install -e '.[llm]'     # + Anthropic SDK (LLM per-region refine)
pip install -e '.[accel]'   # + OpenCV/tifffile/rawpy (speed, 16-bit TIFF, camera RAW)
pip install -e '.[heif]'    # + pillow-heif (HEIC/HEIF phone photos; AVIF needs no extra)
```

No GPU needed to try it: the **stub segmenter** (heuristic sky/subject/skin) runs the whole
pipeline on CPU. Swap to real SAM 3 with `--backend sam3`.

**First run with SAM 3** (once per machine): the weights are gated on Hugging Face under Meta's
SAM License. Accept it at https://huggingface.co/facebook/sam3, then `huggingface-cli login`
(or set `HF_TOKEN`); the ~850 MB model downloads on first use and is cached. `autoedit doctor`
tells you exactly which of these steps is missing. Apple Silicon runs on the `mps` device out of
the box (a CPU fallback is enabled for the few ops Metal lacks); NVIDIA uses `cuda`; anything else
falls back to CPU (slow but works). Camera RAW input (`.cr2/.nef/.arw/.dng`…) needs the `accel`
extra for `rawpy`. AVIF input works with Pillow 11.3 or later; HEIC and HEIF input needs the
`heif` extra (without it a HEIC photo fails alone, with a message naming the extra, and the rest of
the batch runs).

## Use

```bash
autoedit run ./photos ./out --preset wedding --look natural --intensity 0.8
autoedit run ./photos ./out --concepts "sky,skin,dress,foliage"   # arbitrary concepts
autoedit run ./photos ./out --decider llm --allow-cloud           # BYO-key LLM (see Privacy)
autoedit run ./photos ./out --dry-run                              # decisions + sidecars, no pixels
autoedit curate ./photos ./curated --mode best --count 300         # copy likely keepers for review
autoedit find ./out snow lake                                   # photos by what is in them
autoedit declare ./out                                          # what was changed, per photo, with hashes
autoedit gallery ./out                                          # a static page of the exports
autoedit sync ./out/IMG_0012.jpg                                # copy one photo's changes to its neighbours
autoedit check ./photos                                         # what the photos may give away (needs SAM 3)
autoedit train-aesthetic labels.csv ./images ./aesthetic.json       # train a lightweight taste scorer
autoedit curate ./photos ./curated --mode aesthetic --aesthetic-model ./aesthetic.json
autoedit run ./raws ./out --raw-mode preview_ev_headroom       # RAW development candidate (default: none)
autoedit run ./photos ./out --metadata keep                    # keep GPS and serial numbers too (default shareable drops both; none writes no EXIF)
autoedit run ./photos ./out --edit "brighten the jacket, darken the sky slightly"   # region instructions in words
autoedit adjust ./out/IMG_0001.jpg --set sky.exposure=-0.3   # re-render one photo from its sidecar
autoedit adjust ./out/IMG_0001.jpg --mark 0.58,0.12,0.92,0.94 --edit "make this guy more purple" --explain   # an oval (shares of the frame) and what should change there
autoedit run ./photos ./out --workers 4                        # render processes for a batch (default 1)
autoedit run ./photos ./out --export web                       # export preset: format, size, name, subfolder
autoedit exports       # list export presets
autoedit run ./photos ./out --consistency bursts               # even out frames of one framing (default: brightness)
autoedit looks save warm --from natural --set vibrance_add=6   # save your own look into your profile
autoedit run ./photos ./out --look warm --profile ~/my-profile # use a profile folder (or set AUTOEDIT_PROFILE)
autoedit calibrate ./photos --out ./calib                      # fit a personal look from blind pairs you vote on
autoedit models status # SAM 3 weights: status | download | remove
autoedit settings      # every run setting with its type, default and range (--json for the schema)
autoedit run ./photos ./out --highlight-hold --denoise --levels   # three quality options, all off by default
autoedit run ./photos ./out --grain 25                         # film grain (0-100), added after the export resize
autoedit run ./photos ./out --look bw_red_filter               # black and white with a red filter (dark skies); also film_warm, film_faded, instant…
autoedit run ./photos ./out --lut film.cube --lut-amount 70     # a .cube colour LUT over the finished picture
autoedit looks lut film_faded -o film_faded.cube              # a look's whole-picture colour as a LUT for other programs
autoedit run ./photos ./out --look surprise --seed 7          # prints six looks drawn by the seed, runs the first
autoedit run ./photos ./out --history                          # keep a local history of your own changes (off by default)
autoedit style suggest                                         # habits found in that history; --yes NAME saves them as a look
autoedit looks export film_faded -o film_faded.aelook          # a look as one file to give away; looks import FILE takes one in
autoedit looks card film_faded ./photos -o ./card              # before-and-after sheet (PNG + HTML) of a look on your photos
autoedit run ./photos ./out --genre alpine                     # a genre pack: things to find, look and settings for a kind of photo
autoedit run ./photos ./out --straighten --aspect 4:5          # level a horizon (SAM 3) and crop to a shape; both off by default
autoedit run ./products ./out --neutral 0.12,0.85 --neutral-from ./products/card.jpg --backdrop   # grey card sets the white balance; backdrop evened to white
autoedit run ./night ./out --genre astro                       # night frames: town glow off the sky, stuck pixels repaired, stars spared
autoedit merge ./brackets -o ./merged                         # tripod brackets, repeats, focus series or trails into one 16-bit TIFF each
autoedit genres list                                           # the packs, packaged and your own
autoedit pick ./shoot -o ./picks                               # per burst, the sharpest well-exposed frame; sources never touched
autoedit doctor        # check device + backend availability
autoedit presets       # list concept presets
```

`train-aesthetic` is for datasets like AVA/Flickr-AES that score finished photos. That scorer
does not teach the editor how to transform an original into an edit; it acts as a critic/ranker
for culling huge folders, filtering generated/reference edits, prioritizing human review, and
choosing the best variants from a batch.

Open the interface with `pip install -e '.[ui]'` and `autoedit ui` (a browser tab on
`127.0.0.1:8765`), or as a desktop window with `pip install -e '.[app]'` and `autoedit app` (on
Linux it needs a GTK or Qt web view). Open one photo, a folder, or the generated sample set.
Refine the automatic result with saved tone/color controls, compare looks, mark a region or match
a reference. Preview, Save and batch editing use the same per-photo choices. Projects retain
your changes and undo history when reopened. Split comparison, preview zoom, a filmstrip and the
review queue help check a shoot; Copy to similar lets you choose recipients and adjustments.
Find, share checks and gallery tools remain on the same screens. Light or dark follows the system.
See [APP_GUIDE.md](APP_GUIDE.md).

Run without installing:
```bash
PYTHONPATH=src python -m autoedit.cli run ./photos ./out --backend stub
# or: scripts/dev-run.sh ./photos ./out
```

## Privacy / cloud

AutoEdit is **local by default** — segmentation and edits run on your machine; nothing is sent
anywhere. The optional LLM decider (`--decider llm`) is **off unless you opt in** with
`--allow-cloud` (or `AUTOEDIT_ALLOW_CLOUD=1`) **and** provide your own API key (`--api-key` or
`ANTHROPIC_API_KEY`). When enabled, it sends a **small thumbnail + image stats** to your AI
provider and bills **your** key per photo — full-resolution pixels never leave your machine.
Without consent or a key it transparently falls back to the local rules. Run `autoedit doctor`
to see your current status.

The photo editor also offers an optional **Gemini assistant**. Enable it explicitly in Settings
and supply a session-only key or `GEMINI_API_KEY`. Each request sends a stripped thumbnail
(at most 512 pixels) and numeric editing context to Google. Review the proposed adjustments
before applying them; they become normal saved controls with undo, and subsequent previews
and exports run locally. No key is saved in projects or presets. Provider errors leave the
local edit usable. Automated checks use a fake provider and do not upload photos.

The guided workspace includes object sliders, **Find an object**, selection brushes, crop and
straighten, native-resolution detail inspection, and reusable **My presets** for photos and
folders. Precise object selection requires SAM 3; the stub offers explicitly approximate demo
regions. The opt-in `natural_clean` look is a candidate for blind comparison, not a new default.
See [APP_GUIDE.md](APP_GUIDE.md) for the controls and [the F55 plan](docs/plans/F55-guided-photo-workflow.md)
for validation and remaining limits.

The interface is a local application. Keep its default loopback address; the Photo Lab website
links to this project and does not expose it or your filesystem. Generated images can
retain source metadata, and sidecars can contain local paths. Review and strip private
metadata before sharing exports. See [SECURITY.md](SECURITY.md) for reporting and publishing
guidance.

## Configure (no code changes)

Everything tunable lives in [src/autoedit/configs/](src/autoedit/configs/) (override with a
local `./configs` dir or `--configs-dir`):
- [rules.toml](src/autoedit/configs/rules.toml) — per-region stats → edit-value expressions (the baseline).
- [styles.toml](src/autoedit/configs/styles.toml) — selectable styles (look + default concepts + brief).
- [looks.toml](src/autoedit/configs/looks.toml) — magnitude shaping + split-tone color grading.
- [compose.toml](src/autoedit/configs/compose.toml) — radial/linear compositional moves (vignette, sky grad).
- [concepts.toml](src/autoedit/configs/concepts.toml) — concept presets + per-concept priority/feather/rule mapping.
- [instructions.toml](src/autoedit/configs/instructions.toml) — the words `--edit` understands (verbs, amounts, limits per op), and the gates and words for marks.
- [style.toml](src/autoedit/configs/style.toml) — when a habit in your history becomes a suggestion (12 photos, 3 days, 70 %).
- [lessons.toml](src/autoedit/configs/lessons.toml) — what each tool does, when to use it and what to watch for (the "Show me how" walk-through).
- [exports.toml](src/autoedit/configs/exports.toml) — export presets for `--export` (`full`, `web`, `social`, `archive`, `webp`).
- [calibrate.toml](src/autoedit/configs/calibrate.toml) — dimensions and step sizes of `autoedit calibrate`.
- [settings.toml](src/autoedit/configs/settings.toml) — the description of every run setting, shown by `autoedit settings`.

A **profile** is a folder that holds only your differences, in TOML files named like the packaged
ones. It is merged key by key over the packaged files, so changing one value does not mean copying
a file. Point to it with `--profile DIR` or `AUTOEDIT_PROFILE` (default: `~/.config/autoedit` if it
exists); `autoedit looks save` writes your own looks there.

## Develop

For unattended recursive RAW/JPEG selection, verified exports and source-relative
safety, use `PYTHONPATH=src python3 scripts/quality_workflow.py PHOTOS EXPORTS --backend stub`.
The default wraps the current editor; `--workflow global-first` and `--workflow optimized`
are experimental alternatives. The [completed evaluation](docs/QUALITY_EVALUATION_2026-09-29.md) retains the current default and documents measured tradeoffs. See [the quality workflow guide](docs/QUALITY_WORKFLOW.md)
for frozen tuning/holdout experiments, sidecar provenance, limitations and two-arm blind review.

```bash
PYTHONPATH=src python3 tests/run_all.py    # zero-dependency test runner (or: python3 -m pytest)
python3 scripts/make_smoke_corpus.py ./test-corpus  # once, in an empty fixture directory
PYTHONPATH=src python3 -m autoedit.cli run ./test-corpus ./out-smoke-new --backend stub
PYTHONPATH=src python3 scripts/tone_report.py ./raws --render --backend stub --fail-on-regression
```

The deterministic smoke generator refuses to overwrite existing fixtures. Use a new
output directory for each smoke run. The quality guide documents separate two-arm
global-baseline and camera-reference galleries from accepted exports.

Use `tone_report.py --fail-on-regression` for an unattended gate; incomplete reports fail too.
Blind galleries retain the run configuration and file hashes in `run.json`, plus pipeline sidecars
with mask-aware statistics and review reasons. Rank the gallery in `index.html`, download its votes,
then score them with `scripts/blindtest.py --score path/to/results.csv`.

Architecture and conventions: [CLAUDE.md](CLAUDE.md). The implementation plan that produced
this: see the approved plan referenced in [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE) for AutoEdit's code. **SAM 3** ships under Meta's custom SAM License (not
MIT) — its weights are fetched from Hugging Face on first use and never redistributed here;
read Meta's license before any commercial use.
