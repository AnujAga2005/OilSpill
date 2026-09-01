# 3 — What we have built, and what is left

Every number in this document was read out of the project's own output files. Nothing here is
estimated or rounded up. If a figure appears on stage it should come from this page.

---

## 3.1 One paragraph summary

SpillTrace is a working nine-stage pipeline with a six-screen analyst dashboard. It takes a real
Sentinel-1 SAR scene, segments the oil with a U-Net trained on this repository's 1,200 real
image/mask pairs, measures the slick on a sphere, runs a Lagrangian particle simulation backwards
to a probability envelope and forwards to a forecast, generates a clearly-labelled synthetic AIS
fleet for that envelope, and ranks vessels on a transparent 100-point scale with every component
and its evidence exposed. It runs offline on one laptop with three Python dependencies and no
frontend dependencies at all. 532 automated tests pass.

**Requirements (a), (b) and (c) of the problem statement are substantively satisfied**, clause by
clause, in §3.7. One clause is not: the PS mentions EO (optical) imagery alongside SAR and we do
SAR only. Two things are true but stand in for something better — the AIS and the ocean forcing
are synthetic, both labelled as such on every screen that uses them, and both permitted by the
statement. And one number needs a re-run before it can be quoted as final: see
[`KNOWN-ISSUES.md`](../../KNOWN-ISSUES.md) §1.

---

## 3.2 The nine stages

These are the literal stage names the pipeline reports as it runs:

| # | Stage | What happens |
|---|---|---|
| 1 | `decode` | read the GeoTIFF, extract both polarisation bands and the geotransform |
| 2 | `detect` | U-Net inference → per-pixel oil probability → thresholded mask |
| 3 | `geometry` | connected components, contour tracing, spherical area/perimeter/orientation |
| 4 | `forcing` | build the current + wind field for this place and time |
| 5 | `backward` | **hindcast** — particles integrated backwards to an origin envelope and release window |
| 6 | `forward` | **forecast** — particles integrated forwards to a drift prediction |
| 7 | `ais` | synthesise the vessel traffic that passed through the envelope during the window |
| 8 | `scoring` | score and rank every vessel on the 100-point scale |
| 9 | `previews` | render the PNG layers the dashboard displays |

A full run on scene `00053` takes **about 19 seconds** end to end — 9.7 s of that is decoding the
2048 × 2048 GeoTIFF and 5.7 s is inference; the six stages after `geometry` cost about 1 s
between them. The run is **deterministic** — run it twice and every figure is byte-identical,
because every random process is seeded. Only the timing block and the generation timestamp
change. Those figures are read off the `timing` block of `data/processed/cases/demo.json`, so
they are the timing of the very run that produced the demo numbers in §3.8.

---

## 3.3 Repository map

```
Oil/  Mask_oil/            the supplied dataset — 1,200 GeoTIFF pairs, never modified
data/processed/            audit, patch cache, metrics, rendered cases
models/                    unet_vv_vh.npz — the trained weights
services/
  common/spilltrace_common/   config, GeoTIFF reader, NetCDF reader, seeds
  ml/spilltrace_ml/           U-Net, training loop, classical baseline,
                              geometry.py (morphology, contours, spherical area),
                              preview.py (hand-written PNG encoder)
  drift/spilltrace_drift/     forcing.py, engine.py (particles), ais.py, scoring.py,
                              age.py (the spill-age bound), marinecadastre.py (the AIS schema)
  api/spilltrace_api/         server.py, jobs.py, case.py, store.py
apps/web/                  the dashboard — 6 screens, vanilla ES modules, zero dependencies
scripts/                   run_audit, run_preprocess, run_train, run_scene_eval,
                           run_api, build_web
tests/                     532 tests
dist/                      the offline static bundle
docs/sih/                  these six documents
RUNBOOK.md                 how to run everything
KNOWN-ISSUES.md            open defects, honestly stated — read before quoting a number
DATA_AUDIT.md              the generated dataset audit
```

**Dependency count: three.** `numpy`, `opencv-python`, `pytest`. Everything else — HTTP server,
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

