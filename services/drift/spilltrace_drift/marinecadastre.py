"""The MarineCadastre AIS schema: its columns, its coded values, and readers for it.

The problem statement names https://marinecadastre.gov/accessais/ as the authority for AIS
format, so this module is written against that format rather than against a convenient
internal one. Two consequences worth stating up front:

* Our synthetic AIS is emitted in **exactly** these 17 columns, in this order, so the
  Vessels screen can say `AIS source: synthetic (MarineCadastre schema)` truthfully.
* `read_csv` reads a real MarineCadastre file. Swapping synthetic data for a real download
  is a path argument, not a code change.

The field list, units and valid domains come from NOAA's AIS data dictionary
(coast.noaa.gov/data/marinecadastre/ais/data-dictionary.pdf) and the vessel-group codes from
VesselTypeCodes2018.pdf. Everything in `SENTINELS` below, however, was measured from a real
file -- `AIS_2022_06_01.csv`, 8.6 million rows -- because the documentation describes the
domain a field *should* hold and says nothing about what it holds when the transmitter had
nothing to send. In 400,000 sampled rows:

* `Heading` was the sentinel 511.0 in **53%** of rows, and `COG` was 360.0 in 14%. Both are
  outside the documented domains (0-359, 0-359.9). Read either as a bearing and you get a
  vessel pointing at nothing, which then poisons any trajectory score computed from it.
* `Status` was empty in exactly the Class B population -- 103,948 empty rows, 103,948 Class B
  rows, no overlap with Class A. Class B transceivers do not report navigation status, so
  "missing status" is a property of the receiver, not evidence about the vessel.
* `IMO` was absent in 49% of rows, and `IMO0000000` appeared in another 65,278 -- a
  placeholder that is worse than absent, because it looks like an identifier.
* `Length`, `Width` and `Draft` each appeared as both `0` and empty, meaning the same thing.

None of that is exotic; it is what terrestrial AIS looks like. But a scoring pipeline that
treats a sentinel as a measurement produces confident nonsense, and this project ranks
vessels for investigation, so the parsing is deliberately suspicious.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

# ---------------------------------------------------------------------------
# The schema
# ---------------------------------------------------------------------------

#: The CSV header, verbatim and in order. A real MarineCadastre daily file starts with
#: exactly this line; our exporter writes exactly this line.
HEADER: tuple[str, ...] = (
    "MMSI",
    "BaseDateTime",
    "LAT",
    "LON",
    "SOG",
    "COG",
    "Heading",
    "VesselName",
    "IMO",
    "CallSign",
    "VesselType",
    "Status",
    "Length",
    "Width",
    "Draft",
    "Cargo",
    "TransceiverClass",
)

HEADER_LINE = ",".join(HEADER)


@dataclass(frozen=True)
class FieldSpec:
    """One column, as the data dictionary defines it.

    `low`/`high` are the *documented* valid domain, not the observed one. Where the two
    disagree the observation wins at parse time and the disagreement is recorded here, so a
    reader can see that the file is legal-but-surprising rather than corrupt.
    """

    name: str
    kind: str  # "text" | "float" | "int"
    unit: str = ""
    low: float | None = None
    high: float | None = None
    note: str = ""


FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("MMSI", "text", note="9 digits; the vessel's radio identity, not its registry id"),
    FieldSpec("BaseDateTime", "text", note="ISO 8601, UTC, no offset suffix: 2022-06-01T00:00:02"),
    FieldSpec("LAT", "float", "degrees", -90.0, 90.0),
    FieldSpec("LON", "float", "degrees", -180.0, 180.0),
    FieldSpec("SOG", "float", "knots", 0.0, 99.9, "102.3 also seen upstream as unavailable"),
    FieldSpec("COG", "float", "degrees", 0.0, 359.9, "360.0 means unavailable (14% of rows)"),
    FieldSpec("Heading", "float", "degrees", 0.0, 359.0, "511.0 means unavailable (53% of rows)"),
    FieldSpec("VesselName", "text", note="up to 32 chars; static message, often blank"),
    FieldSpec("IMO", "text", note="'IMO' + 7 digits; IMO0000000 is a placeholder, not an id"),
    FieldSpec("CallSign", "text", note="up to 8 chars; NA / N/A / NO COMM / NONE seen as junk"),
    FieldSpec("VesselType", "int", low=1, high=1024, note="0 and blank both mean unavailable"),
    FieldSpec("Status", "int", low=1, high=15, note="blank for every Class B row; 15 = undefined"),
    FieldSpec("Length", "float", "metres", 1, 509, "0 and blank both mean unavailable"),
    FieldSpec("Width", "float", "metres", 1, 61, "0 and blank both mean unavailable"),
    FieldSpec("Draft", "float", "metres", 1, 24, "0 and blank both mean unavailable"),
    FieldSpec("Cargo", "int", low=1, high=1024, note="usually mirrors VesselType in practice"),
    FieldSpec("TransceiverClass", "text", note="A or B; never blank in the file measured"),
)

FIELDS_BY_NAME: dict[str, FieldSpec] = {spec.name: spec for spec in FIELDS}


# ---------------------------------------------------------------------------
# What "no value" looks like
# ---------------------------------------------------------------------------

#: A heading of 511 is the ITU-R M.1371 "not available" code, transmitted as a real number
#: rather than an empty field. 53% of sampled rows carried it.
HEADING_UNAVAILABLE = 511.0

#: Likewise course-over-ground 360, which is one degree past the legal domain. 14% of rows.
COG_UNAVAILABLE = 360.0

#: Speed-over-ground 102.3 is the corresponding SOG sentinel (1023 in tenths of a knot).
SOG_UNAVAILABLE = 102.3

#: Call signs that are present but say nothing. Compared upper-cased and stripped.
PLACEHOLDER_CALLSIGNS: frozenset[str] = frozenset(
    {"NA", "N/A", "NONE", "NO COMM", "NOCOMM", "UNKNOWN", "-", "--", "0", "NIL"}
)

#: Names that are present but say nothing, by the same rule.
PLACEHOLDER_NAMES: frozenset[str] = frozenset(
    {"NA", "N/A", "NONE", "UNKNOWN", "NO NAME", "NONAME", "VESSEL", "-", "--", "0"}
)

SENTINELS: dict[str, str] = {
    "Heading": f"{HEADING_UNAVAILABLE} -> None",
    "COG": f"{COG_UNAVAILABLE} -> None",
    "SOG": f"{SOG_UNAVAILABLE} -> None",
    "IMO": "blank, or IMO followed by all zeros / fewer than 7 significant digits -> None",
    "CallSign": "blank or one of PLACEHOLDER_CALLSIGNS -> None",
    "VesselName": "blank or one of PLACEHOLDER_NAMES -> None",
    "Status": "blank -> None (always the case for TransceiverClass B)",
    "Length": "blank or 0 -> None",
    "Width": "blank or 0 -> None",
    "Draft": "blank or 0 -> None",
    "VesselType": "blank or 0 -> None",
    "Cargo": "blank or 0 -> None",
}


# ---------------------------------------------------------------------------
# Coded values: vessel type -> group
# ---------------------------------------------------------------------------

# NOAA's VesselTypeCodes2018.pdf collapses the 100-odd AIS ship-and-cargo codes into nine
# groups. The groups are not contiguous -- 21, 22, 31, 32 and 52 are all Tug Tow while 23-29
# are Other -- so the table is transcribed as the source gives it rather than smoothed into
# ranges that would be easier to read and wrong.
_GROUP_CODES: dict[str, tuple[int | tuple[int, int], ...]] = {
    "Not Available": (0,),
    "Other": ((1, 19), 20, (23, 29), 33, 34, (38, 51), (53, 59), (90, 99)),
    "Tug Tow": (21, 22, 31, 32, 52),
    "Fishing": (30,),
    "Military": (35,),
    "Pleasure Craft/Sailing": (36, 37),
    "Passenger": ((60, 69),),
    "Cargo": ((70, 79),),
    "Tanker": ((80, 89),),
}


def _expand(codes: Iterable[int | tuple[int, int]]) -> Iterator[int]:
    for entry in codes:
        if isinstance(entry, tuple):
            yield from range(entry[0], entry[1] + 1)
        else:
            yield entry


VESSEL_GROUP_BY_CODE: dict[int, str] = {
    code: group for group, codes in _GROUP_CODES.items() for code in _expand(codes)
}

#: The groups this project treats as carrying oil in bulk, and so as plausible sources of a
#: mineral-oil slick. Kept separate from the scoring weights on purpose: this is a statement
#: about the AIS code table, not about how much a match is worth.
OIL_CAPABLE_GROUPS: frozenset[str] = frozenset({"Tanker"})


def vessel_group(code: int | None) -> str | None:
    """The 2018 group name for an AIS ship-and-cargo code.

    Returns None for a missing code and for anything above 99. The `VesselType` domain runs
    to 1024, but the 2018 table only defines 0-99; codes above that are second-digit-encoded
    hazard categories which the table does not group, so guessing would invent a fact.
    """
    if code is None:
        return None
    return VESSEL_GROUP_BY_CODE.get(int(code))


# ---------------------------------------------------------------------------
# Coded values: navigation status
# ---------------------------------------------------------------------------

# The NOAA dictionary gives `Status` a domain and no names, so the names come from the
# message definition itself -- ITU-R M.1371, message types 1/2/3, the 4-bit navigational
# status field. Codes 9-13 are reserved or regional and are reported verbatim rather than
# interpreted.
NAV_STATUS_TEXT: dict[int, str] = {
    0: "under way using engine",
    1: "at anchor",
    2: "not under command",
    3: "restricted manoeuvrability",
    4: "constrained by draught",
    5: "moored",
    6: "aground",
    7: "engaged in fishing",
    8: "under way sailing",
    9: "reserved (high-speed craft)",
    10: "reserved (wing in ground)",
    11: "towing astern",
    12: "pushing ahead or towing alongside",
    13: "reserved",
    14: "AIS-SART, MOB or EPIRB active",
    15: "undefined",
}

#: Statuses in which the vessel is not making way under power. A stop inside the release
#: window is one of the behavioural signals the scorer looks for, so it needs a definition
#: that survives the round trip through a real file.
STATIONARY_STATUSES: frozenset[int] = frozenset({1, 5, 6})


def nav_status_text(code: int | None) -> str | None:
    """The ITU status name for a code, or None when the field was blank.

    Blank is the norm rather than the exception: it is what every Class B transceiver in the
    measured file reported, because Class B does not carry the field at all. Callers must
    treat None as "the receiver cannot say", never as "the vessel was under way".
    """
    if code is None:
        return None
    return NAV_STATUS_TEXT.get(int(code), f"unrecognised status {int(code)}")


#: The exporter's side of the same table. Our internal records carry status as text, and two
#: of those strings are ones we generate ourselves; both are mapped so a synthetic file and a
#: real file are read back by identical code.
STATUS_CODE_BY_TEXT: dict[str, int] = {
    **{text: code for code, text in NAV_STATUS_TEXT.items()},
    "moored/stopped": 5,
    "stopped": 5,
    "under way": 0,
}


# ---------------------------------------------------------------------------
# Scalars
# ---------------------------------------------------------------------------


def _text(value: Any, placeholders: frozenset[str] = frozenset()) -> str | None:
    """A trimmed string, or None if it was blank or one of `placeholders`."""
    if value is None:
        return None
    trimmed = str(value).strip()
    if not trimmed or trimmed.upper() in placeholders:
        return None
    return trimmed


def _number(value: Any) -> float | None:
    """A float, or None if the field was blank or unparseable.

    Unparseable is not an error worth raising. An 882 MB daily file assembled from thousands
    of receivers contains a handful of malformed rows, and aborting the import over one of
    them would be a worse outcome than dropping the field.
    """
    if value is None:
        return None
    trimmed = str(value).strip()
    if not trimmed:
        return None
    try:
        return float(trimmed)
    except ValueError:
        return None


def _positive(value: Any) -> float | None:
    """A dimension in metres: None when blank, and also None when 0.

    The documented domains for `Length`, `Width` and `Draft` all start at 1, so a zero is out
    of domain. The measured file writes both a literal `0` and an empty field for the same
    absence -- 263,787 blank drafts and a further population of zeros -- and treating the
    zero as a measurement would put a 0 m tanker in the evidence table.
    """
    number = _number(value)
    if number is None or number <= 0.0:
        return None
    return number


def _code(value: Any) -> int | None:
    """An integer code, with 0 read as "unavailable" rather than as a code.

    `VesselType` and `Cargo` both document a domain starting at 1, and the 2018 group table
    lists 0 explicitly as *Not Available*.
    """
    number = _number(value)
    if number is None or int(number) == 0:
        return None
    return int(number)


def _bearing(value: Any, sentinel: float) -> float | None:
    """A bearing in degrees, or None if it was the field's unavailable sentinel.

    Anything outside 0-360 is also rejected. This is the single most consequential parse in
    the module: `headingDeg` and `cogDeg` feed the trajectory component of the vessel score,
    and 511 degrees read as a bearing points a vessel at nothing in particular while still
    looking like data.
    """
    number = _number(value)
    if number is None or number == sentinel or not 0.0 <= number < 360.0:
        return None
    return number


def _imo(value: Any) -> str | None:
    """An IMO number, or None for the placeholders that pretend to be one.

    In the sampled rows `IMO` was blank 48.6% of the time -- expected, since it rides on the
    static message and only Class A ships carry it -- but `IMO0000000` appeared in 65,278
    rows and 49 distinct `IMO0*` values appeared in 67,888. Those are worse than blank: a
    downstream join on them would merge unrelated vessels into one, and the data-completeness
    component of the score would reward a vessel for a field it never actually sent.

    The rule applied is structural rather than a blocklist: strip the `IMO` prefix, and
    require seven digits that are not all zeros and do not begin with one.
    """
    text = _text(value)
    if text is None:
        return None
    digits = text[3:] if text.upper().startswith("IMO") else text
    digits = digits.strip()
    if not digits.isdigit() or len(digits) != 7 or digits.startswith("0"):
        return None
    return f"IMO{digits}"


def _mmsi(value: Any) -> str | None:
    """A nine-digit MMSI as text, or None.

    Kept as text deliberately -- it is an identifier, not a quantity, and leading digits are
    the country prefix. 99.9% of sampled rows held nine digits; the remainder held one to ten
    and are dropped rather than padded, because a truncated MMSI identifies the wrong vessel.
    """
    text = _text(value)
    if text is None or not text.isdigit() or len(text) != 9:
        return None
    return text


def parse_time(value: Any) -> datetime | None:
    """`BaseDateTime` as an aware UTC datetime.

    MarineCadastre writes `2022-06-01T00:00:02` with no offset and documents it as UTC, while
    the rest of this project writes an explicit `Z`. Both are accepted; a naive value is
    assumed UTC on the file's authority.
    """
    text = _text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_time(moment: datetime | str | None) -> str | None:
    """A timestamp in the form this project stores: ISO 8601, UTC, trailing `Z`."""
    if moment is None:
        return None
    parsed = moment if isinstance(moment, datetime) else parse_time(moment)
    if parsed is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def format_base_datetime(moment: datetime | str | None) -> str:
    """A timestamp in the form MarineCadastre writes: no offset, no `Z`, second precision."""
    parsed = moment if isinstance(moment, datetime) else parse_time(moment)
    if parsed is None:
        return ""
    return parsed.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def parse_row(row: dict[str, str] | Sequence[str]) -> dict[str, Any] | None:
    """One CSV row as an internal report, or None if it cannot be positioned.

    A row is rejected only when it lacks the three things every later stage needs: an MMSI, a
    timestamp and a position. Every other field is allowed to be absent, and absence is
    recorded as None rather than as a default -- `sogKn: None` and `sogKn: 0.0` mean different
    things, and the second one is a claim that the vessel was stationary.

    The returned dict is a superset of the record shape `ais.py` already builds, so the two
    sources are interchangeable downstream.
    """
    if not isinstance(row, dict):
        row = dict(zip(HEADER, row))

    mmsi = _mmsi(row.get("MMSI"))
    moment = parse_time(row.get("BaseDateTime"))
    lat = _number(row.get("LAT"))
    lon = _number(row.get("LON"))
    if mmsi is None or moment is None or lat is None or lon is None:
        return None
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        return None

    status_code = _number(row.get("Status"))
    status_code = None if status_code is None else int(status_code)
    transceiver = _text(row.get("TransceiverClass"))
    type_code = _code(row.get("VesselType"))
    sog = _number(row.get("SOG"))
    if sog is not None and (sog == SOG_UNAVAILABLE or sog < 0.0):
        sog = None

    return {
        "mmsi": mmsi,
        "timeUtc": format_time(moment),
        "lon": round(lon, 6),
        "lat": round(lat, 6),
        "sogKn": None if sog is None else round(sog, 1),
        "cogDeg": _bearing(row.get("COG"), COG_UNAVAILABLE),
        "headingDeg": _bearing(row.get("Heading"), HEADING_UNAVAILABLE),
        "statusCode": status_code,
        "navStatus": nav_status_text(status_code),
        "name": _text(row.get("VesselName"), PLACEHOLDER_NAMES),
        "imo": _imo(row.get("IMO")),
        "callSign": _text(row.get("CallSign"), PLACEHOLDER_CALLSIGNS),
        "vesselTypeCode": type_code,
        "vesselGroup": vessel_group(type_code),
        "cargoCode": _code(row.get("Cargo")),
        "lengthM": _positive(row.get("Length")),
        "widthM": _positive(row.get("Width")),
        "draftM": _positive(row.get("Draft")),
        "transceiverClass": transceiver.upper() if transceiver else None,
        "synthetic": False,
    }


#: Static fields: carried once per vessel rather than per report. MarineCadastre repeats them
#: on every row, but not consistently -- the same MMSI may have a name on one row and a blank
#: on the next -- so the importer keeps the first non-empty value it sees for each.
STATIC_FIELDS: tuple[str, ...] = (
    "name",
    "imo",
    "callSign",
    "vesselTypeCode",
    "vesselGroup",
    "cargoCode",
    "lengthM",
    "widthM",
    "draftM",
    "transceiverClass",
)

#: Per-report fields, in the order the rest of the pipeline expects to see them.
REPORT_FIELDS: tuple[str, ...] = (
    "timeUtc",
    "lon",
    "lat",
    "sogKn",
    "cogDeg",
    "headingDeg",
    "statusCode",
    "navStatus",
    "synthetic",
)


def _within(bounds: Sequence[float] | None, lon: float, lat: float) -> bool:
    """Is this position inside `[lon_min, lat_min, lon_max, lat_max]`?

    A box spanning the antimeridian is not supported and would silently exclude everything;
    no scene in this dataset crosses it, and pretending otherwise would be untested code.
    """
    if bounds is None:
        return True
    lon_min, lat_min, lon_max, lat_max = (float(v) for v in bounds)
    return lon_min <= lon <= lon_max and lat_min <= lat <= lat_max


def _normalise_window(window: Sequence[Any] | None) -> tuple[datetime | None, datetime | None] | None:
    """`(start, end)` as datetimes, parsed once instead of once per row.

    Either end may be None, which leaves that side unbounded -- useful for "everything after
    the acquisition" without having to invent a far-future date. A window whose ends are both
    unparseable is treated as no window at all rather than as a window that matches nothing,
    because the second reading turns a typo into an empty result set with no error.
    """
    if window is None:
        return None
    start = parse_time(window[0])
    end = parse_time(window[1])
    if start is None and end is None:
        return None
    return start, end


def _in_window(window: tuple[datetime | None, datetime | None] | None, moment: datetime) -> bool:
    """Is this timestamp inside the half-open interval `[start, end)`?"""
    if window is None:
        return True
    start, end = window
    if start is not None and moment < start:
        return False
    if end is not None and moment >= end:
        return False
    return True


class SchemaError(ValueError):
    """The file does not have MarineCadastre's header."""


