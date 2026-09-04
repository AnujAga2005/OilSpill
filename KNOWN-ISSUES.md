# Known issues

Open defects, honestly stated. Read this before quoting any number from the project.

---

## 1. The shipped metrics were computed on a leaky split ⚠

**Severity: high. It affects every accuracy figure in the repository and in `docs/sih/`.**

### What is wrong

Splits are supposed to be grouped by parent Sentinel-1 acquisition. The 1,200 files in the
dataset come from only 270 satellite passes, so several files are crops of the *same* image. If
two crops of one pass land on opposite sides of the train/test line, the model is tested on water
it has already been trained on and the score is inflated.

The splitter itself does this correctly. The code that fed it did not.
`annotate_from_audit()` in `services/ml/spilltrace_ml/cache.py` read the acquisition key from the
audit report as `groupKey`, but the audit writes `group_key`. Every key came back `None`, and the
grouping quietly fell back to the scene stem — one group per crop.

### What that did to the numbers

Cross-referencing `data/processed/splits.json` against the real `group_key` in
`data/processed/audit.json`:

| | |
|---|---|
| Scenes used | 240 |
| Groups the split believed it had | 240 (one per crop) |
| Real distinct acquisitions among them | **70** |
| Acquisitions spanning more than one split | **39** |
| — of those, spanning train *and* test | **23** |
| **Test scenes sharing an acquisition with a training scene** | **34 of 36** |

So the reported test scores — patch IoU 0.771, pooled scene IoU 0.782, mean per-scene IoU 0.641 —
are measured almost entirely on acquisitions the model saw during training. **Treat them as an
upper bound.** The comparison against the classical baseline (0.771 vs 0.582) is somewhat more
robust, because the baseline was calibrated and scored on the same leaky split, but it is not
clean either.

### What is already fixed

- `services/ml/spilltrace_ml/cache.py` now accepts both spellings, so the key reaches the
  splitter.
- `tests/test_dataset.py::TestAuditGroupKeysReachTheSplitter` covers the wiring end to end —
  audit report on disk in, single split per acquisition out. `cache.py` previously had no test
  coverage at all, which is why a passing suite did not catch this.

### What is not fixed

**The artefacts have not been regenerated.** `data/processed/splits.json`,
`data/processed/cache/*.npz`, `models/unet_vv_vh.npz`, `data/processed/metrics.json`,
`data/processed/scene_metrics.json` and the stored cases all still come from the leaky run. Their
own `rule` and `splitGrouping` fields claim acquisition-level grouping, and for those files that
claim is false.

### How to regenerate (requires the full dataset)

Needs `Oil/` and `Mask_oil/` present — they are not in the repository, see the README. Expect
roughly an hour end to end; training alone took 1,575 s on the original run.

```bash
.venv/bin/python scripts/run_audit.py && .venv/bin/python scripts/run_preprocess.py && .venv/bin/python scripts/run_train.py && .venv/bin/python scripts/run_scene_eval.py && .venv/bin/python scripts/build_web.py
```

Afterwards, confirm the fix took effect — this should print far fewer groups than scenes, and no
acquisition in more than one split:

```bash
.venv/bin/python -c "import json,collections; s=json.load(open('data/processed/splits.json')); a=json.load(open('data/processed/audit.json')); gk={x['name']:x.get('group_key') for x in a['scenes']}; o=collections.defaultdict(set); [o[gk.get(n)].add(v) for n,v in s['groups'].items()]; print('groups:',len(s['groups']),'| real acquisitions:',len(o),'| spanning >1 split:',sum(1 for v in o.values() if len(v)>1))"
```

Then update every figure in `docs/sih/03-STATUS-AND-ROADMAP.md`, and move the leakage discussion
in docs 3, 4 and 5 into the past tense.

### What to say on stage until then

Do **not** say "no data leakage". Say:

> "Splits are grouped by parent acquisition — and we'll be straight with you, we found a wiring
> bug where that grouping wasn't reaching the splitter. The numbers on screen predate the re-run,
> so treat them as an upper bound. It's fixed and there's a regression test."

