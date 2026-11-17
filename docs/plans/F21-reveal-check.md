# F21. Reveal check

Status: spec (2026-11-16). Size L. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F21.
The blur that follows it is F22 and has its own spec; nothing in F21 changes a pixel.

## Goal

Before a shoot is posted or delivered, the photographer sees which photos give something away:
a number plate, a house number, a screen, a letter, a name badge, a face, a child, a key, a
barcode, and what the file itself carries (GPS position, serial numbers, names). The check runs
on the photographer's computer, across the whole batch, for a list of things that is data and
that the photographer can extend.

It reports. It never says a photo is safe, it never works out who a person is, and it never
edits.

## Step one: the measurement

F21 is "measure first". Two parts: one that needed no photos from the owner and is done, and one
that needs them and is task T0 below.

### Done on 2026-11-16: cost and false alarms on landscapes

The repo's 110 landscape JPEGs, SAM 3 on this computer's GPU, at the analysis size (long edge
1536), 20 words. The scripts and their output were scratch files and are not in the repo.

**Cost.** Today the segmenter encodes the picture again for every word. SAM 3 can encode it once
and run each word against that.

| Route | Measured |
|---|---|
| Today: one full call per word | 4.6 s per word (median of 18 calls on the first six photos, which ran slower than the rest) |
| One image pass, then each word | 2.0 s for the pass (4.0 s at the 90th percentile), 0.41 s per word (0.68 s) |
| The 20 words on one photo, one pass | 10 s (median over the 110 photos) |
| The 20 words on one photo, today's route | about 90 s at 4.6 s per word; about 50 s at the later, faster speed |

The two routes give the same result: on 4 photos and the 7 landscape words (28 pairs, 17 with a
mask) not one pixel differs and the scores agree to four places. All words in one batched call
failed with a shape error in transformers 5.12.1 and was not pursued.

The one-pass route is task T1 of [F36](F36-speed-and-frameworks.md), which found the same thing
on the same day and is being built. F21 does not build it and waits for it (section 11): without
it the check costs 50 to 90 s per photo, with it about 10 s. The first six photos of this probe
shared the GPU with F36's own measurement; the rest ran alone.

