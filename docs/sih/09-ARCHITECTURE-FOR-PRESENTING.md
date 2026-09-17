# 9 — The architecture, explained so you can defend it

**This is the document for the part of the presentation where you explain how the thing works.**
It is written for you to read once tonight, then skim before you walk in.

Wait — if the technical words are the problem, read
[**document 12, the plain-language primer**](12-PLAIN-LANGUAGE.md) first. It explains SAR, decibels,
IoU, U-Net, hindcast, forcing and look-alikes with no assumed background at all, and it has the five
sentences to say on stage. This document assumes you have read it, or that you already know the
vocabulary. §9.0 below is the quick reference if you only have five minutes.

How it is organised:

| Part | What it covers | Time to read |
|---|---|---|
| **9.0** | Every technical term, one line each — the quick reference | 5 min |
| **9.1** | The one-paragraph version, to say out loud | 2 min |
| **9.2** | The four things the evaluation actually scores, mapped to what we have | 5 min |
| **9.3** | The whole system in one diagram, in English | 5 min |
| **9.4** | Every piece, one at a time: what it is, where it lives, why, alternatives, trade-offs | 25 min |
| **9.5** | The technology choices, with the alternatives we rejected | 10 min |
| **9.6** | Data flow: what happens when you press a button, second by second | 5 min |
| **9.7** | The honest trade-off table — what we gave up and why | 5 min |
| **9.8** | Questions they will ask about the architecture, with answers | 10 min |

---

## 9.0 Every technical term, one line each

Skim this. If a word comes up on stage and you're not sure of it, it's here. The full explanations
are in [document 12](12-PLAIN-LANGUAGE.md).

**The satellite side**

| Term | Say this if you're asked |
|---|---|
| **SAR** (Synthetic Aperture Radar) | A satellite that uses radio waves instead of light, so it sees through cloud and at night. |
| **Sentinel-1** | The actual European satellites we use. Free, public, anyone can download their data. |
| **Backscatter** | How much of the radio wave bounced back. Rough water bounces a lot and looks bright; oil smooths the water and looks dark. *That contrast is the entire basis of the project.* |
| **VV / VH** | The two polarisations every SAR image ships as — think of them as two filters. The model reads both at once. |
| **dB** (decibel) | A physical unit for backscatter, on a log scale. We convert every image into dB before the model sees it — see §9.6. |
| **Speckle** | The grainy salt-and-pepper noise inherent to radar. Filtered out during the standard processing chain. |
| **GeoTIFF** | An image file that also stores where on Earth each pixel is. |
| **NetCDF / HDF5** | The formats scientists use for gridded data like ocean currents and wind. |
| **EPSG:4326** | The standard code for plain latitude/longitude coordinates. |

**The AI side**

| Term | Say this if you're asked |
|---|---|
| **Segmentation** | Labelling every pixel individually, not just saying "this image contains oil." |
| **U-Net** | The standard network *shape* for segmentation: shrink the image to understand it, expand it back to draw precise boundaries. A 2015 design — **not** our innovation. |
| **Mask** | The answer key: a black-and-white image where white means "this pixel really is oil." What we train against and what we score against. |
| **Held-out / test set** | Scenes the model has **never seen**, used to score it. Scoring on training data is meaningless. |
| **IoU** (Intersection over Union) | The standard overlap score: how much our predicted oil area and the true oil area coincide. 1.0 perfect, 0 no overlap. **Ours: 0.769.** |
| **Dice** | A near-identical overlap score to IoU, computed slightly differently. Reported because papers use either. |
| **Precision / recall** | *Precision* 0.834 — of the pixels we call oil, 83% really are. *Recall* 0.907 — we find 91% of the real oil. |
| **Baseline** | The simple classical method we compare against, so the AI's score means something. Ours is a dark-pixel detector at **0.676**. |
| **Threshold** | The cutoff on the model's per-pixel confidence. **Two of them**: **0.65** at patch scale (the precision and recall above are at this one) and **0.70** at whole-scene scale, which is what the interface shows. Each chosen on its own validation split, then applied unchanged to test. |
| **Parameters** | The numbers inside the network learned during training. We have **1,963,953**. |
| **Checkpoint** | The saved file holding the trained parameters — ours is **7 MB**. |
| **Forward / backward pass** | Forward: image in, prediction out. Backward: the maths that works out how to correct every parameter. **We wrote both.** |
| **Gradient / finite-difference check** | A test that proves a hand-written backward pass is mathematically correct, by comparing it against numerically measured slopes. **We have this test.** |
| **Optimiser** | The rule that decides how much to nudge each parameter on each training step. Also hand-written. |
| **Normalisation** | Rescaling every scene onto the same numeric range, so a dark patch means the same thing in every image. |

**The physics and vessels side**

