"""Tier 1 item 5: real currents and wind read from real products.

Nothing in this repository can *write* NetCDF-4, and the one CMEMS file supplied holds a
single timestep on a date no scene was acquired on. Every behaviour that only appears on
a multi-step product -- picking the timestep nearest the acquisition, interpolating wind
between hours, handling 0-360 longitudes, skipping an ``expver`` slice of no-data -- is
therefore unreachable through the real files. Both readers take an injectable file
handle for exactly this reason, and these tests drive them through one.

The fake handle mimics the three things the readers actually use from
:class:`spilltrace_common.netcdf4.NetCDF4File`: ``variables()`` returning nodes with
``dims`` and ``attributes``, ``read_variable(name)``, and ``global_attributes()``.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from spilltrace_common import cmems as cmems_mod
from spilltrace_common import config as C
from spilltrace_common import era5 as era5_mod
from spilltrace_drift import forcing as F


# ---------------------------------------------------------------------------
# A fake NetCDF-4 handle
# ---------------------------------------------------------------------------


class FakeNode:
    """Stands in for a parsed HDF5 dataset header."""

    def __init__(self, dims: tuple[int, ...], attributes: dict[str, object] | None = None):
        self.dims = dims
        self.attributes = attributes or {}


class FakeNetCDF:
    """A dict of arrays behind the handful of methods the readers call."""

    def __init__(self, arrays: dict[str, np.ndarray], units: dict[str, str] | None = None):
        self.arrays = {k: np.asarray(v) for k, v in arrays.items()}
        self.units = units or {}
        self.reads: list[str] = []
        self.closed = False

    def variables(self) -> dict[str, FakeNode]:
        return {
            name: FakeNode(
                tuple(int(s) for s in array.shape),
                {"units": self.units[name]} if name in self.units else {},
            )
            for name, array in self.arrays.items()
        }

    def read_variable(self, name: str) -> np.ndarray:
        self.reads.append(name)
        return self.arrays[name]

    def global_attributes(self) -> dict[str, object]:
        return {"title": "fake product"}

    def close(self) -> None:
        self.closed = True


EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
HOURS_UNITS = "hours since 1970-01-01 00:00:00"


def hours_since(when: datetime) -> float:
    return (when - EPOCH).total_seconds() / 3600.0


# ---------------------------------------------------------------------------
# CMEMS: the right timestep, not the first one
# ---------------------------------------------------------------------------


def make_multistep_cmems(step_count: int = 5) -> tuple[FakeNetCDF, list[datetime]]:
    """A daily product where timestep *i* carries a current of exactly (i+1)/10 m/s."""
    lats = np.linspace(27.0, 30.0, 7)
    lons = np.linspace(-92.0, -87.0, 11)
    times = [datetime(2017, 3, 9, tzinfo=timezone.utc) + timedelta(days=i) for i in range(step_count)]
    u = np.zeros((step_count, 1, lats.size, lons.size), dtype=np.float32)
    v = np.zeros_like(u)
    for i in range(step_count):
        u[i, 0] = (i + 1) / 10.0
        v[i, 0] = -(i + 1) / 100.0
    return (
        FakeNetCDF(
            {
                "latitude": lats,
                "longitude": lons,
                "time": np.asarray([hours_since(t) for t in times]),
                "depth": np.asarray([0.494]),
                "uo": u,
                "vo": v,
            },
            units={"time": HOURS_UNITS},
        ),
        times,
    )


def test_cmems_names_its_leading_axes_from_their_lengths() -> None:
    handle, times = make_multistep_cmems()
    surface = cmems_mod.CmemsSurface("fake.nc", file=handle)
    assert surface.leading_axis_roles((5, 1, 7, 11)) == ["time", "depth"]
    assert surface.times == times


def test_cmems_reads_the_timestep_nearest_the_acquisition() -> None:
    """The defect this replaced: every step was read as step 0 while the payload
    reported the gap to the nearest one."""
    handle, times = make_multistep_cmems()
    surface = cmems_mod.CmemsSurface("fake.nc", file=handle)
    bounds = [-90.0, 28.0, -89.0, 29.0]
    for index, when in enumerate(times):
        window = surface.window(bounds, when=when)
        assert window is not None
        assert window.time_index == index
        # Timestep i holds exactly (i+1)/10 m/s, so the field identifies its own step.
        assert window.u.mean() == pytest.approx((index + 1) / 10.0)
        assert window.time_utc == when.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_cmems_overlap_reports_the_index_it_would_integrate() -> None:
    handle, times = make_multistep_cmems()
    surface = cmems_mod.CmemsSurface("fake.nc", file=handle)
    when = times[3] + timedelta(hours=2)
    verdict = surface.overlap([-90.0, 28.0, -89.0, 29.0], when)
    assert verdict["usable"] is True
    assert verdict["nearestProductTimeIndex"] == 3
    assert verdict["timeGapHours"] == pytest.approx(2.0)
    assert verdict["productTimeStepCount"] == 5
    # The stats must come from the step named above, not from step 0.
    assert verdict["windowStats"]["speedMean"] == pytest.approx(
        math.hypot(0.4, 0.04), abs=1e-4
    )


def test_cmems_refuses_a_timestep_it_does_not_have() -> None:
    handle, _ = make_multistep_cmems(step_count=2)
    surface = cmems_mod.CmemsSurface("fake.nc", file=handle)
    with pytest.raises(cmems_mod.CmemsError, match="outside the product"):
        surface.surface_u(time_index=7)


def test_cmems_abridges_a_long_time_axis_in_its_payload() -> None:
    handle, _ = make_multistep_cmems(step_count=40)
    surface = cmems_mod.CmemsSurface("fake.nc", file=handle)
    stamps = surface.describe()["timesUtc"]
    assert len(stamps) == 9  # 8 kept plus the elision marker
    assert "32 more" in stamps[4]
    assert surface.describe()["timeStepCount"] == 40


# ---------------------------------------------------------------------------
# ERA5: the three things that are not like CMEMS
# ---------------------------------------------------------------------------

ACQUIRED = datetime(2017, 3, 11, 12, 0, tzinfo=timezone.utc)


def make_era5(
    hours: int = 6,
    start: datetime | None = None,
    lon_convention: str = "0-360",
    expver: bool = False,
    all_nan: bool = False,
) -> FakeNetCDF:
    """An hourly wind file over a Gulf of Mexico footprint.

    Hour *i* carries ``u = i`` m/s exactly, so an interpolated sample states which
    hours it came from and with what weight. Latitudes descend, as ERA5's do.
    """
    start = start or (ACQUIRED - timedelta(hours=hours // 2))
    lats = np.arange(30.0, 26.75, -0.25)
    lons = (
        np.arange(266.0, 272.25, 0.25)
        if lon_convention == "0-360"
        else np.arange(-94.0, -87.75, 0.25)
    )
    times = [start + timedelta(hours=i) for i in range(hours)]
    shape = (hours, lats.size, lons.size)
    u = np.zeros(shape, dtype=np.float64)
    for i in range(hours):
        u[i] = float(i)
    v = np.full(shape, 2.0, dtype=np.float64)
    if all_nan:
        u[:] = np.nan
        v[:] = np.nan
    if expver:
        # ERA5 first, ERA5T second: exactly one of them holds numbers for a given hour,
        # and here it is the second, so index 0 is a field of NaN.
        u = np.stack([np.full_like(u, np.nan), u], axis=1)
        v = np.stack([np.full_like(v, np.nan), v], axis=1)
    return FakeNetCDF(
        {
            "latitude": lats,
            "longitude": lons,
            "valid_time": np.asarray([hours_since(t) for t in times]),
            "u10": u,
            "v10": v,
        },
        units={"valid_time": HOURS_UNITS},
    )


SCENE = [-92.0, 28.0, -91.0, 29.0]


def test_era5_shifts_a_negative_query_onto_a_0_to_360_axis() -> None:
    reader = era5_mod.Era5Wind("fake_era5.nc", file=make_era5())
    assert float(reader.normalise_lon(-92.0)) == pytest.approx(268.0)
    assert float(reader.normalise_lon(268.0)) == pytest.approx(268.0)
    assert reader.index_window(SCENE) is not None
    verdict = reader.overlap(SCENE, ACQUIRED)
    assert verdict["spatialOverlap"] is True
    assert verdict["queryLonRange"] == [268.0, 269.0]


def test_era5_leaves_a_signed_axis_alone() -> None:
    reader = era5_mod.Era5Wind("fake_era5.nc", file=make_era5(lon_convention="signed"))
    assert float(reader.normalise_lon(-92.0)) == pytest.approx(-92.0)
    assert float(reader.normalise_lon(268.0)) == pytest.approx(-92.0)
    assert reader.overlap(SCENE, ACQUIRED)["usable"] is True


def test_era5_skips_the_no_data_expver_slice() -> None:
    """Index 0 of ``expver`` is all NaN here; taking it blindly hands the engine NaN."""
    reader = era5_mod.Era5Wind("fake_era5.nc", file=make_era5(expver=True))
    window = reader.window(SCENE, when=ACQUIRED)
    assert window is not None
    assert window.valid.all()
    assert reader._expver_choice == 1


def test_era5_brackets_an_instant_between_two_hours() -> None:
    reader = era5_mod.Era5Wind("fake_era5.nc", file=make_era5(hours=6))
    first = reader.times[0]
    before, after, weight = reader.bracketing_time_indices(first + timedelta(minutes=30))
    assert (before, after) == (0, 1)
    assert weight == pytest.approx(0.5)
    # Before the file starts and after it ends, both indices collapse: an hour of ERA5
    # says nothing about the hour after the file stops.
    assert reader.bracketing_time_indices(first - timedelta(days=1)) == (0, 0, 0.0)
    assert reader.bracketing_time_indices(reader.times[-1] + timedelta(days=1)) == (5, 5, 0.0)


def test_era5_reads_each_component_once_for_a_whole_horizon() -> None:
    handle = make_era5(hours=12)
    reader = era5_mod.Era5Wind("fake_era5.nc", file=handle)
    handle.reads.clear()
    series = reader.window_series(SCENE, ACQUIRED, horizon_hours=24.0)
    assert len(series) == 12  # the whole file falls inside a 24 h horizon
    assert handle.reads.count("u10") == 1
    assert handle.reads.count("v10") == 1
    assert [w.time_index for w in series] == sorted(w.time_index for w in series)


def test_era5_refuses_a_variable_larger_than_it_will_load(monkeypatch) -> None:
    """A global hourly month is 6 GB in float64. Refusing it with an actionable message
    beats being OOM-killed halfway through a case."""
    reader = era5_mod.Era5Wind("fake_era5.nc", file=make_era5())
    monkeypatch.setattr(era5_mod, "MAX_ELEMENTS", 10)
    with pytest.raises(era5_mod.Era5Error, match="request a smaller ERA5 area"):
        reader.window(SCENE, when=ACQUIRED)


def test_era5_partial_horizon_is_a_note_not_a_reason() -> None:
    """A short file still forces the acquisition hour correctly; it just clamps at the
    edges. Putting that in ``reasons`` would flip ``usable`` and throw the file away."""
    reader = era5_mod.Era5Wind("fake_era5.nc", file=make_era5(hours=3))
    verdict = reader.overlap(SCENE, ACQUIRED, horizon_hours=24.0)
    assert verdict["usable"] is True
    assert verdict["reasons"] == []
    assert verdict["horizonCoverageFraction"] < 1.0
    assert verdict["notes"] and "held constant" in verdict["notes"][0]


def test_era5_rejects_a_file_that_misses_the_acquisition_by_days() -> None:
    reader = era5_mod.Era5Wind(
        "fake_era5.nc", file=make_era5(start=ACQUIRED + timedelta(days=9))
    )
    verdict = reader.overlap(SCENE, ACQUIRED)
    assert verdict["usable"] is False
    assert verdict["spatialOverlap"] is True
    assert verdict["temporalOverlap"] is False
    assert any("beyond the" in reason for reason in verdict["reasons"])


def test_era5_window_stats_report_the_direction_wind_blows_from() -> None:
    """Met convention: a wind with u > 0 and v > 0 blows *from* the south-west."""
    reader = era5_mod.Era5Wind("fake_era5.nc", file=make_era5(hours=6))
    stats = reader.window(SCENE, when=reader.times[3]).stats()
    assert stats["speedMeanMs"] == pytest.approx(math.hypot(3.0, 2.0), abs=1e-3)
    assert stats["meanFromDirectionDeg"] == pytest.approx(
        (math.degrees(math.atan2(-3.0, -2.0)) + 360.0) % 360.0, abs=0.1
    )


# ---------------------------------------------------------------------------
# The wind field the engine samples
# ---------------------------------------------------------------------------


def build_gridded_wind(hours: int = 6, **kwargs) -> F.GriddedWind:
    reader = era5_mod.Era5Wind("fake_era5.nc", file=make_era5(hours=hours, **kwargs))
    overlap = reader.overlap(SCENE, ACQUIRED, horizon_hours=24.0)
    wind = F.GriddedWind.build(reader, SCENE, ACQUIRED, 24.0, overlap)
    assert wind is not None
    return wind


def sample(wind: F.WindField, seconds: float) -> tuple[float, float]:
    """One particle's wind, as plain floats. ``at`` is array-shaped for the integrator."""
    u, v = wind.at(np.array([-91.5]), np.array([28.5]), seconds)
    return float(np.ravel(u)[0]), float(np.ravel(v)[0])


