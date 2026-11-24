# AI editing and photographer references

## Editing workflows

1. On the start screen, offer an automatic export and an editor with adjustable AI changes. Both open on the original and wait for an explicit edit.
2. Give both workflows the same Standard, Sunset glow, Blue hour, Moody landscape and Natural portrait choices. Each describes a photographic intention. Gemini chooses adjustments for the current image using the controls available in that workflow.
3. In Automatic, request permission for the selected photos, develop each locally, and let Gemini refine each result. Record which images received AI adjustments and which used local editing. In the editor, let the photographer inspect, apply and undo the proposed slider changes.
4. Exercise both workflows with a controlled provider response and verify offline export, failure handling, and the existing editing pipeline.

## Evaluating the look

Start with blue hour. In the bridge example the lit masonry gains detail, the lamps stay warm and the sky and water stay blue. Inspect those areas separately at the same crop. In the snowy sunset, judge the sky color and the shadow color of the snow separately. In the lake image, inspect the mountain silhouette for halos and the water for retained texture.

Collect original RAW files and finished edits of the same frames from a photographer who wants to teach the app their style. Keep other frames from the same shoot together when dividing the collection into development and evaluation sets. Record a short intention for each pair, such as “open the arch but keep the lamps warm.” Use the screenshots as visual briefs and the aligned original/export pairs to measure improvements.

### First comparison

These measurements compare crops of the supplied bridge screenshot. Brightness is the mean Rec. 709 weighted RGB value on a 0–255 encoded scale. The local reference-match run received the finished half as its reference. The separately directed run used SAM 3 sky, water and bridge masks and hand-picked slider values. Neither experiment made a cloud call.

| Region | Original | Local rules (stub) | Local reference match (stub) | Directed local edit (SAM 3) | Photographer's reference |
| --- | ---: | ---: | ---: | ---: | ---: |
| Sky | 107 | 127 | 152 | 153 | 164 |
| Water | 49 | 59 | 79 | 85 | 81 |
| Bridge masonry | 81 | 97 | 106 | 101 | 102 |

Lifting sky and water separately leaves room to keep warm masonry at its target brightness. The directed edit used +0.60 EV and −10 saturation on the sky, +0.45 EV and +12 shadows on the water, and +5 temperature on the bridge. These values describe one frame. The next experiment is to use a different finished blue-hour image as the reference and have the photographer judge the result on a new shoot.

On held-out shoots, compare the local edit, AI edit and photographer's edit anonymously at the same crop and size. Record preferences and the first correction the photographer wants to make. Inspect highlight clipping, noise in lifted shadows, color casts and mask edges on the full-size exports. Prioritize the repeated correction, then run the same comparison again. AutoEdit already includes a tone report, a blind-comparison script and a taste-calibration workflow. Direct comparison with a photographer's delivery also requires their reference export.
