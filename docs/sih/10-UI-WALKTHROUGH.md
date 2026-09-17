# 10 — Every screen, card, button and switch

**What this document is for.** On stage, someone will point at something and ask "what does that
do?" This document answers that for every visible element on all seven screens — the six case
screens plus the intake screen the app opens on — in the order they appear on the page, top to
bottom. Nothing here needs you to read code. Where a control has a
non-obvious job, the *why* is one line underneath it — that line is usually the answer the judge is
actually looking for.

**How to use it.** Before the demo, read §10.1 (the frame — it is the same on every screen) and
then the section for each screen once. During the demo, keep it open on a second device. If you get
a question you cannot answer, say *"that control is in the method notes — I can show you"* and move
on. Never invent a number; every figure on screen comes from the case file described in
[document 3](03-STATUS-AND-ROADMAP.md).

**Read §10.12 as well.** With the API running, the app **opens on the New analysis screen** — the
first question a detection product has to answer is *"on what?"*. §10.12 is that screen, and it is
also the exact answer to give when a judge asks "what if I upload my own data?" You can show it
rather than describe it.


**If a technical term on screen throws you:** [document 12](12-PLAIN-LANGUAGE.md) explains SAR,
backscatter, VV/VH, dB, IoU, U-Net, hindcast, forcing, windage and look-alike in plain words. Read it
first if you have time; it is the shortest path to being able to answer anything.

---

## 10.1 The frame — what is on every screen

Everything below appears on all seven screens, in the same place. Learn this once. The one
exception is called out where it happens: the **New analysis** screen carries no case controls,
because no case has been asked for yet.

### Left rail (the sidebar)

| Element | What it is | What to say if asked |
|---|---|---|
| **SpillTrace** with the mark | The product name. The mark is drawn as SVG in the code, not an image file. | "It's a one-page app with no build step — everything ships as plain JavaScript modules." |
| **SIH 26143** | Our problem-statement ID. It is on screen deliberately, on every screen. | "That's the problem statement we're answering; the judges can match it instantly." |
| **Case** group — **New analysis**, then steps 1 to 5 | New analysis (unnumbered) → Command centre → Imagery → Slick → Drift → Vessels. Numbered because the investigation runs in that order; New analysis has no number because it comes *before* the investigation — it is where a case starts existing. | "The numbers are a path, not a menu. Start with what's in the water, end with who to ask first." |
| **Reference** group — Method | Screen 6. No step number, because it is not part of the investigation — it is the evidence behind it. | "That's where every number is defensible — held-out data, and the figures that don't flatter us too." |
| The **highlighted** row | Marks which screen you are on. A screen-reader equivalent (`aria-current`) is set too. | |

### Top bar

| Element | What it is | Why it exists |
|---|---|---|
| **Page title** (e.g. "Command centre") | The current screen name, large. Nothing sits between it and the first card — no strip, no description line. | The screen explains itself through its cards; the narration is yours. |
| **The case line** next to the title | Scene id · region · acquisition time, for the case currently loaded. **Absent on New analysis** — naming a case there would answer a question nobody asked. | |
| **Case picker** (a dropdown) | Lists the stored cases. A case that has been given a name shows that name; one that hasn't shows its scene id. The seeded demo scene appears as **`00223 · demo`**. | That `· demo` marker comes from the server's `isDemo` flag, so it can only appear on a case that really is the seeded one. |
| **Export JSON** | Downloads the entire case document — every number behind every screen — as one JSON file. Disabled until a case has loaded. | "If you don't believe a figure, this is the whole case in one file." |
| **PDF Report** *(live API only)* | Asks the server to build the incident report PDF. | Only appears when the API is running — the PDF is generated server-side from the case document. |
| **Email Report** *(live API only)* | Opens a sheet asking for recipients, then hands the report to the dispatcher. | The sheet states up front whether it will *send* over SMTP or write a `.eml` file, because that depends on whether the server has credentials configured. A button that always said "sent" would lie. |
| **Save analysis** *(live API only)* | Names the loaded case. The sheet says plainly that the run **is already stored** — it was written to disk the moment the pipeline finished — and that this gives it a name the case picker can show instead of an id. An optional description goes with it. | A demo machine fills up with `00119`, `00844`, `01041`. A name is what makes a shelf of finished runs readable a week later. It changes nothing about the computation. |
| **Print** | Print stylesheet — nav and buttons drop out, the findings print. | |
| **Live API / Offline demo badge** (top right) | **Live API** with a green dot when the local server is reachable. **Offline demo** when it is not. | Green means a real HTTP call just succeeded. Offline means the bundled demo case is being replayed from static files. Say which one is on — never claim live if the badge says offline. |
| **Spinner + stage text + Cancel** | Appears only while an analysis job is running. The text is the pipeline's own current-stage message, not a guessed label. | "That's the pipeline naming its own stage in real time." **Cancel** stops the job. |

> **On New analysis, the picker and all five case buttons are gone.** Exporting, printing or
> emailing a report for a case the reader has not asked for yet is an offer about the wrong thing.
> That screen has its own door to the saved cases, and it lands you on the Command centre.


### Where the data conditions are stated

There is **no label strip**. Earlier builds carried one — a row of small pills under every page
title naming the imagery, the mask source, the AIS mode, the drift forcing and the project status.
It was removed because five pills repeated on six screens is furniture, not information, and the
same facts are already stated where they are actually used. **You explain these conditions out
loud; the screens carry them as plain rows and sentences.**

