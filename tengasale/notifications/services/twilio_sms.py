"""
TengaSale Twilio SMS / WhatsApp Notification Service.

Sends transactional SMS and WhatsApp messages via Twilio.
Never raises exceptions into the calling workflow — all failures are logged.

Environment variables:
  TWILIO_ACCOUNT_SID
  TWILIO_AUTH_TOKEN
  TWILIO_PHONE_NUMBER       — SMS sender (e.g. +1234567890)
  TWILIO_WHATSAPP_NUMBER    — WhatsApp sender (e.g. whatsapp:+14155238886)

Usage:
    from notifications.services.twilio_sms import send_payment_receipt_sms
    send_payment_receipt_sms(tx, contract)
"""
from __future__ import annotations

import logging
from typing import Optional

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Low-level send helpers
# ---------------------------------------------------------------------------

def _is_twilio_configured() -> bool:
    return bool(
        getattr(settings, "TWILIO_ACCOUNT_SID", "")
        and getattr(settings, "TWILIO_AUTH_TOKEN", "")
    )


def _load_twilio_client():
    """Safely load the Twilio client. Returns None if not installed/configured."""
    if not _is_twilio_configured():
        return None
    try:
        from twilio.rest import Client
        return Client(
            settings.TWILIO_ACCOUNT_SID,
            settings.TWILIO_AUTH_TOKEN,
        )
    except ImportError:
        logger.warning("Twilio library not installed. Run: pip install twilio")
        return None
    except Exception as exc:
        logger.warning("Could not create Twilio client: %s", exc)
        return None


def _normalise_phone_malawi(phone: str) -> str:
    """
    Convert any Malawi phone to E.164 format (+265XXXXXXXXX).
    Accepts: 0991234567, 991234567, +265991234567, 265991234567.
    """
    digits = "".join(c for c in phone if c.isdigit())
    if digits.startswith("265"):
        digits = digits[3:]
    if digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 9:
        return f"+265{digits}"
    # Return as-is if we can't normalise — Twilio will reject invalid numbers
    return f"+{digits}" if not phone.startswith("+") else phone


def send_sms(to_phone: str, body: str) -> bool:
    """
    Send an SMS message via Twilio.
    Returns True on success, False on failure (never raises).
    """
    if not to_phone:
        return False

    client = _load_twilio_client()
    if not client:
        logger.debug("[SMS/skip] Twilio not configured — would send to %s: %s", to_phone[:6], body[:60])
        return False

    from_number = getattr(settings, "TWILIO_PHONE_NUMBER", "")
    if not from_number:
        logger.warning("TWILIO_PHONE_NUMBER not set — cannot send SMS")
        return False

    try:
        e164 = _normalise_phone_malawi(to_phone)
        message = client.messages.create(
            to=e164,
            from_=from_number,
            body=body,
        )
        logger.info("SMS sent to %s, SID=%s", e164[:8], message.sid)
        return True
    except Exception as exc:
        logger.warning("SMS failed to %s: %s", to_phone[:6], exc)
        return False


def send_whatsapp(to_phone: str, body: str) -> bool:
    """
    Send a WhatsApp message via Twilio.
    to_phone should be a Malawi phone like 0991234567 or +265991234567.
    Returns True on success, False on failure (never raises).
    """
    if not to_phone:
        return False

    client = _load_twilio_client()
    if not client:
        logger.debug("[WA/skip] Twilio not configured — would WhatsApp %s: %s", to_phone[:6], body[:60])
        return False

    from_wa = getattr(settings, "TWILIO_WHATSAPP_NUMBER", "")
    if not from_wa:
        logger.debug("TWILIO_WHATSAPP_NUMBER not set — skipping WhatsApp")
        return False

    try:
        e164 = _normalise_phone_malawi(to_phone)
        to_wa = f"whatsapp:{e164}"
        if not from_wa.startswith("whatsapp:"):
            from_wa = f"whatsapp:{from_wa}"

        message = client.messages.create(
            to=to_wa,
            from_=from_wa,
            body=body,
        )
        logger.info("WhatsApp sent to %s, SID=%s", e164[:8], message.sid)
        return True
    except Exception as exc:
        logger.warning("WhatsApp failed to %s: %s", to_phone[:6], exc)
        return False