def test_gridded_wind_interpolates_linearly_between_hours() -> None:
    """Hour *i* carries ``u = i``, so an interpolated sample names its own weight."""
    wind = build_gridded_wind(hours=6)  # hours ACQUIRED-3 .. ACQUIRED+2, u = 0 .. 5
    assert sample(wind, 0.0)[0] == pytest.approx(3.0)
    assert sample(wind, 1800.0)[0] == pytest.approx(3.5)
    assert sample(wind, 3600.0)[0] == pytest.approx(4.0)
    assert sample(wind, -3600.0)[0] == pytest.approx(2.0)
    # v is uniform in this file, so it must not drift with time.
    assert sample(wind, 1800.0)[1] == pytest.approx(2.0)


def test_gridded_wind_clamps_past_the_ends_of_the_file() -> None:
    wind = build_gridded_wind(hours=6)
    # A 24 h hindcast runs past a 6 h file. Clamping holds the last known hour; it does
    # not extrapolate a trend that the file does not contain.
    assert sample(wind, -86_400.0)[0] == pytest.approx(0.0)
    assert sample(wind, 86_400.0)[0] == pytest.approx(5.0)


def test_gridded_wind_answers_a_0_to_360_file_with_signed_queries() -> None:
    """The engine works in signed longitudes throughout; the file's convention is the
    reader's problem, not the integrator's."""
    signed = build_gridded_wind(hours=6, lon_convention="signed")
    global_axis = build_gridded_wind(hours=6, lon_convention="0-360")
    assert global_axis.lon_shift == "to-360"
    assert signed.lon_shift == "to-signed"  # a signed query passes through unchanged
    assert sample(global_axis, 0.0) == pytest.approx(sample(signed, 0.0))


