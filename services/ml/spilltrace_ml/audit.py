"""Phase 1: audit the supplied datasets.

Reports exactly what PRD section 7.1 requires -- file counts, matched and
unmatched names, dimensions, channel counts, dtypes and value ranges, CRS and
bounds, mask unique values and invalid values -- and refuses to declare the
dataset usable if image/mask pairing is broken.

Nothing here is assumed about the data: every fact is read from the files.
"""

from __future__ import annotations

import math
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from spilltrace_common import config as C
from spilltrace_common import dimap
from spilltrace_common.geotiff import GeoTiff, GeoTiffError, read_geotiff
from spilltrace_common.regions import region_label

# How many full scenes to decode for true pixel-value statistics. Each decode
# costs ~6 s of pure-Python LZW, so the audit samples deterministically and says
# so, rather than silently reporting stats from one file.
DEFAULT_PIXEL_SAMPLE = 16


@dataclass
class FileProblem:
    """A single defect found during the audit."""

    severity: str  # "error" blocks the pipeline, "warning" is recorded only
    kind: str
    detail: str
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "kind": self.kind,
            "detail": self.detail,
            "name": self.name,
        }


def _display_path(path: Path | str) -> str:
    """A path fit to publish: relative to the repository whenever it sits inside it.

    The audit report is committed, so an absolute path here would pin it to one machine's
    home directory and read as nonsense after a clone. A dataset kept outside the
    repository is left absolute, because there is nothing shorter to say about it.
    """
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(C.REPO_ROOT).as_posix()
    except ValueError:
        return str(resolved)


@dataclass
class SceneRecord:
    """Everything the audit learned about one image/mask pair."""

    name: str
    image_path: str
    mask_path: str | None
    width: int
    height: int
    bands: int
    dtype: str
    compression: str
    tiled: bool
    crs: str | None
    epsg: int | None
    transform: list[float] = field(default_factory=list)
    bounds: list[float] | None = None
    pixel_size_deg: list[float] | None = None
    pixel_size_m: list[float] | None = None
    band_names: list[str] = field(default_factory=list)
    vv_band: int | None = None
    vh_band: int | None = None
    nodata: float | None = None
    acquired_start: str | None = None
    acquired_stop: str | None = None
    mission: str | None = None
    mode: str | None = None
    product_type: str | None = None
    absolute_orbit: int | None = None
    group_key: str | None = None
    subset_index: int | None = None
    processing_chain: list[str] = field(default_factory=list)
    region: str | None = None
    centroid: list[float] | None = None
    mask_width: int | None = None
    mask_height: int | None = None
    mask_dtype: str | None = None
    mask_has_geo: bool = False
    mask_values: list[float] = field(default_factory=list)
    oil_pixels: int | None = None
    oil_fraction: float | None = None
    pixel_stats: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def _iter_tifs(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in (".tif", ".tiff")
    )


def _pixel_size_metres(transform: Sequence[float], lat: float) -> list[float]:
    """Convert degree pixel spacing to metres at a given latitude."""
    deg_x = abs(transform[1])
    deg_y = abs(transform[5])
    m_per_deg_lat = math.pi * C.EARTH_RADIUS_M / 180.0
    m_per_deg_lon = m_per_deg_lat * math.cos(math.radians(lat))
    return [round(deg_x * m_per_deg_lon, 4), round(deg_y * m_per_deg_lat, 4)]


