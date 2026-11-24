# F56 L0 — LightCraft feasibility check

**Status on 2026-11-23.** The machine has no discoverable LightCraft desktop app, headless CLI,
or Rust toolchain in its command path or usual application and toolchain locations. The owner
chose to handle installation. The five-photo comparison is pending that step.

The upstream source revision identified for comparison is
`5a29fe602b5a2680d6771890a43380cd6bc34558`. If the owner supplies a binary release, record
its actual version, checksum, and corresponding source revision alongside the measurements.

| Question | Evidence needed | Status |
| --- | --- | --- |
| How does this camera's RAW decode compare? | Open copies of the same five RAWs in both engines, confirm sensor decode, record median luminance, highlight/shadow clipping and seconds per photo under the same output conditions. | Unmeasured |
| Can the headless editor round-trip a named edit on a TIFF? | Apply a specified adjustment to a known 16-bit TIFF, reopen the export, check actual sample depth, dimensions, color profile and changed pixels. | Unmeasured |
| What survives a manual finish? | Compare an AutoEdit export and sidecar to the file saved from the LightCraft desktop UI. Check visible edits, masks, editability, metadata and profile separately. | Unmeasured |

The upstream NOTICE distinguishes the general MIT/Apache choice from the Apache-only segmentation
port, separately licensed weights, asset credits, and restricted ArtCraft branding.
[LightCraft NOTICE at the evaluation revision](https://github.com/storytold/lightcraft/blob/5a29fe602b5a2680d6771890a43380cd6bc34558/NOTICE)

**Subsequent editor work (2026-11-24).** Automatic and Guided use the AutoEdit renderer.
Photo editor runs the official LightCraft v0.21.1 web release locally. Guided can open the
original or a saved, flattened export there. Photo editor keeps its own editable library.
The five-RAW desktop measurements above are still pending.

## Check of the editor work (2026-11-24)

A second session reviewed the Automatic and Photo editor work on a frozen copy of its commit.

What holds: the full test suite passes, default pictures are byte-identical to the commit before,
and no protected file changed. The pinned checksum equals the one GitHub publishes for
`lightcraft-web-0.21.1.zip` (74,569,049 bytes). Only a fixed list of files is unpacked and served.
A photo outside an opened folder is refused, and so is a request from another site or host name.

Fixed after the check:

- A photo marked for review or as failed, and every photo in the review queue, opens on its edit
  again. Other photos still open on the original.
- Automatic follows what Settings shows, such as the region finder and the export format. It had
  always asked for the automatic region finder, which loads SAM 3 when that is installed.
- Photo editor asks before it downloads LightCraft and names the size. Like the SAM 3 weights, the
  download never starts by itself.

Open, for the owner to decide:

- **Exports from Photo editor are outside AutoEdit.** They are saved as a browser download, with
  no sidecar and outside `AutoEdit Exports`. The `metadata = "shareable"` default, which drops GPS
  and serial numbers, does not reach them. What LightCraft writes into its files was not tested.
- **Edits made in Photo editor live in browser storage.** LightCraft calls its browser version
  experimental and warns that the browser may delete the library. Each opened original is copied
  there; one 22-megapixel RAW opened as TIFF was 135 MB.
- **RAW files.** The host now recovers from "LightCraft stopped" after a RAW it cannot decode.
  That is the first question of the table above, and it is still unmeasured. **Open RAW as TIFF**
  needs `tifffile` (the `accel` extra); without it the button answers with an install hint, and
  its test is skipped.
- **The plan and the build disagree.** The F56 decisions say "a one-day check", "no LightCraft code
  in `src/`" and "no new top-level areas", and [UI-minimal.md](UI-minimal.md) says "never a
  sidebar of modes". This work has no feature number, spec or row in the feature plan.

Not checked: Compare, Ask Gemini, Masking and Export inside the embedded editor, and the desktop
window.

**Taken up on 2026-11-24.** [F58](F58-editor-sidebar-and-cr2.md) turns the four open points into
work: exports and browser storage in its M2, RAW files in its M1, and the disagreement between
plan and build in its decision D7. The feature plan has rows for F57 and F58 since that day.

**Built on 2026-11-24.** F58 was built and checked the same day. A Fine-tune export goes through
AutoEdit with its sidecar and the metadata rule, the project keeps the manual edit, and a RAW file
opens through AutoEdit's decoder. The five-RAW desktop measurements of the table above are still
unmeasured. The browser probe of [F58](F58-probe.md) answered the questions the editor depends on.
