"""Filesystem layout and run configuration for SpillTrace.

Everything is derived from the repository root so the pipeline runs from any
working directory, and so raw datasets stay outside the frontend bundle.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# services/common/spilltrace_common/config.py -> repo root is 3 levels up
REPO_ROOT = Path(__file__).resolve().parents[3]

def _load_local_env() -> None:
    """Load a simple repository .env without requiring python-dotenv.

    Existing process environment variables always win. Values are intentionally parsed
    conservatively; this is configuration, not a shell script.
    """
    env_path = REPO_ROOT / ".env"
    if not env_path.is_file():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass

_load_local_env()

# Raw inputs supplied by the user. Overridable for tests and alternate scenes.
IMAGE_DIR = Path(os.environ.get("SPILLTRACE_IMAGE_DIR", REPO_ROOT / "Oil"))
MASK_DIR = Path(os.environ.get("SPILLTRACE_MASK_DIR", REPO_ROOT / "Mask_oil"))
CMEMS_GLOB = "cmems_mod_glo_phy_*.nc"
# No wind file ships with the repository; see era5_path() for why.
ERA5_GLOB = "era5_wind*.nc"

DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
CACHE_DIR = PROCESSED_DIR / "cache"
SPLITS_PATH = PROCESSED_DIR / "splits.json"
AUDIT_JSON = PROCESSED_DIR / "audit.json"
AUDIT_MD = REPO_ROOT / "DATA_AUDIT.md"
NORM_STATS_PATH = PROCESSED_DIR / "norm_stats.json"
METRICS_PATH = PROCESSED_DIR / "metrics.json"
CASES_DIR = PROCESSED_DIR / "cases"
PREVIEW_DIR = PROCESSED_DIR / "previews"
EVAL_PREVIEW_DIR = PREVIEW_DIR / "eval"
MODELS_DIR = REPO_ROOT / "models"
CHECKPOINT_PATH = MODELS_DIR / "unet_vv_vh.npz"
WEB_DIR = REPO_ROOT / "apps" / "web"
# The fixtures sit directly under the web root because the client asks for them at
# `./demo/...`, relative to `index.html`, and there is no build step to rewrite that path.
WEB_FIXTURE_DIR = WEB_DIR / "demo"

# Pipeline version stamped into every result, per PRD section 15.
PIPELINE_VERSION = "spilltrace-0.1.0"
MODEL_VERSION = "unet-vvvh-b16d4-v1"
BASELINE_VERSION = "darkspot-vv-v1"

# Deterministic seeds. Every synthetic product is reproducible from these.
SEED_SPLIT = 20180803
SEED_SYNTHETIC_FORCING = 26143
SEED_SYNTHETIC_AIS = 4726143

# Physical constants
EARTH_RADIUS_M = 6_371_008.8

# Provenance strings the UI must display verbatim (PRD section 4).
LABEL_SATELLITE = "Satellite imagery: Supplied Sentinel-1 SAR dataset"
LABEL_PREDICTION = "Segmentation mask: Model prediction"
LABEL_REFERENCE = "Reference mask: Supplied ground truth"
LABEL_AIS = "AIS mode: Synthetic demonstration data"
LABEL_DRIFT_CMEMS = "Drift forcing: CMEMS data"
LABEL_DRIFT_SYNTHETIC = "Drift forcing: Synthetic scenario data"
# Wind and currents come from different products and either can be real on its own, so
# the label names both halves rather than collapsing them into one verdict.
LABEL_DRIFT_HYBRID = "Drift forcing: Synthetic currents with ERA5 wind"
LABEL_DRIFT_REAL = "Drift forcing: CMEMS currents with ERA5 wind"
LABEL_STATUS = "Status: Research PoC - human review required"
LABEL_CANDIDATE = "Priority candidate for investigation"
# Region names come from a hand-written bounding-box table (see regions.py), not from a
# gazetteer, so the UI has to say so wherever it prints one.
LABEL_REGION_APPROXIMATE = "Region name is an approximate offline label, not a gazetteer lookup"


@dataclass
class PreprocessConfig:
    """Preprocessing settings, stored alongside every derived product."""

    patch_size: int = 128
    # Scenes are 2048x2048; a stride equal to the patch size tiles without
    # overlap, which keeps patches from one scene from leaking across splits.
    stride: int = 128
    min_oil_fraction: float = 0.002  # patch is kept as positive above this
    # A patch that is mostly no-data padding teaches nothing.
    max_invalid_fraction: float = 0.5
    negatives_per_positive: float = 1.0
    max_patches_per_scene: int = 24
    max_scenes: int = 240  # decode budget; the full set is 1200 scenes
    val_fraction: float = 0.15
    test_fraction: float = 0.15
    seed: int = SEED_SPLIT
    # Clip limits are derived from training-split percentiles rather than fixed
    # constants: DATA_AUDIT.md shows VH reaching -84 dB and VV +22 dB, so any
    # hard-coded window would silently truncate real signal.
    clip_percentiles: tuple[float, float] = (0.5, 99.5)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class TrainConfig:
    """U-Net training settings.

    Width and depth were chosen by timing the actual forward+backward pass of
    this NumPy implementation rather than by convention. At batch 8 on 128x128
    patches: base 8 / depth 3 costs 30 ms per sample for 122 k parameters,
    base 16 / depth 3 costs 41 ms for 488 k, and base 16 / depth 4 costs 45 ms
    for 1.96 M. The extra encoder level runs at a quarter of the resolution, so
    depth buys capacity and receptive field far more cheaply than width - hence
    depth 4, which sees 128 -> 64 -> 32 -> 16 -> 8 and so has enough context to
    separate a slick from speckle.
    """

    base_channels: int = 16
    depth: int = 4
    epochs: int = 18
    batch_size: int = 8
    learning_rate: float = 2e-3
    # Cosine decay to this fraction of the initial rate by the final epoch.
    final_lr_fraction: float = 0.05
    weight_decay: float = 1e-5
    dice_weight: float = 0.5
    bce_weight: float = 0.5
    # Oil is a small minority of valid pixels, so the BCE term is up-weighted on
    # the positive class. Kept modest: a large value trades precision for recall.
    positive_weight: float = 2.0
    grad_clip_norm: float = 5.0
    # Stop when the validation Dice has not improved for this many epochs.
    patience: int = 5
    # Flips and 90-degree rotations only: a SAR scene has no canonical up, and
    # these are the transforms that leave the decibel values untouched.
    augment: bool = True
    threshold: float = 0.5
    seed: int = 1337
    max_train_patches: int = 1800
    max_val_patches: int = 400
    max_test_patches: int = 600

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class DriftConfig:
    """Particle drift settings (PRD section 9)."""

    horizon_hours: int = 24
    time_step_minutes: int = 30
    particle_count: int = 300
    windage_factor: float = 0.03
    diffusion_m2_s: float = 8.0
    use_wind: bool = True
    seed: int = SEED_SYNTHETIC_FORCING

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class ScoringWeights:
    """Vessel scoring maxima, exactly as tabulated in PRD section 11."""

    distance: int = 30
    time_window: int = 25
    trajectory: int = 20
    behaviour: int = 10
    vessel_type: int = 10
    data_completeness: int = 5

    @property
    def total(self) -> int:
        return (
            self.distance
            + self.time_window
            + self.trajectory
            + self.behaviour
            + self.vessel_type
            + self.data_completeness
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "distance": self.distance,
            "time_window": self.time_window,
            "trajectory": self.trajectory,
            "behaviour": self.behaviour,
            "vessel_type": self.vessel_type,
            "data_completeness": self.data_completeness,
        }


def display_path(path: Path | str) -> str:
    """A path fit to publish: relative to the repository whenever it sits inside it.

    Reports, case payloads and the audit are all committed or bundled, so an absolute
    path in one pins the artefact to a single machine's home directory and reads as
    nonsense after a clone. ``build_web.py`` refuses a bundle containing one. A dataset
    kept outside the repository is left absolute, because there is nothing shorter to say
    about it.
    """
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(resolved)


def cmems_path() -> Path | None:
    """Locate the supplied CMEMS NetCDF, if present."""
    override = os.environ.get("SPILLTRACE_CMEMS")
    if override:
        candidate = Path(override)
        return candidate if candidate.exists() else None
    matches = sorted(REPO_ROOT.glob(CMEMS_GLOB))
    return matches[0] if matches else None


def era5_path() -> Path | None:
    """Locate an ERA5 10 m wind NetCDF, if the operator has downloaded one.

    Absent by design: the Copernicus Data Store needs an account, so no wind file ships
    with the repository and the drift stays honest about having no wind rather than
    inventing one. ``SPILLTRACE_ERA5`` overrides the search.
    """
    override = os.environ.get("SPILLTRACE_ERA5")
    if override:
        candidate = Path(override)
        return candidate if candidate.exists() else None
    for root in (RAW_DIR, REPO_ROOT):
        matches = sorted(root.glob(ERA5_GLOB))
        if matches:
            return matches[0]
    return None


def utc_now_iso() -> str:
    """Current UTC instant, formatted the way every artefact stamps itself."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stable_seed(key: str, base: int) -> int:
    """A reproducible 32-bit seed from a string key.

    ``hash()`` on a string varies with ``PYTHONHASHSEED``, which would make every
    "deterministic" synthetic product differ between runs. SHA-256 does not.
    """
    digest = hashlib.sha256(f"{base}:{key}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


def ensure_dirs() -> None:
    for directory in (
        RAW_DIR,
        PROCESSED_DIR,
        CACHE_DIR,
        CASES_DIR,
        PREVIEW_DIR,
        MODELS_DIR,
        WEB_FIXTURE_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: Any) -> None:
    """Write JSON atomically so a crashed run never leaves a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, default=_json_default)
        fh.write("\n")
    tmp.replace(path)


def read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")
