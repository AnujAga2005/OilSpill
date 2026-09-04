"""Tests for the explainable vessel scoring.

The scoring is the part of this system most capable of doing harm: it ranks named
vessels against an oil spill. Three properties therefore matter more than any accuracy
number, and each has tests here:

1. **Language.** No output may assert responsibility. Every entry is a
   ``Priority candidate for investigation`` and nothing else, at every level of the
   payload and in both export formats.
2. **No free points.** A vessel must not score on a component the data cannot support.
   Background traffic tens of kilometres away has to come out at zero on distance, time
   and trajectory -- the failure mode where "was present somewhere during a 24-hour
   window" silently earns a quarter of the total is tested explicitly, because an earlier
   version of this module had exactly that bug.
3. **Discrimination.** The four planted evidence patterns must separate in the intended
   order, or the explanation is decorative.

Distances and times are asserted against the drift envelope rather than against
hard-coded values, so the tests still mean something if the forcing changes.
"""

from __future__ import annotations

import csv
import io
import json
import math
from datetime import timedelta

import pytest

from spilltrace_common import config as C
from spilltrace_drift import ais as A
from spilltrace_drift import engine as E
from spilltrace_drift import forcing as F
from spilltrace_drift import scoring as S

CASE = "test-scoring-00000"
BOUNDS = [-89.50, 28.40, -89.30, 28.60]
ACQUIRED = "2017-06-14T23:41:12Z"

COMPONENTS = ("Distance", "Time", "Trajectory", "Behaviour", "Type", "Data quality")


def by_pattern(ranking, pattern):
    return next(c for c in ranking["candidates"] if c["pattern"] == pattern)


def reason_for(candidate, component: str) -> str:
    """The prose the UI shows beside one component's number."""
    entry = next(e for e in candidate["componentDetail"] if e["component"] == component)
    return entry["reason"]


@pytest.fixture(scope="module")
def context():
    forcing, _ = F.resolve_forcing(BOUNDS, ACQUIRED, CASE)
    lon, lat, seeding = E.seed_from_point(-89.40, 28.50, 1200.0, 240, 7)
    backward = E.simulate(forcing, lon, lat, ACQUIRED, direction="backward", seeding=seeding)
    feed = A.generate_ais(backward, CASE, is_water=forcing.is_water)
    return backward, feed


@pytest.fixture(scope="module")
def ranking(context):
    backward, feed = context
    return S.rank_vessels(feed, backward)


# ---------------------------------------------------------------------------
# Language: nothing may assert responsibility
# ---------------------------------------------------------------------------


def test_every_candidate_carries_the_required_status_and_nothing_stronger(ranking):
    assert C.LABEL_CANDIDATE == "Priority candidate for investigation"
    for candidate in ranking["candidates"]:
        assert candidate["status"] == C.LABEL_CANDIDATE
    assert ranking["candidateLabel"] == C.LABEL_CANDIDATE
    assert ranking["status"] == C.LABEL_STATUS


def test_no_output_text_ever_uses_the_language_of_guilt(ranking):
    """A blunt substring sweep over everything the UI could render."""
    forbidden = (
        "guilty",
        "culprit",
        "responsible for",
        "confirmed responsible",
        "the polluter",
        "proven",
        "convicted",
        "perpetrator",
        "at fault",
        "caused the spill",
    )
    haystack = json.dumps(ranking).lower()
    for word in forbidden:
        assert word not in haystack, f"scoring output contains {word!r}"

    # The explanation blocks are rendered separately, so sweep those too.
    for candidate in ranking["candidates"]:
        block = S.explanation_block(candidate).lower()
        for word in forbidden:
            assert word not in block


def test_the_ranking_states_its_own_provenance_and_limits(ranking):
    assert ranking["aisLabel"] == C.LABEL_AIS
    # Any of the four: currents and wind are separate products and either can be real on
    # its own, so there is no single "real" and no single "synthetic" label to assert.
    assert ranking["driftLabel"] in (
        C.LABEL_DRIFT_SYNTHETIC,
        C.LABEL_DRIFT_CMEMS,
        C.LABEL_DRIFT_HYBRID,
        C.LABEL_DRIFT_REAL,
    )
    assert ranking["caveat"] == S.CAVEAT
    for phrase in ("triage", "not evidence", "human verification"):
        assert phrase in ranking["caveat"].lower()


