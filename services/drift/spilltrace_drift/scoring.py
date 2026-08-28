"""Explainable vessel scoring.

The score has one job: order a list of vessels for a human to look at, and say in plain
words why each one is where it is. It is a triage aid, not a finding. Every result is
labelled :data:`spilltrace_common.config.LABEL_CANDIDATE` -- "Priority candidate for
investigation" -- and no code path in this module can produce a stronger word than that.

The weights are fixed by PRD section 11 and are not tuned:

======================================  ====
component                               max
======================================  ====
distance from the estimated origin        30
presence during the release window        25
trajectory consistency                    20
speed / behaviour anomaly                 10
vessel type relevance                     10
data completeness                          5
======================================  ====

Two design choices matter more than the weights themselves.

**Distance and time are scored against the same object.** A backward drift run does not
produce one origin point; it produces, for every instant before the acquisition, a region
where the oil plausibly was. Distance is therefore measured to the envelope centre *for
the vessel's own timestamp*, normalised by that envelope's radius, and the time component
asks whether the closest approach happened while the vessel was inside the window at all.
Scoring distance against a single point and time against a separate interval would let a
vessel that was in the right place a day too late collect nearly full marks.

**Nothing is scored on unavailable data.** Where a component cannot be evaluated -- no
reports inside the window, no speed field -- it scores zero and the explanation says the
data was missing rather than that the vessel was cleared.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

import numpy as np

from spilltrace_common import config as C

from .ais import (
    EnvelopeTrack,
    SLOW_KN,
    STOPPED_KN,
    VESSEL_TYPES,
    bearing_deg,
    envelope_from_drift,
    haversine_km,
    iso_utc,
    parse_utc,
)

# A vessel is "in" the origin region when it is inside this many envelope radii. The P90
# radius already covers 90% of the particle cloud, so 1.0 is the natural boundary and
# 3.0 is the point past which proximity carries no information at all.
INSIDE_RADII = 1.0
IRRELEVANT_RADII = 3.0

# STOPPED_KN and SLOW_KN come from the AIS module so that a "stopped" navigational status
# in the feed and a "stopped" behaviour score always mean the same speed.

CAVEAT = (
    "This ranking is a triage aid computed from synthetic AIS and synthetic drift "
    "forcing. It is not evidence, it does not establish responsibility, and every entry "
    "requires human verification against licensed AIS and observed metocean data."
)


@dataclass(frozen=True)
class Approach:
    """The vessel's closest approach to the time-matched drift envelope."""

    distance_km: float | None
    radii: float | None  # distance expressed in envelope radii
    radius_km: float | None
    at_utc: str | None
    inside_window: bool
    reports_in_window: int
    reports_relevant: int  # in the window *and* within IRRELEVANT_RADII of the envelope
    reports_total: int
    window_minutes: float  # span of the relevant reports, not of the whole track
    nearest_outside_km: float | None
    course_deg: float | None
    sog_kn: float | None

    @property
    def proximity(self) -> float:
        """1.0 inside the envelope, falling to 0.0 at :data:`IRRELEVANT_RADII`.

        Every component that depends on the vessel having been anywhere near the oil is
        gated on this, so that no amount of the right course or the right hours can earn
        marks from the wrong place.
        """
        if self.radii is None:
            return 0.0
        span = IRRELEVANT_RADII - INSIDE_RADII
        return float(max(0.0, min(1.0, (IRRELEVANT_RADII - self.radii) / span)))


def _component(name: str, earned: float, maximum: int, reason: str) -> dict[str, Any]:
    earned = float(max(0.0, min(float(maximum), earned)))
    return {
        "component": name,
        "score": round(earned, 1),
        "max": maximum,
        "reason": reason,
    }


