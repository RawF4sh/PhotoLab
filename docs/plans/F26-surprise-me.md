# F26. Surprise me

Status: spec (2026-11-17). Size S. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F26.
Written in an unattended run ([UNATTENDED_RUN.md](../UNATTENDED_RUN.md)): nothing was asked, and
the decisions that are the owner's are in the table below. Built on [F24](F24-bw-film-looks.md).

The design was built once on a scratch copy of the tree (with F24's tasks T1 and T2 in it), which
is deleted again. The reference code is that code and every number is measured. On the copy both
suites passed, on the system Python and on `.venv` (798 tests of that tree, 10 of this spec), and
the Studio part was run in a browser, the page pointed at the stub.

## Goal

Six looks from the library on one photo, side by side, picked by a seed. The user takes one, and
what is taken is a look by name: the batch that follows names it in every sidecar and can be
repeated. `--look surprise --seed N` gives a batch the first of the same six. Default output stays
byte-identical, and no config file, route or run setting is added.

## What was found in the code

1. `variants.py` holds five fixed specs; a spec is a name plus run options.
   `web/service.py::make_variants` renders each (`make_cfg`, `build_pipeline`, `process_one` on a
   1400 px copy, a 1100 px JPEG), sorts by score and answers `image`, `best`, `review`,
   `variants`. Callers: `POST /variants`, `cli.py::_cmd_rank_variants`. No test calls it.
2. The look of a run is `cfg.look or style.look` (`pipeline.py::process_one`); `RunConfig.look` is
   any string. A name no file defines is used as the neutral look, without a word
   (`looks.get(name, Look())`): today `--look surprise` renders that, and the sidecar says so.
3. The sidecar holds the run settings under `audit.run_config`, so an explicit look is at
   `audit.run_config.look`, and `adjust.py::rerender` builds its config from there.
4. The library is `looks.load(cfg.looks_path)`: the packaged `looks.toml` with the profile's file
   merged over it, 13 packaged looks after F24. A look is numbers that shape what the rules
   decided, plus `grain` and `bw`: none needs SAM 3 or a reference photo. The name of a profile
   look is not checked (`paths._check_looks`): it is text from a file.
5. `cli.py::load_run_config` is shared with `calibrate.py` (which has a `--seed` of its own) and
   two scripts. Only `autoedit run` passes through `_cmd_run`.
6. `batch.py::run` writes one `run.json` and one mask cache per output folder: six renders of
   every photo would need six folders and, with SAM 3, six passes over each photo.
7. `tests/test_api_contract.py::_projection` records method, path, parameter names and whether
   there is a body: a new body key does not change `tests/snapshots/api_v1.json`. `make_cfg`
   reads the keys of `_ALLOWED`, `export` and `concepts`; no other key reaches `RunConfig`.
8. Studio builds every request in `editOptions()` and never sends `look`. `renderVariants` writes
   names as markup; `applyVariant` replaces style, palette and prompt. The page always asks for SAM 3.
9. `hash()` of a string changes with every process; `random.Random(seed).sample` and `.shuffle`
   may change between Python versions. `hashlib.blake2b` does not (the grain seed uses it).

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Which looks can come up? | Every look of the library, each with the same chance: the packaged ones (the five old, F24's eight) and the profile's own. None is left out for needing SAM 3 or a reference, because none does (fact 4) | another name in `skip=` in `variants.surprise_for` |
| Is the look the photo has now among the six? | No: its own look, or else the style's. With the default style `natural` never comes up | drop `skip=(now,)` in `variants.surprise_for` |
| May a look come up twice? | Not within one six. A library with fewer than seven looks gives fewer than six. Another seed may show a look again | not meant to be reversed |
| How does the seed pick? | Each name gets a place from a digest of the seed and the name, and the first six places win. The same on every machine and Python version; a new look changes at most one of the six | not meant to be reversed |
| What is a seed, and what without one? | A whole number from 0 to 4294967295. The API draws one below a million and gives it back. The command line asks for one, so a run is repeated by its own command, also with `--resume` | `seed = variants.surprise_seed(seed)` in place of the error in `cli._surprise` |
| What does `--look surprise --seed N` do to a batch? | The batch gets one look: the first of the six. The run prints all six and the one it uses. Not six renders of every photo (fact 6) | `cli._surprise`; six of every photo is a feature of its own |
| What does the sidecar record? | The look by name, at `audit.run_config.look`. Not the word and not the seed: the name repeats the batch, also after the library has changed | a run setting for the seed, which is also a new key of the settings schema |
| A route or a parameter? | `"surprise": true` and `"seed"` in the body of `POST /variants`. The route list does not change (fact 7) | a route that calls `make_variants` the same way |
| The word in other places | `"look": "surprise"` in any API request is refused with a hint. A look of the user's own that is named `surprise` stays a look everywhere | the check in `service.make_cfg` |
| What are the six rendered with? | The request's own settings (style, strength, palette, prompt, reference, decider); only the look differs. A small picture shows what the batch gives | not meant to be reversed |
| Studio | One button, the six in the variants window, a pick sets the look, and a line under the style cards names it. No list of looks | the Studio rebuild |

## Design

### 1. The pick (`variants.py`)

Add `import hashlib` and `import random` above `import numpy as np`, and above `spec_opts`:

```python
SURPRISE = "surprise"     # F26: the word after --look; never a run setting, so never in a sidecar
SURPRISE_COUNT = 6
_SEED_END = 2 ** 32


def surprise_seed(seed=None) -> int:
    """The seed, checked. None draws one below a million, so every answer can be repeated."""
    if seed is None:
        return random.randrange(1_000_000)
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < _SEED_END:
        raise ValueError(f"seed must be a whole number from 0 to {_SEED_END - 1}")
    return seed


def surprise_looks(names, seed: int, count: int = SURPRISE_COUNT, skip=()) -> list[str]:
    """Up to `count` different names, in the order the seed gives them, without those in `skip`.
    A name's place is a digest of the seed and the name: the same on every machine and Python
    version, and a look that is added, removed or skipped leaves the order of the others alone."""
    def place(name: str):
        return hashlib.blake2b(f"{seed}:{name}".encode("utf-8"), digest_size=8).digest(), name
    return sorted((n for n in names if n not in skip), key=place)[:count]


def surprise_for(cfg, seed: int) -> list[str]:
    """The looks the seed picks for a run config: out of its library, without the look it has now."""
    from .looks import load as load_looks
    from .styles import load_styles, style_for

    library = load_looks(cfg.looks_path)
    now = cfg.look if cfg.look in library else style_for(cfg.style, load_styles(cfg.styles_path)).look
    return surprise_looks(library, seed, skip=(now,))
```

Measured with the thirteen names of F24's library, nothing skipped; the same on Python 3.9.6,
3.11.14 and 3.13.7 and under two values of `PYTHONHASHSEED`. Over the seeds 0 to 999 each look is
first 65 to 90 times (77 would be even), and `natural` is among the six for 468 seeds: the reason
the look in use is skipped.

| Seed | The order: the six, then the rest where it is needed |
|---|---|
| 0 | `instant`, `film_faded`, `cinematic_preserved`, `punchy`, `film_warm`, `bw_soft` |
| 7 | `film_warm`, `cinematic_preserved`, `instant`, `bw_red_filter`, `cross_process`, `film_cool`; then `soft_portrait`, `bw_soft`, `dramatic`, `film_faded`, `natural`, `punchy`, `bw_classic` |
| 42 | `natural`, `cross_process`, `bw_classic`, `instant`, `cinematic_preserved`, `bw_soft`; then `punchy` |

### 2. The API (`web/service.py`)

`POST /api/v1/variants` takes two more keys in its body: `"surprise": true` (the pictures are
looks of the library, not the five fixed recipes; a `variants` list beside it is an error) and
`"seed"` (a whole number from 0 to 4294967295; left out, one is drawn). The answer gains `seed`
and `looks`, the names in the order the seed gives. `variants` is as today, sorted by score, one
entry per look: `name` is the look, `opts` is `{"look": name}`, `after` is the picture (a JPEG
data URL, 1100 px), `needs_review` the safety flag. `best` and `review` come from that scoring
and mean little here (six looks that all pass give `review: true`). To use a pick, a page sends
`"look": "NAME"` with its next `/preview`, `/run-one`, `/jobs` or `/run`. Without `surprise`
nothing changes.

The import line becomes
`from .. import batch, curate, emit, instructions, lessons, looks as looks_mod, paths, variants`,
and the line `from .. import variants` inside `make_variants` goes. `web/app.py` does not change.
In `make_cfg`, directly after `cfg = RunConfig(**kw)`:

```python
    if cfg.look == variants.SURPRISE and cfg.look not in looks_mod.load(cfg.looks_path):
        # F26: the word is not a look, unless the library has one of that name. It never reaches a sidecar.
        raise ValueError('"surprise" is not a look: POST /api/v1/variants with "surprise": true, '
                         'then send the name of the look that was picked')
```

In `make_variants`, the line `specs = variants.resolve_specs(opts.get("variants"))` becomes the
block below. The dict it returns is built as today, and when `picks is not None` it gains
`seed=seed, looks=picks` before the `return`:

```python
    seed = picks = None
    if opts.get("surprise"):   # F26: the specs are looks of the library, picked by the seed
        if opts.get("variants"):
            raise ValueError("give surprise or variants, not both")
        seed = variants.surprise_seed(opts.get("seed"))
        picks = variants.surprise_for(make_cfg(opts, configs_dir, profile), seed)
        if not picks:
            raise ValueError("surprise: the look library holds no other look")
        specs = [{"name": n, "look": n} for n in picks]
    else:
        specs = variants.resolve_specs(opts.get("variants"))
```

### 3. The command line (`cli.py`)

In `_add_run_flags`, the help of `--look` gains `; "surprise" with --seed N takes a look the seed
picks and names it`, and below it comes
`p.add_argument("--seed", type=int, help="with --look surprise: a whole number that picks the look (the same number, the same look)")`.
In `_cmd_run`, `cfg = load_run_config(args)` becomes
`cfg = _surprise(load_run_config(args), getattr(args, "seed", None))`, inside the same `try`.
`load_run_config` does not change (fact 5). Above `_cmd_run`:

```python
def _surprise(cfg: RunConfig, seed: int | None) -> RunConfig:
    """`--look surprise --seed N` (F26): the run gets the first of the looks the seed picks, by
    name. The config that comes back never holds the word, so no sidecar does."""
    from . import looks as looks_mod, variants

    if cfg.look != variants.SURPRISE or variants.SURPRISE in looks_mod.load(cfg.looks_path):
        return cfg   # not asked for, or a look of your own has that name
    if seed is None:
        raise ValueError("--look surprise needs --seed N (any whole number from 0 up): "
                         "the seed picks the look, so the run can be repeated")
    picks = variants.surprise_for(cfg, variants.surprise_seed(seed))
    if not picks:
        raise ValueError("--look surprise: the look library holds no other look")
    print(f"surprise, seed {seed}: {', '.join(picks)}\nthis run uses {picks[0]}, the same as --look {picks[0]}")
    return cfg.model_copy(update={"look": picks[0]})
```

The word may also stand in a run file (`look = "surprise"` under `[run]`). The seed is a flag
only; without the word it is ignored. On the smoke corpus seed 7 wrote the bytes of `--look film_warm`.

### 4. Studio (`web/static/studio.html`)

Seven edits. Look names go in through `T` (`textContent`), never as markup (fact 4).

1. After the closing tag of the button `#variantsBtn`:
   `<button class="tbtn" id="surpriseBtn" aria-label="Surprise me" title="Six looks from your library on this photo, side by side; pick one" style="display:none">`
   `<svg viewBox="0 0 24 24"><path d="M12 3l2.2 5.6 5.8.7-4.4 3.9 1.4 5.8-5-3-5 3 1.4-5.8L4 9.3l5.8-.7z"/></svg>Surprise me</button>`
2. After `<div class="styles" id="styles"></div>`:
   `<div class="tip" id="lookNote" style="margin-top:6px"></div>`.
3. `STATE` gains `look:'',` (a look picked with "Surprise me", by name; `''` is the style's own).
4. `editOptions`: after `style:STATE.style,` comes `...(STATE.look?{look:STATE.look}:{}),`.
5. `renderStyles`: after `STATE.style=id;` comes `STATE.look='';renderLook();`.
6. `selectPhoto`: after `$('#variantsBtn').style.display='';` comes `$('#surpriseBtn').style.display='';`.
7. `applyVariant`: after `o=o||{};` comes `STATE.look='';renderLook();`. After the function comes
   the block below. A pick is then part of every request of the page (preview, batch, surprise).

```js
/* ---- surprise me (F26): six looks on this photo; a pick is a look, by name. Names are text (T). */
function renderLook(){const n=$('#lookNote');n.textContent='';if(!STATE.look)return;
  n.appendChild(T('span','','Look: '+STATE.look+' '));
  const b=T('button','minibtn','Clear');b.onclick=()=>setLook('');n.appendChild(b);}
function setLook(name){STATE.look=name||'';renderLook();markPreviewStale();}
$('#surpriseBtn').onclick=()=>{if(STATE.sel<0||!STATE.served)return;$('#varScrim').classList.add('open');runSurprise();};
function runSurprise(){const p=STATE.files[STATE.sel];
  $('#varTitle').textContent='Surprise me: '+p.name;
  $('#varStatus').textContent='rendering six looks...';$('#varGrid').innerHTML='';
  const body=Object.assign(editOptions(),{file:p.path,surprise:true});   /* no seed: the server draws one and names it */
  fetch('/api/variants',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
    .then(r=>r.json()).then(j=>{if(j.error){$('#varStatus').textContent=j.error;return;}renderSurprise(j);})
    .catch(e=>$('#varStatus').textContent=String(e));}
function renderSurprise(j){
  const s=$('#varStatus');s.textContent='Six looks from your library (seed '+j.seed+'). Pick one, or ';
  const more=T('button','minibtn','show six more');more.onclick=runSurprise;s.appendChild(more);
  const by=new Map((j.variants||[]).map(v=>[v.name,v]));
  const g=$('#varGrid');g.innerHTML='';
  (j.looks||[]).forEach(name=>{const v=by.get(name);if(!v)return;
    const c=el('div','rcard'),ph=el('div','ph'),im=el('img'),ft=el('div','ft');
    im.src=v.after;im.alt=name;ph.appendChild(im);ft.appendChild(T('div','nm',name));
    if(v.needs_review)ft.appendChild(T('div','tip','the safety check flags it on this photo'));
    const b=T('button','minibtn','Use this look');b.style.cssText='margin-top:8px;width:100%';
    b.onclick=()=>{$('#varScrim').classList.remove('open');setLook(name);};
    ft.appendChild(b);c.appendChild(ph);c.appendChild(ft);g.appendChild(c);});}
```

## Files

| File | Change | Task |
|---|---|---|
| `src/autoedit/variants.py` | two imports, three constants, `surprise_seed`, `surprise_looks`, `surprise_for` | T1 |
| `src/autoedit/web/service.py` | the import line, the check in `make_cfg`, the two blocks in `make_variants` | T1 |
| `src/autoedit/cli.py` | `_surprise`, one line in `_cmd_run`, `--seed`, the help of `--look` | T1 |
| `tests/test_surprise.py` | new: tests 1 to 10 | T1 |
| `src/autoedit/web/static/studio.html` | the seven edits of Design 4 | T2 |

Not changed: `contract.py`, `pipeline.py`, `web/app.py`, every file under `configs/`, both
snapshots. F24 pieces this relies on: the eight looks in `looks.toml`, most of the pool. None is
named in the code, and the tests bring their own names or count the library, so a look F24 drops
breaks nothing (they also passed with the five old looks alone). Nothing of F23 or F24 changes.
F25 is not needed; where it edits the same places (`_add_run_flags`, the import line and
`_ALLOWED` of `web/service.py`), the edits above are placed by their anchor text.

## Tasks

T1 does not run while another Builder has `cli.py` or `web/service.py` open; T2 can run beside it.

| Task | Tier | Content | Proving command |
|---|---|---|---|
| T1 | Builder, Sonnet, medium | Design 1 to 3; tests 1 to 10 | `PYTHONPATH=src python3 -m pytest tests/test_surprise.py tests/test_variants.py tests/test_web_service.py tests/test_cli.py tests/test_api_contract.py tests/test_profile.py tests/test_calibrate.py tests/test_jobs.py tests/test_settings_schema.py -q` (79 tests, 45 s) |
| T2 | Builder, Sonnet, medium, or the session inline: the text is complete | Design 4 | The page's script still parses (needs `node`; `.tmp/` is the tests' scratch folder): `python3 -c "import pathlib,subprocess;s=pathlib.Path('src/autoedit/web/static/studio.html').read_text();pathlib.Path('.tmp/studio.js').write_text(s[s.index('<script>')+8:s.rindex('</script>')]);raise SystemExit(subprocess.call(['node','--check','.tmp/studio.js']))"` |
| Check | Checker, Opus, high, together with the run's other small features (no protected file) | The diff against this spec, the definition of done, the sheet | none |
| Docs | The session, inline | `APP_GUIDE.md`: section 8c (the two keys, the answer, the button), `--look surprise --seed N` in the command list, the row of `POST /api/variants`; one example in `README.md`; `CHANGELOG.md`; the plan row and the two tables | none |

## Tests

`tests/test_surprise.py`. `_scene(path, w=300, h=200)` is `tests/test_grain.py::_scene` at another
size. `_run(*flags)` calls `cli.main(["run", *flags, "--backend", "stub"])` with stdout and stderr
captured (`contextlib.redirect_stdout`; `tests/run_all.py` has no `capsys`) and returns the code
and the text. `NAMES` is the thirteen names of F24's library, written out in the file; `ORDER_7`
is the row of seed 7 in Design 1. In tests 8 and 10 no test, folder or file name holds `surprise`.

| # | Test | Asserts |
|---|---|---|
| 1 | `test_same_seed_same_six_golden` | `surprise_looks(NAMES, seed)` is the six of Design 1 for 0, 7 and 42; `count=99` gives `ORDER_7`; seed 8 gives another six. The same six from 20 shuffled copies of the list, from `set(NAMES)` and from a dict in reverse order |
| 2 | `test_six_different_fewer_when_the_library_is_small` | Seeds 0 to 49: six names, all different, all of `NAMES`, and more than 40 different sixes. `SURPRISE_COUNT == 6`. Three names give the three, none gives `[]`, `["a"]` with `skip=("a",)` gives `[]` |
| 3 | `test_a_look_added_or_skipped_keeps_the_order_of_the_others` | Seeds 0, 7, 42 with `count=99`: with `"mine"` added, the order without it is the old one; with `natural`, the first or the sixth name in `skip`, the order is the old one without that name. Seed 42 with `skip=("natural",)`: `cross_process`, `bw_classic`, `instant`, `cinematic_preserved`, `bw_soft`, `punchy` |
| 4 | `test_the_seed_is_checked` | 0, 7 and 4294967295 come back; `None` gives a whole number below a million (20 draws); `True`, -1, 4294967296, `"7"`, `7.0` and `[7]` raise `ValueError` |
| 5 | `test_the_pick_for_a_run_config` | With `n = min(6, len(library) - 1)` on the packaged library (6 after F24): `surprise_for(RunConfig(), 7)` is `n` different names of it, equal to `surprise_looks(library, 7, skip=("natural",))`, and the same twice. With `look=` its first name: equal to `surprise_looks(library, 7, skip=(that,))`. With `style="dramatic"`: no `dramatic`. With `look="surprise"`: the first result. The library has no look named `surprise` |
| 6 | `test_the_api_gives_six_looks` | `make_variants(photo, {"backend": "stub", "surprise": True, "seed": 7})`: `seed == 7`; `looks == surprise_for(make_cfg({"backend": "stub"}), 7)`; the names of `variants` are those; each `opts == {"look": name}`; each `after` starts with `data:image/jpeg;base64,` and no two are equal; `best` is one of the names. The same call again: the same `looks` and the same `after` per name. With `"look":` its first name: `looks == surprise_looks(library, 7, skip=(that,))`. Without `seed`: a seed below a million, and `looks` is the pick of that seed. With `with_images=False`: no `after`. A `seed` of -1, `"7"` or `True`, or a `variants` list beside `surprise`: `ValueError` |
| 7 | `test_variants_without_surprise_are_unchanged` | Without `surprise` (also with a stray `seed`, also with `"surprise": False`): the keys are exactly `image`, `best`, `review`, `variants`, and the names are those of `DEFAULT_VARIANTS`. `cli._surprise(cfg, None) is cfg` and `cli._surprise(cfg, 7) is cfg`, for a default config and for `look="punchy"` |
| 8 | `test_the_cli_run_takes_the_first_and_the_sidecar_names_it` | Two photos. `_run(IN, o1, "--look", "surprise", "--seed", "7")` returns 0; its text has `surprise, seed 7: ` with the names of `surprise_for(RunConfig(), 7)` and `this run uses ` with the first. Each sidecar: `audit.run_config.look` is that name, and the text of the file does not hold `surprise`. The same command into `o2` and `--look NAME` into `o3` write the export bytes of `o1`; a default run into `o4` writes others. A run file with `look = "surprise"` under `[run]`, with `--seed 7`: the bytes of `o1` |
| 9 | `test_the_cli_refuses_what_it_cannot_repeat` | Without `--seed`: code 2, the text names `--seed`, and no output folder. `--seed -1`: code 2, no folder. A copy of the packaged configs (`paths._packaged()`) whose `looks.toml` is `[natural]` alone, as `--configs-dir`: code 2, `no other look`, no folder; `make_variants` with that folder raises `ValueError` |
| 10 | `test_the_word_is_never_a_run_setting` | `make_cfg({"look": "surprise"})` raises `ValueError` that names `/variants`; `preview` and `run_one` with it raise, and nothing is written. `run_one` with `"look":` the third name of a surprise answer: `audit.run_config.look` is that name in the result and in the file, whose text does not hold `surprise`. With a profile whose `looks.toml` has `[surprise]`: `make_cfg({"look": "surprise"}, profile=...)` keeps it, `cli._surprise(cfg, None) is cfg`, and `surprise_looks` of that library can give `surprise` |

No existing test changes. On the scratch build none failed first.

## Acceptance checks (the session runs these)

1. `PYTHONPATH=src python3 tests/run_all.py` and `PYTHONPATH=src .venv/bin/python tests/run_all.py`
   are green, with ten tests more than before.
2. The stub smoke run of `CLAUDE.md` completes with sidecars and a manifest. Once more into a new
   folder with `--look surprise --seed 7`: it prints the six and the look it uses; the exports are
   the bytes of a third run with `--look` and that name; each sidecar has the name at
   `audit.run_config.look`, and `grep -l surprise` finds none. Without `--seed`: code 2, no folder.
3. Default output is byte-identical to the baseline renders (maximum difference 0); on the scratch
   build the smoke exports were, and the sidecars differed in the timings only. No config file
   changes, so `config_digest` stays. No tone gate: no exposure, tone or decode step changes.
4. A sheet for the owner, `out-features/F26/six_01.jpg`, from a small script of the session that
   is not committed. For the corpus photos 0002, 0041 and 0109:
   `service.make_variants(photo, {"backend": B, "surprise": True, "seed": 7})`, each `after`
   decoded in the order of `looks`, two rows of three through
   `scripts/contact_sheet.py::_write_sheets` (`_fit` for the size), captioned with the look, the
   review flag, the backend and the seed. SAM 3 through `.venv/bin/python` when no other SAM 3 job
   runs, else the stub (15 s a photo on the scratch build). The Checker looks at it: six
   different looks per photo, and every black and white picture neutral.
5. Studio in the browser, while no other SAM 3 job runs: "Surprise me" shows six small pictures
   with look names and a seed; "show six more" gives another seed; "Use this look" closes the
   window, the line under the style cards reads `Look: NAME`, and the next preview has the name
   at `audit.run_config.look`; "Clear", a style card and "Apply" on an old variant each remove
   it. On the scratch build (the page pointed at the stub) all of this held, the console stayed
   empty, and a look name written as markup was shown as text.

## Known limits

- Some of the six look alike. F24's film casts are mild: on corpus photo 0002 with seed 7 (stub),
  the small pictures of `film_warm` and `instant` differ by 2.3 of 255 and those of `film_warm`
  and `film_cool` by 2.9; `bw_red_filter` differs from each other one by 18 to 22.
- The small pictures are 118 px high in Studio's variants window and come from a 1400 px render.
  A photo's own follow-up sentences and marks are not in them, as for the variants today. The
  command line shows no pictures: its other five looks are a run each.
- Six looks are six renders. On a 24 megapixel JPEG with the stub, on a machine busy with other
  work: 15 to 16 s, against 14 to 15 s for the five variants of today and 16 to 18 s for one
  preview. With `"decider": "llm"` and consent in the request they are six calls to the provider.
- Found on the way, not built: a look name that no file defines renders as the neutral look
  without a word (fact 2), and the sidecars then name a look that was not used. That is a typo
  after `--look`, a profile that was not found, and the word `surprise` where it is not read
  (`autoedit calibrate --look`, `tone_report.py --set look=`). The fix changes runs F26 does not
  touch, in `pipeline.py`, so it is a decision below.

## Proposed plan row

`| F26 | Surprise me | S | done (`--look surprise --seed N`, `"surprise": true` on `POST /variants`, a Studio button); the owner looks at the sheet in `out-features/F26/` |`

For the two tables at the end of `FEATURE_PLAN.md`:

- For the owner: "Should a look name that no file defines be refused?" Evidence: fact 2. The
  change: a small task of its own, a check of `cfg.look` in `Pipeline.build`.
- For the owner: "Should `--look surprise` render every photo six times, not take the first
  look?" Evidence: fact 6. The change: a feature of its own.
- Changed against the plan: "`--look surprise --seed N` gives a batch one look, the first of the
  six, and asks for the seed; the look a photo has now is not among its six." Why: fact 6; a run
  with a drawn seed is not repeated by its own command; and `natural` was among the six for 468
  of 1000 seeds. Reverse it by: the decisions table.

## Result

Built on 2026-11-17 in the unattended run. Status: done.

- **Built** by one Builder (Sonnet, medium), T1 and T2, from this spec. Reviewed together with
  F27 by the Checker (Opus, high): nothing to fix in F26. The seed picks use `blake2b` and sort
  by digest (no `hash()`, no set order) and hold from shuffled inputs; neither the word nor the
  seed reaches a sidecar or `run.json`; `"look": "surprise"` is refused on every route that builds
  a config; the Studio code puts every name through `textContent`.
- **Checks:** both suites green; the stub smoke run is byte-identical to the baseline;
  `--look surprise --seed 7` printed `film_warm, cinematic_preserved, instant, bw_red_filter,
  cross_process, film_cool`, wrote the bytes of `--look film_warm`, and the sidecars name
  `film_warm`. Without `--seed` the command exits 2 and creates no folder.
- **Not done:** the sheet of six looks on three photos (acceptance check 4) and the Studio button
  with SAM 3 in a browser; the button's code was checked with `node --check` only.
