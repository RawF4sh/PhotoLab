# 26. Shoot neighbours: a photo that fell back to the broader scene stays there

Status: done
Spec: [F62](../F62-auto.md), milestone M1
Who: Builder (Sonnet, medium), then a Checker (Opus, high) on the diff.

**No owner decision is open.** Ticket 25's fresh check found that its rule adds daylight/twilight
swaps, which the scene bar forbids: on the owner's card folder it called 23 frames of a dawn
timelapse daylight, and the owner calls them twilight. This ticket stops the rule from doing that.
The session proposed a rule of two sides: a photo takes a scene only when the photos before it
and the photos after it agree. The replay on all three sets says a narrower change removes the
same swaps and keeps the answers ticket 25 made right, so this ticket builds the narrower one. The
two-sided rule and its numbers are under "Left out".

## What to build

**What went wrong.** Ticket 25 lets two kinds of photo take a scene from the photos around them:

- a photo that answered **daylight by default** (the classifier's branch "daylight exposure or
  bright, well-defined highlights"). Its exposure shows the day. What it does not show is the
  weather.
- a photo that **fell back** to `landscape` ("no clear lighting signature"). Nothing in it says
  what the light is, not even the time of day.

The second kind is found where the light changes. Of the 64 such frames on the card folder, 36
are one dawn: a timelapse, one frame every 10 seconds, that runs from frames read as twilight
through 36 frames with no answer into frames read as daylight. The window of a frame in the
middle holds 7 twilight frames and one mist frame before it and 25 daylight frames after it.
That is 76 % daylight, the share is met, and the later scene spreads backwards over 23 frames.

On the three sets that can be replayed (Comments), ticket 25's rule handed out a scene 79 times:

| The photo had… | Times | Made right | Made wrong | Made a daylight/twilight swap | Wrong before and after | Not a landscape | No label |
|---|---:|---:|---:|---:|---:|---:|---:|
| answered daylight by default | 49 | 27 | 3 | 0 | 0 | 18 | 1 |
| fallen back to `landscape` | 30 | 1 | 0 | 23 | 2 | 4 | 0 |

**The rule.** Only a photo that answered daylight by default listens to its neighbours. It takes
mist or sunset on ticket 25's terms, which do not change: the direct frames taken within
`window_min` minutes, at least `min_frames` of them, `min_share` of them alike. As before, it
never takes twilight. A photo that fell back keeps `landscape`: the broader goal at
`fallback_strength`, which is what the owner decided for a photo Auto is unsure of ("When unsure"
in the spec's decisions).

The rule then gives mist or sunset and nothing else. It can no longer call a photo daylight or
twilight, so it cannot add a daylight/twilight swap, whatever the frames around a photo say. It
cannot mend one either: that stays the classifier's work.

Why the two kinds are treated differently: "daylight by default" is a guess. On the 30 labelled
landscapes where the neighbours overruled it, the guess was right 3 times and the neighbours 27
times. `landscape` is not a guess. It is Auto saying that it does not know, and Auto already has
a safe edit for that.

No number is added or changed: `[shoot]` keeps `window_min`, `min_frames` and `min_share`. Which
photo listens is not a tuning value. Like `direct`, it is the branch of the classifier that
answered. No stage of the pipeline is added, moved or fused, and all of this runs only with the
Auto style: Simple and default runs never reach it.

**1. `src/autoedit/consistency.py`**

`neighbour_scenes` becomes the code below. Two conditions change (who listens, and what can be
taken), and the docstring. The Planner ran this code through the replays and the tests of this
ticket, put into the loaded module in memory with the tree untouched.

```python
def neighbour_scenes(frames, cfg):
    """path -> scene, for the frames that take the scene of the frames around them.

    `frames`: (path, capture time in seconds, scene, direct) for the landscape frames of one
    folder, RAW files apart from the others. `direct`: the frame's own sky or light decided its
    scene. A frame that answered "daylight" by default takes the scene that the direct frames
    taken within `window_min` minutes agree on: at least `min_frames` of them, `min_share` of
    them alike. Its exposure shows the day, so it never takes twilight: the rule gives mist or
    sunset and nothing else, and cannot swap daylight and twilight. A frame that shows its own
    light keeps it. So does a frame that fell back to "landscape": nothing in it says what time
    of day it is, and such frames sit where the light changes, between neighbours that disagree.
    ponytail: every frame against every frame of its folder; sort and bisect if a folder of
    tens of thousands shows up."""
    reach = cfg["window_min"] * 60.0
    out = {}
    for path, when, scene, direct in frames:
        if direct or scene != "daylight":       # only a daylight answer given by default listens
            continue
        seen = Counter(s for p, t, s, d in frames if d and p != path and abs(t - when) <= reach)
        total = sum(seen.values())
        if total < cfg["min_frames"]:
            continue
        top, count = seen.most_common(1)[0]
        if count >= cfg["min_share"] * total and top not in ("daylight", "twilight"):
            out[path] = top
    return out
```

The condition names the one answer that listens instead of the one that does not: a default
answer added to the classifier later then stays out until somebody lets it in.

`_auto_shoots`, `shoot_blocks`, `shoot_time` and the settings pull stay as they are. A photo that
fell back still sits with its neighbours in the blocks of the settings pull, so its settings are
still pulled toward the block's median.

**2. `src/autoedit/configs/auto.toml`**

Only the comment above the `[shoot]` table changes. Keep the table's name in square brackets out
of the comment: `test_load_auto_refuses_a_bad_shoot_table` replaces its first occurrence in the
file.

```toml
# Frames of one folder taken close together share their light. A frame that says daylight only by
# default (no sky in the picture, or a clipped one) takes mist or sunset when the frames around it
# show it. A frame that shows its own light keeps it. A frame that fell back to the broader
# landscape scene stays there: its neighbours are not asked.
```

**3. Tests**, in `tests/test_scene.py`.

In `test_a_frame_without_its_own_sky_takes_its_neighbours_and_a_frame_with_one_keeps_it` the last
two lines (the `dusk` case, in which a frame that fell back takes twilight) are replaced by these
five. The lines above them stay.

```python
    assert take(deck + [("f", 90, "landscape", False)], cfg) == {}        # it fell back: it stays there
    dusk = [("a", 0, "twilight", True), ("b", 60, "twilight", True)]
    assert take(dusk + [("q", 30, "daylight", False), ("f", 40, "landscape", False)], cfg) == {}
    glow = [("a", 0, "sunset", True), ("b", 60, "sunset", True)]
    assert take(glow + [("q", 30, "daylight", False)], cfg) == {"q": "sunset"}
```

A new test, the golden test of the rule change. Its first half is the dawn run of the card
folder in small; its second half is the promise that the rule cannot make a swap:

```python
def test_neighbours_never_give_daylight_or_twilight():
    import random
    cfg = dict(window_min=10, min_frames=2, min_share=.75)
    take = consistency.neighbour_scenes
    # F62 ticket 26. A timelapse from dusk light into day, a frame every 10 seconds: 8 twilight
    # frames, 36 that fell back, 30 daylight frames. Ticket 25's rule called the later ones daylight.
    dawn = ([(f"t{i}", 10 * i, "twilight", True) for i in range(8)]
            + [(f"u{i}", 80 + 10 * i, "landscape", False) for i in range(36)]
            + [(f"d{i}", 440 + 10 * i, "daylight", True) for i in range(30)])
    assert take(dawn, cfg) == {}
    assert take([(p, -t, s, d) for p, t, s, d in dawn], cfg) == {}        # the same run at dusk
    rng = random.Random(26)
    for _ in range(200):
        frames = []
        for i in range(rng.randint(2, 40)):
            direct = rng.random() < .6
            kind = rng.choice(scene.LANDSCAPES) if direct else rng.choice(("daylight", "landscape"))
            frames.append((str(i), rng.uniform(0, 1800), kind, direct))
        own = {p: (s, d) for p, _t, s, d in frames}
        taken = take(frames, cfg)
        assert set(taken.values()) <= {"mist", "sunset"}
        assert all(own[p] == ("daylight", False) for p in taken)
```

With this seed 9 of the 200 draws hand out a scene, so the loop does test something; ticket 25's
rule breaks it on 6 draws.

`test_auto_shoots_hands_a_frame_without_sky_its_neighbours_scene` gains a frame that fell back,
between the two deck frames: `"f.jpg": 45` in `times`, `"f.jpg": ("landscape", False)` in
`canned`, and one more assertion at the end. The two existing assertions stay as they are and
must still hold: `f.jpg` gets nothing.

```python
    assert [c for c in calls if c[0] == "f.jpg"] == [("f.jpg", None)]     # fell back: analysed once, handed nothing
```

On the tree as it is, all three tests fail: the first two at a new assertion, the third at its
`got == …` line, because the frame that fell back has been handed mist.

**4. Docs.**

- `CHANGELOG.md`, under "Scene-based Auto (F62)", after the line that begins "Auto's scene
  follows the shoot": "A photo that Auto could not give a finer scene keeps the broader landscape
  scene in a batch as well: only a photo that says daylight by default takes a scene from its
  neighbours, and only mist or sunset. On a fresh card folder the neighbours had turned 23 frames
  of a dawn timelapse into daylight."
- `docs/plans/F62-auto.md`, the decisions row "One shoot": "A photo that shows its own sky or
  light keeps its scene; a photo that says daylight only by default takes the scene its
  neighbours agree on (mist or sunset); a photo Auto is unsure of stays on the broader scene.
  Settings are pulled toward one median, so the shoot looks alike".
- The same file, the M1 bullet "One shoot, one scene": "A photo that answered daylight by default
  (no sky in the frame, a clipped sky, or a sky the classifier cannot read) takes the scene that
  the photos of its folder taken within `[shoot] window_min` minutes agree on (at least
  `min_frames` of them, `min_share` alike): mist or sunset, never twilight. A photo that shows
  its own sky or light keeps its scene (ticket 25), and a photo that fell back to `landscape`
  stays there (ticket 26)." The bullet's last sentence, about `plan_groups`, stays.

## Left out

- **Two sides for every photo that listens** (the session's proposal). A photo takes a scene only
  when the direct frames before it and the direct frames after it each agree on it, each side on
  today's terms (`min_frames` of them, `min_share` alike). It removes the 23 swaps and makes no
  answer wrong on any set. It also gives up most of what ticket 25 gained. Card folder: 4 answers
  changed instead of 30, 2 made right instead of 11, and 98 of 147 in the random sample instead of
  102. Swiss trip: 8 made right instead of 16. Dolomites: no change instead of one. For the
  photos that fell back it does nothing that this ticket does not: none of the 70 on the three
  sets has neighbours that agree on both sides. For the default daylight photos it trades 17
  right answers for 3 wrong ones. Bring it back for them when a labelled set shows the rule
  making more answers wrong than these three.
- **Two sides for the photos that fell back only.** It gives the same answers as this ticket on
  all three sets, with more code. It can also still make a swap where the direct frames on both
  sides are misread, and before this dawn they are (the second finding in Comments). Bring it
  back when a labelled set shows frames that fell back inside a run whose light holds, such as a
  night series: the three sets have none.
- **A side that may stay silent**, tried for the two-sided rule. If a side with no direct frame
  counts as agreeing, the swaps come back as soon as the folder is thinner: in 71 of 300 random
  copies of the card folder that keep each frame with a chance of one half, and in 117 of 300
  that keep each with a chance of one quarter. If only the first and last frames of a shoot may
  have a silent side, 4 of the 300 quarter copies still get a swap, and on the labelled frames at
  the edge of a shoot the exception was right once and wrong twice.
- **A default daylight photo between two blocks.** Its window can be three quarters mist while
  the frames on one side of it mostly show an open sky. The three sets hold one labelled case,
  and the rule made it wrong. One case is no basis for a rule.
- **Sunset from neighbours** stays as built. On the card folder it turned 9 photos to sunset, and
  the Checkers called none of the 9 a landscape: there is still no labelled case for or against.
- **The dawn frames themselves.** The 36 keep `landscape`, and the owner calls them twilight.
  Their neighbours cannot say so, because the frames after them are read as daylight. Naming them
  needs the classifier (ticket 02).
- **The two answers ticket 25 made wrong on the card folder** (a thin-sky daylight read as mist,
  because the two frames just before it were) stay wrong. Both have a silent side.

## Done when

- Before the first edit, the Builder runs the stub pipeline into a scratch folder:
  `PYTHONPATH=src python3 -m autoedit.cli run ./test-corpus <scratch>/before --backend stub`.
  After the change the same command into `<scratch>/after` completes with sidecars and manifest,
  and the two image files are byte-identical to the ones in `before` (`cmp`).
- `PYTHONPATH=src python3 tests/run_all.py test_scene.py` passes with 24 tests, one more than
  today, and `PYTHONPATH=src python3 -m pytest --collect-only -q tests/test_scene.py` collects 24.
- The whole suite is green on both interpreters: `PYTHONPATH=src python3 tests/run_all.py` and
  `PYTHONPATH=src .venv/bin/python tests/run_all.py` (the tests that need tifffile run only on
  the second). Nobody edits `src/` or `tests/` while a suite runs: `run_all.py` loads the test
  files one by one, and a tree that changes under it reports failures that are not there.
- `python3 scripts/check_public_repo.py` passes. CHANGELOG, the two places in the spec and the
  comment in `auto.toml` are updated.
- The Checker (Opus, high) reviews the diff against this ticket, read-only, with findings as
  `file:line`, and runs the session's replays from the repository root. They read the per-frame
  answers kept with the private F62 evidence and start no SAM 3 job. The third is new with this
  ticket: before the check, the session copies the Planner's `replay_card.py` into the harness
  folder and the frame times to `out-webloop/f62/sdcf/times.json`.
  - `.venv/bin/python -I out-webloop/f62/harness/neighbours/replay.py swiss`: the line "proposed
    rule" (it is the source tree's rule) reports 18 changes, all daylight to mist, where ticket
    25's rule made 20.
  - `.venv/bin/python -I out-webloop/f62/harness/neighbours/real_plan.py --all` (about 10
    minutes): the real `consistency.plan` on the Dolomites RAW folder still changes one answer,
    the frame without sky on the overcast afternoon.
  - `.venv/bin/python -I out-webloop/f62/harness/neighbours/replay_card.py`: on the card folder
    the source tree's rule changes 30 answers (21 daylight to mist, 9 daylight to sunset) where
    ticket 25's changed 58. Of the 30, 11 are made right, 2 made wrong, 17 are not landscapes, and
    none is a daylight/twilight swap. The random sample stands at 102 of 147 with 1 swap, the one
    the classifier makes alone, and the sure and owner labels at 72 of 85 with none.
- No tone report and no contact sheet: nothing in exposure, tone or decode changes, and a default
  run renders the same pixels. `scene.py` is not touched, so the answer of a photo alone does not
  move.
- No git writes: the owner gets a commit block.
- Not part of this ticket's "done": the 85 % bar (Comments, "What it will not do").

## Owner steps

No decision blocks the build.

| | Step | Proposed |
|---|---|---|
| O1 | Photos for a second fresh check. The card folder is spent for this rule: the rule was chosen on it | One more folder as it came off the card, from a trip nothing here has used, with a dawn or a dusk in it if there is one. Kept private like the other corpora. Does not block the build |

## Comments

2026-11-26 — Planner (Fable, max). The session's two-sided proposal and a narrower rule were
replayed side by side. A shoot rule reads only three things per frame: its capture time, the
answer it gave alone, and whether that answer was direct. All three sets have them on disk, so
no SAM 3 job was started and nothing in the tree was changed.

**How it was measured.**

- *Card folder* (ticket 25's fresh check: 1,581 camera JPEGs in one pool, 1,147 of them
  landscape frames, of which 875 direct, 208 daylight by default and 64 fell back): the batch
  run's answer for every frame, and the capture time from each file's EXIF. Replaying ticket 25's
  rule over them gives the batch run's 58 changes, frame for frame. Labels: the random 320 with
  the owner's label on the dawn run, and the 46 changed frames outside the sample.
- *Swiss development trip* (345 RAW files) and *Dolomites RAW folder* (110): ticket 25's
  per-frame records, through its harness, with the code of this ticket put into the loaded module
  in memory. The real `consistency.plan` on the Dolomites folder, run the same way, changes the
  same single answer as today.
- The three tests of this ticket were run the same way. Each fails on the tree as it is and
  passes with the code above.

**Before and after.**

| | Ticket 25, as built | This ticket | Two sides, every photo that listens |
|---|---:|---:|---:|
| **Card folder:** answers changed | 58 | 30 | 4 |
| made right | 11 | 11 | 2 |
| made wrong | 2 | 2 | 0 |
| made a daylight/twilight swap | 23 | 0 | 0 |
| wrong before and after | 1 | 0 | 0 |
| not landscapes | 21 | 17 | 2 |
| random sample, all labels (alone 97 of 147) | 102 | 102 | 98 |
| daylight/twilight swaps in it (alone 1) | 5 | 1 | 1 |
| sure and owner labels (alone 72 of 85) | 72 | 72 | 72 |
| daylight/twilight swaps in them (alone 0) | 4 | 0 | 0 |
| **Swiss trip:** answers changed | 20 | 18 | 8 |
| made right | 17 | 16 | 8 |
| made wrong | 1 | 1 | 0 |
| wrong before and after | 1 | 0 | 0 |
| a close-up, left out of the labels | 1 | 1 | 0 |
| agreeing, of the 153 labelled (alone 73) | 89 | 88 | 81 |
| agreeing, of the 48 sure (alone 34) | 40 | 39 | 35 |
| daylight/twilight swaps (alone 1) | 1 | 1 | 1 |
| **Dolomites RAW folder:** answers changed | 1 | 1 | 0 |

The 12 development labels on the Dolomites trip all decided by their own sky or light, so no
shoot rule moves them. The one Swiss answer this ticket gives up is a frame that fell back between
daylight frames and is labelled daylight, sure.

The card folder is no longer a fresh check for this rule: the rule was chosen on its labels. Its
"after" column is a development number, like the Swiss one. Hence O1.

**The frames that fell back.** Asked the two-sided way (two direct frames on each side, three
quarters of them alike), none of the 70 on the three sets (64, 5 and 1) has neighbours that
agree before and after. On the card folder the two sides name different scenes for 33 (all in
the dawn run), at least one side is mixed or too thin for 24, one side is silent for 4, and
nobody speaks for 3. A two-sided rule and no rule at all give these frames the same answer.

**The default daylight frames.** The 49 changes that stay, by what the direct frames on the two
sides of the photo show:

| | Made right | Made wrong | Not landscapes | No label |
|---|---:|---:|---:|---:|
| Before and after both mostly show the scene taken | 16 | 0 | 8 | 1 |
| One side shows it, the other has no direct frame | 10 | 2 | 10 | 0 |
| One side shows it, the other is split evenly | 1 | 0 | 0 | 0 |
| One side shows it, the other mostly shows another scene | 0 | 1 | 0 | 0 |

Where only one side speaks, the neighbours were right 10 times and the default answer twice.
That is why this ticket does not ask these photos for two sides.

**Thinner folders.** The dawn result does not hang on how this one folder happens to be spaced.
Each rule was run on 300 random copies of the card folder that keep each frame with a chance of
one half, and on 300 that keep each with a chance of one quarter. Ticket 25's rule made a dawn
swap in 298 and 272 of them. This ticket and the two-sided rule made none. With the ten direct
frames between the folder's two timelapse runs taken out, ticket 25's rule turns all 36 dawn
frames to daylight, and this ticket none.

**What it will not do.**

- Meet the bar of ticket 03. On the card folder's random sample the batch answer stays at 102 of
  147 (69.4 %) against 85 %, and at 72 of 85 (84.7 %) on the sure and owner labels. One
  daylight/twilight swap is left in the sample. The classifier makes it alone: a frame read
  directly as daylight that the Checker labelled twilight, unsure.
- Name the 36 dawn frames (see "Left out").
- Stop a swap the classifier makes on a photo by itself.

**A second finding, not planned here.** The 18 frames between the dawn run and the first sun on
the peak show a clear blue sky before sunrise, and Auto reads them alone as daylight, directly.
They are in none of the labelled sets. They are also most of what ticket 25's rule spread
backwards: 18 of the 25 daylight frames in the window of the first dawn frame it changed. A
direct answer is not this ticket's to change. It is the classifier's (ticket 02), and it needs
labels first.

2026-11-26 (session): Built as written: `neighbour_scenes` in `consistency.py`, the comment above `[shoot]`, the three test edits, CHANGELOG and the spec. `test_scene.py` 24 passed and collected; full suites green (1,438 passed and 4 skipped; 1,442 passed with the venv); stub images byte-identical; public-repo check passes. A Checker (Opus, high) found no blockers. The three tests fail on ticket 25's rule, as predicted. The replays match: Swiss 18 changes, all daylight to mist; the Dolomites plan still changes its one answer; the card folder 30 changes (21 to mist, 9 to sunset), 11 right, 2 wrong, 17 not landscapes, 0 swaps; random sample 102/147 with 1 swap, sure and owner labels 72/85 with none. Minor: counting both assertions, ticket 25's rule breaks the random test on 14 of 200 draws, not 6, so the test is stronger than stated above. The 85 % bar stays open (the classifier's).
