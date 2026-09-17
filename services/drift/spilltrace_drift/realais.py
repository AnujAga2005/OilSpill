"""Turning a real MarineCadastre extract into the feed the scorer consumes.

:mod:`spilltrace_drift.ais` fabricates a feed. :mod:`spilltrace_drift.marinecadastre`
reads a real one. The two shapes are not the same, and the gap between them is not
cosmetic -- which is the reason this module exists rather than the endpoint simply
passing ``read_csv`` output to ``rank_vessels``.

Three fields the scorer reads are absent from a parsed CSV, and each one fails in a
different direction if it is left missing:

* **``cleaning``** feeds the data-completeness component. ``score_completeness`` defaults
  a missing block to ``reportCompleteness = fieldCompleteness = 1.0``, so a gappy real
  feed would score *full marks* for data quality -- the one component whose entire job is
  to say the data is poor. It is computed here instead.
* **``typeKey``** feeds the vessel-type component. Real AIS carries a numeric
  ship-and-cargo code, not this project's category keys, so the code is mapped to a
  relevance through the documented ``typeRelevance``/``typeRationale`` fallback that
  ``score_type`` already provides.
* **``track``** is the map polyline. Absent, the vessel is scored but cannot be drawn.

The completeness figure is measured, not assumed. The synthetic cleaner knows its own
reporting interval because it generated it; a real feed's interval varies with vessel
class, speed and receiver coverage, so each vessel's *own median* observed interval is
used as the expectation. That makes "90% complete" mean "one report in ten is missing
relative to how often this vessel was actually reporting", which is a statement about
the file rather than about a constant borrowed from the generator.

Nothing here invents a value. Where the file is silent -- no speed, no course, no type
code -- the field stays ``None`` and the completeness score falls, which is the intended
consequence rather than something to paper over.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Sequence

from spilltrace_common import config as C

from . import marinecadastre as mc
from .ais import EnvelopeTrack, haversine_km, iso_utc, parse_utc, track_line

#: Relevance of each 2018 AIS group to a bulk-oil enquiry, with the reason stated in the
#: same voice ``VESSEL_TYPES`` uses. These are the nine groups ``_GROUP_CODES`` defines.
#:
#: The values mirror the synthetic categories where the two describe the same hull --
#: ``Tanker`` matches ``oil_tanker`` at 1.0, ``Cargo`` sits at 0.5 between the container
#: and general-cargo entries -- so a case built on a real file and one built on the
#: synthetic feed weigh the same kind of ship the same way. Where AIS is less specific
#: than our own categories, the lower specificity is visible in the rationale rather than
#: hidden behind a confident number.
GROUP_RELEVANCE: dict[str, tuple[float, str]] = {
    "Tanker": (
        1.0,
        "AIS ship-and-cargo group 80-89: carries liquid cargo in bulk, so an operational "
        "discharge is physically possible",
    ),
    "Cargo": (
        0.5,
        "AIS ship-and-cargo group 70-79: no liquid cargo in bulk, but bunker fuel and "
        "engine-room slops remain possible sources",
    ),
    "Tug Tow": (
        0.4,
        "tug or towing vessel: carries its own fuel and may be handling a barge the AIS "
        "code does not describe",
    ),
    "Fishing": (
        0.25,
        "fishing vessel: bunker quantities only, and a discharge on this scale would be "
        "unusual",
    ),
    "Passenger": (
        0.2,
        "passenger vessel: bunker and bilge only, under comparatively close inspection",
    ),
    "Military": (
        0.2,
        "military vessel: bunker quantities only; AIS reporting is often incomplete by policy",
    ),
    "Pleasure Craft/Sailing": (
        0.1,
        "pleasure craft: too small to account for a slick of this size",
    ),
    "Other": (
        0.3,
        "AIS group 'Other': the code table does not identify the hull, so relevance "
        "cannot be narrowed further",
    ),
    "Not Available": (
        0.3,
        "the vessel reported no ship-and-cargo code, so its category is unknown",
    ),
}

#: Used when the code is above 99, where the 2018 table defines no group at all.
UNKNOWN_RELEVANCE = (
    0.3,
    "the reported ship-and-cargo code is outside the 2018 group table, so the hull type "
    "cannot be determined from it",
)

#: A report older or newer than the drift window by more than this is not read from the
#: file at all. The envelope cannot say anything about a vessel outside its own timeline,
#: and a national daily extract is 8.6 million rows, so the window is the filter that
#: makes the read proportional to the question.
WINDOW_PAD_HOURS = 2.0


class RealAisError(RuntimeError):
    """A supplied AIS file could not be used. Carries a message fit to show an operator."""


def _type_fields(vessel: dict[str, Any]) -> dict[str, Any]:
    """Map a real ship-and-cargo code onto the scorer's type fallback."""
    code = vessel.get("vesselTypeCode")
    group = vessel.get("vesselGroup") or mc.vessel_group(code)
    if group is None and code is not None:
        relevance, rationale = UNKNOWN_RELEVANCE
        label = f"ship-and-cargo code {int(code)}"
    elif group is None:
        relevance, rationale = GROUP_RELEVANCE["Not Available"]
        label = "no reported type"
    else:
        relevance, rationale = GROUP_RELEVANCE.get(group, UNKNOWN_RELEVANCE)
        label = group
    return {
        # Deliberately not set: `typeKey` names one of the eight *synthetic* categories,
        # and a real code does not resolve to one. Leaving it None routes `score_type`
        # down its documented fallback instead of asserting a category the file does not
        # support.
        "typeKey": None,
        "type": label,
        "typeRelevance": relevance,
        "typeRationale": rationale,
    }


