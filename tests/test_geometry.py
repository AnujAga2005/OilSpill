"""Geometry tests: real units, real winding, real validity.

Area is checked against an independently written closed form rather than against
the implementation's own row-by-row sum, and the latitude dependence is checked
explicitly, because a single pixel-area constant is the easiest way for this kind
of code to be quietly wrong by tens of per cent.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from spilltrace_common.geotiff import Affine
from spilltrace_ml.geometry import (
    EARTH_RADIUS_M,
    GeometryConfig,
    analyse,
    denoise,
    haversine_m,
    mask_area_m2,
    ring_length_m,
    ring_self_intersects,
    signed_area_deg2,
)

# The pixel size every supplied scene uses.
PIXEL_DEG = 8.9831528e-05
DEG = math.pi / 180.0


def transform_at(lat: float, lon: float = 0.0) -> Affine:
    """North-up transform whose origin is the top-left corner of the raster."""
    return Affine(lon, PIXEL_DEG, 0.0, lat, 0.0, -PIXEL_DEG)


def rectangle(shape: tuple[int, int], top: int, left: int, height: int, width: int):
    mask = np.zeros(shape, dtype=bool)
    mask[top : top + height, left : left + width] = True
    return mask


def test_pixel_size_is_about_ten_metres() -> None:
    """Sanity anchor: the dataset's pixel spacing is Sentinel-1 GRDH 10 m."""
    metres = PIXEL_DEG * DEG * EARTH_RADIUS_M
    assert 9.9 < metres < 10.1


def test_area_matches_an_independent_closed_form() -> None:
    mask = rectangle((256, 256), 100, 100, 40, 60)
    lat = 25.0
    transform = transform_at(lat)
    measured = mask_area_m2(mask, transform)

    # Independent estimate: mean latitude of the block, flat-earth rectangle.
    lat_mid = lat - PIXEL_DEG * (100 + 40 / 2)
    metres_per_deg = EARTH_RADIUS_M * DEG
    expected = (
        60 * PIXEL_DEG * metres_per_deg * math.cos(lat_mid * DEG)
    ) * (40 * PIXEL_DEG * metres_per_deg)
    assert abs(measured - expected) / expected < 1e-4


def test_area_shrinks_with_latitude() -> None:
    """A degree of longitude shortens as cos(latitude); the area must follow."""
    mask = rectangle((256, 256), 50, 50, 50, 50)
    equator = mask_area_m2(mask, transform_at(0.0))
    high = mask_area_m2(mask, transform_at(60.0))
    assert abs(high / equator - math.cos(60.0 * DEG)) < 1e-3
    # And the naive single-constant answer would be wrong by a factor of two,
    # which is exactly the error this guards against.
    assert high < 0.51 * equator


def test_elongation_and_orientation_of_a_bar() -> None:
    mask = rectangle((512, 512), 100, 200, 200, 20)  # tall and thin: runs north-south
    result = analyse(mask, transform_at(10.0), cfg=GeometryConfig(min_area_px=100))
    assert result["summary"]["componentsPublished"] == 1
    slick = result["slicks"][0]
    assert 1900 < slick["lengthM"] < 2100
    assert 150 < slick["widthM"] < 250
    assert slick["elongation"] > 7
    # A north-south bar has a major axis pointing north: 0 degrees, mod 180.
    bearing = slick["orientationDegFromNorth"]
    assert min(bearing, 180.0 - bearing) < 2.0
    assert "linear form" in " ".join(slick["qualityFlags"])


def test_orientation_of_an_east_west_bar() -> None:
    mask = rectangle((512, 512), 200, 100, 20, 200)
    result = analyse(mask, transform_at(10.0), cfg=GeometryConfig(min_area_px=100))
    bearing = result["slicks"][0]["orientationDegFromNorth"]
    assert abs(bearing - 90.0) < 2.0


def test_small_components_are_dropped_and_counted() -> None:
    mask = rectangle((256, 256), 40, 40, 60, 60)
    mask[200, 200] = True  # one-pixel speckle
    mask[210:212, 210:212] = True  # four-pixel speckle
    cfg = GeometryConfig(min_area_px=500, open_radius=0, close_radius=0)
    result = analyse(mask, transform_at(5.0), cfg=cfg)
    assert result["summary"]["componentsPublished"] == 1
    assert result["summary"]["componentsBelowMinArea"] == 2


def test_raw_and_filtered_masks_are_both_reported() -> None:
    mask = rectangle((256, 256), 40, 40, 60, 60)
    mask[200, 200] = True
    result = analyse(mask, transform_at(5.0), cfg=GeometryConfig())
    assert result["rawMask"]["pixels"] == 3601
    # The opening removes the isolated pixel, so the filtered count is lower.
    assert result["filteredMask"]["pixels"] < result["rawMask"]["pixels"]
    assert result["rawMask"]["areaKm2"] > result["filteredMask"]["areaKm2"]


