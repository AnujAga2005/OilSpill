"""Phase 2: preprocessing, image-mask pairing, splits and patch extraction.

Design decisions all trace back to findings in ``DATA_AUDIT.md``:

* Bands are located by DIMAP name, so ``VV``/``VH`` are correct per scene rather
  than assumed from position (the supplied files store VH first).
* Masks carry no georeferencing, so a pair is only accepted when both rasters
  have identical dimensions and the image has a usable transform.
* Scenes are ``subset_N_of_<product>`` crops of a smaller number of parent
  acquisitions, so splits are grouped by parent product to stop crops of one
  acquisition from appearing in more than one split.
* The DIMAP no-data value is ``0.0``; exact zeros and non-finite samples become
  an explicit invalid mask that is excluded from statistics, loss and metrics.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import numpy as np

from spilltrace_common import config as C
from spilltrace_common import dimap
from spilltrace_common.geotiff import GeoTiff, GeoTiffError, read_geotiff

# Label values used inside the packed per-pixel target array.
LABEL_BACKGROUND = 0
LABEL_OIL = 1
LABEL_INVALID = 2

CHANNEL_ORDER = ("VV", "VH")


class PreprocessError(Exception):
    """Raised when a scene cannot be turned into a training sample."""


@dataclass
class Scene:
    """A decoded, channel-ordered scene with its invalid mask."""

    name: str
    # (2, H, W) float32 decibels, ordered VV then VH.
    channels: np.ndarray
    # (H, W) bool, True where the sample must not be used.
    invalid: np.ndarray
    # (H, W) uint8 reference mask, or None when not requested.
    mask: np.ndarray | None
    transform: list[float]
    bounds: list[float] | None
    crs: str | None
    epsg: int | None
    acquired_start: str | None
    acquired_stop: str | None
    band_source: dict[str, int]
    nodata: float | None
    group_key: str | None
    #: How ``band_source`` was arrived at, in words. Published because a mapping read from
    #: a header and one assumed from the dataset's convention carry different weight.
    band_basis: str = ""
    header: dict[str, Any] = field(default_factory=dict)

    @property
    def height(self) -> int:
        return int(self.channels.shape[1])

    @property
    def width(self) -> int:
        return int(self.channels.shape[2])

    def valid_fraction(self) -> float:
        return float(1.0 - self.invalid.mean())


#: Band orders an operator can declare for a scene whose file does not name its bands.
#: Values are the raster index of each polarisation.
BAND_ORDERS: dict[str, dict[str, int]] = {
    "vh-vv": {"VH": 0, "VV": 1},
    "vv-vh": {"VV": 0, "VH": 1},
}


def resolve_band_indices(
    header: dimap.DimapHeader | None,
    band_count: int,
    declared: str | None = None,
) -> tuple[dict[str, int], str]:
    """Map ``VV``/``VH`` to raster band indices, and say how the mapping was decided.

    The second element of the return is the basis, because the three ways of arriving at
    a mapping are not equally trustworthy and the difference has to survive into the case
    document. A DIMAP header *names* its bands. A declaration is an operator's word. The
    fallback is a guess taken from the supplied dataset: if a scene's bands are the other
    way round, every pixel is scored with VV and VH swapped and nothing looks wrong.
    """
    resolved: dict[str, int] = {}
    if header is not None:
        for pol in CHANNEL_ORDER:
            index = header.band_index(pol)
            if index is not None and index < band_count:
                resolved[pol] = index
    if len(resolved) == len(CHANNEL_ORDER):
        return resolved, "band names read from the product's DIMAP header"

    if declared:
        key = str(declared).strip().lower()
        if key not in BAND_ORDERS:
            raise PreprocessError(
                f"unknown band order {declared!r}; expected one of "
                f"{', '.join(sorted(BAND_ORDERS))}"
            )
        order = BAND_ORDERS[key]
        if band_count < len(order):
            raise PreprocessError(
                f"band order {key!r} needs {len(order)} bands; the file has {band_count}"
            )
        return dict(order), f"band order {key} declared by the operator; the file does not name its bands"

    # Fall back to the order observed in the supplied dataset (VH, then VV) only
    # when the DIMAP names are unavailable, and only if the band count fits.
    if band_count >= 2:
        return {"VH": 0, "VV": 1}, (
            "assumed VH then VV, the order of the supplied dataset; this file names no "
            "bands and no order was declared"
        )
    raise PreprocessError(
        f"cannot resolve VV/VH from {band_count} band(s) and header {header}"
    )


def load_scene(
    image_path: str | Path,
    mask_path: str | Path | None = None,
    want_mask: bool = True,
    band_order: str | None = None,
) -> Scene:
    """Decode one image (and optionally its mask) into a :class:`Scene`.

    ``band_order`` is only consulted when the file carries no DIMAP header to name its
    bands, which is the case for a plain GeoTIFF an operator exports themselves. A header,
    when present, always wins over a declaration.
    """
    image_path = Path(image_path)
    with GeoTiff(str(image_path)) as tif:
        meta = tif.meta
        blob = tif.read_bulk_tag(dimap.DIMAP_TAG, dimap.HEADER_BYTES)
        pixels = tif.read_float32()
    header: dimap.DimapHeader | None = None
    if blob:
        try:
            header = dimap.parse_header(blob)
        except dimap.DimapError:
            header = None

    if meta.geo.transform is None:
        raise PreprocessError(f"{image_path.name} has no geotransform")

    indices, band_basis = resolve_band_indices(header, pixels.shape[0], band_order)
    channels = np.stack([pixels[indices[pol]] for pol in CHANNEL_ORDER]).astype(
        np.float32, copy=False
    )

    nodata = meta.nodata
    if header is not None and header.nodata_used and header.nodata_value is not None:
        nodata = header.nodata_value
    invalid = ~np.isfinite(channels).all(axis=0)
    if nodata is not None:
        invalid |= (channels == np.float32(nodata)).any(axis=0)

    mask = None
    if want_mask and mask_path is not None:
        mask_path = Path(mask_path)
        with GeoTiff(str(mask_path)) as mtif:
            if (mtif.meta.width, mtif.meta.height) != (meta.width, meta.height):
                raise PreprocessError(
                    f"{mask_path.name} is {mtif.meta.width}x{mtif.meta.height} but "
                    f"{image_path.name} is {meta.width}x{meta.height}"
                )
            mask = mtif.read()[0]
        mask = (np.asarray(mask) > 0.5).astype(np.uint8)

    bounds = meta.bounds()
    return Scene(
        name=image_path.stem,
        channels=channels,
        invalid=invalid,
        mask=mask,
        transform=list(meta.geo.transform.as_list()),
        bounds=list(bounds) if bounds else None,
        crs=meta.geo.crs,
        epsg=meta.geo.epsg,
        acquired_start=dimap.iso_utc(header.scene_start) if header else None,
        acquired_stop=dimap.iso_utc(header.scene_stop) if header else None,
        band_source=indices,
        band_basis=band_basis,
        nodata=nodata,
        group_key=header.group_key if header else None,
        header=header.to_dict() if header else {},
    )


# ---------------------------------------------------------------------------
# Splits
# ---------------------------------------------------------------------------


def _stable_unit(key: str, seed: int) -> float:
    """Deterministic value in [0, 1) from a string key, independent of PYTHONHASHSEED."""
    digest = hashlib.sha256(f"{seed}:{key}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def make_splits(
    scenes: Sequence[dict[str, Any]],
    cfg: C.PreprocessConfig | None = None,
) -> dict[str, Any]:
    """Assign scenes to train/val/test, grouped by parent acquisition.

    Grouping is what prevents leakage: every crop of one Sentinel-1 product lands
    in the same split. Assignment is a stable hash of the group key, so the split
    is reproducible and stays stable when scenes are added.
    """
    cfg = cfg or C.PreprocessConfig()
    groups: dict[str, list[dict[str, Any]]] = {}
    for scene in scenes:
        key = scene.get("group_key") or scene.get("groupKey") or scene["name"]
        groups.setdefault(str(key), []).append(scene)

    ordered = sorted(groups.items(), key=lambda kv: kv[0])
    assignments: dict[str, str] = {}
    for key, _members in ordered:
        u = _stable_unit(key, cfg.seed)
        if u < cfg.test_fraction:
            assignments[key] = "test"
        elif u < cfg.test_fraction + cfg.val_fraction:
            assignments[key] = "val"
        else:
            assignments[key] = "train"

    # Guarantee every split is non-empty even on tiny datasets: move the largest
    # groups from train until val and test have at least one group each.
    for split in ("val", "test"):
        if not any(v == split for v in assignments.values()):
            donors = sorted(
                (k for k, v in assignments.items() if v == "train"),
                key=lambda k: (-len(groups[k]), k),
            )
            if len(donors) > 1:
                assignments[donors[-1]] = split

    out: dict[str, list[str]] = {"train": [], "val": [], "test": []}
    group_map: dict[str, str] = {}
    for key, members in ordered:
        split = assignments[key]
        group_map[key] = split
        out[split].extend(sorted(m["name"] for m in members))
    for split in out:
        out[split].sort()

    return {
        "seed": cfg.seed,
        "rule": (
            "scenes are grouped by parent Sentinel-1 product; each group is assigned "
            "to one split by a stable SHA-256 hash of the group key, so no crop of an "
            "acquisition can appear in more than one split"
        ),
        "fractions": {
            "train": round(1.0 - cfg.val_fraction - cfg.test_fraction, 4),
            "val": cfg.val_fraction,
            "test": cfg.test_fraction,
        },
        "counts": {k: len(v) for k, v in out.items()},
        "groupCounts": {
            split: sum(1 for v in group_map.values() if v == split)
            for split in ("train", "val", "test")
        },
        "groups": group_map,
        "scenes": out,
    }


def split_of(splits: dict[str, Any], scene_name: str) -> str | None:
    for split, names in splits["scenes"].items():
        if scene_name in names:
            return split
    return None


# ---------------------------------------------------------------------------
# Patch extraction
# ---------------------------------------------------------------------------


@dataclass
class Patch:
    """One training sample cut from a scene."""

    scene: str
    row: int
    col: int
    # (2, S, S) float32 decibels, VV then VH
    channels: np.ndarray
    # (S, S) uint8 in {LABEL_BACKGROUND, LABEL_OIL, LABEL_INVALID}
    target: np.ndarray

    @property
    def oil_fraction(self) -> float:
        return float((self.target == LABEL_OIL).mean())

    @property
    def invalid_fraction(self) -> float:
        return float((self.target == LABEL_INVALID).mean())


def iter_patches(scene: Scene, cfg: C.PreprocessConfig) -> Iterator[Patch]:
    """Tile a scene into non-overlapping patches, image and mask together."""
    size = cfg.patch_size
    stride = cfg.stride or size
    if scene.mask is None:
        raise PreprocessError(f"scene {scene.name} has no reference mask")
    for row in range(0, scene.height - size + 1, stride):
        for col in range(0, scene.width - size + 1, stride):
            window = (slice(row, row + size), slice(col, col + size))
            target = scene.mask[window].astype(np.uint8).copy()
            target[scene.invalid[window]] = LABEL_INVALID
            yield Patch(
                scene=scene.name,
                row=row,
                col=col,
                channels=scene.channels[:, window[0], window[1]].copy(),
                target=target,
            )


def select_patches(
    patches: Sequence[Patch],
    cfg: C.PreprocessConfig,
    rng: np.random.Generator | None = None,
) -> list[Patch]:
    """Keep the oil-bearing patches plus a bounded, deterministic negative sample.

    The audit found the oil class occupies about 3 % of pixels on average, so
    training on every tile would be dominated by empty water. Negatives are still
    needed to teach the model what clean sea and look-alikes look like, so a fixed
    ratio is kept rather than dropping them.
    """
    rng = rng or np.random.default_rng(cfg.seed)
    usable = [p for p in patches if p.invalid_fraction < cfg.max_invalid_fraction]
    positives = [p for p in usable if p.oil_fraction >= cfg.min_oil_fraction]
    negatives = [p for p in usable if p.oil_fraction == 0.0]

    cap = cfg.max_patches_per_scene
    positive_cap = max(1, int(round(cap / (1.0 + cfg.negatives_per_positive))))
    if len(positives) > positive_cap:
        # Keep the strongest signal: the patches with the most labelled oil.
        positives = sorted(positives, key=lambda p: (-p.oil_fraction, p.row, p.col))
        positives = sorted(positives[:positive_cap], key=lambda p: (p.row, p.col))

    want = min(
        int(round(len(positives) * cfg.negatives_per_positive)),
        max(0, cap - len(positives)),
    )
    chosen: list[Patch] = []
    if want and negatives:
        # Prefer the darkest negatives: look-alike-prone water is more useful than
        # bright open sea, and this keeps the choice deterministic.
        order = np.argsort([float(p.channels[0].mean()) for p in negatives])
        pool = [negatives[i] for i in order[: max(want * 2, want)]]
        if len(pool) > want:
            idx = rng.choice(len(pool), size=want, replace=False)
            pool = [pool[i] for i in sorted(idx)]
        chosen = pool
    return sorted(positives + chosen, key=lambda p: (p.row, p.col))


# ---------------------------------------------------------------------------
# Normalisation statistics
# ---------------------------------------------------------------------------


def compute_norm_stats(
    patches: Iterable[Patch], clip_percentiles: tuple[float, float] = (0.5, 99.5)
) -> dict[str, Any]:
    """Per-channel statistics from the training split only.

    Invalid pixels are excluded. Robust clip limits are reported alongside the
    mean and standard deviation so inference can bound outliers the same way.
    """
    sums = np.zeros(2, dtype=np.float64)
    squares = np.zeros(2, dtype=np.float64)
    counts = np.zeros(2, dtype=np.int64)
    reservoir: list[np.ndarray] = []
    total = 0
    for patch in patches:
        total += 1
        valid = patch.target != LABEL_INVALID
        if not valid.any():
            continue
        for channel in range(2):
            values = patch.channels[channel][valid].astype(np.float64)
            sums[channel] += values.sum()
            squares[channel] += np.square(values).sum()
            counts[channel] += values.size
        # Keep a bounded, deterministic subsample for percentile estimation.
        if len(reservoir) < 400:
            step = max(1, patch.channels[0][valid].size // 512)
            reservoir.append(
                np.stack([patch.channels[c][valid][::step] for c in range(2)])
            )

    if not counts.all():
        raise PreprocessError("no valid pixels available to compute normalisation stats")
    mean = sums / counts
    variance = np.maximum(squares / counts - np.square(mean), 1e-12)
    std = np.sqrt(variance)

    clip_low = [None, None]
    clip_high = [None, None]
    if reservoir:
        pooled = np.concatenate([r for r in reservoir], axis=1)
        for channel in range(2):
            lo, hi = np.percentile(pooled[channel], clip_percentiles)
            clip_low[channel] = round(float(lo), 4)
            clip_high[channel] = round(float(hi), 4)

    return {
        "channels": list(CHANNEL_ORDER),
        "unit": "decibels (sigma0, intensity_db)",
        "source": "training split patches only, invalid pixels excluded",
        "patchesUsed": total,
        "mean": [round(float(m), 6) for m in mean],
        "std": [round(float(s), 6) for s in std],
        "clipPercentiles": list(clip_percentiles),
        "clipLow": clip_low,
        "clipHigh": clip_high,
        "pixelsPerChannel": [int(c) for c in counts],
    }


def normalise(channels: np.ndarray, stats: dict[str, Any]) -> np.ndarray:
    """Apply clip-then-standardise using training-split statistics."""
    out = np.asarray(channels, dtype=np.float32).copy()
    mean = np.asarray(stats["mean"], dtype=np.float32)
    std = np.asarray(stats["std"], dtype=np.float32)
    low = stats.get("clipLow") or [None, None]
    high = stats.get("clipHigh") or [None, None]
    for channel in range(out.shape[0]):
        lo = low[channel] if channel < len(low) else None
        hi = high[channel] if channel < len(high) else None
        if lo is not None and hi is not None:
            np.clip(out[channel], lo, hi, out=out[channel])
        out[channel] = (out[channel] - mean[channel]) / max(float(std[channel]), 1e-6)
    return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)


def normalise_batch(channels: np.ndarray, stats: dict[str, Any]) -> np.ndarray:
    """Cached batch ``(N, 2, H, W)`` decibels -> model input ``(N, H, W, 2)``.

    The cache is channel-first because that is how a scene is decoded; the network
    is channels-last because that makes each convolution one large BLAS call. The
    transpose happens here, once, alongside the same clip-then-standardise the
    per-scene path applies.
    """
    values = np.asarray(channels, dtype=np.float32)
    if values.ndim != 4:
        raise PreprocessError(f"expected (N, C, H, W), got shape {values.shape}")
    mean = np.asarray(stats["mean"], dtype=np.float32)
    std = np.maximum(np.asarray(stats["std"], dtype=np.float32), 1e-6)
    low = stats.get("clipLow") or []
    high = stats.get("clipHigh") or []
    out = np.transpose(values, (0, 2, 3, 1)).copy()
    for channel in range(out.shape[-1]):
        lo = low[channel] if channel < len(low) else None
        hi = high[channel] if channel < len(high) else None
        if lo is not None and hi is not None:
            np.clip(out[..., channel], lo, hi, out=out[..., channel])
    out -= mean
    out /= std
    return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)
