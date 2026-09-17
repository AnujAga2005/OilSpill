# 11 — The data: what we have, what we made, and what we did with it

**Why this document exists.** You will be asked "where did your data come from?" and it is the
easiest question to lose points on, because it has three different answers — one dataset is public,
one file is real but useless for our dates, and one thing on the product is entirely fabricated by
us. This document separates those three cleanly, gives you the number for each, and gives you the
sentence to say.

Read §11.1 first. It is the answer, and everything after it is the evidence for it.

**If any term here is unfamiliar** — SAR, backscatter, dB, IoU, MMSI, IMO — [document 12](12-PLAIN-LANGUAGE.md)
has a one-line plain explanation of each.

---

## 11.1 The 30-second version

> **"Our imagery and our labels are a real, published satellite dataset — 1,200 SAR scenes from 270
> separate satellite passes between March 2015 and October 2019, across 24 seas. Our ocean-current
> file is real but it only holds one timestep — June 2026 — so it physically cannot describe the
> ocean on the day any of our images were taken. So we use it for two things it *can* legitimately
> give us, and we generate the rest ourselves and label it synthetic on every screen. And the vessel
> tracks are entirely synthetic, because real AIS for 2015 is a licensed commercial feed we don't
> have — so we built a generator that mimics the real data's schema exactly, seeded it so it
> reproduces, and labelled it synthetic at every level. Nothing on that screen claims to be a real
> ship."**

Three buckets. Learn which is which:

| Bucket | What is in it | The word to use |
|---|---|---|
| **Real, public** | 1,200 SAR scenes + their reference masks; the look-alike archive | "Published dataset" |
| **Real but unusable for our dates** | The CMEMS current file | "Spatially overlapping, temporally out of range" |
| **Ours, generated, labelled** | The drift forcing (currents, in part), the wind, the vessel tracks | "Synthetic, and labelled as synthetic on screen" |

---

## 11.2 Dataset A — the SAR imagery and reference masks

### What it is

Part I of a published Sentinel-1 SAR oil-spill dataset, on Zenodo, **DOI `10.5281/zenodo.8346860`**.
It is a peer-reviewed, openly licensed release — documented by a paper in *Marine Pollution
Bulletin*. It was matched on eight independent fingerprints, not assumed from a filename.

### What is actually in it — read these numbers out if asked

| Property | Value |
|---|---|
| Images on disk | **1,200** |
| Masks on disk | **1,200** |
| Matched image–mask pairs | **1,200** (0 images without a mask, 0 masks without an image) |
| Distinct satellite passes | **270** parent Sentinel-1 products |
| Image size | **2048 × 2048** pixels, every one of the 1,200 |
| Bands per image | **2** — `Sigma0_VH_db` and `Sigma0_VV_db` |
| Image data type | 32-bit float (`>f4`) |
| Mask data type | 8-bit unsigned integer (`|u1`) |
| Compression | LZW, on every file |
| Level-1 processing chain | `Orb → NR → Cal → Spk → TC → dB` |
| Projection | **EPSG:4326** (plain latitude/longitude), every file |
| Date coverage | **2015-03-12 → 2019-10-30** |
| Satellites | Sentinel-1A (1,076 scenes), Sentinel-1B (124 scenes) |
| Acquisition mode | **IW** (Interferometric Wide swath), all 1,200 |
| Masks carrying georeferencing | **0** — this matters, see §11.2 below |
| Dataset bounds | Longitude −95.25 → 130.30, Latitude −8.16 → 61.45 |

### The two bands, and why there are two

| Band | What it is | Typical value in this data |
|---|---|---|
| **Sigma0_VV_db** | Co-polarised. Radar sent vertically, received vertically. Oil damps the small waves, so a slick goes dark here more strongly. | mean **−20.5 dB**, valid range −37.4 to −7.1 dB |
| **Sigma0_VH_db** | Cross-polarised. Weaker, noisier return — but it responds differently to a surface film, so it is genuinely extra information rather than a copy. | mean **−33.8 dB**, valid range −48.6 to −13.8 dB |

The `_db` suffix is the whole story of the "back conversion" question: these are already in
decibels, not raw digital numbers. See §11.6.

