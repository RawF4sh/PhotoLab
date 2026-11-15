# F14. Model manager

Status: spec (2026-11-11). Size M. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F14.

## Goal

`autoedit models status | download | remove` replaces the manual steps around the SAM 3 weights:
it says what is installed and what is missing, downloads with progress and resume, and frees the
disk space again. Accepting Meta's licence and logging in to Hugging Face stay with the user; the
command says exactly what to do when they are missing.

## What an unattended run does and does not do

- It builds and tests the three commands against a stand-in for `huggingface_hub`.
- **It downloads nothing and removes nothing.** `autoedit models status` is run for real (it only
  reads the local cache). `download` and `remove` are not run on this machine.
- **It does not evaluate a second segmenter.** That needs a model download, which is the owner's
  step. The plug point is documented, and a short list of candidates with what must be verified
  is written down for the owner.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Gated SAM 3 flow, or a second segmenter? | Not decided here. The gated flow is made comfortable; the alternative is described, not built | The owner's call (see the plan's decision table) |
| May `remove` delete without asking? | No. It prints what it would free and needs `--yes` | Nothing to reverse |

## Design

New module `src/autoedit/models.py`. Every `huggingface_hub` import is inside a function, so the
core install is not affected.

```python
"""Local model management for the SAM 3 backend (F14): what is installed, download, remove."""

from __future__ import annotations

import importlib.util

SAM3_REPO = "facebook/sam3"
LICENCE_URL = "https://huggingface.co/facebook/sam3"


def _hub():
    try:
        import huggingface_hub
    except Exception as exc:
        raise RuntimeError("the SAM 3 backend is not installed: pip install '.[sam]'") from exc
    return huggingface_hub


def status(repo: str = SAM3_REPO) -> dict:
    """What the SAM 3 backend has on this machine. Reads the local cache only: no network."""
    out = {"repo": repo, "licence_url": LICENCE_URL,
           "torch": importlib.util.find_spec("torch") is not None,
           "transformers": importlib.util.find_spec("transformers") is not None,
           "hub": importlib.util.find_spec("huggingface_hub") is not None,
           "cached": False, "size_bytes": 0, "revisions": 0, "path": ""}
    if not out["hub"]:
        return out
    try:
        for entry in _hub().scan_cache_dir().repos:
            if entry.repo_id == repo and entry.repo_type == "model":
                out.update(cached=True, size_bytes=int(entry.size_on_disk),
                           revisions=len(entry.revisions), path=str(entry.repo_path))
    except Exception:
        pass
    return out


def download(repo: str = SAM3_REPO) -> str:
    """Download the weights into the Hugging Face cache (resumes a partial download, shows the
    hub's own progress bars). Returns the local path. A gated or unauthorised repository raises
    PermissionError with the steps the user has to take."""
    hub = _hub()
    try:
        return str(hub.snapshot_download(repo_id=repo))
    except Exception as exc:
        name = type(exc).__name__
        if name in ("GatedRepoError", "RepositoryNotFoundError", "LocalTokenNotFoundError") \
                or "401" in str(exc) or "403" in str(exc):
            raise PermissionError(
                f"Hugging Face refused the download of {repo}. The weights are gated: accept the "
                f"licence at {LICENCE_URL} (Meta's SAM licence; read it before commercial use), "
                f"then run `huggingface-cli login`, then run this command again.") from exc
        raise


def remove(repo: str = SAM3_REPO) -> int:
    """Delete every cached revision of the weights. Returns the bytes freed (0 if none)."""
    hub = _hub()
    for entry in hub.scan_cache_dir().repos:
        if entry.repo_id == repo and entry.repo_type == "model":
            hashes = [rev.commit_hash for rev in entry.revisions]
            hub.scan_cache_dir().delete_revisions(*hashes).execute()
            return int(entry.size_on_disk)
    return 0
```

CLI (`cli.py`): a `models` command with three sub-commands.