| Term | Say this if you're asked |
|---|---|
| **Drift** | Oil being pushed by the ocean current and by wind dragging the surface film. |
| **Hindcast** | Running the physics **backwards** in time — from where the oil is now to where it probably came from. |
| **Forecast** | Running it **forwards** — where it's heading next. |
| **Particle simulation** | Releasing many virtual dots and moving each one. Their spread *is* the uncertainty. We use **300**. |
| **Windage** | How much the wind drags floating oil — about **3% of wind speed**, a standard figure. |
| **Forcing** | The collective term for the current and wind fields that push the particles. |
| **Envelope** | The outline containing all the particles — the "somewhere in here" region. |
| **CMEMS / ERA5** | Two real, free scientific datasets: measured ocean currents, and measured historical wind. |
| **AIS** (Automatic Identification System) | Ships broadcast their position every few seconds, like a fitness tracker. **Ours is synthetic and labelled synthetic.** |
| **MMSI / IMO** | Two ship ID numbers. MMSI is a 9-digit radio ID; IMO is the permanent hull number. Ours start with **999**, which no real ship can be assigned. |
| **Look-alike** | A dark patch in radar that **is not oil** — low wind, algae, rain, a ship wake. The hardest problem in this field. |
| **Unresolvable** | When the physics genuinely cannot pin a number down, we print "unresolvable" instead of inventing one. Age is unresolvable on some scenes. |

**The software side**

| Term | Say this if you're asked |
|---|---|
| **Monorepo** | One repository holding several independent programs that work together. |
| **API** | The program that answers requests from the website and does the actual computing. |
| **Endpoint / route** | One addressable thing the API can do — we have 21. |
| **Job queue** | Long work is accepted immediately and polled, rather than making you wait. |
| **JSON** | A plain text format for structured data — ours is human-readable, which is why results are auditable. |
| **PNG** | The image format the preview pictures are written in. |
| **SVG** | The format our charts are drawn in — vector graphics, drawn by us rather than a charting library. |
| **DOM** | The browser's live tree of the page. We manipulate it with our own small helper, no framework. |
| **HTTP keep-alive** | A browser reusing one connection for many requests. Our server is threaded because a single thread deadlocks on this. |

---

## 9.1 The one-paragraph version

> SpillTrace is a monorepo (**one repository holding several independent programs that work
> together**) with four Python services and one dependency-free web dashboard. A user opens the
> dashboard, picks a satellite scene, and presses one button. The request goes to a standard-library
> HTTP server, which queues a job. That job runs a ten-stage pipeline: it decodes a Sentinel-1 radar
> image, runs a U-Net (**a neural network that labels every pixel as oil or not-oil**) written by
> hand in NumPy, measures the resulting slick's geometry on a sphere, screens it for look-alikes,
> loads a current field, drifts particles backwards to estimate where the oil came from, loads
> vessel tracks, scores each vessel for how well it matches, and writes preview images. The whole
> thing takes about twenty seconds on a laptop with no GPU and no internet. Every result the user
> sees is either a real measurement from the supplied data, or is explicitly labelled as synthetic.

That paragraph is roughly 45 seconds spoken. It is the spine of your architecture segment.

---

## 9.2 The evaluation, and where our material sits

The internal round scores four things. Here is what each one wants, and which document answers it.

### A) Problem Understanding & Impact — 25 marks

**What they are really asking:** do you know why this problem exists in the physical world, and do
you know who is hurt when it is unsolved?

**Where our material is:** [01-THE-PROBLEM.md](01-THE-PROBLEM.md) for the framing,
[02-WHAT-YOU-NEED-TO-KNOW.md](02-WHAT-YOU-NEED-TO-KNOW.md) §2.1–2.2 for the physics, and
[06-PS-COMPLIANCE.md](06-PS-COMPLIANCE.md) for the line-by-line mapping back to the problem
statement.

**The single strongest thing to say:** *"The problem statement asks for detection plus attribution.
Detection is a solved-enough problem that we can put a number on it. Attribution is not, and almost
nobody puts a number on it. We did both, and we are explicit about which half is validated."*

### B) Innovation & Technical Excellence — 30 marks

**What they are really asking:** is there anything here that took real engineering, or is this a
wrapper around somebody else's model?

**Where our material is:** this document, §9.4 and §9.7. Plus
[08-THE-WHOLE-PROJECT.md](08-THE-WHOLE-PROJECT.md) §8.11 for the look-alike work.

**Our three defensible claims of real engineering:**

1. **The neural network is written from scratch in NumPy** — we implemented the forward pass and
   the backward pass (the part that teaches the network) ourselves, and we verify the gradients
   against a numerical approximation in the test suite. Nobody does this; it is genuinely harder
   than calling a library.
2. **We found and fixed our own data leak.** Our first split let crops of the same satellite pass
   land in both training and test. We rebuilt the split so that cannot happen, and our accuracy
   **went down** — from a margin of +0.189 to +0.093. We published the lower number.
