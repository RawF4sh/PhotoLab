# 27. Scenes: an afterglow over land in shadow is a sunset

Status: ready-for-agent
Spec: [F62](../F62-auto.md), milestone M1
Who: Builder (Sonnet, medium), then a Checker (Opus, high) on the diff.

**No owner decision blocks the build.** Sunset is the scene Auto misses most: alone, it names 7
of the 35 sunsets in the random sample of the owner's card folder. This ticket mends the one group
of those misses that the frame itself can settle. A warm afterglow over land in shadow is read as
twilight, because the land came out as dark as night. That is 5 of the 28 misses on the card
folder and 2 of the 6 on the 120. Nothing else can move: the rule only ever turns "twilight,
because the land is at night light" into sunset. On the five sets measured it does so 26 times, 7
of them on labelled photos, and all 7 are made right. What was measured for the other 23 misses
of the card folder is under "Left out". The 85 % bar stays open.

## What to build

**What goes wrong.** `landscape_scene` first asks how much light is on the land. At or below
`night_land_max_ev` it answers twilight ("night exposure and low ambient light") and never looks
at the colour of the sky. Ticket 02 put that branch first for a town at night: its lamps turn the
cloud above it gold, and there is no sun anywhere. The same branch takes a silhouette. A camera
set for a glowing sky leaves the land 3 to 6 stops under mid-grey, so the land reads as night a
few minutes after sunset, while the sky above it is 5 to 8 stops brighter.

On the card folder, 57 landscape frames of one evening were taken within 25 minutes. Two of them
show one sky, 3 seconds apart, with gold on 27 % and 28 % of it. The frame whose land comes out
at 5.7 is sunset. The other holds more foreground in shadow, its land comes out at 4.5, and it is
twilight.

The 28 sunsets of the random sample that Auto misses alone:

| What the frame shows | Read as | Frames | Sure labels | This ticket |
|---|---|---:|---:|---|
| A warm afterglow sky, the land in shadow | twilight | 5 | 0 | sunset |
| A blue or lavender sky with pink-lit cloud, the land unlit | twilight 8, fallback 1 | 9 | 0 | unchanged |
| A pastel sky, alpenglow on a peak | mist | 5 | 1 | unchanged |
| Low sun on the land under a blue sky | daylight | 5 | 2 | unchanged |
| Warm light on the land, hardly any sky or a pale one | mist | 2 | 1 | unchanged |
| A low sun behind cloud, with rays | mist 1, daylight 1 | 2 | 0 | unchanged |

**The rule.** A frame that the night branch would call twilight is a sunset when all three hold:

1. **There is gold in the sky.** The gold test of the next branch passes, exactly as it is today
   (`warm_min_share` of the sky, no rose dusk, a second witness at a sunlit exposure).
2. **The sky is still lit.** The light of the sky is above `afterglow_sky_min_ev`. It is measured
   the way ticket 02 measures the light on the land: the capture EV plus how far the sky came out
   from mid-grey, in stops.
3. **Warm is the sky's main colour.** More than `afterglow_min_share` of the sky's pixels are on
   the warm side of neutral (red above blue).

Each condition is there for frames that are not sunsets:

| Without… | …this frame would lose its twilight answer | Seen in |
|---|---|---|
| gold in the sky | a pale, washed-out dusk sky with a warm cast and no gold in it (it would read as mist) | card folder, a twilight label |
| a sky that is lit | a town at night under cloud that its lamps light, a moon over the sea, a street under lamps, a campfire, a night ridge with a glow on the horizon (each would read as sunset) | development set (a sure twilight), card folder (10 night frames, by eye) |
| warm as the main colour | an orange band on the horizon under a dark blue sky (it would read as sunset) | development set (a sure twilight), card folder (2 twilight labels) |

The third condition needs a measure the classifier does not have. `sky_warmth` is the mean of red
minus blue, and a bright pixel counts for more in a mean than a dark one. A dark blue sky with an
orange band across a third of it has a warm mean. The new measure counts pixels, whatever their
brightness: the share of the sky on the warm side of neutral.

**What it cannot do.** The rule sits in front of one answer only. A frame the night branch does
not take is not asked. A frame it takes either stays twilight or becomes sunset. So no daylight,
mist or fallback answer can change, and no daylight/twilight swap can be added. A twilight answer
can be lost only where all three conditions hold. The second test below holds the code to this on
2,000 random frames.

**The two numbers** were fixed before they were scored on any set and have not been moved
(Comments, "How the rule was kept from fitting one set"):

- `afterglow_min_share = 0.5`. "Main colour" means more than half. It is the wording of the label
  rule that the card folder and the development set were labelled by.
- `afterglow_sky_min_ev = 5.0`. The level the table already calls night, on the land, asked of
  the sky. A sky lit by the sun below the horizon stays above it through most of civil twilight.
  The eleven night frames of the three sets with gold in their sky (a town, a moon, lamps, a fire)
  have skies 4 to 15 stops below it.

**Nothing else changes.**

