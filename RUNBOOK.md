# SpillTrace — how to run it

Everything below runs offline on this machine. No API keys, no npm install, no build step for
the frontend. Copy a block, paste it in a terminal opened at the repository root.

The repository root is wherever you cloned or unpacked it — the directory containing
`pytest.ini`. Every command below assumes your terminal is open there:

```
cd path/to/OilSpill
```

All Python is run through the project interpreter, `.venv/bin/python`, so you never need to
activate the virtualenv.

---

## 1. The one command you actually need

```bash
.venv/bin/python scripts/run_api.py
```

Then open **http://localhost:8765** in a browser.

That single process is both the API and the dashboard. It prints:

```
SpillTrace API <version> on http://localhost:8765
  dashboard  http://localhost:8765/
  health     http://localhost:8765/api/health
  cases      http://localhost:8765/api/cases
```

Stop it with `Ctrl-C`.

**How to tell it worked:** the badge at the top right of the dashboard reads **Live API**
(green dot). If it reads **Offline demo** (amber), the page is being served from static files
and the API is not answering — see §6.

The pipeline outputs are already on disk, so this starts in under a second. If
`data/processed/cases/demo.json` were missing, the server would build the demo case in a
background thread on first start (about a minute) while still answering `/api/health`
immediately.

---

## 2. Walk through the dashboard

Five case screens and one reference screen, in the order an analyst would use them. The left
sidebar on desktop, the tab bar on a phone.

| # | Screen | What to do on it |
|---|--------|------------------|
| 1 | **Command centre** | Read the headline: total detected slick area, model confidence, origin window, candidate count. Click **Open investigation** to start the walkthrough. |
| 2 | **Imagery** | Switch **band** between VV and VH. Switch **overlay** between prediction, reference mask and agreement. Drag the split handle on the before/after viewer. Toggle the layer switches. Click any thumbnail in *All layers*. **Run detection again** re-segments the scene. |
| 3 | **Slick** | Read the measured extent and the per-region table. Click a row to see that region's detail. Press **Edit boundary** and drag a handle — the analyst ring is stored separately from the model ring and measured with the same spherical formula. Export **GeoJSON** or **CSV**. |
| 4 | **Drift** | Press play on the timeline to animate the particle cloud. The segmented control switches the view between backward (to origin) and forward (where it goes). **Run drift now** recomputes it. |
| 5 | **Vessels** | The ranked shortlist. Click a vessel to open its score breakdown — every component of the score, the weight applied, and the evidence sentence behind it. Nothing is called responsible; the strongest phrasing is *Priority candidate for investigation*. |
| — | **Method** | Every number's provenance: model architecture, patch-scale vs whole-scene metrics, the threshold-selection sweep, and what the system cannot tell you. |

Also worth trying:

- **Export JSON** in the page header — the full case document, exactly what the API returned.
- **Print** — the layout has a print stylesheet; the dark imagery stages get a hairline
  border and cards avoid page breaks.
- Resize the window narrow, or open it on your phone over the LAN, to see the mobile layout.
- The case picker next to *Export JSON* switches between `00053` and `demo`.

---

## 3. Start a real analysis from the interface

The demo case is precomputed. To make the pipeline actually run, there are two buttons:

- **Imagery** screen, *Detection* card → **Run detection again**. Re-segments the scene and
  rebuilds the whole case downstream of it.
- **Drift** screen, *Trajectories* card → **Run drift now**. Reuses the stored detection mask
  and re-runs drift, trajectories and vessel scoring on top of it.

What happens when you press one:

1. The API replies `202 Accepted` with a job id — it never blocks on a long computation.
2. The client polls the job and streams its progress lines.
3. The *Processing status* card on the Command centre shows the live state, and each stage's
   timing appears when it finishes.
4. When the job finishes, the case reloads and every screen updates.

This is why GET requests are always fast: they only ever read stored results. Vessel scores
have no button of their own because any run rebuilds them — the ranking is a function of the
drift result, so scoring it separately from the drift that produced it would let the two drift
apart.

---

## 4. Talk to the API directly

With the server running, in a second terminal:

```bash
curl -s http://localhost:8765/api/health
```

```bash
curl -s http://localhost:8765/api/cases
```

```bash
curl -s http://localhost:8765/api/cases/demo | head -c 2000
```

Start a job and watch it. A `drift` POST reuses the stored detection, so it needs a case that
already has one — `00053` and `demo` both do; anything else returns 404 telling you to POST
`/detect` first:

```bash
curl -s -X POST http://localhost:8765/api/cases/00053/drift -H 'Content-Type: application/json' -d '{"horizonHours": 24, "particles": 1200}'
```

The response contains `pollUrl`. Poll it until `state` is `done`, `failed` or `cancelled`:

```bash
curl -s http://localhost:8765/api/jobs/<job-id>
```

Every POST body is optional and takes the same keys, all with defaults:
`detector` (`auto`), `particles` (50–20000), `horizonHours`, `threshold`, `previews`, `seed`.
An empty body `{}` is valid and uses the configured defaults.

