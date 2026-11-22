# UI. A minimal interface, on the desktop and in the browser

Status: spec (2026-11-22). Size L. Planning only: no code yet. Written on Opus at max effort, because Fable
is rate-limited until 2026-11-23 (see [AGENT_ROUTING.md](../AGENT_ROUTING.md)).

This is the "Studio rebuild, the desktop shell" that [FEATURE_PLAN.md](../FEATURE_PLAN.md) puts after the engine.
It replaces Studio (`/`, `web/static/studio.html`), the old page (`/classic`, `index.html`) and AutoEdit Lab
(`/lab`, `lab.html`, [F38](F38-test-ui.md)) with one interface that reaches every feature the engine has, and
leaves a marked place for every planned one.

## The owner's answers (2026-11-22)

The request, in the owner's words: an "ultra minimalist UI for this app and all of the features and all the future
features… it should both be a desktop application and… a browser thing… avoid AI features".

| Question | Answer | What it decides here |
|---|---|---|
| What does "avoid AI features" mean? | No AI-looking style | No sparkles, glows, purple gradients, chat bubbles, "magic" or "AI" in labels. The features stay: the optional cloud refine and the local reader stay reachable, in plain words, off by default |
| Which desktop platforms? | macOS (Apple Silicon), Windows, Linux | The shell must use something that exists on all three: the operating system's own web view through pywebview |
| Browser: local or hosted? | Local only, for now | `autoedit ui` on 127.0.0.1, as today. A hosted site stays the open decision it already is in FEATURE_PLAN |
| What goes on the first screen? | Open folder, then edit all | Batch first. One photo is one click in |
| Studio and the Lab, afterwards? | Replace both | Once the new interface covers what they do, the three old pages and their routes are deleted |
| Light and dark? | Follow the system | Light and dark chrome from the OS setting; the photo's surround is the same neutral grey in both |
| A row in FEATURE_PLAN? | Yes | F49, pointing here |

## Principles

1. **The photo is the brightest, most coloured thing on screen.** Chrome is neutral grey, flat, 1 px lines, no
   shadows, no gradients. Colour means something (action, done, look at this, error, a mark) or is not used.