**What fired** (score 0.4 or more, the backend's floor). Crops of the findings were looked at.

| Word | Photos of 110 | Scores | What it was |
|---|---|---|---|
| person | 10 | 0.73 to 0.98 | hikers, on the three photos that were looked at; the other seven were not checked by eye |
| face | 7 | 0.48 to 0.83 | one human face (0.82). Five were animals or a carving: a cow, a goat, a marmot, a dog (0.83) and a wooden bear. One was not looked at |
| child | 3 | 0.54 to 0.60 | hikers far away, and two adults with rucksacks. The word reacts to small figures |
| letter | 1 | 0.85, 16 areas | the carved lettering of a summit sign. The word finds written characters, not post |
| street sign | 1 | 0.59 | a signpost on a trail |
| ID card | 1 | 0.41 | a small blue object in a dark frame: a false alarm, under the default of 0.5 |
| the other 14 words | 0 | | |

A second pass on the 23 photos that mattered (every photo with a finding, and six without) tried
other words. "human face" fired once, at 0.81, on the one human face and on none of the animals.
For writing, "letter" is the word that works (0.85); "text" found nothing, "writing" 0.48,
"sign" 0.43.

**What this changed in the design:**

- Faces use the words "human face", not "face".
- People are their own kind, with the word "person", which was steady and did not fire on
  animals.
- Writing is its own kind, with the word "letter". A summit sign names the place, so that
  finding is a true one. A letter in the post is covered by "envelope" and "document".
- "child" was wrong on all three photos it fired on. It stays on the list for T0 with the label
  "possibly a child"; T0 decides whether it stays at all.
- With these words and the default score of 0.5, 3 of the 110 photos carry a wrong finding (the
  three "child" ones).

These photos hold no plates, screens or documents. The probe says nothing about whether the
check finds those.

### Open (task T0): does SAM 3 find the things?

The repo's photos are mountain landscapes; they hold almost none of the things on the list, so
they cannot say whether the check finds a plate. That needs photos that have them.

- **Photos:** at least 100 of the owner's own, in a folder outside the repo: at least 20 with a
  number plate a person can read at full size, 20 with a screen, 20 with a document, sign or
  label, and 40 with none of the listed things. Phone photos and camera files both.
- **Labels:** `scripts/eval_reveal.py label FOLDER` writes a page with one row per photo and one
  tick box per kind; its button downloads `labels.csv` (the pattern of the calibration page,
  F12). A tick means: this photo has at least one such thing that a person can read or recognise
  at full size.
- **Measure:** `scripts/eval_reveal.py run FOLDER --labels labels.csv`. Per kind: photos with
  the thing, photos where the check found it (hits), photos without it where the check reported
  it (false alarms), at the proxy size, and again with tiles (design, section 9).
- **Rules that follow from the numbers:**
  - Number plates found on fewer than 9 of 10 photos that have one, with tiles on: **stop**. The
    feature then needs a second detector, and the owner decides.
  - A kind found on fewer than 7 of 10: it ships with `weak = true`, and every report says "may
    miss" beside it.
  - Each kind's `min_score` is set to the lowest value at which no more than 1 in 10 photos
    without the thing raises a false alarm. A kind that cannot reach that is left off the default
    list and stays available for the profile.
  - Tiles are built only if they raise the hits of at least one kind by 10 points or more.
  - A word that adds no hit beyond the other words of its kind is removed. Each word costs about
    0.4 s per photo.
- The numbers go into this file under "Result of the measurement" before tasks T2 onward start.
  The photos and the labels are never committed.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| On or off by default | Off. `--reveal-check`, or `reveal_check = true` under `[run]` | `defaults.toml`, after the measurement |
| The first list of things | Twelve kinds, 21 words (section 1) | `configs/reveal.toml`, or a `reveal.toml` in the profile |
| What a "finding" is | One connected area of a word's mask. The engine's mask record (`RegionMask`) and the mask cache keep one mask and one score per word and photo, so every area of a word carries the word's best score, and two things that touch are one finding | Carry SAM 3's separate instances through the segmenter interface and the mask cache |
| People and faces | The check reports that a person or a face is there, never whose. In a photo of people, the largest one and any at least 0.6 of its size are listed as "the subject", the others as "other people" | `subject_ratio` in `reveal.toml` |
| Values of metadata | Never read out. The report says "GPS position: in the source, not in the export", not where | none intended |
| Does the check change the edit? | No. With the check on, every exported image is byte-identical to the run without it | none intended |
| With the stub segmenter | The file part runs. The picture part is reported as "not run: needs SAM 3", never as "nothing found" | none intended |
| Reading text in the picture | Not in F21. It needs a local text reader the engine does not have | a later feature |
| An exit code | `autoedit check --strict` ends with 2 when any photo has a finding; without it, 0 | the flag |

## Design

### 1. The list: `configs/reveal.toml` (new)

```toml
[check]
min_score = 0.5       # for a kind without its own value
min_side = 0.006      # smallest side of a finding, as a share of the frame's long edge
max_findings = 30     # per photo; the rest is counted ("and 12 more")
merge_iou = 0.5       # two findings of one kind that overlap this much are one
subject_ratio = 0.6   # see "People and faces" above
tiles = 1             # 1 = the proxy only; 2 = also 2 x 2 tiles (section 9)

[kind.plate]
label = "number plate"
words = ["license plate"]

[kind.address]
label = "house number or street sign"
words = ["house number", "street sign"]

[kind.writing]
label = "writing"
words = ["letter"]              # finds written characters (measured), not a letter in the post

[kind.screen]
label = "screen"
words = ["computer screen", "phone screen", "television"]

[kind.document]
label = "document, letter or label"
words = ["document", "envelope", "whiteboard", "shipping label"]

[kind.card]
label = "card or ticket"
words = ["credit card", "ID card", "ticket"]

[kind.badge]
label = "name badge"
words = ["name tag"]

[kind.people]
label = "person"
words = ["person"]

[kind.face]
label = "face"
words = ["human face"]         # "face" alone also finds animals (measured)

[kind.child]
label = "possibly a child"
words = ["child"]              # fired on distant adults in the probe; T0 decides

[kind.key]
label = "key"
words = ["key"]

[kind.code]
label = "barcode or QR code"
words = ["barcode", "QR code"]
```

A kind may also carry `min_score`, `weak = true` (set by the measurement) and `off = true` (a
profile switches a kind off this way). The values above are the starting point; T0 replaces the
scores.

The file joins the profile layer (F11): `"reveal"` is added to `_CONFIG_FILES` and `_OPTIONAL` in
`paths.py`, and a `reveal.toml` in the profile is merged over the packaged one, so the owner adds
a kind (`[kind.logo] words = ["company logo"]`) without copying the file. A check
`_check_reveal`, in the style of `_check_looks`, refuses: an unknown key; a kind that is not a
table; `words` that is not a list of 1 to 8 strings of 1 to 40 characters made of letters,
digits, spaces and hyphens; a number that is not finite or lies outside its range (`min_score`
0 to 1, `min_side` 0 to 0.2, `max_findings` 1 to 200, `merge_iou` 0 to 1, `subject_ratio` 0 to 1,
`tiles` 1 or 2); more than 40 words in all (each word is a model call).

### 2. `reveal.py` (new)

Measurement only. No function here takes or returns pixels to edit.

```python
@dataclass(frozen=True)
class Kind:
    name: str; label: str; words: tuple[str, ...]; min_score: float; weak: bool

@dataclass(frozen=True)
class Config:
    kinds: tuple[Kind, ...]          # kinds with off = true are left out
    min_side: float; max_findings: int; merge_iou: float; subject_ratio: float; tiles: int

def load(path: str) -> Config
def words(cfg: Config) -> list[str]                      # every word once, in file order
def findings(raw: list[RegionMask], cfg: Config, scene: str) -> tuple[list[RevealFinding], int]
def report(raw, cfg, scene, can_find: bool, source: str | None, metadata: str, fmt: str) -> RevealReport
def summary(reports) -> dict                             # the block for run.json
def write_page(out_dir: str, items: list[tuple[str, str | None, RevealReport]]) -> str
```

`findings`, step by step:

1. For each kind and each of its words, take the `RegionMask` of that name from `raw`, if there
   is one and its `score` is at least the kind's `min_score`.
2. Split the mask into connected areas with `edit/mask.py::blobs` (section 3). Each area is a
   candidate: its box (the smallest rectangle round the area's pixels), its share of the frame,
   the word, the word's score.
3. Drop a candidate whose shorter box side is under `min_side` times the frame's long edge.
4. Inside one kind, merge candidates whose boxes overlap by `merge_iou` or more (two words that
   found the same thing); the merged finding keeps the larger box, the higher score and that
   word.
5. Kinds `people` and `face`, scene `people`, each kind by itself: mark the largest finding and
   every one whose area is at least `subject_ratio` of it with `subject = true`. In any other
   scene nobody is the subject.
6. Order: the kinds in file order, inside a kind by share of the frame, largest first. Keep
   `max_findings`; return the number left out as the second value.

Boxes are `[x0, y0, x1, y1]` as shares of the frame's width and height, rounded to four places,
so they hold for the proxy, the preview and the full-size export alike.

`report` builds the `RevealReport` of section 5: with `can_find` false it sets `picture` to
`"not_run"` and leaves the findings empty; in both cases it adds the file part (section 4).

### 3. Connected areas: `edit/mask.py::blobs` (new, shared)

`light.py` already holds a flood fill over a coarse grid (`_largest_blob`). It moves into
`edit/mask.py` as

```python
def blobs(mask: np.ndarray, grid: int) -> list[np.ndarray]:
    """The 8-connected groups of True pixels, found on a grid of at most grid x grid cells,
    largest first, each as a full-size mask."""
```

`light._largest_blob` becomes `blobs(mask, _GRID)[0]` (or `None` for an empty mask); its tests
stay green unchanged. `reveal.py` calls it with a grid of 192, so two things closer than 8 pixels
on the 1536 proxy count as one. That is enough for a report.

### 4. The file part: `imageio.metadata_report` (new)

```python
def metadata_report(source: str | None, mode: str, fmt: str) -> dict[str, dict[str, bool]]:
    """For each group of tags: is it in the source, and will this export carry it?"""
```

Groups and their tags: `gps` (the GPS directory), `serial` (`_EXIF_SERIALS`), `names` (artist
0x013B, copyright 0x8298, owner name 0xA430), `time` (0x0132, 0x9003, 0x9004), `camera` (make
0x010F, model 0x0110, lens model 0xA434), `description` (0x010E, user comment 0x9286).

"In the export" is not worked out a second time: the function builds the export's metadata with
the code that writes it (`export_exif` for JPEG, WebP and PNG; the tag selection of `_save_tiff16`
for a 16-bit TIFF, which the Builder factors into a helper both use) and reads the answer from
that. The report can then never disagree with the file. A source that cannot be read gives every
group `{"source": false, "export": false}` and never raises: metadata must not fail a photo.

No value is returned, logged or written. A test reads the sidecar and the page and looks for the
test file's GPS numbers and serial number as text.

### 5. `contract.py`

```python
class RevealFinding(BaseModel):
    kind: str
    word: str
    score: float
    box: list[float] = Field(min_length=4, max_length=4)   # x0, y0, x1, y1 as shares of the frame
    share: float                                            # of the frame's area
    subject: bool = False
    weak: bool = False                                      # the kind is marked "may miss"

class RevealReport(BaseModel):
    picture: Literal["checked", "not_run"]
    reason: str = ""                  # for not_run: "needs SAM 3; the stub cannot find named things"
    looked_for: list[str] = []        # kind names, so "nothing found" always says of what
    findings: list[RevealFinding] = []
    more: int = 0                     # findings left out by max_findings
    file: dict[str, dict[str, bool]] = {}

Result.reveal: RevealReport | None = None
RunConfig.reveal_check: bool = False   # with its description for the settings schema (F13)
```

The sidecar of a run without the check gains one line, `"reveal": null`, as earlier features
added theirs. The images do not change.

### 6. Segmenters say whether they can find named things

`Sam3Segmenter.open_vocabulary = True`, `StubSegmenter.open_vocabulary = False`, both class
attributes. `DiskMaskCache` forwards attributes already; `CachingSegmenter` gets a one-line
property that forwards this one. The pipeline reads
`getattr(self.segmenter, "open_vocabulary", False)`. The stub returns nothing for a word it does
not know, which would read as "nothing found"; this attribute is what prevents that.

### 7. `pipeline.py`

- `build`: `self.reveal = reveal.load(paths["reveal"]) if cfg.reveal_check else None`.
- One helper builds the word list for a photo, and both `process_one` and `presegment` use it:

  ```python
  def _segment_words(self, concepts, adjs) -> list[str]:
      extra = self._with_light_concept(instructions.extra_concepts(adjs, concepts), concepts)
      words = concepts + extra
      if self.reveal is not None and getattr(self.segmenter, "open_vocabulary", False):
          words += [w for w in reveal.words(self.reveal) if w not in words]
      return words
  ```

  `presegment` matters: with several render workers the parent segments into the disk cache and
  a worker's segmenter is `_NoModel`, which returns nothing for a word that was not segmented
  before. If the reveal words were missing from `presegment`, every worker would report "nothing
  found". Test 9 guards this.
- `process_one`, after the scene is known:

  ```python
  rep = None
  if self.reveal is not None:
      rep = reveal.report(raw, self.reveal, sc.name, can_find, path, cfg.metadata, cfg.out_format)
      stage_timings["reveal"] = ...
  ```

  and `reveal=rep` into `emit.build_result`. `resolve` is still called with the style's concepts
  only, so a reveal mask is never a region and never edited. The stage order does not change:
  the words ride along in `segment`, the report is made beside `stats` and `scene`.

### 8. Batch, the page, the command

- `batch.run`: when the check is on, after the loop, `run.json` gains

  ```json
  "reveal": {"picture": "checked", "photos": 212, "with_findings": 14,
             "by_kind": {"plate": 6, "screen": 5, "people": 9},
             "file": {"gps": {"source": 180, "export": 0}}}
  ```

  and the last line of the run says "Reveal check: 14 of 212 photos have findings (plate 6,
  screen 5, face 9). See reveal.html." With the stub it says "Reveal check: the pictures were not
  checked (needs SAM 3); file metadata was." The reports are read back from the sidecars, so a
  resumed run counts the photos done earlier too.
- `reveal.write_page` writes `reveal.html` into the output folder: a header that names what was
  looked for and what was not checked, then the photos with findings, most findings first. Each
  has a thumbnail (long edge 640, written to `reveal/<stem>.jpg`, made from the exported image,
  or from the source in a dry run) with the boxes drawn over it as positioned elements, the list
  of findings with their labels ("number plate", "person: other people", "document: may miss"),
  and the file part as plain sentences. After them one line: "198 photos: nothing on the list
  was found. That is not a statement that they are safe to share." Every name is escaped; every
  link is relative; no script and no outside resource.
- `autoedit check FOLDER [--out DIR] [--strict]`: a dry run with the check on (no image is
  written), then the page. `--out` defaults to `FOLDER/autoedit-check`. The masks land in the
  mask cache of that folder, so an `autoedit run` into the same folder afterwards does not
  segment again.
- `autoedit run ... --reveal-check`: the flag, wired as `--light-shaping` is in `cli.py` (the
  argument, the map of command-line overrides, and the list of keys read from a run file).

### 9. Small things: tiles (built only if T0 says so)

The picture is analysed at a long edge of 1536. A plate that is 150 pixels wide in a 6000-pixel
photo is 38 pixels there, and a house number across the street is a few. With `tiles = 2`, the
reveal words (only those) are also run on four overlapping quarters of the full-size image, each
scaled to the analysis size, and the tile masks are mapped back and joined with the proxy's mask
before `findings`. Cost: five passes per word instead of one. The tile masks go through the same
disk cache, keyed by the tile's pixels.

### 10. Studio and the API

- `service.preview` accepts `reveal_check` (added to `_ALLOWED`) and returns the
  result's `reveal` block as it is. No new route, so the route snapshot does not change.
- Studio: a switch "Check before sharing" in the settings panel. With it on, the preview draws
  each finding's box over the image (the boxes are shares of the frame, so plain positioned
  elements do it) and a panel lists them, under the same wording rules as the page. A finding of
  a `weak` kind says "may miss". When the picture part did not run, the panel says so in place of
  a list.
