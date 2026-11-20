# F39. Masks that reach the frame edge

Status: spec (2026-11-18). Size S. Off by default.

## Goal

SAM 3 masks stop 1 to 12 proxy pixels short of the frame edge on most photos
([F2 result](F2-region-instructions.md)), so a region edit leaves a thin unedited rim along the
border. With `snap_edges` on, a region mask that comes close to an edge is extended to it.

## Design

`edit/mask.py`:

```python
def snap_to_edges(mask: np.ndarray, band: int) -> np.ndarray:
    """A new bool mask: for every column (top and bottom edge) and every row (left and right
    edge), when the mask has a True pixel within `band` pixels of that edge, the gap between
    the edge and the first True pixel is filled. The input is never changed. band <= 0
    returns the input unchanged."""
```

Top edge, column x: `r` = the first row in `[0, band)` where `mask[r, x]`; if there is one, set
`mask[0:r, x] = True`. The same for the bottom, left and right edges. Vectorised with numpy
(`argmax` over the band), no Python loop over pixels.

`segment/resolve.py::resolve` gains `snap_px: int = 0`. When it is above 0, every present
concept's mask is replaced by `snap_to_edges(rm.mask, snap_px)` before arbitration, for the
union, the label map and the region alike. Masks from the cache are shared objects: never write
into `rm.mask`.

Config: `rules.toml` gains

```toml
[masks]
snap_edge_px = 12     # with snap_edges on: gaps this close to the frame edge (proxy pixels) are filled
```

`RunConfig.snap_edges: bool = False` (settings schema description in `configs/settings.toml`),
`--snap-edges` on `autoedit run`, wired as `--light-shaping` is (argument, override map, run-file
keys). The pipeline passes `snap_px` to `resolve` only when the setting is on. Shapes, glows and
area marks are not masks from SAM 3 and are untouched.

## Tests (`tests/test_edge_masks.py`)

1. `snap_to_edges` math: a square 3 px from the top edge is filled up to row 0 in its columns
   only; a mask 20 px from the edge with band 12 is unchanged; the input array is unchanged;
   band 0 returns an equal array.
2. All four edges, and a corner.
3. `resolve` with `snap_px` fills the gap in the region, the union and the label map; with
   `snap_px = 0` the result is identical to today.
4. Default output: a stub run with the setting off is byte-identical to one before the change
   (compare two runs of the same code with and without the flag: with it off nothing differs).

## Acceptance (the session)

- Both suites; stub smoke run; default renders byte-identical.
- On cached SAM 3 masks of the 110 landscape photos: the share of masks that touch the edge
  before and after, and the largest number of pixels added to one mask.
- Tone gate with the setting on: 0 and 0.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Default | Off (`--snap-edges`) | `snap_edges` in `defaults.toml` after a look at the crops |
| Band | 12 proxy pixels, the largest gap measured in F2 | `[masks] snap_edge_px` |

## Result

Built on 2026-11-18 by Sonnet Builders, reviewed by the Checker (Opus, high). The Checker's corner bug (the left and right passes read the unfilled mask) and its finding that instructions and marks saw unsnapped masks were fixed: the masks are now snapped once in the pipeline, right after segmentation, so regions, instruction cells and thing marks agree; `resolve` is unchanged. On the 326 cached SAM 3 masks of the 110 landscape photos, snapping adds a median of 0.12 % of the frame per mask (at most 0.76 %). Default output byte-identical; the tone gate was not run (no tone or decode change).
