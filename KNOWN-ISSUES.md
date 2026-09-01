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

## 4. Look-alike rejection is untested

The dataset contains no labelled algal blooms, low-wind glassy zones, or biogenic slicks, so the
model's ability to *reject* dark patches that are not oil has never been measured. This is stated
in `data/processed/metrics.json` under `limitations` and on the methodology screen. It is a
dataset gap, not a code gap.

## 5. AIS and ocean forcing are synthetic

By design and clearly labelled everywhere (`AIS mode: Synthetic demonstration data`,
`Drift forcing: Synthetic scenario data`). The problem statement explicitly permits synthetic AIS
where real historic data is unavailable. The supplied CMEMS file does not overlap the demo scene
in time, so deterministic seeded forcing is used instead. Not a defect — but never describe either
as real.