def test_gridded_wind_drops_an_all_no_data_hour() -> None:
    reader = era5_mod.Era5Wind("fake_era5.nc", file=make_era5(all_nan=True))
    overlap = reader.overlap(SCENE, ACQUIRED, horizon_hours=24.0)
    assert overlap["usable"] is False  # no finite cell over the footprint
    assert F.GriddedWind.build(reader, SCENE, ACQUIRED, 24.0, overlap) is None


def test_no_wind_says_the_trajectory_is_a_lower_bound() -> None:
    described = F.NoWind().describe()
    assert described["available"] is False
    assert described["isSynthetic"] is False
    assert "lower bound" in described["note"]
    assert F.NoWind().at(np.array([-91.5]), np.array([28.5]), 0.0) == (0.0, 0.0)


# ---------------------------------------------------------------------------
# Resolution: which forcing a scene actually gets
# ---------------------------------------------------------------------------


def test_resolve_wind_reports_a_disabled_configuration_as_such() -> None:
    wind, decision = F.resolve_wind(SCENE, ACQUIRED, C.DriftConfig(use_wind=False))
    assert wind is None
    assert decision["mode"] == "disabled"
    assert decision["requested"] is False


def test_resolve_wind_accepts_a_covering_file() -> None:
    reader = era5_mod.Era5Wind("fake_era5.nc", file=make_era5(hours=12))
    wind, decision = F.resolve_wind(SCENE, ACQUIRED, C.DriftConfig(), reader=reader)
    assert isinstance(wind, F.GriddedWind)
    assert decision["mode"] == "era5"
    assert decision["frameCount"] == 12
    assert decision["overlap"]["usable"] is True
    # A caller-supplied reader is the caller's to close.
    assert reader._file.closed is False