- The "What it did" window gains a section "What it found" with the same list. Nothing was done
  to the picture, and the section says that.

### 11. One image pass for all words: built by F36, needed here

The owner decided on 2026-11-16: the one-pass change is built once, as F36's T1, before F21.
[F36](F36-speed-and-frameworks.md), task T1, makes `Sam3Segmenter.segment` encode the picture
once and run each word against that. Its task T2 keeps the last photo's image features in
memory, so switching the check on in Studio for a photo that is already previewed costs the 21
light passes (about 9 s here) and no new image pass.

F21 adds 21 words to every photo, so its tasks T2 onward start only when F36 is closed. Until
then F21 edits none of F36's files (`segment/sam3.py`, `segment/cache.py` and their tests).
Nothing in F21 touches that code afterwards beyond the `open_vocabulary` attribute of section 6.
F21's task T1 (sections 1 to 5) shares no file with F36 and can be built beside it.

## Wording rules (page, Studio, command line)

1. Never "safe", "clean", "private" or "OK to share".
2. "Nothing found" is always followed by what was looked for.
3. A part that did not run is named as not run.
4. A person is "a person", "a face", "the subject" or "other people". Never a name, an age or
   anything else about them. The kind `child` is shown as "possibly a child".
5. Metadata is named by group and place ("in the source", "in this export"), never by value.

