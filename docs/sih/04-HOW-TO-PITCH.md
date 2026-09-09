# 4 — How to pitch it

The build is done. Winning now depends on delivery. This document is the pitch strategy, the
deck, and the demo script.

Confirm your round's exact format and time limit on the day — SIH formats vary. Everything below
is written so it can be cut down or stretched without losing the spine.

---

## 4.1 The one strategic decision: lead with the working product

**Get the slides out of the way in about 90 seconds, then demo.**

Almost every team in the room will have a deck, an architecture diagram and a promise. Very few
will have a thing that runs. Yours runs, offline, on a laptop, in about 25 seconds, with no
internet.
That is your entire competitive advantage and every minute spent on slides is a minute spent not
using it.

The instinct will be to explain everything before showing anything. Resist it. Judges have sat
through many decks by the time they reach you and they are tired of being told. Show them.

**The opening line, memorised word for word:**

> "Problem 26143 asks for satellite imagery plus AIS correlation to attribute a spill to a
> vessel. We built the whole chain and it runs end to end on this laptop, offline. Let me show
> you, and then I'll show you the four things it can't do yet."

That sentence does five jobs: it quotes their own problem back at them, it claims a working
system, it says "offline" (which means nothing can break), it promises brevity, and it
pre-commits to honesty — which buys enormous goodwill in the Q&A that follows.

---

## 4.2 The narrative arc

Six beats. Everything you say should belong to one of them.

**1. The gap.** Spills happen constantly and they *"remain un-attributable to the vessel causing
such spills"* — NTRO's own words. Nobody can prove who did it, so nobody is penalised, so it
keeps happening. Detection is not the problem. **Attribution is the problem.**

**2. The idea.** Oil drifts. Drift is physics. Physics runs backwards. So if you can see the oil
now, you can compute where it started and when — and then ask which ships were there.

**3. The chain.** Ten stages, satellite pixel to ranked candidate. Show, do not describe.

**4. The proof.** Real data, real model, and a measured comparison against the non-AI method.
0.769 IoU against 0.676, on a split where no acquisition appears on both sides.

**5. The honesty.** Here is what is synthetic, here is our worst-performing scene, here is the
question we cannot yet answer, and here is why we still never name a vessel as guilty.

**6. The road.** Four concrete next steps, each one a download or a week, not a research
programme.

Beat 5 is the one teams skip and the one that wins with an NTRO audience. Do not skip it.

---

## 4.3 The deck — eight slides, ninety seconds

Keep it this short. The demo is the presentation.

| # | Slide | Content |
|---|---|---|
| 1 | **Title** | SpillTrace · PS 26143 · NTRO · Disaster Management · team name. Nothing else. |
| 2 | **The gap** | One number about spill frequency or damage, and NTRO's "remain un-attributable" quote. 15 seconds. |
| 3 | **The chain** | The ten-stage diagram from document 1 §1.4. **This is your most important slide.** Point at it once, then never return. |
| 4 | **PS compliance** | Left column: verbatim (a)/(b)/(c). Right column: the screen that does it and the number it produces. **Built for you: [document 6](06-PS-COMPLIANCE.md).** Show five of its thirteen rows; hand the full table over as a printout. Judges score against a rubric — hand it to them filled in. |
| 5 | **The proof** | 1,200 real pairs · 270 acquisitions · grouped by acquisition, **zero acquisitions spanning two splits** (doc 3 §3.5 — and tell the story of the leak we fixed, it is a better line than the number) · **0.769 IoU vs 0.676 classical baseline**. |
| 6 | **What's real, what's synthetic** | The table from document 3 §3.4, verbatim. Put it *before* the demo, not after. |
| 7 | **Architecture** | Ten stages, two pipeline dependencies, zero frontend dependencies, runs offline. One line: *Node handles people and process, Python handles physics and pixels.* |
| 8 | **Roadmap** | Tier 0 is done — say so in one line and move on. Then the Tier-1 items from doc 3 §3.10 with honest effort estimates, real forcing data first. |

**Slide 6 goes before the demo deliberately.** Disclosing the synthetic parts *before* showing
anything means that for the rest of the session you are the team that volunteered its limitations
rather than the team that got caught. That inversion is worth more than any extra feature.

---

## 4.4 The demo — six minutes, beat by beat

