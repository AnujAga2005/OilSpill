# 7 — The SIH 2026 IDEA submission deck, slide by slide

This is the **idea-stage** PPT on SIH's own template. It is a different document from the finals
deck in [document 4](04-HOW-TO-PITCH.md) §4.3, and it is written under harder constraints.

> **In plain words:** SIH makes you submit a six-slide idea deck on their template before the
> finals. This file fills it in, slide by slide, with the exact text to paste and the word count
> each slide allows. This is a *submission*, not a presentation — different rules, and §7.0 lists
> them.

## 7.0 The template's own rules, and what each one costs us

Read off the template's *Important Instructions* slide. These are not suggestions — format
non-compliance gets a deck rejected before anyone reads it.

| Rule | What it means for us |
|---|---|
| **Max 6 slides, including the title** | No PS-compliance slide, no roadmap slide, no architecture slide. The (a)/(b)/(c) mapping has to live inside slide 2. |
| **Avoid paragraphs — use points, diagrams, infographics, pictures** | Every line below is a bullet with a number in it. Budget **~100 words per slide**. If a bullet needs a comma-spliced second clause, cut it. |
| **Keep the explanation precise and easy to understand** | A non-specialist reads this. "Hindcast particles to an origin envelope" needs the plain-English gloss beside it. |
| **Idea should be unique and novel** | Slide 2's innovation block is scored against this. Three claims, no more. |
| **Only the provided template; do not change the idea-detail pointers** | Do not rename a heading, do not delete a sub-bullet prompt, do not reorder slides. |
| **Save as PDF and upload the PDF. No PPT, Word, or anything else.** | Export at the end. Check the PDF opens and that no text box has overflowed off-slide. |
| **Delete the Important Instructions slide before upload** | It is slide 7 in the template. Remove it; six slides go up. |

Two more mechanical things from the template: there is a **"Your Team Name" oval top-left on
slides 2–6** — fill it on all five, and the footer says *@SIH Idea submission - Template*, which
you leave alone.

**The strategy in one sentence.** The template is written for teams describing something they
intend to build. We have a running product with 893 passing tests, so every slide should carry a
measured number rather than an intention, and slide 3 should carry a screenshot of software that
runs. At the idea stage almost everything submitted is a plan; that is the cheapest advantage we
have and it costs nothing to use.

Every figure below is a real output of `data/processed/cases/demo.json`. If the pipeline changes,
regenerate with `.venv/bin/python scripts/run_api.py --build-demo` and re-read the numbers off the
case. Cross-check against [document 6](06-PS-COMPLIANCE.md).

---

## 7.1 Slide 1 — Title page

The template supplies six labelled bullets. Fill them exactly as the portal has them; a mismatch
is a disqualification risk and costs nothing to get right.

- **Problem Statement ID —** `26143`
- **Problem Statement Title —** copy character-for-character off the portal
- **Theme —** `Disaster Management`
- **PS Category —** `Software`
- **Team ID —** off the portal
- **Team Name —** exactly as registered

Two traps. **Theme is Disaster Management, not "oil spill detection"** — the framing is worth
marks, see [document 4](04-HOW-TO-PITCH.md) §4.6. And do not add a tagline, logo or stock tanker
photo; the template's own layout is the compliant one.

---

## 7.2 Slide 2 — Proposed Solution

The template's three prompts stay as the three sub-headings. Word budget is tightest here because
three sections share one slide. **Do not exceed these bullets — cut rather than shrink the font
below ~16 pt.**

### Detailed explanation of the proposed solution

- One SAR satellite image in → **ranked vessel shortlist + signed PDF incident report** out, in
  **20 seconds** on a laptop.
- **10 automated stages:** detect slick → screen out look-alikes → measure geometry → **run drift
  backward** to origin + release window → **run it forward** to a forecast → reconstruct vessel
  traffic → filter irrelevant traffic → score and rank → dispatch report.
- **Working prototype, not a concept:** 6 screens, **893 automated tests passing**, runs offline.

### How it addresses the problem

One line per PS clause, each with a number. A reader scanning for compliance must find it in two
seconds.

- **(a) Detect, characterise, age —** mean per-scene **IoU 0.693** over 35 held-out scenes;
  **12 regions, 26.90 km²** measured on a sphere; spill age reported **with a resolvability test**
  instead of a fabricated midpoint.
- **(b) Ocean *and* met data, origin point *and* time, future flow —** current **0.214 m/s** plus
  wind drift **0.168 m/s**; origin **14.52 °E, 35.89 °N**, P90 radius **9.60 km**, release window
  **24 h**; forward forecast from the same integrator.
- **(c) Reconstruct traffic, filter the irrelevant, score it —** **1,112 AIS reports · 10 vessels →
  2 relevant, 8 filtered out**; six weighted components summing to **100**, top candidate **90.3**.

