# LR AI-mask XMP schema — Phase 0a findings

> **Status: NOT YET CAPTURED.** This is the Rosetta Stone for the whole project
> (PLAN.md §7, 0a). Fill it in before writing any real apply logic.

## How to capture (no code)

1. In Lightroom Classic, edit a photo using **Select Subject** + **Select Sky** masks,
   giving each a *distinct, obvious* value (e.g. Subject Exposure +1.0, Sky Exposure −1.0).
2. Save as a preset and/or write XMP (Metadata ▸ Save Metadata to File for a DNG, or
   export the user preset `.xmp` from the develop presets folder).
3. Obtain one Adobe **adaptive** preset (the parametric kind) and export *its* XMP.
4. Diff (2) vs (3). The adaptive one stores the mask as an *instruction*, not baked pixels.

## What to record here

- [ ] LR Classic version used: ____
- [ ] Full `crs:MaskGroupBasedCorrections` block for a Select-Subject mask (paste below)
- [ ] Full block for a Select-Sky mask
- [ ] Which attributes are the AI *instruction* (re-segments on apply) vs. baked data
- [ ] The per-region value attribute names (e.g. `crs:LocalExposure2012`)
- [ ] Whether a baked mask blob is present and whether it's required for apply

## Phase 0b — SDK apply test result

- [ ] Path tested: `applyDevelopPreset` / `applyDevelopSettings`
- [ ] Did each of 5 different photos get a *distinct* mask? YES / NO
- [ ] If NO: fall back to parametric luminance/color **range masks** (still per-photo).

## Paste captured XMP below

```xml
<!-- paste real captured mask XML here, then port into plugin/.../templates/*.xmp.tmpl -->
```
