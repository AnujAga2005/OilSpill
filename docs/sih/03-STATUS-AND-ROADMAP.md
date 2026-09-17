# 3 — What we have built, and what is left

Every number in this document was read out of the project's own output files. Nothing here is
estimated or rounded up. If a figure appears on stage it should come from this page.

> **In plain words:** this is the scoreboard. What's finished, what isn't, and the real number for
> each. If someone on stage asks "what is your accuracy?" or "how many scenes?", the answer is in
> here and nowhere else — do not quote a figure from memory. §3.8 is the honest list of what's
> still missing.

---

## 3.1 One paragraph summary

SpillTrace is a working ten-stage pipeline with a seven-screen analyst dashboard. It takes a real
Sentinel-1 SAR scene, segments the oil with a U-Net trained on this repository's 1,200 real
image/mask pairs, measures the slick on a sphere, runs a Lagrangian particle simulation backwards
to a probability envelope and forwards to a forecast, generates a clearly-labelled synthetic AIS
fleet for that envelope, and ranks vessels on a transparent 100-point scale with every component
and its evidence exposed. It runs offline on one laptop with two pipeline dependencies and no
frontend dependencies at all. 893 automated tests pass.

**Requirements (a), (b) and (c) of the problem statement are substantively satisfied**, clause by
clause, in §3.7. One clause is not: the PS mentions EO (optical) imagery alongside SAR and we do
SAR only. Two things are true but stand in for something better — the AIS and the ocean forcing
are synthetic, both labelled as such on every screen that uses them, and both permitted by the
statement. The split-leakage defect that made every metric an upper bound has been fixed and the
pipeline re-run; the figures below are the clean ones, and they are lower than the ones that
preceded them. See [`KNOWN-ISSUES.md`](../../KNOWN-ISSUES.md) §1 for what changed and why.

---

## 3.2 The ten stages

These are the literal stage names the pipeline reports as it runs:

| # | Stage | What happens |
|---|---|---|
| 1 | `decode` | read the GeoTIFF, extract both polarisation bands and the geotransform |
| 2 | `detect` | U-Net inference → per-pixel oil probability → thresholded mask |
| 3 | `geometry` | connected components, contour tracing, spherical area/perimeter/orientation |
| 4 | `screening` | test every dark patch against the look-alike screen; keep, reject or mark uncertain |
| 5 | `forcing` | build the current + wind field for this place and time |
| 6 | `backward` | **hindcast** — particles integrated backwards to an origin envelope and release window |
| 7 | `forward` | **forecast** — particles integrated forwards to a drift prediction |
| 8 | `ais` | synthesise the vessel traffic that passed through the envelope during the window |
| 9 | `scoring` | score and rank every vessel on the 100-point scale |
| 10 | `previews` | render the PNG layers the dashboard displays |

A full run on scene `00223` takes **about 20 seconds** end to end — 9.7 s of that is decoding the
2048 × 2048 GeoTIFF, 5.7 s is inference, 1.9 s is geometry and 1.8 s is the look-alike screen; the
six stages after `screening` cost about 1 s
between them. The run is **deterministic** — run it twice and every figure is byte-identical,
because every random process is seeded. Only the timing block and the generation timestamp
change. Those figures are read off the `timing` block of `data/processed/cases/demo.json`, so
they are the timing of the very run that produced the demo numbers in §3.8.

---

## 3.3 Repository map

```
Oil/  Mask_oil/            the supplied dataset — 1,200 GeoTIFF pairs, never modified
data/processed/            audit, patch cache, metrics, rendered cases
data/uploads/              operator-supplied files — gitignored, outside the audited dataset
models/                    unet_vv_vh.npz — the trained weights
services/
  common/spilltrace_common/   config, GeoTIFF reader, NetCDF reader, seeds
  ml/spilltrace_ml/           U-Net, training loop, classical baseline,
                              geometry.py (morphology, contours, spherical area),
                              preview.py (hand-written PNG encoder)
  drift/spilltrace_drift/     forcing.py, engine.py (particles), ais.py, scoring.py,
                              age.py (the spill-age bound), marinecadastre.py (the AIS schema),
                              realais.py (a real extract → the feed the scorer reads)
  api/spilltrace_api/         server.py, jobs.py, case.py, store.py, uploads.py
apps/web/                  the dashboard — 7 screens, vanilla ES modules, zero dependencies
scripts/                   run_audit, run_preprocess, run_train, run_scene_eval,
                           run_api, build_web
tests/                     893 tests
dist/                      the offline static bundle
docs/sih/                  these fourteen documents
RUNBOOK.md                 how to run everything
KNOWN-ISSUES.md            open defects, honestly stated — read before quoting a number
DATA_AUDIT.md              the generated dataset audit
```

**Dependency count: two, for the pipeline.** `numpy` and `opencv-python`. Two more are peripheral —
`reportlab` (plus the Pillow it drags in) is imported *inside* the single function that renders the
PDF incident report, so without it you lose that one endpoint and nothing else, and `pytest` is
tests only. Everything else — HTTP server,
PNG encoder, NetCDF reader, GeoTIFF reader, CSV and GeoJSON writers, morphology, connected
components, contour tracing, spherical geometry — is standard library or written here. **The
frontend has zero dependencies:** no React, no bundler, no `npm install`. Python 3.11.15.

This is worth mentioning on stage. "Clone it and it runs" is a real advantage over a submission
that needs a 400 MB `node_modules` and a CUDA install.

---

## 3.4 What is real and what is a stand-in

The most important table in these documents. Learn it. Volunteer it before you are asked.