def closest_approach(reports: Sequence[dict[str, Any]], env: EnvelopeTrack) -> Approach:
    """Find where and when the vessel came nearest to the oil's own past position.

    Reports outside the drift horizon are tracked separately: they cannot support a
    release, but knowing the vessel passed 2 km away six hours late is worth saying.
    """
    best: tuple[float, dict[str, Any], float, int] | None = None
    best_outside: float | None = None
    in_window = 0
    relevant: list[datetime] = []

    for index, row in enumerate(reports):
        try:
            stamp = parse_utc(row["timeUtc"])
        except Exception:
            continue
        lon, lat = row.get("lon"), row.get("lat")
        if lon is None or lat is None:
            continue
        centre_lon, centre_lat, radius = env.at(stamp)
        distance = haversine_km(lon, lat, centre_lon, centre_lat)
        if env.covers(stamp):
            in_window += 1
            if distance <= IRRELEVANT_RADII * radius:
                relevant.append(stamp)
            if best is None or distance < best[0]:
                best = (distance, row, radius, index)
        elif best_outside is None or distance < best_outside:
            best_outside = distance

    window_minutes = 0.0
    if len(relevant) >= 2:
        window_minutes = (max(relevant) - min(relevant)).total_seconds() / 60.0

    if best is None:
        return Approach(
            distance_km=None,
            radii=None,
            radius_km=None,
            at_utc=None,
            inside_window=False,
            reports_in_window=0,
            reports_relevant=0,
            reports_total=len(reports),
            window_minutes=0.0,
            nearest_outside_km=round(best_outside, 3) if best_outside is not None else None,
            course_deg=None,
            sog_kn=None,
        )

    distance, row, radius, index = best
    # Course from the surrounding reports, not the declared field: a feed with a missing
    # or stale COG should not silently score as "no heading information".
    course = row.get("cogDeg")
    if course is None and len(reports) >= 2:
        a = reports[max(0, index - 1)]
        b = reports[min(len(reports) - 1, index + 1)]
        if a is not b:
            course = bearing_deg(a["lon"], a["lat"], b["lon"], b["lat"])

    return Approach(
        distance_km=round(distance, 3),
        radii=round(distance / max(radius, 1e-6), 3),
        radius_km=round(radius, 3),
        at_utc=row["timeUtc"],
        inside_window=True,
        reports_in_window=in_window,
        reports_relevant=len(relevant),
        reports_total=len(reports),
        window_minutes=round(window_minutes, 1),
        nearest_outside_km=round(best_outside, 3) if best_outside is not None else None,
        course_deg=None if course is None else round(float(course), 1),
        sog_kn=row.get("sogKn"),
    )


def score_distance(approach: Approach, weights: C.ScoringWeights) -> dict[str, Any]:
    """Proximity to the envelope centre at the vessel's own timestamp.

    Full marks inside the envelope, falling linearly to zero at three radii. Using radii
    rather than absolute kilometres means the score tightens automatically when the
    hindcast is confident and relaxes when it is not, which is the honest behaviour.
    """
    maximum = weights.distance
    if approach.radii is None:
        detail = (
            f"no reports inside the drift window; nearest pass was "
            f"{approach.nearest_outside_km} km away outside it"
            if approach.nearest_outside_km is not None
            else "no reports inside the drift window"
        )
        return _component("Distance", 0.0, maximum, detail)

    radii = approach.radii
    if radii <= INSIDE_RADII:
        earned = maximum
        reason = (
            f"passed {approach.distance_km} km from the drift envelope centre at "
            f"{approach.at_utc}, inside the {approach.radius_km} km envelope for that time"
        )
    elif radii >= IRRELEVANT_RADII:
        earned = 0.0
        reason = (
            f"closest pass was {approach.distance_km} km away, {radii:.1f} envelope radii "
            f"from where the oil is estimated to have been"
        )
    else:
        span = IRRELEVANT_RADII - INSIDE_RADII
        earned = maximum * (IRRELEVANT_RADII - radii) / span
        reason = (
            f"closest pass was {approach.distance_km} km away, {radii:.1f} envelope radii "
            f"from the estimated position at {approach.at_utc}"
        )
    return _component("Distance", earned, maximum, reason)