3. **We evaluated the look-alike screen on a completely independent published archive** that our
   system had never seen, and we report it per family, including the family where we fail.

**Do not say:** *"we used a U-Net"* as if that is the innovation. A U-Net is a design from 2015 and
every team will say it. The innovation is the from-scratch implementation, the self-caught leak, and
the cross-domain evaluation.

### C) Feasibility, Practicability & Scalability — 25 marks

**What they are really asking:** could this actually be operated, by a real organisation, on real
data, without a research team standing next to it?

**Where our material is:** this document §9.5 and §9.6, plus
[03-STATUS-AND-ROADMAP.md](03-STATUS-AND-ROADMAP.md) for the roadmap.

**Our answer, in three beats:**

1. **It runs on a laptop.** Twenty seconds per scene, no GPU, no internet, no cloud account. The
   five-package dependency list is printed in `requirements.txt` with a comment explaining what is
   deliberately absent.
2. **It degrades instead of crashing.** If the email server is not configured, the report endpoint
   writes a file instead of failing. If the historical current data does not cover the scene, the
   drift screen says so and uses a labelled synthetic field. If the model checkpoint is corrupt,
   the API falls back to the classical detector rather than going down. That is what "operable"
   means in practice.
3. **Scaling is a queue and a bigger machine, not a rewrite.** The pipeline is already a job queue
   with a status endpoint. Running 200 scenes is the same code with more workers.

**The honest gap to volunteer before they find it:** we have not run it on a live feed yet. We have
run it on 1,200 archived scenes. Live ingest is a downloader plus the same processing chain — see
§9.6 — and it is a week of work, not a redesign.

### D) Solution Quality & Prototype Presentation — 20 marks

**What they are really asking:** does it look like a product, and does the demo work?

**Where our material is:** [10-UI-WALKTHROUGH.md](10-UI-WALKTHROUGH.md) — every card, button and
label, screen by screen — and [04-HOW-TO-PITCH.md](04-HOW-TO-PITCH.md) for the eight-minute path.

---

## 9.3 The system in one picture

Read this top to bottom. Each box is one real program in the repository.

```
   ┌─────────────────────────────────────────────────────────────┐
   │  THE DASHBOARD            apps/web/                         │
   │  Plain HTML, CSS and JavaScript. No framework, no build.    │
   │  Seven screens. Talks to the API over HTTP.                 │
   └───────────────────────────┬─────────────────────────────────┘
                               │  HTTP: "build me a case for scene 00223"
                               ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  THE API                  services/api/                     │
   │  Python's own http.server. Accepts the job, queues it,      │
   │  reports progress, serves the finished case as JSON.        │
   └───────────────────────────┬─────────────────────────────────┘
                               │  runs the ten stages in order
                               ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  THE PIPELINE             case.py  →  calls everything below│
   └──┬────────────┬──────────────┬───────────────┬─────────────┘
      │            │              │               │
      ▼            ▼              ▼               ▼
 ┌─────────┐ ┌──────────┐ ┌────────────┐ ┌──────────────┐
 │  ML     │ │ COMMON   │ │  DRIFT     │ │  PREVIEW     │
 │services/│ │services/ │ │ services/  │ │ ml/preview.py│
 │  ml/    │ │ common/  │ │  drift/    │ │              │
 │         │ │          │ │            │ │ writes the   │
 │ U-Net   │ │ file     │ │ currents,  │ │ PNG images   │
 │ detect  │ │ readers, │ │ particles, │ │ the UI shows │
 │ geometry│ │ geometry │ │ vessels,   │ │              │
 │ metrics │ │ on sphere│ │ scoring    │ │              │
 └─────────┘ └──────────┘ └────────────┘ └──────────────┘
      │            │              │               │
      └────────────┴──────────────┴───────────────┘
                               │
                               ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  THE RESULT               data/processed/cases/*.json       │
   │  One JSON file holding every number, polygon and label      │
   │  the case screens display. The UI renders it; it computes   │
   │  almost nothing itself.                                     │
   └─────────────────────────────────────────────────────────────┘
```

**The one idea that makes the rest make sense:** the API is the only thing that computes. The
dashboard is a renderer. That is why the same numbers appear on the Command centre, on the Slick
screen and in the exported JSON — there is exactly one source for each of them, and it is a file on
disk.

---

## 9.4 Every piece, one at a time

For each piece: **what it is** in plain words, **where it lives** so you can point at it,
**why we did it this way**, **what the alternative was**, and **what it costs us**.

### 9.4.1 The web dashboard — `apps/web/`

**What it is.** Seven screens of ordinary HTML, CSS and JavaScript. When you open it, your browser
downloads about 442 KB of text files and runs them. There is no compilation step, no `npm install`,
no framework.