def test_denoise_closes_a_pinhole() -> None:
    mask = rectangle((128, 128), 30, 30, 60, 60)
    mask[60, 60] = False
    cleaned = denoise(mask, GeometryConfig())
    assert cleaned[60, 60]


def test_hole_becomes_an_interior_ring() -> None:
    mask = rectangle((256, 256), 40, 40, 120, 120)
    mask[80:120, 80:120] = False  # a hole too large for the closing to fill
    cfg = GeometryConfig(open_radius=1, close_radius=1, min_area_px=100)
    result = analyse(mask, transform_at(5.0), cfg=cfg)
    coordinates = result["geojson"]["features"][0]["geometry"]["coordinates"]
    assert len(coordinates) == 2, "expected an exterior ring plus one hole"
    # RFC 7946 winding: exterior counter-clockwise, hole clockwise.
    assert signed_area_deg2(coordinates[0]) > 0
    assert signed_area_deg2(coordinates[1]) < 0


def test_self_intersection_detector() -> None:
    square = [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]
    bowtie = [[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]
    assert not ring_self_intersects(square)
    assert ring_self_intersects(bowtie)
    triangle = [[0, 0], [1, 0], [0, 1], [0, 0]]
    assert not ring_self_intersects(triangle)


def test_ring_length_is_geodesic() -> None:
    # One degree of latitude is about 111.2 km on this sphere.
    assert abs(haversine_m(0.0, 0.0, 0.0, 1.0) - EARTH_RADIUS_M * DEG) < 1.0
    ring = [[0.0, 0.0], [0.0, 1.0], [1.0, 1.0], [0.0, 0.0]]
    length = ring_length_m(ring)
    assert length > 2 * EARTH_RADIUS_M * DEG


def test_perimeter_and_compactness_of_a_square() -> None:
    mask = rectangle((512, 512), 100, 100, 200, 200)
    result = analyse(mask, transform_at(0.0), cfg=GeometryConfig(min_area_px=100))
    slick = result["slicks"][0]
    side_m = 200 * PIXEL_DEG * DEG * EARTH_RADIUS_M
    assert abs(slick["perimeterM"] - 4 * side_m) / (4 * side_m) < 0.05
    # 4*pi*A/P^2 is 1 for a circle and pi/4 for a square.
    assert 0.7 < slick["compactness"] < 0.85


def test_edge_contact_is_flagged() -> None:
    mask = rectangle((256, 256), 0, 40, 80, 80)  # runs off the top of the raster
    result = analyse(mask, transform_at(5.0), cfg=GeometryConfig(min_area_px=100))
    slick = result["slicks"][0]
    assert slick["touchesSceneEdge"] is True
    assert any("beyond the image" in flag for flag in slick["qualityFlags"])


def test_probability_becomes_a_stated_confidence() -> None:
    mask = rectangle((256, 256), 40, 40, 80, 80)
    probability = np.where(mask, 0.9, 0.05).astype(np.float32)
    result = analyse(
        mask, transform_at(5.0), probability=probability, cfg=GeometryConfig(min_area_px=100)
    )
    slick = result["slicks"][0]
    assert slick["confidence"] == pytest.approx(0.9, abs=1e-3)
    assert "not a calibrated probability" in slick["confidenceBasis"]


def test_no_probability_means_no_confidence() -> None:
    mask = rectangle((256, 256), 40, 40, 80, 80)
    result = analyse(mask, transform_at(5.0), cfg=GeometryConfig(min_area_px=100))
    slick = result["slicks"][0]
    assert slick["confidence"] is None
    assert "no probability map" in slick["confidenceBasis"]


def test_empty_mask_is_handled() -> None:
    result = analyse(np.zeros((64, 64), dtype=bool), transform_at(0.0))
    assert result["summary"]["componentsPublished"] == 0
    assert result["summary"]["totalAreaKm2"] == 0.0
    assert result["geojson"]["features"] == []


def test_output_is_json_serialisable() -> None:
    mask = rectangle((256, 256), 40, 40, 80, 80)
    result = analyse(mask, transform_at(5.0), cfg=GeometryConfig(min_area_px=100))
    blob = json.dumps(result)
    assert "slick-01" in blob
    for feature in result["geojson"]["features"]:
        for ring in feature["geometry"]["coordinates"]:
            assert ring[0] == ring[-1], "rings must be closed"
            assert len(ring) >= 4


def test_polygon_cap_is_reported_not_hidden() -> None:
    mask = np.zeros((512, 512), dtype=bool)
    for index in range(6):
        row = 20 + (index // 3) * 200
        col = 20 + (index % 3) * 150
        mask[row : row + 60, col : col + 60] = True
    cfg = GeometryConfig(min_area_px=100, max_polygons=4)
    result = analyse(mask, transform_at(5.0), cfg=cfg)
    assert result["summary"]["componentsFound"] == 6
    assert result["summary"]["componentsPublished"] == 4
    assert result["summary"]["componentsOverPolygonCap"] == 2
