# 1 — The problem statement, and what we have to build

Read this first. It is the contract. Everything else in these documents exists to satisfy it.

> **In plain words:** the government has written down a problem it wants solved. This file is that
> problem, quoted, plus what each clause is actually asking for. If you only remember one thing:
> they want us to *find* oil spills and *work out who to look at* — two different jobs, and the
> second one is the hard half. New to the vocabulary? [Document 12](12-PLAIN-LANGUAGE.md) first.

---

## 1.1 The official record

| Field | Value |
|---|---|
| Problem Statement ID | **26143** |
| Title | Leveraging satellite imagery to determine Oil spills at sea along with AIS data correlations to identify vessel responsible for the spill. |
| Organisation | **National Technical Research Organisation (NTRO)** |
| Department | National Technical Research Organisation (NTRO) |
| Category | Software |
| Theme | **Disaster Management** |
| Briefing video | https://www.youtube.com/watch?v=cQoHSStTEdM |

**Two things to take from the metadata before reading a word of the description.**

**NTRO is India's technical intelligence organisation.** Not an environmental agency, not a
university. That tells you who is in the room. They will care about whether a conclusion can
survive scrutiny, whether the provenance of every number is traceable, and whether the output
is usable by an analyst at a desk. They will be less impressed by a pretty chart than by a
clearly stated confidence bound.

**The theme is Disaster Management, not remote sensing.** So the yardstick is *response* — how
fast does a responder learn something actionable, and what do they do next. A model with a good
IoU that ends at a screenshot has not managed a disaster. Keep that in mind when you decide
what to build next.

---

## 1.2 The description, broken into its parts

The description is one long paragraph. It is actually five separate demands. Here they are
separated out, with the original words preserved.

### Background

> *Marine oil spills inflict great damage on marine ecosystems and several times remains
> un-attributable to the vessel causing such spills. Leveraging satellite imagery along with
> AIS data will enable detection of oil spills and vessel responsible for the same.*

The problem being solved is **attribution**, not detection. Detection is the easy half and it
is already a solved research area. The pain point NTRO names is that spills "remain
un-attributable" — nobody can prove who did it, so nobody is penalised, so it keeps happening.
Your project's reason to exist is closing that gap.

### Requirement (a) — detect and characterise

> *Detect and characterise the oil spill and calculating geometric properties **and age if
> feasible**.*

Three deliverables: find the oil, measure it, and estimate how old it is. The "if feasible"
on age is a hedge, not permission to skip it — a reasoned estimate with stated uncertainty
scores; silence does not.

### Requirement (b) — hindcast and forecast

> *Using **oceanographic and meteorological data**, it is envisaged to trace the slick towards
> the origin point and time, predict the future flow of the slick.*

Note **two** data types, not one. Oceanographic means currents. Meteorological means wind.
Both are required — and wind matters enormously for surface oil, so a submission that only
uses currents is physically incomplete.

Note also **"origin point *and time*"**. You must produce a *when*, not just a *where*. That
"when" is what makes the AIS cross-reference possible at all.

### Requirement (c) — filter, score, attribute

> *analyse and attribute the spill to a vessel using historic AIS data to reconstruct vessel
> traffic around the origin window in space and time. **The irrelevant traffic is to be
> filtered out** and potential suspect vessels are to be scored considering various aspects
> such as **proximity, trajectory, behavioural anomalies etc.***

The most prescriptive clause in the whole statement. It tells you the algorithm:

1. Take the origin window (space **and** time) from step (b).
2. Reconstruct all vessel traffic through it.
3. **Throw away the irrelevant traffic** — and be seen to throw it away.
4. Score what remains on **proximity**, **trajectory**, **behavioural anomalies**, and more.

If you only remember one sentence from the PS, remember this one. It is a specification.

### Expected solution

> *An automated detection and **hindcasting** machine learning model that identified oils
> slicks from satellite imagery, mapping their drift paths backward and forward. It also ranks
> potential culprit vessel based on **spatio-temporal correlation** with AIS data. A suitable
> **visual interface** is also to be [developed].*

Four required properties, and two words worth stealing:

- **Automated** — a pipeline, not a notebook you run cell by cell.
- **Hindcasting** — their word for running the physics backwards. Use it. It signals you read
  the statement.
- **Spatio-temporal correlation** — their word for the matching step. Use it too.
- **Visual interface** — explicitly required, not a bonus. Good news: it is where you can most
  visibly out-build other teams.

---

## 1.3 The data they gave you — read this row carefully

> *AIS Data: 1. Format of AIS data can be obtained from sample AIS data available at
> https://marinecadastre.gov/accessais/. 2. **Real AIS if available may be used else synthetic
> data can be prepared for the region of oil spill to demonstrate the functioning of the
> algorithm.** Satellite Imagery Data of Oil spills: 3. Zenodo – Sentinel-1 SAR Oil Spill
> Dataset*

Three separate gifts here, and each one removes a risk.

**Gift 1 — synthetic AIS is explicitly permitted.** In writing, by the problem setter. This is
the single most important sentence in the document for us, because our AIS is synthetic. NTRO
anticipated that real historic AIS for an arbitrary ocean patch is not obtainable by students,
and pre-approved the substitute. The condition attached is that it must *"demonstrate the
functioning of the algorithm"* — meaning the data may be invented but the algorithm operating
on it must be real. That is exactly our situation.

**Memorise that sentence.** If a judge challenges the synthetic AIS, you quote it and move on.

**Gift 2 — they named the format authority.** `marinecadastre.gov/accessais` is the reference
schema. This is a strong hint: if your synthetic feed emits *exactly* those columns, then your
pipeline is provably real-AIS-ready and a judge can see that switching to a live feed is a file
drop rather than a rewrite. Download their sample CSV and match the header exactly.

