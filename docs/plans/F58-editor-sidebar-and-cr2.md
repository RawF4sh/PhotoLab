# F58 · One editor sidebar and reliable Canon RAW opening

Proposed on 2026-11-24. This plan supersedes the AI button names and default workflow in F57. **AI Edit** means AutoEdit’s built-in local photo pipeline. **Ask Gemini** is a separate, optional assistant. The on-device path must work with the network disconnected and no API key.

## What the photographer sees

The start screen offers **Automatic edit** for hands-off processing and **Photo editor** for work on one photo. Opening a photo shows its original. **Run AI Edit** explicitly begins the local edit. Both modes use the same Standard and specialized local styles.

| Part of the editor | Controls |
| --- | --- |
| Top bar | Library, filename, Export |
| Photo canvas | Original on open. After an edit, a small **Before / After** control switches the canvas to a draggable comparison. |
| Editing sidebar · **Adjust** | Existing LightCraft light, color, curves, detail, crop and manual controls |
| **AI Edit** | Standard by default, specialized local styles, optional reference photo, **Run AI Edit**, editable changes and Undo |
| **Isolate** | Pick or name an object, view its mask, refine with a brush, and adjust the selected area |
| **Learn** | **What changed** and **Try it yourself** for the active photo and edit |
| **Ask Gemini** | Optional cloud suggestions, preview-sharing permission and a review step |

Place the sections together in one editing sidebar. Opening a section should keep the photo and zoom in place. On narrow screens the same sections fit in a tools drawer. Keep help near actions that need it, such as a missing decoder or an undetected object.

## 1. Make Open Photo handle CR2

The current **Open photo** sends original bytes to LightCraft. **Open RAW as TIFF** separately uses AutoEdit’s local decoder. The macOS browser launcher and desktop window also use different file-picker filters. A picker rejecting a file, a decoder failing, and a preview-only import need different fixes. A repository Canon CR2 passed the current conversion endpoint and returned a full-resolution 16-bit RGB TIFF at 3,870 × 5,796. The exact failure on the reported file still needs reproduction.

- Trace an affected file through the macOS browser and desktop window. Record whether it can be selected, which decoder runs, the developed dimensions, and whether the editor can render and export it.
- Give Open, folder selection, drag-and-drop and reopen a shared RAW import decision. Recognize uppercase and lowercase extensions. Use the local full-resolution decoder for CR2s whose native editor support is unverified. Show **Opening RAW…** over the canvas and display the neutral original when ready. Keep the RAW linked as the source and identify a TIFF working copy in photo info.
- Reuse a valid working copy on reopen. Preserve an existing edit when reopening its source. Handle a missing decoder, a corrupt file, a cancelled import and insufficient storage without breaking the library or losing the previously selected photo.

**Acceptance.** Use an actual Canon CR2 through the ordinary Open Photo flow, adjust exposure, compare, export at the developed dimensions, close and reopen the same edit. Repeat in a mixed JPEG/CR2 folder, and verify the original file hash stays the same.

## 2. Give the sidebar one active photo and edit

Today the header’s **AI Edit** calls Gemini and **Local auto** runs LightCraft’s own auto-development. The Python AutoEdit pipeline, teaching views and named-object tools live in Guided. Moving the buttons alone would preserve the split between the two edit histories.

- Connect the original source, working rendition, AutoEdit project revision and LightCraft develop state to one active photo. For photos previously imported only into LightCraft, preserve their existing adjustments and offer to relink the original when a local operation needs it.
- Prove one edit end to end before moving all the panels. Run local Standard, inspect its actual settings and masks, change the result manually in LightCraft, then undo, save, reopen and export. Compare the exported pixels with the on-screen composite.
- Give each change one rendering owner. Use native controls for changes with verified equivalent rendering. For operations such as regional tone edits that cannot be expressed faithfully in native sliders, retain the editable AutoEdit step and its mask in the project. Render that stage from the original and reapply the saved native develop state above it. Verify a base update preserves crop, masks and manual history. Reject stale preview results when the photo or revision changes.
- Integrate the sections at LightCraft’s supported panel boundary if available. Its controls are drawn inside an embedded canvas. If panel insertion needs an upstream change, use a host-owned sidebar with one active tools panel and a single canvas. Test access to the complete manual control set.

## 3. Wire Standard, specialized edits and comparison

- Both **Run AI Edit** and the hands-off export call the local pipeline. Keep the optional Gemini call behind **Ask Gemini**. Preserve existing recipes, settings and in-progress projects when routing old links to the renamed screens.
- Start with **Standard**, **Blue hour**, **Sunset glow**, **Moody landscape** and **Natural portrait**. Give each local style a tested look, relevant region choices and data-driven rules. The Gemini briefs in F57 can describe an intention, but the local engine needs actual settings. Show which global and region changes were applied. Running the same style twice replaces its previous contribution.
- After the first edit, show **Before / After** beside the image. Start the split at 50%, label the two sides **Original** and **Current edit**, and support drag, touch and arrow keys. Match frame, crop, rotation and zoom across the two renders. While the original is untouched, show it without an empty comparison. Selecting an edit step can additionally compare that step on and off.

## 4. Restore Isolate and Learn on the same canvas

- **Isolate** reuses Guided’s named masks, selection overlay, add/subtract brush and region sliders. Offer a mask-only view so the photographer can check the selected pixels. Keep the selected object when switching panels. When an object is missing, offer a manual selection. Preserve the existing overlap resolution in previews and exports.
- **Learn** opens the saved changes for this image. Selecting a step shows its region, values, reason and visual effect. **Try it yourself** uses a temporary copy of the chosen step. **Apply practice edit** explicitly accepts it. Closing practice restores the current edit. Distinguish locally measured reasons, manual edits and Gemini suggestions in the history.

## Release checks

1. Open a CR2, choose Standard, drag Before / After, isolate the sky, change it, see that change in Learn, then undo and export. Repeat offline in the desktop window and a browser.
2. Compare edits from Automatic and Photo editor with identical local settings. Check JPEG, TIFF and CR2, an older saved project, and a mixed batch containing an unreadable file.
3. On differently lit images, check that specialized local styles make appropriate adjustments, that a region edit stays inside its feathered mask, and that highlights, skin and shadow detail survive export. Use the bridge reference from F57 as one comparison and reserve a different shoot for judging the result.
4. Run the existing full suite, stub batch export and RAW tone report. Check the sidebar and divider with a mouse, keyboard and touch. The primary workflow should be discoverable from the editor without opening Guided or entering a Gemini key.