Volunteering this is a stronger position than being caught by it. See
`docs/sih/05-EXPLAINING-TO-JUDGES.md` §A.

---

## 2. Traffic is scored but never filtered — **fixed**

Requirement (c) of the problem statement says *"the irrelevant traffic is to be filtered out"*.
`IRRELEVANT_RADII = 3.0` zeroed the proximity component for a distant vessel, but nothing in the
output said how many vessels had been set aside or why. The funnel existed in spirit, not in
output.

`scoring.py` now publishes `attribution.filtering` — report and vessel counts on both sides of
the filter, the two exclusion reasons kept apart, the rule in prose, and a one-line summary. Each
candidate carries `relevant` and `relevanceReason`, both of which travel in the CSV export.
Relevance is now the primary sort key, so an excluded vessel cannot outrank a relevant one on
type and data-quality marks alone. The Vessels screen renders the funnel as a card and dims
excluded rows rather than hiding them.

**Excluded vessels are retained, not deleted.** A shortlist that silently drops eight of ten
vessels cannot be audited, and the cheapest way to hide a scoring bug is to delete the vessels it
mis-ranked. `candidateCount` therefore still counts every vessel; `relevantCount` and
`excludedCount` are new fields beside it.

Covered by `tests/test_scoring.py` — the additive identity
`vesselsSeen == vesselsRelevant + excludedOutsideWindow + excludedTooFar`, the sort order under
weights that would otherwise invert it, and the two exclusion reasons reading differently.

## 3. Spill age is computed but not labelled — **fixed**

Requirement (a) says *"and age if feasible"*. The backward drift produced a 24-hour release
window but nothing named it as an age.

`services/drift/spilltrace_drift/age.py` now publishes `spillAge` on every case: the interval,
its basis, and — the part that matters — whether the hindcast can narrow it. It cannot, for this
scene, and the payload says so with the arithmetic: over 24 h the estimated position moves
**8.556 km against an 11.263 km P90 radius, a ratio of 0.76**, so the whole release window sits
inside its own error bar. The lower bound is 0 h because nothing in a single acquisition rules
out a release minutes before the pass.

**The midpoint is deliberately not reported.** Printing "12 h" would be a fabricated metric. The
card shows the bound, the test used, the best separation achieved, and three data sources that
would genuinely narrow it — a second acquisition, licensed metocean forcing, or an earlier
acquisition showing the area clear.

Resolvability is a per-case property, not a property of the method: a tightly seeded run *is*
resolvable, and `tests/test_age.py` asserts both sides of that pair so the claim stays tied to
the physics rather than to this one scene.

## 4. Look-alike rejection was untested — **now measured**

The dataset contains no labelled algal blooms, low-wind glassy zones or biogenic slicks, so the
model's ability to *reject* dark patches that are not oil had never been measured. Every other
number in this project answers "how well is the slick outlined", having already assumed the dark
patch is oil. That was the largest unquantified claim in the build.

It is now quantified twice, on two different datasets, and the honest summary is that **a dark
patch is not evidence of oil and the U-Net alone treats it as though it were.**

**A dedicated screen was added**, not a retrained detector: `services/ml/spilltrace_ml/lookalike.py`
proposes dark regions, measures seven features of each (darkness against the local background,
its 10th-percentile tail, a texture ratio, edge sharpness, compactness, solidity, elongation) and
scores them with a logistic model. Every feature is a ratio of same-unit quantities, so it
survives a change of radiometric scale. Fitted on 11 623 dark regions from the 270 supplied
parent products — 930 over labelled oil, 10 693 not — with regions between 5 % and 50 % mask
overlap dropped rather than guessed at.

**Held out, same domain** (5-fold cross-validation grouped by parent Sentinel-1 product, so no
scene is scored by a model that saw its sibling): **AUC 0.9573**, keeping **90.2 %** of the
labelled-oil regions while rejecting **89.4 %** of the dark water that is not oil.