- A frame without capture EV, without sky or without gold in the sky keeps today's answer. So does
  a caller that passes no share: the new argument defaults to 0.
- The answer stays `direct`, as the twilight answer was, and the sidecar's reason is the gold
  branch's own: "gold light in the sky".
- The dark frame at a high ISO that `night.is_night` flags does not overrule a lit, warm sky. Two
  frames of the card folder's afterglow were taken at ISO 1600 and show the sky of the frames
  beside them.
- Simple's three-way answer (`classify` without thresholds), a scene the user chose and every
  default run are untouched: all of this runs only with the Auto style. No stage of the pipeline
  is added, moved or fused. The `scene` stage hands one more number from `sky_colour` to
  `classify`.

**1 to 3. `src/autoedit/scene.py`, `src/autoedit/pipeline.py`, `src/autoedit/configs/auto.toml`**

The patch below applies cleanly to the working tree of 2026-11-27 (`git apply --check`). The
Planner built it on a copy of the tree and ran that copy through the tests, the stub run and the
real pipeline on every labelled photo (Comments). In `auto.toml`, `styles.py::load_auto` needs no
change: it checks a key ending in `_ev` as a finite number and every other key of `[scenes]` as a
number from 0 to 1.

```diff
--- a/src/autoedit/scene.py
+++ b/src/autoedit/scene.py
@@ -36,14 +36,17 @@
 
 
 def sky_colour(rgb, sky, cfg):
-    """Colour in the sky. Separate gold light from a pink band in otherwise blue twilight."""
+    """Colour in the sky. Separate gold light from a pink band in otherwise blue twilight.
+
+    The last value is the share of the sky on the warm side of neutral (red above blue): its main
+    colour by area. A bright orange band lifts the mean warmth of a dark blue sky, not this share."""
     if sky is None or not sky.any():
-        return 0.0, 0.0, 0.0
+        return 0.0, 0.0, 0.0, 0.0
     r, g, b = rgb[sky > 0].T
     warmth = r - b
     gold = (warmth >= cfg["warm_pixel"]) & (g >= b)
     rose = (r - g >= cfg["rose_red"]) & (b - g >= cfg["rose_blue"])
-    return float(warmth.mean()), float(gold.mean()), float(rose.mean())
+    return float(warmth.mean()), float(gold.mean()), float(rose.mean()), float((warmth > 0).mean())
 
 
 def cloud_cover(rgb, sky, cfg, cloud=None):
@@ -85,7 +88,8 @@
     return None
 
 
-def landscape_scene(g, cfg, sky_warmth, warm_share, rose_share, capture_ev, light, night, clouds=None, fog=0.0):
+def landscape_scene(g, cfg, sky_warmth, warm_share, rose_share, capture_ev, light, night, clouds=None, fog=0.0,
+                    warm_side=0.0):
     """A compact evidence trail for the scene. Ambiguous evidence uses the broader goal."""
     sky = g.sky_cov >= cfg["min_sky"]
     contrast = g.lum_p90 - g.lum_p10
@@ -129,10 +133,17 @@
         g.sky_lum >= cfg["overcast_sky_min"] and g.sky_sat <= cfg["overcast_sat_max"]))
     strong, likely = cfg["strong_confidence"], cfg["likely_confidence"]
     direct = True
-    if very_dark and not bright_capture:
+    gold_sky = sky and warm_share >= cfg["warm_min_share"] and not (dim_capture and rose_dusk) and (
+        warm_light or sky_warmth >= cfg["warm_sky"] or not sunlit_capture)
+    # An afterglow over a silhouette. The land is at night light, but the sky is still lit by the sun
+    # below the horizon (its own light, measured like the land's) and warm is its main colour. A sky
+    # that a town lights at night is far darker, and an orange band under a blue sky is mostly blue.
+    sky_tone = float(srgb_to_linear(g.sky_lum))
+    sky_ev = capture_ev + float(np.log2(max(sky_tone, np.finfo(np.float32).eps) / _MID_GREY)) if metered else None
+    afterglow = gold_sky and metered and sky_ev > cfg["afterglow_sky_min_ev"] and warm_side > cfg["afterglow_min_share"]
+    if very_dark and not bright_capture and not afterglow:
         label, confidence, reason = "twilight", strong, "night exposure and low ambient light"
-    elif sky and warm_share >= cfg["warm_min_share"] and not (dim_capture and rose_dusk) and (
-            warm_light or sky_warmth >= cfg["warm_sky"] or not sunlit_capture):
+    elif gold_sky:
         label, confidence, reason = "sunset", strong if warm_light else likely, "gold light in the sky"
     elif not bright_capture and (metered_dusk or unmetered_dusk):
         label, confidence, reason = "twilight", strong if metered_dusk else likely, (
@@ -165,7 +176,8 @@
 def classify(g: GlobalStatsModel, present: set[str],
              region_stats: dict[str, RegionStatsModel] | None = None,
              override: str | None = None, *, thresholds=None, sky_warmth=0.0, warm_share=0.0,
-             light=None, night=False, rose_share=0.0, capture_ev=None, clouds=None, fog=0.0) -> Scene:
+             light=None, night=False, rose_share=0.0, capture_ev=None, clouds=None, fog=0.0,
+             warm_side=0.0) -> Scene:
     if override:
         broad = "landscape" if override in LANDSCAPES else override
         return Scene(broad, 1.0, override, "scene chosen by the user")
@@ -182,7 +194,8 @@
         return Scene(name="people", confidence=0.7)
     if present & _LANDSCAPE:
         if thresholds is not None:
-            return landscape_scene(g, thresholds, sky_warmth, warm_share, rose_share, capture_ev, light, night, clouds, fog)
+            return landscape_scene(g, thresholds, sky_warmth, warm_share, rose_share, capture_ev, light, night, clouds, fog,
+                                   warm_side)
         return Scene(name="landscape", confidence=0.6)
     if present & _PEOPLE:  # small skin, no landscape context → still a person-ish frame
         return Scene(name="people", confidence=0.55)
--- a/src/autoedit/pipeline.py
+++ b/src/autoedit/pipeline.py
@@ -545,13 +545,14 @@
                           r.score >= meta_for("cloud", self.meta).min_score), None)
             fog = next((float(r.mask.mean()) for r in raw if r.name == "mist" and
                         r.score >= meta_for("mist", self.meta).min_score), 0.0)
-            sky_warmth, warm_share, rose_share = scene_mod.sky_colour(proxy.rgb, sky, thresholds)
+            sky_warmth, warm_share, rose_share, warm_side = scene_mod.sky_colour(proxy.rgb, sky, thresholds)
             detected_light = self._find_light(proxy.rgb, raw, resolved.union, asked=True)
             detected = scene_mod.classify(gstats, present, region_stats, cfg.scene, thresholds=thresholds,
                                           sky_warmth=sky_warmth, warm_share=warm_share, rose_share=rose_share,
                                           capture_ev=(proxy.exif or {}).get("capture_ev100"), light=detected_light,
                                           night=night.is_night(gstats, self.night_cfg),
-                                          clouds=scene_mod.cloud_cover(proxy.rgb, sky, thresholds, cloud), fog=fog)
+                                          clouds=scene_mod.cloud_cover(proxy.rgb, sky, thresholds, cloud), fog=fog,
+                                          warm_side=warm_side)
             choice = AutoPhoto.select(detected, style, self.auto_cfg, requested_strength, cfg.auto_unreleased,
                                       auto_strength, None if cfg.scene else auto_shoot)
             auto_scene, strength = choice.scene, choice.strength
--- a/src/autoedit/configs/auto.toml
+++ b/src/autoedit/configs/auto.toml
@@ -26,11 +26,19 @@
 # Light on the land: the capture EV100 plus how far the land (the frame outside the sky) came out
 # from mid-grey, in stops. A camera set for a bright dusk sky has a daylight EV and dark land.
 # An open sky counts as daylight from day_land_min_ev up and as dusk up to dusk_land_max_ev;
-# between the two it is neither. At or below night_land_max_ev the sun is gone, whatever the sky's colour.
+# between the two it is neither. At or below night_land_max_ev the land is in night light: twilight,
+# unless the sky above it shows an afterglow.
 day_land_min_ev = 8.25
 dusk_land_max_ev = 7.0
 night_land_max_ev = 5.0
 metered_dusk_median_max = 0.50
+# An afterglow over a silhouette is a sunset, however dark the land: gold in a sky that is still
+# lit and whose main colour is warm. The sky's light is measured like the land's (capture EV100
+# plus how far the sky came out from mid-grey) and starts at the land's night level; a sky that a
+# town or the moon lights stays stops below it. afterglow_min_share is the share of the sky on the
+# warm side of neutral: more than half, so an orange band under a blue sky stays twilight.
+afterglow_sky_min_ev = 5.0
+afterglow_min_share = 0.5
 # The camera's own setting, uncorrected: at a sunlit exposure a neutral patch may be a lit cloud,
 # and gold in the sky needs a second witness (a low sun or a warm sky overall).
 sunlit_capture_min_ev = 13.7
```

