"""Phase 4: turn a binary oil mask into geospatial slick geometry.

The input is a pixel mask - either a model prediction or a supplied reference -
and the georeferencing of the scene it came from. The output is per-slick geometry
in real units plus a GeoJSON ``FeatureCollection`` the dashboard can draw.

Decisions that matter for correctness:

* **Area is computed on a sphere, per raster row.** The supplied scenes are
  EPSG:4326 at a constant 8.9831528e-05 degrees per pixel, so a pixel is about
  10 m tall everywhere but only ``10 * cos(latitude)`` metres wide. Multiplying a
  pixel count by a single constant would overstate the area of a North Sea slick
  by 43 % against a Gulf of Guinea one. Each row gets its own pixel area.
* **Raw and filtered masks are both reported.** Morphological cleanup changes the
  answer, so the pixel counts before and after it are recorded side by side
  rather than silently replaced.
* **Rings are validated before they are published.** A simplified contour can
  self-intersect, which produces a polygon that renderers and area formulas
  disagree about. Such a ring falls back to the unsimplified contour, and if that
  also self-intersects the polygon is excluded from the GeoJSON and flagged.
* **Confidence is a measured quantity, not a score.** It is the mean predicted
  probability inside the slick, stated as such, next to explicit quality flags.
  Nothing here invents a calibrated likelihood.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import cv2
import numpy as np

from spilltrace_common.geotiff import Affine

EARTH_RADIUS_M = 6_371_008.8
DEG = math.pi / 180.0


@dataclass
class GeometryConfig:
    """Post-processing and vectorisation settings."""

    open_radius: int = 2  # removes speckle-sized detections
    close_radius: int = 3  # bridges gaps inside one slick
    min_area_px: int = 500  # ~0.05 km^2 at 10 m/px
    simplify_tolerance_px: float = 1.5  # Douglas-Peucker, in pixels
    max_polygons: int = 40  # publication cap; anything dropped is reported
    elongation_flag: float = 4.0  # length:width above this is flagged as linear

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


# ---------------------------------------------------------------------------
# Spherical helpers
# ---------------------------------------------------------------------------


def row_pixel_area_m2(transform: Affine, rows: np.ndarray) -> np.ndarray:
    """Area in square metres of one pixel on each given raster row.

    Assumes a north-up, axis-aligned transform in degrees, which is what every
    supplied scene has; a rotated transform would need the full Jacobian.
    """
    rows = np.asarray(rows, dtype=np.float64)
    lat = transform.d + transform.f * (rows + 0.5)
    width_deg = abs(transform.b)
    height_deg = abs(transform.f)
    metres_per_deg = EARTH_RADIUS_M * DEG
    return (width_deg * metres_per_deg * np.cos(lat * DEG)) * (height_deg * metres_per_deg)


def mask_area_m2(mask: np.ndarray, transform: Affine) -> float:
    """Total area of a boolean mask, summed row by row."""
    counts = np.asarray(mask, dtype=bool).sum(axis=1)
    rows = np.arange(counts.size)
    return float((counts * row_pixel_area_m2(transform, rows)).sum())


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = lat1 * DEG, lat2 * DEG
    dlat = p2 - p1
    dlon = (lon2 - lon1) * DEG
    h = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return float(2.0 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h))))


def ring_length_m(ring: Sequence[Sequence[float]]) -> float:
    """Geodesic length of a closed ring of ``[lon, lat]`` vertices."""
    total = 0.0
    for index in range(len(ring) - 1):
        lon1, lat1 = ring[index]
        lon2, lat2 = ring[index + 1]
        total += haversine_m(lon1, lat1, lon2, lat2)
    return total


def local_metres(
    lon: np.ndarray, lat: np.ndarray, lon0: float, lat0: float
) -> tuple[np.ndarray, np.ndarray]:
    """Equirectangular projection about ``(lon0, lat0)``, in metres.

    Valid over a scene 20 km across; used only for shape statistics, never for
    area, which is integrated per row instead.
    """
    east = (np.asarray(lon) - lon0) * DEG * EARTH_RADIUS_M * math.cos(lat0 * DEG)
    north = (np.asarray(lat) - lat0) * DEG * EARTH_RADIUS_M
    return east, north


# ---------------------------------------------------------------------------
# Ring validity
# ---------------------------------------------------------------------------


def _segments_cross(
    a: Sequence[float], b: Sequence[float], c: Sequence[float], d: Sequence[float]
) -> bool:
    """True when segments ab and cd properly cross (shared endpoints do not count)."""

    def orient(p, q, r) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    d1, d2 = orient(c, d, a), orient(c, d, b)
    d3, d4 = orient(a, b, c), orient(a, b, d)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def ring_self_intersects(ring: Sequence[Sequence[float]]) -> bool:
    """O(n^2) self-intersection test. ``n`` is small after simplification."""
    points = list(ring)
    if len(points) > 3 and points[0] == points[-1]:
        points = points[:-1]
    count = len(points)
    if count < 4:
        return False
    for i in range(count):
        a, b = points[i], points[(i + 1) % count]
        for j in range(i + 1, count):
            if j == i or (j + 1) % count == i or j == (i + 1) % count:
                continue
            if _segments_cross(a, b, points[j], points[(j + 1) % count]):
                return True
    return False


def signed_area_deg2(ring: Sequence[Sequence[float]]) -> float:
    """Shoelace area in square degrees; sign gives the winding direction."""
    total = 0.0
    for index in range(len(ring) - 1):
        x1, y1 = ring[index]
        x2, y2 = ring[index + 1]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def orient_ring(ring: list[list[float]], counter_clockwise: bool) -> list[list[float]]:
    """Force a ring's winding, per RFC 7946: exterior CCW, holes CW."""
    if (signed_area_deg2(ring) >= 0.0) != counter_clockwise:
        return list(reversed(ring))
    return ring