> ### ⚠ This is a known open defect. Read it before you quote any metric.
>
> The splitter groups by parent acquisition and is correct. The code that fed it was not: it
> read the audit's group key under a field name the audit never writes, so every key arrived
> empty and the split silently fell back to grouping **by crop**. In the shipped artefacts that
> produced **240 groups from 240 crops — 70 real acquisitions**, and **34 of the 36 test scenes
> share a parent acquisition with a training scene**.
>
> The wiring is fixed in `services/ml/spilltrace_ml/cache.py`, with a regression test in
> `tests/test_dataset.py::TestAuditGroupKeysReachTheSplitter`. **The artefacts and every number
> below were produced before the fix and have not been regenerated.** Treat the metrics as an
> upper bound until you re-run the pipeline — see [KNOWN-ISSUES.md](../../KNOWN-ISSUES.md) for
> the three commands.
>
> Until then, **do not say "no leakage" on stage.** Say what is true: *"splits are grouped by
> parent acquisition — we found and fixed a wiring bug where that grouping wasn't reaching the
> splitter, and these numbers predate the re-run."* Volunteering that is a far stronger answer
> than being caught by it.

The split table below reports what the artefacts contain. The "Acquisitions" column is what the
run *recorded*; it is really a count of crop-level groups.

| Split | Acquisitions (recorded) | Patches available | Positives / negatives | Used in training |
|---|---|---|---|---|
| Train | 167 | 3,804 | 1,902 / 1,902 | 1,800 |
| Validation | 37 | 886 | 443 / 443 | 400 |
| Test | 36 | 840 | 420 / 420 | 600 |

Grouping by acquisition is the right design and it is worth explaining on stage — just explain it
as a design you fixed, not a guarantee you are currently meeting.

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
| Training time | **1,575 s ≈ 26 minutes**, CPU only |

The hand-written backprop was originally forced by a blocked package registry, but it is worth
owning as a choice: nothing in the model is a black box we imported, and the entire thing trains
in 26 minutes on a laptop CPU with no GPU. That is a genuinely unusual thing for a student team
to be able to say. Its limitation is real too — see Tier 2.

### Patch-scale results (128 × 128 patches, threshold 0.6, held-out test split)

| Metric | Value |
|---|---|
| **IoU** | **0.771** |
| Dice | 0.871 |
| Precision | 0.824 |
| Recall | 0.923 |
| Accuracy | 0.931 |

### The classical baseline — our most valuable single number

We implemented a non-AI detector (`darkspot-vv-v1`): despeckle the VV channel, threshold the
dark pixels, clean up with morphology, drop regions below a minimum area. This is roughly what
the field did before deep learning.

| Detector | Test IoU |
|---|---|
| Classical dark-spot threshold | **0.582** |
| Our U-Net | **0.771** |
| **Improvement** | **+0.189 IoU** |

**This is the number that proves the machine learning earns its place.** Most teams present a
model score with nothing to compare it against, which means the score is unfalsifiable. Ours is
measured against a baseline we built and can show. Lead with it.

### Whole-scene results — the honest number

Patch metrics flatter every model (document 2 §2.4 explains why). So we also evaluate full
2048 × 2048 scenes, and we sweep the threshold on **validation** before applying it unchanged to
**test**.

- Patch-scale threshold: **0.6**
- Whole-scene threshold: **0.8**, selected on the validation scenes
- Test scenes evaluated: **36**

| Metric at scene threshold 0.8 | Value |
|---|---|
| Pooled IoU (all pixels of all scenes together) | **0.782** |
| Dice | 0.877 |
| Precision | 0.830 |
| Recall | 0.931 |
| **Mean per-scene IoU** | **0.641** |
| Median per-scene IoU | 0.674 |
| **Worst single scene IoU** | **0.131** |

**Pooled 0.78 versus mean-per-scene 0.64 — know the difference.** Pooled throws every pixel of
every scene into one bucket, so big easy slicks dominate. Mean-per-scene scores each scene and
averages, so a small hard scene counts the same as a big easy one. **The mean is the harsher and
more operationally meaningful figure.** Quote both.

For comparison, at threshold 0.6 (the patch threshold) whole-scene performance is worse — pooled
0.741, mean per scene 0.617, worst scene **0.085**. So the worst-case figure you quote depends on
the threshold: **0.131 at our operating point of 0.8**, 0.085 at 0.6. Use 0.131 unless someone
asks specifically about the patch threshold. Both are real, both are in `scene_metrics.json`, and
the per-scene distribution is drawn on the Method screen. Do not hide it — a judge who finds a
weakness you concealed will discount everything else you said.

