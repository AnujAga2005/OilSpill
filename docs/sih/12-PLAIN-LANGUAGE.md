# 12 — The whole project in plain language

**Read this one first.** It assumes you know AI and the MERN stack and nothing about satellites,
oceanography or this codebase. Every technical word is explained the first time it is used, in
italics right after it. It does not replace the other documents — it makes them readable.

If you have one hour before you present, read this document and then
[document 9](09-ARCHITECTURE-FOR-PRESENTING.md). If you have twenty minutes, read §12.2 and §12.9.

---

## 12.1 What the project is, in one paragraph with no jargon

Somebody dumps oil into the sea. A satellite flies over and photographs the water. The photograph
comes back to us as a file. We run a program on it that finds the oil in the picture and tells you
how big it is. Then we run a second program that runs the clock **backwards** to work out roughly
where the oil probably leaked from and roughly when — and a third that runs the clock **forwards** to
say where it is going next. Finally we look at the ships that were moving around in that area at
that time and **rank** them — this one first, then this one — so a coastguard knows who to phone
first. That's it. That's the whole product.

Everything else in these documents is detail about how we do those five things and how honest we are
about what we don't know.

---

## 12.2 The vocabulary, explained like you're new

You will hear these words on stage. Learn them here so they don't ambush you.

### Words about the satellite

| Word | What it actually means |
|---|---|
| **SAR** | *Synthetic Aperture Radar.* A satellite that sends out radio waves and listens for the echo instead of taking a photograph with light. The important part: it works at night and through clouds, which a normal camera can't. That matters for oil spills because they don't wait for good weather. |
| **Sentinel-1** | The name of the actual European satellites we use — Sentinel-1A and Sentinel-1B. They're free, they're public, and anyone can download their data. |
| **Backscatter** | *How much of the radio wave bounced back.* Rough sea (with little waves on it) bounces a lot back — it looks **bright**. Oil makes the sea surface smooth, so it bounces almost nothing back — it looks **dark**. **That is the entire trick of the project: oil looks like a dark patch in a radar picture.** |
| **VV and VH** | Every SAR image actually comes as two images, taken with the radio waves polarised two different ways. Think of it like taking the photo through two different filters. VV is *co-polarised* (sent one way, received the same way) and it's the one where oil shows up darkest. VH is *cross-polarised* and it's noisier but gives extra information. Our model looks at both at once. |
| **dB (decibel)** | Just a unit for measuring backscatter, on a logarithmic scale. See §12.5 — why we convert to it is the single best technical answer you have. |
| **GeoTIFF, NetCDF, HDF5** | Three file formats. A GeoTIFF is an image file that also records *where on Earth* each pixel is. NetCDF and HDF5 are formats scientists use for big multi-dimensional data like ocean currents over time. We had to write our own readers for these — see §12.6. |
| **EPSG:4326** | The standard numbering system for map coordinate systems. `4326` means plain latitude/longitude. All our data uses it. |

### Words about the AI

