"""Tests for the offline region labeller.

``scene.region`` is a convenience string -- nothing in the geometry, the drift or the
scoring reads it -- but it is printed in the dashboard header, in the case list and in the
PDF report, so being wrong about it is a credibility problem rather than a cosmetic one.
It *was* wrong: the fallback used to be longitude bands with no latitude at all, which put
the Aegean Sea at 39 N, 25 E in the "North Indian Ocean" and the Straits of Florida in the
"North Pacific Ocean". 133 of the 1200 supplied scenes fell through to that fallback and
34 of them named the wrong ocean.

So the tests are:

1. **The real coordinates that were wrong.** Taken from ``data/processed/audit.json``, not
   invented, because the bug was invisible to a hand-picked point.
2. **No shipped scene reaches the fallback at all.** The named boxes now cover every scene
   in the dataset, and the assertion is over all 1200 centroids rather than a sample.
3. **The fallback is still latitude-aware where it does fire.** Points outside the dataset
   check that a coordinate has to be *in* a basin to be named after it.
4. **The table is well formed and ordered.** First match wins, so a specific box listed
   after the basin that contains it would be dead code.
5. **Missing and nonsensical input is refused**, rather than silently mapped to a sea.
"""

from __future__ import annotations

import json

import pytest

from spilltrace_common import config as C
from spilltrace_common import regions as R

#: Every coordinate here is a real scene centroid, with the label the old longitude-band
#: fallback produced. ``(scene, lat, lon, was, expected)``.
RELABELLED = (
    ("00696", 39.437421, 25.558609, "North Indian Ocean", "Aegean Sea"),
    ("00698", 38.922237, 25.259739, "North Indian Ocean", "Aegean Sea"),
    ("00753", -0.544291, 105.889231, "South Indian Ocean", "South China Sea"),
    ("00757", -1.059834, 106.043562, "South Indian Ocean", "South China Sea"),
    ("01262", 23.387058, -80.080522, "North Pacific Ocean", "Straits of Florida"),
    ("01266", 23.413828, -80.472726, "North Pacific Ocean", "Straits of Florida"),
    ("00461", 36.387861, -7.33531, "North Atlantic Ocean", "Gulf of Cadiz"),
    ("00530", 43.420621, 14.520259, "North Atlantic Ocean", "Adriatic Sea"),
    ("00635", 52.222971, -5.78397, "North Atlantic Ocean", "Irish Sea"),
    ("00113", -6.227691, 10.660507, "South Atlantic Ocean", "Angolan shelf"),
)

#: Scenes that were already labelled correctly and must not move.
UNCHANGED = (
    ("00000", 55.241978, 4.057651, "North Sea"),
    ("00053", 25.59354, 54.690095, "Persian Gulf"),
)


@pytest.fixture(scope="module")
def audit():
    """The shipped audit report, for the assertions that must hold over all 1200 scenes."""
    if not C.AUDIT_JSON.exists():
        pytest.skip(f"{C.AUDIT_JSON} not present; run scripts/run_audit.py")
    return json.loads(C.AUDIT_JSON.read_text(encoding="utf-8"))


class TestTheScenesThatWereMislabelled:
    """Real centroids, and the wrong answer each one used to get."""

    @pytest.mark.parametrize(("scene", "lat", "lon", "was", "expected"), RELABELLED)
    def test_gets_its_real_sea(self, scene, lat, lon, was, expected):
        assert R.region_label(lat, lon) == expected, scene

    @pytest.mark.parametrize(("scene", "lat", "lon", "was", "expected"), RELABELLED)
    def test_no_longer_falls_through_to_a_basin(self, scene, lat, lon, was, expected):
        # The specific failure mode: the label is not merely different, it is no longer a
        # hemisphere-plus-ocean string, which is the shape every one of these bugs had.
        assert R.region_label(lat, lon) != was, scene

    @pytest.mark.parametrize(("scene", "lat", "lon", "expected"), UNCHANGED)
    def test_the_scenes_that_were_right_stay_right(self, scene, lat, lon, expected):
        assert R.region_label(lat, lon) == expected

    @pytest.mark.parametrize(
        ("scene", "lat", "lon"),
        (
            ("00925", 24.319941, -81.31356),
            ("00926", 24.271342, -81.413992),
            ("00927", 24.268827, -81.513795),
            ("00928", 24.271971, -81.597518),
        ),
    )
    def test_water_south_of_the_florida_keys_is_not_the_gulf(self, scene, lat, lon):
        # These four read "Gulf of Mexico" before the strait had a box. The Gulf is north
        # of the Keys; these are south of them.
        assert R.region_label(lat, lon) == "Straits of Florida", scene

    def test_the_gulf_keeps_its_own_water_west_of_the_dry_tortugas(self):
        # 00451, the nearest scene on the other side of the line. The strait's box has to
        # stop short of it, which is the only reason its western edge is 82.4 W.
        assert R.region_label(24.71772, -83.64162) == "Gulf of Mexico"


