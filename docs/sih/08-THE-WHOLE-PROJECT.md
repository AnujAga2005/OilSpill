# 8 — The whole project, taught from zero

This is the long document. It exists so that one person, in two or three sittings, can go from
knowing nothing about SAR or oil spills to being able to open any file in this repository, say what
it does and what it calls, and demo the product to a judge without a script.

It has four parts, and they are meant to be read in order:

| Part | What it gives you | Roughly |
|---|---|---|
| **1 — The project** | the mental model: what goes in, what comes out, the ten stages | §8.1 – §8.5 |
| **2 — The domain** | SAR, look-alikes, U-Net, IoU, drift physics, AIS, scoring | §8.6 – §8.16 |
| **3 — The pitch** | the six screens as they exist today, and the eight-minute path | §8.17 – §8.25 |
| **4 — Every file** | 88 source files: what each does, what it calls, what those calls do | §8.26 – §8.36 |

Documents 1–7 in this folder are shorter and task-shaped: [1](01-THE-PROBLEM.md) is the problem
statement, [2](02-WHAT-YOU-NEED-TO-KNOW.md) is a faster domain primer, [3](03-STATUS-AND-ROADMAP.md)
holds every measured number, [4](04-HOW-TO-PITCH.md) is the finals deck and demo script,
[5](05-EXPLAINING-TO-JUDGES.md) is the question bank, [6](06-PS-COMPLIANCE.md) is the clause-by-clause
audit, [7](07-IDEA-PPT.md) is the idea-stage deck. This one is the reference behind all of them.

**Every figure here was read out of the project's own output files on 10 September 2026**, from
`apps/web/demo/case-demo.json` and `apps/web/demo/metrics.json`. When the pipeline is re-run, those
files change and this document goes stale. Re-read the numbers; do not trust the prose.

---

---

# Part 1 — The project

## 8.1 What it is, in one paragraph

A satellite passes over the sea and takes a radar picture. Oil on water shows up in that picture as
a dark patch. **SpillTrace takes that one picture and produces an incident file**: where the oil is
and how much of it there is, where it drifted *from* and in what time window it was probably
released, where it will be in the next 24 hours, which ships were in that place at that time, and
which of those ships is worth investigating first — ranked, with the reasoning for every point
awarded printed next to the score. It runs in **20 seconds on a laptop with no GPU and no internet**,
and it ends with a signed PDF a responder can file.

That is the whole product. Everything below is detail.

## 8.2 What goes in and what comes out

**In —** one Sentinel-1 GeoTIFF (2048 × 2048 pixels, two radar channels called VV and VH), plus
optionally: a ground-truth mask if you have one, a CMEMS ocean-current file, an ERA5 wind file, and
an AIS traffic file in MarineCadastre's 17-column format.

**Out —** one JSON document (the *case*), six PNG previews, and a PDF. The case document is the only
thing the dashboard ever reads. Its top-level keys, straight out of
`assemble()` in [case.py:986](../../services/api/spilltrace_api/case.py:986):

```
id  pipelineVersion  generatedUtc  requestKey  status
scene the acquisition: bounds, CRS, timestamps, product id, polarisations
provenance   what was real and what was synthetic, per source
detection    which model ran, at what threshold, over how many tiles
slick  the headline geometry: total area, largest region, shape, edge flag
geometry     every region, its ring, its statistics, the GeoJSON
screening     what looked like oil and was rejected, with the numbers behind each call
forcing     the velocity field that was used, and whether it was measured or built
trajectories  the two centre-lines the map draws, plus the uncertainty rings
drift         the full particle runs, backward and forward
spillAge      the age estimate and, crucially, whether it is resolvable at all
ais           the traffic feed and its disclosure text
vessels       the ranked candidates
attribution   the ranking, the funnel, the weights
previews    the PNG filenames
limits   five sentences the product refuses to run without
```

**If you learn one thing about the architecture, learn this:** the dashboard has no model, no
physics and no opinions. It is a renderer for that document. Anything the screen says, the JSON said
first. That is what makes the claim "every number on screen is checkable" true rather than a slogan.

## 8.3 The ten stages

This is the pipeline, in order, with the real measured time for the demo scene. The stage names are
the literal strings passed to `timer.record(...)` inside `build_case()`
([case.py:704](../../services/api/spilltrace_api/case.py:704)) — you can grep them.

| # | Stage | Seconds | What happens | Lives in |
|---|---|---|---|---|
| 1 | `decode` | 9.685 | Read the GeoTIFF, pull VV and VH out, read the georeferencing and the acquisition time | `spilltrace_common/geotiff.py`, `spilltrace_ml/dataset.py` |
| 2 | `detect` | 5.674 | Run the U-Net over the scene in overlapping tiles, stitch a probability map, threshold it | `spilltrace_ml/model.py`, `case.py:infer_probability` |
| 3 | `geometry` | 1.938 | Turn the binary mask into polygons on the globe: area, perimeter, length, width, orientation | `spilltrace_ml/geometry.py` |
| 4 | `screening` | 1.785 | For every dark region, ask "is this oil or something that merely looks like it" | `spilltrace_ml/lookalike.py` |
| 5 | `forcing` | 0.033 | Decide what velocity field moves the oil — real CMEMS/ERA5 if they cover the scene, otherwise a labelled synthetic one | `spilltrace_drift/forcing.py` |
| 6 | `backward` | 0.255 | Release 300 particles in the slick and run the clock **backwards** 24 h to find the origin | `spilltrace_drift/engine.py` |
| 7 | `forward` | 0.253 | Same integrator, clock forwards, to forecast where the oil goes | `spilltrace_drift/engine.py` |
| 8 | `ais` | 0.190 | Reconstruct vessel traffic in the origin zone during the release window | `spilltrace_drift/ais.py` |
| 9 | `scoring` | 0.082 | Score every vessel on six weighted components, filter the irrelevant, rank the rest | `spilltrace_drift/scoring.py` |
| 10 | `previews` | 0.219 | Write six PNGs the browser can display without a tile server | `spilltrace_ml/preview.py` |
|  | **total** | **20.113** | | |

Two stages are not in the table because they are too fast to time separately: the **spill-age**
estimate (`spilltrace_drift/age.py`) runs off the backward result inside `assemble()`, and the PDF is
generated on demand rather than during the run.

Notice the shape of the cost: **77% of the run is decode and inference.** The physics is free. That
is worth knowing on stage, because a judge who assumes the drift simulation is the expensive part
has the system's shape wrong.

## 8.4 The four refusals

These are not caveats bolted on at the end. They are enforced in code and covered by tests, and they
are the reason the product is defensible.

1. **It never says a vessel is guilty.** The strongest phrase anywhere in the product or the PDF is
   *"Priority candidate for investigation."* A test asserts that "guilty", "culprit" and
   "responsible party" appear nowhere in the generated report.
2. **It never presents synthetic data as real.** The AIS feed carries
   `"AIS mode: Synthetic demonstration data"` in the case document, and the Vessels screen prints it
   verbatim on the first card, under the count of reports it describes. The generator's full
   disclaimer — *"These vessel
   tracks are fabricated for demonstration…"* — is in the **About this synthetic feed** fold on the
   Vessels screen, in the case JSON and in the PDF report. Drift forcing carries its own label, and
   wind and currents are labelled *separately*, because either can be real on its own.
3. **It never fabricates a number it could not measure.** The spill-age card prints the arithmetic
   that shows the age is *unresolvable* for this scene rather than inventing a midpoint. The Method
   screen ships the **worst** single scene the model was evaluated on.
4. **It never hides what it threw away.** The 8 vessels excluded from the ranking are retained in
   the case document with the reason for each exclusion, and the screen shows them.

## 8.5 The repository, top level

```
Oil/  Mask_oil/       the raw dataset — 1,200 scenes, 48 GB. Not in git. Never modified.
data/raw/             CMEMS .nc, ERA5 .nc, AIS csv, the DARTIS archive. Not in git.
data/processed/    everything the pipeline writes: splits, patch cache, cases, previews, metrics
models/         unet_vv_vh.npz — the trained weights, 7 MB, committed
services/common/      things every service needs: config, file-format readers, region naming
services/ml/  the model and everything around it: audit, cache, train, geometry, look-alike
services/drift/       physics, AIS, scoring, spill age
services/api/         the HTTP server, the case builder, the store, jobs, PDF, email
apps/web/ the dashboard: index.html, four CSS files, 18 ES modules. No build step.
scripts/              the command-line entry points — this is what you actually run
tests/     776 tests
dist/       the built, self-contained bundle: `python -m http.server` in it and it works
docs/sih/ these documents
```

Two rules about this layout that matter more than they look:

- **The dataset is never written to.** `Oil/` and `Mask_oil/` are read-only inputs. Everything
  derived lands under `data/processed/`. You can delete `data/processed/` entirely and rebuild.
- **`dist/` must contain no raw data, no absolute host paths and no credentials.**
  `scripts/build_web.py` asserts this on every single build (`check_bundle_contents()`), and fails
  the build if it finds any. That is what makes the bundle shareable.

---

---

# Part 2 — The domain knowledge

You need about six ideas. None of them require a physics degree; all of them will be asked about.

## 8.6 Why a radar satellite can see oil at all

An optical satellite takes a photograph — it needs daylight and a clear sky. **Synthetic Aperture
Radar does not.** It sends its own microwave pulse down at the sea and measures how much comes back.
It works at night and straight through cloud, which is why it is the sensor for an operational spill
service: a spill does not wait for good weather.

The sea surface is normally covered in small wind-driven capillary waves — ripples a few centimetres
across. Those ripples scatter the radar pulse back towards the satellite. **The sea therefore looks
bright.**

Oil is a film. It damps those capillary waves; the surface goes locally smooth. A smooth surface
reflects the pulse *away* from the satellite like a mirror tilted the wrong way. **So oil looks
dark.** The technical name is *Bragg scattering suppression*, and if a judge asks why radar sees oil,
that is the two-sentence answer: oil flattens the ripples, flat water sends the pulse away, the
return goes dark.

**VV and VH** are the two channels in the file. The first letter is how the pulse was sent, the
second is how the echo was received; V is vertical polarisation, H is horizontal. VV is the strong
channel where the ripple physics lives, VH is the weak cross-polarised channel that behaves
differently for rough surfaces. Using both gives the model two views of the same water. Our model
takes both as its two input channels — hence the checkpoint name `unet_vv_vh.npz`.

The pixel values in the file are in **decibels**, a logarithmic scale, because radar backscatter
spans several orders of magnitude.

## 8.7 The look-alike problem — the single hardest thing here

Everything that damps capillary waves looks like oil.

- **Low wind.** Below about 3 m/s there are no ripples to damp. A whole calm patch of sea goes dark.
- **Algal blooms and natural biogenic films** — a slick of biology, not petroleum.
- **Rain cells**, which flatten the surface under them.
- **Wind shadows** behind islands and headlands.
- **Ship wakes**, current fronts, upwelling, grease ice.