### The masks

Value set is exactly **`{0, 1}`** — oil or not oil, with no graded labels and no "maybe" class. Every
mask is used as ground truth throughout the project.

| Mask statistic | Value |
|---|---|
| Smallest oil fraction in any scene | 0.0012 (0.12% of pixels) |
| Largest oil fraction | 0.5737 (57.4%) |
| Mean | 0.0298 |
| Median | 0.0176 |
| Empty masks (no oil at all) | **0** |

That last row is important and you should say it before a judge finds it: **every scene in this
dataset contains oil.** It is a dataset of spills, not of empty sea. It is why we cannot report a
false-alarm rate from it — see §11.7.

### The 24 seas it covers

The dataset is **global, not Persian Gulf**. The largest region by scene count is the Gulf of
Mexico, not the Gulf.

| Region | Scenes | Region | Scenes |
|---|---|---|---|
| Gulf of Mexico | 388 | Central Mediterranean *(our demo scene)* | 21 |
| Eastern Mediterranean | 172 | Aegean Sea | 20 |
| Persian Gulf | 88 | Adriatic Sea | 18 |
| North Sea | 69 | Strait of Malacca | 15 |
| Western Mediterranean | 62 | Suez Canal approaches | 9 |
| Red Sea | 57 | South China Sea | 9 |
| Gulf of Guinea | 55 | Straits of Florida | 9 |
| Nile Delta shelf | 53 | Black Sea | 7 |
| Gulf of Cadiz | 40 | Bay of Biscay | 6 |
| Angolan shelf | 39 | East China Sea | 4 |
| Caribbean Sea | 29 | Irish Sea | 2 |
| Java Sea | 26 | Sea of Japan | 2 |

**No Indian water at all.** If an Indian judge asks whether it works on our coast, the honest answer
is that the method is not region-specific — decibels, spherical geometry and particle drift are the
same physics everywhere — but that getting a scene of Indian water is on our list, and document 3
has the Copernicus recipe for it. Do not claim coverage we do not have.

### The four things the audit flagged

The audit ran over all 1,200 pairs (taking 161 seconds) and found **0 errors and 4 warnings**. The
warnings are all the same kind: four masks (`00055`, `00058`, `00059`, `00085`) carry a
pixel-space transform — `[0, 1, 0, 2048, 0, -1]` — with no CRS attached. That is a transform that
describes a plain pixel grid, not the Earth.

**How we handled it, and why you should volunteer this:** we ignore the mask's transform and read
the transform from the paired image instead, after confirming both rasters are exactly the same
size. The rule is written into the code and stated in the audit. If a judge asks how we know the
masks line up with the imagery, this is the answer — and it is a better answer than "we assumed it".

### What we do with it — the split

The headline number a judge will want is how we avoided fooling ourselves. Here it is:

| Split | Satellite passes | Scenes |
|---|---|---|
| Train | 169 | 169 |
| Validation | 36 | 36 |
| Test | 35 | 35 |
| **Total** | **240 grouped** | — |

The rule: **scenes are grouped by their parent Sentinel-1 product, and each group is assigned to a
split by a stable SHA-256 hash of the group key** — so no crop of the same acquisition can appear in
two splits. This matters because a single satellite pass produces many overlapping crops; splitting
by image instead of by pass would put near-copies of the same water on both sides of the test, and
the model would look far better than it is.

**We found exactly that leak in our own evaluation and fixed it.** Before the fix our margin over
the classical baseline was **+0.189**; after grouping properly and retraining, it **halved to
+0.093**. Those are the numbers we quote. Saying both out loud — the wrong one and the right one —
is the single most credible thing available to you on stage.

### What the model does with it

| | |
|---|---|
| Patches available / used, train | 3,710 available, **1,800 used** |
| Patches available / used, validation | 826 available, **400 used** |
| Patches available / used, test | 794 available, **600 used** |
| Patch size | 128 × 128 pixels |
| Held-out test IoU | **0.769** |
| Dice | 0.869 |
| Precision / recall | 0.834 / 0.907 |
| Classical baseline IoU | 0.676 |
| **Our margin** | **+0.093** |

