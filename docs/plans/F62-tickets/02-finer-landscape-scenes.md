# 02. Finer landscape scenes, confidence and fallback

Status: needs-info
Blocked by: 01
Spec: [F62](../F62-auto.md), milestone M1
Who: Builder (Sonnet, medium)

## What to build

- `scene.py::classify` adds a finer landscape scene (`sunset`, `twilight`, `daylight`, `mist`)
  with a confidence, from measures the pipeline has: sky coverage, sky brightness and warmth,
  global brightness and contrast, the detected light (`light.py`), `night.py::is_night`, and
  `veil` (`contract.py`).
- All thresholds in a `[scenes]` table in `configs/auto.toml`. No number in Python.
- Below the confidence threshold: the broader `landscape` goal at `fallback_strength`.
- `--scene` passes the existing `override`. The scene and the reason go to `notes`.
- One shoot, one scene: within a `consistency.py::plan_groups` group every photo gets the most
  common scene, unless the group spans a change in time of day.
- `styles.toml [auto] targets` gains the four finer scenes, each pointing at `auto_landscape`
  until the measured tables exist (ticket 13).

## Done when

- A new `tests/test_scene.py` with synthetic stats for each scene, the fallback and the shoot rule.
- A golden test that today's three-way answer is unchanged for Simple.
- `PYTHONPATH=src python tests/run_all.py` is green and the tests stay pytest-collectable.
- `autoedit run ./test-corpus ./out --backend stub` completes, with images byte-identical to the
  previous commit (nothing chooses Auto by default).
- `python scripts/check_public_repo.py` passes. CHANGELOG and docs updated if behaviour changed.
- No git writes: the owner gets a commit block.

## Comments

2026-11-25 — Implemented fine scene labels with confidence, recorded reasons, a manual override, a lower-strength landscape fallback and per-shoot voting across time-of-day transitions. Simple keeps its original three-way decision. Ticket 03 reopened this ticket after 97 of 120 labels matched. Daylight and twilight never crossed; bright overcast scenes remain the main error.

A cloud mask is now measured alongside sky colour in bright overcast photos. The same evidence is used for a released scene and its fallback. In the fixed sample this recovered three additional mist/overcast scenes and called four additional daylight scenes overcast. The retained version recognises 10 of the 20 overcast scenes.

2026-11-26 — Planner (Fable, max), after the second failed check: root cause and a change that is not a threshold nudge. The fixed 120 were neither opened nor scored for this.

**How it was measured.** A fresh development set of 97 originals, labelled blind (31 daylight, 27 mist, 21 twilight, 18 sunset; 30 of the labels are marked unsure), private in `out-webloop/f62/scene-dev/`. Each photo went through the real pipeline once (SAM 3, no forced scene) and every input of `landscape_scene`, `cloud_cover` and `sky_colour` was kept, so a rule change replays in seconds. The replay returns the pipeline's own answer on all 97. Today's code agrees on 59 of 97 (60.8 %; 45 of the 67 sure labels), with five daylight/twilight swaps, two of them on sure labels. The set holds hard cases on purpose, so its numbers are not the 120's (80.8 % there).

**Root causes**, by how many of the 38 development errors they explain:

1. *The camera's setting is read as the light.* `capture_ev` is what the camera was set to. A dusk or sunset frame exposed for its bright sky has a daylight setting and dark land: it passes `day_capture_min_ev`, and its blue sky then reads "blue openings: daylight". 15 of the 38 errors took that decision (7 sunset, 4 twilight, 4 mist). The camera's EV cannot separate them: it reaches 13.0 on dusk frames and goes down to 12.3 on daylight frames. Adding how far the land came out from mid-grey (`fg_p50`, already measured) gives the light on the land: 6.4 or less on the sure twilight labels and 8.0 on the unsure ones (one dusk snow scene is at 11.1), 8.4 or more on the 27 daylight photos that have an EV.
2. *No capture EV for most RAW containers.* EXIF is read with Pillow, which opens only the TIFF-like RAW files. 27 of the 97 have no EV (RAF, ORF, RW2, CR3, ARW, NRW, some DNG): 9 of today's 10 fallbacks to `landscape` and one of the two sure swaps are among them. LibRaw parses the same settings: `rawpy`'s `raw.other` gives an EV for 18 of the 27, and the same value as Pillow on the 66 files both can read.
3. *Fog measures as nothing.* `veil` needs a lifted dark channel and the default RAW development is dark: four fog frames have a veil of 0.00 or 0.01. SAM 3 asked for "mist" covers 62 to 99 % of five frames, all labelled mist, 46 % of a sixth mist frame, and at most 21 % of any other. On 120 unlabelled photos of another trip it never reaches 50 %.
4. Three single conditions. A night frame rendered bright fails the median gate of "very dark", and its lit sky then reads as sunset. A dim frame under a cloud deck is called twilight because the picture is dark. Gold in the sky is refused "sunset" above the old day level without a second witness, though the sun on the horizon leaves the land at 8.5 to 9.7 (seven development sunsets).