def score_time(approach: Approach, env: EnvelopeTrack, weights: C.ScoringWeights) -> dict[str, Any]:
    """How long the vessel was near the oil's estimated position during the window.

    "Presence during the release window" has to mean presence *at the scene*, not merely
    existence during those hours -- otherwise every ship in the ocean collects full marks
    for having its transponder on. Only reports that are both inside the window and within
    :data:`IRRELEVANT_RADII` of the time-matched envelope count. Half the component is for
    being there at all and half scales with duration, saturating at six hours.
    """
    maximum = weights.time_window
    if approach.reports_relevant == 0:
        if approach.reports_in_window:
            return _component(
                "Time",
                0.0,
                maximum,
                f"{approach.reports_in_window} reports fall inside the release window, but none "
                f"within reach of the estimated oil position at their own timestamps",
            )
        return _component(
            "Time",
            0.0,
            maximum,
            "no AIS reports fall inside the estimated release window",
        )

    presence = 0.5 * maximum
    hours = approach.window_minutes / 60.0
    duration = 0.5 * maximum * min(1.0, hours / 6.0)
    reason = (
        f"within reach of the estimated oil position for {hours:.1f} h during the release "
        f"window ({approach.reports_relevant} of {approach.reports_total} reports)"
    )
    if hours < 1.0:
        reason += "; a brief pass supports less than a sustained presence"
    return _component("Time", presence + duration, maximum, reason)


def score_trajectory(
    reports: Sequence[dict[str, Any]],
    approach: Approach,
    env: EnvelopeTrack,
    weights: C.ScoringWeights,
) -> dict[str, Any]:
    """Whether the track actually crosses the envelope, and how it is oriented.

    Two-thirds of the component is for intersecting the envelope at the matching time --
    the geometric fact that matters -- and one third for the vessel's course being roughly
    across the drift axis, the orientation that leaves a slick behind it rather than
    alongside. The orientation term is gated on proximity, so a parallel course fifty
    kilometres away earns nothing: a course is only informative once the vessel is close
    enough for the crossing to mean something.
    """
    maximum = weights.trajectory
    if approach.radii is None:
        return _component(
            "Trajectory",
            0.0,
            maximum,
            "the track never enters the drift envelope during the release window",
        )

    crossings = 0
    for row in reports:
        try:
            stamp = parse_utc(row["timeUtc"])
        except Exception:
            continue
        if not env.covers(stamp):
            continue
        centre_lon, centre_lat, radius = env.at(stamp)
        if haversine_km(row["lon"], row["lat"], centre_lon, centre_lat) <= radius:
            crossings += 1

    proximity = approach.proximity
    intersect_max = maximum * 2.0 / 3.0
    if crossings > 0:
        intersect = intersect_max
        geometry = f"track intersects the drift envelope for {crossings} consecutive reports"
    elif proximity > 0:
        intersect = intersect_max * proximity
        geometry = f"track skirts the envelope, closest approach {approach.radii:.1f} radii"
    else:
        intersect = 0.0
        geometry = f"track stays {approach.radii:.1f} envelope radii clear throughout"

    axis = bearing_deg(env.lons[0], env.lats[0], env.lons[-1], env.lats[-1])
    orient_max = maximum - intersect_max
    if proximity <= 0:
        orient = 0.0
        orientation = "course carries no information at that distance"
    elif approach.course_deg is None:
        orient = 0.0
        orientation = "no usable course over ground at the closest approach"
    else:
        delta = abs((approach.course_deg - axis + 180.0) % 360.0 - 180.0)
        # 90 degrees to the drift axis scores 1, parallel scores 0.
        alignment = 1.0 - abs(math.cos(delta * math.pi / 180.0))
        orient = orient_max * alignment * proximity
        orientation = (
            f"course {approach.course_deg:.0f} deg sits {delta:.0f} deg off the "
            f"{axis:.0f} deg drift axis"
        )

    return _component("Trajectory", intersect + orient, maximum, f"{geometry}; {orientation}")


