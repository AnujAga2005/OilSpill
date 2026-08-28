"""The classical dark-spot detector the learned model is measured against.

The reason this module has tests at all is that it is the honesty control: if the baseline is
quietly broken, the U-Net's reported improvement over it is manufactured. So these tests check
that the detector actually detects -- a synthetic dark patch on lighter water is found, at
roughly the right contrast -- and that the two parts most likely to fail silently do not: the
local reference must not pull no-data zeros into its mean, and calibration must never look at
anything but the split it was handed.
"""

from __future__ import annotations

import numpy as np
import pytest

from spilltrace_ml import baseline as B
from spilltrace_ml.dataset import LABEL_BACKGROUND, LABEL_INVALID, LABEL_OIL


def slick_scene(
    size: int = 96,
    water_db: float = -14.0,
    oil_db: float = -20.0,
    slick: tuple[int, int, int, int] = (24, 24, 48, 48),
    invalid_cols: int = 0,
    noise: float = 0.0,
    seed: int = 4,
) -> tuple[np.ndarray, np.ndarray]:
    """A dark rectangle on lighter water. Returns `(channels, target)`.

    Real SAR is speckled and inhomogeneous; this is not. The point is not realism but
    control: with a known contrast and a known slick, a detector that fails here is broken
    rather than merely challenged.
    """
    r0, c0, height, width = slick
    vv = np.full((size, size), water_db, dtype=np.float32)
    vv[r0:r0 + height, c0:c0 + width] = oil_db
    if noise:
        vv += np.random.default_rng(seed).normal(0.0, noise, vv.shape).astype(np.float32)

    target = np.full((size, size), LABEL_BACKGROUND, dtype=np.uint8)
    target[r0:r0 + height, c0:c0 + width] = LABEL_OIL
    if invalid_cols:
        target[:, :invalid_cols] = LABEL_INVALID
        vv[:, :invalid_cols] = 0.0  # what a no-data border actually holds

    channels = np.stack([vv, vv + 6.0])  # VH is the weaker channel, and is unused
    return channels, target


def valid_of(target: np.ndarray) -> np.ndarray:
    return target != LABEL_INVALID


# -- despeckle ---------------------------------------------------------------

class TestDespeckle:
    def test_a_kernel_below_three_is_a_no_op(self):
        values = np.arange(25, dtype=np.float32).reshape(5, 5)
        for kernel in (0, 1, 2):
            assert B.despeckle(values, kernel) is values

    def test_an_isolated_bright_pixel_is_removed(self):
        values = np.full((9, 9), -15.0, dtype=np.float32)
        values[4, 4] = 5.0
        out = B.despeckle(values, 3)
        assert out[4, 4] == pytest.approx(-15.0)

    def test_an_even_kernel_is_rounded_up_to_odd(self):
        """`cv2.medianBlur` rejects even sizes, so the config value cannot be trusted raw."""
        values = np.full((9, 9), -15.0, dtype=np.float32)
        assert B.despeckle(values, 4).shape == values.shape

    def test_a_large_kernel_is_capped_at_five(self):
        """float32 median is only supported up to ksize 5; larger would raise."""
        values = np.full((21, 21), -15.0, dtype=np.float32)
        assert B.despeckle(values, 31).shape == values.shape

    def test_an_edge_is_not_dissolved(self):
        values = np.full((16, 16), -14.0, dtype=np.float32)
        values[:, 8:] = -20.0
        out = B.despeckle(values, 5)
        assert out[8, 2] == pytest.approx(-14.0)
        assert out[8, 13] == pytest.approx(-20.0)


# -- the local reference -----------------------------------------------------

class TestLocalBackground:
    def test_a_uniform_field_is_its_own_background(self):
        values = np.full((32, 32), -13.0, dtype=np.float32)
        out = B.local_background(values, np.ones_like(values, dtype=bool), 4)
        assert out == pytest.approx(np.full_like(values, -13.0), abs=1e-4)

    def test_no_data_zeros_do_not_enter_the_mean(self):
        """The bug this function exists to avoid: a plain blur next to a no-data border
        would read the zeros as very bright water and invent a slick along every edge."""
        values = np.full((32, 32), -13.0, dtype=np.float32)
        values[:, :8] = 0.0
        valid = np.ones((32, 32), dtype=bool)
        valid[:, :8] = False
        out = B.local_background(values, valid, 4)
        assert out[16, 12] == pytest.approx(-13.0, abs=1e-3)

    def test_a_fully_invalid_neighbourhood_gives_zero_not_a_nan(self):
        values = np.zeros((16, 16), dtype=np.float32)
        out = B.local_background(values, np.zeros((16, 16), dtype=bool), 2)
        assert np.isfinite(out).all()
        assert out == pytest.approx(0.0)

    def test_the_background_follows_a_gradient(self):
        """The reason a local mode is offered at all: wind speed varies across a scene."""
        values = np.tile(np.linspace(-20.0, -8.0, 64, dtype=np.float32), (64, 1))
        out = B.local_background(values, np.ones((64, 64), dtype=bool), 4)
        assert out[32, 8] < out[32, 32] < out[32, 56]


