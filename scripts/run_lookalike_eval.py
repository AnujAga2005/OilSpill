#!/usr/bin/env python3
"""Measure look-alike rejection: the number ``KNOWN-ISSUES.md`` item 4 says is missing.

The supplied dataset contains labelled oil and nothing else, so every accuracy
figure in this repository describes how well the model *delineates* a slick it has
been pointed at. It says nothing about what the model does when shown dark water
that is not oil -- low wind, an algal bloom, a wind shadow behind a headland.

This script closes that gap with two separate measurements, and keeps them
separate because they answer different questions and carry different caveats.

**Same domain.** Dark regions are proposed on this project's own decibel scenes
and labelled by the supplied reference mask. A seven-feature linear screen
(``spilltrace_ml.lookalike``) is fitted and scored under grouped cross-validation
-- grouped by parent Sentinel-1 product, because two crops of one pass on
opposite sides of a fold boundary is exactly the mistake item 1 of the same file
records. This gives sensitivity and specificity in the deployment domain.

**Cross domain.** The fitted screen -- with no retraining, no threshold tuning,
nothing refitted -- is then applied to the DARTIS 2019 no-oil set: 2290 patches
of real, published look-alikes. Every region proposed there is a false positive by
construction, so the rejection rate is a generalisation test against imagery the
screen has never seen, from a different sea, at a different pixel spacing, in
8-bit JPEG rather than calibrated decibels.

With ``--detector`` the U-Net itself is also run over a stratified sample of those
patches, which is the "before" number the screen is an improvement on. That path
needs an assumption the archive does not support -- an 8-bit JPEG cannot be turned
back into decibels -- so it is run under two different mappings and both rates are
reported. A single number there would be a claim the data cannot carry.

Writes ``data/processed/lookalike_metrics.json`` and ``models/lookalike_screen.json``.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for package in ("common", "ml", "drift", "api"):
    sys.path.insert(0, str(ROOT / "services" / package))

from spilltrace_api import case as case_mod  # noqa: E402
from spilltrace_common import config as C  # noqa: E402
from spilltrace_common import dimap  # noqa: E402
from spilltrace_common.geotiff import Affine, GeoTiff  # noqa: E402
from spilltrace_ml import dartis as dartis_mod  # noqa: E402
from spilltrace_ml import dataset as ds  # noqa: E402
from spilltrace_ml import geometry as geometry_mod  # noqa: E402
from spilltrace_ml import lookalike as L  # noqa: E402

DARTIS_DIR = ROOT / "data" / "raw" / "dartis2019"
DARTIS_IMAGES = DARTIS_DIR / "images"
OUT_PATH = ROOT / "data" / "processed" / "lookalike_metrics.json"
MODEL_PATH = ROOT / "models" / "lookalike_screen.json"

# A proposal is called oil only when the reference mask covers most of it, and
# not-oil only when the mask is essentially absent. Everything between is dropped:
# a region half over a slick belongs to neither class and forcing it into one
# teaches the screen noise.
OIL_OVERLAP = 0.5
CLEAN_OVERLAP = 0.05

# The VV/VH means and clip bounds the training normalisation was built from. Used only
# to invent a decibel plane for the 8-bit look-alike patches in the --detector path.
VV, VH = 0, 1

# Buckets for the published overlap histogram. The label rule above is only defensible
# if the two classes really are separated, and this is what shows whether they are.
OVERLAP_BINS = (0.0, 0.001, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 1.001)


def scenes_by_product() -> dict[str, list[str]]:
    """Paired scene ids grouped by parent Sentinel-1 product, read from headers only.

    Decoding a scene costs about fifteen seconds; reading its embedded DIMAP header
    costs half a millisecond. That gap is worth exploiting, because scene ids run in
    acquisition order -- ``00045`` to ``00059`` are fifteen crops of one pass -- so
    taking the first N ids in filename order would fit the screen on a handful of
    passes while appearing to use N independent sites.
    """
    groups: dict[str, list[str]] = {}
    for name in case_mod.available_scenes():
        key: str | None = None
        with GeoTiff(str(C.IMAGE_DIR / f"{name}.tif")) as tif:
            blob = tif.read_bulk_tag(dimap.DIMAP_TAG, dimap.HEADER_BYTES)
        if blob:
            try:
                key = dimap.parse_header(blob).group_key
            except dimap.DimapError:
                key = None
        groups.setdefault(key or name, []).append(name)
    return {key: sorted(value) for key, value in sorted(groups.items())}


def select_scenes(limit: int) -> tuple[list[str], int]:
    """Up to ``limit`` scenes, dealt round-robin across products for diversity.

    One scene from every product first, then a second from each, and so on. At
    ``limit`` at or below the product count this yields exactly one crop per
    satellite pass, which is the most independent evidence per second of decoding
    the dataset can give.
    """
    groups = scenes_by_product()
    ordered: list[str] = []
    depth = 0
    while len(ordered) < (limit or 10**9):
        added = False
        for names in groups.values():
            if depth < len(names):
                ordered.append(names[depth])
                added = True
                if limit and len(ordered) >= limit:
                    break
        if not added:
            break
        depth += 1
    return ordered, len(groups)


def nominal_spacing_m(transform: list[float], height: int) -> float:
    """Square-root of the area of one pixel at the middle row of a scene.

    Latitude changes the ground width of a degree of longitude, so a scene has no
    single pixel size. The middle row is within a few centimetres of every other
    row across 2048 lines and gives one number to resample the look-alike patches
    to.
    """
    middle = np.array([height / 2.0], dtype=np.float64)
    area = float(geometry_mod.row_pixel_area_m2(Affine(*transform), middle)[0])
    return math.sqrt(area)


def collect_scene_rows(
    names: list[str],
    cfg: L.ScreenConfig,
) -> tuple[list[dict[str, Any]], list[int], list[str], dict[str, Any]]:
    """Propose dark regions on our own decibel scenes and label them by the mask.

    The proposer is given no notion of oil, so most of what it returns on a scene
    that contains a slick is not the slick. That is the point: those regions are the
    look-alike class, and the reference mask is what tells the two apart. The
    published overlap histogram is what shows the split is real rather than
    asserted -- in practice a proposal is either almost entirely inside the mask or
    touches it not at all, and the handful in between are dropped.
    """
    rows: list[dict[str, Any]] = []
    labels: list[int] = []
    groups: list[str] = []
    spacings: list[float] = []
    per_scene: list[dict[str, Any]] = []
    histogram = [0] * (len(OVERLAP_BINS) - 1)
    dropped = 0
    unusable = 0
    recalls: list[float] = []

    for index, name in enumerate(names, start=1):
        started = time.perf_counter()
        image_path, mask_path = case_mod.scene_paths(name)
        if mask_path is None:
            continue
        scene = ds.load_scene(image_path, mask_path)
        spacing = nominal_spacing_m(scene.transform, scene.height)
        spacings.append(spacing)
        plane = scene.channels[VV]
        second = scene.channels[VH]
        valid = ~np.asarray(scene.invalid, dtype=bool)
        truth = np.asarray(scene.mask, dtype=bool)

        proposals, count = L.propose(plane, valid, cfg)
        kept = 0
        for label in range(1, count + 1):
            region = proposals == label
            features = L.region_features(
                plane,
                region,
                exclude=(proposals > 0) & ~region | ~valid,
                second_plane=second,
                spacing_m=spacing,
                plane_unit="dB",
                cfg=cfg,
            )
            if features is None:
                unusable += 1
                continue
            overlap = float(truth[region].mean())
            for bucket in range(len(histogram)):
                if OVERLAP_BINS[bucket] <= overlap < OVERLAP_BINS[bucket + 1]:
                    histogram[bucket] += 1
                    break
            if overlap >= OIL_OVERLAP:
                target = 1
            elif overlap <= CLEAN_OVERLAP:
                target = 0
            else:
                dropped += 1
                continue
            features["maskOverlap"] = round(overlap, 4)
            rows.append(features)
            labels.append(target)
            groups.append(scene.group_key or name)
            kept += 1

        recall = float(truth[proposals > 0].sum()) / max(1, int(truth.sum()))
        recalls.append(recall)
        per_scene.append(
            {
                "scene": name,
                "group": scene.group_key or name,
                "proposals": count,
                "labelled": kept,
                "referenceOilFraction": round(float(truth[valid].mean()), 8),
                "maskRecallOfProposer": round(recall, 4),
            }
        )
        print(
            f"  [{index}/{len(names)}] {name}: {count} dark regions, {kept} labelled "
            f"({time.perf_counter() - started:.1f} s)",
            flush=True,
        )

    meta = {
        "scenes": len(per_scene),
        "products": len(set(groups)),
        "regions": len(rows),
        "oilRegions": int(sum(labels)),
        "lookAlikeRegions": len(labels) - int(sum(labels)),
        "droppedAmbiguous": dropped,
        "droppedNoBackground": unusable,
        "spacingM": round(float(np.median(spacings)), 4) if spacings else None,
        "labelRule": (
            f"a dark region counts as oil when the supplied reference mask covers "
            f">= {OIL_OVERLAP:.0%} of it and as not-oil when it covers "
            f"<= {CLEAN_OVERLAP:.0%}; the rest are dropped rather than guessed at"
        ),
        "overlapHistogram": [
            {
                "from": OVERLAP_BINS[i],
                "to": OVERLAP_BINS[i + 1],
                "regions": histogram[i],
            }
            for i in range(len(histogram))
        ],
        "meanMaskRecallOfProposer": round(float(np.mean(recalls)), 4) if recalls else None,
        "proposerNote": (
            "the proposer is the same code that runs on the look-alike set and is given "
            "no reference mask; its recall of the labelled oil is reported so a high "
            "screening score cannot hide a proposer that simply missed the slick"
        ),
        "perScene": per_scene,
    }
    return rows, labels, groups, meta


def cross_validate(
    rows: list[dict[str, Any]],
    labels: list[int],
    groups: list[str],
    folds: int,
    cfg: L.ScreenConfig,
) -> dict[str, Any]:
    """Grouped k-fold: fit on the other folds, score on this one, pool the scores.

    Held-out likelihoods are collected across folds and the AUC is computed once
    over the pooled set, because five AUCs from five small folds average into
    something noisier than one AUC over every held-out region.
    """
    assignment = L.grouped_folds(groups, folds=folds)
    pooled_scores: list[float] = []
    pooled_labels: list[int] = []
    pooled_rows: list[dict[str, Any]] = []
    per_fold: list[dict[str, Any]] = []

    for fold in sorted(set(assignment)):
        train = [i for i, f in enumerate(assignment) if f != fold]
        test = [i for i, f in enumerate(assignment) if f == fold]
        train_labels = [labels[i] for i in train]
        test_labels = [labels[i] for i in test]
        if not test or len(set(train_labels)) < 2:
            continue
        screen = L.fit([rows[i] for i in train], train_labels, cfg=cfg)
        held_rows = [rows[i] for i in test]
        pooled_rows.extend(held_rows)
        pooled_labels.extend(test_labels)
        pooled_scores.extend(screen.likelihood(row) for row in held_rows)
        entry = L.measure(screen, held_rows, test_labels, cfg)
        entry["fold"] = fold
        entry["trainRegions"] = len(train)
        entry["products"] = len({groups[i] for i in test})
        per_fold.append(entry)
        print(
            f"  fold {fold}: {len(test)} held-out regions from {entry['products']} products, "
            f"AUC {entry['auc']}, kept {entry['keptSensitivity']}, "
            f"rejected {entry['rejectionSpecificity']}",
            flush=True,
        )

    return {
        "folds": len(per_fold),
        "grouping": "parent Sentinel-1 product id from the DIMAP header",
        "pooledHeldOut": _pooled_outcomes(pooled_scores, pooled_labels, pooled_rows, cfg),
        "perFold": per_fold,
    }


def _pooled_outcomes(
    scores: list[float],
    labels: list[int],
    rows: list[dict[str, Any]],
    cfg: L.ScreenConfig,
) -> dict[str, Any]:
    """The same table :func:`lookalike.measure` produces, from held-out scores.

    ``measure`` takes one screen; a cross-validated likelihood comes from whichever
    fold's screen did not see that region, so the thresholds have to be applied to
    the scores directly rather than re-derived from a model.
    """

    def table(selected: list[float]) -> dict[str, int]:
        counts = {"accepted": 0, "uncertain": 0, "rejected": 0}
        for value in selected:
            if value <= cfg.reject_at_or_below:
                counts["rejected"] += 1
            elif value >= cfg.accept_at_or_above:
                counts["accepted"] += 1
            else:
                counts["uncertain"] += 1
        return counts

    oil = table([s for s, y in zip(scores, labels) if y > 0.5])
    other = table([s for s, y in zip(scores, labels) if y <= 0.5])
    oil_total = max(1, sum(oil.values()))
    other_total = max(1, sum(other.values()))
    return {
        "regions": len(scores),
        "oilRegions": int(sum(labels)),
        "lookAlikeRegions": len(labels) - int(sum(labels)),
        "oilOutcomes": oil,
        "lookAlikeOutcomes": other,
        "keptSensitivity": round((oil_total - oil["rejected"]) / oil_total, 4),
        "rejectionSpecificity": round(other["rejected"] / other_total, 4),
        "auc": L.auc(scores, labels),
        "singleFeatureAuc": single_feature_auc(rows, labels),
        "definitions": {
            "keptSensitivity": "labelled-oil regions not rejected / all labelled-oil regions",
            "rejectionSpecificity": "look-alike regions rejected / all look-alike regions",
            "uncertain": "likelihood between the two thresholds; kept in the case",
        },
    }


def single_feature_auc(rows: list[dict[str, Any]], labels: list[int]) -> dict[str, float | None]:
    """AUC of each feature used alone, oriented the way its rationale claims.

    A seven-feature model that beats none of its own features individually is a
    model doing nothing, and this is the cheapest way to see that.
    """
    out: dict[str, float | None] = {}
    for spec in L.FEATURES:
        values = [spec.oil_direction * float(row.get(spec.name, 0.0) or 0.0) for row in rows]
        out[spec.name] = L.auc(values, labels)
    return out


# ---------------------------------------------------------------------------
# Cross domain: the published look-alike set
# ---------------------------------------------------------------------------


def _stratum(patch: dartis_mod.Patch) -> str:
    return f"{patch.subset}-{patch.cluster}" if patch.cluster else patch.subset


def collect_dartis_rows(
    patches: list[dartis_mod.Patch],
    images_dir: Path,
    target_spacing_m: float,
    cfg: L.ScreenConfig,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Propose and measure dark regions on look-alike patches, resampled to our scale.

    No labels are used and none are needed: every patch in the no-oil subsets is a
    published look-alike, so every region proposed here is a false positive by
    construction. The resampling is not cosmetic -- the shape features and the
    proposer's minimum area are both in pixels, so a 20 m patch scored at native
    scale would be judged against thresholds meant for 9.5 m ground sampling.
    """
    rows: list[dict[str, Any]] = []
    per_patch = 0
    empty = 0
    unusable = 0
    for index, patch in enumerate(patches, start=1):
        plane, spacing = dartis_mod.load_image(patch, images_dir, target_spacing_m)
        proposals, count = L.propose(plane, None, cfg)
        if count == 0:
            empty += 1
        for label in range(1, count + 1):
            region = proposals == label
            features = L.region_features(
                plane,
                region,
                exclude=(proposals > 0) & ~region,
                spacing_m=spacing,
                plane_unit="8-bit digital number",
                cfg=cfg,
            )
            if features is None:
                unusable += 1
                continue
            features["patch"] = patch.name
            features["subset"] = patch.subset
            features["stratum"] = _stratum(patch)
            rows.append(features)
            per_patch += 1
        if index % 250 == 0 or index == len(patches):
            print(f"  [{index}/{len(patches)}] {len(rows)} regions so far", flush=True)

    return rows, {
        "patches": len(patches),
        "patchesWithNoDarkRegion": empty,
        "regions": len(rows),
        "droppedNoBackground": unusable,
        "resampledToSpacingM": round(float(target_spacing_m), 4),
    }