| The condition | Where it is on screen now |
|---|---|
| **Satellite imagery** — supplied Sentinel-1 SAR | Overview → the **Acquisition** fold, whose closed summary already reads `Sentinel-1A · 2015-08-04 16:55 UTC`. Open it for `Mission`, `Polarisations`, `Product`. |
| **Segmentation mask** — model prediction, not the answer key | Imagery → the *Detection* card, unfolded, headed `Segmentation mask: Model prediction` — the pill's exact words, on the screen where you are looking at the mask. Overview → the **Provenance** fold, closed summary `U-Net · threshold 0.70`. |
| **AIS mode: Synthetic demonstration data** | Vessels → *Traffic filtering*, the first card on the screen, under the reports-ingested figure. Stated **verbatim**, unfolded, no click required. |
| **Drift forcing** — synthetic on this case | Drift → the **Forcing** fold, whose closed summary reads `Drift forcing: Synthetic scenario data`. The reason it is synthetic is on the Method screen. |
| **Status** — Research PoC, human review required | The footer of every screen. |

Two of those five sit in foldouts. That is deliberate and it is safe, because **a foldout's
summary line shows its headline fact while closed** — you can read the mission and the forcing
label without clicking anything. The two conditions the problem statement mandates in specific
words — the AIS label and the candidate wording — are *not* folded, and neither is the mask
source on the screen that shows the mask.

**The offline/live state** is the top-right badge, next to the controls it disables.

> **If you saw an older build.** This product has shed two layers of standing disclaimer: first
> three tinted prose banners at the top of every screen, then the pill strip that replaced them.
> Both said what the table above says. Nothing was weakened and nothing was hidden — the mandated
> wording `AIS mode: Synthetic demonstration data` is still on screen verbatim, the full disclosure
> is still one click away in *About this synthetic feed*, and every sentence that was removed is
> still in the exported report, on the Method screen and in these documents. What changed is that
> the screens stopped repeating themselves and **you** now say it.

### Every map frames itself

There are four maps on the product (Command centre, Slick, Drift, Vessels). **You never have to
zoom one.** Each one measures its own canvas and frames its findings on load — the slick outlines
on Slick, the drift corridor and origin zone on Drift, the vessel tracks on Vessels. Check the
**scale bar** in the corner: it should read a couple of kilometres on Slick, single-digit
kilometres on Drift, and a couple of hundred kilometres on Vessels, because that is the genuine
span of the tracks.

You can still move each map by hand if a judge asks to look closer: drag to pan, double-click to
zoom in, and the three buttons in the corner are **zoom in**, **zoom out** and **fit to content**
(the crosshair — it frames everything including the satellite backdrop, which is a slightly wider
view than the one the screen opens on). With the map focused, the arrow keys pan, `+` / `-` zoom,
and `0` fits. The page scroll wheel is deliberately *not* bound to zoom, so scrolling the page
past a map never changes what the map is showing.

Nothing about the framing is cosmetic-only: the map divides the canvas size by the span it is
framing, so a map that opened before its canvas had been laid out used to clamp to the widest
allowed view — a 2000 km picture of a 16 km scene. That is fixed; if you ever see a scale bar in
the thousands of kilometres on Slick or Drift, it is a bug and not the data.

---

## 10.2 Screen 1 — Command centre (`/`)

**The idea of this screen:** one acquisition, one detection, one ranked shortlist. Answer first,
evidence folded away underneath. It is the screen you open on.

**Say this when you open the screen:** *"One acquisition, one detection, one ranked shortlist. Every figure below is
computed from the supplied scene; nothing on this screen is a placeholder."*

> Each screen below has one of these. They used to be printed under the page title; they were
> removed from the interface, because a sentence a judge can read is a sentence you are not
> saying. They are now **your lines** — the screens show findings, you supply the narration.

### The hero block

| Element | What it is | Notes for the demo |
|---|---|---|
| **Label** — "Total detected slick area" | Names the big number. | |
| **The big number** in km² | Total area of every detected region added together. | For the demo case this is the total across all regions; the *largest single* region is the number the first answer card uses. |
| **Sub-line** | "Across N disconnected regions in the supplied Central Mediterranean, acquired 4 Aug 2015 16:55 UTC." | Region name and time come from the scene metadata, not typed in. |
| **Chips** | Four small tags: the satellite + mode (e.g. **Sentinel-1A IW**), **U-Net prediction** (or *Supplied reference mask*, depending on what produced the mask), **Synthetic AIS**, and **Seeded offline case** on the demo case. | The U-Net chip is the one that matters: it says the mask on screen was produced by our model, not taken from the dataset. |
| **Aside — the ring + score** | The top candidate's score out of its maximum, labelled **Priority candidate · \<vessel name\>**. | This is a *ranking*, not a verdict. The wording "priority candidate for investigation" is deliberate and required. |
| **Open investigation** (primary button) | Jumps to the Imagery screen — step 2. | "This is the one button that starts the walkthrough." |

### The link out — **Analyse your own scene →**

Directly under the hero, above the four answers: one quiet row, a button and a hint reading *"SAR
GeoTIFF required · wind, currents and AIS optional"*. It goes to the **New analysis** screen
(§10.12), which is where the upload form lives.

It is a row rather than a form because of what this screen is for. *"Does it work on my data?"* is
the first question anyone asks of a detection product, so the door is where the question gets
asked — but the four answers below it are what a judge came to see, and a five-slot intake form
sitting on top of them would be furniture in front of the findings. The form has a screen of its
own, and this is the one line pointing at it.

**On the offline bundle** the row is still there and still works; the screen it opens says *"No API
to upload to"* and prints the command that starts one. A static page has no pipeline to hand a file
to, and it says so rather than failing on click.

**The full "what if we give you our own data?" answer, with the lines to say, is §10.12.**


