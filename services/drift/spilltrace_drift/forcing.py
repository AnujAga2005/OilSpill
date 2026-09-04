"""Phase 5a: the velocity field that moves the oil, and where it comes from.

The PRD rule is that CMEMS currents are used *only* when the product actually
covers the scene in space **and** time. The audit settled that question against
the supplied files: all 270 parent acquisitions fall inside the product's grid,
but the product holds a single timestep (2026-06-23) while the imagery runs
2015-03-12 to 2019-10-30, so the smallest time gap is about 2880 days. Nothing in
that file describes the ocean on the day any of these scenes were taken.

So the forcing here is synthetic and says so, in every payload, via
``LABEL_DRIFT_SYNTHETIC``. ``resolve_forcing`` still performs the overlap check at
runtime rather than trusting that conclusion: point it at a product that does
cover the acquisition and it returns a :class:`CmemsForcing` instead.

Two things the CMEMS file legitimately contributes even with no temporal overlap:

* **The land mask.** Cells where both velocity components are NaN are land. Coastlines
  do not move between 2019 and 2026, so this mask is valid for these scenes even
  though the currents are not.
* **A magnitude anchor.** The measured speeds over these scene footprints (0.046 to
  0.142 m/s, mean 0.094) set the scale of the synthetic field, so the simulation
  moves oil at a rate this ocean region plausibly supports instead of at an
  invented one.

The synthetic field is built from a streamfunction, which makes it exactly
non-divergent: oil is advected and stirred but never artificially concentrated or
thinned by the flow itself. ``tests/test_drift.py`` checks that numerically.

**Wind is resolved separately from currents**, by :func:`resolve_wind`, because the two
come from different products and either can be real on its own. Oil moves at about 3%
of the wind, and at the current speeds measured here that term is the same size as the
current -- so which wind a run used is a substantive claim, not a footnote. There are
three possibilities and each names itself:

* :class:`GriddedWind` -- a real ERA5 file covering the acquisition, interpolated
  bilinearly in space and linearly in time between hours.
* :class:`SyntheticWind` -- the rotating uniform vector the synthetic scenario has
  always used, plausible in magnitude and invented in direction.
* :class:`NoWind` -- currents only, which is what the CMEMS path did before this module
  could read a wind file at all.

A real wind over a synthetic current is the commonest real-data case, and it gets its
own label (``LABEL_DRIFT_HYBRID``) rather than being reported as either fully real or
fully synthetic. ``decision["mode"]`` still describes the *currents* only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

import numpy as np

from spilltrace_common import cmems as cmems_mod
from spilltrace_common import config as C
from spilltrace_common import era5 as era5_mod

DEG = math.pi / 180.0

# Measured over scene 00000's footprint in the supplied CMEMS product (see
# DATA_AUDIT.md). Used as the amplitude anchor when the product cannot be read.
FALLBACK_SPEED_MS = 0.0939

# Tidal and near-inertial periods the synthetic field is built from, in hours.
M2_PERIOD_H = 12.4206  # principal lunar semidiurnal
K1_PERIOD_H = 23.9345  # lunar-solar diurnal
M4_PERIOD_H = 6.2103  # first M2 overtide


# ---------------------------------------------------------------------------
# Grid sampling
# ---------------------------------------------------------------------------


class LatLonGrid:
    """Bilinear / nearest sampling on a regular lat-lon grid, NaN-aware.

    CMEMS latitudes ascend and longitudes ascend in the supplied product, but
    other products descend, so the axes are normalised on construction rather
    than assumed.
    """

    def __init__(self, lats: np.ndarray, lons: np.ndarray, **arrays: np.ndarray):
        lats = np.asarray(lats, dtype=np.float64)
        lons = np.asarray(lons, dtype=np.float64)
        self._flip_lat = lats.size > 1 and lats[0] > lats[-1]
        self._flip_lon = lons.size > 1 and lons[0] > lons[-1]
        self.lats = lats[::-1] if self._flip_lat else lats
        self.lons = lons[::-1] if self._flip_lon else lons
        self.arrays: dict[str, np.ndarray] = {}
        for name, values in arrays.items():
            grid = np.asarray(values)
            if self._flip_lat:
                grid = grid[::-1, :]
            if self._flip_lon:
                grid = grid[:, ::-1]
            self.arrays[name] = grid

    @property
    def bounds(self) -> list[float]:
        return [
            float(self.lons.min()),
            float(self.lats.min()),
            float(self.lons.max()),
            float(self.lats.max()),
        ]

    def _fractional_index(self, axis: np.ndarray, values: np.ndarray) -> np.ndarray:
        if axis.size < 2:
            return np.zeros_like(np.asarray(values, dtype=np.float64))
        step = (axis[-1] - axis[0]) / (axis.size - 1)
        return (np.asarray(values, dtype=np.float64) - axis[0]) / step

    def nearest(self, name: str, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        grid = self.arrays[name]
        row = np.clip(
            np.rint(self._fractional_index(self.lats, lat)).astype(np.int64),
            0,
            grid.shape[0] - 1,
        )
        col = np.clip(
            np.rint(self._fractional_index(self.lons, lon)).astype(np.int64),
            0,
            grid.shape[1] - 1,
        )
        return grid[row, col]

    def bilinear(self, name: str, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        """Bilinear sample, ignoring non-finite neighbours.

        A cell adjacent to land has NaN neighbours. Dropping those from the
        weighting keeps the flow finite right up to the coast; a plain bilinear
        pass would poison a whole cell of coastal water with NaN.
        """
        grid = np.asarray(self.arrays[name], dtype=np.float64)
        if grid.shape[0] < 2 or grid.shape[1] < 2:
            return np.nan_to_num(np.broadcast_to(grid.ravel()[:1], np.shape(lon)).copy())
        fy = np.clip(self._fractional_index(self.lats, lat), 0, grid.shape[0] - 1)
        fx = np.clip(self._fractional_index(self.lons, lon), 0, grid.shape[1] - 1)
        y0 = np.clip(np.floor(fy).astype(np.int64), 0, grid.shape[0] - 2)
        x0 = np.clip(np.floor(fx).astype(np.int64), 0, grid.shape[1] - 2)
        ty = fy - y0
        tx = fx - x0
        total = np.zeros(np.shape(fy), dtype=np.float64)
        weight = np.zeros(np.shape(fy), dtype=np.float64)
        for dy, wy in ((0, 1.0 - ty), (1, ty)):
            for dx, wx in ((0, 1.0 - tx), (1, tx)):
                value = grid[y0 + dy, x0 + dx]
                w = wy * wx * np.isfinite(value)
                total += np.where(np.isfinite(value), value, 0.0) * w
                weight += w
        return np.where(weight > 0, total / np.maximum(weight, 1e-12), 0.0)


# ---------------------------------------------------------------------------
# Forcing implementations
# ---------------------------------------------------------------------------


class Forcing:
    """Interface the drift engine integrates against."""

    mode = "unspecified"
    label = C.LABEL_DRIFT_SYNTHETIC

    def velocity(
        self, lon: np.ndarray, lat: np.ndarray, seconds: float
    ) -> tuple[np.ndarray, np.ndarray]:
        """Eastward and northward speed in m/s at each position and offset time."""
        raise NotImplementedError

    def is_water(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def land_cells(self, bounds: Sequence[float], max_cells: int = 4000) -> dict[str, Any]:
        """Land-mask cells overlapping ``bounds``, for drawing. Never used for physics."""
        land = getattr(self, "land", None)
        if land is None or not hasattr(land, "land_cells"):
            return {"available": False, "cells": [], "note": "no land mask available"}
        return land.land_cells(bounds, max_cells=max_cells)

    def domain_bounds(self) -> list[float] | None:
        """``[lon_min, lat_min, lon_max, lat_max]`` outside which the field is undefined.

        ``None`` means the field is defined everywhere, which is true of the
        analytic synthetic flow but not of a gridded product.
        """
        return None

    def describe(self) -> dict[str, Any]:
        raise NotImplementedError


class NoLandMask:
    """Used when no land mask is available: everything is treated as water."""

    resolution_deg: float | None = None
    available = False

    def is_water(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        return np.ones(np.shape(lon), dtype=bool)

    def land_cells(self, bounds: Sequence[float], max_cells: int = 4000) -> dict[str, Any]:
        return {
            "available": False,
            "cells": [],
            "note": "no land mask available, so no coastline can be drawn",
        }

    def describe(self) -> dict[str, Any]:
        return {
            "available": False,
            "note": (
                "no land mask available, so every particle is treated as being at sea "
                "and beaching is not detected"
            ),
        }


class GridLandMask:
    """Land mask taken from the CMEMS no-data pattern."""

    available = True

    def __init__(self, grid: LatLonGrid, resolution_deg: float):
        self.grid = grid
        self.resolution_deg = float(resolution_deg)
        self.water_fraction = float(np.mean(grid.arrays["water"] > 0))

    def is_water(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        return self.grid.nearest("water", lon, lat) > 0

    def land_cells(self, bounds: Sequence[float], max_cells: int = 4000) -> dict[str, Any]:
        """Land cells overlapping ``bounds``, as ``[west, south, east, north]`` boxes.

        This is what the dashboard draws as a coastline, and calling it a coastline would
        overstate it: these are 9 km product cells, so the shoreline it implies is a
        staircase accurate to about one cell. Drawing the mask the model actually beached
        particles against is more honest than overlaying a crisp vector coastline from some
        other source, which would look authoritative and would not be the surface the
        simulation used.
        """
        water = self.grid.arrays["water"]
        lats = self.grid.lats
        lons = self.grid.lons
        half = self.resolution_deg / 2.0
        west, south, east, north = (float(v) for v in bounds)

        rows = np.nonzero((lats >= south - half) & (lats <= north + half))[0]
        cols = np.nonzero((lons >= west - half) & (lons <= east + half))[0]
        cells: list[list[float]] = []
        truncated = False
        for row in rows:
            for col in cols:
                if water[row, col] > 0:
                    continue
                if len(cells) >= max_cells:
                    truncated = True
                    break
                cells.append(
                    [
                        round(float(lons[col]) - half, 6),
                        round(float(lats[row]) - half, 6),
                        round(float(lons[col]) + half, 6),
                        round(float(lats[row]) + half, 6),
                    ]
                )
            if truncated:
                break

        km = self.resolution_deg * DEG * C.EARTH_RADIUS_M / 1000.0
        return {
            "available": True,
            "cells": cells,
            "resolutionDeg": round(self.resolution_deg, 6),
            "resolutionKm": round(km, 2),
            "truncated": truncated,
            "source": "CMEMS no-data cells (land is where both velocity components are NaN)",
            "note": (
                f"{len(cells)} land cells of {km:.1f} km, i.e. the beaching surface the "
                "simulation used, not a surveyed coastline"
            ),
        }

    def describe(self) -> dict[str, Any]:
        km = self.resolution_deg * DEG * C.EARTH_RADIUS_M / 1000.0
        return {
            "available": True,
            "source": "CMEMS no-data cells (land is where both velocity components are NaN)",
            "resolutionDeg": round(self.resolution_deg, 6),
            "resolutionKm": round(km, 2),
            "waterFractionOverWindow": round(self.water_fraction, 4),
            "caveat": (
                f"the mask is {km:.1f} km per cell while the imagery is 10 m per pixel, so "
                "beaching is detected only to that resolution and a particle within one "
                "cell of the coast may already be ashore"
            ),
            "validity": (
                "coastlines do not move between the acquisition dates and the product date, "
                "so this mask is used even though the currents are not"
            ),
        }


@dataclass
class SyntheticSpec:
    """Every number that defines one synthetic field, so a run can be replayed."""

    seed: int
    target_speed_ms: float
    background: tuple[float, float]
    modes: list[dict[str, float]]
    wind_speed_ms: float
    wind_direction_deg: float
    wind_period_h: float
    scale: float = 1.0
    rms_speed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "targetSpeedMs": round(self.target_speed_ms, 5),
            "backgroundMs": [round(self.background[0], 5), round(self.background[1], 5)],
            "modes": self.modes,
            "windSpeedMs": round(self.wind_speed_ms, 3),
            "windDirectionDeg": round(self.wind_direction_deg, 2),
            "windPeriodH": round(self.wind_period_h, 3),
            "calibrationScale": round(self.scale, 6),
            "currentRmsSpeedMs": round(self.rms_speed_ms, 5),
        }


class SyntheticForcing(Forcing):
    """Deterministic, non-divergent synthetic surface flow plus a rotating wind.

    The current is the curl of a streamfunction built from three travelling
    eddy modes at tidal periods, on top of a steady background drift. Because
    ``(u, v) = (dpsi/dy, -dpsi/dx)`` the divergence is analytically zero, so the
    field stirs and translates the slick without inventing convergence.

    The amplitude is not invented: the field is rescaled so its RMS speed equals
    the speed measured in the CMEMS product over this scene's own footprint.
    """

    mode = "synthetic"
    # ``label`` is a property below: it names the wind product too, and only one half of
    # the forcing may be real.

    def __init__(
        self,
        lon0: float,
        lat0: float,
        spec: SyntheticSpec,
        land: GridLandMask | NoLandMask,
        cfg: C.DriftConfig,
        anchor: dict[str, Any] | None = None,
        wind: "WindField | None" = None,
    ):
        self.lon0 = float(lon0)
        self.lat0 = float(lat0)
        self.spec = spec
        self.land = land
        self.cfg = cfg
        self.anchor = anchor or {}
        # A real ERA5 wind over a synthetic current is the common case: wind is a
        # 30 MB hourly download and currents for a 2017 date are neither.
        self.wind_field: "WindField" = wind or SyntheticWind(spec, enabled=cfg.use_wind)
        self._metres_per_deg_lon = DEG * C.EARTH_RADIUS_M * math.cos(self.lat0 * DEG)
        self._metres_per_deg_lat = DEG * C.EARTH_RADIUS_M
        self._calibrate()

    # -- construction ------------------------------------------------------

    @classmethod
    def build(
        cls,
        lon0: float,
        lat0: float,
        scene_key: str,
        target_speed_ms: float,
        land: GridLandMask | NoLandMask,
        cfg: C.DriftConfig,
        anchor: dict[str, Any] | None = None,
        wind: "WindField | None" = None,
    ) -> "SyntheticForcing":
        seed = C.stable_seed(scene_key, cfg.seed)
        rng = np.random.default_rng(seed)
        # Wavelengths spanning a mesoscale eddy down to a submesoscale filament.
        wavelengths_m = (42_000.0, 16_000.0, 6_500.0)
        periods_h = (M2_PERIOD_H, K1_PERIOD_H, M4_PERIOD_H)
        weights = (1.0, 0.55, 0.28)
        modes: list[dict[str, float]] = []
        for wavelength, period, weight in zip(wavelengths_m, periods_h, weights):
            direction = float(rng.uniform(0.0, 2.0 * math.pi))
            k = 2.0 * math.pi / wavelength
            modes.append(
                {
                    "wavelengthM": wavelength,
                    "periodH": round(period, 4),
                    "amplitude": weight,
                    "kx": k * math.cos(direction),
                    "ky": k * math.sin(direction),
                    "phaseX": float(rng.uniform(0.0, 2.0 * math.pi)),
                    "phaseY": float(rng.uniform(0.0, 2.0 * math.pi)),
                    "phaseT": float(rng.uniform(0.0, 2.0 * math.pi)),
                }
            )
        bearing = float(rng.uniform(0.0, 2.0 * math.pi))
        # A steady component at half the target keeps the slick translating, so the
        # backward envelope points somewhere rather than just diffusing outward.
        steady = 0.5 * target_speed_ms
        spec = SyntheticSpec(
            seed=seed,
            target_speed_ms=float(target_speed_ms),
            background=(steady * math.sin(bearing), steady * math.cos(bearing)),
            modes=modes,
            wind_speed_ms=float(rng.uniform(3.0, 7.0)),
            wind_direction_deg=float(rng.uniform(0.0, 360.0)),
            wind_period_h=float(rng.uniform(26.0, 40.0)),
        )
        return cls(lon0, lat0, spec, land, cfg, anchor, wind=wind)

    def _calibrate(self) -> None:
        """Scale the field so its RMS current speed matches the anchor exactly."""
        offsets = np.linspace(-20_000.0, 20_000.0, 13)
        east, north = np.meshgrid(offsets, offsets)
        times = np.linspace(0.0, 24.0 * 3600.0, 9)
        self.spec.scale = 1.0
        squares = []
        for seconds in times:
            u, v = self._current_local(east, north, float(seconds))
            squares.append(np.mean(u * u + v * v))
        rms = float(math.sqrt(max(float(np.mean(squares)), 1e-12)))
        self.spec.scale = self.spec.target_speed_ms / rms if rms > 0 else 1.0
        self.spec.rms_speed_ms = self.spec.target_speed_ms

    # -- field -------------------------------------------------------------

    def _current_local(
        self, east: np.ndarray, north: np.ndarray, seconds: float
    ) -> tuple[np.ndarray, np.ndarray]:
        """Curl of the streamfunction, in local metres. Divergence is exactly zero."""
        u = np.zeros(np.shape(east), dtype=np.float64)
        v = np.zeros(np.shape(east), dtype=np.float64)
        for mode in self.spec.modes:
            kx = mode["kx"]
            ky = mode["ky"]
            k = math.hypot(kx, ky)
            if k <= 0.0:
                continue
            omega = 2.0 * math.pi / (mode["periodH"] * 3600.0)
            # psi = c * sin(kx*X + px) * sin(ky*Y + py) * cos(omega*t + pt)
            c = mode["amplitude"] / k
            phase_t = math.cos(omega * seconds + mode["phaseT"])
            sx = np.sin(kx * east + mode["phaseX"])
            cx = np.cos(kx * east + mode["phaseX"])
            sy = np.sin(ky * north + mode["phaseY"])
            cy = np.cos(ky * north + mode["phaseY"])
            u += c * ky * sx * cy * phase_t
            v -= c * kx * cx * sy * phase_t
        u = u * self.spec.scale + self.spec.background[0]
        v = v * self.spec.scale + self.spec.background[1]
        return u, v

    def wind(self, seconds: float) -> tuple[Any, Any]:
        """The wind vector at the scene origin.

        Kept as a scene-wide accessor for reporting. The synthetic wind is uniform so
        this *is* the field; a gridded wind varies in space and :meth:`velocity` samples
        it per particle instead.
        """
        return self.wind_at(self.lon0, self.lat0, seconds)

    def wind_at(
        self, lon: np.ndarray | float, lat: np.ndarray | float, seconds: float
    ) -> tuple[Any, Any]:
        if not self.cfg.use_wind:
            return 0.0, 0.0
        return self.wind_field.at(lon, lat, seconds)

    def velocity(
        self, lon: np.ndarray, lat: np.ndarray, seconds: float
    ) -> tuple[np.ndarray, np.ndarray]:
        east = (np.asarray(lon, dtype=np.float64) - self.lon0) * self._metres_per_deg_lon
        north = (np.asarray(lat, dtype=np.float64) - self.lat0) * self._metres_per_deg_lat
        u, v = self._current_local(east, north, seconds)
        wind_u, wind_v = self.wind_at(lon, lat, seconds)
        return (
            u + self.cfg.windage_factor * wind_u,
            v + self.cfg.windage_factor * wind_v,
        )

    def is_water(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        return self.land.is_water(lon, lat)

    @property
    def label(self) -> str:
        """Names both halves of the forcing, because only one of them may be real."""
        return C.LABEL_DRIFT_HYBRID if self.wind_field.real else C.LABEL_DRIFT_SYNTHETIC

    def wind_speed_ms(self) -> float:
        """Representative wind speed, from whichever field is in use."""
        gridded = getattr(self.wind_field, "mean_speed_ms", None)
        return float(gridded()) if callable(gridded) else float(self.spec.wind_speed_ms)

    def describe(self) -> dict[str, Any]:
        windage = self.cfg.windage_factor * self.wind_speed_ms()
        return {
            "mode": self.mode,
            "label": self.label,
            # The currents are synthetic whatever the wind is, and this flag has always
            # meant the currents; the wind reports its own provenance below.
            "isSynthetic": True,
            "origin": [round(self.lon0, 6), round(self.lat0, 6)],
            "method": (
                "surface current is the curl of a streamfunction made of three "
                "travelling modes at the M2, K1 and M4 tidal periods over a steady "
                "background drift, so the flow is analytically non-divergent"
            ),
            "amplitudeAnchor": self.anchor,
            "spec": self.spec.to_dict(),
            "wind": self.wind_field.describe(),
            "windIsReal": bool(self.cfg.use_wind and self.wind_field.real),
            "windDriftMs": round(windage, 4),
            "windNote": (
                self.wind_field.note(self.cfg.windage_factor)
                + (
                    f", comparable to the {self.spec.target_speed_ms:.3f} m/s current - "
                    "as it is in reality, where wind drift often dominates"
                    if windage > 0.4 * self.spec.target_speed_ms
                    else ""
                )
                if self.cfg.use_wind
                else "wind drift disabled for this run"
            ),
            "landMask": self.land.describe(),
            "reproducibility": (
                f"seed {self.spec.seed}, derived by SHA-256 from the scene identifier and "
                f"base seed {self.cfg.seed}, so the field is identical on every run"
            ),
            "warning": (
                "the current field is a plausible synthetic ocean, not the ocean on the "
                "acquisition date; trajectories are a methodology demonstration and are "
                "not evidence about any real event"
                if not self.wind_field.real
                else "the wind is measured but the current field is synthetic, so the "
                "trajectory is still a methodology demonstration and not evidence "
                "about any real event"
            ),
        }


# ---------------------------------------------------------------------------
# Wind
# ---------------------------------------------------------------------------


class WindField:
    """The 10 m wind the windage term is taken from.

    Three implementations, and which one a case used is printed rather than implied.
    Oil moves at roughly 3% of the wind, and at the speeds involved here that term is
    the same size as the current -- so a run that silently had no wind in it is a
    different physical claim from one that had a measured wind, and the payload has to
    distinguish them.
    """

    source = "none"
    real = False

    def at(
        self, lon: np.ndarray | float, lat: np.ndarray | float, seconds: float
    ) -> tuple[Any, Any]:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        raise NotImplementedError

    def note(self, windage_factor: float) -> str:
        raise NotImplementedError


class NoWind(WindField):
    """No wind at all, which is what the CMEMS path used to do without saying so."""

    source = "none"
    real = False

    def at(
        self, lon: np.ndarray | float, lat: np.ndarray | float, seconds: float
    ) -> tuple[float, float]:
        return 0.0, 0.0

    def describe(self) -> dict[str, Any]:
        return {
            "available": False,
            "source": "none",
            "isSynthetic": False,
            "note": (
                "no wind product covers this scene, so the trajectory reflects currents "
                "only; real oil also moves with the wind, so the spread here is a lower "
                "bound on where it could be"
            ),
        }

    def note(self, windage_factor: float) -> str:
        return (
            "no wind product is supplied, so wind drift is omitted and the "
            "trajectory reflects currents only"
        )


class SyntheticWind(WindField):
    """The slowly rotating uniform wind the synthetic scenario has always used."""

    source = "synthetic"
    real = False

    def __init__(self, spec: SyntheticSpec, enabled: bool = True):
        self.spec = spec
        self.enabled = bool(enabled)

    def at(
        self, lon: np.ndarray | float, lat: np.ndarray | float, seconds: float
    ) -> tuple[float, float]:
        if not self.enabled:
            return 0.0, 0.0
        omega = 2.0 * math.pi / (self.spec.wind_period_h * 3600.0)
        angle = self.spec.wind_direction_deg * DEG + omega * seconds
        return (
            self.spec.wind_speed_ms * math.sin(angle),
            self.spec.wind_speed_ms * math.cos(angle),
        )

    def describe(self) -> dict[str, Any]:
        return {
            "available": self.enabled,
            "source": "synthetic",
            "isSynthetic": True,
            "speedMs": round(self.spec.wind_speed_ms, 3),
            "directionDeg": round(self.spec.wind_direction_deg, 2),
            "rotationPeriodH": round(self.spec.wind_period_h, 3),
            "note": (
                "a uniform wind vector rotating on a fixed period, drawn from the scene's "
                "own seed; plausible in magnitude and invented in direction"
            ),
        }

    def note(self, windage_factor: float) -> str:
        drift = windage_factor * self.spec.wind_speed_ms
        return (
            f"oil also moves at {windage_factor:.0%} of a synthetic "
            f"{self.spec.wind_speed_ms:.1f} m/s wind, i.e. {drift:.3f} m/s"
        )


class GriddedWind(WindField):
    """Real ERA5 10 m wind, interpolated in space and in time.

    The frames are read once at construction and the reader is then closed: a scene
    footprint at 0.25 degrees is a few hundred cells, so a whole 24 h horizon of hourly
    wind is smaller than one CMEMS timestep and there is no reason to keep a file handle
    open across the integration.

    Time interpolation is linear between the bracketing hours and clamps at the ends of
    the file. Clamping is visible in :meth:`describe` through ``horizonCoverageFraction``
    on the overlap verdict, because a hindcast that ran past the end of its wind file is
    a weaker claim than one that did not.
    """

    source = "era5"
    real = True

    def __init__(
        self,
        frames: list[tuple[float, LatLonGrid]],
        overlap: dict[str, Any],
        product: str,
        cadence_hours: float,
        lon_shift: str = "none",
        expver_index: int | None = None,
    ):
        if not frames:
            raise ValueError("GriddedWind needs at least one frame")
        self.frames = sorted(frames, key=lambda item: item[0])
        self.offsets = np.asarray([offset for offset, _ in self.frames], dtype=np.float64)
        self.overlap = overlap
        self.product = product
        self.cadence_hours = float(cadence_hours)
        self.lon_shift = lon_shift
        self.expver_index = expver_index

    # -- construction ------------------------------------------------------
    @classmethod
    def build(
        cls,
        reader: Any,
        bounds: Sequence[float],
        when: datetime,
        horizon_hours: float,
        overlap: dict[str, Any],
        pad_deg: float = 1.0,
    ) -> "GriddedWind | None":
        windows = reader.window_series(bounds, when, horizon_hours, pad_deg=pad_deg)
        frames: list[tuple[float, LatLonGrid]] = []
        for win in windows:
            if not win.valid.any():
                continue  # an all-NaN hour carries no wind; interpolate across it
            stamp = reader.times[win.time_index]
            frames.append(
                (
                    (stamp - when).total_seconds(),
                    LatLonGrid(win.lats, win.lons, u=win.u, v=win.v),
                )
            )
        if not frames:
            return None
        lon_lo, lon_hi = reader.lon_range
        if lon_hi > 180.0 and lon_lo >= 0.0:
            lon_shift = "to-360"
        elif lon_lo < 0.0:
            lon_shift = "to-signed"
        else:
            lon_shift = "none"
        return cls(
            frames=frames,
            overlap=overlap,
            product=str(overlap.get("product") or ""),
            cadence_hours=reader.cadence_hours(),
            lon_shift=lon_shift,
            expver_index=getattr(reader, "_expver_choice", None),
        )

    # -- sampling ----------------------------------------------------------
    def _query_lon(self, lon: np.ndarray | float) -> np.ndarray:
        """Put query longitudes into the file's convention.

        The same decision :meth:`Era5Wind.normalise_lon` makes, taken once at build time
        and replayed here so the reader does not have to stay alive for it.
        """
        arr = np.asarray(lon, dtype=np.float64)
        if self.lon_shift == "to-360":
            return np.where(arr < 0.0, arr + 360.0, arr)
        if self.lon_shift == "to-signed":
            return np.where(arr > 180.0, arr - 360.0, arr)
        return arr

    def _bracket(self, seconds: float) -> tuple[int, int, float]:
        """Frame indices either side of ``seconds``, and the weight of the second."""
        if self.offsets.size == 1:
            return 0, 0, 0.0
        position = int(np.searchsorted(self.offsets, float(seconds)))
        if position <= 0:
            return 0, 0, 0.0
        if position >= self.offsets.size:
            last = int(self.offsets.size - 1)
            return last, last, 0.0
        lo, hi = position - 1, position
        span = self.offsets[hi] - self.offsets[lo]
        if span <= 0:
            return lo, lo, 0.0
        return lo, hi, float((float(seconds) - self.offsets[lo]) / span)

    def at(
        self, lon: np.ndarray | float, lat: np.ndarray | float, seconds: float
    ) -> tuple[np.ndarray, np.ndarray]:
        query_lon = self._query_lon(lon)
        lo, hi, weight = self._bracket(seconds)
        grid_lo = self.frames[lo][1]
        u = grid_lo.bilinear("u", query_lon, lat)
        v = grid_lo.bilinear("v", query_lon, lat)
        if hi != lo and weight > 0.0:
            grid_hi = self.frames[hi][1]
            u = (1.0 - weight) * u + weight * grid_hi.bilinear("u", query_lon, lat)
            v = (1.0 - weight) * v + weight * grid_hi.bilinear("v", query_lon, lat)
        return u, v

    # -- reporting ---------------------------------------------------------
    def mean_speed_ms(self) -> float:
        """Mean wind speed over the frame nearest the acquisition."""
        index = int(np.argmin(np.abs(self.offsets)))
        grid = self.frames[index][1]
        u = np.asarray(grid.arrays["u"], dtype=np.float64)
        v = np.asarray(grid.arrays["v"], dtype=np.float64)
        finite = np.isfinite(u) & np.isfinite(v)
        if not finite.any():
            return 0.0
        return float(np.hypot(u[finite], v[finite]).mean())

    def describe(self) -> dict[str, Any]:
        first = self.frames[0][0] / 3600.0
        last = self.frames[-1][0] / 3600.0
        return {
            "available": True,
            "source": "era5",
            "isSynthetic": False,
            "product": self.product,
            "speedMs": round(self.mean_speed_ms(), 3),
            "frameCount": len(self.frames),
            "cadenceHours": round(self.cadence_hours, 3),
            "frameOffsetsHours": [round(first, 2), round(last, 2)],
            "expverIndex": self.expver_index,
            "interpolation": (
                "bilinear in space, linear in time between the bracketing hours, "
                "clamped at the ends of the file rather than extrapolated"
            ),
            "overlap": self.overlap,
            "note": (
                "ERA5 reanalysis 10 m wind covering the acquisition, read from the "
                "operator-supplied file; this is a measured field, not a synthetic one"
            ),
        }

    def note(self, windage_factor: float) -> str:
        drift = windage_factor * self.mean_speed_ms()
        return (
            f"oil also moves at {windage_factor:.0%} of the ERA5 10 m wind, which "
            f"averages {self.mean_speed_ms():.1f} m/s over this footprint, i.e. "
            f"{drift:.3f} m/s"
        )


class CmemsForcing(Forcing):
    """Real CMEMS surface currents. Only constructed when the overlap check passes."""

    mode = "cmems"
    # ``label`` is a property below, so a run with real wind as well as real currents
    # says so instead of reporting only the currents.

    def __init__(
        self,
        grid: LatLonGrid,
        land: GridLandMask,
        cfg: C.DriftConfig,
        overlap: dict[str, Any],
        wind: "WindField | None" = None,
    ):
        self.grid = grid
        self.land = land
        self.cfg = cfg
        self.overlap = overlap
        self.wind_field: "WindField" = wind or NoWind()

    @property
    def label(self) -> str:
        return C.LABEL_DRIFT_REAL if self.wind_field.real else C.LABEL_DRIFT_CMEMS

    def wind_speed_ms(self) -> float:
        gridded = getattr(self.wind_field, "mean_speed_ms", None)
        return float(gridded()) if callable(gridded) else 0.0

    def velocity(
        self, lon: np.ndarray, lat: np.ndarray, seconds: float
    ) -> tuple[np.ndarray, np.ndarray]:
        u = self.grid.bilinear("u", lon, lat)
        v = self.grid.bilinear("v", lon, lat)
        if self.cfg.use_wind:
            wind_u, wind_v = self.wind_field.at(lon, lat, seconds)
            u = u + self.cfg.windage_factor * wind_u
            v = v + self.cfg.windage_factor * wind_v
        return u, v

    def is_water(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        return self.land.is_water(lon, lat)

    def domain_bounds(self) -> list[float] | None:
        return self.grid.bounds

    def describe(self) -> dict[str, Any]:
        finite = self.grid.arrays["water"] > 0
        speed = np.hypot(
            np.where(finite, self.grid.arrays["u"], 0.0),
            np.where(finite, self.grid.arrays["v"], 0.0),
        )
        windage = self.cfg.windage_factor * self.wind_speed_ms() if self.cfg.use_wind else 0.0
        return {
            "mode": self.mode,
            "label": self.label,
            "isSynthetic": False,
            "method": (
                "bilinear interpolation of the CMEMS surface velocity field, held steady "
                "over the drift horizon at the nearest available product timestep"
                + (
                    ", plus an ERA5 wind interpolated hourly - the wind product is finer "
                    "in time than the current product, and neither is resampled to the "
                    "other"
                    if self.wind_field.real
                    else ""
                )
            ),
            "productTimeUtc": self.overlap.get("nearestProductTimeUtc"),
            "productTimeIndex": self.overlap.get("nearestProductTimeIndex"),
            "timeGapHours": self.overlap.get("timeGapHours"),
            "speedMeanMs": round(float(speed[finite].mean()), 4) if finite.any() else None,
            "speedMaxMs": round(float(speed[finite].max()), 4) if finite.any() else None,
            "wind": self.wind_field.describe(),
            "windIsReal": bool(self.cfg.use_wind and self.wind_field.real),
            "windDriftMs": round(windage, 4),
            "windNote": (
                "wind drift disabled for this run"
                if not self.cfg.use_wind
                else self.wind_field.note(self.cfg.windage_factor)
            ),
            "landMask": self.land.describe(),
        }


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def _parse_time(when: str | datetime | None) -> datetime | None:
    if when is None:
        return None
    if isinstance(when, datetime):
        return when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    text = str(when).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def resolve_wind(
    bounds: Sequence[float],
    when: datetime | None,
    cfg: C.DriftConfig,
    horizon_hours: float | None = None,
    reader: Any | None = None,
    close_reader: bool = True,
) -> tuple["WindField | None", dict[str, Any]]:
    """Choose a real ERA5 wind for one scene, or nothing, and say why.

    ``None`` means *no real wind is available* rather than *no wind*: the caller then
    applies its own default, which is the synthetic rotating vector on the synthetic
    path and no wind at all on the CMEMS path. Those two defaults are different claims
    and only the caller knows which one it is entitled to make.

    The same rule the PRD sets for currents applies here: the file is used only when it
    covers the scene in space and in time.
    """
    horizon = float(cfg.horizon_hours if horizon_hours is None else horizon_hours)
    decision: dict[str, Any] = {
        "requested": bool(cfg.use_wind),
        "windageFactor": cfg.windage_factor,
        "rule": (
            "ERA5 10 m wind is used only when the file covers the scene footprint and "
            "its nearest hour is within tolerance of the acquisition; otherwise the wind "
            "is synthetic on the synthetic path and omitted on the CMEMS path"
        ),
    }
    if not cfg.use_wind:
        decision["mode"] = "disabled"
        decision["reasons"] = ["wind drift is switched off in this run's configuration"]
        return None, decision

    opened_here = False
    if reader is None:
        try:
            reader = era5_mod.open_default()
            opened_here = reader is not None
        except Exception as exc:  # a malformed file must not stop the pipeline
            reader = None
            decision["error"] = f"{type(exc).__name__}: {exc}"
    decision["era5Available"] = reader is not None
    if reader is None:
        decision["mode"] = "none"
        decision["reasons"] = [
            "no ERA5 wind file was found on disk (see docs: SPILLTRACE_ERA5 or "
            "data/raw/era5_wind*.nc)"
        ]
        return None, decision

    try:
        overlap = reader.overlap(bounds, when, horizon_hours=horizon)
        decision["overlap"] = overlap
        decision["product"] = reader.describe()
        if not overlap.get("usable"):
            decision["mode"] = "none"
            decision["reasons"] = list(overlap.get("reasons") or []) or [
                "the ERA5 overlap check did not pass"
            ]
            return None, decision
        if when is None:  # cannot place the frames on a timeline without an instant
            decision["mode"] = "none"
            decision["reasons"] = ["the scene acquisition time is unknown"]
            return None, decision
        wind = GriddedWind.build(reader, bounds, when, horizon, overlap)
        if wind is None:
            decision["mode"] = "none"
            decision["reasons"] = [
                "every ERA5 hour over this footprint is no-data, so no frame could be "
                "built from the file"
            ]
            return None, decision
    except era5_mod.Era5Error as exc:
        decision["mode"] = "none"
        decision["error"] = f"{type(exc).__name__}: {exc}"
        decision["reasons"] = [str(exc)]
        return None, decision
    finally:
        if opened_here and close_reader:
            try:
                reader.close()
            except Exception:  # closing a reader must never mask the verdict
                pass

    decision["mode"] = "era5"
    decision["reasons"] = ["the ERA5 file covers the scene in space and time"]
    decision["meanSpeedMs"] = round(wind.mean_speed_ms(), 3)
    decision["frameCount"] = len(wind.frames)
    return wind, decision


def resolve_forcing(
    bounds: Sequence[float],
    when: str | datetime | None,
    scene_key: str,
    cfg: C.DriftConfig | None = None,
    surface: "cmems_mod.CmemsSurface | None" = None,
    close_surface: bool = True,
    wind_reader: Any | None = None,
    close_wind_reader: bool = True,
) -> tuple[Forcing, dict[str, Any]]:
    """Choose CMEMS or synthetic forcing for one scene, and say why.

    ``bounds`` is ``[lon_min, lat_min, lon_max, lat_max]``. The decision is made
    from the product itself every time; nothing is hard-coded from the audit.

    Currents and wind are resolved independently, because they come from different
    products and either one can be real without the other. The commonest real-data case
    is a synthetic current under a measured ERA5 wind: an hourly wind subset for one day
    is a few megabytes, while a current field for a 2017 date is a different download
    entirely.
    """
    cfg = cfg or C.DriftConfig()
    lon0 = (float(bounds[0]) + float(bounds[2])) / 2.0
    lat0 = (float(bounds[1]) + float(bounds[3])) / 2.0
    timestamp = _parse_time(when)

    opened_here = False
    if surface is None:
        try:
            surface = cmems_mod.open_default()
            opened_here = True
        except Exception as exc:  # a malformed product must not stop the pipeline
            surface = None
            opened_here = False
            probe_error: str | None = f"{type(exc).__name__}: {exc}"
        else:
            probe_error = None
    else:
        probe_error = None

    decision: dict[str, Any] = {
        "generatedUtc": C.utc_now_iso(),
        "sceneKey": scene_key,
        "sceneBounds": [round(float(b), 6) for b in bounds],
        "sceneTimeUtc": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ") if timestamp else None,
        "rule": (
            "CMEMS currents are used only when the product covers the scene in space "
            "and its nearest timestep is within the stated tolerance of the acquisition; "
            "otherwise the forcing is synthetic and labelled as such"
        ),
        "cmemsAvailable": surface is not None,
        "cmemsError": probe_error,
    }

    land: GridLandMask | NoLandMask = NoLandMask()
    anchor: dict[str, Any] = {
        "source": "documented default",
        "speedMs": FALLBACK_SPEED_MS,
        "note": (
            "the CMEMS product could not be read, so the amplitude falls back to the "
            "mean speed recorded over these scene footprints in DATA_AUDIT.md"
        ),
    }
    overlap: dict[str, Any] | None = None
    window = None

    try:
        if surface is not None:
            overlap = surface.overlap(bounds, timestamp)
            decision["overlap"] = overlap
            # ``timestamp`` matters: the window is cut from the timestep nearest the
            # acquisition, which is the one ``overlap`` reports the gap to.
            window = surface.window(bounds, when=timestamp)
            if window is not None and window.water.any():
                grid = LatLonGrid(
                    window.lats,
                    window.lons,
                    u=window.u,
                    v=window.v,
                    water=window.water.astype(np.uint8),
                )
                lat_res, lon_res = surface.resolution()
                land = GridLandMask(grid, max(abs(lat_res), abs(lon_res)))
                stats = window.stats()
                decision["cmemsSpeedOverScene"] = stats
                if stats.get("speedMean"):
                    anchor = {
                        "source": "CMEMS product, measured over this scene's footprint",
                        "speedMs": stats["speedMean"],
                        "speedMinMs": stats["speedMin"],
                        "speedMaxMs": stats["speedMax"],
                        "productTimeUtc": window.time_utc,
                        "note": (
                            "the magnitude is taken from the real product even though its "
                            "timestep does not match the acquisition; only the scale is "
                            "borrowed, not the flow pattern"
                        ),
                    }
            elif window is not None:
                decision["cmemsSpeedOverScene"] = window.stats()
                decision.setdefault("notes", []).append(
                    "the product window over this scene is entirely land or no-data, so "
                    "neither a land mask nor an amplitude anchor could be taken from it"
                )
    finally:
        if opened_here and surface is not None and close_surface:
            surface.close()

    wind_field, wind_decision = resolve_wind(
        bounds,
        timestamp,
        cfg,
        reader=wind_reader,
        close_reader=close_wind_reader,
    )
    decision["wind"] = wind_decision
    decision["windMode"] = wind_decision.get("mode")

    usable = bool(overlap and overlap.get("usable"))
    if usable and window is not None and land.available:
        grid = LatLonGrid(
            window.lats,
            window.lons,
            u=window.u,
            v=window.v,
            water=window.water.astype(np.uint8),
        )
        forcing: Forcing = CmemsForcing(grid, land, cfg, overlap or {}, wind=wind_field)
        decision["mode"] = "cmems"
        # ``mode`` stays "cmems" whatever the wind is - the UI and the audit key on it -
        # so the wind's contribution shows up in the label and in ``windMode``.
        decision["label"] = forcing.label
        decision["reasons"] = ["the product covers the scene in space and time"]
    else:
        forcing = SyntheticForcing.build(
            lon0,
            lat0,
            scene_key,
            float(anchor.get("speedMs") or FALLBACK_SPEED_MS),
            land,
            cfg,
            anchor,
            wind=wind_field,
        )
        decision["mode"] = "synthetic"
        decision["label"] = forcing.label
        reasons = list((overlap or {}).get("reasons") or [])
        if surface is None:
            reasons.append("no CMEMS product was found on disk")
        decision["reasons"] = reasons or ["the CMEMS overlap check did not pass"]
    # ``windMode`` reports the wind that was actually integrated, which is why it is set
    # here and not from the resolution verdict above: ``resolve_wind`` answers "none" both
    # for a scene with no real wind file and for one where no wind is applied at all, and
    # those are different physics -- a synthetic rotating wind still moves the oil, while
    # the CMEMS-without-wind case makes the spread a lower bound. The verdict itself stays
    # available at ``decision["wind"]["mode"]``.
    decision["windMode"] = "disabled" if not cfg.use_wind else forcing.wind_field.source
    decision["forcing"] = forcing.describe()
    return forcing, decision
