"""Tests for the synthetic AIS generator.

Real AIS is not available for these scenes, so this module fabricates traffic. That
makes two properties load-bearing and worth testing hard:

1. **It must be inseparable from real AIS in structure but never mistakable for it in
   identity.** Every identifier sits in the MMSI 999 test range, every name is prefixed,
   every record carries ``synthetic: True``, and the label the UI shows is fixed text.
2. **Reported speed must agree with reported position.** A generator that writes a
   declared SOG unrelated to the spacing of its own positions would let the scoring
   model earn points on data that contradicts itself, which is the exact failure mode
   that makes a demo dishonest.

The tracks are also the input to the vessel scoring, so the five evidence patterns are
asserted to exist and to sit where the scorer expects them relative to the drift
envelope -- not at hard-coded coordinates, which would make the test a copy of the
implementation.
"""

from __future__ import annotations

import json
import math
import statistics
from datetime import timedelta

import numpy as np
import pytest

from spilltrace_common import config as C
from spilltrace_drift import ais as A
from spilltrace_drift import engine as E
from spilltrace_drift import forcing as F
from spilltrace_drift import scoring as S

CASE = "test-ais-00000"
# A patch of the Gulf of Mexico, which is where 392 of the 1200 supplied scenes sit.
BOUNDS = [-89.50, 28.40, -89.30, 28.60]
ACQUIRED = "2017-06-14T23:41:12Z"

REQUIRED_PATTERNS = {
    "origin_on_time",
    "origin_wrong_time",
    "course_match_far",
    "slowdown_near_origin",
    "transit_background",
}


@pytest.fixture(scope="module")
def backward():
    """A backward drift run, which is the substrate the AIS feed is built against."""
    forcing, _ = F.resolve_forcing(BOUNDS, ACQUIRED, CASE)
    lon, lat, seeding = E.seed_from_point(-89.40, 28.50, 1200.0, 240, 7)
    run = E.simulate(forcing, lon, lat, ACQUIRED, direction="backward", seeding=seeding)
    return run, forcing


@pytest.fixture(scope="module")
def feed(backward):
    run, forcing = backward
    return A.generate_ais(run, CASE, is_water=forcing.is_water)


@pytest.fixture(scope="module")
def envelope(backward):
    run, _ = backward
    return A.envelope_from_drift(run)


# ---------------------------------------------------------------------------
# Identity safety
# ---------------------------------------------------------------------------


def test_the_feed_labels_itself_as_synthetic_everywhere_it_can(feed):
    """The label is the PRD's exact string; nothing downstream may paraphrase it."""
    assert feed["label"] == C.LABEL_AIS == "AIS mode: Synthetic demonstration data"
    assert feed["mode"] == "synthetic"
    assert feed["disclaimer"] and feed["identifierNote"] and feed["nameNote"]

    for vessel in feed["vessels"]:
        assert vessel["synthetic"] is True
        for report in vessel["reports"]:
            assert report["synthetic"] is True


def test_identifiers_cannot_collide_with_a_real_vessel(feed):
    """MMSI 999xxxxxx is the reserved test range, and names are prefixed."""
    seen = set()
    for vessel in feed["vessels"]:
        mmsi = vessel["mmsi"]
        assert len(mmsi) == 9 and mmsi.isdigit()
        assert mmsi.startswith(A.SYNTHETIC_MMSI_PREFIX)
        assert mmsi not in seen, "two synthetic vessels share an MMSI"
        seen.add(mmsi)
        assert vessel["name"].startswith("SYNTHETIC DEMO ")


