# 14 — The demo walkthrough, spoken while you click

**What this is.** The script for the live demo: what you say while you drive the interface, screen
by screen, so the architecture gets explained *by* the UI instead of alongside it. Document 13 is
the two-minute opener you say before you touch anything. **This is what comes after "let me show
you."**

**How to read it.** Lines in `[ CLICK ]` are stage directions — do the thing, don't say it. Lines
in blockquote are the words. Everything in **bold** inside a blockquote is a number that is on the
screen at that moment; if it isn't on screen, don't say it. **There is exactly one exception**, the
dataset-provenance paragraph in §14.8, and it is flagged where it occurs.

**The one structural idea that makes this work:** each screen *is* one stage of the pipeline, in
order. You are not showing six pages, you are walking the data through ten stages and the page
happens to follow. Say the stage name as you arrive at it.

**§14.9 is a lookup table, not a script** — the dataset, split, training and evaluation figures laid
out for question time. Don't read it aloud; know where it is.

**Every number here is real** — read off `data/processed/cases/demo.json`, `scene_metrics.json`,
`metrics.json`, `lookalike_metrics.json`, `splits.json`, `cache_manifest.json` and `audit.json`, and
every click cue was checked against the running interface on 2026-09-12. If you change the pipeline,
regenerate and re-read them (§14.13).

---

## 14.1 Before you click

Set up so nothing surprises you mid-sentence:

- Case picker shows **`00223 · demo`**. That is the only case in the store.
- Start on **Overview** (`#/`). Browser at desktop width, not a phone window.
- Serve the built copy — `dist/` — so the demo runs with no API and no network.
- The header reads **Offline demo**. If a judge notices, that is a feature: say *"there is no
  server behind this; it is the case file and the interface."*

**Know this before you start:** the app is offline by design, so the API calls fail and fall back to
the bundled case. That is the intended path, not a broken demo.

---

## 14.2 The spine

Memorise this table, not the script. If you know the six arrivals you can rebuild the words.

| Screen | Pipeline stage | Time | The one thing it must land |
|---|---|---|---|
| **1 Overview** | the whole run | 20.1 s | Ten stages, one command, one image in. |
| **2 Imagery** | `decode` → `detect` | 9.7 s + 5.7 s | The reader is ours; the U-Net is ours. |
| **3 Slick** | `geometry` → `screening` | 1.9 s + 1.8 s | Real km² on a sphere, then *is it even oil?* |
| **4 Drift** | `forcing` → `backward` → `forward` | 0.03 + 0.26 + 0.25 s | Where it came from — and what we refuse to claim. |
| **5 Vessels** | `ais` → `scoring` | 0.19 s + 0.08 s | Ten ships to two, with the eight kept and reasoned. |
| **6 Method** | evaluation | — | What it trained on, what it was tested on, and the leak we found in our own numbers. |

---

## 14.3 Screen 1 — Overview · *"this is the whole run"*

`[ CLICK ] Overview. Stay here while you say all of this. Don't scroll yet.`

> So this is the workflow. One Sentinel-1 radar image goes in — this one is **Sentinel-1A**, IW
> mode, dual polarisation, **2048 by 2048** pixels, taken on **4 August 2015** over the Central
> Mediterranean, about 20 kilometres off Malta. One command runs the pipeline and everything on
> these six screens comes out of that one run.
>
> It runs in **ten stages** and takes **20.1 seconds** on a laptop. No GPU, nothing over the
> network.

`[ CLICK ] Processing status — unfold it. The ten per-stage bars appear.`

> These are the ten stages with the time each one actually took, measured on the run that produced
> this case. Reading it is a decent map of the architecture: **decode** at 9.7 seconds is the
> single most expensive thing we do, then **detect** — that's the neural network — at 5.7.
> Everything after those two, all the physics and the vessel scoring, is under three seconds
> combined.
>
> The code is four Python packages. `spilltrace_common` reads the data formats.
> `spilltrace_ml` is the model and the geometry. `spilltrace_drift` is the ocean physics and the
> vessel scoring. `spilltrace_api` serves it. Let me walk the image through them.

