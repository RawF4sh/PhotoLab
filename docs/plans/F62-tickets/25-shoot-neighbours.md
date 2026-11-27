# 25. Shoot neighbours: a photo that does not show its light takes its neighbours'

Status: done
Spec: [F62](../F62-auto.md), milestone M1
Who: Builder (Sonnet, medium), then a Checker (Opus, high) on the diff. Not started before the
owner's answer.

**Waiting on the owner: one decision (O1).** On 2026-11-25 the owner decided that "photos of the
same shoot get the same scene". Ticket 02 built that as a vote: the most common scene of a group
goes to every photo in it. This ticket replaces the vote. A photo that shows its own sky or light
keeps its scene, and only a photo that shows neither takes the scene of the photos taken around
it. Two photos taken a minute apart can then have different scenes, when one shows a blue opening
and the other a closed cloud deck; the shoot pull still evens out their settings. **Yes or no?**
The Planner recommends yes: on the one whole trip that could be judged by eye, the vote turned 37
right answers wrong and 21 wrong ones right, the new rule 1 and 17 (Comments). On a no, this
ticket is rewritten, not built.

## What to build

Photos taken minutes apart mostly share their light, and about one photo in five of a mountain
shoot does not show it: no sky in the frame, a sky clipped white, or a sky the classifier cannot
read. The classifier then answers "daylight" by default. On the Swiss development trip that
default is wrong on about five such photos in six, by eye, and it is the largest group of wrong
answers there.

**The rule.** Among the photos of one folder (RAW files apart from the others), a photo that
**decided by default** takes the scene that the photos taken within `window_min` minutes of it
agree on. "Decided by default" is the daylight answer of the classifier's last branch ("daylight
exposure or bright, well-defined highlights") and the fallback to `landscape`. The photos it
listens to are those that decided by their own sky or light: at least `min_frames` of them, and
`min_share` of those with the same scene. Nothing else changes a scene:

- a photo that decided by its own sky or light keeps its answer, whatever its neighbours say;
- a daylight answer is never turned into twilight;
- a photo with no capture time, a photo of people or of something else, and a photo run alone
  get today's answer.

This replaces the vote of ticket 02 (`consistency.shoot_scenes`). Simple and default runs are
untouched: everything below runs only with the Auto style (`pipeline.auto_cfg`).

**The order of the pipeline (invariant 1).** No stage is added, moved or fused. As today, the batch
plan (`consistency.plan`, before the first render) runs the whole per-photo pipeline once without
applying it (`process_one(do_apply=False)`) and hands each photo its conclusion through the
`auto_shoot` argument, which the `scene` stage already reads (`AutoPhoto.select`). The argument
keeps its two keys, `scene` and `median`, so the render workers in `batch.py`, the app's saved
receipts (`web/photo_base.py`), `adjust` and `sync` need no change.

**1. `src/autoedit/scene.py`**

- `Scene` gains `direct: bool = False`: the frame's own sky or light decided the finer scene.
- `landscape_scene` returns `direct=True` from every branch except the last two (the default
  daylight branch and the fallback), and `False` whenever the label ends as `landscape`. The
  three-way answer of Simple and a scene chosen by the user keep `False`.

**2. `src/autoedit/styles.py`**

- `AutoPhoto` gains `direct: bool = False`, set in `select` from `detected.direct` and written by
  `audit()` as `"direct"`.
- `load_auto` checks the new table and names it when it is missing: `window_min` from 1 to 120,
  `min_frames` a whole number of 1 or more, `min_share` from 0.5 to 1.

**3. `src/autoedit/configs/auto.toml`**, after `[auto]`:

```toml
# Frames of one folder taken close together share their light. A frame that does not show it (no
# sky in the picture, or a clipped one) takes what the frames around it show. A frame that shows
# its own keeps it.
[shoot]
window_min = 10      # how far apart in time two frames may be, before or after
min_frames = 2       # at least this many of them must show the light themselves
min_share = 0.75     # and this share of those must agree
```

