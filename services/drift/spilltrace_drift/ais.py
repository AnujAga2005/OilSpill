"""Deterministic synthetic AIS for the demonstration case.

Real AIS is a licensed commercial feed and is not available here, so this module
fabricates one. Everything it produces is fiction and is labelled as such at every
level: the returned payload carries :data:`spilltrace_common.config.LABEL_AIS`, each
vessel carries ``synthetic: True``, and the identities are constructed so that they
cannot be mistaken for a real ship:

* MMSI numbers begin ``999``, which is outside the ITU Maritime Identification Digits
  range (201-775). No real vessel can hold one of these numbers.
* Names are of the form ``SYNTHETIC DEMO ALPHA`` -- a callsign-shaped placeholder, not
  a plausible ship name, and deliberately not any name in a real registry.

The vessels are not scattered at random. They are placed *relative to the backward
drift result*, because that is what makes the scoring meaningful: for every time ``t``
before the acquisition, the hindcast gives a region where the oil plausibly was, and a
vessel is only a candidate if it was inside that region at that same time. The five
evidence patterns required by the PRD are therefore expressed as relationships to the
time-matched envelope, not as arbitrary coordinates:

============================  ============================================
pattern                       what it demonstrates
============================  ============================================
``origin_on_time``            inside the origin zone during the window
``origin_wrong_time``         inside the zone, hours outside the window
``course_match_far``          drift-aligned heading, far from the zone
``slowdown_near_origin``      speed drop to near zero beside the zone
``transit_background``        ordinary traffic, no relationship at all
============================  ============================================

Generation is reproducible from the case key alone: ``stable_seed`` hashes the key with
SHA-256 rather than using ``hash()``, so the same case yields identical tracks in every
process and on every machine.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Sequence

import numpy as np

from spilltrace_common import config as C

DEG = math.pi / 180.0
KN_PER_MS = 1.9438444924406046
KM_PER_NAUTICAL_MILE = 1.852
# Speed thresholds shared with the scoring module, so a "stopped" navigational status in
# the feed and a "stopped" behaviour score can never disagree about what stopped means.
STOPPED_KN = 1.0
SLOW_KN = 3.0
SYNTHETIC_MMSI_PREFIX = "999"

MMSI_NOTE = (
    "MMSI numbers begin 999, which is outside the ITU Maritime Identification Digits "
    "range (201-775), so none of these identifiers can belong to a real vessel."
)
NAME_NOTE = (
    "Names are placeholders of the form 'SYNTHETIC DEMO <word>' and are not drawn from "
    "any vessel registry."
)
DISCLAIMER = (
    "These vessel tracks are fabricated for demonstration. They are not evidence, they "
    "describe no real voyage, and no real vessel was near the imaged slick."
)

# Phonetic words give a stable, obviously-synthetic naming series.
CALLSIGN_WORDS = (
    "ALPHA",
    "BRAVO",
    "CHARLIE",
    "DELTA",
    "ECHO",
    "FOXTROT",
    "GOLF",
    "HOTEL",
    "INDIA",
    "JULIET",
    "KILO",
    "LIMA",
    "MIKE",
    "NOVEMBER",
    "OSCAR",
    "PAPA",
)


@dataclass(frozen=True)
class VesselType:
    """A vessel category and how relevant it is to an oil-discharge enquiry.

    ``relevance`` is a 0-1 multiplier on the vessel-type component of the score. It
    encodes only what kind of ship *could* discharge oil in quantity -- an oil tanker
    can, a passenger ferry is far less likely to -- and carries no implication about
    any individual vessel.
    """

    name: str
    relevance: float
    rationale: str
    cruise_kn: float
    length_m: int


VESSEL_TYPES: dict[str, VesselType] = {
    "oil_tanker": VesselType(
        "Oil tanker",
        1.0,
        "carries persistent oil in bulk, so an operational discharge is physically possible",
        12.5,
        250,
    ),
    "chemical_tanker": VesselType(
        "Chemical/product tanker",
        0.9,
        "carries liquid cargo in bulk; tank washings are a known discharge source",
        13.0,
        180,
    ),
    "bulk_carrier": VesselType(
        "Bulk carrier",
        0.6,
        "no liquid cargo, but bunker fuel and engine-room slops remain possible sources",
        12.0,
        230,
    ),
    "container_ship": VesselType(
        "Container ship",
        0.5,
        "bunker fuel and bilge water are the only plausible sources",
        18.0,
        300,
    ),
    "general_cargo": VesselType(
        "General cargo",
        0.5,
        "bunker fuel and bilge water are the only plausible sources",
        11.5,
        120,
    ),
    "fishing": VesselType(
        "Fishing vessel",
        0.3,
        "small fuel volumes; a large slick is unlikely to originate here",
        7.0,
        40,
    ),
    "offshore_supply": VesselType(
        "Offshore supply vessel",
        0.7,
        "services production facilities and handles oil-contaminated deck drainage",
        11.0,
        70,
    ),
    "passenger": VesselType(
        "Passenger ferry",
        0.2,
        "tightly regulated waste handling and no bulk oil cargo",
        20.0,
        150,
    ),
}


@dataclass(frozen=True)
class AisConfig:
    """Knobs for the synthetic feed. Defaults suit a 24 h backward horizon."""

    report_interval_s: int = 600  # 10 minutes, a plausible terrestrial-AIS cadence
    lead_hours: float = 8.0  # track starts this long before the release window opens
    trail_hours: float = 6.0  # and ends this long after the acquisition
    background_vessels: int = 6  # unrelated traffic, on top of the four patterned ships
    seed: int = C.SEED_SYNTHETIC_AIS
    min_reports: int = 12
    max_reports: int = 600


@dataclass
class Leg:
    """One constant-speed segment of a planned track."""

    lon: float
    lat: float
    speed_kn: float


@dataclass
class VesselPlan:
    """Everything needed to realise one vessel's track, before sampling."""

    key: str
    pattern: str
    type_key: str
    waypoints: list[Leg]
    start: datetime
    end: datetime
    dropout: float = 0.0  # fraction of reports deliberately missing
    field_gaps: tuple[str, ...] = ()
    story: str = ""
    # A slowdown covers both the "hove to" and the "eased off" cases: for ``slow_seconds``
    # the vessel makes way at ``slow_factor`` of its cruising speed (0.0 being a full stop),
    # centred on ``slow_fraction`` of the path.
    slow_seconds: float = 0.0
    slow_factor: float = 0.0
    slow_fraction: float = 0.5
    jitter_m: float = 45.0
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Geometry helpers (local tangent plane; the case spans a few tens of km)
# ---------------------------------------------------------------------------