Rehearse this until it needs no thought. Server already running, browser already open on the
Command centre, `Live API` badge already green, **and the case picker set to `demo`** — the
`00053` case has no stored probability map, so its confidence field shows an em dash. Never start
a server in front of judges.

**If you are cut short**, drop the boundary-editing sub-beat on Slick and shorten Imagery to one
slider drag. Do **not** drop the spill-age card or the filtering funnel: those are the two beats
that answer the clauses the statement was most prescriptive about, and they are what other teams
will not have. Document 6 §6.2 explains why.

### 0:00 — Command centre

> "One Sentinel-1 radar pass over the Persian Gulf, 11 March 2017. Our model found **223 square
> kilometres** of oil in **9 separate patches**."

Point at the headline figure. Then, without being asked:

> "And note this flag — the slick runs off the edge of the image, so 223 is a lower bound, not a
> total. The app says so rather than quietly reporting a number it can't support."

*Why this beat:* a big real number in the first ten seconds, immediately followed by a
self-imposed caveat. You have established both capability and credibility before you have
touched a second screen.

### 0:45 — Imagery

Switch VV to VH.

> "Two radar polarisations — these are the two channels the model takes. Radar, not a camera, so
> this works at night and through cloud."

Now drag the split slider between prediction and reference mask.

> "Left is our model, right is the human ground truth. Drag it and you can see exactly where we
> agree and where we don't. We didn't hide the disagreement behind a confidence percentage."

*Why this beat:* it is the most viscerally convincing interaction in the product. A judge
watching a slider reveal a near-match believes the model in a way no metric achieves.

### 1:45 — Slick

> "148 km² in the largest region, nine regions total. Area is integrated row by row on a sphere,
> because a degree of longitude shrinks with latitude — multiply pixels by one flat constant and
> every area you report is systematically wrong."

Then press **Edit boundary** and drag a handle.

> "And an analyst can disagree with us. Their boundary is stored separately from the model's and
> measured with the same formula. The human stays in the loop and the audit trail shows who
> changed what."

*Why this beat:* the spherical-area remark is a 15-second demonstration of technical depth.
The boundary editing shows you designed for an operator, not for a leaderboard.

### 2:45 — Drift

Press play.

> "This is the part the problem statement calls hindcasting. We seed thousands of particles on
> the detected oil and integrate the ocean **backwards** — currents plus wind, because surface
> oil moves at about 3% of wind speed and ignoring that puts your origin tens of kilometres out."

Let the animation run to the end.

> "And notice it doesn't converge to a point. It fans out into a region and a 24-hour window.
> That's honest — uncertainty grows every step you go backwards. That region and that window are
> the search box for the next screen."

Then toggle to forward.

> "Same engine forwards, for the cleanup crew and the coastline at risk."

Now scroll to **Estimated spill age** — this is the beat to slow down on.

> "The statement asks for the spill's age *if feasible*. We bound it at **up to 24 hours**, and
> then we tested whether the hindcast can tell one end of that window from the other. It can't:
> the estimated position moves **8.6 kilometres** while the uncertainty around it is **11.3** — the
> whole window sits inside its own error bar. So we report the bound and the test, not a midpoint.
> Printing '12 hours' would have been a made-up number, and a second acquisition would fix it —
> that's a procurement decision, not a modelling one."

*Why this beat:* this is the intellectual core and the thing no other team will have. The
"it gives a region, not a point" line pre-empts the sharpest question in the room, and the age
card is the clearest signal in the whole demo that you know what you are allowed to claim.

### 4:15 — Vessels

Start with the funnel card, **before** the ranked list.

> "The statement doesn't say score the traffic — it says *the irrelevant traffic is to be filtered
> out*. So: **987 AIS reports, 10 vessels. Nine had reports inside the release window. Two of
> those were actually near the oil when the oil was there. Eight are irrelevant traffic** — and we
> keep the two reasons apart, because they mean different things. Seven were in the window but 45
> to 91 kilometres away: those mean look at a different ship. **One passed within 1.5 kilometres —
> but outside the window.** Right place, wrong time. That one doesn't mean look elsewhere, it means
> the window's own width is what's excluding it, and tightening the window needs better forcing
> data."

Then point out that the excluded rows are still on screen, dimmed:

