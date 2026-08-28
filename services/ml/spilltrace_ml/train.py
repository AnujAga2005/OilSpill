"""Phase 3: train and honestly evaluate the oil-spill segmentation model.

The protocol is fixed before any number is produced, so nothing can be tuned on
the held-out data:

* the split is by parent Sentinel-1 acquisition (``dataset.make_splits``), so no
  crop of one product appears in two splits;
* normalisation statistics come from the training split only;
* the decision threshold is chosen on the validation split by maximising IoU, and
  the whole sweep is recorded so the trade-off stays visible;
* the test split is evaluated exactly once, at that threshold;
* a classical dark-spot detector is calibrated on the same validation split and
  reported next to the model, so the model's score has a reference point;
* every metric ignores ``LABEL_INVALID`` pixels.

What the numbers cannot say is stated in the output rather than left implied: the
supplied dataset contains only scenes that already contain oil, so it can measure
delineation quality but not the false-alarm rate on clean sea, and it contains no
labelled look-alikes (algal blooms, low-wind zones, rain cells) at all.
"""

from __future__ import annotations

import time
from typing import Any, Callable

import numpy as np

from spilltrace_common import config as C

from . import baseline as baseline_mod
from . import cache as cache_mod
from . import nn
from .dataset import LABEL_INVALID, LABEL_OIL, normalise_batch
from .metrics import (
    best_threshold,
    combined_loss,
    evaluate,
    per_patch_metrics,
    threshold_sweep,
)
from .model import UNet, make_optimizer
from .preview import render_comparison, write_png

Say = Callable[[str], None]

# Fixed per-split seed offsets. ``hash()`` on a string varies with
# PYTHONHASHSEED, which would make the subsample non-reproducible.
SPLIT_SEED_OFFSET = {"train": 0, "val": 101, "test": 202}


# ---------------------------------------------------------------------------
# Data handling
# ---------------------------------------------------------------------------


