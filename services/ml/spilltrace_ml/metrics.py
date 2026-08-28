"""Masked losses and segmentation metrics.

Every function here ignores pixels marked ``LABEL_INVALID``. That matters for
honesty as much as for training: the supplied scenes carry large no-data borders,
and counting those zero-filled pixels as correctly-classified background would
inflate accuracy and specificity without the model having learned anything.

Metrics reported: IoU, Dice/F1, precision, recall, accuracy and false-positive
rate, all on held-out data, plus a threshold sweep so the operating point is a
recorded choice rather than a hidden default.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

import numpy as np

from .dataset import LABEL_INVALID, LABEL_OIL
from . import nn
from .nn import sigmoid

DICE_SMOOTH = 1.0


def split_target(target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(oil, valid)`` boolean arrays from a packed label array."""
    labels = np.asarray(target)
    valid = labels != LABEL_INVALID
    oil = labels == LABEL_OIL
    return oil, valid


def bce_with_logits(
    logits: np.ndarray,
    oil: np.ndarray,
    valid: np.ndarray,
    pos_weight: float = 1.0,
) -> tuple[float, np.ndarray]:
    """Masked binary cross-entropy, computed in a numerically stable form."""
    z = np.asarray(logits, dtype=np.float64)
    y = oil.astype(np.float64)
    mask = valid.astype(np.float64)
    count = max(float(mask.sum()), 1.0)
    weight = 1.0 + (pos_weight - 1.0) * y
    # log(1 + exp(-|z|)) + max(z, 0) - z*y is stable for both signs of z.
    per_pixel = weight * (np.maximum(z, 0.0) - z * y + np.log1p(np.exp(-np.abs(z))))
    loss = float((per_pixel * mask).sum() / count)
    grad = weight * (sigmoid(z) - y)
    return loss, (grad * mask / count).astype(nn.DTYPE)


def soft_dice(
    logits: np.ndarray,
    oil: np.ndarray,
    valid: np.ndarray,
    smooth: float = DICE_SMOOTH,
) -> tuple[float, np.ndarray]:
    """Masked soft Dice loss on probabilities, with its gradient wrt logits."""
    probability = sigmoid(np.asarray(logits, dtype=np.float64))
    y = oil.astype(np.float64)
    mask = valid.astype(np.float64)
    p = probability * mask
    intersection = float((p * y).sum())
    p_sum = float(p.sum())
    y_sum = float((y * mask).sum())
    denominator = p_sum + y_sum + smooth
    dice = (2.0 * intersection + smooth) / denominator
    loss = 1.0 - dice
    # d(1 - dice)/dp = -(2y * denom - (2*inter + smooth)) / denom^2
    grad_p = -(2.0 * y * denominator - (2.0 * intersection + smooth)) / (
        denominator * denominator
    )
    grad = grad_p * probability * (1.0 - probability) * mask
    return loss, grad.astype(nn.DTYPE)


def combined_loss(
    logits: np.ndarray,
    target: np.ndarray,
    dice_weight: float = 0.5,
    bce_weight: float = 0.5,
    pos_weight: float = 1.0,
) -> tuple[float, np.ndarray, dict[str, float]]:
    """Dice + BCE, the pairing the audit's class imbalance calls for.

    Dice alone is unstable on empty patches; BCE alone is dominated by the ~97 %
    background the audit measured. Together the loss stays informative for both.
    """
    oil, valid = split_target(target)
    bce_value, bce_grad = bce_with_logits(logits, oil, valid, pos_weight)
    dice_value, dice_grad = soft_dice(logits, oil, valid)
    total = bce_weight * bce_value + dice_weight * dice_value
    grad = bce_weight * bce_grad + dice_weight * dice_grad
    return total, grad, {"bce": bce_value, "dice": dice_value, "total": total}


# ---------------------------------------------------------------------------
# Confusion-matrix metrics
# ---------------------------------------------------------------------------


