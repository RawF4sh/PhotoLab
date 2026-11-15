"""Local model management for the SAM 3 backend (F14): what is installed, download, remove."""

from __future__ import annotations

import importlib.util

SAM3_REPO = "facebook/sam3"
LICENCE_URL = "https://huggingface.co/facebook/sam3"


def _hub():
    # Lazy so the core install (and the stub pipeline) never needs huggingface_hub.
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
        pass  # an unreadable cache reads as "not downloaded"; status must never fail
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
    scan = _hub().scan_cache_dir()
    for entry in scan.repos:
        if entry.repo_id == repo and entry.repo_type == "model":
            hashes = [rev.commit_hash for rev in entry.revisions]
            scan.delete_revisions(*hashes).execute()
            return int(entry.size_on_disk)
    return 0
