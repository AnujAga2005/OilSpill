"""CMEMS surface-current access and overlap validation.

The PRD allows CMEMS forcing only when the product actually covers the scene in
*both* space and time. This module answers that question from the file itself
and exposes the surface ``uo``/``vo`` fields when they are usable.

It also exposes the NaN pattern of the current field as a land/no-data mask,
which the drift engine uses to flag particles that leave the water.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from . import config
from .netcdf4 import NetCDF4File

# Candidate variable names, in preference order, so a differently-named product
# still works without code changes.
U_NAMES = ("uo", "u", "eastward_sea_water_velocity", "utotal", "ugos")
V_NAMES = ("vo", "v", "northward_sea_water_velocity", "vtotal", "vgos")
LAT_NAMES = ("latitude", "lat", "nav_lat", "y")
LON_NAMES = ("longitude", "lon", "nav_lon", "x")
TIME_NAMES = ("time", "time_counter", "t")
DEPTH_NAMES = ("depth", "deptht", "lev", "z")

_UNIT_SECONDS = {
    "second": 1.0,
    "seconds": 1.0,
    "sec": 1.0,
    "s": 1.0,
    "minute": 60.0,
    "minutes": 60.0,
    "hour": 3600.0,
    "hours": 3600.0,
    "day": 86400.0,
    "days": 86400.0,
}


class CmemsError(Exception):
    """Raised when a NetCDF file cannot serve as current forcing."""


def _time_list(times: Sequence[datetime], limit: int = 8) -> list[str]:
    """Timestamps for a payload, abridged in the middle when there are many.

    A month of hourly currents is 720 steps. The overlap verdict is embedded in every
    case, so the whole axis is not printed there.
    """
    stamps = [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in times]
    if len(stamps) <= limit:
        return stamps
    half = limit // 2
    return stamps[:half] + [f"... {len(stamps) - limit} more ..."] + stamps[-half:]


def parse_time_units(units: str | None) -> tuple[float, datetime] | None:
    """Parse a CF ``<unit> since <epoch>`` string into (scale, epoch)."""
    if not units:
        return None
    match = re.match(
        r"\s*(\w+)\s+since\s+(\d{4})-(\d{1,2})-(\d{1,2})"
        r"(?:[ T](\d{1,2}):(\d{1,2})(?::(\d{1,2}(?:\.\d+)?))?)?",
        units,
    )
    if not match:
        return None
    unit = match.group(1).lower()
    scale = _UNIT_SECONDS.get(unit)
    if scale is None:
        return None
    year, month, day = (int(match.group(i)) for i in (2, 3, 4))
    hour = int(match.group(5) or 0)
    minute = int(match.group(6) or 0)
    second = float(match.group(7) or 0.0)
    epoch = datetime(
        year, month, day, hour, minute, int(second),
        int(round((second % 1) * 1e6)), tzinfo=timezone.utc,
    )
    return scale, epoch


@dataclass
class CmemsWindow:
    """A latitude/longitude sub-window of the current field."""

    lats: np.ndarray
    lons: np.ndarray
    u: np.ndarray  # (lat, lon) m/s, NaN over land / no data
    v: np.ndarray
    water: np.ndarray  # bool, True where both components are finite
    time_utc: str | None
    # Which timestep of the product this window was cut from. Defaulted so the
    # single-timestep supplied product reads the same as it always did.
    time_index: int = 0

    def stats(self) -> dict[str, Any]:
        finite = self.water
        if not finite.any():
            return {
                "cells": int(self.water.size),
                "waterCells": 0,
                "speedMin": None,
                "speedMax": None,
                "speedMean": None,
            }
        speed = np.hypot(self.u[finite], self.v[finite])
        return {
            "cells": int(self.water.size),
            "waterCells": int(finite.sum()),
            "uMin": round(float(self.u[finite].min()), 4),
            "uMax": round(float(self.u[finite].max()), 4),
            "vMin": round(float(self.v[finite].min()), 4),
            "vMax": round(float(self.v[finite].max()), 4),
            "speedMin": round(float(speed.min()), 4),
            "speedMax": round(float(speed.max()), 4),
            "speedMean": round(float(speed.mean()), 4),
        }


class CmemsSurface:
    """Reader for the surface layer of a CMEMS physics product."""

    def __init__(self, path: str | Path, file: Any | None = None):
        self.path = str(path)
        # The handle is injectable so the timestep logic below can be tested against
        # a multi-timestep product. Nothing in this repository can *write* NetCDF-4,
        # and the one supplied file holds a single timestep, so a fake handle is the
        # only way to exercise the branch that matters.
        self._file = file if file is not None else NetCDF4File(self.path)
        self._vars = self._file.variables()
        self.u_name = self._pick(U_NAMES)
        self.v_name = self._pick(V_NAMES)
        self.lat_name = self._pick(LAT_NAMES)
        self.lon_name = self._pick(LON_NAMES)
        self.time_name = self._pick(TIME_NAMES)
        self.depth_name = self._pick(DEPTH_NAMES)
        missing = [
            label
            for label, value in (
                ("u", self.u_name), ("v", self.v_name),
                ("latitude", self.lat_name), ("longitude", self.lon_name),
            )
            if value is None
        ]
        if missing:
            raise CmemsError(
                f"{Path(self.path).name} lacks required variables: {', '.join(missing)}"
            )
        self.lats = np.asarray(self._file.read_variable(self.lat_name), dtype=np.float64)
        self.lons = np.asarray(self._file.read_variable(self.lon_name), dtype=np.float64)
        self.times = self._read_times()
        self.depths = (
            np.asarray(self._file.read_variable(self.depth_name), dtype=np.float64)
            if self.depth_name
            else np.asarray([0.0])
        )
        self._u_cache: np.ndarray | None = None
        self._v_cache: np.ndarray | None = None
        self._cache_key: tuple[int, int] | None = None

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> "CmemsSurface":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._file.close()

    def _pick(self, candidates: Sequence[str]) -> str | None:
        for name in candidates:
            if name in self._vars:
                return name
        return None

    def _read_times(self) -> list[datetime]:
        if not self.time_name:
            return []
        raw = np.atleast_1d(np.asarray(self._file.read_variable(self.time_name)))
        node = self._vars[self.time_name]
        units = node.attributes.get("units") if hasattr(node, "attributes") else None
        parsed = parse_time_units(units if isinstance(units, str) else None)
        if parsed is None:
            return []
        scale, epoch = parsed
        out = []
        for value in raw.ravel():
            if not np.isfinite(value):
                continue
            out.append(epoch + timedelta(seconds=float(value) * scale))
        return out

    # -- field access ------------------------------------------------------
    def leading_axis_roles(self, shape: Sequence[int]) -> list[str]:
        """Name the axes in front of ``(lat, lon)`` for an array of this shape.

        The HDF5 parser exposes shapes and attributes but not ``DIMENSION_LIST``, so
        the roles are inferred from the axis lengths, falling back on the CF ordering
        that puts time before depth.

        This exists because the alternative was silent. The reader used to collapse
        leading axes with a bare ``data = data[0]`` in a loop, so a product holding a
        hundred timesteps was *always* sampled at the first one -- while
        :meth:`overlap` went on reporting the gap to the nearest one. A user who
        downloaded real currents for their acquisition would have been shown
        ``timeGapHours: 0.5`` over a field taken from a different week, and nothing in
        the payload would have contradicted it.
        """
        sizes = [int(s) for s in shape[:-2]]
        n_times = len(self.times)
        n_depths = int(self.depths.size)
        roles: list[str] = []
        for size in sizes:
            if "time" not in roles and (size == n_times or n_times == 0):
                roles.append("time")
            elif "depth" not in roles and size == n_depths:
                roles.append("depth")
            elif "time" not in roles:
                roles.append("time")
            else:
                roles.append("other")
        return roles

    def nearest_time_index(self, when: datetime | None) -> tuple[int, float | None]:
        """Index of the timestep closest to ``when``, and that gap in hours."""
        if when is None or not self.times:
            return 0, None
        gaps = [abs((t - when).total_seconds()) for t in self.times]
        index = int(min(range(len(gaps)), key=gaps.__getitem__))
        return index, gaps[index] / 3600.0

    def _surface(self, name: str, time_index: int = 0, depth_index: int = 0) -> np.ndarray:
        """Read one variable and reduce it to a (lat, lon) surface slice."""
        data = np.asarray(self._file.read_variable(name), dtype=np.float32)
        picks = {"time": int(time_index), "depth": int(depth_index)}
        for role in self.leading_axis_roles(data.shape):
            index = picks.get(role, 0)
            if not 0 <= index < data.shape[0]:
                # Raised rather than clamped: a silent clamp is exactly the failure
                # this method was rewritten to remove.
                raise CmemsError(
                    f"{name}: {role} index {index} is outside the product's "
                    f"{data.shape[0]} {role} step(s)"
                )
            data = data[index]
        while data.ndim > 2:  # any axis the roles could not account for
            data = data[0]
        if data.shape != (self.lats.size, self.lons.size):
            raise CmemsError(
                f"{name} has shape {data.shape}, expected "
                f"{(self.lats.size, self.lons.size)}"
            )
        return data

    def _load(self, time_index: int = 0, depth_index: int = 0) -> tuple[np.ndarray, np.ndarray]:
        """Both components at one timestep, caching only the most recent one.

        A single global timestep is 35 MB per component in the supplied product, so
        the cache holds one step rather than every step a caller asks for.
        """
        key = (int(time_index), int(depth_index))
        if self._cache_key != key or self._u_cache is None or self._v_cache is None:
            self._u_cache = self._surface(self.u_name, time_index, depth_index)
            self._v_cache = self._surface(self.v_name, time_index, depth_index)
            self._cache_key = key
        return self._u_cache, self._v_cache

    def surface_u(self, time_index: int = 0, depth_index: int = 0) -> np.ndarray:
        return self._load(time_index, depth_index)[0]

    def surface_v(self, time_index: int = 0, depth_index: int = 0) -> np.ndarray:
        return self._load(time_index, depth_index)[1]

    # -- geometry ----------------------------------------------------------
    @property
    def lat_range(self) -> tuple[float, float]:
        return float(self.lats.min()), float(self.lats.max())

    @property
    def lon_range(self) -> tuple[float, float]:
        return float(self.lons.min()), float(self.lons.max())

    @property
    def time_range(self) -> tuple[datetime, datetime] | None:
        if not self.times:
            return None
        return min(self.times), max(self.times)

    def resolution(self) -> tuple[float, float]:
        dlat = float(abs(np.diff(self.lats).mean())) if self.lats.size > 1 else 0.0
        dlon = float(abs(np.diff(self.lons).mean())) if self.lons.size > 1 else 0.0
        return dlat, dlon

    def index_window(
        self, bounds: Sequence[float], pad_deg: float = 0.5
    ) -> tuple[slice, slice] | None:
        """Index slices covering ``bounds`` (west, south, east, north) + padding."""
        west, south, east, north = (float(v) for v in bounds)
        lat_hits = np.nonzero(
            (self.lats >= south - pad_deg) & (self.lats <= north + pad_deg)
        )[0]
        lon_hits = np.nonzero(
            (self.lons >= west - pad_deg) & (self.lons <= east + pad_deg)
        )[0]
        if lat_hits.size == 0 or lon_hits.size == 0:
            return None
        return (
            slice(int(lat_hits[0]), int(lat_hits[-1]) + 1),
            slice(int(lon_hits[0]), int(lon_hits[-1]) + 1),
        )

    def window(
        self,
        bounds: Sequence[float],
        pad_deg: float = 0.5,
        when: datetime | None = None,
    ) -> CmemsWindow | None:
        """Extract the current field over a bounding box.

        ``when`` selects the timestep: the one nearest the acquisition, not the first
        one in the file. With ``when`` unset, or on a single-timestep product, that is
        timestep 0 either way.
        """
        window = self.index_window(bounds, pad_deg)
        if window is None:
            return None
        lat_slice, lon_slice = window
        time_index, _ = self.nearest_time_index(when)
        u = self.surface_u(time_index)[lat_slice, lon_slice]
        v = self.surface_v(time_index)[lat_slice, lon_slice]
        water = np.isfinite(u) & np.isfinite(v)
        return CmemsWindow(
            lats=self.lats[lat_slice],
            lons=self.lons[lon_slice],
            u=u,
            v=v,
            water=water,
            time_utc=(
                self.times[time_index].strftime("%Y-%m-%dT%H:%M:%SZ")
                if time_index < len(self.times)
                else None
            ),
            time_index=time_index,
        )

    # -- overlap verdict ---------------------------------------------------
    def overlap(
        self,
        bounds: Sequence[float],
        when: datetime | None,
        max_time_gap_hours: float = 24.0,
        pad_deg: float = 0.5,
    ) -> dict[str, Any]:
        """Decide whether this product may be used for a scene.

        Both tests must pass. A spatial hit additionally requires that the window
        contains water cells, because a box entirely over land carries no usable
        current.
        """
        west, south, east, north = (float(v) for v in bounds)
        lat_lo, lat_hi = self.lat_range
        lon_lo, lon_hi = self.lon_range
        inside = (
            lon_lo <= west and east <= lon_hi and lat_lo <= south and north <= lat_hi
        )
        window = self.window(bounds, pad_deg, when)
        water_cells = int(window.water.sum()) if window else 0
        spatial_ok = bool(inside and window is not None and water_cells > 0)

        index, gap_hours = self.nearest_time_index(when)
        nearest = self.times[index] if self.times and when is not None else None
        temporal_ok = gap_hours is not None and gap_hours <= max_time_gap_hours

        reasons: list[str] = []
        if not inside:
            reasons.append("scene footprint is outside the product grid")
        elif water_cells == 0:
            reasons.append("product has no water cells over the scene footprint")
        if when is None:
            reasons.append("scene acquisition time is unknown")
        elif not self.times:
            reasons.append("product carries no readable time axis")
        elif not temporal_ok:
            reasons.append(
                f"nearest product time is {gap_hours / 24.0:.1f} days from the "
                f"acquisition, beyond the {max_time_gap_hours:.0f} h tolerance"
            )

        return {
            "product": Path(self.path).name,
            "spatialOverlap": spatial_ok,
            "temporalOverlap": temporal_ok,
            "usable": bool(spatial_ok and temporal_ok),
            "sceneBounds": [west, south, east, north],
            "productLatRange": [round(lat_lo, 6), round(lat_hi, 6)],
            "productLonRange": [round(lon_lo, 6), round(lon_hi, 6)],
            "productTimesUtc": _time_list(self.times),
            "productTimeStepCount": len(self.times),
            "sceneTimeUtc": (
                when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                if when
                else None
            ),
            "nearestProductTimeUtc": (
                nearest.strftime("%Y-%m-%dT%H:%M:%SZ") if nearest else None
            ),
            # The slice a run would actually integrate, so the gap below can be checked
            # against the field that was read rather than trusted.
            "nearestProductTimeIndex": index if nearest else None,
            "timeGapHours": round(gap_hours, 2) if gap_hours is not None else None,
            "timeToleranceHours": max_time_gap_hours,
            "waterCellsOverScene": water_cells,
            "reasons": reasons,
            "windowStats": window.stats() if window else None,
        }

    def describe(self) -> dict[str, Any]:
        dlat, dlon = self.resolution()
        return {
            # Repo-relative: this description is embedded in the committed audit report
            # and in case payloads that get bundled into ``dist/``.
            "path": config.display_path(self.path),
            "file": Path(self.path).name,
            "variables": sorted(self._vars),
            "currentVariables": {"u": self.u_name, "v": self.v_name},
            "gridShape": [int(self.lats.size), int(self.lons.size)],
            "latRange": [round(v, 6) for v in self.lat_range],
            "lonRange": [round(v, 6) for v in self.lon_range],
            "resolutionDeg": [round(dlat, 6), round(dlon, 6)],
            "depthsM": [round(float(d), 4) for d in self.depths[:4]],
            "timesUtc": _time_list(self.times),
            "timeStepCount": len(self.times),
            "globalAttributes": {
                k: v
                for k, v in self._file.global_attributes().items()
                if isinstance(v, (str, int, float)) and len(str(v)) < 400
            },
        }


def open_default() -> CmemsSurface | None:
    """Open the CMEMS file supplied with the repository, if present."""
    path = config.cmems_path()
    if path is None:
        return None
    try:
        return CmemsSurface(path)
    except (CmemsError, OSError, ValueError):
        return None
