# SpillTrace

Satellite oil-spill detection, backward drift to the release point, and explainable ranking of
nearby vessels.

Built for **Smart India Hackathon problem statement 26143** (National Technical Research
Organisation — *"Leveraging satellite imagery to determine oil spills at sea along with AIS data
correlations to identify vessel responsible for the spill"*).

**Status: research proof of concept. Human review required. It does not establish
responsibility for a spill.** See [KNOWN-ISSUES.md](KNOWN-ISSUES.md) before quoting any number.

---

## What it does

Ten deterministic stages, satellite pixel to ranked candidate:

```
decode → detect → geometry → screening → forcing → backward drift → forward drift → AIS →
scoring → previews
```

1. **Detect** oil in a Sentinel-1 SAR scene with a U-Net (2 channels: VV + VH).
2. **Characterise** it — area, perimeter, centroid, patch count — integrated on a sphere, not on
   a flat grid. Its **age** is bounded too, along with a stated test of whether one acquisition can
   narrow that bound (for the demo scene it cannot, and the app shows the arithmetic).
3. **Screen** every dark patch for look-alikes — algae, low wind and wakes all darken radar the way
   oil does. On an external look-alike archive the U-Net alone raises an alarm on **90–100%** of a
   340-patch subsample (two dB mappings, since that archive is 8-bit JPEG); the screen rejects
   **69%** of the 84,758 dark regions across all 2,290 patches, and separates oil from look-alike at
   **AUC 0.957** in 5-fold cross-validation grouped by parent product. A real improvement, not a
   solution: 73% of patches still keep at least one region, and anything the screen is unsure of is
   returned as *uncertain* and kept, because suppressing a detection it cannot judge would trade a
   measured false positive for an unmeasured missed spill.
4. **Hindcast** it: seed particles on the slick and integrate the ocean *backwards* under currents
   plus 3% windage to get a release region and a 24-hour time window.
5. **Forecast** it forwards, for response planning.
6. **Filter** the traffic — a published funnel of counts separates vessels that could have been at
   the oil when the oil was there from those that could not. Excluded vessels are dimmed and kept,
   never deleted, so the filter itself can be audited.
7. **Rank** what survives by proximity, trajectory, timing, behavioural anomalies, type and data
   completeness — 100 points, every component and its evidence shown.
8. **Show** all of it in a dashboard: six screens, no build step.

Every ranked vessel is labelled **"priority candidate for investigation"**. Nothing in this
project calls a vessel guilty.

---

## Run it

Needs Python 3.11+. No npm, no build step, no API keys, no internet.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
.venv/bin/python scripts/run_api.py
```

Then open **http://localhost:8765** and pick the `demo` case.

The synthetic AIS feed is emitted in the **17-column MarineCadastre/NAIS schema** the problem
statement names as the format authority, so it can be diffed against a real daily extract:

```bash
curl -s http://localhost:8765/api/cases/demo/ais.csv | head -3
```

Tests — **776, about 42 seconds:**

```bash
.venv/bin/python -m pytest
```

There is also a fully static offline bundle in `dist/`, useful as a demo hot spare:

```bash
.venv/bin/python -m http.server 8787 --directory dist
```

Full operational detail — every script, every flag, troubleshooting — is in
[RUNBOOK.md](RUNBOOK.md).

---

## The dataset is not in this repository

The Sentinel-1 source rasters are about **91 GB** and are excluded by
[`.gitignore`](.gitignore):

| Excluded | Size | What it is |
|---|---|---|
| `Oil/` | 48 GB | 1,200 Sentinel-1 GeoTIFF scenes, 2048 × 2048, VV + VH |
| `Mask_oil/` | 4.7 GB | 1,200 matching ground-truth masks |
| `*.7z` | 38 GB | the archives those arrived in |
| `*.nc` | 370 MB | CMEMS ocean current NetCDF |
| `data/processed/cache/` | 242 MB | patch cache, rebuilt by `run_preprocess.py` |
| `.venv/` | 277 MB | virtualenv |

**What still works after a clone:** the API, all six dashboard screens, the stored `demo` and
`00223` cases with their preview imagery, and the entire test suite. The trained model
(`models/unet_vv_vh.npz`, 7 MB) is committed, so inference works.

> The picker also still lists `00053`. Do not demo it — it was generated on 2026-09-04 by a
> superseded pre-retrain checkpoint, and its scene is not in the current train/val/test split at
> all. Delete `data/processed/cases/00053.*` and rebuild if you want the picker clean.

**What needs the dataset:** re-running the audit, rebuilding the patch cache, and retraining.

To get it: this is **Part I** of *"Sentinel-1 SAR oil spill image dataset for train, validate, and
test deep learning models"* — Trujillo-Acatitla, Tuxpan-Vargas, Ovando-Vázquez & Monterrubio-Martínez
(IPICYT), Zenodo, CC BY 4.0, DOI **[10.5281/zenodo.8346860](https://doi.org/10.5281/zenodo.8346860)**
(concept DOI `10.5281/zenodo.8346859`), and it is the dataset the SIH problem statement recommends.
It is documented by *"Marine oil spill detection and segmentation in SAR data with two steps Deep
Learning framework,"* Marine Pollution Bulletin 204: 116549,
[doi:10.1016/j.marpolbul.2024.116549](https://doi.org/10.1016/j.marpolbul.2024.116549). Drop the
scenes in `Oil/` and the masks in `Mask_oil/`, then

```bash
.venv/bin/python scripts/run_audit.py && .venv/bin/python scripts/run_preprocess.py && .venv/bin/python scripts/run_train.py
```

Paths are overridable with `SPILLTRACE_IMAGE_DIR` and `SPILLTRACE_MASK_DIR` if you keep the data
on an external drive.

Two companion parts exist and are worth knowing about, because they close stated limitations rather
than adding scenes for their own sake:

| Part | DOI | What it adds |
|---|---|---|
| **II** | `10.5281/zenodo.8253899` | 685 no-oil + 685 **look-alike** images with masks. The only route to a detector that *learns* the oil/look-alike distinction instead of being screened after the fact — see KNOWN-ISSUES.md §4. |
| **III** | `10.5281/zenodo.13761290` | A held-out test split — 150 images + 150 masks for each of look-alike / no-oil / oil. A clean external test set, immune to the grouping bug in KNOWN-ISSUES.md §1. |

The dataset is **global**, not regional: 1,200 scenes across 24 named seas from 95°W to 130°E. The
Gulf of Mexico is the largest block at 388 scenes, the Persian Gulf is 88. **No scene falls in
Indian water** — see `DATA_AUDIT.md` for the full region table.

---

## Layout

| Path | What lives there |
|---|---|
| `apps/web/` | the dashboard — vanilla ES modules, zero dependencies, no build |
| `services/ml/` | SAR decoding, the U-Net, training, geometry |
| `services/drift/` | forcing, particle advection, synthetic AIS in the MarineCadastre schema, the spill-age bound, vessel scoring and traffic filtering |
| `services/api/` | the HTTP API (stdlib only apart from the PDF report), which also serves the dashboard |
| `services/common/` | config, GeoTIFF/DIMAP/NetCDF readers, regions |
| `scripts/` | the pipeline entry points |
| `data/processed/` | audit, splits, metrics, stored cases, preview PNGs |
| `models/` | the trained checkpoint |
| `docs/sih/` | six team documents: the problem, the domain, status, pitch, Q&A, PS compliance |
| `dist/` | the offline static bundle |

Two third-party imports carry the whole pipeline — `numpy` and `cv2`. Two more sit off to one side:
`reportlab` is imported *inside* the one function that renders the PDF incident report, so a missing
install costs you that one endpoint and nothing else (Pillow comes along because reportlab imports
it unguarded), and `pytest` is tests only. Five lines in `requirements.txt`; the frontend has none.

---

## Honesty labels

These appear in the product and should never be dropped:

- `AIS mode: Synthetic demonstration data` — real historic AIS was not available. The problem
  statement explicitly permits synthetic AIS for demonstration.
- `Drift forcing: Synthetic scenario data` — the supplied CMEMS file does not overlap the demo
  scene in time, so deterministic seeded forcing is used. Currents and wind are separate products
  and either can be made real on its own, so this label has four forms
  (`… CMEMS data`, `… Synthetic currents with ERA5 wind`, `… CMEMS currents with ERA5 wind`);
  supplying a wind file is described in [RUNBOOK.md](RUNBOOK.md) §6a.
- `Status: Research PoC — human review required`
- `Priority candidate for investigation` — never "responsible", never "guilty".

Read [KNOWN-ISSUES.md](KNOWN-ISSUES.md) before quoting any accuracy figure. The train/test
leakage bug it documents is **fixed and the pipeline has been re-run** — the current figures are
measured on a split where no acquisition appears on both sides, and they are lower than the ones
that preceded them.