### Stated limitations — these are in the shipped output, not just this document

1. Every supplied scene contains labelled oil, so these figures measure *delineation quality on
   scenes already known to contain a slick*. They are **not a false-alarm rate on clean sea.**
2. The dataset contains **no labelled look-alikes** (algal blooms, low-wind zones, rain cells,
   ship wakes), so the model's ability to reject them is **untested and unquantified**.
3. Patch metrics do not include errors that only appear at scene scale — hence the scene
   evaluation.
4. The reference masks are the supplied labels; their own accuracy is unknown and is treated as
   ground truth throughout.

---

## 3.7 Requirement-by-requirement compliance

Checked against the code, clause by clause.

| PS clause | Status | Where |
|---|---|---|
| (a) Detect the oil spill | ✅ | U-Net, 0.771 IoU vs 0.582 classical |
| (a) Characterise / geometric properties | ✅ | area, perimeter, centroid, orientation, per-region breakdown, spherical |
| (a) **…and age if feasible** | ✅ | `spillAge` on every case — up to 24 h at acquisition, with the separation test showing why one acquisition cannot tighten it |
| (b) **Oceanographic** data → origin | ✅ | current field drives the particles (synthetic field) |
| (b) **Meteorological** data | ✅ | wind at 3–7 m/s, 3% windage, `forcing.py:470` |
| (b) Origin **point and time** | ✅ | probability envelope + release window |
| (b) Predict future flow | ✅ | forward drift |
| (c) Reconstruct traffic in the origin window | ✅ | tracks in space and time |
| (c) **Irrelevant traffic filtered out** | ✅ | `attribution.filtering` publishes the funnel — 10 vessels → 9 in window → 2 relevant, 8 excluded under two distinct reasons; excluded rows dimmed, not deleted, so the filter can be audited |
| (c) Score on proximity / trajectory / behavioural anomalies | ✅ | six components, below |
| Automated pipeline | ✅ | nine stages, job queue, 19 s |
| **Hindcasting** ML model | ✅ | backward drift |
| Backward **and** forward mapping | ✅ | both |
| Ranks candidates by spatio-temporal correlation | ✅ | 100-point scale |
| Suitable visual interface | ✅ | 6 screens, desktop + mobile + print |
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

Six screens, in the order an analyst would use them.

| Screen | File | What it shows |
|---|---|---|
| **Command centre** | `command.js` | headline area, confidence, origin window, candidate count, live pipeline status |
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

Two stored cases share the same scene and the same slick figures: `00053` and `demo`.

| Field | Value |
|---|---|
| Scene | `00053`, Persian Gulf |
| Acquired | 2017-03-11T02:15:12Z |
| Mission / mode | Sentinel-1A, IW |
| **Total detected slick area** | **223.19 km²** |
| Largest single region | **148.39 km²** |
| Separate regions found | **9** |
| Mean model confidence | **99.31%** — *on the `demo` case only, see below* |
| Touches scene edge | **yes** — the slick continues outside the image, so the area is a lower bound |
| Release window | **24.0 h** — 2017-03-10T02:15:12Z to 2017-03-11T02:15:12Z |
| **Estimated spill age** | **up to 24 h at acquisition** (0–24 h), **not resolvable** — 8.556 km of drift against an 11.263 km P90 radius, a ratio of 0.76 |
| AIS reports · vessels | **987 reports · 10 vessels** |
| **Traffic filter** | 10 → **9** with reports in the window → **2** relevant · **8** excluded (1 never in the window, 7 in the window but too far) |
| Candidates ranked | **10** |
| Top candidate | **91.5 / 100** — `SYNTHETIC DEMO ALPHA`, band *"Strong geometric and temporal overlap – review first"* |

> **Present the `demo` case, not `00053`.** They have identical slick geometry, but the stored
> `00053` case has no probability map saved, so its confidence field correctly shows an em dash
> and the reason *"no probability map supplied, so no confidence is reported."* That is the
> interface behaving properly, but it is not what you want on screen while saying "99.31%
> confidence". Use the case picker next to *Export JSON* to select `demo` before judges arrive.

**Note the "touches scene edge" flag.** The slick runs off the side of the image, so 223 km² is
a floor, not a total. The interface says so. That is the kind of detail that makes a technical
judge trust the rest of the numbers.

