#!/usr/bin/env python3
"""Evaluate the trained model on whole 2048 px scenes, not 128 px patches.

Why this exists as a separate script: the patch cache is sampled around labelled oil, so
``metrics.json`` measures how well the model *delineates* a slick it has been pointed at.
It cannot measure the false positives that appear over the other 99% of a scene the
sampler never visited. On scene 00000 -- a training scene -- patch-scale IoU of 0.77
collapses to 0.35 once the whole frame is scored. That gap is a real property of the
system and belongs on the methodology screen, not in a footnote.

The probability field is computed once per scene and then thresholded across a grid, so
adding thresholds costs nothing. The operating threshold is chosen on the *validation*
scenes and only then applied to the test scenes; picking it on test would make the
headline number meaningless.

Writes ``data/processed/scene_metrics.json``.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for package in ("common", "ml", "drift", "api"):
    sys.path.insert(0, str(ROOT / "services" / package))

from spilltrace_api import case as case_mod  # noqa: E402
from spilltrace_common import config as C  # noqa: E402
from spilltrace_ml import dataset as ds  # noqa: E402

THRESHOLDS = [0.5, 0.6, 0.7, 0.8, 0.9, 0.95]


def confusion(prediction: np.ndarray, truth: np.ndarray, valid: np.ndarray) -> dict[str, int]:
    predicted = prediction & valid
    actual = truth & valid
    return {
        "tp": int(np.count_nonzero(predicted & actual)),
        "fp": int(np.count_nonzero(predicted & ~actual)),
        "fn": int(np.count_nonzero(~predicted & actual)),
        "tn": int(np.count_nonzero(~predicted & ~actual & valid)),
    }


def scores(counts: dict[str, int]) -> dict[str, float]:
    tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
    union = tp + fp + fn
    return {
        "iou": round(tp / union, 6) if union else 1.0,
        "dice": round(2 * tp / (2 * tp + fp + fn), 6) if (2 * tp + fp + fn) else 1.0,
        "precision": round(tp / (tp + fp), 6) if (tp + fp) else 0.0,
        "recall": round(tp / (tp + fn), 6) if (tp + fn) else 0.0,
    }


def aggregate(per_scene: list[dict], threshold: float) -> dict[str, object]:
    """Pooled and per-scene-averaged scores.

    Pooled counts are dominated by whichever scenes carry the most oil pixels; the mean
    over scenes weights every scene equally. They answer different questions, so both are
    reported rather than one being picked to look better.
    """
    key = f"{threshold:g}"
    totals = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for entry in per_scene:
        for field, value in entry["thresholds"][key]["counts"].items():
            totals[field] += value
    per = [entry["thresholds"][key]["scores"]["iou"] for entry in per_scene]
    dice = [entry["thresholds"][key]["scores"]["dice"] for entry in per_scene]
    return {
        "threshold": threshold,
        "pooled": scores(totals),
        "meanSceneIou": round(float(np.mean(per)), 6),
        "medianSceneIou": round(float(np.median(per)), 6),
        "worstSceneIou": round(float(np.min(per)), 6),
        "meanSceneDice": round(float(np.mean(dice)), 6),
        "scenes": len(per_scene),
    }


def evaluate_split(names: list[str], model, stats, split: str) -> list[dict]:
    out: list[dict] = []
    for index, name in enumerate(names, start=1):
        started = time.perf_counter()
        image_path, mask_path = case_mod.scene_paths(name)
        if mask_path is None:
            print(f"  [{split} {index}/{len(names)}] {name}: no reference mask, skipped")
            continue
        scene = ds.load_scene(image_path, mask_path)
        probability = case_mod.infer_probability(model, scene.channels, stats)
        probability = np.where(scene.invalid, 0.0, probability)

        truth = np.asarray(scene.mask, dtype=bool)
        valid = ~np.asarray(scene.invalid, dtype=bool)
        entry: dict[str, object] = {
            "scene": name,
            "split": split,
            "referenceOilFraction": round(float(truth[valid].mean()) if valid.any() else 0.0, 8),
            "validFraction": round(float(valid.mean()), 6),
            "thresholds": {},
        }
        for threshold in THRESHOLDS:
            counts = confusion(probability >= threshold, truth, valid)
            entry["thresholds"][f"{threshold:g}"] = {
                "counts": counts,
                "scores": scores(counts),
                "predictedOilFraction": round(
                    float((probability >= threshold)[valid].mean()) if valid.any() else 0.0, 8
                ),
            }
        out.append(entry)
        best = entry["thresholds"]["0.6"]["scores"]
        print(
            f"  [{split} {index}/{len(names)}] {name}: IoU@0.6 {best['iou']:.4f} "
            f"dice {best['dice']:.4f} ({time.perf_counter() - started:.1f} s)",
            flush=True,
        )
    return out


def main() -> int:
    loaded = case_mod._load_checkpoint()
    if loaded is None:
        print("no checkpoint at", C.CHECKPOINT_PATH, "- run scripts/run_train.py first")
        return 1
    model, extra = loaded
    stats = extra.get("normStats") or case_mod._norm_stats()
    patch_threshold = float(extra.get("threshold", 0.5))

    splits = json.loads((C.PROCESSED_DIR / "splits.json").read_text())
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None

    results: dict[str, list[dict]] = {}
    for split in ("val", "test"):
        names = sorted(splits["scenes"][split])
        if limit:
            names = names[:limit]
        print(f"{split}: {len(names)} whole scenes")
        results[split] = evaluate_split(names, model, stats, split)

    val_grid = [aggregate(results["val"], t) for t in THRESHOLDS]
    # Selected on validation only. Mean-over-scenes is the criterion because an operator
    # cares about the typical scene, not about the one with the most oil pixels in it.
    chosen = max(val_grid, key=lambda entry: entry["meanSceneIou"])
    selected = float(chosen["threshold"])

    payload = {
        "pipelineVersion": C.PIPELINE_VERSION,
        "generatedUtc": C.utc_now_iso(),
        "checkpoint": C.CHECKPOINT_PATH.name,
        "modelParameters": int(model.parameter_count()),
        "note": (
            "Whole-scene evaluation at 2048x2048. The patch metrics in metrics.json are "
            "measured on 128 px crops sampled around labelled oil and therefore do not "
            "include the false positives that only appear at scene scale."
        ),
        "patchThreshold": patch_threshold,
        "sceneThreshold": selected,
        "thresholdSelection": (
            "chosen on the validation scenes by mean per-scene IoU, then applied unchanged "
            "to the test scenes"
        ),
        "thresholdGrid": THRESHOLDS,
        "validation": {"grid": val_grid, "atSelected": aggregate(results["val"], selected)},
        "test": {
            "atPatchThreshold": aggregate(results["test"], patch_threshold),
            "atSceneThreshold": aggregate(results["test"], selected),
            "grid": [aggregate(results["test"], t) for t in THRESHOLDS],
        },
        "perScene": results["val"] + results["test"],
        "limitations": [
            "Every supplied scene contains labelled oil, so these figures still measure "
            "delineation on scenes known to contain a slick and are not a false-alarm rate "
            "on clean sea.",
            "Scene-scale IoU is markedly lower than patch-scale IoU because the patch "
            "sampler never visited most of each frame; the scene figure is the one that "
            "reflects operating on a full acquisition.",
            "The reference masks are the supplied labels and are treated as ground truth; "
            "their own accuracy is unknown.",
        ],
    }
    out_path = C.PROCESSED_DIR / "scene_metrics.json"
    out_path.write_text(json.dumps(payload, indent=2))

    test_patch = payload["test"]["atPatchThreshold"]
    test_scene = payload["test"]["atSceneThreshold"]
    print()
    print("whole-scene results")
    print(f"  scene threshold selected on validation: {selected:g} (patch threshold {patch_threshold:g})")
    print(
        f"  test @ {patch_threshold:g}: pooled IoU {test_patch['pooled']['iou']:.4f} "
        f"mean-scene IoU {test_patch['meanSceneIou']:.4f}"
    )
    print(
        f"  test @ {selected:g}: pooled IoU {test_scene['pooled']['iou']:.4f} "
        f"mean-scene IoU {test_scene['meanSceneIou']:.4f} "
        f"precision {test_scene['pooled']['precision']:.4f} recall {test_scene['pooled']['recall']:.4f}"
    )
    print(f"  worst test scene IoU: {test_scene['worstSceneIou']:.4f}")
    print(f"  wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