> "We don't delete them. A shortlist that silently drops eight of ten can't be audited — and the
> cheapest way to hide a scoring bug is to delete the vessels it got wrong."

> "Of what's left, here's the top candidate at **91.5 out of 100** — and here is exactly why."

Click into the breakdown.

> "Proximity 30, time window 25, trajectory 20, behavioural anomalies 10, vessel type 10, data
> completeness 5. Every component, its weight, and the evidence sentence behind it. The weights
> are fixed in advance, not tuned to produce a flattering answer."

Then, deliberately, slowly:

> "And note what it does **not** say. It says *priority candidate for investigation*. We never
> call a vessel guilty. This is an NTRO deliverable — a product that overstates its confidence
> can't survive scrutiny, and a false accusation against a named vessel is a diplomatic problem,
> not a software bug. We produce the strongest defensible statement and leave the finding to the
> investigator."

Also point at the label — and if you have the laptop online, this is the moment to open
`/api/cases/demo/ais.csv` in a second tab:

> "AIS mode: synthetic demonstration data. The problem statement explicitly permits synthetic
> AIS where real historic data isn't available, and it's labelled on every screen that touches
> it. What isn't synthetic is the format — that's the 17-column MarineCadastre schema the
> statement names as the format authority, header identical to a real daily extract. You can
> download it from the app and diff it yourself. Swapping in a licensed feed is a file drop."

*Why this beat:* you show the filter the statement asked for by name, you deliver the deliverable,
you show the explainability, and you convert your biggest apparent weakness into a demonstration
of judgement — in about eighty seconds.

### 5:40 — Method, and stop here

> "And this screen is everything we can't tell you. Patch-scale IoU is 0.769, but patches are
> sampled near known oil, so that number flatters us. On full 2048-pixel scenes the honest
> figures are **0.58 pooled and 0.69 averaged per scene**, and our single worst scene is 0.046 —
> a near-total miss, and it's on this screen. The dataset contains no labelled algal blooms or
> low-wind zones, so we went and got 2,290 published look-alike patches and scored ourselves on
> them: **our U-Net alone alarms on all of them.** A dedicated screen removes about seven in ten
> of those dark regions, which is an improvement and not a solution. All of that ships in the
> product, not just in the pitch."

**Land the plane here.** Do not go back to the Command centre for a triumphant flourish. Ending
on your own limitations is a power move with a technical intelligence audience, and it sets the
Q&A up on your terms — see document 5.

---

## 4.5 The three numbers, and the one sentence

**If you are interrupted and get thirty seconds, say the sentence. If you get a minute, say the
sentence and the three numbers.**

**The sentence:**

> "We detect the oil with a model that beats the classical method by 0.19 IoU, run the ocean
> backwards to find where it was dumped and when, and rank the ships that were there — with every
> score component visible and without ever calling anyone guilty."

**The three numbers:**

| Number | Why this one |
|---|---|
| **1,200 real Sentinel-1 image/mask pairs, 270 acquisitions, grouped by acquisition** | real data, and a splitting rule you can defend — with the leak we found and fixed volunteered, not hidden |
| **0.769 IoU vs 0.676 for the classical dark-spot baseline** | the ML earns its place, measured not asserted |
| **776 automated tests; the whole pipeline runs offline on one scene in 24 seconds** | it is engineering, not a notebook |

Have **0.584 pooled scene IoU** ready as the fourth number the moment anyone probes — it is the
lower of the two whole-scene figures, so offering it unprompted cannot be turned against you.
(Mean per-scene is 0.693. On this split pooled is the harsher number, which is the reverse of the
usual case: the scenes we fail on are large ones.)

---

## 4.6 Frame it as Disaster Management, not remote sensing

The theme is Disaster Management. The judging rubric will weight *impact and usefulness*, not
model architecture. So convert your capabilities into response outcomes:

| Instead of | Say |
|---|---|
| "0.769 IoU" | "we delineate the slick accurately enough to size the response" |
| "backward drift simulation" | "we tell the investigator where and when to look, within hours of the image" |
| "forward drift" | "we tell the cleanup crew where it's going and which coastline is at risk" |
| "ranked candidates" | "we turn thousands of vessel movements into a shortlist of six an officer can actually work" |
| "223 km²" | "223 km² — and here is what that costs to clean and what fishery it threatens" |