**What this does not fix, and what would.**

- *The "bright-exposure decision" of ticket 03.* The development set has two such errors, so no number here can confirm a fix. A look at 120 unlabelled photos from the Swiss shoots (the camera of the Dolomites corpus; not in the 120, not in the development set) shows who takes that decision: 26 photos, of which 17 have no sky at all, 4 a clipped sky, 3 a sky colour between the cloud verdicts, 1 a deck with the sun's glow in it and 1 a sliver of sky. By eye, not blind, 11 to 16 of the 26 are flat overcast light. Today's measures do not tell those from sun: the tonal range of the land overlaps (daylight 1.9 to 5.5 stops, mist 0.7 to 4.4, on half of the development set) and so does the light on the land. What would: the weather of the neighbouring frames (the shoot rule does this in a batch; the check scores single photos), or a vision model's answer. Expect the overcast class of the 120 to stay weak: this change will not by itself bring 10 of 20 to 15.
- *A white-balance cast on cloud reads as blue openings.* Three of the four errors left on sure mist labels. No colour measure tried separates a cast from pale blue sky: blue share, variation, and the blue tail against the whitest part of the sky all overlap. SAM 3's score for "blue sky" does on this set (0.53 to 0.63 on the three, 0.76 or more on 30 of the 31 daylight skies), but the gap is thin and three photos are no basis. It deserves its own ticket and labels made for it.
- *Golden hour under a blue sky* (6 of the 18 development sunsets): the sky has no gold. Sun elevation from capture time and place would settle it.
- A frame with no EV at all (a manual lens, a phone DNG without ISO): three development daylight photos still fall back to `landscape`. A daytime long exposure whose settings read as night (1). Snow at dusk is as bright as land by day: the one swap that remains, a swap today as well.

**The change.** Four files. The patch below applied cleanly to the working tree of 2026-11-26 (`git apply --check`); it was built and run on a copy, not on the tree.

| File | What changes |
|---|---|
| `src/autoedit/scene.py::landscape_scene` | The light on the land, `capture_ev + log2(linear(fg_p50) / 0.18)`, replaces the camera's EV in the day, dusk and night tests. The rendered-brightness rules (`day_sky_min`, `day_median_min` with `day_contrast_min`, the dark-frame dusk rule) apply only when there is no EV. "Very dark" loses its median gate. Metered dusk takes an open sky as evidence in place of a dark picture. Gold needs its second witness only at `sunlit_capture_min_ev`. A cloud deck needs no brightness test when metered. A frame whose sky is not open keeps daylight above dusk light. New rule ahead of the sky rules: fog across the frame is mist. `landscape_scene` and `classify` gain `fog=0.0`. |
| `src/autoedit/configs/auto.toml [scenes]` | The key table below. `styles.py::load_auto` needs no change: keys ending in `_ev` are checked as finite numbers, `fog_min_share` as 0 to 1. |
| `src/autoedit/pipeline.py` | `_segment_words`: Auto asks for "mist" beside "cloud". `process_one`: the share of the frame the "mist" mask covers, at the concept's `min_score`, goes to `classify(fog=...)`. Simple never asks for it. |
| `src/autoedit/imageio.py` | `_ev100(seconds, aperture, iso)`, taken out of `_read_exif`. `_raw_capture_ev(path)` reads `rawpy`'s `raw.other`. `_load_image` calls it only when Pillow gave no `capture_ev100`, and adds that one key, never `iso`: the default sharpening reads `iso`. |

| Key in `[scenes]` | Old | New |
|---|---|---|
| `day_capture_min_ev` | 10.5, the camera's EV | removed |
| `dusk_capture_max_ev` | 8.0, the camera's EV | removed |
| `night_capture_max_ev` | 4.0, the camera's EV | removed |
| `day_land_min_ev` | | 8.25, light on the land |
| `dusk_land_max_ev` | | 7.0, light on the land |
| `night_land_max_ev` | | 5.0, light on the land |
| `fog_min_share` | | 0.5 |
| `sunlit_capture_min_ev` | 13.7 | 13.7, still the camera's EV; a comment is added |

The three land numbers have a physical reading in this unit (mid-grey land in the default RAW development): the sun on the horizon leaves about 6.7, deep shade or heavy overcast by day 9 to 11, full sun about 14.7. Between 7.0 and 8.25 an open sky is neither dusk nor day and the photo falls back to `landscape`: a miss, never a swap. The comments for the new keys are in the patch.