- `autoedit models status`: prints the dict as lines (`weights: in the local cache, 3.4 GB` or
  `weights: not downloaded`, the packages, the licence URL, and what `backend = "auto"` would use
  when F8's `resolve_backend` exists). Exit code 0.
- `autoedit models download`: prints the licence reminder first, then downloads. A
  `PermissionError` prints its message and returns 2; `RuntimeError` (packages missing) too.
- `autoedit models remove [--yes]`: without `--yes` it prints the size it would free and returns
  1; with it, it deletes and prints the size freed.

`autoedit doctor` points to `autoedit models status` in its segmentation block.

## The plug point for another segmenter

A new section in `ARCHITECTURE.md` ("Adding a segmenter"), written by the session:

- the protocol (`segment/base.py::Segmenter`: `segment(img, concepts) -> list[RegionMask]`, one
  union mask per concept at proxy resolution, with a score);
- where a backend is registered (`segment/__init__.py::make_segmenter`, `RunConfig.backend`,
  `--backend`), and that heavy imports stay inside the backend's own file;
- what the rest of the pipeline needs from it: text prompts for arbitrary concepts, a score that
  `min_score` in `concepts.toml` can gate on, masks that reach the frame edge;
- candidates to evaluate, each with the two things to verify before any use: the licence of the
  code *and* of the weights, and whether it takes free-text prompts. The session lists them from
  public documentation it reads at that time and marks every licence statement as "verify".

## Files

`src/autoedit/models.py` (new), `src/autoedit/cli.py`, `tests/test_models.py` (new),
`ARCHITECTURE.md` (the session).

## Task

One Builder (Sonnet, medium). Proving command:
`PYTHONPATH=src python3 -m pytest tests/test_models.py tests/test_cli.py -q`, then
`PYTHONPATH=src python3 tests/run_all.py`. **No test and no command of the Builder may contact
the network or touch the real Hugging Face cache**: every test replaces `models._hub` (restored
in `finally`) with a stand-in.

## Tests: `tests/test_models.py`

The stand-in hub: an object with `scan_cache_dir()` returning an object with `.repos` (a list of
simple objects with `repo_id`, `repo_type`, `size_on_disk`, `revisions`, `repo_path`) and
`delete_revisions(*hashes)` returning an object whose `execute()` records the hashes; and
`snapshot_download(repo_id=...)`.

| # | Test | Asserts |
|---|---|---|
| 1 | `test_status_cached_and_not` | With the repo in the stand-in cache: `cached`, the size and the revision count are reported; with an empty cache `cached` is false; with `find_spec` reporting no `huggingface_hub`, `hub` is false and nothing else is called |
| 2 | `test_download_calls_the_hub` | `download()` calls `snapshot_download` with `repo_id="facebook/sam3"` and returns its path |
| 3 | `test_download_explains_a_gated_repo` | A stand-in that raises an exception class named `GatedRepoError` gives `PermissionError` whose text contains the licence URL and `huggingface-cli login`; an exception with `401` in its text does too; another exception passes through unchanged |
| 4 | `test_remove_deletes_every_revision` | `remove()` passes all commit hashes of the repo to `delete_revisions`, executes, and returns the size; with no cached repo it returns 0 and deletes nothing; another repo in the cache is not touched |
| 5 | `test_cli_models` | `cli.main(["models", "status"])` returns 0 and prints `weights:`; `["models", "remove"]` without `--yes` returns 1 and deletes nothing; with `--yes` it deletes; `["models", "download"]` with the gated stand-in returns 2 and prints the licence URL |
| 6 | `test_missing_packages` | With `models._hub` raising `RuntimeError`, `download` and `remove` through the CLI return 2 with the install hint; `status` still returns 0 |

## Acceptance checks (the session runs these)

1. Both test suites green.
2. `autoedit models status` on this machine (read-only) reports the installed weights.
3. Listed as open for the owner: a real `download` on a machine without the weights, and the
   evaluation of a second segmenter.

## Result

Built on 2026-11-11, in the unattended run. Status: done for the status and removal commands and
for the download path as code; no download and no login were made in this run.

- **Built** by one Builder (Sonnet, medium) from this spec: `autoedit models status | download |
  remove`, `models.py`, a pointer in `doctor`, and the section "Adding a segmenter" in
  `ARCHITECTURE.md`.
- **Deviations accepted:** `remove` scans the cache once; `models remove` checks for the packages
  before the size and `--yes`; with nothing cached it says so and returns 0; errors go to stderr.
- **On this machine** `autoedit models status` reports the SAM 3 weights in the local cache
  (3.4 GB, one revision) and says that `backend = "auto"` would use `sam3`.
- **Not verified in this run:** a real download through the gated Hugging Face flow, and a real
  removal. Both are exercised in tests through a stand-in only.
- **Final check** (Checker, Opus, high): clean. Nothing downloads without `models download`,
  `remove` needs `--yes` and deletes only the model's own cache entry, no token is read or
  printed, and the hub library is imported lazily.
- **For the owner:** the candidate list for a second segmenter in `ARCHITECTURE.md` was written
  without network access. Every licence in it is marked "verify". Whether to keep the gated SAM 3
  flow or add a second segmenter stays the owner's decision.