def test_resolve_wind_refuses_a_file_from_the_wrong_week() -> None:
    reader = era5_mod.Era5Wind(
        "fake_era5.nc", file=make_era5(start=ACQUIRED + timedelta(days=9))
    )
    wind, decision = F.resolve_wind(SCENE, ACQUIRED, C.DriftConfig(), reader=reader)
    assert wind is None
    assert decision["mode"] == "none"
    assert decision["reasons"]


def test_resolve_wind_finds_nothing_on_disk_by_default() -> None:
    """No wind file ships with the repository: the Copernicus Data Store needs an
    account, so the default state is honest about having no wind rather than inventing
    one."""
    wind, decision = F.resolve_wind(SCENE, ACQUIRED, C.DriftConfig())
    assert wind is None
    assert decision["mode"] == "none"
    assert decision["era5Available"] is False
    assert "SPILLTRACE_ERA5" in decision["reasons"][0]


def test_real_wind_over_synthetic_currents_is_labelled_as_a_hybrid() -> None:
    reader = era5_mod.Era5Wind("fake_era5.nc", file=make_era5(hours=12))
    forcing, decision = F.resolve_forcing(
        SCENE, ACQUIRED, "scene-00000", wind_reader=reader
    )
    assert decision["mode"] == "synthetic"  # the currents are still synthetic
    assert decision["windMode"] == "era5"
    assert decision["label"] == C.LABEL_DRIFT_HYBRID
    described = decision["forcing"]
    assert described["isSynthetic"] is True
    assert described["windIsReal"] is True
    assert described["wind"]["source"] == "era5"
    assert described["wind"]["isSynthetic"] is False
    assert "ERA5 10 m wind" in described["windNote"]
    assert "the wind is measured but the current field is synthetic" in described["warning"]