def screen_on_dartis(
    screen: L.Screen,
    rows: list[dict[str, Any]],
    total_patches: int,
    cfg: L.ScreenConfig,
) -> dict[str, Any]:
    """Rejection rates for the fitted screen on the look-alike set.

    Two rates, because they answer different questions. The region rate is what
    fraction of proposed dark patches the screen throws out. The patch rate is what
    fraction of *images* still come out of the screen carrying something -- which is
    what an operator would experience as a false alarm, since one surviving region
    is enough to raise one.
    """
    outcomes = {"accepted": 0, "uncertain": 0, "rejected": 0}
    by_stratum: dict[str, dict[str, int]] = {}
    by_subset: dict[str, dict[str, int]] = {}
    surviving_patches: set[str] = set()
    for row in rows:
        label = L.verdict(row, screen, cfg, top_reasons=0)["label"]
        outcomes[label] += 1
        for table, key in ((by_stratum, row["stratum"]), (by_subset, row["subset"])):
            bucket = table.setdefault(key, {"accepted": 0, "uncertain": 0, "rejected": 0})
            bucket[label] += 1
        if label != "rejected":
            surviving_patches.add(row["patch"])

    def rate(bucket: dict[str, int]) -> dict[str, Any]:
        total = max(1, sum(bucket.values()))
        return {
            **bucket,
            "regions": sum(bucket.values()),
            "rejectionRate": round(bucket["rejected"] / total, 4),
        }

    total = max(1, sum(outcomes.values()))
    return {
        "regionOutcomes": outcomes,
        "regionRejectionRate": round(outcomes["rejected"] / total, 4),
        "patchesWithSurvivingRegion": len(surviving_patches),
        "patchFalseAlarmRate": (
            round(len(surviving_patches) / total_patches, 4) if total_patches else None
        ),
        "bySubset": {
            key: {**rate(bucket), "label": dartis_mod.SUBSET_LABELS.get(key, key)}
            for key, bucket in sorted(by_subset.items())
        },
        "byCluster": {key: rate(bucket) for key, bucket in sorted(by_stratum.items())},
        "note": (
            "the screen was fitted on this project's decibel scenes only; nothing here "
            "was used to choose a weight, a threshold or a feature"
        ),
    }