| Component | Status |
|---|---|
| Sentinel-1 SAR imagery | **REAL** — 1,200 supplied pairs, unmodified |
| Ground-truth oil masks | **REAL** — the supplied reference labels |
| Segmentation model | **REAL** — trained on this data, on this machine |
| Every accuracy metric | **REAL** — measured on held-out scenes |
| Classical baseline comparison | **REAL** — implemented and measured, not cited |
| Slick geometry: area, perimeter, orientation, region count | **REAL** — computed on a sphere from the mask |
| Drift simulation engine | **REAL** — Lagrangian particle physics with windage |
| Ocean currents driving it | **SYNTHETIC** — deterministic, seeded, labelled on every screen |
| Wind driving it | **SYNTHETIC** — 3–7 m/s, 3% windage, labelled |
| AIS vessel tracks | **SYNTHETIC** — explicitly permitted by the PS, labelled everywhere |
| Vessel ranking | **REAL arithmetic over synthetic tracks** — a method demonstration |

**Why the forcing is synthetic:** the repository does contain a CMEMS NetCDF file, and we do have
a working reader for it. Its time coverage does not overlap the acquisition dates of the imagery.
Rather than silently interpolating across a multi-year gap and calling the result real, the
pipeline falls back to a deterministic synthetic field and says so. **This is a data-coverage
problem, not a missing feature** — see §3.10 Tier 1 item 5, which fixes it with a download.

**Why the AIS is synthetic:** the problem statement permits it in writing. See document 1 §1.3.

Safeguards built into the synthetic AIS so it can never be mistaken for real:
- MMSI numbers all begin `999`, which is outside the ITU country-code range — no real vessel can
  hold one.
- Vessel names are NATO phonetic words (`SYNTHETIC DEMO ALPHA`, `BRAVO`, …).
- Every record carries `"synthetic": true`.
- Every screen showing vessel data displays `AIS mode: Synthetic demonstration data`.

---

## 3.5 The dataset, audited

Generated by `scripts/run_audit.py` into `DATA_AUDIT.md` and `data/processed/audit.json`.

| Property | Value |
|---|---|
| Image files | **1,200** |
| Mask files | **1,200** |
| Successfully paired | **1,200** |
| Unpaired | **0** |
| Distinct parent acquisitions | **270** |
| Raster size | 2048 × 2048, every file |
| Bands | 2 — `Sigma0_VH_db`, `Sigma0_VV_db` |
| Data type | `>f4` (big-endian 32-bit float), LZW compressed |
| Processing chain | `Orb, NR, Cal, Spk, TC, dB` |
| Missions | Sentinel-1A: 1,076 · Sentinel-1B: 124 |
| Mode | IW (Interferometric Wide) for all |
| Time span | 2015-03-12T05:12:51Z → 2019-10-30T00:25:55Z |

**1,200 files but only 270 acquisitions.** Multiple crops come from the same parent satellite
pass. This matters enormously: if crops from one acquisition landed in both train and test, the
model would be tested on water it had already seen and every metric would be inflated.

> ### ✅ This defect is fixed and the artefacts have been regenerated.
>
> The splitter groups by parent acquisition and was always correct. The code that fed it was not:
> it read the audit's group key under a field name the audit never writes, so every key arrived
> empty and the split silently fell back to grouping **by crop**. The old artefacts contained
> **240 groups from 240 crops — 70 real acquisitions**, with **34 of the 36 test scenes sharing a
> parent acquisition with a training scene**.
>
> The wiring is fixed in `services/ml/spilltrace_ml/cache.py`, with a regression test in
> `tests/test_dataset.py::TestAuditGroupKeysReachTheSplitter`, and **every number below was
> produced by the re-run.** The split now contains 240 scenes from 240 distinct acquisitions and
> **no acquisition appears in more than one split** — see [KNOWN-ISSUES.md](../../KNOWN-ISSUES.md)
> §1 for the verification command and its output.
>
> **What it cost:** the advantage over the classical baseline halved, from +0.189 to **+0.093 IoU**,
> because the leak had been flattering the baseline rather than the model. Pooled scene IoU fell
> from 0.782 to **0.584**.
>
> You may now say the splits are grouped by parent acquisition. Do not present it as though it were
> never otherwise: *"they weren't at first — a wiring bug meant the grouping never reached the
> splitter, we found it, fixed it, wrote the regression test, re-ran everything, and our margin over
> the baseline halved."* Volunteering that is a far stronger answer than being caught by it.

The split table below reports what the artefacts contain. Every acquisition now contributes exactly
one crop, so the "Acquisitions" column is a true acquisition count.

| Split | Acquisitions | Patches available | Positives / negatives | Used in training |
|---|---|---|---|---|
| Train | 169 | 3,710 | 1,855 / 1,855 | 1,800 |
| Validation | 36 | 826 | 413 / 413 | 400 |
| Test | 35 | 794 | 397 / 397 | 600 |

Grouping by acquisition is the right design and it is worth explaining on stage — and it is now a
guarantee we actually meet, not just an intention.

---

## 3.6 The model and the numbers

### Architecture

| Property | Value |
|---|---|
| Architecture | U-Net, encoder–decoder with skip connections |
| Framework | **NumPy, hand-written forward and backward pass** |
| Input channels | 2 (VV, VH) |
| Depth | 4 |
| Encoder widths | 16 → 32 → 64 → 128 |
| Bottleneck | 256 |
| Normalisation / activation | batch norm + ReLU |
| Upsampling | nearest-neighbour + concatenated skips |
| **Parameters** | **1,963,953** |
| Random seed | 1337 |
| Training time | **1,068 s ≈ 18 minutes**, CPU only |

