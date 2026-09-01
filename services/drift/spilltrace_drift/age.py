"""How old the spill was when the satellite saw it.

The problem statement asks for the spill's age, and the honest answer from one SAR
acquisition is an interval, not a number. This module computes that interval from the
backward drift run and -- more importantly -- computes whether the run can distinguish
one end of it from the other.

The test it applies is geometric. A hindcast can tell a 6-hour-old slick from a
24-hour-old one only if the oil's estimated position at those two lookbacks differs by
more than the uncertainty in that position. So for every step of the backward run we
compare how far the particle cloud's centre has moved from the observed centroid against
the P90 radius of the cloud at that step. The first lookback where the displacement
exceeds the radius is the point from which age becomes resolvable; if no lookback inside
the horizon reaches it, the run cannot narrow the age at all, and this module says so
instead of quoting the midpoint as though it were an estimate.

For the demo case the answer is that it cannot: over 24 h the estimated position moves
about 8.5 km while the P90 radius is about 11.3 km, so every release time inside the
window remains consistent with the image. That is a real finding about what a single
acquisition can support, and it is the sort of thing this product exists to state plainly
rather than paper over.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Sequence

from .ais import haversine_km, iso_utc, parse_utc

# A lookback is treated as distinguishable from the observation when the estimated
# position has moved further than the P90 radius of the uncertainty around it. Anything
# closer than that is inside its own error bar.
RESOLUTION_RADII = 1.0

UNRESOLVED_NOTE = (
    "The hindcast cannot narrow this further. Over the whole run the estimated position "
    "of the oil moves less than the P90 radius of the uncertainty around it, so every "
    "release time inside the window is equally consistent with this one image."
)

NARROWED_BY = (
    "a second acquisition of the same area, which fixes the drift actually observed "
    "rather than the drift modelled",
    "licensed metocean currents and winds for this scene and these hours, replacing the "
    "synthetic forcing",
    "an earlier acquisition showing the area clear of oil, which bounds the age from "
    "below directly",
)


def _centroid(step: dict[str, Any]) -> tuple[float, float] | None:
    centroid = step.get("centroid")
    if not centroid or len(centroid) < 2:
        return None
    return float(centroid[0]), float(centroid[1])


def _resolution(timeline: Sequence[dict[str, Any]]) -> tuple[float | None, dict[str, Any]]:
    """The earliest lookback the hindcast can tell apart from the acquisition.

    Returns the lookback in hours -- positive, counting back from the observation -- or
    `None` when no step inside the horizon separates from the observed position by more
    than its own P90 radius.
    """
    origin = _centroid(timeline[0]) if timeline else None
    furthest = {"hours": 0.0, "displacementKm": 0.0, "radiusKm": 0.0, "ratio": 0.0}
    if origin is None:
        return None, furthest

    resolvable: float | None = None
    for step in timeline:
        point = _centroid(step)
        if point is None:
            continue
        radius = float(step.get("spreadP90Km") or 0.0)
        if radius <= 0.0:
            continue
        hours = abs(float(step.get("hoursFromObservation") or 0.0))
        displacement = haversine_km(origin[0], origin[1], point[0], point[1])
        ratio = displacement / radius
        if ratio > furthest["ratio"]:
            furthest = {
                "hours": round(hours, 2),
                "displacementKm": round(displacement, 3),
                "radiusKm": round(radius, 3),
                "ratio": round(ratio, 3),
            }
        if resolvable is None and ratio >= RESOLUTION_RADII:
            resolvable = hours
    return resolvable, furthest


def estimate(backward: dict[str, Any]) -> dict[str, Any]:
    """The spill's age at acquisition, as an interval with its resolution stated.

    `minHours` is zero unless the hindcast can rule out a recent release, which one image
    cannot: nothing in a single scene says the oil was not put there minutes earlier.
    `maxHours` is the horizon, because the run looks no further back than that -- a wider
    interval would be a claim about oil the model never followed.
    """
    horizon = float(backward.get("horizonHours") or 0.0)
    timeline = backward.get("timeline") or []
    resolvable, furthest = _resolution(timeline)

    observed = backward.get("observedUtc")
    window_start = None
    if observed and horizon:
        try:
            window_start = iso_utc(parse_utc(observed) - timedelta(hours=horizon))
        except Exception:
            window_start = None

    min_hours = 0.0
    max_hours = round(horizon, 2)
    if min_hours <= 0.0:
        label = f"Estimated spill age: up to {max_hours:g} h at acquisition"
    else:  # pragma: no cover - reachable only once a lower bound has an observation behind it
        label = f"Estimated spill age: {min_hours:g}-{max_hours:g} h at acquisition"

    resolution: dict[str, Any] = {
        "resolvable": resolvable is not None,
        "fromHours": None if resolvable is None else round(resolvable, 2),
        "test": (
            "a lookback is distinguishable from the acquisition when the estimated position "
            f"has moved more than {RESOLUTION_RADII:g} P90 radius from the observed centroid"
        ),
        "bestSeparation": furthest,
    }
    if resolvable is None:
        resolution["note"] = UNRESOLVED_NOTE
    else:
        resolution["note"] = (
            f"positions from {resolvable:g} h back separate from the observed centroid by more "
            f"than their own P90 radius, so the age can be narrowed to that side of the window"
        )

    return {
        "label": label,
        "minHours": min_hours,
        "maxHours": max_hours,
        "acquiredUtc": observed,
        "earliestReleaseUtc": window_start,
        "basis": (
            f"the {max_hours:g} h backward-drift horizon: oil seen at the acquisition could "
            f"have entered the water at any time within it"
        ),
        "resolution": resolution,
        "narrowedBy": list(NARROWED_BY),
        "caveat": (
            "The interval is bounded by the drift horizon, not measured. It inherits every "
            "limitation of the forcing it was computed from."
        ),
    }