# -- the contrast score ------------------------------------------------------

class TestContrastScore:
    def test_a_slick_scores_above_the_surrounding_water(self):
        channels, target = slick_scene()
        score = B.contrast_score(channels, valid_of(target), B.BaselineConfig())
        assert score[48, 48] > score[4, 4]

    def test_the_score_is_in_decibels_below_the_reference(self):
        """A 6 dB darker patch on flat water should score about 6, since the global
        reference lands on the water level."""
        channels, target = slick_scene(water_db=-14.0, oil_db=-20.0)
        score = B.contrast_score(channels, valid_of(target), B.BaselineConfig(mode="global"))
        assert score[48, 48] == pytest.approx(6.0, abs=0.5)

    def test_invalid_pixels_score_zero_whatever_they_contain(self):
        channels, target = slick_scene(invalid_cols=12)
        channels[0, :, :12] = -60.0  # darker than any real slick
        score = B.contrast_score(channels, valid_of(target), B.BaselineConfig())
        assert score[:, :12] == pytest.approx(0.0)

    def test_both_modes_find_a_slick_smaller_than_the_local_box(self):
        """The local estimator only has contrast to measure while its box can still see
        water. That condition holds here and is violated deliberately in the next test."""
        channels, target = slick_scene(size=128, slick=(52, 52, 24, 24))
        valid = valid_of(target)
        for mode in ("global", "local"):
            cfg = B.BaselineConfig(mode=mode, local_radius=32)
            score = B.contrast_score(channels, valid, cfg)
            assert score[64, 64] > 3.0, mode

    def test_the_local_mode_loses_contrast_inside_a_wide_slick(self):
        """A documented weakness, worth pinning: a slick wider than the box becomes its
        own background. This is why both estimators are calibrated and compared."""
        channels, target = slick_scene(size=128, slick=(16, 16, 96, 96))
        valid = valid_of(target)
        local = B.contrast_score(channels, valid, B.BaselineConfig(mode="local", local_radius=8))
        glob = B.contrast_score(channels, valid, B.BaselineConfig(mode="global"))
        assert local[64, 64] < glob[64, 64]

    def test_an_unknown_mode_is_rejected_rather_than_defaulted(self):
        channels, target = slick_scene()
        with pytest.raises(ValueError, match="unknown baseline mode"):
            B.contrast_score(channels, valid_of(target), B.BaselineConfig(mode="magic"))

    def test_an_all_invalid_tile_does_not_divide_by_an_empty_pool(self):
        channels, target = slick_scene(size=32)
        target[:] = LABEL_INVALID
        score = B.contrast_score(channels, valid_of(target), B.BaselineConfig())
        assert np.isfinite(score).all()
        assert score == pytest.approx(0.0)

    def test_only_vv_is_used(self):
        """VV is chosen because the audit measured the separation there. Changing VH must
        not change the score, or the documented reason is wrong."""
        channels, target = slick_scene()
        altered = channels.copy()
        altered[1] += 25.0
        valid = valid_of(target)
        cfg = B.BaselineConfig()
        assert B.contrast_score(altered, valid, cfg) == pytest.approx(
            B.contrast_score(channels, valid, cfg)
        )


# -- mask cleaning -----------------------------------------------------------