def test_a_real_wind_actually_changes_the_velocity() -> None:
    """The label is not decoration: the windage term is 3% of a different vector."""
    reader = era5_mod.Era5Wind("fake_era5.nc", file=make_era5(hours=12))
    real, _ = F.resolve_forcing(SCENE, ACQUIRED, "scene-00000", wind_reader=reader)
    synthetic, _ = F.resolve_forcing(SCENE, ACQUIRED, "scene-00000")
    lon, lat = np.array([-91.5]), np.array([28.5])
    # Same seed, so the current halves are identical and only the wind differs.
    assert real.spec.seed == synthetic.spec.seed
    assert not np.allclose(real.velocity(lon, lat, 0.0), synthetic.velocity(lon, lat, 0.0))
    cfg = C.DriftConfig()
    wind_u, wind_v = real.wind_at(lon, lat, 0.0)
    current = synthetic._current_local(
        (lon - real.lon0) * real._metres_per_deg_lon,
        (lat - real.lat0) * real._metres_per_deg_lat,
        0.0,
    )
    u_real, v_real = real.velocity(lon, lat, 0.0)
    assert u_real == pytest.approx(current[0] + cfg.windage_factor * wind_u)
    assert v_real == pytest.approx(current[1] + cfg.windage_factor * wind_v)


def test_without_a_wind_file_the_synthetic_path_is_unchanged() -> None:
    """A regression guard on the default state of the repository: adding the ERA5 reader
    must not have moved a single number in the shipped demo."""
    forcing, decision = F.resolve_forcing(SCENE, ACQUIRED, "scene-00000")
    assert decision["label"] == C.LABEL_DRIFT_SYNTHETIC
    # "synthetic", not "none": the rotating wind still enters the velocity, and the fact
    # that no *real* wind was found is recorded separately on the resolution verdict.
    assert decision["windMode"] == "synthetic"
    assert decision["wind"]["mode"] == "none"
    described = decision["forcing"]
    assert described["windIsReal"] is False
    assert described["wind"]["source"] == "synthetic"
    spec = forcing.spec
    assert described["windDriftMs"] == pytest.approx(
        round(C.DriftConfig().windage_factor * spec.wind_speed_ms, 4)
    )
    assert "a synthetic" in described["windNote"]


def test_real_currents_with_real_wind_say_both() -> None:
    cmems_handle, times = make_multistep_cmems()
    surface = cmems_mod.CmemsSurface("fake.nc", file=cmems_handle)
    reader = era5_mod.Era5Wind(
        "fake_era5.nc", file=make_era5(hours=12, start=times[2] - timedelta(hours=6))
    )
    forcing, decision = F.resolve_forcing(
        SCENE,
        times[2],
        "scene-00000",
        surface=surface,
        close_surface=False,
        wind_reader=reader,
    )
    assert decision["mode"] == "cmems"  # the UI and the audit key on this string
    assert decision["windMode"] == "era5"
    assert decision["label"] == C.LABEL_DRIFT_REAL
    described = decision["forcing"]
    assert described["isSynthetic"] is False
    assert described["windIsReal"] is True
    assert described["productTimeIndex"] == 2
    assert "interpolated hourly" in described["method"]


def test_real_currents_without_wind_still_say_currents_only() -> None:
    cmems_handle, times = make_multistep_cmems()
    surface = cmems_mod.CmemsSurface("fake.nc", file=cmems_handle)
    forcing, decision = F.resolve_forcing(
        SCENE, times[2], "scene-00000", surface=surface, close_surface=False
    )
    assert decision["label"] == C.LABEL_DRIFT_CMEMS
    # The one case where "none" means what it says: no wind term entered the integration
    # at all, which is why the payload calls the spread a lower bound.
    assert decision["windMode"] == "none"
    described = decision["forcing"]
    assert described["windIsReal"] is False
    assert described["windDriftMs"] == 0.0
    assert "no wind product" in described["windNote"]
    assert "lower bound" in described["wind"]["note"]