**Gift 3 — they named the imagery dataset, and it is the one we have.** *Verified, not assumed.*
"Zenodo – Sentinel-1 SAR Oil Spill Dataset" is:

> Trujillo-Acatitla, R., Tuxpan-Vargas, J., Ovando-Vázquez, C., & Monterrubio-Martínez, E. (2023).
> *Sentinel-1 SAR oil spill image dataset for train, validate, and test deep learning models.
> Part I.* Zenodo. DOI **10.5281/zenodo.8346860** (concept DOI `10.5281/zenodo.8346859`),
> CC BY 4.0. Authors at IPICYT, Mexico.

That is the archive holding our 1,200 image/mask pairs in `Oil/` and `Mask_oil/`. It is documented
by a peer-reviewed paper you can hand a judge: *"Marine oil spill detection and segmentation in SAR
data with two steps Deep Learning framework,"* **Marine Pollution Bulletin 204: 116549** (2024),
DOI `10.1016/j.marpolbul.2024.116549`. The match was confirmed on eight independent fingerprints —
file count, the `NNNNN.tif` naming, raster dimensions, band naming, dtype, the embedded BEAM-DIMAP
processing chain, mask value encoding, and the acquisition date span.

**Two things follow, and the second one matters more.**

*The compliance answer is now a fact, not a guess.* "Which dataset is this?" has a citation with two
DOIs and a journal article behind it.

*But do not repeat the "Persian Gulf data" framing — it is wrong, and it is our own error.* This
dataset is **global**, not regional: 1,200 scenes across **24 named seas**, from 95°W to 130°E and
8°S to 61°N. The Gulf of Mexico is the largest block at 388 scenes; the Eastern Mediterranean has
172; the **Persian Gulf is 88, about 7%**. Only our *demo case* is a Persian Gulf scene. The full
breakdown is the "Approximate regions covered" table in `DATA_AUDIT.md`.

So the honest version of the awkward question is not "why the Persian Gulf?" but **"why no Indian
water?"** — and the answer is that the recommended dataset contains none. Not one of the 1,200
scenes falls between 65–95°E and 5–25°N. That is a real gap, it is a generalisation question rather
than a compliance one, and §3.8 item 6 is how we close it.

---

## 1.4 So what do we actually have to build?

Assembling the clauses above into a system, the required chain is:

```
Sentinel-1 SAR image
      │
      ▼
[1] AI segmentation ────────────────────► which pixels are oil          (req a)
      │
      ▼
[2] Geometry ───────────────────────────► area, perimeter, shape, age   (req a)
      │
      ▼
[3] Ocean currents + wind ──────────────► the forcing field             (req b)
      │
      ├──► [4] BACKWARD drift (hindcast) ► origin point AND time        (req b)
      │
      └──► [5] FORWARD drift (forecast) ─► where it goes next           (req b)
      │
      ▼
[6] AIS traffic reconstruction ─────────► who was in that window        (req c)
      │
      ▼
[7] Filter out irrelevant traffic ──────► shortlist                     (req c)
      │
      ▼
[8] Score on proximity / trajectory /
    behavioural anomalies ──────────────► ranked candidates             (req c)
      │
      ▼
[9] Visual interface ───────────────────► the analyst's screen      (expected soln)
```

Nine boxes. Our pipeline implements them as **ten** named stages with almost exactly these
boundaries — the extra one is the look-alike screen, which sits between the geometry and the
forcing and has no box of its own above because the PS never asked for it. See document 3.

---

## 1.5 The one hard constraint we impose on ourselves

The PS uses the words *"identify vessel responsible"*, *"culprit vessel"* and *"attribute"*.
Our product deliberately never says a vessel is guilty. The strongest phrase anywhere in the
interface is:

> **Priority candidate for investigation.**

This is not us failing requirement (c). It is us satisfying it responsibly, and you must be
able to explain why in one breath:

> We do attribute — we rank vessels by spatio-temporal correlation with the hindcast origin,
> with every scoring component and its evidence exposed. What we don't do is *assert* guilt.
> For an NTRO deliverable, an intelligence product that overstates its confidence cannot
> survive scrutiny, and a false accusation against a named, identifiable vessel is a
> diplomatic problem, not a software bug. So we produce the strongest defensible statement and
> leave the finding to the investigator.

Use *their* vocabulary for the capability — *attribution*, *hindcasting*, *spatio-temporal
correlation*, *suspect scoring*, *prime candidate* — so they hear their own rubric. Keep the
careful phrasing inside the product. Restraint reads as professionalism to a technical
intelligence audience.

---

## 1.6 Before the finals

- [ ] **Watch the briefing video** (link above). These NTRO videos usually state what the
      evaluators actually want. Ten minutes that could redirect a week of work. Nobody has
      watched it yet.
- [x] **Verify the Zenodo dataset identity** against our `Oil/` filenames. Done — Part I,
      DOI `10.5281/zenodo.8346860`, matched on eight independent fingerprints. See §1.3, gift 3.
- [ ] **Add one Indian scene.** The recommended dataset has none; §3.8 item 6 has the recipe.
- [x] **Download the MarineCadastre sample CSV** and record its exact column header. Done —
      `data/raw/AIS_2022_06_01.csv`, and the 17-column header now matches what the app emits at
      `/api/cases/demo/ais.csv` byte-for-byte.
- [ ] Print the (a)/(b)/(c) clauses and pin them where the team can see them.

Next: **[document 2 — what you need to know](02-WHAT-YOU-NEED-TO-KNOW.md)**, the domain
knowledge, from zero.