The Builder changes no other number, and no other line of `pipeline.py`.

**4. Tests.** Four new tests, appended to the two files. No existing test changes, and none
changes its answer. The first is the golden test of the rule: one case for the afterglow, and one
for each frame in the table of conditions above. The second is the promise that nothing else
moves. The third pins the new measure. The fourth proves the pipeline hands the measure over: with
`scene.py` alone, the first three would pass and no photo would ever reach the rule.

```diff
--- a/tests/test_scene.py
+++ b/tests/test_scene.py
@@ -351,3 +351,67 @@
     assert imageio._ev100(1/200, 14, 200) == pytest.approx(np.log2(19600))
     assert imageio._ev100(.125, 0, 100) is None and imageio._ev100(.125, 8, 0) is None
     assert imageio._raw_capture_ev("no-such-file.raf") is None
+
+
+def test_an_afterglow_over_unlit_land_is_sunset_and_a_band_under_a_blue_sky_stays_twilight():
+    cfg = _thresholds()
+    def detect(g, ev, warmth, gold, side, **kw):
+        return scene.classify(g, {"sky"}, thresholds=cfg, capture_ev=ev, sky_warmth=warmth, warm_share=gold,
+                              warm_side=side, **kw).finer
+    # F62 ticket 27. A peach sky over land in shadow: the land 4.6 stops under mid-grey (2.0), the sky at 8.5.
+    glow = _stats(sky_cov=.48, sky_lum=.85, sky_sat=.13, lum_p10=.04, lum_p50=.33, lum_p90=.91, fg_p50=.08)
+    assert detect(glow, 6.6, .12, .37, .96) == "sunset"
+    assert detect(glow, 6.6, .12, .37, 0.) == "twilight"                # as before, for a caller that gives no share
+    assert detect(glow, 6.6, .12, .37, .96, night=True) == "sunset"     # a high ISO does not make a lit sky night
+    assert detect(glow, 6.6, .12, .37, cfg["afterglow_min_share"]) == "twilight"    # half is not the main colour
+    assert detect(glow, 6.6, .01, 0., 1.) == "twilight"                 # a white sky with a warm cast: no gold in it
+    # An orange band under a dark blue sky: the band lifts the mean warmth, most of the sky is blue.
+    band = _stats(sky_cov=.29, sky_lum=.44, sky_sat=.33, lum_p10=.03, lum_p50=.11, lum_p90=.51, fg_p50=.08)
+    assert detect(band, 9.1, .055, .29, .42) == "twilight"
+    # A town at night under cloud that its lamps light: warm all over, and 9 stops under the afterglow.
+    town = _stats(sky_cov=.68, sky_lum=.38, sky_sat=.20, lum_p50=.37, lum_p90=.47, fg_p50=.30)
+    assert detect(town, .3, .07, .14, .94) == "twilight"
+    # The sky's own light decides, at the level in the config: the same sky a stop above it and a stop below.
+    level = cfg["afterglow_sky_min_ev"] - float(np.log2(scene.srgb_to_linear(.85) / .18))
+    assert detect(glow, level + 1, .12, .37, .96) == "sunset" and detect(glow, level - 1, .12, .37, .96) == "twilight"
+
+
+def test_the_afterglow_rule_only_ever_turns_a_night_answer_into_sunset():
+    import random
+    cfg = _thresholds()
+    rng = random.Random(27)
+    moved = 0
+    for _ in range(2000):
+        low, mid, high = sorted(rng.uniform(0, 1) for _ in range(3))
+        g = _stats(sky_cov=rng.choice((0., rng.uniform(.04, .9), rng.uniform(.04, .9))), sky_lum=rng.uniform(0, 1),
+                   sky_sat=rng.uniform(0, .5), lum_p10=low, lum_p50=mid, lum_p90=high, fg_p50=rng.uniform(0, .6),
+                   veil=rng.choice((0., rng.uniform(0, .3))), blown_sky=rng.choice((0., rng.uniform(0, 1))))
+        kw = dict(thresholds=cfg, capture_ev=rng.choice((None, rng.uniform(-6, 16), rng.uniform(2, 12))),
+                  sky_warmth=rng.uniform(-.3, .3), warm_share=rng.uniform(0, 1),
+                  rose_share=rng.choice((0., rng.uniform(0, .5))), night=rng.random() < .2,
+                  clouds=rng.choice((None, "open", "deck", "ceiling")), fog=rng.choice((0., rng.uniform(0, 1))))
+        before = scene.classify(g, {"sky"}, **kw)
+        after = scene.classify(g, {"sky"}, warm_side=rng.uniform(0, 1), **kw)
+        if (after.finer, after.reason) != (before.finer, before.reason):
+            moved += 1
+            assert (before.finer, before.reason) == ("twilight", "night exposure and low ambient light")
+            assert after.finer == "sunset" and after.direct
+    assert moved > 20     # the draws do reach the rule
+
+
+def test_sky_colour_counts_the_share_of_the_sky_on_the_warm_side():
+    cfg = _thresholds()
+    rgb = np.zeros((10, 10, 3), np.float32)
+    sky = np.zeros((10, 10), bool)
+    sky[:6] = True
+    rgb[:4] = [.05, .08, .16]       # dark blue above
+    rgb[4:6] = [.95, .55, .10]      # a bright orange band on the horizon
+    warmth, gold, rose, side = scene.sky_colour(rgb, sky, cfg)
+    assert warmth > 0 and gold == pytest.approx(1 / 3)      # the mean says warm
+    assert side == pytest.approx(1 / 3)                     # the share says blue
+    rgb[:4] = [.80, .76, .74]       # a pale peach sky above the band: warm, and too pale to count as gold
+    assert scene.sky_colour(rgb, sky, cfg)[1::2] == (pytest.approx(1 / 3), 1.0)
+    rgb[:6] = .5                    # grey is on neither side
+    assert scene.sky_colour(rgb, sky, cfg)[3] == 0.0
+    assert scene.sky_colour(rgb, np.zeros_like(sky), cfg) == (0.0, 0.0, 0.0, 0.0)
+    assert scene.sky_colour(rgb, None, cfg) == (0.0, 0.0, 0.0, 0.0)
--- a/tests/test_scene_lighting.py
+++ b/tests/test_scene_lighting.py
@@ -154,3 +154,34 @@
         assert ("mist" in pipe.segmenter.words) == (style == "auto")
         if scene:
             assert info.scene == scene
+
+
+def test_auto_reads_an_afterglow_from_the_pixels_and_an_unreleased_sunset_still_renders_simple():
+    import numpy as np
+    from autoedit.contract import RunConfig
+    from autoedit.imageio import Image
+    from autoedit.pipeline import Pipeline
+    from autoedit.segment.base import RegionMask
+
+    class Masks:
+        def segment(self, im, words):
+            sky = np.indices(im.rgb.shape[:2])[0] < im.height // 2
+            items = {"sky": sky, "mountain": ~sky}
+            return [RegionMask(w, items[w], .99) for w in words if w in items]
+
+    y, x = np.indices((40, 64))
+    def photo(*skies):
+        rgb = np.stack([.05 + .04*x/64]*3, axis=2).astype(np.float32)   # land in shadow, 5 stops under mid-grey: 2.7
+        for rows, colour in skies:
+            rgb[rows] = colour
+        return Image(rgb=rgb, exif={"capture_ev100":7.6})
+    def render(im, style):
+        pipe = Pipeline.build(RunConfig(backend="stub", style=style))
+        pipe.segmenter = Masks()
+        return pipe.process_one("sample.jpg", image=im)
+    peach = photo((y < 20, [.92, .78, .66]))                                            # the sky at 9.4
+    band = photo((y < 20, [.10, .14, .30]), ((y >= 14) & (y < 20), [.95, .55, .10]))    # at 6.1, gold on 30 % of blue
+    pixels, info = render(peach, "auto")
+    assert info.scene == "sunset"
+    assert render(band, "auto")[1].scene == "twilight"
+    assert np.array_equal(pixels, render(peach, "standard")[0])
```

