# 5 — Explaining it to the judges

The question bank, with answers written out. Read it, then have a teammate fire questions at you
from it until the answers come without thinking.

Rule zero: **every answer in this file is true.** Do not improve on them by adding a claim. The
strength of this project's position is that it can survive being checked.

---

## 5.1 The same explanation at three depths

### Thirty seconds — for a judge passing through

> "Radar satellites see oil as a dark patch on the sea, because oil flattens the ripples that
> radar bounces off. We segment those pixels with a U-Net, measure the slick, then run the ocean
> physics **backwards** to work out where it was dumped and when. Then we look at which ships were
> in that place at that time and rank them for investigation."

### Two minutes — the standard answer

> "The problem statement says spills often 'remain un-attributable to the vessel causing such
> spills'. That's the gap we're closing.
>
> We start with a real Sentinel-1 radar scene. Radar rather than a camera, because radar sees at
> night and through cloud, and because oil damps the small ripples that radar reflects off — so
> oil shows up as a dark hole in the image. We take both radar polarisations, VV and VH, as two
> input channels.
>
> A U-Net segments the oil per pixel. We measure the result on a sphere — area, perimeter, shape,
> and the nine separate regions in our demo case.
>
> Then the interesting part. Oil drifts with currents and wind, and drift is physics, so physics
> runs backwards. We seed thousands of particles on the detected oil and integrate backwards in
> time — that's what the problem statement calls hindcasting. It doesn't give a point; it gives a
> probability region and a release window, because uncertainty grows every step you go back. In
> our case that's a 24-hour window.
>
> That region and that window are a search box. We reconstruct the vessel traffic that went
> through it and filter out the irrelevant traffic — ten vessels down to two, with the eight
> exclusions kept on screen and reasoned so the filter itself can be checked — then score what's
> left on proximity, time-window
> overlap, trajectory, behavioural anomalies, vessel type and data completeness — 100 points
> total, every component shown on screen with the evidence behind it.
>
> We also bound the spill's age at 24 hours and say plainly that one radar pass can't tighten that,
> with the arithmetic on screen.
>
> And it never says a vessel is guilty. The strongest phrase in the product is 'priority candidate
> for investigation'."

### Ten minutes — the deep dive

Follow the demo script in document 4 §4.4, and expand at whichever beat they lean in on. Do not
front-load depth; let them pull it out of you. A judge who asks a follow-up is a judge who is
engaged, and answering the question they asked scores better than delivering the lecture you
prepared.

---

## 5.2 Five rules for answering

1. **Answer the question asked, then stop.** Volunteering three extra caveats after a good answer
   turns a strength into a wobble.
2. **Volunteer the limitation before they find it.** You cannot be caught out on something you
   said first. This is the whole strategy.
3. **Numbers, not adjectives.** Not "quite accurate" — "0.771 IoU at patch scale, 0.64 mean per
   scene."
4. **Route to the owner.** "That's the drift model — my teammate built it, let them take it" reads
   as depth. Guessing on a teammate's behalf reads as a team that doesn't know its own code.
5. **Never guess at a fact.** Say what you know, name what you'd check. An NTRO panel will notice
   a bluffed regulatory or sensor detail instantly, and it costs you the credibility of everything
   you said before it.

---

## 5.3 The question bank

### A. Data and honesty

**"Is this real data?"**
> "The imagery and the ground-truth masks are completely real — 1,200 Sentinel-1 pairs from 270
> distinct satellite passes, and the problem statement's own recommended Zenodo dataset. The model
> and every metric are real, trained and measured here. Two things are synthetic and labelled as
> such on every screen: the ocean current and wind field, and the AIS tracks. I can tell you
> exactly why for each."

