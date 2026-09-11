"""Recompute only the detector half of ``lookalike_metrics.json``.

``run_lookalike_eval.py --detector`` produces two independent halves. The *screen*
half fits a linear model on dark-region features and takes about three hours,
almost all of it pure-Python LZW decoding of 270 scenes. The *detector* half runs
the U-Net over a stratified sample of published look-alike patches and takes about
half an hour. Only the second one depends on the checkpoint.

The checkpoint was retrained on the acquisition-grouped split after that file was
written, which left the stored detector block describing a model that no longer
exists -- and quoting a threshold of 0.8 while citing ``scene_metrics.json``, which
now says 0.7. This script recomputes that block alone, against the current
checkpoint at the current operating point, and writes it back in place.

The screen half is untouched: its region proposer is the classical dark-region
finder, not the network, so retraining cannot have moved it.

    .venv/bin/python scripts/refresh_lookalike_detector.py

To regenerate the whole file from scratch instead (three hours):

    .venv/bin/python scripts/run_lookalike_eval.py --detector
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys
import time
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_eval_module():
    """Import the evaluation script by path; it is a script, not an installed module."""
    spec = importlib.util.spec_from_file_location(
        "run_lookalike_eval", ROOT / "scripts" / "run_lookalike_eval.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    rle = _load_eval_module()
    dartis = rle.dartis_mod
    cfg = rle.L.ScreenConfig()

    out_path = rle.OUT_PATH
    if not out_path.exists():
        print(f"nothing to patch: {out_path} does not exist")
        return 1
    payload: dict[str, Any] = json.loads(out_path.read_text(encoding="utf-8"))

    loaded = rle.case_mod._load_checkpoint()
    if loaded is None:
        print("no checkpoint on disk; nothing to recompute")
        return 1
    unet, extra = loaded
    stats = extra.get("normStats") or rle.case_mod._norm_stats()
    threshold = rle._scene_threshold(extra)

    # The spacing the screen half resampled to, so both halves stay comparable.
    spacing = payload.get("crossDomain", {}).get("screen", {}).get("resampledToSpacingM")
    if not spacing:
        print("stored screen block has no resampledToSpacingM; refusing to guess")
        return 1

    index_path = dartis.find_index(rle.DARTIS_DIR)
    present = dartis.present(dartis.read_index(index_path), rle.DARTIS_IMAGES)
    look_alikes = [p for p in present if not p.has_oil]
    oil_patches = [p for p in present if p.has_oil]

    # Same seed as the original run, so this is the same sample rather than a new draw.
    sample = rle.stratified_sample(look_alikes, 20)
    print(
        f"detector: {len(sample)} look-alike patches x {len(rle.MAPPINGS)} mappings "
        f"at threshold {threshold:g} (checkpoint {rle.C.CHECKPOINT_PATH.name})"
    )

    started = time.perf_counter()
    detector = {
        "threshold": threshold,
        "thresholdSource": (
            "sceneThreshold from scene_metrics.json, the operating point chosen "
            "on the validation scenes"
        ),
        "alarmRule": (
            f"a patch counts as an alarm when a connected region of at least "
            f"{cfg.min_area_px} pixels exceeds the threshold"
        ),
        "lookAlikes": rle.detector_on_dartis(
            sample, rle.DARTIS_IMAGES, spacing, unet, stats, threshold, cfg
        ),
    }
    if oil_patches:
        print(f"detector sanity: {len(oil_patches)} oil patches on disk")
        detector["oilSanityCheck"] = rle.detector_on_dartis(
            oil_patches, rle.DARTIS_IMAGES, spacing, unet, stats,
            threshold, cfg, want_oil_hits=True,
        )
    else:
        detector["oilSanityCheck"] = None

    # Provenance for this block alone: the file's own generatedUtc still describes the
    # screen half, which was not recomputed and must not be given a newer date.
    detector["computedUtc"] = rle.C.utc_now_iso()
    detector["checkpoint"] = rle.C.CHECKPOINT_PATH.name
    # The checkpoint stores its version and training config but not a parameter count,
    # so name the model by the version string it does carry.
    detector["modelVersion"] = extra.get("modelVersion")
    detector["checkpointTrainedUtc"] = extra.get("trainedUtc")
    detector["recomputedNote"] = (
        "Recomputed on its own after the checkpoint was retrained on the "
        "acquisition-grouped split. The screen half of this file predates that retrain "
        "and is unaffected by it: its region proposer is the classical dark-region "
        "finder, not the network."
    )
    detector["elapsedSeconds"] = round(time.perf_counter() - started, 2)

    payload.setdefault("crossDomain", {})["detector"] = detector

    tmp = out_path.with_suffix(".json.tmp")
    # `indent=2` matches what `run_lookalike_eval.py` writes. Anything else reindents all
    # three thousand lines and buries the block this script actually changed.
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, out_path)
    print(f"patched {out_path} in {detector['elapsedSeconds']:.1f}s")

    for mapping, entry in detector["lookAlikes"].items():
        print(
            f"  {mapping}: {entry['patchesWithAlarm']}/{entry['patches']} alarmed "
            f"({entry['patchesWithAlarm'] / max(1, entry['patches']):.1%})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
