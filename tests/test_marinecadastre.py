"""The MarineCadastre schema module: parsing, coded values, and the round trip.

Two things are being defended here.

The first is that our synthetic feed really is in MarineCadastre's format, not merely
described as being. The claim is only worth making if it is falsifiable, so
`test_the_header_is_byte_identical_to_a_real_file` compares against a literal transcribed
from a real download rather than against `HEADER` -- comparing the module to itself would
pass no matter what the module said.

The second is that a sentinel is never read as a measurement. `Heading` is 511 in 53% of real
rows and `COG` is 360 in 14%; both are outside their documented domains, and both would look
like plausible bearings to code that did not know. Every vessel score in this project has a
trajectory component computed from those two fields, so the parse is where a wrong answer
would enter and never be noticed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from spilltrace_common import config as C
from spilltrace_drift import ais as A
from spilltrace_drift import marinecadastre as MC

#: A real MarineCadastre daily extract, if one has been downloaded. Not in the repository --
#: one day of the national feed is 882 MB -- so the tests that use it skip rather than fail.
REAL_FILE = C.REPO_ROOT / "data" / "raw" / "AIS_2022_06_01.csv"
needs_real_file = pytest.mark.skipif(
    not REAL_FILE.exists(),
    reason=f"{REAL_FILE.name} not present; download a day from marinecadastre.gov/accessais",
)

# Transcribed from the first line of a real download. Deliberately a literal: a test that
# built this from MC.HEADER would agree with the module however wrong the module was.
REAL_HEADER_LINE = (
    "MMSI,BaseDateTime,LAT,LON,SOG,COG,Heading,VesselName,IMO,CallSign,"
    "VesselType,Status,Length,Width,Draft,Cargo,TransceiverClass"
)

# Two real rows, copied verbatim. The first has a 511 heading, a 360 course, no IMO and zeros
# in all three dimension fields; the second adds a blank Width and Draft and Status 15.
REAL_ROWS = (
    "367777550,2022-06-01T00:00:02,28.10198,-96.93931,0.0,360.0,511.0,HARRY LEE,,"
    "WDJ4420,52,0,0,0,0.0,52,A",
    "367544180,2022-06-01T00:00:07,46.16795,-123.91430,0.0,342.5,511.0,JEANNE ARAIN,,"
    "WDG5186,30,15,18,,,30,A",
)


def _row(**overrides: str) -> dict[str, str]:
    """A minimal valid row, with named fields replaced."""
    base = dict.fromkeys(MC.HEADER, "")
    base.update(
        {
            "MMSI": "367777550",
            "BaseDateTime": "2022-06-01T00:00:02",
            "LAT": "28.10198",
            "LON": "-96.93931",
            "TransceiverClass": "A",
        }
    )
    base.update(overrides)
    return base


class TestTheSchemaItself:
    def test_the_header_is_byte_identical_to_a_real_file(self):
        assert MC.HEADER_LINE == REAL_HEADER_LINE
        assert len(MC.HEADER) == 17

    def test_every_column_has_a_documented_spec(self):
        assert tuple(spec.name for spec in MC.FIELDS) == MC.HEADER

    def test_the_field_order_is_load_bearing_and_enforced(self):
        # LAT and LON are adjacent, same type, same plausible magnitude at low latitudes.
        # Swapping them would put every vessel somewhere else without raising anything, so
        # the reader refuses a file whose columns are right but out of order.
        swapped = list(MC.HEADER)
        swapped[2], swapped[3] = swapped[3], swapped[2]
        with pytest.raises(MC.SchemaError, match="different order"):
            MC.check_header(swapped)

    def test_a_missing_column_is_named_in_the_error(self):
        with pytest.raises(MC.SchemaError, match="Draft"):
            MC.check_header([name for name in MC.HEADER if name != "Draft"])

    def test_an_empty_file_is_rejected_rather_than_read_as_zero_rows(self):
        with pytest.raises(MC.SchemaError, match="empty"):
            MC.check_header(None)


class TestSentinelsAreNotMeasurements:
    def test_an_unavailable_heading_is_not_a_bearing(self):
        assert MC.parse_row(_row(Heading="511.0"))["headingDeg"] is None
        assert MC.parse_row(_row(Heading="107.0"))["headingDeg"] == 107.0

    def test_an_unavailable_course_is_not_a_bearing(self):
        assert MC.parse_row(_row(COG="360.0"))["cogDeg"] is None
        assert MC.parse_row(_row(COG="342.5"))["cogDeg"] == 342.5

    def test_an_unavailable_speed_is_not_a_speed(self):
        assert MC.parse_row(_row(SOG="102.3"))["sogKn"] is None
        assert MC.parse_row(_row(SOG="11.8"))["sogKn"] == 11.8

    def test_a_zero_dimension_means_the_same_as_a_blank_one(self):
        # The measured file writes both for the same absence, and the documented domains all
        # start at 1. A 0 m draft read as a measurement is a vessel drawing no water.
        for field in ("Length", "Width", "Draft"):
            key = {"Length": "lengthM", "Width": "widthM", "Draft": "draftM"}[field]
            assert MC.parse_row(_row(**{field: "0"}))[key] is None
            assert MC.parse_row(_row(**{field: "0.0"}))[key] is None
            assert MC.parse_row(_row(**{field: ""}))[key] is None
            assert MC.parse_row(_row(**{field: "18"}))[key] == 18.0

    def test_a_zero_type_code_is_not_a_type(self):
        assert MC.parse_row(_row(VesselType="0"))["vesselTypeCode"] is None
        assert MC.parse_row(_row(VesselType="80"))["vesselTypeCode"] == 80

    def test_a_blank_status_stays_blank(self):
        # It is what every Class B row carries. Defaulting it to 0 would assert that a vessel
        # was under way using engine on the strength of its receiver's hardware class.
        parsed = MC.parse_row(_row(Status="", TransceiverClass="B"))
        assert parsed["statusCode"] is None
        assert parsed["navStatus"] is None


class TestIdentifiersThatLookLikeIdentifiers:
    def test_the_all_zero_imo_placeholder_is_rejected(self):
        # 65,278 rows of one sample carried IMO0000000. Joining on it would merge unrelated
        # vessels into one record.
        assert MC.parse_row(_row(IMO="IMO0000000"))["imo"] is None
        assert MC.parse_row(_row(IMO="IMO0000001"))["imo"] is None

    def test_a_real_imo_survives_and_is_normalised(self):
        assert MC.parse_row(_row(IMO="IMO9627980"))["imo"] == "IMO9627980"
        assert MC.parse_row(_row(IMO="9627980"))["imo"] == "IMO9627980"

    def test_a_malformed_imo_is_dropped_rather_than_padded(self):
        for junk in ("IMO", "IMO123", "IMO96279801", "IMOABCDEFG", "N/A"):
            assert MC.parse_row(_row(IMO=junk))["imo"] is None

    @pytest.mark.parametrize("junk", ["NA", "N/A", "NO COMM", "NONE", "UNKNOWN", "na", ""])
    def test_a_call_sign_that_says_nothing_is_recorded_as_nothing(self, junk):
        assert MC.parse_row(_row(CallSign=junk))["callSign"] is None

    def test_a_real_call_sign_survives(self):
        assert MC.parse_row(_row(CallSign="WDJ4420"))["callSign"] == "WDJ4420"

    def test_a_truncated_mmsi_is_dropped_rather_than_guessed(self):
        # A padded MMSI identifies the wrong vessel, which in a ranking of investigation
        # candidates is worse than identifying none.
        for junk in ("", "3677", "36777755012", "36777755X"):
            assert MC.parse_row(_row(MMSI=junk)) is None
        assert MC.parse_row(_row(MMSI="367777550"))["mmsi"] == "367777550"


class TestCodedValues:
    @pytest.mark.parametrize(
        ("code", "group"),
        [
            (30, "Fishing"),
            (35, "Military"),
            (36, "Pleasure Craft/Sailing"),
            (37, "Pleasure Craft/Sailing"),
            (21, "Tug Tow"),
            (22, "Tug Tow"),
            (31, "Tug Tow"),
            (32, "Tug Tow"),
            (52, "Tug Tow"),
            (23, "Other"),
            (29, "Other"),
            (33, "Other"),
            (34, "Other"),
            (90, "Other"),
            (99, "Other"),
            (60, "Passenger"),
            (69, "Passenger"),
            (70, "Cargo"),
            (79, "Cargo"),
            (80, "Tanker"),
            (89, "Tanker"),
        ],
    )
    def test_the_2018_group_table_is_transcribed_correctly(self, code, group):
        # The groups are not contiguous: 21 and 22 are Tug Tow, 23-29 are Other, 31 and 32 are
        # Tug Tow again. Any implementation that smoothed them into ranges fails here.
        assert MC.vessel_group(code) == group

    def test_a_code_the_table_does_not_define_returns_nothing(self):
        # VesselType runs to 1024 but the 2018 table stops at 99. Guessing would invent a fact.
        assert MC.vessel_group(1004) is None
        assert MC.vessel_group(None) is None

    def test_status_codes_carry_their_itu_names(self):
        assert MC.nav_status_text(0) == "under way using engine"
        assert MC.nav_status_text(1) == "at anchor"
        assert MC.nav_status_text(5) == "moored"
        assert MC.nav_status_text(None) is None

    def test_an_out_of_domain_status_is_reported_not_hidden(self):
        # Status 15 appeared in a real file although the dictionary documents 1-14.
        assert MC.nav_status_text(15) == "undefined"
        assert "99" in MC.nav_status_text(99)


class TestReadingAFile:
    def _write(self, path: Path, rows) -> Path:
        path.write_text("\n".join([MC.HEADER_LINE, *rows]) + "\n", encoding="utf-8")
        return path

    def test_real_rows_parse_into_the_expected_record(self, tmp_path):
        path = self._write(tmp_path / "ais.csv", REAL_ROWS)
        result = MC.read_csv(path, min_reports=1)
        by_mmsi = {v["mmsi"]: v for v in result["vessels"]}
        assert set(by_mmsi) == {"367777550", "367544180"}

        harry = by_mmsi["367777550"]
        assert harry["name"] == "HARRY LEE"
        assert harry["callSign"] == "WDJ4420"
        assert harry["imo"] is None
        assert harry["vesselTypeCode"] == 52
        assert harry["vesselGroup"] == "Tug Tow"
        assert harry["lengthM"] is None and harry["widthM"] is None and harry["draftM"] is None
        report = harry["reports"][0]
        assert report["cogDeg"] is None and report["headingDeg"] is None
        assert report["timeUtc"] == "2022-06-01T00:00:02Z"
        assert report["synthetic"] is False

        jeanne = by_mmsi["367544180"]
        assert jeanne["lengthM"] == 18.0
        assert jeanne["vesselGroup"] == "Fishing"
        assert jeanne["reports"][0]["navStatus"] == "undefined"

    def test_a_row_without_a_position_is_counted_not_silently_dropped(self, tmp_path):
        rows = [REAL_ROWS[0], "367777550,2022-06-01T00:10:02,,,0.0,360.0,511.0,HARRY LEE,,,,,,,,,A"]
        path = self._write(tmp_path / "ais.csv", rows)
        result = MC.read_csv(path, min_reports=1)
        assert result["counts"]["rowsExamined"] == 2
        assert result["counts"]["rowsUnusable"] == 1
        assert result["counts"]["reportsKept"] == 1

    def test_bounds_and_window_filter_and_are_counted_separately(self, tmp_path):
        path = self._write(tmp_path / "ais.csv", REAL_ROWS)
        # The Texas row only: the Oregon row is outside the box.
        result = MC.read_csv(path, bounds=[-97.5, 27.5, -96.0, 29.0], min_reports=1)
        assert [v["mmsi"] for v in result["vessels"]] == ["367777550"]
        assert result["counts"]["outsideBounds"] == 1
        assert result["counts"]["outsideWindow"] == 0

        # Neither row: the window closes before both of them.
        empty = MC.read_csv(
            path, window=["2022-06-01T01:00:00Z", "2022-06-01T02:00:00Z"], min_reports=1
        )
        assert empty["vessels"] == []
        assert empty["counts"]["outsideWindow"] == 2

    def test_a_half_open_window_excludes_its_own_end(self, tmp_path):
        path = self._write(tmp_path / "ais.csv", REAL_ROWS)
        result = MC.read_csv(
            path, window=["2022-06-01T00:00:00Z", "2022-06-01T00:00:07Z"], min_reports=1
        )
        # 00:00:02 is in, 00:00:07 is the end of the interval and so is out. Half-open, so a
        # sequence of adjacent windows partitions the day instead of double-counting its seams.
        assert [v["mmsi"] for v in result["vessels"]] == ["367777550"]

    def test_a_vessel_with_one_report_is_dropped_by_default(self, tmp_path):
        path = self._write(tmp_path / "ais.csv", REAL_ROWS)
        result = MC.read_csv(path)
        assert result["vessels"] == []
        assert result["counts"]["vesselsSeen"] == 2
        assert result["counts"]["vesselsDroppedTooFewReports"] == 2
        assert result["counts"]["reportsInKeptVessels"] == 0

    def test_the_source_label_names_the_file_it_read(self, tmp_path):
        path = self._write(tmp_path / "AIS_2022_06_01.csv", REAL_ROWS)
        assert MC.read_csv(path, min_reports=1)["source"]["label"] == (
            "MarineCadastre AIS_2022_06_01.csv"
        )
        assert MC.source_label(None) == "synthetic (MarineCadastre schema)"

    def test_the_naive_timestamps_in_the_file_are_read_as_utc(self, tmp_path):
        # The dictionary documents BaseDateTime as UTC and writes no offset. Reading it as
        # local time would shift every vessel by the reader's timezone, which on this project
        # would silently move vessels in and out of the release window.
        assert MC.parse_time("2022-06-01T00:00:02").isoformat() == "2022-06-01T00:00:02+00:00"
        assert MC.parse_time("2022-06-01T00:00:02Z") == MC.parse_time("2022-06-01T00:00:02")
        assert MC.format_base_datetime("2017-03-11T02:15:12.182Z") == "2017-03-11T02:15:12"


@pytest.fixture(scope="module")
def synthetic_feed():
    """A real synthetic feed, built from the stored demo case's backward drift."""
    case = C.read_json(C.CASES_DIR / "demo.json")
    if not case or not (case.get("drift") or {}).get("backward"):
        pytest.skip("no stored demo case; run scripts/run_api.py --build-demo")
    return A.generate_ais(case["drift"]["backward"], "demo")