def test_the_band_descriptions_never_promise_more_than_overlap(ranking):
    for candidate in ranking["candidates"]:
        band = candidate["band"].lower()
        assert "overlap" in band or "review" in band or "completeness" in band


# ---------------------------------------------------------------------------
# Weights and component arithmetic
# ---------------------------------------------------------------------------


def test_the_weights_are_the_ones_the_specification_fixes():
    weights = C.ScoringWeights()
    assert weights.distance == 30
    assert weights.time_window == 25
    assert weights.trajectory == 20
    assert weights.behaviour == 10
    assert weights.vessel_type == 10
    assert weights.data_completeness == 5
    assert weights.total == 100


def test_no_component_can_exceed_its_weight_and_the_total_is_their_sum(ranking):
    weights = ranking["weights"]
    maxima = {
        "Distance": weights["distance"],
        "Time": weights["timeWindow"],
        "Trajectory": weights["trajectory"],
        "Behaviour": weights["behaviour"],
        "Type": weights["vesselType"],
        "Data quality": weights["dataCompleteness"],
    }
    for candidate in ranking["candidates"]:
        assert set(candidate["components"]) == set(COMPONENTS)
        for name, earned in candidate["components"].items():
            assert 0.0 <= earned <= maxima[name] + 1e-9, f"{name} exceeded its weight"
        total = sum(candidate["components"].values())
        assert candidate["score"] == pytest.approx(total, abs=0.05)
        assert candidate["scoreMax"] == sum(maxima.values()) == 100


def test_every_component_explains_itself_in_words(ranking):
    """An unexplained number is not usable by an analyst, which is the whole point."""
    for candidate in ranking["candidates"]:
        assert len(candidate["explanation"]) == len(COMPONENTS)
        detail_list = candidate["componentDetail"]
        # componentDetail is a list of {component, score, max, reason} dicts.
        names = {entry["component"] for entry in detail_list}
        assert names == set(COMPONENTS)
        for entry in detail_list:
            assert entry["reason"], f"{entry['component']} scored without a reason"
            assert entry["score"] == pytest.approx(
                candidate["components"][entry["component"]], abs=0.05
            )
            assert entry["max"] > 0
        for line in candidate["explanation"]:
            assert " - " in line and "/" in line


def test_the_method_block_documents_every_component(ranking):
    method = ranking["method"]
    assert len(method) >= len(COMPONENTS)
    assert all(isinstance(text, str) and text for text in method.values())


# ---------------------------------------------------------------------------
# No free points
# ---------------------------------------------------------------------------


def test_distant_background_traffic_scores_nothing_on_geometry_or_timing(ranking):
    """The regression: presence during the window is not evidence of proximity.

    Traffic placed outside the search corridor has to earn zero on distance, time and
    trajectory. What it may still earn is its cargo relevance and the completeness of its
    own feed, neither of which is a claim about this spill. The distance bound is written
    in envelope radii rather than kilometres because that is what the model gates on: the
    same 30 km pass is decisive beside a 2 km uncertainty and meaningless beside a 40 km
    one.
    """
    background = [c for c in ranking["candidates"] if c["pattern"] == "transit_background"]
    assert background
    for candidate in background:
        radii = candidate["evidence"]["closestApproachRadii"]
        assert radii is None or radii > S.IRRELEVANT_RADII, (
            f"{candidate['name']} is inside the relevance gate and is not background traffic"
        )
        assert candidate["components"]["Distance"] == 0.0
        assert candidate["components"]["Time"] == 0.0
        assert candidate["components"]["Trajectory"] == 0.0
        assert candidate["components"]["Behaviour"] == 0.0
        # Type and data quality are properties of the vessel and its feed, not of the
        # spill, so they are the only things left.
        assert candidate["score"] == pytest.approx(
            candidate["components"]["Type"] + candidate["components"]["Data quality"], abs=0.05
        )
        assert candidate["score"] < 20.0


