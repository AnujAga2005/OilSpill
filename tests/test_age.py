"""Tests for the spill-age estimate.

The problem statement asks for the spill's age, and this module's whole value is that it
refuses to answer with a number the data cannot support. So the tests here are mostly
about the refusal:

1. **The bound is the horizon, and nothing wider.** A lower bound of zero is the honest
   floor from one acquisition -- nothing in a single image rules out oil that entered the
   water minutes earlier -- and the upper bound must be the distance the model actually
   integrated, not a round figure.
2. **Resolvability is measured, not asserted.** The separation test is exercised on
   hand-built timelines in both directions, so a change that makes every case
   "resolvable" (or none of them) fails here rather than in a demo.
3. **The prose matches the arithmetic.** The label, the basis sentence and the note are
   read by a judge next to the numbers, so they are checked against them.

The timelines are constructed by hand where the property under test is arithmetic, and
taken from a real backward run where the property is about the pipeline agreeing with
itself.
"""

from __future__ import annotations

import json
import math

import pytest

from spilltrace_drift import age as AGE
from spilltrace_drift import engine as E
from spilltrace_drift import forcing as F
from spilltrace_drift.ais import haversine_km

CASE = "test-age-00000"
BOUNDS = [-89.50, 28.40, -89.30, 28.60]
ACQUIRED = "2017-06-14T23:41:12Z"


@pytest.fixture(scope="module")
def backward():
    """A real backward run, so the module is tested against the shape it is given."""
    forcing, _ = F.resolve_forcing(BOUNDS, ACQUIRED, CASE)
    lon, lat, seeding = E.seed_from_point(-89.40, 28.50, 1200.0, 240, 7)
    return E.simulate(forcing, lon, lat, ACQUIRED, direction="backward", seeding=seeding)


def timeline(*steps: tuple[float, float, float]) -> list[dict[str, object]]:
    """A minimal timeline: (hours back, kilometres east of the origin, P90 radius km)."""
    lat = 28.5
    per_km = 1.0 / (111.320 * math.cos(math.radians(lat)))
    return [
        {
            "stepIndex": index,
            "hoursFromObservation": -hours,
            "centroid": [-89.40 + east * per_km, lat],
            "spreadP90Km": radius,
        }
        for index, (hours, east, radius) in enumerate(steps)
    ]


def run(steps, horizon: float = 24.0, observed: str = ACQUIRED) -> dict[str, object]:
    return {"horizonHours": horizon, "observedUtc": observed, "timeline": steps}


# ---------------------------------------------------------------------------
# The interval itself
# ---------------------------------------------------------------------------


def test_the_upper_bound_is_the_horizon_the_model_actually_integrated(backward):
    result = AGE.estimate(backward)
    assert result["maxHours"] == pytest.approx(round(backward["horizonHours"], 2))
    assert result["minHours"] == 0.0
    assert str(int(result["maxHours"])) in result["basis"]
    assert "horizon" in result["basis"]


def test_the_lower_bound_is_zero_because_one_image_cannot_rule_out_a_fresh_release(backward):
    """A non-zero floor would be a claim about oil that was never observed absent."""
    result = AGE.estimate(backward)
    assert result["minHours"] == 0.0
    assert result["label"].startswith("Estimated spill age: up to ")
    assert "at acquisition" in result["label"]


def test_the_window_runs_from_the_horizon_to_the_acquisition(backward):
    result = AGE.estimate(backward)
    assert result["acquiredUtc"] == backward["observedUtc"]
    assert result["earliestReleaseUtc"] == backward["timeline"][-1]["timeUtc"]


def test_a_run_without_an_observation_time_still_produces_an_interval():
    """Cases are assembled stage by stage, so the estimate must not raise on a partial one."""
    result = AGE.estimate(run(timeline((0.0, 0.0, 10.0)), observed=None))
    assert result["maxHours"] == 24.0
    assert result["acquiredUtc"] is None
    assert result["earliestReleaseUtc"] is None