| Word | What it actually means |
|---|---|
| **Segmentation** | An AI task where you label **every pixel** rather than the whole image. Not "this picture contains oil" but "this pixel is oil, that pixel is water". |
| **U-Net** | The standard shape of network for segmentation. It first *shrinks* the image while figuring out what's in it, then *grows* it back up while deciding, pixel by pixel, what each one is. The "skip connections" that link the shrinking part to the growing part are why it keeps sharp edges. Nobody on the judging panel will be impressed that we used a U-Net — it's from 2015. What's impressive is that **we wrote it ourselves** — see §12.6. |
| **Training / held-out data** | Training is showing the model examples so it learns. Held-out data is examples it has **never seen**, used to test it. If you test on data the model trained on, your score is meaningless — it's like giving a student the exam paper the night before. |
| **Overfitting** | When a model memorises its training data instead of learning the general pattern. It looks brilliant in training and is useless in the real world. This is why held-out data exists. |
| **IoU** | *Intersection over Union.* The standard score for segmentation. Take the area the model painted as oil, take the area that was actually oil, and see how much they overlap. 1.0 is perfect, 0 is no overlap at all. **Our score is 0.769 on held-out data.** |
| **Dice** | A very similar overlap score to IoU, calculated slightly differently. We report both because papers usually use one or the other. |
| **Precision and recall** | *Precision:* of the pixels we called oil, what fraction really was oil? (0.834 — so about 17% of what we call oil isn't). *Recall:* of the pixels that really were oil, how many did we find? (0.907 — we catch most of it). Different numbers, and you need both. |
| **Threshold** | The model doesn't output a yes/no, it outputs a probability per pixel — "I'm 73% sure this pixel is oil". The threshold is the cutoff: above it, call it oil. **Ours is 0.65**, and it was chosen by testing different values on validation data, not guessed. |
| **Baseline** | A simple, classical method you compare against, so "our AI works" means something. Ours is a classic dark-spot detector — it just finds dark pixels. **It scores 0.676 IoU.** Our model scores 0.769. |
| **Parameters** | The numbers inside the model that get adjusted during training. We have **1,963,953** of them. That's small for modern AI — deliberately, so it runs on a laptop. |

### Words about the physics and the ships

| Word | What it actually means |
|---|---|
| **Drift** | Oil on the sea doesn't stay still. It's pushed by the *current* (the water moving) and the *wind* (which drags the top layer of water and the oil with it). Drift is just the movement of the oil. |
| **Hindcast** | Running the physics **backwards in time**. Start from where the oil is today, run the model backwards, and you get a region where the oil *probably* was a few hours ago. That's how we estimate the leak site. |
| **Forecast** | Running it **forwards** — where will it go next. Same maths, opposite direction. |
| **Particle simulation** | Instead of moving one blob of oil, we release lots of little virtual dots (*particles*) and let the current and wind move each one. Where the dots spread out is the **uncertainty** — a wide cloud means we're not sure, a tight cloud means we are. **We use 300 particles.** |
| **Windage** | How much the wind drags the oil. Oil floats on top, so the wind pushes it directly — the standard figure is about **3% of wind speed**. We use that. |
| **Forcing** | The collective word for the current and wind fields that push the particles. "Forcing" = "what's forcing the oil to move". |
| **CMEMS / ERA5** | Two real, free, scientific datasets. CMEMS is real measured ocean currents. ERA5 is real measured historical weather (wind). We try to use both, and we explain exactly when we can't — §12.7. |
| **AIS** | *Automatic Identification System.* Every large ship is legally required to broadcast its position every few seconds, like a fitness tracker for ships. It's how marine authorities know who's where. **Ours is fake and labelled fake** — §12.7. |
| **MMSI / IMO** | Two different ID numbers a ship has. MMSI is a 9-digit radio ID. IMO is a permanent 7-digit hull number for the ship itself. |
| **Look-alike** | **The hardest problem in this whole field.** Remember the trick — oil looks dark. But lots of things look dark! Low wind makes a patch of sea smooth and dark. Rain does. Algal blooms do. A ship's wake does. So the radar picture says "dark patch" and you have to work out whether it's *actually oil* or just something that looks like it. **A naive system screams "OIL!" at all of them.** Our system tries to reject the look-alikes, and we measured how well — §12.8. |

---

## 12.3 What actually happens, step by step, when you press the button

Ten steps. Here's what each one does in plain words, and how long it really takes:

| # | Step | What it does in plain words | Time |
|---|---|---|---|
| 1 | **Decode** | Open the satellite file, read the two radar images, and convert the raw numbers into real physical units (§12.5). | **9.7 s** |
| 2 | **Detect** | Run our neural network over the picture. It gives every pixel a probability of being oil. Apply the 0.70 cutoff (the whole-scene operating point). Now we have a mask — a black-and-white shape. | **5.7 s** |
| 3 | **Geometry** | Convert that shape into real numbers: how many square kilometres, how long is the edge, where is the centre, which way is it stretched. Requires spherical maths (§12.6). | 1.9 s |
| 4 | **Screening** | For each dark region, decide: oil or look-alike? Or "too close to call, flag it for a human". | 1.8 s |
| 5 | **Forcing** | Look for real ocean current and wind data covering this place and date. Use it if it exists; otherwise build a labelled synthetic stand-in. | 0.03 s |
| 6 | **Backward** | Run 300 particles backwards in time from the slick to find where it probably came from. | 0.3 s |
| 7 | **Forward** | Run them forwards to see where it's going. | 0.25 s |
| 8 | **AIS** | Generate the synthetic ship tracks for that area and time window. | 0.2 s |
| 9 | **Scoring** | Score each ship against the origin zone and the time window, and record *why* it scored what it did. | 0.08 s |
| 10 | **Previews** | Write the PNG images the website shows. | 0.2 s |

**Total: about 20 seconds**, on a laptop, with no GPU and no internet.

**The thing to notice:** steps 1 and 2 are **77%** of the time (15.4 of the 20.1 seconds). That's
useful to say out loud — if someone asks "how would you make it faster?", the answer is "steps 1 and
2, and nothing else is worth touching".

---

## 12.4 The confusing bit: why there are two programs

There's a website and there's a server. Here's why, without jargon.

Think of a restaurant. The **website** is the dining room — it's what you see, it's pretty, it takes
your order. The **server** is the kitchen — it's where the actual work happens. When you click
something on the website, it places an order with the kitchen, the kitchen cooks, and the website
puts the dish on the table.

**Why it's built this way:** the "cooking" here is running a neural network over a satellite image,
which takes twenty seconds. A website in a browser can't do that well. So the website asks the
server, the server does the work and *saves the result to a file*, and then the website just displays
that file.

**The one sentence that makes everything click:** *the server is the only thing that computes. The
website is a display.* That's why the same number appears identically on three different screens —
there's only one place the number is calculated, and everything else just reads it.

**And the second important consequence:** because the result is saved to a plain file on disk, the
website works **completely offline** with no server at all. It just reads the pre-computed demo case.
That's why you can demo with the wifi off.

---

## 12.5 The best technical answer you have — "back conversion"

If a judge asks anything that touches this, you can make a very strong impression. Here it is in
plain words.

**The problem:** a satellite image doesn't come with "brightness" numbers in it. It comes with
**raw numbers whose meaning depends on the settings of that particular satellite on that particular
day**. It's like two different cameras taking a photo of the same grey wall — one might record it as
120, the other as 200, purely because of their settings. The wall is the same; the numbers aren't.

**Why that breaks AI:** if you feed your neural network raw numbers from two different satellites,
it will "learn" that the difference between the two cameras is the thing to look for — instead of
learning what oil actually looks like. Your model learns the equipment, not the ocean. And our data
spans 270 different satellite passes over four years. This would be fatal.

**The fix:** before the model sees anything, we convert every image into **decibels (dB)** — a
*physical* unit of how much radar energy actually bounced back. Now a dark patch is the same
*darkness* no matter which satellite took it or when.

**The sentence to say:** *"We convert every scene to decibels before the model sees it. If you feed a
network raw digital numbers from one satellite and decibels from another, it learns the wrong thing —
it learns the difference between the two files instead of the difference between oil and water.
Converting first is what lets one model be valid across 270 acquisitions from 2015 to 2019."*

The file names literally record the chain: **`Orb_NR_Cal_Spk_TC_dB`** — orbit-corrected,
noise-removed, calibrated, speckle-filtered, terrain-corrected, in decibels. All 1,200 files carry
it. That's a nice thing to be able to point at on screen.

---

## 12.6 Why this isn't just "we called a library"

This is the section for when a judge asks "so what did *you* actually build?" — and it's the answer
to the innovation marks.

**We wrote the neural network ourselves.** Normally you'd use PyTorch or TensorFlow — big, popular
AI libraries that do the maths for you. We didn't. We implemented the network using only **NumPy**
(the basic maths library), including the *backward pass* — the part that actually teaches the network
by working out how to adjust every one of its 1.96 million numbers. That part is hard.

**And we checked our own maths.** We wrote a test that verifies our hand-written learning maths
against a completely different method (numerically approximating the derivative, for anyone who
knows calculus). If our implementation were wrong, the test fails. **That's a real claim of
engineering, and it's one almost no student team can make.**

**We wrote our own file readers.** The normal way to read these scientific files is to install
libraries called GDAL, rasterio and xarray. GDAL is famously the hardest piece of software in this
whole field to install — it breaks, it needs system libraries, it's a nightmare on demo day. So we
wrote our own readers for the four file formats we need: plain PNG, GeoTIFF, NetCDF-4/HDF5, and
BEAM-DIMAP. They cover the parts of each format our data actually uses.

**We did our own map maths.** Normally you'd use shapely and pyproj for geometry. But those treat
the Earth as flat. At these latitudes that introduces real error into area calculations. We
implemented the maths properly on a sphere.

**Why this matters for the pitch:** it's the difference between "we assembled a product" and "we
built the difficult parts". Say it plainly: *"We wrote the neural network from scratch, including the
backward pass, and we verify our gradients against a numerical approximation in the test suite."*

---

## 12.7 The honesty section — what's real and what's ours

**Read this twice. This is the part that wins or loses you credibility, and it's also the part you
must never get wrong on stage.**

Three buckets:

### Real, and publicly available

**The satellite images and the answer keys.** 1,200 radar scenes and 1,200 hand-labelled oil masks
from a published Zenodo dataset (DOI `10.5281/zenodo.8346860`). 270 separate satellite passes, March
2015 to October 2019, across 24 different seas.

**An archive of look-alikes** from another published dataset (DARTIS, DOI `10.1594/PANGAEA.980773`),
which we use to test how well we reject non-oil dark patches. **We never trained on it** — that's
what makes it a fair test.

### Real, but useless for our dates

**The ocean current file.** It's a genuinely real CMEMS product. But it contains **a single
timestep, dated June 2026**, and our images are from 2015–2019. The closest it gets to *any* of our
270 scenes is about **2,428 days** — and for the demo scene specifically, **3,975 days** away. It
physically cannot tell us about the ocean on the day our images were taken.

So we don't pretend. We use that file for the two things it *can* legitimately give us:
- **Where the land is** — coastlines don't move between 2019 and 2026, so the land mask is valid.
- **How fast the water moves there** — so our synthetic currents move oil at a believable speed
  instead of a made-up one.

And the code doesn't just assume this — it **performs the check every time it runs**. Point it at
current data covering the right date and it uses the real thing automatically, no code change.
*"The check is in the code, not in a comment"* is a good line.

### Ours, and labelled ours

**The ocean current field.** Built from a *streamfunction* — a piece of maths that guarantees the
flow can move oil around and stir it, but can never artificially pile it up or thin it out. Real
currents don't create or destroy water, so ours shouldn't either. There's a test for it.

**The wind.** A plausible rotating wind pattern, invented. Real wind drags oil at ~3% of its speed,
and at our current speeds that wind effect is as big as the current effect — so *which* wind we used
is a substantive claim, not a footnote. The system labels each of the four possible combinations
separately, including the awkward common one where the wind is real but the current isn't.

**The ship tracks.** Entirely fabricated, because real AIS for 2015 is a paid commercial feed we
don't have. But we went out of our way to make them **impossible to mistake for real ships**:

- Every **MMSI starts with 999** — that's outside the range real ships can be assigned. No real
  vessel can have one of our numbers.
- Every **name** is `SYNTHETIC DEMO ALPHA` — obviously a placeholder.
- Every **call sign** starts with Q — a letter the international body reserves, so no real station
  can have one.
- Every **IMO number is left blank on purpose.** Unlike MMSI, there's no "safe" range of IMO
  numbers — every 7-digit number is either a real ship or reserved. So a fake one would be stealing
  some real vessel's identity. (A blank IMO is also what 48.6% of rows in a real AIS file contain,
  so it's realistic too.)

The tracks aren't random, either. Each ship is placed *relative to the drift result* — because the
only meaningful question is "was this ship in the region where the oil probably was, at the time it
was probably there?" That's what the score measures.

**And on the Vessels screen, on the first card, under the very first number:**
`AIS mode: Synthetic demonstration data`. No click, no fold, no hover — it is on the line that
says how many AIS reports went in.

**And we never say a ship did it.** Ever. The wording is always **"priority candidate for
investigation"** — a ranking of who to phone first, not an accusation. If a judge tries to get you to
say a ship is guilty, don't.

---

## 12.8 The look-alike problem, and our strongest result

This is worth understanding properly because it's the most interesting thing in the project.

**The setup:** remember, we find oil by looking for dark patches. But **dark ≠ oil**. Low wind makes
sea smooth and dark. Rain does. Algae does. Ship wakes do.

**The uncomfortable truth about accuracy scores in this field:** almost everyone reports a score like
our 0.769 IoU. But that score is measured on images that **are known to contain oil**. So it only
tells you "when there IS oil, do you draw the right shape around it?" It says **nothing** about "how
often do you scream OIL at a patch of dark water that's just a bit of low wind?"

The second question is the one an actual coastguard cares about. **If your system cries wolf all the
time, it's useless.**

**So we built a screen that answers the second question**, and then tested it on a completely separate
published archive of look-alike patches that our system had never seen.

**The result, honestly:** we reject about **69%** of dark regions that aren't oil. Which means about
**31% get through**, and about **73% of the look-alike patches still have at least one region we
didn't reject.**

**That is not a solved problem, and you should say so before anyone asks.** But: it's a real,
measured number on data we never trained on, from a published archive we credited properly. Almost
nobody in this field reports anything at all on this question, because it's easier not to.

---

## 12.9 The five things to say on stage

If you remember nothing else from this document, remember these five lines.

**1. The one-sentence pitch.**
> "We find oil spills in satellite radar images, work out where and when they probably started, and
> rank the ships worth investigating first — and we're explicit about which half of that is validated
> and which isn't."

**2. The honesty line.**
> "Detection is a solved-enough problem that we can put a number on it. Attribution is not, and almost
> nobody puts a number on it. We did both — and the AIS is synthetic, and it says so on every screen."

**3. The credibility line — the one that lands hardest.**
> "We found a leak in our own evaluation. Our first split let crops from the same satellite pass land
> in both training and test, which inflated our result. We rebuilt the split, retrained, and our
> margin over the classical baseline **halved, from +0.189 to +0.093**. We publish the lower number."

**4. The engineering line.**
> "The neural network is written from scratch in NumPy — we implemented the backward pass ourselves
> and verify our gradients against a numerical approximation in the test suite."

**5. The practicality line.**
> "It runs on a laptop. Twenty seconds per scene, no GPU, no internet, no cloud account, five Python
> packages."

Then **stop talking and let them ask.** The instinct to keep listing features is the thing that
loses marks.

---

## 12.10 The four questions you'll definitely get

**"Is this real data?"**
> "The imagery and the labels are real — 1,200 scenes from a published Zenodo dataset, 270 satellite
> passes, 2015 to 2019, 24 seas. The vessel tracks are synthetic and labelled synthetic on every
> screen, because real AIS for those dates is a licensed commercial feed."

**"What's the accuracy?"**
> "0.769 IoU on held-out data at patch scale, and 0.693 mean per-scene at scene scale. The classical
> baseline scores 0.676, so our margin is plus 0.093. Both numbers are on the Methodology screen,
> side by side, labelled with what each was measured on."

**"Can we put our own data in?"**
> "Not today — that's the feature we're building. You'd drop the scene into the site, it processes on
> the machine running the pipeline, and the finished case appears in the picker a few minutes later.
> The API already takes a job and returns a case; the upload screen is the last piece." *(Full wording
> in [document 10](10-UI-WALKTHROUGH.md) §10.12.)*

**"Why should we believe any of it?"**
> "Because everything's on disk and reproducible. Every number comes from a file the pipeline wrote,
> the whole build is seeded, and the Reproduce section on the Method screen lists the exact commands.
> Also — we found our own mistake and published the worse number."

---

## 12.11 What to read next

| If you want… | Read |
|---|---|
| To present the technical architecture | [Document 9](09-ARCHITECTURE-FOR-PRESENTING.md) — and open `architecture.html` in a browser |
| To answer any question about any button on screen | [Document 10](10-UI-WALKTHROUGH.md) |
| To answer any question about the data | [Document 11](11-DATASETS.md) |
| Every actual number in the project | [Document 3](03-STATUS-AND-ROADMAP.md) |
| The pitch script and the deck | [Document 4](04-HOW-TO-PITCH.md) |
| The full question bank with written answers | [Document 5](05-EXPLAINING-TO-JUDGES.md) |
| To run it yourself right now | [RUNBOOK.md](../../RUNBOOK.md) |