On the tree as it is, all four fail: the first two with a `TypeError` (`classify` has no
`warm_side`), the third because `sky_colour` returns three values, the fourth because the peach
sky is read as twilight. With the seed above, 73 of the 2,000 draws of the second test reach the
rule.

**5. Docs.**

- `CHANGELOG.md`, under "Scene-based Auto (F62)", after the entry that begins "A photo that Auto
  could not give a finer scene": "Auto calls an afterglow a sunset. Gold in a sky that is still
  lit and mostly warm, over land that came out as dark as night, used to read as twilight
  (`[scenes] afterglow_sky_min_ev` and `afterglow_min_share`). On the labelled sets this names 12
  of 35 sunsets in the card folder's sample, up from 7, and 9 of 13 on the 120, up from 7. No
  label of another scene lost its answer."
- The comment in `auto.toml` is in the patch. The spec already lists this ticket.

## Left out

- **The mean warmth as the main colour.** The first form of the rule asked for a mean warmth above
  zero. It gave the same 5 on the card folder and 3 on the 120, and it turned a sure twilight of
  the development set into sunset: an orange band under a dark blue sky, whose mean is warm
  because the band is bright. The share keeps that frame and gives up one of the three on the 120,
  a red band under a dark sky that its labeller called sunset.
- **Without the sky's own light.** Ten night frames of the card folder turn sunset: two of a moon
  over the sea, a street under lamps, a figure against a lamp, a campfire, and five of a night
  ridge with a glow on the horizon. So does a sure twilight of the development set, a town at
  night. Their skies sit between -10.1 and 1.0; the afterglows start at 5.6.
