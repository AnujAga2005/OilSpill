# 16 · Reading the codebase

**Who this is for:** you want to understand the whole thing, not just the part you wrote. This is
the order to read it in and what to look for in each file, so you never open a file before the one
that explains it.

**91 source files, 47,356 lines**, across nine directories. Read in the order below, none of it is
hard. Read it directory by directory and most of it looks arbitrary.

| Directory | Files | Lines |
|---|---:|---:|
| `services/common/spilltrace_common` — file formats and config | 7 | 3,829 |
| `services/ml/spilltrace_ml` — the model and everything around it | 13 | 5,895 |
| `services/drift/spilltrace_drift` — drift, AIS, scoring | 8 | 5,036 |
| `services/api/spilltrace_api` — the server and the pipeline | 7 | 4,759 |
| `apps/web/app` — the interface's machinery | 15 | 5,648 |
| `apps/web/app/screens` — one file per screen | 7 | 5,534 |
| `apps/web/styles` — CSS | 4 | 3,984 |
| `scripts` — the command-line entry points | 11 | 2,700 |
| `tests` | 19 | 9,971 |

> Counted with `wc -l` on 18 September 2026, excluding three empty `__init__.py` files and
> `apps/web/index.html` (38 lines), which [document 8](08-THE-WHOLE-PROJECT.md) counts and this
> table does not — hence 92 there and 91 here. The count moves; the shape does not.

---

## 16.1 Before you open a single file

Do these two things first. An hour of reading is worth less than five minutes of watching it run.

```bash
.venv/bin/python scripts/run_api.py
```

Open http://localhost:8765, go to **New analysis**, and run one of the scenes from
[15-TEST-DATA.md](15-TEST-DATA.md). Watch the progress bar name each stage as it goes:

> `decoding 00223` → `detecting oil` → `inference 64/361 tiles` → `measuring slick geometry` →
> `screening dark patches for look-alikes` → `resolving drift forcing` →
> `reconstructing backward drift` → `projecting forward drift` → `generating synthetic AIS` →
> `scoring vessels` → `rendering previews`

**That list is the codebase.** Ten stages, one file each, in that order. Everything below is a
detour off that spine.

Then:

```bash
.venv/bin/python -m pytest -q
```

905 tests, ~43 seconds. Two reasons this matters before you read anything: it proves the tree you
are reading actually works, and the test files are the best documentation in the repository —
almost every test name is a full English sentence about a decision somebody made.

---

## 16.2 The reading order, in seven sittings

Each sitting is one or two hours. Do them in order. If you only have one evening, do sittings 1
and 2 and stop — that is the pipeline, and the pipeline is the project.

---

### Sitting 1 · The spine: one run, end to end

This is the most valuable two hours in this document. You are following one scene from a file on
disk to a ranked vessel list, and you are reading each file at the moment the pipeline reaches it.

