# AutoEdit — Project Plan

> Open-vocabulary batch photo auto-editing. Segment a folder into *arbitrary named regions*
> with SAM 3, decide per-region + global edits (style + a plain-language brief, rule baseline,
> optional LLM), and bake good-looking results — locally, no per-user training.

This is the live plan (v0.2). The prior Lightroom-plugin plan is history in [OVERVIEW.md](OVERVIEW.md).

## 1. What this is
A standalone tool (CLI today; local web app next) that batch-edits photos the way the
incumbents won't: **open-vocabulary** region targeting (edit "the dress", "the foliage", any
named concept), steered by **a style you pick + a sentence describing what you want** — instead
of cloning your 3,000-edit catalog the way Imagen/Aftershoot do.

**Honest positioning (keep in README):** on baked 8-bit JPEGs this can't match raw-grade
highlight/shadow *recovery*. It's "consistent, intentional, open-vocab batch editing," not a raw
developer. 16-bit TIFF input raises the ceiling.

## 2. The wedge (why this isn't just another auto-editor)
Segmentation-driven auto-masking is commoditized (Lightroom Adaptive Presets, Imagen,
Aftershoot, Evoto). Two things are *not* shipping as a turnkey product:
1. **Open-vocabulary** regions (arbitrary named concepts), and
2. **No-training intentionality** — pick a style + describe intent, and the tool reasons about
   *which* regions to treat and *how* (the LLM layer), including future **compositional masks**
   (radial/linear gradients placed to guide the eye).
The catch, stated plainly: "edit like a pro" is a taste problem; reasoned-from-scratch edits
must rival cloned-from-catalog edits. That blind-test bar is the whole bet (§6 gate).

## 3. The pipeline (understand → decide → apply)
Every photo flows through three movements; the code is in [pipeline.py](src/autoedit/pipeline.py).

```
DECODE        imageio.py      raw/JPEG/TIFF → float RGB, color-managed
              │
┌─ UNDERSTAND ─────────────────────────────────────────────────┐
│ segment      SAM 3 / stub    → masks for named concepts        │  what's in the photo
│ resolve      resolve.py      → priority arbitration, drop absent│  where each thing is
│ stats        stats.py        → per-region lum/clip/sat/haze     │  how each region looks
│ scene        scene.py        → people / landscape / other       │  what kind of photo
└──────────────────────────────────────────────────────────────┘
              │
┌─ DECIDE ─────────────────────────────────────────────────────┐
│ rules        rules.toml      → stats → baseline values/region   │  what it needs
│ style        looks.toml      → shape magnitude + intensity       │  the look you picked
│ LLM (opt)    llm_decider     → bounded per-region deltas + brief │  what you asked for
└──────────────────────────────────────────────────────────────┘
              │
┌─ APPLY ──────────────────────────────────────────────────────┐
│ mask         edit/mask.py    → upsample + feather to full res    │
│ ops          edit/ops.py     → per-region pixel edits, composite │  do it
│ finish       edit/apply.py   → global pass + gamut guard         │
└──────────────────────────────────────────────────────────────┘
              │
EMIT          emit.py          → baked image + <name>.autoedit.json ("why" notes)
```

**Built today** (Python package [src/autoedit/](src/autoedit/), runs on CPU with the stub
segmenter; 12 tests green): every stage above except real SAM 3 (placeholder in
[segment/sam3.py](src/autoedit/segment/sam3.py), pending the Phase-A spike) and the
LLM natural-language brief / compositional masks (§4). `RulesDecider` + `LlmDecider` (Claude
tool-use, refine/propose, cost caps, safe fallback) both exist; `autoedit` CLI
(`run`/`doctor`/`presets`); config-as-data in [configs/](configs/); Python CI.

### 3a. What the edits are made *of* — the adjustment vocabulary
| Vocabulary | What it is | Role | Status |
|---|---|---|---|
| Basic sliders | exposure, contrast, highlights/shadows/whites/blacks, WB, vibrance/sat, dehaze, clarity | the floor | ✅ [edit/ops.py](src/autoedit/edit/ops.py) |
| Tone curves | per-region luminance + RGB curves | real tonal shaping | add |
| Targeted HSL | shift/desaturate specific hues (greens, skin) | color control | add |
| **Color grading** | shadow/mid/highlight color wheels + split-tone | **the jump from "corrected" → "graded"** | add |
| **Compositional masks** | radial / linear gradients *placed by reasoning* (guide the eye) | the differentiator | planned (§4) |
| LUTs | drop-in film/creative look per style | fast style identity | easy add |
| Generative relight | AI/depth-aware light shaping | highest ceiling, heaviest | far future |