class TestTheRoundTrip:
    """Export then import, and compare. This is what makes the schema claim checkable."""

    def test_the_exported_header_matches_a_real_download(self, synthetic_feed, tmp_path):
        target = tmp_path / "synthetic.csv"
        A.export_marinecadastre_csv(synthetic_feed, target)
        assert target.read_text(encoding="utf-8").splitlines()[0] == REAL_HEADER_LINE

    def test_every_row_has_seventeen_fields(self, synthetic_feed, tmp_path):
        target = tmp_path / "synthetic.csv"
        A.export_marinecadastre_csv(synthetic_feed, target)
        for line in target.read_text(encoding="utf-8").splitlines()[1:]:
            assert len(line.split(",")) == 17

    def test_the_file_is_written_in_time_order_like_a_real_extract(self, synthetic_feed, tmp_path):
        target = tmp_path / "synthetic.csv"
        A.export_marinecadastre_csv(synthetic_feed, target)
        stamps = [line.split(",")[1] for line in target.read_text().splitlines()[1:]]
        assert stamps == sorted(stamps)

    def test_our_own_reader_reads_our_own_writer_without_special_cases(
        self, synthetic_feed, tmp_path
    ):
        target = tmp_path / "synthetic.csv"
        A.export_marinecadastre_csv(synthetic_feed, target)
        back = MC.read_csv(target, min_reports=1)

        original = {v["mmsi"]: v for v in synthetic_feed["vessels"]}
        imported = {v["mmsi"]: v for v in back["vessels"]}
        assert set(original) == set(imported)

        for mmsi, before in original.items():
            after = imported[mmsi]
            for key in (
                "name",
                "imo",
                "callSign",
                "vesselTypeCode",
                "vesselGroup",
                "cargoCode",
                "widthM",
                "draftM",
                "transceiverClass",
            ):
                assert before[key] == after[key], f"{mmsi}.{key}"
            assert float(before["lengthM"]) == after["lengthM"]
            assert len(before["reports"]) == len(after["reports"])
            for r1, r2 in zip(before["reports"], after["reports"]):
                for key in ("timeUtc", "cogDeg", "headingDeg", "statusCode", "navStatus"):
                    assert r1[key] == r2[key], f"{mmsi}.{key}"
                assert abs(r1["lon"] - r2["lon"]) <= 1e-5
                assert abs(r1["lat"] - r2["lat"]) <= 1e-5

    def test_an_absent_bearing_is_written_back_as_the_sentinel_not_a_blank(
        self, synthetic_feed, tmp_path
    ):
        # A consumer of this file expects 511 for an absent heading, because that is what the
        # radio protocol transmits. Writing a blank would be a different file format.
        target = tmp_path / "synthetic.csv"
        A.export_marinecadastre_csv(synthetic_feed, target)
        rows = [line.split(",") for line in target.read_text().splitlines()[1:]]
        headings = {row[6] for row in rows}
        assert "" not in headings
        assert any(h == "511.0" for h in headings), "no gapped heading in the feed to check"

    def test_the_synthetic_identifiers_cannot_collide_with_a_real_vessel(self, synthetic_feed):
        for vessel in synthetic_feed["vessels"]:
            assert vessel["mmsi"].startswith("999")  # MID 999 is unassigned by the ITU
            assert vessel["callSign"].startswith("QDEMO")  # Q is not an allocated series
            assert vessel["imo"] is None  # every valid IMO belongs to some real ship

    def test_class_b_vessels_carry_no_status_through_the_round_trip(
        self, synthetic_feed, tmp_path
    ):
        target = tmp_path / "synthetic.csv"
        A.export_marinecadastre_csv(synthetic_feed, target)
        rows = [line.split(",") for line in target.read_text().splitlines()[1:]]
        classes = {row[0]: row[16] for row in rows}
        blank_status = {row[0] for row in rows if row[11] == ""}
        class_b = {mmsi for mmsi, klass in classes.items() if klass == "B"}
        assert class_b, "the feed has no Class B vessel to check"
        # The same exact correlation a real file shows: blank Status iff Class B.
        assert blank_status == class_b