# ---------------------------------------------------------------------------
# The "before" number: the U-Net itself on 8-bit look-alikes
# ---------------------------------------------------------------------------

MAPPINGS = {
    "clipRange": (
        "digital numbers 0-255 mapped linearly onto the clip bounds the training "
        "normalisation uses for each polarisation, i.e. assuming the JPEG stretch "
        "spanned exactly that dynamic range"
    ),
    "momentMatch": (
        "each patch standardised by its own mean and standard deviation, then given "
        "the training mean and standard deviation of each polarisation, i.e. assuming "
        "the patch is radiometrically typical of the training scenes"
    ),
}


def to_decibels(plane: np.ndarray, stats: dict[str, Any], mapping: str) -> np.ndarray:
    """Invent a two-channel decibel scene from one 8-bit plane.

    This is the weakest step in the whole script and is why the detector result is
    reported under two mappings rather than one. An 8-bit JPEG has lost the
    calibration, the archive does not publish the stretch that produced it, and it
    is single-channel, so the second polarisation has to be manufactured from the
    first. The synthesised VH is therefore perfectly correlated with VV, which no
    real acquisition is -- the network is being shown an input outside the
    distribution it was fitted on, in a way no mapping can fix.
    """
    values = np.asarray(plane, dtype=np.float32)
    mean = np.asarray(stats["mean"], dtype=np.float32)
    std = np.asarray(stats["std"], dtype=np.float32)
    if mapping == "clipRange":
        low = np.asarray(stats["clipLow"], dtype=np.float32)
        high = np.asarray(stats["clipHigh"], dtype=np.float32)
        unit = np.clip(values / 255.0, 0.0, 1.0)
        return np.stack([low[c] + unit * (high[c] - low[c]) for c in (VV, VH)])
    if mapping == "momentMatch":
        spread = float(values.std())
        z = (values - float(values.mean())) / (spread if spread > 1e-6 else 1.0)
        return np.stack([mean[c] + z * std[c] for c in (VV, VH)])
    raise ValueError(f"unknown mapping {mapping!r}")