def confusion(
    probability: np.ndarray, target: np.ndarray, threshold: float = 0.5
) -> dict[str, int]:
    """Counts over valid pixels only."""
    oil, valid = split_target(target)
    predicted = (np.asarray(probability) >= threshold) & valid
    actual = oil & valid
    return {
        "truePositive": int((predicted & actual).sum()),
        "falsePositive": int((predicted & ~actual & valid).sum()),
        "falseNegative": int((~predicted & actual).sum()),
        "trueNegative": int((~predicted & ~actual & valid).sum()),
        "validPixels": int(valid.sum()),
        "invalidPixels": int((~valid).sum()),
    }


def metrics_from_confusion(counts: dict[str, int]) -> dict[str, Any]:
    tp = counts["truePositive"]
    fp = counts["falsePositive"]
    fn = counts["falseNegative"]
    tn = counts["trueNegative"]
    total = max(1, tp + fp + fn + tn)

    def ratio(numerator: float, denominator: float) -> float | None:
        return round(numerator / denominator, 6) if denominator else None

    precision = ratio(tp, tp + fp)
    recall = ratio(tp, tp + fn)
    dice = ratio(2 * tp, 2 * tp + fp + fn)
    return {
        **counts,
        "iou": ratio(tp, tp + fp + fn),
        "dice": dice,
        "f1": dice,  # identical by definition for a binary mask
        "precision": precision,
        "recall": recall,
        "accuracy": round((tp + tn) / total, 6),
        "specificity": ratio(tn, tn + fp),
        "falsePositiveRate": ratio(fp, fp + tn),
        "balancedAccuracy": (
            round((recall + ratio(tn, tn + fp)) / 2.0, 6)
            if recall is not None and ratio(tn, tn + fp) is not None
            else None
        ),
        "predictedOilFraction": round((tp + fp) / total, 6),
        "referenceOilFraction": round((tp + fn) / total, 6),
    }


def evaluate(
    probability: np.ndarray, target: np.ndarray, threshold: float = 0.5
) -> dict[str, Any]:
    return metrics_from_confusion(confusion(probability, target, threshold))


def threshold_sweep(
    probability: np.ndarray,
    target: np.ndarray,
    thresholds: Sequence[float] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate a grid of operating points so the chosen threshold is justified."""
    grid = thresholds or [round(0.05 * i, 2) for i in range(1, 20)]
    return [{"threshold": t, **evaluate(probability, target, t)} for t in grid]


def best_threshold(sweep: Sequence[dict[str, Any]], key: str = "iou") -> dict[str, Any]:
    """Pick the operating point that maximises ``key``; ties go to the lower threshold."""
    scored = [row for row in sweep if row.get(key) is not None]
    if not scored:
        return {"threshold": 0.5, "reason": f"no operating point produced a {key}"}
    best = max(scored, key=lambda row: (row[key], -row["threshold"]))
    return {
        "threshold": best["threshold"],
        "selectedBy": key,
        "value": best[key],
        "reason": (
            f"chosen on the validation split by maximising {key}; the full sweep is "
            "recorded alongside so the trade-off is visible"
        ),
    }


def per_patch_metrics(
    probability: np.ndarray, target: np.ndarray, threshold: float
) -> dict[str, Any]:
    """Distribution of per-patch IoU, which a pooled figure can hide."""
    scores: list[float] = []
    empty_correct = 0
    empty_total = 0
    for index in range(probability.shape[0]):
        counts = confusion(probability[index], target[index], threshold)
        denominator = (
            counts["truePositive"] + counts["falsePositive"] + counts["falseNegative"]
        )
        if counts["truePositive"] + counts["falseNegative"] == 0:
            empty_total += 1
            if counts["falsePositive"] == 0:
                empty_correct += 1
            continue
        scores.append(counts["truePositive"] / denominator if denominator else 0.0)
    array = np.asarray(scores, dtype=np.float64)
    return {
        "patchesWithOil": int(array.size),
        "patchesWithoutOil": empty_total,
        "emptyPatchesLeftEmpty": empty_correct,
        "iouMean": round(float(array.mean()), 6) if array.size else None,
        "iouMedian": round(float(np.median(array)), 6) if array.size else None,
        "iouP10": round(float(np.percentile(array, 10)), 6) if array.size else None,
        "iouP90": round(float(np.percentile(array, 90)), 6) if array.size else None,
    }