- **Leaving the high-ISO flag in charge.** No labelled frame depends on it. Two unlabelled frames
  of the card folder's afterglow do (ISO 1600), and by eye they show the sky of the frames next
  to them, which turn sunset.
- **A lower share, so that a band under a dark sky counts as sunset.** At 0.3 the 120 gains two
  more (98 of 120) and the development set loses its sure twilight (72 of 97). The card folder
  does not move: its two bands sit at 0.28 and 0.29. Which of the two it should be is the owner's
  call (O2).
- **Pink-lit cloud in a blue sky** (9 of the 23 misses left on the card folder). The labels of the
  three sets do not agree on it. Frames with pink and no gold in the sky are labelled sunset five
  times on the card folder, all unsure, and twilight four times on the other two sets, three of
  them sure. The frames carry no steady signal either: the rose measure sees pink on 4 of the 9
  (7 to 17 % of the sky), and the mean sky colour of all 9 is blue. `test_scene_lighting.py`
  holds a rosy afterglow at dusk light to twilight. Bring it back when the owner has said what
  pink cloud over unlit land is, and a set is labelled by that.
- **Alpenglow under a pastel or a cool sky** (5 on the card folder; the 8 by-eye sunsets of the
  Swiss trip, as ticket 25 found). These skies hold next to no gold: 0 to 5 % of the sky. On the
  card folder the pastel sky has no blue either and reads as a cloud deck, and the land sits at
  7.4 to 8.0, between dusk and day. Nothing measured here separates that from a dim overcast
  evening, which `test_scene.py` holds to mist at 8.05.