| # | File | Lines | What you are there for |
|---|---|---:|---|
| 1 | `services/common/spilltrace_common/config.py` | 354 | **Start here.** Every path, every threshold, every label string in the product. Nothing else makes sense until you know that `C.LABEL_CANDIDATE` is one constant and not a sentence somebody typed twice. Also reads `.env`. |
| 2 | `services/api/spilltrace_api/case.py` → `build_case()` (line 911) | 1,509 | **The spine itself.** Read *only* `build_case` on this pass — about 250 lines. Every `say(...)` in it is a stage you watched in the bar. Each stage calls out to one module; the next nine rows are those modules, in call order. |
| 3 | `services/ml/spilltrace_ml/dataset.py` | 485 | Stage 1, `decoding`. Opens the scene, gives you a `Scene` object. |
| 4 | `services/common/spilltrace_common/geotiff.py` | 851 | How a GeoTIFF is actually read — no GDAL, no rasterio, bytes upward. Skim the parser, read the `Affine` class properly: it is the thing that turns a pixel into a latitude and it is used everywhere downstream. |
| 5 | `services/ml/spilltrace_ml/model.py` + `nn.py` | 242 + 412 | Stage 2, `detecting oil`. `model.py` is the U-Net's shape; `nn.py` is the autograd engine it runs on — convolution, batch norm, the optimiser, written out. Read `model.py` closely and `nn.py` as far as your interest holds. |
| 6 | `case.py` → `infer_probability()` (line 300) | — | Back to `case.py` for one function: the tiled inference that prints `inference 64/361 tiles`. Explains why a 2048×2048 scene is 361 overlapping tiles and how their edges are blended. |
| 7 | `services/ml/spilltrace_ml/geometry.py` | 505 | Stage 3, `measuring slick geometry`. Mask → polygons, areas, centroids. |
| 8 | `services/ml/spilltrace_ml/lookalike.py` | 752 | Stage 4, `screening dark patches`. Why a dark patch might not be oil, and the verdict each region carries — `accepted` / `uncertain` / `rejected`. This is the file behind the oil-vs-look-alike split on the Command Centre map. |
| 9 | `services/drift/spilltrace_drift/forcing.py` | 1,177 | Stage 5, `resolving drift forcing`. Wind and current, real where the data overlaps and deterministic-synthetic where it does not, and — importantly — the record of *which one it used and why*. |
| 10 | `services/drift/spilltrace_drift/engine.py` | 579 | Stages 6 and 7, backward and forward drift. Particle advection. `seed_from_mask` then `simulate(direction=...)`; the same function runs both directions. |
| 11 | `services/drift/spilltrace_drift/ais.py` | 1,133 | Stage 8, `generating synthetic AIS`. Read the module docstring first — it is explicit that this is synthetic and why. |
| 12 | `services/drift/spilltrace_drift/scoring.py` | 770 | Stage 9, `scoring vessels`. Four explainable components, weighted. Never outputs "guilty" — read how carefully the wording is handled, because you will be asked about it. |
| 13 | `services/ml/spilltrace_ml/preview.py` | 322 | Stage 10, `rendering previews`. The PNGs the map draws. |
| 14 | `case.py` → `assemble()` (line 1311) | — | The last stop. Every number above, gathered into the one JSON document the whole frontend reads. **If you only ever read one function in this codebase after `build_case`, read this one** — it is the contract between backend and frontend. |

Then look at what it produced:

```bash
.venv/bin/python -c "import json;d=json.load(open('data/processed/cases/00223.json'));print(list(d))"
```

Every top-level key there was written by a stage you just read.

---

### Sitting 2 · The server around the spine

`build_case` does not run itself. This is what calls it and what happens to the result.

| # | File | Lines | What you are there for |
|---|---|---:|---|
| 1 | `scripts/run_api.py` | 61 | The entry point. Deliberately tiny. |
| 2 | `services/api/spilltrace_api/server.py` | 1,278 | Routing table near the top, then one `_method` per endpoint. Read the route table and `do_POST` first, then `_submit`, then whichever handlers you care about. |
| 3 | `services/api/spilltrace_api/jobs.py` | 192 | **Read in full — it is 192 lines and it explains the whole UX.** A run is 20 seconds, so a POST returns a job id and the browser polls. `Job.to_dict()` is the exact shape the progress bar consumes. |
| 4 | `services/api/spilltrace_api/store.py` | 199 | Where a finished case lives, plus the cached detection mask (`.mask.npz`) that makes a re-run of the drift instant. |
| 5 | `services/api/spilltrace_api/uploads.py` | 359 | What happens to a file dropped on the New analysis screen. Note that uploads land in `data/uploads/` and never touch the evaluated dataset — that separation is the reason the accuracy numbers stay meaningful. |
| 6 | `services/api/spilltrace_api/reports.py` | 868 | The PDF. Nine sections, and the provenance and limitations printed into the document rather than left to the reader. |
| 7 | `services/api/spilltrace_api/dispatch.py` | 354 | Emailing that PDF. SMTP, dry-run mode, the optional recipient allowlist. |

