# F38. Test UI: AutoEdit Lab

Status: spec (2026-11-17). Size M. Written in the unattended run ([UNATTENDED_RUN.md](../UNATTENDED_RUN.md)): nothing
was asked; the owner's decisions are in the table below. A page over routes that exist: no engine file changes, so
default output stays byte-identical. It shows what [F19](F19-follow-up-edits.md) to [F37](F37-point-and-tell.md)
built; [F32](F32-geometry.md), being built, is only a later control here.

## Goal

The owner, for after the overnight run: "create a test UI, this should be a clean minimalistic UI, use online
examples of what a good UI is". A second page beside Studio, `/lab`, one self-contained file: open a folder, choose a
photo, say what to change (sentences, and ovals drawn on the photo), choose a look or let the engine surprise you, the
kind of photo and the strength, hold to see the original, see how the edit was done step by step, try it yourself and
compare, export one photo or the checked ones. Calm: a dark neutral canvas where the photo is the brightest thing, one
accent colour, text labels, the rest one click away. Studio, `/classic` and the API stay as they are.

## Research

Principles only. No product's look, name, icon or wording is copied.

| Principle | Source | In the page |
|---|---|---|
| The photo is the brightest thing; chrome recedes; one window, no ornament | Darkroom guide (session notes); Pixelmator Pro reviews | Flat surfaces, 1 px borders, no shadow, no gradient, no icon beyond × › ← |
| The surround changes how bright a photo looks (dark makes it look brighter); keep it neutral, R = G = B; a black "lights out" surround leads to dark edits | Kasson; Terra Galleria; darktable colour assessment | Stage `#1f1f1f`, neutral grey, never black |
| Dark surfaces near `#121212`; desaturated accents (saturated ones vibrate on dark); text at least 4.5:1, 7:1 for small text where possible | Material dark theme; Apple HIG, Dark Mode | The tokens of Design 2, with their measured ratios |
| Compare by holding, not by a fixed split | Apple Photos (hold M, or click and hold a button); Lightroom (`\` swaps in place, `Y` side by side); Photomator (a comparison slider) | Hold on the photo, hold B, or the Original button; Studio keeps its split |
| Progressive disclosure: rarer features on a secondary screen | NN/g | Edit is home; How it was done and Try it yourself are secondary views; settings in a dialog |
| Minimal must not hide the core or shrink text | Google Photos editor complaints, Lightroom for the web review (session notes); Tom's Guide on Pixelmator Pro | Prompt, look, strength and export never fold away; body text 13 px |
| Colour only for function | Session notes; Material | Accent: action and focus. Green done, amber held back or "worth a look", red error, purple marks |
| Targets at least 24 by 24 CSS px; a focused control is never hidden | WCAG 2.2 (2.5.8, 2.4.11) | Controls 32 px high; modal dialogs; a focus ring everywhere |

Sources (the first four from the session's notes): https://blakecrosley.com/guides/design/darkroom ·
https://thephoblographer.com/2025/05/26/lightroom-for-the-web-review-who-actually-uses-this-thing/ · https://www.androidcentral.com/apps-software/users-dreading-google-photos-less-functional-editor-updates-report ·
https://tubikstudio.com/blog/?p=4007 · https://support.apple.com/en-za/guide/photos/pht304c2ace6 · https://lightroomkillertips.com/lightroom-tip-quick-and-easy-beforeafter-view/ ·
https://support.pixelmator.com/photomator-user-guide/get-started/interface-overview · https://www.tomsguide.com/us/pixelmator-pro,review-5077.html ·
https://www.macstories.net/reviews/pixelmator-pro-first-impressions-a-beautiful-modern-interface-with-advanced-image-editing-tools/ · https://m2.material.io/design/color/dark-theme ·
https://developer.apple.com/design/human-interface-guidelines/dark-mode · https://www.nngroup.com/videos/progressive-disclosure/ · https://www.w3.org/WAI/standards-guidelines/wcag/new-in-22/ ·
https://blog.kasson.com/the-last-word/in-image-editing-the-surround-matters · https://www.terragalleria.com/blog/quick-processing-tip-white-application-background/ ·
https://docs.darktable.org/usermanual/development/en/module-reference/utility-modules/darkroom/color-assessment

## What the API offers

O = the options `{backend, look?, genre?, intensity?}` (Design 4). B = `{file, ...O, photo_edit?, photo_marks?}`:
`photo_edit` is the photo's sentences joined by ". " (at most 2000 characters), `photo_marks` its marks `[{box: [x0,
y0, x1, y1] as shares of the frame, kind: "auto" | "area", edit}]` (at most 20; each side at least 0.01; `edit` 1 to
300 characters). Nothing else is ever sent: never `style`, `concepts`, `decider`, `allow_cloud`, `api_key`, `lut`,
`reference`.

| Feature | Request | Answer fields the page reads |
|---|---|---|
| Open a folder | `POST /api/v1/pick?kind=folder`, no body | `path` (`null` on cancel); the server approves the folder for later requests |
| The photos | `GET /api/v1/groups?dir=FOLDER` | `[{stem, raw, pixel}]`: one entry per photo, a RAW with its JPEG |
| Thumbnails | `GET /api/v1/thumb?px=N&path=P` (N 32 to 2048) | a JPEG |
| The controls | `GET /api/v1/settings/schema` | `settings[]` by `key`: `look.choices`; `intensity.min`, `.max`, `.default`; `backend.choices`, `.help` |
| Kinds of photo (F30) | `GET /api/v1/genres` | `[{name, description, offered}]`; only `offered` ones are listed |
| Version | `GET /api/v1/version` | `engine`, `api` |
| The edit (F19, F20, F23, F24, F30, F37) | `POST /api/v1/preview` with B | `before`, `after` (lossless WebP data URLs, long edge at most 2560); `result.scene`, `.needs_review`, `.review_reason`, `.adjustments[{target, source, status, via}]`, `.audit.run_config.{backend, look, genre, highlight_hold}`; `instruction_problems[]` (`not understood (WHY): "CLAUSE"`); `marks[{kind, word, targets, found, why, status, problems}]`, one per mark in the order sent; `mark_overlays[{target, alpha}]` (PNG data URLs) |
| How it was done (F37, F29) | B + `"steps": true` | `steps[{kind, title, tools[{op, value, name, does, when, watch}], does, when, watch, where[{target, coverage, tools}], typed[], notes[], moved, after}]` (`after`: a JPEG data URL, 1280 px); `walkthrough[{target, sources, label, status, mask{kind, line, box, name, does}, parts[{label, share}], tools[{op, name, asked, applied[{part, value, state}]}], held[]}]`; `mask_overlays[{name, alpha}]`; `mark_overlays` |
| Try it yourself (F29) | B + `"try": {"sliders": {OP: number}, "compare": bool}` | `before`, `after` (the try); `try.sliders[{op, name, does, when, watch, min, max, step, value}]`; with `compare`: `compare.engine` (a picture), `compare.tools[{where, op, name, set, yours, engine}]`, `compare.checks[{key, where, flag, line, name}]`, `compare.said.yours` and `.engine` `{quality, needs_review, warnings}` |
| Surprise me (F26) | `POST /api/v1/variants` `{file, surprise: true, ...O}` | `seed`, `looks` (names in the seed's order), `variants[{name, after, needs_review}]` (`after`: JPEG, 1100 px) |
| Export one photo | `POST /api/v1/run-one` with B | `image_out` (in `AutoEdit Exports` beside the photo), `sidecar` |
| Edit the checked photos | `POST /api/v1/jobs` `{input_dir, files, ...O, photo_edits?, photo_marks?}` (maps from a path to its text or marks) | `{id, status, total, processed, done, review, failed, eta_s, out_dir, items[{src, status, error}]}`; item status: waiting, editing, finished, review, failed; job: running, paused, done, cancelled, error, failed |
| Batch progress | `new EventSource('/api/v1/jobs/' + ID + '/events')` (events `snapshot`, `done`); `POST /api/v1/jobs/ID/control` `{action: "pause" \| "resume" \| "cancel"}` | the same snapshot |
| Your style (F27) | `GET /api/v1/style/suggestions` | `on`, `photos`, `days`, `suggestions[{ask, fact}]` |
| Any failure | every route | `{error, code}`, status 400, 403, 404, 422 or 500 |

Facts from the code the design rests on:

1. `service.make_cfg` reads no `defaults.toml`; a request key that is not `None` wins over a genre, and a named `style`
   keeps the genre's look out (F30 fact 7). Studio sends `style`, `concepts` and `intensity` with every request.
2. `make_cfg` resolves `backend: "auto"` (SAM 3 when `sam3_ready()`, else the stub; `RunConfig` defaults to `stub`).
3. Every path must lie under a folder approved by `/pick`, unless the server runs with `AUTOEDIT_ALLOW_ANY_PATH=1` (the
   launch config `studio-verify` does). The macOS picker offers folders, images or a JSON file: never a `.cube` file.
4. A 22 megapixel preview answers about 10 MB of JSON, 3 to 4 MB more with `steps` (F29); `/thumb` stops at 2048 px.
5. `/variants` reads neither `photo_edit` nor `photo_marks`; `/run-one` and `/jobs` ignore `try` and `steps`.
6. Studio loads a web font from the internet and uses `innerHTML` in 44 lines. The lab does neither.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Where | A second page `/lab`, file `web/static/lab.html`, title "AutoEdit Lab". Studio, `/classic` and the API are unchanged | delete the route and the file |
| What it offers | Goal's list, plus Your style read-only in Settings. Left to Studio: culling, review labels, datasets, training, the five fixed variants, a reference photo, palettes and mood words, a run-wide instruction, the RAW method and the RAW or JPEG switch, export presets and format | a control per item |
| Not offered anywhere yet | LUT (fact 3), geometry (F32 is being built), the reveal check (F21, not wired) | a picker kind for `.cube`; F32's two controls (Known limits) |
| Theme | Dark only (`color-scheme: dark`); no light theme. A fixed neutral surround keeps photos comparable | a `prefers-color-scheme: light` block that redefines the tokens |
| Region finder | The page asks for `auto` (SAM 3 when it is ready, else the stub), as Studio asks for `sam3`; a choice in Settings, remembered in this browser; the status names the finder that answered. The engine's default stays `stub` (an open decision of the owner) | `DEFAULT_FINDER` in `lab.html` |
| What a request carries | Only what the user set, plus `backend` (fact 1): a kind of photo brings its own things to find and its look | (the rule) |
| Preview | Starts on its own when a photo is chosen (after 250 ms) and after every change; one request per view in flight, the latest state wins; no Generate button | a button |
| Before and after | Hold on the photo, hold B, or the Original button (a toggle); no split view | the keys of Design 7 |
| Marks | Drawn only after Mark (or O): a drag never draws without it | (the rule) |
| Storage | `localStorage["autoedit.lab.v1"]`: the finder and each photo's sentences and marks, in Studio's entry format. Not Studio's key: the lab changes nothing in Studio | the key |
| Your style | Read-only; the yes stays on the command line (`autoedit style suggest --yes NAME`) | a button that posts `{name, ids}` to `POST /api/v1/style/accept` |
| How it was done | Its own request with `"steps": true` (JPEGs, 1280 px) when the view opens, never with every preview | (the rule) |
| Look names | The names of `looks.toml`, `_` shown as a space; no label table in the page | a `label` per look in `looks.toml` and the schema |
| Exports | Into `AutoEdit Exports` beside the photos (the server's default); every photo checked when a folder opens; a RAW edited rather than its JPEG (as Studio) | an output picker; start unchecked |
| The route | In the OpenAPI schema like `/` and `/classic`, so the API snapshot gains `GET /lab`; the answer carries a Content-Security-Policy: same-origin requests and `data:` pictures only | `include_in_schema=False`; drop the header |

## Design

### 1. Layout

```
┌ #bar ────────────────────────────────────────────────────────────────────────────────────┐
│ AutoEdit Lab   Dolomites                                       Open folder   Studio   Settings │
├ #stage ─────────────────────────────────────────────────────┬ #panel (320 px) ─────────────┤
│ landscape · regions by stub                     [Original] │ IMG_0042  ·  landscape        │
│                  ┌──────────────────────┐                  │ Look                   [▾]    │
│  #canvas         │      the photo       │                  │ Surprise me                   │
│                  └──────────────────────┘                  │ Kind of photo          [▾]    │
├ #prompt ────────────────────────────────────────────────────┤ Strength  ───●───  0.80      │
│ [Mark] [ Say what to change, e.g. warm the photo   ] [Apply]   │ How it was done             › │
│ (warm the photo a little ×)  (◯ brighten this · the oval ×)   │ Try it yourself             › │
├ #strip ─────────────────────────────────────────────────────┴───── [ Export photo ] ──────┤
│ ☑ All · 12 photos          Editing 3 of 12 · 1 to look at   Pause  Cancel  [Edit 12 photos]   │
│ ▣ ▣ ▣ ▣ ▣ ▣ ▣ ▣ ▣    tiles of 56 px, a check box and a status dot on each                  │
└───────────────────────────────────────────────────────────────────────────────────────────┘
```

- `body` is a grid: `grid-template: "bar bar" var(--h-bar) "main panel" 1fr "strip strip" auto / 1fr var(--w-panel)`,
  height `100dvh`, no page scroll on wide windows; `main` is a flex column of `#stage` (flex 1) and `#prompt`. Below
  1024 px `--w-panel` is 280 px. Below 720 px one column, `"bar" "main" "strip" "panel"`: the page scrolls, the stage
  is `56vh`, the strip scrolls sideways inside itself, 16 px gutters, no sideways page scroll at 375 px.
- Before a folder is open only the bar and `#empty` show. The photo sits centred in `#canvas` at the size of `fit()`.

### 2. Tokens

```css
:root {
  color-scheme: dark;
  --bg: #121212; --panel: #171717; --stage: #1f1f1f; --raised: #262626; --hover: #2f2f2f; --line: #2c2c2c;
  --text: #e3e3e3; --text-2: #a3a3a3; --text-3: #6e6e6e;      /* --text-3 for disabled controls only */
  --accent: #8cb4ff; --on-accent: #0f1622; --ok: #86d29a; --warn: #f2c94c; --bad: #ff8a80; --mark: #c39bff;
  --font: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --fs-s: 12px; --fs-m: 13px; --fs-l: 15px; --fs-xl: 18px;     /* labels, body, the prompt, titles */
  --sp-1: 4px; --sp-2: 8px; --sp-3: 12px; --sp-4: 16px; --sp-5: 24px; --sp-6: 32px;
  --r-s: 6px; --r-m: 10px; --r-pill: 999px;
  --ctl: 32px; --h-bar: 48px; --w-panel: 320px; --tile: 56px;
  --fast: 120ms; --slow: 180ms; --ease: cubic-bezier(.2, 0, 0, 1);
}
```

- Every grey is neutral (R = G = B); no web font. Body `--fs-m`/1.45, labels `--fs-s`/500 in sentence case, titles
  `--fs-xl`/600, numbers `tabular-nums`, placeholders `--text-2`. Contrast (WCAG formula): `--text` on `--bg` about
  14.6:1; `--text-2` 6.0:1 or more on every surface; `--accent` on `--bg` 9.0:1, `--on-accent` on it 8.7:1; `--warn`,
  `--ok`, `--bad`, `--mark` 7.9:1 or more on `--panel`.
- Buttons `--ctl` high, radius `--r-s`, fill `--raised`, 1 px `--line`, hover `--hover` over `--fast`. One primary per
  area (Open folder in the empty state, Export photo, Edit N photos): fill `--accent`, text `--on-accent`. Disabled:
  `--text-3`. Selects native on `--raised`. Ranges: a 2 px `--line` track, the filled part `--text-2`, a 14 px round
  `--text` thumb. Chips: 28 px pills on `--raised`, × a 24 px button; a mark chip starts with a 10 by 7 px `--mark` oval.
- On the photo: ovals 1.5 px white with a 1 px `rgba(0,0,0,.6)` ring outside, the pending one dashed; tints at 45 %
  through `mask-image` (`--mark` for marks, `--accent` for regions); `#tag` a small pill top left.
- `:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }` everywhere, never removed. `#busy`: a
  2 px `--accent` line at the top of the stage, a 1.2 s indeterminate sweep.

### 3. Components

| Area | Elements (id, element, words) |
|---|---|
| `header#bar` | `.brand` "AutoEdit Lab"; `#folder` (the folder's last name, `title` the path); `button#open` "Open folder"; `a#studio href="/"` "Studio"; `button#settingsBtn` "Settings" |
| `section#stage` (`aria-label` "Photo") | `#busy`; a row with `p#status` (`aria-live="polite"`) plus `span#secs` (`aria-hidden`), `button#origBtn` "Original" (`aria-pressed`), `#which` (Try, after a compare: buttons "Yours", "AutoEdit", `aria-pressed`); `#canvas` holding `#empty` ("Open a folder of photos to begin.", `button#open2`, "Your photos stay on this computer.") and `#frame` (`tabindex="0"`, `data-showing`, `aria-label` "Photo. Hold B to see the original.") with `img#before`, `img#after`, `img#engine` stacked, `#tint`, `#ovals`, `#tag` |
| `form#prompt` | `button#markBtn` "Mark" (`aria-pressed`); `#markTag` "for the oval" (hidden); `input#say` (no `name`; label "Say what to change"; placeholder "Say what to change, e.g. warm the photo a little"); `button#apply` "Apply" (submit); `ul#chips` |
| `aside#panel` (`aria-label` "Edit") | `#viewEdit`: `h2#photoName` (`tabindex="-1"`), `#photoMeta`; "Look" `select#look` (first option "Default", value ""), `button#surpriseBtn` "Surprise me"; "Kind of photo" `select#genre` (first "None"), `#genreNote`; "Strength" `input#strength type="range"`, `#strengthVal`, `button#strengthReset` "Reset" with an amber dot (both hidden at the default); `button#howBtn` "How it was done ›"; `button#tryBtn` "Try it yourself ›"; `button#export` "Export photo", `#exportNote` (`aria-live`). `#viewHow`: `button#howBack` "← Edit", h2 "How it was done", h3 "Your changes" `ol#changes`, h3 "Every step" `ol#steps` (a button per step), `#stepDetail`. `#viewTry`: `button#tryBack` "← Edit", h2 "Try it yourself", `#sliders`, `#lesson`, `button#tryCompare` "Compare with AutoEdit" (`aria-pressed`), `button#tryReset` "Reset", `ul#facts`, `table#values`, `#said`, the line "Export saves AutoEdit's edit with your sentences and marks, not these sliders." |
| `nav#strip` (`aria-label` "Photos") | `input#all type="checkbox"` "All"; `#count`; `#batchNote` (`aria-live`); `button#pause`, `button#cancel` (only while a batch runs); `button#batchBtn` "Edit N photos"; `#batchBar`; `ul#film`: per photo an `li` with `button.tile` (`aria-label` the name, `aria-current`, an `img` 56 px, `object-fit: cover`, `loading="lazy"`, `alt=""`), an `input type="checkbox"` (`aria-label` "Include NAME in the batch"), `span.dot[data-status]` |
| `dialog#settings` | "Region finder": a radio per `backend` choice (`name="finder"`; "Auto (SAM 3 when it is installed)", "SAM 3", "Stub (no model)") and the schema's `help`; "Your style" `#styleNote`, `ul#styleList`; "Keys" (Design 7); "Culling, review labels, datasets and training are in Studio." with a link to `/`; `#version`; Close |
| `dialog#surprise` | `h2#surpriseTitle`, `#surpriseNote`, `#surpriseGrid` (six `button.lookTile`: an `img` and the name), `button#surpriseMore` "Six more", Close |

`<head>`: charset, viewport, `<meta name="color-scheme" content="dark">`, `<link rel="icon" href="data:,">` (no favicon
request), `<title>AutoEdit Lab</title>`, one `<style>`. One `<script>` without attributes closes the `body`.

### 4. State and requests (reference code for the hard part)

```js
const DEFAULT_FINDER = 'auto', STORE = 'autoedit.lab.v1', MAX_TEXT = 2000, MAX_MARKS = 20, MIN_SIDE = 0.01;
const LAB = {dir: '', files: [], sel: -1, view: 'edit', finder: DEFAULT_FINDER, look: '', genre: '', strength: null,
  entries: {}, pending: null, marking: false, hold: false, answer: null, how: null, step: 0, tried: null,
  sliders: {}, compare: false, which: 'yours', busy: 0};
const $ = s => document.querySelector(s), isMark = e => e !== null && typeof e === 'object';
function options() {          // what the user set, plus the finder: a key that is sent wins over a genre (F30)
  const o = {backend: LAB.finder};
  if (LAB.look) o.look = LAB.look; if (LAB.genre) o.genre = LAB.genre; if (LAB.strength !== null) o.intensity = LAB.strength;
  return o;
}
const textOf = l => l.filter(e => !isMark(e)).map(s => s.trim().replace(/[.\s]+$/, '')).filter(Boolean).join('. ');
const marksOf = l => l.filter(isMark).map(m => ({box: m.box, kind: m.kind, edit: m.edit}));
function previewBody() {      // B of the API table; also a console handle of the walk-through
  const p = LAB.files[LAB.sel]; if (!p) return null;
  const l = LAB.entries[p.path] || [], b = {file: p.path, ...options()}, t = textOf(l), m = marksOf(l);
  if (t) b.photo_edit = t; if (m.length) b.photo_marks = m;
  return b;
}
async function post(url, body) {
  const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.error) throw new Error(j.error || 'The server answered ' + r.status);
  return j;
}
const BODIES = {edit: () => previewBody(),
  how: () => previewBody() && {...previewBody(), steps: true},
  try: () => previewBody() && {...previewBody(), try: {sliders: {...LAB.sliders}, compare: LAB.compare}}};
const flights = {};           // per view: one request in flight, the latest state wins
async function once(kind) {   // SHOW = {edit: showEdit, how: showHow, try: showTry}
  const f = flights[kind] ||= {busy: false, again: false, shown: ''}, body = BODIES[kind]();
  if (!body) return; const key = JSON.stringify(body);
  if (key === f.shown) return;                      // the view shows this state already
  if (f.busy) { f.again = true; return; }
  f.busy = true; setBusy(1);
  try { const j = await post('/api/v1/preview', body); if (JSON.stringify(BODIES[kind]()) === key) { f.shown = key; SHOW[kind](j); } }
  catch (e) { if (JSON.stringify(BODIES[kind]()) === key) say(e instanceof TypeError ? NO_SERVER : e.message, 'bad'); }
  f.busy = false; setBusy(-1);
  if (f.again) { f.again = false; once(kind); }
}
const refresh = () => once(LAB.view);
const DATA = /^data:image\/(webp|jpeg|png);base64,[A-Za-z0-9+/=]+$/, PNG = /^data:image\/png;base64,[A-Za-z0-9+/=]+$/;
const pic = s => (typeof s === 'string' && DATA.test(s) ? s : '');   // every picture of an answer; '' keeps the old src
const thumb = (path, px) => '/api/v1/thumb?px=' + px + '&path=' + encodeURIComponent(path);
const join = (dir, name) => dir.replace(/\/+$/, '') + '/' + name;
async function loadFolderPath(path) {     // the picker's folder; also a console handle of the walk-through
  const r = await fetch('/api/v1/groups?dir=' + encodeURIComponent(path)), g = await r.json().catch(() => ({}));
  if (!r.ok || !Array.isArray(g)) return say(g.error || 'The folder could not be read.', 'bad');
  if (!g.length) return say('No photos in this folder.');
  LAB.dir = path;
  LAB.files = g.map(x => ({name: x.raw || x.pixel, path: join(path, x.raw || x.pixel),
    thumb: join(path, x.pixel || x.raw), checked: true, status: ''}));
  renderStrip(); select(0);
}
function fit() {              // the frame takes the picture's shape inside #canvas; on load of #after and on resize
  const img = $('#after'), c = $('#canvas'), w = img.naturalWidth, h = img.naturalHeight; if (!w || !h) return;
  const s = Math.min((c.clientWidth - 32) / w, (c.clientHeight - 32) / h);
  $('#frame').style.width = Math.floor(w * s) + 'px'; $('#frame').style.height = Math.floor(h * s) + 'px';
}
const okMark = x => isMark(x) && Array.isArray(x.box) && x.box.length === 4
  && x.box.every(v => typeof v === 'number' && isFinite(v) && v >= 0 && v <= 1)
  && x.box[2] - x.box[0] >= MIN_SIDE && x.box[3] - x.box[1] >= MIN_SIDE && (x.kind === 'auto' || x.kind === 'area')
  && typeof x.edit === 'string' && x.edit.trim() !== '' && x.edit.length <= 300;
```

- `loadStore()` reads `STORE` inside `try`: `finder` only when it is `auto`, `sam3` or `stub`; per photo path the
  entries that are a non-empty string of at most `MAX_TEXT` characters or pass `okMark` (marks copied as `{box, kind,
  edit}`); anything else is dropped. `saveStore()` writes `{v: 1, finder, entries}` inside `try`.
- `setBusy(d)` adds `d` to `LAB.busy`, shows `#busy` and "Working…" while it is above 0, and from 3 s on writes the
  seconds into `#secs`. `say(text, kind)` writes `#status`. `NO_SERVER` is "AutoEdit is not answering. Start it with
  autoedit ui, then reload." `fit()` also runs from a `ResizeObserver` on `#canvas`.
- Each `show*` stores its answer (`LAB.answer`, `LAB.how`, `LAB.tried`) and paints only when its view is current.
  `select(i)` empties all three; the chips read the answer of the current view.

### 5. Interactions

1. **Start.** `loadStore()`; `GET /api/v1/settings/schema` and `GET /api/v1/genres` side by side. `#look` gets `new
   Option(name.replaceAll('_', ' '), name)` per `look.choices`; `#genre` one option per offered genre; `#strength`
   takes `min`, `max`, `step = 0.05` and `default`; the finder radios come from `backend.choices` and `backend.help`.
   A failed read: "Settings could not be read: MESSAGE", the selects disabled; the photo still edits.
2. **Open a folder.** `#open`, `#open2`: `post('/api/v1/pick?kind=folder', {})`, then `loadFolderPath(path)`; a cancel
   does nothing. `#panel`, `#prompt`, `#strip` show; `#folder` takes the last part of the path.
3. **Choose a photo** (a tile, ←, →): `LAB.sel = i`; `#before`, `#after` take `thumb(p.thumb, 1600)`; the pending oval
   and the tints go; chips and `#photoName` are drawn; `refresh()` 250 ms later (a new choice restarts the timer). The
   chosen tile has `aria-current="true"` and scrolls into view.
4. **showEdit(j)** paints `pic(j.before)`, `pic(j.after)`; `#photoMeta` and the status read `SCENE · regions by FINDER`
   (`result.audit.run_config.backend`: `sam3` as "SAM 3", `stub` as "stub"), then " · worth a look: REVIEW_REASON" in
   `--warn` when `result.needs_review`, then " · N not understood" in `--bad`; the chips are drawn again.
5. **Say what to change.** The form's submit (Enter, Apply) always calls `preventDefault`; empty does nothing. A
   pending oval pushes `{box, kind: 'auto', edit}` (at 20: "This photo has 20 marks; remove one first."), else the
   sentence (past 2000 characters: "Too many changes on this photo; remove one first."); then empty, save, chips, refresh.
6. **Chips** (`#chips`, one `li` per entry, `textContent` only). A sentence: when a problem line's clause (between its
   first and last `"`) is inside the lower-cased sentence, the chip turns `--bad`, adds "not understood" and takes the
   line as `title`; a tag "local model" when an adjustment with `via: "local"` has its `source` inside it. A mark (the
   k-th mark of the list is `marks[k]`): its oval glyph, its `edit` and one state line, as Studio's `markState` (F37
   Design 9): problems: "not understood"; a found `thing`: "the WORD", plus " · held back in part" (`clamped`) or " ·
   held back" (`blocked`); a `thing` not found: "That is part of something bigger" (`why` bigger), "The WORD is too
   small here" (small), else "No WORD found in the oval", each with a button "Use the oval" (sets `kind: 'area'`,
   saves, refreshes); an `area`: "the oval" plus that suffix, and with SAM 3 and `kind: 'auto'` ". Name the thing to
   follow its outline: this jacket". Pointer over or focus in a mark chip shows its oval in `#ovals` and, in `#tint`,
   the `mark_overlays` whose `target` is in `marks[k].targets` (after the `PNG` check). Every chip has × (`aria-label`
   "Remove: TEXT"): remove, save, refresh.