def test_mmsi_stride_stays_inside_the_range_for_far_more_vessels_than_we_generate():
    """The stride is coprime with the modulus, so indices cannot alias or overflow."""
    codes = {A._mmsi(i) for i in range(2000)}
    assert len(codes) == 2000
    assert all(c.startswith(A.SYNTHETIC_MMSI_PREFIX) and len(c) == 9 for c in codes)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_the_same_case_key_reproduces_the_same_feed_exactly(backward):
    """Offline demo mode depends on this: no live API, no run-to-run variation."""
    run, forcing = backward
    first = A.generate_ais(run, CASE, is_water=forcing.is_water)
    second = A.generate_ais(run, CASE, is_water=forcing.is_water)

    # generatedUtc is a wall-clock stamp, so compare everything else.
    for payload in (first, second):
        payload.pop("generatedUtc", None)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_a_different_case_key_produces_different_tracks(backward):
    """Otherwise every case in the demo would show identical traffic."""
    run, forcing = backward
    other = A.generate_ais(run, "test-ais-different", is_water=forcing.is_water)
    mine = A.generate_ais(run, CASE, is_water=forcing.is_water)

    a = [v["reports"][0]["lon"] for v in mine["vessels"]]
    b = [v["reports"][0]["lon"] for v in other["vessels"]]
    assert a != b


def test_determinism_does_not_depend_on_python_hash_randomisation(backward):
    """Seeds are derived through SHA-256, not the built-in hash()."""
    run, _ = backward
    assert C.stable_seed("ais:x:origin_on_time", 1) == C.stable_seed("ais:x:origin_on_time", 1)
    assert C.stable_seed("ais:x:origin_on_time", 1) != C.stable_seed("ais:x:origin_wrong_time", 1)


# ---------------------------------------------------------------------------
# Structure and volume
# ---------------------------------------------------------------------------


def test_the_feed_contains_every_evidence_pattern_the_scoring_needs(feed):
    patterns = {v["pattern"] for v in feed["vessels"]}
    assert REQUIRED_PATTERNS <= patterns
    assert set(feed["counts"]["patterns"]) == patterns
    assert feed["counts"]["vessels"] == len(feed["vessels"]) >= 10
    assert feed["counts"]["reports"] == sum(v["reportCount"] for v in feed["vessels"])


def test_every_report_is_a_physically_possible_ais_record(feed):
    for vessel in feed["vessels"]:
        for report in vessel["reports"]:
            assert -180.0 <= report["lon"] <= 180.0
            assert -90.0 <= report["lat"] <= 90.0
            assert 0.0 <= report["sogKn"] <= 45.0
            if report["cogDeg"] is not None:
                assert 0.0 <= report["cogDeg"] < 360.0
            if report.get("headingDeg") is not None:
                assert 0.0 <= report["headingDeg"] < 360.0
            # Class A carries a navigational status; Class B does not carry the field at all.
            # In a real MarineCadastre file that correlation is exact -- every blank Status
            # row is Class B -- so asserting a status on every report would be asserting
            # something real AIS never provides.
            if vessel["transceiverClass"] == "B":
                assert report["navStatus"] is None
                assert report["statusCode"] is None
            else:
                assert report["navStatus"]
                assert report["statusCode"] in (0, 5)


def test_reports_are_ordered_and_regularly_spaced(feed):
    interval = feed["config"]["reportIntervalS"]
    for vessel in feed["vessels"]:
        stamps = [A.parse_utc(r["timeUtc"]) for r in vessel["reports"]]
        assert stamps == sorted(stamps)
        gaps = [(b - a).total_seconds() for a, b in zip(stamps, stamps[1:])]
        # Dropouts create multiples of the interval, never a fraction of it.
        assert all(g >= interval - 1 for g in gaps)
        assert all(abs(g / interval - round(g / interval)) < 0.05 for g in gaps)


def test_vessel_types_are_drawn_from_the_declared_table(feed):
    for vessel in feed["vessels"]:
        assert vessel["typeKey"] in A.VESSEL_TYPES
        spec = A.VESSEL_TYPES[vessel["typeKey"]]
        assert vessel["type"] == spec.name
        assert vessel["typeRelevance"] == spec.relevance
        assert vessel["typeRationale"] == spec.rationale
        assert vessel["lengthM"] > 0