### The four answers (the numbered cards)

Four cards, numbered 1–4, each a question and its answer. Each has a small quiet jump button that
takes you to the screen where that number is derived.

| # | Question | The value shown | Where its button goes | Why it is honest |
|---|---|---|---|---|
| 1 | **How large is the main slick?** | The largest connected region, in km². | **Imagery** | The sub-line says *"fully inside the scene footprint"* — or, if it isn't, that it *"touches the scene edge, so it may continue beyond the image."* |
| 2 | **When could it have been released?** | A window in hours, with the UTC start and end underneath. | **Drift** | It is a window, never an instant. One satellite image cannot give you a release time. |
| 3 | **How old is the oil?** | `≤ N hours`. | **Drift** | The sub-line is the honest part: either *"resolvable from N hours back"* or *"bounded by the hindcast horizon, not narrowed by one image."* Say that sentence out loud — it is a strong moment. |
| 4 | **How many vessels warrant a look?** | The count of relevant candidates, with *"of N screened near the origin"* and the top score and name. | **Ranking** (Vessels) | "Warrant a look" — never "responsible". |

Under the four cards there is one small line, the **area method** sentence, stating how the area was
measured (which mask, which morphology). It is one sentence and it is worth reading on stage.

### The two wide cards

| Card | Contents | What to say |
|---|---|---|
| **Where** | A map with the supplied VV backscatter as the backdrop, the detected regions **coloured by verdict**, the supplied reference mask (dashed), the backward drift path, and the estimated origin zone (dashed). Below it a **legend** with those entries, and below that the **oil / look-alike split**. | "There's no Google Maps behind this — the satellite image *is* the basemap, drawn at its own coordinates. We refuse to place a slick on a base map we can't align." |
| **Candidate shortlist** | One row per candidate vessel: name, score, and the reason it is relevant. Has an **All candidates** button that opens the full Vessels screen. Rows are clickable and go to that vessel's detail. | If a row is excluded you will see **Excluded — irrelevant traffic** — hovering it gives the reason. |

#### The oil / look-alike split, under the locator map

This is the part to point at. The rings on that map are **not all the same thing**, and the panel
under it says so in one line each:

| Row | Colour and style on the map | What it means |
|---|---|---|
| **Consistent with oil** | Orange, solid, filled | Dark, sharp-edged and elongated the way a film is. |
| **Uncertain** | Orange, **dashed**, faint fill | Between the two thresholds, so kept for a human to settle. Still published — not quietly dropped. |
| **Screened out as a look-alike** | **Steel grey-blue**, dashed, no fill | More consistent with something else dark on the water. |
| **Not screened** | Grey | Appears only when a region had no clear water around it to measure against, so the screen returned no verdict. Never invented. |

Each row carries a **count** and the **total km² for that verdict**, summed from the regions
themselves. The head line reads *"Of the N regions drawn above, by the look-alike screen:"*. For
the seeded demo case that is **4 consistent with oil (16.34 km²), 2 uncertain (0.794 km²) and 6
screened out (9.77 km²)** of twelve regions.

**Say this:** *"Twelve dark regions, and the product only calls four of them oil. Half of what a
threshold would have handed you is screened out before anyone is asked to look at it — and it is
still drawn, in a different colour, so you can see what was rejected rather than take our word that
something was."*

**Be straight about the limit, because it is the honest half:** the screen separates *oil-like*
from *not-oil-like*. It does **not** identify what a rejected region actually is. Nothing in the
product calls a rejected patch algae, low wind or a ship wake. If a judge asks what the grey-blue
rings are, the answer is *"things this screen could not accept as oil"* — not a guess at the
phenomenon. The working behind each verdict is on the Slick screen (§10.4).


### The folded audit trail (closed by default)

Three `<details>` blocks on the demo case. Each shows a one-line summary while closed. Open them if
a judge wants proof; leave them closed otherwise. (A **fourth**, **Inputs**, appears only on a case
built from operator uploads — see §10.12 — and lists each supplied file and whether
the forcing stage actually used it.)

| Fold | Closed-state hint shows | What is inside |
|---|---|---|
| **Acquisition** | The scene region | Scene, mission, polarisations, acquisition time, centre, extent, CRS, whether a reference mask exists, the full **product id**, and the detection source. |
| **Provenance** | The pipeline version | The product id, detection source, the scene threshold and what it was chosen on, model parameter count, checkpoint, pipeline version. |
| **Processing status** | The total stage time | A small bar per pipeline stage with the stage name and its seconds, plus per-stage notes. The demo case's ten stages total about 20 seconds. |

### What this does not tell you

The last card on the screen, listing the pipeline's own recorded limitations. **Do not skip this on
stage.** Reading one or two of these out is what separates a demo from a pitch. The five are: the
AIS traffic is synthetic and no real vessel appears anywhere in the product; the drift forcing may be
synthetic, with real CMEMS currents used only where the product covers the scene in space *and* time;
the rankings are triage aids and establish responsibility for nothing; the accuracy comes from
held-out patches grouped by parent acquisition, which makes it an honest test-set score rather than a
field-validated detection rate; and the look-alike screen separates oil-like from not-oil-like
without identifying the phenomenon, so no rejected patch is called algae, low wind or a wake.

---

## 10.3 Screen 2 — Satellite analysis (`/imagery`)

**The idea:** the same pixels, four ways — VV, VH, the model's probability field, the thresholded
mask, and the dataset's own reference mask — so the judge can see exactly what the model did.

**Say this when you open the screen:** *"The supplied VV and VH backscatter, the model's probability field, the
thresholded mask and the dataset's own reference mask — the same pixels, four ways."*