A dark patch in a SAR image is *not* a spill. It is a candidate. Any system that reports every dark
patch as oil is useless operationally, because it will cry wolf on every calm morning.

**What we do about it.** After the model produces a mask, a separate classical screen
(`spilltrace_ml/lookalike.py`) proposes every dark region in the scene and measures **seven
scale-invariant features** on each — contrast against the surrounding water, edge sharpness, shape
compactness, texture, and so on. Scale-invariant matters: they are *ratios*, so they do not change if the image resolution changes.

The measured performance, from `run_lookalike_eval.py`:

- **In-domain AUC 0.9573** over 11,623 dark regions (930 oil, 10,693 look-alike), cross-validated
  with folds grouped by parent product so no product is on both sides.
- **Cross-domain:** against the **DARTIS 2019** archive — 2,290 published no-oil SAR patches from a
  completely different source (PANGAEA, `doi:10.1594/PANGAEA.980773`, Yang & Singha 2025) that the
  screen has never seen — it rejects **69.4%** of dark regions overall: **67.8%** in the coastal
  subset, **69.5%** in the open-water subset.
- The archive groups those patches into **17 look-alike families**. Our rejection rate ranges from
  **96.2%** on the easiest family to **31.5%** on the hardest. That range is the honest finding: we
  can say *which kinds* of false alarm we handle and which still defeat us.
- For comparison, the U-Net alone raises an alarm on **288 of 340** sampled look-alike patches at
  the scene threshold of 0.7. The screen is doing real work.

**Say "reduces false alarms", never "solves look-alikes."** And note what the screen deliberately
does *not* do: it separates oil-like from not-oil-like. It does not name the phenomenon. No rejected
patch is ever called "algae" or "low wind", because we did not measure that and cannot support it.

## 8.8 Segmentation, and why U-Net

A **classifier** answers "is there oil in this image?" — one label per image. Useless here: you
cannot compute an area, a perimeter or a centroid from a yes.

**Segmentation** answers the question per pixel: "is *this* pixel oil?" The output is a mask the same size as the input. From a mask you can measure everything.

**U-Net** is the standard architecture for this (Ronneberger, Fischer & Brox, MICCAI 2015). The shape
is an encoder that halves the resolution repeatedly while learning what things are, then a decoder
that doubles it back to full size — plus **skip connections** that copy the high-resolution detail
across from the encoder to the decoder at each level. Without the skips the output is blobby, because
the decoder has to hallucinate the edges back. The U in the name is that diagram.

Ours is a small one: **1,963,953 parameters**, written in NumPy with the backward passes by hand
(`spilltrace_ml/nn.py`), trained on CPU in **1,068 seconds over 18 epochs**. It is not big and it does
not need to be — the input is two channels of greyscale texture, not a photograph.

## 8.9 IoU, and the one number that makes it mean something

**Intersection over Union** is the standard segmentation score. Overlap the predicted mask and the
true mask: IoU = (pixels both call oil) ÷ (pixels either calls oil). 1.0 is perfect, 0 is disjoint.

There are three ways to average it and they give very different answers, so know which one you are
quoting:

| Measure | Ours | What it means |
|---|---|---|
| **Patch IoU** | **0.769** | on 128 px held-out patches — the training-time number |
| **Pooled scene IoU** | **0.584** | all 35 test scenes' pixels thrown into one bucket — dominated by the big slicks |
| **Mean per-scene IoU** | **0.693** | score each scene, then average — this is the honest headline |
| **Worst single scene** | **0.046** | our worst day, shipped inside the product |

**An IoU with nothing beside it is unfalsifiable**, because a judge has no idea whether 0.69 is good.
So we built a **classical baseline to compete against ourselves** — a dark-spot threshold detector
(`spilltrace_ml/baseline.py`), the sort of thing operational services used before deep learning. It
scores **0.676**. Our model scores **0.769** on the same patches. **The delta is +0.093**, and *that*
is the number to quote.

**The leakage story, which is the credibility beat.** An earlier version of our split assigned
patches to train/val/test randomly. But the 1,200 files are crops of only **270 parent
acquisitions** — so crops of the same satellite pass were landing in both training and test, and the
model was being tested on water it had already memorised. We found it ourselves, rewrote the split to
group by **parent Sentinel-1 product id**, and re-ran everything. The margin over the baseline
**halved, from +0.189 to +0.093**. Those are the numbers we publish.

`data/processed/splits.json` records the rule verbatim: *scenes are grouped by parent Sentinel-1
product; each group is assigned to one split by a stable SHA-256 hash of the group key, so no crop of
an acquisition can appear in more than one split.* 240 groups, 169 train / 36 val / 35 test, and
**zero groups appear in two splits**.

## 8.10 What actually moves oil on the sea

Three things, and you need all three:

1. **The current.** The water itself is moving. The oil goes with it, one-for-one.
2. **The wind.** Oil floats *on* the surface, so the wind pushes it directly. The rule of thumb in
   the literature is that a slick moves at about **3% of the wind speed** — this is called the
   **windage factor**, and ours is `windage_factor = 0.03` in `DriftConfig`.
3. **Turbulent diffusion.** Two oil parcels a metre apart do not stay a metre apart; eddies too small
   to model individually spread them. We represent this as a random walk with an eddy diffusivity of
   **8.0 m²/s**.

For our demo scene the current contributes **0.2138 m/s** and the wind drift **0.1682 m/s**. **The
two terms are comparable, which is why neither can be dropped** — and it is why the problem statement
asks for ocean *and* meteorological data rather than one of them.

**Why particles and not one arrow.** If you advect a single point you get a single answer and no
honesty about uncertainty. We release **300 particles** across the slick and move each one
independently, with its own random-walk kick at every step. After 24 hours they form a cloud, and the
*size* of that cloud is the uncertainty. The P90 radius — the distance containing 90% of the
particles — is what the screen reports as the search area.

**The integrator** is **RK2 (midpoint)**: look up the velocity where the particle is, take a half
step, look up the velocity *there*, and use that for the full step. It is second-order accurate,
which for a 30-minute time step over 24 hours is more than enough. 48 steps per run.

**The land mask is real and it bites.** Particles that hit land beach and stop. In the backward run,
**95 of 300 particles beach**; forward, **21 of 300**. We do not let oil drift through rock to make a
tidier envelope, and the fact that the number is not zero is evidence the land mask works — the demo
scene is about 20 km off Malta.

## 8.11 Hindcasting: the reverse question

Forward drift is a forecast: *where is the oil going?* That is the easy direction and everyone does
it.

**Hindcasting is running the clock backwards** — *where did this oil come from, and when?* — and it
is the direction the problem statement actually asks for, because it is the one that leads to a
vessel. Mechanically it is the same integrator with the time step negated, but the *meaning* is
different: forwards you get a location, backwards you get a **location and a time window**.

The window is the horizon: the release could have been anywhere from 24 hours before the image to the
moment of the image itself, so we report `2015-08-03 16:55 → 2015-08-04 16:55 UTC` and an origin
estimate at **14.5167 °E, 35.8916 °N** with a **P90 radius of 9.60 km**.

That pair — a disc and a window — is what you intersect with vessel traffic. It is the hinge of the
whole product.

## 8.12 Spill age, and why ours says "unresolvable"

**The idea.** If you know how far the oil drifted, and you know how fast the water was moving, you
can divide to get how long it has been drifting. Age = displacement ÷ speed.

**Why it usually fails, and does here.** A slick does two things at once: it *drifts* (the whole
cloud translates) and it *spreads* (the cloud gets bigger). You can only read an age off the
displacement if the displacement is large compared to the spread. For our scene:

- displacement of the cloud centre: **6.563 km**
- P90 radius of the cloud: **9.604 km**
- ratio: **0.683**

The slick spread further than it drifted. The arithmetic does not resolve an age, so
`spillAge.resolvable` is **false** and the screen prints the ratio and says so.

**This is a feature and you should pitch it as one.** The alternative — printing "approximately 14
hours" from arithmetic that does not support it — is exactly the failure mode that makes an
attribution tool unusable. And it is **per-case, not a limitation of the method**: a compact slick in
a fast current resolves fine. Never let it be characterised as "your age estimation doesn't work."

## 8.13 AIS: what it is and why ours is synthetic