**Where:** `apps/web/app/` holds 14 JavaScript modules and `apps/web/app/screens/` seven more, one
per screen; `apps/web/styles/` holds 4 CSS files.

**Why this way.** A hackathon demo has one fatal failure mode: the build breaks on the presentation
laptop. A framework like React needs a build step — the code you write is not the code the browser
runs, and something has to convert between them. That converter is a dependency, and dependencies
are what break. Our source files *are* the shipped files.

**The alternative we rejected.** React or Vue with a bundler. It would be faster to write, and it
would give us component re-use. It would also triple what can go wrong at 9 a.m. in front of judges.

**What it costs us.** We write our own small helper for building DOM elements (`dom.js`), and every
chart is hand-drawn as SVG rather than pulled from a charting library. That is maybe 600 extra lines
of code we wrote ourselves.

**How to say it:** *"Zero build step. What you are looking at is literally the source files. There
is no package.json in this repository."*

### 9.4.2 The API server — `services/api/spilltrace_api/server.py`

**What it is.** A program that listens for web requests and answers them. Written using Python's
built-in `http.server` module.

**Why this way.** Flask and FastAPI are the normal choices. Both are third-party packages we would
have to install, pin, and hope install correctly on the demo machine. Our API has twenty-one
endpoints. `http.server` is not the right tool for a production web service under load — but it is
entirely sufficient for one operator on a laptop, and it removes an entire class of failure.

**The alternative we rejected.** FastAPI (would give us automatic API documentation and request
validation) or Flask (simpler, more familiar). Both are reasonable; we chose zero dependencies.

**What it costs us.** We wrote our own routing, our own JSON serialisation helpers, and we handle
concurrency ourselves. And this would not survive a thousand concurrent users — though nothing about
this project would, since the bottleneck is that each analysis takes twenty seconds of CPU.

**And the upload made that cost concrete.** A framework hands you request-body handling; the
standard library hands you a socket. Refusing a 43 MB upload is not simply "return 400": on an
HTTP/1.1 keep-alive connection the bytes the client already sent are still in the socket, and a
handler that answers without reading them leaves the next request to be parsed starting from the
middle of a GeoTIFF — which the base handler answers with a 501 HTML error page. So refusals happen
in a deliberate order: **cheap checks before the body is read** (declared length against the cap,
then the file extension), the body **drained** when we refuse after the client has begun sending,
and the connection **closed** when a refusal comes after the body was read and draining would be
guesswork. Five tests in `tests/test_uploads.py` exist purely to hold that behaviour in place, and
two of them were written by breaking the server first and confirming they caught it.

**How to say it:** *"This is the Python standard library's HTTP server. For a single-operator
triage tool on a laptop, FastAPI's extra machinery is a liability, not an asset — because every
package we add is a thing that can fail to install on the machine we're presenting from."*

### 9.4.3 The job queue — `services/api/spilltrace_api/jobs.py`

**What it is.** A list of work that has been asked for and not yet finished. When you press a
button, the API does not make you wait twenty seconds staring at nothing. It immediately answers
"accepted, here is a job id", and the browser then asks "how is job 7 doing?" every second.

**Why this way.** A twenty-second request over HTTP is fragile — browsers time out, proxies drop
connections, and if anything goes wrong the user has no idea how far it got. A job id gives us a
progress line and a log, which is what you actually want on screen during a demo.

**The alternative we rejected.** A real queue — Celery, Redis, RabbitMQ. Correct for production,
absurd here: those need a message broker, a second process, and installation. Our queue is a Python
dictionary and a worker thread.

**What it costs us.** All state is in memory. Restart the API and the job history is gone. The
finished *results* survive, because they are written to disk as files.

**How to say it:** *"Single-process job queue. The finishing state is on disk, the transient state is
in memory — which is the right split for this scale."*

### 9.4.4 The five file readers — `services/common/spilltrace_common/`

This is where a lot of the real engineering is, and where you can impress a judge.

**What it is.** Satellite data does not come in convenient formats. Our four readers decode the
files that real agencies produce:

| Reader | File | Plain-English job |
|---|---|---|
| `geotiff.py` | `.tif` | Reads the image. A GeoTIFF is a picture **plus** a stored mapping from pixel position to latitude and longitude. |
| `dimap.py` | inside the `.tif` | Every so often a file needs to say "I am from satellite pass 7116, acquired at 16:55 on 4 August 2015." ESA's software hides that in an unassuming corner of the image file. This reads it. |
| `netcdf4.py` | `.nc` | Reads scientific data files — ocean current maps, wind maps. The format is called HDF5 and it is genuinely awkward: a tree of named arrays with metadata at each level. |
| `era5.py`, `cmems.py` | `.nc` | Two different agencies' arrangements of that same format, one for wind and one for currents. |
| `geotiff.py` | `.tif` | Also **writes** the outputs. |