**On "off Malta":** the scene's corner coordinates are real and on screen — 14.48–14.66 °E,
35.79–35.97 °N — but the label *Central Mediterranean* is, in the pipeline's own words, "an
approximate offline label, not a gazetteer lookup." Say *"about 20 kilometres off Malta"* as
orientation, not as a geocoded fact. If pushed: *"we derive that from the corner coordinates; we
don't run a place-name lookup offline."*

**If you are short on time, this is the screen to cut to 20 seconds.** Say the first paragraph,
skip the stage bars, go to Imagery.

---

## 14.4 Screen 2 — Satellite analysis · *`decode` → `detect`*

`[ CLICK ] Imagery.`

> Stage one, **decode**. The dataset ships as BEAM-DIMAP — that's ESA's format, an XML header and
> raw binary blobs. We wrote the reader. There's no GDAL here, no rasterio: `dimap.py` parses the
> header, pulls the VV and VH bands out, and reads the corner coordinates so every pixel has a real
> latitude and longitude. We also wrote readers for GeoTIFF and for NetCDF-4, which is HDF5
> underneath, because the ocean data comes in that.
>
> That's 9.7 seconds of the 20, and it's honest work — decoding four megapixels across two bands.

`[ CLICK ] In the Viewer: switch the band VV → VH, then the overlay None → Prediction → Reference → Both.`

> Stage two, **detect**. This is the U-Net. Two input channels — VV and VH — four levels deep,
> starting at 16 channels and doubling: 16, 32, 64, 128, with a 256-wide bottleneck. **1.96 million
> parameters.** Batch norm, ReLU, nearest-neighbour upsampling with concatenated skip connections.
>
> It's written in NumPy. The forward pass and the backward pass are both hand-written — there's no
> PyTorch in this repository. It trained in **17.8 minutes** on a laptop CPU, **18 epochs**, batch
> size 8, on a half-Dice half-cross-entropy loss with the positive class weighted double, because
> oil is about one percent of the pixels.

`[ CLICK ] Scroll to All layers. Point at Model probability, then Predicted mask, then Reference mask.`

> This is what it actually outputs — **Model probability**, one number per pixel between 0 and 1,
> not a mask. The **predicted mask** below it is that field cut at **0.7**, and we chose 0.7 on the
> validation scenes, not the test ones. Then the dataset's own **reference mask**, and an
> **agreement** layer underneath: green where we agree, amber where we called oil and the reference
> didn't.

---

## 14.5 Screen 3 — Slick analysis · *`geometry` → `screening`*

`[ CLICK ] Slick.`

> Stage three, **geometry**. **12 disconnected regions, 26.9 square kilometres** of oil in total,
> the largest one **15.76**. Perimeter **42.7 kilometres**, length **11.5**, width **4.2**.
>
> The detail that matters: those are real square kilometres, integrated row by row on a sphere of
> radius 6,371 kilometres. A degree of longitude is shorter at 36° north than at the equator, so if
> you count pixels and multiply you get the wrong answer. Every number downstream depends on this
> one being right.

`[ CLICK ] Scroll to the look-alike screening card.`

> Stage four, **screening**, and this is the stage most teams don't have. A dark patch in radar is
> not necessarily oil. Low wind makes the sea glassy and it goes dark. Algal blooms go dark. Ship
> wakes go dark.
>
> So after the U-Net we run a second, independent check. It proposes dark regions from the scene's
> own statistics — anything more than one standard deviation below the local water — and then
> scores each one on **seven shape-and-contrast features**. Not a network: seven measurements you
> can name, weighted. The strongest single one is darkness against the surrounding water, which
> alone separates oil from look-alikes at **0.948 AUC**.
>
> On this scene it proposed **12 dark patches** and rejected **none** — the patches here are
> genuinely oil-like, and **four** of the published slicks have a patch it positively accepted.
> Three came back *uncertain*, and we keep those and say so rather than forcing a call.

**The honest framing, if a judge pushes:** *"zero rejected is not the screen failing — this scene is
a real spill, so there was nothing to throw out. The number that shows it works is on the Method
screen: on an independent archive it rejects 69% of dark regions."*