---

### Sitting 3 · The frontend, machinery first

No framework, no build step, no dependencies. Plain ES modules the browser loads directly, which
means **you can read it in the order the browser does**.

| # | File | Lines | What you are there for |
|---|---|---:|---|
| 1 | `apps/web/index.html` | — | Ends in one line: `<script type="module" src="app/main.js">`. That is the entire bootstrap. |
| 2 | `apps/web/app/dom.js` | 278 | **Read first and read properly.** `h()` builds an element, `mount()` replaces a container's children, `icon()` draws an SVG. Three functions. Every screen in the app is built from them, so ten minutes here saves you from re-deriving them in every file afterwards. |
| 3 | `apps/web/app/state.js` | 168 | The store. One object, a `set`, a set of listeners. Also the separate progress channel, which exists so a running job does not rebuild the page. |
| 4 | `apps/web/app/router.js` | 75 | Hash routing. `#/imagery?case=00223`. Shortest file in the frontend. |
| 5 | `apps/web/app/api.js` | 480 | Every call to the server, plus the offline fixture fallback that lets `dist/` work with no API at all. `awaitJob` is the polling loop. |
| 6 | `apps/web/app/main.js` | 839 | **The hub.** `SCREENS` at the top is the nav; `render()` draws the shell and hands off to a screen; `runAnalysis()` is what the Run button actually does; `boot()` at the bottom is the start-up sequence. |
| 7 | `apps/web/app/ui.js` | 640 | The component vocabulary — `U.card`, `U.stat`, `U.notice`, `U.dialog`, `U.field`. You will recognise every one of them from the screen. |
| 8 | `apps/web/app/format.js` | 176 | Number and date formatting. Boring and worth knowing exists, so you never write a second one. |
| 9 | `apps/web/app/progress.js` | 300 | The run bar. Worth reading for the docstring alone: it is explicit about which part of the bar is measured and which part is estimated. |

---

### Sitting 4 · The frontend's hard part: the map

The map is hand-written too — no Leaflet, no Mapbox. These four files are the densest code in the
frontend and they are best read together, in this order.

| # | File | Lines | What you are there for |
|---|---|---:|---|
| 1 | `apps/web/app/mapview.js` | 828 | Canvas, projection, pan and zoom, `drawVector`. Everything drawn on a map goes through here. |
| 2 | `apps/web/app/layers.js` | 478 | What each thing on the map *is*: slick rings, drift path, origin zone, vessels — and the colour vocabulary, which is meaning and not decoration. |
| 3 | `apps/web/app/chart.js` + `charts.js` | 212 + 310 | The same idea for plots. `chart.js` draws axes; `charts.js` is the specific charts. |
| 4 | `apps/web/app/exporters.js` | 273 | JSON, CSV, GeoJSON and PNG out of the browser. |

---

### Sitting 5 · The seven screens

Now the screens themselves. Each one is self-contained and reads top-to-bottom, so take them in
whatever order interests you — but this order matches the pipeline you read in sitting 1.

| # | File | Lines | The screen |
|---|---|---:|---|
| 1 | `screens/analysis.js` | 218 | `#/new` — New analysis. The shortest; start here. |
| 2 | `apps/web/app/upload.js` | 531 | The upload form that screen hosts. Five slots, four fields, one button. |
| 3 | `screens/command.js` | 644 | `#/` — Command Centre. The locator map, the verdict panel, the headline numbers. |
| 4 | `screens/satellite.js` | 547 | `#/imagery` — the SAR scene and the layer switches. |
| 5 | `screens/slick.js` | 1,094 | `#/slick` — geometry, per-region screening, the look-alike evidence. |
| 6 | `screens/drift.js` | 861 | `#/drift` — the hindcast, the origin zone, spill age. |
| 7 | `screens/vessels.js` | 805 | `#/vessels` — the ranked candidates and the four score components. |
| 8 | `screens/methodology.js` | 1,365 | `#/method` — the accuracy figures, the evaluation protocol, the stated limits. **The longest file in the frontend and the one judges' questions land on.** |