def test_background_traffic_never_outranks_a_patterned_vessel(ranking):
    """Ranking order is the product: unrelated traffic must not reach the review list.

    This is the end-to-end form of the placement regression. A background vessel scoring
    above a patterned one means the demonstration is arguing the wrong thing, however
    defensible each individual component is.
    """
    background = [c["score"] for c in ranking["candidates"] if c["pattern"] == "transit_background"]
    patterned = [c["score"] for c in ranking["candidates"] if c["pattern"] != "transit_background"]
    assert background and patterned
    assert max(background) < min(patterned)


def test_a_matching_course_far_away_earns_no_trajectory_points(ranking):
    """Trajectory alone must not carry a score: this vessel runs along the drift axis."""
    candidate = by_pattern(ranking, "course_match_far")
    assert candidate["evidence"]["reportsInWindow"] > 0, "the control is only meaningful in-window"
    assert candidate["evidence"]["closestApproachRadii"] > S.IRRELEVANT_RADII
    assert candidate["components"]["Trajectory"] == 0.0
    assert candidate["components"]["Distance"] == 0.0
    reason = reason_for(candidate, "Trajectory").lower()
    assert "distance" in reason or "never" in reason


def test_presence_in_the_wrong_hours_earns_no_time_points(ranking):
    """Same water, after the acquisition: the time component is the only discriminator."""
    candidate = by_pattern(ranking, "origin_wrong_time")
    assert candidate["evidence"]["reportsInWindow"] == 0
    assert candidate["components"]["Time"] == 0.0
    assert candidate["components"]["Distance"] == 0.0
    # It should still be reported, with its near-miss recorded rather than hidden.
    assert candidate["evidence"]["nearestPassOutsideWindowKm"] is not None
    assert candidate["evidence"]["nearestPassOutsideWindowKm"] < 25.0


def test_a_vessel_with_no_reports_at_all_scores_zero_without_crashing(context):
    backward, feed = context
    env = A.envelope_from_drift(backward)
    empty = dict(feed["vessels"][0])
    empty["reports"] = []
    empty["track"] = []
    empty["reportCount"] = 0
    empty["cleaning"] = {
        "reportCompleteness": 0.0,
        "fieldCompleteness": 0.0,
        "rejectedTotal": 0,
        "rejected": {},
        "gaps": [],
        "largestGapMinutes": 0.0,
        "expectedReports": 0,
    }

    scored = S.score_vessel(empty, env)

    assert scored["components"]["Distance"] == 0.0
    assert scored["components"]["Time"] == 0.0
    assert scored["components"]["Trajectory"] == 0.0
    assert scored["components"]["Behaviour"] == 0.0
    assert scored["components"]["Data quality"] == 0.0
    assert scored["status"] == C.LABEL_CANDIDATE
    assert scored["evidence"]["closestApproachKm"] is None


# ---------------------------------------------------------------------------
# Discrimination
# ---------------------------------------------------------------------------


def test_the_on_time_origin_pattern_ranks_first(ranking):
    """If the planted signal does not win, the scoring is not measuring what it claims."""
    top = ranking["candidates"][0]
    assert top["rank"] == 1
    assert top["pattern"] == "origin_on_time"
    assert top["components"]["Distance"] == pytest.approx(ranking["weights"]["distance"])
    assert top["components"]["Type"] == pytest.approx(ranking["weights"]["vesselType"])
    assert top["score"] > 70.0


def test_the_two_near_patterns_outrank_everything_else(ranking):
    near = {"origin_on_time", "slowdown_near_origin"}
    scores = {c["pattern"]: c["score"] for c in ranking["candidates"]}
    worst_near = min(scores[p] for p in near)
    best_other = max(score for pattern, score in scores.items() if pattern not in near)
    assert worst_near > best_other + 10.0


def test_a_stop_beside_the_zone_earns_full_behaviour_marks(ranking):
    candidate = by_pattern(ranking, "slowdown_near_origin")
    assert candidate["components"]["Behaviour"] == pytest.approx(ranking["weights"]["behaviour"])
    assert candidate["evidence"]["sogAtApproachKn"] is not None
    reason = reason_for(candidate, "Behaviour").lower()
    assert "kn" in reason


