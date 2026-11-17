# F37. Point and tell

Status: spec (2026-11-16), with a first measurement and its second reading (T0, done). The
owner's decisions are made, except where F37 goes in the order (decision 3). Size L. Not yet in
[FEATURE_PLAN.md](../FEATURE_PLAN.md): other sessions had that file open while this was written.
The proposed row is the last section.
Builds on [F2](F2-region-instructions.md) (instructed things and their cells),
[F3](F3-per-photo-adjustments.md) (changes kept per photo), [F18](F18-shaped-light.md) (shapes),
[F19](F19-follow-up-edits.md) (follow-ups in Studio), [F20](F20-free-text-instructions.md) (colour
words, the local model) and [F36](F36-speed-and-frameworks.md) (one image pass per photo). It
starts the lessons file that F29 (teaching mode) will complete.

## Goal

In Studio the user drags an oval over something in the photo and types what should change
there: "make this guy more purple". The change is applied through a mask made from the oval. A
button, "Show me how", then walks through what was done: the mask, each tool with its amount,
and a line on what the tool does and what to watch for. The feature works as a plain tool and as
a way to learn.

Until now a place can only be named in words ("the top", "the center", F18) and a thing only by
its name ("the jacket", F2). Nothing lets the user point.

The same works without a browser: four numbers on the command line or in the API.

**The content rule.** A mark adds no new kind of change. It only says where the tools that exist
already act: exposure, white balance, saturation, clarity and contrast, inside the limits of
`instructions.toml`. Nothing is generated, moved, removed or painted over. "More purple" is the
colour verb of F20: a white-balance shift of about 6 to 10 levels of 255 per step, held by the
limits, the region policies and the gates. It cannot turn a blue jacket purple, and it is not
meant to (FEATURE_PLAN.md, "Never").

## What an oval becomes

A **mark** is the oval the user drew, kept as its box: four shares of the frame. It becomes one
of two masks.

| | Thing | Area |
|---|---|---|
| When | The sentence names it: "this guy", "this jacket" | The sentence names nothing ("make this warmer"), or says "area", "part", "spot" |
| The mask | Everything SAM 3 finds under that name that has its centre in the oval | The oval itself, soft at the edge |
| Works like | An instructed thing of F2: cells inside the arbitrated regions, with their policies | A place of F18: a layer of its own, like a vignette |
| Needs | SAM 3 | Nothing. It also runs with the stub |
| Good for | A change that should stop at an outline: a jacket, a person, a tree | Light: a brighter face, a darker corner, a warm spot |

**The default: the sentence decides.** A name means the thing. No name means the area. Without
SAM 3 every mark is an area.