**AIS** (Automatic Identification System) is a VHF transponder that ships over 300 tonnes are
required to carry. It broadcasts, every few seconds to few minutes: MMSI (the ship's radio identity),
position, speed over ground, course over ground, heading, name, vessel type, navigational status, and
dimensions. Coastal receivers and satellites log it. It is how you know which ship was where.

**We do not have a real feed.** Licensed regional AIS costs money a student team does not have, and
the free archives do not cover the Mediterranean on 4 August 2015. So the demo case uses
**deterministic synthetic traffic**, generated from a fixed seed, and says so in four places.

**What makes this defensible rather than a cop-out** is that the synthetic feed is emitted in the
**exact 17-column MarineCadastre schema** — the format the problem statement itself names. We
downloaded a real daily extract (`data/raw/AIS_2022_06_01.csv`) and matched its header byte-for-byte
against what our API serves at `/api/cases/demo/ais.csv`. Swapping in a real feed is therefore a
*path* argument to a reader, not a rewrite. `spilltrace_drift/marinecadastre.py` is that reader, and
it handles the real file's coded vessel types, its sentinel values (heading 511 means "not
available"), and its blank fields.

The demo feed: **1,112 reports from 10 vessels**, generated across five deliberate patterns —
`origin_on_time`, `origin_wrong_time`, `course_match_far`, `slowdown_near_origin` and
`transit_background`. The mix is the point: a feed where every ship matches would prove nothing about
the filter.

## 8.14 The scoring model

Six components, weights summing to 100, defined once in `ScoringWeights`
([config.py:187](../../services/common/spilltrace_common/config.py:187)):

| Component | Max | The question it answers |
|---|---|---|
| Distance | 30 | how close did this vessel pass to the oil's estimated origin? |
| Time window | 25 | was it there during the release window, or at some other time? |
| Trajectory | 20 | was its course consistent with laying the slick along its track? |
| Behaviour | 10 | anything anomalous — a speed change, a course change, an AIS gap? |
| Vessel type | 10 | is it a type that carries oil? |
| Data completeness | 5 | how much of its record is actually filled in? |

**The part that is genuinely novel, and the part to say out loud:** the spatio-temporal match is
*temporal*. Each AIS ping is compared against where the oil was **at that ping's own timestamp** —
the drift envelope as it existed at that moment, not a static circle drawn around the origin. A
vessel that was merely "somewhere in the area sometime during the window" scores **zero** on the time
component. That is the difference between a real match and a proximity filter, and most competing
systems do the proximity filter.

For our demo case: **10 vessels → 2 relevant**, 8 filtered out (1 outside the window, 7 too far — 33
to 73 km). Top candidate **SYNTHETIC DEMO ALPHA** at **90.3 / 100**.

**Every component prints the sentence that earned it.** Not "distance: 27/30" but the actual reason.
That is what "explainable" means here, and it is checkable on screen.

## 8.15 The vocabulary sheet

Learn these well enough to use them in a sentence without hesitating.

| Term | One line |
|---|---|
| **SAR** | Synthetic Aperture Radar — active microwave imaging; works at night, through cloud |
| **Sentinel-1** | ESA's free SAR satellite pair. Ours is Sentinel-1A |
| **IW** | Interferometric Wide swath — the standard 250 km coastal mode |
| **GRD** | Ground Range Detected — the amplitude product, already projected to ground range |
| **VV / VH** | send-vertical/receive-vertical, send-vertical/receive-horizontal |
| **Backscatter** | how much of the pulse came back, in decibels |
| **Bragg scattering** | the ripple-scale mechanism that makes normal sea bright |
| **Look-alike** | anything dark in SAR that is not oil |
| **Speckle** | SAR's characteristic grainy noise, from coherent interference |
| **Segmentation** | per-pixel classification; the output is a mask |
| **U-Net** | encoder–decoder CNN with skip connections; the standard segmentation net |
| **IoU** | intersection over union; the segmentation score |
| **Data leakage** | test data the model effectively saw in training; inflates every score |
| **Hindcast** | run the drift backwards to find the origin |
| **Windage** | the fraction of wind speed a floating slick picks up; 3% |
| **Advection** | transport by the bulk flow |
| **Eddy diffusivity** | how fast unresolved turbulence spreads the cloud; 8 m²/s here |
| **RK2** | midpoint integration; second-order accurate |
| **P90 radius** | the distance containing 90% of the particles |
| **AIS** | ship transponder broadcast: identity, position, course, speed |
| **MMSI** | the ship's nine-digit radio identity |
| **CMEMS** | Copernicus Marine Service — the ocean current product |
| **ERA5** | ECMWF's reanalysis — the wind product |
| **EPSG:4326** | plain latitude/longitude on the WGS-84 ellipsoid |
| **GeoTIFF** | TIFF with georeferencing tags |
| **NetCDF-4** | the HDF5-backed scientific array format CMEMS and ERA5 ship in |

## 8.16 The one thing that is not obvious about this codebase

**There are no dependencies to speak of, and that was forced, not chosen.**

`requirements.txt` has five packages. Only **two** carry the pipeline: `numpy` and `opencv-python`.
`reportlab` and `pillow` are lazily imported for the PDF alone; `pytest` is tests only. There is no
PyTorch, no TensorFlow, no rasterio, no GDAL, no xarray, no netCDF4, no Flask, no React.

So the following are all hand-written in this repository:

- the **neural network**, with explicit backward passes (`nn.py`, `model.py`)
- the **GeoTIFF reader**, including LZW and PackBits decompression (`geotiff.py`)
- the **NetCDF-4 / HDF5 reader** (`netcdf4.py`)
- the **BEAM-DIMAP XML parser** for the metadata SNAP hides in TIFF tag 65000 (`dimap.py`)
- the **PNG writer** (`preview.py`)
- the **HTTP server** (`server.py`, on `http.server`)
- the **map**, projection and all (`mapview.js`)
- the **charts** (`chart.js`, `charts.js`)
- the **DOM layer** (`dom.js` — 278 lines instead of React)

**Pitch this correctly.** It is not "we wrote everything from scratch to show off." It is: the whole
system has an auditable dependency surface, it runs air-gapped, and it deploys anywhere Python 3.12
exists. For an NTRO use case, that is the point.

---

---

# Part 3 — Pitching the current UI

This part describes the dashboard **as it exists now**, after the restructure. If a screenshot in an
older document disagrees with this section, this section is right — but check the running product
before you trust either.

## 8.17 The design rule every screen now follows

Every screen was rebuilt around one rule:

> **The answer first. The mandated disclosure in the open. The working folded.**

Concretely, on every screen:

- The **conclusion** is the first thing on the page — a hero figure, or a summary card of four stats.
- Anything the **problem statement requires us to disclose** (the AIS is synthetic; the forcing is
  synthetic) is stated on the card that uses it, in plain text: the mandated AIS label sits under
  the reports-ingested figure on the Vessels funnel, the forcing label on Drift's *Forcing* fold.
  This
  has been through three forms — first three tinted prose banners, then a row of small pills under
  every page title, now a line on the card itself. Each move cut repetition, never substance: the
  required wording survives verbatim — `AIS mode: Synthetic demonstration data` — and the paragraph
  explaining *what the label means* lives on the Method screen, in the exported report and in these
  documents.
- The **diagnostics and provenance** — weights, thresholds, numerics, generator internals, quality
  flags — moved into `<details>` foldouts. Each foldout's summary line keeps its headline fact
  visible while closed, so nothing is hidden, it is one click away.
- Prose that merely restated a number sitting next to it was deleted.

Why it matters for the pitch: **you can now present a screen top to bottom without scrolling past
something you have to explain away.** Your eye and the judge's travel in the same direction.

## 8.18 The frame

Around every screen: a **top bar** with the case selector, the run metadata and the actions (rebuild,
report, dispatch), and a **left side-nav** in two groups —

- **Case** — Overview, Imagery, Slick, Drift, Vessels (steps 1–5)
- **Reference** — Method

Routing is a hash router (`#/drift?direction=forward`), so **every view is a URL**. Practical
consequence for the demo: you can pre-open the exact state you want in a second tab. If the live
click fails, you navigate by URL instead of fumbling.

The screen definitions live in one table at [main.js:29](../../apps/web/app/main.js:29) — path, title,
short label, step number, icon, module, group. Six entries. Adding a screen is one entry plus one
module.

## 8.19 Screen 1 — Overview (`/`)

**What is on it, in render order** ([command.js:20](../../apps/web/app/screens/command.js:20)):

1. `U.hero` — **26.90 km² total area**, sub-line "Across 12 disconnected regions in the supplied
   Central Mediterranean, acquired 4 August 2015", four chips (Sentinel-1A IW · U-Net prediction ·
   Synthetic AIS · Seeded offline case), and on the right a score ring: **90.3 / 100**, "Priority
   candidate · SYNTHETIC DEMO ALPHA", with an **Open investigation** button.
2. Four **answer cards** in a row — the four questions the PS asks, each with its number and a link
   to the screen that proves it:
   - ① largest slick **15.76 km²** → Imagery
   - ② release window **24 h** → Drift
   - ③ age **unresolvable** → Drift
   - ④ vessels **2 of 10** → Ranking
3. The area-method sentence (how the km² was computed).
4. A two-column block: the locator map, and the candidate shortlist.
5. Folded: acquisition, provenance, processing.
6. **"What this does not tell you"** — the five limits, always open.

**What to say (about 45 seconds).**

> "This is one Sentinel-1 image of the Central Mediterranean, taken on the 4th of August 2015. The
> system found **26.9 square kilometres of oil across 12 separate regions**, and it has already
> ranked the traffic — that's the 90.3 on the right, and it says *priority candidate*, not *guilty*.
>
> These four cards are the problem statement's four questions with our four answers. Each one links
> to the screen that proves it.
>
> And this scene is in our **held-out test split** — 240 satellite acquisitions, split by parent
> acquisition, and not one of them appears on both sides of the line. The model has never seen this
> water. We'll show you the split rule on the last screen."

**The trap on this screen, and a judge will find it.** There are two area figures. The headline
**26.9 km²** is the total across all 12 regions; card ① says **15.76 km²**, the largest *single*
connected region. Card ① also says *"the largest connected region, fully inside the scene
footprint"* — meaning the slick does not run off the edge of the image, so 26.9 is a complete
measurement of what the model found, not a lower bound. **If you demo a scene that does touch the
edge, that card changes its own wording** (the conditional is at
[command.js](../../apps/web/app/screens/command.js), in the `sub:` of the largest-slick answer). Do
not say it unless the screen says it.

## 8.20 Screen 2 — Imagery (`/imagery`)

**In render order** ([satellite.js:35](../../apps/web/app/screens/satellite.js:35)):

1. **Viewer** card — the SAR image with band toggle (VV / VH), overlay toggle (prediction / reference
   / both / none), opacity slider, a compare mode, and a "Run detection again" button.
2. Two columns: the six preview tiles on the left; on the right the **detection** card and the
   **look-alike** card.
3. Folded: the analyst's note, and the georeferencing.

**What to say (30 seconds).**

> "This is the actual radar image. Oil is dark because it flattens the ripples the radar bounces off.
> Orange is what our model predicted. I can flip to the supplied ground truth" — *toggle* — "and back,
> and this is the overlap the accuracy number is computed on.
>
> Note there's no colour anywhere except the data. Everything in the chrome is grey on purpose."

The six tiles are VV, VH, prediction, probability, reference mask, and the comparison composite. If
the judge is technical, the **probability** tile is the interesting one — it shows the model's
confidence before thresholding, and the soft edges are honest.

## 8.21 Screen 3 — Slick (`/slick`)

**In render order** ([slick.js:30](../../apps/web/app/screens/slick.js:30)):

1. **Measured extent** — four stats: total area, largest region and its share of the total, extent
   (length × width, with elongation and bearing), perimeter (with compactness — 1.0 is a circle).
   Buttons export GeoJSON and CSV.
2. **Boundary** — the map with the outlines, editable.
3. Two columns: the region table (all 12), and the detail for the selected region.
4. **Look-alike screening** — every dark patch in the scene, its three decisive statistics, its
   verdict, and whether it lands on a published slick.
5. Folded: the morphology method, and the quality flags.

**What to say (40 seconds).**

> "Twelve regions, measured on a sphere — not by counting pixels, because a pixel near the pole covers
> less ground than a pixel at the equator, and we compute the true area of each pixel row.
>
> This card is the one I'd point at" — *the screening card* — "because it's the only place in the
> product that reports something we **threw away**. Every dark patch in the scene, the three numbers
> the decision turned on, and the verdict. And notice the verdict is 'rejected', not 'algae' — we
> separate oil-like from not-oil-like, we don't claim to name the phenomenon."

The verdict badges are `accepted` / `uncertain` / `rejected` / `unscreened`. **`uncertain` is the
interesting one** — a patch between the two thresholds is kept for human review rather than forced
into a decision.

## 8.22 Screen 4 — Drift (`/drift`)

**In render order** ([drift.js:29](../../apps/web/app/screens/drift.js:29)):

1. **Hindcast summary** (or Forecast — there is a Backward/Forward toggle) — horizon, origin spread
   P90, mean displacement, search corridor area.
2. **The map with a timeline scrubber.** Drag it and the envelope grows: at +2 h it is small, at
   +24 h it is large. That growth *is* the uncertainty accumulating, and showing it is the point.
3. Two columns: **origin zone** and **spill age**.
4. **The forcing notice — never folded.** The sentence saying whether the velocity field was measured
   or constructed. Everything above inherits its meaning from that sentence, so it stays on screen.
5. Folded: the spread chart, the particle outcomes, the forcing parameters, the numerics.
6. The caveat card.

**What to say (60 seconds — this is the screen that wins the round).**

