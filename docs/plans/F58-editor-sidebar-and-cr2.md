# F58 · One editor sidebar and reliable Canon RAW opening

**Status on 2026-11-24.** Built, on the fallback of decision D1: AutoEdit's own photo screen is
the editor, and LightCraft opens from **Adjust → Fine-tune in LightCraft**. The
[probe](F58-probe.md) chose that path. A second session checked the build the same day and fixed
eight things ([what the check found](#check-of-the-build-2026-11-24)). Three decisions are still
the owner's: D4's last question, D6 and D12.

The sections from "What the photographer sees" to "Release checks" are the plan as it was written
before the build. [As built](#as-built) says what exists.

This plan supersedes the AI button names and default workflow in F57. **AI Edit** means AutoEdit's
built-in local photo pipeline. **Ask Gemini** is a separate, optional assistant. The on-device path
must work with the network disconnected and no API key.

Two plans belong with this one. [F59](F59-local-styles.md) builds the local styles that
**Run AI Edit** offers. [F60](F60-web-photo-loop.md) is the loop that tests them, and this editor,
on photos from the web.

## What the photographer sees

The start screen offers **Automatic edit** for hands-off processing and **Photo editor** for work
on one photo. Opening a photo shows its original. **Run AI Edit** explicitly begins the local edit.
Both modes use the same Standard and specialized local styles.

| Part of the editor | Controls |
| --- | --- |
| Top bar | Library, filename, Export |
| Photo canvas | Original on open. After an edit, a small **Before / After** control switches the canvas to a draggable comparison. |
| Editing sidebar · **Adjust** | Existing LightCraft light, colour, curves, detail, crop and manual controls |
| **AI Edit** | Standard by default, specialized local styles, optional reference photo, **Run AI Edit**, editable changes and Undo |
| **Isolate** | Pick or name an object, view its mask, refine with a brush, and adjust the selected area |
| **Learn** | **What changed** and **Try it yourself** for the active photo and edit |
| **Ask Gemini** | Optional cloud suggestions, preview-sharing permission and a review step |

Place the sections together in one editing sidebar. Opening a section should keep the photo and
zoom in place. On narrow screens the same sections fit in a tools drawer. Keep help near actions
that need it, such as a missing decoder or an undetected object.

## What the review found

Checked on 2026-11-24 in the code, in the pinned LightCraft 0.21.1 web release, and in LightCraft's
own documents (`web.md`, `control-protocol.md`, `ai-masks.md` and `gaps.md` in its repository). The
documents were read on its main branch. M0 repeats the checks that matter on the pinned version.

| # | Finding | What it means here |
| --- | --- | --- |
| 1 | LightCraft draws its whole interface inside one canvas. The host page cannot put its own sections into LightCraft's panel. | The sidebar belongs to the host page. This plan's own fallback, "a host-owned sidebar with one active tools panel and a single canvas", is the design (M4). |
| 2 | The host can drive LightCraft with commands. It can read and write the complete manual edit of a photo (`develop.get`, `develop.merge`, `develop.set`, `develop.controls`), group changes into one undo step, set zoom and pan (`view.navigate`), and receive an exported file as bytes (`lightcraft:save`). | The manual edit can be saved in the AutoEdit project, restored, laid over a new picture, and exported through AutoEdit (M2, M3). |
| 3 | LightCraft keeps an object mask as a small table of numbers from SAM 3, and `mask.add` takes a stored mask as a parameter (`seg`). Its browser version cannot make these masks, but it draws them. AutoEdit runs the same SAM 3. | Isolate can hand AutoEdit's object masks to LightCraft as its own masks: sliders in real time, LightCraft's brush and overlay. M0 has to prove it; M5 has a fallback. |
| 4 | The browser's storage is the only place LightCraft keeps photos and edits. Upstream marks this as experimental: the browser may clear it, and removing a photo does not delete its stored original. One 22-megapixel working copy is 135 MB. | The AutoEdit project becomes the record. LightCraft's library becomes a cache that can be rebuilt, and the host removes pictures it has replaced (M2). |
| 5 | An export leaves as a browser download: outside `AutoEdit Exports`, with no sidecar, and without the `metadata = "shareable"` rule that drops GPS and serial numbers. | Export goes through AutoEdit (M2). |
| 6 | LightCraft estimates colour for a camera it has no measured profile for. Upstream reports colour differences of 2.9 to 10 and lists ten cameras with a built-in profile. Some RAW files stop its browser decoder. AutoEdit's decoder has the camera data. | Supports this plan's rule that a RAW file opens through AutoEdit's decoder (M1). |
| 7 | Today's buttons do the opposite of this plan. **AI Edit** in the editor and **AI Edit & export** in Automatic ask Gemini (F57). **Local auto** is LightCraft's own automatic development. The AutoEdit pipeline runs only from Guided and from **Local edit & export**. | M3 turns this round. |
| 8 | The two file pickers differ. The browser launcher asks macOS for `public.image`; the desktop window uses AutoEdit's own list of extensions. A RAW type that macOS does not know is greyed out in the first and selectable in the second. | One list for both (M1). |
| 9 | **Open RAW as TIFF** needs `tifffile` (the `accel` extra) and ignores the RAW settings (`raw_mode`, highlight recovery). | M1, and decision D8. |
| 10 | The Guided photo screen already has what Isolate and Learn need: find by name, choose in the photo, a brush, sliders per object, the Changes list, "How it was done", "Try it yourself" and a draggable split. They are tied to that screen's own canvas. | The server routes and the logic are reused. The screen code is not moved as it is. |
| 11 | LightCraft has heal and clone tools. AutoEdit's content rule says nothing is generated, replaced or filled in. | Decision D4. |
| 12 | LightCraft's browser version cannot render without its page. A photo with manual changes can only be exported while the editor is open. Automatic and `autoedit run` cannot apply them. | Decision D11. |
| 13 | LightCraft calls itself alpha and "moving fast", and its brush cannot be used from the keyboard. | The version and checksum stay pinned; an upgrade repeats M0 (D10). Release check 4 covers the host's sidebar and the divider; the canvas tools are a stated limit. |

## How the parts fit together

One photo has two stages in a fixed order. Every change belongs to exactly one of them.

```
original file (never changed)
  └─ decode ........................ AutoEdit           the neutral original
       └─ AI stage ................. AutoEdit pipeline  style, strength, Changes, object steps, hide
            └─ working picture ..... 16-bit, preview size while editing, full size at export
                 └─ manual stage ... LightCraft         sliders, curves, mixer, crop, its masks
                      └─ export .... AutoEdit           LUT, resize, grain, watermark, metadata, sidecar
```

| | AI stage | Manual stage |
| --- | --- | --- |
| Rendered by | AutoEdit's pipeline, the same code as Automatic | LightCraft |
| Changed in | AI Edit and Learn (and Guided) | Adjust, Isolate and Ask Gemini |
| Speed | a new picture in a few seconds | real time |
| Saved in | the AutoEdit project, as today | the AutoEdit project (new), copied from LightCraft after each change |
| Covered by the safety checks | yes | no, and the sidecar says so |

Rules:

1. **The AutoEdit project is the record.** It holds the source path, the AI stage as Guided
   already saves it, the manual stage as LightCraft's own settings with LightCraft's version, and
   which working picture they belong to. If the browser's storage is lost, the editor rebuilds the
   photo from the project.
2. **The AI stage always renders the uncropped frame from the original.** Crop and rotation belong
   to the manual stage. Both engines place masks relative to the uncropped, upright photo.
3. **When the AI stage changes**, AutoEdit renders a new working picture, the host hands it to
   LightCraft, lays the saved manual stage over it and removes the picture it replaced.
4. **Export.** AutoEdit renders the AI stage at full size. If there is a manual stage, LightCraft
   applies it and returns the picture as bytes. AutoEdit then writes the file into
   `AutoEdit Exports` with its sidecar and the chosen metadata rule. A photo without a manual
   stage exports through the code Automatic uses, so the two give the same file.
5. **Hide (F22) is part of the AI stage.** Once something is hidden, no picture without the cover
   reaches LightCraft. LUT, resize, grain, watermark and metadata are applied by AutoEdit last.
6. **Undo.** LightCraft's own undo works between two AI-stage changes. The project's history per
   photo holds both stages and is the undo across them.
7. **Left out of the first version:** expressing an AI-stage change as LightCraft sliders ("use
   native controls for changes with verified equivalent rendering" in the first proposal). Every
   AI-stage change is rendered by AutoEdit, so Automatic and the editor agree by construction.

## Decisions for the owner

| # | Decision | Proposed | Outcome on 2026-11-24 |
| --- | --- | --- | --- |
| D1 | Which canvas is the editor's home? | LightCraft's, if M0 passes. If not: the Guided photo screen becomes the editor, and LightCraft opens from Adjust as a "Fine-tune" step. | The fallback. M0 measured 4.70 s from Run AI Edit to the picture in LightCraft, against a budget of 4 s. |
| D2 | Is the AutoEdit project the record for manual edits too? | Yes. | Built. |
| D3 | Does every export go through AutoEdit? | Yes. | Built. A Fine-tune export gets its sidecar and the chosen metadata rule. |
| D4 | Heal and clone in the manual stage | Allowed as the photographer's own tools. The sidecar records that they were used, and `autoedit declare` says so. AutoEdit itself still generates nothing. | Built: the sidecar counts every retouched spot and names its tool, and the declaration changes its statement. **Open:** LightCraft also has **remove** and **fill**, which replace content. They are recorded like heal and clone. Allow them, or refuse an export that used them? |
| D5 | Size of the working picture | 2,560 px on the long edge while editing; full size at export and for a 100 % view. | Fine-tune works on 2,560 px. The editor's own canvas previews at 1,600 px. Export and **Inspect at 100%** use every pixel. |
| D6 | The RAW default (`raw_mode`, open since F8). With today's default the editor's "neutral original" is about 1.3 EV darker than the camera's own picture. | Decide with the existing blind gallery before M1 ships. | **Open.** Measured again: a median of 1.33 EV darker on 110 RAW files. |
| D7 | This plan replaces three earlier rules for the editor page: F56's "LightCraft is a one-day check" and "no new top-level areas", and UI-minimal's "never a sidebar of modes". Start → Folder → Photo stays a stack. | Yes. | Taken by building. |
| D8 | `rawpy` and `tifffile` become part of the `app` extra, because opening a RAW in the editor needs both. | Yes. | Done. An installation made earlier needs `pip install -e '.[app]'` once more. Without `tifffile`, Fine-tune answers a RAW with an install hint. |
| D9 | Names | Start screen: **Automatic edit** and **Photo editor**. Buttons: **Run AI Edit** and **Ask Gemini**. **Local auto** leaves the top bar; LightCraft's own Auto stays in its panel. | Done. |
| D10 | Upgrading the pinned LightCraft | Only together with a repeat of M0. | Stands. |
| D11 | A photo with manual changes cannot be exported by Automatic or the command line. | Accept. The Folder screen marks such a photo "finish in Photo editor". | Built for `autoedit run`, Automatic and a single export. Since the check also for `autoedit adjust` and `autoedit sync`. |
| D12 | New: **Automatic edit** exports each photo on its own, without evening a folder's brightness from photo to photo. That makes its file equal to the editor's and the command line's for one photo. Before this build it evened the folder. | Was not asked. | **Open.** Keep it, or give Automatic a switch for shoot consistency. |

## The work, in order

Each milestone can ship on its own. M0 decides D1 before anything else is built.

### M0. Prove the stack on one photo

"Prove one edit end to end before moving all the panels." Run by a session in a visible browser,
on a scratch profile, with one Canon CR2 and one JPEG from the corpus. Nothing in `src/` changes.
The answers and numbers go into one page, `docs/plans/F58-probe.md`.

| # | Question | Passes when |
| --- | --- | --- |
| P1 | Which commands does the pinned version have, with which parameters? | The list is saved in the probe page. |
| P2 | Does a manual edit survive `develop.get` → a second import of the same picture → `develop.merge`? Covering sliders, a curve point, the mixer, crop with straighten, a brush mask and a gradient mask with local sliders. | Both renders agree within 1 of 255 at 1,536 px. |
| P3 | After the working picture is replaced by one with other pixels, do crop and masks sit in the same place? Can a picture be replaced in place? | Same place. In-place replacement is a bonus. |
| P4 | Does an export arrive as bytes without a dialog, at full size and 16 bits? What does LightCraft write into the file (profile, EXIF, GPS)? | Bytes arrive; depth, size and profile are as asked. |
| P5 | Can a SAM 3 mask from AutoEdit be given to LightCraft as a stored mask? | LightCraft draws it, and it overlaps AutoEdit's mask by 95 % or more. |
| P6 | Can the host read or set zoom and pan? Can LightCraft's own side panel be closed by command? Does the photo keep its place when the canvas gets 320 px narrower? | Recorded; decides the layout in M4. |
| P7 | How long from **Run AI Edit** to the new picture on the canvas? How much does browser storage grow per photo, and after 20 AI-stage changes? | 4 s or less after the first run; growth of one working picture, not twenty. |
| P8 | Which format should the working picture have: 16-bit TIFF, a compressed 16-bit file, or 8-bit while editing? | The smallest that shows no banding at +2 EV. |

P2, P3, P4 and P7 decide D1. If P5 fails, Isolate uses its fallback. If P6 fails, LightCraft's own
panel stays visible beside the host's sidebar.

### M1. Open Photo handles CR2

The current **Open photo** sends original bytes to LightCraft. **Open RAW as TIFF** separately
uses AutoEdit's local decoder. A picker rejecting a file, a decoder failing, and a preview-only
import need different fixes. A repository Canon CR2 passed the current conversion endpoint and
returned a full-resolution 16-bit RGB TIFF at 3,870 × 5,796. The exact failure on the reported
file still needs reproduction.

- Trace an affected file through the macOS browser and desktop window. Record whether it can be
  selected, which decoder runs, the developed dimensions, and whether the editor can render and
  export it.
- One import decision on the server, used by Open, folder selection, drag-and-drop and reopen.
  JPEG, PNG, TIFF and WebP open as they are. Every RAW file, and every other format only AutoEdit
  reads, opens as a working copy made by AutoEdit's decoder with the RAW settings the Settings
  sheet shows. Upper- and lower-case extensions count the same. One **Open photo** button; the
  separate TIFF button goes.
- One list of file types for both pickers, taken from `imageio.picker_extensions()`.
- Show **Opening RAW…** over the canvas and the neutral original when ready. Keep the RAW linked
  as the source, and name the working copy in the photo's info.
- Keep working copies in a cache folder with a size cap, and reuse a valid one on reopen.
  Preserve an existing edit when its source is reopened.
- Handle a missing decoder, a corrupt file, a cancelled import and too little storage without
  breaking the library or losing the photo that was selected before.
- A file dropped on a browser page has no path. A dropped JPEG opens unlinked, and the editor
  offers to link the original when a local step needs it. A dropped RAW is sent to the local
  server for decoding and kept in the capped cache. In the desktop window a drop has a path and
  takes the ordinary route.

**Acceptance.** Use an actual Canon CR2 through the ordinary Open Photo flow, adjust exposure,
compare, export at the developed dimensions, close and reopen the same edit. Repeat in a mixed
JPEG/CR2 folder, and verify the original file hash stays the same.

### M2. One record per photo, one way out

Today the editor's edits live only in LightCraft's browser storage, and its exports bypass
AutoEdit. Moving buttons alone would keep the split between the two edit histories.

- Connect the original source, the working picture, the AutoEdit project revision and LightCraft's
  settings to one active photo. For photos previously imported only into LightCraft, keep their
  adjustments and offer to link the original when a local step needs it.
- Save the manual stage after each change, with LightCraft's version. Keep it in a file of its own
  per photo beside the project, written atomically and capped in size; the project holds a pointer
  and a checksum. The project file is capped at 8 MB, and a manual stage with masks can run to
  tens of kilobytes.
- Rebuild a photo whose browser copy is gone: working picture from the cache or a new decode, then
  the saved manual stage.
- **Export** in the top bar follows rule 4 above. The sidecar gains a `manual` block: engine,
  version, which controls changed, how many masks, and whether heal or clone was used. The review
  check runs on the AI stage; the sidecar says the manual stage was not checked.
- The host removes replaced working pictures from browser storage itself.

**Acceptance.** Clear the browser's storage and reopen: the edit is back. An export under
`shareable` has no GPS and no serial numbers. A photo with no manual stage exports byte for byte
as Automatic does with the same settings.

### M3. Run AI Edit, compare, rename

- Both **Run AI Edit** and the hands-off export call the local pipeline. Keep the optional Gemini
  call behind **Ask Gemini**. Preserve existing recipes, settings and in-progress projects when
  routing old links to the renamed screens.
- The AI stage uses the keys Guided already saves (`look`, `intensity`, `edit_steps`,
  `region_settings`, and the rest), so a photo edited in Guided opens here with its edit, and the
  other way round. AutoEdit's own sliders appear as editable AI-stage changes, never as a second
  set of exposure sliders in Adjust.
- A new server call returns the AI stage's working picture, with the same answer `preview` gives
  about totals, limits, objects and reasons. Show which whole-photo and object changes were
  applied. Running the same style twice replaces its earlier contribution.
- Reject a result that arrives after the photo or its revision changed, with the session and
  revision numbers `preview` already uses.
- Start with **Standard**. The specialized styles appear as F59 delivers each one with its test
  (M7). The Gemini briefs in F57 stay as the text Ask Gemini sends.
- After the first edit, show **Before / After** beside the image. Start the split at 50 %, label
  the two sides **Original** and **Current edit**, and support drag, touch and arrow keys. Match
  frame, crop, rotation and zoom across the two renders. The host draws the comparison: the
  Original side from AutoEdit's neutral picture with the manual stage's crop and rotation, the
  other side from LightCraft. With Upright or lens distortion on, the split is replaced by a
  hold-to-see-original switch and a line says why. While the original is untouched, show it
  without an empty comparison.

**Acceptance.** With the network off and no key: open, Run AI Edit, change one step, compare,
export. The export equals Automatic's for the same settings. No request leaves the computer
unless **Ask Gemini** is used.

### M4. The sidebar

- A host-owned sidebar of 320 px with the five sections, one open at a time. On a narrow screen
  it is a drawer.
- **Adjust** shows LightCraft's light, colour and effects sliders as ordinary page controls,
  built from `develop.controls`, each drag one undo step. Buttons open the tools that live on the
  canvas: curves, crop, masks, detail. If P6 shows that LightCraft's own panel cannot be closed,
  Adjust instead gives LightCraft the full width and its own panel does the work.
- The top bar's **Library** is the AutoEdit folder. LightCraft stays in its single-photo view;
  the host moves between photos.
- Opening a section keeps the photo and its zoom in place, by setting zoom and pan again after
  the canvas changes width.

**Acceptance.** Every sidebar control works with mouse, keyboard and touch. A screen reader
reads each slider's name and value.

### M5. Isolate

- Pick or name an object. AutoEdit finds it with SAM 3 and hands the mask to LightCraft as a
  stored mask. LightCraft shows its overlay as the mask-only view, its brush adds and subtracts,
  and its local sliders adjust the selected area. Keep the selected object when switching panels.
  When an object is missing, offer a manual selection: a click in the photo, or LightCraft's
  brush.
- AI-stage regions keep AutoEdit's rule that no pixel is edited by two regions. Masks the
  photographer adds in the manual stage may overlap, as in LightCraft.
- Fallback if P5 fails: an object adjustment becomes an AI-stage change, rendered by AutoEdit in
  a few seconds, with the mask shown as a still overlay and Guided's brush on a fitted view.

**Acceptance.** Isolate the sky, darken it, switch to Adjust and back: the sky is still
selected. Export, and the change is in the file and inside its feathered mask.

### M6. Learn

- **Learn** opens the saved changes for this image. Selecting a step shows its region, values,
  reason and visual effect, as a step-on, step-off comparison over the canvas. AI-stage steps
  come from the `steps` answer the server already has. Manual changes come from a log the host
  keeps of which controls changed at each save.
- **Try it yourself** uses a temporary copy of the chosen AI-stage step. **Apply practice edit**
  explicitly accepts it. Closing practice restores the current edit.
- Distinguish locally measured reasons, manual edits and Gemini suggestions in the history.

### M7. Specialized styles

**Blue hour**, **Sunset glow**, **Moody landscape** and **Natural portrait** join Standard one at
a time, each when F59 has built it and F60's loop and the owner's blind vote have passed it. Give
each local style a tested look, relevant region choices and data-driven rules.

## As built

Another tool built the plan in one pass on 2026-11-24. The task list of the first version named
files for the LightCraft-first design, and most of them were never needed.

| Milestone | What exists | Where |
| --- | --- | --- |
| M0 | The eight answers with numbers. All passed except the time from Run AI Edit to the picture in LightCraft, which decided D1. | [F58-probe.md](F58-probe.md) |
| M1. Open | One import decision on the server. A RAW file, also one inside a TIFF container, opens through AutoEdit's decoder with the RAW choices of Settings. One list of file types for both pickers. Working pictures live in a cache capped at 4 GB. A dropped file is kept as a local copy until its original is linked. | `web/editor_files.py`, `web/editor_routes.py`, `imageio.py` |
| M2. Record and export | The manual stage of a photo is a file beside the project (2 MB at most, checked by its checksum, written atomically), with a pointer in the project and undo across both stages. A full-size export needs a receipt from AutoEdit's own render. The sidecar has a `manual` block. | `web/manual_stage.py`, `web/projects.py` |
| M3. Run AI Edit | Photo editor and Automatic edit run the local pipeline. **Ask Gemini** is separate and optional. A **Before / After** divider, also by keyboard. | `static/app/photo.js`, `static/app/modes.js`, `web/jobs.py` |
| M4. Sidebar | Five sections on AutoEdit's own page: Adjust, AI Edit, Isolate, Learn, Ask Gemini. One is open at a time, and a narrow screen gets a drawer. Adjust holds crop and straighten and the way into Fine-tune. LightCraft's sliders were not rebuilt as page controls. | `static/app/photo.js`, `static/app/editor_sidebar.js` |
| M5. Isolate | On AutoEdit's canvas, with its masks, brush and sliders: the fallback of M5. The probe showed that LightCraft accepts a SAM 3 mask (overlap 0.994), and the build does not use that. | `static/app/photo.js`, `static/app/photo_tools.js` |
| M6. Learn | **What changed**, step by step, and practice on a temporary copy. | `static/app/photo.js` |
| Fine-tune | LightCraft on the current stage, with its settings restored from the project, **Inspect at 100%**, and export through AutoEdit. | `static/app/editor_host.js`, `static/app/editor_native.js` |
| M7. Styles | Standard only. No specialized style has passed ([F59](F59-local-styles.md)). | |

## Release checks

1. Open a CR2, choose Standard, drag Before / After, isolate the sky, change it, see that change
   in Learn, then undo and export. Repeat offline in the desktop window and a browser.
2. Compare edits from Automatic and Photo editor with identical local settings. Check JPEG, TIFF
   and CR2, an older saved project, and a mixed batch containing an unreadable file.
3. On differently lit images, check that specialized local styles make appropriate adjustments,
   that a region edit stays inside its feathered mask, and that highlights, skin and shadow
   detail survive export. Use the bridge reference from F57 as one comparison and reserve a
   different shoot for judging the result. F60 is the standing form of this check.
4. Run the existing full suite, stub batch export and RAW tone report. Check the sidebar and
   divider with a mouse, keyboard and touch. The primary workflow should be discoverable from the
   editor without opening Guided or entering a Gemini key.
5. Clear the browser's storage and reopen a photo: its edit comes back from the project.
6. Change the AI stage twenty times on one photo: browser storage grows by one working picture.
7. `python scripts/check_public_repo.py` passes, and default pictures from
   `autoedit run ./test-corpus ./out --backend stub` are byte-identical to the commit before.

## Left out, and what would bring each back

| Left out | Bring it back when |
| --- | --- |
| AI-stage changes written as LightCraft sliders | A check shows the same picture within 1 of 255 for that control |
| LightCraft's library features (ratings, albums) inside the editor | Not planned; they are in LightCraft itself |
| Exporting a manual stage from Automatic or the command line | The owner installs LightCraft's command-line renderer and wants it |
| Curves, a colour mixer and colour wheels in AutoEdit's own engine | D1 falls back to the Guided screen and the owner misses them; each is new op math with its own spec |
| Lightroom catalogue import, LightCraft's agent interface | Not planned |

## Implementation result · 24 November 2026

D1 took the Guided-primary path. The native handoff's warm component measurements totaled 4.70 seconds against the four-second budget. Three additional end-to-end warm runs took 8.68–11.96 seconds during concurrent local verification. The pinned release passed the native editing round trip, the 16-bit export check and the SAM sky-mask handoff. Photo editor consequently owns the sidebar, local render, original/current divider, selection and practice canvas. **Adjust → Fine-tune in LightCraft** opens the native toolset on the current stage. **Automatic edit** sends the selected local style to the same rules engine. Individual commands, sample dimensions, IoU and precision measurements are in the [F58 probe](F58-probe.md).

### Verification

The RAW brightness decision (D6) is still with the owner. The current Standard render was a median of 1.33 EV darker than the embedded camera JPEG on 110 local RAWs. The local gallery at `out-features/galleries/raw-mode/index.html` compares RAW development choices. This is a separate preference from the landscape style galleries.

- In Chrome the normal file-picker action opened each of the first 20 verified F60 inputs. All 20 showed the selected source in the original view, and all 20 used the Settings-aware neutral RAW decoder, including two older Canon cameras that wrote RAW sensor data inside TIFF containers. The repeatable picker fixture supplied the selected paths. The private run records dimensions and elapsed times in `out-webloop/app/opens.json`.
- On four web JPEGs and one Canon CR2, Photo editor, Automatic and the CLI exported identical JPEG bytes at the original developed dimensions. The SHA-256 values and file paths are in `out-webloop/app/parity.json`. JPEG sidecars differed only in run timings. The CR2 also recorded the editor’s explicit sky-selection request and corresponding configuration digest. Each JPEG had a working split and a selectable sky mask in the local stub smoke test. A practice change was discarded, then another was applied and reset before the parity export. This checks editing behavior on the stub and leaves the artistic SAM/owner evaluations to F59 and F60.
- A macOS pywebview window opened a Canon CR2 on the original, ran Standard, displayed the split, opened Isolate, showed 12 Learn steps and exported a JPEG. Another desktop pass moved the divider from 50 to 51 using the keyboard. A 700 × 400 source with an off-centre Guided crop and a native exposure edit produced a 490 × 200 export and sidecar.
- Fine-tune’s **Inspect at 100%** loaded all 3,870 × 5,796 source pixels of a Canon CR2. The native editor reported 100% zoom and retained the same manual-stage checksum and +0.45 EV exposure. Native export uploads are bounded by the authorized source dimensions, allowing a 100-megapixel 16-bit TIFF to exceed the ordinary file-drop limit.
- A browser drop stayed marked as a browser copy until the original was linked. A different file was rejected and the exact original was accepted. Cross-stage undo and redo persisted the corresponding manual-stage pointer, including the return to the original. The library surfaced **Finish in Photo editor** for a saved manual stage.

The primary editor gave both Canon TIFFs a 1,600-pixel neutral preview and exported the complete 2,496 × 1,662 and 4,082 × 2,718 developed frames. Fine-tune separately reopened the 4,082 × 2,718 source and a third Canon TIFF at 1,736 × 1,160. Inspect at 100%, an exposure adjustment of +0.15 EV and full native export preserved those dimensions. Source checksums matched across both tests. See the private `out-webloop/app/opens-canon-tiff.json` and `camera-tiff-native.json` records.

## Check of the build (2026-11-24)

A second session reviewed the commit against this plan, with an independent checker for the
server and page code and a second one for the fixes.

**What holds**

- Every new route sits behind the loopback host check, and every new POST behind the local-origin
  check. A file path reaches code only after the folder check and the image check.
- The size limit of an export upload comes from the server's own full-size render, never from the
  page. The upload is then checked again: one page, 16 bits, three channels, the saved crop.
- A manual stage is written atomically, capped at 2 MB and verified by its checksum when read.
  Two photos with the same file stem get separate folders.
- With no key and **Ask Gemini** unused, nothing leaves the computer. Automatic calls Gemini only
  when three settings are given together, and no page sends them.
- Messages from the embedded editor are checked for their source and origin. No file name is
  inserted as HTML.
- A walk through the app on a scratch profile: a JPEG and a Canon CR2 opened on the original,
  **Run AI Edit**, the divider by keyboard, Isolate, Learn with 12 steps, Export. The CR2's export
  at 5,796 × 3,870 was byte-identical to the command line's for the same settings, the originals
  kept their checksums, and the console stayed empty.

**Fixed after the check**

- `autoedit adjust` and `autoedit sync` refuse a photo that has a manual stage. Before, they
  wrote the export again without it.
- A photo's own **Hide** reaches every picture made for Fine-tune. The server adds it now, so it
  no longer depends on what the page sends. Before, a photo whose only edit was Hide went to
  LightCraft uncovered.
- A single export never replaces a file AutoEdit did not write, and never another photo's export.
  Before, an export into the photo's own folder could replace an original with the same stem.
- A run of saves in Fine-tune is one undo step. Before, a minute of slider work pushed the AI
  stage's own steps out of the history of 50.
- A missing or damaged manual-stage file no longer leaves the photo stuck. The pointer is
  dropped, Fine-tune says so, and the photo opens and exports again.
- Every retouched spot is recorded. Before, a spot without a named tool was left out, and the
  edit declaration then kept its plain statement.
- **Export** on a photo with no edit shows a notice. Before, it jumped into Fine-tune.
- One failed request while LightCraft starts no longer leaves **Open** disabled.

**Open**

- The decisions D4 (remove and fill), D6 and D12.
- A slow RAW decode holds one lock, so the picture of another photo waits for it. An upload is
  written inside the request handler.
- The manual-stage files of photos that left a project are never deleted, and temporary folders a
  crash leaves in the cache are not pruned.
- An export is matched to its photo by the source's file name. Two photos of the same name from
  different folders, exported into one shared folder, still replace each other.
- No page test covers opening and exporting in Fine-tune. Inside the test suite nothing compares
  the editor's export with Automatic's; that was checked by hand and in the builder's records.
- Not checked: LightCraft's own behaviour in Fine-tune, touch, and a screen reader.
