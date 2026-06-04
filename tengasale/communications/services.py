import logging
import re
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import PhoneOTP, SMSLog
from .templates import format_mwk, format_sms_date, render_sms_template

try:
    from twilio.rest import Client
except ImportError:
    Client = None

logger = logging.getLogger(__name__)


def normalize_malawi_phone(phone):
    cleaned = re.sub(r"[\s\-\(\)]", "", str(phone or ""))
    if cleaned.startswith("+265"):
        return cleaned
    if cleaned.startswith("265"):
        return f"+{cleaned}"
    if cleaned.startswith("0"):
        return f"+265{cleaned[1:]}"
    if cleaned.isdigit() and len(cleaned) == 9:
        return f"+265{cleaned}"
    return cleaned


def default_sms_language(language=None):
    return language or getattr(settings, "DEFAULT_SMS_LANGUAGE", "ny") or "ny"


def send_sms(
    phone_number,
    message,
    purpose="general",
    application=None,
    contract=None,
    payment_contract=None,
    payment_transaction=None,
    language=None,
):
    phone = normalize_malawi_phone(phone_number)
    sms_log = SMSLog.objects.create(
        phone_number=phone,
        message=message,
        purpose=purpose,
        application=application,
        contract=contract,
        payment_contract=payment_contract,
        payment_transaction=payment_transaction,
        language=default_sms_language(language),
    )

    enabled = getattr(settings, "SMS_ENABLED", False)
    sid = getattr(settings, "TWILIO_ACCOUNT_SID", "")
    token = getattr(settings, "TWILIO_AUTH_TOKEN", "")
    from_number = getattr(settings, "TWILIO_PHONE_NUMBER", "")

    if not enabled:
        sms_log.status = SMSLog.STATUS_PENDING_CONFIG
        sms_log.error_message = "SMS_ENABLED is false."
        sms_log.save(update_fields=["status", "error_message"])
        return sms_log

    if not (sid and token and from_number):
        sms_log.status = SMSLog.STATUS_PENDING_CONFIG
        sms_log.error_message = "Twilio credentials are missing."
        sms_log.save(update_fields=["status", "error_message"])
        return sms_log

    if Client is None:
        sms_log.status = SMSLog.STATUS_FAILED
        sms_log.error_message = "twilio package is not installed."
        sms_log.save(update_fields=["status", "error_message"])
        return sms_log

    try:
        client = Client(sid, token)
        result = client.messages.create(body=message, from_=from_number, to=phone)
        sms_log.status = SMSLog.STATUS_SENT
        sms_log.provider_message_id = getattr(result, "sid", "") or ""
        sms_log.sent_at = timezone.now()
        sms_log.save(update_fields=["status", "provider_message_id", "sent_at"])
    except Exception as exc:
        logger.exception("Twilio SMS failed for %s", phone)
        sms_log.status = SMSLog.STATUS_FAILED
        sms_log.error_message = str(exc)
        sms_log.save(update_fields=["status", "error_message"])
    return sms_log


def _application_update_otp_status(application, status, verified=False):
    if not application:
        return
    application.phone_verified = verified
    application.phone_verification_status = status
    application.save(update_fields=["phone_verified", "phone_verification_status"])


