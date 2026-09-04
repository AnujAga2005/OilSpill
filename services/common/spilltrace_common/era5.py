"""ERA5 10 m wind access and overlap validation.

The drift engine has always had a windage term -- 3% of the wind vector, added to the
current -- and until now nothing ever supplied a wind vector to it. On the CMEMS path
the field was a hard ``(0.0, 0.0)`` and every payload said so: *"no wind product is
supplied, so wind drift is omitted"*. This module is the missing half.

It reads ERA5 single-level ``10m_u_component_of_wind`` / ``10m_v_component_of_wind``
from a NetCDF-4 file, using the same hand-written HDF5 reader as the currents, and
applies the same rule the PRD sets for CMEMS: **the product is used only when it
covers the scene in space and in time.** A file that misses either test is reported,
not quietly interpolated.

Three things here are not shared with :mod:`spilltrace_common.cmems`, and each of them
is a real property of ERA5 rather than a stylistic choice:

* **Longitudes run 0 to 360.** A global ERA5 download has no negative longitudes, so a
  Gulf of Mexico scene at -95 degrees sits outside the axis unless the query is
  shifted. :meth:`Era5Wind.normalise_lon` does that shift, in one place.
* **Latitudes descend**, 90 to -90, which is the opposite of the supplied CMEMS
  product. Both are handled by inspecting the axis rather than assuming a direction.
* **An ``expver`` axis appears** on downloads that straddle the ERA5 / ERA5T boundary,
  and one of its two slices is all no-data for any given hour. Taking index 0 blindly
  would hand the engine a field of NaN, so the finite slice is chosen and the choice is
  reported.

ERA5 is hourly, which is finer than the drift horizon, so the wind is interpolated
*in time* between the two bracketing hours rather than held constant. The currents are
not: CMEMS daily means are a single field per day, and a global timestep is 35 MB per
component, so the current field stays at the nearest step and
:meth:`Era5Wind.describe` says which of the two is which.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from . import config
from .cmems import _time_list, parse_time_units
from .netcdf4 import NetCDF4File

U10_NAMES = ("u10", "10u", "u10m", "eastward_wind", "uas")
V10_NAMES = ("v10", "10v", "v10m", "northward_wind", "vas")
LAT_NAMES = ("latitude", "lat", "y")
LON_NAMES = ("longitude", "lon", "x")
TIME_NAMES = ("valid_time", "time", "t")

# A whole-variable read is the only read the HDF5 layer offers, and ``apply_cf``
# upcasts to float64 on the way out. A global hourly month is 7.5e8 elements, i.e.
# 6 GB in float64; refusing that with an explanation beats being killed by the OOM
# reaper halfway through a case.
MAX_ELEMENTS = 60_000_000

# ERA5 is hourly. A gap wider than this and the file is not describing the acquisition.
DEFAULT_TOLERANCE_HOURS = 3.0


class Era5Error(Exception):
    """Raised when a NetCDF file cannot serve as wind forcing."""


@dataclass
class WindWindow:
    """A latitude/longitude sub-window of the 10 m wind field at one hour."""

    lats: np.ndarray
    lons: np.ndarray
    u: np.ndarray  # (lat, lon) m/s
    v: np.ndarray
    valid: np.ndarray  # bool, True where both components are finite
    time_utc: str | None
    time_index: int = 0

    def stats(self) -> dict[str, Any]:
        finite = self.valid
        if not finite.any():
            return {
                "cells": int(self.valid.size),
                "validCells": 0,
                "speedMeanMs": None,
                "meanFromDirectionDeg": None,
            }
        speed = np.hypot(self.u[finite], self.v[finite])
        # Direction is reported the way a met report reads it: the bearing the wind
        # blows *from*. The vector itself points where the air is going, so this is
        # the reverse of atan2(u, v), and mixing the two conventions up would put the
        # oil on the wrong side of the release point.
        u_mean = float(self.u[finite].mean())
        v_mean = float(self.v[finite].mean())
        from_deg = (np.degrees(np.arctan2(-u_mean, -v_mean)) + 360.0) % 360.0
        return {
            "cells": int(self.valid.size),
            "validCells": int(finite.sum()),
            "speedMinMs": round(float(speed.min()), 3),
            "speedMaxMs": round(float(speed.max()), 3),
            "speedMeanMs": round(float(speed.mean()), 3),
            "meanUMs": round(u_mean, 3),
            "meanVMs": round(v_mean, 3),
            "meanFromDirectionDeg": round(float(from_deg), 1),
        }


class Era5Wind:
    """Reader for an ERA5 single-level 10 m wind file."""

    def __init__(self, path: str | Path, file: Any | None = None):
        self.path = str(path)
        # Injectable for the same reason as the current reader: nothing here can write
        # NetCDF-4, so multi-hour behaviour is only testable through a fake handle.
        self._file = file if file is not None else NetCDF4File(self.path)
        self._vars = self._file.variables()
        self.u_name = self._pick(U10_NAMES)
        self.v_name = self._pick(V10_NAMES)
        self.lat_name = self._pick(LAT_NAMES)
        self.lon_name = self._pick(LON_NAMES)
        self.time_name = self._pick(TIME_NAMES)
        missing = [
            label
            for label, value in (
                ("10 m u", self.u_name), ("10 m v", self.v_name),
                ("latitude", self.lat_name), ("longitude", self.lon_name),
            )
            if value is None
        ]
        if missing:
            raise Era5Error(
                f"{Path(self.path).name} lacks required variables: {', '.join(missing)}"
            )
        self.lats = np.asarray(self._file.read_variable(self.lat_name), dtype=np.float64)
        self.lons = np.asarray(self._file.read_variable(self.lon_name), dtype=np.float64)
        self.times = self._read_times()
        self._cache: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        self._expver_choice: int | None = None

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> "Era5Wind":
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
        return [
            epoch + timedelta(seconds=float(value) * scale)
            for value in raw.ravel()
            if np.isfinite(value)
        ]

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

    def normalise_lon(self, values: np.ndarray | float) -> np.ndarray:
        """Put longitudes into the convention this file uses.

        A global ERA5 grid runs 0 to 359.75, so a scene at -95 degrees has to be asked
        for as 265. A regional subset may equally well run -100 to -80, in which case
        nothing is shifted. The axis decides, not a constant.
        """
        arr = np.asarray(values, dtype=np.float64)
        lon_lo, lon_hi = self.lon_range
        if lon_hi > 180.0 and lon_lo >= 0.0:
            return np.where(arr < 0.0, arr + 360.0, arr)
        if lon_lo < 0.0:
            return np.where(arr > 180.0, arr - 360.0, arr)
        return arr

    def nearest_time_index(self, when: datetime | None) -> tuple[int, float | None]:
        """Index of the hour closest to ``when``, and that gap in hours."""
        if when is None or not self.times:
            return 0, None
        gaps = [abs((t - when).total_seconds()) for t in self.times]
        index = int(min(range(len(gaps)), key=gaps.__getitem__))
        return index, gaps[index] / 3600.0

    def bracketing_time_indices(self, when: datetime) -> tuple[int, int, float]:
        """The two hours either side of ``when``, and the weight of the second.

        Returns ``(before, after, weight)`` such that a field at ``when`` is
        ``(1 - weight) * field[before] + weight * field[after]``. Outside the file's
        range both indices collapse onto the nearest end, which clamps rather than
        extrapolates: an hour of ERA5 says nothing about the hour after the file stops.
        """
        if not self.times:
            return 0, 0, 0.0
        ordered = sorted(range(len(self.times)), key=lambda i: self.times[i])
        first, last = ordered[0], ordered[-1]
        if when <= self.times[first]:
            return first, first, 0.0
        if when >= self.times[last]:
            return last, last, 0.0
        for position in range(1, len(ordered)):
            lo, hi = ordered[position - 1], ordered[position]
            if self.times[lo] <= when <= self.times[hi]:
                span = (self.times[hi] - self.times[lo]).total_seconds()
                if span <= 0:
                    return lo, lo, 0.0
                return lo, hi, (when - self.times[lo]).total_seconds() / span
        return last, last, 0.0

    # -- field access ------------------------------------------------------
    def _read_full(self, name: str) -> np.ndarray:
        """Read one whole variable, refusing an unreasonably large one.

        The HDF5 layer reads whole datasets -- it cannot read a sub-box -- so this is
        the only granularity available, and an oversized request has to be refused with
        an actionable message rather than discovered as an OOM kill mid-case.
        """
        node = self._vars[name]
        total = 1
        for size in node.dims:
            total *= int(size)
        if total > MAX_ELEMENTS:
            raise Era5Error(
                f"{name} holds {total:,} values, over the {MAX_ELEMENTS:,} this reader "
                "will load; request a smaller ERA5 area or time range (the HDF5 layer "
                "reads whole variables, it cannot read a sub-box)"
            )
        return np.asarray(self._file.read_variable(name), dtype=np.float64)

    def _reduce(self, name: str, data: np.ndarray, time_index: int) -> np.ndarray:
        """Collapse a raw variable to the ``(lat, lon)`` field for one hour.

        Leading axes in front of ``(lat, lon)`` are: the time axis, identified by its
        length, and possibly ``expver``. For ``expver`` the finite slice is taken --
        an ERA5/ERA5T download carries both and exactly one of them holds data for any
        given hour, so index 0 is a coin flip that lands on NaN half the time.
        """
        shape = (self.lats.size, self.lons.size)
        n_times = len(self.times)
        picked_expver: int | None = None
        while data.ndim > 2:
            if data.shape[0] == n_times and n_times > 0:
                if not 0 <= time_index < data.shape[0]:
                    raise Era5Error(
                        f"{name}: time index {time_index} is outside the file's "
                        f"{data.shape[0]} hour(s)"
                    )
                data = data[time_index]
                n_times = 0  # the time axis is consumed; a later axis is not time
                continue
            # Not the time axis: choose the slice that actually holds numbers.
            best, best_finite = 0, -1
            for index in range(data.shape[0]):
                finite = int(np.isfinite(data[index]).sum())
                if finite > best_finite:
                    best, best_finite = index, finite
            picked_expver = best
            data = data[best]
        if data.shape != shape:
            raise Era5Error(f"{name} has shape {data.shape}, expected {shape}")
        self._expver_choice = picked_expver
        return data

    def _slice_at(self, name: str, time_index: int) -> np.ndarray:
        """One hour of one component, as a (lat, lon) array."""
        return self._reduce(name, self._read_full(name), time_index)

    def _load(self, time_index: int) -> tuple[np.ndarray, np.ndarray]:
        """Both components at one hour. Two hours are cached, which is what
        interpolation needs; a third eviction-free entry would grow without bound."""
        if time_index not in self._cache:
            if len(self._cache) >= 2:
                self._cache.pop(next(iter(self._cache)))
            self._cache[time_index] = (
                self._slice_at(self.u_name, time_index),
                self._slice_at(self.v_name, time_index),
            )
        return self._cache[time_index]

    def index_window(
        self, bounds: Sequence[float], pad_deg: float = 1.0
    ) -> tuple[slice, slice] | None:
        """Index slices covering ``bounds`` (west, south, east, north) + padding.

        The pad is wider than the current reader's because ERA5 at 0.25 degrees is
        coarse against a 20 km scene: without it a small footprint can select a single
        cell, and a single cell cannot be interpolated.
        """
        west, south, east, north = (float(v) for v in bounds)
        west, east = (float(v) for v in self.normalise_lon([west, east]))
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
        pad_deg: float = 1.0,
        when: datetime | None = None,
        time_index: int | None = None,
    ) -> WindWindow | None:
        """The wind field over a bounding box at one hour."""
        window = self.index_window(bounds, pad_deg)
        if window is None:
            return None
        lat_slice, lon_slice = window
        index = time_index if time_index is not None else self.nearest_time_index(when)[0]
        u_full, v_full = self._load(index)
        u = u_full[lat_slice, lon_slice]
        v = v_full[lat_slice, lon_slice]
        return WindWindow(
            lats=self.lats[lat_slice],
            lons=self.lons[lon_slice],
            u=u,
            v=v,
            valid=np.isfinite(u) & np.isfinite(v),
            time_utc=(
                self.times[index].strftime("%Y-%m-%dT%H:%M:%SZ")
                if index < len(self.times)
                else None
            ),
            time_index=index,
        )

    def cadence_hours(self) -> float:
        """Median spacing of the time axis, in hours. Zero on a single-hour file."""
        if len(self.times) < 2:
            return 0.0
        ordered = sorted(self.times)
        gaps = [
            (b - a).total_seconds() / 3600.0
            for a, b in zip(ordered, ordered[1:])
            if (b - a).total_seconds() > 0
        ]
        return float(np.median(gaps)) if gaps else 0.0

    def window_series(
        self,
        bounds: Sequence[float],
        when: datetime,
        horizon_hours: float,
        pad_deg: float = 1.0,
    ) -> list[WindWindow]:
        """Every hour needed to force a drift of ``horizon_hours`` either side of ``when``.

        One read per component, not one per hour. ``_slice_at`` reads a whole variable
        each call and the hour cache holds two entries, so asking for a 24 h horizon an
        hour at a time would decode the same file fifty times.

        The span is padded by one cadence step at each end so a query at the very edge
        of the horizon is still bracketed by two hours rather than clamped.
        """
        window = self.index_window(bounds, pad_deg)
        if window is None or not self.times:
            return []
        lat_slice, lon_slice = window
        slack = timedelta(hours=abs(float(horizon_hours)) + max(self.cadence_hours(), 1.0))
        lo, hi = when - slack, when + slack
        indices = [i for i, t in enumerate(self.times) if lo <= t <= hi]
        if not indices:
            indices = [self.nearest_time_index(when)[0]]
        raw_u = self._read_full(self.u_name)
        raw_v = self._read_full(self.v_name)
        out: list[WindWindow] = []
        for index in sorted(indices, key=lambda i: self.times[i]):
            u = self._reduce(self.u_name, raw_u, index)[lat_slice, lon_slice]
            v = self._reduce(self.v_name, raw_v, index)[lat_slice, lon_slice]
            out.append(
                WindWindow(
                    lats=self.lats[lat_slice],
                    lons=self.lons[lon_slice],
                    u=u,
                    v=v,
                    valid=np.isfinite(u) & np.isfinite(v),
                    time_utc=self.times[index].strftime("%Y-%m-%dT%H:%M:%SZ"),
                    time_index=index,
                )
            )
        return out

    # -- overlap verdict ---------------------------------------------------
    def overlap(
        self,
        bounds: Sequence[float],
        when: datetime | None,
        max_time_gap_hours: float = DEFAULT_TOLERANCE_HOURS,
        pad_deg: float = 1.0,
        horizon_hours: float = 0.0,
    ) -> dict[str, Any]:
        """Decide whether this file may be used as wind forcing for a scene.

        ``horizon_hours`` is the drift horizon. A file that covers the acquisition hour
        but stops an hour later cannot force a 24 h hindcast without being clamped, and
        the caller deserves to know that before the trajectory is drawn rather than
        after, so the covered fraction of the run is reported.
        """
        west, south, east, north = (float(v) for v in bounds)
        query_west, query_east = (float(v) for v in self.normalise_lon([west, east]))
        lat_lo, lat_hi = self.lat_range
        lon_lo, lon_hi = self.lon_range
        inside = (
            lon_lo <= query_west and query_east <= lon_hi
            and lat_lo <= south and north <= lat_hi
        )
        window = self.window(bounds, pad_deg, when) if inside else None
        valid_cells = int(window.valid.sum()) if window else 0
        spatial_ok = bool(inside and window is not None and valid_cells > 0)

        index, gap_hours = self.nearest_time_index(when)
        nearest = self.times[index] if self.times and when is not None else None
        temporal_ok = gap_hours is not None and gap_hours <= max_time_gap_hours

        covered = None
        if when is not None and self.times and horizon_hours:
            span = self.time_range
            assert span is not None
            wanted_start = when - timedelta(hours=abs(horizon_hours))
            wanted_end = when + timedelta(hours=abs(horizon_hours))
            overlap_start = max(span[0], wanted_start)
            overlap_end = min(span[1], wanted_end)
            wanted = (wanted_end - wanted_start).total_seconds()
            have = max(0.0, (overlap_end - overlap_start).total_seconds())
            covered = round(have / wanted, 4) if wanted > 0 else None

        reasons: list[str] = []
        notes: list[str] = []
        if not inside:
            reasons.append("scene footprint is outside the wind grid")
        elif valid_cells == 0:
            reasons.append("wind file has no finite cells over the scene footprint")
        if when is None:
            reasons.append("scene acquisition time is unknown")
        elif not self.times:
            reasons.append("wind file carries no readable time axis")
        elif not temporal_ok:
            assert gap_hours is not None
            reasons.append(
                f"nearest wind hour is {gap_hours:.1f} h from the acquisition, beyond "
                f"the {max_time_gap_hours:.0f} h tolerance"
            )
        if covered is not None and covered < 1.0:
            notes.append(
                f"the file covers {covered:.0%} of the {abs(horizon_hours):.0f} h "
                "window either side of the acquisition; outside it the nearest "
                "available hour is held constant"
            )

        return {
            "product": Path(self.path).name,
            "variable": "10 m wind",
            "spatialOverlap": spatial_ok,
            "temporalOverlap": temporal_ok,
            "usable": bool(spatial_ok and temporal_ok),
            "sceneBounds": [west, south, east, north],
            "queryLonRange": [round(query_west, 6), round(query_east, 6)],
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
            "nearestProductTimeIndex": index if nearest else None,
            "timeGapHours": round(gap_hours, 2) if gap_hours is not None else None,
            "timeToleranceHours": max_time_gap_hours,
            "horizonCoverageFraction": covered,
            "validCellsOverScene": valid_cells,
            "reasons": reasons,
            "notes": notes,
            "windowStats": window.stats() if window else None,
        }

    def describe(self) -> dict[str, Any]:
        dlat, dlon = self.resolution()
        span = self.time_range
        return {
            # Repo-relative wherever the file sits inside the repository: this
            # description reaches the committed audit and the bundled case payloads.
            "path": config.display_path(self.path),
            "file": Path(self.path).name,
            "variables": {"u": self.u_name, "v": self.v_name},
            "gridShape": [int(self.lats.size), int(self.lons.size)],
            "latRange": [round(v, 6) for v in self.lat_range],
            "lonRange": [round(v, 6) for v in self.lon_range],
            "resolutionDeg": [round(dlat, 6), round(dlon, 6)],
            "timesUtc": _time_list(self.times),
            "timeStepCount": len(self.times),
            "timeRangeUtc": (
                [span[0].strftime("%Y-%m-%dT%H:%M:%SZ"), span[1].strftime("%Y-%m-%dT%H:%M:%SZ")]
                if span
                else None
            ),
            "expverSliceChosen": self._expver_choice,
            "height": "10 m above the surface, which is the height ERA5 reports",
        }


def open_default() -> "Era5Wind | None":
    """Open the ERA5 wind file supplied with the repository, if there is one.

    There is no such file in the repository as shipped, and that is not a failure: the
    Copernicus Data Store requires an account, so the download is the operator's step.
    ``SPILLTRACE_ERA5`` points at one; otherwise ``data/raw/era5_wind*.nc`` is used.
    """
    path = config.era5_path()
    if path is None:
        return None
    try:
        return Era5Wind(path)
    except (Era5Error, OSError, ValueError, KeyError):
        return None