**Cross-domain, never trained on** — all **2 290** published look-alike patches of the DARTIS 2019
archive (`doi:10.1594/PANGAEA.980773`, Yang & Singha 2025, CC-BY-4.0), a different sea and a
different sensor product:

| | Screen | U-Net alone, `clipRange` | U-Net alone, `momentMatch` |
| --- | --- | --- | --- |
| Patches raising an alarm | **1 677 of 2 290 (73.2 %)** | 305 of 340 (89.7 %) | **340 of 340 (100 %)** |
| Dark regions rejected | **58 812 of 84 758 (69.4 %)** | — | — |

Read the two columns together, because that contrast is the finding. The U-Net alone alarms on
essentially every look-alike patch it is shown; the screen removes about seven of every ten dark
regions before the case is built. Neither number is good enough to call the problem solved, and
neither is a published benchmark figure — see the caveats below.

**Where the numbers are honest about themselves.** `data/processed/lookalike_metrics.json` carries
seven limitations, and three of them bound the headline:

- The same-domain "look-alike" label is *absence of oil in the reference mask*, not a positive
  identification of a phenomenon. A dark region the mask does not cover may be low wind, a wake,
  an unlabelled slick, or a labelling error.
- The look-alike archive is 8-bit JPEG at about 20 m/pixel and single-channel. It cannot be turned
  back into calibrated decibels, so the second polarisation is synthesised from the first and is
  perfectly correlated with it. **Two mappings are reported precisely because one number would
  overstate what the data supports** — `clipRange` assumes the JPEG stretch spanned the training
  clip bounds, `momentMatch` assumes the patch is radiometrically typical. The detector's
  false-alarm rate is indicative, not a benchmark.
- A region the screen calls *uncertain* stays in the case. That is deliberate for a response tool
  — suppressing an unsure detection trades a measured false positive for an unmeasured missed
  spill — so the rejection rate is the rate of outright rejections and is **not** one minus the
  acceptance rate. 11 794 of the 84 758 regions are in that middle band.
- 126 of the 2 290 patches produced no dark region at all, so the *proposer*, not the screen,
  rejected them. They count in the patch rate and are absent from the region rate, which is why
  both are reported.

**Reproduce it** with `.venv/bin/python scripts/run_lookalike_eval.py` (RUNBOOK.md §5). The
cross-domain half needs the archive on disk first:
`.venv/bin/python scripts/fetch_dartis2019.py --subset nc,nw`. The full eval took **2 h 57 m**;
the same-domain half alone is minutes.

**What is still a dataset gap.** The screen never names *which* look-alike it thinks it is looking
at, because nothing in either dataset labels the phenomenon. Closing that needs Part II of the
source dataset (685 look-alike images with masks, `10.5281/zenodo.8253899`), which would let the
detector itself learn the distinction instead of being screened after the fact.

## 5. AIS and ocean forcing are synthetic

By design and clearly labelled everywhere (`AIS mode: Synthetic demonstration data`,
`Drift forcing: Synthetic scenario data`). The problem statement explicitly permits synthetic AIS
where real historic data is unavailable. The supplied CMEMS file does not overlap the demo scene
in time, so deterministic seeded forcing is used instead. Not a defect — but never describe either
as real.

The forcing half of that is **two products, not one**, and either can become real without the
other. Currents come from CMEMS and wind from ERA5; the shipped state is synthetic on both, but a
drop-in ERA5 file (RUNBOOK.md §6a) makes the wind a measurement while the currents stay synthetic,
and the label changes to `Drift forcing: Synthetic currents with ERA5 wind` on its own. Four labels
cover the four combinations. This matters more than the tidiness suggests: oil moves at ~3% of the
wind, which against this scene's 0.0939 m/s current anchor makes the wind term the same size as the
current. With no wind file at all on the CMEMS path there is no wind term, and the Forcing card
then states that the drift spread is a **lower bound** on where the oil could have gone rather than
implying it is the answer.
