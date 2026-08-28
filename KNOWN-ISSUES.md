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
  coverage at all, which is why 432 passing tests did not catch this.

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

## 2. Traffic is scored but never filtered

Requirement (c) of the problem statement says *"the irrelevant traffic is to be filtered out"*.
`services/drift/spilltrace_drift/scoring.py` defines `IRRELEVANT_RADII = 3.0`, which zeroes the
proximity component for a distant vessel, but `rank_vessels()` sets
`candidateCount = len(scored)` — every vessel stays in the list and no exclusion count is
reported. The funnel exists in spirit, not in output. Low effort to fix: filter, and publish
`considered` / `excluded` counts.

## 3. Spill age is computed but not labelled

Requirement (a) says *"and age if feasible"*. The backward drift already produces a 24-hour
release window (`attribution.releaseWindow`), which is the age estimate — no field or UI label
names it as one. Low effort: surface it as "estimated slick age" with the window as its
uncertainty.

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
