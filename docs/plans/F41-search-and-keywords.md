# F41. Search by what is in the photo, and keywords

Status: spec (2026-11-18). Size M. Read-only over sidecars; no pixel changes.

## Goal

A run already knows what every photo holds: the scene, each concept with its score and coverage,
and (with F21's check) its reveal findings. Make that searchable, and write it as keywords that
other programs read.

## Design

New module `src/autoedit/search.py`. No model, no network.

```python
def index(folder: str) -> list[dict]:
    """One entry per *.autoedit.json under folder (recursive): {"sidecar", "image", "scene",
    "concepts": {name: coverage} for concepts with present = true, "reveal": [kind, ...]}.
    A sidecar that does not parse is skipped and counted, never raised."""

def find(entries, words: list[str], min_coverage: float) -> list[dict]:
    """Entries that match every word (AND). A word matches the scene, a present concept whose
    coverage is at least min_coverage, or a reveal kind or its label. Case-insensitive; a word
    also matches a concept name that contains it as a whole word ("lake" matches "lake",
    "mountain lake"). Ordered by the summed coverage of the matched concepts, largest first."""

def keywords(entry, min_coverage: float) -> list[str]:
    """The scene and the present concepts at or above min_coverage, sorted, deduplicated.
    Never a reveal kind: a keyword "child" or "face" would travel with the photo."""

def write_xmp(image_path: str, words: list[str]) -> str:
    """Write <image stem>.xmp beside the image with dc:subject as an rdf:Bag. Escape every word.
    Does not touch the image. Returns the path."""
```

Reuse the XMP text pattern of `curate.py::_write_xmp_sidecar` (it writes xmp:Rating and
xmp:Label); factor the shared envelope into one helper if that keeps both short.

Commands (`cli.py`, registered as `merge` is):

- `autoedit find FOLDER WORD [WORD ...] [--min-coverage 0.02] [--json]`: prints one image path per
  line (the exported image named in the sidecar, or the sidecar when the image is missing), then a
  count line "3 of 212 photos". `--json` prints the matching entries. Exit 0 with matches, 1
  without (like grep).
- `autoedit keywords FOLDER [--min-coverage 0.02] [--dry-run]`: writes one `.xmp` per exported
  image with its keywords; `--dry-run` prints them. Refuses to overwrite an existing `.xmp` that
  it did not write (one without its own `xmp:CreatorTool` value "AutoEdit"); says so and skips it.

Config: `--min-coverage` default from `configs/rules.toml`:

```toml
[search]
min_coverage = 0.02   # a concept counts for search and keywords from this share of the frame
```

## Tests (`tests/test_search.py`)

1. `index` over a folder with three hand-written sidecars (and one broken file): three entries,
   one skipped.
2. `find`: AND across words; the scene matches; a concept under `min_coverage` does not; a reveal
   kind matches; whole-word containment ("lake" vs "flake"); ordering by coverage.
3. `keywords` never includes a reveal kind.
4. `write_xmp`: the file parses as XML; a word with `<&"` is escaped; dc:subject holds the words.
5. CLI: `find` exit codes and output; `keywords --dry-run` writes nothing; a foreign `.xmp` is
   not overwritten.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Keywords go into a sidecar `.xmp`, not into the image | No exported file is rewritten | embed into the JPEG in a later step |
| Reveal findings are searchable but never keywords | Keywords travel with the photo | `keywords()` |
| No image description draft | Needs a language model; out of scope | a later feature |

## Result

Built on 2026-11-18. After the Checker: keywords also leave out every reveal kind's name, label and words (a scene called `people` included) and the engine's guard concepts (`[search] exclude = ["skin", "subject"]`); a sidecar of a dry run has no image, so no `.xmp` is ever written beside a source photo; an `.xmp` that AutoEdit did not write is never overwritten.
