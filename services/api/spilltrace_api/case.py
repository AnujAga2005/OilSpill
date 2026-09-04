"""Build a complete SpillTrace case from one supplied scene.

A "case" is the whole chain for a single Sentinel-1 acquisition: decode the scene,
segment the oil, measure the slick's geometry, reconstruct where it drifted from and
where it is heading, generate the labelled synthetic AIS traffic, and rank the vessels.
Every stage records its own provenance so the dashboard can state, for each number it
shows, whether it came from the supplied imagery, from a trained model, or from
synthetic forcing.

Two design points matter here:

**Detection falls back honestly.** If no trained checkpoint exists, the case is still
built -- from the supplied reference mask, labelled as such. It never presents a
reference mask as a prediction, and it never silently produces an empty slick.

**Drift is seeded from the slick's own pixels, not its centroid.** A slick does not
originate from a single point, so seeding from the observed footprint is what makes the
backward envelope mean anything.

The heavy part is the scene decode (about 9 s for a 2048x2048 two-band LZW GeoTIFF) and
the NumPy inference pass, which is why the API runs this as a background job.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from spilltrace_common import config as C
from spilltrace_common.geotiff import Affine
from spilltrace_drift import age as age_mod
from spilltrace_drift import ais as ais_mod
from spilltrace_drift import engine as drift_engine
from spilltrace_drift import forcing as forcing_mod
from spilltrace_drift import scoring as scoring_mod
from spilltrace_ml import dataset as dataset_mod
from spilltrace_ml import geometry as geometry_mod
from spilltrace_ml import lookalike as lookalike_mod
from spilltrace_ml import preview as preview_mod
from spilltrace_ml.model import UNet

# Backwards-compatible export for older helper scripts that imported CaseStore from case.
from .store import CaseStore  # noqa: E402,F401

Progress = Callable[[str], None]

# Tiles the inference pass over the scene. 128 matches the training patch size, and the
# 16-pixel overlap is trimmed from each interior edge so tile seams do not appear as
# straight lines in the probability field.
INFERENCE_TILE = 128
INFERENCE_OVERLAP = 16

# The look-alike screen, fitted by scripts/run_lookalike_eval.py. The case reads it if
# it exists and says so if it does not; an unfitted screen scores every region 0.5 and
# calls it "uncertain", which is the only honest output for a model nobody has fitted.
SCREEN_PATH = C.MODELS_DIR / "lookalike_screen.json"

# The evaluation proposes up to 64 dark regions per scene because it wants every region
# it can label. A case wants the patches an analyst would plausibly look at, and each
# one costs a full-frame feature pass, so the pipeline asks for fewer.
SCREEN_MAX_REGIONS = 24


class CaseError(RuntimeError):
    """A case could not be built. Carries a message fit to show a user."""


@dataclass
class CaseRequest:
    """What to build. Defaults come from the audited dataset, not from guesses."""

    scene: str
    detector: str = "auto"  # "auto" | "model" | "baseline" | "reference"
    particles: int = C.DriftConfig().particle_count
    horizon_hours: float | None = None
    threshold: float | None = None
    previews: bool = True
    seed: int = C.DriftConfig().seed

    def key(self) -> str:
        """Stable identity for this request, used to seed the synthetic generators."""
        parts = [self.scene, self.detector, str(self.particles), str(self.seed)]
        if self.horizon_hours is not None:
            parts.append(f"h{self.horizon_hours:g}")
        return ":".join(parts)


@dataclass
class Stage:
    """One step of the pipeline, timed so the UI can show where the seconds went."""

    name: str
    seconds: float
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"stage": self.name, "seconds": round(self.seconds, 3), "note": self.note}


@dataclass
class _Timer:
    stages: list[Stage] = field(default_factory=list)

    def record(self, name: str, started: float, note: str = "") -> None:
        self.stages.append(Stage(name, time.perf_counter() - started, note))


# ---------------------------------------------------------------------------
# Scene discovery
# ---------------------------------------------------------------------------


def scene_paths(scene: str) -> tuple[Path, Path | None]:
    """Resolve a scene id to its image and (if present) reference mask."""
    name = str(scene).strip()
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise CaseError(f"invalid scene id {scene!r}")
    image = C.IMAGE_DIR / f"{name}.tif"
    if not image.exists():
        raise CaseError(f"scene {name} is not in {C.IMAGE_DIR}")
    mask = C.MASK_DIR / f"{name}.tif"
    return image, (mask if mask.exists() else None)


def available_scenes(limit: int | None = None) -> list[str]:
    """Scene ids that have both an image and a mask, in stable order."""
    if not C.IMAGE_DIR.exists():
        return []
    names = sorted(path.stem for path in C.IMAGE_DIR.glob("*.tif"))
    paired = [name for name in names if (C.MASK_DIR / f"{name}.tif").exists()]
    return paired[:limit] if limit else paired


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _load_checkpoint() -> tuple[UNet, dict[str, Any]] | None:
    if not C.CHECKPOINT_PATH.exists():
        return None
    try:
        return UNet.load(C.CHECKPOINT_PATH)
    except Exception:
        # A corrupt checkpoint must degrade to the baseline, not take the API down.
        return None


def _pad_scene(values: np.ndarray, tile: int, multiple: int) -> np.ndarray:
    """Grow a ``(C, H, W)`` scene so it holds at least one whole tile on each axis.

    Padding here rather than per tile keeps every tile exactly ``tile`` square, which in
    turn lets the whole scene go through the network as one uniform stack.
    """
    _, height, width = values.shape
    target_h = max(height, tile)
    target_w = max(width, tile)
    target_h += (-target_h) % multiple
    target_w += (-target_w) % multiple
    pad_h, pad_w = target_h - height, target_w - width
    if not pad_h and not pad_w:
        return values
    # Reflection needs at least as many real rows as it borrows; fall back to edge
    # replication for scenes smaller than their own padding.
    mode = "reflect" if pad_h < height and pad_w < width else "edge"
    return np.pad(values, ((0, 0), (0, pad_h), (0, pad_w)), mode=mode)


def _tile_origins(extent: int, tile: int, step: int) -> list[int]:
    """Tile start offsets covering ``extent``, with the last one pulled back to fit."""
    if extent <= tile:
        return [0]
    return sorted({min(start, extent - tile) for start in range(0, extent, step)})


def infer_probability(
    model: UNet,
    channels: np.ndarray,
    stats: dict[str, Any],
    progress: Progress | None = None,
) -> np.ndarray:
    """Run the U-Net over a full scene in overlapping tiles.

    Two details matter for the result to mean anything. First, tiles go through
    ``normalise_batch`` -- the same clip-then-standardise-then-transpose the training
    loop used -- so the network sees inputs distributed exactly as it was fitted on.
    Second, interior tile borders are trimmed after inference: a convolution has no
    context beyond its own tile edge, so the outermost pixels are the least reliable and
    are precisely where seams would otherwise appear.
    """
    from spilltrace_ml import dataset as ds

    values = np.asarray(channels, dtype=np.float32)
    if values.ndim != 3:
        raise CaseError(f"expected (C, H, W) channels, got shape {values.shape}")
    _, height, width = values.shape

    multiple = 2**model.depth
    tile = max(INFERENCE_TILE, multiple)
    tile += (-tile) % multiple
    padded = _pad_scene(values, tile, multiple)
    _, grid_h, grid_w = padded.shape

    step = max(1, tile - INFERENCE_OVERLAP)
    coords = [
        (top, left)
        for top in _tile_origins(grid_h, tile, step)
        for left in _tile_origins(grid_w, tile, step)
    ]

    accumulated = np.zeros((grid_h, grid_w), dtype=np.float32)
    weight = np.zeros((grid_h, grid_w), dtype=np.float32)
    trim = INFERENCE_OVERLAP // 2
    total = len(coords)
    chunk = 8

    for start in range(0, total, chunk):
        block = coords[start : start + chunk]
        stack = np.stack(
            [padded[:, top : top + tile, left : left + tile] for top, left in block]
        )
        # (N, C, tile, tile) decibels -> (N, tile, tile, C) standardised, as trained.
        probability = model.predict_proba(ds.normalise_batch(stack, stats), len(block))

        for index, (top, left) in enumerate(block):
            # Trim every interior side; a side sitting on the grid boundary has no
            # neighbouring tile to hand over to, so it keeps its border.
            t0 = trim if top > 0 else 0
            l0 = trim if left > 0 else 0
            t1 = tile - trim if top + tile < grid_h else tile
            l1 = tile - trim if left + tile < grid_w else tile
            accumulated[top + t0 : top + t1, left + l0 : left + l1] += probability[
                index, t0:t1, l0:l1
            ]
            weight[top + t0 : top + t1, left + l0 : left + l1] += 1.0

        if progress:
            done = min(start + chunk, total)
            if done == total or done % 64 < chunk:
                progress(f"inference {done}/{total} tiles")

    if not weight[:height, :width].all():
        raise CaseError("tiled inference left uncovered pixels; tile geometry is wrong")
    return (accumulated / np.maximum(weight, 1.0))[:height, :width]


def detect(
    scene: dataset_mod.Scene,
    request: CaseRequest,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Produce a binary oil mask plus a full account of how it was produced."""
    wanted = request.detector
    if wanted not in ("auto", "model", "baseline", "reference"):
        raise CaseError(f"unknown detector {wanted!r}")

    if wanted in ("auto", "model"):
        loaded = _load_checkpoint()
        if loaded is None and wanted == "model":
            raise CaseError("no trained checkpoint is available; train the model first")
        if loaded is not None:
            model, extra = loaded
            stats = extra.get("normStats") or _norm_stats()
            patch_threshold = float(extra.get("threshold", 0.5))
            scene_threshold = _scene_threshold()
            threshold = request.threshold
            if threshold is None:
                threshold = scene_threshold if scene_threshold is not None else patch_threshold
            probability = infer_probability(model, scene.channels, stats, progress)
            probability = np.where(scene.invalid, 0.0, probability)
            mask = (probability >= threshold).astype(np.uint8)
            chosen_on = (
                "whole validation scenes"
                if request.threshold is None and scene_threshold is not None
                else "validation patches"
                if request.threshold is None
                else "the caller's request"
            )
            return {
                "mask": mask,
                "probability": probability,
                "source": "model",
                "label": C.LABEL_PREDICTION,
                "threshold": round(float(threshold), 4),
                "patchThreshold": round(patch_threshold, 4),
                "sceneThreshold": (
                    None if scene_threshold is None else round(scene_threshold, 4)
                ),
                "thresholdSelectedOn": chosen_on,
                "checkpoint": C.CHECKPOINT_PATH.name,
                "modelParameters": int(model.parameter_count()),
                "testMetrics": extra.get("testMetrics"),
                "note": (
                    "U-Net prediction on the supplied VV/VH decibel bands, thresholded at "
                    f"{round(float(threshold), 4)} (selected on {chosen_on}). A whole scene "
                    "is not a training patch: the patch-scale operating point over-predicts "
                    "at scene scale, so the scene-scale threshold is preferred when it has "
                    "been measured."
                ),
            }

    if wanted in ("auto", "baseline"):
        from spilltrace_ml import baseline as baseline_mod

        cfg = _baseline_config()
        if cfg is not None or wanted == "baseline":
            cfg = cfg or baseline_mod.BaselineConfig()
            mask = baseline_mod.predict_patch(scene.channels, ~scene.invalid, cfg)
            mask = np.asarray(mask, dtype=np.uint8)
            if mask.any() or wanted == "baseline":
                return {
                    "mask": mask,
                    "probability": None,
                    "source": "baseline",
                    "label": "Oil extent: Classical dark-spot baseline (not a trained model)",
                    "threshold": None,
                    "note": (
                        "local VV contrast against a despeckled background, calibrated on "
                        "the training split; used because no trained checkpoint is available"
                    ),
                }

    if scene.mask is None:
        raise CaseError(
            f"scene {scene.name} has no reference mask and no detector could produce one"
        )
    return {
        "mask": np.asarray(scene.mask, dtype=np.uint8),
        "probability": None,
        "source": "reference",
        "label": C.LABEL_REFERENCE,
        "threshold": None,
        "note": (
            "the supplied ground-truth annotation, shown because no model prediction was "
            "requested or available; this is not a detection result"
        ),
    }


