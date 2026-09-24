# 17 — The demonstration video script (AICTE panel)

**What this is.** A shot-by-shot script for a *recorded* demonstration video, ~5–6 minutes,
for the AICTE panel. It says how to open the app, what to show on each screen, and the exact
words to say over each shot.

**How it differs from [document 14](14-THE-DEMO-WALKTHROUGH.md).** Doc 14 is the *live* demo —
~17 minutes, spoken while you drive the interface, with cut-downs for a live slot. This is an
*edited video*: shorter, retakeable, with recording directions and a title card. Where the two
overlap, doc 14 is the source of truth for every figure.

**How to read it.** `[ SCREEN ]` / `[ ACTION ]` lines are what the recording shows — do the
thing, don't say it. `> blockquote` lines are the voiceover, word for word. Numbers in **bold**
are on screen at that moment; if a number isn't visible, don't say it. Every figure here is read
off `data/processed/cases/00223.json`, `scene_metrics.json`, `metrics.json` and
`lookalike_metrics.json` — the same files doc 14 verifies. Regenerate and re-read if you change
the pipeline (doc 14 §14.13).

---

## 17.1 Before you record

- **Serve the offline build.** Run a static server inside `dist/` so there is no API and no
  network — the header will read **Offline demo**, which is the intended path, not a bug.

```bash
cd dist && python3 -m http.server 8000
```

- **Case picker shows `00223 · demo`** — the only case in the store.
- **Desktop width**, not a phone window. Hide the browser bookmarks bar and any extensions so the
  frame is clean.
- **Record at 1080p**, cursor-highlight on if your recorder has it, and give maps a beat to draw
  before you narrate them.
- **Record voiceover separately** if you can — it is far easier to get a clean take of the words
  and lay it over clean screen capture than to do both at once.
- **The offline app opens on the Command Centre** (`/`), on the stored case. The "New analysis"
  intake screen is in the nav but its upload panel is a dead end offline — show it only as the
  optional cold open below, don't try to "run" anything from it on camera.

---

## 17.2 The spine

Nine segments. If you know these, you can rebuild the words.

| # | Segment | Screen | Length | The one thing it must land |
|---|---|---|---|---|
| 0 | Title | card | 0:08 | Name, problem statement, one line |
| 1 | The problem | card / map | 0:20 | A dark patch in radar might be oil — prove it, place it, trace it |
| 2 | Overview | Command Centre `/` | 0:35 | Ten stages, one command, one image, ~20 s, offline |
| 3 | Detection | Satellite `/imagery` | 0:50 | The reader is ours; the U-Net is ours, in NumPy |
| 4 | Measure + screen | Slick `/slick` | 0:45 | Real km² on a sphere, then *is it even oil?* |
| 5 | Drift + age | Drift `/drift` | 1:05 | Where it came from — and what we refuse to claim |
| 6 | Attribution | Vessels `/vessels` | 0:55 | Ten ships to two, priority candidate — never "guilty" |
| 7 | Evidence | Method `/method` | 0:55 | What it was tested on, and the leak we caught ourselves |
| 8 | Close | card | 0:20 | 905 tests, zero dependencies, all offline |

Target ~6:00. The two moments you must never cut: the **forcing refusal** (§5) and the
**split-protocol leak** (§7) — they separate you from a team that just reports a good number.

---

## 17.3 Segment 0 — Title · 0:00–0:08

`[ SCREEN ] Title card: project name "SpillTrace", one-line tagline, your team + institution, "AICTE / SIH 2026". Hold static.`

> SpillTrace — an offline pipeline that takes one Sentinel-1 radar image and finds the oil spill
> in it, measures it, traces where it came from, and ranks the ships that could be responsible.

---

## 17.4 Segment 1 — The problem · 0:08–0:28

`[ SCREEN ] A single greyscale SAR scene (the imagery screen's raster, or a still). Let it sit.`

> Satellites see the ocean day and night through cloud, using radar. Oil flattens the sea, so it
> shows up as a dark patch. The catch: low wind, algal blooms and ship wakes look dark too. So the
> real problem isn't just *spot the dark patch* — it's prove it's oil, put real numbers on it, work
> out where it drifted from, and point to who was there. That's the whole pipeline you're about to
> watch.