The hand-written backprop was originally forced by a blocked package registry, but it is worth
owning as a choice: nothing in the model is a black box we imported, and the entire thing trains
in 18 minutes on a laptop CPU with no GPU. That is a genuinely unusual thing for a student team
to be able to say. Its limitation is real too — see Tier 2.

### Patch-scale results (128 × 128 patches, threshold 0.65, held-out test split)

Measured on the regenerated, acquisition-grouped split. See KNOWN-ISSUES.md §1 for what these
numbers were before the split was fixed and why the difference matters.

| Metric | Value |
|---|---|
| **IoU** | **0.769** |
| Dice | 0.869 |
| Precision | 0.834 |
| Recall | 0.907 |
| Accuracy | 0.930 |

### The classical baseline — our most valuable single number

We implemented a non-AI detector (`darkspot-vv-v1`): despeckle the VV channel, threshold the
dark pixels, clean up with morphology, drop regions below a minimum area. This is roughly what
the field did before deep learning.

| Detector | Test IoU |
|---|---|
| Classical dark-spot threshold | **0.676** |
| Our U-Net | **0.769** |
| **Improvement** | **+0.093 IoU** |

**This is the number that proves the machine learning earns its place.** Most teams present a
model score with nothing to compare it against, which means the score is unfalsifiable. Ours is
measured against a baseline we built and can show. Lead with it.

**Quote +0.093, never +0.189.** The old figure came from the leaky split, and it was the
*baseline* that the leak was flattering, not the model: the baseline's decibel offset is calibrated
on the training scenes, so scoring it on overlapping test scenes handed it 0.09 IoU it had not
earned. Fixing the split cost the U-Net 0.002 and the baseline 0.094. If someone finds the old
number in an earlier document, the honest answer is that we re-ran it and the gain halved.

### Whole-scene results — the honest number

Patch metrics flatter every model (document 2 §2.4 explains why). So we also evaluate full
2048 × 2048 scenes, and we sweep the threshold on **validation** before applying it unchanged to
**test**.

- Patch-scale threshold: **0.65**
- Whole-scene threshold: **0.7**, selected on the 36 validation scenes by mean per-scene IoU
- Test scenes evaluated: **35**

| Metric at scene threshold 0.7 | Value |
|---|---|
| Pooled IoU (all pixels of all scenes together) | **0.584** |
| Dice | 0.737 |
| Precision | 0.628 |
| Recall | 0.893 |
| **Mean per-scene IoU** | **0.693** |
| Median per-scene IoU | 0.765 |
| **Worst single scene IoU** | **0.046** |

**Pooled 0.58 versus mean-per-scene 0.69 — know the difference, and know it changed.** Pooled
throws every pixel of every scene into one bucket, so the largest scenes dominate. Mean-per-scene
scores each scene and averages, so a small hard scene counts the same as a big easy one. On the
old leaky split pooled was the *higher* number and the mean was the harsher one; on the clean split
that has reversed, because the scenes the model fails on are large. **Do not repeat the old line
that "the mean is the harsher figure"** — which one is harsher depends on the split, and here it is
pooled. Quote both, and if pressed quote **0.584 pooled** as the conservative figure.

Seven of the 35 test scenes score below 0.5, and the distribution has a genuine tail: `00014`
scores **0.046** and `00629` **0.167**, while `00734` scores 0.962 and `00100` 0.956. The median of
0.765 is a fairer summary of the typical scene than either aggregate.

For comparison, at threshold 0.65 (the patch threshold) whole-scene performance is pooled 0.565,
mean per scene 0.692, worst scene 0.041; at 0.9 pooled rises to 0.645 but the mean falls to 0.643.
0.7 is the point that maximises mean per-scene IoU **on validation**, which is the criterion we
committed to before looking at test. All of it is in `scene_metrics.json` and the per-scene
distribution is drawn on the Method screen. Do not hide it — a judge who finds a weakness you
concealed will discount everything else you said.

### Stated limitations — these are in the shipped output, not just this document

1. Every supplied scene contains labelled oil, so these figures measure *delineation quality on
   scenes already known to contain a slick*. They are **not a false-alarm rate on clean sea.**
2. The dataset contains **no labelled look-alikes** (algal blooms, low-wind zones, rain cells,
   ship wakes). The model's ability to reject them is therefore **not learned from this dataset** —
   but it is no longer unmeasured. A separate seven-feature screen was fitted and then scored
   against 2 290 published look-alike patches it never trained on: **AUC 0.9573** held out
   in-domain, **69.4 %** of 84 758 cross-domain dark regions rejected, and — the number that
   matters — the U-Net **alone** alarms on **100 %** of those patches under one radiometric mapping
   and **84.7 %** under the other. See KNOWN-ISSUES.md §4 and `lookalike_metrics.json`.
3. Patch metrics do not include errors that only appear at scene scale — hence the scene
   evaluation.
4. The reference masks are the supplied labels; their own accuracy is unknown and is treated as
   ground truth throughout.

---

## 3.7 Requirement-by-requirement compliance

Checked against the code, clause by clause.