---

## 14.6 Screen 4 — Drift reconstruction · *`forcing` → `backward` → `forward`*

`[ CLICK ] Drift.`

> Stage five, **forcing** — the ocean we drift the oil through. And I want to show you what the
> pipeline did here, because it's the thing I'm proudest of.

`[ CLICK ] Forcing — unfold it.`

> We have a real CMEMS current product. The code checked whether it covers this scene, and it does,
> in space. Then it checked time. The product's nearest timestep is **3,975 days** from this
> acquisition — about eleven years — against a tolerance of 24 hours. So it **refused it** and fell
> back to synthetic forcing, and it labels itself **"Drift forcing: Synthetic scenario data"** right
> there on the card.
>
> Most code would have interpolated silently and produced a confident, wrong answer.
>
> The synthetic field is still real physics: it's the curl of a streamfunction, which makes it
> analytically non-divergent — mathematically incapable of pooling the oil up on its own. We test
> that numerically. If we didn't, the flow itself would concentrate the slick and every area number
> would be an artefact.

`[ CLICK ] Backward. Let the particle cloud draw.`

> Stage six, **backward**. **300 particles**, released from the observed slick and run *backwards*
> 24 hours — 48 steps of 30 minutes, midpoint integration, with diffusion at 8 m²/s, a land mask so
> particles stop at the coast instead of drifting through Malta, and a **3% windage** factor,
> because oil on the surface is pushed by wind as well as current. Seed **26143** — the same run
> reproduces bit for bit.
>
> That gives the origin: **14.52 east, 35.89 north**, with a **4.2 kilometre** radius at P50 and
> **9.6** at P90. And a release window: some time in the 24 hours before the image.

`[ CLICK ] Estimated spill age. Slow down here.`

> Now the part I want you to hold me to. The problem statement asks for the age of the spill *"if
> feasible."* The tempting move is to print 12 hours and call it done.
>
> We measured whether we can actually tell one end of that window from the other. Over the whole
> hindcast the estimated position moves **6.6 kilometres**, and the uncertainty around it is **9.6
> kilometres**. The signal is **0.68** of the error bar. The whole window sits inside its own
> uncertainty.
>
> So we print **"not resolvable"** and show the arithmetic. One image can't do it. A second
> acquisition would, and that's a procurement decision, not a modelling one.

`[ CLICK ] Forward.`

> Stage seven, **forward** — the same integrator, same forcing, run forwards instead. 300
> particles, **21 beach** on Malta, none leave the domain. That's the operational output: where to
> send the response boat.

---

## 14.7 Screen 5 — Vessel attribution · *`ais` → `scoring`*

`[ CLICK ] Vessels.`

> Stage eight, **AIS**. **1,112 position reports from 10 vessels.** These are synthetic — it says
> so right there under the number — because we don't have a licensed live feed. But they're written
> in the exact **17-column MarineCadastre schema** the problem statement names as the format
> authority, and you can download the CSV from this app and diff it against a real daily extract.
> Swapping in real traffic is a file path, not a rewrite.
>
> Stage nine, **scoring**, and this is requirement (c): *the irrelevant traffic is to be filtered
> out.*

`[ CLICK ] Traffic filtering. Point at each number as you say it.`

> Here's the funnel, and the numbers add up in public. **1,112 reports, 10 vessels. Nine** have
> reports inside the release window. **Two** also come within three envelope radii of where the oil
> is estimated to have been. **Eight** are excluded as irrelevant.
>
> And the two exclusion reasons mean completely different things. **Seven** vessels were in the
> window but **33 to 73 kilometres** away — between 3.7 and 8.5 envelope radii — those mean *look at
> a different ship*. **One** passed within **1.8 kilometres** but had no reports inside the window:
> right place, wrong time. That one isn't excluded by distance, it's excluded by the width of our
> own release window — and tightening that window is a forcing-data problem, not a scoring problem.
>
> We keep all eight, dimmed and still scored, so the filter itself can be audited. Filtering means
> separating, not deleting.

`[ CLICK ] First in the queue — the top-ranked vessel, with its score bars.`