class TestCleanMask:
    def test_a_speck_below_the_minimum_area_is_dropped(self):
        mask = np.zeros((64, 64), dtype=bool)
        mask[10:14, 10:14] = True  # 16 px
        out = B.clean_mask(mask, B.BaselineConfig(min_area_px=256, open_radius=0, close_radius=0))
        assert not out.any()

    def test_a_component_at_the_minimum_area_is_kept(self):
        mask = np.zeros((64, 64), dtype=bool)
        mask[10:26, 10:26] = True  # 256 px exactly
        out = B.clean_mask(mask, B.BaselineConfig(min_area_px=256, open_radius=0, close_radius=0))
        assert out.sum() == 256

    def test_area_is_measured_per_component_and_not_in_total(self):
        """Two specks that together clear the threshold must still both go."""
        mask = np.zeros((64, 64), dtype=bool)
        mask[4:16, 4:16] = True    # 144 px
        mask[40:52, 40:52] = True  # 144 px
        out = B.clean_mask(mask, B.BaselineConfig(min_area_px=256, open_radius=0, close_radius=0))
        assert not out.any()

    def test_a_pinhole_is_closed(self):
        mask = np.ones((40, 40), dtype=bool)
        mask[20, 20] = False
        out = B.clean_mask(mask, B.BaselineConfig(close_radius=2, open_radius=0, min_area_px=0))
        assert out[20, 20]

    def test_an_empty_mask_stays_empty_without_erroring(self):
        out = B.clean_mask(np.zeros((32, 32), dtype=bool), B.BaselineConfig())
        assert not out.any()

    def test_the_result_is_boolean(self):
        mask = np.zeros((64, 64), dtype=bool)
        mask[8:40, 8:40] = True
        assert B.clean_mask(mask, B.BaselineConfig()).dtype == np.bool_

    def test_disabling_every_step_returns_the_mask_unchanged(self):
        mask = np.zeros((32, 32), dtype=bool)
        mask[3, 3] = True
        cfg = B.BaselineConfig(open_radius=0, close_radius=0, min_area_px=0)
        assert B.clean_mask(mask, cfg).tolist() == mask.tolist()


# -- end to end --------------------------------------------------------------

class TestPredict:
    def test_the_detector_finds_a_clear_slick(self):
        channels, target = slick_scene(slick=(24, 24, 48, 48))
        cfg = B.BaselineConfig(contrast_db=3.0, min_area_px=256)
        detected = B.predict_patch(channels, valid_of(target), cfg)
        scores = B.evaluate_detections(detected[None, ...], target[None, ...])
        assert scores["iou"] > 0.85

    def test_flat_water_produces_no_detection(self):
        """No slick to find. A detector that fires here would be a false-alarm machine."""
        size = 96
        channels = np.stack([np.full((size, size), -14.0, dtype=np.float32)] * 2)
        target = np.full((size, size), LABEL_BACKGROUND, dtype=np.uint8)
        cfg = B.BaselineConfig(contrast_db=2.0, min_area_px=256)
        assert not B.predict_patch(channels, valid_of(target), cfg).any()

    def test_nothing_is_ever_detected_in_no_data(self):
        channels, target = slick_scene(invalid_cols=16)
        channels[0, :, :16] = -80.0
        detected = B.predict_patch(channels, valid_of(target), B.BaselineConfig(contrast_db=1.0))
        assert not detected[:, :16].any()

    def test_a_higher_contrast_requirement_cannot_detect_more(self):
        channels, target = slick_scene(noise=1.2)
        valid = valid_of(target)
        low = B.predict_patch(channels, valid, B.BaselineConfig(contrast_db=1.0, min_area_px=0))
        high = B.predict_patch(channels, valid, B.BaselineConfig(contrast_db=5.0, min_area_px=0))
        assert high.sum() <= low.sum()

    def test_the_batch_path_agrees_with_the_single_path(self):
        channels, target = slick_scene()
        cfg = B.BaselineConfig()
        batch = B.predict_batch(channels[None, ...], target[None, ...], cfg)
        assert batch[0].tolist() == B.predict_patch(channels, valid_of(target), cfg).tolist()

    def test_the_batch_path_derives_validity_from_the_labels(self):
        channels, target = slick_scene(invalid_cols=16)
        batch = B.predict_batch(channels[None, ...], target[None, ...], B.BaselineConfig())
        assert not batch[0][:, :16].any()

    def test_the_detector_is_deterministic(self):
        channels, target = slick_scene(noise=1.0)
        cfg = B.BaselineConfig()
        first = B.predict_patch(channels, valid_of(target), cfg)
        second = B.predict_patch(channels, valid_of(target), cfg)
        assert first.tolist() == second.tolist()


# -- calibration -------------------------------------------------------------