def _norm_stats() -> dict[str, Any]:
    """Normalisation statistics from the cache, or a hard failure explaining why not."""
    path = C.PROCESSED_DIR / "norm_stats.json"
    if not path.exists():
        raise CaseError("norm_stats.json is missing; run scripts/run_preprocess.py first")
    import json

    return json.loads(path.read_text())


def _scene_threshold() -> float | None:
    """The whole-scene operating threshold, if it has been measured.

    Training selects a threshold on validation *patches*, which are cropped to contain
    oil. A full 2048-pixel scene is mostly open water, so that operating point trades
    away precision at scene scale. `scripts/run_scene_eval.py` re-selects the threshold
    on whole validation scenes; when that file exists it is the honest choice for a
    whole-scene request. Returns None rather than inventing a value.
    """
    import json

    path = C.PROCESSED_DIR / "scene_metrics.json"
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text()).get("sceneThreshold")
    except (ValueError, OSError):
        return None
    if not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if 0.0 < value < 1.0 else None


def _baseline_config():
    """The calibrated baseline configuration written by training, if it exists."""
    import json

    path = C.PROCESSED_DIR / "metrics.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
        config = (payload.get("baseline") or {}).get("config")
    except (ValueError, OSError):
        return None
    if not config:
        return None
    from spilltrace_ml import baseline as baseline_mod

    try:
        return baseline_mod.config_from_dict(config)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Look-alike screening