def test_the_whole_feed_survives_a_json_round_trip(feed):
    """The API serves this straight out; a numpy scalar in here would 500 the endpoint."""
    text = json.dumps(feed)
    assert json.loads(text)["counts"] == feed["counts"]
    assert len(text) < 2_000_000  # keeps the offline demo payload sane


# ---------------------------------------------------------------------------
# Self-consistency: declared speed against walked distance
# ---------------------------------------------------------------------------


def implied_speeds(reports) -> list[tuple[float, float]]:
    """(implied SOG, declared SOG) for each consecutive pair of reports."""
    out = []
    for a, b in zip(reports, reports[1:]):
        gap_h = (A.parse_utc(b["timeUtc"]) - A.parse_utc(a["timeUtc"])).total_seconds() / 3600.0
        if gap_h <= 0:
            continue
        km = A.haversine_km(a["lon"], a["lat"], b["lon"], b["lat"])
        out.append((km / gap_h / A.KM_PER_NAUTICAL_MILE, (a["sogKn"] + b["sogKn"]) / 2.0))
    return out


def test_declared_speed_matches_the_distance_actually_walked(feed):
    """The bug this catches: a plan whose duration and path length disagree.

    Position jitter and the deceleration ramp both perturb individual pairs, so the
    median is the meaningful statistic. A generator with the wrong path rate would show
    a systematic offset, not an occasional one.
    """
    for vessel in feed["vessels"]:
        pairs = implied_speeds(vessel["reports"])
        assert pairs
        errors = [abs(imp - dec) for imp, dec in pairs]
        assert statistics.median(errors) < 1.0, f"{vessel['name']} reports a speed it does not travel"
        # No single pair may imply a speed a ship cannot make.
        assert max(imp for imp, _ in pairs) < 45.0


def test_a_stopped_vessel_holds_its_position(feed):
    """A full stop must consume time without consuming path."""
    vessel = next(v for v in feed["vessels"] if v["pattern"] == "slowdown_near_origin")
    slow = [r for r in vessel["reports"] if r["sogKn"] < A.STOPPED_KN]
    assert len(slow) >= 3, "the stop is not visible in the feed"

    moved = [
        A.haversine_km(a["lon"], a["lat"], b["lon"], b["lat"])
        for a, b in zip(slow, slow[1:])
        if (A.parse_utc(b["timeUtc"]) - A.parse_utc(a["timeUtc"])).total_seconds() < 1200
    ]
    assert moved and max(moved) < 0.5  # jitter only, well under a knot of travel
    assert vessel["minSogKn"] < A.STOPPED_KN < vessel["medianSogKn"]


def test_a_partial_slowdown_eases_off_without_stopping(feed):
    """The harder case: a speed reduction that still leaves the vessel making way."""
    vessel = next(v for v in feed["vessels"] if v["pattern"] == "origin_on_time")
    assert vessel["minSogKn"] > A.STOPPED_KN, "this pattern should not stop dead"
    assert vessel["minSogKn"] <= A.SLOW_KN
    assert vessel["minSogKn"] < 0.6 * vessel["medianSogKn"]

    # It keeps moving through the slow section, unlike a stop.
    slow = [r for r in vessel["reports"] if r["sogKn"] <= A.SLOW_KN]
    assert len(slow) >= 3
    span = A.haversine_km(slow[0]["lon"], slow[0]["lat"], slow[-1]["lon"], slow[-1]["lat"])
    assert span > 0.5


# ---------------------------------------------------------------------------
# Placement relative to the drift envelope
# ---------------------------------------------------------------------------


def closest_approach_km(reports, env: A.EnvelopeTrack) -> tuple[float, int]:
    """Smallest time-matched distance to the envelope centre, and reports in the window."""
    best = math.inf
    inside = 0
    for report in reports:
        when = A.parse_utc(report["timeUtc"])
        if not env.covers(when):
            continue
        inside += 1
        lon, lat, _ = env.at(when)
        best = min(best, A.haversine_km(report["lon"], report["lat"], lon, lat))
    return best, inside