class TestEveryShippedScene:
    """Assertions over all 1200 centroids, because a sample is what missed this."""

    def test_named_boxes_cover_the_whole_dataset(self, audit):
        fell_through = [
            (s["name"], s["centroid"], R.region_label(s["centroid"][1], s["centroid"][0]))
            for s in audit["scenes"]
            if s.get("centroid")
            and R.region_label(s["centroid"][1], s["centroid"][0]).endswith(
                ("Ocean", "Unclassified waters")
            )
        ]
        assert fell_through == []

    def test_no_scene_is_named_after_an_ocean_it_is_not_in(self, audit):
        # A weaker claim than the one above, and the one that actually failed: the Aegean
        # scenes were in the Indian Ocean's longitude band but 9 degrees north of its water.
        for scene in audit["scenes"]:
            centroid = scene.get("centroid")
            if not centroid:
                continue
            lon, lat = centroid
            label = R.region_label(lat, lon)
            if "Indian Ocean" in label:
                assert 20.0 <= lon <= 120.0 and -60.0 <= lat <= 30.0, scene["name"]
            if "Pacific Ocean" in label:
                assert lon >= 120.0 or lon <= -70.0, scene["name"]

    def test_the_dataset_is_not_one_sea(self, audit):
        # The docs used to answer "why Persian Gulf data?" as though the dataset were
        # Persian Gulf data. It is 22 seas wide and the Persian Gulf is 7% of it.
        labels = {
            R.region_label(s["centroid"][1], s["centroid"][0])
            for s in audit["scenes"]
            if s.get("centroid")
        }
        assert len(labels) >= 20
        assert {"Gulf of Mexico", "Eastern Mediterranean", "Persian Gulf"} <= labels


class TestTheFallbackBasins:
    """Coordinates outside the dataset, where the fallback is what answers."""

    @pytest.mark.parametrize(
        ("lat", "lon", "expected"),
        (
            (-25.0, 75.0, "South Indian Ocean"),
            (2.0, 70.0, "North Indian Ocean"),
            (-30.0, 160.0, "South Pacific Ocean"),
            (40.0, -140.0, "North Pacific Ocean"),
            (-20.0, -100.0, "South Pacific Ocean"),
            (40.0, -40.0, "North Atlantic Ocean"),
            (-30.0, -20.0, "South Atlantic Ocean"),
            (80.0, 10.0, "Arctic Ocean"),
            (-70.0, 40.0, "Southern Ocean"),
            # Just past the northern edge of the Adriatic box. The catch-all keeps a
            # near-miss inside the Mediterranean instead of promoting it to an ocean.
            (46.5, 13.5, "Mediterranean Sea"),
        ),
    )
    def test_names_the_basin_it_is_in(self, lat, lon, expected):
        assert R.region_label(lat, lon) == expected

    def test_latitude_is_part_of_the_test(self):
        # 25 E is inside the Indian Ocean's longitude band at every latitude, which is why
        # the old table answered "North Indian Ocean" for both of these.
        assert R.region_label(-25.0, 25.0) == "South Indian Ocean"
        assert R.region_label(39.0, 25.0) != "North Indian Ocean"

    def test_the_americas_are_atlantic_on_their_atlantic_side(self):
        # The Florida coast at 80 W used to come back Pacific.
        assert R.region_label(30.0, -79.0) == "North Atlantic Ocean"
        assert R.region_label(0.0, -30.0) == "North Atlantic Ocean"
        assert R.region_label(45.0, -145.0) == "North Pacific Ocean"

    def test_nothing_matched_is_admitted_rather_than_guessed(self):
        # A gap in the tables produces a vague label, not a plausible wrong one. This
        # coordinate is in Kazakhstan: no box claims it and none should.
        assert R.region_label(48.0, 68.0) == "Unclassified waters"


class TestTheTables:
    """Structure, so a malformed box fails here rather than by never matching."""

    @pytest.mark.parametrize("table", (R.REGIONS, R._BASINS))
    def test_every_box_is_well_formed(self, table):
        for name, west, south, east, north in table:
            assert name and isinstance(name, str)
            assert -180.0 <= west < east <= 180.0, name
            assert -90.0 <= south < north <= 90.0, name

    def test_no_named_region_is_shadowed_by_an_earlier_box(self):
        # First match wins, so a box whose centre is already claimed by something above it
        # can never be reached and the name is dead code.
        for index, (name, west, south, east, north) in enumerate(R.REGIONS):
            centre_lat = (south + north) / 2.0
            centre_lon = (west + east) / 2.0
            for earlier, ewest, esouth, eeast, enorth in R.REGIONS[:index]:
                if earlier == name:
                    continue  # a second box for the same sea is deliberate
                shadowed = ewest <= centre_lon <= eeast and esouth <= centre_lat <= enorth
                assert not shadowed, f"{name} is shadowed by {earlier}"

    def test_basins_are_reached_only_after_the_named_regions(self):
        # The demo scene is in a named box, so no basin should ever answer for it.
        assert R.region_label(25.59354, 54.690095) == "Persian Gulf"


class TestRefusals:
    @pytest.mark.parametrize(
        ("lat", "lon"),
        ((None, 4.0), (55.0, None), (None, None), ("x", 4.0), (55.0, "x")),
    )
    def test_missing_or_unparseable_input(self, lat, lon):
        assert R.region_label(lat, lon) == "Unknown region"

    @pytest.mark.parametrize(("lat", "lon"), ((91.0, 4.0), (-91.0, 4.0), (55.0, 181.0)))
    def test_out_of_range_input(self, lat, lon):
        assert R.region_label(lat, lon) == "Unknown region"

    def test_a_string_that_parses_is_accepted(self):
        # The audit hands these in from JSON, where a number may arrive as a string.
        assert R.region_label("55.241978", "4.057651") == "North Sea"


class TestDescribeLocation:
    def test_hemispheres_are_named(self):
        assert R.describe_location(55.241978, 4.057651) == "55.242 N, 4.058 E"
        assert R.describe_location(-6.227691, -10.660507) == "6.228 S, 10.661 W"

    def test_missing_input(self):
        assert R.describe_location(None, 4.0) == "unknown"
        assert R.describe_location(55.0, None) == "unknown"