| PS clause | Status | Where |
|---|---|---|
| (a) Detect the oil spill | ✅ | U-Net, 0.769 IoU vs 0.676 classical |
| (a) Characterise / geometric properties | ✅ | area, perimeter, centroid, orientation, per-region breakdown, spherical |
| (a) **…and age if feasible** | ✅ | `spillAge` on every case — up to 24 h at acquisition, with the separation test showing why one acquisition cannot tighten it |
| (b) **Oceanographic** data → origin | ✅ | current field drives the particles (synthetic field) |
| (b) **Meteorological** data | ✅ | wind at 3–7 m/s, 3% windage, `forcing.py:470` |
| (b) Origin **point and time** | ✅ | probability envelope + release window |
| (b) Predict future flow | ✅ | forward drift |
| (c) Reconstruct traffic in the origin window | ✅ | tracks in space and time |
| (c) **Irrelevant traffic filtered out** | ✅ | `attribution.filtering` publishes the funnel — 10 vessels → 9 in window → 2 relevant, 8 excluded under two distinct reasons; excluded rows dimmed, not deleted, so the filter can be audited |
| (c) Score on proximity / trajectory / behavioural anomalies | ✅ | six components, below |
| Automated pipeline | ✅ | ten stages, job queue, 20 s |
| **Hindcasting** ML model | ✅ | backward drift |
| Backward **and** forward mapping | ✅ | both |
| Ranks candidates by spatio-temporal correlation | ✅ | 100-point scale |
| Suitable visual interface | ✅ | 7 screens, desktop + mobile + print |
| PS mentions SAR **and EO** imagery | ❌ | SAR only |

### The scoring scale, since (c) is the most prescriptive clause

| Component | Max | What it measures |
|---|---|---|
| **Distance** | **30** | great-circle distance to the envelope centre at the vessel's own timestamp. Full marks inside 1 envelope radius, zero beyond 3 |
| **Time window** | **25** | half for being within reach of the estimated oil position during the release window, half scaling with up to 6 h of such coverage |
| **Trajectory** | **20** | two thirds for intersecting the envelope, one third for a course across the drift axis; both gated on proximity |
| **Behaviour** | **10** | full marks below the stopped-speed threshold near closest approach, partial for a relative slow-down |
| **Vessel type** | **10** | category relevance to bulk oil carriage — oil tanker 1.0, chemical tanker 0.9, offshore supply 0.7, bulk carrier 0.6, container/general cargo 0.5, fishing 0.3, passenger ferry 0.2 |
| **Data completeness** | **5** | 60% report completeness, 40% field completeness, penalised for rejected rows |
| **Total** | **100** | |

Map that straight onto the PS wording: *proximity* → Distance, *trajectory* → Trajectory,
*behavioural anomalies* → Behaviour, *etc.* → Time window, Vessel type, Data completeness. The
weights are fixed in advance and are not tuned to produce a flattering answer.

Note the vessel-type rationale is about **capability, not character** — an oil tanker *can*
discharge persistent oil in bulk; a passenger ferry realistically cannot. It carries no
implication about any individual vessel, and the reasoning string is shown on screen.

---

## 3.8 The dashboard

Seven screens, in the order an analyst would use them.

| Screen | File | What it shows |
|---|---|---|
| **New analysis** | `analysis.js` | the intake screen, and the app's front door with a live API: one dark panel holding the five-slot upload form, the four fields and the run button, then what the run does, then the stored cases with a **Load previous saved cases** button. Computes nothing itself |
| **Command centre** | `command.js` | headline area, then four numbered answers (extent, release window, age, vessels worth a look) each linking to its evidence; map, shortlist, and a folded audit trail of acquisition / provenance / stage timings |
| **Imagery** | `satellite.js` | VV/VH switch, prediction vs reference vs agreement overlays, before/after split slider, all layers |
| **Slick** | `slick.js` | measured extent, per-region table, **analyst boundary editing**, GeoJSON/CSV export |
| **Drift** | `drift.js` | animated particle timeline, backward/forward toggle |
| **Vessels** | `vessels.js` | ranked candidates, per-component score breakdown with evidence |
| **Method** | `methodology.js` | full provenance: architecture, patch vs scene metrics, threshold sweep, per-scene distribution, stated limitations |

Also built: responsive down to phone width, a print stylesheet, JSON/GeoJSON/CSV export, a
case picker, and an offline static bundle (`dist/`, 45 files, 3.2 MB) that replays the demo with
no Python running at all — for handing to someone with no environment.

`build_web.py` asserts on every run that `dist/` contains no raw datasets, no absolute host
paths and no credentials.

### The demo case — the numbers that will be on screen

Two stored cases share the same scene and the same slick figures: `00223` and `demo`.

| Field | Value |
|---|---|
| Scene | `00223`, Central Mediterranean *(approximate offline label, not a gazetteer lookup)* |
| Acquired | 2015-08-04T16:55:41Z |
| Mission / mode | Sentinel-1A, IW |
| Split | **test** — held out; the model never trained on this acquisition |
| **Total detected slick area** | **26.90 km²** |
| Largest single region | **15.76 km²** |
| Separate regions found | **12** |
| Mean model confidence | **94.65%** — *on the `demo` case only, and shown on **Imagery**, not on the Command centre* |
| Touches scene edge | **no** — the slick is fully inside the footprint, so the area is a complete measurement of what the model found |
| Release window | **24.0 h** — 2015-08-03T16:55:41Z to 2015-08-04T16:55:41Z |
| **Estimated spill age** | **up to 24 h at acquisition** (0–24 h), **not resolvable** — 6.563 km of drift against a 9.604 km P90 radius, a ratio of 0.68 |
| Origin estimate | **14.5167 °E, 35.8916 °N** · P50 4.21 km · P90 9.60 km |
| Particles | 300 released · backward: 205 still drifting, **95 beached**, 0 left the domain · forward: 279, **21 beached**, 0 |
| AIS reports · vessels | **1,112 reports · 10 vessels** |
| **Traffic filter** | 10 → **9** with reports in the window → **2** relevant · **8** excluded (1 never in the window, 7 in the window but too far) |
| Candidates ranked | **10** |
| Top candidate | **90.3 / 100** — `SYNTHETIC DEMO ALPHA`, band *"Strong geometric and temporal overlap – review first"* |