**Why this way.** Every one of these has a standard Python library — `rasterio`, `xarray`, `netCDF4`.
Using them would have saved weeks. We wrote our own because each pulls in a large dependency tree
(`rasterio` alone drags in GDAL, which is the single most notoriously painful package to install on
a fresh machine).

**The alternative we rejected.** `rasterio` + `xarray` + `netCDF4`. Faster to write, standard,
better tested by far — and a genuine risk of not installing.

**What it costs us.** Our readers handle the specific subset of each format our data uses. Hand us a
GeoTIFF with a different compression scheme, or an unusual NetCDF layout, and ours may reject it
where `rasterio` would cope. We compensate with tests: 893 of them, including deliberately malformed
inputs.

**How to say it:** *"We hand-wrote four file-format readers so the whole project needs two real
libraries — NumPy and OpenCV. That is a deliberate bet on operability over convenience."*

### 9.4.5 The from-scratch neural network — `services/ml/spilltrace_ml/nn.py` and `model.py`

**What it is.** A U-Net. In plain terms: a program that takes a satellite image and outputs a
same-sized image where every pixel is labelled "oil" or "not oil." It works by first shrinking the
image through several stages (to understand the big picture — is this a slick or a coastline?), then
expanding it back out (to decide precise boundaries), with shortcut connections that carry fine
detail from the shrinking side to the expanding side so the answer does not come out blurry.

**Why a network and not a rule.** The obvious rule is "dark pixels are oil." That rule is the
baseline we built and it scores 0.676 IoU. It fails on look-alikes — dark patches that are low wind,
or algae, or a ship wake. Distinguishing those needs texture and context, not brightness.

**Why from scratch.** This is the claim that separates us from every team that will say "we used a
U-Net." We wrote the forward pass (image in, prediction out), the backward pass (the mathematics
that teaches the network), and the optimiser, in NumPy. Then, in the test suite, we check our
backward pass numerically: run the network on slightly different inputs, measure how the output
changes, and confirm that matches what our backward pass claims. That test is called a
finite-difference gradient check and it is the standard way to prove a hand-written network is
correct.

**The alternative we rejected.** PyTorch or TensorFlow. Both would give us a pre-trained model and
a one-line training loop. Both are enormous (hundreds of megabytes) and would have meant shipping a
model card we cannot explain.

**What it costs us.** Training took 1,068 seconds — under eighteen minutes on CPU. With a GPU and
PyTorch it would be seconds. But our final model is under two million parameters and the checkpoint
is 7 MB, so it travels in the repository.

**How to say it:** *"The network is 1.96 million parameters, written in NumPy. We verify the
gradients by finite differences in the test suite, which is the standard proof that a hand-written
network is mathematically correct. Training took eighteen minutes on a laptop CPU."*

### 9.4.6 The geometry on a sphere — `services/ml/spilltrace_ml/geometry.py`

**What it is.** Turning a mask of oil pixels into real measurements: how many square kilometres, how
long is the perimeter, where is the centre, how far is the farthest point.

**The trap everybody falls into.** A satellite image is a rectangle of pixels, but the Earth is a
sphere. One pixel covers less ground near the poles than at the equator, because lines of longitude
converge. The naive approach — multiply pixel count by one constant area — silently gives every
scene a wrong answer, and the error scales with latitude. Our demo scene is at 36°N, where it would
have been about 19% wrong.

**What we do instead.** We integrate pixel by pixel over a sphere of radius 6,371,008.8 m, using each
row's own latitude. This is written out in the interface too, because it is a good answer to give.

**Why not a library.** `shapely` is the standard tool for polygon geometry, but it works on a flat
plane. Using it on latitude/longitude coordinates is the same mistake in a nicer package.

**How to say it:** *"Area is integrated row by row on a sphere, because a degree of longitude
shortens with latitude. A single pixel-area constant would bias every scene, and at our demo
latitude it would have been wrong by about a fifth."*

### 9.4.7 The drift engine — `services/drift/spilltrace_drift/engine.py`

**What it is.** An oil slick moves with two things: the ocean current, and the wind pushing on the
thin film at the surface. The engine drops 300 virtual particles into the water at the slick's
location and moves them — backwards in time to ask "where did this come from?", forwards in time to
ask "where will it go?"

**Why 300 and not thousands.** 300 is what we use. Saying "thousands" would be false. 300 is enough
for the envelope to be stable and it takes under a second.

**The method.** At each time step each particle moves by (current × 1.0) + (wind × 0.03). The 0.03
is the **windage factor** — the fraction of wind speed a floating film moves at. It is a standard
value, not something we measured. The two terms are comparable in size: the current term is
0.2138 m/s and the wind term is 0.1682 m/s. Neither can be dropped.