def metres_per_degree(lat: float) -> tuple[float, float]:
    """(east metres per degree of longitude, north metres per degree of latitude)."""
    per_lat = DEG * C.EARTH_RADIUS_M
    per_lon = per_lat * max(math.cos(lat * DEG), 1e-6)
    return per_lon, per_lat


def offset(lon: float, lat: float, east_m: float, north_m: float) -> tuple[float, float]:
    per_lon, per_lat = metres_per_degree(lat)
    return lon + east_m / per_lon, lat + north_m / per_lat


def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = lat1 * DEG, lat2 * DEG
    dp = p2 - p1
    dl = (lon2 - lon1) * DEG
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * C.EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h))) / 1000.0


def bearing_deg(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Initial great-circle bearing, degrees clockwise from true north."""
    p1, p2 = lat1 * DEG, lat2 * DEG
    dl = (lon2 - lon1) * DEG
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def parse_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    stamp = datetime.fromisoformat(text)
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def iso_utc(stamp: datetime) -> str:
    return stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# The drift result, reduced to what scoring needs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnvelopeTrack:
    """The backward drift envelope as a function of time.

    ``times`` runs from the earliest (the origin estimate) to the acquisition. At each
    time the oil cloud is summarised by a centroid and a P90 radius; that circle is the
    region a release at that instant would have had to occur in.
    """

    times: tuple[datetime, ...]
    lons: tuple[float, ...]
    lats: tuple[float, ...]
    radii_km: tuple[float, ...]
    observed: datetime
    origin_time: datetime

    @property
    def window_start(self) -> datetime:
        return min(self.times)

    @property
    def window_end(self) -> datetime:
        return max(self.times)

    def at(self, when: datetime) -> tuple[float, float, float]:
        """Interpolated (lon, lat, radius_km); clamped outside the horizon."""
        span = [(t - self.window_start).total_seconds() for t in self.times]
        target = (when - self.window_start).total_seconds()
        lon = float(np.interp(target, span, self.lons))
        lat = float(np.interp(target, span, self.lats))
        radius = float(np.interp(target, span, self.radii_km))
        return lon, lat, radius

    def covers(self, when: datetime) -> bool:
        return self.window_start <= when <= self.window_end


def envelope_from_drift(backward: dict[str, Any]) -> EnvelopeTrack:
    """Read the hindcast timeline into an :class:`EnvelopeTrack`.

    The drift timeline is emitted newest-first for a backward run (step 0 is the
    acquisition), so it is reversed here to run forward in time.
    """
    timeline = backward.get("timeline") or []
    if len(timeline) < 2:
        raise ValueError("the backward drift result has no usable timeline")
    observed = parse_utc(backward["observedUtc"])

    rows = []
    for entry in timeline:
        lon, lat = entry["centroid"]
        rows.append(
            (
                parse_utc(entry["timeUtc"]),
                float(lon),
                float(lat),
                # A zero radius at step 0 would make the acquisition instant infinitely
                # discriminating, so the observed slick's own extent floors it.
                max(float(entry["spreadP90Km"]), 0.25),
            )
        )
    rows.sort(key=lambda r: r[0])
    return EnvelopeTrack(
        times=tuple(r[0] for r in rows),
        lons=tuple(r[1] for r in rows),
        lats=tuple(r[2] for r in rows),
        radii_km=tuple(r[3] for r in rows),
        observed=observed,
        origin_time=rows[0][0],
    )


# ---------------------------------------------------------------------------
# Track realisation
# ---------------------------------------------------------------------------


def _leg_lengths(waypoints: Sequence[Leg]) -> list[float]:
    return [
        haversine_km(waypoints[i].lon, waypoints[i].lat, waypoints[i + 1].lon, waypoints[i + 1].lat)
        for i in range(len(waypoints) - 1)
    ]


def _position_on_path(waypoints: Sequence[Leg], fraction: float) -> tuple[float, float, float]:
    """Interpolate (lon, lat, speed_kn) at ``fraction`` of the total path length."""
    lengths = _leg_lengths(waypoints)
    total = sum(lengths)
    if total <= 0:
        return waypoints[0].lon, waypoints[0].lat, waypoints[0].speed_kn
    target = max(0.0, min(1.0, fraction)) * total
    walked = 0.0
    for i, length in enumerate(lengths):
        if walked + length >= target or i == len(lengths) - 1:
            local = 0.0 if length <= 0 else (target - walked) / length
            a, b = waypoints[i], waypoints[i + 1]
            return (
                a.lon + (b.lon - a.lon) * local,
                a.lat + (b.lat - a.lat) * local,
                a.speed_kn + (b.speed_kn - a.speed_kn) * local,
            )
        walked += length
    last = waypoints[-1]
    return last.lon, last.lat, last.speed_kn


def realise_track(plan: VesselPlan, cfg: AisConfig, rng: np.random.Generator) -> list[dict[str, Any]]:
    """Sample a plan into timestamped AIS reports.

    Position is walked along the planned path at a rate set by the path length and the
    plan's duration, so the reported speed over ground and the spacing of consecutive
    positions agree. A slowdown scales that rate: at ``slow_factor`` of cruising speed
    the vessel covers ``slow_factor`` of the path per second, so a full stop consumes
    time without consuming path and the vessel resumes from exactly where it stopped.
    Course over ground is derived from consecutive positions rather than declared, and
    heading differs from course by a small yaw offset, as it would with any real feed.
    """
    duration = (plan.end - plan.start).total_seconds()
    if duration <= 0:
        raise ValueError(f"vessel {plan.key} has a non-positive track duration")
    steps = int(duration // cfg.report_interval_s)
    steps = max(cfg.min_reports, min(cfg.max_reports, steps))

    # The slowdown interval, in seconds elapsed since the start of the track.
    slow_seconds = min(max(0.0, plan.slow_seconds), 0.9 * duration)
    slow_factor = min(1.0, max(0.0, plan.slow_factor))
    slow_from = plan.slow_fraction * (duration - slow_seconds)
    slow_to = slow_from + slow_seconds
    # Path length is expressed in cruising-seconds: the slow interval contributes only
    # its own reduced share, so the cruise legs still run at their declared speeds.
    cruise_seconds = max(1e-9, duration - slow_seconds * (1.0 - slow_factor))
    per_lon, per_lat = metres_per_degree(plan.waypoints[0].lat)

    raw: list[dict[str, Any]] = []
    for i in range(steps + 1):
        share = i / steps
        elapsed = share * duration
        stamp = plan.start + timedelta(seconds=elapsed)
        slowed = slow_seconds > 0.0 and slow_from <= elapsed < slow_to
        before = min(elapsed, slow_from)
        within = min(max(elapsed - slow_from, 0.0), slow_seconds)
        beyond = max(elapsed - slow_to, 0.0)
        fraction = (before + slow_factor * within + beyond) / cruise_seconds
        lon, lat, speed = _position_on_path(plan.waypoints, fraction)
        if slowed:
            # A stopped hull still shows a fraction of a knot of set and drift.
            speed = speed * slow_factor + float(rng.uniform(0.0, 0.4)) * (1.0 - slow_factor)
        else:
            # Reported speed over ground and reported position are separate measurements
            # in a real feed, so a couple of per cent of independent noise on the speed is
            # realistic and gives the behaviour score something other than a flat line.
            speed *= 1.0 + float(rng.normal(0.0, 0.02))
        jitter_e = float(rng.normal(0.0, plan.jitter_m))
        jitter_n = float(rng.normal(0.0, plan.jitter_m))
        raw.append(
            {
                "timeUtc": iso_utc(stamp),
                "_stamp": stamp,
                "lon": lon + jitter_e / per_lon,
                "lat": lat + jitter_n / per_lat,
                "sogKn": round(max(0.0, speed), 2),
                "stopped": slowed and speed < STOPPED_KN,
            }
        )

    reports: list[dict[str, Any]] = []
    for i, point in enumerate(raw):
        nxt = raw[min(i + 1, len(raw) - 1)]
        prv = raw[max(i - 1, 0)]
        if nxt is point and prv is point:
            course = 0.0
        else:
            course = bearing_deg(prv["lon"], prv["lat"], nxt["lon"], nxt["lat"])
        yaw = float(rng.normal(0.0, 3.0))
        record = {
            "timeUtc": point["timeUtc"],
            "lon": round(point["lon"], 6),
            "lat": round(point["lat"], 6),
            "sogKn": point["sogKn"],
            "cogDeg": round(course, 1),
            "headingDeg": round((course + yaw) % 360.0, 1),
            "navStatus": "moored/stopped" if point["stopped"] else "under way using engine",
            "synthetic": True,
        }
        # Deliberate feed defects, so the data-completeness component has something to
        # measure instead of always scoring full marks.
        if plan.dropout > 0 and i not in (0, len(raw) - 1) and rng.random() < plan.dropout:
            continue
        for name in plan.field_gaps:
            if rng.random() < 0.35:
                record[name] = None
        reports.append(record)
    return reports


# ---------------------------------------------------------------------------
# Cleaning and reconstruction
# ---------------------------------------------------------------------------


def clean_reports(
    reports: Iterable[dict[str, Any]],
    cfg: AisConfig,
    max_speed_kn: float = 45.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Sort, de-duplicate and sanity-check one vessel's reports.

    A real feed contains duplicate timestamps, positions outside the valid range and
    implied speeds no hull can achieve. Rejected rows are counted rather than silently
    dropped, because the count feeds the data-completeness score.
    """
    accepted: list[dict[str, Any]] = []
    rejected = {"badPosition": 0, "duplicateTimestamp": 0, "impossibleSpeed": 0, "unparsableTime": 0}

    ordered = []
    for row in reports:
        try:
            stamp = parse_utc(row["timeUtc"])
        except Exception:
            rejected["unparsableTime"] += 1
            continue
        ordered.append((stamp, row))
    ordered.sort(key=lambda pair: pair[0])

    seen: set[str] = set()
    for stamp, row in ordered:
        lon, lat = row.get("lon"), row.get("lat")
        if lon is None or lat is None or not (-180.0 <= lon <= 180.0) or not (-90.0 <= lat <= 90.0):
            rejected["badPosition"] += 1
            continue
        if row["timeUtc"] in seen:
            rejected["duplicateTimestamp"] += 1
            continue
        if accepted:
            prev = accepted[-1]
            gap_h = (stamp - parse_utc(prev["timeUtc"])).total_seconds() / 3600.0
            if gap_h > 0:
                implied = haversine_km(prev["lon"], prev["lat"], lon, lat) / gap_h / KM_PER_NAUTICAL_MILE
                if implied > max_speed_kn:
                    rejected["impossibleSpeed"] += 1
                    continue
        seen.add(row["timeUtc"])
        accepted.append(row)

    expected_gap = cfg.report_interval_s
    gaps: list[dict[str, Any]] = []
    for i in range(1, len(accepted)):
        seconds = (parse_utc(accepted[i]["timeUtc"]) - parse_utc(accepted[i - 1]["timeUtc"])).total_seconds()
        if seconds > expected_gap * 1.5:
            gaps.append(
                {
                    "fromUtc": accepted[i - 1]["timeUtc"],
                    "toUtc": accepted[i]["timeUtc"],
                    "minutes": round(seconds / 60.0, 1),
                }
            )

    span_s = 0.0
    if len(accepted) >= 2:
        span_s = (parse_utc(accepted[-1]["timeUtc"]) - parse_utc(accepted[0]["timeUtc"])).total_seconds()
    ideal = int(span_s // expected_gap) + 1 if span_s > 0 else len(accepted)
    missing_fields = sum(
        1
        for row in accepted
        for key in ("sogKn", "cogDeg", "headingDeg")
        if row.get(key) is None
    )

    summary = {
        "accepted": len(accepted),
        "rejected": rejected,
        "rejectedTotal": sum(rejected.values()),
        "expectedIntervalS": expected_gap,
        "expectedReports": ideal,
        "gaps": gaps,
        "largestGapMinutes": round(max((g["minutes"] for g in gaps), default=0.0), 1),
        "missingFieldValues": missing_fields,
        "reportCompleteness": round(min(1.0, len(accepted) / ideal), 4) if ideal else 0.0,
        "fieldCompleteness": round(1.0 - missing_fields / max(1, 3 * len(accepted)), 4),
    }
    return accepted, summary


def track_line(reports: Sequence[dict[str, Any]]) -> list[list[float]]:
    """The cleaned reports as a [lon, lat] polyline, ready for the map."""
    return [[row["lon"], row["lat"]] for row in reports]


# ---------------------------------------------------------------------------
# Plan construction: the five required evidence patterns
# ---------------------------------------------------------------------------


def _mmsi(index: int) -> str:
    """A synthetic 9-digit MMSI in a range no real vessel can occupy.

    The stride is coprime with the modulus, so the six trailing digits are distinct for
    any realistic number of vessels instead of colliding after a wrap.
    """
    return f"{SYNTHETIC_MMSI_PREFIX}{(100000 + index * 1117) % 1000000:06d}"


def _name(index: int) -> str:
    word = CALLSIGN_WORDS[index % len(CALLSIGN_WORDS)]
    suffix = "" if index < len(CALLSIGN_WORDS) else f" {index // len(CALLSIGN_WORDS) + 1}"
    return f"SYNTHETIC DEMO {word}{suffix}"


def _drift_axis(env: EnvelopeTrack) -> float:
    """Bearing from the origin estimate to the observed slick."""
    return bearing_deg(env.lons[0], env.lats[0], env.lons[-1], env.lats[-1])


def _straight_track(
    lon_c: float,
    lat_c: float,
    course_deg: float,
    speed_kn: float,
    centre_time: datetime,
    hours: float,
) -> tuple[list[Leg], datetime, datetime]:
    """A straight constant-speed track of ``hours`` duration through (lon_c, lat_c).

    Duration drives the geometry rather than the other way round, so the declared speed
    over ground always matches the spacing of consecutive reports.
    """
    half_km = speed_kn * KM_PER_NAUTICAL_MILE * hours / 2.0
    head = course_deg * DEG
    east, north = math.sin(head), math.cos(head)
    start = offset(lon_c, lat_c, -east * half_km * 1000.0, -north * half_km * 1000.0)
    end = offset(lon_c, lat_c, east * half_km * 1000.0, north * half_km * 1000.0)
    legs = [
        Leg(start[0], start[1], speed_kn),
        Leg(lon_c, lat_c, speed_kn),
        Leg(end[0], end[1], speed_kn),
    ]
    half = timedelta(hours=hours / 2.0)
    return legs, centre_time - half, centre_time + half


def _crossing_track(
    env: EnvelopeTrack,
    cross_at: datetime,
    approach_deg: float,
    speed_kn: float,
    hours: float,
    offset_km: float = 0.0,
) -> tuple[list[Leg], datetime, datetime]:
    """A straight track whose closest approach to the time-matched envelope is ``offset_km``.

    The vessel is aimed at the envelope centre for ``cross_at``, so the geometry -- not a
    hand-tuned coordinate pair -- decides how close it gets and when.
    """
    lon_c, lat_c, _ = env.at(cross_at)
    if offset_km > 0:
        side = (approach_deg + 90.0) * DEG
        lon_c, lat_c = offset(
            lon_c, lat_c, math.sin(side) * offset_km * 1000.0, math.cos(side) * offset_km * 1000.0
        )
    return _straight_track(lon_c, lat_c, approach_deg, speed_kn, cross_at, hours)


# ---------------------------------------------------------------------------
# Keeping "far away" traffic actually far away
# ---------------------------------------------------------------------------
# Scoring measures distance in envelope radii, not kilometres: a vessel earns full
# distance marks inside one radius and nothing at all beyond three. That is the right
# design -- the separation that matters is relative to how uncertain the origin estimate
# is -- but it means a fixed placement in kilometres is not a fixed placement in score.
# A 20 km slick produces a P90 radius around 10 km, so the three-radius relevance gate
# reaches past 30 km, and "background" traffic drawn at 28-95 km lands inside it.
#
# So the exclusions below are expressed in radii, with a kilometre floor for small
# envelopes, and the realised track is *measured* rather than assumed: a straight chord
# running for twenty hours sweeps hundreds of kilometres and can pass far closer than its
# placement radius suggests.

BACKGROUND_CLEARANCE_RADII = 3.6
BACKGROUND_CLEARANCE_FLOOR_KM = 26.0
FAR_CONTROL_CLEARANCE_RADII = 3.4
FAR_CONTROL_FLOOR_KM = 45.0
# A course within this angle of the offset bearing runs almost straight down the line
# joining the vessel to the envelope, so moving the vessel further out along that bearing
# barely separates the two and the placement cannot be corrected by translation. Such a
# course is turned away to the nearest heading that genuinely crosses the bearing.
MIN_OBLIQUITY = 0.35


def _closest_to_envelope(
    waypoints: Sequence[Leg], start: datetime, end: datetime, env: EnvelopeTrack
) -> tuple[float | None, float | None]:
    """Smallest time-matched separation of a noise-free plan, as ``(km, radii)``.

    Only samples inside the drift window count, matching
    :func:`spilltrace_drift.scoring.closest_approach`. This minimises the *ratio* where
    scoring minimises the kilometres and reports the ratio there, which makes it the
    conservative of the two: a plan whose minimum ratio clears the gate cannot have a
    below-gate ratio at its nearest kilometre.
    """
    duration = max((end - start).total_seconds(), 1.0)
    steps = int(min(512, max(16, duration // 300.0)))
    best_km: float | None = None
    best_radii: float | None = None
    for index in range(steps + 1):
        fraction = index / steps
        stamp = start + timedelta(seconds=duration * fraction)
        if not env.covers(stamp):
            continue
        lon, lat, _ = _position_on_path(waypoints, fraction)
        centre_lon, centre_lat, radius = env.at(stamp)
        km = haversine_km(lon, lat, centre_lon, centre_lat)
        radii = km / max(radius, 1e-6)
        if best_radii is None or radii < best_radii:
            best_km, best_radii = km, radii
    return best_km, best_radii


def _turn_off_the_bearing(course_deg: float, offset_deg: float) -> float:
    """``course_deg``, rotated the least amount needed to cross ``offset_deg`` obliquely."""
    delta = ((offset_deg - course_deg + 180.0) % 360.0) - 180.0
    if abs(math.sin(delta * DEG)) >= MIN_OBLIQUITY:
        return course_deg % 360.0
    limit = math.degrees(math.asin(MIN_OBLIQUITY))
    sign = 1.0 if delta >= 0.0 else -1.0
    delta = sign * (limit if abs(delta) <= 90.0 else 180.0 - limit)
    return (offset_deg - delta) % 360.0


def _place_clear_of(
    env: EnvelopeTrack,
    centre_time: datetime,
    course_deg: float,
    offset_deg: float,
    speed_kn: float,
    hours: float,
    clearance_radii: float,
    floor_km: float,
) -> tuple[list[Leg], datetime, datetime]:
    """A straight track that stays ``clearance_radii`` envelope radii clear throughout.

    ``offset_deg`` is the bearing from the envelope centre to the vessel's mid-track
    position. Placement starts at the wanted separation and is then *measured* and pushed
    outward until it holds, rather than being solved for once and trusted: the envelope
    centre moves across the window, and a chord running for twenty hours can pass much
    closer than its mid-track offset suggests.
    """
    lon_e, lat_e, radius_km = env.at(centre_time)
    target_km = max(clearance_radii * radius_km, floor_km)
    course_deg = _turn_off_the_bearing(course_deg, offset_deg)
    bearing = offset_deg * DEG
    east, north = math.sin(bearing), math.cos(bearing)
    legs, start, end = _straight_track(lon_e, lat_e, course_deg, speed_kn, centre_time, hours)

    for _ in range(8):
        lon_b, lat_b = offset(lon_e, lat_e, east * target_km * 1000.0, north * target_km * 1000.0)
        legs, start, end = _straight_track(lon_b, lat_b, course_deg, speed_kn, centre_time, hours)
        km, radii = _closest_to_envelope(legs, start, end, env)
        if km is None or radii is None:
            # The track never enters the drift window, so it can never be scored on
            # distance at all. That is already clear of the envelope.
            return legs, start, end
        if radii >= clearance_radii and km >= floor_km:
            return legs, start, end
        deficit = max(clearance_radii / max(radii, 1e-6), floor_km / max(km, 1e-6))
        target_km *= min(max(deficit, 1.08), 2.5)
    return legs, start, end


def build_plans(env: EnvelopeTrack, cfg: AisConfig, rng: np.random.Generator) -> list[VesselPlan]:
    """The four patterned vessels plus background traffic."""
    axis = _drift_axis(env)
    horizon_h = (env.observed - env.origin_time).total_seconds() / 3600.0
    plans: list[VesselPlan] = []

    # 1. Inside the origin zone, at the time that zone corresponds to, easing off as it
    #    goes through. This is the pattern that should score highest -- and it is the only
    #    one that should. It eases to roughly a quarter of cruising speed rather than
    #    stopping outright, which is the harder case to distinguish from ordinary traffic.
    cross = env.origin_time + timedelta(hours=horizon_h * 0.12)
    slow_seconds = 50 * 60.0
    slow_factor = 0.22
    legs, start, end = _crossing_track(env, cross, (axis + 96.0) % 360.0, speed_kn=11.0, hours=5.0)
    plans.append(
        VesselPlan(
            key="origin_on_time",
            pattern="origin_on_time",
            type_key="oil_tanker",
            waypoints=legs,
            start=start,
            end=end + timedelta(seconds=slow_seconds * (1.0 - slow_factor)),
            slow_seconds=slow_seconds,
            slow_factor=slow_factor,
            slow_fraction=0.5,
            story="crossed the estimated origin zone inside the plausible release window",
        )
    )

    # 2. Same water, wrong time: the track is shifted past the acquisition, so distance
    #    scores well and time does not.
    legs2, s2, e2 = _crossing_track(env, cross, (axis + 200.0) % 360.0, speed_kn=12.0, hours=4.5)
    shift = timedelta(hours=horizon_h + 5.0)
    plans.append(
        VesselPlan(
            key="origin_wrong_time",
            pattern="origin_wrong_time",
            type_key="chemical_tanker",
            waypoints=legs2,
            start=s2 + shift,
            end=e2 + shift,
            story="crossed the same water, but hours after the image was acquired",
        )
    )

    # 3. Course aligned with the drift axis, but displaced far enough that it could not
    #    have been the source. Tests that trajectory alone cannot carry a score, so the
    #    displacement has to clear the relevance gate at any envelope scale.
    mid_time = env.origin_time + timedelta(hours=horizon_h * 0.5)
    legs3, s3, e3 = _place_clear_of(
        env,
        mid_time,
        course_deg=axis,
        offset_deg=(axis + 90.0) % 360.0,
        speed_kn=13.0,
        hours=horizon_h * 0.6,
        clearance_radii=FAR_CONTROL_CLEARANCE_RADII,
        floor_km=FAR_CONTROL_FLOOR_KM,
    )
    plans.append(
        VesselPlan(
            key="course_match_far",
            pattern="course_match_far",
            type_key="bulk_carrier",
            waypoints=legs3,
            start=s3,
            end=e3,
            story="ran parallel to the drift axis but stayed tens of kilometres away",
        )
    )

    # 4. A near-total stop beside the zone during the window. Behaviour scores fully,
    #    distance only partly, because it never enters the zone itself.
    stop_seconds = 100 * 60.0
    stop_mid = env.origin_time + timedelta(hours=horizon_h * 0.4)
    legs4, s4, e4 = _crossing_track(
        env, stop_mid, (axis + 35.0) % 360.0, speed_kn=9.5, hours=4.0, offset_km=3.2
    )
    plans.append(
        VesselPlan(
            key="slowdown_near_origin",
            pattern="slowdown_near_origin",
            type_key="offshore_supply",
            waypoints=legs4,
            start=s4,
            end=e4 + timedelta(seconds=stop_seconds),
            slow_seconds=stop_seconds,
            slow_factor=0.0,
            slow_fraction=0.5,
            dropout=0.08,
            field_gaps=("headingDeg",),
            story="slowed almost to a stop a few kilometres from the estimated origin",
        )
    )

    # 5. Background traffic. Bearings, speeds and courses are drawn from the case seed so
    #    the scene looks like a shipping lane rather than a ring of decoys, but the
    #    separation is measured in envelope radii and verified against the realised chord,
    #    because "background" has to mean "outside the search corridor" and not "28 km".
    background_types = ("container_ship", "general_cargo", "fishing", "passenger", "bulk_carrier", "general_cargo")
    for i in range(cfg.background_vessels):
        type_key = background_types[i % len(background_types)]
        vtype = VESSEL_TYPES[type_key]
        bearing = float(rng.uniform(0.0, 360.0))
        course = float(rng.uniform(0.0, 360.0))
        spread = float(rng.uniform(1.0, 2.2))
        centre_time = env.origin_time + timedelta(hours=horizon_h * float(rng.uniform(0.2, 0.8)))
        speed = float(np.clip(rng.normal(vtype.cruise_kn, 1.6), 4.0, 24.0))
        hours = float(rng.uniform(horizon_h * 0.5, horizon_h + cfg.lead_hours))
        legs_b, s_b, e_b = _place_clear_of(
            env,
            centre_time,
            course_deg=course,
            offset_deg=bearing,
            speed_kn=speed,
            hours=hours,
            # Spread the lane out in depth rather than parking every vessel on the same
            # circle, while keeping the nearest of them clear of the relevance gate.
            clearance_radii=BACKGROUND_CLEARANCE_RADII * spread,
            floor_km=BACKGROUND_CLEARANCE_FLOOR_KM * spread,
        )
        plans.append(
            VesselPlan(
                key=f"transit_{i + 1}",
                pattern="transit_background",
                type_key=type_key,
                waypoints=legs_b,
                start=s_b,
                end=e_b,
                dropout=float(rng.uniform(0.0, 0.22)),
                field_gaps=("headingDeg",) if rng.random() < 0.4 else (),
                story="unrelated transit traffic",
            )
        )
    return plans


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def generate_ais(
    backward: dict[str, Any],
    case_key: str,
    cfg: AisConfig | None = None,
    is_water: Callable[[float, float], bool] | None = None,
) -> dict[str, Any]:
    """Build the whole synthetic feed for one case.

    ``backward`` is the result of a backward :func:`spilltrace_drift.engine.simulate`
    run; its timeline defines both the release window and the time-matched envelope the
    vessels are positioned against. ``is_water`` is consulted only to record how much of
    each track falls on the CMEMS land mask -- tracks are *not* silently moved, because
    a mask at ~9 km per cell cannot adjudicate a ship's position at 10 m resolution.
    """
    cfg = cfg or AisConfig()
    env = envelope_from_drift(backward)
    rng = np.random.default_rng(C.stable_seed(f"ais:{case_key}", cfg.seed))

    plans = build_plans(env, cfg, rng)
    vessels: list[dict[str, Any]] = []
    for index, plan in enumerate(plans):
        vtype = VESSEL_TYPES[plan.type_key]
        track_rng = np.random.default_rng(C.stable_seed(f"ais:{case_key}:{plan.key}", cfg.seed))
        raw = realise_track(plan, cfg, track_rng)
        reports, cleaning = clean_reports(raw, cfg)
        if not reports:
            continue
        on_land = 0
        if is_water is not None:
            on_land = sum(1 for row in reports if not is_water(row["lon"], row["lat"]))
        speeds = [row["sogKn"] for row in reports if row.get("sogKn") is not None]
        vessels.append(
            {
                "mmsi": _mmsi(index),
                "name": _name(index),
                "type": vtype.name,
                "typeKey": plan.type_key,
                "typeRelevance": vtype.relevance,
                "typeRationale": vtype.rationale,
                "lengthM": vtype.length_m,
                "pattern": plan.pattern,
                "generatorStory": plan.story,
                "synthetic": True,
                "firstReportUtc": reports[0]["timeUtc"],
                "lastReportUtc": reports[-1]["timeUtc"],
                "reportCount": len(reports),
                "rawReportCount": len(raw),
                "minSogKn": round(min(speeds), 2) if speeds else None,
                "maxSogKn": round(max(speeds), 2) if speeds else None,
                "medianSogKn": round(float(np.median(speeds)), 2) if speeds else None,
                "reportsOnLandMask": on_land,
                "cleaning": cleaning,
                "reports": reports,
                "track": track_line(reports),
            }
        )

    return {
        "mode": "synthetic",
        "label": C.LABEL_AIS,
        "disclaimer": DISCLAIMER,
        "identifierNote": MMSI_NOTE,
        "nameNote": NAME_NOTE,
        "pipelineVersion": C.PIPELINE_VERSION,
        "generatedUtc": C.utc_now_iso(),
        "caseKey": case_key,
        "reproducibility": {
            "seed": cfg.seed,
            "derivation": "sha256(f'{seed}:ais:{caseKey}[:vessel]') -> numpy default_rng",
            "note": "the same case key always produces these exact tracks",
        },
        "config": {
            "reportIntervalS": cfg.report_interval_s,
            "leadHours": cfg.lead_hours,
            "trailHours": cfg.trail_hours,
            "backgroundVessels": cfg.background_vessels,
        },
        "releaseWindow": {
            "startUtc": iso_utc(env.window_start),
            "endUtc": iso_utc(env.window_end),
            "hours": round((env.window_end - env.window_start).total_seconds() / 3600.0, 2),
            "basis": (
                "the span covered by the backward drift run: oil seen at the acquisition "
                "could have entered the water at any time within it"
            ),
        },
        "originZone": {
            "centroid": [round(env.lons[0], 6), round(env.lats[0], 6)],
            "radiusKm": round(env.radii_km[0], 4),
            "atUtc": iso_utc(env.origin_time),
        },
        "envelopeTrack": {
            "note": (
                "the time-matched drift envelope used for scoring; a vessel is only a "
                "candidate if it was inside the circle for its own timestamp"
            ),
            "samples": [
                {
                    "timeUtc": iso_utc(t),
                    "centroid": [round(lon, 6), round(lat, 6)],
                    "radiusKm": round(r, 4),
                }
                for t, lon, lat, r in zip(env.times, env.lons, env.lats, env.radii_km)
            ],
        },
        "counts": {
            "vessels": len(vessels),
            "reports": sum(v["reportCount"] for v in vessels),
            "patterns": sorted({v["pattern"] for v in vessels}),
        },
        "vessels": vessels,
    }


def to_geojson(feed: dict[str, Any]) -> dict[str, Any]:
    """Vessel tracks as a FeatureCollection, every feature labelled synthetic."""
    features = []
    for vessel in feed.get("vessels") or []:
        line = vessel.get("track") or []
        if len(line) >= 2:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": line},
                    "properties": {
                        "kind": "aisTrack",
                        "mmsi": vessel["mmsi"],
                        "name": vessel["name"],
                        "vesselType": vessel["type"],
                        "synthetic": True,
                        "label": feed["label"],
                    },
                }
            )
    zone = feed.get("originZone") or {}
    if zone.get("centroid"):
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": zone["centroid"]},
                "properties": {
                    "kind": "originZone",
                    "radiusKm": zone.get("radiusKm"),
                    "atUtc": zone.get("atUtc"),
                    # Derived from the drift run, not from AIS, but still not an
                    # observation -- so it carries the flag every other feature carries.
                    "synthetic": True,
                    "basis": "centroid and P90 radius of the backward drift envelope",
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}