def _summarise_band(values: np.ndarray, nodata: float | None = None) -> dict[str, Any]:
    """Value statistics for one band, reported raw and excluding invalid samples.

    The DIMAP declares 0.0 as no-data and terrain-corrected scenes are zero-padded
    outside the swath, so raw min/max alone would misrepresent the real signal
    range that normalisation has to cover.
    """
    finite_sel = np.isfinite(values)
    finite = values[finite_sel]
    zeros = int((values == 0).sum())
    out: dict[str, Any] = {
        "zeros": zeros,
        "zeroFraction": round(zeros / float(values.size), 6),
        "nonFinite": int(values.size - finite.size),
    }
    if finite.size == 0:
        out.update({"finite": 0, "min": None, "max": None, "mean": None, "std": None})
        return out
    percentiles = np.percentile(finite, [0.5, 1, 50, 99, 99.5])
    out.update(
        {
            "finite": int(finite.size),
            "min": round(float(finite.min()), 4),
            "max": round(float(finite.max()), 4),
            "mean": round(float(finite.mean()), 4),
            "std": round(float(finite.std()), 4),
            "p0_5": round(float(percentiles[0]), 4),
            "p1": round(float(percentiles[1]), 4),
            "median": round(float(percentiles[2]), 4),
            "p99": round(float(percentiles[3]), 4),
            "p99_5": round(float(percentiles[4]), 4),
        }
    )

    valid_sel = finite_sel
    if nodata is not None:
        valid_sel = valid_sel & (values != np.asarray(nodata, dtype=values.dtype))
    valid = values[valid_sel]
    if valid.size:
        vp = np.percentile(valid, [0.5, 50, 99.5])
        out["valid"] = {
            "count": int(valid.size),
            "min": round(float(valid.min()), 4),
            "max": round(float(valid.max()), 4),
            "mean": round(float(valid.mean()), 4),
            "std": round(float(valid.std()), 4),
            "p0_5": round(float(vp[0]), 4),
            "median": round(float(vp[1]), 4),
            "p99_5": round(float(vp[2]), 4),
        }
    else:
        out["valid"] = {"count": 0}
    return out


def audit(
    image_dir: Path | None = None,
    mask_dir: Path | None = None,
    pixel_sample: int = DEFAULT_PIXEL_SAMPLE,
    scan_masks: bool = True,
    limit: int = 0,
    progress: bool = True,
) -> dict[str, Any]:
    """Run the full Phase 1 audit and return the report as a dict."""
    started = time.time()
    image_dir = Path(image_dir or C.IMAGE_DIR)
    mask_dir = Path(mask_dir or C.MASK_DIR)

    images = _iter_tifs(image_dir)
    masks = _iter_tifs(mask_dir)
    if limit:
        images = images[:limit]
        masks = [m for m in masks if m.stem in {i.stem for i in images}]

    image_by_stem = {p.stem: p for p in images}
    mask_by_stem = {p.stem: p for p in masks}
    matched = sorted(set(image_by_stem) & set(mask_by_stem))
    images_without_mask = sorted(set(image_by_stem) - set(mask_by_stem))
    masks_without_image = sorted(set(mask_by_stem) - set(image_by_stem))

    problems: list[FileProblem] = []
    if not images:
        problems.append(
            FileProblem("error", "no-images", f"no TIFF files found in {image_dir}")
        )
    if not masks:
        problems.append(
            FileProblem("error", "no-masks", f"no TIFF files found in {mask_dir}")
        )
    for stem in images_without_mask:
        problems.append(
            FileProblem("error", "unmatched-image", "image has no matching mask", stem)
        )
    for stem in masks_without_image:
        problems.append(
            FileProblem("error", "unmatched-mask", "mask has no matching image", stem)
        )

    # Deterministic, evenly spread sample for the expensive full-pixel decode.
    sample_stems: set[str] = set()
    if matched and pixel_sample > 0:
        count = min(pixel_sample, len(matched))
        step = len(matched) / count
        sample_stems = {matched[min(len(matched) - 1, int(i * step))] for i in range(count)}

    records: list[SceneRecord] = []
    mask_value_counter: Counter[float] = Counter()
    decoded_stats: list[dict[str, Any]] = []

    for index, stem in enumerate(matched):
        if progress and index % 100 == 0:
            print(f"  scanning {index}/{len(matched)}", flush=True)
        image_path = image_by_stem[stem]
        mask_path = mask_by_stem[stem]
        try:
            record = _scan_pair(
                stem,
                image_path,
                mask_path,
                problems,
                scan_mask=scan_masks,
                decode_pixels=stem in sample_stems,
                mask_value_counter=mask_value_counter,
            )
        except (GeoTiffError, OSError, ValueError) as exc:
            problems.append(
                FileProblem("error", "unreadable", f"{type(exc).__name__}: {exc}", stem)
            )
            continue
        records.append(record)
        if record.pixel_stats:
            decoded_stats.append({"name": stem, **record.pixel_stats})

    report = _assemble(
        image_dir=image_dir,
        mask_dir=mask_dir,
        images=images,
        masks=masks,
        matched=matched,
        images_without_mask=images_without_mask,
        masks_without_image=masks_without_image,
        records=records,
        problems=problems,
        mask_value_counter=mask_value_counter,
        decoded_stats=decoded_stats,
        sample_stems=sorted(sample_stems),
        elapsed=time.time() - started,
        scan_masks=scan_masks,
    )
    report["forcing"] = _audit_forcing(records)
    report["elapsedSeconds"] = round(time.time() - started, 2)
    return report