def test_a_partial_slowdown_earns_partial_behaviour_marks(ranking):
    """Easing off is weaker evidence than stopping, and must score as such."""
    partial = by_pattern(ranking, "origin_on_time")
    stopped = by_pattern(ranking, "slowdown_near_origin")
    behaviour_max = ranking["weights"]["behaviour"]
    assert 0.0 < partial["components"]["Behaviour"] < behaviour_max
    assert partial["components"]["Behaviour"] < stopped["components"]["Behaviour"]


def test_behaviour_is_deliberately_a_small_share_of_the_total(ranking):
    """Ships slow down for many innocent reasons, so this must not dominate."""
    weights = ranking["weights"]
    assert weights["behaviour"] <= 0.15 * 100
    assert weights["distance"] + weights["timeWindow"] >= 0.5 * 100


def test_scores_are_distinct_enough_to_be_a_ranking(ranking):
    scores = [c["score"] for c in ranking["candidates"]]
    assert len(set(scores)) >= 5
    assert scores == sorted(scores, reverse=True)
    assert ranking["candidateCount"] == len(ranking["candidates"])
    assert [c["rank"] for c in ranking["candidates"]] == list(range(1, len(scores) + 1))


def test_cargo_relevance_orders_the_vessel_type_component(ranking):
    """An oil tanker is physically capable of an oil discharge; a ferry much less so."""
    tanker = by_pattern(ranking, "origin_on_time")
    assert tanker["typeKey"] == "oil_tanker"
    assert tanker["components"]["Type"] == pytest.approx(ranking["weights"]["vesselType"])

    for candidate in ranking["candidates"]:
        expected = ranking["weights"]["vesselType"] * A.VESSEL_TYPES[candidate["typeKey"]].relevance
        assert candidate["components"]["Type"] == pytest.approx(expected, abs=0.05)


def test_ordering_is_stable_and_reproducible(context):
    """Two runs of the same case must present the same list in the same order."""
    backward, feed = context
    first = S.rank_vessels(feed, backward)
    second = S.rank_vessels(feed, backward)
    assert [(c["mmsi"], c["score"]) for c in first["candidates"]] == [
        (c["mmsi"], c["score"]) for c in second["candidates"]
    ]


def test_ties_are_broken_deterministically_not_by_dictionary_order(context):
    """Identical vessels must not swap places between runs."""
    backward, feed = context
    twinned = dict(feed)
    clone = json.loads(json.dumps(feed["vessels"][-1]))
    clone["mmsi"] = "999999999"
    clone["name"] = "SYNTHETIC DEMO ZULU"
    twinned["vessels"] = list(feed["vessels"]) + [clone]

    order = [
        [c["mmsi"] for c in S.rank_vessels(twinned, backward)["candidates"]] for _ in range(3)
    ]
    assert order[0] == order[1] == order[2]


# ---------------------------------------------------------------------------
# Closest approach: the geometry the whole score rests on
# ---------------------------------------------------------------------------


def test_closest_approach_is_measured_against_the_time_matched_envelope(context):
    """The key design decision: a vessel is compared to where the oil was *at its own
    timestamp*, not to a single static origin point."""
    backward, feed = context
    env = A.envelope_from_drift(backward)
    vessel = next(v for v in feed["vessels"] if v["pattern"] == "origin_on_time")

    approach = S.closest_approach(vessel["reports"], env)

    assert approach.at_utc is not None
    when = A.parse_utc(approach.at_utc)
    assert env.window_start <= when <= env.window_end

    lon, lat, radius = env.at(when)
    matching = min(
        vessel["reports"],
        key=lambda r: abs((A.parse_utc(r["timeUtc"]) - when).total_seconds()),
    )
    expected = A.haversine_km(matching["lon"], matching["lat"], lon, lat)
    # The stored values are rounded for display, so compare at that resolution.
    assert approach.distance_km == pytest.approx(expected, abs=1e-3)
    assert approach.radius_km == pytest.approx(radius, abs=1e-3)
    assert approach.radii == pytest.approx(expected / radius, abs=1e-3)


