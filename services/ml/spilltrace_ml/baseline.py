"""A classical dark-spot detector, so the learned model has something to beat.

A segmentation score reported on its own says nothing: on a dataset where oil is
a few per cent of the valid pixels, plausible-looking numbers can come from a
detector that is barely better than a threshold. This module implements that
threshold properly, calibrates it on the same validation split by the same rule,
and reports it next to the U-Net.

The method is the textbook one for SAR oil detection: oil damps capillary waves,
so a slick returns less energy than the surrounding sea and appears as a dark
patch. So

1. despeckle the VV channel with a small median filter (multiplicative SAR
   speckle is the dominant noise, and a median is the cheap standard remedy);
2. estimate a clean-water reference level and flag pixels a calibrated number of
   decibels below it;
3. clean up with a morphological opening and drop components below a minimum area.

VV is used because ``DATA_AUDIT.md`` measured the oil-to-background separation at
-3.8 to -7.1 dB in ``Sigma0_VV_db`` against roughly -1 dB in ``Sigma0_VH_db``.
Two reference estimators are offered and both are calibrated, so the comparison
is against the better of them rather than a straw man:

``global``
    A high percentile of the valid pixels in the tile. Robust as long as oil
    covers less than ``100 - percentile`` per cent of it.
``local``
    A large box mean over valid pixels only. Adapts to a wind-speed gradient
    across the tile, but loses contrast inside a slick wider than the box.

Everything ignores ``LABEL_INVALID`` pixels, and no step uses the reference mask.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Sequence

import cv2
import numpy as np

from .dataset import LABEL_INVALID, LABEL_OIL
from .metrics import confusion, metrics_from_confusion, split_target

# The supplied scenes are all 8.9831528e-05 degrees per pixel, which is the
# standard Sentinel-1 GRDH 10 m spacing. Areas below are quoted at that scale.
PIXEL_METRES = 10.0


@dataclass
class BaselineConfig:
    """Dark-spot detector settings. Calibrated fields are noted."""

    mode: str = "global"  # "global" or "local"; calibrated
    despeckle_kernel: int = 5  # 0 disables; must be odd and <= 5 for float32
    water_percentile: float = 80.0  # clean-water reference for mode="global"
    local_radius: int = 48  # ~0.5 km at 10 m/px, for mode="local"
    contrast_db: float = 2.0  # decibels below the reference; calibrated
    open_radius: int = 1  # morphological opening, in pixels
    close_radius: int = 1  # morphological closing, in pixels
    min_area_px: int = 256  # ~0.026 km^2 at 10 m/px; calibrated

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _kernel(radius: int) -> np.ndarray:
    size = 2 * radius + 1
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))


def despeckle(values: np.ndarray, kernel: int) -> np.ndarray:
    """Median filter in decibel space. ``cv2`` supports float32 at ksize 3 and 5."""
    if kernel < 3:
        return values
    size = min(5, kernel if kernel % 2 else kernel + 1)
    return cv2.medianBlur(np.ascontiguousarray(values, dtype=np.float32), size)


def local_background(values: np.ndarray, valid: np.ndarray, radius: int) -> np.ndarray:
    """Box mean over valid pixels only, via unnormalised box sums.

    Dividing a sum of masked values by a sum of the mask is what makes this a mean
    over *valid* samples; a plain blur would pull the no-data zeros into the
    estimate and invent contrast along every scene border.
    """
    size = (2 * radius + 1, 2 * radius + 1)
    weight = valid.astype(np.float32)
    numerator = cv2.boxFilter(
        values.astype(np.float32) * weight,
        -1,
        size,
        normalize=False,
        borderType=cv2.BORDER_REFLECT,
    )
    denominator = cv2.boxFilter(
        weight, -1, size, normalize=False, borderType=cv2.BORDER_REFLECT
    )
    return np.divide(
        numerator,
        denominator,
        out=np.zeros_like(numerator),
        where=denominator > 0.5,
    )


def contrast_score(
    channels: np.ndarray, valid: np.ndarray, cfg: BaselineConfig
) -> np.ndarray:
    """Decibels below the clean-water reference; larger means more oil-like.

    ``channels`` is ``(2, H, W)`` raw decibels ordered VV then VH, matching
    :data:`spilltrace_ml.dataset.CHANNEL_ORDER`.
    """
    vv = np.asarray(channels[0], dtype=np.float32)
    valid = np.asarray(valid, dtype=bool)
    smoothed = despeckle(vv, cfg.despeckle_kernel)
    if cfg.mode == "local":
        reference = local_background(smoothed, valid, cfg.local_radius)
    elif cfg.mode == "global":
        pool = smoothed[valid]
        level = (
            float(np.percentile(pool, cfg.water_percentile)) if pool.size else 0.0
        )
        reference = np.full(smoothed.shape, level, dtype=np.float32)
    else:
        raise ValueError(f"unknown baseline mode {cfg.mode!r}")
    score = (reference - smoothed).astype(np.float32)
    score[~valid] = 0.0
    return score


def clean_mask(mask: np.ndarray, cfg: BaselineConfig) -> np.ndarray:
    """Opening, closing and a minimum-area filter on a boolean detection mask."""
    binary = np.ascontiguousarray(mask.astype(np.uint8))
    if cfg.open_radius > 0:
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, _kernel(cfg.open_radius))
    if cfg.close_radius > 0:
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, _kernel(cfg.close_radius))
    if cfg.min_area_px > 0 and binary.any():
        count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
        keep = np.zeros(count, dtype=bool)
        for index in range(1, count):
            keep[index] = stats[index, cv2.CC_STAT_AREA] >= cfg.min_area_px
        binary = keep[labels].astype(np.uint8)
    return binary.astype(bool)


def predict_patch(
    channels: np.ndarray, valid: np.ndarray, cfg: BaselineConfig
) -> np.ndarray:
    score = contrast_score(channels, valid, cfg)
    return clean_mask((score >= cfg.contrast_db) & valid, cfg)


def predict_batch(
    channels: np.ndarray, targets: np.ndarray, cfg: BaselineConfig
) -> np.ndarray:
    """Detections for a cached batch. ``channels`` is ``(N, 2, H, W)`` raw decibels."""
    out = np.zeros(targets.shape, dtype=bool)
    for index in range(channels.shape[0]):
        valid = targets[index] != LABEL_INVALID
        out[index] = predict_patch(channels[index], valid, cfg)
    return out


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

DEFAULT_CONTRAST_GRID: tuple[float, ...] = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0)
DEFAULT_AREA_GRID: tuple[int, ...] = (0, 256, 1024)
DEFAULT_MODES: tuple[str, ...] = ("global", "local")


def evaluate_detections(detections: np.ndarray, targets: np.ndarray) -> dict[str, Any]:
    """Confusion-matrix metrics for a boolean detection mask, invalid pixels excluded."""
    return metrics_from_confusion(
        confusion(detections.astype(np.float32), targets, 0.5)
    )


def calibrate(
    channels: np.ndarray,
    targets: np.ndarray,
    base: BaselineConfig | None = None,
    modes: Sequence[str] = DEFAULT_MODES,
    contrast_grid: Sequence[float] = DEFAULT_CONTRAST_GRID,
    area_grid: Sequence[int] = DEFAULT_AREA_GRID,
    key: str = "iou",
    progress: Any = None,
) -> dict[str, Any]:
    """Grid-search the detector on the validation split, maximising ``key``.

    The score map is computed once per (mode, patch) and reused across the
    threshold and area grid, so the sweep costs one pass over the data rather
    than one per grid point.
    """
    say = progress or (lambda _msg: None)
    base = base or BaselineConfig()
    sweep: list[dict[str, Any]] = []
    for mode in modes:
        cfg_mode = replace(base, mode=mode)
        scores = np.zeros(targets.shape, dtype=np.float32)
        valids = np.zeros(targets.shape, dtype=bool)
        for index in range(channels.shape[0]):
            valids[index] = targets[index] != LABEL_INVALID
            scores[index] = contrast_score(channels[index], valids[index], cfg_mode)
        for contrast in contrast_grid:
            raw = (scores >= contrast) & valids
            for area in area_grid:
                cfg = replace(cfg_mode, contrast_db=float(contrast), min_area_px=int(area))
                detections = np.zeros(targets.shape, dtype=bool)
                for index in range(raw.shape[0]):
                    detections[index] = clean_mask(raw[index], cfg)
                row = {
                    "mode": mode,
                    "contrastDb": float(contrast),
                    "minAreaPx": int(area),
                    **evaluate_detections(detections, targets),
                }
                sweep.append(row)
            say(f"  baseline {mode} contrast {contrast} dB done")

    scored = [row for row in sweep if row.get(key) is not None]
    if not scored:
        raise ValueError(f"baseline calibration produced no {key}")
    best = max(scored, key=lambda row: (row[key], -row["contrastDb"]))
    chosen = replace(
        base,
        mode=best["mode"],
        contrast_db=best["contrastDb"],
        min_area_px=best["minAreaPx"],
    )
    return {
        "config": chosen.to_dict(),
        "selectedBy": key,
        "value": best[key],
        "best": best,
        "sweep": sweep,
        "rule": (
            "calibrated on the validation split only, by maximising "
            f"{key}; both reference estimators were swept so the comparison uses "
            "the stronger classical detector"
        ),
    }


def config_from_dict(payload: dict[str, Any]) -> BaselineConfig:
    fields = BaselineConfig().to_dict()
    return BaselineConfig(**{k: v for k, v in payload.items() if k in fields})