Path from auto-corrected to intentionally edited: **sliders → + color grading + tone curves +
compositional masks**, in that order. LUTs give each *style* a strong identity cheaply.

### 3b. What the edits are made *through* — the apply/output target
| Target | How | Pros | Cons | Verdict |
|---|---|---|---|---|
| **A. Bake ourselves** | numpy/GPU pixel ops → JPEG/TIFF | full control, **any open-vocab region**, standalone, instant preview | destructive, **8-bit recovery ceiling** | **Primary** — only target that keeps open-vocab |
| B. Editable XMP → Lightroom/ACR | write develop settings + parametric masks | non-destructive, **raw latitude**, pros refine in LR | **SAM masks can't be LR-native masks** → only global + radial/linear/range survive; **loses open-vocab** | optional "hand-off to Lightroom" mode |
| C. Layered PSD/TIFF | region = layer + mask alpha | separable, any region | baked-per-layer, huge files, PS-centric | niche export later |
| D. Raw-developed bake | rawpy/libraw → edit 16-bit linear | **best quality**, still open-vocab | raw is hard, heavier/slower | the quality upgrade for A |

**Decision: bake ourselves (A) is the spine** — it's the *only* medium that can carry an
arbitrary-concept edit like "warm the dress" (B literally cannot). Raise A's ceiling later with
raw input (D); offer XMP export (B) as an optional Lightroom hand-off, honestly labeled
global+parametric only.

## 4. Next feature: style + describe-what-you-want
- **Styles:** promote [configs/looks.toml](configs/looks.toml) into selectable **style profiles**
  (look params + default concept set + a baked style brief), e.g. Natural / Editorial / Warm
  Film / Punchy.
- **Brief:** a `--prompt "..."` natural-language brief threaded into `LlmDecider` (it already
  gets thumbnail + stats); it steers per-region deltas *and* concept selection (open-vocab).
- **Compositional masks (the differentiator):** add radial/linear alpha generators to `edit/`
  and let the LLM *place* them (center/falloff) by reasoning about subject location — "radial
  lift on the subject, graduated darken on the sky." This is the "intentional composition" piece.
- Consistency guards already designed: rules baseline + bounded deltas + temp 0 + stats-cache so
  a gallery stays coherent rather than drifting per photo.

## 5. Delivery: a local web app (the "app or website" answer)
A **FastAPI backend + browser UI** (`autoedit ui` launches a local server and opens the
browser). One codebase that is simultaneously:
- **an app** — runs on your machine, photos never leave (private), uses your GPU or BYO API key;
- **a website** — the same backend deployed to a server *is* the hosted product (§7).

