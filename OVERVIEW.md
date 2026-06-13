# AutoEdit — Mini Plan

> ⚠️ **Superseded (v0.1 history).** Describes the original Lightroom-plugin product. The
> project pivoted to a standalone open-vocabulary editor — see [README.md](README.md).


The one-page version. Full engineering spec is [PLAN.md](PLAN.md); working rules are
[CLAUDE.md](CLAUDE.md).

## What it is

A free, open-source **Lightroom Classic** plugin that auto-edits photos by computing
**per-photo edit values** and applying them onto Lightroom's own native AI masks
(subject, sky, background, faces). Every result is a normal, fully editable Lightroom
edit. Nothing is uploaded; nothing is baked; no style training required.

The difference from what already exists: Adobe's adaptive presets pick *where* to edit but
apply *fixed* values. AutoEdit measures each photo (how buried the subject is, whether the
sky is blown or hazy, white balance, clipping) and computes *values that fit that photo*.

## Why it can exist at all (the structural edge)

Lightroom Classic is the **only** host that has *both* a real third-party plugin SDK *and*
on-device AI masking. So AutoEdit gets world-class segmentation for free, computed locally
on the user's machine — masks travel as instructions, not pixels. That means **zero servers,
zero marginal cost, zero uploads**. Competitors that learn-from-catalog (Imagen, Aftershoot)
don't get this free local segmentation; that combination is the one edge worth leaning on.

## How it's built

Two halves joined by one JSON seam:

- **Thin Lua plugin** — orchestration only: UI, pull a thumbnail + metadata, call the
  engine, drop computed values into a preset, apply it. Kept thin so Adobe's version churn
  stays contained.
- **Fat Rust engine** — a single self-contained binary (no deps, no GPU). Decodes the
  thumbnail, does *rough* segmentation to **measure** regional stats, classifies the scene,
  and runs rules (`rules/*.toml`) that map stats → per-region develop values. Milliseconds
  per photo.

**Two segmentations, two jobs:** ours is rough and only *measures*; Lightroom's is precise
and *applies* the values. We never produce pixel-perfect masks — and we **never author an
AI mask, only inject values into one Lightroom's own UI produced** (see PLAN.md §5/§7).

## What's honestly hard (read before betting time)

- **The whole thesis hinges on Phase 0:** can the SDK apply a preset and make each photo
  re-segment locally? Prove it in a weekend before building anything else.
- **Real competition is style-learners, not static presets.** Rule-based edits must clearly
  beat "LR Auto + a good preset" in a blind test, or there's no reason to ship.
- **v1 is a distribution play, not an IP play.** The rules aren't a moat; the *durable* IP
  is learned profiles built later from real keep/override data. v1's job is audience + that
  training data.

## Platform

- **Host:** Lightroom Classic (the only viable choice — see above).
- **OS:** develop on macOS, **ship macOS + Windows from v1.** A large share of high-volume
  event/wedding shooters (the beachhead) are on Windows; Mac-only would exclude them. Test
  Windows deliberately since it's the less-convenient platform to validate from a Mac.

## Build order

| Phase | Goal | Gate to pass |
|---|---|---|
| 0 · spike | Prove SDK re-segments per photo; capture one known-good mask preset | Works + ≤3s/photo, else stop |
| 1 · engine | Rules engine standalone; tune on real photos | Blind test clearly beats LR-Auto+preset, else stop |
| 2 · plugin MVP | 3 scenes × 3 looks, batch UI; capture keep/override signal | 15–20 beta shooters on real galleries |
| 3 · launch | Notarized mac+win release, README before/after GIFs, press | Public artifact + usage data |

## Success gate (revised)

Within **90 days** of launch: 1,000+ downloads, **200+ 30-day-active** installs (batch tools
are used per-gig, not weekly), recurring requests clustering on gated features, **and** a
positive willingness-to-pay signal (waitlist/pledge). Hit it → build Pro. Miss it → stop;
~3 part-time months spent, a public artifact + real data gained.
