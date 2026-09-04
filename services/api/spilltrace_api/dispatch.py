"""Email dispatch for completed SpillTrace incident reports.

Standard library only, like the rest of the API. SMTP credentials are read from
the environment so they never reach source control, and the PDF that a responder
would open in the dashboard is the same file attached here.

Two deliberate safety properties, because `POST /api/cases/<id>/dispatch` has no
authentication in front of it:

  * **Recipients are allowlisted.** Without `SPILLTRACE_ALERT_RECIPIENTS` the
    dispatcher refuses to *send*, so an open port cannot be turned into an open
    relay that mails arbitrary strangers from the configured account.
  * **Dry run is the default whenever SMTP is not configured.** A fresh clone
    writes a .eml beside the PDF and reports `sent: false` instead of raising, so
    the demo path works with no secrets on disk at all.
"""

from __future__ import annotations

import os
import re
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from pathlib import Path
from typing import Any, Iterable

from .reports import build_case_number, generate_incident_report

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Subject and body arrive in a request body and go into a mail header. Cap them
# rather than letting a caller stuff a megabyte into an SMTP conversation.
MAX_SUBJECT = 200
MAX_MESSAGE = 4000


class DispatchError(RuntimeError):
    """An incident email could not be dispatched."""


def _split(value: Any) -> list[str]:
    """Accept either a list or one comma/semicolon separated string."""
    if isinstance(value, str):
        raw: Iterable[Any] = re.split(r"[,;]", value)
    elif isinstance(value, (list, tuple)):
        raw = value
    else:
        raw = []
    return [str(item).strip() for item in raw if str(item).strip()]


def parse_recipients(value: Any) -> list[str]:
    """Validate and de-duplicate recipient addresses, preserving order."""
    out: list[str] = []
    seen: set[str] = set()
    for address in _split(value):
        _name, parsed = parseaddr(address)
        address = parsed or address
        if not EMAIL_RE.match(address):
            raise DispatchError(f"invalid recipient address {address!r}")
        if address.lower() not in seen:
            seen.add(address.lower())
            out.append(address)
    if not out:
        raise DispatchError("at least one recipient email address is required")
    return out


def allowed_recipients() -> list[str]:
    """The configured allowlist: bare addresses and/or `@domain` entries."""
    return _split(os.environ.get("SPILLTRACE_ALERT_RECIPIENTS", ""))


def _check_allowlist(recipients: list[str]) -> None:
    """Refuse to send anywhere the operator has not named in advance.

    An entry of `@example.gov` permits any address in that domain; anything else
    must match in full, case-insensitively. An empty allowlist permits nothing,
    which is why real sending requires configuring it.
    """
    allowed = [entry.lower() for entry in allowed_recipients()]
    if not allowed:
        raise DispatchError(
            "sending is disabled because SPILLTRACE_ALERT_RECIPIENTS is not set. "
            "List the permitted addresses (or @domains) there, or set "
            "SPILLTRACE_EMAIL_DRY_RUN=1 to write the message to disk instead."
        )
    exact = {entry for entry in allowed if not entry.startswith("@")}
    domains = {entry for entry in allowed if entry.startswith("@")}
    for address in recipients:
        low = address.lower()
        if low in exact:
            continue
        if any(low.endswith(domain) for domain in domains):
            continue
        raise DispatchError(
            f"recipient {address!r} is not in SPILLTRACE_ALERT_RECIPIENTS"
        )


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _sender() -> str:
    """The From address, accepting the names the original project .env used."""
    return (
        os.environ.get("SPILLTRACE_EMAIL_FROM")
        or os.environ.get("SPILLTRACE_ALERT_FROM")
        or os.environ.get("SPILLTRACE_SMTP_USERNAME")
        or os.environ.get("SPILLTRACE_SMTP_USER")
        or ""
    ).strip()


def _smtp_host() -> str:
    return os.environ.get("SPILLTRACE_SMTP_HOST", "").strip()


def _smtp_config() -> dict[str, Any]:
    host = _smtp_host()
    if not host:
        raise DispatchError("SPILLTRACE_SMTP_HOST is not configured")
    try:
        port = int(os.environ.get("SPILLTRACE_SMTP_PORT", "587"))
    except ValueError as exc:
        raise DispatchError("SPILLTRACE_SMTP_PORT must be an integer") from exc
    try:
        timeout = float(os.environ.get("SPILLTRACE_SMTP_TIMEOUT", "20"))
    except ValueError as exc:
        raise DispatchError("SPILLTRACE_SMTP_TIMEOUT must be a number") from exc
    username = (
        os.environ.get("SPILLTRACE_SMTP_USERNAME")
        or os.environ.get("SPILLTRACE_SMTP_USER")
        or ""
    ).strip()
    sender = _sender()
    if not sender:
        raise DispatchError("SPILLTRACE_EMAIL_FROM or SPILLTRACE_SMTP_USERNAME is required")
    return {
        "host": host,
        "port": port,
        "username": username,
        "password": os.environ.get("SPILLTRACE_SMTP_PASSWORD", ""),
        "sender": sender,
        "starttls": _env_bool("SPILLTRACE_SMTP_STARTTLS", True),
        "ssl": _env_bool("SPILLTRACE_SMTP_SSL", False),
        "timeout": timeout,
    }


def dispatch_mode() -> dict[str, Any]:
    """What a dispatch request would actually do, without doing it.

    The dashboard shows this so the button can say "write a .eml" when that is
    what will happen, instead of promising an email that no SMTP host can send.
    """
    dry_run = _env_bool("SPILLTRACE_EMAIL_DRY_RUN", not _smtp_host())
    return {
        "mode": "dryRun" if dry_run else "send",
        "smtpConfigured": bool(_smtp_host()),
        "recipientsConfigured": bool(allowed_recipients()),
        "note": (
            "No SMTP host is configured, so a dispatch writes the message to disk "
            "as .eml beside the PDF and reports sent: false."
            if dry_run
            else "SMTP is configured; dispatch sends to allowlisted recipients only."
        ),
    }