> **Present the case the picker labels `00223 · demo`.** That is scene `00223`, and every figure in
> this section is measured on it. The old `00053` case — generated on 2026-09-04, before the
> retrain, by a superseded checkpoint, on a scene that is not in the current train/val/test split at
> all, whose 223.19 km² headline could not be defended — has been deleted from the store, so it can
> no longer be clicked by accident. Anything else in the picker was built by
> `scripts/build_cases.py`, which refuses non-test scenes, so it is safe to open if a judge asks.
>
> Confidence is deliberately **not** on the Command centre. A mean probability over the pixels the
> model already decided were oil is close to 100% by construction — it measures how decisive the
> model was, not how right it was. It belongs next to the agreement overlay on Imagery, where the
> reference mask is on screen to contradict it, and not in the first figure a judge reads.

**Note the two area figures.** The Command centre headline is the **total** across all 12 regions,
26.90 km²; the first numbered answer card is the **largest single connected region**, 15.76 km². That
card also reports whether the slick touches the scene edge and changes its own wording accordingly —
here it reads *"fully inside the scene footprint"*, so 26.90 km² is a complete measurement rather
than a floor. Do not claim an edge effect unless the card says there is one.

### The four mandated labels, verbatim

These strings appear in the shipped output. Know them; they are your honesty policy in code. They
are no longer repeated in a strip on every screen — each is stated once, where it applies, and all
four are in the case JSON and the PDF report regardless of what is on screen.

- `AIS mode: Synthetic demonstration data` — Vessels, on the *Traffic filtering* card, under the
  reports-ingested figure
- `Drift forcing: Synthetic scenario data` — Drift, the *Forcing* fold (its summary line, readable
  without opening it)
- `Status: Research PoC - human review required` — the footer of every screen
- `Priority candidate for investigation` — the strongest phrase anywhere in the product; on the
  Vessels verdict card and every ranking row

Plus the ranking caveat, in full:

> *"This ranking is a triage aid computed from synthetic AIS and synthetic drift forcing. It is
> not evidence, it does not establish responsibility, and every entry requires human verification
> against licensed AIS and observed metocean data."*

---

## 3.9 Tests and reproducibility

```bash
.venv/bin/python -m pytest
```

**893 tests, about 28 seconds, all passing, nothing skipped.**

Everything is seeded and reproducible:

| Seed | Value | Governs |
|---|---|---|
| Model init | 1337 | training |
| Synthetic forcing | **26143** | the current and wind field |
| Synthetic AIS | **4726143** | the vessel fleet |

The forcing seed is the problem statement number. A small thing, but if a judge notices it, it
reads as a team that was paying attention.

---

## 3.10 What is left — the roadmap

Ordered by marks gained per hour spent. **Tier 0 is done**, and **Tier 1 items 8 and 9 are done**
— both are kept below as a record of what was delivered and how to point at it. Items 5, 6 and 7
are still open, and item 5 is now the single largest credibility gain available.

### Tier 0 — done, and each one quotes the PS back at them ✓

These four were the cheapest marks on the board and they are all shipped. They are listed here
rather than deleted because each one is a thing to *point at* in the demo, and because the next
section is easier to prioritise when you can see what the tier above it cost.

**1. Conform to the MarineCadastre AIS schema.** ✓ The PS names `marinecadastre.gov/accessais`
as the format authority, so `services/drift/spilltrace_drift/marinecadastre.py` now holds the
17-column header, a `FieldSpec` per column with its documented domain, a parser for a real
extract, and the writer the app exports through. Download it live at
**`/api/cases/demo/ais.csv`**. The header is byte-for-byte identical to a real daily extract —
verified against `data/raw/AIS_2022_06_01.csv` (924 MB, 1 June 2022), which is the check to
run on stage if anyone doubts it:

```bash
.venv/bin/python -c "from spilltrace_drift import marinecadastre as MC; print(open('data/raw/AIS_2022_06_01.csv').readline().strip() == MC.HEADER_LINE)"
```

Because the importer exists, switching to a live feed is a file drop rather than a rewrite — and it
is now literally a file drop: the New analysis screen takes a real MarineCadastre extract,
and `services/drift/spilltrace_drift/realais.py` turns it into the feed the scorer consumes. That
module exists because a parsed CSV is *not* the same shape as the synthetic feed, and the gap is not
cosmetic: `score_completeness` defaults a missing cleaning block to full marks, so a gappy real feed
would score **perfectly on data quality** — the one component whose whole job is to say the data is
poor. The completeness figure is therefore measured against each vessel's own median reporting
interval, not a constant borrowed from the generator. Covered by `tests/test_marinecadastre.py` and
7 route tests in `tests/test_api.py`.

**2. An explicit traffic-filtering funnel.** ✓ The rule existed in `IRRELEVANT_RADII`; what was
missing was output. `scoring.py` now publishes `attribution.filtering` — counts on both sides of
the filter, the two exclusion reasons kept apart, and the rule in prose. For the demo case:

```
1112 AIS reports · 10 vessels
  → 9 vessels with reports inside the release window
  → 2 intersect the origin envelope (within 3 envelope radii)
  → 8 excluded as irrelevant traffic (1 never in the window, 7 in the window but too far)
```

