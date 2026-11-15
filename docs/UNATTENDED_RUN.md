# Unattended runs: working through the plan while the owner is away

These rules apply when the owner tells a Claude session to work through
[FEATURE_PLAN.md](FEATURE_PLAN.md) on its own, without questions, and to leave git alone. The
owner said so for the first time on 2026-11-11. The run ends when the plan is finished or when
nothing more can be done without the owner.

The rules add to `CLAUDE.md` and to [AGENT_ROUTING.md](AGENT_ROUTING.md). Where the feature loop in
`AGENT_ROUTING.md` says "stop and ask the owner", section 3 below replaces that step for the length
of the run. Everything else in both files stays in force.

## 1. Do not ask and do not wait

- No question to the owner, no option dialog, no "shall I continue". Decide, write the decision
  down (section 3), and go on.
- An ordinary obstacle (a failing test, a missing tool, a slow command) is worked through, not
  reported and waited on.
- A step that only the owner can take is skipped and listed as open (section 4). The run goes on
  with the next thing that does not depend on it.

## 2. Git is read-only

- Never commit, push, stash, reset, check out, switch branches or create a branch. Sub-agents
  get the same rule in every brief.
- All work stays in the working tree. The run ends with one copy-and-paste block for the owner.
- Rollback points are plain copies, not git: after each closed feature, the tracked and the new
  files are copied to a snapshot folder outside the repository.

## 3. Decisions that belong to the owner

| Kind of decision | What the run does |
|---|---|
| A default that changes how photos look | Builds the feature behind a setting that is off. Runs the tone gate with the setting on and prepares a blind gallery. The default stays as it was |
| The owner's blind vote | Never cast for them. Features that wait for a vote are built behind settings and marked `done, off by default` |
| A choice with no effect on pixels (a name, a file layout, a command word) | Takes the conservative, reversible option |
| Anything that needs the owner's accounts, a licence, money or a download | Not done. The feature is built and tested against fakes; the step is listed as open |

Every decision taken this way is recorded twice: in the feature's spec under "Decisions taken
without the owner", and in the table at the end of `FEATURE_PLAN.md`.

## 4. Never done unattended

- A commit or a push.
- A download or an install: packages, model weights, fonts, sample files.
- A login, a licence acceptance, or a call that bills the owner or sends a photo off the machine.
- Deleting or overwriting a file the run did not create: the photo folders, the `out-*` evidence
  folders, the mask cache.
- Loosening a threshold in `configs/quality.toml`, or flipping a default that changes pixels.

## 5. Match the agent to the level of the work

The tiers, models and efforts are in the table in [AGENT_ROUTING.md](AGENT_ROUTING.md): the
session plans and verifies on Fable, a Checker reviews on Opus, Builders implement on Sonnet,
Tweakers and Scouts do mechanical work on Haiku. The run adds these rules to keep the token cost
down:

- **The session does not build features.** It writes the spec, briefs the agents, verifies their
  reports and makes changes of one or two tool calls.
- **A spec is exact enough for a Builder to start without exploring.** It names files, functions,
  config keys, tests and the proving command, and gives reference code for the hard part.
- **Fact-finding goes to a Scout.** The session reads a file itself only when the design depends
  on its exact code.
- **One Builder per task, and one feature at a time** unless two features touch no common file.
- **Mechanical follow-ups go to a Tweaker only as a rule or a table**: a rename across files, a
  list of config values, a count sync. Text the session has already written out is applied inline.
- **Checks are sized to the risk.** The Checker always reviews a feature that touches a protected
  area, and every M or L feature. Small features outside the protected areas are reviewed together,
  one Checker for several diffs. A check costs about 100,000 tokens whatever the size of the diff.
- **Reports are short.** Every brief asks for a summary of the diff and the tail of the proving
  command, never for file contents.
- **Long commands run once, in the background, by the session**: the two test suites, the tone
  report, renders. An agent runs only the proving command of its own task.
- **Two failures change the approach.** A task that fails its check twice goes back to the session
  for a new design, not to a third attempt on the same one.

## 6. Closing a feature

A feature is closed, in this order, before the next one starts:

1. `PYTHONPATH=src python3 tests/run_all.py` is green on the system Python and on `.venv`.
2. The stub smoke run from `CLAUDE.md` completes with sidecars and a manifest.
3. If the feature must not change default output: a default render of the reference photos is
   pixel-identical to the baseline (maximum difference 0).
4. If the feature touches exposure, tone or decode: the tone report gate reports 0 and 0, for the
   default settings and with the new setting switched on.
5. The status row in `FEATURE_PLAN.md` and the "Result" section of the spec are updated.
6. A snapshot is taken (section 2).

A feature that cannot be closed after a second design is rolled back to the last snapshot, marked
`blocked` with the reason, and the run moves on.

## 7. Handing back

The run ends with a report to the owner, in this order:

1. What was built, feature by feature, with the evidence for each.
2. What is open, and what only the owner can do: votes, downloads, licences, defaults.
3. Every decision taken without the owner, each with the one-line change that reverses it.
4. One commit and push block: a new branch, an explicit file list, checked for local paths and
   personal details with `scripts/check_public_repo.py`.

## 8. What the first run taught (2026-11-11)

Checks that looked green and were not. Each cost a second pass.

- **A tone gate at 0 does not mean the edit was accepted.** The safe renderer pulls a failing edit
  back toward the source and then counts 0. Compare the remediation factor per photo
  (`safety_fallback` in the report rows, 1.0 = not pulled back) with the default run. The first
  black point passed the gate this way while three photos lost half of their edit.
- **Compare an option with the default output, not only with the gate.** LibRaw's blend mode had
  0 gate failures and made 109 of 110 photos darker.
- **Test a changed copy of the tree directly.** The pytest config puts the checkout's own `src`
  first, so `PYTHONPATH=<copy>/src python3 -m pytest` runs the checkout's code. To see a test
  fail without its fix, call the test function with `python3 -c`.
- **A blind pair must be plainly different.** Measure the mean pixel difference between the two
  versions before anyone is asked to vote. About one 8-bit level is invisible.
- **Run a new batch mode on a real shoot before closing it.** Bursts mode was correct on
  synthetic bursts and needed more than 25 minutes of planning on 800 photos, because a RAW file
  and its JPEG counted as a burst.
- **Try the real import, not the package list.** A machine can have torch and Transformers
  installed and still fail to import SAM 3.
- **Review in a second model.** The independent checks found a path escape through a style name
  and a way to overwrite source photos that the builders and the session had missed.