At scene scale — the honest one, where the whole 2048 px scene is scored instead of sampled patches
— mean per-scene IoU is **0.693** across the 35 held-out test scenes.

---

## 11.3 Dataset B — the CMEMS current file (real, but the wrong decade)

**What it is:** a real Copernicus Marine (CMEMS) global ocean-physics product,
`cmems_mod_glo_phy_my_0.083deg_P1D-m`, at 1/12° resolution, with `uo`/`vo` current components. It is
genuinely real ocean data. It is 2,041 × 4,320 grid cells covering −80° to 90° latitude. We read it
with our own NetCDF-4/HDF5 reader — no xarray, no netCDF4 package.

**The problem, stated exactly:**

| | |
|---|---|
| Product timesteps present | **1** |
| That timestep is | **2026-06-23** |
| Our imagery runs | 2015-03-12 → 2019-10-30 |
| Smallest time gap between the product and any of our scenes | **about 2,428 days** (scene `00965`, Gulf of Mexico) |
| Our tolerance for a match | **24 hours** |

So the product is **spatially valid over all 270 acquisitions but temporally valid over none**. The
audit states it as: `acquisitionsChecked: 270, acquisitionsUsable: 0, acquisitionsSpatialOnly: 270`.

**The PRD rule we followed, and why it is the right behaviour:** CMEMS currents are used *only* when
they cover the scene in both space and time. They do not. So we do not use them as the current field
— and the code still performs the overlap check at runtime rather than trusting that conclusion. Point
it at a product covering the acquisition date, and it returns a real-current object instead of a
synthetic one, with no code change. Say that: **"the check is in the code, not in a comment."**

**What the file legitimately still gives us — two things, and both are worth saying:**

1. **A land mask.** Cells where both velocity components are NaN are land. Coastlines do not move
   between 2019 and 2026, so that part of the file is valid for our scenes even though the currents
   are not. The drift engine needs to know where the coast is.
2. **A magnitude anchor.** Measured speeds over our scene footprints run **0.061 to 0.438 m/s, mean
   0.214 m/s** for the demo scene. We scale the synthetic field to those speeds, so the simulation
   moves oil at a rate this ocean region plausibly supports, instead of at a number we invented.

---

## 11.4 Dataset C — what we generated ourselves, and how

This is the part judges probe hardest, so here is exactly what was fabricated and exactly how.

### C1. The synthetic current field

**Why:** no current data covers our acquisition dates (§11.3).

**How:** a **streamfunction**. This is the one technical detail worth knowing. A streamfunction is a mathematical construction where the resulting flow is *exactly non-divergent* — meaning the flow can move oil around and stir it, but can never artificially pile it up in one place or thin it out in another. Oil in the real ocean is advected and stirred; it is not created or destroyed by the
current. Building the field this way makes our simulation obey that physical rule by construction
rather than by hoping. **`tests/test_drift.py` checks it numerically.** That is a strong answer to
"is your simulation physically sound?" — not "we think so", but "there is a test for it".

The field is built from the real ocean's dominant tidal periods — the principal lunar semidiurnal
(M2, 12.42 h), the lunar-solar diurnal (K1, 23.93 h) and the first M2 overtide (M4, 6.21 h) — so the
time variation is at believable frequencies, not arbitrary noise.

**Labelled:** `LABEL_DRIFT_SYNTHETIC` — "Drift forcing: Synthetic scenario data" — appears in the
payload and on screen.

### C2. The synthetic wind

**Why:** the same reason, resolved separately because wind and current come from different products
and either can be real on its own.

**How:** a rotating uniform vector, plausible in magnitude and invented in direction. Oil moves at
about **3% of wind speed**, and at our measured current speeds the wind term is the **same size** as
the current term — so which wind a run used is a substantive claim, not a footnote.

There are three possibilities and each one names itself in the output:

| Class | When it is used | What it means |
|---|---|---|
| `GriddedWind` | A real ERA5 file covers the acquisition | Interpolated bilinearly in space, linearly in time between hours |
| `SyntheticWind` | No real wind file covers it | The rotating uniform vector |
| `NoWind` | Currents only | What the CMEMS path did before this module could read wind at all |

