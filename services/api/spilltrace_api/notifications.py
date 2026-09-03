# notifications.py

import os
from typing import Optional

from dotenv import load_dotenv
from twilio.rest import Client
import resend

load_dotenv()


def build_alert_message(
    position: str,
    area: str,
    drift_forecast: str,
    top_candidate: str,
) -> str:
    return f"""
DISASTER RESPONSE ALERT

Position: {position}
Affected Area: {area}

Drift Forecast:
{drift_forecast}

Top Candidate:
{top_candidate}

Please dispatch the appropriate responder.
""".strip()


def send_email_alert(
    recipient: str,
    position: str,
    area: str,
    drift_forecast: str,
    top_candidate: str,
):
    message = build_alert_message(
        position,
        area,
        drift_forecast,
        top_candidate,
    )

    resend.api_key = os.getenv("RESEND_API_KEY")

    return resend.Emails.send({
        "from": os.getenv("ALERT_FROM_EMAIL"),
        "to": [recipient],
        "subject": "🚨 Disaster Response Alert",
        "text": message,
    })


def send_sms_alert(
    phone_number: str,
    position: str,
    area: str,
    drift_forecast: str,
    top_candidate: str,
):
    message = build_alert_message(
        position,
        area,
        drift_forecast,
        top_candidate,
    )

    client = Client(
        os.getenv("TWILIO_ACCOUNT_SID"),
        os.getenv("TWILIO_AUTH_TOKEN"),
    )

    return client.messages.create(
        body=message,
        from_=os.getenv("TWILIO_PHONE_NUMBER"),
        to=phone_number,
    )