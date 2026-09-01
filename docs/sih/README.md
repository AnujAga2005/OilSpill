# SpillTrace — SIH 2026 team documents

Six documents. Read them in order once, then use 2, 3, 5 and 6 as reference.

| # | Document | Read it when |
|---|---|---|
| 1 | **[The problem statement, and what we have to build](01-THE-PROBLEM.md)** | first, and again the week before the finals |
| 2 | **[What you need to know](02-WHAT-YOU-NEED-TO-KNOW.md)** | to learn the domain from zero — SAR, drift, AIS. §2.9 is the minimum |
| 3 | **[What we have built, and what is left](03-STATUS-AND-ROADMAP.md)** | for every real number, and the roadmap |
| 4 | **[How to pitch it](04-HOW-TO-PITCH.md)** | deck, demo script, roles, logistics |
| 5 | **[Explaining it to the judges](05-EXPLAINING-TO-JUDGES.md)** | the question bank with written answers |
| 6 | **[The PS-compliance slide](06-PS-COMPLIANCE.md)** | building the one slide that maps each PS clause to a screen and a number — and the handout to leave on the judges' table |

**Every number in these documents was read out of the project's own output files.** If a figure
appears on stage, it should come from document 3.

To run the product: `.venv/bin/python scripts/run_api.py` → http://localhost:8765.
Operational detail is in [RUNBOOK.md](../../RUNBOOK.md).

## The three things nobody has done yet

1. **Watch the NTRO briefing video** — https://www.youtube.com/watch?v=cQoHSStTEdM
2. **Verify our dataset is the Zenodo one** the problem statement recommends
3. **Get an ocean current field covering 11 March 2017** so the forcing label flips to real

The MarineCadastre header is now settled: a real daily extract
(`data/raw/AIS_2022_06_01.csv`) was downloaded and its 17-column header matched byte-for-byte
against what the app emits at `/api/cases/demo/ais.csv`. See document 6, row (c).

One thing does need re-running rather than fetching: the leaky-split retrain in
[`KNOWN-ISSUES.md`](../../KNOWN-ISSUES.md) §1. Every accuracy figure in documents 3 and 6 is an
upper bound until it is done.