> The correlation is spatio-*temporal*, not a circle on a map: every AIS report is compared against
> where the oil is estimated to have been **at that report's own timestamp**, in units of the
> uncertainty radius at that timestamp. Which is why "present somewhere during a 24-hour window"
> scores zero.
>
> Top candidate scores **90.3 out of 100** — proximity **30 of 30**, time **18.9 of 25**, trajectory
> **19.4 of 20**, behaviour **7 of 10**, type **10**, data quality **5**. And every component prints
> the sentence that earned it: *"passed 0.335 km from the drift envelope centre."*
>
> But read what it says: **"Priority candidate for investigation."** That's the strongest phrase
> anywhere in this product, and there's a test that keeps it that way. We rank who to ask first. We
> never say who did it. A false accusation against a named vessel is a diplomatic problem, not a
> software bug.

---

## 14.8 Screen 6 — Methodology · *the data, then the accuracy*

`[ CLICK ] Method.`

> Last screen, and this is where we keep the numbers that aren't flattering. Before I give you an
> accuracy figure, let me tell you what it was measured on — the number means nothing without it.

`[ CLICK ] Data provenance.`

> **The dataset is real and it's published.** Part I of a Sentinel-1 SAR oil-spill dataset on
> Zenodo, DOI **10.5281/zenodo.8346860**, released with a paper in *Marine Pollution Bulletin*.
> Not something we assembled ourselves.
>
> **1,200 scenes with 1,200 masks** — every image paired, none missing on either side. They come
> from **270 distinct Sentinel-1 acquisitions**, mostly Sentinel-1A with 124 files from 1B, all IW
> mode, spanning **March 2015 to October 2019**, across 24 seas. Every scene is **2048 × 2048**,
> two bands, VV and VH, in decibels.
>
> We decoded **240 of the 1,200**, taken round-robin across the parent acquisitions so a partial
> budget still covers every acquisition, region and date rather than over-sampling one product.
> Zero decode failures.

**⚠ This is the one paragraph in the whole script where you are not reading off the screen.** The
card shows *"1 200 scenes indexed"* and the provenance labels, and that's all. The DOI, the 270
acquisitions, the mission split and the date range are **not** on it — they live in
`data/processed/audit.json` and in [document 11](11-DATASETS.md). So say this one looking at the
panel, not pointing at it. If a judge asks to see the DOI, the honest answer is *"it's in the
dataset document and the audit file, not on this screen"* — and note that the **270** you can see on
this screen belongs to the look-alike card, which was fitted on all 270 parent products. Don't let
the two get conflated.

`[ CLICK ] Split protocol — unfold it.`

> Here's the part I'd want to hear if I were judging. Our scenes are crops of larger acquisitions.
> If two crops of the same acquisition land on opposite sides of the train/test split, the model has
> effectively seen the test set and the score is inflated.
>
> We found that bug **in our own evaluation.** The grouping wasn't reaching the splitter. We fixed
> it, added a regression test, and retrained from scratch.
>
> So: **240 acquisitions — 169 train, 36 validation, 35 test.** Grouped by parent Sentinel-1 product
> and assigned by a hash of the product ID, so it's deterministic, and **no acquisition appears in
> two splits.** Not one.

`[ CLICK ] Training.`

> **What we trained on.** From those scenes we cut **128-pixel patches** and cached **3,710 for
> training, 826 for validation, 794 for test** — each cache balanced half oil-bearing, half clean.
>
> Training itself used **1,800 of the 3,710, all oil-bearing**, because the cache holds more
> positives than the epoch budget. **18 epochs**, batch 8, **17.8 minutes** on a laptop CPU.
> Validation IoU rose from 0.667 at epoch 1 to **0.811 at epoch 17**, which is the checkpoint we
> kept.
>
> The **test** set is the mixed one, and that's the point: **600 patches — 397 with oil, 203 with
> none** — so it measures both whether we find oil and whether we hallucinate it on clean water.

`[ CLICK ] Accuracy, both ways round. Then Threshold sweep.`

