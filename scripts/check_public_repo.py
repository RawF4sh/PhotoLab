"""Reject private artifacts in the Git index and, optionally, reachable history.

Two checks. Paths: no private file (a photo, a run folder, a key) is tracked. Contents: no tracked
text file holds an absolute path into somebody's home folder, which names a user and a disk
layout. The contents check reads the files as they are on disk now, not the history.

This is a publication guard, not a credential detector. Run Gitleaks separately.
"""
from __future__ import annotations

import argparse
from pathlib import PurePosixPath
import re
import subprocess
import sys


PRIVATE_DIRS = {".tmp", ".claude", ".codex", ".agents", "testImages", "output",
                "outputs", "out", "eval_3way", "split_before_after_landscapes"}
PRIVATE_NAMES = {"NEW_CHAT_HANDOFF.md", "PROJECT_ASSESSMENT.md", "run.json", "history.jsonl"}
PRIVATE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff",
                    ".cr2", ".cr3", ".nef", ".arw", ".dng", ".xmp", ".pem",
                    ".key", ".pt", ".pth", ".safetensors", ".onnx", ".npz",
                    ".npy", ".db", ".sqlite", ".sqlite3", ".log", ".zip",
                    ".tar", ".gz", ".bundle"}


def private_path(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        any(p in PRIVATE_DIRS or p.startswith(("out-", "curated-")) for p in path.parts)
        or path.name in PRIVATE_NAMES
        or (path.name.startswith(".env") and path.name != ".env.example")
        or path.name.lower().startswith("credentials")
        or "service-account" in path.name.lower()
        or path.name.endswith((".autoedit.json", ".merge.json"))
        or path.suffix.lower() in PRIVATE_SUFFIXES
    )


# A path that starts at a home folder: the macOS, Linux and Windows spellings. The name after it is
# captured; names that documentation uses as stand-ins are not a leak.
_HOME = re.compile(rb"(?<![A-Za-z0-9._~-])(?:/Users/|/home/|[A-Za-z]:\\Users\\)([A-Za-z0-9._-]+)")
_STAND_INS = {b"me", b"you", b"user", b"username", b"name", b"someone", b"example", b"runner", b"shared"}


def home_path_lines(data: bytes) -> list[int]:
    """The 1-based lines of a text file that hold an absolute path into a real home folder."""
    if b"\0" in data[:8192]:
        return []       # not text
    return [number for number, line in enumerate(data.splitlines(), 1)
            if any(re.search(rb"[A-Za-z0-9]", m.group(1)) and m.group(1).lower() not in _STAND_INS
                   for m in _HOME.finditer(line))]


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="also inspect every reachable commit")
    args = parser.parse_args(argv)
    names = {p.decode("utf-8", errors="replace") for p in git("ls-files", "-z").split(b"\0") if p}
    bad = {name for name in names if private_path(name)}
    if args.history:
        # Tree enumeration catches aliases and renames of the same blob too.
        commits = git("rev-list", "--all").splitlines()
        for commit in commits:
            paths = git("ls-tree", "-rz", "--name-only", commit.decode()).split(b"\0")
            bad.update(p.decode("utf-8", errors="replace") for p in paths
                       if p and private_path(p.decode("utf-8", errors="replace")))
        emails = set(git("log", "--all", "--format=%ae%n%ce").decode().splitlines())
        private_emails = sum(not e.endswith("@users.noreply.github.com") for e in emails)
        if private_emails:
            print(f"FAIL: {private_emails} non-GitHub-noreply email address(es) in commit metadata; values hidden.")
            return 1
    if bad:
        print(f"FAIL: {len(bad)} private artifact path(s); values hidden. Remove them from the index/history.")
        return 1
    leaks = []
    for name in sorted(names):
        try:
            with open(name, "rb") as fh:
                lines = home_path_lines(fh.read(4_000_000))
        except OSError:     # deleted in the working tree, or a submodule
            continue
        if lines:
            leaks.append(f"{name}:{lines[0]}")
    if leaks:
        print(f"FAIL: an absolute home-folder path in {len(leaks)} tracked file(s); values hidden: "
              + ", ".join(leaks) + ". Write the path relative to the repository.")
        return 1
    print(f"PASS: {len(names)} tracked paths; no blocked private artifacts, no home-folder paths in their text"
          + (", no personal commit emails in reachable history." if args.history else "."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