def score_behaviour(
    reports: Sequence[dict[str, Any]],
    approach: Approach,
    weights: C.ScoringWeights,
) -> dict[str, Any]:
    """Speed anomalies near the closest approach.

    A discharge is easier at low speed, so a slowdown or stop while near the envelope is
    weak corroboration. It is scored small (10 of 100) on purpose: ships slow down for
    many innocent reasons, and treating a stop as strong evidence is exactly the error
    this component must not make.
    """
    maximum = weights.behaviour
    speeds = [row["sogKn"] for row in reports if row.get("sogKn") is not None]
    if not speeds:
        return _component("Behaviour", 0.0, maximum, "no speed-over-ground values in the feed")
    if approach.at_utc is None:
        return _component(
            "Behaviour",
            0.0,
            maximum,
            "no reports inside the release window, so no behaviour near the origin to assess",
        )

    median = float(np.median(speeds))
    focus_time = parse_utc(approach.at_utc)
    near: list[float] = []
    for row in reports:
        if row.get("sogKn") is None:
            continue
        try:
            stamp = parse_utc(row["timeUtc"])
        except Exception:
            continue
        if abs((stamp - focus_time).total_seconds()) <= 3600.0:
            near.append(float(row["sogKn"]))
    if not near:
        near = [float(approach.sog_kn)] if approach.sog_kn is not None else [median]

    lowest = min(near)
    if lowest <= STOPPED_KN:
        earned = maximum
        reason = (
            f"stopped ({lowest:.1f} kn) within an hour of the closest approach, against a "
            f"track median of {median:.1f} kn"
        )
    elif lowest <= SLOW_KN:
        earned = maximum * 0.7
        reason = f"slowed to {lowest:.1f} kn near the closest approach (median {median:.1f} kn)"
    elif median > 0 and lowest < 0.6 * median:
        earned = maximum * 0.4
        reason = (
            f"speed dropped to {lowest:.1f} kn near the closest approach, "
            f"{100 * (1 - lowest / median):.0f}% below its own median"
        )
    else:
        earned = 0.0
        reason = f"steady transit speed near the closest approach ({lowest:.1f} kn, median {median:.1f} kn)"
    return _component("Behaviour", earned, maximum, reason)


def score_type(vessel: dict[str, Any], weights: C.ScoringWeights) -> dict[str, Any]:
    """Vessel category relevance -- what kind of ship could produce this slick."""
    maximum = weights.vessel_type
    key = vessel.get("typeKey")
    profile = VESSEL_TYPES.get(key) if key else None
    if profile is None:
        relevance = float(vessel.get("typeRelevance") or 0.0)
        rationale = vessel.get("typeRationale") or "vessel category not recognised"
        label = vessel.get("type") or "unknown type"
    else:
        relevance, rationale, label = profile.relevance, profile.rationale, profile.name
    return _component(
        "Type",
        maximum * relevance,
        maximum,
        f"{label}: {rationale}",
    )