CSS last, and only if you are changing it: `tokens.css` (194, the variables — read this one),
then `base.css`, `components.css`, `screens.css`.

---

### Sitting 6 · How the model got there

Nothing in this sitting runs during a demo. It is how the checkpoint on disk was produced, and it
is where the accuracy numbers come from — so read it before anyone asks you to defend them.

| # | File | Lines | What you are there for |
|---|---|---:|---|
| 1 | `services/ml/spilltrace_ml/audit.py` | 828 | **Read first.** Checks the dataset before anything trains on it — duplicates, mask/scene mismatches, leakage between splits. The reason the split is trustworthy. |
| 2 | `services/ml/spilltrace_ml/dartis.py` | 370 | The source dataset's own structure and its split definition. |
| 3 | `services/ml/spilltrace_ml/train.py` | 494 | The training loop. |
| 4 | `services/ml/spilltrace_ml/metrics.py` | 213 | IoU, Dice, precision, recall — defined once, used everywhere. |
| 5 | `services/ml/spilltrace_ml/baseline.py` | 251 | The classical threshold baseline the model is compared against. Without this, "0.693 IoU" means nothing. |
| 6 | `services/ml/spilltrace_ml/cache.py` | 359 | Preprocessed-patch caching, which is why training is not I/O-bound. |
| 7 | `scripts/run_audit.py`, `run_preprocess.py`, `run_train.py`, `run_scene_eval.py` | 73 + 115 + 88 + 228 | The four commands, in the order you would actually run them. Each one is thin — the work is in the modules above. |

---

### Sitting 7 · The edges

Read as needed, not cover to cover.

| File | Lines | When you need it |
|---|---:|---|
| `services/common/spilltrace_common/netcdf4.py` | 1,093 | A NetCDF-4/HDF5 reader, written out. Only if you are touching ERA5 or CMEMS ingestion. |
| `services/common/spilltrace_common/era5.py` · `cmems.py` | 555 + 459 | Wind and current products: what they contain and whether they cover a given scene and time. |
| `services/common/spilltrace_common/dimap.py` | 362 | Sentinel-1 SAFE/DIMAP metadata — where a real acquisition time comes from. |
| `services/common/spilltrace_common/regions.py` | 155 | Scene id → sea region name. |
| `services/drift/spilltrace_drift/marinecadastre.py` · `realais.py` | 800 + 375 | Reading a real AIS CSV, when one is supplied instead of the synthetic feed. |
| `services/drift/spilltrace_drift/age.py` | 154 | Spill-age estimation. Short, and the reasoning is stated, not just the number. |
| `services/ml/spilltrace_ml/audit_report.py` | 662 | Renders the audit into something readable. |
| `scripts/build_web.py` | 508 | Builds `dist/`. Asserts on every run that no dataset formats, host paths or credentials made it into the bundle. |
| `scripts/build_cases.py` | 220 | Regenerates the stored demo cases. |
| `scripts/refresh_lookalike_detector.py` · `run_lookalike_eval.py` | 144 + 917 | Refits and evaluates the look-alike screen. |

---

## 16.3 Reading the tests instead

If you learn better from tests than from source, this is a legitimate route through the whole
codebase — the test names are written as sentences and most of them state a decision.

```bash
.venv/bin/python -m pytest --collect-only -q | head -60
```

