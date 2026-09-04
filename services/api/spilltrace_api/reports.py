"""Incident report PDF — the document a responder actually files.

Government workflows run on paper, so the report is shaped like a form: a case
number, a timestamp, the numbers, and — as prominently as the numbers — what the
numbers do not mean. Every caveat printed here is *read from the case payload*
rather than written into this module, so the document cannot drift out of step
with what the pipeline actually did. If the drift becomes real-forced tomorrow,
the PDF says so without an edit here.

reportlab is imported lazily, inside the one function that needs it. It is the
only third-party dependency in the entire API and it exists for this file alone;
importing it at module scope makes a PDF library a prerequisite for starting the
server and for *collecting* the test suite. That is how a broken Pillow wheel
once took the whole unrelated suite from passing to not running at all.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from spilltrace_common import config as C

REPORT_DIR = C.PROCESSED_DIR / "reports"

# A case number arrives in a request body and ends up in a filename. Anything
# outside this class is rejected rather than scrubbed, so "../../etc/passwd"
# fails loudly instead of quietly writing somewhere surprising.
SAFE_CASE_NUMBER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

_RL: SimpleNamespace | None = None


class ReportUnavailable(RuntimeError):
    """reportlab, or a dependency of it, is not importable in this environment.

    Raised instead of ImportError so the HTTP layer can answer 503 (the feature
    is missing) rather than 500 (the server is broken).
    """


def _reportlab() -> SimpleNamespace:
    """Import reportlab on demand and cache the handful of names we use."""
    global _RL
    if _RL is not None:
        return _RL
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            PageBreak,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except Exception as exc:  # ImportError, or a bad binary wheel underneath it
        raise ReportUnavailable(
            f"PDF generation requires reportlab and a working Pillow: {exc}. "
            "Install with: .venv/bin/python -m pip install -r requirements.txt"
        ) from exc

    _RL = SimpleNamespace(
        colors=colors,
        TA_CENTER=TA_CENTER,
        A4=A4,
        ParagraphStyle=ParagraphStyle,
        getSampleStyleSheet=getSampleStyleSheet,
        mm=mm,
        PageBreak=PageBreak,
        Paragraph=Paragraph,
        SimpleDocTemplate=SimpleDocTemplate,
        Spacer=Spacer,
        Table=Table,
        TableStyle=TableStyle,
    )
    return _RL


def _value(value: Any, default: str = "Not available") -> str:
    """Render a scalar for a table cell.

    `0` and `0.0` are legitimate answers — an unmeasurable spill age of zero
    hours, a scene with no relevant vessels — so only None and the empty string
    fall through to the default.
    """
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _coord(pair: Any, default: str = "Not available") -> str:
    """Format a project-convention [lon, lat] pair for human eyes.

    Bounds and centroids are stored lon-first throughout the pipeline; reports
    are read latitude-first, which is the convention a coastguard watch officer
    will expect. Convert once, here, rather than in five call sites.
    """
    if isinstance(pair, dict):
        lon, lat = pair.get("lon"), pair.get("lat")
    elif isinstance(pair, (list, tuple)) and len(pair) >= 2:
        lon, lat = pair[0], pair[1]
    else:
        return default
    if lon is None or lat is None:
        return default
    try:
        lat_f, lon_f = float(lat), float(lon)
    except (TypeError, ValueError):
        return default
    ns = "N" if lat_f >= 0 else "S"
    ew = "E" if lon_f >= 0 else "W"
    return f"{abs(lat_f):.6f}° {ns}, {abs(lon_f):.6f}° {ew}"


def _bounds(bounds: Any, default: str = "Not available") -> str:
    """Render [lon_min, lat_min, lon_max, lat_max] as two labelled corners."""
    if not isinstance(bounds, (list, tuple)) or len(bounds) < 4:
        return default
    sw = _coord([bounds[0], bounds[1]])
    ne = _coord([bounds[2], bounds[3]])
    return f"SW {sw}  /  NE {ne}"


def _first_coordinate(case: dict[str, Any]) -> Any:
    """Return the slick centroid as a [lon, lat] pair, or None."""
    slick = case.get("slick") or {}
    centroid = slick.get("centroid")
    if isinstance(centroid, (list, tuple)) and len(centroid) >= 2:
        return centroid

    # Older stored cases carried the centroid as two scalars, or under geometry.
    lon, lat = slick.get("centroidLon"), slick.get("centroidLat")
    if lon is not None and lat is not None:
        return [lon, lat]

    centroid = (case.get("geometry") or {}).get("centroid")
    if isinstance(centroid, (list, tuple, dict)):
        return centroid
    return None


def _top_candidate(case: dict[str, Any]) -> dict[str, Any]:
    vessels = case.get("vessels") or []
    return vessels[0] if vessels else {}


def _forward_endpoint(case: dict[str, Any]) -> Any:
    """Where the forward hindcast leaves the oil.

    `trajectories.forwardEndpoint` exists in the schema but is null in practice,
    so fall back to the last vertex of the forward centroid track — which is the
    same quantity, just not pre-extracted.
    """
    traj = case.get("trajectories") or {}
    endpoint = traj.get("forwardEndpoint")
    if isinstance(endpoint, dict):
        endpoint = endpoint.get("centroid")
    if isinstance(endpoint, (list, tuple)) and len(endpoint) >= 2:
        return endpoint
    track = traj.get("forward")
    if isinstance(track, list) and track and isinstance(track[-1], (list, tuple)):
        return track[-1]
    return None


def _release_window(window: Any) -> tuple[str, str]:
    """Render the release window as (interval, basis).

    Stored as {startUtc, endUtc, hours, basis}. Printing the dict repr would be
    unreadable, and the basis belongs in prose under the table rather than
    squeezed into a cell.
    """
    if not isinstance(window, dict):
        return _value(window), ""
    start, end = window.get("startUtc"), window.get("endUtc")
    hours = window.get("hours")
    if start and end:
        span = f" ({_value(hours)} h)" if hours is not None else ""
        return f"{start}  to  {end}{span}", str(window.get("basis") or "")
    return _value(window.get("label")), str(window.get("basis") or "")


def build_case_number(case: dict[str, Any], override: str | None = None) -> str:
    """Build a stable, filesystem-safe case number.

    Scheme: ST-<acquisition date>-<scene>-<case id>, e.g.
    `ST-20170311-00053-demo`. Derived entirely from the case, so re-running the
    report for the same incident produces the same number rather than a new one
    every time somebody presses the button.
    """
    if override is not None:
        candidate = str(override).strip()
        if not SAFE_CASE_NUMBER.match(candidate):
            raise ValueError(
                "case number must be 1-64 characters of letters, digits, dot, "
                f"dash or underscore, starting alphanumeric; got {candidate!r}"
            )
        return candidate

    scene = case.get("scene") or {}
    acquired = str(scene.get("acquiredStartUtc") or "")
    date = re.sub(r"[^0-9]", "", acquired)[:8] or "00000000"
    parts = ["ST", date]
    for extra in (scene.get("name"), case.get("caseId") or case.get("id")):
        token = re.sub(r"[^A-Za-z0-9]+", "-", str(extra or "")).strip("-")
        if token:
            parts.append(token)
    number = "-".join(parts)[:64].rstrip("-._")
    return number if SAFE_CASE_NUMBER.match(number) else "ST-UNNUMBERED"


def _styles(rl: SimpleNamespace) -> dict[str, Any]:
    base = rl.getSampleStyleSheet()
    return {
        "title": rl.ParagraphStyle(
            "IncidentTitle", parent=base["Title"], alignment=rl.TA_CENTER,
            fontSize=18, spaceAfter=2,
        ),
        "subtitle": rl.ParagraphStyle(
            "IncidentSubtitle", parent=base["BodyText"], alignment=rl.TA_CENTER,
            fontSize=9, textColor=rl.colors.HexColor("#8a1c1c"), spaceAfter=10,
        ),
        "heading": rl.ParagraphStyle(
            "IncidentHeading", parent=base["Heading2"], fontSize=11.5,
            spaceBefore=12, spaceAfter=5,
            textColor=rl.colors.HexColor("#12263a"),
        ),
        "body": rl.ParagraphStyle(
            "IncidentBody", parent=base["BodyText"], fontSize=8.5, leading=12,
            spaceAfter=4,
        ),
        "note": rl.ParagraphStyle(
            "IncidentNote", parent=base["BodyText"], fontSize=7.5, leading=10.5,
            textColor=rl.colors.HexColor("#4a5568"), spaceAfter=4,
        ),
    }


def _kv_table(rl: SimpleNamespace, rows: list[list[Any]], label_mm: float = 58.0) -> Any:
    """The one table style used throughout: bold grey label column, value column.

    Five copies of this TableStyle is five places for the report to look subtly
    inconsistent, so there is one.
    """
    table = rl.Table(rows, colWidths=[label_mm * rl.mm, (174.0 - label_mm) * rl.mm])
    table.setStyle(
        rl.TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, rl.colors.HexColor("#b8c2cc")),
                ("BACKGROUND", (0, 0), (0, -1), rl.colors.HexColor("#eef2f6")),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7.8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def _wrapped(rl: SimpleNamespace, styles: dict[str, Any], text: Any) -> Any:
    """Long prose in a table cell must be a Paragraph or reportlab will not wrap it."""
    return rl.Paragraph(_value(text).replace("&", "&amp;").replace("<", "&lt;"), styles["note"])


def _score_table(rl: SimpleNamespace, styles: dict[str, Any], detail: Any) -> Any | None:
    """The score breakdown: one row per component, with the reason it scored that.

    This table is the point of the whole section. A rank without a reason is an
    accusation; a rank with six auditable reasons is a triage aid somebody can
    check and overrule.
    """
    if not isinstance(detail, list) or not detail:
        return None
    rows = [["Component", "Score", "Why it scored that"]]
    for item in detail:
        if not isinstance(item, dict):
            continue
        rows.append(
            [
                _value(item.get("component")),
                f"{_value(item.get('score'))} / {_value(item.get('max'))}",
                _wrapped(rl, styles, item.get("reason")),
            ]
        )
    if len(rows) == 1:
        return None
    table = rl.Table(rows, colWidths=[26 * rl.mm, 20 * rl.mm, 128 * rl.mm], repeatRows=1)
    table.setStyle(
        rl.TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, rl.colors.HexColor("#b8c2cc")),
                ("BACKGROUND", (0, 0), (-1, 0), rl.colors.HexColor("#12263a")),
                ("TEXTCOLOR", (0, 0), (-1, 0), rl.colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7.8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def _patch_table(rl: SimpleNamespace, styles: dict[str, Any], patches: Any, limit: int = 6) -> Any | None:
    """The screened dark patches: one row per proposal, with the reason for the verdict.

    Ordered rejected first. A report that only listed what the pipeline detected would give
    a reader no way to judge how discriminating the detection was, and the rejected patches
    are the only evidence in the document about what it declines to call oil.
    """
    if not isinstance(patches, list) or not patches:
        return None
    order = {"rejected": 0, "uncertain": 1, "unscreened": 2, "accepted": 3}
    ranked = sorted(
        (patch for patch in patches if isinstance(patch, dict)),
        key=lambda patch: (order.get(str(patch.get("label")), 4), -float(patch.get("areaKm2") or 0.0)),
    )
    rows = [["Patch", "Area km2", "Oil-like", "Verdict", "On slick", "Leading reason"]]
    for patch in ranked[: max(1, limit)]:
        measured = patch.get("measured") or {}
        reasons = patch.get("reasons") or []
        rows.append(
            [
                _value(patch.get("id")),
                _value(patch.get("areaKm2")),
                _value(patch.get("oilLikelihood")),
                _value(patch.get("label")),
                _value(patch.get("overlapsSlick"), "no"),
                _wrapped(
                    rl,
                    styles,
                    reasons[0] if reasons else f"darkness {measured.get('darknessZ')} background sigma",
                ),
            ]
        )
    if len(rows) == 1:
        return None
    table = rl.Table(
        rows,
        colWidths=[16 * rl.mm, 17 * rl.mm, 15 * rl.mm, 19 * rl.mm, 17 * rl.mm, 90 * rl.mm],
        repeatRows=1,
    )
    table.setStyle(
        rl.TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, rl.colors.HexColor("#b8c2cc")),
                ("BACKGROUND", (0, 0), (-1, 0), rl.colors.HexColor("#12263a")),
                ("TEXTCOLOR", (0, 0), (-1, 0), rl.colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7.8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def _footer(rl: SimpleNamespace, case_number: str):
    """Stamp every page, so a loose printout is still traceable and still caveated."""

    def draw(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(rl.colors.HexColor("#6b7280"))
        canvas.drawString(18 * rl.mm, 10 * rl.mm, f"SpillTrace case {case_number}")
        canvas.drawCentredString(
            rl.A4[0] / 2.0, 10 * rl.mm,
            "Research proof of concept — not evidence — human review required",
        )
        canvas.drawRightString(rl.A4[0] - 18 * rl.mm, 10 * rl.mm, f"Page {doc.page}")
        canvas.restoreState()

    return draw


def generate_incident_report(
    case: dict[str, Any],
    case_number: str | None = None,
    output_dir: Path | None = None,
) -> Path:
    """Write the incident report PDF for one case and return its path.

    Raises ReportUnavailable if reportlab cannot be imported, and ValueError if
    `case_number` is not filesystem-safe.
    """
    rl = _reportlab()
    styles = _styles(rl)

    number = build_case_number(case, case_number)
    timestamp = datetime.now(timezone.utc)

    directory = Path(output_dir) if output_dir is not None else REPORT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    output_path = directory / f"{number}_incident_report.pdf"

    scene = case.get("scene") or {}
    slick = case.get("slick") or {}
    detection = case.get("detection") or {}
    geometry = case.get("geometry") or {}
    provenance = case.get("provenance") or {}
    attribution = case.get("attribution") or {}
    spill_age = case.get("spillAge") or {}
    trajectories = case.get("trajectories") or {}
    drift = case.get("drift") or {}
    limits = case.get("limits") or []

    origin = trajectories.get("originEstimate") or {}
    if not isinstance(origin, dict):
        origin = {}
    resolution = spill_age.get("resolution") or {}
    separation = resolution.get("bestSeparation") or {}
    candidate = _top_candidate(case)
    filtering = attribution.get("filtering") or {}
    backward_cfg = ((drift.get("backward") or {}).get("config")) or {}
    numerics = ((drift.get("backward") or {}).get("numerics")) or {}
    screening = case.get("screening") or {}
    if not isinstance(screening, dict):
        screening = {}
    screening_counts = screening.get("counts") or {}
    screening_thresholds = screening.get("thresholds") or {}
    screening_components = screening.get("components") or {}
    primary_screening = slick.get("screening") or {}
    if not isinstance(primary_screening, dict):
        primary_screening = {}

    document = rl.SimpleDocTemplate(
        str(output_path),
        pagesize=rl.A4,
        title=f"SpillTrace incident report {number}",
        author="SpillTrace (research proof of concept)",
        subject="Oil spill detection, drift hindcast and vessel triage",
        rightMargin=18 * rl.mm,
        leftMargin=18 * rl.mm,
        topMargin=16 * rl.mm,
        bottomMargin=18 * rl.mm,
    )

    story: list[Any] = []

    # -- header ------------------------------------------------------------
    story.append(rl.Paragraph("OIL SPILL INCIDENT REPORT", styles["title"]))
    story.append(
        rl.Paragraph(
            "SpillTrace — research proof of concept. Not an operational or legal "
            "determination. Human review required before any action.",
            styles["subtitle"],
        )
    )

    # -- 1. case information -----------------------------------------------
    story.append(rl.Paragraph("1. Case information", styles["heading"]))
    story.append(
        _kv_table(
            rl,
            [
                ["Case number", number],
                ["Report generated (UTC)", timestamp.strftime("%Y-%m-%d %H:%M:%S")],
                ["Case identifier", _value(case.get("caseId") or case.get("id"))],
                ["Pipeline version", _value(case.get("pipelineVersion"))],
                ["Case status", _value(case.get("status"))],
                ["Satellite / mission", _value(provenance.get("satellite") or scene.get("mission"))],
                ["Acquisition start (UTC)", _value(scene.get("acquiredStartUtc"))],
                ["Acquisition stop (UTC)", _value(scene.get("acquiredStopUtc"))],
                ["Scene", _value(scene.get("name"))],
                ["Region", _value(scene.get("region"))],
                ["Source product", _wrapped(rl, styles, scene.get("productId"))],
            ],
        )
    )
    if scene.get("regionNote"):
        story.append(rl.Paragraph(f"Region: {scene['regionNote']}.", styles["note"]))

    # -- 2. location -------------------------------------------------------
    story.append(rl.Paragraph("2. Incident location", styles["heading"]))
    story.append(
        _kv_table(
            rl,
            [
                ["Slick centroid", _coord(_first_coordinate(case))],
                ["Scene footprint", _bounds(scene.get("bounds"))],
                ["Coordinate reference", _value(scene.get("crs") or f"EPSG:{scene.get('epsg')}")],
                ["Scene size (pixels)", f"{_value(scene.get('width'))} x {_value(scene.get('height'))}"],
            ],
        )
    )

    # -- 3. detection ------------------------------------------------------
    story.append(rl.Paragraph("3. Oil spill detection", styles["heading"]))
    story.append(
        _kv_table(
            rl,
            [
                ["Detection source", _value(provenance.get("detectionLabel") or detection.get("label"))],
                ["Largest slick area (km²)", _value(slick.get("areaKm2"))],
                [
                    "Total detected area (km²)",
                    _value(slick.get("totalAreaKm2") if slick.get("totalAreaKm2") is not None else slick.get("areaKm2")),
                ],
                ["Slick count", _value(slick.get("slickCount"))],
                ["Detection confidence", _value(slick.get("confidence"))],
                ["Perimeter (km)", _value(slick.get("perimeterKm"))],
                ["Length x width (km)", f"{_value(slick.get('lengthKm'))} x {_value(slick.get('widthKm'))}"],
                ["Orientation (° from north)", _value(slick.get("orientationDegFromNorth"))],
                ["Extends beyond scene edge", _value(slick.get("touchesSceneEdge"))],
                ["Probability threshold", _value(detection.get("threshold"))],
                ["Threshold selected on", _value(detection.get("thresholdSelectedOn"))],
                ["Model checkpoint", _value(detection.get("checkpoint"))],
                [
                    "Look-alike verdict, largest slick",
                    f"{_value(primary_screening.get('label'), 'not screened')} "
                    f"(oil-likelihood {_value(primary_screening.get('oilLikelihood'))})",
                ],
            ],
        )
    )
    if detection.get("note"):
        story.append(rl.Paragraph(f"<b>Method:</b> {detection['note']}", styles["note"]))
    if geometry.get("areaMethod"):
        story.append(rl.Paragraph(f"<b>Area:</b> {geometry['areaMethod']}.", styles["note"]))
    if slick.get("touchesSceneEdge"):
        story.append(
            rl.Paragraph(
                "<b>Area is a lower bound.</b> The slick reaches the edge of the "
                "imaged swath, so an unknown part of it lies outside this scene.",
                styles["note"],
            )
        )

    # -- 3.1 look-alike screening -------------------------------------------
    # A sub-section rather than a section of its own: this is a property of the detection,
    # and promoting it would renumber a document people may already have printed.
    if screening:
        story.append(rl.Paragraph("3.1 Look-alike screening", styles["heading"]))
        story.append(
            _kv_table(
                rl,
                [
                    ["Screen fitted", _value(screening.get("fitted"))],
                    ["Dark patches examined", _value(screening_counts.get("proposed"))],
                    ["Rejected as look-alikes", _value(screening_counts.get("rejected"))],
                    ["Kept for human review", _value(screening_counts.get("uncertain"))],
                    ["Consistent with oil", _value(screening_counts.get("accepted"))],
                    [
                        "Patches on a published slick",
                        _value(screening_counts.get("overlappingPublishedSlick")),
                    ],
                    [
                        "Published slicks screened",
                        f"{_value(screening_components.get('screened'), '0')} screened, "
                        f"{_value(screening_components.get('accepted'), '0')} consistent with "
                        f"oil, {_value(screening_components.get('rejected'), '0')} rejected",
                    ],
                    [
                        "Decision thresholds",
                        f"reject at or below {_value(screening_thresholds.get('rejectAtOrBelow'))}, "
                        f"accept at or above {_value(screening_thresholds.get('acceptAtOrAbove'))}",
                    ],
                    ["Calibration", _wrapped(rl, styles, screening.get("calibration"))],
                ],
            )
        )
        if screening.get("headline"):
            story.append(rl.Paragraph(f"<b>Result:</b> {screening['headline']}.", styles["note"]))
        if screening_components.get("note"):
            story.append(
                rl.Paragraph(
                    f"<b>Two populations:</b> {screening_components['note']}.",
                    styles["note"],
                )
            )
        patch_table = _patch_table(rl, styles, screening.get("patches"))
        if patch_table is not None:
            story.append(rl.Spacer(1, 3))
            story.append(patch_table)
        screening_limits = screening.get("limits")
        if isinstance(screening_limits, list) and screening_limits:
            # Verbatim from the screen rather than paraphrased. An unfitted screen says so
            # here, and the fixed paragraph below cannot vary with what actually ran.
            story.append(rl.Spacer(1, 3))
            for index, item in enumerate(screening_limits, start=1):
                story.append(rl.Paragraph(f"{index}. {item}", styles["body"]))
        story.append(
            rl.Paragraph(
                "<b>What this screening does not say.</b> It separates oil-like from "
                "not-oil-like and cannot name the phenomenon, so no rejected patch is "
                "called algae, low wind or a wake. A patch between the two thresholds is "
                "returned as uncertain and kept: suppressing a detection the screen is "
                "unsure of would trade a measured false positive for an unmeasured missed "
                "spill. Patches that overlap no published slick are darkness proposals, "
                "not detections this report is making.",
                styles["note"],
            )
        )

    # -- 4. drift ----------------------------------------------------------
    window_text, window_basis = _release_window(
        attribution.get("releaseWindow") or spill_age.get("releaseWindow")
    )
    story.append(rl.Paragraph("4. Drift hindcast and estimated origin", styles["heading"]))
    story.append(
        _kv_table(
            rl,
            [
                ["Forcing", _value(provenance.get("driftLabel") or provenance.get("driftMode"))],
                ["Estimated origin", _coord(origin.get("centroid"))],
                ["Origin uncertainty P50 (km)", _value(origin.get("radiusP50Km"))],
                ["Origin uncertainty P90 (km)", _value(origin.get("radiusP90Km"))],
                ["Hindcast horizon (h)", _value(origin.get("hoursBeforeObservation") or backward_cfg.get("horizon_hours"))],
                ["Estimated release window", window_text],
                ["Forward drift endpoint", _coord(_forward_endpoint(case))],
                ["Particles / timestep", f"{_value(backward_cfg.get('particle_count'))} / {_value(backward_cfg.get('time_step_minutes'))} min"],
                ["Windage factor", _value(backward_cfg.get("windage_factor"))],
                ["Diffusivity (m²/s)", _value(backward_cfg.get("diffusion_m2_s"))],
                ["Integration scheme", _value(numerics.get("scheme"))],
            ],
        )
    )
    if origin.get("interpretation"):
        story.append(rl.Paragraph(f"<b>Reading the origin:</b> {origin['interpretation']}", styles["note"]))
    if numerics.get("note"):
        story.append(rl.Paragraph(f"<b>Numerics:</b> {numerics['note']}", styles["note"]))

    if window_basis:
        story.append(rl.Paragraph(f"<b>Release window:</b> {window_basis}.", styles["note"]))

    # -- 5. spill age ------------------------------------------------------
    story.append(rl.Paragraph("5. Spill age", styles["heading"]))
    story.append(
        _kv_table(
            rl,
            [
                ["Assessment", _value(spill_age.get("label"))],
                [
                    "Age interval (h)",
                    f"{_value(spill_age.get('minHours'))} to {_value(spill_age.get('maxHours'))}",
                ],
                ["Earliest release (UTC)", _value(spill_age.get("earliestReleaseUtc"))],
                ["Observed at (UTC)", _value(spill_age.get("acquiredUtc"))],
                ["Narrowed by drift geometry", _value(resolution.get("resolvable"))],
                [
                    "Displacement vs uncertainty",
                    f"{_value(separation.get('displacementKm'))} km moved against "
                    f"{_value(separation.get('radiusKm'))} km P90 radius "
                    f"(ratio {_value(separation.get('ratio'))})",
                ],
            ],
        )
    )
    if spill_age.get("basis"):
        story.append(rl.Paragraph(f"<b>Basis:</b> {spill_age['basis']}", styles["note"]))
    if resolution.get("note"):
        story.append(rl.Paragraph(f"<b>Why it cannot be narrowed:</b> {resolution['note']}", styles["note"]))
    if spill_age.get("caveat"):
        story.append(rl.Paragraph(f"<b>Caveat:</b> {spill_age['caveat']}", styles["note"]))
    narrowed_by = spill_age.get("narrowedBy") or []
    if isinstance(narrowed_by, list) and narrowed_by:
        # An en dash, not a bullet: reportlab's WinAnsi table for the base-14 fonts has
        # no U+2022, so `unicode2T1` quietly substitutes the notdef byte and the marker
        # renders as a blank. U+2013 is in the table.
        items = "".join(f"<br/>&nbsp;&nbsp;&#8211; {item}" for item in narrowed_by)
        story.append(rl.Paragraph(f"<b>What would narrow it:</b>{items}", styles["note"]))

    # -- 6. vessel attribution ---------------------------------------------
    story.append(rl.PageBreak())
    story.append(rl.Paragraph("6. Vessel triage", styles["heading"]))
    story.append(
        rl.Paragraph(
            f"<b>{_value(provenance.get('aisLabel'), 'AIS mode: unstated')}</b>",
            styles["body"],
        )
    )
    story.append(
        _kv_table(
            rl,
            [
                ["AIS mode", _value(provenance.get("aisMode"))],
                ["AIS field schema", _value(provenance.get("aisSchema"))],
                ["Schema reference", _wrapped(rl, styles, provenance.get("aisSchemaReference"))],
                ["Vessels seen in scene window", _value(filtering.get("vesselsSeen") or attribution.get("candidateCount"))],
                ["AIS reports considered", _value(filtering.get("aisReports"))],
                ["Reports inside release window", _value(filtering.get("reportsInWindow"))],
                ["Reports near drift envelope", _value(filtering.get("reportsNearEnvelope"))],
                ["Vessels passing both tests", _value(filtering.get("vesselsRelevant") or attribution.get("relevantCount"))],
                [
                    "Vessels excluded",
                    f"{_value(filtering.get('excludedTotal') or attribution.get('excludedCount'))} "
                    f"({_value(filtering.get('excludedOutsideWindow'))} outside the window, "
                    f"{_value(filtering.get('excludedTooFar'))} too far from the envelope)",
                ],
            ],
        )
    )
    if filtering.get("rule"):
        story.append(rl.Paragraph(f"<b>Relevance rule:</b> {filtering['rule']}", styles["note"]))

    story.append(rl.Paragraph("6.1 Highest-ranked candidate", styles["heading"]))
    if candidate:
        story.append(
            _kv_table(
                rl,
                [
                    ["Status", _value(attribution.get("candidateLabel"), "Priority candidate for investigation")],
                    ["Vessel name", _value(candidate.get("name"))],
                    ["MMSI", _value(candidate.get("mmsi"))],
                    ["Reported type", _value(candidate.get("type"))],
                    ["Rank", _value(candidate.get("rank"))],
                    ["Score", f"{_value(candidate.get('score'))} / {_value(candidate.get('scoreMax'))}"],
                    ["Assessment", _wrapped(rl, styles, candidate.get("band"))],
                    ["Why it is in scope", _wrapped(rl, styles, candidate.get("relevanceReason"))],
                ],
            )
        )
        breakdown = _score_table(rl, styles, candidate.get("componentDetail"))
        if breakdown is not None:
            story.append(rl.Spacer(1, 6))
            story.append(breakdown)
    else:
        story.append(
            rl.Paragraph(
                "No vessel satisfied both halves of the relevance test for this case. "
                "No candidate is named.",
                styles["body"],
            )
        )

    evidence = candidate.get("evidence") or {}
    if isinstance(evidence, dict) and evidence:
        story.append(rl.Paragraph("6.2 Supporting measurements", styles["heading"]))
        story.append(
            _kv_table(
                rl,
                [
                    [
                        "Closest approach",
                        f"{_value(evidence.get('closestApproachKm'))} km at "
                        f"{_value(evidence.get('closestApproachUtc'))} "
                        f"({_value(evidence.get('closestApproachRadii'))} envelope radii)",
                    ],
                    ["Drift envelope radius then (km)", _value(evidence.get("envelopeRadiusKm"))],
                    [
                        "Reports near envelope",
                        f"{_value(evidence.get('reportsNearEnvelope'))} of "
                        f"{_value(evidence.get('reportsTotal'))} "
                        f"({_value(evidence.get('minutesNearEnvelope'))} min)",
                    ],
                    [
                        "Course / speed at approach",
                        f"{_value(evidence.get('courseAtApproachDeg'))}° at "
                        f"{_value(evidence.get('sogAtApproachKn'))} kn",
                    ],
                    [
                        "AIS track coverage",
                        f"{_value(evidence.get('firstReportUtc'))} to {_value(evidence.get('lastReportUtc'))}",
                    ],
                ],
            )
        )
    if attribution.get("caveat"):
        story.append(rl.Spacer(1, 4))
        story.append(rl.Paragraph(f"<b>{attribution['caveat']}</b>", styles["note"]))

    # -- 7. provenance -----------------------------------------------------
    story.append(rl.Paragraph("7. Provenance", styles["heading"]))
    story.append(
        rl.Paragraph(
            "Where every number above came from. A field marked synthetic was "
            "generated deterministically by this pipeline and describes no real "
            "vessel, current or wind.",
            styles["body"],
        )
    )
    labels = {
        "aisLabel": "AIS label", "aisMode": "AIS mode", "aisSchema": "AIS schema",
        "aisSchemaReference": "AIS schema reference", "aisSource": "AIS source",
        "detectionLabel": "Detection label", "detectionSource": "Detection source",
        "driftLabel": "Drift label", "driftMode": "Drift mode",
        "satellite": "Satellite", "status": "Overall status",
    }
    provenance_rows = [
        [labels.get(key, re.sub(r"(?<!^)(?=[A-Z])", " ", str(key)).capitalize()),
         _wrapped(rl, styles, value)]
        for key, value in sorted(provenance.items())
    ]
    if provenance_rows:
        story.append(_kv_table(rl, provenance_rows, label_mm=46.0))

    # -- 8. stated limitations ---------------------------------------------
    # Printed verbatim from the case rather than paraphrased here. If the
    # pipeline's own honesty text changes, this section changes with it.
    story.append(rl.Paragraph("8. Stated limitations", styles["heading"]))
    if limits:
        for i, item in enumerate(limits, start=1):
            story.append(rl.Paragraph(f"{i}. {item}", styles["body"]))
    else:
        story.append(
            rl.Paragraph(
                "The case payload carries no limitations block. Treat that as "
                "missing information, not as an absence of limitations.",
                styles["body"],
            )
        )

    # -- 9. disclaimer and sign-off ----------------------------------------
    story.append(rl.Paragraph("9. Operational disclaimer", styles["heading"]))
    story.append(
        rl.Paragraph(
            "This report is the output of a research proof of concept. The vessel "
            "ranking in section 6 is a triage aid: it orders candidates for human "
            "attention and does not establish responsibility for the observed oil. "
            f"For this case the AIS is <b>{_value(provenance.get('aisMode'), 'unstated')}</b> and the "
            f"drift forcing is <b>{_value(provenance.get('driftMode'), 'unstated')}</b>; read sections 7 "
            "and 8 before quoting any figure. Verification against licensed AIS, "
            "observed metocean data and, where possible, a second acquisition of "
            "the same area is required before operational or legal use.",
            styles["body"],
        )
    )
    story.append(rl.Spacer(1, 10))
    signoff = _kv_table(
        rl,
        [
            ["Reviewed by (name, role)", ""],
            ["Review date (UTC)", ""],
            ["Verified against licensed AIS", ""],
            ["Action taken", ""],
        ],
        label_mm=58.0,
    )
    # Blank rows for a wet signature: the report is a form, and somebody has to
    # own the decision it leads to. Extra padding gives them room to write.
    signoff.setStyle(
        rl.TableStyle(
            [
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(signoff)

    document.build(story, onFirstPage=_footer(rl, number), onLaterPages=_footer(rl, number))
    return output_path
