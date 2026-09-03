"""Email dispatch for completed SpillTrace incident reports.

The dispatcher deliberately uses the Python standard library. SMTP configuration is
read from environment variables so credentials never live in source control. The same
PDF generated for an incident is attached to the outgoing message.
"""

from __future__ import annotations

import os
import re
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from pathlib import Path
from typing import Any, Iterable

from .reports import generate_incident_report

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class DispatchError(RuntimeError):
    """An incident email could not be dispatched."""


def _recipients(value: Any) -> list[str]:
    if isinstance(value, str):
        raw: Iterable[Any] = re.split(r"[,;]", value)
    elif isinstance(value, (list, tuple)):
        raw = value
    else:
        raw = []

    out: list[str] = []
    for item in raw:
        address = str(item).strip()
        if not address:
            continue
        _name, parsed = parseaddr(address)
        address = parsed or address
        if not EMAIL_RE.match(address):
            raise DispatchError(f"invalid recipient address {address!r}")
        if address.lower() not in {x.lower() for x in out}:
            out.append(address)
    if not out:
        raise DispatchError("at least one recipient email address is required")
    return out


def _env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, "1" if default else "0").strip().lower() in {"1", "true", "yes", "on"}


def _smtp_config() -> dict[str, Any]:
    host = os.environ.get("SPILLTRACE_SMTP_HOST", "").strip()
    if not host:
        raise DispatchError("SPILLTRACE_SMTP_HOST is not configured")
    try:
        port = int(os.environ.get("SPILLTRACE_SMTP_PORT", "587"))
    except ValueError as exc:
        raise DispatchError("SPILLTRACE_SMTP_PORT must be an integer") from exc
    # Accept both the canonical names and the names used by the original project .env.
    username = (
        os.environ.get("SPILLTRACE_SMTP_USERNAME")
        or os.environ.get("SPILLTRACE_SMTP_USER")
        or ""
    ).strip()
    password = os.environ.get("SPILLTRACE_SMTP_PASSWORD", "")
    sender = (
        os.environ.get("SPILLTRACE_EMAIL_FROM")
        or os.environ.get("SPILLTRACE_ALERT_FROM")
        or username
    ).strip()
    if not sender:
        raise DispatchError("SPILLTRACE_EMAIL_FROM or SPILLTRACE_SMTP_USERNAME is required")
    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "sender": sender,
        "starttls": _env_bool("SPILLTRACE_SMTP_STARTTLS", True),
        "ssl": _env_bool("SPILLTRACE_SMTP_SSL", False),
        "timeout": float(os.environ.get("SPILLTRACE_SMTP_TIMEOUT", "20")),
    }


def _case_summary(case: dict[str, Any]) -> dict[str, Any]:
    scene = case.get("scene") or {}
    slick = case.get("slick") or {}
    attribution = case.get("attribution") or {}
    vessels = case.get("vessels") or []
    top = vessels[0] if vessels else {}
    return {
        "case_id": case.get("id") or case.get("caseId"),
        "scene": scene.get("name"),
        "region": scene.get("region"),
        "acquired": scene.get("acquiredStartUtc"),
        "area_km2": slick.get("totalAreaKm2") or slick.get("areaKm2"),
        "confidence": slick.get("confidence"),
        "candidate_count": attribution.get("candidateCount", len(vessels)),
        "relevant_count": attribution.get("relevantCount"),
        "top_candidate": top.get("name"),
        "top_score": top.get("score"),
    }


def build_email(case: dict[str, Any], recipients: Any, report_path: Path, subject: str | None = None, message: str | None = None) -> EmailMessage:
    to = _recipients(recipients)
    summary = _case_summary(case)
    case_id = summary["case_id"] or "UNKNOWN"
    subject = subject or f"SpillTrace incident report — {case_id}"
    body = message.strip() if message and message.strip() else (
        f"SpillTrace incident report\n\n"
        f"Case: {case_id}\n"
        f"Scene: {summary['scene'] or 'Not available'}\n"
        f"Region: {summary['region'] or 'Not available'}\n"
        f"Acquired: {summary['acquired'] or 'Not available'}\n"
        f"Detected area: {summary['area_km2'] if summary['area_km2'] is not None else 'Not available'} km²\n"
        f"Detection confidence: {summary['confidence'] if summary['confidence'] is not None else 'Not available'}\n"
        f"Candidate vessels: {summary['candidate_count']}\n"
        f"Relevant candidates: {summary['relevant_count'] if summary['relevant_count'] is not None else 'Not available'}\n"
        f"Top candidate: {summary['top_candidate'] or 'None'}"
        + (f" ({summary['top_score']})" if summary['top_candidate'] else "")
    )
    msg = EmailMessage()
    msg["From"] = (
        os.environ.get("SPILLTRACE_EMAIL_FROM")
        or os.environ.get("SPILLTRACE_ALERT_FROM")
        or os.environ.get("SPILLTRACE_SMTP_USERNAME")
        or os.environ.get("SPILLTRACE_SMTP_USER")
        or ""
    ).strip()
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False, usegmt=True)
    msg["Message-ID"] = make_msgid()
    msg.set_content(body)
    data = report_path.read_bytes()
    msg.add_attachment(data, maintype="application", subtype="pdf", filename=report_path.name)
    return msg


def send_email(message: EmailMessage) -> str:
    cfg = _smtp_config()
    if cfg["ssl"]:
        client = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=cfg["timeout"])
    else:
        client = smtplib.SMTP(cfg["host"], cfg["port"], timeout=cfg["timeout"])
    with client:
        client.ehlo()
        if cfg["starttls"] and not cfg["ssl"]:
            client.starttls()
            client.ehlo()
        if cfg["username"]:
            client.login(cfg["username"], cfg["password"])
        client.send_message(message)
    return message.get("Message-ID", "")


def dispatch_case_email(
    case: dict[str, Any],
    recipients: Any,
    *,
    case_number: str | None = None,
    subject: str | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    report_path = generate_incident_report(case, case_number=case_number)
    email = build_email(case, recipients, report_path, subject=subject, message=message)

    # Safe local verification mode: creates the .eml beside the PDF and never sends mail.
    if _env_bool("SPILLTRACE_EMAIL_DRY_RUN", False):
        eml_path = report_path.with_suffix(".eml")
        eml_path.write_bytes(email.as_bytes())
        return {
            "sent": False,
            "dryRun": True,
            "recipients": [x.strip() for x in email["To"].split(",")],
            "subject": email["Subject"],
            "reportPath": str(report_path),
            "emailPath": str(eml_path),
            "messageId": email.get("Message-ID"),
        }

    message_id = send_email(email)
    return {
        "sent": True,
        "dryRun": False,
        "recipients": [x.strip() for x in email["To"].split(",")],
        "subject": email["Subject"],
        "reportPath": str(report_path),
        "messageId": message_id,
    }
