"""Phase 5b: backward and forward particle drift.

Two questions, one integrator:

* **Backward** - the slick was seen *here*, at this time. Where was this oil in the
  hours before the image? The answer is a region, not a point, and it is what the
  vessel-scoring stage searches.
* **Forward** - where will it go next? That is the response-planning question.

Design decisions that keep the output honest:

* **Particles are seeded from the slick's own pixels**, not from its centroid. A
  20 km filament does not originate at one point, and seeding from the footprint
  preserves that.
* **A backward run is not the forward run reversed.** Diffusion is a random walk;
  running one backwards gives a *plausible origin envelope*, not the trajectory
  the oil actually took. Every backward payload says so.
* **Uncertainty is reported, and it grows.** Each timestep records the particle
  spread, so the envelope widens visibly with distance from the observation
  instead of presenting one confident line.
* **Beaching and domain exit are flags, not silent clamps.** A particle that hits
  the land mask stops and is counted.

Numerically this is an RK2 midpoint scheme, which halves the truncation error of
explicit Euler for the same number of field evaluations per unit accuracy, on a
30 minute step - small enough that a 0.25 m/s particle moves 450 m per step,
well under the scale of the flow features.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

import numpy as np

from spilltrace_common import config as C
from spilltrace_common.geotiff import Affine

from .forcing import DEG, Forcing

BACKWARD_CAVEAT = (
    "Diffusion is a random walk, so a backward run does not reverse the path the oil "
    "actually took. It maps the region the observed slick could plausibly have come "
    "from, and that region widens with time before the image."
)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def convex_hull_indices(points: np.ndarray) -> list[int]:
    """Andrew's monotone chain. ``points`` is (N, 2) in planar units.

    Returns indices into ``points``, counter-clockwise, without a repeated first
    vertex. Particle counts here are in the hundreds, so the O(n log n) sort
    dominates and no spatial structure is warranted.
    """
    values = np.asarray(points, dtype=np.float64)
    if values.shape[0] == 0:
        return []
    order = np.lexsort((values[:, 1], values[:, 0]))
    unique: list[int] = []
    for index in order:
        if unique and np.allclose(values[index], values[unique[-1]], atol=1e-9):
            continue
        unique.append(int(index))
    if len(unique) < 3:
        return unique

    def cross(o: int, a: int, b: int) -> float:
        return float(
            (values[a, 0] - values[o, 0]) * (values[b, 1] - values[o, 1])
            - (values[a, 1] - values[o, 1]) * (values[b, 0] - values[o, 0])
        )

    lower: list[int] = []
    for index in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], index) <= 0:
            lower.pop()
        lower.append(index)
    upper: list[int] = []
    for index in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], index) <= 0:
            upper.pop()
        upper.append(index)
    hull = lower[:-1] + upper[:-1]
    return hull if len(hull) >= 3 else unique


def _circle_ring(lon0: float, lat0: float, radius_m: float, steps: int = 24) -> list[list[float]]:
    """A closed ring approximating a circle, for degenerate hulls."""
    per_lon = DEG * C.EARTH_RADIUS_M * max(math.cos(lat0 * DEG), 1e-6)
    per_lat = DEG * C.EARTH_RADIUS_M
    ring = []
    for index in range(steps):
        angle = 2.0 * math.pi * index / steps
        ring.append(
            [
                round(lon0 + radius_m * math.cos(angle) / per_lon, 8),
                round(lat0 + radius_m * math.sin(angle) / per_lat, 8),
            ]
        )
    ring.append(list(ring[0]))
    return ring


def hull_ring(
    lon: np.ndarray, lat: np.ndarray, lat0: float, min_radius_m: float = 60.0
) -> tuple[list[list[float]], str]:
    """Convex hull of scattered lon/lat points, as a closed counter-clockwise ring.

    The hull is computed in local metres rather than in degrees: at 60 degrees
    latitude a degree of longitude is half a degree of latitude, and hulling the
    raw coordinates would pick different vertices than the true geometry has.
    """
    lon = np.asarray(lon, dtype=np.float64)
    lat = np.asarray(lat, dtype=np.float64)
    if lon.size == 0:
        return [], "no particles"
    per_lon = DEG * C.EARTH_RADIUS_M * max(math.cos(lat0 * DEG), 1e-6)
    per_lat = DEG * C.EARTH_RADIUS_M
    local = np.stack([lon * per_lon, lat * per_lat], axis=1)
    hull = convex_hull_indices(local)
    if len(hull) >= 3:
        ring = [[round(float(lon[i]), 8), round(float(lat[i]), 8)] for i in hull]
        ring.append(list(ring[0]))
        return ring, "convex hull"
    centre_lon = float(lon.mean())
    centre_lat = float(lat.mean())
    spread = float(np.hypot((lon - centre_lon) * per_lon, (lat - centre_lat) * per_lat).max())
    return (
        _circle_ring(centre_lon, centre_lat, max(spread, min_radius_m)),
        "particles are effectively collocated, so the envelope is a nominal circle",
    )


def distances_m(
    lon: np.ndarray, lat: np.ndarray, lon0: float, lat0: float
) -> np.ndarray:
    """Great-circle distance from one reference point, vectorised."""
    p1 = lat0 * DEG
    p2 = np.asarray(lat, dtype=np.float64) * DEG
    dlon = (np.asarray(lon, dtype=np.float64) - lon0) * DEG
    h = (
        np.sin((p2 - p1) / 2.0) ** 2
        + math.cos(p1) * np.cos(p2) * np.sin(dlon / 2.0) ** 2
    )
    return 2.0 * C.EARTH_RADIUS_M * np.arcsin(np.minimum(1.0, np.sqrt(h)))


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def seed_from_mask(
    mask: np.ndarray, transform: Affine, count: int, seed: int
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Sample ``count`` particle positions uniformly from a slick mask's pixels."""
    rows, cols = np.nonzero(np.asarray(mask, dtype=bool))
    if rows.size == 0:
        raise ValueError("cannot seed particles: the mask is empty")
    rng = np.random.default_rng(seed)
    replace = rows.size < count
    picked = rng.choice(rows.size, size=count, replace=replace)
    # Jitter within the pixel so a small slick does not produce duplicate points.
    jitter_r = rng.random(count)
    jitter_c = rng.random(count)
    lon, lat = transform.apply_array(cols[picked] + jitter_c, rows[picked] + jitter_r)
    info = {
        "method": "uniform sample of the slick's own mask pixels",
        "maskPixels": int(rows.size),
        "particles": int(count),
        "withReplacement": bool(replace),
        "note": (
            "particles start spread across the observed footprint rather than at its "
            "centroid, because a slick does not originate from a single point"
        ),
    }
    return np.asarray(lon, dtype=np.float64), np.asarray(lat, dtype=np.float64), info