A test holds rules 1 and 5 by searching the page and the Studio strings.

## Files

| File | Change |
|---|---|
| `src/autoedit/configs/reveal.toml` | new |
| `src/autoedit/reveal.py` | new |
| `src/autoedit/edit/mask.py` | `blobs` |
| `src/autoedit/light.py` | `_largest_blob` calls `blobs` |
| `src/autoedit/imageio.py` | `metadata_report`; the TIFF tag selection as a helper |
| `src/autoedit/contract.py` | `RevealFinding`, `RevealReport`, `Result.reveal`, `RunConfig.reveal_check` |
| `src/autoedit/paths.py` | `reveal` in the config files, `_check_reveal` |
| `src/autoedit/segment/sam3.py`, `stub.py`, `cache.py` | `open_vocabulary` |
| `src/autoedit/pipeline.py` | `_segment_words`, the report in `process_one` |
| `src/autoedit/emit.py` | `build_result` takes `reveal` |
| `src/autoedit/batch.py` | the `reveal` block of `run.json`, the summary line, the page |
| `src/autoedit/cli.py` | `--reveal-check`, `autoedit check` |
| `src/autoedit/settings_schema.py` | the description of `reveal_check` |
| `src/autoedit/web/service.py`, `web/static/studio.html` | section 10 |
| `scripts/eval_reveal.py` | new: `label` and `run` |
| `tests/test_reveal.py` | new |
| `README.md`, `CHANGELOG.md`, `ARCHITECTURE.md` | the command, the setting, the module |

