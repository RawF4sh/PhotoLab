# Kinds of photography: the list and a plan for each

Written on 2026-11-16 at the owner's request. It belongs to [FEATURE_PLAN.md](FEATURE_PLAN.md),
phase 11. Nothing here is built. The feature numbers (F21 to F35) point into that plan.

## What a pack is

A genre pack (F30) is data: which things to find, which rule sets to switch on, a default look, a
few run settings, and what the reveal check (F21) looks for. `--genre NAME` selects it. There is
no code per kind. A kind that needs a tool the engine does not have waits for that tool's feature.

Two limits hold for every kind:

- **Tone and colour of what is there: yes. Anything else: no.** A pack may brighten, darken,
  recolour and sharpen a region. It never removes, adds, reshapes, smooths away or replaces
  anything. Each kind below lists what its editors usually do that AutoEdit will not.
- **A pack is tested on photos of its kind.** At least 20, with a golden test and the tone gate at
  0 and 0. The repo has one real test set: mountain landscapes. Every other pack needs photos from
  the owner, or a licence-free set, and a download needs the owner's yes. Until then the pack
  stays `planned`.

## What unlocks what

| Feature | Kinds that need it |
|---|---|
| F30 Genre packs | all |
| F21, F22 Reveal check and hiding | street, events, real estate, cars, phone photos, travel, drone, family, school, wildlife (nest positions) |
| F23, F24 Grain, black and white, film looks | black and white, film and vintage, street, concerts, weddings |
| F31 Night sky tools | night sky |
| F32 Geometry | real estate, architecture, sea and lakes, landscape, drone, products, artwork copies, school portraits, wildlife (crop) |
| F33 Merge frames | night sky, real estate, macro, products, city at night, sunsets |
| F34 Exact colour and clean backgrounds | products, food, artwork copies, school portraits, sports halls |
| F35 Pick the keepers | sports, wildlife, weddings, events |

## Overview

"Today" says how well the engine handles the kind right now. It is judged from the rules and the
code. Only kinds 7 and 8 were measured on real photos; the others are an expectation until their
test photos exist. "Pack only" means the pack needs no new tool.