def stratified_sample(
    patches: list[dartis_mod.Patch], per_stratum: int, seed: int = 20260904
) -> list[dartis_mod.Patch]:
    """At most ``per_stratum`` patches from each published sub-cluster.

    The paper's K-Means clusters group the look-alikes by phenomenon family, and
    they are wildly unequal in size. Sampling evenly across them keeps the biggest
    family from being the only one measured.
    """
    if per_stratum <= 0:
        return list(patches)
    buckets: dict[str, list[dartis_mod.Patch]] = {}
    for patch in patches:
        buckets.setdefault(_stratum(patch), []).append(patch)
    rng = np.random.default_rng(seed)
    out: list[dartis_mod.Patch] = []
    for key in sorted(buckets):
        group = sorted(buckets[key], key=lambda p: p.name)
        order = rng.permutation(len(group))[:per_stratum]
        out.extend(group[int(i)] for i in sorted(order))
    return out


def _scene_threshold(extra: dict[str, Any]) -> float:
    """The operating threshold, preferring the one chosen on the validation scenes.

    ``scene_metrics.json`` records a whole-scene threshold selected on validation;
    the checkpoint carries the patch-scale one. A look-alike patch is a whole scene
    as far as the detector is concerned, so the scene threshold is the right one --
    with the checkpoint's value as the fallback when that file is absent.
    """
    path = C.PROCESSED_DIR / "scene_metrics.json"
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            stored = {}
        value = stored.get("sceneThreshold")
        if isinstance(value, (int, float)):
            return float(value)
    return float(extra.get("threshold", 0.5))