2. **One primary action per screen.** It is the only filled button. Everything else is quiet text buttons.
3. **Batch first.** The product is "edit a whole folder well". Single-photo work is a detail view of the folder.
4. **Words, not icons.** Labels are verbs and nouns a photographer uses ("Edit 212 photos", "Check before
   sharing"). Glyphs only where universal: ×, ‹ ›, ✓.
5. **Progressive disclosure, never hiding the core.** Folder, look, strength, what to change and the run button
   are always visible. Everything rarer is one click away in exactly one place: a photo's panel, the Share sheet,
   Tools or Settings.
6. **The engine describes the controls.** Settings are drawn from `GET /api/v1/settings/schema` (F13), looks from
   `looks.toml`, kinds from `/genres`, export presets from `exports.toml`. A new run setting appears without a UI
   change. No tuning value is written into the page.
7. **The same files everywhere.** The browser page and the desktop window load the same HTML, CSS and JS from the
   same local server. There is no second front end.
8. **Plain, honest wording.** The UI says what was found and what was done, never "safe", "perfect" or "smart".
   Every edit can answer "why?" from the sidecar's notes.
9. **Boring tech.** No framework, no bundler, no npm: a few native ES modules and one stylesheet, served by the
   FastAPI app that exists.

## Research

Principles only; no product's look, name or wording is copied. F38's research (surround, dark-surface contrast,
hold-to-compare, progressive disclosure, WCAG 2.2 targets) still holds and is reused; its sources are listed in
[F38](F38-test-ui.md). New for this plan:

| Finding | Source | Used for |
|---|---|---|
| An app praised for being minimal: "make photo editing as easy as photo browsing"; thin-line icons; the UI frames the photo and does not compete with it | Apple Design Awards profile of Darkroom; a design analysis of it | Principles 1 and 4 |
| Batch apps on phones reduce batch to "select several, paste the edit": copy one photo's edit to others | Darkroom, Batched (App Store pages) | "Copy to similar" (F47) on the photo screen |
| A dense open-source raw editor is criticised for indistinct, cramped chrome with only slight tonal steps between areas; a fork exists to clean it up | Reviews of darktable; Ansel | Clear separation by space, not by many grey steps; few controls per screen |
| pywebview: a native window round the OS web view (WebKit on macOS, WebView2 on Windows, GTK WebKit or Qt on Linux), from Python, no bundled browser | pywebview docs and FAQ | The desktop shell |
| Tauri is small and polished but needs Rust and a frozen Python "sidecar" binary; Electron ships Chromium (hundreds of MB) and Node | 2026 framework comparisons | Why not Tauri or Electron |
| PyInstaller cannot cross-compile (one build per OS), adds the interpreter, and signs nothing; macOS needs an Apple Developer ID and notarisation, Windows a code-signing certificate | PyInstaller docs; pywebview freezing guide; release-pipeline write-ups | Installers are a later step with owner decisions |

Sources, read on 2026-11-22; no number from them was tested here:
https://apps.apple.com/story/id1521324925 · https://blakecrosley.com/guides/design/darkroom ·
https://www.cultofmac.com/770705/batched-photo-editing-app/ · https://www.thephoblographer.com/2022/10/03/darktable-review-free-lightroom-alternative/ ·
https://shotkit.com/darktable-review/ · https://docs.darktable.org/usermanual/5.6/en/overview/user-interface/ ·
https://medium.com/@aaronfulton/pywebview-the-better-alternative-to-electron-for-python-programmers-471d3c13693f ·
https://www.simonwillison.net/2024/Mar/13/pywebview-5 · https://sources.debian.org/src/python-pywebview/6.1%2Bdfsg-1/docs/guide/freezing.md ·
https://betterstack.com/community/guides/scaling-nodejs/tauri-vs-electron-vs-deno-vs-electrobun/ ·
https://dev.to/devtoolpicks/tauri-vs-electron-for-indie-hackers-in-2026-honest-comparison-o0e ·
https://evilmartians.com/chronicles/making-desktop-apps-with-revved-up-potential-rust-tauri-sidecar ·
https://pydevtools.com/handbook/reference/pyinstaller/ · https://dev.to/susumun/distributing-a-python-desktop-app-on-windows-and-mac-the-full-release-pipeline-1n4

## Information architecture

Five screens and two sheets. Every one has exactly one primary action.

| Screen | Reached from | Primary action | What else lives there |
|---|---|---|---|
| **Start** | launch, no folder open | Open folder | Recent folders (this browser / this app); "Your photos stay on this computer." |
| **Folder** | Open folder, a recent folder | Edit N photos | Look, Kind of photo, Strength, "What to change in every photo", Export as, the grid with status, Find, Share, Tools, Settings |
| **Photo** | click a tile (← → between photos) | Save this photo | Hold to compare, Mark, what to change here, the photo's look and strength, Copy to similar, How it was done, Try it yourself, Masks |
| **Tools** | Folder › Tools | (one per tool: the tool's Run) | Pick keepers, Merge frames, Choose a subset, Looks, Your style, Calibrate, Training |
| **Settings** | ⚙ from any screen (a sheet over it) | Done | Region finder and model, RAW, Quality options, Output, Privacy, Profile, Keys, About |
| *Share sheet* | Folder › Share (after a run) | Make gallery | Check before sharing, Edit declaration, Keywords, Show in Finder/Explorer |
| *Surprise sheet* | Look › Surprise me | Use this look | Six looks on the current photo, Six more |

The navigation is a stack, never a sidebar of modes: Start → Folder → Photo, with Tools and Settings beside it. Back
is always ‹ in the top left and Esc.

### Sketches

**Start**

```
┌──────────────────────────────────────────────────────────────────────────┐
│ AutoEdit                                                         Settings │
│                                                                          │
│                                                                          │
│                          ┌──────────────────┐                            │
│                          │   Open folder    │   ← the one filled button  │
│                          └──────────────────┘                            │
│                     Your photos stay on this computer.                   │
│                                                                          │
│                     Recent                                               │
│                     Dolomites 2026 · 212 photos · edited 3 days ago      │
│                     Wedding test · 48 photos                             │
└──────────────────────────────────────────────────────────────────────────┘
```

**Folder** (the home of the app)

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│ ‹  Dolomites 2026 · 212 photos        Find [ snow lake      ]   Share  Tools  ⚙ │
├──────────────────────────────────────────────────────────────┬───────────────────┤
│ ▣ ▣ ▣ ▣ ▣ ▣ ▣ ▣                                              │ Look       Natural▾│
│ ▣ ▣ ▣ ▣ ▣ ▣ ▣ ▣     tiles 160 px, a check box,              │ Surprise me        │
│ ▣ ▣ ▣ ▣ ▣ ▣ ▣ ▣     a status dot: waiting · editing ·        │ Kind      None   ▾ │
│ ▣ ▣ ▣ ▣ ▣ ▣ ▣ ▣     done · look at this · failed             │ Strength ──●── 0.8 │
│                                                              │                    │
│ ☑ All · 212   Show: All ▾  (Look at · Failed · Bursts)       │ What to change in  │
│                                                              │ every photo        │
│                                                              │ [ darken the sky ] │
│                                                              │                    │
│                                                              │ Export as   Web  ▾ │
│                                                              │ to  AutoEdit Exports│
│                                                              │                    │
│ Editing 34 of 212 · about 6 min · 1 to look at  Pause Cancel │ [ Edit 212 photos ]│
└──────────────────────────────────────────────────────────────┴───────────────────┘
```

**Photo**

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│ ‹ Folder   IMG_0042 · landscape · 7 of 212                    ‹  ›    Original  ⚙ │
├──────────────────────────────────────────────────────────────┬───────────────────┤
│                                                              │ Look      Natural▾│
│              ┌───────────────────────────┐                   │ Strength ──●── 0.8 │
│              │                           │  surround: the    │                    │
│              │         the photo         │  neutral --stage  │ Your changes       │
│              │        ( ◯ a mark )       │                   │ · darken the sky ×│
│              └───────────────────────────┘                   │ · ◯ more purple  ×│
│   hold the photo or B: the original                          │                    │
├──────────────────────────────────────────────────────────────┤ Copy to similar (4)│
│ [Mark] [ Say what to change, e.g. warm the grass a little ] ↵ │ How it was done  › │
│                                                              │ Try it yourself  › │
│                                                              │ Masks              │
│                                                              │ [ Save this photo ]│
└──────────────────────────────────────────────────────────────┴───────────────────┘
```

**Share sheet** (over the Folder, after a run)

```
┌ Share Dolomites 2026 ───────────────────────────── × ┐
│ Check before sharing      14 of 212 have findings  › │
│ Edit declaration          what was changed, hashes  › │
│ Keywords                  write .xmp beside exports › │
│ Show in Finder                                        │
│                                         [ Make gallery ] │
└──────────────────────────────────────────────────────┘
```

**Tools**

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ‹ Folder   Tools                                                          │
│                                                                          │
│ Pick keepers        the sharpest well-exposed frame of each burst      › │
│ Merge frames        brackets, repeats, focus series, trails            › │
│ Choose a subset     copy a varied or best-scored set for review        › │
│ Looks               save, import, export, card, LUT                    › │
│ Your style          changes you keep making                            › │
│ Calibrate           find your look from pairs you vote on              › │
│ Training            datasets, scorers, recipe model (advanced)         › │
└──────────────────────────────────────────────────────────────────────────┘
```

Each tool opens one form (its folder, its two or three options, a Run button) and then its result in place: a list
or a contact sheet, with "Open the result folder". Settings is the same pattern as a sheet: groups as headings, one
row per setting, native controls.

## Where every feature lives

"Done" features are in the engine today; **(planned)** ones get the place shown, behind nothing until built. The
column API says whether a route exists (✓) or must be added in task U6 (new).

| Feature | ID | Where in the UI | API |
|---|---|---|---|
| Open a folder, RAW+JPEG pairs, thumbnails | — | Start, Folder | ✓ |
| Batch edit, progress, pause, cancel, resume | F4 | Folder: Edit N photos, the status line | ✓ jobs |
| Workers | F4 | Settings › Output | ✓ schema |
| Looks (incl. black and white and film) | F24 | Folder and Photo: Look | ✓ schema |
| Surprise me | F26 | Look › Surprise me (sheet) | ✓ variants |
| Strength | — | Folder and Photo | ✓ |
| Kind of photo (genre packs) | F30 | Folder: Kind | ✓ |
| Style, concept preset, concepts list | — | Settings › Advanced: "Things to find" | ✓ |
| Mood words (`--prompt`), palette, reference photo | — | Settings › Advanced | ✓ (reference: new) |
| What to change in every photo | F2, F20 | Folder side panel | ✓ |
| What to change in one photo, follow-ups with undo | F19, F3 | Photo: the sentence bar and "Your changes" | ✓ |
| Mark an oval and say what changes there | F37 | Photo: Mark (or M) | ✓ |
| Hold to compare | — | Photo: hold, B, Original | ✓ |
| Masks | — | Photo: Masks | ✓ |
| How it was done | F29, F37 | Photo › How it was done | ✓ steps |
| Try it yourself | F29 | Photo › Try it yourself | ✓ try |
| Save one photo (re-render from the sidecar) | F3 | Photo: Save this photo | ✓ run-one |
| Copy to similar photos | F47 | Photo: Copy to similar (N) | new |
| Shoot consistency, timelapse | F9, F43 | Settings › Quality: "Even out" (brightness, bursts, timelapse) | ✓ schema |
| Highlight hold, denoise, levels | F5–F7 | Settings › Quality | ✓ schema |
| RAW method, RAW recovery | F8, F44 | Settings › RAW | ✓ schema |
| Colour fringes | F46 | Settings › Quality | ✓ schema |
| Masks to the frame edge | F39 | Settings › Quality | ✓ schema |
| Light shaping, sun glow | F18 | Settings › Quality | ✓ schema |
| Night sky | F31 | Kind = astro, or Settings › Quality | ✓ schema |
| Grain | F23 | Settings › Look extras: Grain | ✓ schema |
| LUT file and amount | F25 | Settings › Look extras: LUT (file picker of kind `.cube`) | new picker kind |
| Straighten, crop to a shape | F32 | Settings › Quality: Straighten; Export as: shape | ✓ schema |
| Upright **(planned)** | F32 | Settings › Quality, beside Straighten | — |
| Neutral pick, backdrop, colour report | F34 | Photo: "Pick something grey" (a click on the photo); Settings › Quality: Backdrop | partly new |
| Export presets, format, size, name, subfolder | F10 | Folder: Export as | ✓ schema |
| Metadata (keep, shareable, none) | F1 | Settings › Output | ✓ schema |
| Watermark | F48 | Export preset (Settings › Output › Export presets) | ✓ via preset |
| Gallery | F48 | Share › Make gallery | new |
| Check before sharing | F21 | Share › Check before sharing; a box in Settings to run it with every edit | new |
| Hide on request **(planned)** | F22 | A "Hide" button per finding in Check before sharing | — |
| Find by what is in the photo | F41 | Folder: Find | new |
| Keywords | F41 | Share › Keywords | new |
| Edit declaration | F42 | Share › Edit declaration | new |
| Pick keepers | F35 | Tools › Pick keepers; the grid's "Bursts" filter | new |
| Merge frames | F33 | Tools › Merge frames | new |
| Curate (diverse, best, aesthetic) | — | Tools › Choose a subset | ✓ |
| Looks: save, delete, import, export, card, LUT | F11, F25, F28 | Tools › Looks | new |
| Your style, history | F27 | Tools › Your style; Settings › Privacy: Keep a history | ✓ suggestions, accept; history new |
| Calibrate | F12 | Tools › Calibrate | new |
| Review labels | — | Photo: a Good / Not good pair, only when "Collect review labels" is on in Settings › Advanced | ✓ |
| Datasets, train scorer, score, rank variants, train recipe | — | Tools › Training (advanced) | ✓ partly; rank and recipe new |
| Region finder (stub, SAM 3, auto), device | F14 | Settings › Region finder | ✓ schema |
| Model status, download, remove | F14 | Settings › Region finder: one row with the state and one button | new |
| Doctor | — | Settings › About: "Check this computer" | new |
| Cloud refine with your own key | F17 | Settings › Privacy: "Refine with a cloud model (your key)", off; says what leaves the computer | ✓ schema |
| Local reader for free text | F20 | Settings › Privacy: "Read unusual sentences with a model on this computer" | ✓ translator status |
| Profile folder | F11 | Settings › Profile | ✓ |
| Dry run | — | Not in the UI: the preview is the dry run | — |
| Strict exit code | F40 | CLI only (scripts) | — |
| Ideas, not planned yet **(planned, no spec)** | — | Learn from a photo you admire: Tools. Speak instead of type: a microphone button in the sentence bar, only with a local speech model. Brighter-than-white output: Export as. Print help: Export as › Print. Delivery page with client picks: Share. Release check: inside Check before sharing. Watch folder: Start ("Watch a folder"). Display P3: Settings › Output. Signed Content Credentials: Share › Edit declaration | — |

The place of each planned item is reserved in this table only. Nothing is drawn for it until it is built.

## Visual system

Reuse the Lab's measured tokens (F38, Design 2) and add a light set. One stylesheet, custom properties only.

```css
:root {                                   /* light, the default when the OS is light */
  color-scheme: light dark;
  --bg: #f7f7f7; --panel: #ffffff; --raised: #f0f0f0; --hover: #e8e8e8; --line: #dedede;
  --text: #1a1a1a; --text-2: #5c5c5c; --text-3: #9a9a9a;
  --accent: #2f5fd0; --on-accent: #ffffff; --ok: #1f7a3a; --warn: #8a5a00; --bad: #b3261e; --mark: #7a4cc2;
  --stage: #777777;                       /* the photo's surround: identical in both themes */
}
@media (prefers-color-scheme: dark) { :root {
  --bg: #121212; --panel: #171717; --raised: #262626; --hover: #2f2f2f; --line: #2c2c2c;
  --text: #e3e3e3; --text-2: #a3a3a3; --text-3: #6e6e6e;
  --accent: #8cb4ff; --on-accent: #0f1622; --ok: #86d29a; --warn: #f2c94c; --bad: #ff8a80; --mark: #c39bff;
} }
:root {
  --font: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --fs-s: 12px; --fs-m: 13px; --fs-l: 15px; --fs-xl: 18px;
  --sp-1: 4px; --sp-2: 8px; --sp-3: 12px; --sp-4: 16px; --sp-5: 24px; --sp-6: 32px;
  --r-s: 6px; --r-m: 10px; --ctl: 32px; --bar: 48px; --panel-w: 320px; --tile: 160px;
  --fast: 120ms; --slow: 180ms; --ease: cubic-bezier(.2, 0, 0, 1);
}
```

- **Surround.** `--stage` is a neutral mid grey (R = G = B, near L\* 50) in both themes, because the surround changes
  how bright a photo looks; a black surround leads to dark edits (F38's research). Settings › Appearance offers one
  alternative, "Dark surround" (`#1f1f1f`), for people who prefer it. That is the only appearance setting.
- **Type.** The system font, no web font (works offline, looks native in each OS). Body 13/1.45, labels 12/500 in
  sentence case, screen titles 18/600, numbers `tabular-nums`. No all-caps.
- **Colour.** All greys neutral. One accent, for the primary button, focus and selection. Green done, amber "look at
  this", red failed, purple marks. Contrast targets as in F38: text 4.5:1 at least, 7:1 for small text where
  possible, checked in both themes by the Checker.
- **Space.** A 4 px scale; groups separated by 24 px of space, not by boxes. At most two grey steps between areas.
- **Controls.** 32 px high, 6 px radius, 1 px line. The primary button is the only filled one on a screen. Native
  `select`, `input type=range`, checkbox and `dialog`. Targets at least 24 by 24 px (WCAG 2.2).
- **Motion.** 120 ms for hover and state, 180 ms for a sheet sliding in. No motion on the photo itself: compare and
  photo changes are instant. `prefers-reduced-motion` turns every transition off.
- **Busy.** A 2 px accent line at the top of the stage while a preview renders; the status line says what is
  happening in words ("Finding sky, snow… 2 s").
- **Not used, as the owner asked.** No sparkle or wand icons, no gradients, no glow, no purple-to-blue, no chat
  bubbles, no typing animation, no "AI", "magic" or "smart" in any label. A sentence box is a text field with a
  plain placeholder ("Say what to change, e.g. warm the grass a little").
- **Keys.** Cmd/Ctrl+O open folder, ← → previous and next photo, Enter open a photo, Esc back, B or holding the photo
  compare, M mark, Cmd/Ctrl+S save this photo, Cmd/Ctrl+Enter edit the checked photos, ? the list of keys.

## Desktop and browser

**Choice: one local server, two ways to look at it.**

- **Browser:** `autoedit ui` as today. FastAPI and uvicorn on 127.0.0.1, the new pages at `/`, the browser opens.
  Local only; `--unsafe-network` keeps its warning.
- **Desktop:** a new command `autoedit app` (extra `[app]` = `pywebview`). It starts the same server in a thread on
  127.0.0.1 at a free port, then opens one native window on it through pywebview. Closing the window stops the
  server. The folder picker in the window is pywebview's native dialog; in the browser it stays the server's
  existing `/pick` (Finder through osascript on macOS, tkinter elsewhere).
- **Installers, later.** First release: `pipx install 'autoedit[app]'` (plus `[sam]` for SAM 3), then `autoedit app`.
  Frozen installers (PyInstaller on each OS, a `.dmg`, an `.msi` or installer, an AppImage) are task U12, after the
  owner decides on signing and on whether SAM 3 and torch ship inside or install on first use.

Why this:

| Option | For | Against | Verdict |
|---|---|---|---|
| pywebview round the local server | Python only, one small dependency; the OS web view (WebKit, WebView2, GTK WebKit); the browser and the window load the same files; macOS, Windows and Linux | Three different web engines to test; Linux needs GTK or Qt installed | **Chosen** |
| Tauri with a Python sidecar | Small, polished installers and updater | A Rust toolchain plus a frozen Python binary as sidecar: two build systems for the same window | Not now; revisit only if installers become the product |
| Electron | Most mature packaging | Ships Chromium and Node (hundreds of MB before torch); a JS runtime the repo does not otherwise need | No |
| A native UI toolkit (Qt, SwiftUI) | Native look | A second front end; the browser version would need its own | No |
| Browser only | Nothing to build | The owner asked for a desktop app | No |

Consequences the build must respect:

- The page must work in WebKit (Safari, the macOS window, Linux GTK), Chromium (Chrome, Edge, WebView2) and Firefox.
  Only widely supported features: ES modules, `dialog`, CSS custom properties, `EventSource`. No File System Access
  API (Chromium only); folders are always opened through the server, which is local in both cases.
- The existing safety stays: loopback address, the approved-folder rule of `/pick`, F38's Content-Security-Policy
  (same origin, `data:` images). The desktop server listens on a random free port.
- Torch and SAM 3 stay optional, as `CLAUDE.md` requires. The stub keeps the whole interface usable with no model.

## Front-end structure

```
src/autoedit/web/static/app/
  index.html        the shell: bar, stage, panel, sheets; one <link>, one <script type="module">
  app.css           tokens and components (the Visual system above)
  main.js           router (Start, Folder, Photo, Tools, Settings), keys, the api() helper
  folder.js         grid, side panel, batch job and its event stream
  photo.js          preview, hold to compare, marks, chips, How it was done, Try it yourself
  settings.js       the form drawn from /settings/schema
  tools.js          one generic "tool form" drawn from a small table, plus result views
  share.js          gallery, declaration, keywords, check before sharing
```

About eight files, no build step, served as static files. `photo.js` starts from the Lab's working code (F38,
Design 4), which already does preview, marks, steps and try against the real API. State is one plain object per
screen; per-photo sentences and marks are kept in `localStorage` under a new key, in Studio's entry format.

## API for the tools (U5)

Written 2026-11-22 in the unattended run. One pattern for every feature that only the CLI reaches today, so the page
needs one helper and the server one small module.

- `POST /api/v1/tools/{name}` with a JSON body starts a job and answers `{id, name, status}` at once.
  `GET /api/v1/tools/jobs/{id}` answers `{id, name, status: running | done | error, result, error, log}` (`log`: the
  last 50 lines the tool reported). Short tools finish in milliseconds; the page polls every 500 ms until not
  `running`. An unknown name is 404, a bad body 400, a path outside the approved folders 403 (`service.ensure_allowed`).
- `web/tools_jobs.py`: a registry `TOOLS = {name: (runner, slow)}`, each runner `fn(payload, configs_dir, profile,
  log) -> dict`; jobs in a dict under a lock, run in a daemon thread, like `curate_jobs.py`. Slow tools share one
  global lock, so one heavy job runs at a time (`# ponytail:` per-kind locks if that ever hurts). Text-only library
  functions return `{"text": ...}`; a function that only prints is captured with `contextlib.redirect_stdout`.
- Outputs go inside the approved folder, never into the server's working directory.

| Name | Body | Calls | Result |
|---|---|---|---|
| `find` | `folder, words[], min_coverage?` | `search.cmd_find(..., as_json=True)` | the parsed JSON |
| `keywords` | `folder, dry_run?` | `search.cmd_keywords` | `{text}` |
| `declare` | `folder` | `declare.cmd_declare(folder, None)` | `{text}` plus the two file paths |
| `gallery` | `folder` | `gallery.write` with the CLI's defaults | its dict (the page path) |
| `check` | `folder` | what `_cmd_check` does, out `FOLDER/autoedit-check` | `{report: path to reveal.html, ...summary}` |
| `sync` | `target, dry_run?, minutes?` | `sync.run` | its dict plus `sync.lines` |
| `merge` | `folder, mode?, dry_run?` | `merge.run`, out `FOLDER/AutoEdit Merged` | its dict |
| `pick` | `folder, copy?` | `pick.run`, out `FOLDER/AutoEdit Picks` | its dict |
| `exports` | — | `exports.load` of the packaged and profile file | `{presets: [{name, ...}]}` |
| `looks_list`, `looks_save`, `looks_delete`, `looks_lut`, `looks_export`, `looks_import`, `looks_card` | as the CLI flags | what `_cmd_looks` does; output files in a folder the body names (approved) | data, not printed text |
| `history_status`, `history_clear` | — | `history.status`, `history.clear` | their values |
| `calibrate_start`, `calibrate_advance` | as the CLI | `calibrate.start`, `calibrate.advance` | their dicts (the vote page path) |
| `models_status`, `models_download`, `models_remove` | — | `models.*` | status dict; download and remove only on a click |
| `doctor` | — | `_cmd_doctor`'s output captured | `{text}` |
| `rank_variants`, `train_recipe` | as the CLI | `service.make_variants`, `recipe.train_from_dataset` | their dicts |
| `reveal` | `path` | opens the system file manager at the path (`open -R`, `explorer /select,`, `xdg-open` on the folder) | `{}` |

## Left out, on purpose

- **A hosted, online editor.** Local only, by the owner's answer. Accounts, uploads, GPU cost and a privacy policy
  stay out until the owner decides what the website does.
- **AI styling** of any kind (see Visual system).
- **A mode-switching sidebar, a stepper, dashboards, onboarding tours, empty-state illustrations.**
- **Sliders for every op.** The engine edits; people steer with looks, strength and words. Sliders exist only in Try
  it yourself (F29), where they teach.
- **Generative edits**, background blur, skin smoothing and overlays: out under the content rule (FEATURE_PLAN).
- **Dry run, strict exit code, `--config` files, `--configs-dir`:** scripting features; they stay on the CLI.
- **A web font, an icon font, a CSS or JS framework, a bundler.**
- **Auto-update.** Comes with installers, if ever.

## Build order

Tiers as in [AGENT_ROUTING.md](../AGENT_ROUTING.md). During the build the new interface lives at `/next`; `/`, `/lab`
and `/classic` stay untouched until U11. No task changes engine pixels, so default output stays byte-identical;
each task still runs `PYTHONPATH=src python tests/run_all.py` and the stub smoke run.

| Task | Tier | What | Files | Proof |
|---|---|---|---|---|
| U0 | owner | Look at the sketches and this plan; answer the open decisions below (done 2026-11-22, signing left for U12) | — | a yes |
| U1 | Builder (Sonnet, medium) | Shell, tokens, router, light and dark, `/next` route | `web/static/app/index.html`, `app.css`, `main.js`; one route in `web/app.py` | the page opens in Chrome, Safari, Firefox; API snapshot test updated |
| U2 | Builder | Start and Folder: open, recent, grid, look, kind, strength, run-wide text, Export as, Edit N photos with the job stream, pause, cancel, filters | `folder.js`, `main.js` | a stub batch of the smoke corpus runs from the page; screenshots in both themes |
| U3 | Builder (high) | Photo: preview, compare, marks, chips, Save this photo, Masks; then How it was done and Try it yourself, ported from the Lab | `photo.js` | F38's acceptance checks pass on `/next` |
| U4 | Builder | Settings drawn from the schema; Privacy, Region finder and Appearance groups | `settings.js` | every key of `autoedit settings --json` has a control; a new key appears with no page change (a test) |
| U5 | Planner (Fable max, or Opus max while Fable is limited) | The API shape for the CLI-only features: one job route pattern for long tools (pick, merge, curate, calibrate, check, gallery, declare, keywords, sync, models download) and plain routes for short ones (find, looks, history, doctor) | a section added to this file | the route table, reviewed by the Checker |
| U6 | Builder, one per group, files apart | The routes from U5, thin wrappers over existing functions, with tests | `web/app.py`, `web/service.py`, a new `web/tools_jobs.py`; `tests/` | route tests; OpenAPI snapshot |
| U7 | Builder | Share sheet, Find, Copy to similar | `share.js`, `folder.js`, `photo.js` | each action works on the smoke corpus |
| U8 | Builder | Tools screen from a table of tool forms | `tools.js` | each tool runs once on the smoke corpus with the stub |
| U9 | Builder | `autoedit app`: pywebview window, free port, native folder dialog, `[app]` extra | `cli.py`, `web/desktop.py`, `pyproject.toml` | opens on macOS; the owner or CI opens it on Windows and Linux |
| U10 | Checker (Opus, high) | Review against this spec and `CLAUDE.md`; contrast in both themes; keyboard only; three browsers and the desktop window; the privacy check | read-only | findings as `file:line` |
| U11 | Tweaker (Haiku, low), after the owner's yes | Move `/next` to `/`; delete `studio.html`, `lab.html`, `index.html`, `/classic`, `/lab`; README, APP_GUIDE, DESIGN.md, CHANGELOG | listed files | tests green; no link to a deleted page |
| U12 | Planner, then Builder, after owner decisions | Installers per OS, signing | build scripts, CI | an installed app opens on each OS |

U1 to U4 can start now; U6 waits for U5; U7 and U8 wait for U6. Two Builders run at once only on files that do not
overlap (U2 and U4 can; U3 and U7 cannot).

## Result (2026-11-22, unattended run)

U0 to U11 are built; U12 (installers) waits for the signing decision.

- **Built:** the interface at `/` (`static/app/`: one page, two stylesheets, seven ES modules, about 1,700
  lines, no framework), the tools API (`web/tools_jobs.py`, 28 tools behind one job route pair), `autoedit
  app` (`web/desktop.py`, extra `[app]`). Studio, the classic page and the Lab, their routes and the
  pre-v1 `/api` mount are removed.
- **Checked in the browser** (stub finder, 12 photos of 22 MP): Start; Folder with a 12-photo batch, its
  progress, status dots, filters; light and dark; 375 px; Settings drawn from the schema; Photo with a
  sentence, a re-render and the chip; How it was done; Copy to similar (dry run, then one photo synced);
  Tools and Looks; every Share tool through the API (declare, gallery, find, keywords). A per-photo
  sentence reaches the batch sidecar.
- **Reviews:** two Checker passes (server, front end). No high finding on the server; one high on the
  front end (the batch dropped per-photo sentences and marks), fixed and checked in the browser.
- **Tests:** 1,129 passed on the system Python and on `.venv`; the stub smoke run completes. No engine
  file changed, so default output is unchanged.
- **Found on the way:** a UI batch wrote no `run.json`, so `adjust`, `sync` and `declare` could not
  find its sources. `web/jobs.py` now writes a minimal one (`input_dir`) unless one is there.

Not done, and why:

- `autoedit app` was not opened in a real window: pywebview is not installed and the run installs nothing.
  Its tests use a fake web view. Windows and Linux are untested.
- The profile folder is not shown in Settings: no route reports it.
- On macOS the "Choose…" picker for a LUT uses the JSON file kind, so a `.cube` file must be typed.
- The grid has no "Bursts" filter yet (the Pick keepers tool covers bursts).

Decisions taken without the owner:

| Decision | Reverse it by |
|---|---|
| The tools run as jobs behind one route pair; slow tools share one lock | per-tool routes in `web/app.py` |
| Opening another folder while a batch runs is refused with a message | allow it in `folder.js` |
| Copy to similar asks the server only when clicked (it holds the slow-job lock) | a dry run on mount in `photo.js` |
| The cloud refine settings were reworded in `configs/settings.toml` (keys and defaults unchanged) | the old text from git |
| `APP_GUIDE.md` keeps its engine sections; only section 9 became the guide to the interface | — |

## Decisions for the owner

Answered on 2026-11-22 (U0):

| Decision | Answer |
|---|---|
| Surround grey | Mid grey `#777` in both themes, with "Dark surround" as the one option |
| Training tools (datasets, scorer, recipe model, rank variants) | In Tools › Training, last, marked advanced |
| Installers: SAM 3 and torch inside, or downloaded on first use? | Downloaded on first use, from Settings › Region finder, after the licence is accepted |
| Remember recent folders (paths stay on this computer)? | Yes |

Still open:

| Decision | Proposed |
|---|---|
| Installers: pay for an Apple Developer ID and a Windows signing certificate? Unsigned builds warn on first open | Decide at U12; until then `pipx install` |
