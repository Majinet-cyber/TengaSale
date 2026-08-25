"""
integrations/sendgrid_email.py

SendGrid email integration for TengaSale.
Supports payment receipts, approval emails, and admin alerts.

Configuration (settings.py / .env):
    SENDGRID_API_KEY
    DEFAULT_FROM_EMAIL      (e.g. noreply@tengasale.com)

If credentials are missing, falls back to Django's built-in email backend
(or mock mode if EMAIL_BACKEND is also not configured).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from django.conf import settings

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────────


def is_configured() -> bool:
    return bool(getattr(settings, "SENDGRID_API_KEY", ""))


def _from_email() -> str:
    return getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@tengasale.com")


# ──────────────────────────────────────────────────────────────────────────────
# Core send function
# ──────────────────────────────────────────────────────────────────────────────


def send_email(
    to: str,
    subject: str,
    body_html: str,
    body_text: Optional[str] = None,
    from_email: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Send a transactional email.

    Falls back to Django's email backend if SendGrid is not configured.

    Returns:
        {'success': bool, 'message': str}
    """
    sender = from_email or _from_email()

    if is_configured():
        return _send_via_sendgrid(to=to, subject=subject, body_html=body_html, body_text=body_text, from_email=sender)

    # Fallback: Django email backend
    return _send_via_django(to=to, subject=subject, body_html=body_html, body_text=body_text, from_email=sender)


def _send_via_sendgrid(*, to, subject, body_html, body_text, from_email) -> Dict[str, Any]:
    try:
        from sendgrid import SendGridAPIClient  # type: ignore
        from sendgrid.helpers.mail import Content, Mail, To  # type: ignore
    except ImportError:
        logger.error("sendgrid package not installed — run: pip install sendgrid")
        return {"success": False, "message": "sendgrid package not installed."}

    try:
        sg = SendGridAPIClient(settings.SENDGRID_API_KEY)
        mail = Mail(
            from_email=from_email,
            to_emails=to,
            subject=subject,
            html_content=body_html,
        )
        if body_text:
            mail.add_content(Content("text/plain", body_text))
        response = sg.send(mail)
        success = response.status_code in (200, 202)
        logger.info("SendGrid email: to=%s subject=%r status=%s", to, subject, response.status_code)
        return {"success": success, "message": f"HTTP {response.status_code}"}
    except Exception as exc:
        logger.error("SendGrid send_email failed: to=%s error=%s", to, exc)
        return {"success": False, "message": str(exc)}


def _send_via_django(*, to, subject, body_html, body_text, from_email) -> Dict[str, Any]:
    try:
        from django.core.mail import EmailMultiAlternatives
        msg = EmailMultiAlternatives(
            subject=subject,
            body=body_text or "",
            from_email=from_email,
            to=[to],
        )
        if body_html:
            msg.attach_alternative(body_html, "text/html")
        msg.send(fail_silently=False)
        logger.info("Django email sent: to=%s subject=%r", to, subject)
        return {"success": True, "message": "Sent via Django email backend."}
    except Exception as exc:
        logger.warning("Django email failed (mock mode): to=%s error=%s", to, exc)
        return {"success": False, "message": str(exc)}


# ──────────────────────────────────────────────────────────────────────────────
# Named email helpers
# ──────────────────────────────────────────────────────────────────────────────


def send_payment_receipt(
    to: str,
    customer_name: str,
    amount: str,
    contract_number: str,
    payment_date: str,
    reference: str = "",
) -> Dict[str, Any]:
    subject = f"TengaSale Payment Receipt — {contract_number}"
    html = f"""
    <p>Dear {customer_name},</p>
    <p>Thank you for your payment to TengaSale.</p>
    <table style="border-collapse:collapse;font-family:sans-serif;">
      <tr><td><strong>Contract:</strong></td><td>{contract_number}</td></tr>
      <tr><td><strong>Amount:</strong></td><td>MWK {amount}</td></tr>
      <tr><td><strong>Date:</strong></td><td>{payment_date}</td></tr>
      {"<tr><td><strong>Reference:</strong></td><td>" + reference + "</td></tr>" if reference else ""}
    </table>
    <p>Keep your device safe and continue making payments on time.</p>
    <p>— The TengaSale Team<br><em>{settings.TENGA_MOTTO}</em></p>
    """
    return send_email(to=to, subject=subject, body_html=html)