**And a fourth case that matters:** a **real wind over a synthetic current** gets its own label
(`LABEL_DRIFT_HYBRID`) rather than being reported as either fully real or fully synthetic. That is
the commonest real-data situation, and the product refuses to round it to a comfortable answer.

### C3. The synthetic vessel tracks (AIS)

**Why real AIS is not used:** real AIS is a licensed commercial feed and is not available to us. So
the module fabricates one — and labels it at every level.

**How we made it not look like real ships.** This is the part to read out, because it is where the
honesty is engineered rather than asserted:

| Field | What we did | Why it cannot be mistaken for a real vessel |
|---|---|---|
| **MMSI** | All begin **`999`** | 999 is outside the ITU Maritime Identification Digits range (201–775). No real vessel can hold one. |
| **Names** | Form `SYNTHETIC DEMO ALPHA` | A callsign-shaped placeholder, deliberately not any name in a real registry. |
| **Call signs** | Form `QDEMOnnn` | The ITU allocates no international call-sign series beginning with Q — the letter is reserved for Q-codes. |
| **IMO** | **Deliberately left empty** | Every 7-digit IMO number is either allocated to a real ship or reserved. Unlike MMSI there is no synthetic-safe range, so a fabricated number would be some real vessel's. An empty IMO is also what **48.6%** of rows in a real MarineCadastre file carry — so the gap is realistic as well as safe. |

**How the tracks are placed — this is the interesting design decision.** The vessels are not
scattered at random. They are placed **relative to the backward drift result**, because that is what
makes the scoring mean anything: for every time *t* before the acquisition, the hindcast gives a
region where the oil plausibly was, and a vessel is only a candidate if it was inside that region at
that same time. So the five evidence patterns are defined as *relationships to the time-matched
envelope*, not as arbitrary coordinates:

| Pattern | What it demonstrates |
|---|---|
| `origin_on_time` | Inside the origin zone *during* the release window — the plausible candidate |
| `origin_wrong_time` | Inside the zone, but hours outside the window — right place, wrong time |
| `course_match_far` | Heading aligned with the drift, but far from the zone — suggestive, not probative |
| `slowdown_near_origin` | Speed drops to near zero beside the zone — the loitering pattern |
| `transit_background` | Ordinary traffic with no relationship at all — the control case |

**The demo case generates 10 vessels and 1,112 position reports**, on a **10-minute reporting
cadence** (a plausible terrestrial-AIS rate), spanning **8 hours before** and **6 hours after** the
acquisition.

**Reproducibility:** generation is seeded from the case key alone, using `sha256` rather than
Python's built-in `hash()` — so the same case yields identical tracks in every process and on every
machine. Two numbers are worth keeping straight, in case you are asked: the **base seed constant** is
**4,726,143**, and the demo case's **actual per-case RNG seed** is **4,025,779,775**, computed as
`stable_seed('ais:00223:auto:300:26143', 4726143)` — a SHA-256 of the key mixed with the base seed.
The case JSON records the base constant (`4726143`) under `ais.reproducibility.seed`; the derived
value is what the generator is actually handed. **The same case key always produces these exact
tracks**, and the drift run is seeded the same way.

**The schema is real even though the data is not.** The output carries the real MarineCadastre
17-column header — `MMSI, BaseDateTime, LAT, LON, SOG, COG, Heading, VesselName, IMO, CallSign,
VesselType, Status, Length, Width, Draft, Cargo, TransceiverClass` — matched byte-for-byte against a
real daily extract we downloaded (`data/raw/AIS_2022_06_01.csv`). It also reproduces the real
format's **sentinel values**: `Heading = 511.0` means "not available", `COG = 360.0` means the same,
`SOG = 102.3` means the same. Those are not our inventions; they are what the real feed does, and
getting them right is what lets the pipeline be tested against real-format data later.

**On screen:** `AIS mode: Synthetic demonstration data`, verbatim, on the *Traffic filtering* card
at the top of the Vessels screen — the line directly beneath the count of AIS reports ingested,
which is the figure it qualifies. Nothing has to be opened to read it. The full disclaimer — *"These
vessel tracks
are fabricated for demonstration. They are not evidence, they describe no real voyage, and no real
vessel was near the imaged slick."* — is one click away in the **About this synthetic feed** fold on
the Vessels screen, and is printed in both the exported case JSON and the PDF report.