UI: drop a folder → pick a style → type a brief → toggle concepts → before/after preview → run
batch → review. Reuses [batch.py](src/autoedit/batch.py)/[pipeline.py](src/autoedit/pipeline.py)
unchanged. No native installer / notarization now (a Tauri wrapper is an optional "real
installer" later). Install = `pip install autoedit`.

## 6. Roadmap & the kill gate
- **Phase A — Quality (the gate).** Wire the real SAM 3 call (spike), tune `rules.toml` +
  styles on real photos, build the blind test ([scripts/blindtest.py](scripts/blindtest.py)):
  AutoEdit vs global-auto baseline vs unedited, blind-scored. **Kill criteria:** clearly beats
  baseline; low halo/banding rate; ~0 catastrophic. Miss → fix or stop. *Nothing below matters
  until this passes.*
- **Phase B — Local web UI.** FastAPI + browser app over the existing batch engine; styles
  picker + brief + before/after. The portfolio centerpiece.
- **Phase C — Open-source launch.** Polished README + real before/after GIFs + 90-sec demo;
  GitHub release; `pip install`; post to r/photography, r/Lightroom, Show HN, Fstoppers/PetaPixel.
- **Phase D — Optional cloud + money (only if traction).** Hosted-compute toggle (serverless
  GPU for SAM + LLM proxy) so no-GPU users get one-click results; that hosted tier is the paid
  product. Build only if Phase C shows demand.

## 7. Money (open-core, side-project pace)
- **Now:** MIT OSS + **GitHub Sponsors / Ko-fi**. The real near-term return is the public
  artifact + credibility, not revenue.
- **Seam for later (no build now):** **open-core.** Engine/CLI/UI stay MIT and free to run
  locally (BYO GPU or API key). The paid thing, *if* it earns its keep, is the **hosted cloud
  service** — managed SAM+LLM, no setup, no GPU, accounts/credits — the same free-local-vs-paid-
  cloud split as Aftershoot (local) vs Imagen (cloud). Possible add-ons: premium curated **style
  packs**, higher batch limits. The architecture (Segmenter protocol + decider + FastAPI backend)
  already makes this a bolt-on, not a rewrite.
- **Don't build billing/auth now** — it's a side project; revalidate if a release gains users.

### The three tiers of the "decide" brain (and the BYO-key question)
The "decide" stage can run three ways. This is the spine of the free→paid split:

| Tier | "Decide" brain | Who pays | Audience | Status |
|---|---|---|---|---|
| **1. Rules (default)** | local rule engine | nobody | everyone, zero setup | ✅ built |
| **2. BYO-key power-up** | the user's own API key | the user, per photo, to the AI provider | devs/power-users who opt in | ✅ built (off by default) |
| **3. Managed cloud** | our hosted LLM **+ SAM** | they pay *us* (credits/sub) | photographers who won't touch keys/GPUs | future seam (Phase D) |

**Important reality checks on "target people with an AI subscription":**
- An "AI subscription" (ChatGPT Plus / Claude Pro) is **not** API access — those are the chat
  apps; the API is a separate pay-per-token developer account. So BYO-key reaches *people with a
  paid API account* (a small, technical niche), **not** photographers. Targeting them would
  trade the photographer wedge for a dev niche — don't.
- The LLM is only the **brain** (intent/values + concept planning). It does **not** segment —
  **SAM still runs** (local GPU or hosted). BYO-key covers Tier 2's brain, not segmentation.
- BYO-key still sends photos to the AI provider and still costs the user per photo. It
  relocates privacy/cost, it doesn't remove them.

**Recommendation:** keep targeting **photographers**, rules-first and fully usable with no AI.
Treat **BYO-key as an opt-in mode, not a target** — it lets the "describe-what-you-want" brain
exist at zero cost to us *and* serves as the demand signal for whether the **managed cloud
(Tier 3)** — the actual monetization — is worth building.

## 8. Licensing
- **MIT** for AutoEdit code (engine, CLI, UI, rules, styles).
- **SAM 3** is Meta's custom SAM License (not MIT) — weights fetched from Hugging Face on first
  run, never vendored; flag commercial terms before any paid cloud use.
- Future closed bits (only if they exist): the hosted service + any learned/premium profiles.

## 9. GitHub & launch setup
- Repo: keep this one; update topics → `photo-editing`, `segment-anything`, `sam3`, `python`,
  `batch-editing`, `open-vocabulary`, `ai-photography`.
- [pyproject.toml](pyproject.toml) extras already define the install story: `pip install autoedit`
  (core), `.[sam]` (real SAM), `.[llm]`, `.[accel]`.
- CI is Python (`tests/run_all.py`); add a release workflow that publishes to PyPI on tag.
- Issue templates + `FUNDING.yml` (Sponsors). Support policy: GitHub Issues, best-effort.
- README is the landing page — before/after GIFs are the product; that's the launch effort.

## 10. Risks
SAM 3 license/redistribution (isolate, fetch weights, don't vendor) · GPU/packaging (extras +
`doctor` + CPU/stub fallback; cloud toggle removes the GPU requirement later) · **the quality
bet** — reasoned edits must look pro, measured by the Phase-A blind gate · LLM cost/consistency
(refine deltas, clamps, caching, budget caps, rules fallback) · 8-bit recovery ceiling (prefer
16-bit; position honestly) · scope creep — it's a side project, so Phase A→C is the spine;
cloud/money is strictly optional.

## 11. Strategic reality-checks (carried from v0.1, still true)
- Competition is **style-learners, not static presets**; beating "a good preset" to a trained
  eye is the bar.
- The auto-masking itself is **not** the moat (everyone has it); the wedge is open-vocab +
  no-training + describe-what-you-want + (eventually) compositional masks.
- v0.2's job as a side project: a credible, novel public artifact + the option value of a paid
  cloud tier — not immediate revenue.