```diff
--- a/src/autoedit/scene.py
+++ b/src/autoedit/scene.py
@@ -11,6 +11,7 @@
 import numpy as np
 
 from .contract import GlobalStatsModel, RegionStatsModel
+from .imageio import srgb_to_linear
 
 _PEOPLE = {"skin", "person", "face"}
 _LANDSCAPE = {"sky", "snow", "mountain", "grass", "water", "foliage", "trees", "rock"}
@@ -18,6 +19,8 @@
 # always warm rock/grass/sand — it must NOT drag a landscape into the people ruleset (or warm
 # scattered specks into red dots). A real face/half-body portrait is well above this.
 _SKIN_PORTRAIT_COV = 0.08
+# The tone a light meter aims at. Capture EV is defined against it: a unit, not a threshold.
+_MID_GREY = 0.18
 
 
 @dataclass
@@ -81,13 +84,19 @@
     return None
 
 
-def landscape_scene(g, cfg, sky_warmth, warm_share, rose_share, capture_ev, light, night, clouds=None):
+def landscape_scene(g, cfg, sky_warmth, warm_share, rose_share, capture_ev, light, night, clouds=None, fog=0.0):
     """A compact evidence trail for the scene. Ambiguous evidence uses the broader goal."""
     sky = g.sky_cov >= cfg["min_sky"]
     contrast = g.lum_p90 - g.lum_p10
     warm_light = light is not None and light.cy >= cfg["warm_light_y"]
-    bright_capture = capture_ev is not None and capture_ev >= cfg["day_capture_min_ev"]
-    dim_capture = capture_ev is not None and capture_ev <= cfg["dusk_capture_max_ev"]
+    # The capture EV is what the camera was set to, not how much light there was: a dusk frame
+    # exposed for its sky has a daylight setting and dark land. Adding how far the land came out
+    # from mid-grey gives the light on the land itself, whatever the photographer exposed for.
+    metered = capture_ev is not None
+    land_tone = float(srgb_to_linear(g.fg_p50 or g.lum_p50))    # fg_p50 is 0 only in stats nobody measured
+    land_ev = capture_ev + float(np.log2(max(land_tone, np.finfo(np.float32).eps) / _MID_GREY)) if metered else None
+    bright_capture = metered and land_ev >= cfg["day_land_min_ev"]
+    dim_capture = metered and land_ev <= cfg["dusk_land_max_ev"]
     # A shaded foreground alone is common in midday mountains. When capture metadata is
     # absent, require evidence from both the sky and the bright end of the frame.
     dark_view = g.sky_lum <= cfg["dusk_sky_max"] and g.lum_p50 <= cfg["dusk_median_max"]
@@ -98,17 +107,22 @@
     # The camera may meter a rose or blue twilight sky to mid-grey. Conversely an
     # isolated lamp may shoot through p90 in an otherwise dark long exposure.
     dim_midtone = g.lum_p50 <= cfg["metered_dusk_median_max"]
-    night_capture = capture_ev is not None and capture_ev <= cfg["night_capture_max_ev"]
-    very_dark = night or (night_capture and g.lum_p50 <= cfg["dusk_median_max"] and
-                          (sky or dim_highlights))
+    night_capture = metered and land_ev <= cfg["night_land_max_ev"]
+    very_dark = night or (night_capture and (sky or dim_highlights))
     dusk_colour = sky and (cool_sky or rose_dusk)
-    metered_dusk = dim_capture and dim_midtone and (dusk_colour or (sky and dark_view and dim_highlights))
-    unmetered_dusk = dusk_colour and dark_view and dim_highlights
+    # With the land at or below sunset light, a dusk-coloured or a clear sky is a dusk sky. A cloud
+    # ceiling in the same light is overcast, however dark the picture came out.
+    metered_dusk = dim_capture and dim_midtone and (dusk_colour or clouds == "open")
+    # How bright the picture was rendered stands in for the light only when the settings are unknown.
+    unmetered_dusk = not metered and dusk_colour and dark_view and dim_highlights
+    bright_sky = not metered and g.sky_lum >= cfg["day_sky_min"]
+    bright_view = not metered and g.lum_p50 >= cfg["day_median_min"] and contrast >= cfg["day_contrast_min"]
+    sunlit_capture = metered and capture_ev >= cfg["sunlit_capture_min_ev"]
     # A bright neutral patch can be a white sunlit cloud. Strong daylight
     # exposure plus a full tonal range makes that weak evidence for overcast;
     # a genuinely low-contrast veil still takes precedence.
     clear_lighting = bright_capture and contrast > cfg["mist_contrast_max"] and (
-        capture_ev >= cfg["sunlit_capture_min_ev"] or g.blown_sky >= cfg["sunlit_sky_clip_min"]
+        sunlit_capture or g.blown_sky >= cfg["sunlit_sky_clip_min"]
         or (sky and not neutral_sky))
     diffuse = neutral_sky and light is None and (contrast <= cfg["mist_contrast_max"] or (
         g.sky_lum >= cfg["overcast_sky_min"] and g.sky_sat <= cfg["overcast_sat_max"]))
@@ -116,24 +130,25 @@
     if very_dark and not bright_capture:
         label, confidence, reason = "twilight", strong, "night exposure and low ambient light"
     elif sky and warm_share >= cfg["warm_min_share"] and not (dim_capture and rose_dusk) and (
-            warm_light or sky_warmth >= cfg["warm_sky"] or not bright_capture):
+            warm_light or sky_warmth >= cfg["warm_sky"] or not sunlit_capture):
         label, confidence, reason = "sunset", strong if warm_light else likely, "gold light in the sky"
     elif not bright_capture and (metered_dusk or unmetered_dusk):
         label, confidence, reason = "twilight", strong if metered_dusk else likely, (
             "dim capture with dusk sky or low ambient light" if metered_dusk else "dark frame with a cool or rose sky")
-    elif (clouds == "deck" and light is None and not dim_capture and (bright_capture or g.sky_lum >= cfg["day_sky_min"])
+    elif fog >= cfg["fog_min_share"]:
+        label, confidence, reason = "mist", likely, "fog or mist across the frame"
+    elif (clouds == "deck" and light is None and not dim_capture and (metered or bright_sky)
           and g.blown_sky <= cfg["deck_clip_max"]):
         label, confidence, reason = "mist", likely, "a continuous cloud deck with no distinct blue openings"
     elif (clouds == "ceiling" and light is None and not dim_capture
           and g.sky_lum >= cfg["day_sky_min"] and g.blown_sky <= cfg["cloud_clip_max"]):
         label, confidence, reason = "mist", likely, "continuous neutral cloud cover across the sky"
-    elif (clouds == "open" and not dim_capture and (bright_capture or g.sky_lum >= cfg["day_sky_min"])
+    elif (clouds == "open" and not dim_capture and (bright_capture or bright_sky)
           and not (g.veil >= cfg["mist_veil"] and contrast <= cfg["mist_contrast_max"])):
         label, confidence, reason = "daylight", likely, "clear blue sky or blue openings between clouds"
     elif not clear_lighting and (g.veil >= cfg["mist_veil"] or diffuse):
         label, confidence, reason = "mist", likely, "atmospheric veil or diffuse light under a neutral sky"
-    elif bright_capture or (sky and g.sky_lum >= cfg["day_sky_min"] and not dim_capture) or (
-            g.lum_p50 >= cfg["day_median_min"] and contrast >= cfg["day_contrast_min"] and not dim_capture):
+    elif bright_capture or (metered and not dim_capture and clouds != "open") or (sky and bright_sky) or bright_view:
         label, confidence, reason = "daylight", likely, "daylight exposure or bright, well-defined highlights"
     else:
         label, confidence, reason = "landscape", cfg["weak_confidence"], "no clear lighting signature"
@@ -146,7 +161,7 @@
 def classify(g: GlobalStatsModel, present: set[str],
              region_stats: dict[str, RegionStatsModel] | None = None,
              override: str | None = None, *, thresholds=None, sky_warmth=0.0, warm_share=0.0,
-             light=None, night=False, rose_share=0.0, capture_ev=None, clouds=None) -> Scene:
+             light=None, night=False, rose_share=0.0, capture_ev=None, clouds=None, fog=0.0) -> Scene:
     if override:
         broad = "landscape" if override in LANDSCAPES else override
         return Scene(broad, 1.0, override, "scene chosen by the user")
@@ -163,7 +178,7 @@
         return Scene(name="people", confidence=0.7)
     if present & _LANDSCAPE:
         if thresholds is not None:
-            return landscape_scene(g, thresholds, sky_warmth, warm_share, rose_share, capture_ev, light, night, clouds)
+            return landscape_scene(g, thresholds, sky_warmth, warm_share, rose_share, capture_ev, light, night, clouds, fog)
         return Scene(name="landscape", confidence=0.6)
     if present & _PEOPLE:  # small skin, no landscape context → still a person-ish frame
         return Scene(name="people", confidence=0.55)
--- a/src/autoedit/configs/auto.toml
+++ b/src/autoedit/configs/auto.toml
@@ -14,11 +14,16 @@
 dusk_sky_max = 0.52
 dusk_median_max = 0.29
 dusk_highlight_max = 0.48
-day_capture_min_ev = 10.5
-dusk_capture_max_ev = 8.0
-# EV100 describes capture light, independently of how brightly a JPEG/RAW is rendered.
-night_capture_max_ev = 4.0
+# Light on the land: the capture EV100 plus how far the land (the frame outside the sky) came out
+# from mid-grey, in stops. A camera set for a bright dusk sky has a daylight EV and dark land.
+# An open sky counts as daylight from day_land_min_ev up and as dusk up to dusk_land_max_ev;
+# between the two it is neither. At or below night_land_max_ev the sun is gone, whatever the sky's colour.
+day_land_min_ev = 8.25
+dusk_land_max_ev = 7.0
+night_land_max_ev = 5.0
 metered_dusk_median_max = 0.50
+# The camera's own setting, uncorrected: at a sunlit exposure a neutral patch may be a lit cloud,
+# and gold in the sky needs a second witness (a low sun or a warm sky overall).
 sunlit_capture_min_ev = 13.7
 sunlit_sky_clip_min = 0.04
 overcast_sky_min = 0.60
@@ -46,6 +51,9 @@
 rose_blue = 0.02
 rose_share = 0.10
 mist_veil = 0.12
+# Share of the frame the segmenter calls "mist". Fog hides the sky and reads dark in a dark RAW
+# development, where the veil measure sees nothing.
+fog_min_share = 0.5
 mist_sky_sat_max = 0.16
 mist_contrast_max = 0.48
 day_sky_min = 0.54
--- a/src/autoedit/pipeline.py
+++ b/src/autoedit/pipeline.py
@@ -361,8 +361,8 @@
         extra = self._with_geometry_concepts(extra, concepts)
         # Sky weather is measured even while a scene awaits release. Keep the
         # diagnostic cloud mask outside Simple's resolved edits.
-        if self.auto_cfg and "sky" in concepts and "cloud" not in concepts and "cloud" not in extra:
-            extra.append("cloud")
+        if self.auto_cfg and "sky" in concepts:
+            extra += [word for word in ("cloud", "mist") if word not in concepts and word not in extra]
         if self._may_fit_landscape() and "lights" not in concepts and "lights" not in extra:
             extra.append("lights")
         words = concepts + extra
@@ -543,13 +543,15 @@
             sky = resolved.union.get("sky")
             cloud = next((r.mask for r in raw if r.name == "cloud" and
                           r.score >= meta_for("cloud", self.meta).min_score), None)
+            fog = next((float(r.mask.mean()) for r in raw if r.name == "mist" and
+                        r.score >= meta_for("mist", self.meta).min_score), 0.0)
             sky_warmth, warm_share, rose_share = scene_mod.sky_colour(proxy.rgb, sky, thresholds)
             detected_light = self._find_light(proxy.rgb, raw, resolved.union, asked=True)
             detected = scene_mod.classify(gstats, present, region_stats, cfg.scene, thresholds=thresholds,
                                           sky_warmth=sky_warmth, warm_share=warm_share, rose_share=rose_share,
                                           capture_ev=(proxy.exif or {}).get("capture_ev100"), light=detected_light,
                                           night=night.is_night(gstats, self.night_cfg),
-                                          clouds=scene_mod.cloud_cover(proxy.rgb, sky, thresholds, cloud))
+                                          clouds=scene_mod.cloud_cover(proxy.rgb, sky, thresholds, cloud), fog=fog)
             choice = AutoPhoto.select(detected, style, self.auto_cfg, requested_strength, cfg.auto_unreleased,
                                       auto_strength, None if cfg.scene else auto_shoot)
             auto_scene, strength = choice.scene, choice.strength
--- a/src/autoedit/imageio.py
+++ b/src/autoedit/imageio.py
@@ -175,6 +175,11 @@
             exif = _read_exif(PILImage.open(path))
         except Exception:
             exif = {}
+        if "capture_ev100" not in exif:   # RAF, ORF, RW2, CR3, ARW, NRW: containers Pillow cannot read
+            ev100 = _raw_capture_ev(path)
+            if ev100 is not None:
+                # Only this key: ISO from here would change the sharpening of a default run.
+                exif["capture_ev100"] = ev100
         return Image(rgb=np.ascontiguousarray(rgb), source_bits=bits, path=path, exif=exif,
                      decode_ev=ev, decode_info=decode_info)
 
@@ -672,14 +677,33 @@
     except (TypeError, ValueError, ZeroDivisionError):
         pass
     try:
-        seconds, aperture = float(ifd[0x829A]), float(ifd[0x829D])
-        if seconds > 0 and aperture > 0 and out.get("iso", 0) > 0:
-            ev100 = np.log2((aperture ** 2 / seconds) * (100.0 / out["iso"]))
-            if np.isfinite(ev100):
-                out["capture_ev100"] = float(ev100)
+        ev100 = _ev100(float(ifd[0x829A]), float(ifd[0x829D]), out.get("iso", 0))
+        if ev100 is not None:
+            out["capture_ev100"] = ev100
     except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError):
         pass
     return out
+
+
+def _ev100(seconds: float, aperture: float, iso: float) -> float | None:
+    """The exposure value at ISO 100 of a shot, or None when a setting is missing."""
+    if not (seconds > 0 and aperture > 0 and iso > 0):
+        return None
+    ev100 = np.log2((aperture ** 2 / seconds) * (100.0 / iso))
+    return float(ev100) if np.isfinite(ev100) else None
+
+
+def _raw_capture_ev(path: str) -> float | None:
+    """EV100 from LibRaw's own reading of a RAW file's settings, for the containers Pillow cannot
+    read. None when a setting is missing (a manual lens has no aperture) or rawpy is too old.
+    ponytail: a second open of the file, 0.06 s in the median; fold it into _decode_raw if it shows."""
+    try:
+        import rawpy
+        with rawpy.imread(path) as raw:
+            shot = raw.other
+            return _ev100(float(shot.shutter_speed), float(shot.aperture), float(shot.iso_speed))
+    except Exception:
+        return None
 
 
 # ── export metadata (F1, docs/plans/F1-export-metadata.md) ─────────────────────
```

