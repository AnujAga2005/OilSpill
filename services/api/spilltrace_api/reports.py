from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from spilltrace_common import config as C

from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
)


REPORT_DIR = C.PROCESSED_DIR / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def _value(value: Any, default: str = "Not available") -> str:
    if value is None or value == "":
        return default
    return str(value)


def _first_coordinate(case: dict[str, Any]) -> tuple[Any, Any]:
    """Return the slick centroid as (longitude, latitude)."""
    slick = case.get("slick") or {}
    centroid = slick.get("centroid")
    if isinstance(centroid, (list, tuple)) and len(centroid) >= 2:
        return centroid[0], centroid[1]

    # Backward-compatible support for older stored cases.
    lon = slick.get("centroidLon")
    lat = slick.get("centroidLat")
    if lon is not None and lat is not None:
        return lon, lat

    geometry = case.get("geometry") or {}
    centroid = geometry.get("centroid")
    if isinstance(centroid, (list, tuple)) and len(centroid) >= 2:
        return centroid[0], centroid[1]
    if isinstance(centroid, dict):
        lon, lat = centroid.get("lon"), centroid.get("lat")
        if lon is not None and lat is not None:
            return lon, lat

    return "Not available", "Not available"


def _top_candidate(case: dict[str, Any]) -> dict[str, Any]:
    vessels = case.get("vessels") or []

    if not vessels:
        return {}

    return vessels[0]


