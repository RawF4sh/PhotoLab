# Issue tracker: docs/plans

Specs and tickets for this repo live as markdown files under `docs/plans/`, beside the feature
specs that were there before the engineering skills. `docs/FEATURE_PLAN.md` holds one status row per
feature. Nothing goes to GitHub Issues: the repository is public.

## Conventions

- A feature has a number, the next free `F<N>` in `docs/FEATURE_PLAN.md`.
- The spec is `docs/plans/F<N>-<slug>.md`. Add or update the feature's row in `docs/FEATURE_PLAN.md`.
- Tickets are one file each at `docs/plans/F<N>-tickets/<NN>-<slug>.md`, numbered from `01`, never a
  single combined tickets file.
- Near the top of each ticket: a `Status:` line (a label from `triage-labels.md`) and, where it waits
  on others, a `Blocked by: NN, NN` line.
- Comments and conversation history append to the bottom of the file under `## Comments`.
- No home-folder paths, personal details, author names or web file names in these files
  (`python scripts/check_public_repo.py` checks).

## When a skill says "publish to the issue tracker"

Write the file (and the `FEATURE_PLAN.md` row for a new spec). Never commit or push: the owner drives
git and gets a commit block instead.

## When a skill says "fetch the relevant ticket"

Read the file at the referenced path. The user will normally pass the path or the ticket number.

## Closing a ticket

Set `Status:` to `done` and append what was built and how it was checked under `## Comments`.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a file with one **child** file per ticket.

- **Map**: `docs/plans/<effort>/map.md` (the Notes / Decisions-so-far / Fog body).
- **Child ticket**: `docs/plans/<effort>/issues/NN-<slug>.md`, numbered from `01`, with the question in
  the body. A `Type:` line records the ticket type (`research`/`prototype`/`grilling`/`task`); a
  `Status:` line records `claimed`/`resolved`.
- **Blocking**: a `Blocked by: NN, NN` line near the top. A ticket is unblocked when every file it
  lists is `resolved`.
- **Frontier**: scan `docs/plans/<effort>/issues/` for files that are open, unblocked and unclaimed;
  first by number wins.
- **Claim**: set `Status: claimed` and save before any work.
- **Resolve**: append the answer under an `## Answer` heading, set `Status: resolved`, then append a
  context pointer (gist + link) to the map's Decisions-so-far in `map.md`.