**"Why is the AIS synthetic?"**
> "Because the problem statement permits it. It says real AIS may be used 'else synthetic data can
> be prepared for the region of oil spill to demonstrate the functioning of the algorithm.' Real
> historic AIS for an arbitrary ocean patch in 2017 isn't obtainable by us. So we generate a fleet
> for the hindcast envelope, label it on every screen, give every vessel an MMSI starting 999 —
> outside the ITU country-code range, so no real ship can hold one — and name them with NATO
> phonetic words. The algorithm operating on it is entirely real."

**"Why is the ocean forcing synthetic? That seems like a bigger problem."**
> "It is the weaker part and it's fixable with a download rather than a rewrite. We have a CMEMS
> NetCDF file and a working reader for it. Its time coverage doesn't overlap our imagery's
> acquisition dates. We could have interpolated across a multi-year gap and called the result
> real — instead the pipeline falls back to a deterministic synthetic field and says so. Getting a
> current field that covers 11 March 2017, from CMEMS or from INCOIS, flips that label to real
> without touching the physics."

**"Why the Persian Gulf and not Indian waters?"**
> "Because it's the dataset the problem statement recommended — the Zenodo Sentinel-1 SAR oil spill
> dataset. The pipeline is region-agnostic: it reads the geotransform from the file, so any
> Sentinel-1 GeoTIFF works. Running an Indian scene — Gulf of Kutch, or the 2017 Ennore spill — is
> a download and a run, and it's the next thing on our list."