### Card: **Viewer**

| Control | Options | What it does |
|---|---|---|
| **Band** (segmented) | **VV** / **VH** | VV is co-polarised — the channel oil suppresses most. VH is cross-polarised: weaker return, noisier over calm water. Hovering shows those explanations. |
| **Overlay** (segmented) | **None** / **Prediction** / **Reference** / **Both** | *Reference* is only offered when the scene actually carries a reference mask. Prediction is our model's mask; Reference is the dataset's own label. **Both** is the money shot — you see where they agree and where they don't. |
| **Overlay opacity** (slider) | 0–100% | The label updates live as *"Overlay opacity · N%"*. Fading the overlay down shows the raw radar under it. |
| **Before / after** (switch) | on/off | Splits or toggles the raw and overlaid view for a side-by-side read. |
| **Show pixels** (switch) | on/off | Removes the smoothing so you see the 512 px preview as actual pixels. Hover text: *"Show the 512 px preview as the pixels it is, without smoothing."* |
| **Run detection again** (button) | | Re-runs the detection job for this scene through the API. This is the only control on the screen that starts real work — it shows the spinner in the top bar. |

Below the images sits a small colour key: **VV backscatter, dB** / **Predicted oil** / **Supplied
reference mask** — only the ones currently switched on.

### Cards: **All layers** and **Detection**

**All layers** shows each raster layer in the case (the preview files) with what it is. **Detection**
is the numbers behind the mask. Rows in Detection:

| Row | Meaning — say this if asked |
|---|---|
| **Threshold applied** | The probability cut — **0.70**, the whole-scene operating point, chosen on validation scenes. Pixels above it become oil. (The patch-scale figures elsewhere use 0.65; a case is a whole scene, so this screen shows 0.70.) |
| **Mean probability over slick** | How confident the model is across the region it called oil. |
| **Slick area** | The area after the mask is finished (morphology applied). |
| **Raw mask area** | The area before that clean-up — so the two rows together show how much the clean-up changed. |
| **Held-out scene IoU** | The score of a *different* scene the model never trained on, so the reader can see the model generalises and isn't just good on this one. |

### Card: **Look-alike risk**

This is the screen's second act and the strongest technical differentiator. It shows what fraction
of dark regions in the scene were rejected as look-alikes rather than called oil, and the numbers
come from a screen that was fitted on our own scenes and then **tested against a published
look-alike archive it never saw**. The archive is credited by DOI and licence.

Why it matters, in one sentence: *dark water that is not oil is the single hardest thing in this
field, and most detectors never test against it.*

### Fold: **Analyst review**

Three buttons — **Accept prediction**, **Reject prediction**, **Mark for analyst review**. These
record a human's decision on top of the model's. Say: *"A human can overrule the model, and the
record keeps both the model's answer and the human's."*

### Fold: **Georeferencing**

CRS, north-west corner, south-east corner, pixel size, scene size and the no-data value. This is
the proof that the pixel grid maps to real coordinates. Say: *"Every pixel has a longitude and
latitude; that's what makes the drift run possible at all."*

---

## 10.4 Screen 3 — Slick analysis (`/slick`)

**The idea:** the geometry of the slick, region by region, plus every quality measure behind the
one area number — and a boundary an analyst can edit and re-measure.

**Say this when you open the screen:** *"Area, perimeter, length, orientation and the method behind each, plus an editable
boundary so an analyst's own delineation can be measured the same way."*

### Card: **Measured extent**

The headline geometry: **Area**, **Pixels**, **Perimeter**, **Length × width**, **Elongation**,
**Orientation**, **Compactness**, **Centroid**, and probability percentiles (**Mean**, **Median**,
**10th percentile**, **Above 0.8**). Also **Proposer** and **Calibration** — which method produced
the outline and how.

The percentile rows are the honest ones: a mean probability can hide a weak edge, the 10th
percentile does not.

### Card: **Look-alike screening**

Per-region verdicts. Each region gets a badge — **accepted**, **rejected**, **uncertain**, or
**unscreened** — and hovering the badge gives the plain-English meaning. The four meanings:

- **accepted** — consistent with an oil film.
- **rejected** — more consistent with a look-alike than with oil. (The screen does not claim to name
  *which* look-alike.)
- **uncertain** — between the two thresholds, so kept for human review.
- **unscreened** — no clear water around the region to measure it against, so no verdict is offered.

It also lists the quality measures the screen uses, with names worth knowing: **Oil-likelihood**,
**Darkness**, **Darkest tenth**, **Interior roughness**, **Edge definition**, **Solidity**,
**Contrast**, **Depolarisation**.

### Card: **Boundary**

The editable outline, with a full editing toolbar:

| Button | What it does |
|---|---|
| **GeoJSON** | Downloads the boundary as GeoJSON — the format every GIS tool reads. |
| **CSV** (one per table) | Downloads that table as a spreadsheet. |
| **Edit boundary** / **Resume editing** | Enters the editor. The label changes if a saved edit exists. |
| **Discard saved edit** | Throws away the analyst's saved edit. |
| **Save boundary** | Stores the analyst's outline. |
| **Reset to model** | Puts the model's outline back. |
| **Close editor** | Leaves the editor (keeps what is saved). |

When an analyst outline is saved, an **Analyst area N km²** badge appears next to the model's
number, so both are visible at once. Say: *"It doesn't overwrite the model. Both measurements
stay, labelled."*

### Card: **Regions** and **Region detail**