> **What we tested on, and two numbers, because there are honestly two.**
>
> At **patch scale** — 128-pixel tiles, **600 test patches** — **0.769 IoU**, precision 0.834,
> recall 0.907. And of the 203 patches with no oil in them, **164 were left correctly empty**.
>
> At **scene scale** — the full 2048-pixel image, **35 held-out scenes** — **mean per-scene IoU
> 0.693**, pooled **0.584**, precision **0.628**, recall **0.893**.
>
> **Quote the scene number.** Patches are sampled around labelled oil, so patch scale flatters us.
> The scene figure describes what you'd get handed a new image. It finds most of the oil and
> over-calls at the edges — for a screening tool, that's the right direction to be wrong in.
>
> The two scales have **two operating points — 0.65 for patches, 0.70 for whole scenes** — and both
> were chosen on the **validation** set by mean per-scene IoU, then applied unchanged to test. We
> didn't tune a threshold on the thing we're reporting.

`[ CLICK ] Does the model beat a threshold?`

> **And is the neural network even earning its place?** We built a classical baseline to check —
> despeckled VV, thresholded at a calibrated decibel offset below clean water, morphological
> cleanup. That's how this was done before deep learning.
>
> Like-for-like at patch scale: **baseline 0.676, our model 0.769. A margin of +0.0935 — 13.8%
> relative.**
>
> Before we fixed the split leak that margin read **+0.189**. It halved. We publish the lower
> number, because it's the true one. And read what the card itself says: *"most of the way without a
> model."* A calibrated threshold gets most of the way there, and the improvement is the entire
> contribution of the network. That's worth knowing before deciding a network is required at all —
> and we'd rather be the team that says it than the team a judge says it to.

`[ CLICK ] Look-alike screening: dark water that is not oil.`

> And the look-alike screen, tested properly: the supplied dataset has no labelled look-alikes, so
> we scored against **2,290 published non-oil patches from an independent archive** — a different
> sea, nothing the model trained on.
>
> The U-Net **on its own** raises an alarm on **288 of 340** of them. That's the honest headline: a
> dark patch is not evidence of oil, and a segmentation model alone will tell you it is. With the
> screen in front of it we reject **69%** of **84,758** dark regions while keeping **90%** of real
> oil, at **0.957 AUC** held out, five-fold cross-validated and grouped the same way. Real
> improvement, not a solution — a third still survive.

⚠ **288 is the kinder of two numbers.** Those patches are 8-bit JPEG, so the brightness has to be
mapped back to decibels, and the file reports both mappings: 288 of 340 under one, **340 of 340
under the other**. Quote 288 — it is the conservative claim — but if a judge has
`lookalike_metrics.json` open and says "your file says 100%," agree at once: *"Yes — under the other
radiometric assumption it alarms on every one. We report both because one number would overstate
what an 8-bit archive can support."* That answer is stronger than the figure.

`[ CLICK ] Limitations, as recorded by the pipeline.`

> And these aren't ours, they're the pipeline's — the evaluation scripts write their own limitations
> into the metrics files, and the screen just reads them back.
> **Fourteen of them, across the three evaluations.** The one that matters most: **every supplied
> scene contains oil**, so these figures measure how well we outline a slick we already know is
> there. They are **not a false-alarm rate on clean sea.** And the reference masks are the dataset's
> own; their accuracy is unknown and we treat them as truth throughout.

**The one-sentence version, if a judge asks what you trained on and you have ten seconds:**
*"1,200 published Sentinel-1 scenes, 270 acquisitions; we decoded 240 and split them 169/36/35 by
parent product with zero overlap, trained on 1,800 patches of 128 pixels, and report 0.693 mean
per-scene IoU on 35 held-out scenes."*

---

## 14.9 The data and evaluation numbers, as a lookup

Not for reading aloud — for finding a figure fast when a judge interrupts.

**What we trained on**