| # | Kind | Today | Waits for |
|---|---|---|---|
| 1 | Portrait and headshot | works (people scene, skin rules) | pack only |
| 2 | Wedding | works in part (concept set exists) | pack; F35, F23, F24 make it complete |
| 3 | Events and conferences | works in part | pack; F21, F35 |
| 4 | Family, newborn, children | works in part | pack only |
| 5 | School, team and volume portraits | weak | F34, F32 |
| 6 | Fashion and editorial | colour and tone only | pack only; most of the genre's retouching stays out |
| 7 | Landscape | works (the engine's home ground) | pack exists; F32 for the horizon |
| 8 | Mountains, snow, winter | works, and is the tested set | pack only |
| 9 | Sea, lakes, waterfalls | works in part | pack; F32 |
| 10 | Forest and autumn | works in part | pack only |
| 11 | Sunrise, sunset, golden hour | works in part | pack; the sun glow of F18 needs its vote |
| 12 | Aerial and drone | weak | pack; F32, F21 |
| 13 | Travel | mixed | pack; F21 |
| 14 | Night sky | expected wrong | F31, F33 |
| 15 | City at night, blue hour | weak | pack; F32, F33 |
| 16 | Concerts, stage, theatre | weak | pack; F24 helps |
| 17 | Real estate and interiors | weak | F32, F33, F34, F21 |
| 18 | Architecture | weak | F32 |
| 19 | Street | works in part | pack; F23, F24, F21, F22 |
| 20 | Products and online shops | weak | F34, F32, F33 |
| 21 | Food | works in part | pack; F34 |
| 22 | Cars and motorbikes | works in part | pack; F22 for plates |
| 23 | Macro, close-up, flowers | works in part | pack; F33 |
| 24 | Wildlife and birds | works in part | pack; F35, F32 |
| 25 | Pets | works in part | pack only |
| 26 | Sports and action | weak | F35, F34 |
| 27 | Black and white | weak (saturation at zero) | F24, F23 |
| 28 | Film and vintage | weak | F23, F24, F25 |
| 29 | Underwater | expected wrong | pack with a wider white balance range; photos |
| 30 | Phone photos | works, edits too much | pack only; F21 |
| 31 | Scans of prints and slides | weak | pack for faded prints; negatives are later |
| 32 | Artwork and document copies | weak | F34, F32 |

## People

### 1. Portrait and headshot

- **Editors do:** even out skin colour, lift the eyes a little, take yellow out of teeth, calm the
  background, warm the white balance.
- **Today:** the people scene with rules for skin and subject, a skin guard that keeps strong
  tools off faces, the `soft_portrait` look.
- **Pack `portrait`:** find skin, hair, eyes, teeth, clothing, background. New rules as data: eyes
  a touch brighter, teeth a touch less yellow, background a little darker and less coloured.
- **Stays out:** skin smoothing, blemish removal, reshaping. Background blur is an owner decision
  (FEATURE_PLAN, "Ideas").
- **Reveal check:** name badges, screens behind the person, other people.
- **Test:** 20 portraits with different skin tones; skin colour must stay inside the skin guard.

### 2. Wedding

- **Editors do:** keep the dress white with detail, keep the suit black with detail, hold skin
  steady through a day of changing light, deliver hundreds of frames that match.
- **Today:** the `wedding` concept set (skin, dress, suit, sky, foliage, flowers). Dress and suit
  are edited with the general subject rule and flowers with the foliage rule; none has a rule of
  its own.
- **Pack `wedding`:** rules for dress (highlights held, no colour cast), suit (shadows opened a
  little), flowers (colour without clipping); bursts consistency (F9) on.
- **Waits for:** F35 to pick from thousands of frames; F23 and F24 for the film-style set.
- **Stays out:** retouching, removing guests or objects.
- **Reveal check:** guests' faces, place cards with names, number plates, the venue's signs.
- **Test:** one real wedding, with the couple's permission.

### 3. Events and conferences

- **Editors do:** fix mixed indoor light room by room, clean high-ISO noise, keep faces readable
  under stage light.
- **Today:** people scene; noise reduction exists and is off by default.
- **Pack `event`:** find skin, subject, stage, screen; noise reduction on; white balance evened per
  room (needs F34's set white balance for the full effect).
- **Reveal check:** this is where it earns its place: badges, slides on screens, laptops,
  whiteboards, attendees' faces.
- **Waits for:** F21, F35.
- **Test:** one event of at least 100 frames.

### 4. Family, newborn, children

- **Editors do:** soft, warm, low contrast; less red in a newborn's skin; white blankets held.
- **Today:** people scene, skin rules, `soft_portrait`.
- **Pack `family`:** a gentler skin rule (less red), whites held, low contrast look.
- **Stays out:** skin smoothing.
- **Reveal check:** children's faces, a school crest or uniform, the house number, names on toys
  and doors, a hospital wristband.
- **Test:** 20 photos from the owner's own family, kept out of the repo.

### 5. School, team and volume portraits

- **Editors do:** the same exposure, white balance and crop on hundreds of people in front of one
  backdrop; an even backdrop.
- **Today:** brightness evened across a burst (F9). No white balance evening, no crop, no backdrop
  rule.
- **Pack `volume`:** find skin, backdrop, clothing; strict consistency.
- **Waits for:** F34 (backdrop, set white balance), F32 (the same crop for everyone).
- **Reveal check:** name boards held in the photo, crests, name tags.
- **Test:** one session of at least 50 people.

### 6. Fashion and editorial

- **Editors do:** a strong look, fabric colour that matches the garment, heavy skin and body
  retouching.
- **Today:** the `editorial` style.
- **Pack `fashion`:** find skin, clothing, background; the clothing's hue is held while the look
  changes everything else.
- **Stays out:** the retouching. AutoEdit covers colour and tone for this kind and says so.
- **Test:** 20 frames with a known garment colour.

## Outdoors

### 7. Landscape

- **Editors do:** balance the sky against the land, bring out depth, level the horizon.
- **Today:** the best-covered kind: concept set, rules for sky, mountain, snow, foliage, grass,
  water; gradients and glows on request (F18).
- **Pack `landscape`:** today's set, unchanged. It is the check that packs change nothing by
  themselves.
- **Waits for:** F32 (horizon), F33 (brackets).
- **Stays out:** sky replacement, removing people, wires or litter.
- **Test:** exists (110 photos).

### 8. Mountains, snow, winter

- **Editors do:** keep snow white, not blue and not grey; hold detail in bright snow; keep rock
  dark and textured.
- **Today:** snow and mountain rules; the highlight hold (F6, off by default); this is the test
  set the engine was tuned on.
- **Pack `alpine`:** landscape plus the highlight hold on, once the owner's vote says yes.
- **Test:** exists. This is the first pack to build, because it can be verified today.

### 9. Sea, lakes, waterfalls

- **Editors do:** a level horizon above all; water colour without an unnatural teal; bright foam
  and long-exposure water held.
- **Today:** the water rule. No horizon tool.
- **Pack `water`:** find water, sky, rock, sand, foam; foam kept white (the highlight hold).
- **Waits for:** F32. A sloping sea horizon is the most visible fault of this kind.
- **Test:** 20 photos, at least 10 with a visible sea horizon.

### 10. Forest and autumn

- **Editors do:** tame the greens, lift autumn oranges and yellows, keep the frame dark and calm,
  keep mist soft.
- **Today:** the foliage rule (trees use it too); green controls; the handling of dark photos may
  brighten a forest that is meant to be dark.
- **Pack `forest`:** a look with `preserve_exposure`; an orange and yellow band rule (two new HSL
  bands, as data plus a small op change); no dehaze on mist.
- **Test:** 20 photos, half of them dark woodland.

### 11. Sunrise, sunset, golden hour

- **Editors do:** hold the colour near the sun, warm the light, darken the top of the sky, let the
  sun glow.
- **Today:** the mood words (sunset, golden, sunrise), the highlight hold, gradients and the sun
  glow on request (F18). The automatic glow is built and not yet checked on photos with a visible
  sun.
- **Pack `sunset`:** highlight hold on; light shaping on, after its tone gate and blind vote.
- **Waits for:** F33 for bracketed sets.
- **Test:** 20 photos with the sun in or near the frame. The owner has to supply them.

### 12. Aerial and drone

- **Editors do:** cut haze, clean small-sensor noise, level the horizon; straight-down frames have
  no sky at all.
- **Today:** dehaze and noise reduction exist. A straight-down frame lands in the scene "other".
- **Pack `aerial`:** find road, field, water, roof, trees, sky; noise reduction on; stronger
  dehaze.
- **Waits for:** F32.
- **Reveal check:** drones write position and height into every file; people in gardens, number
  plates, roofs that identify a house.
- **Test:** 20 frames, half straight down.

### 13. Travel

- **Editors do:** a bit of everything, and a set that looks like one trip.
- **Today:** the generic set; each photo is judged alone.
- **Pack `travel`:** the generic set, bursts consistency, and the genre suggestion per folder
  (F30) so a day in the mountains and an evening in a city each get their own pack.
- **Reveal check:** passports, tickets, boarding passes, hotel room numbers, number plates, GPS.
- **Test:** one trip of at least 100 mixed frames, phone and camera.

## Night

### 14. Night sky: Milky Way, aurora, star trails

- **Editors do:** remove the cast and slope of city light, stretch the faint sky without burning
  star cores, clean noise without eating stars, stack frames.
- **Today:** expected to be wrong; no night-sky photo has been run. The exposure rules read a
  dark frame as underexposed and lift it; noise reduction and sharpening have no notion of stars.
- **Pack `astro`:** built (F31), untested: the landscape set, the `astro` look (without `preserve_exposure`, an open owner question), `night_sky` and noise reduction on.
- **Waits for:** F31 (sky gradient, hot pixels, star protection), F33 (stacking, trails).
- **Stays out:** adding or enlarging stars, drawn star spikes, sky replacement, telescope
  processing with calibration frames.
- **Test:** 20 night-sky photos and one stack of at least 10 frames. The owner has to supply them.

### 15. City at night, blue hour

- **Editors do:** hold the lamps, open the shadows a little, keep night looking like night, clean
  noise, straighten buildings.
- **Today:** the highlight hold and noise reduction exist; the frame gets lifted too far.
- **Pack `city_night`:** find building, street light, window, road, sky, water; hold on, noise
  reduction on, a dark look.
- **Waits for:** F32, F33.
- **Reveal check:** number plates, lit windows with people inside, signs.
- **Test:** 20 photos.

### 16. Concerts, stage, theatre

- **Editors do:** rescue faces from pure red or blue LED light, clean very high ISO, often give up
  on colour and go black and white.
- **Today:** the white balance rule would try to "correct" stage light, which is the wrong move,
  and the skin rule would treat a face lit in red as a colour fault.
- **Pack `stage`:** white balance correction off and skin colour correction off (rule values),
  strong noise reduction. The chroma guard has to be checked on light of a single colour, where
  one channel clips long before the others.
- **Waits for:** F24 (black and white as the fallback look).
- **Reveal check:** faces in the audience.
- **Test:** 20 photos under coloured light.

## Places

### 17. Real estate and interiors

- **Editors do:** balance bright windows against dark rooms, make walls vertical, make white walls
  white, keep every room of a house consistent.
- **Today:** weak. No verticals, no bracket merge; a window is not a known region.
- **Pack `interior`:** find window, wall, floor, ceiling, furniture; windows darker, room brighter,
  walls neutral.
- **Waits for:** F32 (verticals are a must), F33 (brackets are how this kind is shot), F34 (neutral
  walls, consistent set).
- **Stays out:** sky replacement, a fire in the fireplace, pictures on TV screens, removing cables
  and objects, virtual furniture.
- **Reveal check:** the strongest case after street. A listing is public and its address is
  known: family photos on walls, letters and calendars, screens, the alarm panel, names on
  children's doors, keys, medicine labels, the photographer in a mirror.
- **Test:** one whole property, bracketed.

### 18. Architecture

- **Editors do:** vertical lines, a clean sky, structure brought out, often black and white.
- **Today:** clarity and gradients exist; no verticals.
- **Pack `architecture`:** find building, glass, concrete, sky; clarity on the building only.
- **Waits for:** F32.
- **Test:** 20 photos, at least 10 shot from below.

### 19. Street

- **Editors do:** contrast, black and white, grain; nothing staged, nothing removed.
- **Today:** the generic set; no grain; black and white by saturation only.
- **Pack `street`:** find person, face, building, road, sign, car.
- **Waits for:** F23, F24.
- **Reveal check:** strangers' faces, number plates, shop signs. Whether a stranger may be shown
  differs from country to country. The tool reports; the photographer decides; F22 hides.
- **Test:** 20 photos.

## Things

### 20. Products and online shops

- **Editors do:** colour that matches the product, an evenly white backdrop, every frame of a
  product line the same, sharp from front to back.
- **Today:** weak. The engine aims for "looks good", not "is exact".
- **Pack `product`:** find product, backdrop, shadow, label; no look at all.
- **Waits for:** F34 (the main one), F32 (straight, same crop), F33 (focus series).
- **Stays out:** cutting the product onto a new background, removing dust and scratches, cleaning
  reflections.
- **Reveal check:** serial numbers and barcodes on the product, the room reflected in shiny
  surfaces.
- **Test:** one product line of at least 30 frames with a grey card in one of them.

### 21. Food

- **Editors do:** warm but not yellow, a white plate that is white, colour in the food, a quiet
  background.
- **Today:** the generic set; plate and food together are likely found as one subject.
- **Pack `food`:** find food, plate, table, hands, drink; colour on the food, background down.
- **Waits for:** F34 (white balance from the plate); F32 for straight overhead shots.
- **Reveal check:** receipts, a menu with the restaurant's name, faces behind, phone screens.
- **Test:** 20 photos.

### 22. Cars and motorbikes

- **Editors do:** deep, exact paint colour; reflections held; dark tyres with detail; a calmer
  background.
- **Today:** the subject rule; its clarity can leave halos on smooth paint.
- **Pack `car`:** find car body, wheel, window, headlight, road; paint gets colour with its hue
  held and little clarity; highlight hold on.
- **Stays out:** removing reflections, people or the background.
- **Reveal check:** the number plate, the most-asked blur there is (F22); the chassis number
  plate; faces reflected in the paint; the house behind.
- **Test:** 20 photos.

### 23. Macro, close-up, flowers

- **Editors do:** colour without burning one channel (red and magenta petals clip first), a soft
  background, sharpness on the subject only, focus series.
- **Today:** flowers are edited with the foliage rule, which is tuned for green leaves; the
  chroma guard protects single channels.
- **Pack `macro`:** find flower, insect, leaf, background; vibrance, not saturation; sharpening
  inside the subject mask.
- **Waits for:** F33.
- **Test:** 20 photos, at least 5 with red or magenta flowers.

## Animals and action

### 24. Wildlife and birds

- **Editors do:** crop hard, clean high ISO in the background while the animal stays sharp, lift
  the eye.
- **Today:** noise reduction works on the whole frame; no crop.
- **Pack `wildlife`:** find bird, animal, fur, feathers, eye, branch, sky, water; noise reduction
  weighted toward the background (a small change to the op: a weight map).
- **Waits for:** F35 (bursts), F32 (crop).
- **Stays out:** removing twigs and branches, adding a light in the eye, sky replacement.
- **Reveal check:** the GPS position. A published position of a nest or a rare animal can do real
  harm. The pack warns when `metadata = "keep"` is chosen.
- **Test:** 20 photos, at least 5 bursts.

### 25. Pets

- **Editors do:** fur detail in black and in white coats, bright eyes, indoor light fixed.
- **Today:** the subject rule.
- **Pack `pet`:** find dog, cat, fur, eye, grass; shadows opened in dark fur, highlights held in
  white fur.
- **Reveal check:** the tag on the collar (a name and a phone number), the house number, the
  owner's face.
- **Test:** 20 photos, dark and light coats.

### 26. Sports and action

- **Editors do:** choose from thousands of frames, fix hall light, clean noise, crop.
- **Today:** bursts are found (F9); nothing ranks them.
- **Pack `sport`:** find player, face, ball, field, crowd; noise reduction on.
- **Waits for:** F35 first, then F34 (one white balance per hall), F32 (crop).
- **Reveal check:** children in youth sport: faces, names on shirts.
- **Test:** one game of at least 300 frames.

## Styles that cut across kinds

### 27. Black and white

- **Editors do:** choose how each colour turns grey, set contrast by region, add grain.
- **Today:** saturation at zero.
- **Plan:** F24 (the `bw` op and three looks), F23 (grain). Any pack can be combined with a black
  and white look.
- **Test:** the contact sheet of F24.

### 28. Film and vintage

- **Editors do:** faded blacks, soft highlights, a colour cast in shadows and highlights, grain, a
  LUT from a pack they bought.
- **Today:** the mood words matte and faded, split-tone, the `warm_film` style.
- **Plan:** F23 (grain), F24 (film looks as data), F25 (LUT files), F26 (surprise me).
- **Stays out:** light leaks, dust and scratches laid over the photo, fake frames (owner
  decision).
- **Test:** the contact sheet of F24.

## Special sources

### 29. Underwater

- **Editors do:** bring back the red that water takes away, cut the haze of floating particles,
  keep the water blue or green.
- **Today:** expected to be wrong; no underwater photo has been run. The white balance limits
  are set for air, so the result would stay blue-green.
- **Pack `underwater`:** its own, wider white balance range as an opt-in rule set; dehaze; find
  diver, fish, coral, water, sand.
- **Stays out:** removing the bright specks of lit particles.
- **Test:** 20 photos at different depths. Not buildable without them.

### 30. Phone photos

- **Editors do:** little. The phone has already brightened, sharpened and cleaned the picture.
- **Today:** the engine edits a phone JPEG as hard as a camera file, which is too much. HEIC input
  is built and needs one check (F16).
- **Pack `phone`:** lower strength, no sharpening, no noise reduction, colour only. Suggested when
  the file names a phone as its camera (reading the camera's make is a small addition).
- **Reveal check:** the largest need of all kinds. Phone photos are the ones that get posted: the
  position is always in the file, and screens and documents are common.
- **Test:** 30 photos from two phones.

### 31. Scans of prints and slides

- **Editors do:** bring back faded colour, remove a cast, crop the border; turn negatives into
  positives.
- **Today:** the black and white point (F5) sets both ends from brightness. The classic fix for a
  faded print sets them per colour channel, which F5 does not do.
- **Pack `scan`:** F5 with a per-channel mode (a small change to the op, with its math test); no
  noise reduction (film grain is not noise).
- **Later:** negatives (turning them positive and removing the orange base is its own tool).
- **Stays out:** repairing dust, scratches and tears (owner decision).
- **Test:** 20 scans of faded prints.

### 32. Artwork and document copies

- **Editors do:** flat even light, exact colour, straight and square, no look.
- **Today:** weak: the engine adds a look where none is wanted.
- **Pack `copy`:** no look, no region edits; evenness and exact colour only.
- **Waits for:** F34 (neutral pick, evenness), F32 (square up).
- **Reveal check:** the document is the content, so findings of kind `document` are expected and
  listed plainly; the file's metadata is still checked.
- **Test:** 10 artworks with a colour chart in the frame.

## Order

Proposed. The owner decides (FEATURE_PLAN, "Decisions for the owner").

1. **Packs that need no new tool and can be tested soon:** mountains and snow (photos exist),
   landscape, then portrait, family, pets, forest, sunset, food, cars, travel, phone photos, as
   the owner supplies photos.
2. **Packs that the reveal check or the film features complete:** street, events, wedding, black
   and white, film, concerts.
3. **Kinds that wait for a new tool:** night sky (F31, F33), real estate and architecture (F32,
   F33), products, school portraits and artwork copies (F34), wildlife and sports (F35), macro,
   city at night, drone.
4. **Kinds that need both a new rule range and photos the owner may not have:** underwater, scans.

Kinds left off the list on purpose: telescope astrophotography, medical and forensic imaging,
infrared, and 360-degree panoramas. Each needs tools far from what this engine is.