### Innovation and uniqueness of the solution

Exactly three. Each is something a competing deck almost certainly cannot say.

- **A classical baseline we built to compete against ourselves —** dark-spot threshold **0.676 IoU**
  vs our U-Net **0.769**, a **+0.093** gain on a leak-free split. A model score with nothing to
  compare it to is unfalsifiable.
- **The spatio-temporal match is genuinely temporal —** each AIS ping is compared to where the oil
  was **at that ping's own timestamp**, not to a static circle. A vessel merely "present sometime
  in the window" scores **zero**.
- **Every output states what it does not know —** the age card prints the arithmetic showing the age
  is unresolvable; the Method screen ships our **worst single scene, IoU 0.046**, inside the product
  rather than only in the pitch; the strongest phrase in the product is
  *"Priority candidate for investigation."*

That third point is a design decision, not an apology, and belongs under *innovation*. An
attribution tool that overstates confidence is unusable by the agency that asked for it.

---

## 7.3 Slide 3 — Technical Approach

The template asks for technologies, then methodology with flow charts / images / working
prototype. **This slide carries the "already built" message. Make it visual — diagram on top,
short tech list bottom-left, screenshot bottom-right.**

### Methodology — the flow diagram

Ten boxes left to right with the measured time under each. The timings are what make it read as a
real system rather than a generic data → model → output chain.

```
SAR GeoTIFF
 → decode 9.7s → detect 5.7s → geometry 1.9s → look-alike screen 1.8s
 → forcing 0.03s → BACKWARD hindcast 0.26s → FORWARD forecast 0.25s
 → AIS reconstruct 0.19s → score 0.08s → previews 0.22s
→ ranked candidates + PDF report                        TOTAL 20.1 s
```

Draw `BACKWARD` and `FORWARD` as one shared integrator block. The PS uses the word *hindcasting*,
and this is the box that answers it literally.

### Technologies to be used

- **Python 3.12 · NumPy · OpenCV** — two pipeline dependencies. reportlab optional, PDF only.
- **U-Net** segmentation, trained on **1,200 real Sentinel-1 pairs / 240 acquisitions used**.
- **RK2 particle advection**, tidal streamfunction current + 3% windage. Seed 26143 —
  **reproduces bit-for-bit**.
- **Standard-library HTTP API**; **zero-dependency frontend**, 477 KB plain ES modules, no build
  step, runs offline.
- **ERA5 wind + CMEMS current** readers; **MERN** deployment layer.

### Working prototype

One screenshot. Use **Screen 5 · Vessel attribution** with the traffic-filtering funnel and score
breakdown visible — the densest single view of PS clause (c), and the screen no other team will
have. Caption it: **6 screens · 893 tests passing · runs offline.**

Screenshot the case the picker labels `00223 · demo`. The old `00053` case predated the retrain and
sat on a scene outside the current split, so nothing it showed could be defended in the Q&A that
follows; it has been deleted from the store.

---

## 7.4 Slide 4 — Feasibility and Viability

Template prompts: feasibility analysis, potential challenges and risks, strategies to overcome.
**Most teams fill this with hypotheticals. Ours is retrospective — the feasibility question is
already answered because the thing runs.**

### Analysis of feasibility

- Runs on a **consumer laptop, CPU only**. No GPU, no cloud spend, no licence fee.
- **20.1 s per scene** → a day of regional acquisitions in minutes on one machine.
- Inputs are **free and operational**: Sentinel-1 GRD, ERA5, CMEMS, MarineCadastre-schema AIS.
- **Deploys air-gapped** — relevant for an NTRO use case.

### Challenges, risks, and strategies

Render as a two-column mini table. Name the real ones: a weakness a reader finds that you
concealed discounts everything else on the slide.

| Risk | Strategy |
|---|---|
| **Look-alikes** — low wind, algal blooms, rain cells all look like oil in SAR | 7-feature screen, all scale-invariant ratios. **AUC 0.9573** in-domain. On 2,290 published look-alike patches never trained on, grouped into **17 families**: rejects **96%** of dark regions in the easiest, **32%** in the hardest. Real gain, not solved. |
| **Metrics could be optimistic** | Splits **grouped by parent acquisition** — 240 scenes, 240 distinct acquisitions, **zero spanning two splits**. An earlier split leaked; we found it, re-ran everything, and our margin over the baseline **halved from +0.189 to +0.093**. These are the post-fix numbers. |
| **Licensed AIS unavailable to a student team** | Synthetic AIS in the **exact 17-column MarineCadastre schema**, diff-able against a real extract. Swapping in real feeds is a reader change, not a rewrite. |
| **Attribution has legal consequences** | A test asserts "guilty", "culprit", "responsible party" appear **nowhere** in the PDF — and that "priority candidate for investigation" does. |
| **No Indian-water validation yet** | 0 of 1,200 scenes fall in 65–95 °E, 5–25 °N. One Sentinel-1 scene over the **Gulf of Kutch** closes it. |

