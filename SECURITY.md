# Security and privacy

AutoEdit processes photos locally by default. The optional Anthropic decider requires
explicit cloud consent and a key; it sends a thumbnail and image statistics. SAM weights
are downloaded separately from Hugging Face. Those provider requests are subject to the
provider's policies.

The browser interface is intended for a trusted local machine, bound to loopback. Do not
host it as a public upload service or proxy it through Photo Lab without a separate
authentication, authorization, and storage design.

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