- **Warm light on the land only** (7 on the card folder, 5 on the development set). The colour of
  the land does not show its light. Eight measures of it (how orange the land or its highlights
  are, red against green and against blue in the highlights, the split between highlights and
  shadows) were run on the 50 sunsets Auto misses against the 174 daylight labels of four sets.
  Over the four sets together the best one ranks a missed sunset above a daylight photo 61 % of
  the time, where 50 % is chance. None stays on one side of chance in every set: red against
  green in the highlights does 76 % on the card folder and 44 % on the 120. Daylight photos of
  the development set and of the 120 have highlights as orange as any missed sunset. The height
  of the sun would settle it, and that needs a clock and a place: the card folder's clock is
  hours off and its files carry no position.
- **Asking the same share of every gold answer.** Outside the night branch, gold calls three
  twilight labels sunset today. The share is under half on one of them, on the 120, and that frame
  would then read as daylight: a swap. All 25 sunsets that gold names rightly on the three sets
  have more than half (0.53 at the lowest), so the share would cost nothing there. Bring it back
  with a rule for what such a frame is instead.
- **The dawn frames.** 7 of the 9 twilight labels that the sample still misses are the dawn run of
  ticket 26, on the fallback. Their sky is blue. This ticket does not reach them.
- **A reason of its own in the sidecar.** The frames say "gold light in the sky", which is what
  decided. A second wording is one more string to keep in step with the scorers.

## Done when

- Before the first edit, the Builder runs the stub pipeline into a scratch folder:
  `PYTHONPATH=src python3 -m autoedit.cli run ./test-corpus <scratch>/before --backend stub`.
  After the change the same command into `<scratch>/after` completes with sidecars and manifest,
  and the two image files are byte-identical to the ones in `before` (`cmp`).
- `PYTHONPATH=src python3 tests/run_all.py test_scene.py test_scene_lighting.py` passes with 36
  tests (27 and 9), four more than today, and
  `PYTHONPATH=src python3 -m pytest --collect-only -q tests/test_scene.py tests/test_scene_lighting.py`
  collects 36.
- The whole suite is green on both interpreters: `PYTHONPATH=src python3 tests/run_all.py` and
  `PYTHONPATH=src .venv/bin/python tests/run_all.py` (the tests that need tifffile run only on
  the second). Nobody edits `src/` or `tests/` while a suite runs: `run_all.py` loads the test
  files one by one, and a tree that changes under it reports failures that are not there.
- `python3 scripts/check_public_repo.py` passes. CHANGELOG and the comments in `auto.toml` are
  updated.
- The Checker (Opus, high) reviews the diff against this ticket, read-only, with findings as
  `file:line`, and runs the session's scorer from the repository root. Before the check, the
  session copies the Planner's `t27_score.py` from its scratch folder `t27/` into
  `out-webloop/f62/harness/scene/`. The scorer sends every photo through the real pipeline of the
  source tree (decode, cached SAM 3 masks, stats, scene). It starts no SAM 3 job: a photo whose
  masks are not cached is reported and left out, so the shards may run side by side.
  - The commands, in this order:

    ```
    .venv/bin/python -I out-webloop/f62/harness/scene/t27_score.py card --folder <the card folder> --shard 0/4
    .venv/bin/python -I out-webloop/f62/harness/scene/t27_score.py dev
    .venv/bin/python -I out-webloop/f62/harness/scene/t27_score.py check
    .venv/bin/python -I out-webloop/f62/harness/scene/t27_score.py swiss
    .venv/bin/python -I out-webloop/f62/harness/scene/t27_score.py dolomites
    .venv/bin/python -I out-webloop/f62/harness/scene/t27_score.py report
    ```

    The first runs four times, with `0/4`, `1/4`, `2/4` and `3/4`: about 8 minutes side by side.
    The Swiss trip takes about 12 minutes in one piece (`--shard` works for every set), each of
    the others 2 to 4.
  - Card folder: 1,581 photos scored, no errors. Alone 97 of 147 before and 102 after, sure and
    owner labels 72 of 85 both times, one daylight/twilight swap both times and none on the sure
    and owner labels. Sunset 7 of 35 before and 12 after; daylight 33 of 34, twilight 47 of 56 and
    mist 10 of 22 both times. 24 answers changed, all from twilight to sunset: 5 made right, 19
    without a label. In the batch 102 before and 107 after, with the same 30 scenes taken from
    neighbours (21 mist, 9 sunset), frame for frame.
  - Development set: 73 of 97 before and after, no answer changed.
  - The 120: 94 before and 96 after, 2 answers changed, both made right, no swap.
  - Swiss trip: 345 photos scored, no answer changed. Dolomites frames of ticket 25: 113 scored,
    one answer changed, from twilight to sunset. It is a photo of the 120.