def closest_approach_radii(reports, env: A.EnvelopeTrack) -> tuple[float, int]:
    """The same closest approach, divided by the envelope's own radius at that instant.

    This is the quantity the scoring model actually gates on, and the only scale-free way
    to ask whether a vessel is "far away": ten kilometres is nothing beside a fifty
    kilometre uncertainty and decisive beside a two kilometre one.
    """
    best = math.inf
    inside = 0
    for report in reports:
        when = A.parse_utc(report["timeUtc"])
        if not env.covers(when):
            continue
        inside += 1
        lon, lat, radius = env.at(when)
        distance = A.haversine_km(report["lon"], report["lat"], lon, lat)
        best = min(best, distance / max(radius, 1e-6))
    return best, inside


def test_the_on_time_pattern_actually_enters_the_time_matched_envelope(feed, envelope):
    vessel = next(v for v in feed["vessels"] if v["pattern"] == "origin_on_time")
    distance, inside = closest_approach_km(vessel["reports"], envelope)
    assert inside > 0
    _, _, radius = envelope.at(A.parse_utc(vessel["reports"][0]["timeUtc"]))
    assert distance < radius, "the on-time vessel never reaches the envelope"


def test_the_wrong_time_pattern_is_absent_from_the_window(feed, envelope):
    """Same water, different hours: this is the control for the time component."""
    vessel = next(v for v in feed["vessels"] if v["pattern"] == "origin_wrong_time")
    _, inside = closest_approach_km(vessel["reports"], envelope)
    assert inside == 0

    first = A.parse_utc(vessel["reports"][0]["timeUtc"])
    assert first > envelope.window_end


def test_the_course_match_pattern_stays_far_away(feed, envelope):
    """The control for trajectory: right heading, impossible distance."""
    vessel = next(v for v in feed["vessels"] if v["pattern"] == "course_match_far")
    radii, inside = closest_approach_radii(vessel["reports"], envelope)
    assert inside > 0, "it must be present during the window, or it tests nothing"
    assert radii > S.IRRELEVANT_RADII, (
        "the far control must earn nothing on distance, or it stops being a control on "
        "trajectory alone"
    )


def test_background_traffic_is_present_but_never_near_the_envelope(feed, envelope):
    background = [v for v in feed["vessels"] if v["pattern"] == "transit_background"]
    assert len(background) >= 4
    for vessel in background:
        radii, _ = closest_approach_radii(vessel["reports"], envelope)
        assert radii > S.IRRELEVANT_RADII, (
            f"{vessel['name']} is close enough to muddy the ranking"
        )


def test_background_traffic_clears_the_gate_on_a_much_larger_envelope(backward):
    """The regression that matters: "far away" has to mean far away at any spill scale.

    Placement used to be drawn in fixed kilometres while scoring gates on envelope radii.
    On a small envelope the two agreed by luck; on a twenty-kilometre slick the relevance
    gate reached past the placement band and background traffic was promoted into the
    review list. Inflating the envelope here reproduces that geometry directly.
    """
    run, _ = backward
    env = A.envelope_from_drift(run)
    wide = A.EnvelopeTrack(
        times=env.times,
        lons=env.lons,
        lats=env.lats,
        radii_km=tuple(r * 6.0 for r in env.radii_km),
        observed=env.observed,
        origin_time=env.origin_time,
    )
    cfg = A.AisConfig()
    rng = np.random.default_rng(C.stable_seed(f"ais:{CASE}-wide", cfg.seed))
    plans = A.build_plans(wide, cfg, rng)

    checked = 0
    for plan in plans:
        if plan.pattern not in ("transit_background", "course_match_far"):
            continue
        _, radii = A._closest_to_envelope(plan.waypoints, plan.start, plan.end, wide)
        if radii is None:
            continue  # never inside the window, so never scored on distance at all
        checked += 1
        assert radii > S.IRRELEVANT_RADII, (
            f"{plan.key} sits {radii:.2f} envelope radii out on a "
            f"{wide.radii_km[0]:.0f} km envelope, inside the relevance gate"
        )
    assert checked >= 4