def score_completeness(vessel: dict[str, Any], weights: C.ScoringWeights) -> dict[str, Any]:
    """How trustworthy the track itself is.

    This scores the *data*, never the vessel. A gappy feed lowers confidence in the other
    five components, which is why it is worth marks at all -- and only 5 of them.
    """
    maximum = weights.data_completeness
    cleaning = vessel.get("cleaning") or {}
    reports = float(cleaning.get("reportCompleteness", 1.0) or 0.0)
    fields = float(cleaning.get("fieldCompleteness", 1.0) or 0.0)
    rejected = int(cleaning.get("rejectedTotal", 0) or 0)
    gap = float(cleaning.get("largestGapMinutes", 0.0) or 0.0)

    quality = 0.6 * reports + 0.4 * fields
    if rejected:
        quality *= max(0.5, 1.0 - 0.05 * rejected)
    bits = [f"{cleaning.get('accepted', len(vessel.get('reports') or []))} reports accepted"]
    if reports < 0.995:
        bits.append(f"{(1 - reports) * 100:.0f}% of expected reports missing")
    if gap > 0:
        bits.append(f"largest gap {gap:.0f} min")
    if rejected:
        bits.append(f"{rejected} rows rejected in cleaning")
    if fields < 0.995:
        bits.append(f"{(1 - fields) * 100:.0f}% of speed/course/heading fields empty")
    if len(bits) == 1:
        bits.append("complete synthetic track")
    return _component("Data quality", maximum * quality, maximum, "; ".join(bits))


def confidence_band(total: float, maximum: int) -> str:
    """A word for the score, chosen so that none of them assigns responsibility."""
    share = total / max(1, maximum)
    if share >= 0.75:
        return "Strong geometric and temporal overlap - review first"
    if share >= 0.5:
        return "Partial overlap - worth reviewing"
    if share >= 0.25:
        return "Weak overlap - low priority"
    return "No meaningful overlap - retain for completeness only"


def score_vessel(
    vessel: dict[str, Any],
    env: EnvelopeTrack,
    weights: C.ScoringWeights | None = None,
) -> dict[str, Any]:
    """Score one vessel and explain every component of the result."""
    weights = weights or C.ScoringWeights()
    reports = vessel.get("reports") or []
    approach = closest_approach(reports, env)

    components = [
        score_distance(approach, weights),
        score_time(approach, env, weights),
        score_trajectory(reports, approach, env, weights),
        score_behaviour(reports, approach, weights),
        score_type(vessel, weights),
        score_completeness(vessel, weights),
    ]
    total = sum(c["score"] for c in components)

    return {
        "mmsi": vessel.get("mmsi"),
        "name": vessel.get("name"),
        "type": vessel.get("type"),
        "typeKey": vessel.get("typeKey"),
        "pattern": vessel.get("pattern"),
        "synthetic": True,
        "status": C.LABEL_CANDIDATE,
        "score": round(total, 1),
        "scoreMax": weights.total,
        "band": confidence_band(total, weights.total),
        "components": {c["component"]: c["score"] for c in components},
        "componentDetail": components,
        "explanation": [f"{c['component']}: {c['score']:g}/{c['max']} - {c['reason']}" for c in components],
        "evidence": {
            "closestApproachKm": approach.distance_km,
            "closestApproachRadii": approach.radii,
            "envelopeRadiusKm": approach.radius_km,
            "closestApproachUtc": approach.at_utc,
            "reportsInWindow": approach.reports_in_window,
            "reportsNearEnvelope": approach.reports_relevant,
            "reportsTotal": approach.reports_total,
            "minutesNearEnvelope": approach.window_minutes,
            "nearestPassOutsideWindowKm": approach.nearest_outside_km,
            "courseAtApproachDeg": approach.course_deg,
            "sogAtApproachKn": approach.sog_kn,
            "firstReportUtc": vessel.get("firstReportUtc"),
            "lastReportUtc": vessel.get("lastReportUtc"),
        },
        "track": vessel.get("track") or [],
    }