> "Everyone forecasts forwards. The problem statement asks for **hindcasting** — run the clock
> backwards and find where it came from.
>
> We release 300 particles in the slick and integrate them backwards for 24 hours under the current,
> a 3% windage term for the wind, and a random walk for turbulence. Here's the scrubber" — *drag it* —
> "and you can watch the uncertainty accumulate. The answer isn't a point, it's this disc: **9.6 km
> P90 radius**, centred at 14.52 East, 35.89 North, with a **24-hour release window**.
>
> Two honest things on this screen. **21 of the 300 particles beach** — they hit land and stop. We
> don't let oil drift through rock to make a tidier envelope. And the **spill age says
> unresolvable**: the slick spread 9.6 km while its centre moved 6.6 km, so the arithmetic doesn't
> support an age, and we print the ratio instead of inventing a number."

If asked which forcing term dominates: **the current, 0.214 m/s, against 0.168 m/s of wind drift.
They are comparable, which is exactly why neither can be dropped.**

## 8.23 Screen 5 — Vessels (`/vessels`)

**In render order** ([vessels.js:48](../../apps/web/app/screens/vessels.js:48)):

1. **The filter funnel** — how 10 vessels became 2, and the screen's first card. Its first figure,
   *AIS reports ingested*, carries `AIS mode: Synthetic demonstration data` verbatim on the line
   beneath it, so the mandated label is the first data condition a reader meets on the one screen
   where it matters most. The funnel comes *before* the shortlist deliberately, because the PS
   clause is "the irrelevant traffic is to be filtered out."
2. **The verdict card** — the top candidate and its score.
3. The map with the tracks.
4. Two columns: the ranking table, and the evidence for the selected vessel.
5. Folded: the weights, the full explanation text, track-quality defects, the generator's own intent,
   and the feed disclosure detail.

> **Changed from an earlier build.** This screen used to say the same thing three times: a tinted
> banner above the funnel, the pill strip under the page title, and the "About this synthetic feed"
> fold. The banner and the strip are both gone. The mandated label now sits on the funnel card —
> unfolded, verbatim, no click — and the full disclosure detail is still in the fold.

**What to say (60 seconds).**

> "Before anything else, the line under the first figure on this screen: **the AIS is synthetic**.
> We don't
> have a licensed feed. What we do have is the exact 17-column MarineCadastre schema the problem
> statement names — we downloaded a real daily extract and matched the header byte for byte, so
> swapping in a real feed is a file path, not a rewrite.
>
> Now the funnel: **10 vessels, 8 filtered out** — one was outside the release window, seven were 33
> to 73 kilometres away. Two remain. And every exclusion is **kept and auditable**, not deleted.
>
> Top candidate scores 90.3 out of 100, and every component prints the sentence that earned it —
> not 'distance 27 of 30', the actual reason.
>
> The part I'd defend hardest: each AIS ping is matched against where the oil was **at that ping's own
> timestamp**, not against a static circle. A ship that was merely in the area at some point in the
> window scores zero on time."

**Never** say "the guilty ship", "we identified the polluter", or "this vessel did it". The product
will not say it and neither should you.

## 8.24 Screen 6 — Method (`/method`)

**In render order** ([methodology.js:23](../../apps/web/app/screens/methodology.js:23)):

1. **Pipeline card** — the ten stages with their measured times.
2. **The honesty card** — the headline accuracy figure.
3. Two columns: **the baseline** and **the worst case**. Deliberately side by side, because an
   accuracy figure with no baseline beside it and no worst case under it is a number an operator
   cannot act on.
4. **Look-alike** results.
5. **Limitations** — in the open.
6. Folded: look-alike detail, scale, thresholds, protocol, training curves, sample images.
7. Folded, outside the metrics switch: the dataset provenance, and how to reproduce the run.

**What to say (45 seconds).**

> "Mean per-scene IoU **0.693** across 35 held-out scenes. On its own that number means nothing, so we
> built a classical dark-spot detector to compete against ourselves — it gets **0.676**, we get
> **0.769** on patches, and the honest claim is the **+0.093** delta.
>
> And here" — *the worst-case card* — "**is our worst single scene: IoU 0.046.** That's inside the
> product, not just in the pitch.
>
> One more thing. Our first split leaked — the 1,200 files are crops of only 270 satellite passes, and
> crops of the same pass were landing in train and test. We found it, regrouped the split by parent
> acquisition, re-ran everything, and our margin over the baseline **halved**. These are the
> post-fix numbers."

## 8.25 The eight-minute path, and what to do when it breaks

**The path.** Overview (45 s) → Imagery (30 s) → Slick (40 s) → Drift (60 s) → Vessels (60 s) →
Method (45 s) → PDF (20 s). That is 5 minutes of talking, which leaves room for the questions that
will interrupt you.

**Before you present:**

1. `.venv/bin/python scripts/run_api.py` and confirm http://localhost:8765 loads.
2. Confirm the case selector shows **`00223 · demo`** and that you are on it. The old `00053` case
   — a superseded pre-retrain checkpoint on a scene outside the current split — has been deleted,
   so it can no longer be selected. Every other entry was built by `scripts/build_cases.py`, which
   refuses anything that is not a held-out test scene.
3. Open a second tab on `dist/` served statically, as the fallback. It needs no API.

**If the API dies mid-demo:** switch to the `dist/` tab. The dashboard falls back to the offline
fixtures in `demo/` automatically (`apiMode()` in [api.js:23](../../apps/web/app/api.js:23)) and every
screen still renders — you lose only "run it again" and the live PDF. Say so plainly: "that's the
offline bundle, it's the same case document."

**If a screen is blank:** it is the render gate, not a crash. Click into the window and switch
screens once.

**If someone asks for a number you don't have:** "I don't have that measured, so I won't guess — it's
in the roadmap." That answer costs you nothing and buys you everything else you said.

---

---

# Part 4 — Every file

## 8.26 How to read these tables

**Calls** lists the project's *own* modules that the file imports — the edges of the internal call
graph. Standard-library and NumPy imports are omitted; they are everywhere and tell you nothing.

Line counts and line references were taken on 10 September 2026. Use them as a starting point, not a
guarantee — grep the symbol name if a jump lands in the wrong place.

The dependency direction is strict and never violated:

```
scripts/  →  services/api/  →  services/drift/  ─┐
             →  services/ml/     ─┼→  services/common/
apps/web/ →  (HTTP only, never imports Python)  ┘
```

`services/common` imports nothing from the project. Everything else may import it.

---

## 8.27 `services/common/spilltrace_common/` — the foundations

Five real modules. Everything else in the project sits on top of these, and none of them import
anything from the project.

### `config.py` (313 lines) — the single source of truth

Every path, seed, label and tunable in the project. Nothing else hard-codes a directory.

| Symbol | What it is |
|---|---|
| `REPO_ROOT`, `IMAGE_DIR`, `MASK_DIR`, `DATA_DIR`, `RAW_DIR`, `PROCESSED_DIR`, `CACHE_DIR`, `MODELS_DIR`, `WEB_DIR` | the filesystem layout. `IMAGE_DIR`/`MASK_DIR` are overridable by env var |
| `SPLITS_PATH`, `METRICS_PATH`, `NORM_STATS_PATH`, `CASES_DIR`, `PREVIEW_DIR`, `CHECKPOINT_PATH` | the specific artefacts |
| `PIPELINE_VERSION = "spilltrace-0.1.0"`, `MODEL_VERSION`, `BASELINE_VERSION` | stamped into every case |
| `SEED_SPLIT = 20180803`, `SEED_SYNTHETIC_FORCING = 26143`, `SEED_SYNTHETIC_AIS = 4726143` | the three fixed seeds that make runs reproduce bit-for-bit |
| `LABEL_AIS`, `LABEL_DRIFT_*`, `LABEL_STATUS`, `LABEL_CANDIDATE`, `LABEL_REGION_APPROXIMATE` | **the mandated disclosure strings, defined once.** `LABEL_AIS` is literally `"AIS mode: Synthetic demonstration data"` |
| `PreprocessConfig` | `patch_size=128`, `min_oil_fraction=0.002`, `max_patches_per_scene=24`, **`max_scenes=240`** |
| `TrainConfig` | epochs, learning rate, batch size, patch budgets per split |
| `DriftConfig` | `horizon_hours=24`, `time_step_minutes=30`, `particle_count=300`, `windage_factor=0.03`, `diffusion_m2_s=8.0`, `seed=26143` |
| `ScoringWeights` | 30 / 25 / 20 / 10 / 10 / 5, with a `.total` property that must be 100 |

| Function | What it does |
|---|---|
| `_load_local_env()` | reads `.env` at import time — this is how SMTP credentials reach `dispatch.py` without ever being in code |
| `display_path(path)` | a path fit to publish: relative to the repo when it is inside it. **This is what keeps absolute host paths out of the bundle** |
| `cmems_path()`, `era5_path()` | locate the forcing files if present, else `None` |
| `stable_seed(key, base)` | SHA-256 of a string into a deterministic integer. Used so every scene gets its own reproducible seed |
| `utc_now_iso()`, `write_json()`, `read_json()`, `ensure_dirs()` | the shared plumbing |

### `geotiff.py` (851 lines) — the GeoTIFF reader

A dependency-free TIFF/GeoTIFF reader, because GDAL and rasterio were not reachable. It parses the
IFD tag table, decompresses the strips or tiles, and reconstructs the georeferencing.

| Symbol | What it does |
|---|---|
| `lzw_decode(data, expected)` | LZW decompression, written out — the compression Sentinel-1 GRD products actually use |
| `packbits_decode(data)` | the other TIFF compression |
| `_numpy_dtype(bits, sample_format, big_endian)` | maps TIFF's sample description onto a NumPy dtype, endianness included |
| `Affine` | the six-number pixel→world transform, with the inverse |
| `GeoInfo`, `_parse_geo_keys`, `_build_geo_info` | the GeoTIFF key directory → CRS and EPSG code |
| `TiffMeta` | width, height, bands, dtype, transform, CRS, nodata, and the raw tag dict |
| `GeoTiff` | the reader object: open, read a band, read a window, close |
| `read_geotiff(path, bands, step)` | **the function everything else calls.** Returns `(array, TiffMeta)` |
| `read_meta(path)` | metadata only, no pixels — used by the audit to scan 1,200 files quickly |

### `netcdf4.py` (1093 lines) — the NetCDF-4 / HDF5 reader

CMEMS and ERA5 ship NetCDF-4, which is HDF5 underneath. This is a minimal HDF5 reader: superblock,
B-tree, heap, object headers, chunked layout, and the shuffle/deflate filters.

`_Buf` is the byte cursor, `Datatype`/`_parse_datatype` decode HDF5's type messages, `_Layout` and
`_Filter` handle chunked and compressed storage, `Node` is a group or dataset in the tree, and
`NetCDF4File` is the public object — open a file, walk its variables, read an array.

You will probably never touch this file. It is here because there was no other way to read the
forcing products.

### `dimap.py` (362 lines) — the metadata SNAP hides in the TIFF