def test_the_envelope_track_is_monotonic_and_never_zero_radius(envelope):
    """A zero radius at the acquisition instant would make that instant infinitely
    discriminating, so the generator floors it."""
    assert list(envelope.times) == sorted(envelope.times)
    assert envelope.window_start < envelope.window_end
    assert float(np.min(envelope.radii_km)) >= 0.25
    assert np.isfinite(envelope.radii_km).all()

    mid = envelope.window_start + (envelope.window_end - envelope.window_start) / 2
    lon, lat, radius = envelope.at(mid)
    assert -180 <= lon <= 180 and -90 <= lat <= 90 and radius > 0
    # Queries outside the window clamp rather than extrapolate.
    assert envelope.at(envelope.window_start - timedelta(days=5))[2] > 0


def test_the_release_window_matches_the_drift_run(feed, envelope, backward):
    run, _ = backward
    window = feed["releaseWindow"]
    assert A.parse_utc(window["startUtc"]) == envelope.window_start
    assert A.parse_utc(window["endUtc"]) == envelope.window_end
    assert window["hours"] == pytest.approx(run["horizonHours"], rel=1e-6)
    assert window["basis"]


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------


def test_cleaning_rejects_duplicate_timestamps():
    cfg = A.AisConfig()
    base = {"timeUtc": "2017-06-14T12:00:00Z", "lon": -89.4, "lat": 28.5, "sogKn": 10.0}
    reports = [dict(base), dict(base), {**base, "timeUtc": "2017-06-14T12:10:00Z"}]

    kept, report = A.clean_reports(reports, cfg)

    assert len(kept) == 2
    assert report["rejected"]["duplicateTimestamp"] == 1
    assert report["rejectedTotal"] == 1


def test_cleaning_rejects_a_position_no_ship_could_reach():
    cfg = A.AisConfig()
    reports = [
        {"timeUtc": "2017-06-14T12:00:00Z", "lon": -89.4, "lat": 28.5, "sogKn": 10.0},
        {"timeUtc": "2017-06-14T12:10:00Z", "lon": -80.0, "lat": 28.5, "sogKn": 10.0},
        {"timeUtc": "2017-06-14T12:20:00Z", "lon": -89.4, "lat": 28.5, "sogKn": 10.0},
    ]

    kept, report = A.clean_reports(reports, cfg)

    assert report["rejected"]["impossibleSpeed"] == 1
    assert all(abs(r["lon"] + 89.4) < 1.0 for r in kept)


def test_cleaning_rejects_out_of_range_and_unparsable_records():
    cfg = A.AisConfig()
    reports = [
        {"timeUtc": "2017-06-14T12:00:00Z", "lon": -89.4, "lat": 28.5, "sogKn": 10.0},
        {"timeUtc": "2017-06-14T12:10:00Z", "lon": 999.0, "lat": 28.5, "sogKn": 10.0},
        {"timeUtc": "not a timestamp", "lon": -89.4, "lat": 28.5, "sogKn": 10.0},
    ]

    kept, report = A.clean_reports(reports, cfg)

    assert report["rejected"]["badPosition"] == 1
    assert report["rejected"]["unparsableTime"] == 1
    assert len(kept) == 1


def test_a_clean_feed_reports_no_rejections(feed):
    """The tally must not fire on the generator's own well-formed output."""
    for vessel in feed["vessels"]:
        assert vessel["cleaning"]["rejectedTotal"] == 0
        assert set(vessel["cleaning"]["rejected"]) == {
            "badPosition",
            "duplicateTimestamp",
            "impossibleSpeed",
            "unparsableTime",
        }