def _case_summary(case: dict[str, Any]) -> dict[str, Any]:
    scene = case.get("scene") or {}
    slick = case.get("slick") or {}
    attribution = case.get("attribution") or {}
    provenance = case.get("provenance") or {}
    vessels = case.get("vessels") or []
    top = vessels[0] if vessels else {}
    total = slick.get("totalAreaKm2")
    return {
        "case_id": case.get("id") or case.get("caseId"),
        "scene": scene.get("name"),
        "region": scene.get("region"),
        "acquired": scene.get("acquiredStartUtc"),
        # `or` would discard a genuine 0.0, which is a real detection outcome.
        "area_km2": total if total is not None else slick.get("areaKm2"),
        "confidence": slick.get("confidence"),
        "candidate_count": attribution.get("candidateCount", len(vessels)),
        "relevant_count": attribution.get("relevantCount"),
        "top_candidate": top.get("name"),
        "top_score": top.get("score"),
        "candidate_label": attribution.get("candidateLabel") or "Priority candidate for investigation",
        "ais_label": provenance.get("aisLabel"),
        "drift_label": provenance.get("driftLabel"),
        "spill_age": (case.get("spillAge") or {}).get("label"),
    }


def _shorten(text: Any, limit: int) -> str:
    """Collapse newlines out of a header value and cap its length."""
    flat = re.sub(r"\s+", " ", str(text)).strip()
    return flat[:limit]


def _default_body(summary: dict[str, Any], case_number: str) -> str:
    """The plain-text body.

    It carries the same caveats as the PDF, because the person who forwards an
    email rarely opens the attachment first.
    """

    def show(value: Any) -> str:
        return "Not available" if value is None or value == "" else str(value)

    lines = [
        "SpillTrace incident report",
        "Research proof of concept — not evidence — human review required.",
        "",
        f"Case number:        {case_number}",
        f"Case:               {show(summary['case_id'])}",
        f"Scene / region:     {show(summary['scene'])} / {show(summary['region'])}",
        f"Acquired (UTC):     {show(summary['acquired'])}",
        f"Detected area:      {show(summary['area_km2'])} km²",
        f"Confidence:         {show(summary['confidence'])}",
        f"Spill age:          {show(summary['spill_age'])}",
        "",
        f"Vessels considered: {show(summary['candidate_count'])}",
        f"Passed both tests:  {show(summary['relevant_count'])}",
    ]
    if summary["top_candidate"]:
        lines.append(
            f"Highest ranked:     {summary['top_candidate']} "
            f"(score {show(summary['top_score'])}) — {summary['candidate_label']}"
        )
    else:
        lines.append("Highest ranked:     none — no vessel passed the relevance test")
    lines += [
        "",
        show(summary["ais_label"]),
        show(summary["drift_label"]),
        "",
        "The attached PDF states the full provenance and limitations. The ranking "
        "orders candidates for human attention and does not establish "
        "responsibility for the observed oil.",
    ]
    return "\n".join(lines)


def build_email(
    case: dict[str, Any],
    recipients: Any,
    report_path: Path,
    subject: str | None = None,
    message: str | None = None,
    case_number: str | None = None,
) -> EmailMessage:
    to = parse_recipients(recipients)
    summary = _case_summary(case)
    number = case_number or build_case_number(case)

    msg = EmailMessage()
    # A dry run has no configured sender and does not need one; label it plainly
    # rather than emitting a message with an empty From header.
    msg["From"] = _sender() or "spilltrace-dry-run@localhost"
    msg["To"] = ", ".join(to)
    msg["Subject"] = _shorten(
        subject or f"SpillTrace incident report {number} — {summary['region'] or 'unknown region'}",
        MAX_SUBJECT,
    )
    msg["Date"] = formatdate(localtime=False, usegmt=True)
    msg["Message-ID"] = make_msgid()
    msg["X-SpillTrace-Case"] = number
    body = str(message).strip()[:MAX_MESSAGE] if message and str(message).strip() else _default_body(summary, number)
    msg.set_content(body)
    msg.add_attachment(
        report_path.read_bytes(),
        maintype="application",
        subtype="pdf",
        filename=report_path.name,
    )
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
    """Generate the PDF, build the message, and either send it or write a .eml.

    Dry run is the default whenever no SMTP host is configured, so a clone with
    no `.env` produces a verifiable artefact instead of an exception.
    """
    to = parse_recipients(recipients if recipients else allowed_recipients())
    dry_run = _env_bool("SPILLTRACE_EMAIL_DRY_RUN", not _smtp_host())
    if not dry_run:
        _check_allowlist(to)
        _smtp_config()  # fail before spending time on a PDF nobody can receive

    number = build_case_number(case, case_number)
    report_path = generate_incident_report(case, case_number=number)
    email = build_email(case, to, report_path, subject=subject, message=message, case_number=number)

    result = {
        "caseNumber": number,
        "recipients": to,
        "subject": email["Subject"],
        "reportPath": str(report_path),
        "messageId": email.get("Message-ID"),
    }

    if dry_run:
        eml_path = report_path.with_suffix(".eml")
        eml_path.write_bytes(email.as_bytes())
        return {
            **result,
            "sent": False,
            "dryRun": True,
            "emailPath": str(eml_path),
            "reason": "no SMTP host configured" if not _smtp_host() else "SPILLTRACE_EMAIL_DRY_RUN is set",
        }

    return {**result, "sent": True, "dryRun": False, "messageId": send_email(email)}