def test_proximity_falls_off_between_one_and_three_envelope_radii():
    inside = S.Approach(
        distance_km=1.0, radii=0.5, radius_km=2.0, at_utc="2017-06-14T00:00:00Z",
        inside_window=True, reports_in_window=5, reports_relevant=5, reports_total=5,
        window_minutes=60.0, nearest_outside_km=None, course_deg=10.0, sog_kn=9.0,
    )
    edge = S.Approach(**{**inside.__dict__, "radii": S.IRRELEVANT_RADII})
    midway = S.Approach(**{**inside.__dict__, "radii": 2.0})
    absent = S.Approach(**{**inside.__dict__, "radii": None})

    assert inside.proximity == 1.0
    assert edge.proximity == 0.0
    assert midway.proximity == pytest.approx(0.5)
    assert absent.proximity == 0.0
    # Beyond the irrelevance radius it must clamp, not go negative.
    assert S.Approach(**{**inside.__dict__, "radii": 40.0}).proximity == 0.0


def test_distance_is_scored_in_envelope_radii_not_kilometres(context):
    """A confident hindcast must tighten the score; a diffuse one must loosen it.

    The same 6 km pass scores full marks against a 10 km envelope and nothing against a
    1 km one, because the envelope radius *is* the uncertainty.
    """
    backward, _ = context
    env = A.envelope_from_drift(backward)
    when = env.window_start + (env.window_end - env.window_start) / 2
    lon, lat, radius = env.at(when)

    per_lon, _ = A.metres_per_degree(lat)
    near = [{"timeUtc": A.iso_utc(when), "lon": lon + (0.4 * radius * 1000.0) / per_lon,
             "lat": lat, "sogKn": 10.0, "cogDeg": 90.0}]
    far = [{"timeUtc": A.iso_utc(when), "lon": lon + (8.0 * radius * 1000.0) / per_lon,
            "lat": lat, "sogKn": 10.0, "cogDeg": 90.0}]

    weights = C.ScoringWeights()
    near_score = S.score_distance(S.closest_approach(near, env), weights)
    far_score = S.score_distance(S.closest_approach(far, env), weights)

    assert near_score["score"] == pytest.approx(weights.distance)
    assert far_score["score"] == 0.0


# ---------------------------------------------------------------------------
# Traffic filtering: the counts on both sides of the filter must agree
# ---------------------------------------------------------------------------


def test_the_funnel_counts_add_up_to_the_traffic_seen(ranking):
    """The point of publishing a funnel is that it can be checked by eye.

    If these three groups did not partition the vessels, the interface would be showing
    an operator a shortlist and an exclusion count that describe different populations.
    """
    funnel = ranking["filtering"]
    assert funnel["vesselsSeen"] == len(ranking["candidates"])
    assert funnel["vesselsSeen"] == (
        funnel["vesselsRelevant"] + funnel["excludedOutsideWindow"] + funnel["excludedTooFar"]
    )
    assert funnel["excludedTotal"] == funnel["excludedOutsideWindow"] + funnel["excludedTooFar"]
    assert funnel["vesselsInWindow"] == funnel["vesselsSeen"] - funnel["excludedOutsideWindow"]
    assert funnel["vesselsRelevant"] <= funnel["vesselsInWindow"]
    assert ranking["relevantCount"] == funnel["vesselsRelevant"]
    assert ranking["excludedCount"] == funnel["excludedTotal"]


def test_the_funnel_report_counts_match_the_feed_it_describes(ranking, context):
    """Report totals are re-derived from the feed, not from the scored entries."""
    _, feed = context
    funnel = ranking["filtering"]
    assert funnel["aisReports"] == sum(len(v["reports"]) for v in feed["vessels"])
    assert funnel["reportsNearEnvelope"] <= funnel["reportsInWindow"] <= funnel["aisReports"]
    assert funnel["irrelevantRadii"] == S.IRRELEVANT_RADII
    assert funnel["insideRadii"] == S.INSIDE_RADII


def test_the_funnel_summary_states_every_stage_of_the_filter(ranking):
    """A slide screenshot of this one line has to be defensible on its own."""
    funnel = ranking["filtering"]
    summary = funnel["summary"]
    for number in (
        funnel["aisReports"],
        funnel["vesselsSeen"],
        funnel["vesselsInWindow"],
        funnel["vesselsRelevant"],
        funnel["excludedTotal"],
    ):
        assert str(number) in summary
    assert "irrelevant traffic" in summary
    assert "filter" in funnel["retentionNote"].lower()
    assert "envelope radii" in funnel["rule"]