**Tests.** Seven new cases, in the patch below; nothing else in the two files changes and no existing assertion changes its answer. Six of the seven fail on today's code. The seventh (a deck just above sunset light, a frame without sky) passes before and after: it guards the two answers the land EV must not change. `tests/run_all.py` has no `monkeypatch`, so the LibRaw case tests `_ev100` and a missing file.

```diff
--- a/tests/test_scene.py
+++ b/tests/test_scene.py
@@ -188,3 +188,60 @@
     style = Style("auto", targets={"landscape":"land"})
     choice = AutoPhoto.select(scene.Scene("people",.9), style, cfg, 1, True, shoot={"scene":"sunset"})
     assert choice.scene == "people" and not choice.target
+
+
+def test_light_on_the_land_not_the_camera_setting_separates_dusk_from_day():
+    cfg = _thresholds()
+    def detect(land):
+        g = _stats(sky_lum=.50, lum_p50=.19, lum_p90=.63, fg_p50=land)
+        return scene.classify(g, {"sky"}, thresholds=cfg, sky_warmth=-.03, capture_ev=12.5, clouds="open").finer
+    # One camera setting and one clear sky, three amounts of light on the land.
+    assert detect(.30) == "daylight"     # 1.3 stops under mid-grey: 11.2
+    assert detect(.04) == "twilight"     # 5.9 stops under: 6.6, at sunset light
+    assert detect(.08) == "landscape"    # 7.9: between the two it is neither, and never a swap
+
+
+def test_a_night_frame_rendered_bright_is_night_and_a_dim_cloud_deck_is_overcast():
+    cfg = _thresholds()
+    # A long exposure lifts a town at night to a mid-grey median; the glow on its sky is gold.
+    town = _stats(sky_cov=.68, sky_lum=.38, sky_sat=.20, lum_p50=.37, lum_p90=.47, fg_p50=.30)
+    assert scene.classify(town, {"sky"}, thresholds=cfg, capture_ev=.3, sky_warmth=.07, warm_share=.14).finer == "twilight"
+    # Standing stones against a storm sky: the land is at dusk light, the camera was set for the sky.
+    stones = _stats(sky_cov=.60, sky_lum=.28, sky_sat=.08, lum_p10=.12, lum_p50=.21, lum_p90=.36, fg_p50=.03)
+    def detect(clouds):
+        return scene.classify(stones, {"sky"}, thresholds=cfg, capture_ev=12.6, sky_warmth=-.02, clouds=clouds).finer
+    assert detect("deck") == "mist" and detect("open") == "twilight"
+
+
+def test_gold_in_the_sky_needs_a_second_witness_only_at_a_sunlit_exposure():
+    cfg = _thresholds()
+    g = _stats(sky_cov=.33, sky_lum=.68, sky_sat=.21, lum_p50=.21, lum_p90=.74, fg_p50=.16)
+    def detect(ev):
+        return scene.classify(g, {"sky"}, thresholds=cfg, capture_ev=ev, sky_warmth=.05, warm_share=.34, clouds="open").finer
+    assert detect(12.4) == "sunset"      # the sun on the horizon, the land still at 9.4
+    assert detect(14.5) == "daylight"    # a sunlit exposure: a warm cast alone is no sunset
+
+
+def test_fog_across_the_frame_is_mist_whatever_colour_it_took():
+    cfg = _thresholds()
+    g = _stats(sky_cov=.42, sky_lum=.26, sky_sat=.19, lum_p10=.17, lum_p50=.22, lum_p90=.28, fg_p50=.20)
+    def detect(fog):
+        return scene.classify(g, {"sky"}, thresholds=cfg, capture_ev=12.9, sky_warmth=-.06, clouds="open", fog=fog).finer
+    assert detect(.68) == "mist"         # a blue cast makes fog read as open sky; the fog mask does not care
+    assert detect(.20) == "daylight"     # a bank of low cloud in a clear view
+
+
+def test_a_deck_just_above_sunset_light_is_overcast_and_a_frame_without_sky_keeps_daylight():
+    cfg = _thresholds()
+    deck = _stats(sky_cov=.41, sky_lum=.57, sky_sat=.21, lum_p50=.17, lum_p90=.64, fg_p50=.11)      # land at 8.05
+    assert scene.classify(deck, {"sky"}, thresholds=cfg, capture_ev=12.0, sky_warmth=-.14, clouds="deck").finer == "mist"
+    # A waterfall in forest shade: no sky to judge, the land 4.6 stops under mid-grey, a daylight setting.
+    shade = _stats(sky_cov=0, sky_lum=0, sky_sat=0, lum_p10=.03, lum_p50=.08, lum_p90=.21, fg_p50=.08)
+    assert scene.classify(shade, {"water"}, thresholds=cfg, capture_ev=12.3).finer == "daylight"
+
+
+def test_capture_ev_falls_back_to_libraw_only_for_its_own_key():
+    from autoedit import imageio
+    assert imageio._ev100(1/200, 14, 200) == pytest.approx(np.log2(19600))
+    assert imageio._ev100(.125, 0, 100) is None and imageio._ev100(.125, 8, 0) is None
+    assert imageio._raw_capture_ev("no-such-file.raf") is None
--- a/tests/test_scene_lighting.py
+++ b/tests/test_scene_lighting.py
@@ -124,3 +124,33 @@
         else:
             assert "cloud" not in pipe.segmenter.words
     assert np.array_equal(photos["standard", False], photos["auto", False])
+
+
+def test_auto_asks_for_mist_and_a_frame_of_fog_is_mist():
+    import numpy as np
+    from autoedit.contract import RunConfig
+    from autoedit.imageio import Image
+    from autoedit.pipeline import Pipeline
+    from autoedit.segment.base import RegionMask
+
+    class Masks:
+        def __init__(self):
+            self.words = []
+        def segment(self, im, words):
+            self.words = list(words)
+            everywhere = np.ones(im.rgb.shape[:2], bool)
+            sky = np.indices(im.rgb.shape[:2])[0] < im.height // 2
+            items = {"sky": sky, "mountain": ~sky, "mist": everywhere}
+            return [RegionMask(w, items[w], .6) for w in words if w in items]
+
+    y, x = np.indices((40, 64))
+    rgb = np.stack([.20 + .06*x/64]*3, axis=2).astype(np.float32)
+    rgb[y<20] = [.24, .30, .42]   # a blue cast: the sky colour alone says open sky
+    im = Image(rgb=rgb, exif={"capture_ev100":12.9})
+    for style, scene in (("auto", "mist"), ("standard", None)):
+        pipe = Pipeline.build(RunConfig(backend="stub", style=style))
+        pipe.segmenter = Masks()
+        _, info = pipe.process_one("sample.jpg", image=im)
+        assert ("mist" in pipe.segmenter.words) == (style == "auto")
+        if scene:
+            assert info.scene == scene
```