Every candidate carries `relevant` and `relevanceReason`, both of which travel into the CSV
export, and **relevance is the primary sort key** so an excluded vessel cannot outrank a relevant
one on type and data-quality marks alone. Excluded vessels are **dimmed, not deleted** — a
shortlist that silently drops eight of ten cannot be audited. Covered by 9 tests in
`tests/test_scoring.py`, including the additive identity and the sort order under weights that
would otherwise invert it.

**3. Spill age, surfaced and bounded.** ✓ `services/drift/spilltrace_drift/age.py` publishes
`spillAge` on every case: **up to 24 h at acquisition (0–24 h)**, its basis, and — the part that
earns the mark — whether the hindcast can narrow it. For this scene it cannot, and the card says
so with the arithmetic: over 24 h the estimated position moves **6.563 km against a 9.604 km
P90 radius, a ratio of 0.68**, so the whole release window sits inside its own error bar. **The
midpoint is deliberately not printed** — "12 h" would be a fabricated metric. Three data sources
that would genuinely narrow it are listed instead.

Note that this is a *per-case* answer, not a limit of the method: a tightly seeded run does
resolve, and `tests/test_age.py` (18 tests) asserts both sides of that pair so the claim stays
tied to the physics — initial patch size against distance drifted — rather than to one scene.

The weathering proxy from the original plan is **not** done and has moved to Tier 2: thin sheen
and thick emulsion damp the sea differently, so a coarse thin/moderate/thick class is defensible
off statistics we already compute, but it is a real classifier and not a half-day.

**4. The PS-compliance slide.** ✓ Written as
**[document 6](06-PS-COMPLIANCE.md)** — thirteen rows, left column the PS in its own words, right
column the screen that answers it and the number that screen prints. Show five rows on the
projector, hand the full table over as a printout. §6.3 of that document holds the six push-back
questions with the numbers already looked up.

### Tier 1 — before the finals

Items **5, 8 and 9 are done**. They are left in number order rather than moved to a "done"
section, because the other documents cite these numbers. Items 6 and 7 are open.

**5. Real currents and wind for the actual acquisition date.** ✓ *(code done; one optional
download left to the operator.)* This item was written as "**This is a download, not a rewrite**"
and that was wrong — the audit that followed found two defects that made the download useless on
its own, and both are now fixed:

- **There was no wind reader at all.** The CMEMS reader existed; ERA5 did not. Downloading a wind
  file would have changed nothing, because nothing could open it. There is now an ERA5 reader
  handling the things that file actually does: `u10`/`v10`, a time axis called either
  `valid_time` or `time`, 0–360 longitudes, descending latitudes, and an `expver` axis where one
  slice is entirely no-data.
- **The current window ignored the acquisition time.** `resolve_forcing` cut its window from
  timestep 0 while the overlap report quoted the gap to the *nearest* timestep, so a covering
  multi-day product would have been read on the wrong day and said nothing about it.

**Wind and currents are now separate products, and either can be real on its own.** That is not a
technicality: oil moves at ~3 % of the wind, which at this scene's 0.0939 m/s current anchor makes
the wind term the same size as the current. So there are four labels, not two, and the interface
names both halves — `Synthetic scenario data`, `Synthetic currents with ERA5 wind`, `CMEMS data`,
`CMEMS currents with ERA5 wind`. The Forcing card reports the wind's source, its mean speed over
the footprint, how many hourly frames were read, and what fraction of the drift horizon the file
covered; with no wind file it says the spread is a *lower bound*, because real oil also moves with
the wind. Wind is interpolated hourly in time and currents are not, and the card says so rather
than pretending the two products have the same cadence.

What is left is genuinely a download, and it is optional: ERA5 needs a free Copernicus account,
so no wind file ships with the repository. The exact request — variables, the three days, the
padded footprint, NetCDF4 — is in **RUNBOOK.md §6a**, and CMEMS in §6b. Covered by 28 new tests in
`tests/test_forcing_products.py`, which drive both readers through a fake NetCDF handle because
nothing in the repository can *write* NetCDF-4.

**And the download no longer has to be wired in by hand.** The New analysis screen takes an ERA5 or
CMEMS file directly — separate slots, because they are separate products and either can be
real on its own. ERA5 is the one to fetch first: a few MB against CMEMS's 370, and the bigger change
to the drift, since the wind term is the same size as the current at this scene's anchor. Each file
is re-checked against *this* scene's footprint and window on its own terms, so one that does not
overlap is reported with the reason rather than quietly ignored.

**6. Dataset identity — ✓ verified. One Indian scene — still open.** *(~1 day of the two spent.)*

**The identity half is done, and the answer corrected a mistake of ours.** Our data is Part I of
*"Sentinel-1 SAR oil spill image dataset for train, validate, and test deep learning models"* —
Trujillo-Acatitla, Tuxpan-Vargas, Ovando-Vázquez & Monterrubio-Martínez (IPICYT, Mexico), Zenodo,
CC BY 4.0, DOI **10.5281/zenodo.8346860**, concept DOI `10.5281/zenodo.8346859` — the dataset the
problem statement names. It is documented by a peer-reviewed paper: *"Marine oil spill detection and
segmentation in SAR data with two steps Deep Learning framework,"* **Marine Pollution Bulletin 204:
116549** (2024), `doi:10.1016/j.marpolbul.2024.116549`. Matched on eight independent fingerprints:
file count, the `NNNNN.tif` naming, raster dimensions, band naming, dtype, the embedded BEAM-DIMAP
processing chain, mask value encoding, and the acquisition date span. Put the DOI on a slide.