### The four standing labels, verbatim

These strings appear in the shipped output. Know them; they are your honesty policy in code.

- `AIS mode: Synthetic demonstration data`
- `Drift forcing: Synthetic scenario data`
- `Status: Research PoC – human review required`
- `Priority candidate for investigation` — the strongest phrase anywhere in the product

Plus the ranking caveat, in full:

> *"This ranking is a triage aid computed from synthetic AIS and synthetic drift forcing. It is
> not evidence, it does not establish responsibility, and every entry requires human verification
> against licensed AIS and observed metocean data."*

---

## 3.9 Tests and reproducibility

```bash
.venv/bin/python -m pytest
```

**532 tests, about 40 seconds, all passing.**

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

Ordered by marks gained per hour spent. **Tier 0 is done** — it is kept below as a record of what
was delivered and how to point at it. Everything from Tier 1 onwards is still open, and item 5 is
now the single largest credibility gain available.

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

Because the importer exists, switching to a live feed is a file drop rather than a rewrite.
Covered by `tests/test_marinecadastre.py` and 7 route tests in `tests/test_api.py`.

**2. An explicit traffic-filtering funnel.** ✓ The rule existed in `IRRELEVANT_RADII`; what was
missing was output. `scoring.py` now publishes `attribution.filtering` — counts on both sides of
the filter, the two exclusion reasons kept apart, and the rule in prose. For the demo case:

```
987 AIS reports · 10 vessels
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
so with the arithmetic: over 24 h the estimated position moves **8.556 km against an 11.263 km
P90 radius, a ratio of 0.76**, so the whole release window sits inside its own error bar. **The
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

**5. Real currents and wind for the actual acquisition date.** *(~1 day, mostly downloading.)*
The largest single credibility gain available. The NetCDF reader already works; we simply have a
file covering the wrong dates. Get a current field from CMEMS (or **INCOIS** for Indian waters)
and wind from ERA5 covering the scene's timestamp, and the "synthetic forcing" label flips to
real by itself. **This is a download, not a rewrite.**

**6. Verify the Zenodo dataset identity, then add one Indian scene.** *(~2 days.)* If our data
is NTRO's own recommended dataset, put that on a slide — it neutralises "why the Persian Gulf?"
instantly. Then adding a **Gulf of Kutch**, **Mumbai coast** or **Ennore/Chennai 2017** scene from
the Copernicus Data Space becomes a generalisation bonus rather than a fix. Ennore has real,
citable damage figures.

**7. Look-alike discrimination.** *(~1 week.)* Classify each dark patch as oil / algae /
low-wind / wake. This is the hardest genuine question we will face and we currently have no
answer. Even a modest classifier over shape, texture and darkness statistics, presented as
"3 dark patches rejected as low-wind artefacts," is a serious differentiator.

**8. Close the loop — alerting.** *(~2 days. MERN-shaped.)* The theme is Disaster Management and
our pipeline currently ends at a screen. Add dispatch to a responder: email/SMS/WhatsApp with
position, area, drift forecast and top candidate. Judges always ask "and then what happens?"

**9. PDF incident report** *(~1 day)* with a case number, timestamp and full provenance.
Government workflows run on documents.

### Tier 2 — if time allows

- **A weathering proxy to narrow the age** — dropped out of Tier 0 because it is a classifier, not
  a label. Thin fresh sheen and thick weathered emulsion damp the sea differently and so differ in
  backscatter contrast, which makes a coarse thin/moderate/thick class defensible off statistics
  `geometry.py` already computes. It attacks the age from the imagery instead of the drift, which
  is the one route that does not need a second acquisition.
- **PyTorch retrain on a GPU** with a pretrained encoder and proper augmentation. Our
  mean-per-scene 0.641 has real headroom, and the hand-written NumPy backprop caps how deep we
  can practically go.
- **EO/optical as a second input** for cloud-free days — the PS mentions EO and we do not use it.
- **Multi-pass time series** — two passes over the same water gives a growth rate. "This slick
  grew 40% in six days" is a far stronger story than one snapshot.
- **Dark-ship detection** — SAR sees the metal hull as a bright target even with AIS switched
  off. This is the answer to "what if they turn the transponder off," and it is buildable.
- **Impact quantification** — convert 223 km² into cleanup cost, fishery value at risk, response
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