**Why the current data is labelled synthetic on our demo scene.** The historical current product we
have covers one date in 2026; our scenes are from 2015–2019. There is no overlap, so we could not
use it. The problem statement told us exactly this: *use real currents only where the coverage
overlaps, otherwise use deterministic synthetic forcing and label it clearly.* We do exactly that,
and every screen carries the label.

**The alternative we rejected.** Interpolating the 2026 currents and calling them real. That would be
fabricating evidence, and it is the single fastest way to lose a technical judge.

**How to say it:** *"Drift is current plus three per cent of wind. On this scene the current data does
not overlap in time, so the field is synthetic and every screen says so. The problem statement asked
for exactly that behaviour."*

### 9.4.8 The vessel scoring — `services/drift/spilltrace_drift/scoring.py`

**What it is.** Each vessel is scored out of 100 on how well its track matches the estimated release
point and time.

**Six components**, each contributing points: how close it came to the origin zone, whether it was
there at the right time, whether it was slow enough to be discharging, whether it followed a
plausible route, whether it loitered, and whether its characteristics fit the scenario.

**The rule that matters most.** Our instructions were explicit and we enforce it in the code and in
the tests: **no vessel is ever called guilty.** The strongest phrase the system can produce is
*"Priority candidate for investigation."* The verdicts are bands — review first, worth a look,
retained for completeness — not accusations.

**And the honest limit.** The vessel tracks are generated by us, because real AIS for these dates is
not available to us. So the scoring has been tested for *mechanics* — does it award points for the
right reasons — but **never validated against a case where the true polluter is known.** We say this
in the product itself, and you should say it out loud before a judge finds it.

**How to say it:** *"The ranking is a triage aid. Nothing in this system establishes responsibility,
and the vessel data is synthetic, so the ranking has never been checked against a real discharge.
That limitation is printed in the product, not just in our notes."*

### 9.4.9 The look-alike screen — `services/ml/spilltrace_ml/lookalike.py`

**What it is.** The hardest problem in this field, and the one most teams skip. A dark patch in a
radar image is not necessarily oil. It can be low wind, or algae, or a ship wake, or rain. This
module decides which dark regions to reject before they ever reach a human.

**Why it is credible.** We did not evaluate it on our own data only. We evaluated it against a
**separate published archive** — 2,290 no-oil patches from a different research group, released
under an open licence, grouped into 17 look-alike families by what they actually are. Our system had
never seen any of them.

**The result, stated completely.** Across all the dark regions in those patches, it rejects 69.4% —
67.8% in the coastal families, 69.5% in the open-water ones. And per family it varies enormously:
96% in the easiest family, 32% in the hardest.

**What it cannot do.** It separates oil-like from not-oil-like. It does not name the phenomenon. No
rejected patch is labelled "algae" or "low wind," because we cannot prove which it is.

**How to say it:** *"We evaluated this on somebody else's published archive, never seen in training.
It rejects 69% of dark regions overall and 96% in the easiest look-alike family, and I can tell you
which family defeats it at 32%. We do not claim to identify what the look-alike is."*

### 9.4.10 The synthetic vessel traffic — `services/drift/spilltrace_drift/ais.py`

**What it is.** Real AIS is the public system where ships broadcast their identity and position.
Real AIS for the Eastern Mediterranean in August 2015 is not something we have access to. So we
generate vessel tracks that behave like real traffic — using a real AIS file's schema and speed
distributions — and label them everywhere as synthetic.

**The five traffic patterns we generate:** tankers on transit routes, vessels approaching a port,
fishing fleets working an area, vessels loitering, and vessels passing through at speed.

**The design decision worth mentioning.** Every vessel we filter *out* is kept in the output with
the reason it was excluded. A system that silently drops data cannot be audited.

**How to say it:** *"The traffic is generated, using the real AIS schema and real speed
distributions. The product says so on every screen that shows a vessel. And nothing we filtered out
is deleted — it is retained with its exclusion reason, because a triage system that quietly drops
data cannot be audited."*

### 9.4.11 The offline demo bundle — `scripts/build_web.py`

**What it is.** A build step that asks the running API for its own answers, writes them into static
files, and packages the whole interface into a `dist/` folder that runs with **no server at all.**

**Why it exists.** Demo-day insurance. If the API will not start, you open the static bundle in a
browser and every screen still works, showing the same numbers.

**The part that shows engineering maturity.** Because the fixtures must not leak data, the build
script asserts on every single run that the shipped bundle contains no raw dataset formats, no
absolute paths from our machine, and no credentials. That check ran on the build you have now:

```
ok 53 bundle files, no dataset formats, no host paths, no credentials
```

**How to say it:** *"The offline bundle is generated from the live API's own responses, so it cannot
disagree with it. And the build refuses to ship if it finds a credential, a raw dataset or a local
file path in the bundle."*

---

## 9.5 The technology choices, with what we rejected

This is the table to have in front of you. If they ask "why not X", X is almost certainly here.