# ---------------------------------------------------------------------------
# Payment notifications
# ---------------------------------------------------------------------------

def send_payment_receipt_sms(tx, contract) -> bool:
    """
    Send payment receipt SMS/WhatsApp after a confirmed payment.
    tx: PaymentTransaction
    contract: PaymentContract
    """
    phone = contract.customer_phone
    if not phone:
        return False

    pay_ref = tx.internal_reference or ""
    cnum = contract.contract_number or ""
    payg = contract.payg_number or cnum
    amount = int(tx.amount)

    body = (
        f"TengaSale Payment Received\n"
        f"Amount: MWK {amount:,}\n"
        f"Contract: {cnum}\n"
        f"PayG: {payg}\n"
        f"Ref: {pay_ref}\n"
        f"Thank you."
    )

    # Try WhatsApp first, fall back to SMS
    sent = send_whatsapp(phone, body)
    if not sent:
        sent = send_sms(phone, body)
    return sent


def send_deposit_received_sms(tx, contract) -> bool:
    """SMS when deposit payment is received."""
    phone = contract.customer_phone
    if not phone:
        return False

    amount = int(tx.amount)
    remaining = int(contract.deposit_remaining)
    cnum = contract.contract_number or ""

    if contract.deposit_complete:
        body = (
            f"TengaSale: Deposit fully received. Contract {cnum} is now active. "
            f"Amount: MWK {amount:,}. Thank you!"
        )
    else:
        body = (
            f"TengaSale: Deposit payment received. MWK {amount:,} for Contract {cnum}. "
            f"Still owed: MWK {remaining:,}."
        )

    sent = send_whatsapp(phone, body)
    if not sent:
        sent = send_sms(phone, body)
    return sent


def send_contract_active_sms(contract) -> bool:
    """SMS when contract becomes active after full deposit."""
    phone = contract.customer_phone
    if not phone:
        return False

    payg = contract.payg_number or contract.contract_number
    base_url = getattr(settings, "TENGASALE_PUBLIC_BASE_URL", "").rstrip("/")
    pay_link = f"{base_url}/pay/payg/{payg}/" if base_url else f"/pay/payg/{payg}/"

    body = (
        f"TengaSale: Deposit received. Your contract is active! PayG: {payg}. "
        f"Keep payments current to avoid device locking. "
        f"Pay here: {pay_link}"
    )

    sent = send_whatsapp(phone, body)
    if not sent:
        sent = send_sms(phone, body)
    return sent


def send_payment_link_sms(contract, amount: Optional[int] = None) -> bool:
    """Send a payment link to the customer."""
    phone = contract.customer_phone
    if not phone:
        return False

    payg = contract.payg_number or contract.contract_number
    base_url = getattr(settings, "TENGASALE_PUBLIC_BASE_URL", "").rstrip("/")
    pay_link = f"{base_url}/pay/payg/{payg}/" if base_url else f"/pay/payg/{payg}/"
    cnum = contract.contract_number

    if amount:
        body = (
            f"TengaSale: Your payment of MWK {amount:,} is due. "
            f"Contract: {cnum}, PayG: {payg}. "
            f"Pay here: {pay_link}"
        )
    else:
        deposit_remaining = int(contract.deposit_remaining)
        body = (
            f"TengaSale: Deposit due for Contract {cnum}. "
            f"Amount: MWK {deposit_remaining:,}. PayG: {payg}. "
            f"Pay here: {pay_link}"
        )

    sent = send_whatsapp(phone, body)
    if not sent:
        sent = send_sms(phone, body)
    return sent


def send_final_settlement_sms(contract) -> bool:
    """SMS when contract is fully settled."""
    phone = contract.customer_phone
    if not phone:
        return False

    cnum = contract.contract_number
    body = (
        f"TengaSale: Your contract {cnum} is fully settled. "
        f"Congratulations! Final documents are ready. "
        f"Thank you for choosing TengaSale."
    )

    sent = send_whatsapp(phone, body)
    if not sent:
        sent = send_sms(phone, body)
    return sent