**Stop saying "Persian Gulf data."** We were wrong about our own dataset. It is global: 1,200 scenes
across **24 named seas**, 95°W to 130°E, 8°S to 61°N. Gulf of Mexico 388, Eastern Mediterranean 172,
**Persian Gulf 88 — about 7%**. Our demo case sits in the Central Mediterranean, which is a reminder
that the demo scene is chosen by held-out-split membership, not by region. The region
table in `DATA_AUDIT.md` is generated from each scene's own corner coordinates.

**The real gap is narrower and sharper: no Indian water at all.** Not one of the 1,200 scenes falls
in 65–95°E, 5–25°N. So "have you validated on Indian seas?" gets a straight no, and one scene fixes
that. Free, no account beyond a signup, roughly two hours:

1. Register at **dataspace.copernicus.eu** and open the Browser.
2. Search **Sentinel-1**, product type **GRD**, mode **IW**, polarisation **VV+VH** over one of:
   **Gulf of Kutch** (~68.5–70.5°E, 22.2–23.2°N — dense tanker traffic into Kandla and Vadinar),
   **Mumbai coast** (~72.5–73.2°E, 18.8–19.3°N), or **Ennore/Chennai** (~80.2–80.5°E, 13.1–13.4°N),
   whose January 2017 collision has citable damage figures.
3. Download the `.SAFE` product and run SNAP's standard chain to match ours exactly — the processing
   chain our files carry is `Orb_NR_Cal_Spk_TC_dB`: Apply-Orbit-File → ThermalNoiseRemoval →
   Calibration (σ⁰) → Speckle-Filter → Terrain-Correction (EPSG:4326) → LinearToFromdB. Export
   GeoTIFF with **VH as band 0 and VV as band 1**, the order `audit.py` detects by band name.
4. **Analyse it through the app's upload form** — the **New analysis** screen the app opens on → drop
   the GeoTIFF in the SAR scene slot, keep or overwrite the case id it suggests from the filename,
   type the acquisition instant from the product metadata, and run. No mask is needed: a scene
   without one is a prediction-only case, which is exactly the real
   operational situation. Uploads are stored outside the audited dataset and are never added to it,
   so this costs nothing and changes no measured number. **Only if you want the scene to become a
   permanent dataset member** do you drop it in `Oil/` and re-run `scripts/run_audit.py` — and that
   re-runs the whole chain below it.

Expect it to look *worse* than our test scenes, and say so: no reference mask, a different sea
state, and a possible incidence-angle difference. **A weaker honest number on Indian water beats a
strong number on water nobody asked about.**

**Two companion parts are the cheaper win, and they close stated limitations rather than adding
scenery.** Part III (`10.5281/zenodo.13761290`) is a held-out test split — 150 images + 150 masks
each for look-alike / no-oil / oil, 900 files, 9.86 GB — an external test set immune to the
grouping bug in KNOWN-ISSUES.md §1, so it retires "your numbers are upper bounds" without the
retrain. Part II (`10.5281/zenodo.8253899`) is 685 no-oil + 685 **look-alike** images with masks:
the only route to a detector that *learns* the oil/look-alike distinction instead of being screened
after the fact. **Fetch Part III first** — it is the smaller download and it answers the harder
question.

**7. Look-alike discrimination.** ✓ *(measured; the discrimination itself is partial.)* This was
the hardest genuine question we faced and the one place we had no answer at all. There is now a
number, and it is worth knowing before a judge finds it.

`services/ml/spilltrace_ml/lookalike.py` screens every dark region the detector proposes on seven
features — darkness against the local background, its 10th-percentile tail, a texture ratio, edge
sharpness, compactness, solidity, elongation. All seven are ratios of same-unit quantities, so the
screen survives a change of radiometric scale, which is the only reason it can be scored on a
foreign archive at all. Fitted on 11 623 dark regions from the 270 supplied products (930 over
labelled oil, 10 693 not); regions between 5 % and 50 % mask overlap are dropped rather than
guessed at.

| | Screen | U-Net alone |
|---|---|---|
| Held out, same domain (5-fold, grouped by parent product) | **AUC 0.9573** — keeps 90.2 % of oil, rejects 89.4 % of dark non-oil | — |
| 2 290 published look-alike patches, never trained on | **69.4 %** of 84 758 dark regions rejected; 73.2 % of patches still raise something | **84.7 %** of patches alarm under one radiometric mapping, **100 %** under the other |

Say the right thing about this on stage: **the screen is a real improvement and the problem is not
solved.** The U-Net on its own alarms on essentially every look-alike patch it is shown. Two
detector numbers are reported rather than one because the archive is 8-bit JPEG — it cannot be
turned back into calibrated decibels, so a single figure would overstate what the data supports.
The screen also never names *which* look-alike it thinks it is seeing, because nothing in either
dataset labels the phenomenon; that needs Part II of the source dataset (item 6). Reproduce with
`scripts/run_lookalike_eval.py` after `scripts/fetch_dartis2019.py --subset nc,nw`; full detail
and all seven stated limitations are in KNOWN-ISSUES.md §4.

**The pooled 69.4 % is the least useful way to state this result**, and Screen 6 no longer stops
there. The archive's no-oil patches carry the source paper's K-Means cluster in the filename, so
the look-alikes arrive pre-grouped into **17 families** — 5 coastal, 12 open water. Broken out,
the screen rejects **96.2 %** of the dark regions in the family it handles best (`nw-05`) and
**31.5 %** in the family it handles worst (`nw-11`). Both tables now render in the *Look-alike
screening* card: coastal versus open water, then all 17 families sorted best to worst.