**Expected on the development set**, replayed, then confirmed end to end through the pipeline with all four files changed:

| | Now | After | Leave one shoot out |
|---|---:|---:|---:|
| Daylight | 23/31 | 25/31 | 24 |
| Mist | 17/27 | 20/27 | 20 |
| Twilight | 10/21 | 17/21 | 17 |
| Sunset | 9/18 | 11/18 | 10 |
| All | 59 (60.8 %) | 73 (75.3 %) | 71 (73.2 %) |
| Sure labels | 45/67 | 54/67 | 53/67 |
| Daylight/twilight swaps | 5, 2 on sure labels | 1, on a sure label | 1 |
| Fallback to `landscape` | 10 | 4 | |

21 answers change: 15 become right, 5 were wrong and stay wrong, 1 becomes wrong (an overcast city view with a cast: with an EV it leaves the no-EXIF rules and its sky reads open). The three more mist answers are fog frames with unsure labels; on sure mist labels the count stays at 11 of 15, one won and one lost. Taking one piece out of the full change gives, of 97: the land EV 68 and three swaps; the LibRaw EV 69 and two swaps; the fog rule 69; the open sky as dusk evidence 70; the gold witness 71; the deck without a brightness test 72; "very dark" without its median gate 72. Two pieces move nothing on this set at these numbers and are kept for what they close: without "rendered brightness only when there is no EV" a dark daylight frame can still be called twilight (it happened with the day level at 9.0), and without "a sky that is not open keeps daylight above dusk light" two shaded Swiss frames without sky lose their daylight.