def generate_incident_report(
    case: dict[str, Any],
    case_number: str | None = None,
) -> Path:

    case_id = (
        case_number
        or case.get("caseId")
        or case.get("id")
        or "UNKNOWN"
    )

    timestamp = datetime.now(timezone.utc)

    filename = f"{case_id}_incident_report.pdf"
    output_path = REPORT_DIR / filename

    slick = case.get("slick") or {}
    scene = case.get("scene") or {}
    provenance = case.get("provenance") or {}
    attribution = case.get("attribution") or {}
    spill_age = case.get("spillAge") or {}
    backward = case.get("backward") or {}
    forward = case.get("forward") or {}

    candidate = _top_candidate(case)

    lon, lat = _first_coordinate(case)

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "IncidentTitle",
        parent=styles["Title"],
        alignment=TA_CENTER,
        fontSize=20,
        spaceAfter=12,
    )

    heading_style = ParagraphStyle(
        "IncidentHeading",
        parent=styles["Heading2"],
        fontSize=13,
        spaceBefore=12,
        spaceAfter=6,
    )

    normal_style = ParagraphStyle(
        "IncidentNormal",
        parent=styles["BodyText"],
        fontSize=9,
        leading=13,
    )

    document = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )

    story = []

    # ---------------------------------------------------------
    # HEADER
    # ---------------------------------------------------------

    story.append(
        Paragraph(
            "SPILLTRACE — OIL SPILL INCIDENT REPORT",
            title_style,
        )
    )

    story.append(
        Paragraph(
            "Research PoC — Human Review Required",
            normal_style,
        )
    )

    story.append(Spacer(1, 8))

    # ---------------------------------------------------------
    # CASE INFORMATION
    # ---------------------------------------------------------

    story.append(
        Paragraph(
            "1. Case Information",
            heading_style,
        )
    )

    case_table = [
        ["Case Number", _value(case_id)],
        ["Generated UTC", timestamp.isoformat()],
        ["Case ID", _value(case.get("id"))],
        ["Pipeline Version", _value(case.get("pipelineVersion"))],
        ["Status", _value(case.get("status"))],
        ["Scene", _value(scene.get("name"))],
        ["Region", _value(scene.get("region"))],
        ["Mission", _value(scene.get("mission"))],
        [
            "Acquisition Start",
            _value(scene.get("acquiredStartUtc")),
        ],
    ]

    table = Table(case_table, colWidths=[55 * mm, 115 * mm])

    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )

    story.append(table)

    # ---------------------------------------------------------
    # INCIDENT LOCATION
    # ---------------------------------------------------------

    story.append(
        Paragraph(
            "2. Incident Location",
            heading_style,
        )
    )

    location_table = [
        ["Latitude", _value(lat)],
        ["Longitude", _value(lon)],
        ["Scene Bounds", _value(scene.get("bounds"))],
    ]

    table = Table(
        location_table,
        colWidths=[55 * mm, 115 * mm],
    )

    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
            ]
        )
    )

    story.append(table)

    # ---------------------------------------------------------
    # DETECTION
    # ---------------------------------------------------------

    story.append(
        Paragraph(
            "3. Oil Spill Detection",
            heading_style,
        )
    )

    detection_table = [
        ["Detected Area (km²)", _value(slick.get("areaKm2"))],
        ["Total Area (km²)", _value(slick.get("totalAreaKm2"))],
        ["Slick Count", _value(slick.get("slickCount"))],
        ["Confidence", _value(slick.get("confidence"))],
        ["Perimeter (km)", _value(slick.get("perimeterKm"))],
        ["Length (km)", _value(slick.get("lengthKm"))],
        ["Width (km)", _value(slick.get("widthKm"))],
        ["Orientation (° from north)", _value(slick.get("orientationDegFromNorth"))],
        ["Touches Scene Edge", _value(slick.get("touchesSceneEdge"))],
    ]

    table = Table(
        detection_table,
        colWidths=[65 * mm, 105 * mm],
    )

    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
            ]
        )
    )

    story.append(table)

    # ---------------------------------------------------------
    # DRIFT
    # ---------------------------------------------------------

    story.append(
        Paragraph(
            "4. Drift Analysis",
            heading_style,
        )
    )

    origin = (case.get("trajectories") or {}).get("originEstimate") or backward.get("originEstimate") or {}
    endpoint = (case.get("trajectories") or {}).get("forwardEndpoint") or {}
    drift_table = [
        ["Drift Mode", _value(provenance.get("driftMode"))],
        ["Spill Age", _value(spill_age.get("label"))],
        ["Release Window", _value((backward.get("originEstimate") or {}).get("releaseWindow") or (case.get("attribution") or {}).get("releaseWindow"))],
        ["Origin Estimate", _value(origin.get("centroid") if isinstance(origin, dict) else origin)],
        ["Origin Radius (km)", _value(origin.get("radiusKm") if isinstance(origin, dict) else None)],
        ["Forward Endpoint", _value(endpoint.get("centroid") if isinstance(endpoint, dict) else endpoint)],
    ]

    table = Table(
        drift_table,
        colWidths=[65 * mm, 105 * mm],
    )

    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
            ]
        )
    )

    story.append(table)

    # ---------------------------------------------------------
    # VESSEL ATTRIBUTION
    # ---------------------------------------------------------

    story.append(
        Paragraph(
            "5. Vessel Attribution",
            heading_style,
        )
    )

    vessel_table = [
        ["Candidate Count", _value(attribution.get("candidateCount"))],
        ["Relevant Candidates", _value(attribution.get("relevantCount"))],
        ["Top Candidate", _value(candidate.get("name"))],
        ["MMSI", _value(candidate.get("mmsi"))],
        ["Score", _value(candidate.get("score"))],
        ["Assessment", _value(candidate.get("band"))],
    ]

    table = Table(
        vessel_table,
        colWidths=[65 * mm, 105 * mm],
    )

    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
            ]
        )
    )

    story.append(table)

    # ---------------------------------------------------------
    # PROVENANCE
    # ---------------------------------------------------------

    story.append(
        PageBreak()
    )

    story.append(
        Paragraph(
            "6. Full Provenance",
            heading_style,
        )
    )

    story.append(
        Paragraph(
            "The following provenance fields describe the source and processing "
            "status of the information contained in this incident report.",
            normal_style,
        )
    )

    provenance_rows = []

    for key, value in provenance.items():
        provenance_rows.append(
            [
                str(key),
                str(value),
            ]
        )

    if provenance_rows:
        table = Table(
            provenance_rows,
            colWidths=[60 * mm, 110 * mm],
        )

        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("FONTSIZE", (0, 0), (-1, -1), 7),
                ]
            )
        )

        story.append(table)

    # ---------------------------------------------------------
    # DISCLAIMER
    # ---------------------------------------------------------

    story.append(
        Spacer(1, 15)
    )

    story.append(
        Paragraph(
            "<b>Operational disclaimer:</b> This report is a research proof of "
            "concept. Vessel attribution is a triage aid and does not establish "
            "responsibility. AIS and drift forcing may be synthetic depending "
            "on the case. Human verification against licensed AIS and observed "
            "metocean data is required.",
            normal_style,
        )
    )

    # ---------------------------------------------------------
    # BUILD PDF
    # ---------------------------------------------------------

    document.build(story)

    return output_path