| | |
|---|---|
| Source | Sentinel-1 SAR oil-spill dataset, Part I · Zenodo DOI `10.5281/zenodo.8346860` · *Marine Pollution Bulletin* |
| Supplied | **1,200 scenes, 1,200 masks**, 1,200 matched pairs, 0 unpaired either way |
| Acquisitions | **270** distinct parent products · Sentinel-1A 1,076 files, Sentinel-1B 124 · all IW mode |
| Date range | **2015-03-12 → 2019-10-30** |
| Scene size | **2048 × 2048**, 2 bands (VV, VH), float32 dB, LZW |
| Decoded | **240 of 1,200**, round-robin across parents so every acquisition/region/date is represented · **0 failures** |
| Patch size | **128 px**, stride 128, ≥0.2% oil to count as positive, 1 negative per positive, ≤24 patches/scene |

**The split** — grouped by parent product, assigned by SHA-256 hash of the group key, seed 20180803

| Split | Acquisitions | Patches cached | Positive / negative | Used in training | Oil pixel fraction, as used |
|---|---|---|---|---|---|
| Train | **169** | **3,710** | 1,855 / 1,855 | **1,800** — positives only | 38.3% |
| Validation | **36** | **826** | 413 / 413 | **400** — positives only | 37.6% |
| Test | **35** | **794** | 397 / 397 | **600** — all 397 positives + 203 clean | 25.6% |
| | **240 total** | | | | **no acquisition in two splits** |

**Read that table carefully before you quote it.** The cache is balanced 50/50; *training* drew
positives only, because the cache holds more oil-bearing patches than the epoch budget. The **test**
set is the mixed one — 397 with oil, 203 without — which is what makes "164 of 203 clean patches
left correctly empty" a meaningful statement.

**Training**

| | |
|---|---|
| Architecture | U-Net, depth 4, 16→32→64→128 encoder, 256 bottleneck, **1,963,953 parameters** |
| Framework | NumPy, hand-written forward **and** backward pass |
| Loss | 0.5 × Dice + 0.5 × BCE, positive class weighted ×2 |
| Schedule | **18 epochs**, batch 8, LR 0.002 → 5% of it, weight decay 1e-5, grad clip 5.0, patience 5, augmentation on |
| Cost | **17.8 minutes**, laptop CPU, seed 1337 |
| Best epoch | **17** — val IoU **0.811**, val Dice 0.896, precision 0.868, recall 0.926 |
| Trajectory | validation IoU 0.667 at epoch 1 → 0.805 at epoch 18, best at 17 |

**Evaluation — two scales, two thresholds, and they differ**

| | Patch (128 px) | Scene (2048 px) |
|---|---|---|
| Threshold | **0.65** | **0.70** |
| Test set | **600 patches** (397 oil, 203 clean) | **35 scenes** |
| IoU | **0.769** | pooled **0.584** · **mean per-scene 0.693** |
| Dice | 0.869 | pooled 0.737 · mean 0.793 |
| Precision | 0.834 | **0.628** |
| Recall | 0.907 | **0.893** |
| Also | 164 of 203 clean patches left correctly empty · per-patch median 0.814 | **worst 0.046 · median 0.765 · best 0.962** — the three figures under the strip |

Both thresholds were chosen on **validation** by mean per-scene IoU and applied unchanged to test.
**Quote the scene number** — patches are sampled around labelled oil, so patch scale flatters.

**Baseline** — `darkspot-vv-v1`: despeckled VV, calibrated dB offset below clean water, morphological
opening, minimum-area filter

| | |
|---|---|
| Baseline test IoU | **0.6756** |
| Model test IoU | **0.7690** |
| Margin | **+0.0935 · 13.8% relative** · was +0.189 before the split-leak fix |
| The card's own verdict | *"Most of the way without a model."* The improvement is the entire contribution of the network. |

**Look-alike screening**

| | |
|---|---|
| Fitted on | **11,623** dark regions from **270** scenes — 930 oil, 10,693 not · 336 ambiguous dropped, not guessed |
| Features | **7** shape-and-contrast measures · strongest alone: darkness vs local water, **0.948 AUC** |
| Held out | 5-fold, grouped by parent product — **AUC 0.957**, keeps **90.2%** of oil, rejects **89.4%** of look-alikes |
| Cross-domain | DARTIS 2019 (PANGAEA `doi:10.1594/PANGAEA.980773`) — **2,290** non-oil patches, **84,758** dark regions, **69.4%** rejected |
| Bare U-Net | alarms on **288 of 340** look-alike patches (84.7%) under one brightness mapping, **340 of 340** under the other — the reason the screen exists |

