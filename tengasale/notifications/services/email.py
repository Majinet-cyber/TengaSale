"""
TengaSale Email Notification Service.

Sends transactional emails via SendGrid (when SENDGRID_API_KEY is set)
or falls back to Django's console backend in development.

Never raises exceptions into the calling workflow — all failures are logged.

Usage:
    from notifications.services.email import send_payment_receipt_email
    send_payment_receipt_email(tx, contract)
"""
from __future__ import annotations

import logging
from typing import Optional

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.utils import timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Low-level send helper
# ---------------------------------------------------------------------------

def _send(
    to_email: str,
    subject: str,
    html_template: str,
    context: dict,
    from_email: Optional[str] = None,
) -> bool:
    """
    Render and send an HTML email. Returns True on success.
    Logs errors without raising — never crashes the calling workflow.
    """
    if not to_email or "@" not in to_email:
        logger.debug("Email skipped — no valid recipient: %r", to_email)
        return False

    _from = from_email or getattr(settings, "SENDGRID_FROM_EMAIL", None) or getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@tengasale.com")

    # Add global context
    ctx = {
        "site_name": "TengaSale",
        "support_whatsapp": getattr(settings, "TENGASALE_WHATSAPP_NUMBER", "+265883596135"),
        "base_url": getattr(settings, "TENGASALE_PUBLIC_BASE_URL", ""),
        **context,
    }

    try:
        html_body = render_to_string(html_template, ctx)
        text_body = strip_tags(html_body)

        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=_from,
            to=[to_email],
        )
        msg.attach_alternative(html_body, "text/html")
        msg.send()
        logger.info("Email sent: %r to %s", subject, to_email)
        return True
    except Exception as exc:
        logger.warning("Email failed to %s [%s]: %s", to_email, subject, exc)
        return False


def _send_plain(to_email: str, subject: str, body: str, from_email: Optional[str] = None) -> bool:
    """Send a plain-text email."""
    if not to_email or "@" not in to_email:
        return False
    _from = from_email or getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@tengasale.com")
    try:
        msg = EmailMultiAlternatives(subject=subject, body=body, from_email=_from, to=[to_email])
        msg.send()
        logger.info("Plain email sent: %r to %s", subject, to_email)
        return True
    except Exception as exc:
        logger.warning("Plain email failed to %s [%s]: %s", to_email, subject, exc)
        return False


# ---------------------------------------------------------------------------
# Payment notifications
# ---------------------------------------------------------------------------

def send_payment_receipt_email(tx, contract) -> bool:
    """
    Send a payment receipt email to the customer after a successful payment.
    tx: PaymentTransaction instance
    contract: PaymentContract instance
    """
    # Try to get customer email from source application
    customer_email = _get_customer_email(contract)
    if not customer_email:
        logger.debug("No customer email for contract %s — skipping receipt email", contract.contract_number)
        return False

    blantyre = timezone.localtime(tx.paid_at) if tx.paid_at else timezone.now()

    subject = f"TengaSale Payment Receipt — {contract.payg_number or contract.contract_number}"
    ctx = {
        "tx": tx,
        "contract": contract,
        "amount_formatted": f"{tx.amount:,.0f}",
        "paid_at_local": blantyre,
        "payment_type_label": tx.get_payment_type_display() if hasattr(tx, "get_payment_type_display") else tx.payment_type.title(),
        "balance_after_formatted": f"{(tx.balance_after or contract.remaining_amount):,.0f}",
        "network_label": _network_label(tx.provider, tx.network if hasattr(tx, "network") else ""),
    }

    return _send(
        to_email=customer_email,
        subject=subject,
        html_template="notifications/emails/payment_receipt.html",
        context=ctx,
    )


def send_deposit_received_email(tx, contract) -> bool:
    """Email when deposit payment is received."""
    customer_email = _get_customer_email(contract)
    if not customer_email:
        return False

    subject = f"Deposit Received — Contract {contract.contract_number}"
    ctx = {
        "tx": tx,
        "contract": contract,
        "amount_formatted": f"{tx.amount:,.0f}",
        "deposit_remaining": contract.deposit_remaining,
        "deposit_complete": contract.deposit_complete,
    }

    return _send(
        to_email=customer_email,
        subject=subject,
        html_template="notifications/emails/deposit_received.html",
        context=ctx,
    )


def send_contract_active_email(contract) -> bool:
    """Email when deposit is fully paid and contract becomes active."""
    customer_email = _get_customer_email(contract)
    if not customer_email:
        return False

    subject = f"Your TengaSale Contract is Active — {contract.payg_number}"
    ctx = {
        "contract": contract,
        "pay_url": f"{getattr(settings, 'TENGASALE_PUBLIC_BASE_URL', '').rstrip('/')}/pay/payg/{contract.payg_number}/",
    }

    return _send(
        to_email=customer_email,
        subject=subject,
        html_template="notifications/emails/contract_active.html",
        context=ctx,
    )


def send_contract_created_email(contract) -> bool:
    """Email when a new contract is created."""
    customer_email = _get_customer_email(contract)
    if not customer_email:
        return False

    subject = f"TengaSale Contract Created — {contract.contract_number}"
    ctx = {"contract": contract}

    return _send(
        to_email=customer_email,
        subject=subject,
        html_template="notifications/emails/contract_created.html",
        context=ctx,
    )


def send_repayment_receipt_email(tx, contract) -> bool:
    """Email after a repayment is confirmed."""
    return send_payment_receipt_email(tx, contract)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_customer_email(contract) -> str:
    """Try to find a valid customer email from the contract or source application."""
    # Check source application
    try:
        app = contract.source_application
        if app:
            email = getattr(app, "customer_email", "") or getattr(app, "email", "") or ""
            if email and "@" in email:
                return email.strip()
    except Exception:
        pass

    return ""


def _network_label(provider: str, network: str) -> str:
    if provider == "airtel_money" or network == "airtel":
        return "Airtel Money"
    if provider == "tnm_mpamba" or network == "tnm":
        return "TNM Mpamba"
    if provider == "paychangu":
        return "PayChangu"
    if provider == "mock":
        return "Test Payment"
    return provider.replace("_", " ").title()