def _audit_forcing(records: Sequence[SceneRecord]) -> dict[str, Any]:
    """Check the supplied CMEMS product against every distinct acquisition."""
    from spilltrace_common.cmems import CmemsError, CmemsSurface

    path = C.cmems_path()
    if path is None:
        return {
            "cmemsPresent": False,
            "verdict": "no CMEMS file supplied; drift uses synthetic forcing",
            "driftMode": "synthetic",
            "label": C.LABEL_DRIFT_SYNTHETIC,
        }
    try:
        surface = CmemsSurface(path)
    except (CmemsError, OSError, ValueError) as exc:
        return {
            "cmemsPresent": True,
            "cmemsReadable": False,
            "error": f"{type(exc).__name__}: {exc}",
            "verdict": "CMEMS file unreadable; drift uses synthetic forcing",
            "driftMode": "synthetic",
            "label": C.LABEL_DRIFT_SYNTHETIC,
        }

    try:
        description = surface.describe()
        # One representative scene per parent acquisition keeps this cheap.
        seen: dict[str, SceneRecord] = {}
        for record in records:
            key = record.group_key or record.name
            if key not in seen and record.bounds:
                seen[key] = record
        checks = []
        usable = 0
        spatial_only = 0
        for key, record in sorted(seen.items()):
            when = _parse_iso(record.acquired_start)
            verdict = surface.overlap(record.bounds, when)
            if verdict["usable"]:
                usable += 1
            elif verdict["spatialOverlap"]:
                spatial_only += 1
            checks.append(
                {
                    "groupKey": key,
                    "representativeScene": record.name,
                    "region": record.region,
                    "sceneTimeUtc": record.acquired_start,
                    "spatialOverlap": verdict["spatialOverlap"],
                    "temporalOverlap": verdict["temporalOverlap"],
                    "usable": verdict["usable"],
                    "timeGapDays": (
                        round(verdict["timeGapHours"] / 24.0, 1)
                        if verdict["timeGapHours"] is not None
                        else None
                    ),
                    "waterCellsOverScene": verdict["waterCellsOverScene"],
                    "windowStats": verdict["windowStats"],
                    "reasons": verdict["reasons"],
                }
            )
        drift_mode = "cmems" if usable == len(checks) and checks else "synthetic"
        return {
            "cmemsPresent": True,
            "cmemsReadable": True,
            "product": description,
            "acquisitionsChecked": len(checks),
            "acquisitionsUsable": usable,
            "acquisitionsSpatialOnly": spatial_only,
            "checks": checks,
            "driftMode": drift_mode,
            "label": (
                C.LABEL_DRIFT_CMEMS if drift_mode == "cmems" else C.LABEL_DRIFT_SYNTHETIC
            ),
            "verdict": _forcing_verdict(usable, spatial_only, len(checks)),
        }
    finally:
        surface.close()


def _forcing_verdict(usable: int, spatial_only: int, total: int) -> str:
    if total == 0:
        return "no georeferenced acquisitions to test"
    if usable == total:
        return "CMEMS covers every acquisition in space and time; CMEMS forcing is used"
    if spatial_only:
        return (
            f"CMEMS covers {spatial_only}/{total} acquisitions in space but none in "
            "time, so drift uses deterministic synthetic forcing and the UI reports "
            "the time-overlap failure"
        )
    return (
        "CMEMS does not cover the supplied acquisitions; drift uses deterministic "
        "synthetic forcing"
    )