def _clean(reports: Sequence[dict[str, Any]], max_speed_kn: float = 45.0) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Sort, de-duplicate and sanity-check one real vessel's reports.

    The same three rejections the synthetic cleaner applies -- impossible position,
    duplicate timestamp, impossible implied speed -- because they are properties of AIS
    rather than of the generator. What differs is the completeness expectation, which is
    measured from this vessel's own reporting cadence.
    """
    rejected = {"badPosition": 0, "duplicateTimestamp": 0, "impossibleSpeed": 0, "unparsableTime": 0}

    ordered: list[tuple[datetime, dict[str, Any]]] = []
    for row in reports:
        try:
            ordered.append((parse_utc(row["timeUtc"]), row))
        except Exception:
            rejected["unparsableTime"] += 1
    ordered.sort(key=lambda pair: pair[0])

    accepted: list[dict[str, Any]] = []
    stamps: list[datetime] = []
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
            gap_h = (stamp - stamps[-1]).total_seconds() / 3600.0
            if gap_h > 0:
                implied = (
                    haversine_km(accepted[-1]["lon"], accepted[-1]["lat"], lon, lat)
                    / gap_h
                    / 1.852
                )
                if implied > max_speed_kn:
                    rejected["impossibleSpeed"] += 1
                    continue
        seen.add(row["timeUtc"])
        accepted.append(row)
        stamps.append(stamp)

    intervals = [
        (stamps[i] - stamps[i - 1]).total_seconds() for i in range(1, len(stamps))
    ]
    positive = [s for s in intervals if s > 0]
    # This vessel's own cadence. The median rather than the mean: one six-hour coverage
    # gap would drag a mean far enough to make a well-reported track look sparse.
    typical = float(statistics.median(positive)) if positive else 0.0

    gaps: list[dict[str, Any]] = []
    if typical > 0:
        for i in range(1, len(stamps)):
            seconds = (stamps[i] - stamps[i - 1]).total_seconds()
            if seconds > typical * 1.5:
                gaps.append(
                    {
                        "fromUtc": accepted[i - 1]["timeUtc"],
                        "toUtc": accepted[i]["timeUtc"],
                        "minutes": round(seconds / 60.0, 1),
                    }
                )

    span_s = (stamps[-1] - stamps[0]).total_seconds() if len(stamps) >= 2 else 0.0
    ideal = int(span_s // typical) + 1 if typical > 0 and span_s > 0 else len(accepted)
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
        # Measured from this vessel, not configured. Published so a reader can see what
        # the completeness figure below was measured against.
        "expectedIntervalS": round(typical, 1),
        "intervalBasis": "median observed interval for this vessel",
        "expectedReports": ideal,
        "gaps": gaps,
        "largestGapMinutes": round(max((g["minutes"] for g in gaps), default=0.0), 1),
        "missingFieldValues": missing_fields,
        "reportCompleteness": round(min(1.0, len(accepted) / ideal), 4) if ideal else 0.0,
        "fieldCompleteness": round(1.0 - missing_fields / max(1, 3 * len(accepted)), 4),
    }
    return accepted, summary


def load_feed(
    path: Path | str,
    env: EnvelopeTrack,
    *,
    bounds: Sequence[float] | None = None,
    min_reports: int = 2,
    limit: int | None = None,
    is_water: Any | None = None,
) -> dict[str, Any]:
    """Read a MarineCadastre CSV into the feed shape :func:`scoring.rank_vessels` expects.

    ``env`` is the backward-drift envelope; its window, padded by
    :data:`WINDOW_PAD_HOURS`, is the time filter applied during the read. ``bounds`` is
    optional and, when given, restricts the read spatially as well.
    """
    source = Path(path)
    if not source.exists():
        raise RealAisError(f"no AIS file at {C.display_path(source)}")

    pad = timedelta(hours=WINDOW_PAD_HOURS)
    window_iso = (env.window_start - pad, env.window_end + pad)

    try:
        parsed = mc.read_csv(
            source,
            bounds=bounds,
            window=window_iso,
            limit=limit,
            min_reports=min_reports,
        )
    except mc.SchemaError as exc:
        raise RealAisError(
            f"{source.name} does not have the MarineCadastre header: {exc}"
        ) from exc
    except (OSError, ValueError) as exc:
        raise RealAisError(f"the AIS file could not be read: {exc}") from exc

    vessels: list[dict[str, Any]] = []
    for raw in parsed["vessels"]:
        accepted, cleaning = _clean(raw["reports"])
        if len(accepted) < min_reports:
            continue
        speeds = [row["sogKn"] for row in accepted if row.get("sogKn") is not None]
        on_land = 0
        if is_water is not None:
            on_land = sum(1 for row in accepted if not is_water(row["lon"], row["lat"]))
        vessels.append(
            {
                "mmsi": raw["mmsi"],
                # The file's own name, or nothing. A vessel that did not broadcast a name
                # is shown by its MMSI rather than given a label this project invented.
                "name": raw.get("name") or f"MMSI {raw['mmsi']}",
                "synthetic": False,
                "imo": raw.get("imo"),
                "callSign": raw.get("callSign"),
                "vesselTypeCode": raw.get("vesselTypeCode"),
                "vesselGroup": raw.get("vesselGroup"),
                "cargoCode": raw.get("cargoCode"),
                "lengthM": raw.get("lengthM"),
                "widthM": raw.get("widthM"),
                "draftM": raw.get("draftM"),
                "transceiverClass": raw.get("transceiverClass"),
                **_type_fields(raw),
                # No `pattern`: the synthetic feed assigns each vessel one of five
                # evidence patterns because it built them to demonstrate those patterns.
                # A real vessel was not built to demonstrate anything.
                "pattern": None,
                "firstReportUtc": accepted[0]["timeUtc"],
                "lastReportUtc": accepted[-1]["timeUtc"],
                "reportCount": len(accepted),
                "rawReportCount": len(raw["reports"]),
                "minSogKn": round(min(speeds), 2) if speeds else None,
                "maxSogKn": round(max(speeds), 2) if speeds else None,
                "medianSogKn": round(float(statistics.median(speeds)), 2) if speeds else None,
                "reportsOnLandMask": on_land,
                "cleaning": cleaning,
                "reports": accepted,
                "track": track_line(accepted),
            }
        )

    vessels.sort(key=lambda v: str(v["mmsi"]))
    counts = dict(parsed["counts"])
    counts["vesselsAfterCleaning"] = len(vessels)

    return {
        "mode": "real",
        # The scorer copies this onto the ranking, which the Vessels screen, the
        # provenance block and the incident report all read. Nothing else has to change
        # for the interface to stop saying "synthetic".
        "label": C.LABEL_AIS_REAL,
        "disclaimer": (
            "Positions are as broadcast by the vessels themselves and as received by the "
            "reporting network. AIS can be switched off, mis-configured or wrong, and "
            "gaps in a track are not evidence of anything on their own."
        ),
        "identifierNote": (
            "MMSI, name, IMO and call sign are the vessel's own broadcast identifiers, "
            "reproduced from the supplied file without alteration."
        ),
        "schema": {
            "format": "MarineCadastre AIS",
            "label": mc.source_label(source),
            "reference": "https://marinecadastre.gov/accessais/",
            "header": list(mc.HEADER),
            "sentinels": mc.SENTINELS,
            "file": source.name,
            "note": (
                "Read by spilltrace_drift.marinecadastre.read_csv, the same reader the "
                "synthetic feed is written out through."
            ),
        },
        "pipelineVersion": C.PIPELINE_VERSION,
        "generatedUtc": C.utc_now_iso(),
        "source": parsed["source"],
        "counts": {
            **counts,
            "vessels": len(vessels),
        },
        "filters": {
            "windowStartUtc": iso_utc(window_iso[0]),
            "windowEndUtc": iso_utc(window_iso[1]),
            "windowPadHours": WINDOW_PAD_HOURS,
            "bounds": [round(float(b), 6) for b in bounds] if bounds else None,
            "minReports": min_reports,
            "rule": (
                "reports are read only inside the backward-drift window padded by "
                f"{WINDOW_PAD_HOURS:g} h, and inside the scene footprint when one is given; "
                "a vessel with fewer than the minimum number of surviving reports is dropped "
                "because there is no course to compare against the hindcast"
            ),
        },
        "releaseWindow": {
            "startUtc": iso_utc(env.window_start),
            "endUtc": iso_utc(env.window_end),
            "hours": round((env.window_end - env.window_start).total_seconds() / 3600.0, 2),
            "basis": (
                "the span covered by the backward drift run: oil seen at the acquisition "
                "could have entered the water at any point along the hindcast"
            ),
        },
        "vessels": vessels,
    }
