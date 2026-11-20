# F43. Timelapse flicker

Status: spec (2026-11-18). Size M. Off by default.

## Goal

A folder of timelapse frames flickers: each frame is metered and edited on its own, so brightness
jumps from frame to frame. Even it out, while keeping the slow change of light (a sunset gets
darker; that must stay).

## Design

A new consistency mode beside `brightness` and `bursts` (F9), in `consistency.py`:

```python
def timelapse_biases(lums: list[float], window: int, max_ev: float) -> list[float]:
    """Per frame, in order: the exposure bias in EV that moves its luminance to the running median
    of its neighbours (window frames centred on it, shrunk at the ends), clamped to +-max_ev.
    Luminance is compared in log2 (EV)."""
```

- Order: frames sorted by capture time (EXIF), then by file name; the same source of time
  `find_bursts` uses.
- The luminance is the one the mode `brightness` measures after the decided edit (look at how
  `plan` gets it for `bursts` and use the same, so a frame is evened on what it will look like).
- `plan(...)` dispatches `mode == "timelapse"` to it. The biases are applied the way `bursts`
  applies its exposure biases.
- Config in `rules.toml` `[consistency]`:

```toml
timelapse_window = 9      # frames in the running median (odd)
timelapse_max_ev = 0.5    # largest correction of one frame
```

- `--consistency timelapse` (add to the `choices` in `cli.py`), and the settings schema's choices.

## Tests (`tests/test_timelapse.py`)

1. `timelapse_biases` on a slow ramp (luminance falling 2 EV over 60 frames) with random +-0.3 EV
   flicker (fixed seed): after the biases, the frame-to-frame EV differences have a standard
   deviation at least 70 % smaller; the ramp's start and end stay within 0.15 EV of the ramp.
2. A clean ramp gets biases under 0.02 EV.
3. Clamping at `max_ev`; window shrinking at the ends; a single frame gets 0.
4. A stub run on 12 synthetic frames (made in the test with flicker on a ramp, with increasing
   file names) with `--consistency timelapse`: exported frames' mean luminance steps vary less than
   without the flag; with the flag off, output is byte-identical to before.

## Acceptance (the session)

- Both suites; stub smoke run; default renders byte-identical.
- Tone gate with the setting on: 0 and 0 (the corpus is not a timelapse; it shows nothing breaks).
- A real timelapse is open: the corpora hold none.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Brightness only, no white-balance flicker | Most flicker is exposure; WB can follow with `consistency_wb` (F34) | a later step |
| Window and cap | 9 frames, 0.5 EV, the same cap as bursts | `[consistency]` in `rules.toml` |
| A frame may be darkened as well as lifted | flicker goes both ways | add the bursts guard |

## Result

Built on 2026-11-18. After the Checker: RAW and JPEG frames are evened apart; a real sudden change of light (`timelapse_step_ev = 0.75`) is kept by using only the frames on the frame's own side; the window's slope is a Theil-Sen slope, so one frame across a step does not tilt it. On made-up frames: flicker cut by at least 70 %, a clean ramp and a clean 2 EV step get no correction. A real timelapse is open.