def send_approval_email(
    to: str,
    customer_name: str,
    contract_number: str,
    device: str = "",
) -> Dict[str, Any]:
    subject = "TengaSale — Your Application Has Been Approved!"
    html = f"""
    <p>Dear {customer_name},</p>
    <p>Congratulations! Your TengaSale phone-financing application has been <strong>approved</strong>.</p>
    <table style="border-collapse:collapse;font-family:sans-serif;">
      <tr><td><strong>Contract Number:</strong></td><td>{contract_number}</td></tr>
      {"<tr><td><strong>Device:</strong></td><td>" + device + "</td></tr>" if device else ""}
    </table>
    <p>Please visit your TengaSale agent to sign your contract and collect your device.</p>
    <p>Welcome to TengaSale — <em>{settings.TENGA_MOTTO}</em></p>
    """
    return send_email(to=to, subject=subject, body_html=html)


def send_admin_alert(
    subject: str,
    body: str,
    to: Optional[str] = None,
) -> Dict[str, Any]:
    recipient = to or getattr(settings, "ADMIN_ALERT_EMAIL", _from_email())
    html = f"<pre style='font-family:monospace'>{body}</pre>"
    return send_email(to=recipient, subject=f"[TengaSale Alert] {subject}", body_html=html, body_text=body)


def send_marked_field_correction_email(
    to: str,
    customer_name: str,
    edit_url: str,
    expires_hours: int = 72,
    marked_fields: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Email customer a secure link to correct their marked application fields."""
    subject = "TengaSale — Action Required: Please Update Your Application"
    fields_html = ""
    if marked_fields:
        items = "".join(f"<li>{f}</li>" for f in marked_fields)
        fields_html = f"<p><strong>Fields requiring update:</strong></p><ul>{items}</ul>"
    html = f"""
    <p>Dear {customer_name},</p>
    <p>Your TengaSale application requires a small update. Our team has flagged the following fields:</p>
    {fields_html}
    <p>Please click the secure link below to make the corrections (valid for {expires_hours} hours):</p>
    <p><a href="{edit_url}" style="background:#e67e22;color:#fff;padding:10px 20px;text-decoration:none;border-radius:6px;">
      Update My Application
    </a></p>
    <p>If the button does not work, copy and paste this link:<br><code>{edit_url}</code></p>
    <p>Thank you for your cooperation — TengaSale: <em>{settings.TENGA_MOTTO}</em></p>
    """
    return send_email(to=to, subject=subject, body_html=html)


def send_merchant_payout_notification_email(
    to: str,
    merchant_name: str,
    cash_price: str,
    merchant_commission: str,
    total_payable: str,
    contract_number: str,
    payout_method: str = "",
) -> Dict[str, Any]:
    """Notify merchant that a payout has been created for a contract."""
    subject = f"TengaSale — Merchant Payout Ready: {contract_number}"
    method_row = f"<tr><td><strong>Payout Method:</strong></td><td>{payout_method}</td></tr>" if payout_method else ""
    html = f"""
    <p>Dear {merchant_name},</p>
    <p>A payout has been prepared for the following TengaSale contract:</p>
    <table style="border-collapse:collapse;font-family:sans-serif;margin-top:12px;">
      <tr><td><strong>Contract:</strong></td><td>{contract_number}</td></tr>
      <tr><td><strong>Cash Price:</strong></td><td>MWK {cash_price}</td></tr>
      <tr><td><strong>Merchant Commission (1%):</strong></td><td>MWK {merchant_commission}</td></tr>
      <tr style="border-top:1px solid #ccc;"><td><strong>Total Payable:</strong></td><td><strong>MWK {total_payable}</strong></td></tr>
      {method_row}
    </table>
    <p>No withholding tax (WHT) is applied to merchant payouts.</p>
    <p>— The TengaSale Team<br><em>{settings.TENGA_MOTTO}</em></p>
    """
    return send_email(to=to, subject=subject, body_html=html)


def send_payout_notification_email(
    to: str,
    agent_name: str,
    gross_amount: str,
    wht_amount: str,
    net_amount: str,
    period: str,
    destination_phone: str = "",
) -> Dict[str, Any]:
    subject = f"TengaSale Commission Payout — {period}"
    html = f"""
    <p>Dear {agent_name},</p>
    <p>Your TengaSale commission payout for <strong>{period}</strong> has been processed.</p>
    <table style="border-collapse:collapse;font-family:sans-serif;margin-top:12px;">
      <tr><td><strong>Gross Earnings:</strong></td><td>MWK {gross_amount}</td></tr>
      <tr><td><strong>WHT (20%):</strong></td><td style="color:#d93025">− MWK {wht_amount}</td></tr>
      <tr style="border-top:1px solid #ccc;"><td><strong>Net Payable:</strong></td><td><strong>MWK {net_amount}</strong></td></tr>
      {"<tr><td><strong>Destination:</strong></td><td>" + destination_phone + "</td></tr>" if destination_phone else ""}
    </table>
    <p>Thank you for your hard work — TengaSale: <em>{settings.TENGA_MOTTO}</em></p>
    """
    return send_email(to=to, subject=subject, body_html=html)
