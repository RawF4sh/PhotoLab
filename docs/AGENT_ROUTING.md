# Agent routing: which model does which work

The strongest model decides, a strong model checks, and cheaper models build and make mechanical
changes. This file applies to every Claude session that works in this repository; `CLAUDE.md`
points here.

When a session delegates work to a sub-agent, it sets the sub-agent's `model` and `effort` from the
table below. A sub-agent started without a `model` inherits the session's model, which is the
expensive default this file exists to avoid.

## Tiers

| Tier | Model | Effort | Agent type | Use it for | Never for |
|---|---|---|---|---|---|
| Planner | `fable` (Fable 5.2) | `max` | `general-purpose` | Feature specs, architecture, op math and rule design, root-cause analysis after two failed attempts, anything in the protected areas below | Routine edits |
| Checker | `opus` (Opus 5.5) | `high` | `general-purpose` | Reviewing a diff against its spec and the invariants in `CLAUDE.md`, running and reading the checks, judging blind contact sheets, the privacy check before a commit block | Fixing what it finds |
| Builder | `sonnet` (Sonnet 5.5) | `medium` | `general-purpose` | Implementing one task from a spec: the code plus its tests | Deciding scope or changing the spec |
| Tweaker | `haiku` (Haiku 5.5) | `low` | `general-purpose` | Changing named values in TOML, renames, doc and changelog sync, counts, formatting, a test case copied from a given template | Anything that needs judgement |
| Scout | `haiku` (Haiku 5.5) | `low` | `Explore` | Finding where something lives, listing callers, collecting facts across files | Design conclusions |

- The model names are the aliases the Agent tool accepts. The versions in brackets are what they
  stood for on 2026-11-10.
- A session that itself runs on Fable at `max` effort plans in place and does not spawn a Planner.
- A Builder's effort rises to `high` for a task the spec marks as hard.

## The feature loop

Features come from [FEATURE_PLAN.md](FEATURE_PLAN.md), one at a time.

1. **Spec (Planner).** Read the code the feature touches, then write
   `docs/plans/<ID>-<slug>.md`: the goal, the files, the config keys, the tasks in order, the tier
   and the proving command for each task, the acceptance checks, and every decision that is the
   owner's. If that last list is not empty, stop and ask the owner before anything is built.
2. **Build (Builder).** One task per agent. Two Builders run at the same time only when their
   files do not overlap.
3. **Tweak (Tweaker).** Collect the feature's mechanical follow-ups into one task: config values,
   docs, changelog, counts.
4. **Check (Checker).** Read-only. Review the diff against the spec, run the definition of done
   from `CLAUDE.md`, and report findings as `file:line`, most severe first.
5. **Fix.** Findings go back to a Builder, or to a Tweaker when they are mechanical. A task that
   fails its check twice goes to the Planner for a new approach.
6. **Close (the session itself).** Re-run the decisive command, read the diff, update the status
   row in `FEATURE_PLAN.md`, and give the owner the commit and push block.

## What a brief must contain

- The spec path and the task id.
- The files the agent may touch.
- The command that proves the task.
- What to report: a summary of the diff and the output of that command.
- The standing rules below.

The cheaper the tier, the more exact the brief. A Tweaker brief names the file, the key, the old
value and the new value. It never says "find" or "decide".

## Standing rules for every sub-agent

- Never commit, stash, reset or switch branches. The owner drives git.
- Work in the main checkout, not a worktree. The photo corpora, the mask cache and `.venv` are
  gitignored and exist only there.
- Use `.venv/bin/python` for anything with `--backend sam3`, and run one SAM 3 job at a time.
- Put no absolute local path and no personal detail into a file that gets committed.
- When unsure, stop and report. Do not guess.

## Protected areas

For these, the Planner writes the spec and the Checker reviews, without exception:

- Op math: `src/autoedit/edit/ops.py`.
- Safety gates: `src/autoedit/edit/gates.py`, `src/autoedit/edit/banding.py`,
  `src/autoedit/quality.py`.
- `src/autoedit/configs/quality.toml`. Its thresholds are never loosened to make something pass.
- The pipeline order in `src/autoedit/pipeline.py`.
- The network path: `src/autoedit/decide/llm_decider.py`.

## What the session keeps for itself

- Talking to the owner, and every decision that is theirs.
- Verifying sub-agent reports. A report is not proof: re-run the command or read the diff before
  calling something done.
- Git blocks, and long-running commands (the full tests, the tone report, renders), which run in
  the background.
- Small changes. A change that takes one or two tool calls in a file already open is made inline,
  because spawning costs a brief and a verification.

## Changes to default pixels

Anything that changes what a default run renders needs a blind contact sheet
(`scripts/contact_sheet.py --panel`) judged by the Checker, and then the owner's own blind vote, as
in `docs/IMPROVEMENT_LOOP_PROMPT.md`. The owner's vote decides.