## Tasks

Models and effort as [AGENT_ROUTING.md](../AGENT_ROUTING.md) sets them.

| Task | Who | What | Proves itself with |
|---|---|---|---|
| T0 | Builder (Sonnet, medium), then the session with the owner's photos | `scripts/eval_reveal.py` and a throwaway copy of `findings`; the owner labels; the session runs it and writes "Result of the measurement" | the table of hits and false alarms per kind |
| T1 | Builder (Sonnet, medium) | sections 1 to 5: the config and its check, `reveal.py` without the page, `blobs`, `metadata_report`, the contract. Can run beside T0: it needs no model | tests 1 to 8, both suites |
| T2 | Builder (Sonnet, medium), after F36 is closed | sections 6 to 8: segmenters, pipeline, batch, page, command | tests 9 to 15, both suites, the stub run of the definition of done |
| T3 | Builder (Sonnet, medium) | section 10: service and Studio | tests 16 and 17; the browser walkthrough |
| T4 | Builder (Sonnet, medium), only if T0 says so | section 9: tiles | test 18; T0's script again with `tiles = 2` |
| Check | Checker (Opus, high) | the diff of T1 to T4 against this spec, with these questions first: can any path print or store a metadata value; can any path report "nothing found" for a part that did not run; does the check change a pixel; can a `reveal.toml` from a profile make the run slow or unsafe | findings |

