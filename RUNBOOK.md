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

Then open **http://localhost:8765** in a browser. It opens on **New analysis**, which asks for a
scene of your own; **Load previous saved cases** goes to the stored ones. A link that names a case
— `http://localhost:8765/#/?case=00223` — skips the intake screen and opens that case directly.

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
and the API is not answering — see §7.

The pipeline outputs are already on disk, so this starts in under a second. If
`data/processed/cases/demo.json` were missing, the server would build the demo case in a
background thread on first start (about a minute) while still answering `/api/health`
immediately.

---

## 2. Walk through the dashboard

An intake screen, five case screens and one reference screen, in the order an analyst would use
them. The left sidebar on desktop, the tab bar on a phone.

| # | Screen | What to do on it |
|---|--------|------------------|
| — | **New analysis** | Where the app opens with the API up. One panel, in the order you work in it: five upload slots (only the SAR scene is required), then four fields — a **required case id** (suggested from the filename), an optional analysis name, the acquisition instant and the band order — then **Run the pipeline**, which processes the scene and lands you on the Command centre. **Load previous saved cases** skips straight to the stored ones — see §6c. |
| 1 | **Command centre** | Read the headline slick area, then the four numbered answers below it — *how large*, *when released*, *how old*, *how many vessels*. Each one links to the screen that shows its working. Acquisition, provenance and stage timings are folded away underneath; click a heading to open one. Click **Open investigation** to start the walkthrough. |
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
- The case picker next to *Export JSON* switches between the stored cases. Present
  `00223 · demo`. The other entries are held-out test scenes built by `scripts/build_cases.py`,
  which refuses anything the model trained on, so any of them is safe to open if a judge asks.

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
already has one — `00223` and `demo` both do; anything else returns 404 telling you to POST
`/detect` first:

```bash
curl -s -X POST http://localhost:8765/api/cases/00223/drift -H 'Content-Type: application/json' -d '{"horizonHours": 24, "particles": 1200}'
```

The response contains `pollUrl`. Poll it until `state` is `done`, `failed` or `cancelled`:

```bash
curl -s http://localhost:8765/api/jobs/<job-id>
```

Every POST body is optional and takes the same keys, all with defaults:
`detector` (`auto`), `particles` (50–20000), `horizonHours`, `threshold`, `previews`, `seed`.
An empty body `{}` is valid and uses the configured defaults.

A `drift` run on `00223` with the parameters above takes **about 21 seconds** and moves through
ten stages — `decode, detect, geometry, screening, forcing, backward, forward, ais, scoring,
previews`.
Most of that is fixed cost you pay whatever you ask for: 9.7 s decoding the 2048 × 2048 GeoTIFF,
5.7 s of inference, 1.9 s of geometry and 1.8 s screening the dark patches for look-alikes. The two
drift stages are 1 s each at 1200 particles, so particle count is
a cheap dial. The run is seeded, so running it twice gives byte-identical figures; only the
timing block and the generated timestamp change.

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
| GET | `/api/cases/<id>/ais.csv` | the case's synthetic AIS feed in the 17-column MarineCadastre schema |
| GET | `/api/eval/<name>.png` | evaluation figures |
| POST | `/api/cases/<id>/detect` | run segmentation |
| POST | `/api/cases/<id>/slick` | recompute geometry |
| POST | `/api/cases/<id>/drift` | run drift |
| POST | `/api/cases/<id>/trajectories` | recompute trajectories |
| POST | `/api/cases/<id>/vessels` | rescore candidates |
| GET | `/api/jobs` · `/api/jobs/<id>` · `/api/jobs/<id>/result` | job queue |
| POST | `/api/jobs/<id>/cancel` | cancel a job |
| POST | `/api/cases/<id>/report` · `/api/cases/<id>/dispatch` | the printable report, and emailing it |
| GET | `/api/uploads` | what the operator has staged, and which slots are filled |
| POST | `/api/uploads?kind=&name=` | **store one file.** The body is the file itself, not JSON — `kind` is one of `scene`, `mask`, `era5`, `cmems`, `ais` |
| POST | `/api/uploads/clear` | delete every stored upload |

The upload route is the one that does not take a JSON body, so `curl --data-binary` is the way in:

```bash
curl -s -X POST "http://localhost:8765/api/uploads?kind=scene&name=my_scene.tif" --data-binary @my_scene.tif
```