- No tone report and no contact sheet: nothing in exposure, tone or decode changes, and a default
  run renders the same pixels. Under Auto a photo of an unreleased scene still gets Simple, and
  all four scenes are unreleased.
- No git writes: the owner gets a commit block.
- Not part of this ticket's "done": the 85 % bar (Comments, "What it will not do").

## Owner steps

No decision blocks the build.

| | Step | Proposed |
|---|---|---|
| O1 | Photos for an honest check. Every labelled set is a development set for this rule now: it was drawn on the card folder, its form was changed after the development set, and the 120 are spent | One more folder as it came off the card, from a trip nothing here has used, with a sunset or a dusk in it if there is one. Kept private like the other corpora |
| O2 | Where sunset ends and twilight begins once the land is unlit. The label rule of the card folder and of the development set goes by the sky's main colour: a warm afterglow is a sunset, an orange band on the horizon under a dark blue sky is twilight. The labeller of the 120 called two such bands sunset. The rule is the Checkers'; the owner has not ruled on it | As labelled, and as built. If a band is a sunset too: `afterglow_min_share = 0.3` names both frames of the 120 and loses a sure twilight of the development set. If an afterglow is twilight after all: `afterglow_min_share = 1` switches the rule off |

## Comments

2026-11-27 — Planner (Fable, max). What the card folder's missed sunsets show, a rule for the group
a frame can settle, and what the other groups would need. Nothing in the tree was changed.

**How it was measured.** Every frame went through the real pipeline once, with SAM 3 masks from
the caches and no new SAM 3 job: all 1,581 camera JPEGs of the card folder, the 97 development
photos, the 120, and the two RAW trips of ticket 25 (345 Swiss and 110 Dolomites frames). Every
input of `landscape_scene` was kept, with the proxy pixels and the sky mask, so a rule replays in
seconds and a new sky measure can be taken afterwards. The replay returns the pipeline's own
answer on every frame of the five sets. The code of this ticket was then applied to a copy of the
tree, and the copy was run through the real pipeline again on all 1,581 card frames, the 97, the
120 and the frames of the two trips. Every count of answers below is from that run, and the
offline replay gives the same answers. On that copy the four new tests pass, both suites are
green (1,442 passed and 4 skipped; 1,446 passed with the venv) and the stub run's two images are
byte-identical to the tree's. On the tree as it is, the four new tests fail and the other 32 of
the two files pass.

**Before and after, each photo alone.**

| | Before | After |
|---|---:|---:|
| **Card folder, random sample:** all 147 labels | 97 (66.0 %) | 102 (69.4 %) |
| daylight, of 34 | 33 | 33 |
| twilight, of 56 | 47 | 47 |
| sunset, of 35 | 7 | 12 |
| mist, of 22 | 10 | 10 |
| daylight/twilight swaps | 1 | 1 |
| sure and owner labels, of 85 | 72 (84.7 %) | 72 (84.7 %) |
| daylight/twilight swaps in them | 0 | 0 |
| in the batch, all 147 labels | 102 (69.4 %) | 107 (72.8 %) |
| scenes taken from neighbours | 30 | the same 30 |
| **Development set:** all 97 labels | 73 (75.3 %) | 73 (75.3 %) |
| daylight 31, mist 27, twilight 21, sunset 18 | 25, 20, 17, 11 | 25, 20, 17, 11 |
| sure labels, of 67 | 54 | 54 |
| daylight/twilight swaps | 1 | 1 |
| **The 120** | 94 (78.3 %) | 96 (80.0 %) |
| daylight, of 64 | 57 | 57 |
| twilight, of 23 | 20 | 20 |
| sunset, of 13 | 7 | 9 |
| mist, of 20 | 10 | 10 |
| daylight/twilight swaps | 0 | 0 |
| **All three sets,** 364 labels | 264 (72.5 %) | 271 (74.5 %) |

Answers changed: 24 on the card folder's 1,581 frames, none on the development set, 2 on the 120,
none on the Swiss trip, 1 on the Dolomites RAW folder (a photo of the 120). All of them go from
twilight to sunset. Of the 26, 7 carry a label and all 7 are made right. The other 19 are frames
of the card folder outside the sample.

**Where the rule reaches.** Frames the night branch calls twilight, and what the three conditions
make of them:

| | Card folder | Development set | The 120 |
|---|---:|---:|---:|
| Twilight by the night branch | 371 | 16 | 25 |
| of them, with gold in the sky | 36 | 3 | 7 |
| of the gold ones, the sky lit above the level | 26 | 1 | 4 |
| of the gold ones, more than half of the sky warm | 34 | 2 | 2 |
| of the gold ones, both: sunset | 24 | 0 | 2 |

- Card folder: the 36 are 24 frames of one afterglow, 2 bands under a dark blue sky (lit, 0.28 and
  0.29 of the sky warm, both labelled twilight) and the 10 night frames (more than half warm, not
  lit).