A `drift` run on `00053` with default parameters takes about 13 seconds and moves through nine
stages — `decode, detect, geometry, forcing, backward, forward, ais, scoring, previews`. It is
seeded, so running it twice gives byte-identical figures; only the timing block and the
generated timestamp change.

Full route list:

| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/api/health` | liveness, pipeline version, what is on disk |
| GET | `/api/metrics` | model metrics, patch scale and whole scene |
| GET | `/api/scenes` | every matched image/mask pair found in the supplied data |
| GET | `/api/cases` | stored cases |
| GET | `/api/cases/<id>` | the full case document |
| GET | `/api/cases/<id>/images` | the rendered PNG layers for that case |
| GET | `/api/cases/<id>/report` | printable report payload |
| GET | `/api/eval/<name>.png` | evaluation figures |
| POST | `/api/cases/<id>/detect` | run segmentation |
| POST | `/api/cases/<id>/slick` | recompute geometry |
| POST | `/api/cases/<id>/drift` | run drift |
| POST | `/api/cases/<id>/trajectories` | recompute trajectories |
| POST | `/api/cases/<id>/vessels` | rescore candidates |
| GET | `/api/jobs` · `/api/jobs/<id>` · `/api/jobs/<id>/result` | job queue |
| POST | `/api/jobs/<id>/cancel` | cancel a job |

Run the API without the dashboard:

```bash
.venv/bin/python scripts/run_api.py --api-only --port 9000
```

---

## 5. Rerun the pipeline from scratch

These are the stages that produced what is on disk. You do **not** need any of them to use
the app — they are here so you can reproduce the numbers. None of them modify `Oil/`,
`Mask_oil/` or the CMEMS `.nc` file.

```bash
.venv/bin/python scripts/run_audit.py
```
Inspects every supplied file, pairs images to masks, and writes `data/processed/audit.json`
plus `DATA_AUDIT.md`. Start here if you want to see what the datasets actually contain.

```bash
.venv/bin/python scripts/run_preprocess.py --max-scenes 60
```
Builds the patch cache and the preview PNGs. `--max-scenes 0` processes every pair and takes
roughly two hours; 60 is enough to train on.

```bash
.venv/bin/python scripts/run_train.py --epochs 12
```
Trains the NumPy U-Net and writes `models/unet_vv_vh.npz` and `data/processed/metrics.json`.

```bash
.venv/bin/python scripts/run_scene_eval.py 12
```
Scores whole 2048 px scenes rather than sampled patches, and sweeps the threshold on the
validation scenes before applying it to test. The positional argument limits how many scenes
are scored. Writes `data/processed/scene_metrics.json`. This is the honest number — patch IoU
flatters the model badly, and the Method screen shows both.

```bash
.venv/bin/python scripts/run_api.py --build-demo
```
Rebuilds the seeded offline demo case and exits.

---

## 6. The offline static bundle

There is a second way to run the frontend with no Python API at all — a self-contained folder
that replays the demo case from static files:

```bash
.venv/bin/python scripts/build_web.py
```

```bash
.venv/bin/python -m http.server 8787 --directory dist
```

Then open **http://localhost:8787**. The badge will read **Offline demo** and a standing
notice says new analyses cannot be started. This is the mode for handing the project to
someone who has no environment set up.

`dist/` contains no raw datasets, no absolute host paths and no credentials — `build_web.py`
asserts that on every run.

---

## 7. Tests

```bash
.venv/bin/python -m pytest
```

432 tests, about 35 seconds. Add `-v` for names, or point it at one file:

```bash
.venv/bin/python -m pytest tests/test_api.py -v
```

---

## 8. If something looks wrong

**Badge says "Offline demo" while the API is running.** The page was loaded from `dist/` on
port 8787, not from the API on 8765. Open http://localhost:8765 instead.

**"Address already in use".** Something is already on 8765. Either use it, or
`.venv/bin/python scripts/run_api.py --port 8766`.

**A screen says "No case has been computed yet".** Build the demo case:
`.venv/bin/python scripts/run_api.py --build-demo`.

**Stylesheet or script changes do not appear.** The dashboard is served as plain files with no
bundler, so the browser cache is the only thing between you and your edit. Hard-reload
(`Cmd-Shift-R`).

**A number looks wrong.** Every figure on every screen has a provenance row or a card note
saying where it came from. The Method screen is the full account. If a value is not available
the interface shows an em dash and the reason, never a zero.

---

## What is real and what is not

| Part | Status |
|------|--------|
| Sentinel-1 imagery and reference masks | **real**, the supplied dataset, unmodified |
| Segmentation model and all metrics | **real**, trained here, measured here |
| Slick geometry, area, perimeter, orientation | **real**, computed on a sphere from the mask |
| Drift forcing | **synthetic** deterministic field — the supplied CMEMS reanalysis does not cover this acquisition time, and the interface says so on every screen |
| AIS vessel tracks | **synthetic demonstration data**, labelled as such everywhere |
| Vessel ranking | real arithmetic over synthetic tracks — a methodology demonstration, not evidence |
