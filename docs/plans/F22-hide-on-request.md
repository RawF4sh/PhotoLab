# F22. Hide on request

Status: built (2026-11-22), off by default. Size M. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md),
section F22. It builds on the findings of the reveal check ([F21](F21-reveal-check.md)).

## The owner's decision (2026-11-22)

The plan said AutoEdit must never change content. Hiding changes what can be seen, so the owner
decided the rule:

> AutoEdit never invents content: nothing is generated, replaced or filled in. Hiding a region is
> allowed when the user asks for it, and is always reported.

Nothing is hidden unless the user names a kind. Every hidden region is listed in the sidecar.

## What was built

- **Ask.** `--hide KIND[,KIND]` (the kinds of `reveal.toml`: `plate`, `face`, `screen` and so on),
  the `hide` run setting (a list; the settings sheet takes it as text), and `--hide-method` /
  `hide_method` (`cover`, `mosaic`, `blur`). A kind that the check does not look for stops the
  run, so a typo never looks like a hidden photo. Asking to hide turns the reveal check on.
- **Where.** `hide.py`, called at the end of `apply` in `pipeline.py`, after the banding verdict
  and before the post-edit checks read the result: the last pixel step, on the full-size picture.
  The stage order is unchanged. The source file is never written; the in-memory picture is
  copied first. Grain, the export resize and the watermark come later, on the export, as before.
- **Which pixels.** Every finding of the named kinds, also those past `max_findings`
  ("and 12 more"). The mask is the finding's own mask (the connected area, words of one kind
  merged), grown by a margin: `margin` of the region's shorter side, at least `margin_min_px`
  pixels of the analysis picture, so the rough edge of a proxy mask is covered. The edge is hard:
  no pixel outside the grown mask changes, and nothing is feathered inward. The growth is a
  square, not a disc (a plate is a rectangle; a blob is hidden a little more generously).
- **Methods, as data in `reveal.toml` `[hide]`.** `method` is the default.
  - `cover`: one flat fill in the mean colour of a band (`ring`, a share of the shorter side)
    just outside the grown mask, leaving out every other hidden area. Nothing under the
    region is copied into the fill: at the hide step, two pictures that differ only under the
    grown mask give identical pixels. In a whole run the region still counts toward the
    whole-photo exposure and colour measurement (its average can shift the edit slightly), so two
    exports can differ by a few levels, up to about 8 of 255 in a test. A region that is the whole
    frame gets mid grey.
  - `mosaic`: `blocks` across the shorter side, so a bigger region gets bigger blocks.
  - `blur`: radius `sigma` times the shorter side, so a bigger region gets a stronger blur.
  - Mosaic and blur keep a trace of what was there (the sidecar says `trace: true`, the app says
    so): a plate or text can sometimes be read back. Cover copies nothing from under the region.
- **Report.** `reveal.hidden` in the sidecar lists every hidden region: kind, label, the box that
  was hidden (margin included, shares of the frame), method, trace. The notes say what was hidden
  and by which method. When a request hid nothing, `reveal.hide_note` and a note say why (the stub
  cannot find named things; none of that kind was found). `run.json` has `reveal.hidden` (regions,
  photos, per kind) and `hide_asked`; the last line of a run says it. A resumed run that skips
  photos made without the hide says how many (`not_hidden`) and how to redo them.
- **The app.** With "Check before sharing" on, the photo screen lists the findings by kind with a
  Hide / Show again button for each kind, a "How to hide" choice, and the sentence about the trace
  of mosaic and blur. Hidden regions are listed under it. A kind hidden by the Settings list shows
  a disabled button.
- **No second copy.** The export copies a fixed list of Exif tags and no preview picture. A test
  puts the camera's preview inside a source file and checks that the export has one JPEG and no
  second directory.

## Tests

`tests/test_hide.py`: at the hide step `cover` gives identical pixels for two different pictures under the same
mask; pixels outside the grown mask are byte-identical to a run without hiding (function level and
a lossless run); the margin, mosaic and blur scale with the region and keep a trace; the config is
checked; an unknown kind stops the run; the stub reports that nothing was hidden; the default
output (hide off) is byte-identical; the export holds no embedded preview; the preview request
shows the hidden picture and the report.

## Review fixes (2026-11-22)

- A kind the user asked to hide skips the `min_side` filter, so a thin plate that is readable at
  full size is hidden (the report still lists only findings above `min_side`).
- The Folder's "Edit photos" sends each photo's Hide choices as `photo_hide` (path to
  `{hide, hide_method}`); the job validates them at the start, renders that photo with its own
  settings, and the sidecar records them (`run_config.hide`, `reveal.hidden`).
- A resumed run compares the hide method as well as the kinds.
- A dry run that found something to hide says that nothing was hidden because it was a dry run.

## Open

- A real photo at full size, with SAM 3, has not been looked at yet. The stub finds nothing, so
  this build was proven with a fake open-vocabulary finder.
- The Hide control hides a whole kind on the photo, not one finding. Hiding one finding of
  several needs a per-photo list of boxes; not built.
- Hiding sits before grain, so grain is laid over a hidden area like over the rest. It is seeded
  from the file name, not from the picture.

### Real photo at full size (2026-11-22)

One of the owner's labelled phone photos with a readable plate (4284 x 5712), `autoedit run --backend
sam3 --hide plate`: the check found plate, writing and code; one plate region was hidden with
`cover` and listed in the sidecar (box about 29 % by 14 % of the frame) and in the run's last line.
At full size the plate area is one flat fill with no trace of the characters; the grown edge follows
the mask's outline. Seen by the session, not committed (the photo stays outside the repo).
