# Security and privacy

AutoEdit processes photos locally by default. The optional Anthropic decider requires
explicit cloud consent and a key; it sends a thumbnail and image statistics. SAM weights
are downloaded separately from Hugging Face. Those provider requests are subject to the
provider's policies.

The browser interface is intended for a trusted local machine, bound to loopback. Do not
host it as a public upload service or proxy it through Photo Lab without a separate
authentication, authorization, and storage design.

The edit history (`--history`, off by default) is `history.jsonl` in the profile folder, on this
computer only. A line holds the scene, the photo's statistics, the look's name (only a look the
library defines) and the user's own changes per target; never a path, a file name, pixels, a
typed sentence or a word the config files do not define. `autoedit history status` shows it and
`autoedit history clear --yes` deletes it.

A look file (`.aelook`, `autoedit looks export/import`) is TOML data and can never run code. Import
checks it whole before anything is written: known keys only, finite numbers inside each field's
limits, at most 16 kB, a plain name, habits only within `[limits]`, no `preserve_exposure`. It holds
no photos, paths or history. A look card (`looks card`) holds the pictures, the look's name and the
engine version, never file names.

Every file path in an API request must lie in a folder the user picked in the interface,
including a LUT file (`lut`). A `.cube` file is read as plain text by a strict reader: known
keywords only, a size limit, finite numbers; its error messages name the file, never its
content or folder.

## Sharing source and examples

- Keep API keys in your environment, outside source files. Never include them in issues,
  screenshots, command transcripts, or configuration committed to Git.
- Photos can contain GPS coordinates, camera serial numbers, owner names, and capture
  times. Sidecars and evaluation reports can contain filenames and absolute local paths.
  Publish deliberately reviewed copies with private metadata removed.
- Keep original photographs, RAW files, output galleries, assistant settings, private
  evaluation evidence, and model weights outside version control. Tests generate their
  fixtures locally.
- Run `python3 scripts/check_public_repo.py --history` and
  `gitleaks git . --log-opts=--all --redact=100` before a public release. These checks reduce
  accidental exposure; they do not prove that every kind of sensitive data is absent.
- A deletion commit does not remove earlier copies from Git history. If a credential was
  committed, revoke it before cleaning history. Existing clones must not push old history
  back after a cleanup.

## Reporting a problem

Use GitHub's private vulnerability reporting option on this repository when available.
If it is unavailable, open an issue asking for a private contact channel without posting
the exploit, credentials, photos, or personal data. Do not include sensitive material in
a public issue or pull request.

The MIT license covers the code. Photographs, third-party model weights, and external
services have separate permissions and terms.