def _alarm(probability: np.ndarray, threshold: float, min_area_px: int) -> tuple[bool, float]:
    """Whether a detection survives the pipeline's minimum-area rule, and its extent."""
    binary = (probability >= threshold).astype(np.uint8)
    fraction = float(binary.mean())
    if not binary.any():
        return False, fraction
    count, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    biggest = max((int(stats[i, cv2.CC_STAT_AREA]) for i in range(1, count)), default=0)
    return biggest >= min_area_px, fraction


def detector_on_dartis(
    patches: list[dartis_mod.Patch],
    images_dir: Path,
    target_spacing_m: float,
    model,
    stats: dict[str, Any],
    threshold: float,
    cfg: L.ScreenConfig,
    *,
    want_oil_hits: bool = False,
) -> dict[str, Any]:
    """Run the segmentation network over look-alike patches under both mappings.

    ``want_oil_hits`` switches the bookkeeping to the oil subsets: there an alarm is
    the right answer, and what is counted instead is whether it landed inside an
    annotated box. That is the sanity check the false-positive number needs -- a
    mapping that produces no alarms anywhere would otherwise look like a perfect
    rejection rate.
    """
    results: dict[str, Any] = {}
    for mapping, description in MAPPINGS.items():
        alarms = 0
        hits = 0
        fractions: list[float] = []
        by_stratum: dict[str, dict[str, int]] = {}
        started = time.perf_counter()
        for index, patch in enumerate(patches, start=1):
            plane, spacing = dartis_mod.load_image(patch, images_dir, target_spacing_m)
            channels = to_decibels(plane, stats, mapping)
            probability = case_mod.infer_probability(model, channels, stats)
            fired, fraction = _alarm(probability, threshold, cfg.min_area_px)
            fractions.append(fraction)
            alarms += int(fired)
            bucket = by_stratum.setdefault(_stratum(patch), {"patches": 0, "alarms": 0})
            bucket["patches"] += 1
            bucket["alarms"] += int(fired)
            if want_oil_hits and fired:
                boxes = dartis_mod.oil_mask(patch, probability.shape)
                hits += int(bool((probability >= threshold)[boxes].any()))
            if index % 25 == 0 or index == len(patches):
                print(
                    f"  {mapping} [{index}/{len(patches)}] {alarms} alarms "
                    f"({time.perf_counter() - started:.0f} s)",
                    flush=True,
                )

        total = max(1, len(patches))
        entry: dict[str, Any] = {
            "assumption": description,
            "patches": len(patches),
            "patchesWithAlarm": alarms,
            "meanPredictedFraction": round(float(np.mean(fractions)), 6) if fractions else None,
            "byCluster": {
                key: {
                    **bucket,
                    "alarmRate": round(bucket["alarms"] / max(1, bucket["patches"]), 4),
                }
                for key, bucket in sorted(by_stratum.items())
            },
        }
        if want_oil_hits:
            entry["alarmsInsideAnnotatedBox"] = hits
            entry["detectionRate"] = round(alarms / total, 4)
        else:
            entry["falseAlarmRate"] = round(alarms / total, 4)
        results[mapping] = entry
    return results


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _limitations(
    scene_meta: dict[str, Any],
    cross: dict[str, Any] | None,
    detector: dict[str, Any] | None,
) -> list[str]:
    """What these numbers do not establish. Written out so nothing has to be inferred."""
    out = [
        "The look-alike labels in the same-domain half are the absence of oil in the "
        "supplied reference mask, not a positive identification of a phenomenon. A dark "
        "region the mask does not cover may be low wind, a wake, an unlabelled slick, or "
        "a labelling error.",
        "The screen decides between oil and a look-alike for a region that has already "
        "been proposed. It cannot recover a slick the proposer never found, and its "
        "sensitivity is therefore conditional on the proposer, not on the sea.",
        "A region the screen calls uncertain stays in the case. That is deliberate for a "
        "response tool -- suppressing an unsure detection would trade a measured false "
        "positive for an unmeasured missed spill -- but it means the rejection rate is "
        "the rate of outright rejections and not one minus the acceptance rate.",
        f"The screen was fitted on {scene_meta['products']} parent products; a linear "
        "model over seven features on that many independent sites is a screen, not a "
        "classifier of phenomena, and it deliberately never names which look-alike it "
        "thinks it is looking at.",
    ]
    if cross:
        out.append(
            "The look-alike patches are 8-bit JPEG at about 20 m/pixel from a different "
            "sea. They are resampled to this project's ground sampling before scoring and "
            "every model feature is a ratio of same-unit quantities, so the comparison is "
            "defensible -- but the unpublished greyscale stretch remains an untested "
            "assumption behind the invariance claim."
        )
        out.append(
            f"{cross['patchesWithNoDarkRegion']} of {cross['patches']} look-alike patches "
            "produced no dark region at all, so the proposer -- not the screen -- is what "
            "rejected them. They are counted in the patch rate and absent from the region "
            "rate, which is why both are reported."
        )
    if detector:
        out.append(
            "The detector's false-alarm rate is indicative, not a published figure. An "
            "8-bit JPEG cannot be turned back into calibrated decibels and the archive is "
            "single-channel, so the second polarisation is synthesised from the first and "
            "is perfectly correlated with it. Two mappings are reported precisely because "
            "one number would overstate what the data supports."
        )
    if not cross:
        out.append(
            "The published look-alike set was not available on this run, so only the "
            "same-domain figures here have been measured."
        )
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--scenes", type=int, default=270,
        help="scenes to fit the screen on, dealt one per parent product (0 = every one)",
    )
    parser.add_argument(
        "--folds", type=int, default=5, help="grouped cross-validation folds",
    )
    parser.add_argument(
        "--dartis-limit", type=int, default=0,
        help="look-alike patches per published sub-cluster to screen (0 = every one present)",
    )
    parser.add_argument(
        "--detector", action="store_true",
        help="also run the U-Net over look-alike patches for the 'before' false-alarm rate",
    )
    parser.add_argument(
        "--detector-sample", type=int, default=20,
        help="patches per published sub-cluster for --detector (0 = every one present)",
    )
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    parser.add_argument("--model", type=Path, default=MODEL_PATH)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg = L.ScreenConfig()
    started = time.perf_counter()

    names, products = select_scenes(args.scenes)
    if not names:
        print(f"no paired scenes in {C.IMAGE_DIR} - nothing to fit the screen on")
        return 1

    print(
        f"same domain: proposing dark regions on {len(names)} scenes "
        f"from {products} parent products"
    )
    rows, labels, groups, scene_meta = collect_scene_rows(names, cfg)
    if len(set(labels)) < 2:
        print("the proposals came out single-class; cannot fit or measure a screen")
        return 1
    print(
        f"  {scene_meta['regions']} labelled regions "
        f"({scene_meta['oilRegions']} oil, {scene_meta['lookAlikeRegions']} not) "
        f"from {scene_meta['products']} products; "
        f"{scene_meta['droppedAmbiguous']} ambiguous dropped"
    )

    print(f"grouped {args.folds}-fold cross-validation")
    validation = cross_validate(rows, labels, groups, args.folds, cfg)

    screen = L.fit(
        rows,
        labels,
        trained_on={
            "domain": "Sentinel-1 VV decibels, the scenes supplied with this project",
            "productsInDataset": products,
            **{k: v for k, v in scene_meta.items() if k not in ("perScene", "overlapHistogram")},
        },
        cfg=cfg,
    )
    model_path = screen.save(args.model)
    print(f"  fitted screen written to {model_path}")

    target_spacing = scene_meta["spacingM"] or 9.487
    cross: dict[str, Any] | None = None
    detector: dict[str, Any] | None = None
    dartis_meta: dict[str, Any] = {"available": False}

    index_path: Path | None = None
    try:
        index_path = dartis_mod.find_index(DARTIS_DIR)
    except dartis_mod.DartisError as error:
        print(f"cross domain: skipped -- {error}")

    if index_path is not None:
        catalogue = dartis_mod.read_index(index_path)
        present = dartis_mod.present(catalogue, DARTIS_IMAGES)
        look_alikes = [p for p in present if not p.has_oil]
        oil_patches = [p for p in present if p.has_oil]
        dartis_meta = {
            "available": True,
            "source": "doi:10.1594/PANGAEA.980773 (Yang & Singha 2025, CC-BY-4.0)",
            "index": index_path.name,
            "catalogue": dartis_mod.summarise(catalogue),
            "presentOnDisk": dartis_mod.summarise(present),
            "coverage": (
                f"{len(look_alikes)} of "
                f"{dartis_mod.summarise(catalogue)['noOilPatches']} published look-alike "
                f"patches are on disk"
            ),
        }
        selected = look_alikes
        if args.dartis_limit > 0:
            selected = stratified_sample(look_alikes, args.dartis_limit)
        print(f"cross domain: screening {len(selected)} look-alike patches")
        dartis_rows, dartis_rowmeta = collect_dartis_rows(
            selected, DARTIS_IMAGES, target_spacing, cfg
        )
        if dartis_rows:
            cross = {
                **dartis_rowmeta,
                **screen_on_dartis(screen, dartis_rows, len(selected), cfg),
            }
            print(
                f"  region rejection rate {cross['regionRejectionRate']:.1%}, "
                f"patch false-alarm rate {cross['patchFalseAlarmRate']:.1%}"
            )
        else:
            print("  no dark regions were proposed on any look-alike patch")

        if args.detector:
            loaded = case_mod._load_checkpoint()
            if loaded is None:
                print(f"  --detector needs a checkpoint at {C.CHECKPOINT_PATH}; skipped")
            else:
                unet, extra = loaded
                stats = extra.get("normStats") or case_mod._norm_stats()
                threshold = _scene_threshold(extra)
                sample = stratified_sample(look_alikes, args.detector_sample)
                print(
                    f"detector: {len(sample)} look-alike patches x {len(MAPPINGS)} mappings "
                    f"at threshold {threshold:g}"
                )
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
                    "lookAlikes": detector_on_dartis(
                        sample, DARTIS_IMAGES, target_spacing, unet, stats, threshold, cfg
                    ),
                }
                if oil_patches:
                    print(f"detector sanity: {len(oil_patches)} oil patches on disk")
                    detector["oilSanityCheck"] = detector_on_dartis(
                        oil_patches, DARTIS_IMAGES, target_spacing, unet, stats,
                        threshold, cfg, want_oil_hits=True,
                    )
                else:
                    detector["oilSanityCheck"] = None

    payload: dict[str, Any] = {
        "pipelineVersion": C.PIPELINE_VERSION,
        "generatedUtc": C.utc_now_iso(),
        "elapsedSeconds": round(time.perf_counter() - started, 2),
        "question": (
            "Given a dark region in a SAR scene, is it oil or a look-alike? Every other "
            "metric in this project assumes the answer is already oil."
        ),
        "screen": screen.to_dict(),
        "features": L.describe_features(),
        "contextFields": list(L.CONTEXT_FIELDS),
        "sameDomain": {
            "training": scene_meta,
            "crossValidation": validation,
            "note": (
                "fitted and scored on this project's own Sentinel-1 decibel scenes, "
                "labelled by the supplied reference masks"
            ),
        },
        "crossDomain": {"dataset": dartis_meta, "screen": cross, "detector": detector},
        "limitations": _limitations(scene_meta, cross, detector),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    pooled = validation["pooledHeldOut"]
    print()
    print("look-alike screening results")
    print(
        f"  held out, same domain: AUC {pooled['auc']}, "
        f"kept {pooled['keptSensitivity']:.1%} of labelled-oil regions, "
        f"rejected {pooled['rejectionSpecificity']:.1%} of dark water that is not oil"
    )
    if cross:
        print(
            f"  published look-alikes, never trained on: rejected "
            f"{cross['regionRejectionRate']:.1%} of {cross['regions']} regions; "
            f"{cross['patchesWithSurvivingRegion']} of {cross['patches']} patches "
            f"still raise something"
        )
    if detector:
        for mapping, entry in detector["lookAlikes"].items():
            print(
                f"  U-Net alone under {mapping}: {entry['patchesWithAlarm']}"
                f"/{entry['patches']} patches alarm ({entry['falseAlarmRate']:.1%})"
            )
    print(f"  wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