Say it as a range, not an average — it is a stronger claim and it is the true one. And keep the
distinction the card itself makes: the families are the axis we are **measured along**, not
something the screen predicts. A K-Means cluster is a grouping, not a diagnosis; the paper never
says which cluster is an algal bloom and which is a low-wind patch. *"We classify look-alikes into
classes"* is the one sentence about this work that a judge can puncture in a single question.

**8. Close the loop — alerting.** ✓ *(done.)* The theme is Disaster Management and the pipeline
used to end at a screen. `POST /api/cases/<id>/dispatch` now builds the incident PDF and either
sends it over SMTP or writes an `.eml` beside the PDF, as a background job the dashboard polls.
Two properties are worth stating out loud when a judge asks, because they are the difference
between a demo and something you would let near a real inbox:

- **It can be locked down to named recipients.** The endpoint has no authentication in front of
  it, so `SPILLTRACE_ALERT_RECIPIENTS` exists: set it to a list of addresses or `@domains` and
  the server refuses anything outside that list. Left unset — the default — a report goes to
  whatever addresses the responder types, which is what you want on a laptop and not what you
  want on a machine whose port other people can reach.
- **Dry run is the default.** With no SMTP host configured a clone writes a `.eml` file and says
  so, in the dialog *before* you commit and in the confirmation after. The button reads
  "Write .eml", not "Send report", when that is what will happen.

Demonstrate it with `.venv/bin/python scripts/make_report.py --dispatch ops@example.gov`, which
prints the dispatch mode and the path of whatever it produced. SMS and WhatsApp are deliberately
not built: both need a paid account and a registered sender, and neither adds anything the email
does not already prove.

**9. PDF incident report** ✓ *(done.)* Nine sections — case information, incident location, the
detection, the drift hindcast and estimated origin, spill age, vessel triage, provenance, stated
limitations, and an operational disclaimer with a blank sign-off block for a wet signature. The
case number is `ST-<YYYYMMDD>-<scene>-<caseId>` and every page is footed with it plus "Research
proof of concept — not evidence — human review required".

Three things in it are worth pointing at:

- **The score decomposition is a table, not a number.** One row per scoring component with the
  measured reason it scored that, straight out of `vessels[0].componentDetail`.
- **The limitations are printed verbatim** from `case["limits"]`, and the disclaimer states the
  *actual* AIS and drift mode rather than hedging that they "may be" synthetic.
- **Nothing in it accuses anybody.** There is a test that asserts the words "guilty", "culprit",
  "responsible party" and "confirmed responsible" appear nowhere in the rendered document, and
  that "priority candidate for investigation" does.

reportlab is the project's one optional dependency and it hard-requires a working Pillow, so it
is imported lazily: a broken install answers 503 on that one route instead of taking down the
server and the test suite. `.env.example` documents every variable involved.

### Tier 2 — if time allows

- **A weathering proxy to narrow the age** — dropped out of Tier 0 because it is a classifier, not
  a label. Thin fresh sheen and thick weathered emulsion damp the sea differently and so differ in
  backscatter contrast, which makes a coarse thin/moderate/thick class defensible off statistics
  `geometry.py` already computes. It attacks the age from the imagery instead of the drift, which
  is the one route that does not need a second acquisition.
- **PyTorch retrain on a GPU** with a pretrained encoder and proper augmentation. Our
  mean-per-scene 0.693 has real headroom — and pooled 0.584 more so — and the hand-written
  NumPy backprop caps how deep we
  can practically go.
- **EO/optical as a second input** for cloud-free days — the PS mentions EO and we do not use it.
- **Multi-pass time series** — two passes over the same water gives a growth rate. "This slick
  grew 40% in six days" is a far stronger story than one snapshot.
- **Dark-ship detection** — SAR sees the metal hull as a bright target even with AIS switched
  off. This is the answer to "what if they turn the transponder off," and it is buildable.
- **Impact quantification** — convert 26.9 km² into cleanup cost, fishery value at risk, response
  hours saved. That is what "potential impact" scoring means.
- **Docker** so it runs on any judge's laptop.

---

## 3.11 Where MERN fits

**Do not rewrite the Python.** The value is in the model, the drift physics and the geometry.
Rewriting a working frontend in React earns zero marks and risks the demo.

Where MERN is genuinely the right tool, and where our own coding time should go:

- **Express gateway** in front of the Python service — role-based access (watch officer /
  analyst / supervisor), case assignment, alert dispatch.
- **MongoDB** for what Python should not own — users, case history, analyst annotations, and an
  **immutable audit log**: who opened which case, who edited a boundary, who escalated a
  candidate, when.
- **React** only for genuinely new screens, and only if it is faster than working in the
  existing code.

The audit log deserves emphasis. A **technical intelligence organisation** cares intensely about
provenance of *actions*, not only of numbers. Our app already tracks where every figure came
from; extending that to who did what is a real operational capability, it is entirely MERN, and
it is the kind of thing that separates a student demo from something a desk officer could use.

Architecture slide, one line:

> **Node handles people and process. Python handles physics and pixels.**

---

## 3.12 Running it

```bash
.venv/bin/python scripts/run_api.py
```

Then open **http://localhost:8765**. The badge top-right must read **Live API** in green.

Full detail — every route, every rerun script, the offline bundle, troubleshooting — is in
[RUNBOOK.md](../../RUNBOOK.md).

Next: **[document 4 — how to pitch it](04-HOW-TO-PITCH.md)**.
Related: **[document 6 — the PS-compliance slide](06-PS-COMPLIANCE.md)**, which reads the numbers
on this page back against the problem statement clause by clause.