def _parse_iso(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        pass
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _scan_pair(
    stem: str,
    image_path: Path,
    mask_path: Path,
    problems: list[FileProblem],
    scan_mask: bool,
    decode_pixels: bool,
    mask_value_counter: Counter,
) -> SceneRecord:
    with GeoTiff(str(image_path)) as tif:
        meta = tif.meta
        blob = tif.read_bulk_tag(dimap.DIMAP_TAG, dimap.HEADER_BYTES)
    header: dimap.DimapHeader | None = None
    if blob:
        try:
            header = dimap.parse_header(blob)
        except dimap.DimapError as exc:
            problems.append(FileProblem("warning", "dimap", str(exc), stem))
    else:
        problems.append(
            FileProblem("warning", "dimap-missing", "no embedded DIMAP metadata", stem)
        )

    bounds = meta.bounds()
    transform = meta.geo.transform
    centroid = None
    pixel_m = None
    if bounds:
        centroid = [
            round((bounds[0] + bounds[2]) / 2.0, 6),
            round((bounds[1] + bounds[3]) / 2.0, 6),
        ]
        if transform:
            pixel_m = _pixel_size_metres(transform.as_list(), centroid[1])

    record = SceneRecord(
        name=stem,
        image_path=_display_path(image_path),
        mask_path=_display_path(mask_path),
        width=meta.width,
        height=meta.height,
        bands=meta.samples,
        dtype=str(meta.dtype),
        compression=meta.compression_name,
        tiled=meta.tiled,
        crs=meta.geo.crs,
        epsg=meta.geo.epsg,
        transform=[round(v, 12) for v in transform.as_list()] if transform else [],
        bounds=[round(v, 8) for v in bounds] if bounds else None,
        pixel_size_deg=(
            [abs(transform.pixel_width), abs(transform.pixel_height)] if transform else None
        ),
        pixel_size_m=pixel_m,
        nodata=meta.nodata,
        centroid=centroid,
        region=region_label(centroid[1], centroid[0]) if centroid else None,
    )

    if header:
        record.band_names = header.band_names
        record.vv_band = header.band_index("VV")
        record.vh_band = header.band_index("VH")
        record.acquired_start = dimap.iso_utc(header.scene_start)
        record.acquired_stop = dimap.iso_utc(header.scene_stop)
        record.product_type = header.product_type
        record.group_key = header.group_key
        record.subset_index = header.subset_index
        record.processing_chain = header.processing_chain
        if header.nodata_used and header.nodata_value is not None:
            record.nodata = header.nodata_value
        if header.product:
            record.mission = header.product.mission_name
            record.mode = header.product.mode
            record.absolute_orbit = header.product.absolute_orbit
        if header.ncols and header.ncols != meta.width:
            problems.append(
                FileProblem(
                    "warning",
                    "dimap-size-mismatch",
                    f"DIMAP says {header.ncols}x{header.nrows}, TIFF says "
                    f"{meta.width}x{meta.height}",
                    stem,
                )
            )

    # -- geometry sanity ----------------------------------------------------
    if transform is None:
        problems.append(
            FileProblem("error", "no-transform", "image carries no geotransform", stem)
        )
    else:
        if not (transform.b and transform.f):
            problems.append(
                FileProblem("error", "degenerate-transform", "zero pixel size", stem)
            )
        if bounds and not (
            -180.0 <= bounds[0] <= 180.0
            and -180.0 <= bounds[2] <= 180.0
            and -90.0 <= bounds[1] <= 90.0
            and -90.0 <= bounds[3] <= 90.0
        ):
            problems.append(
                FileProblem(
                    "error", "bounds-out-of-range", f"bounds {bounds} not in WGS84", stem
                )
            )
    if meta.samples < 2:
        problems.append(
            FileProblem(
                "error", "band-count", f"expected 2 SAR channels, found {meta.samples}", stem
            )
        )
    if record.vv_band is None or record.vh_band is None:
        problems.append(
            FileProblem(
                "warning",
                "polarisation-unknown",
                f"could not resolve VV/VH from band names {record.band_names}",
                stem,
            )
        )

    # -- mask ---------------------------------------------------------------
    with GeoTiff(str(mask_path)) as mtif:
        mmeta = mtif.meta
        record.mask_width = mmeta.width
        record.mask_height = mmeta.height
        record.mask_dtype = str(mmeta.dtype)
        mask_transform = mmeta.geo.transform
        # A transform alone is not georeferencing. Four of the supplied masks
        # carry an ImageJ pixel-space identity transform (origin 0/2048, scale
        # 1/-1) with no CRS; trusting it would place the slick at 0 E, 2048 N.
        plausible_geo = bool(
            mask_transform is not None
            and mmeta.geo.epsg is not None
            and abs(mask_transform.pixel_width) < 1.0
            and abs(mask_transform.f) <= 90.0
        )
        record.mask_has_geo = plausible_geo
        if mask_transform is not None and not plausible_geo:
            problems.append(
                FileProblem(
                    "warning",
                    "mask-pixel-space-transform",
                    "mask carries a non-geographic transform "
                    f"{[round(v, 4) for v in mask_transform.as_list()]} with no CRS; "
                    "ignored in favour of the paired image transform",
                    stem,
                )
            )
        if scan_mask:
            mask = mtif.read()[0]
        else:
            mask = None

    if (record.mask_width, record.mask_height) != (record.width, record.height):
        problems.append(
            FileProblem(
                "error",
                "size-mismatch",
                f"image is {record.width}x{record.height} but mask is "
                f"{record.mask_width}x{record.mask_height}",
                stem,
            )
        )

    if mask is not None:
        values = np.unique(mask)
        record.mask_values = [float(v) for v in values[:8]]
        for v in values:
            mask_value_counter[float(v)] += 1
        if not set(record.mask_values) <= {0.0, 1.0}:
            problems.append(
                FileProblem(
                    "error",
                    "mask-values",
                    f"mask holds values {record.mask_values}, expected only 0 and 1",
                    stem,
                )
            )
        oil = int((mask == 1).sum())
        record.oil_pixels = oil
        record.oil_fraction = round(oil / float(mask.size), 8)
        if oil == 0:
            problems.append(
                FileProblem("warning", "empty-mask", "mask contains no oil pixels", stem)
            )

    # -- expensive true pixel statistics -----------------------------------
    if decode_pixels:
        pixels, _ = read_geotiff(str(image_path))
        stats: dict[str, Any] = {"bands": []}
        for band_index in range(pixels.shape[0]):
            label = (
                record.band_names[band_index]
                if band_index < len(record.band_names)
                else f"band{band_index}"
            )
            summary = _summarise_band(pixels[band_index], record.nodata)
            summary["band"] = band_index
            summary["name"] = label
            stats["bands"].append(summary)
        if mask is not None and pixels.shape[1:] == mask.shape:
            # Compare only valid samples, so zero padding cannot fake separation.
            valid = np.isfinite(pixels).all(axis=0)
            if record.nodata is not None:
                valid &= ~(pixels == np.float32(record.nodata)).any(axis=0)
            oil_sel = (mask == 1) & valid
            sea_sel = (mask == 0) & valid
            contrast = []
            for band_index in range(pixels.shape[0]):
                band = pixels[band_index]
                if oil_sel.any() and sea_sel.any():
                    contrast.append(
                        {
                            "band": band_index,
                            "name": (
                                record.band_names[band_index]
                                if band_index < len(record.band_names)
                                else f"band{band_index}"
                            ),
                            "oilMeanDb": round(float(band[oil_sel].mean()), 4),
                            "seaMeanDb": round(float(band[sea_sel].mean()), 4),
                            "separationDb": round(
                                float(band[oil_sel].mean() - band[sea_sel].mean()), 4
                            ),
                        }
                    )
            stats["maskContrast"] = contrast
        record.pixel_stats = stats
    return record


def _assemble(**kw: Any) -> dict[str, Any]:
    records: list[SceneRecord] = kw["records"]
    problems: list[FileProblem] = kw["problems"]
    matched: list[str] = kw["matched"]

    errors = [p for p in problems if p.severity == "error"]
    warnings = [p for p in problems if p.severity == "warning"]

    dims = Counter((r.width, r.height) for r in records)
    band_counts = Counter(r.bands for r in records)
    dtypes = Counter(r.dtype for r in records)
    mask_dtypes = Counter(str(r.mask_dtype) for r in records)
    compressions = Counter(r.compression for r in records)
    crs_values = Counter(str(r.crs) for r in records)
    epsg_values = Counter(r.epsg for r in records)
    band_name_sets = Counter(tuple(r.band_names) for r in records)
    chains = Counter(tuple(r.processing_chain) for r in records)
    missions = Counter(str(r.mission) for r in records)
    modes = Counter(str(r.mode) for r in records)
    regions = Counter(str(r.region) for r in records)
    masks_with_geo = sum(1 for r in records if r.mask_has_geo)

    groups: dict[str, list[str]] = defaultdict(list)
    for r in records:
        groups[r.group_key or "unknown"].append(r.name)

    times = [r.acquired_start for r in records if r.acquired_start]
    oil_fractions = [r.oil_fraction for r in records if r.oil_fraction is not None]

    # Global value range across every decoded sample, per band index.
    band_ranges: dict[int, dict[str, Any]] = {}
    for entry in kw["decoded_stats"]:
        for band in entry["bands"]:
            slot = band_ranges.setdefault(
                band["band"],
                {
                    "band": band["band"],
                    "name": band["name"],
                    "min": band["min"],
                    "max": band["max"],
                    "_means": [],
                    "_p0_5": [],
                    "_p99_5": [],
                    "zeros": 0,
                    "nonFinite": 0,
                    "_validMin": [],
                    "_validMax": [],
                    "_validMeans": [],
                    "_validStds": [],
                    "_validP0_5": [],
                    "_validP99_5": [],
                },
            )
            if band["min"] is not None:
                slot["min"] = min(slot["min"], band["min"])
                slot["max"] = max(slot["max"], band["max"])
                slot["_means"].append(band["mean"])
                slot["_p0_5"].append(band["p0_5"])
                slot["_p99_5"].append(band["p99_5"])
            slot["zeros"] += band["zeros"]
            slot["nonFinite"] += band["nonFinite"]
            valid = band.get("valid") or {}
            if valid.get("count"):
                slot["_validMin"].append(valid["min"])
                slot["_validMax"].append(valid["max"])
                slot["_validMeans"].append(valid["mean"])
                slot["_validStds"].append(valid["std"])
                slot["_validP0_5"].append(valid["p0_5"])
                slot["_validP99_5"].append(valid["p99_5"])

    value_ranges = []
    for band in sorted(band_ranges):
        slot = band_ranges[band]
        means = slot.pop("_means")
        low = slot.pop("_p0_5")
        high = slot.pop("_p99_5")
        valid_min = slot.pop("_validMin")
        valid_max = slot.pop("_validMax")
        valid_means = slot.pop("_validMeans")
        valid_stds = slot.pop("_validStds")
        valid_low = slot.pop("_validP0_5")
        valid_high = slot.pop("_validP99_5")
        slot["mean"] = round(float(np.mean(means)), 4) if means else None
        slot["p0_5"] = round(float(np.min(low)), 4) if low else None
        slot["p99_5"] = round(float(np.max(high)), 4) if high else None
        slot["valid"] = {
            "min": round(float(np.min(valid_min)), 4) if valid_min else None,
            "max": round(float(np.max(valid_max)), 4) if valid_max else None,
            "mean": round(float(np.mean(valid_means)), 4) if valid_means else None,
            "std": round(float(np.mean(valid_stds)), 4) if valid_stds else None,
            "p0_5": round(float(np.min(valid_low)), 4) if valid_low else None,
            "p99_5": round(float(np.max(valid_high)), 4) if valid_high else None,
        }
        value_ranges.append(slot)

    pairing_valid = not errors and bool(matched)

    return {
        "generatedUtc": datetime.now(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%S") + "Z",
        "pipelineVersion": C.PIPELINE_VERSION,
        "elapsedSeconds": round(kw["elapsed"], 2),
        "imageDir": _display_path(kw["image_dir"]),
        "maskDir": _display_path(kw["mask_dir"]),
        "counts": {
            "images": len(kw["images"]),
            "masks": len(kw["masks"]),
            "matchedPairs": len(matched),
            "scanned": len(records),
            "imagesWithoutMask": len(kw["images_without_mask"]),
            "masksWithoutImage": len(kw["masks_without_image"]),
            "distinctAcquisitions": len(groups),
        },
        "pairing": {
            "valid": pairing_valid,
            "rule": "image and mask share the same file stem and raster size",
            "imagesWithoutMask": kw["images_without_mask"][:50],
            "masksWithoutImage": kw["masks_without_image"][:50],
        },
        "raster": {
            "dimensions": [
                {"size": f"{w}x{h}", "files": n} for (w, h), n in dims.most_common()
            ],
            "bandCounts": [{"bands": b, "files": n} for b, n in band_counts.most_common()],
            "imageDtypes": [{"dtype": d, "files": n} for d, n in dtypes.most_common()],
            "maskDtypes": [{"dtype": d, "files": n} for d, n in mask_dtypes.most_common()],
            "compression": [
                {"codec": c, "files": n} for c, n in compressions.most_common()
            ],
            "bandNameSets": [
                {"bandNames": list(names), "files": n}
                for names, n in band_name_sets.most_common()
            ],
            "processingChains": [
                {"chain": list(chain), "files": n} for chain, n in chains.most_common(5)
            ],
        },
        "georeferencing": {
            "crs": [{"crs": c, "files": n} for c, n in crs_values.most_common()],
            "epsg": [{"epsg": e, "files": n} for e, n in epsg_values.most_common()],
            "masksWithGeoTags": masks_with_geo,
            "masksWithoutGeoTags": len(records) - masks_with_geo,
            "maskGeoreferencingRule": (
                "no mask carries usable georeferencing, so every geometry calculation "
                "reads the transform and CRS from the paired image after confirming "
                "both rasters have identical dimensions; a mask transform is only "
                "trusted when it also has an EPSG code, a sub-degree pixel size and an "
                "origin inside valid latitude"
            ),
            "datasetBounds": _dataset_bounds(records),
            "regions": [{"region": r, "files": n} for r, n in regions.most_common()],
        },
        "acquisitions": {
            "missions": [{"mission": m, "files": n} for m, n in missions.most_common()],
            "modes": [{"mode": m, "files": n} for m, n in modes.most_common()],
            "earliestUtc": min(times) if times else None,
            "latestUtc": max(times) if times else None,
            "distinctParentProducts": len(groups),
            "largestGroups": [
                {"groupKey": k, "files": len(v)}
                for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:5]
            ],
        },
        "maskValues": {
            "observed": sorted(kw["mask_value_counter"]),
            "perValueFileCount": {
                str(k): v for k, v in sorted(kw["mask_value_counter"].items())
            },
            "scanned": kw["scan_masks"],
            "oilFraction": {
                "min": round(min(oil_fractions), 8) if oil_fractions else None,
                "max": round(max(oil_fractions), 8) if oil_fractions else None,
                "mean": round(float(np.mean(oil_fractions)), 8) if oil_fractions else None,
                "median": round(float(np.median(oil_fractions)), 8)
                if oil_fractions
                else None,
                "emptyMasks": sum(1 for f in oil_fractions if f == 0.0),
            },
        },
        "pixelValues": {
            "sampledScenes": kw["sample_stems"],
            "note": (
                "Value ranges come from fully decoded scenes only. Decoding every "
                "scene would cost roughly two hours of pure-Python LZW, so the "
                "audit samples deterministically and reports the sample size."
            ),
            "perBand": value_ranges,
            "perScene": kw["decoded_stats"],
        },
        "problems": {
            "errorCount": len(errors),
            "warningCount": len(warnings),
            "errors": [p.to_dict() for p in errors[:100]],
            "warnings": [p.to_dict() for p in warnings[:100]],
            "warningKinds": [
                {"kind": k, "count": v}
                for k, v in Counter(p.kind for p in warnings).most_common()
            ],
        },
        "scenes": [r.to_dict() for r in records],
    }


def _dataset_bounds(records: Sequence[SceneRecord]) -> list[float] | None:
    boxes = [r.bounds for r in records if r.bounds]
    if not boxes:
        return None
    return [
        round(min(b[0] for b in boxes), 6),
        round(min(b[1] for b in boxes), 6),
        round(max(b[2] for b in boxes), 6),
        round(max(b[3] for b in boxes), 6),
    ]
