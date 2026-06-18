# AutoEdit — Design System & GUI Spec

A premium, photographer-focused UI for local-first, open-vocabulary **batch** photo editing.
Positioning: a *serious creative tool* (Lightroom / Capture One / Aftershoot class) that is
**simpler and more guided** — photos first, controls second, and always an answer to *"why
this edit?"*

The high-fidelity, clickable mockup lives at
[`src/autoedit/web/static/studio.html`](../src/autoedit/web/static/studio.html). Open it
directly in a browser, or run `autoedit ui` (served at `/`; the minimal functional UI moves to
`/classic`). It works standalone (fake imagery drawn to `<canvas>`) and progressively enhances
with real endpoints when served.

---

## 1. Navigation

A **linear, guided workflow** with a persistent top stepper — never a sprawling raw-editor.

```
Import → Setup → Concepts → Preview → Run → Review        ⚙ Settings (modeless, top-right)
```

- Top app bar: brand mark · workflow stepper (click any step) · "Runs locally" status pill · settings.
- Steps show state: upcoming (number) · active (gradient) · done (green check).
- Forward motion via one primary CTA per screen; back is always available and non-destructive.
- Settings is reachable anywhere and returns you to your place.

**Why a stepper, not a sidebar:** the product's promise is *guided intentional batch editing*.
The stepper makes the 5-minute path obvious and calm; power lives inside each step, not in a wall
of panels.

---

## 2. Screens → component hierarchy

```
App
├─ TopBar { Brand, Stepper, LocalPill, IconButton(Settings) }
└─ Screens
   ├─ Home            Hero · Dropzone(primary CTA, trust note) · RecentProjects[Card]
   ├─ Setup           ThumbRail[Thumb(no, status)] · PreviewStage(FitFillSeg) · ControlsPanel
   │                    └─ StyleGrid[StyleCard] · BriefTextarea · IntensitySeg · Toggle×2 · CTA
   ├─ Concepts        BeforeAfter(maskOverlay, zoom) · ConceptList[Chip(check,swatch,conf,edit)] · AddConcept
   ├─ Preview         BeforeAfter(Before/After/Split/SideBySide) · GlobalsGrid[ValueChip]
   │                    · RegionList[RegionRow] · WhyPanel(collapsible) · ActionBar
   ├─ Running         NowEditing(thumb, %, ETA) · ProgressBar · CountBadges · Queue[QueueRow(status)] · Log
   ├─ Review          FilterChips · ExportSeg · ReviewGrid[ReviewCard(score, safetyBadge)] · ExportBar
   └─ Settings        SettingsCard × {General, Performance, SAM, AI Planner, Privacy}
```

**Shared/atomic components:** `Button(primary|secondary|ghost · sm|md|lg)`, `Card`, `Panel`,
`Chip`, `Badge(status)`, `Toggle`, `Slider`, `Segmented`, `Dropzone`, `BeforeAfter`, `Thumb`,
`ProgressBar`, `ValueChip`, `Note(info|warn)`, `Stepper`.

---

## 3. Design tokens

### Color
| Role | Token | Value |
|---|---|---|
| Background (matte charcoal) | `--bg` / `--bg-2` | `#0F1115` / `#14161A` |
| Panel / raised | `--panel` / `--panel-2` / `--raise` | `#1A1D23` / `#1E222A` / `#22262F` |
| Hairline / border | `--line` / `--line-2` | `#2A2F38` / `#343A45` |
| Text | `--ink` / `--ink-2` / `--ink-3` / `--ink-4` | `#EDF0F4` → `#5B6470` |
| **Accent (electric blue→violet)** | `--accent` / `--accent-2` | `#3B7DFF` / `#7C5CFF` |
| Accent gradient | `--grad` | `linear-gradient(135deg,#3B7DFF,#7C5CFF)` |
| Success / Warning / Danger / Info | `--ok` `--warn` `--danger` `--info` | `#3DD68C` `#F5B23D` `#FF6060` `#49C0E8` |

Backgrounds carry two faint accent radial glows (top-right violet, left blue) over charcoal for
depth without clutter.

### Typography
Inter / SF Pro / system stack. Scale: **Display 30** · H2 22 · H3 18 · Body 14 · Small 12.5 ·
Micro 11 (uppercase labels, `letter-spacing .08–.14em`, `--ink-3`). Headings `font-weight 650`,
`letter-spacing -.2px`. Tabular numerals for all adjustment values.

