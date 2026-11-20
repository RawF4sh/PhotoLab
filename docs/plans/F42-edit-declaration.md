# F42. Edit declaration

Status: spec (2026-11-18). Size M. Read-only over sidecars and files; no pixel changes.

## Goal

Photo contests and news desks limit editing. A run's sidecars already record every decision.
Turn them into a plain declaration per photo of what was changed, and prove which file it is
about with hashes. Signed Content Credentials (C2PA) are not part of this: they need a signing
certificate.

## Design

New module `src/autoedit/declare.py`. No model, no network.

```python
def declaration(sidecar_path: str) -> dict:
    """For one photo: source file name (no folder), export file name, SHA-256 of the source and
    of the export (None when a file is missing), the edits, and notes."""

def write(folder: str, out: str | None) -> tuple[str, str]:
    """declaration.html and declaration.json for every sidecar under folder; returns both paths."""
```

What "the edits" are, read from the `Result` in the sidecar (read `contract.py::Result` for the
exact fields):

- **Whole photo:** every global setting that is not zero/neutral, by its name and value
  ("exposure +0.30 EV", "contrast +8"). The neutral values are the ones `edit/ops.py::apply_ops({})`
  treats as no-ops.
- **Regions:** per region (concept or mark name), its coverage and its non-zero settings.
- **Shapes:** gradients, glows and area marks by name.
- **Geometry:** straighten angle and crop, when set.
- **Look, LUT, grain, black and white:** named when used.
- **Repairs:** listed separately and plainly when present: stuck-pixel repair (F31), noise
  reduction (F7). These change pixel values locally; the page says so.
- **Merged frames (F33):** if the sidecar records several sources, each is named and hashed.

The page states once, above the photos: "AutoEdit adjusts tone, colour, sharpness and geometry. It
has no generative tools: it does not add, remove or replace content." Nothing else is claimed.
Every name is escaped; no script, no outside resource, relative names only, no absolute path.

Command (`cli.py`, registered as `merge` is): `autoedit declare FOLDER [-o DIR]`, default output
the folder itself. Prints the two paths and "12 photos declared, 1 sidecar skipped".

## Tests (`tests/test_declare.py`)

1. A stub run of the test corpus, then `declaration()` on a sidecar: names, two hashes that equal
   `hashlib.sha256` of the files, and only non-neutral settings listed.
2. A run with an instruction on `sky` lists the sky region and its setting.
3. Repairs are listed under repairs (a hand-made sidecar with `hot_pixels` set).
4. The page: escapes a file name with `<script>`; holds no absolute path and no `http`; holds the
   statement.
5. CLI writes both files; a broken sidecar is skipped and counted.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| No signing | Needs a certificate and a check of how viewers treat a self-made one | a later feature (C2PA) |
| The wording of the statement | As above, limited to what the engine can do | `declare.py` |
| Merged frames (F33) are not declared | The sidecar does not point to the `.merge.json` | record the merge sources in the sidecar |

## Result

Built on 2026-11-18. After the Checker, the declaration also lists the other steps that change pixels and that the sidecar records: RAW decode exposure, consistency biases, neutral and backdrop (F34), reference colour transfer, palette, the highlight hold and the safety gates. Not recorded in a sidecar, so not declared: gate effects under 0.5 % of the frame, the content of a look beyond its name, the ICC conversion and the lossy encoding of the output format, and merged-frame sources (F33).