# ---------------------------------------------------------------------------


def _load_screen() -> tuple[lookalike_mod.Screen, str]:
    """The fitted screen, or an unfitted one plus the reason it is unfitted."""
    if not SCREEN_PATH.exists():
        return (
            lookalike_mod.Screen(),
            f"no screen at {SCREEN_PATH.relative_to(C.REPO_ROOT).as_posix()}, so every "
            "patch scores 0.5 and is returned as uncertain; "
            "scripts/run_lookalike_eval.py fits one",
        )
    try:
        return lookalike_mod.Screen.load(SCREEN_PATH), ""
    except (OSError, ValueError, KeyError, TypeError) as error:
        return lookalike_mod.Screen(), f"{SCREEN_PATH.name} could not be read: {error}"


def _screen_config(screen: lookalike_mod.Screen) -> lookalike_mod.ScreenConfig:
    """The screen's own settings, with only the proposal cap lowered for a live case.

    Everything that changes a *feature* is left exactly as fitted -- the annulus, the
    guard band, the smoothing, the darkness threshold. Only how many regions are kept
    changes, which alters how many patches get reported and nothing about what any one
    of them measures.
    """
    return replace(screen.cfg, max_regions=SCREEN_MAX_REGIONS)


def _measured(features: dict[str, Any]) -> dict[str, Any]:
    """The seven model inputs and the reported-but-unused context fields."""
    keys = list(lookalike_mod.FEATURE_NAMES) + list(lookalike_mod.CONTEXT_FIELDS)
    return {key: features[key] for key in keys if key in features}