## Tests: `tests/test_reveal.py`

| # | Test | Asserts |
|---|---|---|
| 1 | `test_config_loads_and_orders_words` | the packaged file loads; `words` gives every word once, in file order; a kind with `off = true` is gone |
| 2 | `test_config_is_checked` | each bad file is refused with a message that names the key: unknown key, 9 words, a word with a slash, `min_score = 2`, not-a-number, 41 words in all |
| 3 | `test_profile_adds_a_kind` | a profile `reveal.toml` with one new kind: the merged config has the packaged kinds and the new one |
| 4 | `test_blobs` | a mask with three separate squares gives three areas, largest first; an empty mask gives none; `light._largest_blob` returns what it returned before on its own test masks |
| 5 | `test_findings_from_masks` | hand-made masks: two plates give two findings with the right boxes to within one grid cell; a mask under `min_score` gives none; an area under `min_side` gives none; two words of one kind on the same area give one finding; boxes lie in 0 to 1 |
| 6 | `test_subject_faces` | scene `people`, one large and two small faces: the large one is `subject`, the others are not; scene `landscape`: none is |
| 7 | `test_max_findings` | 40 areas with `max_findings = 30`: 30 findings, `more == 10` |
| 8 | `test_metadata_report_never_holds_values` | a test JPEG written with a GPS position, a serial number and an artist: mode `shareable` gives `gps` and `serial` in the source and not in the export, `names` in both; mode `keep` gives all in both; mode `none` gives none in the export; the dumped report contains none of the values as text; an unreadable source gives all false and does not raise |
| 9 | `test_presegment_includes_reveal_words` | with a fake open-vocabulary segmenter that records its calls: `presegment` and `process_one` ask for the same words, and they include the reveal words; with the check off, neither does |
| 10 | `test_stub_reports_not_run` | stub backend, check on: `picture == "not_run"`, a reason, no findings, the file part filled |
| 11 | `test_check_changes_no_pixel` | a fake segmenter that returns masks for the reveal words: the exported image with the check on is byte-identical to the one with it off |
| 12 | `test_sidecar_and_manifest` | a batch of three photos, one with a planted finding: that sidecar holds it; `run.json` has `with_findings == 1` and the count by kind; a resumed run gives the same block |
| 13 | `test_page_wording` | the page has the photo with its box and label; it holds none of "safe", "clean", "OK to share"; "nothing on the list was found" is followed by the list; a file name with `<script>` in it is escaped; no absolute path, no `http` |
| 14 | `test_check_command` | `autoedit check` on a folder writes sidecars and the page and no image; `--strict` ends with 2 when a photo has a finding and 0 when none has |
| 15 | `test_weak_kind_is_marked` | a kind with `weak = true`: its findings carry `weak`, and the page says "may miss" |
| 16 | `test_preview_returns_reveal` | `service.preview` with `reveal_check`: the response holds the block; without it, `reveal` is null |
| 17 | `test_studio_strings` | the Studio page holds the panel, and none of the forbidden words in it |
| 18 | `test_tiles_map_back` (T4) | a thing drawn in one quarter of a large image, too small at proxy size: found with `tiles = 2`, its box in the right place within one grid cell |

## Acceptance checks (the session runs these)

1. Both suites green (`python3` and `.venv/bin/python`). Default renders byte-identical; the stub
   run of the definition of done completes with the check on and reports "not run" for the
   pictures.
2. With SAM 3, the check on the 110 landscape photos: the numbers match the probe in "Step one"
   within the change T0 made to the scores.
3. With SAM 3, on the owner's labelled folder: `autoedit check`, and the page read from top to
   bottom by the session and then by the owner.
4. In Studio, in the browser: the switch on, a photo with a known finding, the box in the right
   place at two window sizes; a photo without findings; the stub backend.
5. Time per photo with the check on and off, written into the result.
