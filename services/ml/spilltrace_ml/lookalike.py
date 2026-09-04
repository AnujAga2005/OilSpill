"""Dark-patch screening: is this dark region oil, or something that looks like it?

A SAR oil detector's hard problem is not finding dark water. It is deciding which
dark water is oil. Low wind leaves glassy patches, algal blooms and biogenic films
damp the surface, rain cells and wind shadows behind headlands all read as
low backscatter. Until this module existed the project had no answer at all --
``KNOWN-ISSUES.md`` item 4 said so plainly, because the supplied dataset contains
no labelled look-alike and so the model's ability to reject one was unmeasured.

The design has three parts, deliberately separated so each can be argued with:

1. **A proposer.** ``propose`` finds dark regions the classical way -- smooth,
   threshold relative to the scene's own water statistics, clean up, drop
   anything too small. It knows nothing about oil. It is what defines "each dark
   patch" so that a rejection rate has a denominator.
2. **Seven scale-free features.** Every feature is a *ratio* of two quantities in
   the same unit, or a pure shape number. That is not tidiness: this project's
   scenes are calibrated decibels and the only labelled look-alike set available
   is 8-bit JPEG with an unpublished greyscale stretch. A feature that survives an
   unknown affine rescaling of the pixel values can be measured on both. Absolute
   decibel features are computed too, and reported, but never fed to the model.
3. **A linear screen.** A logistic regression over those seven features, fitted on
   this project's own real scenes -- oil proposals against non-oil proposals from
   the same imagery -- and then applied to the look-alike set *without any
   retraining*. The look-alike numbers are therefore a generalisation test, not a
   fit. Seven weights are also small enough to print, which matters more than a
   fraction of a point of accuracy: a judge can read the whole model.

What this cannot do: name the phenomenon. It separates oil-like from not-oil-like.
Calling a rejected patch "an algal bloom" would need labels this project does not
have, so a rejection says which measurements failed and by how much, and stops.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import cv2
import numpy as np

@dataclass(frozen=True)
class FeatureSpec:
    """One input to the screen, with the reason it is there."""

    name: str
    label: str
    unit: str
    oil_direction: int  # +1 if larger values argue for oil, -1 if smaller do
    rationale: str


FEATURES: tuple[FeatureSpec, ...] = (
    FeatureSpec(
        "darknessZ",
        "darkness against local water",
        "background standard deviations",
        +1,
        "oil damps the short capillary waves the radar sees, so a film sits several "
        "standard deviations below the water around it; a low-wind patch is shallower "
        "because the water is only slightly smoother, not covered",
    ),
    FeatureSpec(
        "darknessP10Z",
        "darkness of the darkest tenth",
        "background standard deviations",
        +1,
        "a mean can be dragged down by a few very dark pixels, so the tenth percentile "
        "asks whether the whole region is dark or only part of it",
    ),
    FeatureSpec(
        "textureRatio",
        "interior roughness",
        "ratio to background",
        -1,
        "a damped surface is smoother than the sea around it, so its pixel spread is "
        "narrower; wind shadows and blooms stay as speckled as open water",
    ),
    FeatureSpec(
        "edgeSharpness",
        "edge definition",
        "ratio to background gradient",
        +1,
        "a film has a physical boundary and the backscatter steps across it; a low-wind "
        "zone fades, so its edge gradient is no stronger than the ambient texture",
    ),
    FeatureSpec(
        "compactness",
        "compactness",
        "4piA/P^2, 1.0 is a circle",
        -1,
        "spills are drawn out by wind and current into ragged forms; a compact blob is "
        "more consistent with a wind cell or a bloom patch",
    ),
    FeatureSpec(
        "solidity",
        "solidity",
        "area / convex hull area",
        -1,
        "a slick feathers at its edges and leaves gaps inside its hull; a smooth convex "
        "region is a weaker candidate",
    ),
    FeatureSpec(
        "elongation",
        "elongation",
        "length / width",
        +1,
        "a discharge from a moving vessel is linear, and wind stretches any film along "
        "its axis, so elongation argues for oil -- though a ship wake is linear too, "
        "which is why this feature is never decisive on its own",
    ),
)

FEATURE_NAMES: tuple[str, ...] = tuple(spec.name for spec in FEATURES)

# Reported alongside the features but never fed to the screen: both need calibrated
# decibels, and the look-alike set the screen is validated against does not have them.
CONTEXT_FIELDS: tuple[str, ...] = (
    "pixels",
    "areaKm2",
    "contrast",
    "contrastUnit",
    "depolarisation",
    "meanValue",
    "backgroundMean",
    "backgroundStd",
)


@dataclass
class ScreenConfig:
    """Every tunable in one place, published with the numbers it produced."""

    # -- proposer --------------------------------------------------------------
    smooth_sigma_px: float = 1.5      # speckle would otherwise dominate every gradient
    darkness_k: float = 1.0           # threshold at water_mean - k * water_std
    min_area_px: int = 400            # ~0.036 km2 at 9.5 m/pixel
    open_radius_px: int = 2
    close_radius_px: int = 3
    max_regions: int = 64             # a patch with more than this is texture, not slicks

    # -- feature geometry ------------------------------------------------------
    guard_px: int = 3                 # gap between region and background annulus
    annulus_px: int = 20              # thickness of the background annulus
    boundary_px: int = 2              # half-width of the band the edge gradient uses
    min_background_px: int = 200      # below this the local statistics are not usable

    # -- decision --------------------------------------------------------------
    reject_at_or_below: float = 0.35
    accept_at_or_above: float = 0.65

    def to_dict(self) -> dict[str, Any]:
        return {
            "smoothSigmaPx": self.smooth_sigma_px,
            "darknessK": self.darkness_k,
            "minAreaPx": self.min_area_px,
            "openRadiusPx": self.open_radius_px,
            "closeRadiusPx": self.close_radius_px,
            "maxRegions": self.max_regions,
            "guardPx": self.guard_px,
            "annulusPx": self.annulus_px,
            "boundaryPx": self.boundary_px,
            "minBackgroundPx": self.min_background_px,
            "rejectAtOrBelow": self.reject_at_or_below,
            "acceptAtOrAbove": self.accept_at_or_above,
        }


def _disc(radius: int) -> np.ndarray:
    size = max(1, 2 * int(radius) + 1)
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))


def propose(
    plane: np.ndarray,
    valid: np.ndarray | None = None,
    cfg: ScreenConfig | None = None,
) -> tuple[np.ndarray, int]:
    """Find dark regions without any notion of oil. Returns ``(labels, count)``.

    This is the classical first stage of every SAR slick detector: smooth away
    speckle, threshold against the scene's own water statistics, clean up the
    result, discard whatever is too small to measure. The threshold is relative --
    ``mean - k * std`` over the valid pixels -- so it needs no radiometric
    calibration and behaves the same on decibels and on 8-bit digital numbers.

    Labels are 1..count in the returned array, 0 is background, matching
    OpenCV's convention so a caller can index straight into it.
    """
    cfg = cfg or ScreenConfig()
    values = np.asarray(plane, dtype=np.float32)
    if values.ndim != 2:
        raise ValueError(f"expected a 2-D plane, got shape {values.shape}")
    usable = (
        np.ones(values.shape, dtype=bool) if valid is None else np.asarray(valid, dtype=bool)
    )
    if not usable.any():
        return np.zeros(values.shape, dtype=np.int32), 0

    smooth = cv2.GaussianBlur(values, (0, 0), cfg.smooth_sigma_px)
    sample = smooth[usable]
    threshold = float(sample.mean() - cfg.darkness_k * sample.std())
    dark = (smooth <= threshold) & usable

    binary = dark.astype(np.uint8)
    if cfg.open_radius_px > 0:
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, _disc(cfg.open_radius_px))
    if cfg.close_radius_px > 0:
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, _disc(cfg.close_radius_px))

    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    keep = [
        index
        for index in range(1, count)
        if int(stats[index, cv2.CC_STAT_AREA]) >= cfg.min_area_px
    ]
    # Largest first, so a cap on the count keeps the regions worth reporting.
    keep.sort(key=lambda index: -int(stats[index, cv2.CC_STAT_AREA]))
    keep = keep[: cfg.max_regions]

    out = np.zeros(values.shape, dtype=np.int32)
    for position, index in enumerate(keep, start=1):
        out[labels == index] = position
    return out, len(keep)


def _shape_numbers(region: np.ndarray) -> dict[str, float]:
    """Compactness, solidity and elongation from the region's own pixels.

    Elongation uses the principal axes of the pixel cloud rather than a bounding
    box, so a curved slick is not credited with the width of the rectangle around
    it -- the same choice ``geometry.py`` makes for the published measurements.
    """
    binary = region.astype(np.uint8)
    area_px = float(binary.sum())
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    perimeter = max((cv2.arcLength(c, True) for c in contours), default=0.0)
    hull_area = 0.0
    if contours:
        largest = max(contours, key=cv2.contourArea)
        hull_area = float(cv2.contourArea(cv2.convexHull(largest)))

    rows, cols = np.nonzero(binary)
    elongation = 1.0
    if rows.size >= 2:
        points = np.stack([cols.astype(np.float64), rows.astype(np.float64)])
        centred = points - points.mean(axis=1, keepdims=True)
        values, vectors = np.linalg.eigh(np.cov(centred))
        projected = vectors[:, np.argsort(values)[::-1]].T @ centred
        length = float(projected[0].max() - projected[0].min())
        width = float(projected[1].max() - projected[1].min())
        elongation = length / width if width > 1e-6 else float("inf")

    return {
        "compactness": (
            float(4.0 * math.pi * area_px / (perimeter**2)) if perimeter > 0 else 0.0
        ),
        "solidity": float(area_px / hull_area) if hull_area > 0 else 0.0,
        "elongation": min(elongation, 50.0),
    }


def region_features(
    plane: np.ndarray,
    region: np.ndarray,
    *,
    exclude: np.ndarray | None = None,
    second_plane: np.ndarray | None = None,
    spacing_m: float | None = None,
    plane_unit: str | None = None,
    cfg: ScreenConfig | None = None,
) -> dict[str, Any] | None:
    """Measure one dark region against the water immediately around it.

    ``exclude`` is anything that must not count as background -- the other
    proposals, land, invalid pixels. The background is an annulus at
    ``guard_px`` remove from the region so the transition zone, which belongs to
    neither, is not averaged into the water statistics.

    Returns ``None`` when the annulus is too small to characterise the local
    water, which is the honest outcome for a region pressed against the edge of
    its patch or surrounded by other detections. Every ratio is formed from two
    quantities in the same unit, so the seven model features are unchanged by any
    affine rescaling of ``plane`` -- that is what lets one fitted screen run on
    calibrated decibels and on 8-bit digital numbers.
    """
    cfg = cfg or ScreenConfig()
    values = np.asarray(plane, dtype=np.float32)
    mask = np.asarray(region, dtype=bool)
    if mask.shape != values.shape:
        raise ValueError(f"region {mask.shape} does not match plane {values.shape}")
    if not mask.any():
        return None

    blocked = np.zeros(values.shape, dtype=bool) if exclude is None else np.asarray(exclude, bool)
    guard = cv2.dilate(mask.astype(np.uint8), _disc(cfg.guard_px)).astype(bool)
    outer = cv2.dilate(
        mask.astype(np.uint8), _disc(cfg.guard_px + cfg.annulus_px)
    ).astype(bool)
    background = outer & ~guard & ~blocked
    if int(background.sum()) < cfg.min_background_px:
        return None

    smooth = cv2.GaussianBlur(values, (0, 0), cfg.smooth_sigma_px)
    inside = values[mask]
    outside = values[background]
    background_mean = float(outside.mean())
    background_std = float(outside.std())
    if background_std < 1e-6:
        return None

    gradient = cv2.magnitude(
        cv2.Sobel(smooth, cv2.CV_32F, 1, 0, ksize=3),
        cv2.Sobel(smooth, cv2.CV_32F, 0, 1, ksize=3),
    )
    band = cv2.dilate(mask.astype(np.uint8), _disc(cfg.boundary_px)).astype(bool) & ~cv2.erode(
        mask.astype(np.uint8), _disc(cfg.boundary_px)
    ).astype(bool)
    edge_gradient = float(gradient[band].mean()) if band.any() else 0.0
    background_gradient = float(gradient[background].mean())

    numbers = _shape_numbers(mask)
    pixels = int(mask.sum())
    features: dict[str, Any] = {
        "darknessZ": (background_mean - float(inside.mean())) / background_std,
        "darknessP10Z": (background_mean - float(np.percentile(inside, 10))) / background_std,
        "textureRatio": float(inside.std()) / background_std,
        "edgeSharpness": (
            edge_gradient / background_gradient if background_gradient > 1e-6 else 0.0
        ),
        **numbers,
        "pixels": pixels,
        "areaKm2": (
            round(pixels * float(spacing_m) ** 2 / 1e6, 6) if spacing_m else None
        ),
        "meanValue": round(float(inside.mean()), 4),
        "backgroundMean": round(background_mean, 4),
        "backgroundStd": round(background_std, 4),
        "backgroundPixels": int(background.sum()),
    }

    # Absolute contrast, in whatever unit the caller's plane is in. Reported for the
    # analyst and for the report; deliberately absent from the model input, because the
    # look-alike set the screen is validated on has no calibrated unit at all.
    features["contrast"] = round(background_mean - float(inside.mean()), 3)
    features["contrastUnit"] = plane_unit or "unspecified"
    if second_plane is not None:
        other = np.asarray(second_plane, dtype=np.float32)
        if other.shape != values.shape:
            raise ValueError("second_plane must match plane")
        ratio = values - other
        features["depolarisation"] = round(
            float(ratio[background].mean() - ratio[mask].mean()), 3
        )

    for name in FEATURE_NAMES:
        value = features[name]
        features[name] = round(float(np.nan_to_num(value, nan=0.0, posinf=50.0)), 5)
    return features


# ---------------------------------------------------------------------------
# The screen
# ---------------------------------------------------------------------------


@dataclass
class Screen:
    """A fitted (or unfitted) linear screen over :data:`FEATURES`.

    Standardisation is carried inside the model, not applied by the caller, so a
    saved screen is self-contained: the seven weights are on comparable footing
    and the file can be read without knowing the training set's scale.
    """

    weights: dict[str, float] = field(default_factory=dict)
    intercept: float = 0.0
    mean: dict[str, float] = field(default_factory=dict)
    std: dict[str, float] = field(default_factory=dict)
    calibration: str = "defaults (not fitted to any data)"
    trained_on: dict[str, Any] = field(default_factory=dict)
    class_means: dict[str, dict[str, float]] = field(default_factory=dict)
    cfg: ScreenConfig = field(default_factory=ScreenConfig)

    @property
    def fitted(self) -> bool:
        return bool(self.weights) and self.calibration.startswith("fitted")

    def vector(self, features: dict[str, Any]) -> np.ndarray:
        out = np.empty(len(FEATURE_NAMES), dtype=np.float64)
        for index, name in enumerate(FEATURE_NAMES):
            value = float(features.get(name, 0.0) or 0.0)
            centre = float(self.mean.get(name, 0.0))
            scale = float(self.std.get(name, 1.0)) or 1.0
            out[index] = (value - centre) / scale
        return out

    def likelihood(self, features: dict[str, Any]) -> float:
        """Probability that this region is oil rather than a look-alike."""
        weights = np.array([self.weights.get(n, 0.0) for n in FEATURE_NAMES], dtype=np.float64)
        z = float(weights @ self.vector(features) + self.intercept)
        return float(1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, z)))))

    def contributions(self, features: dict[str, Any]) -> list[tuple[str, float]]:
        """Per-feature signed contribution to the log odds, largest first."""
        vector = self.vector(features)
        pairs = [
            (name, float(self.weights.get(name, 0.0) * vector[index]))
            for index, name in enumerate(FEATURE_NAMES)
        ]
        pairs.sort(key=lambda item: -abs(item[1]))
        return pairs

    def to_dict(self) -> dict[str, Any]:
        return {
            "features": [
                {
                    "name": spec.name,
                    "label": spec.label,
                    "unit": spec.unit,
                    "oilDirection": spec.oil_direction,
                    "rationale": spec.rationale,
                    "weight": round(float(self.weights.get(spec.name, 0.0)), 5),
                    "trainingMean": round(float(self.mean.get(spec.name, 0.0)), 5),
                    "trainingStd": round(float(self.std.get(spec.name, 1.0)), 5),
                }
                for spec in FEATURES
            ],
            "intercept": round(float(self.intercept), 5),
            "calibration": self.calibration,
            "trainedOn": self.trained_on,
            "classMeans": {
                label: {k: round(float(v), 5) for k, v in values.items()}
                for label, values in self.class_means.items()
            },
            "config": self.cfg.to_dict(),
            "note": (
                "weights apply to standardised features; the standardisation "
                "constants are carried with them so the file is self-contained"
            ),
        }

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: str | Path) -> "Screen":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        entries = payload.get("features") or []
        config = ScreenConfig()
        stored = payload.get("config") or {}
        for key, value in (
            ("rejectAtOrBelow", "reject_at_or_below"),
            ("acceptAtOrAbove", "accept_at_or_above"),
            ("darknessK", "darkness_k"),
            ("minAreaPx", "min_area_px"),
        ):
            if key in stored:
                setattr(config, value, type(getattr(config, value))(stored[key]))
        return cls(
            weights={e["name"]: float(e.get("weight", 0.0)) for e in entries},
            intercept=float(payload.get("intercept", 0.0)),
            mean={e["name"]: float(e.get("trainingMean", 0.0)) for e in entries},
            std={e["name"]: float(e.get("trainingStd", 1.0)) for e in entries},
            calibration=str(payload.get("calibration", "unknown")),
            trained_on=dict(payload.get("trainedOn") or {}),
            class_means={
                label: {k: float(v) for k, v in (values or {}).items()}
                for label, values in (payload.get("classMeans") or {}).items()
            },
            cfg=config,
        )


_SPEC_BY_NAME = {spec.name: spec for spec in FEATURES}


def _phrase(screen: Screen, name: str, features: dict[str, Any], signed: float) -> str:
    """One sentence of evidence: the measurement, and what it argues for.

    Where the screen was fitted, the sentence carries the two class averages so a
    reader can see the value in context instead of taking a weight on trust.
    """
    spec = _SPEC_BY_NAME[name]
    value = float(features.get(name, 0.0) or 0.0)
    direction = "consistent with oil" if signed > 0 else "argues against oil"
    oil = (screen.class_means.get("oil") or {}).get(name)
    other = (screen.class_means.get("lookAlike") or {}).get(name)
    context = ""
    if oil is not None and other is not None:
        context = (
            f" (fitted oil regions average {oil:.2f}, rejected regions {other:.2f})"
        )
    return f"{spec.label} {value:.2f} {spec.unit} — {direction}{context}"


def verdict(
    features: dict[str, Any],
    screen: Screen | None = None,
    cfg: ScreenConfig | None = None,
    top_reasons: int = 3,
) -> dict[str, Any]:
    """Turn one feature record into an accept / reject / uncertain decision.

    Three outcomes, not two. A region whose likelihood sits between the two
    thresholds is returned as ``uncertain`` and stays in the case: suppressing a
    detection this screen is not confident about would trade a measured false
    positive for an unmeasured missed spill, which is the wrong trade for a
    disaster-response tool.
    """
    model = screen or Screen()
    limits = cfg or model.cfg
    likelihood = model.likelihood(features)
    if likelihood <= limits.reject_at_or_below:
        label = "rejected"
    elif likelihood >= limits.accept_at_or_above:
        label = "accepted"
    else:
        label = "uncertain"

    contributions = model.contributions(features)
    reasons = [
        _phrase(model, name, features, signed)
        for name, signed in contributions[: max(0, top_reasons)]
        if abs(signed) > 1e-9
    ]
    headline = {
        "accepted": "consistent with an oil film",
        "rejected": "more consistent with a look-alike than with oil",
        "uncertain": "not separable by the screen; kept for human review",
    }[label]

    return {
        "label": label,
        "oilLikelihood": round(likelihood, 4),
        "headline": headline,
        "reasons": reasons,
        "contributions": [
            {"feature": name, "logOdds": round(signed, 4)} for name, signed in contributions
        ],
        "calibration": model.calibration,
        "thresholds": {
            "rejectAtOrBelow": limits.reject_at_or_below,
            "acceptAtOrAbove": limits.accept_at_or_above,
        },
        "basis": (
            "a seven-feature linear screen over shape, texture and darkness statistics; "
            "it does not name the phenomenon and it is not a calibrated probability"
        ),
    }


# ---------------------------------------------------------------------------
# Fitting and measurement
# ---------------------------------------------------------------------------


def design_matrix(rows: Sequence[dict[str, Any]]) -> np.ndarray:
    return np.array(
        [[float(row.get(name, 0.0) or 0.0) for name in FEATURE_NAMES] for row in rows],
        dtype=np.float64,
    ).reshape(len(rows), len(FEATURE_NAMES))


def fit(
    rows: Sequence[dict[str, Any]],
    labels: Sequence[int],
    *,
    l2: float = 1.0,
    steps: int = 4000,
    lr: float = 0.2,
    trained_on: dict[str, Any] | None = None,
    cfg: ScreenConfig | None = None,
) -> Screen:
    """Fit the logistic screen by full-batch gradient descent.

    Deliberately plain: seven features and a few thousand rows do not need an
    optimiser worth explaining, and a closed-form-free loop keeps the project's
    "no new dependencies" rule intact. Class weights balance the two sides so a
    ten-to-one split does not train the screen to answer "look-alike" always.
    """
    x = design_matrix(rows)
    y = np.asarray(labels, dtype=np.float64).reshape(-1)
    if x.shape[0] != y.shape[0]:
        raise ValueError(f"{x.shape[0]} feature rows against {y.shape[0]} labels")
    if x.shape[0] < 2 * len(FEATURE_NAMES):
        raise ValueError(
            f"{x.shape[0]} rows is too few to fit {len(FEATURE_NAMES)} weights honestly"
        )
    positives = float(y.sum())
    if positives < 1 or positives >= y.size:
        raise ValueError("fitting needs examples of both classes")

    mean = x.mean(axis=0)
    std = np.maximum(x.std(axis=0), 1e-6)
    z = (x - mean) / std

    # Each class contributes the same total weight, whatever its share of the rows.
    sample_weight = np.where(y > 0.5, y.size / (2.0 * positives), y.size / (2.0 * (y.size - positives)))
    sample_weight /= sample_weight.sum()

    weights = np.zeros(len(FEATURE_NAMES), dtype=np.float64)
    bias = 0.0
    for _ in range(int(steps)):
        prediction = 1.0 / (1.0 + np.exp(-np.clip(z @ weights + bias, -60.0, 60.0)))
        error = (prediction - y) * sample_weight
        weights -= lr * (z.T @ error + l2 * weights / y.size)
        bias -= lr * float(error.sum())

    oil_rows = x[y > 0.5]
    other_rows = x[y <= 0.5]
    return Screen(
        weights={name: float(weights[i]) for i, name in enumerate(FEATURE_NAMES)},
        intercept=float(bias),
        mean={name: float(mean[i]) for i, name in enumerate(FEATURE_NAMES)},
        std={name: float(std[i]) for i, name in enumerate(FEATURE_NAMES)},
        calibration=(
            f"fitted on {int(y.size)} dark regions "
            f"({int(positives)} over labelled oil, {int(y.size - positives)} not)"
        ),
        trained_on=dict(trained_on or {}),
        class_means={
            "oil": {n: float(oil_rows[:, i].mean()) for i, n in enumerate(FEATURE_NAMES)},
            "lookAlike": {n: float(other_rows[:, i].mean()) for i, n in enumerate(FEATURE_NAMES)},
        },
        cfg=cfg or ScreenConfig(),
    )


def auc(scores: Sequence[float], labels: Sequence[int]) -> float | None:
    """Area under the ROC curve, by rank. ``None`` when a class is missing.

    Ties are averaged, which matters here because a screen can return identical
    likelihoods for regions whose features round to the same values.
    """
    values = np.asarray(scores, dtype=np.float64)
    truth = np.asarray(labels, dtype=np.float64)
    positives = float((truth > 0.5).sum())
    negatives = float(values.size - positives)
    if positives < 1 or negatives < 1:
        return None
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(values.size, dtype=np.float64)
    ranks[order] = np.arange(1, values.size + 1, dtype=np.float64)
    sorted_values = values[order]
    start = 0
    while start < sorted_values.size:
        stop = start + 1
        while stop < sorted_values.size and sorted_values[stop] == sorted_values[start]:
            stop += 1
        if stop - start > 1:
            ranks[order[start:stop]] = ranks[order[start:stop]].mean()
        start = stop
    rank_sum = float(ranks[truth > 0.5].sum())
    return round((rank_sum - positives * (positives + 1) / 2.0) / (positives * negatives), 4)


def outcome_counts(
    screen: Screen,
    rows: Sequence[dict[str, Any]],
    cfg: ScreenConfig | None = None,
) -> dict[str, int]:
    counts = {"accepted": 0, "uncertain": 0, "rejected": 0}
    for row in rows:
        counts[verdict(row, screen, cfg, top_reasons=0)["label"]] += 1
    return counts


def measure(
    screen: Screen,
    rows: Sequence[dict[str, Any]],
    labels: Sequence[int],
    cfg: ScreenConfig | None = None,
) -> dict[str, Any]:
    """Three-way outcomes per class, plus the two rates that follow from them.

    ``keptSensitivity`` counts an *uncertain* oil region as kept, because that is
    what the pipeline does with it. ``rejectionSpecificity`` counts only outright
    rejections of look-alikes, for the same reason. Reporting the raw three-way
    table alongside them is what stops either number from being read as more than
    it is.
    """
    oil_rows = [row for row, label in zip(rows, labels) if label > 0.5]
    other_rows = [row for row, label in zip(rows, labels) if label <= 0.5]
    oil = outcome_counts(screen, oil_rows, cfg)
    other = outcome_counts(screen, other_rows, cfg)
    oil_total = max(1, sum(oil.values()))
    other_total = max(1, sum(other.values()))
    scores = [screen.likelihood(row) for row in rows]
    return {
        "regions": len(rows),
        "oilRegions": len(oil_rows),
        "lookAlikeRegions": len(other_rows),
        "oilOutcomes": oil,
        "lookAlikeOutcomes": other,
        "keptSensitivity": round((oil_total - oil["rejected"]) / oil_total, 4) if oil_rows else None,
        "rejectionSpecificity": (
            round(other["rejected"] / other_total, 4) if other_rows else None
        ),
        "auc": auc(scores, labels),
        "definitions": {
            "keptSensitivity": "labelled-oil regions not rejected / all labelled-oil regions",
            "rejectionSpecificity": "look-alike regions rejected / all look-alike regions",
            "uncertain": "likelihood between the two thresholds; kept in the case",
        },
    }


def grouped_folds(groups: Sequence[str], folds: int = 5, seed: int = 20260904) -> list[int]:
    """Assign each row to a fold so no group straddles two folds.

    The group here is the parent satellite product. This is the same discipline
    ``KNOWN-ISSUES.md`` item 1 records the project getting wrong once already:
    two crops of one pass on opposite sides of a split make a model look better
    than it is. Groups are shuffled deterministically and dealt to whichever fold
    is currently smallest, so the folds stay balanced without being ordered.
    """
    keys = sorted({str(g) for g in groups})
    rng = np.random.default_rng(seed)
    rng.shuffle(keys)
    sizes = [0] * max(1, int(folds))
    counts: dict[str, int] = {}
    for group in groups:
        counts[str(group)] = counts.get(str(group), 0) + 1
    assignment: dict[str, int] = {}
    for key in sorted(keys, key=lambda k: -counts[k]):
        target = min(range(len(sizes)), key=lambda i: sizes[i])
        assignment[key] = target
        sizes[target] += counts[key]
    return [assignment[str(g)] for g in groups]


def describe_features() -> list[dict[str, Any]]:
    """The feature specification, for the methodology screen and the report."""
    return [
        {
            "name": spec.name,
            "label": spec.label,
            "unit": spec.unit,
            "oilDirection": spec.oil_direction,
            "rationale": spec.rationale,
            "scaleFree": True,
        }
        for spec in FEATURES
    ]