def rank_vessels(
    feed: dict[str, Any],
    backward: dict[str, Any],
    weights: C.ScoringWeights | None = None,
) -> dict[str, Any]:
    """Score every vessel in a synthetic feed and rank them.

    Ties break on closest approach and then MMSI, so the ordering is stable across runs
    rather than depending on dictionary iteration order.
    """
    weights = weights or C.ScoringWeights()
    env = envelope_from_drift(backward)
    scored = [score_vessel(v, env, weights) for v in feed.get("vessels") or []]
    scored.sort(
        key=lambda s: (
            -s["score"],
            s["evidence"]["closestApproachKm"] if s["evidence"]["closestApproachKm"] is not None else 1e9,
            str(s["mmsi"]),
        )
    )
    for position, entry in enumerate(scored, start=1):
        entry["rank"] = position

    return {
        "pipelineVersion": C.PIPELINE_VERSION,
        "generatedUtc": C.utc_now_iso(),
        "aisLabel": feed.get("label", C.LABEL_AIS),
        "driftLabel": backward.get("label"),
        "status": C.LABEL_STATUS,
        "candidateLabel": C.LABEL_CANDIDATE,
        "caveat": CAVEAT,
        "weights": {
            "distance": weights.distance,
            "timeWindow": weights.time_window,
            "trajectory": weights.trajectory,
            "behaviour": weights.behaviour,
            "vesselType": weights.vessel_type,
            "dataCompleteness": weights.data_completeness,
            "total": weights.total,
        },
        "method": {
            "distance": (
                "great-circle distance to the drift envelope centre at the vessel's own "
                f"timestamp, full marks inside {INSIDE_RADII:g} envelope radii and zero "
                f"beyond {IRRELEVANT_RADII:g}"
            ),
            "time": (
                "half for being within reach of the estimated oil position during the "
                "release window, half scaling to 6 h of such coverage"
            ),
            "trajectory": (
                "two thirds for intersecting the envelope, one third for a course across "
                "the drift axis, both gated on proximity"
            ),
            "behaviour": f"full marks below {STOPPED_KN:g} kn near the closest approach, partial for a relative drop",
            "type": "category relevance to bulk oil carriage",
            "dataCompleteness": "60% report completeness, 40% field completeness, penalised for rejected rows",
        },
        "releaseWindow": feed.get("releaseWindow"),
        "originZone": feed.get("originZone"),
        "candidateCount": len(scored),
        "candidates": scored,
    }


def candidates_csv(ranking: dict[str, Any]) -> str:
    """The ranked table as CSV, for the dashboard's download button."""
    header = [
        "rank",
        "name",
        "mmsi",
        "vessel_type",
        "total_score",
        "score_max",
        "distance",
        "time",
        "trajectory",
        "behaviour",
        "type",
        "data_quality",
        "closest_approach_km",
        "closest_approach_utc",
        "reports_in_window",
        "reports_near_envelope",
        "status",
        "ais_mode",
    ]
    rows = [",".join(header)]
    for entry in ranking.get("candidates") or []:
        components = entry.get("components", {})
        evidence = entry.get("evidence", {})
        values = [
            entry.get("rank"),
            entry.get("name"),
            entry.get("mmsi"),
            entry.get("type"),
            entry.get("score"),
            entry.get("scoreMax"),
            components.get("Distance"),
            components.get("Time"),
            components.get("Trajectory"),
            components.get("Behaviour"),
            components.get("Type"),
            components.get("Data quality"),
            evidence.get("closestApproachKm"),
            evidence.get("closestApproachUtc"),
            evidence.get("reportsInWindow"),
            evidence.get("reportsNearEnvelope"),
            entry.get("status"),
            # The exported file leaves the app, so it carries the full mandated label
            # rather than an abbreviation a reader could mistake for a data source.
            ranking.get("aisLabel") or C.LABEL_AIS,
        ]
        rows.append(",".join(_csv_cell(v) for v in values))
    return "\n".join(rows) + "\n"


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    if any(ch in text for ch in ',"\n'):
        return '"' + text.replace('"', '""') + '"'
    return text


def explanation_block(entry: dict[str, Any]) -> str:
    """The PRD's plain-text candidate summary, verbatim in shape."""
    lines = [
        f"{entry['name']} - {entry['status']}",
        f"Total: {entry['score']:g}/{entry['scoreMax']}",
        "",
    ]
    lines.extend(entry["explanation"])
    return "\n".join(lines)