---

## 17.5 Segment 2 — Overview · 0:28–1:03

`[ SCREEN ] The app open on the Command Centre. Case picker reads 00223 · demo. Don't scroll yet.`

> This is one Sentinel-1A radar image — dual-polarisation, **2048 by 2048** pixels, taken over the
> Central Mediterranean about 20 kilometres off Malta. One command runs the whole pipeline, and
> everything on these screens comes out of that single run.

`[ ACTION ] Unfold the Processing status card — the ten per-stage bars appear.`

> It runs in **ten stages** and takes about **20 seconds** on a laptop — no GPU, nothing over the
> network. Reading the bars is a map of the system: **decode** the radar format, then **detect**
> with the neural network, then geometry, ocean physics, and vessel scoring. Let me walk the image
> through it.

---

## 17.6 Segment 3 — Detection · 1:03–1:53

`[ SCREEN ] Click Satellite analysis.`

> Stage one, **decode**. The data ships in ESA's BEAM-DIMAP format. We wrote the reader ourselves —
> no GDAL, no rasterio — pulling the VV and VH radar bands and the corner coordinates, so every
> pixel has a real latitude and longitude.

`[ ACTION ] In the viewer, switch band VV → VH, then overlay None → Prediction → Reference → Both.`

> Stage two, **detect** — this is the U-Net. Two input channels, four levels deep, **1.96 million
> parameters**. And the part I want to be clear about: it's written in **NumPy**, with the forward
> *and* the backward pass hand-written. There's no PyTorch here. It trained in under **18 minutes**
> on a laptop CPU.

`[ ACTION ] Scroll to All layers. Point at Model probability, then Predicted mask, then Reference mask, then Agreement.`

> It outputs a probability per pixel — not a mask. We cut it at a threshold we chose on validation
> data, never on the test set. Green is where we agree with the dataset's own reference mask; amber
> is where we called oil and it didn't.

---

## 17.7 Segment 4 — Measure and screen · 1:53–2:38

`[ SCREEN ] Click Slick analysis.`

> Stage three, **geometry**: **12 disconnected regions, 26.9 square kilometres** of oil, the
> largest **15.76**. And these are *real* square kilometres — integrated on a sphere, because a
> degree of longitude is shorter at 36 degrees north than at the equator. Count pixels and multiply
> and you get the wrong answer; every number downstream depends on getting this right.

`[ ACTION ] Scroll to the look-alike screening card.`

> Stage four, **screening** — the stage most teams don't have. Because a dark patch isn't
> necessarily oil, after the U-Net we run a second, independent check: it scores each dark region on
> **seven** shape-and-contrast features you can name. On its own, darkness against the surrounding
> water separates oil from look-alikes at **0.948 AUC**.

---

## 17.8 Segment 5 — Drift and age · 2:38–3:43

`[ SCREEN ] Click Drift reconstruction. Unfold the Forcing card.`

> Stage five, **forcing** — the ocean we drift the oil through, and the thing I'm proudest of. We
> have a real ocean-current product. The code checked it, found its nearest timestep was about
> **eleven years** from this image, and **refused it** — falling back to synthetic forcing and
> labelling itself **"Synthetic scenario data"** right on the card. Most code would have interpolated
> silently and given a confident, wrong answer.

`[ ACTION ] Click Backward. Let the particle cloud draw.`

> Stage six, **backward**: **300 particles** released from the slick and run *backwards* 24 hours to
> find the origin — with a land mask so they stop at the coast, and windage because oil is pushed by
> wind as well as current. Same seed, reproduces bit for bit.

`[ ACTION ] Scroll to Estimated spill age. Slow down.`

> The problem statement asks for the spill's age "if feasible." We measured whether we actually can:
> over the window, the estimated position moves less than its own uncertainty. So we print **"not
> resolvable"** and show the arithmetic. One image can't do it; a second pass could. We'd rather say
> that than print a number we can't defend.

`[ ACTION ] Click Forward.`

> Stage seven, **forward** — the same physics run forwards: where the oil is heading, and how many
> particles beach. That's the output an operator actually acts on.

---

## 17.9 Segment 6 — Attribution · 3:43–4:38

`[ SCREEN ] Click Vessel attribution.`