@needs_real_file
class TestAgainstTheRealFile:
    """Everything above uses rows transcribed by hand. This uses the download itself.

    These tests are the reason the sentinel handling exists at all -- the conventions they
    check are not in the documentation, they were measured here.
    """

    def test_the_real_file_has_exactly_our_header(self):
        with REAL_FILE.open("r", encoding="utf-8") as handle:
            assert handle.readline().strip() == MC.HEADER_LINE

    def test_a_slice_of_the_real_file_parses_into_usable_vessels(self):
        result = MC.read_csv(REAL_FILE, limit=40_000)
        assert result["counts"]["rowsExamined"] == 40_000
        # Whatever else is true of a real feed, almost all of it must be parseable; a large
        # unusable fraction would mean the reader, not the file, is wrong.
        assert result["counts"]["rowsUnusable"] < 40_000 * 0.02
        assert result["counts"]["vesselsKept"] > 100
        assert all(len(v["reports"]) >= 2 for v in result["vessels"])
        assert all(v["synthetic"] is False for v in result["vessels"])

    def test_the_documented_sentinels_really_do_dominate_the_real_file(self):
        result = MC.read_csv(REAL_FILE, limit=40_000)
        reports = [r for v in result["vessels"] for r in v["reports"]]
        assert reports
        gapped_heading = sum(1 for r in reports if r["headingDeg"] is None) / len(reports)
        # 53% in a 400,000-row sample. A reader that treated 511 as a bearing would be
        # inventing a course for roughly half of all real reports.
        assert gapped_heading > 0.2

    def test_no_real_mmsi_occupies_our_synthetic_range(self):
        # If any did, a synthetic vessel could be confused with a real one in a merged view.
        result = MC.read_csv(REAL_FILE, limit=200_000, min_reports=1)
        assert not [v for v in result["vessels"] if v["mmsi"].startswith("999")]

    def test_the_real_file_round_trips_through_our_writer(self, tmp_path):
        first = MC.read_csv(REAL_FILE, limit=60_000, min_reports=1)
        target = tmp_path / "rewritten.csv"
        MC.write_csv(target, first["vessels"])
        second = MC.read_csv(target, min_reports=1)
        assert {v["mmsi"] for v in first["vessels"]} == {v["mmsi"] for v in second["vessels"]}
        before = {v["mmsi"]: v for v in first["vessels"]}
        for after in second["vessels"]:
            source = before[after["mmsi"]]
            for key in ("name", "imo", "callSign", "vesselTypeCode", "vesselGroup", "draftM"):
                assert source[key] == after[key], f"{after['mmsi']}.{key}"