ESA's SNAP toolbox writes BEAM-DIMAP XML into **TIFF tag 65000**. That is where the acquisition
timestamp, the polarisations, the product id and the band names actually live — the TIFF tags alone
do not have them.

| Function | What it does |
|---|---|
| `parse_snap_utc`, `parse_compact_utc`, `iso_utc` | SNAP's two timestamp formats → `datetime` → ISO |
| `ProductId`, `parse_product_id(name)` | decompose `S1A_IW_GRDH_1SDV_20150312T051245_..._CE50` into mission, mode, product type, polarisations, times, orbit. **This is where the group key for the split comes from** |
| `DimapHeader` | the parsed result |
| `parse_header(blob)` | XML → `DimapHeader`, by regex rather than a parser, because the blob is not always well-formed |
| `read_header(path, limit)` | read just the first `HEADER_BYTES` of a file and parse — cheap enough to run over 1,200 files |
| `read_full(path)` | the complete metadata dict |

### `regions.py` (155 lines) — offline place names

A lookup table of sea bounding boxes. `region_label(lat, lon)` returns "Central Mediterranean" or
"Persian Gulf"; `describe_location(lat, lon)` builds the longer phrase.

**This is deliberately approximate and says so.** Every case carries
`LABEL_REGION_APPROXIMATE = "Region name is an approximate offline label, not a gazetteer lookup"`.
No network, no geocoder, no pretending otherwise.

### `cmems.py` (459 lines) — real ocean currents, and the overlap test

| Symbol | What it does |
|---|---|
| `parse_time_units(units)` | decode NetCDF's `"hours since 1950-01-01"` convention into a scale and epoch |
| `CmemsWindow` | the time slice of the product that covers a scene |
| `CmemsSurface` | the loaded `uo`/`vo` surface velocity field, with bilinear sampling in space and linear in time |
| `open_default()` | find and open the CMEMS file if one exists, else `None` |

**The important behaviour is the refusal.** `CmemsSurface` checks that the product covers the scene
in *both space and time* before it will be used. If the file is a March 2017 Persian Gulf product and
the scene is August 2015 in the Mediterranean, it declines, and the forcing falls back to synthetic
with a label saying so. It does not silently interpolate across three years.

### `era5.py` (555 lines) — real wind, same contract

`WindWindow`, `Era5Wind`, `open_default()`, and `Era5Error`. Reads ERA5's 10 m `u10`/`v10` fields
through the same `NetCDF4File`, applies the same overlap test, and reuses `_time_list` and
`parse_time_units` from `cmems.py`.

Wind and current are resolved **independently**, which is why the case document has both a
`driftMode` and a separate `windMode`. Either can be real on its own.

---

## 8.28 `services/ml/spilltrace_ml/` — the model and everything around it

### `nn.py` (412 lines) — neural-network primitives with hand-written gradients

This is the deep-learning framework, in 412 lines.

| Symbol | What it does |
|---|---|
| `Parameter` | a weight array plus its gradient buffer |
| `Layer` | the base class: `forward(x)`, `backward(grad)`, `parameters()` |
| `Conv2d` | 2-D convolution. Forward by im2col; **backward derived by hand** |
| `BatchNorm` | normalise per channel, with running statistics for inference |
| `ReLU`, `MaxPool2`, `UpsampleNearest2` | activation, downsample, upsample — each with its own backward |
| `Sequential` | chain layers; backward walks the chain in reverse |
| `conv_block(...)` | conv → batchnorm → ReLU, twice. The U-Net's repeating unit |
| `Adam` | the optimiser: moment estimates, bias correction, weight decay |
| `clip_gradients(params, max_norm)` | global-norm gradient clipping |
| `precision(dtype)` | a context manager to run a block in a given float precision |
| `sigmoid(x)` | numerically stable |

`tests/test_nn_gradients.py` checks every backward pass against a **finite-difference numerical
gradient**. That is the test that makes hand-written backprop trustworthy, and it is worth mentioning
if a judge asks how you know the maths is right.

### `model.py` (242 lines) — the U-Net

`UNet` assembles `conv_block`s from `nn.py` into encoder, bottleneck and decoder with skip
connections, and exposes `forward`, `backward`, `parameters`, `save`, `load`. `make_optimizer(model,
lr, weight_decay)` returns a configured `Adam` over its parameters. **1,963,953 parameters**, two
input channels (VV, VH), one output channel (oil probability).

**Calls:** `nn`.

### `dataset.py` (438 lines) — scenes, splits and patches

| Function | What it does |
|---|---|
| `resolve_band_indices(header, band_count)` | which TIFF band is VV and which is VH — read from the DIMAP header, not assumed |
| `load_scene(image_path, mask_path, want_mask)` | **the entry point.** Returns a `Scene`: the two channels in dB, the mask if present, bounds, CRS, transform, timestamps, product id, group key |
| `make_splits(...)` | **the leakage fix.** Groups scenes by parent product id, hashes each group key with SHA-256, assigns whole groups to train/val/test at 0.70/0.15/0.15 |
| `split_of(splits, scene_name)` | which split a scene is in, or `None` |
| `iter_patches(scene, cfg)` | tile the 2048² scene into 128 px patches with stride 128 — **no overlap, so patches from one scene cannot straddle a split boundary** |
| `select_patches(...)` | keep patches with enough oil and not too much no-data; cap per scene |
| `compute_norm_stats(...)`, `normalise`, `normalise_batch` | per-channel mean/std, computed on train only and stored in `norm_stats.json` |
| `LABEL_OIL`, `LABEL_INVALID` | the label encoding, so a no-data pixel is never counted as background |

**Calls:** `config`, `dimap`, `geotiff`.

### `cache.py` (359 lines) — the patch cache

Turns raw scenes into training-ready `.npz` arrays so training does not re-decode 48 GB every epoch.

`discover_pairs()` matches images to masks; `annotate_from_audit()` attaches the audit's verdict to
each pair; `choose_scenes()` applies the `max_scenes = 240` budget; `build_cache()` walks the chosen
scenes, extracts patches, stacks them and writes one file per split plus an index;
`load_split(split)` and `load_manifest()` read them back.

**Calls:** `config`, `dimap`, `geotiff`, `dataset`.

### `train.py` (494 lines) — the training loop

`train(...)` is the whole of Phase 3: load the cache, build the model, run epochs, evaluate on
validation, sweep the threshold, run the classical baseline on the same patches for comparison, write
`metrics.json` and the checkpoint, and render sample images.

Supporting: `subsample`, `augment_batch` (flips and rotations), `cosine_lr` (schedule),
`predict_probabilities`, `run_epoch`, `write_samples`.

**Calls:** `config`, `baseline`, `cache`, `nn`, `dataset`, `metrics`, `model`, `preview`.

### `metrics.py` (213 lines) — losses and scores

`split_target` separates the oil label from the invalid-pixel mask; `bce_with_logits` and `soft_dice`
are the two loss terms, both **masked** so no-data pixels contribute nothing; `combined_loss` mixes
them. `confusion` counts TP/FP/FN/TN, `metrics_from_confusion` turns those into IoU, Dice, precision
and recall. `threshold_sweep` and `best_threshold` pick the operating point; `per_patch_metrics`
gives the distribution rather than just the mean.

**Calls:** `dataset`, `nn`.

### `baseline.py` (251 lines) — the honest opponent

A classical dark-spot detector: `despeckle` (median filter), `local_background` (mean of the
surrounding valid water), `contrast_score` (how much darker than that background), `clean_mask`
(morphological open/close), `predict_patch` / `predict_batch`, and `calibrate` to fit its threshold on
the training split.

**This file exists so the U-Net has something to beat.** It scores 0.676 against the model's 0.769.
Without it the model's number is unfalsifiable.

**Calls:** `dataset`, `metrics`.

### `geometry.py` (505 lines) — pixels to the globe

| Function | What it does |
|---|---|
| `row_pixel_area_m2(transform, rows)` | **the honest area calculation.** A pixel's ground area shrinks with latitude, so area is computed per row, not as one constant |
| `mask_area_m2(mask, transform)` | sum those row areas over the mask |
| `haversine_m`, `ring_length_m`, `local_metres` | great-circle distance, ring perimeter, and a local tangent-plane projection for shape maths |
| `denoise(mask, cfg)` | morphological cleanup before contouring |
| `_contour_to_ring` | OpenCV contour → a closed lon/lat ring |
| `ring_self_intersects`, `signed_area_deg2`, `orient_ring` | validity and winding order, so the GeoJSON is well-formed |
| `_shape_statistics` | length, width, elongation, compactness, orientation |
| `_quality_flags` | flags a caller should know about, including **`touchesSceneEdge`** |
| `analyse(...)` | **the stage entry point.** Mask in, full geometry block out: every component, its ring, its statistics, the summary and the GeoJSON |

**Calls:** `geotiff` (for `Affine`).

### `lookalike.py` (752 lines) — the look-alike screen

| Symbol | What it does |
|---|---|
| `FeatureSpec` | one of the seven features: its name, how it is measured, which direction means "more oil-like" |
| `ScreenConfig` | thresholds and minimum region size |
| `propose(plane, valid, cfg)` | **find every dark region in the scene.** Classical, not the U-Net — which is why the look-alike numbers survive a retrain |
| `region_features(...)` | measure the seven features on one region |
| `_shape_numbers(region)` | the shape half of the feature set |
| `Screen` | the fitted linear screen: weights, thresholds, `score()` |
| `_phrase(...)` | turn a feature's contribution into the English sentence the UI prints |
| `verdict(...)` | score → `accepted` / `uncertain` / `rejected` / `unscreened`, with the reasoning |
| `fit(...)`, `design_matrix(rows)` | fit the screen on labelled regions |
| `auc(scores, labels)` | the evaluation metric |
| `grouped_folds(groups, folds, seed)` | **cross-validation folds grouped by parent product** — the same anti-leakage discipline as the split |
| `measure(...)`, `outcome_counts(...)` | the evaluation summary |
| `describe_features()` | the seven features as text, for the Method screen |

### `dartis.py` (370 lines) — the external look-alike archive

Reader for **DARTIS 2019** (PANGAEA `doi:10.1594/PANGAEA.980773`), 3,655 patches, 1,365 with oil and
2,290 without, split into coastal/open × oil/no-oil subsets.

`find_index(directory)` locates `DARTIS_2019.tab`; `read_index(path)` parses it into `Patch` records;
`_cluster_of(name)` extracts the **look-alike family** from the patch name — that is where the 17
families come from; `summarise(patches)` counts them; `present(patches, images_dir)` keeps only the
ones actually downloaded; `load_image(...)` reads a patch; `oil_mask(patch, shape)` rasterises its
annotation boxes.

**This is our cross-domain evidence.** A screen fitted on one dataset and evaluated on a completely
different published one is a much stronger claim than a held-out split of the same data.

### `preview.py` (313 lines) — the PNG writer

`write_png(path, image, alpha)` writes a PNG by hand — `_chunk(tag, payload)` builds the chunks with
their CRCs. `downsample` / `downsample_max` reduce 2048² to something a browser can hold (max-pooling
for masks, so a thin slick does not vanish). `stretch` maps decibels to display range.
`mask_png` and `probability_png` colourise. `render_comparison` builds the side-by-side.
`render_scene_previews(...)` is the stage entry point and writes all six.