Ten minutes is also what `autoedit sync` calls "taken together" (`rules.toml [sync] minutes`).

**4. `src/autoedit/consistency.py`**

`shoot_scenes` loses its vote and becomes `shoot_blocks`: it still cuts a group into the runs the
settings pull works on, and no longer names a winner. New, as written here. The Planner ran this
code against the real pipeline, put into the loaded module in memory with the tree untouched:

```python
def shoot_time(path: str) -> float | None:
    """When a frame was taken, for Auto's shoot rule: `capture_time`, or LibRaw's reading for the
    RAW containers Pillow cannot open (RAF, ORF, RW2, CR3, ARW, NRW). Bursts, timelapse and sync
    keep `capture_time`: a new time there would change which frames a default run evens out.
    ponytail: a second open of the file; read it during the decode if it shows in a profile."""
    when = capture_time(path)
    if when is None and is_raw(path):
        try:
            import rawpy
            with rawpy.imread(path) as raw:
                stamp = raw.other.timestamp
            when = stamp.timestamp() if stamp.year > 1990 else None
        except Exception:
            when = None
    return when


def neighbour_scenes(frames, cfg):
    """path -> scene, for the frames that take the scene of the frames around them.

    `frames`: (path, capture time in seconds, scene, direct) for the landscape frames of one
    folder, RAW files apart from the others. `direct`: the frame's own sky or light decided its
    scene. A frame that decided by default, or fell back to "landscape", takes the scene that the
    direct frames taken within `window_min` minutes agree on: at least `min_frames` of them,
    `min_share` of them alike. A frame that shows its own light keeps it, and a daylight answer
    never becomes twilight.
    ponytail: every frame against every frame of its folder; sort and bisect if a folder of
    tens of thousands shows up."""
    reach = cfg["window_min"] * 60.0
    out = {}
    for path, when, scene, direct in frames:
        if direct:
            continue
        seen = Counter(s for p, t, s, d in frames if d and p != path and abs(t - when) <= reach)
        total = sum(seen.values())
        if total < cfg["min_frames"]:
            continue
        top, count = seen.most_common(1)[0]
        if count >= cfg["min_share"] * total and top != scene and not (scene == "daylight" and top == "twilight"):
            out[path] = top
    return out


def shoot_blocks(sequence):
    """Runs of consecutive photos of one time of day, for the settings pull: daylight and mist
    together, a sunset or a twilight apart. A photo that fell back to "landscape" stays with its
    neighbours. People and other subjects are in no block."""
    from .scene import LANDSCAPES
    blocks, phase, block = [], None, []
    for path, scene in sequence:
        if scene not in (*LANDSCAPES, "landscape"):
            continue
        current = None if scene == "landscape" else ("day" if scene in ("daylight", "mist") else scene)
        if phase and current and current != phase:
            blocks.append(block)
            block = []
        block.append(path)
        phase = current or phase
    if block:
        blocks.append(block)
    return blocks
```