### Spacing & shape
4px base scale (8/12/14/16/20/26). Radius: `sm 8 · md 12 · lg 16 · xl 22`. Shadows: inset
hairline + soft drop (`--sh-1`), elevated `--sh-2`, accent focus glow `--sh-glow`. Motion: 0.16–0.28s
`cubic-bezier(.4,0,.2,1)`.

### Components (states)
- **Button** — primary = accent gradient + soft glow; secondary = raised + hairline; ghost = text.
  Hover lightens; `:disabled` 45% + not-allowed.
- **Chip (concept)** — swatch + checkbox + label + confidence pill + hover edit icon. `on` →
  accent border + soft gradient fill. Confidence `—` when the concept is absent.
- **Badge (status)** — pill with LED dot: `Waiting · Analyzing · Editing · Finished · Needs review
  · Failed`, each its own hue.
- **Toggle / Slider / Segmented** — accent-gradient active state; sliders have a white thumb ringed
  in accent.
- **BeforeAfter** — draggable split (vertical handle + knob), `BEFORE`/`AFTER` tags, optional
  colored mask overlay (screen blend). Modes: Before · After · Split · Side-by-side.
- **Thumb** — image number (top-left) + status badge (top-right); selected = accent ring.

---

## 4. Responsive layout (desktop browser)

- **≥1280px** — three-pane work screens: `rail 208 · stage 1fr · controls 340–372`.
- **960–1280** — rail collapses to a horizontal filmstrip; controls stay docked right.
- **<960 (rare; tablet)** — single column: stage on top, controls below as an accordion; stepper
  scrolls horizontally.
- Image previews are the first thing to grow; control panels are fixed-width and scroll
  independently. Grids: Review 4-up → 3 → 2; Recent 3-up → 2 → 1.

---

## 5. Microcopy

**Buttons** — `Choose folder…` · `Choose a single photo` · `Analyze photos →` · `Preview the edit
→` · `Run batch` · `Adjust` · `Accept` · `Apply to similar` · `Export batch →` · `Export notes
(JSON)` · `Pause` / `Resume` / `Cancel`.

**Empty / hero states**
- Dropzone: **"Drop a folder of photos"** / "or use a picker — JPG, PNG, TIFF · RAW coming soon".
- No recents: "Your projects will show up here. Drop a folder to start your first edit."
- Concepts (none yet): "Analyze the photo to see what AutoEdit can target — or add a concept by name."

**Trust / privacy**
- "Runs locally. Your photos never leave your machine unless you opt in to the cloud brief planner."
- Planner warning: "When the planner is on, a small thumbnail + image stats are sent to your
  provider. Full-res pixels never leave your machine."

**Tooltips**
- Intensity: "Subtle keeps it true-to-life; Strong pushes a bolder grade."
- Mask overlay: "See exactly which pixels each concept owns."
- Confidence: "How sure SAM 3 is this region is here. Below 40% is hidden by default."
- Apply to similar: "Reuse this photo's adjustments on visually similar frames."

**Warnings / safety badges**
- `halo risk` — "Edge halos possible from strong clarity/dehaze. Inspect at 100%."
- `clipped` — "Highlights or shadows are clipping. Lower intensity or protect the region."
- `unreadable` — "Couldn't decode this file (RAW or corrupt). Skipped."
- Review filter counts live on the chips: `All 12 · Accepted 9 · Needs review 2 · Failed 1`.

**Why-this-edit (verbatim from the engine's sidecar notes)** — e.g. "Scene classified as landscape
(sky + mountain + snow detected)." · "Highlights recovered −42 to hold pale sky and snow texture."
· "No skin or people detected → portrait protections skipped." This panel is the product's
transparency promise and should always quote the real sidecar `notes`.

---

## 6. Build notes (wiring to the engine)

The mockup already maps 1:1 to existing endpoints:
`/api/styles` (Style grid) · `/api/pick` (folder/file picker) · `/api/list` (thumb rail) ·
`/api/preview` (Concepts + Preview before/after + globals/regions/notes) · `/api/run`,
`/api/run-one` (Running). The **Running queue**, **Review grid**, and **per-photo accept/override**
are the net-new backend surfaces to add (a job/queue model + a results store); everything else is
a thin binding over today's `service.py`.
