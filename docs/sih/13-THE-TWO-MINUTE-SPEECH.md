# 13 — The two-minute speech, to memorise

**What this is.** One speech, 314 words, about two minutes at a normal speaking pace. It covers the
problem, what we built, how it works, what the numbers are, and what is honest about it. Memorise
this one thing and you can open any round with it.

**Every number in it is real** and comes from the demo case `00223 · demo` and the held-out test
split. Nothing here is rounded up or invented. If you forget a figure, say the sentence without it
— never guess a number in front of a panel.

---

## 13.1 The speech

> Oil spills at sea are found late, and by the time anyone asks who caused it, the ship has gone.
> Satellites see the slick; nobody connects it back to the vessel.
>
> We built SpillTrace. You give it one Sentinel-1 radar image and it answers four questions: how
> big the spill is, when it was released, how old the oil is, and which ships are worth asking
> first.
>
> It runs in ten stages. A U-Net segments the oil from the radar
> backscatter. We measure the geometry on the sphere, so the area is in real square kilometres.
> Then we run the ocean backwards — three hundred particles, advection, a land mask, a three
> percent windage factor — to estimate where the oil came from. Finally we score the vessel
> traffic against that zone and window.
>
> On our demo scene — Central Mediterranean, August 2015 — **26.9 square kilometres** of oil in
> **twelve** patches, the origin estimated to a **9.6 kilometre** radius, and **ten** vessels
> screened down to **two** worth investigating, with the arithmetic shown for each.
>
> Accuracy: **0.693 mean IoU** per scene on **thirty-five held-out test scenes**. We found a leak
> in our own evaluation, fixed it, and our margin over a classical baseline halved — from +0.189
> to **+0.093**. We publish the lower number.
>
> And the honest part. The AIS is synthetic — so is the drift forcing here: we have a real CMEMS
> current product, but its nearest timestep is eleven years off this image, so the pipeline refused
> it rather than use the wrong ocean. We never say a vessel is guilty; the strongest phrase in the
> product is **"priority candidate for investigation."** And the spill age is not resolvable on
> this scene — the slick spread faster than it drifted, so we print the ratio instead of a number.
>
> Seven hundred and seventy-six tests pass. It runs entirely offline. Let me show you.

**Length: 314 words** — **2:05** at 150 words a minute, **2:14** at a calmer 140. If you need to be
strictly under two minutes, drop the sentence beginning *"We measure the geometry on the sphere"*
(299 words → 2:00 at 150) — it is the one sentence here that the demo itself makes obvious. Two
minutes is a target, not a gate: nobody stops you at 2:10, and rushing the numbers to save eight
seconds costs you more than it saves.

---

## 13.2 How to learn it

Learn it as **seven beats**, not as 314 words. If you can name the beats you can rebuild the
sentences on stage even if the exact wording deserts you.

| # | Beat | The one thing it must land |
|---|---|---|
| 1 | **The problem** | The slick is seen; the ship is never connected to it. |
| 2 | **What we built** | One radar image in, four answers out. |
| 3 | **How** | Segment → measure → drift backwards → score the traffic. |
| 4 | **The demo numbers** | 26.9 km², 12 patches, 9.6 km origin radius, 10 → 2 vessels. |
| 5 | **The accuracy** | 0.693 IoU, 35 held-out scenes, +0.093 over baseline, *we publish the lower number*. |
| 6 | **The honest part** | Synthetic AIS · the forcing refusal · never "guilty" · age unresolvable here. |
| 7 | **The close** | 905 tests, fully offline, "let me show you." |

**Rehearse against a clock.** Two minutes is a normal pace. If you land at 1:40 you are rushing —
the numbers in beats 4 and 5 are the ones that get swallowed, and those are the two beats the
panel is actually scoring.

**Beat 5 is the most important sentence you will say all day.** *"We found a leak in our own
evaluation, fixed it, and our margin halved. We publish the lower number."* Slow down for it. A
panel that has sat through a morning of inflated accuracy claims will remember the team that
volunteered a worse number, and every figure you quote after it inherits that credibility.

**Beat 6 is not an apology.** Say it at the same pace and in the same tone as beat 4. These are
conditions of the data, stated because the product states them on screen — not confessions. If you
sound defensive here, you invite exactly the cross-examination you are trying to pre-empt. The
forcing refusal in particular is a *strength*: the pipeline had a real current product in hand,
checked whether it covered the date, and declined it. Most teams' code would have silently
interpolated.

---

## 13.3 The four follow-ups that will come straight after

Have these ready. One or two sentences each — do not deliver another speech.

**"Why did you write the neural network by hand?"**

> "Operability. The whole project runs with five Python packages and no frontend dependencies at
> all — clone it and it works offline, no CUDA, no `npm install`. A PyTorch retrain on a GPU is the
> first item on our roadmap, and the mean per-scene IoU of 0.693 has real headroom."

**"Why is the AIS synthetic?"**

> "We don't have a licensed live feed. But the generator writes the exact 17-column MarineCadastre
> schema the problem statement names — we matched a real daily extract header byte for byte — so
> swapping in a real feed is a file path, not a rewrite."

**"Can I give you my own image?"**

> "Not through the browser yet — that's the last piece of plumbing. Today you drop the scene on the
> machine running the pipeline and the case appears in the picker in a few minutes. Your imagery
> never leaves the machine you run it on."

**"So can you tell me who did it?"**

> "No, and we deliberately never will. We rank who to ask first. Rank one on this case scores 90.3
> out of 100, and the screen prints the sentence behind every component of that score — but it is a
> priority for investigation, not a finding of responsibility."

---

## 13.4 What not to say

| Don't say | Because |
|---|---|
| "99% accurate" | Accuracy is meaningless at a 1% positive class, and it invites a demolition. Quote IoU. |
| "We identified the polluter" | The product never says it, and a test enforces that. Neither should you. |
| "It's real-time" | It is roughly 20 seconds of computation per scene on a laptop. Say that instead — it is a good number. |
| "+0.189 over the baseline" | That came from the leaky split. The honest figure is **+0.093**. |
| "The AIS shows…" | Say "the synthetic AIS shows…". Every single time. |

---

## 13.5 Where the numbers come from

If a judge asks you to back any figure in the speech, these are the sources — all of them on screen
or in this repository, none of them typed into a slide.

| Figure | Where it is |
|---|---|
| 26.9 km², 12 regions, 15.76 km² largest | Command centre headline; [document 3](03-STATUS-AND-ROADMAP.md) §3.7 |
| 9.6 km P90 origin radius, 24 h release window | Drift screen, **Estimated origin** card |
| 10 → 2 vessels, 8 excluded | Vessels screen, **Traffic filtering** card |
| 90.3 / 100 top candidate | Vessels screen, **Priority candidates** |
| 0.693 mean per-scene IoU, 35 test scenes | Method screen, **Per-scene distribution** |
| +0.093 over baseline, the leak fix | Method screen, **Split protocol** fold |
| Age unresolvable, 6.563 km drift vs 9.604 km spread | Drift screen, **Estimated spill age** card |
| The CMEMS refusal — product covers the sea, nearest timestep 3,975 days off | Drift screen, **Forcing** fold ("nearest product time is 3975.3 days from the acquisition, beyond the 24 h tolerance") |
| 905 tests | `.venv/bin/python -m pytest` — about 43 seconds |

See also: [document 4](04-HOW-TO-PITCH.md) for the six-minute demo, and
[document 5](05-EXPLAINING-TO-JUDGES.md) for the long-form answers to hard questions.