7. **Mark.** `#markBtn` or O toggles `LAB.marking` (`aria-pressed`, crosshair). Pointer down on `#frame` (primary
   button) keeps the start and captures the pointer; move draws the dashed oval; up keeps the box (shares of `#frame`'s
   rectangle, clamped to 0..1, 4 decimals, as Studio's `markDrag`) as `LAB.pending` when both sides are at least
   `MIN_SIDE`, else drops it; `#markTag` shows and `#say` takes the focus, placeholder "What should change in the
   oval? e.g. brighten this a little". Esc drops the oval; a second Esc leaves Mark.
8. **Original.** Pointer down on `#frame` while not marking, B held (keydown without `repeat`, keyup), or `#origBtn`
   (a toggle) set `LAB.hold`: `#frame.dataset.showing` is `before`, else `after` (`engine` in Try with AutoEdit
   chosen); only that image is visible (`visibility`, all stay decoded); `#tag` reads "Original" ("Before this step"
   in How). Pointer up, cancel, leave and a window blur end the hold. `#frame` has `touch-action: none` and no text
   selection or touch callout.
9. **Look, kind, strength.** `#look` sets `LAB.look`; `#genre` sets `LAB.genre` and writes its `description` into
   `#genreNote`; `#strength` input writes `#strengthVal` (two decimals), change sets `LAB.strength = +value` and shows
   the dot and Reset; Reset sets `null` and the slider to the default. Each then `refresh()`.
10. **Surprise me.** `#surprise.showModal()`, "Rendering six looks…", `post('/api/v1/variants', {file, surprise: true,
    ...options()})`; then `#surpriseNote` "Seed SEED. The same seed gives the same six." and a tile per name of `looks`,
    in order: its variant's `pic(after)`, the name, "the safety check flags it on this photo" when `needs_review`. A
    tile sets `LAB.look` (adding the option `#look` lacks), closes, refreshes. "Six more" asks again (a new seed).
    Close or Esc gives the focus back to `#surpriseBtn`.
11. **How it was done.** `#howBtn`: `LAB.view = 'how'`, its h2 takes the focus, `refresh()`; `showHow` sets
    `LAB.step = 0`. `#changes`, per walkthrough entry: `label`, the `sources` in quotes, a status word (applied; "held
    back in part" for clamped; "held back" for blocked; "not found"), `mask.line`, the `parts` as "LABEL 12 %" joined
    by " · ", per tool "NAME asked VALUE: PART VALUE (held)" for each `applied` item, the `held` lines; none: "No change
    of your own on this photo: the steps below are AutoEdit's edit." Pointer over or focus in an entry tints its
    `mark_overlays` and shows its oval (`mask.box`). `#steps`: a button per step, "N. TITLE"; the chosen one
    (`aria-current="step"`) puts `steps[i].after` into `#after` and `steps[i - 1].after` (step 0: its own) into
    `#before`; status "Step N of M · TITLE". `#stepDetail`: for `tools` and `grain` each tool "NAME VALUE" with its
    `does`, `when`, `watch`, else the step's own three lines; `where` rows "TARGET COVERAGE % · NAME VALUE, …" (pointer
    over or focus tints that `name`'s `mask_overlays` in `--accent`); `typed` as "Your changes here: LABEL, …"; the
    `notes`; "Moved M levels of 255 on average" unless `moved` is null. ← → choose the step. `#howBack` or Esc: Edit
    paints `LAB.answer` (the thumbnail when it is empty) and calls `refresh()`.
12. **Try it yourself.** `#tryBtn`: `LAB.view = 'try'`, `refresh()`; `showTry` paints the try's `before` and `after`.
    The first answer builds `#sliders` from `try.sliders`: label `name`, a range with `min`, `max`, `step`, value
    `LAB.sliders[op] || 0`, a value text that is also `aria-valuetext`. Focus or input on a slider puts its `name`,
    `does`, `when`, `watch` into `#lesson`; change sets `LAB.sliders[op]` (0 deletes it) and refreshes. `#tryCompare`
    toggles `LAB.compare` and refreshes. With `compare` in the answer: `#engine` takes `pic(compare.engine)`; `#which`
    shows (keys 1, 2); `#facts` lists the `checks`, flagged first, "NAME: LINE", a `--warn` dot when `flag`; `#values`
    groups `tools` by `where` (`global` first as "Whole photo", `instruction:X` as "the X", a mark through Studio's
    `niceName`) with columns Tool, Yours, AutoEdit, and "(set VALUE)" after Yours when `set` is not null and differs;
    `#said` "Your version: quality Q" and "AutoEdit: quality Q", plus ", worth a look" when `needs_review`. Without
    `compare`, `#which` hides and `LAB.which` is `yours`. `#tryReset` puts every slider at 0 and refreshes.
13. **Export photo.** `#export` (Edit view only): disabled, "Exporting…", `post('/api/v1/run-one', previewBody())`,
    then "Saved FILE in AutoEdit Exports" (`title` = `image_out`) or the error in `#exportNote`. Never a try (F29).
14. **Edit the checked photos.** Tile check boxes and `#all` (indeterminate when some) set `checked`. `#batchBtn`
    "Edit N photos" (disabled at 0 and while a batch runs): `post('/api/v1/jobs', {input_dir: LAB.dir, files,
    ...options(), photo_edits, photo_marks})`, `files` the checked photos' `path`s, the maps made with `textOf` and
    `marksOf` per checked photo, left out when empty. Then an `EventSource`: each `snapshot` and `done` sets the bar (`processed / total`), `#batchNote`
    "Editing P of T · R to look at · F failed · about S s left" ("Paused" while paused), and each tile's `data-status`
    and `title` from the item whose `src` is its `path`. On `done`: "Done: D photos in AutoEdit Exports" (plus " · R to
    look at · F failed" when not 0; `title` = `out_dir`), "Stopped after P of T." when cancelled, "The batch stopped:
    ERROR" (the first item's `error`) on `error` or `failed`; then the stream closes. Closed before `done`: "Lost the
    progress; the batch goes on." `#pause` posts pause or resume, `#cancel` cancel.
15. **Settings.** `showModal()`; on open `GET /api/v1/style/suggestions` and, once, `/version` ("Engine E · API A"). A
    finder change saves and refreshes. Your style, `on` false: "Off: your edits are not kept. It starts with history =
    true under [run] in your profile's defaults.toml."; on: "P photos on D days.", each suggestion's `ask` with its
    `fact` below, then "To add them to a new look: autoedit style suggest --yes NAME"; none: "No suggestion yet."
16. **Values.** `exposure`, `shadows_ev`: a sign, two decimals, " EV"; other ops: a sign and one decimal without a
    trailing ".0". Shares: "12 %" (one decimal under 10 %).

### 6. States

| State | What shows |
|---|---|
| No folder | the bar and `#empty` only |
| An empty or unreadable folder | "No photos in this folder." / the server's text (a 403 says to use the picker) |
| Working | `#busy`; "Working…", the seconds after 3 s in `#secs` (not announced) |
| Done, not understood | 5.4; the chip in `--bad` |
| Error | the server's `error` text in `--bad`, as it is; `NO_SERVER` when the request threw (export, batch: 5.13, 5.14) |

### 7. Keyboard, focus, screen readers, motion

- Keys act when the focus is not in an input, a select or a dialog and no modifier is held: ← → the photo (in How the
  step); B held the original; O Mark; `/` the focus to `#say`; 1 and 2 Yours or AutoEdit after a compare; Esc (also
  inside `#say`): drop the pending oval, then leave Mark, then back to Edit. One `keydown` and one `keyup` listener on
  `document`. Settings lists the keys.
- Tab follows the layout; dialogs are modal (`showModal`) and give the focus back to their button; a new view moves
  the focus to its heading. Landmarks `header`, `main`, `aside`, `nav`; every control has a visible label or an
  `aria-label`; toggles use `aria-pressed`; `#status`, `#exportNote`, `#batchNote` are `aria-live="polite"`; the
  photos' `alt`: "Edited photo", "Original", "Step N", "AutoEdit's edit".
- `@media (prefers-reduced-motion: reduce)`: no transition, no animation; `#busy` is a still line at 60 % opacity.

### 8. Security

- Server and user text reaches the page only through `textContent`, `new Option(text, value)`, `setAttribute` (for
  `aria-label`, `title`, `alt`) or `.value`. No `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`,
  `eval`, `new Function` or string timer: the static markup is in the file.
- Pictures: `pic()`-checked data URLs and the page's own `/api/v1/thumb` URLs; an overlay enters `url("…")` only after
  the `PNG` check. Paths come from the picker's answer (or the console handle, which the server's boundary checks like
  any request), file names from `/api/v1/groups`; no text box takes a path.
- Relative URLs, same origin, JSON bodies: the Host and Origin checks of `app.py` and the path boundary apply as they
  are. The page never sends `decider`, `allow_cloud` or `api_key`: the LLM path stays off (CLAUDE.md, invariant 6).
- No external URL, no `target="_blank"`, no form `action`; `#say` has no `name`, so a failed script can put no typed
  text into a URL.

### 9. The route

`src/autoedit/web/app.py`: the constant above `create_app`, the route inside it right after `classic()`:

```python
# F38: the test UI page may load nothing but itself, data: pictures and same-origin requests.
LAB_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
           "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'")
    # ... inside create_app:
    @app.get("/lab", response_class=HTMLResponse)
    def lab():
        return HTMLResponse(_page("lab.html"), headers={"Content-Security-Policy": LAB_CSP})   # the test UI (F38)
```

Nothing else changes. The file is read on every request, as Studio's is: a page edit needs a reload, not a restart.
`static/*` is packaged already (`pyproject.toml`, `MANIFEST.in`). No `frame-ancestors`: the in-app browser may frame
pages. The snapshot gains `"GET /lab": {"path": [], "query": [], "body": false}`, regenerated, never typed.

## Files

| File | Change | Task |
|---|---|---|
| `src/autoedit/web/static/lab.html` | new: the page, one `<style>`, one `<script>`; about 1,000 to 1,300 lines | T1 |
| `src/autoedit/web/app.py` | `LAB_CSP` and the route (Design 9) | T2 |
| `tests/test_lab_page.py` | new: tests 1 to 6 | T2 |
| `tests/snapshots/api_v1.json` | regenerated: `GET /lab` | T2 |
| `APP_GUIDE.md`, `README.md`, `CHANGELOG.md`, `docs/FEATURE_PLAN.md` | a short section on the Lab; one line; the entry; the F38 row (`\| F38 \| Test UI: AutoEdit Lab \| M \| … \|`), the lab named in the rows of F27, F29 and F30, these decisions in the end table | the session |

Not changed: `studio.html`, `index.html`, `web/service.py`, every other route, every config file, the engine.

## Tasks

| Task | Tier | Content | Proving command |
|---|---|---|---|
| T1 | Builder, Sonnet, high | `lab.html`, Design 1 to 8 | the static check below prints `ok` |
| T2 | Builder, Sonnet, medium | Design 9; `tests/test_lab_page.py`; the snapshot by `AUTOEDIT_UPDATE_SNAPSHOTS=1 PYTHONPATH=src python3 -m pytest tests/test_api_contract.py -q` | `PYTHONPATH=src python3 -m pytest tests/test_lab_page.py tests/test_api_contract.py tests/test_style.py tests/test_security.py -q` |
| Check | Checker, Opus, high | The diff against this spec: the bodies of Design 4 and 5, Design 8, the snapshot diff (one entry), the header; the privacy check | none |
| Walk | The session | Acceptance check 3 | the steps |

T1 and T2 share no file and may run side by side; T2's proving command needs T1's page. T1's check, from the root:

```
python3 - <<'EOF'
import pathlib, re, subprocess
h = pathlib.Path("src/autoedit/web/static/lab.html").read_text(encoding="utf-8")
js = re.findall(r"<script>(.*?)</script>", h, re.S); assert len(js) == 1, "one inline <script> block"
bad = [w for w in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
                   "http://", "https://", "@import", "<script src", "followups") if w in h]; assert not bad, bad
p = pathlib.Path(".tmp/f38-lab.js"); p.parent.mkdir(exist_ok=True); p.write_text(js[0], encoding="utf-8")
subprocess.run(["node", "--check", str(p)], check=True); p.unlink(); print("ok")
EOF
```

## Tests

`tests/test_lab_page.py`, no browser. Tests that need FastAPI return early without it, as `tests/test_api_contract.py`
does (its `_client()`, `base_url="http://localhost"`).

| # | Test | Asserts |
|---|---|---|
| 1 | `test_the_lab_route_serves_the_page` | `GET /lab`: 200, `text/html`, the body is the file's text; its `Content-Security-Policy` holds `default-src 'none'`, `connect-src 'self'`, `img-src 'self' data:`; with the Host `evil.com` 403; `GET /` and `GET /classic` answer the text of `studio.html` and `index.html` and have no such header |
| 2 | `test_the_page_makes_no_outside_request` | The file holds no `http://`, `https://`, `@import`, `<iframe`, `<script src`, `target="_blank"`, no `<form` with an `action`; no `src=` or `href=` starting with `//`; its one `<link>` is `rel="icon" href="data:,"`; its `<style>` has no `url(` |
| 3 | `test_the_page_builds_no_markup_from_text` | The script has none of `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval(`, `new Function`, and no `setTimeout` or `setInterval` with a string (``r"set(Timeout\|Interval)\(\s*['\"`]"``) |
| 4 | `test_the_page_calls_only_v1_routes_that_exist` | Every literal found by ``r"""['"`](/api/[^'"`?$]*)"""`` starts with `/api/v1/` and starts a path of `tests/snapshots/api_v1.json` (`/api/v1/jobs/` starts `/api/v1/jobs/{job_id}`); the found set includes preview, pick, groups, thumb, run-one, jobs, variants, settings/schema, genres, style/suggestions, version |
| 5 | `test_the_script_parses` | With `node` on the PATH (`shutil.which`): the one inline script, written into `tmp_path`, passes `node --check`; without node the test returns |
| 6 | `test_the_page_keeps_the_basics` | `<html lang="en">`, a viewport meta, `<title>AutoEdit Lab</title>`, `color-scheme: dark`, `prefers-reduced-motion: reduce`, `:focus-visible`, one `<main`, `aria-live="polite"`, `function loadFolderPath(`, `function previewBody(`, `const LAB`, `'autoedit.lab.v1'`; no `autoedit.followups` |

`tests/test_api_contract.py` passes unchanged against the regenerated snapshot.

## Acceptance checks (the session runs these)

1. Both suites green (`PYTHONPATH=src python3 tests/run_all.py`, and with `.venv/bin/python`); the stub smoke run.
2. The diff touches only the files above (default output byte-identical without a render; no tone gate), and the
   snapshot's diff is the one `GET /lab` entry; `/` and `/classic` serve the same bytes as before.
3. The walk-through below, in the in-app browser, with the stub. `<repo>` is the checkout's absolute path.
4. The Checker's review; `scripts/check_public_repo.py` over the changed files.

Walk-through (each check holds before the next step):

1. `mkdir -p .tmp/f38-walk && cp test-corpus/*.jpg .tmp/f38-walk/` (exports land beside the photos: here inside the
   ignored `.tmp`). `preview_start` with `studio-verify` (`AUTOEDIT_ALLOW_ANY_PATH=1`, port 8765); open
   `http://localhost:8765/lab`; `resize_window` 1440 by 900.
2. The empty state shows. Console: `typeof loadFolderPath + typeof previewBody` is `"functionfunction"` (if the
   header stops the tool's console lines, drop it for the walk and record that).
3. Settings: Region finder → Stub (`LAB.finder === 'stub'`); Your style reads "Off: …"; the version is filled.
4. `await loadFolderPath('<repo>/.tmp/f38-walk')`, poll every 300 ms until `LAB.busy === 0 && LAB.answer`: two tiles,
   the first chosen; the status ends "regions by stub"; `LAB.answer.result.audit.run_config.backend === 'stub'`.
5. `#origBtn`: `$('#frame').dataset.showing === 'before'`, the source on screen; again: `after`. The same with
   `keydown`/`keyup` of `{key: 'b'}` dispatched on `document`, and `pointerdown`/`pointerup` (button 0) on `#frame`.
6. "warm the photo a little", Enter: one plain chip; `instruction_problems` is empty; an adjustment with `target ===
   'global'` has status applied or clamped.
7. "don't brighten the face", Enter: that chip is `--bad` with "not understood", its `title` names the negation; the
   status counts 1 not understood.
8. Mark, drag an oval over the lower half, "brighten this a little", Enter: a chip "the oval"; `LAB.answer.marks[0].kind
   === 'area'`, `mark_overlays.length >= 1`; pointing at the chip shows oval and tint (screenshot).
9. Reload; Settings still on Stub; `loadFolderPath` again: the three chips are back.
10. Look "bw classic": `run_config.look === 'bw_classic'`, the photo grey (screenshot). Surprise me: six tiles and a
    seed line; the third tile: `#look` shows its name, the next answer's `run_config.look` is it. Back to Default.
11. Kind of photo "alpine": `previewBody()` has `genre: 'alpine'` and no `style`, `concepts` or `intensity`;
    `run_config.genre === 'alpine'`, `run_config.highlight_hold === true`. Strength: focus, → eight times: "1.20", dot
    and Reset show, `previewBody().intensity === 1.2`; Reset: no `intensity`. Kind back to None.
12. How it was done: the first step is "The photo as it came", `checks` comes once; Your changes lists the sentence
    and the mark; → and ← change step and picture; B held shows the step before; Esc: Edit.
13. F29's browser check (its acceptance 5, open until now), in the console:
    `const b = previewBody(), p2 = x => fetch('/api/v1/preview', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(x)}).then(r => r.json()); const plain = await p2(b), walk = await p2({...b, steps: {px: 2560, lossless: true}}), tried = await p2({...b, try: {sliders: {exposure: 1.2}, compare: true}}); [walk.steps.at(-1).after === plain.after, tried.compare.engine === plain.after, tried.compare.tools.every(r => r.where !== 'global' || Math.abs((plain.result.global_settings[r.op] || 0) - r.engine) < 1e-4)]`
    gives `[true, true, true]`; `const e = {...b}; delete e.photo_edit; delete e.photo_marks; const t = await p2({...e, try: {}}); t.after === t.before` gives `true`.
14. Try it yourself: seven sliders, Exposure first; Exposure to +1.20 (`form_input`, or → 24 times); Compare with
    AutoEdit: `#facts` lists lines (flagged first), `#values` has an Exposure row with Yours "+1.20 EV"; 1 and 2
    switch the picture; ← Edit.
15. Export photo: "Saved … in AutoEdit Exports"; `.tmp/f38-walk/AutoEdit Exports/` holds the JPEG and its sidecar,
    whose adjustments hold "warm the photo a little", a `mark:area:` target, and no `global` one without a `source`
    (what a try's sliders would be).
16. Edit 2 photos: "Done: 2 photos in AutoEdit Exports"; both dots green; two JPEGs and two sidecars on disk.
17. Tab from the top: a ring on every control, in layout order; ← and → change the photo; `/` focuses the box.
18. `resize_window` mobile, reload, open the folder: one column, `document.documentElement.scrollWidth <=
    innerWidth`, box, panel and strip work (screenshot); back to desktop.
19. `read_console_messages` (errors only): none; `read_network_requests`: all to `localhost:8765`; `/` opens Studio.
20. Clean up: `localStorage.removeItem('autoedit.lab.v1')`, `preview_stop`, `rm -rf .tmp/f38-walk`.

## Known limits

- **Weight and work.** A large photo's preview is about 10 MB of JSON, its steps 3 to 4 MB more, a compare three
  pictures. A request the page no longer needs still runs to its end on the server (the page sends at most one more
  per view). A try is a full preview (F29: about 9 s on 22 megapixels, 18 s with compare): sliders apply on release.
- **Surprise me** shows the six looks without the photo's sentences and marks (fact 5), at 1100 px. The page repeats
  four limits of the server (a side of 1 %, 300 characters, 20 marks, 2000 characters), as Studio does (F37).
- **The surround** is neutral dark grey, so a photo looks a little brighter than on mid-grey or white (Research).
- **Later controls.** A mid-grey surround for judging tone. F32's `straighten` ("Straighten") and `out_aspect` ("Crop to shape") are in the schema: a switch
  and a choice in the Edit panel, sent like `look`, once F32 is built (its preview turns the picture and maps a mark
  back). A LUT needs a picker for `.cube` files; the reveal check waits for F21.
- **Not here:** what Studio keeps (Decisions), undo beyond × on a chip, a "this change on and off" switch, an output
  folder. Press and hold cannot be driven by the browser tools: the walk uses the button and dispatched events.
- **Browsers.** Checked in the in-app browser (Chromium) only. The page needs `<dialog>`, `EventSource`,
  `ResizeObserver` and CSS `mask-image` (with `-webkit-mask-image` for Safari).

## Result

Built on 2026-11-17 in the unattended run. Status: done. The spec was written on Opus at max
effort, not Fable as `AGENT_ROUTING.md` says: Fable's quota ran out during the run.

- **Built** by two Builders (Sonnet) from this spec, plus F32's two controls (Straighten, Crop),
  built after the spec was written.
- **The walk-through** (in-app browser, stub, 1440 by 900 and 375 by 812) passed every step: empty
  state; open folder; before/after by button, B and press-and-hold; a sentence applied, a negation
  shown as not understood; an oval mark with its chip, overlay and tint; reload keeps the changes;
  black-and-white look; Surprise me (six tiles, a pick sets the look); strength by keyboard to
  1.20; How it was done (12 steps, arrows, Esc); Try it yourself (seven sliders, compare facts);
  export (the sidecar holds the sentence and the mark, not the sliders); a batch of two; a focus
  ring; no sideways scroll at phone width; no console errors; `/` is still Studio. F29's open
  browser check passed here: `[true, true, true]`. Two faults found and fixed: the Yours/AutoEdit
  switch stayed visible after leaving Try, and "Kind of photo" showed an empty menu (no pack is
  tested, F30), now hidden. Look names are written with a capital and `B&W`.
- **The Checker (Opus, high)** found one thing that had to be fixed: a mark drawn on a straightened
  or cropped preview was read on the whole photo, so it edited the wrong place. Mark is now
  switched off while Straighten or Crop is on, with the reason on the button. Also fixed: a lost
  progress stream now falls back to asking for the job every two seconds, and a late answer can no
  longer reopen a finished batch; batch progress is matched by path only (not by file name across
  folders); the status no longer stays at "Working…" with nothing in flight; stored changes are
  held to the server's limits (20 marks, 2000 characters); the photo has `role="img"`.
- **Known limits kept:** for a moment after a change is removed, a mark chip can read the old
  answer; a slow export that ends after a photo switch writes its note under the new photo; the
  content policy allows inline script (a hash would be stricter) and no `frame-ancestors`, as
  Studio; marks need a drag (no keyboard way); test 4 matches routes by prefix only.
