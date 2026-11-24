# F58 pinned editor probe

Chrome on macOS, 24 November 2026. LightCraft Web v0.21.1, tag commit `361e3bc0c838ffe5e5cea515b1fe5b71951989c6`, release ZIP SHA-256 `57147383a46eaab3b0cc036acf9443b221450c6a19f5dc303ae165bb9678b8cb`. The probe used same-origin scratch profiles, a visible browser for interaction, and a second Chromium instance for repeatable pixel and timing measurements. All renders and measurements ran locally.

**Decision D1.** Use the Guided canvas for Photo editor. The local pipeline and pinned native browser handoff together exceed the four-second warm budget. Keep the full native toolset available through **Adjust → Fine-tune in LightCraft**. A successful isolated probe for a native feature below does not imply that Photo editor uses it in the Guided canvas.

| Probe | Observed result |
| --- | --- |
| P1 · Browser commands | Tested the calls below by dispatching them through the installed release. The browser bridge rejects enumeration through `app.commands`, `engine.commands` and `ui.commands`. The list below covers the exercised commands. |
| P2 · Reopen a manual edit | A 1,536 × 907 JPEG with exposure +0.5 EV, a blue-channel mixer adjustment, three master-curve points, a 3° rotation, an 80% crop, a brush mask at +0.4 EV and a linear mask at −0.3 EV survived `develop.get` → second import → `develop.merge`. Both 1,229 × 726 cropped TIFFs matched exactly in every 16-bit channel. |
| P3 · Replace the underlying picture | Reimported 20 changed, equally oriented 2,560 × 1,709 TIFFs with distinct catalog identities. After restoring development each retained the same crop rectangle, angle, two masks, 110% zoom and normalized pan. The identical-pixels P2 test also covers the rendered brush and gradient. The test used new asset identities and did not establish in-place replacement or equivalent placement across orientation changes. |
| P4 · Full-size native export | The parent received a `lightcraft:save` message containing TIFF bytes and a filename, without a native picker. The export was RGB, 16 bits per channel with a 584-byte ICC profile, at the cropped source dimensions. A synthetic JPEG export had orientation and XMP tags and no GPS tag. AutoEdit must apply its own export metadata policy to camera originals. |
| P5 · Stored object mask | Transferred the cached SAM 3 sky mask from AutoEdit's F36 corpus (1,536 × 1,026, 39.6% coverage). Encoded a 256-square grid of signed logits as raw DEFLATE and base64, then applied +1 EV to `kind: "object"`. On a uniform 640 × 428 test image, thresholding the difference between masked and neutral renders at half the maximum gave **0.99368 IoU** against the resized original mask. The uniform image makes the comparison independent of the scene's brightness. |
| P6 · Sidebar, zoom and pan | `ui.dock({operation:"close",panel:"tools"})` placed the native tools panel in the hidden list. Reopening it removed it from that list. `panel:"edit"` was rejected. Shrinking the browser frame by 320 px and reapplying the recorded navigation returned 110% zoom at normalized pan [0.400000006, 0.600000024]. |
| P7 · Warm time and storage | On a Canon CR2, 19 warm local renders had a median of 2.97 s (2.94–4.08 s), including TIFF encoding. Handoff of a 2,560-pixel working image, restoring settings and waiting for native rendering had a median of 1.726 s (1.707–1.976 s). The sum of the component medians was **4.70 s**. An additional end-to-end run against the final working-picture endpoint opened a Canon CR2, obtained the local stage, imported its bytes, merged the saved development and waited for native rendering. The cold run was 19.40 s and three subsequent changed stages took **8.76, 11.96 and 8.68 s**. The stem-to-stern samples include contention from other local verification jobs. After 20 replacements, exclusively owned obsolete imports were deleted and the retained original-file store grew by **26,250,512 bytes**, one working picture. This measured original-file storage, not total browser quota. |
| P8 · Working format | At +2 EV an exported neutral ramp contained **2,048 distinct values** from either 16-bit TIFF input. The 8-bit version retained **31** across the same shadow interval. The uncompressed ramp occupied 786,704 bytes, the DEFLATE 16-bit version 18,700 bytes, and the uncompressed 8-bit version 393,488 bytes. Lossless compressed 16-bit TIFF retained all tested levels. Natural photographs have different compression ratios. |

## Exercised command vocabulary

The production **Inspect at 100%** action was also exercised on a Canon CR2. The native
editor switched from a 1,709 × 2,560 working picture to 3,870 × 5,796, reported 100% zoom
and retained the existing manual exposure of +0.45 EV and the same saved-stage checksum.

Two Canon sensor files with a TIFF extension originally opened at 288 × 192 and 156 × 104.
After content-based RAW detection, both passed Fine-tune, 100% inspection, a manual exposure
change and full-size JPEG export with a sidecar. The resulting files measured 4,082 × 2,718
and 1,736 × 1,160 respectively. Both source checksums matched their values before the test.

- `develop.get()`, `develop.set({values:{"light.exposure":0.5,"mixer.blue.lum":8}})` and `develop.merge({settings})`. The saved document includes `crop.geometry`, `curve.master` and `masks`.
- `mask.add({kind:"brush"})`, `mask.brushStroke({points:[[0.2,0.2],[0.3,0.3]],size:0.08})`, `mask.add({kind:"linear"})`, `mask.add({kind:"object",seg:{side,data}})` and `mask.adjust({values:{exposure:0.4}})`.
- `photo.inspect()`, `view.navigate({zoom:{percent:110},pan:[0.4,0.6]})`, `view.detail()`, `web.stats()`, `ui.dock({operation:"close",panel:"tools"})` and the matching `open`.
- `app.export({path:"proof.tif",format:"tiff",bitDepth:16})` delivers the rendered file to the host. Asset import uses the parent's same-origin `lightcraft:open` message with a filename and transferable bytes.
- In the scratch profile, `photo.delete({ids:[id]})` and `photo.deletePermanently({ids:[id]})` removed known generated entries. Original-file cleanup additionally required releasing the corresponding content hash. Never remove an original still referenced by a live catalog asset.

The corresponding native sources and release are [LightCraft v0.21.1](https://github.com/storytold/lightcraft/tree/361e3bc0c838ffe5e5cea515b1fe5b71951989c6).