*(If you have verified the Zenodo match, state it as fact. If not yet, say "we believe it's the
recommended dataset and we're confirming the exact Zenodo record.")*

**"How do you know your model isn't just memorising?"**
> "Our 1,200 files come from only 270 satellite acquisitions — multiple crops per pass. If crops
> from one acquisition landed in both train and test, the model would be tested on water it had
> already seen. So the splitter groups by parent acquisition, not by file. And we'll be straight
> with you: we found a wiring bug where the acquisition key wasn't actually reaching the splitter,
> so the run behind these numbers grouped by crop — 34 of our 36 test scenes share an acquisition
> with a training scene. It's fixed, there's a regression test, and the numbers on screen are an
> upper bound until we re-run. That's why we quote per-scene IoU of 0.64 rather than the patch
> figure."

Say this before anyone asks. A judge who finds a leak you didn't disclose stops believing every
other number you gave them; a judge you hand it to concludes you audit your own work. If the
pipeline has been re-run by the time you present, quote the new figures and describe the bug in
the past tense — see [KNOWN-ISSUES.md](../../KNOWN-ISSUES.md).

### B. The model

**"What architecture, and why?"**
> "A U-Net — encoder–decoder with skip connections. The skips matter for us specifically: the
> encoder learns what things are but loses where they are, and the skip connections restore the
> spatial precision. We need sharp boundaries because area and perimeter are deliverables, not
> just a classification. 1,963,953 parameters, depth 4, encoders 16 to 128, 256 bottleneck."

**"You wrote the backprop by hand in NumPy? Why?"**
> "Package installs were blocked in the build environment, so we implemented the forward and
> backward passes ourselves. It turned into an advantage in two ways: nothing in the model is a
> black box we imported, and it trains in 26 minutes on a laptop CPU with no GPU. The limitation is
> real too — it caps how deep we can practically go, which is why a PyTorch retrain on a GPU is on
> our roadmap and would improve the whole-scene number."

**"How accurate is it?"** — *the most important question in the room*
> "Two numbers, and the difference between them matters. At patch scale, 0.771 IoU. But patches are
> 128 pixels sampled near labelled oil, so the model never sees the 99% of open water where false
> alarms live — that number flatters us and it flatters every team that quotes it.
>
> On full 2048-pixel held-out scenes, the honest figures are 0.78 pooled IoU and **0.64 averaged
> per scene**. The mean is the harsher one — pooled lets big easy slicks dominate; the mean scores
> a small hard scene the same as a big easy one. Our worst single scene is **0.13**, and the full
> per-scene distribution is on the Method screen."

Never answer this question with only the patch number. Volunteering the honest one is the single
highest-value thing you do all session.

**"Is 0.77 good?"**
> "Relative to what matters more than the absolute. We built the classical non-AI method as a
> baseline — despeckle the VV channel, threshold the dark pixels, morphological cleanup, minimum
> area. It scores 0.582 on the same test data. We beat it by 0.189 IoU. That comparison is why we
> can say the machine learning earns its place rather than just asserting it."

**"What about false positives?"**
> "Precision 0.83, recall 0.93 — so we slightly over-call. For disaster response that's the right
> direction: missing a real spill costs more than sending an analyst to check a false one.
>
> But the honest framing is bigger than that. Every scene in our dataset contains labelled oil, so
> our metrics measure delineation quality on scenes already known to have a slick. **They are not a
> false-alarm rate on clean sea.** We don't have that number and we say so in the shipped output."

**"How did you pick the threshold?"**
> "Swept on the validation scenes and then applied unchanged to test. 0.6 at patch scale, 0.8 at
> whole-scene scale — a bigger scene needs a stricter cutoff because there's vastly more water to
> raise a false alarm in. The sweep curve is on the Method screen. Choosing the threshold on test
> data would have inflated our numbers and we didn't."

**"Can it tell oil from algae or a calm patch?"** — *the hardest fair question*
> "Not yet, and I won't claim otherwise. Algal blooms, low-wind zones, rain cells and ship wakes
> all damp the sea surface and look dark in SAR. It's the central unsolved problem in this field.
>
> Our specific situation: the supplied dataset contains **no labelled look-alikes**, so our
> ability to reject them is untested and unquantified — that limitation ships in the product, not
> just in this answer.
>
> What we'd do: a second classification stage over each dark patch using shape, texture and
> backscatter statistics. Low-wind zones have diffuse edges and correlate with low overall scene
> backscatter; oil slicks have sharper gradients. Algal blooms are seasonal and geographically
> patterned. Plus a wind-speed gate — below about 3 m/s the whole sea goes dark and no detection
> should be trusted. That's our next real piece of work."

### C. The drift physics

**"How does the backward drift work?"**
> "Lagrangian particle tracking. We seed thousands of particles on the detected oil, then for each
> small time step we look up the local current and wind at each particle's own position, add the
> windage contribution and a turbulent diffusion term, and move it — with the velocity field
> negated, so it runs backwards. After 24 hours of that, the particle cloud is the origin
> probability region."

**"Why don't you get a single origin point?"**
> "Because you can't, and a system that reported one would be lying. You start from a slick that's
> already spread over 223 km², and every backward step adds uncertainty. What comes out is a
> region and a time window. That's honest, and it's still useful — a region and a 24-hour window is
> exactly the search box you need to query AIS."

**"Do you use wind?"**
> "Yes, and the problem statement requires it — it asks for oceanographic *and* meteorological
> data. Surface oil moves at roughly 3% of wind speed; we use 3%. The arithmetic is why it matters:
> a 6 m/s wind contributes about 0.18 m/s, which over 24 hours is roughly 15 km. That's the same
> order as the current contribution. Ignore wind and your origin estimate is tens of kilometres
> off, so you search the wrong water and shortlist the wrong ships."

**"What physics are you missing?"**
> "Three things, and I'd rather name them than be found out. **Stokes drift** — waves impart a net
> drift beyond the mean current, real and sometimes significant, not modelled. **Weathering** — real
> oil evaporates, emulsifies and disperses over hours to days; our particles are conserved
> forever, which makes long hindcasts less trustworthy than short ones. And **spreading physics** —
> real slicks spread under gravity and surface tension; we approximate all unresolved spreading
> with a turbulent random walk."

**"How far back can you reliably hindcast?"**
> "We run 24 hours, and I'd be cautious past that. Error compounds every step, and the weathering
> we don't model becomes more significant the further back you go. The right way to state it is
> that the envelope widens with time — the product shows the envelope growing rather than
> pretending precision it doesn't have."

**"Can you tell how old the spill is?"** — *the statement says "age if feasible", so expect this*
> "We bound it: up to 24 hours at the time of the image, which is the horizon we actually
> integrated. Then we test whether the hindcast can tell one end of that window from the other,
> and for this scene it can't — over 24 hours the estimated position moves 8.6 kilometres while the
> uncertainty around it is 11.3. The whole release window sits inside its own error bar.
>
> So we print the bound and the test, not a midpoint. Saying '12 hours' would have been a
> fabricated number.
>
> And that's a property of *this* case rather than of the method: it's unresolvable because a
> 148 km² slick spreads faster than it drifts. A tight, compact slick does resolve, and we have
> tests asserting both sides of that so the claim stays tied to the physics. Three things would
> narrow it — a second acquisition, licensed metocean forcing, or an earlier acquisition showing
> the area clear. All three are data, not code."

### D. AIS and scoring

**"Where does AIS come from and what's in it?"**
> "AIS is the mandatory ship transponder broadcast — identity, position, speed and course, several
> times a minute, over VHF. It's Flightradar for ships. The fields that matter to us are MMSI and
> IMO number for identity, timestamp and lat/lon for the spatio-temporal match, SOG and COG for
> behaviour and trajectory, and vessel type for plausibility. Ours is synthetic — but it's emitted
> in the exact 17-column MarineCadastre/NAIS schema the problem statement names as the format
> authority, header identical to a real daily extract, and the same module parses a real extract
> back in. You can download ours from the app at `/api/cases/demo/ais.csv` and diff it. A licensed
> feed is a file drop, not a rewrite."

**"How do you filter irrelevant traffic?"**
> "On both axes at once, and we publish the counts. A vessel is relevant only if the *same* AIS
> report is inside the estimated release window **and** within three drift-envelope radii of where
> the oil is estimated to have been at that report's own timestamp. For this case: 987 reports, 10
> vessels → 9 with reports in the window → 2 relevant, 8 excluded.
>
> The two exclusion reasons are kept apart on purpose, because they mean different things. Seven
> were in the window but 45 to 91 kilometres away — look at a different ship. One passed within 1.5
> kilometres but outside the window: right place, wrong time, and what's excluding it is the width
> of our own release window rather than distance.
>
> And the excluded vessels are dimmed, not deleted. A shortlist that silently drops eight of ten
> can't be audited — the cheapest way to hide a scoring bug is to delete the vessels it ranked
> wrong. Relevance is also the primary sort key, so an excluded vessel can't climb above a relevant
> one on vessel type and data-quality marks alone."

**"How does the scoring work? Is it a black box?"**
> "The opposite — it's deliberately arithmetic you can audit. 100 points: proximity 30, time-window
> overlap 25, trajectory 20, behavioural anomalies 10, vessel type 10, data completeness 5. Those
> weights are fixed in advance, not tuned to produce a flattering answer, and every component
> appears on screen with the evidence sentence behind it.
>
> We deliberately did *not* use a learned model for this. There's no ground truth — nobody has a
> labelled dataset of confirmed polluters — so anything learned would be fitted to assumptions and
> unexplainable to an investigator. An investigator has to be able to defend the reasoning, so the
> reasoning has to be legible."

That last paragraph is a strong answer. It shows you chose *not* to use ML where ML was
inappropriate, which is a more mature signal than using it everywhere.

**"Isn't scoring by vessel type prejudicial?"**
> "It scores capability, not character. An oil tanker can discharge persistent oil in bulk, so it
> gets 1.0; a passenger ferry has no bulk oil cargo and tightly regulated waste handling, so it
> gets 0.2. It's 10 points out of 100, it can never by itself make a vessel a candidate, and the
> rationale string is displayed on screen so the analyst can disagree with it."

**"What if the ship turns its AIS off?"**
> "Then this method alone won't see it, and that's a real limitation of any AIS-based approach.
> Two partial answers. First, the gap itself is evidence — a transponder going silent near the
> origin window is a behavioural anomaly, and we score behavioural anomalies. It raises a
> question; it doesn't answer one, because a gap can equally be equipment failure or a reception
> hole.
>
> Second, and better: SAR sees the ship. A metal hull is a bright target in radar whether or not
> it's transmitting. Detecting vessels in the same image we detect the oil in, and cross-checking
> against AIS, would catch a dark ship. We haven't built it. It's on the roadmap and it's the right
> answer to this question."

**"Couldn't they spoof the AIS?"**
> "Yes. AIS position and identity are self-reported and unauthenticated. That's one more reason our
> output is a candidate for investigation rather than a finding — the evidence chain has an
> unauthenticated link in it, and an honest system says so."

### E. Attribution and ethics

**"The problem statement says identify the vessel responsible. You don't. Isn't that a miss?"**

This is the question most likely to be misread as a failure. Answer it with confidence, not
apology.

> "We do attribute — we rank vessels by spatio-temporal correlation with the hindcast origin,
> which is exactly what requirement (c) specifies, with every scoring component and its evidence
> exposed. What we don't do is *assert* guilt.
>
> That's a deliberate design decision for this specific customer. This is an NTRO deliverable. An
> intelligence product that overstates its confidence can't survive scrutiny, and a false
> accusation against a named, identifiable vessel is a diplomatic problem, not a software bug.
> Our top candidate scores 91.5 out of 100 on correlation with the origin window — that's a strong
> investigative lead, and calling it a conviction would make it weaker, not stronger, because the
> first defence lawyer or foreign ministry to look at it would break it.
>
> So we produce the strongest defensible statement — a ranked candidate, fully explained — and
> leave the finding to the investigator who can board the vessel and sample the oil."

**"So what would actually confirm it?"**
> "Chemical fingerprinting. You sample the slick and sample the vessel's tanks and bilge, and
> match the hydrocarbon signature. That's the evidence that stands up. Our job is to tell the
> investigator which vessel to go and sample, and to narrow that from thousands of movements to a
> handful. That's the whole value — we make a physical inspection targetable."

Excellent answer. It shows you understand where your system sits in a real enforcement chain
rather than imagining it is the whole chain.

### F. Engineering

**"What's the stack?"**
> "Python for the pipeline, and deliberately thin: three dependencies — NumPy, OpenCV and pytest.
> The HTTP server, the PNG encoder, the GeoTIFF reader, the NetCDF reader, the morphology,
> connected components, contour tracing and spherical geometry are all standard library or written
> here. The frontend has **zero** dependencies — vanilla ES modules, no framework, no bundler, no
> `npm install`. Clone it and it runs offline."

**"Is it tested?"**
> "532 automated tests, about 40 seconds, all passing. And the whole pipeline is seeded — run the
> same case twice and every figure is byte-identical; only the timestamps change. That matters for
> an evidentiary product: a result you can't reproduce is a result you can't defend."

**"Will it scale? This takes twenty seconds."**
> "Twenty seconds for a full nine-stage run on one 2048-pixel scene, on a laptop CPU,
> single-threaded, with no GPU — and three quarters of that is decoding the GeoTIFF and running
> inference, not the physics. Sentinel-1 revisits every six days, so throughput isn't the binding
> constraint — but the API is already built as a job queue rather than blocking requests, so it
> parallelises across scenes without redesign. Inference on a GPU would be a small fraction of
> those twenty seconds."

**"Why not React? You said you know MERN."**
> "Because it would have earned nothing. The value here is the model, the drift physics and the
> geometry — all Python. Rewriting a working interface in React risks the demo for zero marks.
> Where MERN genuinely belongs is in front: an Express gateway for role-based access — watch
> officer, analyst, supervisor — MongoDB for case history and analyst annotations, and an immutable
> audit log of who opened which case and who changed which boundary. For an intelligence
> organisation, provenance of *actions* matters as much as provenance of numbers. Node handles
> people and process; Python handles physics and pixels."

**"How would this be deployed?"**
> "Automated ingest as Sentinel-1 scenes are published on the Copernicus Data Space, forcing pulled
> from CMEMS or INCOIS, the pipeline run per scene, and results pushed to an analyst queue with an
> alert to the responder — Coast Guard or state pollution board. The alerting is the piece we
> haven't built yet and it's high on our list, because right now the pipeline ends at a screen and
> disaster management has to end at an action."

### G. Impact and deployment

**"What's the actual impact?"**
> "Two things. First, deterrence through attributability — the problem statement's own framing is
> that spills 'remain un-attributable', so nobody is penalised and it keeps happening. Make
> discharge attributable and the behaviour changes.
>
> Second, response time. The forward drift tells a cleanup crew where the oil is going and which
> coastline is at risk before it arrives, which is the difference between containment at sea and
> cleaning a beach."

If asked for numbers and you have not built the impact slide yet, say so:
> "We haven't put rupee figures on it yet — cleanup cost per km², fishery value at risk, response
> hours saved. That's a slide we should have and don't."

Do not invent a cost figure on stage.

**"Who's the user?"**
> "A watch officer or analyst at a coastal monitoring desk — Coast Guard, or a state pollution
> control board. That's why the interface looks the way it does: dark imagery stages inside a light
> analytical interface, provenance on every number, an em dash and a reason when a value isn't
> available rather than a zero, and a boundary the analyst can correct by hand when they disagree
> with the model."

### H. Hostile and trick questions

**"Isn't this just a U-Net on a public dataset? What's new?"**
> "The segmentation isn't the contribution — you're right that it's well-trodden, and that's why we
> also built the classical baseline to show what the model is actually worth. The contribution is
> the **chain**: hindcast to an origin region and time window, then spatio-temporal filtering of
> vessel traffic through that window, then explainable scoring — end to end, automated, in one
> tool, with the provenance of every number visible. Detection alone doesn't attribute anything,
> and attribution is what the problem statement says is missing."

**"How much of this did you write?"**
> Answer truthfully and specifically. Name who built which module. If you used AI assistance, say
> so plainly and then demonstrate understanding — a team that can explain every design decision and
> every limitation in its own system is in a strong position regardless of how the keystrokes
> happened. A team that can't explain its own code is in a weak one no matter who typed it. **This
> is why documents 2 and 3 exist: read them.**

**"Show me the code for X."**
> Open it. Document 3 §3.3 has the repository map. Know where the U-Net, the drift engine, the AIS
> generator and the scorer live. Having the terminal font already enlarged saves you looking
> flustered.

**"Your best score is 0.64 mean per scene. That's not great."**
> "Agreed, and I'd rather quote it than hide it. Two things about that number. It's an average
> across 36 held-out scenes, so a small hard scene weighs the same as a big easy one — pooled IoU
> is 0.78, and our worst single scene is 0.13. And it's from a 2-million-parameter model with a
> hand-written backward pass trained for 26 minutes on a laptop CPU. A PyTorch retrain on a GPU
> with a pretrained encoder and proper augmentation has real headroom. What I can defend is the
> comparison: the same evaluation gives the classical method 0.582."

**"What if I told you your model would fail completely on Indian coastal waters?"**
> "You might be right and I can't currently disprove it. Indian coastal water brings things this
> dataset doesn't have — heavy sediment plumes, monsoon rain cells, dense fishing traffic,
> different wind regimes. Nothing in the pipeline is region-specific; it reads geolocation from the
> file. But 'it should generalise' isn't evidence. Running an Indian scene and reporting the number
> honestly, even if it's worse, is the next thing on our list."

---

## 5.4 How to say "I don't know"

Three parts, in this order, every time:

1. **Say it plainly.** "I don't know."
2. **Say what you do know that bounds it.** "What I can tell you is that we've only tested on
   Sentinel-1 IW scenes from this one dataset."
3. **Say how you'd find out.** "The way to answer it would be to pull three scenes from the Gulf
   of Kutch and run the same evaluation."

This scores far better than a confident guess. Judges have heard hundreds of confident guesses
today and they can tell. What they rarely hear is a student who knows the edge of their own
knowledge — and for a technical intelligence audience specifically, that is the single most
credible thing you can demonstrate.

**Never** invent a number, a regulation, a sensor specification or a citation. One fabricated
detail makes every real number you quoted suspect.

---

## 5.5 If the demo breaks

Stay calm and keep narrating. Nothing here is fatal.

| Failure | Move |
|---|---|
| API stops responding | Switch to the `dist/` tab on port 8787 — the demo case replays from static files. Say: "that's our offline bundle, same case, no server needed." |
| Badge says **Offline demo** unexpectedly | You're on port 8787. Either carry on there or switch to 8765. |
| A screen shows "no case computed" | `.venv/bin/python scripts/run_api.py --build-demo` |
| A run hangs | Don't wait in silence. Keep talking about what the stage does. The precomputed case is still on screen. |
| Browser/CSS looks wrong | Hard reload, `Cmd-Shift-R`. |
| Laptop dies entirely | Screenshots folder, and keep delivering the narrative. The story is the same. |

Whatever happens, do not apologise repeatedly or start debugging in front of them. One sentence
of acknowledgement, then continue. Judges remember composure.

---

## 5.6 Per-screen crib sheet

The one thing to say on each screen if you have only one sentence:

| Screen | The one sentence |
|---|---|
| **Command centre** | "223 km² of oil in 9 regions — and the flag says it runs off the image edge, so that's a lower bound." |
| **Imagery** | "Two radar polarisations in, and you can drag between our prediction and the human ground truth to see where we disagree." |
| **Slick** | "Area integrated row by row on a sphere, because a degree of longitude shrinks with latitude — and an analyst can correct the boundary by hand." |
| **Drift** | "The ocean run backwards — and it gives a region and a 24-hour window, not a point, because uncertainty grows every step back." |
| **Vessels** | "Ranked by spatio-temporal correlation, every score component and its evidence visible, and never called guilty." |
| **Method** | "Everything we can't tell you: patch metrics flatter, the honest whole-scene number is 0.64 mean and 0.13 at our worst scene, and look-alike rejection is untested." |

---

## 5.7 The impression to leave

If a judge remembers one thing about your team, make it this:

> **They built the whole chain, it runs, and they told us what's wrong with it before we asked.**

Not "they had the best model." Not "the UI was pretty." Those are worth points, but a technical
intelligence organisation is staffed by people whose job is assessing the reliability of
information. They will forget your IoU. They will remember whether you were straight with them.

Every judgement call in this project — the labelled synthetic data, the whole-scene metrics
alongside the flattering ones, the worst-scene figure shipped in the product, the refusal to call
a vessel guilty — points the same way. That consistency is the pitch. Lean on it.

---

## 5.8 Final checklist before you walk in

- [ ] The three numbers, cold: **1,200 pairs / 270 acquisitions** · **0.771 vs 0.582 IoU** ·
      **532 tests · whole pipeline offline in 19 s**
- [ ] The fourth number ready for probing: **0.64 mean per-scene IoU**
- [ ] The PS sentence permitting synthetic AIS, quotable
- [ ] The seven minimum concepts from document 2 §2.9
- [ ] The "we attribute but we don't assert guilt" answer, word for word
- [ ] Server warm, `dist/` spare running, notifications off, mains power
- [ ] Everyone knows their role from document 4 §4.7
- [ ] Nobody will say "guilty", "99% accurate", or "real time"
- [ ] The **[document 6](06-PS-COMPLIANCE.md)** compliance table printed, one copy per judge

Next: **[document 6 — the PS-compliance slide](06-PS-COMPLIANCE.md)**, which is the one artefact
to hand across the table rather than talk through.

Good luck. The product is real, the numbers are real, and the honesty is your strongest feature.