- Development set: a town at night (sky at -0.3), the long exposure by day that ticket 02 names
  (2.9, labelled mist) and a band under a dark blue sky (lit, 0.42 warm, a sure twilight).
- The 120: three sure twilights with a band or a gradient (0.22 to 0.39 warm, their skies at 1.8
  to 4.8), two sunsets with a band or pink cloud under a dark sky (0.37 and 0.39), and the two
  that turn sunset (0.55 and 0.62 warm, their skies at 5.6 and 9.4).
- The 24 of the card folder were all taken within 17 minutes. Their land sits at 0.5 to 4.8,
  their sky at 8.3 to 10.9, with gold on 12 to 100 % of it and 56 to 100 % of it warm.

**The evening on the card folder.** 57 landscape frames in 25 minutes. Before: 37 twilight, 6
sunset, 11 on the fallback, 3 mist. After: 30 sunset, 13 twilight, 11 and 3. The Planner looked at
the 24 frames that change and at the other landscape frames of that evening, knowing the answers.
The 24 all show the same peach afterglow over land in shadow. The frames that keep twilight show a
blue or lavender sky with pink cloud, a sky blown to white (one of them labelled twilight), or the
blue bay after the glow.

**How the rule was kept from fitting one set.**

- It was written from the card folder's five frames and from what a night sky is, then run on the
  two other sets. That first form asked for a mean warmth above zero. The other sets gave it 3 of
  the 120 and took a sure twilight of the development set.
- It was changed once: the main colour by area in place of the mean. That change came from the
  development set's frame, so the development set does not test it. On the 120 it costs one of the
  three.
- Each number was fixed before the form that holds it was scored, and was not moved. The sky
  level was in the first form, taken from the land's night level, for the town at night that
  `test_scene.py` already holds to twilight. The share came with the measure, before its value on
  any frame was computed: more than half is what "main colour" means.
- Where the answers would move, on the three labelled sets:

  | `afterglow_min_share`, at a sky level of 5.0 | Card sample | Development set | The 120 |
  |---|---:|---:|---:|
  | 0.30 | 102 | 72 | 98 |
  | 0.40 | 102 | 72 | 96 |
  | 0.45 to 0.55 | 102 | 73 | 96 |
  | 0.60 | 102 | 73 | 95 |
  | 0.70 | 101 | 73 | 94 |

  | `afterglow_sky_min_ev`, at a share of 0.5 | Card sample | Development set | The 120 |
  |---|---:|---:|---:|
  | no level | 102, and 10 night frames turn sunset | 72 | 96 |
  | 0 | 102, and 1 night frame turns sunset | 73, and the long exposure by day turns sunset | 96 |
  | 3.0 to 5.5 | 102 | 73 | 96 |
  | 6.0 to 7.0 | 102 | 73 | 95 |

  Every answer of the three sets is the same for a share from 0.43 to 0.55 and for a level from
  3.0 to 5.5. Below 0.43 the development set's band turns sunset. Above 0.55, or above a level of
  5.6, one of the two sunsets of the 120 goes back to twilight.
- A check from frames the rule does not touch: all 25 sunsets that the gold test names rightly
  today on the three sets have more than half of their sky warm (0.53 at the lowest). Of the
  frames the night branch takes with gold in a lit sky, the twilight labels reach 0.42.
- Every set is reported, also the two where nothing moves. The second test holds the code to the
  one answer it may change.

**How far to trust it.**

- The gain is small and rests on few shoots: the five labels of the card folder are one evening,
  and all five are unsure. One of the two on the 120 sits at 0.55 and 5.6, next to both limits:
  count on one there.
- The sky level is taken on the rendered picture. Ticket 02 found the default RAW development
  about 1.5 stops darker than a camera JPEG, so the same sky sits lower in a RAW file. The two
  afterglows of the 120 are RAW files, at 5.6 and 9.4; the card folder's JPEGs start at 8.3.
- The labels disagree on the band under a dark sky (O2). The rule follows the written rule of two
  sets.
- Nine wrong builds were tried on the copy: the mean in place of the share, no sky level, no gold
  test, the high-ISO flag in charge, half counting as most, the pipeline not handing the share
  over, the land's light in place of the sky's, a share that counts gold pixels only, and neutral
  counting as warm. Each fails at least one of the four tests.

**What it will not do.**

- Meet the bar of ticket 03. The card folder's sample stands at 102 of 147 alone (69.4 %) and 107
  in the batch (72.8 %), against 85 %. The sure and owner labels stay at 72 of 85 (84.7 %): none
  of the five gains is a sure label.
- Name the other 23 sunsets of the sample: 8 twilight, 8 mist, 6 daylight, 1 on the fallback. 4 of
  them are sure labels, and 15 carry a note that says another scene is possible. Of the 45 answers
  still wrong on the sample, 23 are sunsets, 12 are mist (11 read as daylight), 9 are twilight (7
  of them the dawn run on the fallback) and 1 is daylight.
- Remove the sample's one daylight/twilight swap. It is a direct daylight answer on a frame
  labelled twilight, unsure, and no night-branch answer.