class TestCalibrate:
    @staticmethod
    def batch(count: int = 4) -> tuple[np.ndarray, np.ndarray]:
        channels, targets = [], []
        for index in range(count):
            offset = 8 * index
            c, t = slick_scene(slick=(16 + offset, 16, 40, 40), noise=0.8, seed=index)
            channels.append(c)
            targets.append(t)
        return np.stack(channels), np.stack(targets)

    def test_the_chosen_config_is_returned_ready_to_use(self):
        channels, targets = self.batch()
        result = B.calibrate(
            channels, targets, contrast_grid=(2.0, 4.0), area_grid=(0, 256), modes=("global",)
        )
        cfg = B.config_from_dict(result["config"])
        assert cfg.mode == "global"
        assert cfg.contrast_db in (2.0, 4.0)
        assert cfg.min_area_px in (0, 256)

    def test_the_winner_is_the_best_row_of_the_sweep(self):
        channels, targets = self.batch()
        result = B.calibrate(
            channels, targets, contrast_grid=(1.0, 3.0, 5.0), area_grid=(0,), modes=("global",)
        )
        best = max(row["iou"] for row in result["sweep"] if row["iou"] is not None)
        assert result["value"] == best
        assert result["best"]["iou"] == best

    def test_the_whole_grid_is_recorded_not_just_the_winner(self):
        """Publishing the sweep is what makes the chosen point auditable rather than
        asserted."""
        channels, targets = self.batch(2)
        result = B.calibrate(
            channels, targets, contrast_grid=(1.0, 2.0), area_grid=(0, 256),
            modes=("global", "local"),
        )
        assert len(result["sweep"]) == 2 * 2 * 2
        assert {row["mode"] for row in result["sweep"]} == {"global", "local"}

    def test_a_tie_goes_to_the_lower_contrast(self):
        channels, targets = self.batch(2)
        result = B.calibrate(
            channels, targets, contrast_grid=(2.0, 2.0), area_grid=(0,), modes=("global",)
        )
        assert result["config"]["contrast_db"] == 2.0

    def test_another_key_can_be_optimised(self):
        channels, targets = self.batch(2)
        result = B.calibrate(
            channels, targets, contrast_grid=(1.0, 5.0), area_grid=(0,), modes=("global",),
            key="recall",
        )
        assert result["selectedBy"] == "recall"
        # A lower contrast requirement always recalls at least as much.
        assert result["config"]["contrast_db"] == 1.0

    def test_calibration_states_which_split_it_used(self):
        channels, targets = self.batch(2)
        result = B.calibrate(
            channels, targets, contrast_grid=(2.0,), area_grid=(0,), modes=("global",)
        )
        assert "validation split only" in result["rule"]

    def test_an_unscoreable_split_raises_rather_than_guessing(self):
        """Every target invalid, so no row has an IoU. Returning a default config would
        be a silently uncalibrated baseline."""
        channels, targets = self.batch(2)
        targets[:] = LABEL_INVALID
        with pytest.raises(ValueError, match="no iou"):
            B.calibrate(
                channels, targets, contrast_grid=(2.0,), area_grid=(0,), modes=("global",)
            )

    def test_progress_is_reported_when_asked_for(self):
        channels, targets = self.batch(2)
        seen: list[str] = []
        B.calibrate(
            channels, targets, contrast_grid=(1.0, 2.0), area_grid=(0,), modes=("global",),
            progress=seen.append,
        )
        assert len(seen) == 2

    def test_calibration_is_deterministic(self):
        channels, targets = self.batch(3)
        kwargs = dict(contrast_grid=(1.0, 3.0), area_grid=(0, 256), modes=("global", "local"))
        first = B.calibrate(channels, targets, **kwargs)
        second = B.calibrate(channels, targets, **kwargs)
        assert first["config"] == second["config"]
        assert first["value"] == second["value"]


class TestConfigRoundTrip:
    def test_a_config_survives_json_and_back(self):
        """The calibrated config is written to metrics.json and read back at evaluation
        time. If that round trip lost a field, the reported baseline would not be the
        one that was calibrated."""
        original = B.BaselineConfig(mode="local", contrast_db=3.5, min_area_px=1024)
        assert B.config_from_dict(original.to_dict()) == original

    def test_unknown_keys_are_ignored_rather_than_raising(self):
        cfg = B.config_from_dict({"mode": "local", "not_a_field": 7})
        assert cfg.mode == "local"

    def test_missing_keys_fall_back_to_the_defaults(self):
        cfg = B.config_from_dict({"contrast_db": 9.0})
        assert cfg.contrast_db == 9.0
        assert cfg.mode == B.BaselineConfig().mode

    def test_to_dict_is_a_copy_and_not_the_instance_state(self):
        cfg = B.BaselineConfig()
        payload = cfg.to_dict()
        payload["mode"] = "local"
        assert cfg.mode == "global"