---

## 11.5 Dataset D — the external look-alike archive

**What it is:** the DARTIS 2019 archive — `doi:10.1594/PANGAEA.980773` (Yang & Singha, 2025,
**CC-BY-4.0**). It is a published catalogue of SAR patches annotated for look-alikes.

| | |
|---|---|
| Patches in the catalogue | 3,655 (1,365 oil, 2,290 no-oil) |
| Annotated objects | 3,225 |
| Subsets | `nc` no-oil coastal 351 · `nw` no-oil open water 1,939 · `oc` oil coastal 375 · `ow` oil open water 990 |
| Patches actually on disk | **2,300** |
| Look-alike patches on disk | **2,290 of 2,290 published** — complete coverage |

**Why it is used, and why it is the strongest technical claim we have.** Every other accuracy number on this product is measured on scenes that *contain oil*. That means they measure how well we recover the shape of a known slick — not how often dark water that is *not* oil raises an alarm. The second question is the one an operator is actually asking. This archive is the only place we can answer it, because **we never trained on it.**

**The honesty note you must give when you cite it:** the archive holds 2,300 patches on disk but only
**10** of them are oil patches from the open-water subset (`ow`) — so the cross-domain test is
genuinely a test on the **no-oil** side (2,290 patches, 1,939 open-water and 351 coastal). Do not
describe it as a balanced cross-domain benchmark. Described correctly, it is still the most
valuable number on the product:

| Result | Value |
|---|---|
| Dark regions proposed across those patches | **84,758** |
| Rejected as look-alikes | **58,812** |
| Region rejection rate | **0.694** (0.678 coastal, 0.695 open water) |
| Patches where a region survived | 1,677 of 2,290 |
| Patch-level false-alarm rate | **0.732** |
| Patches with no dark region at all | 126 |

Read the last number as the caveat it is: we reject about 69% of dark regions that are not oil, and
**about 73% of those patches still retain at least one region we did not reject.** That is not a
solved problem and you should say so. It is, however, the only measurement of this kind on the
product, and it is measured on data the screen never saw.

---

## 11.6 The "back conversion" question — digital numbers to decibels

If a judge asks what "back conversion" means, this is it, and it is a genuinely good sign that they
asked.

A Sentinel-1 product does not ship backscatter values. It ships **digital numbers** — arbitrary
integers whose meaning depends on the calibration of that particular acquisition. Two scenes taken
on different days are **not comparable** until both are converted into physical units.

So before the model sees anything, every scene is converted to **decibels (dB)** using the
calibration and thermal-noise vectors that travel inside the product. The file-name chain records
the full sequence: **`Orb_NR_Cal_Spk_TC_dB`** — orbit-corrected, noise-removed, calibrated,
speckle-filtered, terrain-corrected, in decibels. Every one of the 1,200 files carries that same
chain, which is how we know they are all processed consistently.

**The sentence to say:** *"If you feed a neural network raw digital numbers from one satellite and
decibels from another, it learns the wrong thing — it learns the difference between the two files
instead of the difference between oil and water. Converting to dB first is what lets one model be
valid across 270 different acquisitions from 2015 to 2019."*

That is the answer to "how does one model work on 24 different seas?" as well.

---

## 11.7 What we do *not* know — say these before you are asked

Four limitations are recorded in the pipeline's own output and shown on the Methodology screen. The
first two are data limitations, so they belong in this document:

1. **Every supplied scene contains labelled oil.** So the accuracy figures measure **delineation
   quality on scenes already known to contain a slick** — they are **not a false-alarm rate on clean
   sea**. A scene of empty water is not in the dataset.
2. **The dataset contains no labelled look-alikes.** Algal blooms, low-wind zones, rain cells and
   ship wakes are not annotated in it — so the model's ability to reject them is untested *on this
   dataset*. This is exactly why the external archive in §11.5 exists, and it is why it was worth
   the effort to fetch it.
