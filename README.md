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

Nine deterministic stages, satellite pixel to ranked candidate:

```
decode → detect → geometry → forcing → backward drift → forward drift → AIS → scoring → previews
```

1. **Detect** oil in a Sentinel-1 SAR scene with a U-Net (2 channels: VV + VH).
2. **Characterise** it — area, perimeter, centroid, patch count — integrated on a sphere, not on
   a flat grid. Its **age** is bounded too, along with a stated test of whether one acquisition can
   narrow that bound (for the demo scene it cannot, and the app shows the arithmetic).
3. **Hindcast** it: seed particles on the slick and integrate the ocean *backwards* under currents
   plus 3% windage to get a release region and a 24-hour time window.
4. **Forecast** it forwards, for response planning.
5. **Filter** the traffic — a published funnel of counts separates vessels that could have been at
   the oil when the oil was there from those that could not. Excluded vessels are dimmed and kept,
   never deleted, so the filter itself can be audited.
6. **Rank** what survives by proximity, trajectory, timing, behavioural anomalies, type and data
   completeness — 100 points, every component and its evidence shown.
7. **Show** all of it in a dashboard: six screens, no build step.

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

Tests — **532, about 40 seconds:**

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
`00053` cases with their preview imagery, and the entire test suite. The trained model
(`models/unet_vv_vh.npz`, 7 MB) is committed, so inference works.

**What needs the dataset:** re-running the audit, rebuilding the patch cache, and retraining.

To get it: the dataset is a Sentinel-1 SAR oil-spill dataset published on **Zenodo** and
recommended by the problem statement. Drop the scenes in `Oil/` and the masks in `Mask_oil/`, then

```bash
.venv/bin/python scripts/run_audit.py && .venv/bin/python scripts/run_preprocess.py && .venv/bin/python scripts/run_train.py
```

Paths are overridable with `SPILLTRACE_IMAGE_DIR` and `SPILLTRACE_MASK_DIR` if you keep the data
on an external drive.

---

## Layout

| Path | What lives there |
|---|---|
| `apps/web/` | the dashboard — vanilla ES modules, zero dependencies, no build |
| `services/ml/` | SAR decoding, the U-Net, training, geometry |
| `services/drift/` | forcing, particle advection, synthetic AIS in the MarineCadastre schema, the spill-age bound, vessel scoring and traffic filtering |
| `services/api/` | the HTTP API (stdlib only), which also serves the dashboard |
| `services/common/` | config, GeoTIFF/DIMAP/NetCDF readers, regions |
| `scripts/` | the pipeline entry points |
| `data/processed/` | audit, splits, metrics, stored cases, preview PNGs |
| `models/` | the trained checkpoint |
| `docs/sih/` | six team documents: the problem, the domain, status, pitch, Q&A, PS compliance |
| `dist/` | the offline static bundle |

Three third-party Python imports in the whole tree — `numpy`, `cv2`, `pytest`. The frontend has
none.

---

## Honesty labels

These appear in the product and should never be dropped:

- `AIS mode: Synthetic demonstration data` — real historic AIS was not available. The problem
  statement explicitly permits synthetic AIS for demonstration.
- `Drift forcing: Synthetic scenario data` — the supplied CMEMS file does not overlap the demo
  scene in time, so deterministic seeded forcing is used.
- `Status: Research PoC — human review required`
- `Priority candidate for investigation` — never "responsible", never "guilty".

Read [KNOWN-ISSUES.md](KNOWN-ISSUES.md) for open defects, including a **train/test leakage bug
that makes the shipped accuracy figures an upper bound.**