**Calls:** `config`.

### `audit.py` (828 lines) + `audit_report.py` (662 lines) — Phase 1

`audit(...)` walks `Oil/` and `Mask_oil/`, opens every file, and records for each: dimensions, bands,
dtype, CRS, transform, nodata, band statistics, acquisition time, product id, region, and any
problem. `_scan_pair` does one image/mask pair; `_audit_forcing` checks whether CMEMS/ERA5 actually
overlap the dataset in space and time; `_forcing_verdict` turns that into the go/no-go sentence;
`_assemble` builds the report; `_dataset_bounds` computes the overall footprint.

`audit_report.render_markdown(report)` renders it as `DATA_AUDIT.md`, and `_consequences(report)`
writes the "so what" section — what each finding forces the rest of the build to do.

**This is why the project never assumed a filename, a band order or a date.** Everything downstream
reads the audit.

**Calls:** `config`, `dimap`, `geotiff`, `regions`.

---

## 8.29 `services/drift/spilltrace_drift/` — physics, traffic and scoring

### `forcing.py` (1177 lines) — where the velocity comes from

The largest file in the drift service, because "what moves the oil" has four possible answers and all
four have to be handled honestly.

| Symbol | What it does |
|---|---|
| `LatLonGrid` | a regular lon/lat grid with bilinear sampling |
| `Forcing` | the interface every field implements: `velocity(lon, lat, when)`, `is_water(lon, lat)` |
| `NoLandMask`, `GridLandMask` | land tests. `GridLandMask` is the one that beaches particles |
| `SyntheticSpec`, `SyntheticForcing` | **the deterministic synthetic current** — a tidal streamfunction seeded from the scene id, so it reproduces exactly and is labelled as constructed |
| `WindField`, `NoWind`, `SyntheticWind`, `GriddedWind` | the wind side, same pattern |
| `CmemsForcing` | the real CMEMS field wrapped in the `Forcing` interface |
| `resolve_wind(...)` | pick the wind source: ERA5 if it covers the scene, else synthetic, else none |
| `resolve_forcing(...)` | **the stage entry point.** Returns `(forcing, decision)`, where `decision` carries the mode, the human label, the wind mode, and the numbers. That decision dict is what the UI prints |

**The nuance worth knowing for the Q&A:** even in synthetic mode, the *magnitude* of the current
(0.2138 m/s for the demo scene) is measured from the real CMEMS product over that scene's own
footprint. Only the spatial pattern is constructed. Say that precisely — it is a stronger claim than
"it's synthetic" and it is true.

**Calls:** `cmems`, `config`, `era5`.

### `engine.py` (579 lines) — the particle integrator

| Function | What it does |
|---|---|
| `seed_from_mask(...)` | scatter particles over the detected slick |
| `seed_from_point(...)` | scatter them around a point, for a forward run from a known release |
| `_step(...)` | **one RK2 midpoint step plus the random walk.** This is the physics, in one function |
| `simulate(...)` | **the stage entry point.** Runs all 48 steps for all 300 particles, records the timeline, computes the envelope, the corridor and the origin estimate, and counts the outcomes (drifting / beached / left domain) |
| `convex_hull_indices(points)` | the hull of the particle cloud |
| `hull_ring(...)`, `_circle_ring(...)` | the envelope polygon the map draws |
| `distances_m(...)` | particle spread, for the P50 and P90 |
| `_ring_area_km2(...)` | the search-corridor area |
| `to_geojson(result)` | export |

Called twice per case with the time step negated for the backward run — same code, both directions.

**Calls:** `config`, `geotiff` (`Affine`), `forcing`.

### `age.py` (154 lines) — the resolvability test

Small and important. `estimate(backward)` computes the displacement of the particle-cloud centroid
against the cloud's own P90 radius, forms the ratio, and returns the age **with `resolvable: true` or
`false`**. `_centroid(step)` and `_resolution(timeline)` do the work.

For the demo case: 6.563 km ÷ 9.604 km = 0.683 → **not resolvable**, and the UI prints the arithmetic
rather than an age.

**Calls:** `ais` (for `haversine_km`, `iso_utc`, `parse_utc`).

### `marinecadastre.py` (800 lines) — the real AIS schema

The 17-column MarineCadastre/NAIS format: its columns, its coded values, and readers and writers for
it.

| Symbol | What it does |
|---|---|
| `FieldSpec` | one column: name, type, whether it may be blank, its sentinel |
| `vessel_group(code)` | the AIS vessel-type integer → a group name (this is where "Oil tanker" comes from) |
| `nav_status_text(code)` | the navigational-status integer → text |
| `_text`, `_number`, `_positive`, `_code`, `_bearing`, `_imo`, `_mmsi` | the coercions the real file needs. **`_bearing` is where heading 511 = "not available" is handled** |
| `parse_time`, `format_time`, `format_base_datetime` | the timestamp format |
| `parse_row(row)` | one CSV row → a report dict, or `None` if unusable |
| `check_header(fieldnames)` | **rejects a file whose header is not the real schema.** This is the test behind the byte-for-byte match claim |
| `iter_reports(...)`, `read_csv(...)` | stream a real extract, filtered by bounds and window |
| `to_row`, `iter_rows`, `write_csv` | emit our synthetic feed in the same format |
| `source_label(path)` | the provenance string |

### `ais.py` (1133 lines) — the synthetic feed

Generates deterministic traffic around the drift envelope. Everything here is seeded; the same case
produces the same ships every time.

| Symbol | What it does |
|---|---|
| `EnvelopeTrack`, `envelope_from_drift(backward)` | the drift envelope **as a function of time** — this is what makes the temporal match temporal |
| `VesselPlan`, `Leg`, `VesselType` | a planned vessel and its route |
| `build_plans(env, cfg, rng)` | **design the scenario**: some vessels crossing the envelope in-window, some out of window, some far away. The mix is the point — a feed where everything matches proves nothing |
| `_straight_track`, `_crossing_track`, `_closest_to_envelope`, `_place_clear_of`, `_turn_off_the_bearing`, `_drift_axis` | the route generators |
| `realise_track(plan, cfg, rng)` | turn a plan into timed AIS reports, with realistic jitter |
| `clean_reports(...)` | the same cleaning a real feed would need — so the pipeline is exercised, not bypassed |
| `metres_per_degree`, `offset`, `haversine_km`, `bearing_deg`, `parse_utc`, `iso_utc` | geometry and time helpers used across the drift service |
| `generate_ais(...)` | **the stage entry point** |
| `to_geojson(feed)`, `export_marinecadastre_csv(feed, path)` | export, the second one in the real schema |

**Calls:** `config`, `marinecadastre`.

### `scoring.py` (758 lines) — explainable attribution

| Function | What it does |
|---|---|
| `closest_approach(reports, env)` | **the core computation.** For each ping, how far was this vessel from where the oil was *at that ping's timestamp*. Returns the closest approach, when it happened, and whether it was in the window |
| `score_distance`, `score_time`, `score_trajectory`, `score_behaviour`, `score_type`, `score_completeness` | the six components. Each returns points **and the sentence that earned them** |
| `_component(name, earned, maximum, reason)` | the shape of every component result |
| `relevance(approach)` | is this vessel relevant at all, and if not, why not — the reason strings the funnel prints |
| `confidence_band(total, maximum)` | the score → a band |
| `score_vessel(...)` | one vessel, all six components |
| `rank_vessels(...)` | **the stage entry point.** Score everyone, filter the irrelevant, sort, return candidates plus the funnel |
| `traffic_funnel(vessels, scored)` | the 10 → 2 breakdown, with every exclusion and its reason retained |
| `candidates_csv(ranking)`, `explanation_block(entry)` | export and the prose block |

**Calls:** `config`, `ais`.

### `__init__.py` (48 lines)

Re-exports the useful names from `engine` and `forcing` so callers can `from spilltrace_drift import
simulate`.

---

## 8.30 `services/api/spilltrace_api/` — the server and the case builder

### `case.py` (1121 lines) — the orchestrator

**If you read one file, read this one.** It is the ten stages, in order, in one function.

| Symbol | What it does |
|---|---|
| `CaseRequest` | the inputs: scene, detector, thresholds, particle count, seeds. `.key()` hashes them, so identical requests reuse stored results |
| `Stage`, `_Timer` | `timer.record(name, started, note)` is where the ten timings come from |
| `scene_paths(scene)`, `available_scenes(limit)` | locate a scene's image and mask |
| `_load_checkpoint()` | load `unet_vv_vh.npz` once |
| `_pad_scene`, `_tile_origins` | tile a 2048² scene for inference with overlap |
| `infer_probability(...)` | run the U-Net tile by tile and stitch a full-scene probability map |
| `detect(scene, request, say)` | probability map → binary mask, at the scene threshold. Can also run the classical baseline instead |
| `_norm_stats`, `_scene_threshold`, `_baseline_config`, `_load_screen`, `_screen_config` | load the fitted artefacts |
| `nominal_spacing_m(transform, height)` | ground sample distance, needed by the screen's scale-invariant features |
| `screen_region(...)`, `slick_screener(...)`, `screen_scene(...)` | the look-alike stage |
| **`build_case(...)`** | **the ten stages.** See below |
| `_largest_slick`, `_polygon_of`, `_all_outlines`, `_km`, `_product_field`, `_region_of` | shaping helpers |
| `assemble(...)` | **stage outputs → the frontend contract, adding nothing new.** Every key the dashboard reads is defined here |
| `LIMITS` | the five limit sentences, at the bottom of the file, each written as *claim, full stop, qualifier* because `U.limitList` splits on that first full stop |

**The call chain inside `build_case`, in order** — this is the whole product in fifteen lines:

```
dataset_mod.load_scene(...)     → timer.record("decode")
detect(scene, request, say)               → timer.record("detect")
  └ infer_probability → UNet.forward, tile by tile
geometry_mod.denoise(...) → geometry_mod.analyse(...)
      → timer.record("geometry")
screen_scene(...)           → timer.record("screening")
  └ lookalike_mod.propose / region_features / verdict
forcing_mod.resolve_forcing(...)    → timer.record("forcing")
  └ cmems.open_default / era5.open_default, or SyntheticForcing
drift_engine.seed_from_mask(...)
drift_engine.simulate(..., backward)     → timer.record("backward")
drift_engine.simulate(..., forward)             → timer.record("forward")
ais_mod.generate_ais(backward, key, is_water)   → timer.record("ais")
scoring_mod.rank_vessels(feed, backward)    → timer.record("scoring")
preview_mod.render_scene_previews(...)          → timer.record("previews")
assemble(...)  ← and age_mod.estimate(backward) is called inside it
```

**Calls:** `config`, `geotiff`, `age`, `ais`, `engine`, `forcing`, `scoring`, `dataset`, `geometry`,
`lookalike`, `preview`, `model`, `store`.