Do not soften the last two rows. Volunteering them is why the rest of the deck gets believed.

---

## 7.5 Slide 5 — Impact and Benefits

Template prompts: potential impact on the target audience, then benefits (social, economic,
environmental). **The failure mode is a wall of unsourced ocean-pollution statistics.** Any number
you cannot cite is a liability. Anchor on what the system changes instead.

### Potential impact on the target audience

- **Coast guard / pollution-response officer** — receives an **incident file, not an image**:
  origin estimate, release window, drift forecast, ranked shortlist, signed PDF, in 20 s.
- **Enforcement and investigation** — "which ship in this sea" collapses to a defensible **2**,
  with each of the 8 exclusions **retained and auditable, not deleted**.
- **NTRO / maritime domain awareness** — automated, reproducible, air-gappable, over free
  satellite data.

### Benefits

- **Environmental** — the **forward forecast** says where the slick *will be*, which is what
  decides whether booms go in the right place.
- **Economic** — avoids the cost of a **wrongly-directed response**. We have **not** quantified
  cleanup cost in rupees; that is roadmap, not a claim.
- **Social / governance** — **auditability**: every score component prints the sentence that
  earned it, and the run reproduces bit-for-bit from a fixed seed.

**One closing line, and it is the most important sentence in the deck:**

> Attribution without honesty is worse than no attribution — it points enforcement at the wrong
> vessel. Every claim SpillTrace makes can be checked, and every claim it cannot support is
> labelled.

---

## 7.6 Slide 6 — Research and References

Template prompt: *details / links of the reference and research work.* This slide is thin in most
competing decks and trivially easy to make strong. Real DOIs, as a plain list.

- **Dataset —** Trujillo-Acatitla, Tuxpan-Vargas, Ovando-Vázquez & Monterrubio-Martínez (IPICYT,
  Mexico). *Sentinel-1 SAR oil spill image dataset*, Part I. Zenodo, CC BY 4.0 —
  **doi:10.5281/zenodo.8346860**
- **Method paper —** *Marine oil spill detection and segmentation in SAR data with two steps Deep
  Learning framework.* Marine Pollution Bulletin **204: 116549** (2024) —
  **doi:10.1016/j.marpolbul.2024.116549**
- **Look-alike evaluation set —** DARTIS annotated SAR dark-formation dataset, PANGAEA —
  **doi:10.1594/PANGAEA.980773**
- **U-Net —** Ronneberger, Fischer & Brox, MICCAI 2015 — **arXiv:1505.04597**
- **Forcing —** ERA5 hourly single levels (Copernicus CDS); CMEMS Global Ocean Physics Analysis
  and Forecast.
- **AIS schema —** MarineCadastre.gov / NAIS 17-column daily extract, the format the PS names.
- **Imagery —** Copernicus Data Space Ecosystem, Sentinel-1 GRD IW.

Two things to get right. **Put the dataset DOI on the slide** — it is the dataset the problem
statement itself names, and quoting it back is a compliance signal. And **say "global dataset",
never "Persian Gulf data"**: 1,200 scenes across 24 named seas, 95 °W to 130 °E; the Persian Gulf
is ~7%, and our demo case sits in the Central Mediterranean.

---

## 7.7 Never put these in this deck

- **No fabricated statistics.** No "spills cost India ₹X crore" without the citation on slide 6.
- **No accusatory language.** Strongest available phrase: *"Priority candidate for
  investigation."* Applies to the deck as much as the product.
- **No implying the synthetic parts are real.** The demo case's AIS and drift forcing are
  synthetic and labelled. If the deck hides that and a judge finds out at the finals, the deck
  becomes the problem.
- **No unbuilt features in the slide-3 flow diagram.** Roadmap items belong in slide 4's risk
  table as things being addressed.
- **No paragraphs.** The template says so explicitly. If a bullet runs past two lines, cut it.

---

## 7.8 Pre-upload checklist

1. Six slides. The Important Instructions slide **deleted**.
2. Team name filled in the oval on slides **2–6**.
3. PS ID **26143**, title character-exact, theme **Disaster Management**.
4. No bullet longer than two lines; nothing overflowing its text box.
5. Screenshot is the **`demo`** case, and it is legible at 100% zoom.
6. Every number matches `data/processed/cases/demo.json`.
7. **Exported as PDF**, PDF reopened and checked, PDF uploaded — not the .pptx.

**The final read-through question:** does the deck contain a number a judge could look up and
verify? Ours has 0.769 vs 0.676, mean per-scene IoU 0.693, 26.90 km², 1,112 reports filtered to 2, 20.1 seconds,
893 tests. A deck with verifiable numbers reads as a report on a working system. A deck without
them reads as a plan.
