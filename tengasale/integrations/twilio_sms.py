"""
integrations/twilio_sms.py

Twilio SMS integration for TengaSale.
Supports payment reminders, OTP, approval notifications, and contract reminders.

Configuration (settings.py / .env):
    TWILIO_ACCOUNT_SID
    TWILIO_AUTH_TOKEN
    TWILIO_PHONE_NUMBER     (e.g. +12025551234)

If credentials are missing, falls back to mock mode (logs only, no crash).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from django.conf import settings

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────────


def is_configured() -> bool:
    return bool(
        getattr(settings, "TWILIO_ACCOUNT_SID", "")
        and getattr(settings, "TWILIO_AUTH_TOKEN", "")
        and getattr(settings, "TWILIO_PHONE_NUMBER", "")
    )


def _get_client():
    try:
        from twilio.rest import Client  # type: ignore
        return Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    except ImportError:
        logger.error("twilio package not installed — run: pip install twilio")
        return None
    except Exception as exc:
        logger.error("Failed to create Twilio client: %s", exc)
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Core send function
# ──────────────────────────────────────────────────────────────────────────────


def send_sms(to: str, body: str) -> Dict[str, Any]:
    """
    Send an SMS via Twilio.

    Args:
        to:   Recipient phone number (E.164 format, e.g. +265991234567)
        body: Message text

    Returns:
        {'success': bool, 'sid': str, 'message': str}
    """
    if not is_configured():
        logger.warning("Twilio SMS mock: to=%s | %s", to, body[:80])
        return {"success": False, "sid": "", "message": "Twilio not configured — SMS not sent (mock mode)."}

    client = _get_client()
    if not client:
        return {"success": False, "sid": "", "message": "Could not create Twilio client."}

    try:
        msg = client.messages.create(
            body=body,
            from_=settings.TWILIO_PHONE_NUMBER,
            to=to,
        )
        logger.info("Twilio SMS sent: to=%s sid=%s", to, msg.sid)
        return {"success": True, "sid": msg.sid, "message": "SMS sent."}
    except Exception as exc:
        logger.error("Twilio send_sms failed: to=%s error=%s", to, exc)
        return {"success": False, "sid": "", "message": str(exc)}


# ──────────────────────────────────────────────────────────────────────────────
# Named notification helpers
# ──────────────────────────────────────────────────────────────────────────────


def send_payment_reminder(to: str, customer_name: str, amount: str, due_date: str, contract_number: str) -> Dict[str, Any]:
    body = (
        f"Hi {customer_name}, this is a reminder from TengaSale. "
        f"Your payment of MWK {amount} for contract {contract_number} is due on {due_date}. "
        f"Please pay via Airtel Money or TNM Mpamba. "
        f"Email support@tenga.africa for help."
    )
    return send_sms(to, body)


def send_approval_notification(to: str, customer_name: str, contract_number: str) -> Dict[str, Any]:
    body = (
        f"Congratulations {customer_name}! "
        f"Your TengaSale phone-financing application {contract_number} has been approved. "
        f"Please visit your TengaSale agent to complete your contract. "
        f"Welcome aboard — {settings.TENGA_MOTTO}"
    )
    return send_sms(to, body)


def send_failed_payment_notification(to: str, customer_name: str, amount: str, contract_number: str) -> Dict[str, Any]:
    body = (
        f"Hi {customer_name}, your TengaSale payment of MWK {amount} "
        f"for contract {contract_number} could not be processed. "
        f"Please retry or email support@tenga.africa for assistance."
    )
    return send_sms(to, body)


def send_otp(to: str, otp_code: str) -> Dict[str, Any]:
    body = f"Your TengaSale verification code is {otp_code}. Valid for 10 minutes. Do not share this code."
    return send_sms(to, body)


def send_contract_due_reminder(to: str, customer_name: str, days_until_due: int, amount: str, contract_number: str) -> Dict[str, Any]:
    body = (
        f"Hi {customer_name}, your TengaSale payment of MWK {amount} "
        f"for contract {contract_number} is due in {days_until_due} day(s). "
        f"Pay now to keep your device active. Email support@tenga.africa for help."
    )
    return send_sms(to, body)


def send_payout_notification(to: str, agent_name: str, net_amount: str, period: str) -> Dict[str, Any]:
    body = (
        f"Hi {agent_name}, your TengaSale commission payout of MWK {net_amount} "
        f"for {period} has been processed. "
        f"Check your TengaSale wallet for details."
    )
    return send_sms(to, body)


def send_overdue_notification(to: str, customer_name: str, contract_number: str, overdue_days: int) -> Dict[str, Any]:
    """Notify customer their contract is overdue and device may be locked."""
    body = (
        f"Hi {customer_name}, your TengaSale contract {contract_number} is {overdue_days} day(s) overdue. "
        f"Please make a payment now to restore full device access. "
        f"Pay via Airtel Money or TNM Mpamba, or email support@tenga.africa."
    )
    return send_sms(to, body)


def send_payment_receipt_sms(to: str, customer_name: str, amount: str, contract_number: str, reference: str = "") -> Dict[str, Any]:
    """Send a brief payment receipt confirmation via SMS."""
    ref_part = f" Ref: {reference}." if reference else ""
    body = (
        f"TengaSale: Payment of MWK {amount} received for contract {contract_number}.{ref_part} "
        f"Thank you, {customer_name}. Keep paying on time."
    )
    return send_sms(to, body)


def send_customer_edit_link(to: str, customer_name: str, edit_url: str, expires_hours: int = 72) -> Dict[str, Any]:
    """Send a secure field-correction link to the customer."""
    body = (
        f"Hi {customer_name}, TengaSale needs you to update some details on your application. "
        f"Click this secure link (expires in {expires_hours} hours): {edit_url}"
    )
    return send_sms(to, body)