`_auto_shoots` takes the run's files as well (`plan` calls `_auto_shoots(images, out, pipeline,
progress)`), because in bursts mode `out` holds only burst photos:

```python
def _auto_shoots(images, biases, pipeline, progress):
    from .scene import LANDSCAPES
    groups, pools = {}, {}
    for path in images:
        when = shoot_time(path)
        folder, raw = os.path.dirname(os.path.abspath(path)), is_raw(path)
        group = biases[path].group if path in biases else ""
        if group:       # the settings pull keeps its groups
            day = datetime.fromtimestamp(when).date() if when is not None else None
            groups.setdefault((folder, group, day, raw), []).append((when, path))
        if when is not None:
            pools.setdefault((folder, raw), []).append((when, path))
    groups = [g for g in groups.values() if len(g) > 1]
    pools = [p for p in pools.values() if len(p) > 1]
    if progress:
        progress("measuring Auto settings within each shoot")
    audit = {}

    def analyse(path, **shoot):
        try:
            audit[path] = pipeline.process_one(path, do_apply=False, **shoot)[1].audit["auto"]
        except Exception:
            audit.pop(path, None)      # a damaged photo cannot steer the rest of its shoot

    for path in sorted({p for part in (*groups, *pools) for _, p in part}):
        analyse(path)
    scene = {p: a["scene"] for p, a in audit.items() if a["scene"] in (*LANDSCAPES, "landscape")}
    for pool in pools:
        frames = [(p, when, scene[p], audit[p]["direct"]) for when, p in sorted(pool) if p in scene]
        for path, taken in neighbour_scenes(frames, pipeline.auto_cfg["shoot"]).items():
            analyse(path, auto_shoot={"scene": taken})      # its fit under the scene it takes
            if path in audit:
                scene[path] = taken
                biases.setdefault(path, Bias()).auto_shoot = {"scene": taken, "median": {}}
            else:
                del scene[path]
    for group in groups:        # the settings pull keeps its groups and its blocks
        order = [p for _, p in sorted(group, key=lambda item: (item[0] or 0, item[1])) if p in scene]
        for paths in shoot_blocks([(p, scene[p]) for p in order]):
            if len(paths) < 2:
                continue
            votes = [audit[p].get("fit", {}) for p in paths if audit[p].get("target")]
            median = median_fit(votes) if len(votes) > 1 else {}
            for path in paths:
                biases[path].auto_shoot = {"scene": scene[path], "median": median}
```

What stays as it is: the settings pull, with its groups (folder, brightness cluster, the camera's
calendar day, RAW or not), its blocks, `median_fit` and `pull_fit`, and the note in the sidecar
("…; shoot agrees on mist"). Daylight and mist photos of one block are still pulled toward one
median, so photos of a shoot go on looking alike; each is now fitted to the goal of its own scene
first, where the vote fitted all of them to the winner's. A file whose capture time only LibRaw
can read gets its day. On 19 Dolomites files with the fit switched on (`auto_unreleased`), the
code above changed one scene and handed 18 photos a median: 7 daylight and 9 mist in one block, a
twilight frame and a fallback in another.

**5. Tests**, in `tests/test_scene.py`. In
`test_shoot_mist_consensus_preserves_sunset_twilight_transition_and_people` the first assertion
becomes `consistency.shoot_blocks(seq) == [["a", "b", "c"], ["e", "f"], ["g", "h"]]`; the rest of
the test stays.

```python
def test_a_frame_without_its_own_sky_takes_its_neighbours_and_a_frame_with_one_keeps_it():
    cfg = dict(window_min=10, min_frames=2, min_share=.75)
    take = consistency.neighbour_scenes
    deck = [("a", 0, "mist", True), ("b", 60, "mist", True), ("c", 120, "mist", True)]
    assert take(deck + [("q", 90, "daylight", False)], cfg) == {"q": "mist"}
    assert take(deck + [("q", 3000, "daylight", False)], cfg) == {}       # 48 minutes later
    assert take(deck[:1] + [("q", 90, "daylight", False)], cfg) == {}     # one neighbour is not enough
    assert take(deck + [("o", 100, "daylight", True)], cfg) == {}         # blue openings of its own
    assert take(deck[:2] + [("o", 30, "daylight", True), ("q", 90, "daylight", False)], cfg) == {}   # 2 of 3
    dusk = [("a", 0, "twilight", True), ("b", 60, "twilight", True)]
    assert take(dusk + [("q", 30, "daylight", False), ("f", 40, "landscape", False)], cfg) == {"f": "twilight"}