**The limitations the pipeline records about itself** — **14 of them across 3 evaluations**
(patch-scale, scene-scale, look-alike). The four that matter most in a demo:

1. Every supplied scene contains oil — these measure **delineation, not a false-alarm rate on clean sea**.
2. The dataset has **no labelled look-alikes**, which is why the cross-domain test above exists.
3. Scene IoU is well below patch IoU **because the patch sampler never visited most of each frame** — the scene figure is the one that reflects operating on a full acquisition.
4. The reference masks are the dataset's own; **their accuracy is unknown** and treated as truth.

---

## 14.10 The close

`[ CLICK ] Back to Overview.`

> So: one radar image, ten stages, twenty seconds. Segment it, measure it, check it isn't a
> look-alike, run the ocean backwards to find where it came from, forwards to find where it's
> going, then score the traffic against that zone and window.
>
> **776 tests pass.** The interface is **396 kilobytes** of plain JavaScript with **zero
> dependencies** and no build step. Everything you just watched ran offline, on this laptop, from a
> bundled case file.
>
> The AIS is synthetic and the forcing is synthetic, both labelled on the card that reports them.
> Everything else — the imagery, the model, the physics, the geometry — is real.

---

## 14.11 Timing

Measured at 145 words a minute, which is an unhurried demo pace.

| Section | Words | Time | Cut to |
|---|---|---|---|
| 14.3 Overview | 177 | 1:13 | 0:25 — first paragraph only |
| 14.4 Imagery | 257 | 1:46 | 1:00 — drop the reader paragraph |
| 14.5 Slick | 231 | 1:36 | 0:50 — drop screening, it returns on Method |
| 14.6 Drift | 405 | 2:48 | 1:40 — drop the forward stage |
| 14.7 Vessels | 382 | 2:38 | 1:30 — drop the exclusion-reasons paragraph |
| 14.8 Method | 868 | 5:59 | **2:00 — see below** |
| 14.10 Close | 105 | 0:43 | 0:20 |
| **Full** | **2,425** | **16:44** | — |
| **Cut** | ~1,020 | **~7:00** | — |

Those are spoken words only — stage directions and the bolded asides aren't counted, but the clicks
add roughly 30–45 seconds across the walk and maps take a beat to draw. **Budget 17–18 minutes for
the full version**, which is longer than most demo slots. Plan a cut.

**Cutting Method from 5:59 to 2:00.** It's now by far the longest section, and most of it is depth
you want *available* rather than *delivered*. Say four things and stop:

1. **The provenance sentence** — "1,200 published Sentinel-1 scenes, 270 acquisitions, we decoded
   240." Ten seconds, and it pre-empts "where did the data come from?"
2. **The split protocol in full.** Never cut this one.
3. **Two accuracy numbers only** — 0.693 mean per-scene on 35 held-out scenes, and +0.093 over the
   baseline with the note that it halved after the leak fix.
4. **The bare-U-Net look-alike figure** — 288 of 340.

Leave the training hyperparameters, the patch-scale table and the limitations for questions.
They're in §14.9 as a lookup for exactly that.

**If you have 6 minutes:** Overview short → Imagery short → skip Slick → Drift with the age card in
full → Vessels with the funnel → Method with the split protocol → close. The two moments you must
never cut are **the forcing refusal** (§14.6) and **the split-protocol leak** (§14.8). They are
what separate you from a team that just reports a good number.

**If you have 3 minutes:** don't walk the UI at all. Say document 13's speech and open Drift on the
age card.

---

## 14.12 The five things that will go wrong

| If | Say |
|---|---|
| A map doesn't draw | *"It renders on demand — one second."* Click the screen again. Don't apologise twice. |
| A judge sees "Offline demo" | *"There's no server behind this. That's the point — it runs from the case file."* |
| Someone asks for a live image | *"Not through the browser yet. You drop the scene on the machine and the case appears in a few minutes."* |
| You blank on a number | Say the sentence without it. **Never guess a figure in front of a panel.** |
| You're being rushed | Jump to §14.6 age card and §14.8 split protocol. Those two, then close. |