**How far to trust it.**

- The bar of 85 % is not reached on the development set. Its ceiling is lower than the 120's: 30 labels are unsure, and 11 of the 24 errors left are on those.
- The first design was made on half of the set (52 photos, split by shoot) and frozen: 32 to 41 of 52 there, 27 to 28 of 45 on the half held back. It did not transfer. The held-back half's errors then gave three of the pieces (a dim deck is not dusk, the gold witness, the night gate) and, with the Swiss frames below, brought the day level from 9.0 to 8.25, so that half no longer tests anything; the halves now stand at 41 and 32. Leave one shoot out refits the four new numbers on the other 85 shoots each time. It tests the numbers, not the rules, and prefers 8.0 to 8.25 for the day level and 8.0 for the dusk level, without the gap between them. The gap is kept: it costs one unsure development photo and turns a wrong side into a fallback. One daylight photo alone holds the day level under 8.4; held out, it falls back.
- The 120 unlabelled Swiss photos: 4 answers change. Three frames with dark land under a dusk or storm sky leave daylight (to twilight, to mist, to the fallback), one close-up moves from the fallback to daylight; the other 70 daylight answers and all 45 mist answers stand. Two earlier drafts failed this look. With the day level at 9.0, plain daylight frames with dark land went to the fallback (3 of the first 39). With a rule "enough sky and no blue in it is mist" and a default that asked for day-level land, 10 of the 120 changed, among them daylight in forest shade and under broken cloud. So the level went to 8.25, the rule was dropped, and a frame whose sky is not open keeps daylight above dusk light. Daylight on those shoots sits much lower than on the development set (27 of 73 below 10): the day level has little room.
- The land EV assumes mid-grey land in the default RAW development. A camera JPEG sits about 1.5 stops higher, snow about 2. `raw.other` may be missing in an old `rawpy`; the photo then has no EV, as today.

