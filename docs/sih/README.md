# SpillTrace — SIH 2026 team documents

Eight documents. Read 1–6 in order once, then use 2, 3, 5 and 6 as reference. Document 8 is the long
teaching reference behind all of them.

| # | Document | Read it when |
|---|---|---|
| 1 | **[The problem statement, and what we have to build](01-THE-PROBLEM.md)** | first, and again the week before the finals |
| 2 | **[What you need to know](02-WHAT-YOU-NEED-TO-KNOW.md)** | to learn the domain from zero — SAR, drift, AIS. §2.9 is the minimum |
| 3 | **[What we have built, and what is left](03-STATUS-AND-ROADMAP.md)** | for every real number, and the roadmap |
| 4 | **[How to pitch it](04-HOW-TO-PITCH.md)** | deck, demo script, roles, logistics |
| 5 | **[Explaining it to the judges](05-EXPLAINING-TO-JUDGES.md)** | the question bank with written answers |
| 6 | **[The PS-compliance slide](06-PS-COMPLIANCE.md)** | building the one slide that maps each PS clause to a screen and a number — and the handout to leave on the judges' table |
| 7 | **[The idea-stage PPT](07-IDEA-PPT.md)** | filling the six-slide SIH IDEA template, slide by slide |
| 8 | **[The whole project, taught from zero](08-THE-WHOLE-PROJECT.md)** | you have two or three sittings and want the full picture: the domain, the current UI screen by screen, and all 88 source files with what each one calls |

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