| We chose | Instead of | Why we chose it | What it costs us |
|---|---|---|---|
| Plain JavaScript, no framework | React / Vue | Source files are the shipped files; no build step to fail on demo day | We hand-wrote the DOM helpers and the SVG charts |
| Python `http.server` | FastAPI / Flask | Zero install risk; ~15 endpoints is well within its capability | Not built for concurrent load; no automatic API docs |
| NumPy (hand-written U-Net) | PyTorch / TensorFlow | Full control, 7 MB checkpoint, gradients provably correct | Training is CPU-bound (18 min); no GPU speed-up |
| Hand-written file readers | rasterio / xarray / netCDF4 | Removes GDAL — the hardest package in this stack to install | Covers our data's subset of each format, not the whole format |
| Own geometry on a sphere | shapely / pyproj | Correct for lat/lon; shapely's flat-plane maths would bias results | Reimplemented polygon area, perimeter, contours |
| OpenCV for morphology | scipy / scikit-image | One package instead of two; it was already needed | Most morphology is actually ours, in `geometry.py` |
| In-memory job queue | Celery / Redis | No broker, no second process, no install | Job history lost on restart (results survive on disk) |
| JSON case files on disk | PostgreSQL | Auditable, greppable, diffable, zero setup | No concurrent queries; fine at one case per scene |
| Synthetic AIS, labelled | Pretend it is real | Real AIS for these dates is unavailable | The attribution half has no ground truth |
| Synthetic drift forcing, labelled | Interpolate 2026 data and call it 2015 | That would be fabricating evidence | The demo's drift field is not the real ocean |

**The single sentence that covers the whole table:** *"Every choice trades convenience for
operability. That is the right trade for a system that has to run in front of judges on a laptop
with no internet."*

---

## 9.6 What happens when you press a button

This is the "where does it happen" answer. If a judge asks *"show me where the data fetching
happens"* or *"where is this computed"*, this is your script.

### Stage by stage, with real timings from the demo scene

| # | Stage | Plain English | Where the code is | Time |
|---|---|---|---|---|
| 1 | Decode | Open the satellite image and its label mask | `common/geotiff.py`, `dimap.py` | 9.69 s |
| 2 | Detect | Run the U-Net over the image, pixel by pixel | `ml/model.py`, `nn.py` | 5.67 s |
| 3 | Geometry | Measure the slick: area, perimeter, position | `ml/geometry.py` | 1.94 s |
| 4 | Screening | Reject dark regions that are probably not oil | `ml/lookalike.py` | 1.79 s |
| 5 | Forcing | Load the current field for this place and time | `common/cmems.py`, `drift/forcing.py` | 0.03 s |
| 6 | Backward drift | Move particles back in time to find the origin | `drift/engine.py` | 0.26 s |
| 7 | Forward drift | Move particles forward to project the path | `drift/engine.py` | 0.25 s |
| 8 | Vessels | Generate and filter the traffic near the origin | `drift/ais.py` | 0.19 s |
| 9 | Scoring | Rank each vessel out of 100 | `drift/scoring.py` | 0.08 s |
| 10 | Previews | Write the PNG images the screens display | `ml/preview.py` | 0.22 s |

**Total: 20.1 seconds.** Stages 1 and 2 are 77% of it — decoding the file and running the network.
Everything else together is under five seconds.

**Where the "data fetching" is.** Note that stage 5 is 0.03 seconds even though it is the only stage
that touches an external data file. That is because the current field is loaded from a local file
that was downloaded once by hand ahead of time. In a live deployment this stage would instead be a
network request to the Copernicus Data Space — which is the one place the architecture changes for
production, and it is a contained change: a downloader in front of the same reader.

### The "back conversion" question

A teammate asked about back conversion. Here is what that refers to and how to answer.

**What it is.** Satellite radar images are distributed as **digital numbers** — plain integers,
usually 0 to 255 or 0 to 65535. That scale is arbitrary; it depends on how the file was saved. To do
physics you need **decibels (dB)** — a logarithmic scale where a fixed difference means the same
physical ratio of radar energy, no matter what the absolute brightness was.

**The conversion.** Our processing chain ends in a step ESA calls `LinearToFromdB`: take the
linear-scaled values and express them in dB. That step is why our file names carry the suffix
`Orb_NR_Cal_Spk_TC_dB` — orbiting corrections applied, noise removed, calibrated, speckle filtered,
terrain corrected, converted to dB.

**Why it matters, in one line:** *if you feed a network digital numbers from one satellite and
decibels from another, it learns the wrong thing — the numbers look different but mean the same.*
Converting to dB is what makes scenes from different dates and passes comparable at all.

**Where it is in our code:** `common/dimap.py` reads the metadata that records which chain produced
the file, and `ml/dataset.py` normalises using statistics computed across the training set
(`data/processed/norm_stats.json`) so that every scene enters the network on the same scale.