def subsample(
    channels: np.ndarray,
    targets: np.ndarray,
    limit: int,
    seed: int,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Cap a split deterministically, keeping every oil-bearing patch first.

    A NumPy training run has a real time budget, so the cache may be larger than
    one epoch can afford. Dropping patches at random would drop positives, which
    are the scarce class; positives are kept and negatives are thinned instead.
    """
    total = int(targets.shape[0])
    info = {"available": total, "used": total, "rule": "no subsampling applied"}
    if limit <= 0 or total <= limit:
        return channels, targets, info
    oil = np.asarray([(t == LABEL_OIL).any() for t in targets])
    positive_idx = np.flatnonzero(oil)
    negative_idx = np.flatnonzero(~oil)
    rng = np.random.default_rng(seed)
    if positive_idx.size >= limit:
        keep = rng.choice(positive_idx, size=limit, replace=False)
        rule = "positives only; the cache holds more oil-bearing patches than the budget"
    else:
        room = limit - positive_idx.size
        take = min(room, negative_idx.size)
        keep = np.concatenate(
            [positive_idx, rng.choice(negative_idx, size=take, replace=False)]
        )
        rule = "every oil-bearing patch kept; negatives thinned at random to fit"
    keep = np.sort(keep)
    info = {"available": total, "used": int(keep.size), "rule": rule}
    return channels[keep], targets[keep], info


def augment_batch(
    x: np.ndarray, target: np.ndarray, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray]:
    """Random flip and 90-degree rotation, applied identically to input and label.

    These are the only geometric transforms that are physically free here: a SAR
    scene has no canonical orientation, and neither operation alters a decibel
    value. Anything that rescales intensity would corrupt the very contrast the
    model is being taught to read.
    """
    out_x = x
    out_t = target
    if rng.random() < 0.5:
        out_x = out_x[:, ::-1, :, :]
        out_t = out_t[:, ::-1, :]
    if rng.random() < 0.5:
        out_x = out_x[:, :, ::-1, :]
        out_t = out_t[:, :, ::-1]
    turns = int(rng.integers(0, 4))
    if turns:
        out_x = np.rot90(out_x, turns, axes=(1, 2))
        out_t = np.rot90(out_t, turns, axes=(1, 2))
    return np.ascontiguousarray(out_x), np.ascontiguousarray(out_t)


def cosine_lr(step: int, total: int, base: float, final_fraction: float) -> float:
    """Cosine decay from ``base`` to ``base * final_fraction`` over ``total`` steps."""
    if total <= 1:
        return base
    progress = min(1.0, max(0.0, step / float(total - 1)))
    cosine = 0.5 * (1.0 + np.cos(np.pi * progress))
    return float(base * (final_fraction + (1.0 - final_fraction) * cosine))


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


def predict_probabilities(model: UNet, x: np.ndarray, batch_size: int) -> np.ndarray:
    """Inference over a whole split in bounded batches."""
    if x.shape[0] == 0:
        return np.zeros(x.shape[:3], dtype=np.float32)
    return np.asarray(model.predict_proba(x, batch_size), dtype=np.float32)


def run_epoch(
    model: UNet,
    optimiser: Any,
    x: np.ndarray,
    targets: np.ndarray,
    cfg: C.TrainConfig,
    rng: np.random.Generator,
    epoch: int,
    total_epochs: int,
    say: Say,
) -> dict[str, float]:
    """One pass over the training split; returns the mean loss components."""
    order = rng.permutation(x.shape[0])
    batches = max(1, int(np.ceil(x.shape[0] / cfg.batch_size)))
    sums = {"total": 0.0, "bce": 0.0, "dice": 0.0, "gradNorm": 0.0}
    counted = 0
    started = time.time()
    for index in range(batches):
        picked = order[index * cfg.batch_size : (index + 1) * cfg.batch_size]
        if picked.size == 0:
            continue
        batch_x = x[picked]
        batch_t = targets[picked]
        if cfg.augment:
            batch_x, batch_t = augment_batch(batch_x, batch_t, rng)
        step = epoch * batches + index
        optimiser.lr = cosine_lr(
            step, total_epochs * batches, cfg.learning_rate, cfg.final_lr_fraction
        )
        optimiser.zero_grad()
        logits = model.forward(batch_x, training=True)
        loss, grad, parts = combined_loss(
            logits,
            batch_t,
            dice_weight=cfg.dice_weight,
            bce_weight=cfg.bce_weight,
            pos_weight=cfg.positive_weight,
        )
        model.backward(grad)
        norm = nn.clip_gradients(model.parameters(), cfg.grad_clip_norm)
        optimiser.step()
        sums["total"] += parts["total"]
        sums["bce"] += parts["bce"]
        sums["dice"] += parts["dice"]
        sums["gradNorm"] += norm
        counted += 1
        if counted == 1 or counted % 25 == 0 or index == batches - 1:
            elapsed = time.time() - started
            say(
                f"    batch {counted}/{batches} loss {parts['total']:.4f} "
                f"(bce {parts['bce']:.4f} dice {parts['dice']:.4f}) "
                f"lr {optimiser.lr:.2e} grad {norm:.2f} "
                f"{elapsed / counted:.2f} s/batch"
            )
    divisor = max(1, counted)
    return {key: value / divisor for key, value in sums.items()}


def train(
    cfg: C.TrainConfig | None = None,
    progress: Say | None = None,
    sample_count: int = 8,
) -> dict[str, Any]:
    """Train, select a threshold, evaluate once on test, and write ``metrics.json``."""
    cfg = cfg or C.TrainConfig()
    say = progress or (lambda _msg: None)
    C.ensure_dirs()

    manifest = cache_mod.load_manifest()
    stats = C.read_json(C.NORM_STATS_PATH)
    if stats is None:
        raise FileNotFoundError(f"{C.NORM_STATS_PATH} is missing; run preprocessing first")

    raw: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    usage: dict[str, dict[str, Any]] = {}
    limits = {
        "train": cfg.max_train_patches,
        "val": cfg.max_val_patches,
        "test": cfg.max_test_patches,
    }
    for split in cache_mod.SPLITS:
        channels, targets = cache_mod.load_split(split)
        channels, targets, info = subsample(
            channels, targets, limits[split], cfg.seed + SPLIT_SEED_OFFSET[split]
        )
        raw[split] = (channels, targets)
        usage[split] = info
        oil_pixels = int((targets == LABEL_OIL).sum())
        valid_pixels = int((targets != LABEL_INVALID).sum())
        info["patches"] = int(targets.shape[0])
        info["oilPixelFraction"] = round(oil_pixels / max(1, valid_pixels), 6)
        say(
            f"{split}: {targets.shape[0]} patches of {info['available']} "
            f"({info['oilPixelFraction'] * 100:.2f} % oil among valid pixels)"
        )

    if raw["train"][0].shape[0] == 0:
        raise ValueError("the training split is empty; rerun scripts/run_preprocess.py")

    inputs = {
        split: normalise_batch(channels, stats) for split, (channels, _t) in raw.items()
    }
    targets = {split: targets for split, (_c, targets) in raw.items()}

    model = UNet(
        in_channels=inputs["train"].shape[-1],
        base_channels=cfg.base_channels,
        depth=cfg.depth,
        seed=cfg.seed,
    )
    optimiser = make_optimizer(model, cfg.learning_rate, cfg.weight_decay)
    say(
        f"model {model.parameter_count()} parameters, "
        f"base {cfg.base_channels} depth {cfg.depth}, "
        f"{inputs['train'].shape[1]}x{inputs['train'].shape[2]} patches"
    )

    rng = np.random.default_rng(cfg.seed)
    history: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None
    best_state: dict[str, np.ndarray] | None = None
    stale = 0
    started = time.time()

    for epoch in range(cfg.epochs):
        epoch_started = time.time()
        losses = run_epoch(
            model,
            optimiser,
            inputs["train"],
            targets["train"],
            cfg,
            rng,
            epoch,
            cfg.epochs,
            say,
        )
        probability = predict_probabilities(model, inputs["val"], cfg.batch_size)
        val = evaluate(probability, targets["val"], cfg.threshold)
        train_probability = predict_probabilities(
            model, inputs["train"][: min(200, inputs["train"].shape[0])], cfg.batch_size
        )
        train_metrics = evaluate(
            train_probability,
            targets["train"][: train_probability.shape[0]],
            cfg.threshold,
        )
        record = {
            "epoch": epoch + 1,
            "learningRate": round(optimiser.lr, 8),
            "trainLoss": round(losses["total"], 6),
            "trainBce": round(losses["bce"], 6),
            "trainDice": round(losses["dice"], 6),
            "gradNorm": round(losses["gradNorm"], 4),
            "trainIou": train_metrics["iou"],
            "valIou": val["iou"],
            "valDice": val["dice"],
            "valPrecision": val["precision"],
            "valRecall": val["recall"],
            "seconds": round(time.time() - epoch_started, 2),
        }
        history.append(record)
        say(
            f"  epoch {epoch + 1}/{cfg.epochs} loss {record['trainLoss']:.4f} "
            f"train IoU {record['trainIou']} val IoU {record['valIou']} "
            f"val dice {record['valDice']} ({record['seconds']:.0f} s)"
        )

        score = val["dice"] or 0.0
        if best is None or score > (best["valDice"] or 0.0):
            best = record
            best_state = {k: v.copy() for k, v in model.state_dict().items()}
            stale = 0
        else:
            stale += 1
            if stale >= cfg.patience:
                say(f"  early stop: no val Dice improvement for {stale} epochs")
                break

    if best_state is not None:
        model.load_state_dict(best_state)
    training_seconds = time.time() - started
    say(f"training finished in {training_seconds / 60.0:.1f} min")

    # -- threshold selection, on validation only ---------------------------
    val_probability = predict_probabilities(model, inputs["val"], cfg.batch_size)
    sweep = threshold_sweep(val_probability, targets["val"])
    choice = best_threshold(sweep, "iou")
    threshold = float(choice["threshold"])
    say(f"threshold {threshold} selected on validation (IoU {choice['value']})")

    val_metrics = evaluate(val_probability, targets["val"], threshold)

    # -- the single test-split evaluation ----------------------------------
    test_probability = predict_probabilities(model, inputs["test"], cfg.batch_size)
    test_metrics = evaluate(test_probability, targets["test"], threshold)
    test_patch_metrics = per_patch_metrics(test_probability, targets["test"], threshold)
    say(
        f"test IoU {test_metrics['iou']} dice {test_metrics['dice']} "
        f"precision {test_metrics['precision']} recall {test_metrics['recall']}"
    )

    # -- classical baseline, calibrated on the same validation split -------
    say("calibrating the classical dark-spot baseline")
    calibration = baseline_mod.calibrate(
        raw["val"][0], targets["val"], progress=say
    )
    baseline_cfg = baseline_mod.config_from_dict(calibration["config"])
    baseline_test = baseline_mod.evaluate_detections(
        baseline_mod.predict_batch(raw["test"][0], targets["test"], baseline_cfg),
        targets["test"],
    )
    say(
        f"baseline ({baseline_cfg.mode}, {baseline_cfg.contrast_db} dB) "
        f"test IoU {baseline_test['iou']} dice {baseline_test['dice']}"
    )

    checkpoint = model.save(
        C.CHECKPOINT_PATH,
        extra={
            "modelVersion": C.MODEL_VERSION,
            "pipelineVersion": C.PIPELINE_VERSION,
            "trainConfig": cfg.to_dict(),
            "normStats": stats,
            "threshold": threshold,
            "trainedUtc": C.utc_now_iso(),
            "testIou": test_metrics["iou"],
        },
    )
    say(f"checkpoint {checkpoint} ({checkpoint.stat().st_size / 1e6:.2f} MB)")

    samples = write_samples(
        raw["test"][0],
        targets["test"],
        test_probability,
        threshold,
        sample_count,
        say,
    )

    report = {
        "pipelineVersion": C.PIPELINE_VERSION,
        "modelVersion": C.MODEL_VERSION,
        "baselineVersion": C.BASELINE_VERSION,
        "generatedUtc": C.utc_now_iso(),
        "label": C.LABEL_PREDICTION,
        "status": C.LABEL_STATUS,
        "protocol": {
            "cacheSplits": manifest["splits"],
            "splitGrouping": (
                "train/val/test are assigned by parent Sentinel-1 acquisition, so no "
                "crop of one product appears in more than one split"
            ),
            "normalisationSource": stats["source"],
            "thresholdSelection": choice,
            "testEvaluations": 1,
            "invalidPixels": "excluded from the loss and from every reported metric",
        },
        "model": model.describe(),
        "trainConfig": cfg.to_dict(),
        "dataUsage": usage,
        "trainingSeconds": round(training_seconds, 1),
        "history": history,
        "bestEpoch": best,
        "thresholdSweepValidation": sweep,
        "validation": val_metrics,
        "test": test_metrics,
        "testPerPatch": test_patch_metrics,
        "baseline": {
            "method": (
                "despeckled VV, threshold at a calibrated decibel offset below a "
                "clean-water reference, morphological opening and a minimum-area filter"
            ),
            "version": C.BASELINE_VERSION,
            "calibration": {k: v for k, v in calibration.items() if k != "sweep"},
            "calibrationSweep": calibration["sweep"],
            "test": baseline_test,
        },
        "comparison": {
            "modelTestIou": test_metrics["iou"],
            "baselineTestIou": baseline_test["iou"],
            "iouDelta": (
                round((test_metrics["iou"] or 0.0) - (baseline_test["iou"] or 0.0), 6)
            ),
        },
        "samples": samples,
        "limitations": [
            "Every supplied scene contains labelled oil, so these figures measure "
            "delineation quality on scenes already known to contain a slick; they are "
            "not a false-alarm rate on clean sea.",
            "The dataset contains no labelled look-alikes (algal blooms, low-wind "
            "zones, rain cells, ship wakes), so the model's ability to reject them is "
            "untested and unquantified here.",
            "Metrics are computed on 128 px patches sampled from the scenes rather "
            "than on whole 2048 px scenes, so they do not include errors that only "
            "appear at scene scale.",
            "The reference masks are the supplied labels; their own accuracy is "
            "unknown and is treated as ground truth throughout.",
        ],
    }
    C.write_json(C.METRICS_PATH, report)
    say(f"wrote {C.METRICS_PATH}")
    return report


def write_samples(
    channels: np.ndarray,
    targets: np.ndarray,
    probability: np.ndarray,
    threshold: float,
    count: int,
    say: Say,
) -> list[dict[str, Any]]:
    """Render comparison panels for a spread of test patches, best to worst."""
    if count <= 0 or targets.shape[0] == 0:
        return []
    prediction = probability >= threshold
    valid = targets != LABEL_INVALID
    reference = targets == LABEL_OIL
    scores: list[tuple[float, int]] = []
    for index in range(targets.shape[0]):
        pred = prediction[index] & valid[index]
        ref = reference[index] & valid[index]
        union = int((pred | ref).sum())
        if not int(ref.sum()):
            continue
        scores.append((int((pred & ref).sum()) / union if union else 0.0, index))
    if not scores:
        return []
    scores.sort(reverse=True)
    # A spread, not a highlight reel: best, worst and evenly spaced in between.
    positions = np.unique(np.linspace(0, len(scores) - 1, min(count, len(scores))).astype(int))
    out: list[dict[str, Any]] = []
    out_dir = C.PREVIEW_DIR / "eval"
    for rank in positions:
        iou, index = scores[rank]
        panel = render_comparison(
            channels[index][0],
            ~valid[index],
            reference[index],
            prediction[index] & valid[index],
        )
        name = f"test_{index:04d}_iou{int(round(iou * 100)):03d}.png"
        write_png(out_dir / name, panel)
        out.append(
            {
                "file": f"eval/{name}",
                "patchIndex": int(index),
                "iou": round(float(iou), 6),
                "referenceOilFraction": round(float(reference[index].mean()), 6),
                "predictedOilFraction": round(
                    float((prediction[index] & valid[index]).mean()), 6
                ),
            }
        )
    say(f"wrote {len(out)} comparison panels to {out_dir}")
    return out
