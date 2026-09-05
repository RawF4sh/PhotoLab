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

<!-- TODO(launch): replace with a real before/after GIF — this is the product page. -->
<p align="center"><i>[ before / after demo GIF goes here ]</i></p>

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
pip install -e '.[accel]'   # + OpenCV/Kornia/colour-science/tifffile/rawpy (quality + 16-bit)
```

No GPU needed to try it: the **stub segmenter** (heuristic sky/subject/skin) runs the whole
pipeline on CPU. Swap to real SAM 3 with `--backend sam3`.

## Use

```bash
autoedit run ./photos ./out --preset wedding --look natural --intensity 0.8
autoedit run ./photos ./out --concepts "sky,skin,dress,foliage"   # arbitrary concepts
autoedit run ./photos ./out --decider llm --allow-cloud           # BYO-key LLM (see Privacy)
autoedit run ./photos ./out --dry-run                              # decisions + sidecars, no pixels
autoedit curate ./photos ./curated --mode best --count 300         # copy likely keepers for review
autoedit train-aesthetic labels.csv ./images ./aesthetic.json       # train a lightweight taste scorer
autoedit curate ./photos ./curated --mode aesthetic --aesthetic-model ./aesthetic.json
autoedit run ./raws ./out --raw-mode preview_ev_headroom       # RAW development candidate (default: none)
autoedit doctor        # check device + backend availability
autoedit presets       # list concept presets
```

`train-aesthetic` is for datasets like AVA/Flickr-AES that score finished photos. That scorer
does not teach the editor how to transform an original into an edit; it acts as a critic/ranker
for culling huge folders, filtering generated/reference edits, prioritizing human review, and
choosing the best variants from a batch.

> **Planned:** `autoedit ui` — a local web app (FastAPI + browser): drop a folder, pick a
> style, type what you want, preview before/after, run the batch. Same backend, so the local
> app and a future hosted version share one codebase. See [PLAN.md](PLAN.md) §5.

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

## Configure (no code changes)

Everything tunable lives in [src/autoedit/configs/](src/autoedit/configs/) (override with a
local `./configs` dir or `--configs-dir`):
- [rules.toml](src/autoedit/configs/rules.toml) — per-region stats → edit-value expressions (the baseline).
- [styles.toml](src/autoedit/configs/styles.toml) — selectable styles (look + default concepts + brief).
- [looks.toml](src/autoedit/configs/looks.toml) — magnitude shaping + split-tone color grading.
- [compose.toml](src/autoedit/configs/compose.toml) — radial/linear compositional moves (vignette, sky grad).
- [concepts.toml](src/autoedit/configs/concepts.toml) — concept presets + per-concept priority/feather/rule mapping.

## Develop

```bash
PYTHONPATH=src python tests/run_all.py    # zero-dependency test runner (or: python -m pytest)
PYTHONPATH=src python scripts/tone_report.py ./raws --render --backend stub   # objective tone gate (dark→darker, clipping, vs camera)
PYTHONPATH=src python scripts/raw_benchmark.py ./raws                          # rank RAW development candidates vs the camera render
```

Architecture and conventions: [CLAUDE.md](CLAUDE.md). The implementation plan that produced
this: see the approved plan referenced in [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE) for AutoEdit's code. **SAM 3** ships under Meta's custom SAM License (not
MIT) — its weights are fetched from Hugging Face on first use and never redistributed here;
read Meta's license before any commercial use.
