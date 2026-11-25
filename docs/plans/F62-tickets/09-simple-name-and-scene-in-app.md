# 09. Simple in the interface, and the scene shown and correctable

Status: ready-for-agent
Blocked by: 01, 02
Spec: [F62](../F62-auto.md), milestone M3
Who: Builder (Sonnet, medium)

## What to build

- Standard becomes **Simple** wherever the user reads it: the app (`editor_assist.js`,
  `modes.js`, `photo_tools.js`, `photo.js` and others), `APP_GUIDE.md`, README, help text. The
  internal value `standard` stays, so saved projects still open.
- Auto is offered beside Simple. The photo shows its scene; a menu corrects it (passed as the scene
  override) and the photo is redone. An unreleased scene says Auto is not ready for it yet.

## Done when

- `grep -rn "'Standard'\|\"Standard\"" src/autoedit/web/static/app/` finds nothing the user reads.
- Browser check on the smoke corpus: pick Auto, see the scene, correct it, undo; a saved project from
  before opens with Simple.
- `PYTHONPATH=src python tests/run_all.py` is green and the tests stay pytest-collectable.
- `autoedit run ./test-corpus ./out --backend stub` completes, with images byte-identical to the
  previous commit (nothing chooses Auto by default).
- `python scripts/check_public_repo.py` passes. CHANGELOG and docs updated if behaviour changed.
- No git writes: the owner gets a commit block.

## Comments