**How the user switches.** To the area: the switch "Oval only" on the mark's chip, or the word
"area" in the sentence. To the thing: name it. The chip of an unnamed mark says so ("Name the
thing to follow its outline: this jacket"). In the API the switch is the mark's `kind`
(`auto` or `area`), on the command line `--mark-kind`.

When a named thing is not found, nothing is applied and the chip says so, with one button that
switches that mark to the oval. There is no silent switch.

Why the oval alone does not look for a thing is measured below.

## What was found in the code (2026-11-16)

1. **A box is an example, not a fence.** The installed `Sam3Model.forward` (transformers 5.12.1)
   takes `input_boxes` and joins them to the text prompt. It then looks through the whole photo
   for what matches the prompt. In the measurement below one box brought back up to 20
   instances, most of them outside it.
2. **A second model exists and is not used.** The same package has `Sam3TrackerModel`, which
   answers a box with one object, the way SAM 2 does. It would load as a second model with its
   own image pass.
3. **Masks are kept by name, and a name is any text.** A box written into the name travels
   through the `Segmenter` interface, both mask caches, the render workers and `autoedit adjust`
   without a change to any of them.
4. **An instructed thing already obeys the arbitration.** `instructions.plan` cuts the arbitrated
   regions into cells, each with the policies of every region it belongs to, and
   `edit/apply.py` blends them so that no pixel is edited by two regions. A thing mark is one
   more such layer.
5. **Nothing limits `tint` on skin.** The skin policy caps `temp` at 6 and has no entry for
   `tint`. "Purple" is tint -90 and temp -10: on skin the warmth is held and the tint is not. By
   the op's arithmetic a skin tone then loses about 6 levels of 255 in green while red and blue
   move by about 1: a magenta cast. With a tint limit of 6 it would be 0.4 levels. The same is
   true today for typed changes (F20): on the synthetic test photo, "make the skin pinker"
   leaves tint -100 on the skin region. The oversaturation gate does not catch it: a shift of
   the middle channel does not make a pixel more colourful by its measure.
6. **A small person has no skin region.** Skin under 4 % of the frame counts as absent (a guard
   against red dots on rock), and the `dramatic` and `cinematic_preserved` styles do not look
   for skin at all. A circled hiker would be recoloured whole, with no policy in the way.
7. **The reader is close.** "make this guy more purple" parses today as purple on a thing
   called "guy". "make this more purple" is refused ("no target"): "this" announces a target and
   none follows.
8. **F36 keeps the last photo's image features** in the SAM 3 backend. A new name on the same
   photo is one light pass.

## Measured before the design (2026-11-16)

A first run by the session that wrote this spec. SAM 3 on this computer's GPU, analysis size
1536, each photo encoded once (F36's route). The boxes, the script, the numbers and the sheets
are scratch files in `out-f37/` and are not committed.

**The machine was not quiet.** Load average 5 to 14 on 10 cores from other programs. The script
looked for other AutoEdit jobs before every photo and saw no SAM 3 job. Read the times as upper
bounds.

**The boxes.** 51 boxes on the 16 photos of `out-loop/panel.txt`, drawn by hand on small copies
before any model ran, and loose on purpose. 38 are `whole`: a thing that fits in the box (a
person, a backpack, a hat, a face, a tree, a hut, a rock in water, a cloud, a whale's back). 13
are `part`: a stretch of something bigger (sky, water, a meadow), where the right answer is "no
thing here". Three photos show a person. One box was misdrawn and is left out of the counts.

**Three routes per box,** each on the photo's kept image features:

- A: the box alone as SAM 3's prompt.
- B: the box and a word.
- C: the word alone, as the engine asks today.

In all three, only what has its centre in the box is kept.

| | A: box alone | B: box and word | C: word alone |
|---|---|---|---|
| Whole boxes judged right (of 37) | 19 | 26 | 28 |
| Judged partly right | 14 | 10 | 8 |
| Wrong, or nothing | 4 | 1 | 1 |
| Part boxes answered "no thing" or "bigger than the mark" (of 13) | 1 | 6 | 12 |
| Same mask when the oval is drawn again, 15 % bigger and 10 % off to one side (overlap 0.85 or more, of 38) | 10 | 14 | 34 |
| Seconds per pass, median (90th percentile) | 0.41 (0.49) | 0.41 (0.47) | 0.40 (0.46) |

The image pass took 2.05 s (median; 1.4 to 2.7 s). No call failed.

**Who judged.** The session, from sheets with panels of about 250 pixels. "Right" means the
thing meant, whole, with at most a thin rim of anything else. This is the first reader's count,
at a small size. A second reader's blind count is under "T0, done" below; the two agree on the
order of the routes.

**What was seen:**

- **A box alone is not usable.** On a large, clear thing (a person, a jacket, a path) it is
  right. On a small thing it paints the box's surroundings: the fog round a hat, the sky round a
  head of hair, the water round a whale. On a stretch of sky or water it returns an arbitrary
  patch as "the thing", 12 times of 13. The same oval drawn a second time gives another mask in
  28 of 38 cases.
- **The word alone, with the oval choosing which, is the best route** and needs no box prompt at
  all. Its masks do not move when the oval is redrawn (34 of 38), because the oval only picks
  among what the word found. The centre rule did real work: a word found up to 44 instances in a
  photo and the oval kept at most 11.
- **Where it was only partly right:** "man" leaves out the backpack he carries (twice); a rock
  among rocks; a hut with some meadow; three mountains (a piece of the next ridge, or the tips
  only). **Where it failed:** "lamp", a point of light, found nothing.
- **Scores do not tell right from wrong.** The box routes score 0.84 to 0.98 whatever they
  return. The share of the mask inside the box does: on whole boxes it was 0.69 or more (0.86 or
  more on all but two), on part boxes 0.47 or less. One box sits between at 0.65: it covers most
  of a strip of water, and the water came back.
- **The words people type for a person work.** On the three photos with a person: "guy", "dude",
  "hiker", "him" and "this guy" each found him, with his backpack (score 0.75 to 0.99). "man",
  "person" and "human" found him without it. "woman" found nothing on any of the three. A
  loosely wrong word can still find him: "boy" did on two photos, at 0.59 and 0.67.
- **Skin was found where a face shows, and nowhere else.** It covers 5 % of the person with a
  visible face and all of the face box, none of the two people seen from behind, and nothing in
  any of the 38 boxes on the 13 photos without people. Glaciers and snow fields came back as
  snow (92 to 100 % of the mask).

**What this changed in the design:**

1. No box prompt. `input_boxes` stays unused. A thing is found by its name, and the oval picks
   which one: `_run_concept` as it is, plus the centre rule.
2. A thing needs a name. A sentence without one gets the area.
3. `inside_share = 0.6` separates "the thing" from "a piece of something bigger".
4. `guard_min_share = 0.03` fits: skin was 5 % of a person with a visible face.
5. The oval can be loose. That is why no route to see the mask before the render is built.

**The numbers that decide,** for T0:

| Question | Number | Then |
|---|---|---|
| Is the word-alone route good enough to build? | The Checker judges it right on at least 60 % of the whole boxes (22 of 37) | Build as specified. Under that: stop. The feature then ships with the area only, and `Sam3TrackerModel` gets a probe and a spec of its own |
| Does the box help after all? | The Checker's count for B is at least 4 boxes above C's | `segment/sam3.py` gets a box pass for named things (three lines differ, section 4). Otherwise not |
| Is "bigger than the mark" set right? | With `inside_share = 0.6`, at least 11 of the 13 part boxes get no thing, and no whole box that was judged right loses its mask | Otherwise move the value once and say so |
| Is it fast enough? | One pass on kept features under 1 s (median), with the load average written beside it | Otherwise report; nothing else changes |
| Does skin fire where there is none? | "skin" inside a mark on a photo without people: at most 1 box of the 38 (the first run: none) | Otherwise raise `guard_min_share` once |

### T0, done on 2026-11-16: a second reader

The count above is one reader's, at a small size. T0 asked for a second one.

- **The sheets.** The three routes were run a second time on the same boxes. All 153 masks came
  back as in the first run: the same number of kept instances, the same coverage. They were
  drawn as 17 blind sheets: panels of 380 pixels, no numbers, no route names, and the three
  masks of a box in a shuffled order, numbered 1 to 3. The key to the order stayed in a file the
  reader did not open.
- **The reader.** A Checker (Opus, high) judged all 51 rows with the definitions above, without
  the first count. It kept "partly" and "wrong" apart by one line: a mask that holds all of the
  thing plus extra is partly; it is wrong when the thing is about a quarter of the mask or less.
  It saw crops of the analysis picture, not the photos at full size.

| Whole boxes (37): first reader / Checker | A: box alone | B: box and word | C: word alone |
|---|---|---|---|
| Right | 19 / 18 | 26 / 24 | 28 / 29 |
| Partly | 14 / 11 | 10 / 11 | 8 / 7 |
| Wrong | 4 / 8 | 1 / 2 | 0 / 0 |
| Nothing | 0 / 0 | 0 / 0 | 1 / 1 |

The two readers gave the same verdict on 94 of the 111 masks.

| Part boxes (13): the Checker | A: box alone | B: box and word | C: word alone |
|---|---|---|---|
| No mask | 0 | 0 | 2 |
| The bigger thing itself, running on past the box | 1 | 8 | 11 |
| A patch inside the box | 12 | 5 | 0 |

- **Where the readers differ.** The larger panels changed the hut: the masks hold the hut alone,
  and the Checker calls all three right. The Checker is harder on the box routes: where a box
  alone returned a blob of water with the whale in it, or the bridge deck with the tower as a
  hole, the first reader said partly and the Checker says wrong. On the word-alone route they
  differ on three boxes: the hut and one mountain (the Checker: right), and one glacier (the
  Checker: partly, because the mask also takes the snow above and beside it).
- **The misdrawn box.** The Checker flagged it without being told: the box holds mostly slope,
  with the trees in a thin band at its bottom. Counted in, nothing changes (29 of 38 for the
  word alone).
- **Unsure.** The Checker marked nine rows as unsure. Two of them are "right" calls for the
  word-alone route (a tower, a mountain). With both counted as partly, that route is right on
  27 of 37.

**The rules, applied to the Checker's count:**

| Question | Result | So |
|---|---|---|
| Is the word-alone route good enough to build? | Right on 29 of the 37 whole boxes (78 %). 22 were needed | Build as specified |
| Does the box help after all? | Box and word: 24. It needed 33, which is 4 above the word alone | No box pass |
| Is "bigger than the mark" set right? | 12 of the 13 part boxes get no thing (11 were needed). No mask the Checker calls right is lost: the lowest share inside the box among them is 0.86 | `inside_share = 0.6` stays |
| Is it fast enough? | 0.40 s per pass (median) at a load average of 5 to 14. Under 1 s was needed | Yes |
| Does skin fire where there is none? | In none of the 38 boxes on photos without people | `guard_min_share = 0.03` stays |

The blind sheets, the key, the Checker's verdicts and the masks are scratch files in `out-f37/`,
beside those of the first run.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| What the user draws | An oval, by dragging. It is kept as its box, four shares of the frame with four decimals. No freehand outline: both kinds need only the box, and a soft oval is what an "area" is in every editor | A polygon shape in `edit/mask.py` and a second drawing mode |
| Whose sentence | Each mark has its own sentence, and every clause of it is about the mark. A clause that names another place ("the top", "the whole photo") is not understood | `parse` in `instructions.py` |
| A thing needs a name | Measured: the box alone is right on half the boxes. The name finds the thing, the oval picks which, and the name also picks the policy ("this face" obeys the skin limits) | A box pass in `segment/sam3.py` (section 4) |
| What "inside the oval" means | What has its centre in the box. If more than 40 % of it lies outside the box, it is bigger than the mark (a stretch of sky) and nothing is applied | `inside_share` under `[marks]` |
| Nothing found | Nothing is applied. The chip offers the oval. No silent switch | `mark_adjustments` in `web/service.py` |
| Size words on a mark | Not understood: a mark has the size that was drawn | `parse` |
| Area marks and protected regions | Inside an area mark, skin and snow that the run found keep their limits. F18's named places follow in T8 (the owner's decision 4) | `guard = false` under `[shapes]` |
| Without SAM 3 | Every mark is an area. The stub is not taught to find things: a thing mark that reaches it is "not found", like any name it does not know | Nothing to reverse |
| Where the sentence is read | On this computer: the word list, then the local model (F20). Never the cloud model, as for follow-ups (F19) | Nothing to reverse |
| Where Studio keeps marks | In the photo's follow-up list: same list, same local storage, same Undo | `STATE.followups` in `studio.html` |
| A run setting | None. A mark is asked for per photo, and a run without one is byte-identical | Nothing to reverse |
| Seeing the mask before the render | Not built. The mask comes with the preview, which costs one light pass more. The oval may be loose (measured), so there is little to correct | A route that returns only the mask |
| The second model | `Sam3TrackerModel` is not used | T0's stop rule |
| The lessons file | `configs/lessons.toml` starts here, with the tools a typed change can use. F29 completes it and uses the same reader. It is not a run input: no `RunConfig` path, so the config digest in old sidecars stays valid | Nothing to reverse |

## Design

### 1. Names: `segment/base.py`

A mark is spelled one way everywhere: as the target of its adjustment in the sidecar, as the
name the segmenter is asked for, and as the key in the mask caches.

```python
MARK = "mark:"      # a concept name that is a mark the user drew (F37), not a noun phrase


def mark_name(kind: str, box, word: str = "") -> str:
    """The one spelling of a mark: `mark:thing:x0,y0,x1,y1:word` or `mark:area:x0,y0,x1,y1`.
    The box is in shares of the frame, written with four decimals, so the same oval always
    gives the same name. Raises ValueError for a kind that is not thing or area, a thing
    without a word, numbers that are not finite, a box outside 0..1, x1 <= x0 or y1 <= y0."""


def parse_mark(name: str) -> tuple[str, tuple[float, float, float, float], str] | None:
    """(kind, box, word) of a mark name. None for anything else, a malformed mark included."""
```

Examples: `mark:thing:0.5800,0.1200,0.9200,0.9400:guy`, `mark:area:0.3000,0.3000,0.7000,0.7000`.
A thing always carries its word, an area never does.

A typed target can never be read as a mark: the reader's words hold letters, digits, hyphens and
apostrophes, never a colon or a comma. `autoedit adjust --set` is the one place where a mark
name can be typed, and it goes through `parse_mark` and `mark_name`.

### 2. The reader: `instructions.parse(text, vocab, known=(), mark=None)`

`mark` is `(box, kind)` with kind `auto` or `area`. Without it nothing changes, and every
existing golden table still passes.

With a mark, per clause:

- Rejections stay as they are (a negation, two verbs, two amounts, numbers).
- A place or a whole-photo word in the clause ("the top", "the edges", "the whole photo"), or a
  verb that brings such a place ("add a vignette"): not understood, reason
  `the sentence names another place than the mark`.
- One place is the exception: the light ("the sun", "add a glow"). With a mark, the mark says
  where the light is. The word is dropped, the glow's values go to the mark, and the mark is an
  area: a glow is light, not a thing.
- A size word: not understood, reason `a mark has the size you drew`.
- "No target" is no longer an error.
- Parts still lend each other a name ("brighten and warm this jacket"), and two names give two
  things on one mark ("this guy and his backpack").
- The target is then made from what is left of the part:

```python
def _mark_target(concept: str, area: bool, mark, vocab) -> str | None:
    """The mark as the target of one part. `concept` is what the part names ("guy", "", "thing").
    `area`: the part says "area", or its verb brings a glow. None when the name is too long.
    The name stays as typed: "tree" is not turned into the known concept "trees", because the
    user pointed at one."""
    box, kind = mark
    m = vocab.marks
    drop = {*m["pointing_words"], *m["area_words"]}
    word = " ".join(t for t in concept.split() if t not in drop)
    if len(word) > vocab.max_target_chars or len(word.split()) > vocab.max_target_words:
        return None
    if word and word not in vocab.global_words and kind == "auto" and not area:
        return mark_name("thing", box, word)
    return mark_name("area", box)
```

`area` is read from all the words of the part, before the stop list is applied: "area" and
"part" are stop words today. So an area word wins over a name: "this part of the jacket" is the
area.

Required parses (golden test), with `B` for the box:

| Text | `kind` sent | Result |
|---|---|---|
| `make this guy more purple` | auto | `mark:thing:B:guy`, tint -90, temp -10 |
| `make him more purple` | auto | `mark:thing:B:him` |
| `make this guy and his backpack more purple` | auto | `mark:thing:B:guy` and `mark:thing:B:backpack` |
| `brighten and warm this jacket` | auto | two adjustments on `mark:thing:B:jacket` |
| `brighten that tree there` | auto | `mark:thing:B:tree` |
| `brighten this face` | auto | `mark:thing:B:face` |
| `make this more purple` | auto | `mark:area:B` |
| `more purple` | auto | `mark:area:B` |
| `brighten this thing` | auto | `mark:area:B` ("thing" names nothing) |
| `darker here` | auto | `mark:area:B`, exposure -0.30 |
| `brighten this area a little` | auto | `mark:area:B`, exposure +0.15 |
| `brighten this part of the jacket` | auto | `mark:area:B` |
| `add a glow here` | auto | `mark:area:B`, the glow values |
| `add a warm glow on the sun` | auto | `mark:area:B`, the glow values |
| `brighten this jacket` | area | `mark:area:B` (the name is dropped) |
| `don't brighten this` | auto | rejected (negation), as today |
| `darken the top` | auto | not understood (the sentence names another place than the mark) |
| `make the whole photo warmer` | auto | the same |
| `add a vignette` | auto | the same |
| `a bigger glow here` | auto | not understood (a mark has the size you drew) |

**The local model.** A clause the word list does not understand goes to the local model as any
follow-up does. What it returns is bound to the mark afterwards:

```python
def to_mark(adj: Adjustment, mark, vocab) -> Adjustment:
    """An adjustment read by a model, made to aim at the mark: the model's target becomes the
    name when it is a thing ("person"), and is dropped when it is a place or the whole photo."""
```

`_doubtful` returns False for a mark target, so a two-word name ("red jacket") is not sent to the
model a second time.

### 3. The area: `instructions.mark_spec`, `layer_spec`

An area mark is a place whose oval comes from the box. It reuses `place_alpha` and
`edit/mask.py::radial_alpha` as they are.

```python
def mark_spec(box, feather: float) -> dict:
    """The oval of an area mark as a place for `place_alpha`. The drawn line is where the change
    is at half strength, so the oval on the screen is a true picture of what happens."""
    x0, y0, x1, y1 = box
    k = 1.0 + feather / 2.0
    return {"shape": "oval", "cx": (x0 + x1) / 2, "cy": (y0 + y1) / 2,
            "rx": (x1 - x0) / 2 / k, "ry": (y1 - y0) / 2 / k, "feather": feather}


def layer_spec(name: str, vocab: Vocab) -> dict:
    """The shape of an instruction layer: a named place, or the oval of an area mark."""
    mark = parse_mark(name)
    return mark_spec(mark[1], vocab.marks["feather"]) if mark else vocab.places[name]
```

In `plan`, an area mark is handled where places are: `layers[target] = delta`,
`hit[target] = [LAYER + target]`, size 1. `extra_concepts` skips it: an area needs no mask. In
`pipeline.py` the two places that read `self.vocab().places[name]` call `layer_spec` instead.

### 4. The thing: the word, then the oval (`segment/sam3.py`)

The loop over the concepts in `segment` gets one branch. Everything else stays, the kept image
features of F36 included.

```python
        for concept in concepts:
            mark = parse_mark(concept)
            if mark is not None and mark[0] != "thing":
                continue                        # an area is a shape, not something to find
            instances = self._run_concept(vision, sizes, mark[2] if mark else concept)
            if mark is not None:                # of everything with that name: what was pointed at
                instances = [(m, s) for m, s in instances if _centre_in(m, mark[1])]
            if not instances:
                continue
            ...                                 # union, best score, RegionMask: as today
```

```python
def _centre_in(mask: np.ndarray, box) -> bool:
    """Does the middle of a mask (the mean position of its pixels) lie in a box given in shares
    of the frame?"""
    ys, xs = np.nonzero(mask)
    cx, cy = xs.mean() / mask.shape[1], ys.mean() / mask.shape[0]
    return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]
```

- No new model call: `_run_concept` is used as it is, on `vision_embeds`. `_kept` is not touched.
  The mask keeps the mark's name (`RegionMask(name=concept, ...)`), so the caches file it there.
- If T0 says the box helps after all, the pass for a mark gives the processor the box as well,
  in pixels, and hands `input_boxes` and `input_boxes_labels` on to the model:

  ```python
  prompt = self._processor(text=word, original_sizes=sizes, input_boxes_labels=[[1]],
                           input_boxes=[[[x0 * w, y0 * h, x1 * w, y1 * h]]], return_tensors="pt")
  ```

  That call ran 102 times in the first measurement without an error. It is not built unless T0
  asks for it.

The stub is not changed in what it finds: it returns nothing for a mark, as for any name it does
not know, and the service turns every mark into an area when SAM 3 is not there (section 8). So
the pipeline stays runnable without a model, and the tests of the thing path use a fake
segmenter, as the tests of F2 and F3 do.

Both segmenters get the class attribute that [F21](F21-reveal-check.md) section 6 defines:
`Sam3Segmenter.open_vocabulary = True`, `StubSegmenter.open_vocabulary = False`. If F21's T2 has
landed, they are there already.

`CachingSegmenter` keeps at most 64 mark masks per photo, the oldest dropped first: every new
oval is a new name, and a long session on one photo must not fill the memory.

`scripts/eval_marks.py` (new, small): reads a boxes file, asks the segmenter for each box's mark
name, and writes the numbers and a sheet of the masks. It replaces the scratch script of the
first measurement and goes through the shipped code.

### 5. The plan: `instructions.plan` and `report`

For a thing mark, in the loop over targets:

- **Gates.** The mask counts when its score is at least `min_score` and it covers at least
  `min_coverage` of the frame, both from `[marks]` (a thing the user pointed at may be far
  smaller than a thing found by name alone). Its overlap with a region becomes a cell from
  `min_cell_coverage` under `[marks]`.
- **Bigger than the mark.** When less than `inside_share` of the mask lies inside the box, the
  target gets no cell and `Plan.why[target] = "bigger"`.
- **Owners.** The policies of the word ("face" obeys skin, through `[policy]`), then the mark
  itself: `_policy_owners(word, meta, vocab)` without the word, plus the target.
- **Cells.** The body of today's loop over a target's mask (cut every cell of every group, then
  the part no region owns) moves into an inner function `lay(mask, name, own)` and is called as
  it is today for typed things. For a mark it is called once per protected part (section 6) and
  once for the rest.

`Plan` gains `why: dict[str, str]` (target to the reason nothing was applied) and
`asked: dict[str, Settings]` (the summed change per target, for the walk-through).

One function holds the gates:

```python
def mark_found(rm: RegionMask | None, box, shape, vocab: Vocab) -> str:
    """"" when the mask of a thing mark can be used, else why not: "nothing" (no mask, or under
    the score), "small" (under the coverage), "bigger" (it lies mostly outside the box)."""
```

`report` words a mark as "the marked area" or "the marked guy", and says why when nothing was
applied:

- `instruction "...": no lamp found in the mark (exposure +0.30 not applied)`
- `instruction "...": the sky is bigger than the mark; nothing was applied. Use the oval, or type it without a mark`

**No pixel is edited by two regions.** A thing mark only cuts cells that exist into smaller
ones. The cells of all regions and all marks stay disjoint, and `edit/apply.py` blends each of
them once (tests 8 and 9). An area mark is not a region: it is a shape layer, applied once on
top of the regions, as a vignette or a place of F18 is. Two area marks that overlap are two such
layers, one after the other.

### 6. What protects skin and other guarded regions

| Layer | Thing mark | Area mark |
|---|---|---|
| A region the run found (sky, snow, skin, grass...) | The mark's cells inside it obey its policy: forbidden ops dropped, the others capped (F2, unchanged) | Skin and snow only (the regions marked `guard_global`): their part of the oval gets the change after their policy |
| Skin or snow the run did not find (the style does not look for it, or it is under its frame floor) | SAM 3 is asked for the guarded concepts once more for this photo. Inside the mark, their part gets its own cell from 3 % of the mark | Not looked for |
| The name in the sentence | "this face", "this glacier": the policy of the protected concept it names | Not used |
| Limits | `[limits]` for the sum of typed changes on one mark, then the op's own bounds (unchanged) | The same |
| After the edit | The oversaturation gate, the banding gate, the highlight hold and the review check, on the whole frame (unchanged) | The same |

Details:

- **Guarded concepts for a thing mark** (`pipeline.py`, where `extra` is built): when a thing
  mark is among the adjustments and the segmenter is `open_vocabulary`, every concept with
  `guard_global = true` in `concepts.toml` that is not in the run's set joins `extra`. With the
  `natural` style both (skin, snow) are in the set already and nothing is added.
- **Inside `plan`:** for each guarded concept that is not one of the run's regions, with a raw
  mask at or above its own `min_score`: the part of the mark's mask it covers. From
  `guard_min_share` of the mark, that part is laid first, with the concept among its owners and
  cells named `<mark>/<concept>@<region>`; the rest of the mask follows. A note says so:
  `mark: skin inside it (5 % of the mark) keeps the skin limits`.
- **It never adds a region to the edit.** These masks are used inside marks and nowhere else.
  Outside the mark a photo renders exactly as without it, the scene is classified as before, and
  no skin rule fires because of them (a test).
- **A false alarm only holds back.** Rock that scores as skin inside a mark would keep its
  colour where the rest of the mark changes. In the first measurement that never happened: no
  skin in any box of the 13 photos without people.
- **Area marks** (`pipeline.py`, where the place layers are built): the change is passed through
  `policies.enforce` with the guarded concepts the run found as owners. If that alters nothing
  ("brighten"), the mark is one layer, as a place is. Otherwise it is two: the oval times
  (1 - guard) with the full change, and the oval times guard with the held one, named
  `instruction:<mark>` and `instruction:<mark>/guarded`. The guard is the feathered mask that
  `_build_guard` makes today; that method gains a way to return the mask also when the global
  pass holds none of the guarded ops. The switch is `guard` under `[shapes]`.
- **The skin tint limit** (decided by the owner on 2026-11-16, decision 2): `tint = 6` in
  `[concept.skin] max`, beside `temp = 6`. With it, "make this guy more purple" changes his
  clothes and keeps his skin. No default render changes: no rule sets tint on a region. A run
  with a reference photo can ask for a little more than 6 on a region (several passes of at
  most 5); on skin the limit then holds it. It also limits typed changes on skin ("make the face
  pinker"), which is the point, and test 24 pins it.
- **Snow.** A circled glacier or snow field takes no colour: the snow policy forbids it, as for
  the typed "warm the snow". The note says so, and brightness still works.
- **Not touched:** `edit/gates.py`, `edit/ops.py`, `edit/banding.py`, `quality.py`,
  `quality.toml`, and the order of the stages.

### 7. Where it is stored

A mark is stored as what it is: adjustments of scope `photo` whose target is the mark's name.
The `Adjustment` type does not change.

```json
{"target": "mark:thing:0.5800,0.1200,0.9200,0.9400:guy",
 "ops": {"tint": -90.0, "temp": -10.0},
 "source": "make this guy more purple", "scope": "photo", "status": "clamped"}
```

Around it the sidecar already says the rest: the mark as a found concept (score, coverage,
instances), its cells with their final values under `masks`, an area mark under `composition`,
and one note per adjustment and per policy action.

- **A batch from Studio** carries `photo_marks` per photo; each photo's marks become its
  photo-scope adjustments, as follow-ups do (F19).
- **`autoedit adjust`** replays photo-scope adjustments from the sidecar. The name rebuilds an
  area from its four numbers, and finds a thing's mask in `.autoedit-masks/` under the same name.
  With the cache gone, SAM 3 is asked again with the same word and box. A re-render without a
  change is byte-identical (F3's rule, a test).
- **A full re-run without `--resume`** drops per-photo adjustments, marks included (F3's
  decision).
- **Never stored:** the mask, the picture, a path.

### 8. The API and the command line

A mark in a request:

```json
{"box": [0.58, 0.12, 0.92, 0.94], "kind": "auto", "edit": "make this guy more purple"}
```

| Where | Field |
|---|---|
| `POST /api/v1/preview`, `POST /api/v1/run-one` | `photo_marks`: a list of marks |
| `POST /api/v1/jobs` | `photo_marks`: a map from a photo's path to its list |

No route is added, and the bodies are free-form, so the route snapshot does not change.

Checks, each a `ValueError` and so a 400: `photo_marks` is a list of at most 20 objects (a map of
at most 10,000 entries for a job, every path one of the job's photos); `box` is four finite
numbers in 0..1 with x0 < x1 and y0 < y1 and each side at least `min_side`; `kind` is `auto` or
`area` (`auto` when left out); `edit` is text of 1 to 300 characters; no other key.

`service.mark_adjustments(pipe, marks)` turns the list into adjustments and facts. Per mark:
`auto` becomes `area` when the segmenter is not `open_vocabulary`; `parse` with the mark;
`translate_problems` with `allow_cloud=False`; `to_mark` on what a model read; scope `photo`.

The preview's answer gains three fields:

```json
"marks": [{"index": 0, "kind": "thing", "word": "guy",
           "targets": ["mark:thing:0.5800,0.1200,0.9200,0.9400:guy"],
           "found": true, "why": "", "score": 0.90, "coverage": 0.097,
           "status": "clamped", "problems": []}],
"mark_overlays": [{"target": "mark:thing:...:guy", "alpha": "data:image/png;base64,..."}],
"walkthrough": [ ... ]
```

`why` is empty, `nothing`, `small` or `bigger`. `status` is the weakest status among the mark's
adjustments. An overlay is the mask at the analysis size as a transparent PNG, as
`mask_overlays` are today; for an area it is the soft oval.

**Without a browser:**

```
autoedit run ./photos ./out --backend sam3
autoedit adjust out/IMG_0042.jpg --mark 0.58,0.12,0.92,0.94 --edit "make this guy more purple" --explain
autoedit adjust out/IMG_0042.jpg --mark 0.3,0.3,0.7,0.7 --edit "brighten this a little"
```

- `--mark X0,Y0,X1,Y1`: with it, the whole `--edit` text is about the mark. It needs `--edit`.
- `--mark-kind auto|area` (default `auto`).
- `--explain`: prints the walk-through of this photo's own changes after the re-render.
- `--set "mark:area:0.3,0.3,0.7,0.7.exposure=0.3"` works too: `parse_set` spells a mark name the
  one way.
- One mark per command. Marks add up in the sidecar like any photo adjustment; `--reset` drops
  them.
- `autoedit run` takes no marks: a mark belongs to one photo.

### 9. Studio: the drawing tool (`web/static/studio.html`)

- **A "Mark" button** beside the Masks button. While it is on: the cursor is a cross, the view
  is "after", and a drag on the photo draws an oval (one positioned element with round corners
  inside `#ba`). The split-view drag does nothing while the tool is on. Esc removes the oval. An
  oval under 1 % of the frame on one side is dropped as a slip.
- **The box** is the drag's rectangle as shares of the picture's own box (the rectangle of
  `#baSizer`), so it does not depend on the preview's size.
- **The sentence.** After the drag, "Edit this version" gets the focus and a small "for the
  mark" tag. Apply (or Enter) adds the mark to the photo's follow-up list as
  `{box, kind: "auto", edit}` and renders the preview. A list entry is a sentence or a mark; the
  mark entries are sent as `photo_marks`, the sentences as `photo_edit`. Undo, the remove button
  and Start over work on both. The local storage key stays; entries are checked on load.
- **A mark's chip** shows a small oval, the sentence, the switch "Oval only" (it sets the
  entry's `kind` to `area` and renders again), "Show me how" and the remove button. Pointing at
  the chip shows its oval and what was caught, tinted with the layer the mask overlays use. Its
  state comes from `marks` in the answer:
  - a thing was found: "the guy", applied or held back;
  - no name in the sentence: "the oval. Name the thing to follow its outline: this jacket";
  - a name, nothing found: "No lamp found in the oval", with the button "Use the oval";
  - bigger than the mark: "That is part of something bigger", with the same button;
  - not understood, with the reader's reason.
- **Names on screen.** A mark's name is long. The page shows "marked guy", "marked area", and for
  a cell "marked guy · subject", in the regions list and in "What it did".
- **Batch:** the job request carries `photo_marks` for the checked photos, beside `photo_edits`.
- Text from the server goes on the page through `textContent`, as the chips do today.

### 10. The walk-through: `configs/lessons.toml`, `lessons.py`

"Show me how" opens a panel with the photo, the mask tinted on it, and these steps:

1. **What you pointed at.** The mask and one line on it: "SAM 3 looked for 'guy' and found one
   with its centre in your oval: 10 % of the frame, score 0.90", or "The oval itself, soft at
   the edge". Then the lesson for this kind of mask.
2. **What is inside it.** Only when the mask was split: "5 % of it is skin. Skin keeps its own
   limits."
3. **One step per tool.** Its name, what was asked, what was applied and where ("tint -90 on the
   jacket, held at -6 on skin"), and the tool's three lesson lines.
4. **What was held back,** and by what: a limit, a policy, a gate.
5. **This change on and off.** A switch that shows the photo without this mark. The page asks
   for a second preview without the mark's entry; the masks are cached, so only the pixels are
   worked again.

The lessons are data, three short lines per entry:

```toml
# What each tool does, when editors reach for it, and what to watch for. F37 starts this file
# with what a typed change can use. F29 (teaching mode) completes it for every op.

[op.exposure]
name = "Exposure"
does = "Makes this part brighter or darker, as more or less light would."
when = "A face in shade, a foreground that sits too dark, a sky that pulls the eye."
watch = "Bright parts turn flat white quickly, and a lifted shadow shows its noise."

[op.tint]
name = "Tint"
does = "Shifts the colour between green and magenta."
when = "To take a colour cast off, or to push a mood: pink dusk, purple shade."
watch = "Skin and snow show a tint first. That is why they keep their own limits."

[mask.thing]
name = "Object mask"
does = "The change follows the outline of the thing you named, the one inside your oval."
when = "The change should stop at an edge: a jacket, a tree, a rock."
watch = "Hair, fur and fine branches. Look at the edge at full size."

[mask.area]
name = "Oval (radial filter)"
does = "The change is strongest inside the oval and fades out across its line."
when = "Light, not things: a brighter face, a darker corner, a warm spot."
watch = "A patch that shows means the oval is too small or the change too strong."

[guard.policy]
name = "Protected region"
does = "Skin, snow and other protected regions keep their own limits inside your mark."
when = "Always. A shift that suits a jacket makes skin look ill and snow look dirty."
watch = "If part of your mark changed less, it was held back here."
```

Entries: one `[op.NAME]` for every op in `[limits]` of `instructions.toml` (exposure, temp, tint,
saturation, clarity, contrast); `[mask.thing]`, `[mask.area]`, `[mask.named]` (a region found by
its name), `[mask.place]` (a named part of the frame), `[mask.photo]` (the whole photo);
`[guard.policy]`, `[guard.limit]`, `[guard.gate]`. The wording above is a first draft; the
session reads every line before the feature closes.

```python
# lessons.py
def load(path: str) -> dict:
    """lessons.toml as {"op": {...}, "mask": {...}, "guard": {...}}. A missing file gives empty
    tables: a walk-through then has its facts and no lesson lines."""


def walk(adjs, plan, before, after, vocab, lessons) -> list[dict]:
    """The walk-through of one photo's own changes: one entry per target of a photo-scope
    adjustment, in order. `before` and `after` are the cell settings before and after the
    policies ran."""
```

One entry:

```json
{"sources": ["make this guy more purple"], "label": "the marked guy", "status": "clamped",
 "mask": {"kind": "thing", "target": "mark:thing:...:guy", "score": 0.90, "coverage": 0.097,
          "name": "Object mask", "does": "...", "when": "...", "watch": "..."},
 "parts": [{"label": "skin", "share": 0.05}, {"label": "subject", "share": 0.87},
           {"label": "outside every region", "share": 0.08}],
 "tools": [{"op": "tint", "name": "Tint", "asked": -90.0,
            "applied": [{"part": "skin", "value": -6.0, "state": "held"},
                        {"part": "subject", "value": -90.0, "state": "full"}],
            "does": "...", "when": "...", "watch": "..."}],
 "held": ["skin keeps the skin limits: tint -90 became -6"]}
```

- What a target added in a cell is `after[cell][op] - (before[cell][op] - asked[op])`. A cell
  that two targets reach reports its total and is marked `shared`.
- `walk` covers every photo-scope target: marks, typed things, places and the whole photo. So
  "Show me how" is on every chip of "Edit this version", not only on marks.
- It is made when the photo is rendered: `process_one` gains a read-only observer
  `on_plan(adjs, plan, before, after)`, called right after `instructions.report`, next to the
  existing `on_resolved`. No stage is added or moved. The service builds `walkthrough` and
  `mark_overlays` from it; `adjust --explain` prints it.
- `lessons.toml` is read through `paths.config_path("lessons.toml", packaged_fallback=True)`.

**What F29 reuses, and what stays F29's.** F29 completes `lessons.toml` for every op (its test
"every op has a lesson" replaces the narrower one here), calls `lessons.walk` for the typed part
of an edit, and adds the rule steps and a picture before and after every step. F37 builds none
of F29's three parts: the replay of the whole edit, "try first", and "your style".

### 11. Speed: what the tool needs from F36

| Moment | SAM 3 work | Measured |
|---|---|---|
| First preview of a photo | The image pass, then one light pass per concept | Image pass 2.05 s here, 1.83 s in F36's result; 3.92 s for the whole segment stage there |
| A preview with a new mark on that photo | One light pass for the name. Up to two more the first time, for skin and snow, when the style does not look for them | 0.40 s per pass here, 0.35 s in F36's result. F36 measured the same case for a typed thing: 0.48 s in the segment stage |
| The same mark again (another sentence, another style) | None: the mask is in the cache under the mark's name | 0.02 s in F36's result |
| The oval moved, or another name | One light pass | 0.40 s |
| Another photo in between, or a batch running | The image pass again | |

What F37 needs from F36, and must keep:

1. **T1 and T2 of F36.** Both were closed and committed on 2026-11-16, while this spec was
   written. F37's T2 changes the same file, `segment/sam3.py`, and builds on them.
2. **Everything goes through `segment()`.** A mark is one more name in the list the pipeline
   asks for. So the kept image features and both mask caches apply, and nothing new is kept: no
   second copy of the features, no cache beside the two that exist.
3. **No pass of its own.** F37 never encodes an image and never touches `_kept`.
4. F36's lock for SAM 3 jobs (T4) was built, measured and dropped on 2026-11-16. So two programs
   that segment at the same time still slow each other, a preview with a mark included. Nothing
   to build for that here.

Target, on the development machine with the load average written beside it: the segment stage of
a preview that adds one named mark stays under 1.5 s (median) once the photo has had a preview.

### 12. The follow-up: named places keep the limits too (T8)

Decided by the owner on 2026-11-16 (decision 4). Until T8 is built, F18's rule holds: a named
place ("the top", "the center") applies its change to everything under it, with no region
policy. On the synthetic test photo, "make the center pink" puts tint -100 over the skin today.

- **What changes.** Every layer made by an instruction goes through the guarded split of section
  6, the named places included. Under "make the top pink", skin and snow that the run found get
  the change after their policy, and the rest of the gradient gets it in full.
- **What does not.** A change the policies leave alone ("darken the top", "add a vignette")
  stays one layer and renders exactly as before. The engine's own moves in `compose.toml` (the
  vignette, the sky gradient, the sun glow) are not touched: they are not instructions, and
  changing them would change default pixels.
- **A glow on snow** keeps its brightness and loses its warmth there: the snow policy forbids
  `temp`. That is the rule that keeps snow white everywhere else.
- **The switch** is the one of the area marks: `guard` under `[shapes]`.
- **F18's spec** gets one line under its decisions table: the row "Region policies on an
  instructed shape" was reversed for skin and snow by F37, T8, with the date.

## Config keys: `[marks]` and `[shapes]` in `configs/instructions.toml`

```toml
[marks]
# A mark is an oval the user drew (F37). A sentence that names a thing ("this guy") edits that
# thing inside the oval; any other sentence edits the oval itself, as a soft shape.
feather = 0.35               # area: the edge fades over this share of the radius
                             # (the drawn line is where the change is at half strength)
min_side = 0.01              # a mark narrower than this share of the frame is refused
min_score = 0.45             # thing: below this score the answer is not used
min_coverage = 0.0001        # thing: the smallest mask, as a share of the frame
min_cell_coverage = 0.0001   # thing: the smallest overlap with a region that is its own cell
inside_share = 0.6           # thing: less of the mask inside the box: "bigger than the mark"
guard_min_share = 0.03       # skin or snow inside a mark is its own cell from this share of it
# The sentence asks for the oval itself:
area_words = ["area", "part", "spot", "patch", "corner", "region", "zone"]
# Words that name nothing:
pointing_words = ["thing", "one", "object", "stuff", "bit", "here", "there",
                  "circle", "oval", "mark", "selection"]

[shapes]
# A shape layer made by an instruction is blended on its own: the oval of an area mark and,
# from T8 on, a named place of F18. Inside it, skin and snow the run found keep their limits.
guard = true
```

`load_vocab` reads `[marks]` into `Vocab.marks` and `guard` into `Vocab.shape_guard`, with these
values as defaults, so an older `instructions.toml` in a profile keeps working.

Where the numbers come from: `inside_share` and `guard_min_share` from the first measurement
(whole boxes 0.69 or more inside, part boxes 0.47 or less; skin 5 % of a person). `min_score` is
the value typed things use; the lowest score of a right answer was 0.57. `min_coverage` lets a
thing of about 12 by 12 pixels at the analysis size through; the smallest right answer covered
0.2 % of the frame. `feather` is a first value for the owner's eye.

Not keys: how many marks a request may carry (20) and how many mark masks a photo keeps in
memory (64). They are limits of the service, beside `MAX_PHOTO_EDIT`.

## Files

| File | Change | Task |
|---|---|---|
| `src/autoedit/segment/base.py` | `MARK`, `mark_name`, `parse_mark` | T1 |
| `src/autoedit/instructions.py` | `Vocab.marks`; `parse(..., mark=)`, `_mark_target`, `to_mark`, `mark_spec`, `layer_spec`, `_doubtful` | T1 |
| `src/autoedit/configs/instructions.toml` | `[marks]`, `[shapes]` | T1 |
| `src/autoedit/segment/sam3.py` | the branch in `segment`, `_centre_in`, `open_vocabulary` | T2 |
| `src/autoedit/segment/stub.py` | `open_vocabulary` only | T2 |
| `src/autoedit/segment/cache.py` | at most 64 mark masks per photo in `CachingSegmenter` | T2 |
| `scripts/eval_marks.py` | new: a boxes file in, numbers and a sheet out | T2 |
| `src/autoedit/instructions.py` | `plan`: `lay`, the gates of a mark, protected parts, `why`, `asked`; `mark_found`; `report`; `extra_concepts` | T3 |
| `src/autoedit/pipeline.py` | guarded concepts for a thing mark; area layers with their guarded part; `layer_spec`; `on_plan`. No stage is added or moved | T3 |
| `src/autoedit/configs/concepts.toml` | `tint = 6` in the skin limits (the owner's decision 2) | T3 |
| `src/autoedit/configs/lessons.toml` | new | T4 |
| `src/autoedit/lessons.py` | new: `load`, `walk` | T4 |
| `src/autoedit/web/service.py` | `mark_adjustments`, `photo_marks` in `preview` and `run_one`, the three answer fields | T5 |
| `src/autoedit/web/jobs.py` | `photo_marks` | T5 |
| `src/autoedit/web/app.py` | `photo_marks` passed on | T5 |
| `src/autoedit/adjust.py` | `mark`, `mark_kind`, `explain`; a mark name in `parse_set` | T5 |
| `src/autoedit/cli.py` | `--mark`, `--mark-kind`, `--explain` on `adjust` | T5 |
| `src/autoedit/web/static/studio.html` | section 9, and the panel of section 10 | T6 |
| `tests/test_marks.py`, `tests/test_lessons.py` | new | T1 to T5 |
| `tests/test_sam3_glue.py` | a thing mark in `segment` | T2 |
| `README.md`, `ARCHITECTURE.md`, `CHANGELOG.md` | the feature, the kinds of target, the three flags, the skin tint limit | T7 |
| `src/autoedit/pipeline.py` | the guarded split for every instruction layer, named places included | T8 |
| `docs/plans/F18-shaped-light.md`, `CHANGELOG.md` | one line each: places keep the limits of skin and snow | T8 |

Protected areas (`docs/AGENT_ROUTING.md`): only `pipeline.py` is touched, and its stage order is
not. This spec is the Planner's part; the Checker reviews T3 without exception.

## Tasks

One after the other. F21's open tasks change the segmenters, `pipeline.py`, `web/service.py` and
`studio.html` too, so F37 and F21 are never built at the same time.

| Task | Tier | Content | Proving command |
|---|---|---|---|
| T0 | Checker (Opus, high), then the session | Done on 2026-11-16. The Checker judged blind sheets of the first run's masks row by row, without the session's count. The session applied "The numbers that decide" and wrote both counts into this file | "T0, done": the two counts side by side |
| T1 | Builder (Sonnet, medium) | Sections 1 to 3; tests 1 to 4 | `PYTHONPATH=src python3 -m pytest tests/test_marks.py tests/test_instructions.py tests/test_instruction_words.py tests/test_shaped_light.py tests/test_follow_up.py -q` |
| T2 | Builder (Sonnet, medium) | Section 4; tests 5 to 7 | `PYTHONPATH=src python3 -m pytest tests/test_marks.py tests/test_sam3_glue.py tests/test_segment_cache.py -q` |
| T3 | Builder (Sonnet, high) | Sections 5 to 7; tests 8 to 17 and 24 | `PYTHONPATH=src python3 -m pytest tests/test_marks.py tests/test_instructions.py tests/test_adjust.py tests/test_apply_groups.py tests/test_policies.py tests/test_guard.py tests/test_pipeline.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` |
| T4 | Builder (Sonnet, medium) | Section 10 without the page; tests 22 and 23 | `PYTHONPATH=src python3 -m pytest tests/test_lessons.py tests/test_marks.py -q` |
| T5 | Builder (Sonnet, medium) | Section 8; tests 18 to 21 | `PYTHONPATH=src python3 -m pytest tests/test_marks.py tests/test_follow_up.py tests/test_web_service.py tests/test_jobs.py tests/test_api_contract.py tests/test_security.py tests/test_adjust.py tests/test_cli.py -q` |
| T6 | Builder (Sonnet, high) | Sections 9 and 10 in the page | T5's command, then acceptance check 6 in the browser |
| T7 | Tweaker (Haiku, low) | The exact old and new strings from the session's brief | `PYTHONPATH=src python3 tests/run_all.py` |
| Check | Checker (Opus, high) | The diff against this spec and the invariants of `CLAUDE.md`; the definition of done; the privacy check | Findings as `file:line` |
| T8 | Builder (Sonnet, medium), then the Checker (Opus, high) on its diff | After the Check. Section 12; test 25 | `PYTHONPATH=src python3 -m pytest tests/test_marks.py tests/test_shaped_light.py tests/test_instruction_words.py tests/test_follow_up.py -q` |

Every task ends with `PYTHONPATH=src python3 tests/run_all.py` and
`PYTHONPATH=src .venv/bin/python tests/run_all.py`. T0 is done and says "build as specified". The
owner's decisions are made, except the order (decision 3): T1 starts when the owner says where
F37 goes in the plan.

## Tests

`tests/test_marks.py`, plain functions with `tmp_path`. Inputs: the synthetic image of
`tests/test_pipeline.py::_make` (sky on top, foliage below, a skin-coloured ellipse), and a fake
segmenter that returns given masks by name, with `open_vocabulary = True`.

| # | Test | Asserts |
|---|---|---|
| 1 | `test_mark_name_round_trip` | The two example names of section 1 come out of `mark_name` and back out of `parse_mark`; 0.58004 and 0.58 give the same name; three numbers, x1 <= x0, a value above 1, NaN, an unknown kind and a thing without a word raise in `mark_name` and give None in `parse_mark`; no text that went through `_toks` parses as a mark |
| 2 | `test_parse_binds_the_sentence_to_the_mark` | The table of section 2, exactly; without a mark the same sentences parse as before |
| 3 | `test_a_model_reading_stays_on_the_mark` | A stand-in answer of the local model for "give him a violet tone" (target `person`, tint -60) becomes `mark:thing:B:person`, `via` local; a target `top` or `global` becomes `mark:area:B`; `service.mark_adjustments` never calls a cloud decider |
| 4 | `test_area_oval` | `place_alpha(mark_spec(box, 0.35), hw)`: 1 at the centre; 0.5 within 0.03 at the four points where the drawn line crosses the axes; 0 beyond 1.2 times the radius; a box that touches the frame edge gives finite values |
| 5 | `test_without_sam3_a_mark_is_an_area` | With the stub, `service.mark_adjustments` turns a named sentence into `mark:area`; a thing mark given straight to the pipeline with the stub is `not_found` and the pixels equal the run without it; `StubSegmenter.open_vocabulary` is False |
| 6 | `test_a_thing_mark_is_its_name_inside_the_box` (in `tests/test_sam3_glue.py`, with the fakes there) | `mark:thing:0.0000,0.0000,0.5000,0.5000:snow`: the processor got the text "snow", the model `vision_embeds` and no `pixel_values`; of the fake's two instances only the top-left one is in the mask, `instance_count` is 1, the score is 0.81; a box that holds neither centre gives no mask; an area name makes no model call; a text concept in the same call comes back as today; the encoder ran once for all |
| 7 | `test_caches_take_mark_names` | `DiskMaskCache` and `CachingSegmenter` return a mask stored under a mark name without asking again; the 65th mark name on one photo drops the first |
| 8 | `test_thing_mark_edits_no_pixel_twice` | A mark across sky, subject and background: its cells are disjoint and their union is the mask; through `apply_image`, pixels outside the feathered mark equal the render without the mark within 1e-4, and inside they differ |
| 9 | `test_two_marks_that_overlap` | The cells stay disjoint; the overlap cell carries the base settings plus both changes, applied once |
| 10 | `test_bigger_than_the_mark` | A mask with 30 % inside the box: status `not_found`, the note says "bigger than the mark", pixels equal the run without the mark |
| 11 | `test_a_small_thing_is_edited` | A mask of 0.02 % of the frame inside a region is applied under a mark; the same mask as a typed concept is "too small" |
| 12 | `test_skin_inside_a_thing_mark_keeps_its_limits` | Concepts `["sky"]`, a mark mask, a `skin` mask over 20 % of it (score 0.9): the segmenter was asked for `skin`; the skin part has `skin` among its owners; with "purple", `temp` and `tint` are held at the skin limits (the test reads them from `concepts.toml`); the rest of the mark gets the full change; status `clamped` |
| 13 | `test_a_speck_of_skin_is_ignored` | Skin over 1 % of the mark: no extra cell, the full change everywhere |
| 14 | `test_snow_inside_a_mark_takes_no_colour` | A mark across a snow region, "purple": the snow cell drops tint and temp, its pixels equal the run without the mark; "brighten" passes |
| 15 | `test_area_mark_holds_guarded_regions` | Stub pipeline, an area mark over the skin ellipse, "purple": `composition` lists the mark and its `/guarded` part; inside the skin the change is the held one; with "brighten" there is one layer; with `guard = false` under `[shapes]` one layer with the full change |
| 16 | `test_no_mark_changes_nothing` | A run without marks is byte-identical to the same run with the new code path never entered (compare `adjustments=None` and `adjustments=[]`), and the segmenter was asked for the run's concepts only |
| 17 | `test_a_mark_leaves_the_rest_alone` | With a thing mark and extra guarded masks: the scene, the regions and their settings equal the run without the mark; the bottom rows of the image are unchanged within 1 level |
| 18 | `test_preview_takes_photo_marks` | Stub; `photo_marks = [{box, edit: "brighten this"}]`: brighter inside the oval, an adjustment of scope `photo`, and `marks`, `mark_overlays`, `walkthrough` in the answer |
| 19 | `test_photo_marks_are_checked` | Each rule of section 8 once: 400 through the API and `ValueError` from the service |
| 20 | `test_batch_and_adjust_keep_marks` | A job on two photos with a mark on one: its sidecar holds the adjustment with the mark's name and its image differs from a run without; the other photo is byte-identical; `adjust.rerender(image)` without a change is byte-identical; `reset=True` removes the mark |
| 21 | `test_cli_adjust_mark` | `--mark 0.3,0.3,0.7,0.7 --edit "brighten this"` returns 0 and brightens the middle only; `--mark 0.3,0.3,0.2,0.7` returns 2; `--mark` without `--edit` returns 2; `--explain` prints the mask line and one line per tool |
| 24 | `test_typed_colour_on_skin_is_limited` (T3, the golden test for the new limit) | Stub pipeline on the synthetic image, `edit="make the skin pinker"`: the skin region's `tint` in the result is the limit of `concepts.toml` (-6, where it is -100 today), a note reads `policy[skin]: clamped tint`, the status is `clamped`; without an instruction the same photo renders byte-identical with and without the limit |
| 25 | `test_named_place_holds_guarded_regions` (T8) | Stub pipeline on the synthetic image, `edit="make the center pink"`: `composition` lists `instruction:center` and `instruction:center/guarded`, and inside the skin the tint is the held one; `edit="brighten the center"` gives one layer, and its pixels equal the run with `guard = false`; a run without instructions is byte-identical before and after T8 |

`tests/test_lessons.py`:

| # | Test | Asserts |
|---|---|---|
| 22 | `test_every_typed_tool_has_a_lesson` | Every op in `[limits]` has `name`, `does`, `when` and `watch`, none empty; so have the five mask kinds and the three guards |
| 23 | `test_walk_matches_the_render` | For the photo of test 12: one entry; its mask kind, word and coverage; its parts add up to 1; for every tool, `asked` is the typed change and each `applied` value equals the cell's settings difference; `held` names skin |

## Acceptance checks (the session runs these)

1. Both suites green. The stub smoke run completes with sidecars and a manifest. Default renders
   are byte-identical to the baseline (no mark, no follow-up).
2. T0's two counts are in this file, and its rules were applied.
3. `scripts/eval_marks.py out-f37/boxes.toml`, through the shipped segmenter, gives the first
   measurement's word-alone numbers again: for each of the 51 boxes the same number of kept
   instances, and the same coverage within 0.1 point.
4. With SAM 3, from the command line, on three panel photos (a person, a tree, a rock in water):
   `autoedit adjust --mark ... --edit ... --explain`. Outside the grown mask the export equals
   the one without the mark (largest difference 0 beyond the feather). A second `adjust` without
   a change is byte-identical and does not load SAM 3 (by its time). The preview's mask and the
   export's mask overlap at 0.98 or more.
5. Speed, with the load average written beside it: the target of section 11.
6. In Studio, in the browser, on the photo with a person: draw an oval, type the owner's
   sentence, see the mask and the result; "Oval only" and back; "Show me how" with its steps and
   the on and off switch; a sentence without a name and its hint; a name that is not there;
   Undo; a reload keeps the mark; a batch of two photos, and the sidecar of the one with the
   mark; `autoedit adjust` on that export without a change is byte-identical.
7. Three masks at full size, by eye, in the edited export: hair against sky, a tree's branches,
   a jacket's edge.
8. The tone gate does not apply: no default pixel changes (check 1 proves it).
9. The Checker's review, with the privacy check before the commit block.
10. After T8: both suites green, default renders byte-identical, "make the top pink" on one real
    photo with snow under the gradient (the snow keeps its colour, the sky takes the pink), and
    the Checker's review of that diff.

## Known limits

- **A thing, not a part of one.** A mark on a stretch of something bigger gives "bigger than the
  mark". The oval is the answer for now. Later: the part of a named thing inside the oval ("this
  part of the mountain"), which editors call intersecting two masks.
- **What the name means is SAM 3's call.** "man" came back without his backpack, "guy" with it.
  "this guy and his backpack" names both.
- **Small lights and odd names.** "lamp" found nothing. The chip then offers the oval.
- **Preview and export.** The export analyses the photo at its own size; for a large photo the
  preview starts from a 4096 pixel copy. The two masks of one mark can differ by a few pixels at
  the edge (check 4 measures it).
- **Two things that touch** can come back as one, and a thing behind another as two.
- **How purple.** One step is F20's colour step, and the ceiling is `[limits]`. A second "more
  purple" on the same mark adds little.
- **Geometry.** A mark is tied to the frame. F32 (level, upright, crop) has to move marks with
  the picture, or apply after them. Its spec must say which.
- **Weights.** The mask cache does not notice new SAM 3 weights (F3's limit).
- **The test photos are mountain landscapes,** three of them with a person. Portraits, groups
  and rooms were not measured.
- **Touch screens** were not tried.

## Decisions for the owner

**Made on 2026-11-16.** Asked "Accept the spec's five proposed answers as they stand?", the owner
answered "Yes, as proposed". Four of the five decisions had a proposed answer:

| Decision | The owner's answer | What follows |
|---|---|---|
| 1. The default: a name in the sentence means the thing, no name means the oval itself. The other choice was to let an unnamed oval guess a thing; measured, that guess is right on half the boxes and changes when the oval is redrawn | As specified | Nothing to change |
| 2. Skin gets a tint limit. Until now nothing stopped a typed change from tinting skin: "make this guy more purple" would give his face a magenta cast, and F20's "make the skin pinker" does | Yes | `tint = 6` in `[concept.skin] max` in `concepts.toml`, built in T3 and pinned by test 24. No default render changes |
| 4. F18's named places protect skin and snow too. Until now "make the top pink" reached the skin and snow under the gradient with no region policy | Yes, as a small follow-up | T8, section 12, test 25 |
| 5. This walk-through is enough until F29: the mask, the tools and amounts, a lesson on each, the change on and off. A picture before and after every single tool stays with F29 | Yes | Nothing to change |

**Still open.** Decision 3 had no proposed answer, so the yes does not cover it:

| Decision | Evidence | The change |
|---|---|---|
| 3. Where F37 goes in the order: before or after F21's open tasks? They share files, so one after the other. Proposed now: F37 first. F21's next tasks wait for the measurement on the owner's labelled photos, and F37 waits for nothing | "Tasks"; the F21 row of `FEATURE_PLAN.md` | The order in `FEATURE_PLAN.md` |

## Proposed plan entry

For the table in `FEATURE_PLAN.md`:

`| F37 | Point and tell: circle a thing, say what to change, see how it was done | L | spec, measured and decided; ready to build once its place in the order is set |`

Under "Decisions for the owner" there, only decision 3 of this spec: the order.