> Stage eight, **AIS**: **1,112 position reports from 10 vessels**. These are synthetic — it says so
> on screen — but written in the exact **MarineCadastre** schema the problem statement names, so
> swapping in real traffic is a file path, not a rewrite.

`[ ACTION ] Open Traffic filtering. Point at each number as you say it.`

> Stage nine, **scoring** — filtering out the irrelevant traffic. Here's the funnel, and it adds up
> in public: **1,112 reports, 10 vessels. Nine** were in the release window, **two** also came near
> where the oil is estimated to have been, and **eight** are excluded. We keep all eight on screen,
> dimmed and still scored, so the filter can be audited — filtering means separating, not deleting.

`[ ACTION ] Scroll to the top-ranked vessel with its score bars.`

> The top candidate scores **90.3 out of 100**, and every component prints the sentence that earned
> it. But read the label: **"Priority candidate for investigation."** That's the strongest phrase
> anywhere in this product — and there's a test that keeps it that way. We rank who to ask first. We
> never say who did it.

---

## 17.10 Segment 7 — Evidence · 4:38–5:33

`[ SCREEN ] Click Methodology and evidence. Unfold Split protocol.`

> Last screen — the numbers that aren't flattering. Our scenes are crops of larger acquisitions, and
> if two crops of the same acquisition land on opposite sides of the train/test split, the model has
> effectively seen the test set. We found that leak **in our own evaluation**, fixed it, added a
> regression test, and retrained. Now it's **169 / 36 / 35** acquisitions, grouped by parent product,
> with **no acquisition in two splits**.

`[ ACTION ] Scroll to accuracy, then the baseline card.`

> On **35 held-out full scenes**, mean per-scene **IoU 0.693** — it finds most of the oil and
> over-calls at the edges, which for a screening tool is the right direction to be wrong in. And
> against a classical threshold baseline, the model wins by **+0.093**. Before we fixed the leak that
> margin was nearly double — we publish the lower number, because it's the true one.

---

## 17.11 Segment 8 — Close · 5:33–5:53

`[ ACTION ] Click back to the Command Centre. Then cut to an outro card.`

> One radar image, ten stages, twenty seconds: segment it, measure it, check it isn't a look-alike,
> run the ocean backwards to find the source and forwards to find where it's going, then score the
> traffic against that zone and time.
>
> **905 tests pass.** The interface is under half a megabyte of plain JavaScript with **zero
> dependencies** and no build step, and everything you just watched ran **offline**, on a laptop,
> from one case file. The AIS and the ocean forcing are synthetic and labelled as such — everything
> else is real.

`[ SCREEN ] Outro card: project name, team, "github / demo link", thank-you.`

---

## 17.12 Timing

At ~145 words a minute. Spoken words only; clicks and map draws add ~30–45 s across the video.

| Segment | Target |
|---|---|
| 0 Title | 0:08 |
| 1 Problem | 0:20 |
| 2 Overview | 0:35 |
| 3 Detection | 0:50 |
| 4 Slick | 0:45 |
| 5 Drift | 1:05 |
| 6 Vessels | 0:55 |
| 7 Method | 0:55 |
| 8 Close | 0:20 |
| **Full** | **~6:00** |

**Cut to ~3:30** by dropping Segment 1, the layer walk in §3, and the whole of §4 (screening returns
implicitly nowhere else, but it's the most droppable). Keep the forcing refusal and the split leak.

---

## 17.13 What not to do on camera

| Don't | Do |
|---|---|
| Guess a number you can't see | Say the sentence without it. Never a figure you can't point to. |
| Call the AIS or forcing "real" | They're synthetic and labelled — say so, it's a strength. |
| Say a vessel is guilty or responsible | "Priority candidate for investigation." |
| Apologise if a map is slow | "It renders on demand" — give it a beat, or re-record the shot. |
| Read the §14.9 lookup tables aloud | Those are for question time, not the video. |

---

Previous: **[document 14 — the live demo walkthrough](14-THE-DEMO-WALKTHROUGH.md)** — the full
spoken version this video is condensed from.
Related: **[document 13 — the two-minute speech](13-THE-TWO-MINUTE-SPEECH.md)** and
**[document 10 — the UI walkthrough](10-UI-WALKTHROUGH.md)**.
