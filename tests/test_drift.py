"""Drift tests: the physics, not just the plumbing.

The two properties worth guarding are that the synthetic current is genuinely
non-divergent (otherwise the flow itself would concentrate or thin the slick, and
every area number downstream would be an artefact of the integrator) and that the
field's amplitude really is the calibrated one rather than an accident of the
random phases. Both are checked numerically rather than assumed from the algebra.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from spilltrace_common import config as C
from spilltrace_common.geotiff import Affine
from spilltrace_drift.engine import (
    _ring_area_km2,
    convex_hull_indices,
    distances_m,
    hull_ring,
    seed_from_mask,
    seed_from_point,
    simulate,
    to_geojson,
)
from spilltrace_drift.forcing import (
    DEG,
    GridLandMask,
    LatLonGrid,
    NoLandMask,
    SyntheticForcing,
    resolve_forcing,
)

PIXEL_DEG = 8.9831528e-05
SCENE_BOUNDS = [-89.5, 28.4, -89.3, 28.6]  # a Gulf of Mexico footprint
SCENE_TIME = "2017-06-14T23:41:12Z"


def make_forcing(
    key: str = "scene-00000",
    speed: float = 0.0939,
    use_wind: bool = False,
    land: object | None = None,
    cfg: C.DriftConfig | None = None,
) -> SyntheticForcing:
    cfg = cfg or C.DriftConfig(use_wind=use_wind)
    return SyntheticForcing.build(
        lon0=-89.4,
        lat0=28.5,
        scene_key=key,
        target_speed_ms=speed,
        land=land or NoLandMask(),
        cfg=cfg,
    )


# ---------------------------------------------------------------------------
# The synthetic field
# ---------------------------------------------------------------------------


def test_synthetic_current_is_non_divergent() -> None:
    """The field is the curl of a streamfunction, so div(u) must vanish."""
    forcing = make_forcing()
    offsets = np.linspace(-15_000.0, 15_000.0, 21)
    east, north = np.meshgrid(offsets, offsets)
    h = 5.0  # metres
    seconds = 3600.0
    u_px, _ = forcing._current_local(east + h, north, seconds)
    u_mx, _ = forcing._current_local(east - h, north, seconds)
    _, v_py = forcing._current_local(east, north + h, seconds)
    _, v_my = forcing._current_local(east, north - h, seconds)
    du_dx = (u_px - u_mx) / (2 * h)
    dv_dy = (v_py - v_my) / (2 * h)
    divergence = np.abs(du_dx + dv_dy)
    scale = np.abs(du_dx).mean() + np.abs(dv_dy).mean()
    assert scale > 0, "the field must actually have gradients for this test to mean anything"
    assert divergence.max() / scale < 1e-4


def test_amplitude_is_calibrated_to_the_anchor() -> None:
    """RMS speed must equal the measured CMEMS speed, not a random value."""
    target = 0.0939
    forcing = make_forcing(speed=target, use_wind=False)
    offsets = np.linspace(-20_000.0, 20_000.0, 41)
    east, north = np.meshgrid(offsets, offsets)
    squares = []
    for hours in (0.0, 3.0, 7.0, 13.0, 19.0):
        u, v = forcing._current_local(east, north, hours * 3600.0)
        squares.append(np.mean(u * u + v * v))
    rms = math.sqrt(float(np.mean(squares)))
    assert abs(rms - target) / target < 0.05


def test_field_varies_in_space_and_time() -> None:
    forcing = make_forcing()
    a = forcing.velocity(np.array([-89.40]), np.array([28.50]), 0.0)
    b = forcing.velocity(np.array([-89.30]), np.array([28.50]), 0.0)
    c = forcing.velocity(np.array([-89.40]), np.array([28.50]), 6 * 3600.0)
    assert not np.allclose(a, b), "the field must vary in space"
    assert not np.allclose(a, c), "the field must vary in time"


def test_different_scenes_get_different_fields_but_each_is_reproducible() -> None:
    one = make_forcing("scene-00000")
    two = make_forcing("scene-00457")
    again = make_forcing("scene-00000")
    lon = np.array([-89.4])
    lat = np.array([28.5])
    assert not np.allclose(one.velocity(lon, lat, 0.0), two.velocity(lon, lat, 0.0))
    assert np.allclose(one.velocity(lon, lat, 0.0), again.velocity(lon, lat, 0.0))
    assert one.spec.seed == again.spec.seed != two.spec.seed


def test_windage_adds_exactly_the_stated_fraction() -> None:
    without = make_forcing("scene-00000", use_wind=False)
    with_wind = make_forcing("scene-00000", use_wind=True)
    lon = np.array([-89.4])
    lat = np.array([28.5])
    u0, v0 = without.velocity(lon, lat, 0.0)
    u1, v1 = with_wind.velocity(lon, lat, 0.0)
    wind_u, wind_v = with_wind.wind(0.0)
    factor = with_wind.cfg.windage_factor
    assert u1 - u0 == pytest.approx(factor * wind_u, abs=1e-9)
    assert v1 - v0 == pytest.approx(factor * wind_v, abs=1e-9)
    assert without.wind(0.0) == (0.0, 0.0)


def test_forcing_description_states_it_is_synthetic() -> None:
    described = make_forcing().describe()
    assert described["isSynthetic"] is True
    assert described["label"] == C.LABEL_DRIFT_SYNTHETIC
    assert "not the ocean on the acquisition" in described["warning"]
    assert described["spec"]["seed"] > 0


# ---------------------------------------------------------------------------
# Grid sampling
# ---------------------------------------------------------------------------


def test_bilinear_ignores_land_neighbours() -> None:
    lats = np.array([0.0, 1.0])
    lons = np.array([0.0, 1.0])
    values = np.array([[1.0, np.nan], [1.0, 1.0]])
    grid = LatLonGrid(lats, lons, u=values)
    sample = grid.bilinear("u", np.array([0.75]), np.array([0.25]))
    assert np.isfinite(sample).all()
    assert sample[0] == pytest.approx(1.0, abs=1e-9)


def test_bilinear_interpolates_linearly() -> None:
    grid = LatLonGrid(
        np.array([0.0, 1.0]), np.array([0.0, 1.0]), u=np.array([[0.0, 1.0], [0.0, 1.0]])
    )
    assert grid.bilinear("u", np.array([0.25]), np.array([0.5]))[0] == pytest.approx(0.25)


def test_descending_axes_are_normalised() -> None:
    ascending = LatLonGrid(
        np.array([0.0, 1.0]), np.array([0.0, 1.0]), u=np.array([[10.0, 20.0], [30.0, 40.0]])
    )
    descending = LatLonGrid(
        np.array([1.0, 0.0]), np.array([0.0, 1.0]), u=np.array([[30.0, 40.0], [10.0, 20.0]])
    )
    lon = np.array([0.3, 0.8])
    lat = np.array([0.2, 0.9])
    assert np.allclose(ascending.bilinear("u", lon, lat), descending.bilinear("u", lon, lat))


def test_land_mask_reports_its_own_coarseness() -> None:
    water = np.ones((8, 8), dtype=np.uint8)
    water[:2, :] = 0
    grid = LatLonGrid(np.linspace(28.0, 29.0, 8), np.linspace(-90.0, -89.0, 8), water=water)
    mask = GridLandMask(grid, 0.0833)
    described = mask.describe()
    assert described["available"] is True
    assert 8.0 < described["resolutionKm"] < 10.0
    assert "10 m per pixel" in described["caveat"]
    assert mask.water_fraction == pytest.approx(0.75)
    assert bool(mask.is_water(np.array([-89.5]), np.array([28.9]))[0]) is True
    assert bool(mask.is_water(np.array([-89.5]), np.array([28.02]))[0]) is False


def test_no_land_mask_says_beaching_is_not_detected() -> None:
    described = NoLandMask().describe()
    assert described["available"] is False
    assert "beaching is not detected" in described["note"]


# ---------------------------------------------------------------------------
# Hulls and areas
# ---------------------------------------------------------------------------


def test_convex_hull_of_a_square_with_an_interior_point() -> None:
    points = np.array([[0, 0], [1, 0], [1, 1], [0, 1], [0.5, 0.5]], dtype=float)
    hull = convex_hull_indices(points)
    assert sorted(hull) == [0, 1, 2, 3], "the interior point must be excluded"


def test_convex_hull_drops_collinear_points() -> None:
    points = np.array([[0, 0], [1, 0], [2, 0], [1, 1]], dtype=float)
    hull = convex_hull_indices(points)
    assert 1 not in hull
    assert len(hull) == 3


def test_hull_ring_is_closed_and_counter_clockwise() -> None:
    rng = np.random.default_rng(7)
    lon = -89.4 + rng.normal(0, 0.01, 60)
    lat = 28.5 + rng.normal(0, 0.01, 60)
    ring, note = hull_ring(lon, lat, 28.5)
    assert note == "convex hull"
    assert ring[0] == ring[-1]
    signed = sum(
        ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
        for i in range(len(ring) - 1)
    )
    assert signed > 0


def test_collocated_particles_get_a_nominal_circle() -> None:
    lon = np.full(20, -89.4)
    lat = np.full(20, 28.5)
    ring, note = hull_ring(lon, lat, 28.5)
    assert "nominal circle" in note
    assert len(ring) >= 4 and ring[0] == ring[-1]


def test_ring_area_of_a_known_square() -> None:
    # A 0.01 degree square at the equator: 1.1119 km on a side.
    ring = [[0.0, 0.0], [0.01, 0.0], [0.01, 0.01], [0.0, 0.01], [0.0, 0.0]]
    side_km = 0.01 * DEG * C.EARTH_RADIUS_M / 1000.0
    assert _ring_area_km2(ring, 0.0) == pytest.approx(side_km**2, rel=1e-6)


def test_distances_are_great_circle() -> None:
    measured = distances_m(np.array([0.0]), np.array([1.0]), 0.0, 0.0)[0]
    assert abs(measured - C.EARTH_RADIUS_M * DEG) < 1.0


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def test_seeding_from_a_mask_stays_inside_the_footprint() -> None:
    mask = np.zeros((64, 64), dtype=bool)
    mask[20:40, 10:30] = True
    transform = Affine(-89.4, PIXEL_DEG, 0.0, 28.5, 0.0, -PIXEL_DEG)
    lon, lat, info = seed_from_mask(mask, transform, 200, seed=11)
    assert lon.size == 200
    assert info["maskPixels"] == 400
    assert info["withReplacement"] is False  # 400 pixels is more than 200 particles
    lon_lo, lat_hi = transform.apply(10, 20)
    lon_hi, lat_lo = transform.apply(30, 40)
    assert lon.min() >= lon_lo - 1e-9 and lon.max() <= lon_hi + 1e-9
    assert lat.min() >= lat_lo - 1e-9 and lat.max() <= lat_hi + 1e-9
    assert len(np.unique(lon)) > 150, "jitter must break up duplicate pixel picks"


def test_seeding_a_slick_smaller_than_the_particle_count() -> None:
    """A 30-pixel slick must still release 300 particles, spread over its pixels."""
    mask = np.zeros((64, 64), dtype=bool)
    mask[10:15, 20:26] = True
    transform = Affine(-89.4, PIXEL_DEG, 0.0, 28.5, 0.0, -PIXEL_DEG)
    lon, lat, info = seed_from_mask(mask, transform, 300, seed=11)
    assert info["maskPixels"] == 30
    assert info["withReplacement"] is True
    assert lon.size == 300
    # Jitter means repeated pixel picks still land on distinct positions.
    assert len(np.unique(lon)) > 250


def test_seeding_an_empty_mask_is_an_error() -> None:
    with pytest.raises(ValueError, match="mask is empty"):
        seed_from_mask(np.zeros((8, 8), dtype=bool), Affine(0, 1, 0, 0, 0, -1), 10, 1)


def test_point_seeding_fills_the_disc_uniformly() -> None:
    lon, lat, info = seed_from_point(-89.4, 28.5, 1000.0, 4000, seed=3)
    radius = distances_m(lon, lat, -89.4, 28.5)
    assert radius.max() <= 1001.0
    # Uniform area density means half the particles fall inside r/sqrt(2).
    inner = (radius < 1000.0 / math.sqrt(2)).mean()
    assert 0.45 < inner < 0.55
    assert info["radiusM"] == 1000.0


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


def run_case(direction: str = "backward", **kwargs) -> dict:
    cfg = C.DriftConfig(particle_count=120, use_wind=False, **kwargs)
    forcing = make_forcing(cfg=cfg)
    lon, lat, info = seed_from_point(-89.4, 28.5, 400.0, cfg.particle_count, seed=5)
    return simulate(forcing, lon, lat, SCENE_TIME, direction, cfg, seeding=info)


def test_backward_run_walks_time_backwards() -> None:
    result = run_case("backward")
    hours = [entry["hoursFromObservation"] for entry in result["timeline"]]
    assert hours[0] == 0.0
    assert hours[-1] == pytest.approx(-24.0)
    assert result["timeline"][-1]["timeUtc"] == "2017-06-13T23:41:12Z"
    assert result["observedUtc"] == "2017-06-14T23:41:12Z"


def test_forward_run_walks_time_forwards() -> None:
    result = run_case("forward")
    assert result["timeline"][-1]["hoursFromObservation"] == pytest.approx(24.0)
    assert result["timeline"][-1]["timeUtc"] == "2017-06-15T23:41:12Z"


def test_uncertainty_grows_with_distance_from_the_observation() -> None:
    result = run_case("backward")
    spread = [entry["spreadP90Km"] for entry in result["timeline"]]
    assert spread[0] < spread[len(spread) // 2] < spread[-1]
    # Diffusive growth: sqrt(2*K*t) with K = 8 m2/s over 24 h is about 1.2 km.
    expected = math.sqrt(2 * 8.0 * 24 * 3600.0) / 1000.0
    assert 0.3 * expected < spread[-1] < 4.0 * expected


def test_displacement_matches_the_forcing_speed() -> None:
    """A 0.094 m/s current over 24 h moves oil about 8 km; check the order."""
    result = run_case("backward")
    travelled = result["endpoints"]["displacementMeanKm"]
    ceiling = 0.0939 * 24 * 3600.0 / 1000.0  # a straight line at full speed
    assert 0.05 * ceiling < travelled < 1.05 * ceiling


def test_envelope_area_widens_along_the_horizon() -> None:
    result = run_case("backward")
    areas = [envelope["areaKm2"] for envelope in result["envelopes"]]
    assert areas[0] > 0
    assert areas[-1] > areas[0]
    assert result["searchCorridor"]["areaKm2"] >= areas[-1]


def test_backward_result_states_it_is_not_a_time_reversal() -> None:
    result = run_case("backward")
    assert "random walk" in result["caveat"]
    assert result["originEstimate"]["hoursBeforeObservation"] == 24
    assert result["originEstimate"]["radiusP90Km"] > 0
    assert result["label"] == C.LABEL_DRIFT_SYNTHETIC
    assert result["status"] == C.LABEL_STATUS


def test_backward_then_forward_does_not_return_to_the_start() -> None:
    """The honest claim: a stochastic hindcast is not invertible.

    Re-releasing from the backward endpoints and running forward should land near
    the original slick but not on it, and the test asserts the gap is real rather
    than letting the UI imply a reversible trajectory.
    """
    cfg = C.DriftConfig(particle_count=150, use_wind=False)
    forcing = make_forcing(cfg=cfg)
    lon, lat, _ = seed_from_point(-89.4, 28.5, 300.0, cfg.particle_count, seed=5)
    back = simulate(forcing, lon, lat, SCENE_TIME, "backward", cfg)
    origins = np.asarray(back["endpoints"]["positions"], dtype=float)
    forward = simulate(
        forcing,
        origins[:, 0],
        origins[:, 1],
        "2017-06-13T23:41:12Z",
        "forward",
        cfg,
    )
    end = forward["endpoints"]["centroid"]
    gap_km = distances_m(np.array([end[0]]), np.array([end[1]]), -89.4, 28.5)[0] / 1000.0
    assert gap_km > 0.05, "a stochastic round trip must not close exactly"
    assert gap_km < 12.0, "but it should still land in the same neighbourhood"


def test_runs_are_reproducible() -> None:
    first = run_case("backward")
    second = run_case("backward")
    assert first["endpoints"]["positions"] == second["endpoints"]["positions"]


def test_particles_beach_and_are_counted() -> None:
    """A coast across the domain must stop particles rather than move them onto land."""
    water = np.ones((32, 32), dtype=np.uint8)
    lats = np.linspace(28.3, 28.7, 32)
    lons = np.linspace(-89.6, -89.2, 32)
    water[lats < 28.5, :] = 0  # everything south of the release is land
    grid = LatLonGrid(lats, lons, water=water)
    land = GridLandMask(grid, 0.0125)
    cfg = C.DriftConfig(particle_count=120, use_wind=False, diffusion_m2_s=400.0)
    forcing = make_forcing(land=land, cfg=cfg)
    lon, lat, _ = seed_from_point(-89.4, 28.505, 300.0, cfg.particle_count, seed=5)
    result = simulate(forcing, lon, lat, SCENE_TIME, "forward", cfg)
    outcomes = result["particleOutcomes"]
    assert outcomes["released"] == 120
    assert outcomes["beached"] > 0
    assert outcomes["beached"] + outcomes["stillDrifting"] + outcomes["leftForcingDomain"] == 120
    ashore = [
        position
        for position, beached in zip(
            result["endpoints"]["positions"], [t["beached"] for t in result["tracks"]]
        )
    ]
    assert ashore  # sanity: tracks are exported
    # No particle may finish south of the coastline.
    assert min(p[1] for p in result["endpoints"]["positions"]) >= 28.49


def test_particles_that_leave_a_gridded_domain_are_flagged() -> None:
    class Bounded(SyntheticForcing):
        def domain_bounds(self):
            return [-89.41, 28.49, -89.39, 28.51]

    cfg = C.DriftConfig(particle_count=60, use_wind=False, diffusion_m2_s=2000.0)
    forcing = Bounded.build(-89.4, 28.5, "scene-00000", 0.3, NoLandMask(), cfg)
    lon, lat, _ = seed_from_point(-89.4, 28.5, 100.0, cfg.particle_count, seed=5)
    result = simulate(forcing, lon, lat, SCENE_TIME, "forward", cfg)
    assert result["particleOutcomes"]["leftForcingDomain"] > 0


def test_tracks_are_subsampled_but_statistics_are_not() -> None:
    result = run_case("backward", horizon_hours=6)
    assert len(result["tracks"]) <= 40
    assert result["endpoints"]["count"] == 120
    assert "all particles contribute" in result["trackNote"]
    steps = int(6 * 60 / result["config"]["time_step_minutes"])
    assert len(result["tracks"][0]["path"]) == steps + 1


def test_result_is_json_serialisable_and_geojson_is_valid() -> None:
    result = run_case("backward")
    json.dumps(result)
    collection = to_geojson(result)
    kinds = {feature["properties"]["kind"] for feature in collection["features"]}
    assert {"searchCorridor", "envelope", "track", "originEstimate"} <= kinds
    for feature in collection["features"]:
        geometry = feature["geometry"]
        if geometry["type"] == "Polygon":
            for ring in geometry["coordinates"]:
                assert ring[0] == ring[-1] and len(ring) >= 4
        elif geometry["type"] == "LineString":
            assert len(geometry["coordinates"]) >= 2
    json.dumps(collection)


def test_bad_direction_is_rejected() -> None:
    forcing = make_forcing()
    with pytest.raises(ValueError, match="backward"):
        simulate(forcing, np.array([-89.4]), np.array([28.5]), SCENE_TIME, "sideways")


def test_empty_seeding_is_rejected() -> None:
    forcing = make_forcing()
    with pytest.raises(ValueError, match="non-empty"):
        simulate(forcing, np.array([]), np.array([]), SCENE_TIME)


# ---------------------------------------------------------------------------
# The forcing decision, against the real supplied product
# ---------------------------------------------------------------------------


def test_forcing_decision_is_recorded_even_without_a_product(monkeypatch) -> None:
    monkeypatch.setattr("spilltrace_common.cmems.open_default", lambda: None)
    forcing, decision = resolve_forcing(SCENE_BOUNDS, SCENE_TIME, "scene-00000")
    assert decision["mode"] == "synthetic"
    assert decision["label"] == C.LABEL_DRIFT_SYNTHETIC
    assert decision["cmemsAvailable"] is False
    assert "no CMEMS product was found on disk" in decision["reasons"]
    assert decision["forcing"]["landMask"]["available"] is False
    assert forcing.spec.target_speed_ms == pytest.approx(0.0939)
    assert "DATA_AUDIT.md" in decision["forcing"]["amplitudeAnchor"]["note"]


@pytest.mark.slow
def test_real_product_gives_a_land_mask_but_not_currents() -> None:
    """The supplied CMEMS file covers these scenes in space but not in time."""
    if C.cmems_path() is None:
        pytest.skip("no CMEMS product in the repository")
    forcing, decision = resolve_forcing(SCENE_BOUNDS, SCENE_TIME, "scene-00000")
    assert decision["cmemsAvailable"] is True
    assert decision["overlap"]["spatialOverlap"] is True
    assert decision["overlap"]["temporalOverlap"] is False
    assert decision["mode"] == "synthetic", "no temporal overlap means no CMEMS currents"
    assert decision["reasons"], "the reason for rejecting the product must be recorded"
    # The land mask and the amplitude anchor still come from the real file.
    assert decision["forcing"]["landMask"]["available"] is True
    anchor = decision["forcing"]["amplitudeAnchor"]
    assert anchor["source"].startswith("CMEMS product")
    assert 0.0 < anchor["speedMs"] < 3.0
    assert forcing.spec.target_speed_ms == pytest.approx(anchor["speedMs"])