def check_header(fieldnames: Sequence[str] | None) -> None:
    """Fail loudly, and specifically, on a file that is not this schema.

    Being told *which* columns are wrong is the difference between a five-minute fix and an
    afternoon. A file with the right columns in the wrong order is rejected too: `LAT` and
    `LON` are adjacent and interchangeable-looking, and swapping them would put every vessel
    in the wrong hemisphere without raising anything.
    """
    if not fieldnames:
        raise SchemaError("the file is empty: expected a MarineCadastre AIS header row")
    found = tuple(name.strip() for name in fieldnames)
    if found == HEADER:
        return
    missing = [name for name in HEADER if name not in found]
    extra = [name for name in found if name not in HEADER]
    detail = []
    if missing:
        detail.append(f"missing {missing}")
    if extra:
        detail.append(f"unexpected {extra}")
    if not detail:
        detail.append("columns are in a different order")
    raise SchemaError(
        "not a MarineCadastre AIS file: " + "; ".join(detail) + f"\n  expected: {HEADER_LINE}"
        f"\n  found:    {','.join(found)}"
    )


def iter_reports(
    path: Path | str,
    *,
    bounds: Sequence[float] | None = None,
    window: Sequence[Any] | None = None,
    limit: int | None = None,
    counts: dict[str, int] | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream reports out of a MarineCadastre CSV, filtering as it goes.

    Streaming rather than loading: a single day of the national feed is 882 MB and 8.6 million
    rows, and the useful subset for one Sentinel-1 scene is a few thousand of them. Filtering
    inside the read keeps peak memory proportional to the answer instead of to the file.

    `limit` caps the rows *examined*, not the rows returned, so it is a cheap way to sample a
    large file without depending on how much of the head happens to match the filters.

    Pass `counts` to have the funnel tallied in place -- how many rows were read, how many
    were unusable, and how many each filter removed. The Vessels screen publishes these, so
    they are counted here rather than estimated later.
    """
    lon_lat_bounds = None if bounds is None else [float(v) for v in bounds]
    time_window = _normalise_window(window)
    tally = counts if counts is not None else {}
    for key in ("rowsExamined", "rowsUnusable", "outsideBounds", "outsideWindow", "reportsKept"):
        tally.setdefault(key, 0)

    with Path(path).open("r", newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        check_header(reader.fieldnames)
        for index, row in enumerate(reader):
            if limit is not None and index >= limit:
                break
            tally["rowsExamined"] += 1
            report = parse_row(row)
            if report is None:
                tally["rowsUnusable"] += 1
                continue
            if not _within(lon_lat_bounds, report["lon"], report["lat"]):
                tally["outsideBounds"] += 1
                continue
            moment = parse_time(report["timeUtc"])
            if moment is None or not _in_window(time_window, moment):
                tally["outsideWindow"] += 1
                continue
            tally["reportsKept"] += 1
            yield report


def source_label(path: Path | str | None) -> str:
    """The string the Vessels screen shows above the shortlist.

    One label, two possible values, no branch anywhere else in the product: with a path it
    names the real file, without one it says the data is synthetic. The honesty label is
    therefore a property of what was loaded rather than something a screen has to remember to
    say.
    """
    if path is None:
        return "synthetic (MarineCadastre schema)"
    return f"MarineCadastre {Path(path).name}"


def read_csv(
    path: Path | str,
    *,
    bounds: Sequence[float] | None = None,
    window: Sequence[Any] | None = None,
    limit: int | None = None,
    min_reports: int = 2,
) -> dict[str, Any]:
    """Read a MarineCadastre CSV into this project's vessel-and-track shape.

    Reports are grouped by MMSI and sorted by time. A vessel with fewer than `min_reports`
    positions is dropped: with one position there is no course to compare against a drift
    trajectory, so it could only ever score on proximity, and admitting it to the shortlist
    would mean ranking a vessel on a quarter of the available evidence alongside vessels
    ranked on all of it.

    The `counts` block is the traffic-filtering funnel, measured rather than asserted.
    """
    counts: dict[str, int] = {}
    vessels: dict[str, dict[str, Any]] = {}

    for report in iter_reports(path, bounds=bounds, window=window, limit=limit, counts=counts):
        mmsi = report["mmsi"]
        vessel = vessels.get(mmsi)
        if vessel is None:
            vessel = {"mmsi": mmsi, "synthetic": False, "reports": []}
            vessel.update({name: None for name in STATIC_FIELDS})
            vessels[mmsi] = vessel
        for name in STATIC_FIELDS:
            if vessel[name] is None and report[name] is not None:
                vessel[name] = report[name]
        vessel["reports"].append({name: report[name] for name in REPORT_FIELDS})

    counts["vesselsSeen"] = len(vessels)
    kept = [v for v in vessels.values() if len(v["reports"]) >= min_reports]
    counts["vesselsDroppedTooFewReports"] = len(vessels) - len(kept)
    counts["vesselsKept"] = len(kept)
    # `reportsKept` counts what survived the spatial and temporal filters, which includes
    # reports belonging to vessels the line above then dropped. This is the number of reports
    # actually carried forward, and the two differ by exactly the singletons.
    counts["reportsInKeptVessels"] = sum(len(v["reports"]) for v in kept)

    for vessel in kept:
        vessel["reports"].sort(key=lambda report: report["timeUtc"])
        vessel["reportCount"] = len(vessel["reports"])
        vessel["firstReportUtc"] = vessel["reports"][0]["timeUtc"]
        vessel["lastReportUtc"] = vessel["reports"][-1]["timeUtc"]
    kept.sort(key=lambda vessel: vessel["mmsi"])

    return {
        "source": {
            "format": "MarineCadastre AIS",
            "label": source_label(path),
            "file": Path(path).name,
            "synthetic": False,
            "schema": list(HEADER),
        },
        "counts": counts,
        "vessels": kept,
    }


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

# Our eight synthetic vessel classes, mapped onto real AIS ship-and-cargo codes.
#
# The mapping is lossy in one direction and the loss is the point: AIS has no code that
# distinguishes a bulk carrier from a container ship. Codes 70-79 are all *Cargo*, with the
# second digit encoding the hazard category of what is aboard rather than the design of the
# hull, so all three of our cargo classes emit 70 and read back as "Cargo". A round trip
# through the CSV therefore loses `typeKey` and keeps `vesselGroup`, which is exactly what a
# real feed would have given us in the first place.
#
# Two choices worth defending:
#   * `chemical_tanker` emits 81, "tanker carrying dangerous goods category A". A chemical
#     tanker's distinguishing mark in AIS is its cargo category, not a separate ship code.
#   * `offshore_supply` emits 90, which the 2018 table groups as *Other*. There is no
#     offshore-supply code in the table; inventing a closer-looking one would be a fiction.
AIS_TYPE_CODES: dict[str, int] = {
    "oil_tanker": 80,
    "chemical_tanker": 81,
    "bulk_carrier": 70,
    "container_ship": 70,
    "general_cargo": 70,
    "offshore_supply": 90,
    "fishing": 30,
    "passenger": 60,
}


def _fmt(value: Any, digits: int) -> str:
    """A number for the CSV, or an empty field when there is nothing to say.

    Absence is written as an empty field rather than as a zero. The measured file uses both
    conventions for the same fact, and this one is the only one that cannot be mistaken for a
    measurement of zero.
    """
    if value is None:
        return ""
    return f"{float(value):.{digits}f}" if digits else str(int(round(float(value))))


def to_row(vessel: dict[str, Any], report: dict[str, Any]) -> list[str]:
    """One report as a MarineCadastre CSV row, in `HEADER` order.

    The unavailable sentinels are written back out as sentinels, not as blanks: a real
    consumer of this file expects 511 for an absent heading and 360 for an absent course,
    because that is what the radio protocol transmits. Blanks are used only for the fields
    that really are blank in a real file -- `Status` on Class B, and the dimensions.
    """
    type_code = vessel.get("vesselTypeCode") or AIS_TYPE_CODES.get(str(vessel.get("typeKey")))
    status = report.get("statusCode")
    if status is None:
        status = STATUS_CODE_BY_TEXT.get(str(report.get("navStatus")))
    # Class B carries no navigation status at all, so emitting one would be a fabrication --
    # and would break the exact Class-B/blank-Status correlation a real file shows.
    if str(vessel.get("transceiverClass", "A")).upper() == "B":
        status = None

    cog = report.get("cogDeg")
    heading = report.get("headingDeg")
    return [
        str(vessel.get("mmsi", "")),
        format_base_datetime(report.get("timeUtc")),
        _fmt(report.get("lat"), 5),
        _fmt(report.get("lon"), 5),
        _fmt(report.get("sogKn"), 1),
        _fmt(COG_UNAVAILABLE if cog is None else cog, 1),
        _fmt(HEADING_UNAVAILABLE if heading is None else heading, 1),
        str(vessel.get("name") or "")[:32],
        str(vessel.get("imo") or ""),
        str(vessel.get("callSign") or "")[:8],
        "" if type_code is None else str(int(type_code)),
        "" if status is None else str(int(status)),
        _fmt(vessel.get("lengthM"), 0),
        _fmt(vessel.get("widthM"), 0),
        _fmt(vessel.get("draftM"), 1),
        "" if vessel.get("cargoCode") is None else str(int(vessel["cargoCode"])),
        str(vessel.get("transceiverClass") or "A").upper(),
    ]


def iter_rows(vessels: Iterable[dict[str, Any]]) -> Iterator[list[str]]:
    """Every report of every vessel as a row, ordered by time then MMSI.

    Time-major, like a real file: MarineCadastre daily files are the receiver's chronological
    log, not a per-vessel grouping. Writing it any other way would produce a file that parses
    but that no real consumer's assumptions match.
    """
    rows: list[tuple[str, str, list[str]]] = []
    for vessel in vessels:
        for report in vessel.get("reports", ()):
            row = to_row(vessel, report)
            rows.append((row[1], row[0], row))
    rows.sort(key=lambda entry: (entry[0], entry[1]))
    for _, _, row in rows:
        yield row


def write_csv(path: Path | str, vessels: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Write a MarineCadastre-format CSV and report what went into it."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(HEADER)
        for row in iter_rows(vessels):
            writer.writerow(row)
            written += 1
    return {"path": str(target), "rows": written, "header": HEADER_LINE, "format": "MarineCadastre AIS"}