def _plural(count: int, word: str) -> str:
    if count == 1:
        return f"{count} {word}"
    suffix = "es" if word.endswith(("ch", "sh", "s", "x", "z")) else "s"
    return f"{count} {word}{suffix}"


def nominal_spacing_m(transform: Affine, height: int) -> float:
    """One pixel's ground size in metres, taken at the middle row of the scene.

    Pixel area varies down a scene in geographic coordinates, so a single number is an
    approximation; it is used only to turn pixel counts into square kilometres for
    display, never inside a feature.
    """
    area = geometry_mod.row_pixel_area_m2(transform, np.array([height / 2.0]))
    return float(math.sqrt(max(1e-9, float(area[0]))))


def screen_region(
    region: np.ndarray,
    *,
    planes: np.ndarray,
    exclude: np.ndarray,
    spacing_m: float,
    screen: lookalike_mod.Screen,
    cfg: lookalike_mod.ScreenConfig,
) -> dict[str, Any]:
    """Screen one dark region and return a block fit to publish beside it."""
    features = lookalike_mod.region_features(
        planes[0],
        region,
        exclude=exclude,
        second_plane=planes[1] if planes.shape[0] > 1 else None,
        spacing_m=spacing_m,
        plane_unit="dB",
        cfg=cfg,
    )
    if features is None:
        return {
            "label": "unscreened",
            "oilLikelihood": None,
            "headline": (
                "not enough clear water around this region to measure it against, so no "
                "verdict is offered"
            ),
            "reasons": [],
            "contributions": [],
            "measured": {},
        }
    out = lookalike_mod.verdict(features, screen, cfg=cfg)
    out["measured"] = _measured(features)
    return out


def slick_screener(
    *,
    planes: np.ndarray,
    detected: np.ndarray,
    valid: np.ndarray,
    spacing_m: float,
    screen: lookalike_mod.Screen,
    cfg: lookalike_mod.ScreenConfig,
    stamp: np.ndarray,
) -> Callable[[dict[str, Any], np.ndarray], dict[str, Any]]:
    """A callback for :func:`geometry.analyse` that screens every published slick.

    ``stamp`` is filled in as a side effect: published component *n* is written into it
    as the value ``n``, so the scene-wide pass that follows can say which slick a dark
    patch overlaps without deriving the components a second time and risking deriving
    them differently.

    One domain shift is worth stating plainly, and the block states it: the screen was
    fitted on regions a darkness *threshold* proposed, and this applies it to a region
    the *network* segmented. The two agree on most slicks, not on all of them.
    """
    order: list[str] = []

    def annotate(record: dict[str, Any], component: np.ndarray) -> dict[str, Any]:
        order.append(str(record.get("id")))
        stamp[component] = len(order)
        block = screen_region(
            component,
            planes=planes,
            exclude=(detected & ~component) | ~valid,
            spacing_m=spacing_m,
            screen=screen,
            cfg=cfg,
        )
        block["appliedTo"] = (
            "a component the segmentation network produced, screened by a model fitted "
            "on regions a darkness threshold proposed"
        )
        return {"screening": block}

    return annotate


def _component_tally(components: Sequence[dict[str, Any]]) -> dict[str, int]:
    """Count the verdicts on what the network published.

    This has to appear beside the patch counts. The proposals are cut out of the scene by a
    darkness threshold and the components by the network, so on a scene holding one large
    slick with faint margins the screen can reject every proposal and still accept every
    component -- the threshold reaches into the half-shades and dilutes the darkness the
    screen measures. A reader shown only the first number would conclude the scene had no
    oil in it, which is the opposite of what the case says.
    """
    tally = {"screened": 0, "accepted": 0, "uncertain": 0, "rejected": 0, "unscreened": 0}
    for entry in components:
        verdict = entry.get("screening") if isinstance(entry, dict) else None
        if not isinstance(verdict, dict):
            continue
        label = str(verdict.get("label"))
        if label not in tally:
            continue
        tally["screened"] += 1
        tally[label] += 1
    return tally


