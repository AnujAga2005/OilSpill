# SpillTrace — SIH 2026 team documents

Sixteen documents plus one diagram. **If you have one evening and you're presenting: memorise 13,
then read 14, 12, 9 and 10.** The rest are reference — read 1–6 in order some time, and use 2, 3, 5, 6
as lookup. **If you want to drive the app yourself before then, 15 says which files to feed it, and
16 is the order to read the source in.**

## The short version

| # | Document | Read it when |
|---|---|---|
| **13** | **[The two-minute speech](13-THE-TWO-MINUTE-SPEECH.md)** | **memorise this one.** ~300 words covering the problem, the build, the numbers and what's honest about it, plus the four follow-ups that come straight after and what never to say |
| **14** | **[The demo walkthrough](14-THE-DEMO-WALKTHROUGH.md)** | **what you say after "let me show you."** The spoken script for driving the UI live, screen by screen, with each screen explained as one stage of the pipeline — click cues, word counts and what to cut when you're short on time |
| **12** | **[The whole project in plain language](12-PLAIN-LANGUAGE.md)** | **first.** Every term explained like you're new — SAR, dB, IoU, U-Net, hindcast, forcing, look-alike. Read this before anything else |
| **9** | **[The architecture, for presenting](09-ARCHITECTURE-FOR-PRESENTING.md)** | you're doing the technical architecture segment. What we use, what we rejected, why, and the evaluation criteria mapped to our material |
| **10** | **[The UI walkthrough](10-UI-WALKTHROUGH.md)** | **the night before.** Every card, button, switch, toggle, badge and fold-out on all seven screens, in order, with what each one shows and what to say if you're asked |
| **11** | **[The datasets](11-DATASETS.md)** | anyone asks about the data. What's in each dataset, what it's for, what we did with it, and exactly what synthetic data we generated and how |
| **architecture.html** | **[The architecture diagram](architecture.html)** | open it in a browser. Full-width, self-contained, no internet needed — green chips are what we use, struck-through grey are what we rejected. Built to be read at a glance, not zoomed into |

## The long version

| # | Document | Read it when |
|---|---|---|
| 1 | **[The problem statement, and what we have to build](01-THE-PROBLEM.md)** | first, and again the week before the finals |
| 2 | **[What you need to know](02-WHAT-YOU-NEED-TO-KNOW.md)** | to learn the domain from zero — SAR, drift, AIS. §2.9 is the minimum |
| 3 | **[What we have built, and what is left](03-STATUS-AND-ROADMAP.md)** | for every real number, and the roadmap |
| 4 | **[How to pitch it](04-HOW-TO-PITCH.md)** | deck, demo script, roles, logistics |
| 5 | **[Explaining it to the judges](05-EXPLAINING-TO-JUDGES.md)** | the question bank with written answers |
| 6 | **[The PS-compliance slide](06-PS-COMPLIANCE.md)** | building the one slide that maps each PS clause to a screen and a number — and the handout to leave on the judges' table |
| 7 | **[The idea-stage PPT](07-IDEA-PPT.md)** | filling the six-slide SIH IDEA template, slide by slide |
| 8 | **[The whole project, taught from zero](08-THE-WHOLE-PROJECT.md)** | you have two or three sittings and want the full picture: the domain, the current UI screen by screen, and all 92 source files with what each one calls |
| 15 | **[Test data for the New analysis screen](15-TEST-DATA.md)** | **you want to run the pipeline yourself.** Which five held-out scenes to upload and where they already sit on disk, why train-vs-test decides whether the result means anything, what the optional wind / current / AIS slots will accept, and a sixth scene that fails on purpose |
| 16 | **[Reading the codebase](16-READING-THE-CODEBASE.md)** | **you want to understand the source, not just present it.** All 91 files in the order to read them — seven sittings, starting with the ten-stage pipeline as one continuous trace — plus which files to skip and which four answer the questions you will be asked |
| 17 | **[The demonstration video script](17-THE-VIDEO-SCRIPT.md)** | **you're recording a video, not presenting live.** A ~6-minute shot-by-shot script for the AICTE panel — how to open the app offline, screen/action cues and word-for-word voiceover per segment, a timing table, a 3:30 cut, and what never to say on camera |

**Every number in these documents was read out of the project's own output files.** If a figure
appears on stage, it should come from document 3.

To run the product: `.venv/bin/python scripts/run_api.py` → http://localhost:8765.
Operational detail is in [RUNBOOK.md](../../RUNBOOK.md).

## The three things nobody has done yet

1. **Watch the NTRO briefing video** — https://www.youtube.com/watch?v=cQoHSStTEdM
2. **Get one Indian scene** — the recommended dataset contains no Indian water at all. Document 3,
   §3.8 item 6 has the Copernicus recipe.
3. **Get wind and current fields covering 3–5 August 2015** in the Central Mediterranean, so the
   forcing label on the demo scene flips from synthetic to real. [RUNBOOK.md](../../RUNBOOK.md) §6
   has the ERA5 recipe and the exact bounding box.

The dataset identity is now settled: it is Part I of the Zenodo Sentinel-1 SAR oil-spill dataset,
DOI `10.5281/zenodo.8346860`, matched on eight independent fingerprints and documented by a Marine
Pollution Bulletin paper. Document 1, §1.3. **The dataset is global, not Persian Gulf** — 24 seas,
388 Gulf of Mexico scenes to the Persian Gulf's 88. Do not repeat the old framing.

The MarineCadastre header is now settled: a real daily extract
(`data/raw/AIS_2022_06_01.csv`) was downloaded and its 17-column header matched byte-for-byte
against what the app emits at `/api/cases/demo/ais.csv`. See document 6, row (c).

The leaky-split retrain is **done** — it ran on 10 September 2026. Every scene is now grouped by its
parent Sentinel-1 product before the split, no group appears on both sides, and the whole model was
re-trained and re-evaluated on the clean split. Our margin over the classical baseline **halved, from
+0.189 to +0.093**, and those are the numbers documents 3 and 6 now carry. The before-and-after table
is in [`KNOWN-ISSUES.md`](../../KNOWN-ISSUES.md) §1. **Quote the post-fix figures, and quote the drop
as well** — finding your own leak and publishing the worse number is the strongest credibility beat
we have.

One note on the demo case: the picker labels it **`00223 · demo`**, and that is the one to present.
The superseded pre-retrain `00053` case has been removed from the store, so it can no longer be
clicked by accident. Any other scene in the picker was built by `scripts/build_cases.py` and is a
held-out test scene — safe to show, and its score is a real one.