It refuses before reading where it can: an oversized `Content-Length` earns a **413** and a wrong
extension a **400**, both decided from the headers rather than after a 43 MB transfer. A file that
passes those and then fails its magic-byte check is a 400 too, but that one costs you the upload.

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
.venv/bin/python scripts/run_lookalike_eval.py
```
Answers the one question every other metric assumes away: given a *dark* region, is it oil or a
look-alike? Fits the seven-feature screen on the supplied scenes, cross-validates it grouped by
parent Sentinel-1 product, then scores it against the published DARTIS 2019 look-alike archive it
never trained on. Writes `data/processed/lookalike_metrics.json`. The same-domain half takes
minutes; the full run took **2 h 57 m**.

The cross-domain half needs the archive on disk first, which is a separate download:

```bash
.venv/bin/python scripts/fetch_dartis2019.py --subset nc,nw
```
Fetches the 2 290 no-oil / look-alike patches (`doi:10.1594/PANGAEA.980773`, CC-BY-4.0). Add
`--subset oc,ow` for the 1 365 oil patches too. `--manifest` prints the catalogue without
downloading, and `--limit N` stops after N patches. Skip both commands and the eval still runs —
it reports the same-domain half and says the cross-domain half is absent.

```bash
.venv/bin/python scripts/run_api.py --build-demo
```
Rebuilds the seeded offline demo case and exits.

---

## 6. Optional: give the drift a real ocean

The drift takes its currents and its wind from **two different products**, and either one can
be real while the other is not. Nothing here is required — the app runs without both — but each
file you supply moves one half of the physics from "plausible construction" to "measurement",
and the interface relabels itself accordingly.

| You supply | Currents | Wind | Label the dashboard shows |
| --- | --- | --- | --- |
| nothing | synthetic | synthetic | `Drift forcing: Synthetic scenario data` |
| ERA5 wind only | synthetic | **real** | `Drift forcing: Synthetic currents with ERA5 wind` |
| CMEMS covering the scene | **real** | none | `Drift forcing: CMEMS data` |
| both | **real** | **real** | `Drift forcing: CMEMS currents with ERA5 wind` |

Wind is worth more than its billing suggests. Oil moves at about 3 % of the wind speed
(`windage_factor = 0.03`), and against the 0.0939 m/s current this scene is anchored to, that
term is the **same size as the current itself** — so where the wind comes from is half the
answer, not a footnote.

### 6a. ERA5 10 m wind (free, needs an account)

1. Register at [cds.climate.copernicus.eu](https://cds.climate.copernicus.eu) and accept the
   ERA5 licence. No key goes anywhere near this repository — you download the file by hand.
2. Open **ERA5 hourly data on single levels from 1940 to present** and request exactly:
   - Variables: `10m_u_component_of_wind` and `10m_v_component_of_wind` — both, and nothing
     else. The reader looks for `u10`/`v10` and will refuse a file that lacks either.
   - Date: the acquisition day **and the day either side**, because a 24 h hindcast reaches
     back past midnight. For the shipped demo scene (`00223`, Central Mediterranean,
     `2015-08-04T16:55:41Z`) that is **3–5 August 2015**.
   - Times: all 24 hours. Wind is the one product interpolated in time, so cadence is used.
   - Geographical area: **sub-region**, padded ~1° around the scene footprint. The demo scene
     spans `14.477…14.661 E, 35.788…35.972 N`, so North `37`, West `13`, South `35`, East `16`.
   - Format: **NetCDF4**. GRIB is not readable here.
3. Save it as `data/raw/era5_wind_2015-08.nc` — anything matching `era5_wind*.nc` in
   `data/raw/` or the repository root is found automatically. To keep it elsewhere:

```bash
export SPILLTRACE_ERA5=/absolute/path/to/your_wind.nc
```

4. Rebuild a case and check the Drift screen's **Forcing** card. It now names ERA5 as the wind
   source, the mean wind over the footprint, how many hourly frames were read, and what
   fraction of the drift horizon the file actually covered:

```bash
.venv/bin/python scripts/run_api.py --build-demo
```

The file is small — a padded footprint at ERA5's 0.25° grid is a few hundred cells per hour, so
three days of hourly wind is a couple of megabytes, not gigabytes.

**What happens if the file does not cover the scene.** Nothing breaks and nothing is faked. The
wind is rejected, the reason appears in the Forcing card's reason list, and the drift falls
back to the synthetic rotation (synthetic currents) or to no wind at all (CMEMS currents) — in
which case the card says the spread is a *lower bound* on where the oil could have gone,
because real oil also moves with the wind. Coverage shorter than the horizon is not a
rejection: the wind clamps at the ends of the file and the card reports the fraction covered.

### 6b. CMEMS currents

The `.nc` that ships here is a real CMEMS global physics product, but it does not cover August
2015, so the temporal overlap check fails and the currents stay synthetic — visibly, on every
screen. Note that the *magnitude* is still borrowed from it: the synthetic field is scaled to the
0.2138 m/s mean the real product reports over this scene's footprint, and only the pattern is
invented. To make the currents real, download a product whose time axis contains the acquisition
from the [Copernicus Marine Service](https://marine.copernicus.eu), keep the
`cmems_mod_glo_phy_*.nc` naming (or point `SPILLTRACE_CMEMS` at it), and rebuild. The overlap
check re-runs on its own; there is no flag to force it, by design.

### 6c. Or hand the file to the interface instead

Everything above puts a file on disk where the pipeline finds it at startup. There is a second
route that needs no shell and no restart: the **New analysis** screen — where the app opens, and one
click from the Command centre's *Analyse your own scene →* row. It has a slot for each product — SAR scene, reference mask, ERA5 wind, CMEMS currents, AIS extract —
and only the scene is required.

Two things to know before using it on stage:

* **The slots are independent, not a bundle.** Supplying ERA5 alone is a real improvement; you do
  not need the 370 MB CMEMS product to get the better half of the physics.
* **Every file is re-checked against that scene**, exactly as a file on disk would be. One that
  does not overlap in space or time is reported with the reason on the case's own Forcing card.
  There is no silent downgrade to synthetic, and no flag to force an overlap that isn't there.

A bare GeoTIFF exported by hand carries less than the dataset's own products do, so the form asks
for two things the file may not state: the **acquisition instant** (every forcing lookup and the
whole release window are positioned against it) and the **band order** (with no header to name
them, a scene stored VV-first would be scored with the polarisations swapped). The case records
whether the time was read from the product or typed by an operator — the two are never presented
as the same thing.

Uploads land in `data/uploads/`, which is gitignored and separate from `Oil/` and `Mask_oil/`. An
uploaded scene can never overwrite or shadow a supplied one: the case is stored under an id you
choose, and an id that already names a case is refused with a 409 rather than replaced. The form's
**Clear** control removes every uploaded file, so you can take your own data off the machine after
a demo without opening a shell.

**Which file to put in it** is a question with a wrong answer — most scenes in `Oil/` were used to
train the model, so uploading one proves nothing. [`docs/sih/15-TEST-DATA.md`](docs/sih/15-TEST-DATA.md)
names five held-out scenes to use instead, a sixth that fails on purpose, and what each optional slot
will accept.

---

## 7. The incident report, and emailing it

A case can leave the screen as a signed-off PDF. Fastest way to look at the document:

```bash
.venv/bin/python scripts/make_report.py
```

It prints the path it wrote under `data/processed/reports/`. `--case <id>` picks a different
stored case and `--number ST-DEMO-0001` overrides the generated case number.

Nine sections: case information, incident location, the detection, the drift hindcast and
estimated origin, spill age, vessel triage with the score broken into its components and the
reason behind each, provenance, the stated limitations verbatim, and a disclaimer with a blank
sign-off block. Every page is footed with the case number and *"Research proof of concept — not
evidence — human review required"*.

To exercise the email path as well:

```bash
.venv/bin/python scripts/make_report.py --dispatch ops@example.gov
```

It prints the dispatch mode first, so you know what is about to happen before you read the
result. **With no SMTP host configured it writes a `.eml` file beside the PDF and sends nothing**
— that is the default in a fresh clone, and the dashboard says so too: the button in the page
header opens a dialog that reads **Write .eml** rather than **Send report**, with one line
explaining which piece is missing.

To actually send, copy the template and fill it in:

```bash
cp .env.example .env
```

`.env` is gitignored and is read at import time by `services/common/spilltrace_common/config.py`,
which only sets variables that are not already in the environment — so a shell `export` always
wins over the file.

For Gmail, the whole setup is six lines. Gmail refuses your ordinary account password over SMTP,
so you need an **App Password**: turn on 2-Step Verification at
`myaccount.google.com/security`, then generate one at `myaccount.google.com/apppasswords` and
paste that 16-character string — not your login password — into `.env`:

```
SPILLTRACE_SMTP_HOST=smtp.gmail.com
SPILLTRACE_SMTP_PORT=587
SPILLTRACE_SMTP_USERNAME=you@gmail.com
SPILLTRACE_SMTP_PASSWORD=your-16-character-app-password
SPILLTRACE_SMTP_STARTTLS=1
SPILLTRACE_EMAIL_FROM=you@gmail.com
```

Then flip the dry-run flag off and restart the API, which re-reads `.env` on start:

```
SPILLTRACE_EMAIL_DRY_RUN=0
```

Other providers are the same shape with a different host: Outlook/Office 365 is
`smtp.office365.com:587`, Zoho is `smtp.zoho.in:587`. If a provider wants implicit TLS on port
465 instead of STARTTLS, set `SPILLTRACE_SMTP_SSL=1`, `SPILLTRACE_SMTP_STARTTLS=0` and
`SPILLTRACE_SMTP_PORT=465`.

Two more variables are worth knowing:

- `SPILLTRACE_EMAIL_DRY_RUN=1` — keeps it in dry run even once SMTP works. Leave it set until
  you have opened a `.eml` in Mail and are happy with what it says.
- `SPILLTRACE_ALERT_RECIPIENTS` — **optional, and empty by default.** Left empty, a report goes
  to whatever addresses the responder types into the dashboard. Setting it *narrows* that to a
  list of the only addresses mail may reach — plain addresses, or `@domain` entries for a whole
  domain. Worth setting on a machine whose port other people can see, because the dispatch
  endpoint has no authentication of its own.

`reportlab` is the project's only optional dependency, and it needs a working Pillow. If the
import fails, that one route answers **503 with the exact install command** and the rest of the
app is unaffected — the server does not fall over and the test suite skips those cases rather
than failing:

```bash
.venv/bin/python -m pip install -r requirements.txt
```

---

## 8. The offline static bundle

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

## 9. Tests

```bash
.venv/bin/python -m pytest
```

893 tests, about 28 seconds. Add `-v` for names, or point it at one file:

```bash
.venv/bin/python -m pytest tests/test_api.py -v
```

With `reportlab` and Pillow both installed — which is the state of this machine — **all 876 pass
and nothing skips**. Without them, the dozen or so tests that render a real PDF report as
**skipped** instead, and turn themselves back on the moment the import works. Everything the
report *says* — every drift figure, every score component, the limitations, the absence of
accusatory language — is asserted by tests that do not need either library, so a blocked install
cannot hide a broken document.

---

## 10. If something looks wrong

**Badge says "Offline demo" while the API is running.** The page was loaded from `dist/` on
port 8787, not from the API on 8765. Open http://localhost:8765 instead.

**"Address already in use".** Something is already on 8765. Either use it, or
`.venv/bin/python scripts/run_api.py --port 8766`.

**A screen says "No case has been computed yet".** Build the demo case:
`.venv/bin/python scripts/run_api.py --build-demo`.

**Stylesheet or script changes do not appear.** The dashboard is served as plain files with no
bundler. The API sends `no-cache` plus an ETag for `.html`, `.js`, `.css` and `.json`, so an edit
shows up on a normal reload; if you are on port 8787 serving `dist/`, that is `http.server` and
you will need a hard reload (`Cmd-Shift-R`) plus a rerun of `scripts/build_web.py`.

**"Report generation requires reportlab".** The PDF route is the one place with an optional
dependency. `.venv/bin/python -m pip install -r requirements.txt`. If it then complains about
`_imaging`, the installed Pillow wheel was built for a different Python than the venv's —
`.venv/bin/python -m pip install --force-reinstall --no-cache-dir pillow` fixes it. Nothing else
in the app is affected either way.

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
| Drift currents | **synthetic** deterministic field — the supplied CMEMS reanalysis does not cover this acquisition time, and the interface says so on every screen. Supply a covering product (§6b) and this row becomes real on its own |
| Drift wind | **synthetic** rotating field by default, because no ERA5 file ships here. Download one (§6a) and this row becomes real on its own — it is a separate product from the currents and the label names both halves |
| AIS vessel tracks | **synthetic demonstration data**, labelled as such everywhere |
| Vessel ranking | real arithmetic over synthetic tracks — a methodology demonstration, not evidence |
| Incident PDF and email dispatch | **real** document, real SMTP client — but every figure in it inherits the status of the row above it, which is why the report prints its own provenance and limitations rather than leaving them to the reader |