---

## 14.13 Regenerating these numbers

```bash
.venv/bin/python scripts/run_api.py --build-demo && .venv/bin/python scripts/build_web.py
```

Then re-read the figures this script quotes:

```bash
.venv/bin/python -c "import json; d=json.load(open('data/processed/cases/demo.json')); t=d['timing']; print('total', t['totalSeconds']); [print('  ', s['stage'], s['seconds'], s['note']) for s in t['stages']]; s=d['slick']; print('slick', s['slickCount'], s['totalAreaKm2'], s['areaKm2']); c=d['screening']['counts']; print('screening', c); a=d['spillAge']['resolution']['bestSeparation']; print('age', a); f=d['attribution']['filtering']; print('funnel', f['aisReports'], f['vesselsSeen'], f['vesselsInWindow'], f['vesselsRelevant'], f['excludedTooFar'], f['excludedOutsideWindow']); print('top', d['attribution']['candidates'][0]['score'], d['attribution']['candidates'][0]['components'])"
```

```bash
.venv/bin/python -c "import json; m=json.load(open('data/processed/scene_metrics.json')); t=m['test']['atSceneThreshold']; print('pooled', t['pooled'], 'scenes', t['scenes'], 'meanSceneIou', t['meanSceneIou'], 'median', t['medianSceneIou'], 'worst', t['worstSceneIou']); l=json.load(open('data/processed/lookalike_metrics.json')); cv=l['sameDomain']['crossValidation']['pooledHeldOut']; print('auc', cv['auc'], 'kept', cv['keptSensitivity'], 'spec', cv['rejectionSpecificity']); print('cross', l['crossDomain']['screen']['regions'], l['crossDomain']['screen']['regionRejectionRate']); [print('bare unet', k, v['patchesWithAlarm'], 'of', v['patches']) for k,v in l['crossDomain']['detector']['lookAlikes'].items()]"
```

And the dataset, split and training figures in §14.9, which come from the audit, the cache manifest
and the training metrics rather than from the case file:

```bash
.venv/bin/python -c "import json; a=json.load(open('data/processed/audit.json')); print('audit  ', a['counts'], a['acquisitions']['missions'], a['acquisitions']['earliestUtc'], '->', a['acquisitions']['latestUtc'], '| parents', a['acquisitions']['distinctParentProducts']); c=json.load(open('data/processed/cache_manifest.json')); print('cache  ', 'budget', c['sceneBudget'], 'available', c['pairsAvailable'], 'decoded', c['scenesDecoded'], 'failures', len(c['failures']), '| patch', c['config']['patch_size']); s=json.load(open('data/processed/splits.json')); print('splits ', s['counts'], 'seed', s['seed']); m=json.load(open('data/processed/metrics.json')); print('cached ', {k:v['patches'] for k,v in m['protocol']['cacheSplits'].items()}, '| used', {k:v['used'] for k,v in m['dataUsage'].items()}, '| oilFrac', {k:round(v['oilPixelFraction'],4) for k,v in m['dataUsage'].items()}); print('model  ', m['model']['parameters'], 'params |', m['trainConfig']['epochs'], 'epochs in', round(m['trainingSeconds']/60,1), 'min | best epoch', m['bestEpoch']['epoch'], 'valIoU', round(m['bestEpoch']['valIou'],4)); print('patch  ', {k:round(v,4) for k,v in m['test'].items() if k in ('iou','dice','precision','recall')}, m['testPerPatch']); print('base   ', m['comparison']); sm=json.load(open('data/processed/scene_metrics.json')); print('thresh ', 'patch', sm['patchThreshold'], 'scene', sm['sceneThreshold'])"
```

---

Previous: **[document 13 — the two-minute speech](13-THE-TWO-MINUTE-SPEECH.md)** — say that first,
then this.
Related: **[document 10 — the UI walkthrough](10-UI-WALKTHROUGH.md)** for what every card on every
screen is, and **[document 5](05-EXPLAINING-TO-JUDGES.md)** for the hard questions.