# ---------------------------------------------------------------------------
# Mask post-processing
# ---------------------------------------------------------------------------


def _kernel(radius: int) -> np.ndarray:
    size = 2 * radius + 1
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))


def denoise(mask: np.ndarray, cfg: GeometryConfig) -> np.ndarray:
    """Opening then closing: drop speckle, then close pinholes inside a slick."""
    binary = np.ascontiguousarray(np.asarray(mask, dtype=bool).astype(np.uint8))
    if cfg.open_radius > 0:
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, _kernel(cfg.open_radius))
    if cfg.close_radius > 0:
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, _kernel(cfg.close_radius))
    return binary.astype(bool)


def _contour_to_ring(
    contour: np.ndarray, transform: Affine, tolerance: float
) -> tuple[list[list[float]], str]:
    """Vectorise one OpenCV contour into a closed lon/lat ring.

    Returns the ring and a note describing which geometry survived validation.
    """

    def to_ring(points: np.ndarray) -> list[list[float]]:
        cols = points[:, 0, 0].astype(np.float64) + 0.5
        rows = points[:, 0, 1].astype(np.float64) + 0.5
        lon, lat = transform.apply_array(cols, rows)
        ring = [[round(float(x), 8), round(float(y), 8)] for x, y in zip(lon, lat)]
        if ring and ring[0] != ring[-1]:
            ring.append(list(ring[0]))
        return ring

    # Simplification is usually what *creates* a crossing: pulling a vertex off a thin
    # isthmus can drag one side of the outline through the other. So back the tolerance
    # off before abandoning it, rather than jumping straight to the raw contour.
    if tolerance > 0:
        for divisor in (1.0, 2.0, 4.0):
            simplified = cv2.approxPolyDP(contour, tolerance / divisor, True)
            if len(simplified) < 3:
                continue
            ring = to_ring(simplified)
            if not ring_self_intersects(ring):
                return ring, "simplified" if divisor == 1.0 else f"simplified/{divisor:g}"
    ring = to_ring(contour)
    if ring_self_intersects(ring):
        return ring, "self-intersecting"
    return ring, "unsimplified"


def _shape_statistics(
    component: np.ndarray, transform: Affine
) -> dict[str, Any]:
    """Length, width, orientation and centroid from the component's pixels.

    Length and width are the extents along the principal axes of the pixel cloud,
    not a bounding box, so a curved slick is not credited with the width of its
    bounding rectangle.
    """
    rows, cols = np.nonzero(component)
    if rows.size == 0:
        return {}
    lon, lat = transform.apply_array(cols + 0.5, rows + 0.5)
    lon0 = float(lon.mean())
    lat0 = float(lat.mean())
    east, north = local_metres(lon, lat, lon0, lat0)
    points = np.stack([east, north])
    if rows.size >= 2:
        covariance = np.cov(points)
        values, vectors = np.linalg.eigh(covariance)
        order = np.argsort(values)[::-1]
        vectors = vectors[:, order]
    else:
        vectors = np.eye(2)
    projected = vectors.T @ points
    length = float(projected[0].max() - projected[0].min())
    width = float(projected[1].max() - projected[1].min())
    major = vectors[:, 0]
    # Bearing of the major axis, degrees clockwise from north, folded to 0-180
    # because a slick's axis has no direction.
    bearing = math.degrees(math.atan2(float(major[0]), float(major[1]))) % 180.0
    return {
        "centroid": [round(lon0, 6), round(lat0, 6)],
        "lengthM": round(length, 1),
        "widthM": round(max(width, 0.0), 1),
        "elongation": round(length / width, 3) if width > 1e-6 else None,
        "orientationDegFromNorth": round(bearing, 2),
        "boundsLonLat": [
            round(float(lon.min()), 6),
            round(float(lat.min()), 6),
            round(float(lon.max()), 6),
            round(float(lat.max()), 6),
        ],
    }