def test_cleaning_measures_gaps_and_completeness(feed):
    """The data-quality score reads these numbers, so they must reflect real defects."""
    dropped = next(v for v in feed["vessels"] if v["pattern"] == "slowdown_near_origin")
    clean = dropped["cleaning"]

    assert clean["largestGapMinutes"] >= feed["config"]["reportIntervalS"] / 60.0
    assert 0.0 < clean["reportCompleteness"] <= 1.0
    # This plan deliberately drops reports and omits a field, so neither is perfect.
    assert clean["reportCompleteness"] < 1.0
    assert clean["fieldCompleteness"] < 1.0
    assert clean["expectedReports"] >= dropped["reportCount"]

    intact = next(v for v in feed["vessels"] if v["pattern"] == "origin_on_time")
    assert intact["cleaning"]["reportCompleteness"] == pytest.approx(1.0)


def test_land_reports_are_counted_but_never_moved(feed):
    """A ~9 km forcing mask cannot adjudicate a 10 m AIS position, so it only informs."""
    for vessel in feed["vessels"]:
        assert 0 <= vessel["reportsOnLandMask"] <= vessel["reportCount"]


# ---------------------------------------------------------------------------
# GeoJSON export
# ---------------------------------------------------------------------------


def test_geojson_export_is_valid_and_still_labelled(feed):
    gj = A.to_geojson(feed)

    assert gj["type"] == "FeatureCollection"
    lines = [f for f in gj["features"] if f["geometry"]["type"] == "LineString"]
    points = [f for f in gj["features"] if f["geometry"]["type"] == "Point"]
    assert len(lines) == len(feed["vessels"])
    assert len(points) == 1  # the origin zone

    for feature in gj["features"]:
        assert feature["properties"]["synthetic"] is True
        coords = feature["geometry"]["coordinates"]
        pairs = coords if feature["geometry"]["type"] == "LineString" else [coords]
        for lon, lat in pairs:
            assert -180 <= lon <= 180 and -90 <= lat <= 90

    json.dumps(gj)


def test_track_line_follows_the_reports(feed):
    vessel = feed["vessels"][0]
    line = vessel["track"]
    assert len(line) == vessel["reportCount"]
    assert line[0] == [vessel["reports"][0]["lon"], vessel["reports"][0]["lat"]]


# ---------------------------------------------------------------------------
# Configuration guards
# ---------------------------------------------------------------------------


def test_a_plan_with_no_duration_is_rejected_rather_than_silently_empty():
    cfg = A.AisConfig()
    start = A.parse_utc("2017-06-14T12:00:00Z")
    plan = A.VesselPlan(
        key="degenerate",
        pattern="transit_background",
        type_key="fishing",
        waypoints=[A.Leg(-89.4, 28.5, 8.0), A.Leg(-89.3, 28.5, 8.0)],
        start=start,
        end=start,
    )
    with pytest.raises(ValueError):
        A.realise_track(plan, cfg, np.random.default_rng(0))


def test_report_count_is_bounded_on_both_sides():
    """A long plan must not emit an unbounded feed, and a short one must not emit two
    points and call it a track."""
    cfg = A.AisConfig(report_interval_s=600, min_reports=12, max_reports=60)
    start = A.parse_utc("2017-06-14T00:00:00Z")
    legs = [A.Leg(-89.4, 28.5, 10.0), A.Leg(-89.0, 28.5, 10.0)]

    long_plan = A.VesselPlan(
        key="long", pattern="transit_background", type_key="fishing",
        waypoints=legs, start=start, end=start + timedelta(days=10),
    )
    short_plan = A.VesselPlan(
        key="short", pattern="transit_background", type_key="fishing",
        waypoints=legs, start=start, end=start + timedelta(minutes=20),
    )

    long_reports = A.realise_track(long_plan, cfg, np.random.default_rng(1))
    short_reports = A.realise_track(short_plan, cfg, np.random.default_rng(2))

    assert len(long_reports) == cfg.max_reports + 1
    assert len(short_reports) == cfg.min_reports + 1