def screen_scene(
    *,
    planes: np.ndarray,
    valid: np.ndarray,
    transform: Affine,
    stamp: np.ndarray,
    slick_ids: Sequence[str],
    spacing_m: float,
    screen: lookalike_mod.Screen,
    cfg: lookalike_mod.ScreenConfig,
    unfitted: str = "",
    components: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Propose every dark patch in the scene and screen each one.

    This is the measurement the project could not make before. A detection count on its
    own has no denominator: "how many dark patches were there, and how many did we
    decline to call oil" is the question a false-positive rate answers, and it is asked
    here on exactly the kind of input the screen was fitted on -- darkness proposals over
    calibrated decibels -- rather than on the network's output.
    """
    proposals, count = lookalike_mod.propose(planes[0], valid, cfg)
    patches: list[dict[str, Any]] = []
    tally: dict[str, int] = {"accepted": 0, "uncertain": 0, "rejected": 0, "unscreened": 0}
    overlapping_count = 0
    rejected_on_slick = 0

    for index in range(1, count + 1):
        region = proposals == index
        block = screen_region(
            region,
            planes=planes,
            exclude=((proposals > 0) & ~region) | ~valid,
            spacing_m=spacing_m,
            screen=screen,
            cfg=cfg,
        )
        label = str(block["label"])
        tally[label] = tally.get(label, 0) + 1

        rows, cols = np.nonzero(region)
        lon, lat = transform.apply(float(cols.mean()), float(rows.mean()))
        hit = stamp[region]
        touched = hit[hit > 0]
        slick: str | None = None
        fraction = 0.0
        if touched.size:
            overlapping_count += 1
            if label == "rejected":
                rejected_on_slick += 1
            top = int(np.bincount(touched).argmax())
            if 0 < top <= len(slick_ids):
                slick = str(slick_ids[top - 1])
            fraction = float(touched.size) / float(max(1, hit.size))
        pixels = int(region.sum())
        patches.append(
            {
                "id": f"patch-{index:02d}",
                "pixels": pixels,
                "areaKm2": round(pixels * spacing_m**2 / 1e6, 6),
                "centroid": [round(float(lon), 6), round(float(lat), 6)],
                "overlapsSlick": slick,
                "overlapFraction": round(fraction, 4),
                "label": label,
                "oilLikelihood": block.get("oilLikelihood"),
                "headline": block.get("headline"),
                "reasons": block.get("reasons") or [],
                "measured": block.get("measured") or {},
            }
        )

    limits = [
        "the screen separates oil-like from not-oil-like; it cannot name the phenomenon, "
        "so a rejected patch is never called algae, low wind or a wake",
        "a patch that falls between the two thresholds is returned as uncertain and kept: "
        "suppressing a detection the screen is unsure of would trade a measured false "
        "positive for an unmeasured missed spill",
        "these patches are darkness proposals, not detections. A rejected patch that "
        "overlaps no published slick was never in this case to begin with -- it is "
        "evidence about what the screen would have thrown out, not a correction to the "
        f"{_plural(len(slick_ids), 'slick')} above",
    ]
    if unfitted:
        limits.insert(0, unfitted)

    # The headline has to carry the overlap. "24 dark patches rejected as look-alikes" printed
    # next to a published slick reads as a contradiction, and on a scene where every proposal
    # sits on that slick the honest reading is narrower: the screen is rejecting the way a
    # darkness threshold cut the water up, not the detection.
    headline = (
        f"{_plural(count, 'dark patch')} proposed, "
        f"{tally['rejected']} rejected as more consistent with a look-alike than oil"
    )
    if tally["rejected"] and rejected_on_slick == tally["rejected"]:
        headline += (
            "; every one of them lies inside a published slick, so what is rejected here is "
            "how the darkness threshold cut that slick up, not the detection"
        )
    elif rejected_on_slick:
        headline += (
            f"; {rejected_on_slick} of those lie inside a published slick and "
            f"{tally['rejected'] - rejected_on_slick} elsewhere in the scene"
        )

    return {
        "question": (
            "which dark patches in this scene are consistent with an oil film, and which "
            "look like oil in radar but are more consistent with something else?"
        ),
        "headline": headline,
        "fitted": bool(screen.fitted),
        "calibration": screen.calibration,
        "trainedOn": screen.trained_on,
        "thresholds": {
            "rejectAtOrBelow": cfg.reject_at_or_below,
            "acceptAtOrAbove": cfg.accept_at_or_above,
        },
        "proposer": {
            "rule": (
                f"pixels at or below mean - {cfg.darkness_k:g} standard deviations of the "
                "scene's own valid water, smoothed first, cleaned up morphologically, then "
                f"anything under {cfg.min_area_px} px discarded"
            ),
            "maxRegions": cfg.max_regions,
            "config": cfg.to_dict(),
        },
        "counts": {
            "proposed": int(count),
            "overlappingPublishedSlick": overlapping_count,
            **tally,
        },
        "components": {
            **_component_tally(components),
            "note": (
                "the same screen applied to what the segmentation network published. The "
                "patch counts above are darkness proposals over the whole scene, cut by a "
                "threshold rather than by the network, so the two sets of verdicts can "
                "disagree about the same water"
            ),
        },
        "features": lookalike_mod.describe_features(),
        "patches": patches,
        "limits": limits,
    }


def build_case(
    request: CaseRequest,
    progress: Progress | None = None,
    detection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run every stage and return the payload the dashboard consumes.

    ``detection`` lets a caller supply an already-computed mask -- the drift endpoint
    re-runs the trajectory with new parameters, and re-inferring the same scene through
    the network to get the same mask back would waste most of the request.
    """
    say: Progress = progress or (lambda _message: None)
    timer = _Timer()
    started_all = time.perf_counter()

    image_path, mask_path = scene_paths(request.scene)

    say(f"decoding {request.scene}")
    started = time.perf_counter()
    scene = dataset_mod.load_scene(image_path, mask_path, want_mask=mask_path is not None)
    timer.record("decode", started, f"{scene.width}x{scene.height}, 2 bands")

    if scene.bounds is None:
        raise CaseError(f"scene {scene.name} has no usable bounds")
    transform = Affine(*scene.transform)
    acquired = scene.acquired_start or scene.acquired_stop
    if acquired is None:
        raise CaseError(f"scene {scene.name} carries no acquisition time")

    started = time.perf_counter()
    if detection is None:
        say("detecting oil")
        detection = detect(scene, request, say)
        reused = False
    else:
        say("reusing the stored detection")
        detection = dict(detection)
        reused = True
    mask = detection.pop("mask")
    probability = detection.pop("probability", None)
    if mask.shape != (scene.height, scene.width):
        raise CaseError(
            f"supplied mask is {mask.shape}, scene is {(scene.height, scene.width)}"
        )
    timer.record("detect", started, detection["source"] + (" (reused)" if reused else ""))

    say("measuring slick geometry")
    started = time.perf_counter()
    geometry_cfg = geometry_mod.GeometryConfig()
    screen, unfitted = _load_screen()
    screen_cfg = _screen_config(screen)
    spacing_m = nominal_spacing_m(transform, scene.height)
    valid = ~np.asarray(scene.invalid, dtype=bool)
    # Denoised once and used three times: it is the footprint the published polygons
    # describe, it is what the screening excludes from a region's background water, and it
    # is what the drift particles are seeded from further down.
    seed_mask = geometry_mod.denoise(mask.astype(bool), geometry_cfg)
    # Published component n is stamped as the value n, so the scene-wide screening pass
    # can attribute a dark patch to a slick without re-deriving the components.
    stamp = np.zeros(mask.shape, dtype=np.int16)
    geometry = geometry_mod.analyse(
        mask,
        transform,
        probability=probability,
        cfg=geometry_cfg,
        epsg=scene.epsg,
        source=detection["source"],
        annotate=slick_screener(
            planes=scene.channels,
            detected=seed_mask,
            valid=valid,
            spacing_m=spacing_m,
            screen=screen,
            cfg=screen_cfg,
            stamp=stamp,
        ),
    )
    published = int(geometry["summary"]["componentsPublished"])
    timer.record("geometry", started, f"{published} slick(s)")
    if published == 0:
        raise CaseError(
            f"no slick survived filtering on scene {scene.name}; nothing to drift or attribute"
        )

    say("screening dark patches for look-alikes")
    started = time.perf_counter()
    screening = screen_scene(
        planes=scene.channels,
        valid=valid,
        transform=transform,
        stamp=stamp,
        slick_ids=[str(entry.get("id")) for entry in geometry["slicks"]],
        spacing_m=spacing_m,
        screen=screen,
        cfg=screen_cfg,
        unfitted=unfitted,
        components=geometry["slicks"],
    )
    timer.record(
        "screening",
        started,
        f"{screening['counts']['proposed']} dark patch(es), "
        f"{screening['counts']['rejected']} rejected; "
        f"{screening['components']['accepted']} of {screening['components']['screened']} "
        "published slick(s) consistent with oil",
    )

    say("resolving drift forcing")
    started = time.perf_counter()
    forcing, decision = forcing_mod.resolve_forcing(
        scene.bounds, acquired, scene.name, close_surface=False
    )
    timer.record("forcing", started, decision.get("mode", "unknown"))

    drift_cfg = C.DriftConfig(particle_count=int(request.particles))
    if request.horizon_hours is not None:
        drift_cfg.horizon_hours = float(request.horizon_hours)

    try:
        say("reconstructing backward drift")
        started = time.perf_counter()
        # Seed from the *denoised* mask, so the particles start from the same footprint
        # the published polygon describes rather than from speckle the geometry dropped.
        seed_lon, seed_lat, seeding = drift_engine.seed_from_mask(
            seed_mask,
            transform,
            drift_cfg.particle_count,
            C.stable_seed(f"drift:{request.key()}", request.seed),
        )
        backward = drift_engine.simulate(
            forcing, seed_lon, seed_lat, acquired, direction="backward",
            cfg=drift_cfg, seeding=seeding,
        )
        timer.record("backward", started, f"{drift_cfg.particle_count} particles")

        say("projecting forward drift")
        started = time.perf_counter()
        forward = drift_engine.simulate(
            forcing, seed_lon, seed_lat, acquired, direction="forward",
            cfg=drift_cfg, seeding=seeding,
        )
        timer.record("forward", started, f"{drift_cfg.horizon_hours:g} h")

        say("generating synthetic AIS")
        started = time.perf_counter()
        feed = ais_mod.generate_ais(backward, request.key(), is_water=forcing.is_water)
        timer.record("ais", started, f"{feed['counts']['vessels']} vessels")
    finally:
        close = getattr(forcing, "close", None)
        if callable(close):
            close()

    say("scoring vessels")
    started = time.perf_counter()
    ranking = scoring_mod.rank_vessels(feed, backward)
    timer.record("scoring", started, f"{ranking['candidateCount']} candidates")

    previews: dict[str, Any] | None = None
    if request.previews:
        say("rendering previews")
        started = time.perf_counter()
        previews = preview_mod.render_scene_previews(
            scene,
            prediction=mask if detection["source"] != "reference" else None,
            # None when the detection came from the store rather than from inference, in
            # which case the renderer simply omits the heatmap instead of inventing one.
            probability=probability if detection["source"] != "reference" else None,
            out_dir=C.PREVIEW_DIR,
        )
        # The renderer returns bare filenames; record where they live so a caller can
        # resolve them without having to know which directory was passed in. Relative to
        # the repository rather than absolute, so a case document written on one machine
        # still resolves on another -- a stored `/Users/someone/...` is a dead end for
        # anyone who clones this.
        previews["directory"] = C.PREVIEW_DIR.relative_to(C.REPO_ROOT).as_posix()
        timer.record("previews", started, ", ".join(sorted(previews.get("files", {}))))

    payload = assemble(
        request=request,
        scene=scene,
        detection=detection,
        geometry=geometry,
        screening=screening,
        decision=decision,
        backward=backward,
        forward=forward,
        feed=feed,
        ranking=ranking,
        previews=previews,
    )
    payload["timing"] = {
        "totalSeconds": round(time.perf_counter() - started_all, 3),
        "stages": [stage.to_dict() for stage in timer.stages],
    }
    # The mask itself is far too large for JSON, but the drift endpoint needs it to avoid
    # re-inferring, so it is handed back out of band for the caller to cache.
    payload["_mask"] = mask
    return payload


def _largest_slick(geometry: dict[str, Any]) -> dict[str, Any]:
    """The slick the summary screens lead with: the biggest one by area."""
    slicks = geometry.get("slicks") or []
    if not slicks:
        return {}
    return max(slicks, key=lambda entry: entry.get("areaKm2") or 0.0)


def _polygon_of(slick: dict[str, Any], geometry: dict[str, Any]) -> list[list[float]]:
    """The outer ring of one slick, pulled from the GeoJSON the geometry stage built."""
    features = (geometry.get("geojson") or {}).get("features") or []
    for feature in features:
        properties = feature.get("properties") or {}
        if properties.get("id") != slick.get("id"):
            continue
        coordinates = (feature.get("geometry") or {}).get("coordinates") or []
        if coordinates:
            return coordinates[0]
    return []


def _all_outlines(geometry: dict[str, Any]) -> list[dict[str, Any]]:
    """Every published component's rings, in descending area order.

    Holes are carried through as well as exteriors: a slick with clean water inside it is
    a different observation from a solid one, and the map should not fill the gap.
    """
    out: list[dict[str, Any]] = []
    for feature in (geometry.get("geojson") or {}).get("features") or []:
        properties = feature.get("properties") or {}
        rings = (feature.get("geometry") or {}).get("coordinates") or []
        if not rings:
            continue
        out.append(
            {
                "id": properties.get("id"),
                "areaKm2": properties.get("areaKm2"),
                "exterior": rings[0],
                "holes": rings[1:],
                "geometryValid": properties.get("geometryValid"),
                "confidence": properties.get("confidence"),
            }
        )
    out.sort(key=lambda entry: -(entry.get("areaKm2") or 0.0))
    return out


def _km(value: Any) -> float | None:
    """Metres to kilometres, preserving "not measured" rather than turning it into 0."""
    return None if value is None else round(float(value) / 1000.0, 6)


def _product_field(scene: dataset_mod.Scene, key: str) -> Any:
    """One field of the decoded Sentinel-1 product identifier, or None.

    ``scene.header`` is :meth:`spilltrace_common.dimap.DimapHeader.to_dict` output, whose
    nested ``product`` entry is populated only when the DIMAP prefix carried a parseable
    product name. Both levels can be missing on a truncated header, so neither is assumed.
    """
    header = getattr(scene, "header", None) or {}
    product = header.get("product") if isinstance(header, dict) else None
    if not isinstance(product, dict):
        return None
    value = product.get(key)
    return list(value) if isinstance(value, list) else value


def _region_of(scene: dataset_mod.Scene) -> str | None:
    """An approximate marine-region name for the scene centre.

    Purely a label for the case list and the header bar. The bounding-box table behind it
    is coarse and is never used for geometry, drift or scoring.
    """
    bounds = scene.bounds
    if not bounds or len(bounds) < 4:
        return None
    from spilltrace_common import regions

    west, south, east, north = (float(value) for value in bounds[:4])
    return regions.region_label((south + north) / 2.0, (west + east) / 2.0)


def assemble(
    *,
    request: CaseRequest,
    scene: dataset_mod.Scene,
    detection: dict[str, Any],
    geometry: dict[str, Any],
    decision: dict[str, Any],
    backward: dict[str, Any],
    forward: dict[str, Any],
    feed: dict[str, Any],
    ranking: dict[str, Any],
    previews: dict[str, Any] | None,
    screening: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Shape the stage outputs into the frontend contract, adding nothing new."""
    primary = _largest_slick(geometry)

    return {
        "id": scene.name,
        "pipelineVersion": C.PIPELINE_VERSION,
        "generatedUtc": C.utc_now_iso(),
        "requestKey": request.key(),
        "status": C.LABEL_STATUS,
        "scene": {
            "name": scene.name,
            "width": scene.width,
            "height": scene.height,
            "bounds": [round(float(value), 6) for value in scene.bounds or []],
            "crs": scene.crs,
            "epsg": scene.epsg,
            "transform": [float(value) for value in scene.transform],
            "acquiredStartUtc": scene.acquired_start,
            "acquiredStopUtc": scene.acquired_stop,
            "bandSource": scene.band_source,
            "nodata": scene.nodata,
            "productId": _product_field(scene, "raw"),
            "mission": _product_field(scene, "missionName"),
            "mode": _product_field(scene, "mode"),
            "productType": _product_field(scene, "productType"),
            "polarisations": _product_field(scene, "polarisations") or list(C.CHANNEL_ORDER),
            "groupKey": (scene.header or {}).get("groupKey") or scene.group_key,
            "subsetIndex": (scene.header or {}).get("subsetIndex"),
            "region": _region_of(scene),
            "regionNote": C.LABEL_REGION_APPROXIMATE,
            "hasReferenceMask": scene.mask is not None,
        },
        "provenance": {
            "satellite": C.LABEL_SATELLITE,
            "aisMode": feed["mode"],
            "aisLabel": feed["label"],
            # The schema the feed conforms to, separate from whether it is real. These are
            # two independent facts and the Vessels screen shows both: the data is
            # synthetic, and it is synthetic *in MarineCadastre's format*, which is what
            # makes the swap to a real extract a path argument rather than a rewrite.
            "aisSchema": (feed.get("schema") or {}).get("format"),
            "aisSource": (feed.get("schema") or {}).get("label"),
            "aisSchemaReference": (feed.get("schema") or {}).get("reference"),
            "driftMode": decision.get("mode"),
            "driftLabel": decision.get("label"),
            # Wind and currents come from different products and either can be real on
            # its own, so ``driftMode`` (the currents) does not answer this. One of
            # "era5", "synthetic", "none" or "disabled" -- the wind that was actually
            # integrated, so "none" means there was no wind term at all.
            "windMode": decision.get("windMode"),
            "detectionSource": detection["source"],
            "detectionLabel": detection["label"],
            "status": C.LABEL_STATUS,
        },
        "detection": detection,
        "slick": {
            "id": primary.get("id"),
            "polygon": _polygon_of(primary, geometry),
            # Every published component, so the map draws the whole spill rather than only
            # its largest lobe: the headline area is the largest slick, but the scene here
            # routinely holds several.
            "outlines": _all_outlines(geometry),
            "areaKm2": primary.get("areaKm2"),
            "centroid": primary.get("centroid"),
            "confidence": primary.get("confidence"),
            "confidenceBasis": primary.get("confidenceBasis"),
            "perimeterKm": _km(primary.get("perimeterM")),
            "lengthKm": _km(primary.get("lengthM")),
            "widthKm": _km(primary.get("widthM")),
            "orientationDegFromNorth": primary.get("orientationDegFromNorth"),
            "elongation": primary.get("elongation"),
            "compactness": primary.get("compactness"),
            "touchesSceneEdge": primary.get("touchesSceneEdge"),
            "geometryValid": primary.get("geometryValid"),
            "ringGeometry": primary.get("ringGeometry"),
            "qualityFlags": primary.get("qualityFlags") or [],
            # The look-alike verdict for the slick the headline is about, so a screen can
            # show "consistent with an oil film" beside the area without walking the
            # geometry. The other components carry their own under `geometry.slicks`.
            "screening": primary.get("screening"),
            "slickCount": int(geometry["summary"]["componentsPublished"]),
            "totalAreaKm2": geometry["summary"].get("totalAreaKm2"),
            "areaMethod": geometry.get("areaMethod"),
        },
        "geometry": geometry,
        "screening": screening,
        "forcing": decision,
        "trajectories": {
            # The centroid of the particle cloud at each step, which is the line the map
            # draws; the per-particle paths live under `drift` for the detail views.
            "backward": [entry["centroid"] for entry in backward.get("timeline") or []],
            "forward": [entry["centroid"] for entry in forward.get("timeline") or []],
            "uncertainty": (backward.get("searchCorridor") or {}).get("ring") or [],
            "forwardUncertainty": (forward.get("searchCorridor") or {}).get("ring") or [],
            "originEstimate": backward.get("originEstimate"),
            "forwardEndpoint": forward.get("originEstimate"),
        },
        "drift": {"backward": backward, "forward": forward},
        "spillAge": age_mod.estimate(backward),
        "ais": feed,
        "vessels": ranking["candidates"],
        "attribution": ranking,
        "previews": previews,
        "limits": LIMITS,
    }


LIMITS = [
    "AIS traffic is synthetic. No real vessel appears anywhere in this product.",
    "Drift forcing is synthetic unless the CMEMS product covers the scene in both space "
    "and time; the mode is stated on every screen that uses it.",
    "Rankings are triage aids. Nothing here establishes responsibility for a discharge.",
    "Segmentation accuracy is measured on held-out patches from the supplied dataset "
    "only, grouped by parent acquisition; it is not a field-validated detection rate.",
    "Look-alike screening separates oil-like from not-oil-like. It does not identify the "
    "phenomenon, so no rejected patch is called algae, low wind or a wake.",
]