def generate_phone_otp(phone_number, application=None, contract=None, payment_contract=None, language=None):
    phone = normalize_malawi_phone(phone_number)
    if not phone:
        _application_update_otp_status(application, PhoneOTP.STATUS_FAILED, False)
        return None

    otp = PhoneOTP.objects.create(
        phone_number=phone,
        otp_code=PhoneOTP.generate_code(),
        application=application,
        contract=contract,
        payment_contract=payment_contract,
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    message = render_sms_template("otp", default_sms_language(language), otp_code=otp.otp_code)
    sms_log = send_sms(
        phone,
        message,
        purpose=SMSLog.PURPOSE_OTP,
        application=application,
        contract=contract,
        payment_contract=payment_contract,
        language=language,
    )
    status = (
        PhoneOTP.STATUS_SENT
        if sms_log.status in (SMSLog.STATUS_SENT, SMSLog.STATUS_PENDING_CONFIG, SMSLog.STATUS_QUEUED)
        else PhoneOTP.STATUS_FAILED
    )
    _application_update_otp_status(application, status, False)
    return otp


def verify_phone_otp(phone_number, otp_code, application=None):
    phone = normalize_malawi_phone(phone_number)
    otp = (
        PhoneOTP.objects.filter(phone_number=phone, application=application, is_verified=False)
        .order_by("-created_at")
        .first()
    )
    if not otp:
        _application_update_otp_status(application, PhoneOTP.STATUS_NOT_SENT, False)
        return False
    otp.attempts += 1
    if otp.is_expired:
        otp.save(update_fields=["attempts"])
        _application_update_otp_status(application, PhoneOTP.STATUS_FAILED, False)
        return False
    if str(otp.otp_code) == str(otp_code or "").strip():
        otp.is_verified = True
        otp.save(update_fields=["attempts", "is_verified"])
        _application_update_otp_status(application, PhoneOTP.STATUS_VERIFIED, True)
        return True
    otp.save(update_fields=["attempts"])
    _application_update_otp_status(application, PhoneOTP.STATUS_FAILED, False)
    return False


def mark_otp_skipped(application):
    _application_update_otp_status(application, PhoneOTP.STATUS_SKIPPED, False)


def _contract_due_date(contract):
    return getattr(contract, "due_date", None) or timezone.localdate()


def _support_number():
    return getattr(settings, "TENGASALE_SUPPORT_PHONE", "+265 999 999 999")


def _days_covered(payment_amount, daily_price):
    """How many full days does this payment cover?"""
    try:
        from decimal import Decimal
        daily = Decimal(str(daily_price or 0))
        if daily <= 0:
            return 0
        return int(Decimal(str(payment_amount)) / daily)
    except Exception:
        return 0


def send_payment_confirmation_sms(payment, language=None):
    contract = payment.payment_contract
    if SMSLog.objects.filter(
        payment_transaction=payment,
        purpose=SMSLog.PURPOSE_PAYMENT_CONFIRMATION,
    ).exists():
        return None

    due_date = _contract_due_date(contract)
    today = timezone.localdate()
    days_remaining = max((due_date - today).days, 0)
    days_covered = _days_covered(payment.amount, contract.daily_price)

    message = render_sms_template(
        "payment_confirmation",
        default_sms_language(language),
        amount_paid=format_mwk(payment.amount),
        balance_left=format_mwk(contract.remaining_amount),
        days_covered=days_covered,
        paid_through_date=format_sms_date(due_date),
        days_remaining=days_remaining,
        support_number=_support_number(),
    )
    return send_sms(
        contract.customer_phone,
        message,
        purpose=SMSLog.PURPOSE_PAYMENT_CONFIRMATION,
        payment_contract=contract,
        application=contract.source_application,
        payment_transaction=payment,
        language=language,
    )


def send_approval_sms(contract, language=None):
    """Send SMS when a contract is approved. Safe — does not crash if SMS is off."""
    try:
        message = render_sms_template(
            "approval",
            default_sms_language(language),
        )
        return send_sms(
            contract.customer_phone,
            message,
            purpose=SMSLog.PURPOSE_PAYMENT_CONFIRMATION,
            payment_contract=contract,
            application=contract.source_application,
            language=language,
        )
    except Exception:
        logger.exception("send_approval_sms failed for contract %s", getattr(contract, "pk", "?"))
        return None


def send_contract_reminder(contract, purpose, language=None):
    due_date = _contract_due_date(contract)
    today = timezone.localdate()
    if SMSLog.objects.filter(payment_contract=contract, purpose=purpose, created_at__date=today).exists():
        return None

    context = {
        "due_date": format_sms_date(due_date),
        "days_until_due": max((due_date - today).days, 0),
        "balance_left": format_mwk(contract.remaining_amount),
        "support_number": _support_number(),
    }
    message = render_sms_template(purpose, default_sms_language(language), **context)
    return send_sms(
        contract.customer_phone,
        message,
        purpose=purpose,
        payment_contract=contract,
        application=contract.source_application,
        language=language,
    )