```

- `direct`: with the helpers of the file, a clear sky (`clouds="open"`) and a cloud deck give
  `True`; a frame without sky at a daylight setting, the fallback, an override and Simple's
  three-way answer give `False`.
- `_auto_shoots` with a stand-in pipeline (`auto_cfg = {"shoot": …}` and a `process_one` that
  returns canned `audit["auto"]` dicts, raising for one file) and `consistency.shoot_time` swapped
  for a table of times inside `try`/`finally` (`run_all.py` has no `monkeypatch`): two deck
  frames, a frame without sky between them, a damaged file and a frame an hour later. The middle
  frame gets `auto_shoot == {"scene": "mist", "median": {}}` and is analysed a second time under
  that scene; the late frame and the damaged file get nothing; nobody else's answer changes.
- `load_auto` refuses `min_share = 0.4`, `min_frames = 0` and a missing `[shoot]` table.

**6. Docs.** One CHANGELOG line under F62. In `docs/plans/F62-auto.md`: the decisions row "One
shoot" and the M1 bullet "One shoot, one scene" get the rule above in two sentences. That bullet
also says `plan_groups` "already groups a shoot"; it groups by brightness.

## Left out

- **Overruling a frame that shows its own sky.** Tried in the replay (a lone verdict against three
  or more agreeing neighbours takes theirs): on the Swiss trip it turned 1 to 4 right answers
  wrong and at most one wrong answer right. By eye, a frame's own sky verdict is right in 55 of 60
  cases there, also where its neighbours disagree.
- **A lower share.** The by-eye labels would carry `min_share` 0.6 at a 5-minute window (28
  answers made right, none wrong, instead of 17 and 1). Not taken: those labels call a default
  frame overcast in 38 of 48 cases even where most of its neighbours show blue openings, and a
  second pair of eyes may not. Lower it when independent labels agree.
- **The light of the neighbours.** A dark car interior by day reads as night (the one
  daylight/twilight swap found on the Swiss trip); a frame taken four minutes earlier has four
  stops more light on the land. One case is no basis for a rule.
- **Sunset.** Alpenglow on the peaks under a cool sky has no gold in the sky: none of the eight
  Swiss frames that look like sunset is called sunset (three twilight, three mist, two
  `landscape`), and their neighbours are no better. That is ticket 02's classifier, not this one.
- **A scene the user corrected** does not speak for its neighbours: the app plans photos with
  their own settings apart from the rest.
- **RAW files and camera JPEGs** of one shoot are two pools, as in the pull.
- **A burst counts frame by frame:** five frames of one view are five neighbours. Counting the
  files of one capture second once changes no answer on the Swiss trip.
- **The groups of the settings pull** keep the camera's calendar day, which split one hiking day
  in two on a camera set to another time zone. Ticket 07 owns them.

## Done when

- The tests above pass, `PYTHONPATH=src python tests/run_all.py` is green and the tests stay
  pytest-collectable.
- `autoedit run ./test-corpus ./out --backend stub` completes with sidecars and manifest, and its
  images are byte-identical to the previous commit.
- The session's replay, from the per-frame records and the mask cache kept with the other private
  F62 evidence in `out-webloop/f62/harness/neighbours/` (no SAM 3 run): `real_plan.py --all`, which calls the built
  `consistency.plan` on the Dolomites RAW folder, reports one changed answer (the frame without
  sky on the trip's overcast afternoon becomes mist) where today's code changes 17.
  `replay.py swiss`, which calls the built `neighbour_scenes`, reports 20 (18 daylight to mist, 2
  `landscape` to daylight) where today's vote changes 117. The development scorer of ticket 02
  still prints 73/97: the answer of a photo alone does not move.
- `python scripts/check_public_repo.py` passes. CHANGELOG and the two places in the spec updated.
- No git writes: the owner gets a commit block.
- Not part of this ticket's "done": the 85 % bar. It needs the check under Owner steps.

## Owner steps

| | Decision | Proposed |
|---|---|---|
| O1 | May two photos of one shoot have different scenes when each shows its own sky? (Today the majority overrules the minority.) | Yes |
| O2 | Photos for an honest check: whole shoots that nothing here has used | Folders from one or two of the owner's own trips, as they came off the card, kept private like the other corpora |

## Comments

2026-11-26 — Planner (Fable, max). Facts about the vote that exists, a design, and what the photos
on disk can and cannot show. Nothing in `out-webloop/f62/scene-check/` was opened and the 120 were
not scored. The Dolomites frames that belong to the 120 went through the classifier as unlabelled
neighbours; none was looked at, and no label of theirs was read.

**The vote that exists.**

- *Built:* yes, `consistency.shoot_scenes` and `_auto_shoots`, with ticket 02.
- *On:* in every Auto batch, from `autoedit run` (unless `--no-consistent`) and from the app's
  jobs. Not for a photo run or previewed alone, unless it replays a shoot saved by a batch.
- *Groups:* same folder, same brightness cluster (`plan_groups`: mean brightness of a small proxy,
  a new cluster at a gap of 0.08), same calendar day on the camera's clock, RAW files apart from
  the others. How many minutes lie between two photos plays no part. On the Dolomites RAW folder
  all 110 files fall into one brightness cluster, so the groups are the camera's seven calendar
  days: 4 to 43 files, up to 23 hours long, one with a gap of 12 hours 48 minutes inside (that
  camera's midnight falls in mid-morning). The 345 Swiss RAW files make three groups of 132, 188
  and 23 files, the first 16 hours long. Files whose capture time Pillow cannot read (25 of the
  97 development files: ARW, CR3, DCR, FFF, NRW, ORF, RAF, RW2, seven DNG and a JPEG) all share
  the day "none". LibRaw reads a time for 90 of the 91 RAW files among the 97, the same second
  as Pillow on 65 of the 67 both can read.
- *Inside a group:* daylight and mist are one phase. A block ends only where sunset or twilight
  begins, the most common answer of a block goes to every photo in it, and a default answer
  counts as a full vote.
- *The scorers went around it.* The development scorer and the scorer of the 120 call
  `process_one` once per photo, with no shoot. Neither number contains the vote. In a batch over
  the Dolomites RAW folder the real `consistency.plan` changes 17 of the 110 answers: all 16
  "mist" answers become daylight, and a morning frame that had fallen back to `landscape` becomes
  twilight, copied from a frame taken 12 hours 48 minutes earlier. Most of the 120 come from that
  folder.

**How it was measured.** Every frame of the two trips went through the real pipeline once (SAM 3,
no forced scene): the 110 Dolomites RAW files and the 345 Swiss RAW files, with each frame's
answer, the branch that gave it and its capture time kept, so a shoot rule replays in seconds.
The replay of today's vote gives the answers of the real `consistency.plan` on all 110 Dolomites
files. The Dolomites folder is a selection: half the gaps between its frames are over 7 minutes.
The Swiss files are close to a whole shoot: half the gaps are under 40 seconds.

| | Dolomites, 110 | Swiss, 345 |
|---|---:|---:|
| Decided by their own sky or light | 89 | 262 |
| Decided by default (daylight) | 18 | 76 |
| Fell back to `landscape` | 1 | 5 |
| People or other | 2 | 2 |
| Answers the existing vote changes | 17 | 117 |
| Answers the proposed rule changes | 1 | 20 |

**How long the light lasts** (no labels needed). Two Swiss frames that both show their weather
give the same verdict, overcast or clear, in 74 to 78 % of pairs taken up to 5 minutes apart, 70 %
at 5 to 15 minutes, 63 % at 15 to 20, 54 % at 20 to 30, and 43 to 50 % from 30 minutes to 4 hours.
Chance on that trip is 50 %. A vote across a day has nothing to stand on. Hiding one frame's
verdict and asking its neighbours with the proposed numbers: where they say overcast, the hidden
verdict is overcast in 74 of 77 cases, where they say clear in 62 of 72, and they speak for 59 %
of the frames. On the thinned Dolomites folder: 1 of 2 and 21 of 22.

**Against labels: the development set.** 83 of the 97 photos have no other frame of their shoot on
disk: web downloads come one frame to a post or one to a folder, and the closest any of them
comes is a frame of the same day five hours away. The 12 Dolomites photos have neighbours, and
two FiveK photos have a frame of their shoot 12 and 33 minutes away, in another folder. All 14
decided by their own sky or light, so the rule changes none of them, with any numbers: 73 of 97
before and after, and "held out by shoot" is the same number. The existing vote, in a batch over
the Dolomites RAW folder, loses one (a cloud deck labelled mist becomes daylight): 72 of 97. The
other folders were not replayed. The development set can show neither a gain nor a loss.

**Against labels: the Swiss trip, by eye.** The Planner labelled 154 Swiss frames from shuffled
camera previews with no answers on them: every frame that decided by default or fell back, every
dusk frame, and samples of the rest. 153 count (one close-up left out); 48 labels are sure. They
are one person's eye, the designer's, and the trip was used to tune earlier revisions: a
development number.

| On the 153 labelled frames | Agree | On the 48 sure | Made right | Made wrong |
|---|---:|---:|---:|---:|
| Each photo alone | 73 | 34 | | |
| Existing vote | 57 | 24 | 21 | 37 |
| A plain vote of everyone within 15 minutes, share 0.75 | 78 | 34 | 9 | 4 |
| Proposed rule (10 minutes, 2 frames, 0.75) | 89 | 40 | 17 | 1 |
| Proposed rule, numbers chosen on the other two days each time (5 minutes, 0.6 every time) | 101 | 44 | 28 | 0 |

- The sample is not the trip. Scaled up by what decided each frame, the trip stands at about 71 %
  alone, 76 % with the rule and 51 % with the existing vote.
- Who is right alone: a sky verdict (deck, ceiling, open) in 55 of 60 labelled cases, the default
  in 12 of 75. The vote loses by overruling the first kind (2 made right, 36 wrong, among 38
  labelled); it gains only where it happens to fill in the second.
- The 18 frames the rule turns to mist had 7 to 22 neighbours each, 77 to 100 % of them
  overcast. Fourteen show no sky at all, three a sky without a verdict, one a clipped sky.
- The share is not pinned down by these labels. A frame that decided by default looks overcast
  in 10 of 10 cases where 90 % or more of its neighbours are, in 42 of 47 between 25 and 90 %,
  and in 11 of 18 below 25 %.
- Of the 21 answers the rule changes on the two trips, 19 are default frames turned to mist and 2
  are fallbacks turned to daylight. No frame there took sunset or twilight from its neighbours:
  that side of the rule has run only in the unit test.

**What it will not do.**

- Move the two spent sets. On the Dolomites folder the rule changes one answer, so the 120 would
  move by one at most. Their 26 misses hold 15 between daylight and mist; the rule reaches the
  ones without a sky verdict, and only in a folder dense enough to have two neighbours within ten
  minutes.
- Mend the sunset and twilight border (see Left out).
- Tell bright overcast from sun where the weather turns within minutes. 58 of the 76 Swiss
  default frames stay daylight: for 47 the neighbours disagree, for 11 they all show an open sky.

**An honest check** needs neighbours, which the labelled sets lack.

- *Photos:* whole shoots, every frame as it came off the card, from trips nothing here has used
  (not the Dolomites trip, not the Swiss trip): eight or more shoot days, with closed cloud on
  part of at least two and broken cloud on two.
- *Labels:* per photo, by a Checker who sees single frames in shuffled order and no answers. A
  random draw of 240 landscape photos for the bar, plus every photo whose answer in the batch
  differs from its answer alone (about 1 in 17 on the Swiss trip), drawn by the session without
  showing which is which.
- *Score:* from one batch run, each photo's answer alone and its answer in the batch. The bar of
  ticket 03 (85 %, no daylight/twilight swap) is read on the batch answer over the random draw.
  The rule itself is judged on the photos it changed: 12 right of 15 or better.
- *Before that, for free:* the Dolomites frames of the 120 are the only labelled trip on disk.
  The set is spent and this design was shaped on that folder's timeline, without its labels, so a
  replay there is a development number. `replay.py dolomites --labels <file>` prints alone,
  existing vote and proposed rule in seconds. Expect about nothing from the rule and up to 17
  answers from the vote. The session decides whether to look.

2026-11-26 (owner): O1 answered yes. A photo that shows its own sky or light keeps its scene; this replaces "one shoot, one scene" from 2026-11-25. O2 is open and does not block the build.

2026-11-26 (session): Built as written (`Scene.direct`, `AutoPhoto.direct`, `[shoot]` in auto.toml, `consistency.neighbour_scenes` and `shoot_blocks` in place of the vote). Replay on the Dolomites RAW folder: 1 changed answer, the overcast-afternoon frame without sky to mist, where the vote changed 17. Swiss development trip: 20 changes (18 daylight to mist, 2 fallback to daylight) where the vote changed 117. Both suites green (1,437 and 1,441 passed), stub images byte-identical, public-repo check passes. A Checker (Opus, high) found no blockers; minor, for later: the `_auto_shoots` test does not reach the settings-pull path of a photo that took its neighbours' scene, nor the branch that drops a photo whose second analysis fails; outside a burst a photo now gets an empty bias (a receipt shows `batch_group: ""`, same render); in bursts mode Auto analyses every timed photo before the first render (time only). The honest check (O2: whole unused shoots, labelled blind) is still open.

2026-11-26 (session): O2, the honest check. The owner gave one card folder from their own trips that nothing here had used: 1,581 camera JPEGs, kept private. Every frame was scored with SAM 3, once alone and once in the batch plan. A Checker (Opus, high) labelled a random 320 blind, from the pixels only: 147 landscapes, 78 of them sure. Random sample, all labels: alone 97/147 (66.0 %), batch 106/147 (72.1 %). Sure labels: 72/78 (92.3 %) in both, with no daylight/twilight swap. By class, alone then batch: daylight 33 then 37 of 41, twilight 47 then 47 of 49, sunset 7 then 7 of 35, mist 10 then 15 of 22. The batch changed 58 frames. A second blind Checker labelled the 46 changed frames outside the sample; all 28 landscapes among them were unsure. Over all 58 changes: 15 became right, 2 that were right became wrong (thin-sky daylight read as mist), and 21 were not landscapes. The other 20 are one lake run that the fallback left without a scene; the batch called it daylight. The two Checkers labelled frames of that run, interleaved, as daylight (first) and twilight (second), so the labels disagree with each other there. Against the second Checker's labels those are 20 daylight/twilight swaps. The 85 % bar is met on sure labels and missed on all labels. Most misses are warm afterglow over unlit land, read as twilight (13 of 28 sunset misses) or as mist (8), and nearly all of those were labelled unsure. Scripts and labels: `out-webloop/f62/sdcf/` (private).

2026-11-26 (owner): The disputed lake run is dawn before sunrise, with the land unlit: twilight. The camera data agrees. The run is a 10-second timelapse at a fixed aperture and ISO, the shutter shortens steadily (the light is rising), and the first sun reaches the peak nine minutes after the run ends.

2026-11-26 (session): Re-scored with the owner's label on the 26 frames of the run. Random sample: alone 97/147 (66.0 %), batch 102/147 (69.4 %). Sure and owner labels: 72/85 (84.7 %) in both. Daylight/twilight swaps: 1 alone, 5 in the batch. Over all 58 batch changes: 11 right, 2 made wrong, 21 not landscapes, and 23 dawn frames turned from undecided into daylight. Cause: a timelapse that runs from twilight into daylight. The 10-minute window of a frame in the middle holds a few twilight frames before it and many daylight frames after it. The 75 % share is met, so the later scene spreads backwards. The rule never turns daylight into twilight, but it does turn undecided twilight into daylight. So the honest check misses both bars: 69.4 % against 85 %, and 4 swaps added in the random sample. A second, smaller finding: the 18 frames between the run and the first sun on the peak have a clear pre-dawn blue sky, and Auto reads them alone as daylight, directly. They are not in the labelled sets.