**Build the impact numbers before the finals.** Cleanup cost per km², fishery and mangrove value
at risk, response hours saved by narrowing the search. That is the difference between a good
technical project and a project that scores on impact. It is also the cheapest slide you will
ever make.

And name the operational path: this output is what an agency like the **Indian Coast Guard**
would act on, with ocean forcing from **INCOIS**. Naming the real Indian institutions in the loop
matters to an NTRO panel.

---

## 4.7 Team roles

Assign these and rehearse in role. Two people talking over each other loses more marks than a
missing feature.

| Role | Owns |
|---|---|
| **Driver** | the laptop. Says nothing. Clicks exactly on cue. Never improvises a click. |
| **Narrator** | all six demo beats, the opening line, the arc. One voice throughout. |
| **Model answerer** | metrics, splits, leakage, thresholds, the baseline, architecture |
| **Physics answerer** | drift, windage, hindcast uncertainty, forcing data sources |
| **Domain answerer** | AIS fields, behavioural anomalies, look-alikes, MARPOL, the "never guilty" position |

The Narrator should not also answer hard technical questions — routing a question to a named
teammate ("that's the drift model — let my teammate take that") reads as a team with depth, not as
hesitation.

---

## 4.8 What never to say

| Never say | Because |
|---|---|
| "We identify the guilty ship" | contradicts the product and the ethics. **Priority candidate for investigation.** |
| "There's no data leakage" | not true of the current run. Say **"grouped by parent acquisition, and we found and fixed a bug where that grouping wasn't reaching the splitter — these numbers predate the re-run."** |
| "99% accurate" | accuracy is meaningless at 1% positive class, and it invites a demolition |
| "Real time" | Sentinel-1 revisits every ~6 days. Say **"within hours of the image being available."** |
| "Our AIS is real" | it is not. It is permitted, labelled, and defensible — but not real. |
| "It works everywhere" | 24 seas but **no Indian water at all**, one sensor, and a look-alike rejection rate of 69%, not 100% |
| "We use AI" *(and stop there)* | say what it replaced and by how much: +0.19 IoU over the classical baseline |
| Any MARPOL ppm figure you have not verified | a wrong regulatory detail in front of NTRO costs more than silence |
| "I don't know" *(and stop there)* | always follow with what you would do to find out. See document 5. |

---

## 4.9 Rehearsal checklist

- [ ] Demo run start to finish **20 times**, out loud, with the laptop, in role.
- [ ] Once with the Wi-Fi physically off — prove to yourselves nothing needs internet.
- [ ] Once on a projector or external display at an unfamiliar resolution.
- [ ] Once in **5 minutes** and once in **90 seconds**, for when a round runs short.
- [ ] Every team member can state the three numbers cold.
- [ ] Every team member can explain their own two screens without the Narrator.
- [ ] Someone plays hostile judge with document 5's question list. Twice.
- [ ] The `pytest` run recorded as a screenshot or short clip, in case you are asked for proof
      and don't want to burn 40 seconds of your slot.

---

## 4.10 Logistics — the things that actually lose demos

- [ ] Server started and warm **before** judges arrive. `Live API` badge green.
- [ ] **Case picker set to `demo`**, not `00053`.
- [ ] Browser zoom checked on the presenting display. Test at the projector's resolution.
- [ ] **The offline `dist/` bundle also running on port 8787** as a hot spare. If the API dies
      mid-demo, switch tabs and keep talking — the demo case replays from static files.
- [ ] Laptop on mains power. Notifications off. Screen sleep off. Slack and mail quit.
- [ ] `RUNBOOK.md` open in a spare tab so any team member can restart anything.
- [ ] A printed copy of the ten-stage diagram and the real-vs-synthetic table, for judges who
      prefer paper.
- [ ] A printed copy of the **[document 6](06-PS-COMPLIANCE.md)** compliance table — one per judge.
      It is the one page that answers "does it do what we asked?" without you saying a word.
- [ ] Terminal font size raised, in case you need to show code or a test run.
- [ ] A screenshot folder as a last resort if the machine dies completely.

Next: **[document 5 — explaining it to the judges](05-EXPLAINING-TO-JUDGES.md)**.
Related: **[document 6 — the PS-compliance slide](06-PS-COMPLIANCE.md)**, which is slide 4 above,
already written.