def test_an_unparseable_observation_time_does_not_take_the_estimate_down():
    result = AGE.estimate(run(timeline((0.0, 0.0, 10.0)), observed="not a timestamp"))
    assert result["earliestReleaseUtc"] is None
    assert result["maxHours"] == 24.0


def test_an_empty_drift_run_reports_an_unresolvable_age_rather_than_failing():
    result = AGE.estimate({})
    assert result["maxHours"] == 0.0
    assert result["resolution"]["resolvable"] is False
    assert result["resolution"]["bestSeparation"]["ratio"] == 0.0


# ---------------------------------------------------------------------------
# Resolvability: the separation test
# ---------------------------------------------------------------------------


def test_age_is_unresolvable_while_the_estimate_stays_inside_its_own_error_bar():
    """8 km of movement against an 11 km radius is not a measurement of anything."""
    result = AGE.estimate(run(timeline((0.0, 0.0, 10.0), (12.0, 4.0, 10.5), (24.0, 8.0, 11.0))))
    resolution = result["resolution"]

    assert resolution["resolvable"] is False
    assert resolution["fromHours"] is None
    assert resolution["note"] == AGE.UNRESOLVED_NOTE
    assert resolution["bestSeparation"]["hours"] == 24.0
    assert resolution["bestSeparation"]["ratio"] == pytest.approx(8.0 / 11.0, abs=2e-3)


def test_age_becomes_resolvable_at_the_first_lookback_that_clears_its_own_radius():
    """A tighter envelope with the same track is the case worth reporting: the hindcast
    has genuinely separated the ends of the window."""
    result = AGE.estimate(run(timeline((0.0, 0.0, 2.0), (6.0, 1.0, 2.0), (12.0, 5.0, 2.0), (24.0, 12.0, 3.0))))
    resolution = result["resolution"]

    assert resolution["resolvable"] is True
    assert resolution["fromHours"] == 12.0
    assert AGE.UNRESOLVED_NOTE not in resolution["note"]
    assert "12 h back" in resolution["note"]
    # The reported separation is the best in the run, not the first one to qualify.
    assert resolution["bestSeparation"]["hours"] == 24.0


def test_the_threshold_is_one_radius_and_is_exercised_from_both_sides():
    below = AGE.estimate(run(timeline((0.0, 0.0, 5.0), (24.0, 4.9, 5.0))))
    above = AGE.estimate(run(timeline((0.0, 0.0, 5.0), (24.0, 5.2, 5.0))))
    assert AGE.RESOLUTION_RADII == 1.0
    assert below["resolution"]["resolvable"] is False
    assert above["resolution"]["resolvable"] is True
    assert f"{AGE.RESOLUTION_RADII:g} P90 radius" in below["resolution"]["test"]


def test_steps_without_a_usable_spread_are_skipped_not_scored_as_infinite():
    """A zero radius would divide the displacement by nothing and declare every case
    resolvable, which is the exact failure this module exists to avoid."""
    steps = timeline((0.0, 0.0, 10.0), (12.0, 6.0, 0.0), (24.0, 8.0, 11.0))
    result = AGE.estimate(run(steps))
    assert result["resolution"]["resolvable"] is False
    assert result["resolution"]["bestSeparation"]["hours"] == 24.0


def test_a_step_without_a_centroid_is_skipped():
    steps = timeline((0.0, 0.0, 10.0), (12.0, 4.0, 10.5), (24.0, 8.0, 11.0))
    steps[1]["centroid"] = None
    result = AGE.estimate(run(steps))
    assert result["resolution"]["bestSeparation"]["hours"] == 24.0


def test_a_timeline_whose_first_step_has_no_centroid_reports_no_resolution():
    steps = timeline((0.0, 0.0, 10.0), (24.0, 40.0, 1.0))
    steps[0]["centroid"] = []
    result = AGE.estimate(run(steps))
    assert result["resolution"]["resolvable"] is False
    assert result["resolution"]["bestSeparation"]["displacementKm"] == 0.0


# ---------------------------------------------------------------------------
# Agreement with the drift run it describes
# ---------------------------------------------------------------------------