### `server.py` (1007 lines) — the HTTP API, on the standard library

`ROUTES` at [server.py:76](../../services/api/spilltrace_api/server.py:76) is a tuple of
`(name, regex)` pairs, and `route(path)` returns `(endpoint, parameter)`. The full surface:

| Method | Path | Handler | Returns |
|---|---|---|---|
| GET | `/api/health` | `_health` | API mode, checkpoint presence, case count, dispatch mode |
| GET | `/api/metrics` | `_detection_metrics` + `_lookalike_metrics` | everything the Method screen renders |
| GET | `/api/scenes` | `_scenes` | scenes available to run |
| GET | `/api/cases` | `_cases` | stored case summaries |
| GET | `/api/cases/{id}` | `_case` | the full case document |
| GET | `/api/cases/{id}/images` | `_images` | the preview index |
| GET | `/api/cases/{id}/slick` | `_section` | just the slick block |
| GET | `/api/cases/{id}/trajectories` | `_section` | just the trajectories |
| GET | `/api/cases/{id}/vessels` | `_vessels` | the ranking |
| GET | `/api/cases/{id}/ais.csv` | `_ais_csv` | **the feed in MarineCadastre's 17 columns** |
| GET | `/api/cases/{id}/report` | `_report` | the PDF |
| GET | `/api/eval/{file}.png` | `_eval_image` | evaluation imagery |
| GET | `/api/jobs/{id}`, `/api/jobs/{id}/result` | `_job`, `_job_result` | job polling |
| POST | `/api/cases/{id}/detect`, `/drift` | `_submit` | queue a run, return a job id |
| POST | `/api/cases/{id}/dispatch` | `_dispatch` | email the report |
| POST | `/api/jobs/{id}/cancel` | | cancel |
| GET | anything else | `_static` | the dashboard files |

`SpillTraceHandler` extends `BaseHTTPRequestHandler`. `_send`, `_json`, `_fail` are the response
helpers; `_case_or_404` is the lookup; `_public_result` strips anything that should not leave the
host. `ApiOnlyHandler` is the same without static serving — used by `build_web.py` to call the API
**in-process**, which matters because localhost is not reachable from the build sandbox.

`pick_demo_scene()` chooses which scene the demo case is built from. **There is no override
parameter** — it picks, deterministically, from what is available. `build_demo(force, particles)`
runs the pipeline on it and stores the result. `run(...)` starts the server.

**Calls:** `dispatch`, `reports`, `case`, `jobs`, `store`, `config`, `marinecadastre`.

### `store.py` (195 lines) — persistence

`CaseStore` reads and writes `data/processed/cases/{id}.json` and the companion `{id}.mask.npz`.
`path_for`, `exists`, `load`, `load_raw`, `save`, `delete`, `save_mask`, `load_mask`, `summaries`,
`__iter__`. `_check(name)` validates the id — **this is the path-traversal guard**, and it is why
`/api/cases/../../etc/passwd` does not work. `summarise(payload, path)` builds the short form the case
picker shows.

**Calls:** `config`.

### `jobs.py` (192 lines) — the background queue

`Job` (id, kind, scene, state, progress, result, error) and `JobRunner` (a single worker thread,
`submit` / `get` / `list` / `cancel` / `shutdown`, with `_evict_locked` bounding memory and `_loop`
as the worker). `JobCancelled` is the cooperative cancellation signal.

Why it exists: a detection takes 20 seconds and an HTTP request should not block for 20 seconds. The
dashboard submits, gets a job id, and polls.

### `reports.py` (868 lines) — the incident PDF

`generate_incident_report(...)` builds the document. `_reportlab()` imports reportlab **lazily** and
raises `ReportUnavailable` if it is not installed — so the whole product still works without it, only
the PDF is missing. `build_case_number(case, override)` makes the reference number. `_styles`,
`_kv_table`, `_wrapped`, `_score_table`, `_patch_table`, `_footer` lay it out. `_value`, `_coord`,
`_bounds`, `_release_window`, `_top_candidate`, `_forward_endpoint`, `_first_coordinate` format the
content and, importantly, **degrade to "Not available" rather than to a guess**.

`tests/test_incident_features.py` asserts the accusatory vocabulary is absent and that "priority
candidate for investigation" is present.

**Calls:** `config`.

### `dispatch.py` (347 lines) — email

`dispatch_case_email(...)` generates the report and sends it. `parse_recipients`,
`allowed_recipients`, and **`_check_allowlist`** — mail only goes to addresses on an explicit
allowlist, so a demo cannot spray a real inbox. `_smtp_config`, `_smtp_host`, `_sender`, `_env_bool`
read credentials **from `.env`, which is gitignored**; there is no password anywhere in the tree.
`dispatch_mode()` reports which mode is active — `.eml` file, or real SMTP — and the UI shows it.
`build_email`, `_default_body`, `_case_summary`, `_shorten` compose the message; `send_email` sends
it.

**Calls:** `reports`.

---

## 8.31 `scripts/` — what you actually run

| Script | Command | What it does |
|---|---|---|
| `run_audit.py` (73) | `.venv/bin/python scripts/run_audit.py` | Phase 1. Walks the dataset, writes `audit.json` and `DATA_AUDIT.md`. Calls `audit.audit()` then `audit_report.render_markdown()` |
| `run_preprocess.py` (115) | `... scripts/run_preprocess.py` | Phase 2. Splits, patch cache, normalisation stats, scene previews. Calls `cache.build_cache()`, `dataset.load_scene()`, `preview.render_scene_previews()` |
| `run_train.py` (88) | `... scripts/run_train.py` | Phase 3. Calls `train.train()`. Writes the checkpoint and `metrics.json` |
| `run_scene_eval.py` (228) | `... scripts/run_scene_eval.py` | Whole-scene evaluation, not 128 px patches. `threshold_grid`, `confusion`, `scores`, `aggregate`, `evaluate_split`. Writes `scene_metrics.json` — this is where 0.584 pooled / 0.693 mean / 0.046 worst come from |
| `run_lookalike_eval.py` (917) | `... scripts/run_lookalike_eval.py` | The look-alike evidence. In-domain cross-validation (`cross_validate`, `grouped_folds`), cross-domain on DARTIS (`collect_dartis_rows`, `screen_on_dartis`), and the U-Net comparison (`detector_on_dartis`). Writes `lookalike_metrics.json` |
| `fetch_dartis2019.py` (284) | `... scripts/fetch_dartis2019.py --limit N` | Downloads the DARTIS archive from PANGAEA. `resolve_index`, `read_index`, `collapse_to_patches`, `fetch`, `report_manifest` |
| `run_api.py` (61) | `.venv/bin/python scripts/run_api.py` | **Starts the API and dashboard on :8765.** `--build-demo` runs the pipeline once and stores the case |
| `build_cases.py` (220) | `... scripts/build_cases.py --list` | **Pre-builds stored cases for other scenes**, so they appear in the dashboard's picker. `--list` shows every test scene with its IoU; `--test-split --limit N` builds the top N. **Refuses train/val scenes unless `--allow-any`**, because their scores beat the published accuracy |
| `make_report.py` (62) | `... scripts/make_report.py demo` | Generates the PDF from the command line, optionally dispatching it |
| `build_web.py` (494) | `... scripts/build_web.py` | **The production build.** See below |

### `build_web.py` in detail

It does five things, in order, and any one of them can fail the build:

1. **Verify the frontend.** `check_javascript(node)` syntax-checks every module (using node if
   available); `check_imports()` verifies every `import` resolves to a real file; `check_css()`
   checks every class the JS references exists in the CSS; `check_html()` checks the entry point.
2. **Write the offline fixtures.** `_LoopbackHandler` and `call_api(path)` call the API
   **in-process** — no socket, because localhost is blocked in the build sandbox — and
   `write_fixtures()` saves the responses as JSON under `apps/web/demo/`. That is what makes the
   dashboard work with no server.
3. **Strip host paths.** `strip_host_paths(payload)` removes absolute paths from the fixtures.
   `previews.directory` is an absolute host path and must never reach the bundle.
4. **Assemble `dist/`.** `build_dist()` copies the app, the styles, the fixtures and the previews.
   `copy_images(...)` moves the PNGs.
5. **Assert the bundle is clean.** `check_bundle_contents()` fails the build if `dist/` contains raw
   datasets, absolute host paths, or anything credential-shaped.

`report(manifest)` prints the summary: **59 files, 4,117 KB** in total, of which the dashboard itself
is **397.9 KB across 24 JS/CSS/HTML files** — the rest is preview PNGs and the offline fixtures.

---

## 8.32 `apps/web/` — the dashboard

No build step, no framework, no bundler, no CDN. `index.html` loads four stylesheets and one ES
module, and the browser does the rest. Same-origin only: **no analytics, no web fonts, no API keys.**

### The core

| File | Lines | What it does |
|---|---|---|
| `index.html` | 38 | four CSS links, one `<script type="module" src="app/main.js">`, an inline SVG favicon, and a `<noscript>` that tells you where the JSON is |
| `app/main.js` | 607 | **the bootstrap.** `SCREENS` (the six-entry table), `frame()` / `brand()` / `topbar()` / `sidenav()` build the chrome, `context(route)` builds the `ctx` object every screen receives, `render()` swaps the active screen, `boot()` starts it. Also `loadCase`, `selectCase`, `runAnalysis`, `cancelAnalysis`, `dispatchIncidentEmail`, `pageHeader` (title and actions — nothing between the title and the first card), `caseSelector` |
| `app/dom.js` | 278 | **the framework, in 278 lines.** `h(tag, props, ...children)` builds real DOM nodes — no virtual DOM, no diffing. `mount`, `append`, `frag`, `icon`, `trapFocus` (modal accessibility), `announce` (the toasts), `debounce`, `raf` |
| `app/state.js` | 138 | one store. `get`, `set(patch)`, `subscribe(fn)`, `load(name, loader)` for async slices with a status key, `annotation`/`annotate` for the analyst's notes, `resetSelection` |
| `app/router.js` | 75 | a hash router. `parse`, `current`, `href`, `go`, `setParams`, `onRoute`, `start`. **Every view is a URL** |
| `app/api.js` | 362 | the API client **with an offline fallback**. `apiMode()` reports live or offline; `probe()` decides which. `health`, `metrics`, `scenes`, `cases`, `loadCase`, `imageIndex`, `report`, `reportPdfUrl`, `dispatchEmail`, `imageUrl`, `csvUrl`. `submitDetect`/`submitDrift` + `runJob`/`awaitJob`/`jobStatus`/`cancelJob` for the polling loop |
| `app/ui.js` | 640 | **the component library.** See below |
| `app/format.js` | 176 | every number the UI prints goes through here. `km2`, `km`, `pct`, `num`, `int`, `metric`, `lat`, `lon`, `coord`, `utc`, `hours`, `seconds`, `bytes`, `bearing`, `axis`, `clip`, `slug`. `DASH` and `isMissing` are the missing-value contract — **a missing value renders as an em dash, never as 0** |
| `app/icons.js` | 55 | `ICONS` and `BRAND_MARK` as SVG path strings. Authored here **so `h(..., {html})` never receives API data** — that is the XSS boundary |
| `app/mapview.js` | 828 | the map: a canvas, an equirectangular projection, no tile server. `createMap(container, options)`, `mapLegend(items)`, `haversineKm(a, b)`. The map **frames itself**: `fit(bounds, pad)`, `fitContent()`, `fitFindings(pad)` and `refit()`, with `measure()` reading the laid-out canvas size *before* every fit — the fix for maps that opened at a 2000 km view of a 16 km scene |
| `app/layers.js` | 433 | **the one place that turns a case document into map primitives.** `C` (colours), `Z` (z-order), `raster`, `baseRasters`, `slickRings`, `slickLayers`, `driftLayers`, `originLayers`, `vesselLayers`, `sceneVectors` |
| `app/chart.js` | 212 | `lineChart`, `strip`, `stackBar` — hand-built SVG |
| `app/charts.js` | 310 | the richer set: `lineChart`, `distributionStrip`, `histogram`, `sparkline`, `stackedBar` |
| `app/exporters.js` | 273 | `candidatesCsv`, `slicksCsv`, `patchesCsv`, `driftCsv`, `downloadCsv`, `downloadJson`, `exportName`, `printPage` |