def seed_from_point(
    lon0: float, lat0: float, radius_m: float, count: int, seed: int
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Sample ``count`` positions uniformly inside a disc, for point releases."""
    rng = np.random.default_rng(seed)
    radius = radius_m * np.sqrt(rng.random(count))  # sqrt gives uniform area density
    angle = rng.uniform(0.0, 2.0 * math.pi, size=count)
    per_lon = DEG * C.EARTH_RADIUS_M * max(math.cos(lat0 * DEG), 1e-6)
    per_lat = DEG * C.EARTH_RADIUS_M
    return (
        lon0 + radius * np.cos(angle) / per_lon,
        lat0 + radius * np.sin(angle) / per_lat,
        {
            "method": "uniform disc around a point release",
            "radiusM": round(float(radius_m), 1),
            "particles": int(count),
        },
    )


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


def _parse_time(when: str | datetime) -> datetime:
    if isinstance(when, datetime):
        return when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(when).strip().replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass
class _State:
    lon: np.ndarray
    lat: np.ndarray
    active: np.ndarray
    beached: np.ndarray
    exited: np.ndarray


def _step(
    forcing: Forcing,
    state: _State,
    seconds: float,
    dt: float,
    diffusion_m: float,
    rng: np.random.Generator,
    domain: Sequence[float] | None,
) -> None:
    """One RK2 midpoint step with an isotropic random walk, in place."""
    live = state.active
    if not live.any():
        return
    lon = state.lon[live]
    lat = state.lat[live]
    per_lon = DEG * C.EARTH_RADIUS_M * np.maximum(np.cos(lat * DEG), 1e-6)
    per_lat = DEG * C.EARTH_RADIUS_M

    u1, v1 = forcing.velocity(lon, lat, seconds)
    mid_lon = lon + 0.5 * dt * np.asarray(u1, dtype=np.float64) / per_lon
    mid_lat = lat + 0.5 * dt * np.asarray(v1, dtype=np.float64) / per_lat
    u2, v2 = forcing.velocity(mid_lon, mid_lat, seconds + 0.5 * dt)

    walk_east = rng.normal(0.0, diffusion_m, size=lon.shape)
    walk_north = rng.normal(0.0, diffusion_m, size=lon.shape)
    new_lon = lon + (dt * np.asarray(u2, dtype=np.float64) + walk_east) / per_lon
    new_lat = lat + (dt * np.asarray(v2, dtype=np.float64) + walk_north) / per_lat
    new_lat = np.clip(new_lat, -89.9, 89.9)

    water = np.asarray(forcing.is_water(new_lon, new_lat), dtype=bool)
    inside = np.ones(new_lon.shape, dtype=bool)
    if domain is not None:
        inside = (
            (new_lon >= domain[0])
            & (new_lon <= domain[2])
            & (new_lat >= domain[1])
            & (new_lat <= domain[3])
        )

    moved = water & inside
    lon_out = np.where(moved, new_lon, lon)
    lat_out = np.where(moved, new_lat, lat)

    indices = np.flatnonzero(live)
    state.lon[indices] = lon_out
    state.lat[indices] = lat_out
    state.beached[indices[~water]] = True
    state.exited[indices[water & ~inside]] = True
    state.active[indices[~moved]] = False


def simulate(
    forcing: Forcing,
    seed_lon: np.ndarray,
    seed_lat: np.ndarray,
    observed_utc: str | datetime,
    direction: str = "backward",
    cfg: C.DriftConfig | None = None,
    seeding: dict[str, Any] | None = None,
    track_sample: int = 40,
) -> dict[str, Any]:
    """Advect the seeded particles and report the envelope and its uncertainty."""
    cfg = cfg or C.DriftConfig()
    if direction not in ("backward", "forward"):
        raise ValueError(f"direction must be 'backward' or 'forward', got {direction!r}")
    seed_lon = np.asarray(seed_lon, dtype=np.float64).ravel()
    seed_lat = np.asarray(seed_lat, dtype=np.float64).ravel()
    if seed_lon.size == 0 or seed_lon.size != seed_lat.size:
        raise ValueError("seed positions must be non-empty and the same length")

    observed = _parse_time(observed_utc)
    sign = -1.0 if direction == "backward" else 1.0
    dt = sign * cfg.time_step_minutes * 60.0
    steps = int(round(cfg.horizon_hours * 60.0 / cfg.time_step_minutes))
    # Random-walk standard deviation per step for diffusivity K: sqrt(2*K*dt).
    diffusion_m = math.sqrt(2.0 * cfg.diffusion_m2_s * abs(dt))

    origin_lon = float(seed_lon.mean())
    origin_lat = float(seed_lat.mean())
    rng = np.random.default_rng(C.stable_seed(f"{direction}:{origin_lon:.4f}:{origin_lat:.4f}", cfg.seed))

    state = _State(
        lon=seed_lon.copy(),
        lat=seed_lat.copy(),
        active=np.ones(seed_lon.size, dtype=bool),
        beached=np.zeros(seed_lon.size, dtype=bool),
        exited=np.zeros(seed_lon.size, dtype=bool),
    )
    domain = forcing.domain_bounds() if hasattr(forcing, "domain_bounds") else None

    history_lon = np.zeros((steps + 1, seed_lon.size), dtype=np.float32)
    history_lat = np.zeros((steps + 1, seed_lon.size), dtype=np.float32)
    history_lon[0] = state.lon
    history_lat[0] = state.lat
    timeline: list[dict[str, Any]] = []

    def snapshot(index: int) -> dict[str, Any]:
        hours = sign * index * cfg.time_step_minutes / 60.0
        lon = history_lon[index].astype(np.float64)
        lat = history_lat[index].astype(np.float64)
        centre_lon = float(lon.mean())
        centre_lat = float(lat.mean())
        spread = distances_m(lon, lat, centre_lon, centre_lat)
        travel = distances_m(lon, lat, origin_lon, origin_lat)
        return {
            "stepIndex": index,
            "hoursFromObservation": round(hours, 3),
            "timeUtc": (observed + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "centroid": [round(centre_lon, 6), round(centre_lat, 6)],
            "spreadP50Km": round(float(np.percentile(spread, 50)) / 1000.0, 4),
            "spreadP90Km": round(float(np.percentile(spread, 90)) / 1000.0, 4),
            "spreadMaxKm": round(float(spread.max()) / 1000.0, 4),
            "displacementMeanKm": round(float(travel.mean()) / 1000.0, 4),
            "displacementMaxKm": round(float(travel.max()) / 1000.0, 4),
            "activeParticles": int(state.active.sum()),
            "beachedParticles": int(state.beached.sum()),
            "exitedParticles": int(state.exited.sum()),
        }

    timeline.append(snapshot(0))
    for index in range(1, steps + 1):
        _step(
            forcing,
            state,
            seconds=sign * (index - 1) * cfg.time_step_minutes * 60.0,
            dt=dt,
            diffusion_m=diffusion_m,
            rng=rng,
            domain=domain,
        )
        history_lon[index] = state.lon
        history_lat[index] = state.lat
        timeline.append(snapshot(index))

    final_lon = history_lon[-1].astype(np.float64)
    final_lat = history_lat[-1].astype(np.float64)

    # Envelopes at four points along the horizon, so the widening is visible.
    checkpoints = []
    for fraction in (0.25, 0.5, 0.75, 1.0):
        index = max(1, int(round(fraction * steps)))
        ring, note = hull_ring(
            history_lon[index].astype(np.float64),
            history_lat[index].astype(np.float64),
            origin_lat,
        )
        checkpoints.append(
            {
                "hoursFromObservation": timeline[index]["hoursFromObservation"],
                "timeUtc": timeline[index]["timeUtc"],
                "fractionOfHorizon": fraction,
                "ring": ring,
                "ringNote": note,
                "areaKm2": round(_ring_area_km2(ring, origin_lat), 4),
            }
        )

    corridor_lon = history_lon.reshape(-1).astype(np.float64)
    corridor_lat = history_lat.reshape(-1).astype(np.float64)
    corridor_ring, corridor_note = hull_ring(corridor_lon, corridor_lat, origin_lat)

    stride = max(1, int(math.ceil(seed_lon.size / max(1, track_sample))))
    tracks = []
    for particle in range(0, seed_lon.size, stride):
        tracks.append(
            {
                "particle": int(particle),
                "beached": bool(state.beached[particle]),
                "exited": bool(state.exited[particle]),
                "path": [
                    [round(float(history_lon[t, particle]), 6), round(float(history_lat[t, particle]), 6)]
                    for t in range(steps + 1)
                ],
            }
        )

    end_spread = distances_m(final_lon, final_lat, float(final_lon.mean()), float(final_lat.mean()))
    travelled = distances_m(final_lon, final_lat, origin_lon, origin_lat)

    result: dict[str, Any] = {
        "pipelineVersion": C.PIPELINE_VERSION,
        "generatedUtc": C.utc_now_iso(),
        "direction": direction,
        "label": forcing.label,
        "status": C.LABEL_STATUS,
        "observedUtc": observed.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "horizonHours": cfg.horizon_hours,
        "config": cfg.to_dict(),
        "numerics": {
            "scheme": "RK2 midpoint advection with an additive isotropic random walk",
            "timeStepMinutes": cfg.time_step_minutes,
            "steps": steps,
            "diffusivityM2S": cfg.diffusion_m2_s,
            "randomWalkStdPerStepM": round(diffusion_m, 2),
            "expectedDiffusiveSpreadKm": round(
                math.sqrt(steps) * diffusion_m / 1000.0, 3
            ),
            "note": (
                "the random walk is seeded deterministically from the release position "
                "and direction, so the same case reproduces exactly"
            ),
        },
        "seeding": {**(seeding or {}), "centroid": [round(origin_lon, 6), round(origin_lat, 6)]},
        "forcing": forcing.describe(),
        "timeline": timeline,
        "envelopes": checkpoints,
        "searchCorridor": {
            "ring": corridor_ring,
            "ringNote": corridor_note,
            "areaKm2": round(_ring_area_km2(corridor_ring, origin_lat), 4),
            "meaning": (
                "the convex hull of every particle position at every timestep - the area "
                "an operator would have to search, not a prediction of where the oil is"
            ),
        },
        "endpoints": {
            "count": int(final_lon.size),
            "centroid": [round(float(final_lon.mean()), 6), round(float(final_lat.mean()), 6)],
            "spreadP50Km": round(float(np.percentile(end_spread, 50)) / 1000.0, 4),
            "spreadP90Km": round(float(np.percentile(end_spread, 90)) / 1000.0, 4),
            "displacementMeanKm": round(float(travelled.mean()) / 1000.0, 4),
            "displacementP90Km": round(float(np.percentile(travelled, 90)) / 1000.0, 4),
            "positions": [
                [round(float(a), 6), round(float(b), 6)] for a, b in zip(final_lon, final_lat)
            ],
        },
        "particleOutcomes": {
            "released": int(seed_lon.size),
            "stillDrifting": int(state.active.sum()),
            "beached": int(state.beached.sum()),
            "leftForcingDomain": int(state.exited.sum()),
        },
        "tracks": tracks,
        "trackNote": (
            f"{len(tracks)} of {seed_lon.size} particle paths are returned for display; "
            "all particles contribute to the envelopes and statistics"
        ),
        "uncertaintyBasis": (
            "spread is the spatial dispersion of the particle cloud under the stated "
            "diffusivity and forcing; it does not include uncertainty in the forcing "
            "itself, which is the dominant unknown here"
        ),
    }

    # The land mask over the area the run actually covered, so the dashboard can draw the
    # beaching surface. Bounds come from the corridor rather than the scene, because a
    # 24 h backward run routinely ends well outside the image.
    corridor_lons = [point[0] for point in corridor_ring] or [origin_lon]
    corridor_lats = [point[1] for point in corridor_ring] or [origin_lat]
    result["landMaskCells"] = forcing.land_cells(
        [min(corridor_lons), min(corridor_lats), max(corridor_lons), max(corridor_lats)]
    )

    if direction == "backward":
        result["originEstimate"] = {
            "centroid": result["endpoints"]["centroid"],
            "radiusP50Km": result["endpoints"]["spreadP50Km"],
            "radiusP90Km": result["endpoints"]["spreadP90Km"],
            "hoursBeforeObservation": cfg.horizon_hours,
            "interpretation": (
                "the centre of the plausible origin region for the observed slick, "
                f"{cfg.horizon_hours} h before the acquisition"
            ),
        }
        result["caveat"] = BACKWARD_CAVEAT
    else:
        result["caveat"] = (
            "a forecast under synthetic forcing shows how the slick would evolve in a "
            "plausible ocean, not what will happen in the real one"
            if forcing.mode == "synthetic"
            else "a forecast is only as good as the forcing that drives it"
        )
    return result


def _ring_area_km2(ring: Sequence[Sequence[float]], lat0: float) -> float:
    """Planar shoelace area of a lon/lat ring, projected to local metres."""
    if len(ring) < 4:
        return 0.0
    per_lon = DEG * C.EARTH_RADIUS_M * max(math.cos(lat0 * DEG), 1e-6)
    per_lat = DEG * C.EARTH_RADIUS_M
    total = 0.0
    for index in range(len(ring) - 1):
        x1, y1 = ring[index][0] * per_lon, ring[index][1] * per_lat
        x2, y2 = ring[index + 1][0] * per_lon, ring[index + 1][1] * per_lat
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0 / 1e6


def to_geojson(result: dict[str, Any]) -> dict[str, Any]:
    """Envelopes, corridor and sampled tracks as one drawable FeatureCollection."""
    features: list[dict[str, Any]] = []
    corridor = result.get("searchCorridor") or {}
    if len(corridor.get("ring") or []) >= 4:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [corridor["ring"]]},
                "properties": {
                    "kind": "searchCorridor",
                    "direction": result["direction"],
                    "areaKm2": corridor.get("areaKm2"),
                    "label": result["label"],
                },
            }
        )
    for envelope in result.get("envelopes") or []:
        if len(envelope.get("ring") or []) < 4:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [envelope["ring"]]},
                "properties": {
                    "kind": "envelope",
                    "direction": result["direction"],
                    "hoursFromObservation": envelope["hoursFromObservation"],
                    "timeUtc": envelope["timeUtc"],
                    "areaKm2": envelope.get("areaKm2"),
                    "label": result["label"],
                },
            }
        )
    for track in result.get("tracks") or []:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": track["path"]},
                "properties": {
                    "kind": "track",
                    "direction": result["direction"],
                    "particle": track["particle"],
                    "beached": track["beached"],
                },
            }
        )
    centroid = (result.get("endpoints") or {}).get("centroid")
    if centroid:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": centroid},
                "properties": {
                    "kind": "originEstimate"
                    if result["direction"] == "backward"
                    else "forecastCentroid",
                    "label": result["label"],
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}