**Regions** lists every connected region with its area and badges: **edge** (the region touches the
scene edge, so it may be cut off), **outline** (the outline is geometrically questionable),
**linear** (the shape is a thin line — the signature of a ship's wake rather than a slick),
**look-alike**, and **uncertain**. Clicking a region opens **Region detail** at the bottom.

The **linear** badge is a good one to mention: it is the product noticing a ship track by its
shape.

### Fold: **How the area was measured**

The measurement chain, row by row: **Raster**, **Pixel area**, **Raw mask**, **After morphology**,
**Opening / closing radius**, **Minimum region**, **Outline simplification**, **Polygon cap**. This
is the fold to open when a judge asks *"how do you know the area is right?"* — it shows the raw
mask becoming the final polygon, one step at a time.

### Fold: **Caveats on this geometry**

The recorded caveats on the measurement. Same reason as the limitations card on screen 1.

---

## 10.5 Screen 4 — Drift reconstruction (`/drift`)

**The idea:** run the clock backwards to find where the oil came from, and forwards to see where it
is going — as particle clouds with a stated uncertainty envelope, not a single line.

**Say this when you open the screen:** *"A backward hindcast to an estimated release zone and a forward forecast from the
observed slick, both as particle clouds with an explicit uncertainty envelope."*

### Top of the screen

| Control | What it does |
|---|---|
| **Backward / Forward** (segmented) | Switches between the hindcast (where it came from) and the forecast (where it is going). Hover text: *"Where the oil came from"* / *"Where the oil is going"*. |
| **Play / pause** (small icon button) | Animates the particles through time. Its screen-reader label is "Play the drift animation". |
| **Timeline slider** | Scrubs through the hours from the observation. The label reads hours-from-observation, and can be negative (before the image) or positive (after). |
| **Run drift now** (button) | Re-runs the drift job. One of only two buttons on the product that starts real computation. |
| **CSV** | Downloads the chronology — the position of the cloud at each hour. |

### Cards

| Card | What it shows | The line to say |
|---|---|---|
| **Estimated spill age** | The `≤ N hours` bound and, crucially, whether the age is *resolvable* from one image or only *bounded*. | "One image cannot date a spill. We say so, and we say which case we're in." |
| **Estimated origin** | Release window, cloud centroid, the 50% and 90% containment radii, mean and P90 displacement, corridor area — and an **Origin** row saying how it was derived. | "That's a search box, not a pin on a map." |
| **Forecast endpoint** | Where the forward run ends: **Centre**, 50% and 90% containment, straight-line distance from the slick centroid. | |
| **What this drift run does not tell you** | The recorded limitations of this run. | Read one. |

### Folds

| Fold | Closed hint | Contents worth knowing |
|---|---|---|
| **How the uncertainty grows** | The containment radii | Why the envelope widens with time, and what the two radii mean. |
| **Particle outcomes** | Particle count | **Seeding**, **Mask pixels available**, **Particles drawn**, **With replacement** — and where particles ended up (how many beached, how many left the domain). Ours runs **300 particles**. |
| **Forcing** | Whether the forcing was real or synthetic | The honest one. **Wind source**, **CMEMS product present**, **Spatial overlap**, **Temporal overlap**, **Tolerance**, **Water cells over the scene**, **Current RMS speed**, **Wind drift contribution**, **Modes**, **Seed**. This is where you show that we use real CMEMS currents and ERA5 wind *when they cover the place and time*, and deterministic synthetic forcing when they do not — clearly labelled. **On the demo case it refuses:** the CMEMS product *does* cover this patch of sea (spatial overlap true), but its only timestep is 23 June 2026 against a 4 August 2015 acquisition — 3,975 days, far beyond the 24-hour tolerance — so temporal overlap is false and the run falls back to labelled synthetic forcing. The fold prints that reason in its own words. Say it out loud: *"we had the real product, it didn't cover the date, so we refused it rather than quietly using the wrong ocean."* |
| **Numerics** | The scheme | **Land mask**, **Resolution**, **Water fraction**, **Scheme**, **Time step**, **Steps**, **Diffusivity**, **Random walk per step**, **Expected diffusive spread**, **Windage factor**, and the **seed**. Every number here is seeded, so the run reproduces exactly. |

---

## 10.6 Screen 5 — Vessel attribution (`/vessels`)

**The idea:** rank the vessels worth asking first, against the estimated release zone and window —
and show the arithmetic, so nobody has to trust a black box.

**Say this when you open the screen:** *"Synthetic vessel tracks scored against the estimated release zone and window. A
ranking of who to ask first, not a finding of who did it."*

### Map and its controls

The map shows the tracks against the release zone. Two switches above it:

| Switch | What it does |
|---|---|
| **All N tracks** | On: draws every candidate. Off: draws only the top few (the map is kept legible by default). |
| **Drift envelopes** | Shows the drift uncertainty clouds on the same map, so you can see which tracks sit inside the search area. |

### Cards

| Card | What it shows | What to say |
|---|---|---|
| **Traffic filtering** | **Relevant traffic**, and the row *"On the basis that…"* giving the rule. Then **Closest approach**, **As a fraction of the envelope**, **At**, **Course at approach**, **Speed at approach**, **Reports inside the window**, **Reports near the envelope**, **Time near the envelope**, **Nearest pass outside the window**. | "This is the funnel: out of all traffic, these are the ones that could plausibly be in the right water at the right time." |
| **Tracks against the estimated release zone** | The spatial test — which tracks ever enter the search area. | |
| **Priority candidates** | The ranking, one row per vessel, with the **Excluded — irrelevant traffic** badge on the ones filtered out (hover for the reason). Has a **Candidates CSV** button. | Order matters. Rank one is "ask them first", not "they did it". |
| **Selected vessel** | The detail for the clicked vessel: score, badges, and a **Synthetic** badge because the track is generated. | |
| **Measured quantities** | The evidence per track: **Reports**, **Reporting interval**, **Speed range**, **Reports on a land cell**, **Behaviour pattern**, **Type relevance**, **Type rationale**, **Length**, **Completeness**, **Gaps**, **Largest gap**, **Missing field values**, **Field completeness**. | The land-cell row is a good one: it is a data-quality check on the synthetic track itself. |

### Folds

| Fold | What it is |
|---|---|
| **About this synthetic feed** | The AIS source, the **schema** (the real MarineCadastre column header — 17 columns matched byte-for-byte against a real daily extract), and the full disclosure text. The one-line label is already stated unfolded on the *Traffic filtering* card at the top of this screen; this fold is where the detail behind it lives. |
| **How the score is weighted** | Every weight in the score and where it came from. This is the "no black box" fold. |
| **Why this score** | The per-vessel breakdown of how its number was built. |
| **Track quality** | Data-quality rows for the selected track. |
| **How this track was generated** | Derivation, **Seed**, and the generation method — the proof that the synthetic track is reproducible and labelled as synthetic. |

---

## 10.7 Screen 6 — Methodology and evidence (`/method`)

**The idea:** every number on the product, with the figures that flatter it and the figures that do
not shown side by side. This is the screen you go to when a judge stops believing you.

**Say this when you open the screen:** *"How each number on this product was produced, measured on held-out data, with the
figures that flatter it and the figures that do not shown side by side."*

### The cards, in order

| Card | What it shows | Why it is here |
|---|---|---|
| **The pipeline, stage by stage** | The ten stages with what each does. | Answers "how does it actually work" without a slide. |
| **Accuracy, both ways round** | Patch-scale and scene-scale metrics side by side, each labelled with what it was measured on. | They disagree, markedly, because patches are sampled around labelled oil while a scene is mostly open water. Showing both is the difference between honest and flattering. |
| **Baseline** | The classical dark-spot detector we compare against, and its calibration. | "Any model beats something. Ours has to beat a fair opponent." |
| **Does the model beat a threshold?** | The comparison against the baseline. | |
| **Threshold** | The value in use (0.65), **Chosen by**, and **Why**. | |
| **Per-scene distribution, test split** | **Mean**, **median**, **worst** and the percentile spread across the 35 held-out test scenes. | The worst-case number being on screen is deliberate. |
| **Limitations, as recorded by the pipeline** | The four recorded limitations verbatim. | |
| **Look-alike screening: dark water that is not oil** | The archive credit (DOI, licence), coverage, and the rejection numbers. | The strongest technical slide you have, on a screen instead of a slide. |

### The folds

| Fold | What it holds |
|---|---|
| **Every measured split** | The split counts and the grouping rule. |
| **Threshold sweep, on validation** | Every threshold and its score, so the reader can see 0.65 was picked from data rather than guessed. |
| **Split protocol** | **Grouping rule**, the fractions, and the grouping key. The leak-fix story lives here: scenes are grouped by their parent Sentinel-1 product, no group spans two splits, and the numbers were re-measured after the fix — the margin over baseline *halved*, and we publish the lower number. |
| **Training** | **Architecture**, **Encoder widths**, **Bottleneck**, **Input channels**, **Normalisation**, **Upsampling**, **Parameters** (1,963,953), **Epochs**, **Batch size**, **Learning rate**, **Loss**, **Positive weight**, **Gradient clip**, **Early stopping patience**, **Augmentation**, **Seed**, **Checkpoint**, and the training duration. |
| **Example patches, worst to best** | Real patches from the held-out data, ordered worst to best. Put the *worst* one on screen when you say the model is imperfect — it is already there. |
| **How the look-alike screen was fitted, and which families defeat it** | The per-family rejection table: which kinds of look-alike the screen catches and which it does not. |
| **Data provenance** | **Imagery** and **Reference masks** (the dataset DOI), **Currents**, **AIS**, **Scenes indexed**, **Cases stored**, **API**. The receipt for every input. |
| **Reproduce this** | The exact commands in order, with the seeded note: running them on the same inputs reproduces every number, *including the synthetic AIS and synthetic forcing*. |

**One note on Reproduce this:** there used to be a second "Export case JSON" button inside it. It
was removed, because it did exactly what the header's **Export JSON** button does — one download
control is a feature; two is clutter.

---

## 10.8 The controls that start real computation

Four, and only four. If someone asks "what actually runs when I click?", these are the answer:

| Control | Screen | What it runs |
|---|---|---|
| **Run the pipeline** | New analysis | All ten stages on an uploaded scene. This is the only control that starts a case from nothing. |
| **Run detection again** | Imagery | The segmentation job — the model over the scene. |
| **Run drift now** | Drift | The particle hindcast and forecast. |
| **Cancel** (top bar) | Any, while running | Stops the running job. |

(**Save analysis** writes two strings onto a case that is already on disk. It computes nothing.)

Everything else on every screen either reads what the case file already holds or changes how
something is displayed. That is a deliberate design rule: **reads never compute.** A page refresh
cannot queue another minute of inference, which is exactly why the whole product works offline from
one bundled case.

---

## 10.9 The vocabulary, if a judge uses a term you don't know

| Term | Plain meaning |
|---|---|
| **SAR** | Synthetic Aperture Radar. The satellite sends a radar pulse and listens for the echo. It works at night and through cloud, unlike an ordinary camera. |
| **Backscatter** | How much radar energy came back. Rough water returns a lot (bright); a smooth oil film returns little (dark). |
| **VV / VH** | The two polarisation channels. VV is the one oil suppresses most; VH is noisier. |
| **dB (decibel)** | The unit backscatter is measured in. We convert the raw image numbers into it before the model sees them. |
| **IoU** | Intersection over Union. How well two shapes overlap: 1.0 is perfect, 0 is no overlap. It is the standard segmentation score. |
| **Dice** | Another overlap score; closely related to IoU and reported alongside it. |
| **Precision / recall** | Precision: of the pixels we called oil, how many were oil. Recall: of the pixels that were oil, how many we found. |
| **U-Net** | The standard shape of a segmentation neural network: it shrinks the image while learning what is in it, then grows it back while deciding per pixel. |
| **Threshold** | The probability above which a pixel is called oil. Ours is 0.65, chosen on validation data. |
| **Baseline** | The simple classical method we compare against, so "our model works" means something. |
| **Look-alike** | Dark water that is not oil — low wind, rain, ship wakes, natural films. The hardest problem in this field. |
| **Hindcast / forecast** | Running the physics backwards (where it came from) or forwards (where it is going). |
| **Particle cloud** | We release many virtual particles and let currents and wind move them. The spread of the cloud *is* the uncertainty. |
| **Forcing** | The current and wind fields that push the particles. |
| **AIS** | Automatic Identification System — the position broadcast ships are legally required to send. **Ours is synthetic and labelled as such.** |
| **Held-out / test split** | Data the model never trained on. Every accuracy number we quote is on held-out data. |
| **Grouped split** | Splitting by parent satellite acquisition, not by image, so near-duplicate images cannot leak across train and test. |

---

## 10.10 The 90-second click-through

If you have a live demo and 90 seconds, do exactly this:

1. **New analysis** — the screen it opens on. One sentence: *"This is the front door — give it a
   scene, or open a run already on this machine."* Click **Load previous saved cases**. (If you
   opened a `#/?case=…` link you will already be past this; don't go back for it.)
2. **Command centre** — read the big number. Point at the four answer cards. Say the age sub-line
   out loud (*"bounded by the hindcast horizon, not narrowed by one image"*). Then point at the
   locator's **oil / look-alike split**: *"twelve regions, four called oil."*
3. Click **Open investigation**.
4. **Imagery** — set Overlay to **Both**. "Prediction and the dataset's own label, on the same
   pixels." Point at **Held-out scene IoU**.
5. **Slick** — point at one **look-alike** and one **linear** badge. "The product notices a ship
   wake by its shape."
6. **Drift** — press play. "Three hundred particles, real advection physics and a land mask. We
   have a real CMEMS current product for this patch of sea, but its nearest timestep is nearly
   eleven years off the acquisition — so the pipeline refused it and labelled the forcing
   synthetic, instead of quietly using the wrong ocean. Open the **Forcing** fold and it tells you
   that itself."
7. **Vessels** — read the top row. "Priority candidate — ask them first. Not a finding of guilt."
8. **Method** — open **Split protocol**. "We found a leak in our own evaluation, fixed it, and our
   margin dropped from +0.189 to +0.093. We publish the lower number."


Then stop. Do not keep clicking. Let them ask.

## 10.11 If something breaks on stage

| What you see | What it means | What to do |
|---|---|---|
| You land on **New analysis** and expected the Command centre | Normal, with the API running: that is the front door, once per browser session. | Click **Load previous saved cases**, or open a case link directly — `#/?case=00223` skips the intake screen every time. Bookmark that link before the demo. |
| Top-right badge says **Offline demo** | The API is not running. The bundled demo case is being replayed from static files. | Keep going — every screen still works, and the offline bundle opens straight on the Command centre. Say: *"The demo case is bundled, so the whole product runs with no server."* |
| Everything empty / "No case has been computed yet" | No case loaded. | Pick **00223 · demo** in the case picker, top bar. |
| A number is missing / "drift not run" | That stage was not computed for this case. | Say what it means; don't invent the number. |
| The map looks empty | The raster images may still be loading. | Wait a second. If a preview PNG is missing the map still draws the geometry over the graticule — it degrades, it does not error. |
| The map is zoomed far out (scale bar in thousands of km on Slick or Drift) | The map framed itself before its canvas had a size. | Click the **crosshair** button in the map's corner controls — it refits. Resizing the window also re-measures. This was a real bug and is fixed; if you see it, note which screen. |

---

## 10.12 Screen 0 — New analysis (`#/new`), and "what if we give you our own data?"

**This is the screen the app opens on, and it is the answer to the question you get asked most.**
With the API running, a fresh visit to SpillTrace lands here rather than on a case, because the
first thing a detection product has to establish is *what it is being pointed at*. It is reachable
any time from **New analysis** at the top of the left rail, and from the **Analyse your own scene →**
row on the Command centre.

**Two doors, neither hidden.**

| Where | What it is |
|---|---|
| **The dark panel at the top** | The whole form, in one box: five file slots, four fields, then **Run the pipeline**. Hand it a scene and it runs the whole pipeline. |
| **Or open a case that has already been run** | The last six finished cases, each a row with its name (or scene id), region, area and acquisition time — plus a **Load previous saved cases** button that opens the Command centre on the most recent one. If there are more than six, a line says how many more are in the picker. |

Between them sits **What the run does** — the four named stages, for anyone who wants to know what
the twenty seconds are spent on. It explains the run; it does not configure it, which is why it is
an ordinary card below rather than anything you have to read before pressing the button.

**The order inside the panel is the order you work in:** *Files* (five slots, one required),
*Details* (the four fields), then the button. Nothing you must fill in is below the control that
consumes it — a case id is required to run, so it sits above **Run the pipeline**, not under it.

**When you open it on stage, say:** *"This is the front door. Give it a scene, or open one of the
runs already on this machine."* Then click **Load previous saved cases** and the demo begins where
it always did.

**The gate is once per browser session, and it is not a wall.** Once you have been through this
screen the app stops sending you here, so a reload mid-demo does not interrupt you. A link that
names a case — `#/?case=00223` — never passes through it at all, which is what makes the rehearsed
click-through safe. **On the offline bundle there is no gate at all**: that page opens straight on
the Command centre, because there is no API behind it to upload to.

**After a run finishes, the Command centre opens on the finished case.** Not the intake screen with
a spinner on it — the thing you asked for, with the numbers in it.

**Naming a run.** The pipeline writes the case to disk the moment it finishes; nothing about it is
waiting on you. What **Save analysis** in the top bar does is give it a name — *"North Sea, evening
pass"* rather than `00119` — and optionally a description. The case picker then shows the name, and
so does the row on this screen. The sheet says that plainly rather than pretending to be the thing
that persists the run. You can also type the name into the form *before* the run, in the **Analysis
name** field, and skip the step.

---

**What you can say and then demonstrate:** you drop a SAR GeoTIFF in, optionally a reference mask,
and — if you happen to have them — real ocean-current, wind, and vessel files. The scene is uploaded
to the machine running the pipeline, processed there through the same ten stages as the demo case,
and the finished case appears ready to walk through exactly like the bundled one.

**The five slots, in the order they sit on the screen.** Each card shows one line about the file it
wants; **hover it for the full condition** — the paragraph is the card's tooltip, not lost. Only the
scene carries a `required` tag, because every other card's line opens with the word *Optional* and
four OPTIONAL tags in a row of five say nothing the one tag does not say better.

| Slot | Card says | What it is | If you don't have it |
|---|---|---|---|
| **SAR scene** | **required** · *"Two-band GRD GeoTIFF, VV and VH."* | The Sentinel-1 GeoTIFF, ~43 MB. The one thing the pipeline cannot run without. | — |
| **Reference mask** | *"Optional. Compared, never substituted."* | A GeoTIFF of the known slick, ~4 MB. Lets the case show agreement with a ground truth. | The case runs without it; the "Reference" overlay and IoU-against-truth simply aren't shown. |
| **ERA5 wind** | *"Optional. Replaces the synthetic wind."* | A NetCDF-4 wind file, a few MB. The single biggest improvement to the drift estimate. | Deterministic synthetic wind, clearly labelled as such. |
| **CMEMS currents** | *"Optional. Used where it overlaps."* | A NetCDF-4 currents file, ~370 MB. Used only where it overlaps the scene in space and time. | Synthetic currents, or real CMEMS only where it overlaps — a stated reason where it doesn't. |
| **Real AIS** | *"Optional. Real broadcast positions."* | A CSV of vessel tracks. | Synthetic AIS, shown as `AIS mode: Synthetic demonstration data`. |

**Say this, because it is the honest and the strong version:** *"Every optional slot gives better
data where it overlaps the scene, and a stated reason where it doesn't. It is never a silent
downgrade — if we fall back to synthetic wind, the case says so on its face."*

**The four fields under *Details*,** left to right. One is required; the other three correct a
default, and a judge with a headerless GeoTIFF will reach for the third and fourth:

- **Case id.** *Required.* The label the result is stored and served under. It is suggested from the
  filename the moment a scene is staged, and can be overwritten; the server refuses an id an
  existing case already uses. Until it has a value the line under the button says so.
- **Analysis name.** Nothing to do with the computation — it is what the case list shows instead of
  an id. Leave it blank and you can add it afterwards with **Save analysis**.
- **Acquired (UTC).** Some GeoTIFFs carry no acquisition timestamp. Drift needs one, so the form
  asks for it, and the case records whether the time was *read from the product* or *typed by an
  operator* — the drift age is only as trustworthy as that field, and we say which it was. A time in
  the file always wins over a typed one.
- **Band order.** The reader would otherwise guess which band is VV and which is VH. The toggle lets
  the operator state it, so a scene with the bands the other way round is not silently mis-read. A
  header, when there is one, wins over the toggle.

**The line under the button is a three-way status,** and it is worth glancing at on stage: with no
scene it says the GeoTIFF is the one required input; with a scene but no id, *"Give the case an id
above, then this runs."*; with both, *"About twenty seconds, then the Command centre opens on the
result."*

**Why it takes a minute, not an instant, and why saying so helps you:** the pipeline is ten stages,
about 20 seconds of computation on the demo scene; a larger scene, or a queue of them, is a few
minutes. The work happens **on the laptop**, not in the browser — which is why it needs no cloud
account and your imagery never leaves the machine. Say that part too: *"Your imagery never leaves
the machine you run it on."*

**Where the files go — be ready for the data-hygiene question:** an upload is written to a separate,
gitignored directory, never into the evaluated dataset. An uploaded scene **cannot overwrite or
shadow** a supplied one: the case is stored under an id you choose, and if that id already names an
existing case the server refuses it rather than replacing it. The screen has a **Clear** control so
an operator can take their own data back off the machine after a demo without opening a shell.

**One thing to still be straight about:** the compute is local. What the website gives you is the
interface, the case library, and now the intake — but the machine you upload to has to be the one
running the pipeline. There is no third-party server in the loop, by design.

**One sentence to have ready:** *"The site is a complete working product on a bundled case, and it
takes your own scene too — it opens by asking for one, you add wind or currents if you have them,
and it runs the same pipeline locally and shows you the case a minute later, with every fallback
labelled."*