### `ui.js`, the components you will see referenced everywhere

| Component | What it renders |
|---|---|
| `hero({label, value, unit, sub, chips, aside})` | the big figure at the top of Overview |
| `heroStat({value, unit, label, fraction})` | the score ring beside it |
| `answer({step, question, value, unit, sub, action})` | the numbered answer cards |
| `stat`, `metric` | the four-across figure blocks |
| `card(title, {hint, note, flush, sunken, id, actions}, ...body)` | the standard container. **Takes `actions`** |
| `foldout(title, {hint, note, open, id}, ...body)` | a `<details>`. `hint` keeps the headline visible while closed. **Does *not* take `actions`** — this is the mistake to avoid when editing a screen |
| `notice(text, {kind, strongPrefix})` | a tinted one-sentence card. Used **inside** dialogs, folds and "what this does not tell you" cards, where one line genuinely has to be read before the reader acts — not as a page-top banner |
| `limitList(items)` | the "what this does not tell you" list. **Splits each string at its first `". "`** into claim and qualifier — keep that shape when editing `LIMITS` |
| `badge`, `chip`, `bar`, `scoreRing`, `legend`, `segmented`, `button`, `field`, `dialog` | the small pieces |
| `rows`, `row(key, value, {mono, stack, muted})` | key/value lists |
| `loadingState`, `missingState`, `failedState`, `emptyState`, `stateSwitch(status, value, render, options)` | **the four load states, handled uniformly.** Every screen starts with a `stateSwitch`, which is why nothing ever renders half a case |
| `runFooter(caseDoc)` | the footer that stamps every screen with pipeline version, run time and status. There is no longer a `provenanceBadges` — the standing label strip it built was removed, and each data condition is now stated on the card that uses it |
| `skeleton` | the loading placeholder |

### The six screens

Each exports exactly two things: a `LEDE` string and `render(ctx)`.

| File | Lines | Screen | Its own helpers |
|---|---|---|---|
| `screens/command.js` | 421 | Overview | hero, four answer cards, locator, shortlist, folded acquisition/provenance/processing, limits |
| `screens/satellite.js` | 551 | Imagery | `viewer`, `controls`, `tilesCard`, `detectionCard`, `lookalikeCard`, folded `analystCard`, `georeferenceCard` |
| `screens/slick.js` | 1098 | Slick | `boundaryCard`, `regionsCard`, `regionDetailCard`, `screeningCard`, `verdictBadge`, folded `methodCard`/`qualityCard`. Also exports `ringAreaKm2` and `simplifyRing` for the boundary editor |
| `screens/drift.js` | 865 | Drift | `mapCard` (with the scrubber), `originCard`, `spillAgeCard`, `forecastCard`, `forcingNotice`, folded `spreadCard`/`outcomesCard`/`forcingCard`/`numericsCard`, `caveatCard` |
| `screens/vessels.js` | 807 | Vessels | `filterCard` (the funnel, and where the mandated AIS label is stated), `verdictCard`, `mapCard`, `rankingCard`, `evidenceCard`, folded `weightsCard`/`explanationCard`/`trackQualityCard`/`generatorCard`/`feedCard` |
| `screens/methodology.js` | 1367 | Method | `pipelineCard`, `honestyCard`, `baselineCard`, `perSceneCard`, `lookAlikeCard`, `limitationsCard`, folded `lookAlikeDetailCard`/`scaleCard`/`thresholdCard`/`protocolCard`/`trainingCard`/`samplesCard`, plus `datasetCard`/`reproduceCard` |

### The styles

| File | Lines | What it holds |
|---|---|---|
| `styles/tokens.css` | 186 | the design tokens. **A light, quiet instrument: soft grey canvas, white cards, large radius, diffuse shadow, nothing saturated in the chrome. Everything saturated on the screen is data** |
| `styles/base.css` | 531 | reset, typography, the app frame |
| `styles/components.css` | 1913 | cards, stats, badges, buttons, tables, tabs, the sheet, the load states |
| `styles/screens.css` | 646 | screen-specific layout. The three imagery surfaces — map, viewer, thumbnail — are **near-black inside an otherwise light interface**, because a greyscale SAR tile has no colour to separate it from the page |

---

## 8.33 `tests/` — 776 tests

`.venv/bin/python -m pytest` → **776 passed in ~42 s**. Nothing skipped.

| File | Lines | What it guards |
|---|---|---|
| `test_api.py` | 1024 | every route, every error path, the path-traversal guard |
| `test_incident_features.py` | 1033 | the PDF, **and the accusatory-language ban** |
| `test_scoring.py` | 696 | the six components and the funnel |
| `test_ais.py` | 557 | the synthetic generator's determinism |
| `test_lookalike.py` | 546 | the screen, its features, its folds |
| `test_forcing_products.py` | 510 | **the overlap refusal** — that CMEMS/ERA5 are declined when they do not cover the scene |
| `test_drift.py` | 498 | the integrator, the beaching, the envelope |
| `test_preview.py` | 499 | the hand-written PNG encoder |
| `test_marinecadastre.py` | 433 | the real schema, its sentinels, its coded values |
| `test_geotiff.py` | 416 | the hand-written TIFF reader, both compressions |
| `test_baseline.py` | 394 | the classical detector |
| `test_dataset.py` | 389 | **the split grouping** — the leakage regression test |
| `test_metrics.py` | 380 | the losses and the scores |
| `test_dartis.py` | 337 | the archive reader |
| `test_nn_gradients.py` | 297 | **every backward pass against a finite-difference gradient** |
| `test_age.py` | 270 | the resolvability test |
| `test_regions.py` | 232 | the offline region labels |
| `test_geometry.py` | 220 | spherical area, rings, shape statistics |

## 8.34 The whole call graph, in one picture

```
scripts/run_api.py
  └ spilltrace_api.server.run()
      ├ jobs.JobRunner            ─ background worker
      ├ store.CaseStore       ─ data/processed/cases/
      ├ reports / dispatch        ─ PDF and email
      └ case.build_case()  ◀── the ten stages
      ├ 1 dataset.load_scene ─ geotiff.read_geotiff, dimap.read_header
        ├ 2 case.detect        ─ case.infer_probability ─ model.UNet ─ nn.*
    ├ 3 geometry.analyse   ─ geometry.row_pixel_area_m2, _shape_statistics
  ├ 4 case.screen_scene  ─ lookalike.propose, region_features, verdict
 ├ 5 forcing.resolve_forcing ─ cmems.open_default │ era5.open_default
          │       │ SyntheticForcing (labelled)
      ├ 6 engine.simulate(backward) ─ engine._step (RK2 + random walk)
        ├ 7 engine.simulate(forward)  ─ same function, positive dt
   ├ 8 ais.generate_ais   ─ ais.build_plans, realise_track, marinecadastre.to_row
      ├ 9 scoring.rank_vessels ─ closest_approach, six score_* fns, traffic_funnel
          ├10 preview.render_scene_previews ─ preview.write_png
    └   assemble()          ─ + age.estimate(backward)
       ↓
  the case document (JSON)
        ↓
   HTTP /api/cases/{id}   or   apps/web/demo/case-demo.json (offline)
  ↓
        apps/web/app/main.js ─ router → context → screens/*.render(ctx)
       └ ui.js / layers.js / mapview.js
```

Everything above `config.py` reads its paths, seeds and labels from `config.py`. Nothing else
hard-codes them.

## 8.35 If you change X, re-run Y

| You changed | Re-run | Because |
|---|---|---|
| anything in `services/` or `scripts/` | `pytest` | 776 tests, 42 seconds, no excuse |
| the dataset, or `Oil/`/`Mask_oil/` contents | `run_audit.py` → `run_preprocess.py` → `run_train.py` → `run_scene_eval.py` | every downstream artefact derives from the audit |
| `PreprocessConfig` (patch size, `max_scenes`) | the full chain above | the cache and the split both change |
| the model or `TrainConfig` | `run_train.py` → `run_scene_eval.py` → rebuild the demo | `metrics.json` and `scene_metrics.json` both move |
| `lookalike.py` or the DARTIS set | `run_lookalike_eval.py` | `lookalike_metrics.json` |
| any pipeline stage | `run_api.py --build-demo` | the stored case and its timings |
| anything in `apps/web/` | `build_web.py` | it verifies the JS, the imports, the CSS classes and the bundle |
| a number in a doc | check it against `apps/web/demo/case-demo.json` | prose goes stale; the case document does not |

**And one browser trap.** After `build_web.py`, `location.reload()` is not enough — the browser holds
the old modules. Hard-reload, or in the console:

```javascript
for (const e of performance.getEntriesByType("resource")) if (/\.(js|css|json)$/.test(e.name)) await fetch(e.name, {cache: "reload"}); location.reload();
```

## 8.36 What is genuinely not done

Said plainly, because a weakness a judge finds that you concealed discounts everything else:

1. **No Indian-water validation.** 0 of the 1,200 dataset scenes fall in 65–95 °E, 5–25 °N. One
   Sentinel-1 scene over the Gulf of Kutch closes it, and [RUNBOOK.md](../../RUNBOOK.md) has the SNAP
   processing chain.
2. **The AIS is synthetic.** Real schema, fabricated content.
3. **The forcing on the demo scene is synthetic in pattern** — the magnitude is measured from CMEMS
   over that footprint, but no CMEMS or ERA5 product in the repository covers 3–5 August 2015 in the
   Central Mediterranean. RUNBOOK §6 has the ERA5 download recipe for exactly that window.
4. **Accuracy is a held-out test-set score, not a field-validated detection rate.** The split is
   clean; the sea is not a test set.
5. **The look-alike screen does not name the phenomenon.** Oil-like versus not-oil-like, and nothing
   more.

Every one of these is printed inside the product. That is the design decision, and it is the one to
defend hardest.