def test_filtering_out_means_setting_aside_and_never_deleting(ranking):
    """Every vessel in the feed survives to the output, tagged with its verdict.

    A filter that dropped rows would make the scoring unauditable: the cheapest way to
    hide a ranking bug is to delete the vessels it mis-ranked.
    """
    for candidate in ranking["candidates"]:
        assert isinstance(candidate["relevant"], bool)
        assert candidate["relevanceReason"]
        assert candidate["status"] == C.LABEL_CANDIDATE
    assert any(c["relevant"] for c in ranking["candidates"])
    assert any(not c["relevant"] for c in ranking["candidates"])


def test_relevance_sorts_ahead_of_score(context):
    """A vessel that was never near the oil must not outrank one that was.

    Scored with the type and completeness weights carrying the whole total, background
    traffic can out-collect a relevant vessel on marks alone. The sort key exists so that
    it still cannot reach the top of the list.
    """
    backward, feed = context
    weights = C.ScoringWeights(
        distance=1, time_window=1, trajectory=1, behaviour=1, vessel_type=48, data_completeness=48
    )
    ranked = S.rank_vessels(feed, backward, weights=weights)

    flags = [c["relevant"] for c in ranked["candidates"]]
    assert flags == sorted(flags, reverse=True), "an excluded vessel ranked above a relevant one"
    # And within each group the ordering is still by score.
    for group in (True, False):
        scores = [c["score"] for c in ranked["candidates"] if c["relevant"] is group]
        assert scores == sorted(scores, reverse=True)


def test_the_two_ways_of_being_irrelevant_are_reported_differently(ranking):
    """Somewhere else in time and somewhere else in space are different findings, and
    only one of them is a reason to go looking for another data source."""
    outside = [
        c for c in ranking["candidates"]
        if not c["relevant"] and not c["evidence"]["reportsInWindow"]
    ]
    too_far = [
        c for c in ranking["candidates"]
        if not c["relevant"] and c["evidence"]["reportsInWindow"]
    ]
    assert outside and too_far, "the fixture must plant both kinds of irrelevant traffic"

    for candidate in outside:
        assert "release window" in candidate["relevanceReason"]
        assert "no AIS reports" in candidate["relevanceReason"]
    for candidate in too_far:
        reason = candidate["relevanceReason"]
        assert "envelope radii" in reason
        assert "km away" in reason
    for candidate in ranking["candidates"]:
        if candidate["relevant"]:
            assert "fall inside the release window" in candidate["relevanceReason"]


def test_relevance_is_decided_by_reports_that_are_near_and_in_window():
    """`relevance` reads one field for the verdict, so the field must be the right one:
    reports in the window alone is the free-points bug this module was built to avoid."""
    base = dict(
        distance_km=4.0, radii=2.0, radius_km=2.0, at_utc="2017-06-14T00:00:00Z",
        inside_window=True, reports_in_window=5, reports_relevant=0, reports_total=40,
        window_minutes=60.0, nearest_outside_km=None, course_deg=10.0, sog_kn=9.0,
    )
    near = S.Approach(**{**base, "reports_relevant": 3})
    in_window_only = S.Approach(**base)
    absent = S.Approach(**{**base, "reports_in_window": 0, "inside_window": False})
    absent_but_close = S.Approach(**{**absent.__dict__, "nearest_outside_km": 1.2})

    assert S.relevance(near)[0] is True
    assert S.relevance(in_window_only)[0] is False
    assert S.relevance(absent)[0] is False
    assert "1.2 km away but outside the window" in S.relevance(absent_but_close)[1]


def test_the_csv_carries_the_filter_verdict_for_every_row(ranking):
    """The download is the only copy of the shortlist without the interface's tags on it,
    so the verdict has to travel inside the file."""
    rows = list(csv.DictReader(io.StringIO(S.candidates_csv(ranking))))
    assert [row["relevant_traffic"] for row in rows] == [
        "yes" if c["relevant"] else "no" for c in ranking["candidates"]
    ]
    for row, candidate in zip(rows, ranking["candidates"]):
        assert row["relevance_reason"] == candidate["relevanceReason"]


# ---------------------------------------------------------------------------
# Data completeness scores the feed, never the vessel
# ---------------------------------------------------------------------------