def test_the_separation_is_measured_from_the_observed_centroid(backward):
    """`displacementMeanKm` in the timeline is a spread, not a centroid displacement --
    it is non-zero at t=0 -- so the estimate must not reuse it."""
    steps = backward["timeline"]
    assert steps[0]["displacementMeanKm"] > 0.0  # the field this must not be read from

    best = AGE.estimate(backward)["resolution"]["bestSeparation"]
    origin = steps[0]["centroid"]
    match = next(s for s in steps if abs(abs(s["hoursFromObservation"]) - best["hours"]) < 1e-6)
    expected = haversine_km(origin[0], origin[1], match["centroid"][0], match["centroid"][1])

    assert best["displacementKm"] == pytest.approx(expected, abs=1e-3)
    assert best["radiusKm"] == pytest.approx(match["spreadP90Km"], abs=1e-3)
    assert best["ratio"] == pytest.approx(expected / match["spreadP90Km"], abs=1e-3)


def test_a_tightly_seeded_run_can_narrow_the_age(backward):
    """A 1.2 km patch spreads to about 2 km while drifting 5 km, so its position at 12 h
    back is outside the uncertainty around it and the two ends of the window differ."""
    resolution = AGE.estimate(backward)["resolution"]
    assert resolution["resolvable"] is True
    assert resolution["bestSeparation"]["ratio"] > AGE.RESOLUTION_RADII
    assert resolution["fromHours"] <= resolution["bestSeparation"]["hours"]


def test_a_broadly_seeded_run_cannot_and_says_so_instead_of_guessing():
    """The same forcing, the same horizon, a patch the size of a real slick: now the
    drift is small next to the spread and the age collapses to the bound.

    This pair is the finding, not either test alone. Resolvability is a property of the
    slick's size against how far it moved, so it is a per-case answer and the demo case --
    seeded from a 148 km² mask -- lands on this side of it.
    """
    forcing, _ = F.resolve_forcing(BOUNDS, ACQUIRED, CASE)
    lon, lat, seeding = E.seed_from_point(-89.40, 28.50, 12_000.0, 240, 7)
    broad = E.simulate(forcing, lon, lat, ACQUIRED, direction="backward", seeding=seeding)

    resolution = AGE.estimate(broad)["resolution"]
    assert resolution["resolvable"] is False
    assert resolution["bestSeparation"]["ratio"] < AGE.RESOLUTION_RADII
    assert "equally consistent with this one image" in resolution["note"]


# ---------------------------------------------------------------------------
# What the reader is told
# ---------------------------------------------------------------------------


def test_the_estimate_never_presents_the_bound_as_a_measurement(backward):
    result = AGE.estimate(backward)
    assert "not measured" in result["caveat"]
    assert "forcing" in result["caveat"]
    for text in (result["label"], result["basis"], result["caveat"]):
        lowered = text.lower()
        for word in ("exact", "precisely", "confirmed", "proven"):
            assert word not in lowered


def test_three_concrete_things_would_narrow_it_and_each_is_a_data_source(backward):
    """"Buy better data" is the actionable half of an unresolvable answer."""
    narrowed = AGE.estimate(backward)["narrowedBy"]
    assert len(narrowed) == 3
    assert narrowed == list(AGE.NARROWED_BY)
    joined = " ".join(narrowed).lower()
    for expected in ("second acquisition", "currents", "earlier acquisition"):
        assert expected in joined


def test_the_estimate_serialises_for_the_api(backward):
    payload = json.loads(json.dumps(AGE.estimate(backward)))
    assert set(payload) == {
        "label",
        "minHours",
        "maxHours",
        "acquiredUtc",
        "earliestReleaseUtc",
        "basis",
        "resolution",
        "narrowedBy",
        "caveat",
    }
    assert set(payload["resolution"]) == {"resolvable", "fromHours", "test", "bestSeparation", "note"}
    assert set(payload["resolution"]["bestSeparation"]) == {
        "hours",
        "displacementKm",
        "radiusKm",
        "ratio",
    }
