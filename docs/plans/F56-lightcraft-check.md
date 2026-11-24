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