**What to say if asked:** *"Sentinel-1 files arrive as digital numbers, which are an arbitrary
scale. We work in decibels, which is a physical scale. That conversion is the last step of the
standard ESA processing chain and our filenames record it. Normalising every scene to the same
scale is what lets one network work across 1,200 scenes from different years."*

---

## 9.7 The honest trade-off table

Have this ready. Volunteering a weakness before you are asked is worth more than defending it later.

| What we gave up | Why | What we gained |
|---|---|---|
| 27.8 GB of raw dataset excluded from the repository | GitHub cannot take it | A repository a teammate can clone in seconds |
| The +0.189 accuracy margin | We found our own data leak | +0.093 that survives a hostile question |
| Using the real 2026 ocean currents | They do not cover our 2015–2019 scenes | A drift field that is labelled rather than wrong |
| Pretending AIS is real | We do not have it | Every screen says "synthetic," so no screen can mislead |
| Naming the look-alike phenomenon | We cannot prove which one it is | A screen that separates without overclaiming |
| Reporting a spill age | The physics cannot resolve it on this scene | We print "unresolvable" instead of a made-up number |
| Pre-training on ImageNet-style data | Dependency weight | A model whose every parameter we can explain |
| A live data feed | Time | Working, tested processing chains for the live feed to plug into |

---

## 9.8 Questions they will ask, with answers

**"Why did you write the neural network yourself instead of using PyTorch?"**
> "Two reasons. First, operability — PyTorch is a large dependency and we wanted the whole project to
> need two real libraries. Second, correctness — because we wrote the backward pass ourselves, we
> prove it numerically in the test suite with a finite-difference gradient check. That is the
> standard proof, and it means I can explain every number the network produces."

**"Why is your accuracy only 0.69?"**
> "That is mean per-scene IoU — the overlap between our predicted oil boundary and the human-drawn
> reference, averaged across 35 held-out scenes. A 0.69 means the boundary sits close but not
> exactly on the human's line. Our margin over a simple dark-pixel baseline is +0.093, which is real
> but modest — that baseline is a serious one and this is genuinely hard. I would rather quote the
> honest per-scene number than a patch number that flatters us."

**"What is your model's worst case?"**
> "One held-out scene scores 0.046. We ship it inside the product — you can open it from the case
> picker. Its failure mode is over-detection: it finds essentially all the oil and a great deal that
> is not oil. Six of our seven weakest scenes fail that way, which is the safer direction for a
> screening tool — you get false alarms, not missed spills."

**"How does this scale to the whole Indian coastline?"**
> "Per-scene cost is twenty seconds of CPU and 77% of that is decoding and inference, both of which
> parallelise trivially. The pipeline is already a job queue, so scaling is more workers and a
> bigger machine, not a different architecture. The real scaling question is data access, and that
> is the live-feed work on the roadmap."

**"Why should we trust a system where the ships are made up?"**
> "You should not trust the attribution half, and we do not ask you to. The detection half is
> validated on held-out real data. The attribution half has correct mechanics and is labelled
> synthetic on every screen. Being explicit about which half is which is the point — a system that
> hid this would be the dangerous one."

**"What would it take to run this operationally?"**
> "Three things, in order. One: a downloader against the Copernicus Data Space, plus automating the
> SNAP processing chain that our filenames already record — about a week. Two: real AIS access —
> there are commercial and national providers, and it is a procurement question, not a technical one.
> Three: real-time current and wind fields for the scene date — the readers and the labelling logic
> for that already exist."

**"What is the single most impressive thing here?"**
> "The look-alike screen. Every other system in this space — including ours before we built it —
> assumes a dark patch is a spill. We measured how often that is false, on an independent published
> archive, and we report which kinds of false alarm we handle well and which defeat us."

---

## The 90-second version, if you only get one shot

> "Sentinel-1 radar sees oil because oil smooths the sea surface. We decode the image, run a U-Net
> we wrote from scratch in NumPy to find the slick, measure its real area on a sphere, reject
> look-alikes, drift particles backwards to find where it came from, and rank the vessels that were
> in that place at that time. Twenty seconds, on a laptop, no GPU, no internet.
>
> Detection is validated on 35 held-out scenes at 0.69 IoU, and we found and fixed our own data leak
> to get an honest number. Attribution has correct mechanics but synthetic vessels, so it has never
> been validated against a known polluter — and the product says that on every screen rather than in
> a footnote.
>
> The thing I would point at is the look-alike screen. Most systems assume a dark patch is a spill.
> We quantified how often that is wrong, on somebody else's published archive, and we can tell you
> which kinds of false alarm we catch and which ones still beat us."

---

**Next:** [10-UI-WALKTHROUGH.md](10-UI-WALKTHROUGH.md) — every card, button and label on every
screen, so nothing on the interface can surprise you.