| Test file | Lines | Reading it teaches you |
|---|---:|---|
| `test_api.py` | 1,359 | Every endpoint, the job model, the case store, path-traversal defences, and the Python↔JS contracts (the progress bar's stage lines are asserted from both ends). |
| `test_incident_features.py` | 1,062 | The report, the email path, and the exact wording of every label and limitation. **The best single file for understanding what this product refuses to claim.** |
| `test_uploads.py` | 876 | Upload validation, and why uploads stay out of the evaluated dataset. |
| `test_scoring.py` | 696 | The four vessel-score components, and that no output ever says "guilty". |
| `test_ais.py` | 557 | That the synthetic feed labels itself as synthetic everywhere it can. |
| `test_forcing_products.py` | 510 | Real-vs-synthetic forcing, and hybrid labelling when only wind is real. |
| `test_preview.py` · `test_geometry.py` · `test_metrics.py` | 499 + 220 + 380 | Rendering, polygons, and the metric definitions. |
| `test_nn_gradients.py` | 297 | The autograd engine checked against numerical gradients — the proof that the hand-written backprop is correct. |
| `test_geotiff.py` · `test_dataset.py` · `test_dartis.py` | 416 + 389 + 337 | The file formats and the split. |
| `test_drift.py` · `test_age.py` | 498 + 270 | Advection and spill age. |
| `test_lookalike.py` | 546 | The screening verdicts. |
| `test_baseline.py` · `test_regions.py` · `test_marinecadastre.py` | 394 + 232 + 433 | The baseline, region naming, real AIS parsing. |

---

## 16.4 Four questions, and the file that answers each

When somebody asks in a review, go straight here.

| Question | File | Where |
|---|---|---|
| "Where does the accuracy number come from?" | `spilltrace_ml/metrics.py`, then `data/processed/metrics/` | the metric is defined once; the number is read from the file, never typed |
| "How do you know a dark patch is oil?" | `spilltrace_ml/lookalike.py` | the verdict and its components |
| "How can you say the spill started *there*?" | `spilltrace_drift/engine.py`, then `forcing.py` | the simulation, then what drove it and how real that was |
| "Are you accusing that ship?" | `spilltrace_drift/scoring.py` | the labels — `Priority candidate for investigation`, and nothing stronger anywhere in the tree |

---

## 16.5 What not to read

Honest advice about where the time goes:

- **`netcdf4.py` (1,093 lines)** — a complete HDF5 reader. Impressive, and irrelevant unless you
  are changing forcing ingestion. Read the docstring and move on.
- **`components.css` (2,111 lines)** — skim `tokens.css` instead; it is the part that carries
  meaning.
- **`nn.py` (412 lines)** — read it if you want to be able to say "we wrote the autograd" and
  defend it. Otherwise `model.py` alone tells you the architecture.
- **`run_lookalike_eval.py` (917 lines)** — an evaluation harness, not product code.
- **`__pycache__` anywhere** — not source.

---

## 16.6 The one-paragraph summary, once you have read it all

A Sentinel-1 GeoTIFF is decoded by a hand-written reader (`geotiff.py`) into a `Scene`
(`dataset.py`). A hand-written U-Net (`model.py` on `nn.py`) runs over it in 361 overlapping tiles
and returns a probability field; a threshold chosen on the validation split makes it a mask.
`geometry.py` turns the mask into polygons and `lookalike.py` decides, per polygon, whether it
looks like oil or like a known impostor. `forcing.py` resolves wind and current for that place and
time — real where the data covers it, deterministic-synthetic and labelled as such where it does
not — and `engine.py` advects particles backwards to an origin zone and forwards to a projection.
`ais.py` places synthetic vessel traffic in the resulting envelope and `scoring.py` ranks it on four
explainable components, calling the top of the list a *priority candidate for investigation* and
never anything stronger. `case.py`'s `assemble()` gathers all of it into one JSON document;
`server.py` serves it; and a dependency-free ES-module frontend draws it across seven screens, with
`methodology.js` printing the accuracy figures and the limitations next to each other on purpose.

---

**Next:** [RUNBOOK.md](../../RUNBOOK.md) is the operational counterpart — how to run, retrain,
rebuild and email, rather than how to read. Document [8](08-THE-WHOLE-PROJECT.md) is the same
material taught as a subject rather than as a reading order; use it when you want the *why* and
this document when you want the *order*.