def test_data_quality_reflects_feed_defects_not_suspicion(ranking):
    dropped = by_pattern(ranking, "slowdown_near_origin")
    intact = by_pattern(ranking, "origin_on_time")

    assert dropped["components"]["Data quality"] < intact["components"]["Data quality"]
    reason = reason_for(dropped, "Data quality").lower()
    assert "report" in reason
    # It describes the data, so it must not describe the ship's conduct.
    for word in ("suspicious", "evasive", "concealed", "went dark"):
        assert word not in reason


def test_rejected_records_reduce_the_quality_score_but_not_below_a_floor(context):
    backward, feed = context
    env = A.envelope_from_drift(backward)
    weights = C.ScoringWeights()

    clean = {"reportCompleteness": 1.0, "fieldCompleteness": 1.0, "rejectedTotal": 0}
    dirty = {"reportCompleteness": 1.0, "fieldCompleteness": 1.0, "rejectedTotal": 40}

    good = S.score_completeness({"cleaning": clean, "reportCount": 30}, weights)
    bad = S.score_completeness({"cleaning": dirty, "reportCount": 30}, weights)

    assert good["score"] == pytest.approx(weights.data_completeness)
    assert 0.0 < bad["score"] <= 0.5 * weights.data_completeness + 1e-9


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------


def test_the_csv_export_round_trips_and_keeps_the_labels(ranking):
    text = S.candidates_csv(ranking)
    rows = list(csv.DictReader(io.StringIO(text)))

    assert len(rows) == len(ranking["candidates"])
    assert "status" in rows[0] and "ais_mode" in rows[0]
    for row, candidate in zip(rows, ranking["candidates"]):
        assert row["mmsi"] == candidate["mmsi"]
        assert row["status"] == C.LABEL_CANDIDATE
        assert row["ais_mode"] == C.LABEL_AIS
        assert float(row["total_score"]) == pytest.approx(candidate["score"])
        assert int(row["rank"]) == candidate["rank"]


def test_the_csv_export_cannot_be_broken_by_a_comma_or_a_quote(ranking):
    """Vessel names are generated here, but the exporter must not assume that."""
    hostile = json.loads(json.dumps(ranking))
    hostile["candidates"][0]["name"] = 'SYNTHETIC DEMO "ALPHA", stern trawler'
    hostile["candidates"][0]["type"] = "Oil tanker\nnewline"

    rows = list(csv.DictReader(io.StringIO(S.candidates_csv(hostile))))

    assert len(rows) == len(hostile["candidates"])
    assert rows[0]["name"] == 'SYNTHETIC DEMO "ALPHA", stern trawler'


def test_the_whole_ranking_serialises_for_the_api(ranking):
    text = json.dumps(ranking)
    assert json.loads(text)["candidateCount"] == ranking["candidateCount"]


def test_the_explanation_block_reads_as_an_analyst_summary(ranking):
    block = S.explanation_block(ranking["candidates"][0])
    lines = block.splitlines()

    assert C.LABEL_CANDIDATE in lines[0]
    assert lines[1].startswith("Total:")
    assert "/100" in lines[1]
    for name in COMPONENTS:
        assert any(line.startswith(f"{name}:") for line in lines), f"{name} missing"


# ---------------------------------------------------------------------------
# Weight overrides
# ---------------------------------------------------------------------------


def test_custom_weights_are_honoured_and_reported(context):
    """The UI exposes these, so a changed weight must actually change the arithmetic."""
    backward, feed = context
    weights = C.ScoringWeights(
        distance=50, time_window=20, trajectory=10, behaviour=10, vessel_type=5, data_completeness=5
    )
    assert weights.total == 100

    ranked = S.rank_vessels(feed, backward, weights=weights)

    assert ranked["weights"]["distance"] == 50
    top = ranked["candidates"][0]
    assert top["components"]["Distance"] <= 50.0
    assert top["components"]["Trajectory"] <= 10.0
    assert top["scoreMax"] == 100


def test_confidence_bands_are_ordered_and_never_assign_responsibility():
    maximum = 100.0
    bands = [S.confidence_band(score, maximum) for score in (5.0, 30.0, 55.0, 85.0)]
    assert len(set(bands)) == 4
    for band in bands:
        lowered = band.lower()
        assert "guilty" not in lowered and "responsible" not in lowered