def _quality_flags(
    stats: dict[str, Any],
    area_km2: float,
    touches_edge: bool,
    ring_note: str,
    cfg: GeometryConfig,
) -> list[str]:
    flags: list[str] = []
    if touches_edge:
        flags.append(
            "touches the scene edge, so the slick may continue beyond the image"
        )
    if area_km2 < 0.05:
        flags.append("area is near the 0.05 km2 minimum kept by post-processing")
    elongation = stats.get("elongation")
    if elongation is not None and elongation >= cfg.elongation_flag:
        flags.append(
            f"length:width is {elongation:.1f}, a linear form consistent with a "
            "moving-source discharge"
        )
    if ring_note == "self-intersecting":
        flags.append(
            "the traced outline self-intersects, so it is published for display only; "
            "pixel-based area and shape statistics are unaffected because they are not "
            "derived from the outline"
        )
    return flags


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def analyse(
    mask: np.ndarray,
    transform: Affine,
    probability: np.ndarray | None = None,
    cfg: GeometryConfig | None = None,
    epsg: int | None = 4326,
    source: str = "unspecified",
) -> dict[str, Any]:
    """Measure every slick in ``mask`` and return geometry plus GeoJSON."""
    cfg = cfg or GeometryConfig()
    raw = np.asarray(mask, dtype=bool)
    if raw.ndim != 2:
        raise ValueError(f"expected a 2-D mask, got shape {raw.shape}")
    filtered = denoise(raw, cfg)

    count, labels, cc_stats, centroids = cv2.connectedComponentsWithStats(
        filtered.astype(np.uint8), connectivity=8
    )
    height, width = raw.shape

    kept: list[dict[str, Any]] = []
    dropped_small = 0
    for index in range(1, count):
        pixels = int(cc_stats[index, cv2.CC_STAT_AREA])
        if pixels < cfg.min_area_px:
            dropped_small += 1
            continue
        left = int(cc_stats[index, cv2.CC_STAT_LEFT])
        top = int(cc_stats[index, cv2.CC_STAT_TOP])
        box_w = int(cc_stats[index, cv2.CC_STAT_WIDTH])
        box_h = int(cc_stats[index, cv2.CC_STAT_HEIGHT])
        component = labels == index
        area_m2 = mask_area_m2(component, transform)
        stats = _shape_statistics(component, transform)
        touches_edge = left == 0 or top == 0 or left + box_w >= width or top + box_h >= height

        contours, hierarchy = cv2.findContours(
            component.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE
        )
        rings: list[list[list[float]]] = []
        note = "no contour"
        perimeter_m = 0.0
        if contours:
            outer_index = 0
            if hierarchy is not None:
                outer = [i for i, h in enumerate(hierarchy[0]) if h[3] < 0]
                outer_index = outer[0] if outer else 0
            exterior, note = _contour_to_ring(
                contours[outer_index], transform, cfg.simplify_tolerance_px
            )
            perimeter_m = ring_length_m(exterior)
            rings.append(orient_ring(exterior, counter_clockwise=True))
            if hierarchy is not None:
                for child, entry in enumerate(hierarchy[0]):
                    if entry[3] == outer_index and len(contours[child]) >= 3:
                        hole, hole_note = _contour_to_ring(
                            contours[child], transform, cfg.simplify_tolerance_px
                        )
                        if hole_note != "self-intersecting":
                            rings.append(orient_ring(hole, counter_clockwise=False))

        probability_stats: dict[str, Any] = {}
        if probability is not None:
            values = np.asarray(probability, dtype=np.float32)[component]
            if values.size:
                probability_stats = {
                    "meanProbability": round(float(values.mean()), 4),
                    "medianProbability": round(float(np.median(values)), 4),
                    "p10Probability": round(float(np.percentile(values, 10)), 4),
                    "fractionAbove0_8": round(float((values >= 0.8).mean()), 4),
                }

        area_km2 = area_m2 / 1e6
        record = {
            "id": f"slick-{len(kept) + 1:02d}",
            "pixels": pixels,
            "areaM2": round(area_m2, 1),
            "areaKm2": round(area_km2, 6),
            "perimeterM": round(perimeter_m, 1),
            "compactness": (
                round(4.0 * math.pi * area_m2 / (perimeter_m**2), 4)
                if perimeter_m > 0
                else None
            ),
            "touchesSceneEdge": bool(touches_edge),
            "ringGeometry": note,
            "geometryValid": note != "self-intersecting" and len(rings) > 0,
            **stats,
            **probability_stats,
            "confidence": probability_stats.get("meanProbability"),
            "confidenceBasis": (
                "mean predicted oil probability over the slick's pixels; this is the "
                "model's own output, not a calibrated probability that the feature is oil"
            )
            if probability_stats
            else "no probability map supplied, so no confidence is reported",
            "qualityFlags": _quality_flags(stats, area_km2, touches_edge, note, cfg),
            "_rings": rings,
        }
        kept.append(record)

    kept.sort(key=lambda r: -r["areaM2"])
    dropped_cap = max(0, len(kept) - cfg.max_polygons)
    published = kept[: cfg.max_polygons]
    for position, record in enumerate(published, start=1):
        record["id"] = f"slick-{position:02d}"

    features = []
    for record in published:
        rings = record.pop("_rings")
        if not rings:
            continue
        # A self-intersecting outline is published anyway, flagged. Area and shape
        # statistics come from the pixel mask, never from the ring, so withholding the
        # ring protects no number -- it only costs the map the shape of the slick, which
        # for the largest component is the single most useful thing on the screen.
        properties = {k: v for k, v in record.items() if not k.startswith("_")}
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": rings},
                "properties": properties,
            }
        )
    for record in kept[cfg.max_polygons :]:
        record.pop("_rings", None)

    total_raw_m2 = mask_area_m2(raw, transform)
    total_filtered_m2 = mask_area_m2(filtered, transform)
    total_kept_m2 = sum(r["areaM2"] for r in published)

    return {
        "source": source,
        "config": cfg.to_dict(),
        "raster": {
            "height": int(height),
            "width": int(width),
            "epsg": epsg,
            "transform": transform.as_list(),
            "pixelAreaM2AtTop": round(float(row_pixel_area_m2(transform, np.array([0]))[0]), 3),
            "pixelAreaM2AtBottom": round(
                float(row_pixel_area_m2(transform, np.array([height - 1]))[0]), 3
            ),
        },
        "areaMethod": (
            "pixel counts are integrated row by row on a sphere of radius "
            f"{EARTH_RADIUS_M:.1f} m, because a degree of longitude shortens with "
            "latitude and a single pixel-area constant would bias every scene"
        ),
        "rawMask": {
            "pixels": int(raw.sum()),
            "areaKm2": round(total_raw_m2 / 1e6, 6),
        },
        "filteredMask": {
            "pixels": int(filtered.sum()),
            "areaKm2": round(total_filtered_m2 / 1e6, 6),
            "note": (
                f"morphological opening (r={cfg.open_radius}) then closing "
                f"(r={cfg.close_radius}); the raw mask is kept above for comparison"
            ),
        },
        "summary": {
            "componentsFound": int(max(0, count - 1)),
            "componentsBelowMinArea": dropped_small,
            "componentsPublished": len(published),
            "componentsOverPolygonCap": dropped_cap,
            "componentsWithInvalidOutline": sum(
                1 for r in published if not r["geometryValid"]
            ),
            "totalAreaKm2": round(total_kept_m2 / 1e6, 6),
            "largestAreaKm2": published[0]["areaKm2"] if published else 0.0,
            "totalPerimeterKm": round(
                sum(r["perimeterM"] for r in published) / 1000.0, 4
            ),
        },
        "slicks": published,
        "geojson": {
            "type": "FeatureCollection",
            "features": features,
            "crs": {"type": "name", "properties": {"name": f"EPSG:{epsg}"}} if epsg else None,
            "note": (
                "outlines are traced from the mask and simplified; a feature whose "
                "properties carry geometryValid=false has a self-intersecting ring and is "
                "included for display only. Area and shape statistics come from the pixel "
                "mask, not from these rings."
            ),
        },
    }