**Done when**, beside the list above: `PYTHONPATH=src python tests/run_all.py test_scene.py test_scene_lighting.py` reports 27 passed, then the whole suite is green. The stub run's two images are byte-identical (checked on a copy of the tree), and so is a default export of one RAW file Pillow cannot read: the new EXIF key must not reach a Simple render. The session's scoring script (`score_dev.py`, kept in `out-webloop/f62/harness/scene/`) run on the built tree prints 73/97 with the counts above. One CHANGELOG line under F62. Then the session scores the 120 once for ticket 03. `imageio.py` changes metadata only, no pixel of a decode; the tone report is the session's call. The Builder touches the four source files, the two test files and the CHANGELOG, and changes no other number.

2026-11-26 (session): The land-EV and fog change above is built as written (scene.py, auto.toml [scenes], the two pipeline.py hunks, the imageio.py capture-EV fallback, seven tests). Fresh development set, 97 originals labelled blind and kept apart from the 120: 59 to 73 (60.8 % to 75.3 %; daylight 25/31, mist 20/27, twilight 17/21, sunset 11/18), daylight/twilight swaps 5 to 1. The 120 scored once afterwards: 97 to 94 (78.3 %; daylight 57/64, mist 10/20, twilight 20/23, sunset 7/13), no swap; six sunsets now read as twilight. Across all 217 labelled photos: 156 to 167 (71.9 % to 77.0 %). The bar is not met. Both suites green (1,433 and 1,437 passed), stub images byte-identical, tone report on the 110 in-repo RAW photos 0 dark made darker and 0 pushed into clipping, public-repo check passes. A Checker (Opus, high) found no blockers; minor findings for later: RAW formats Pillow cannot read open LibRaw a second time on every decode, default runs included (time only, never pixels); the experiments decode cache key has no code hash, so cached RAW decodes lack `capture_ev100` until cleared; `test_capture_ev_falls_back_to_libraw_only_for_its_own_key` does not assert that no other key is added; nothing checks night <= dusk <= day among the `*_land_*_ev` keys. Next: a shoot-neighbours measure (ticket 25). Both labelled sets are now spent as tests; the next check needs a fresh blind-labelled set.
