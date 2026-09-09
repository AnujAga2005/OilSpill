"""Confusion counts, derived scores and threshold selection.

These are the functions that produce every number the product displays, so the tests are
arithmetic rather than statistical: a hand-countable mask, a hand-computed IoU. The gradients
of the losses here are checked against finite differences in `test_nn_gradients.py`; what is
checked here is that invalid pixels never reach a score, that a zero denominator becomes
`None` rather than a plausible-looking number, and that the chosen operating point is the one
the sweep actually justifies.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

from spilltrace_ml import metrics as M
from spilltrace_ml.dataset import LABEL_BACKGROUND, LABEL_INVALID, LABEL_OIL

ROOT = Path(__file__).resolve().parents[1]


def labels(rows: list[str]) -> np.ndarray:
    """A label array from a picture. `o` oil, `.` background, `x` no-data."""
    lookup = {"o": LABEL_OIL, ".": LABEL_BACKGROUND, "x": LABEL_INVALID}
    return np.array([[lookup[c] for c in row] for row in rows], dtype=np.uint8)


def probs(rows: list[str]) -> np.ndarray:
    """A probability array from a picture. `1` certain oil, `0` certain water."""
    return np.array([[1.0 if c == "1" else 0.0 for c in row] for row in rows], dtype=np.float64)


class TestSplitTarget:
    def test_oil_and_valid_are_independent_questions(self):
        oil, valid = M.split_target(labels(["o.x"]))
        assert oil.tolist() == [[True, False, False]]
        assert valid.tolist() == [[True, True, False]]

    def test_an_invalid_pixel_is_never_oil(self):
        oil, valid = M.split_target(labels(["xxx"]))
        assert not oil.any()
        assert not valid.any()


class TestConfusion:
    def test_counts_are_what_you_would_count_by_hand(self):
        target = labels(["oo..", "oo.."])
        probability = probs(["1100", "1010"])
        counts = M.confusion(probability, target, 0.5)
        assert counts["truePositive"] == 3   # three of the four oil pixels predicted
        assert counts["falseNegative"] == 1
        assert counts["falsePositive"] == 1  # row 1, column 2
        assert counts["trueNegative"] == 3

    def test_invalid_pixels_are_in_no_category_but_are_reported(self):
        target = labels(["oox", "..x"])
        probability = probs(["111", "111"])
        counts = M.confusion(probability, target, 0.5)
        assert counts["invalidPixels"] == 2
        assert counts["validPixels"] == 4
        quadrants = sum(
            counts[k] for k in ("truePositive", "falsePositive", "falseNegative", "trueNegative")
        )
        assert quadrants == counts["validPixels"]

    def test_a_confident_prediction_over_no_data_is_not_a_false_positive(self):
        """The scenes have large no-data borders. Scoring them would be free credit or
        free blame depending on the sign, and neither is earned."""
        target = labels(["xxxx"])
        counts = M.confusion(probs(["1111"]), target, 0.5)
        assert counts["falsePositive"] == 0
        assert counts["trueNegative"] == 0

    def test_the_threshold_is_inclusive(self):
        counts = M.confusion(np.array([[0.5]]), labels(["o"]), 0.5)
        assert counts["truePositive"] == 1

    def test_a_higher_threshold_cannot_add_positives(self):
        rng = np.random.default_rng(3)
        probability = rng.random((16, 16))
        target = labels(["".join("o" if (r + c) % 3 == 0 else "." for c in range(16))
                         for r in range(16)])
        low = M.confusion(probability, target, 0.3)
        high = M.confusion(probability, target, 0.7)
        assert high["truePositive"] <= low["truePositive"]
        assert high["falsePositive"] <= low["falsePositive"]


class TestDerivedScores:
    def test_iou_is_intersection_over_union(self):
        counts = {"truePositive": 6, "falsePositive": 2, "falseNegative": 2, "trueNegative": 90}
        scores = M.metrics_from_confusion(counts)
        assert scores["iou"] == pytest.approx(6 / 10)
        assert scores["precision"] == pytest.approx(6 / 8)
        assert scores["recall"] == pytest.approx(6 / 8)
        assert scores["dice"] == pytest.approx(12 / 16)

    def test_dice_and_f1_are_the_same_number(self):
        """Reported under both names because both are asked for. They must not diverge."""
        scores = M.metrics_from_confusion(
            {"truePositive": 7, "falsePositive": 3, "falseNegative": 5, "trueNegative": 40}
        )
        assert scores["dice"] == scores["f1"]

    def test_a_perfect_prediction_scores_one(self):
        scores = M.metrics_from_confusion(
            {"truePositive": 10, "falsePositive": 0, "falseNegative": 0, "trueNegative": 90}
        )
        assert scores["iou"] == 1.0
        assert scores["dice"] == 1.0
        assert scores["accuracy"] == 1.0
        assert scores["falsePositiveRate"] == 0.0

    def test_an_undefined_score_is_none_and_not_zero(self):
        """No oil predicted and none present: precision is undefined. Reporting 0.0 would
        read as a failure, and reporting 1.0 as a success. It is neither."""
        scores = M.metrics_from_confusion(
            {"truePositive": 0, "falsePositive": 0, "falseNegative": 0, "trueNegative": 64}
        )
        assert scores["precision"] is None
        assert scores["recall"] is None
        assert scores["iou"] is None
        assert scores["balancedAccuracy"] is None
        assert scores["accuracy"] == 1.0

    def test_missing_everything_scores_zero_recall_not_none(self):
        scores = M.metrics_from_confusion(
            {"truePositive": 0, "falsePositive": 0, "falseNegative": 12, "trueNegative": 52}
        )
        assert scores["recall"] == 0.0
        assert scores["precision"] is None  # nothing was predicted, so nothing was wrong
        assert scores["iou"] == 0.0

    def test_the_counts_travel_with_the_scores(self):
        counts = {"truePositive": 1, "falsePositive": 2, "falseNegative": 3, "trueNegative": 4}
        scores = M.metrics_from_confusion(counts)
        for key, value in counts.items():
            assert scores[key] == value

    def test_oil_fractions_are_over_valid_pixels(self):
        scores = M.metrics_from_confusion(
            {"truePositive": 10, "falsePositive": 10, "falseNegative": 10, "trueNegative": 70}
        )
        assert scores["predictedOilFraction"] == pytest.approx(0.2)
        assert scores["referenceOilFraction"] == pytest.approx(0.2)

    def test_specificity_and_false_positive_rate_are_complements(self):
        scores = M.metrics_from_confusion(
            {"truePositive": 5, "falsePositive": 15, "falseNegative": 5, "trueNegative": 75}
        )
        assert scores["specificity"] + scores["falsePositiveRate"] == pytest.approx(1.0)

    def test_evaluate_is_confusion_then_scores(self):
        target = labels(["oo.."])
        probability = probs(["1100"])
        assert M.evaluate(probability, target, 0.5) == M.metrics_from_confusion(
            M.confusion(probability, target, 0.5)
        )


class TestThresholdSweep:
    target = labels(["oooo....", "oooo....", "........", "........"])

    def setup_method(self):
        rng = np.random.default_rng(11)
        self.probability = np.clip(
            np.where(self.target == LABEL_OIL, 0.75, 0.25) + rng.normal(0, 0.12, self.target.shape),
            0.0,
            1.0,
        )

    def test_the_default_grid_covers_the_range_in_nineteen_steps(self):
        sweep = M.threshold_sweep(self.probability, self.target)
        assert [row["threshold"] for row in sweep] == [round(0.05 * i, 2) for i in range(1, 20)]

    def test_each_row_carries_the_threshold_it_describes(self):
        sweep = M.threshold_sweep(self.probability, self.target, [0.2, 0.8])
        assert sweep[0]["threshold"] == 0.2
        assert sweep[1]["threshold"] == 0.8

    def test_recall_never_rises_as_the_threshold_rises(self):
        sweep = M.threshold_sweep(self.probability, self.target)
        recalls = [row["recall"] for row in sweep]
        assert recalls == sorted(recalls, reverse=True)

    def test_a_custom_grid_replaces_the_default_entirely(self):
        sweep = M.threshold_sweep(self.probability, self.target, [0.5])
        assert len(sweep) == 1


class TestBestThreshold:
    def test_the_maximum_wins(self):
        sweep = [
            {"threshold": 0.3, "iou": 0.50},
            {"threshold": 0.6, "iou": 0.81},
            {"threshold": 0.9, "iou": 0.40},
        ]
        assert M.best_threshold(sweep)["threshold"] == 0.6

    def test_a_tie_goes_to_the_lower_threshold(self):
        """Two thresholds scoring the same on validation: the lower one over-paints, which
        for a search product loses less than a miss."""
        sweep = [{"threshold": 0.4, "iou": 0.8}, {"threshold": 0.7, "iou": 0.8}]
        assert M.best_threshold(sweep)["threshold"] == 0.4

    def test_another_key_can_be_optimised(self):
        sweep = [
            {"threshold": 0.3, "iou": 0.8, "recall": 0.6},
            {"threshold": 0.6, "iou": 0.5, "recall": 0.9},
        ]
        assert M.best_threshold(sweep, "recall")["threshold"] == 0.6
        assert M.best_threshold(sweep, "recall")["selectedBy"] == "recall"

    def test_rows_with_an_undefined_score_are_skipped(self):
        sweep = [{"threshold": 0.3, "iou": None}, {"threshold": 0.6, "iou": 0.4}]
        assert M.best_threshold(sweep)["threshold"] == 0.6

    def test_an_unscoreable_sweep_falls_back_and_says_so(self):
        result = M.best_threshold([{"threshold": 0.5, "iou": None}])
        assert result["threshold"] == 0.5
        assert "no operating point" in result["reason"]

    def test_the_selection_is_self_describing(self):
        """This dict is published on the methodology screen, so it has to explain itself
        without the code beside it."""
        result = M.best_threshold([{"threshold": 0.6, "iou": 0.81}])
        assert result["selectedBy"] == "iou"
        assert result["value"] == 0.81
        assert "validation" in result["reason"]


class TestPerPatchMetrics:
    def test_empty_patches_are_counted_apart_from_the_distribution(self):
        """A patch with no oil has no IoU. Scoring it 1.0 for being left empty would lift
        the mean without the model finding anything."""
        target = np.stack([labels(["oo.."]), labels(["...."])])
        probability = np.stack([probs(["1100"]), probs(["0000"])])
        result = M.per_patch_metrics(probability, target, 0.5)
        assert result["patchesWithOil"] == 1
        assert result["patchesWithoutOil"] == 1
        assert result["emptyPatchesLeftEmpty"] == 1
        assert result["iouMean"] == 1.0

    def test_a_false_alarm_on_an_empty_patch_is_recorded(self):
        target = np.stack([labels(["...."])])
        probability = np.stack([probs(["1000"])])
        result = M.per_patch_metrics(probability, target, 0.5)
        assert result["patchesWithoutOil"] == 1
        assert result["emptyPatchesLeftEmpty"] == 0
        assert result["iouMean"] is None

    def test_the_mean_is_over_patches_and_not_over_pixels(self):
        """The distinction the pooled figure hides: a large well-segmented patch cannot
        carry a small badly-segmented one."""
        target = np.stack([labels(["oooo"]), labels(["o..."])])
        probability = np.stack([probs(["1111"]), probs(["0111"])])
        result = M.per_patch_metrics(probability, target, 0.5)
        assert result["patchesWithOil"] == 2
        assert result["iouMean"] == pytest.approx((1.0 + 0.0) / 2)

    def test_percentiles_bracket_the_median(self):
        rng = np.random.default_rng(5)
        target = np.stack([labels(["oo.."]) for _ in range(20)])
        probability = rng.random((20, 1, 4))
        result = M.per_patch_metrics(probability, target, 0.5)
        assert result["iouP10"] <= result["iouMedian"] <= result["iouP90"]

    def test_no_patches_with_oil_gives_no_distribution(self):
        target = np.stack([labels(["...."])])
        result = M.per_patch_metrics(np.stack([probs(["0000"])]), target, 0.5)
        assert result["iouMedian"] is None
        assert result["iouP90"] is None


class TestLossValues:
    def test_invalid_pixels_contribute_nothing_to_the_loss(self):
        target = labels(["o.x", "o.x"])
        good = np.array([[8.0, -8.0, -8.0], [8.0, -8.0, -8.0]])
        wild = np.array([[8.0, -8.0, 500.0], [8.0, -8.0, -500.0]])
        assert M.combined_loss(good, target)[0] == pytest.approx(M.combined_loss(wild, target)[0])

    def test_a_confident_correct_prediction_costs_almost_nothing(self):
        target = labels(["oo..", "oo.."])
        logits = np.where(target == LABEL_OIL, 12.0, -12.0)
        total, _, parts = M.combined_loss(logits, target)
        assert total < 0.02
        assert parts["bce"] < 0.01

    def test_a_confident_wrong_prediction_costs_a_lot(self):
        target = labels(["oo..", "oo.."])
        logits = np.where(target == LABEL_OIL, -12.0, 12.0)
        total, _, _ = M.combined_loss(logits, target)
        assert total > 6.0

    def test_the_weights_are_applied_to_the_components(self):
        target = labels(["oo..", "oo.."])
        logits = np.full(target.shape, 0.3)
        _, _, parts = M.combined_loss(logits, target, dice_weight=0.25, bce_weight=0.75)
        assert parts["total"] == pytest.approx(0.75 * parts["bce"] + 0.25 * parts["dice"])

    def test_the_positive_weight_raises_the_price_of_a_miss(self):
        target = labels(["o..."])
        oil, valid = M.split_target(target)
        missed = np.array([[-4.0, -4.0, -4.0, -4.0]])
        plain, _ = M.bce_with_logits(missed, oil, valid, pos_weight=1.0)
        weighted, _ = M.bce_with_logits(missed, oil, valid, pos_weight=4.0)
        assert weighted > plain

    def test_extreme_logits_do_not_overflow(self):
        """`np.exp` of a large positive number is what the stable form exists to avoid."""
        target = labels(["o."])
        for value in (1e4, -1e4):
            total, grad, _ = M.combined_loss(np.full((1, 2), value), target)
            assert np.isfinite(total)
            assert np.isfinite(grad).all()

    def test_dice_on_an_all_background_patch_is_defined(self):
        """Dice alone is unstable when there is no oil; the smoothing term is why this
        returns a number at all."""
        target = labels(["...."])
        loss, grad = M.soft_dice(np.full((1, 4), -8.0), *M.split_target(target))
        assert np.isfinite(loss)
        assert loss < 0.5
        assert np.isfinite(grad).all()

    def test_the_gradient_points_the_right_way(self):
        target = labels(["o."])
        _, grad, _ = M.combined_loss(np.array([[-2.0, 2.0]]), target)
        assert grad[0, 0] < 0  # raise the logit where oil is
        assert grad[0, 1] > 0  # lower it where water is


# ---------------------------------------------------------------------------
# The scene sweep has to contain the point it reports at
# ---------------------------------------------------------------------------

def load_scene_eval():
    spec = importlib.util.spec_from_file_location(
        "spilltrace_run_scene_eval", ROOT / "scripts" / "run_scene_eval.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestSceneThresholdGrid:
    """`run_scene_eval.py` reports test scores *at the patch threshold* for comparability
    with `metrics.json`, and that threshold is chosen on validation by `run_train.py` --
    it is not a constant. When a run selected 0.65 the fixed sweep grid had no such bucket
    and the script died on a `KeyError` after every one of the 71 scenes had already been
    scored, which is the most expensive possible moment to fail.
    """

    def test_the_selected_patch_threshold_is_always_in_the_grid(self):
        module = load_scene_eval()
        for threshold in (0.5, 0.55, 0.6, 0.65, 0.7, 0.85, 0.95, 0.99):
            grid = module.threshold_grid(threshold)
            assert threshold in grid, f"{threshold} missing from {grid}"

    def test_a_threshold_already_on_the_grid_is_not_duplicated(self):
        module = load_scene_eval()
        grid = module.threshold_grid(0.6)
        assert grid == sorted(set(grid))
        assert grid == sorted(module.BASE_THRESHOLDS)

    def test_the_progress_threshold_survives_an_off_grid_selection(self):
        """The per-scene progress line reads one fixed bucket; if the grid is ever rebuilt
        without it, every scene print raises instead of the aggregate at the end."""
        module = load_scene_eval()
        for threshold in (0.51, 0.65, 0.77):
            assert module.PROGRESS_THRESHOLD in module.threshold_grid(threshold)

    def test_the_grid_is_sorted_so_the_reported_sweep_reads_in_order(self):
        module = load_scene_eval()
        grid = module.threshold_grid(0.65)
        assert grid == sorted(grid)
        assert grid[0] == 0.5