3. Metrics are computed on 128 px patches rather than whole 2048 px scenes, so errors that only
   appear at scene scale are not in the headline number. That is why we also report the scene-scale
   figure (§11.2).
4. The reference masks are the supplied labels and **their own accuracy is unknown** — we treat them
   as ground truth throughout.

**One more, and it is a per-case property rather than a dataset property:** whether the age of a
slick is resolvable at all depends on the case. A large slick that spreads faster than it drifts
carries its own age signal; a small one drifts without spreading and genuinely cannot be dated from
one image. Never describe that as a limit of the method — say which case this one is. The product
does, on the Command centre's third answer card.

---

## 11.8 The questions you will actually be asked

| Question | Answer |
|---|---|
| **Is this real data?** | Yes — 1,200 SAR scenes and their masks from a published Zenodo dataset, 270 acquisitions, 2015–2019, 24 seas. |
| **Where did you get it?** | Zenodo, DOI `10.5281/zenodo.8346860`, documented in *Marine Pollution Bulletin*. |
| **Is the AIS real?** | No. It is synthetic, and it is labelled `AIS mode: Synthetic demonstration data` on every screen. That label is a condition of the data, not a notification — it cannot be dismissed. |
| **Why is the AIS synthetic?** | Real AIS for 2015 is a licensed commercial feed. We generate from the real schema instead, with MMSI numbers starting 999 and no IMO, so no identifier can belong to a real ship. |
| **Is the ocean current real?** | The file is a real CMEMS product, but it holds a single 2026 timestep, so it cannot describe 2015. We use it for the land mask and for a speed magnitude anchor, and we generate the current field itself — and label it synthetic. |
| **Then is your drift simulation real?** | The **physics** is real — particle advection, non-divergent flow, a land mask, a windage factor of 3%. The **forcing is not the actual ocean of that day**, and we say so. Given real current and wind fields covering the date, the same code produces a real hindcast with no change. |
| **Why should we believe your accuracy?** | It is measured on data the model never trained on, split by satellite pass rather than by image, and we found and fixed a leak in our own evaluation — which halved our margin from +0.189 to +0.093. We publish the lower number. |
| **Why is there no Indian scene?** | Because the dataset has none — it is 24 seas and India is not one of them. The method is not region-specific, and getting an Indian scene is the first item on our list. *(Do not claim coverage we do not have.)* |
| **Can we put our own data in?** | Yes. With the API running, the app opens on a **New analysis** screen: drop a SAR GeoTIFF in — plus a mask, ERA5 wind, CMEMS currents or a real AIS extract if you have them, each optional and each independent — and the pipeline runs on the machine serving the site, about twenty seconds on a 2048 × 2048 scene. The finished case opens in the Command centre and joins the picker, where **Save analysis** gives it a name. Uploads are stored outside the evaluated dataset and are never added to it, so the accuracy figures still describe the audited test split and nothing else. See [document 10](10-UI-WALKTHROUGH.md) §10.12. |
| **How big is all this?** | The raw datasets stay outside the shipped product entirely — terabytes of `Oil/` and `Mask_oil/` are never copied in. The build asserts on every run that the bundled files contain no dataset formats, no host paths and no credentials. What ships is the interface: **442 KB of code** (JavaScript, CSS, HTML) plus the demo case's images and JSON, **3.0 MB in total** — that is the whole working product, offline. |

---

## 11.9 Where each number in this document comes from

Every figure above was read out of a file the project writes, not from memory. If someone challenges
a number, this is where to look:

| File | What it holds |
|---|---|
| `data/processed/audit.json` | The whole of §11.2 — counts, formats, regions, warnings |
| `data/processed/splits.json` | The split rule and the group counts |
| `data/processed/metrics.json` | Patch-scale accuracy, the baseline, the limitations list verbatim |
| `data/processed/scene_metrics.json` | Scene-scale accuracy |
| `data/processed/lookalike_metrics.json` | Everything in §11.5 — the archive and the rejection rates |
| `data/processed/cases/demo.json` | The per-case AIS, forcing, drift and geometry blocks |
| The Methodology screen (`/method`) | Most of the above, rendered, with **Data provenance** and **Reproduce this** folded at the bottom |
