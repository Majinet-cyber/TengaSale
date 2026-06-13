from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from services.messaging.phone import try_normalize_malawi_phone

from .models import (
    SupportTicket,
    SupportTicketAuditLog,
    WhatsAppContact,
    WhatsAppConversation,
    WhatsAppMessage,
)

logger = logging.getLogger(__name__)

SUPPORT_MENU = (
    "Welcome to TengaSale Support. How can we help you today?\n\n"
    "1. Check my application\n"
    "2. Payment issue\n"
    "3. Device locked\n"
    "4. Deposit paid but not updated\n"
    "5. Warranty / damaged phone\n"
    "6. Speak to support\n"
    "7. Merchant support\n"
    "8. Report fraud"
)

TEMPLATE_REGISTRY = {
    "tengasale_ticket_created": "",
    "tengasale_ticket_assigned": "",
    "tengasale_ticket_waiting_customer": "",
    "tengasale_ticket_escalated": "",
    "tengasale_ticket_resolved": "",
    "tengasale_ticket_closed": "",
    "tengasale_support_menu": "",
    "tengasale_otp_verification": "",
}

GREETING_WORDS = {"hi", "hello", "help", "support", "menu", "start"}
LEGAL_KEYWORDS = (
    "lawyer",
    "police",
    "court",
    "sue",
    "legal",
    "tribunal",
    "report you",
    "complain to regulator",
)

MENU_CATEGORIES = {
    "1": SupportTicket.CAT_APPLICATION_STATUS,
    "2": SupportTicket.CAT_PAYMENT_NOT_REFLECTING,
    "3": SupportTicket.CAT_DEVICE_LOCK,
    "4": SupportTicket.CAT_DEPOSIT_ISSUE,
    "5": SupportTicket.CAT_WARRANTY_ISSUE,
    "6": SupportTicket.CAT_GENERAL_SUPPORT,
    "7": SupportTicket.CAT_MERCHANT_SUPPORT,
    "8": SupportTicket.CAT_FRAUD_REPORT,
}

TEXT_CATEGORY_HINTS = [
    (("application", "status"), SupportTicket.CAT_APPLICATION_STATUS),
    (("payment", "paid", "reflect"), SupportTicket.CAT_PAYMENT_NOT_REFLECTING),
    (("locked", "unlock"), SupportTicket.CAT_DEVICE_LOCK),
    (("deposit",), SupportTicket.CAT_DEPOSIT_ISSUE),
    (("warranty", "damaged", "broken"), SupportTicket.CAT_WARRANTY_ISSUE),
    (("merchant",), SupportTicket.CAT_MERCHANT_SUPPORT),
    (("fraud", "scam"), SupportTicket.CAT_FRAUD_REPORT),
]

SLA_HOURS = {
    SupportTicket.CAT_PAYMENT_NOT_REFLECTING: 2,
    SupportTicket.CAT_DEVICE_LOCK: 1,
    SupportTicket.CAT_UNLOCK_REQUEST: 1,
    SupportTicket.CAT_APPLICATION_STATUS: 4,
    SupportTicket.CAT_WARRANTY_ISSUE: 24,
    SupportTicket.CAT_MERCHANT_SUPPORT: 2,
    SupportTicket.CAT_DEPOSIT_ISSUE: 2,
    SupportTicket.CAT_GENERAL_SUPPORT: 8,
}


def normalize_whatsapp_phone(raw_phone: str) -> str:
    value = (raw_phone or "").strip().replace("whatsapp:", "")
    normalized, error = try_normalize_malawi_phone(value)
    if normalized:
        return normalized
    compact = re.sub(r"[\s\-\(\)\.]+", "", value)
    if compact.startswith("+"):
        return compact
    if compact.startswith("265"):
        return f"+{compact}"
    if compact.startswith("0"):
        fallback, _ = try_normalize_malawi_phone(compact)
        return fallback or compact
    logger.info("Could not normalize WhatsApp phone %r: %s", raw_phone, error)
    return compact or value


def provider_mode() -> str:
    return getattr(settings, "WHATSAPP_PROVIDER", "mock") or "mock"


def provider_label() -> str:
    mode = provider_mode()
    return {
        "mock": "Mock",
        "twilio": "Twilio Production",
        "twilio_sandbox": "Twilio Sandbox",
        "twilio_production": "Twilio Production",
    }.get(mode, mode.replace("_", " ").title())


def provider_is_twilio() -> bool:
    return provider_mode() in {"twilio", "twilio_sandbox", "twilio_production"}


def provider_config_status() -> dict[str, Any]:
    mode = provider_mode()
    required = []
    if provider_is_twilio():
        if not getattr(settings, "TWILIO_ACCOUNT_SID", ""):
            required.append("TWILIO_ACCOUNT_SID")
        if not getattr(settings, "TWILIO_AUTH_TOKEN", ""):
            required.append("TWILIO_AUTH_TOKEN")
        if not (
            getattr(settings, "TWILIO_MESSAGING_SERVICE_SID", "")
            or getattr(settings, "TWILIO_WHATSAPP_FROM", "")
        ):
            required.append("TWILIO_MESSAGING_SERVICE_SID or TWILIO_WHATSAPP_FROM")
    return {
        "mode": mode,
        "label": provider_label(),
        "ready": not required,
        "missing": required,
        "messaging_service_sid": getattr(settings, "TWILIO_MESSAGING_SERVICE_SID", ""),
        "sender": getattr(settings, "TWILIO_WHATSAPP_FROM", ""),
    }


def mask_secret(value: str, visible: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= visible * 2:
        return f"{value[:2]}..."
    return f"{value[:visible]}...{value[-visible:]}"


def parse_media(payload: dict[str, Any]) -> list[dict[str, str]]:
    try:
        count = int(payload.get("NumMedia") or 0)
    except (TypeError, ValueError):
        count = 0
    media = []
    for index in range(count):
        url = payload.get(f"MediaUrl{index}", "")
        content_type = payload.get(f"MediaContentType{index}", "")
        if url or content_type:
            media.append({"url": url, "content_type": content_type})
    return media


def _find_application(phone_e164: str):
    try:
        from applications.models import FinancingApplication
    except Exception:
        return None
    local = phone_e164.replace("+265", "")
    return FinancingApplication.objects.filter(customer_phone=local).order_by("-created_at").first()


def _find_merchant(phone_e164: str):
    try:
        from merchants.models import Merchant
    except Exception:
        return None
    candidates = {phone_e164, phone_e164.replace("+265", "0"), phone_e164.replace("+", "")}
    return Merchant.objects.filter(phone_number__in=candidates).first()


def _find_user_by_phone(phone_e164: str):
    User = get_user_model()
    candidates = {phone_e164, phone_e164.replace("+265", "0"), phone_e164.replace("+", "")}
    return (
        User.objects.filter(profile__phone__in=candidates).first()
        or User.objects.filter(profile__phone_number__in=candidates).first()
    )


def match_or_create_contact(phone_e164: str, profile_name: str = "", wa_id: str = "") -> tuple[WhatsAppContact, Any]:
    now = timezone.now()
    application = _find_application(phone_e164)
    merchant = _find_merchant(phone_e164)
    user = _find_user_by_phone(phone_e164)

    sender_type = WhatsAppContact.SENDER_UNKNOWN
    linked_underwriter = None
    linked_customer = None
    if application:
        sender_type = WhatsAppContact.SENDER_CUSTOMER
    if merchant:
        sender_type = WhatsAppContact.SENDER_MERCHANT
    if user:
        role = getattr(getattr(user, "profile", None), "role", "")
        if role == "underwriter":
            sender_type = WhatsAppContact.SENDER_UNDERWRITER
            linked_underwriter = user
        elif not application:
            linked_customer = user

    contact, _ = WhatsAppContact.objects.get_or_create(
        phone_e164=phone_e164,
        defaults={
            "profile_name": profile_name or "",
            "whatsapp_id": wa_id or "",
            "sender_type": sender_type,
            "linked_customer": linked_customer,
            "linked_merchant": merchant,
            "linked_underwriter": linked_underwriter,
            "last_seen_at": now,
        },
    )
    updates = []
    for field, value in {
        "profile_name": profile_name or contact.profile_name,
        "whatsapp_id": wa_id or contact.whatsapp_id,
        "sender_type": sender_type,
        "linked_customer": linked_customer or contact.linked_customer,
        "linked_merchant": merchant or contact.linked_merchant,
        "linked_underwriter": linked_underwriter or contact.linked_underwriter,
        "last_seen_at": now,
    }.items():
        if getattr(contact, field) != value:
            setattr(contact, field, value)
            updates.append(field)
    if updates:
        contact.save(update_fields=updates + ["updated_at"])
    return contact, application


def _category_from_message(body: str) -> str:
    text = (body or "").strip().lower()
    if text in MENU_CATEGORIES:
        return MENU_CATEGORIES[text]
    for terms, category in TEXT_CATEGORY_HINTS:
        if any(term in text for term in terms):
            return category
    if any(keyword in text for keyword in LEGAL_KEYWORDS):
        return SupportTicket.CAT_LEGAL_REVIEW
    return ""


def _priority_for(category: str, body: str) -> str:
    text = (body or "").lower()
    if category == SupportTicket.CAT_FRAUD_REPORT or any(keyword in text for keyword in LEGAL_KEYWORDS):
        return SupportTicket.PRI_URGENT
    if category in {
        SupportTicket.CAT_DEVICE_LOCK,
        SupportTicket.CAT_UNLOCK_REQUEST,
        SupportTicket.CAT_PAYMENT_NOT_REFLECTING,
        SupportTicket.CAT_DEPOSIT_ISSUE,
    }:
        return SupportTicket.PRI_HIGH
    return SupportTicket.PRI_NORMAL


def _sla_due_for(category: str) -> timezone.datetime | None:
    if category == SupportTicket.CAT_FRAUD_REPORT:
        return timezone.now()
    hours = SLA_HOURS.get(category)
    if not hours:
        return None
    return timezone.now() + timedelta(hours=hours)


def _open_ticket_for(conversation: WhatsAppConversation) -> SupportTicket | None:
    if conversation.active_ticket_id and conversation.active_ticket.status not in {
        SupportTicket.STATUS_RESOLVED,
        SupportTicket.STATUS_CLOSED,
    }:
        return conversation.active_ticket
    return (
        SupportTicket.objects.filter(
            linked_contact=conversation.contact,
            source="whatsapp",
        )
        .exclude(status__in=[SupportTicket.STATUS_RESOLVED, SupportTicket.STATUS_CLOSED])
        .order_by("-created_at")
        .first()
    )


def _ticket_title(category: str) -> str:
    labels = dict(SupportTicket.CATEGORY_CHOICES)
    return f"WhatsApp support: {labels.get(category, category).title()}"


def _ticket_confirmation(ticket: SupportTicket) -> str:
    name = ticket.sender_name or "there"
    return (
        f"Hi {name}, your TengaSale support ticket {ticket.ticket_number} has been created. "
        "Our team will respond shortly."
    )


def _save_auto_reply(conversation: WhatsAppConversation, ticket: SupportTicket | None, body: str) -> WhatsAppMessage:
    provider = WhatsAppMessage.PROVIDER_TWILIO if provider_is_twilio() else WhatsAppMessage.PROVIDER_MOCK
    status = WhatsAppMessage.STATUS_SENT if provider_is_twilio() else WhatsAppMessage.STATUS_DELIVERED
    return WhatsAppMessage.objects.create(
        conversation=conversation,
        ticket=ticket,
        direction=WhatsAppMessage.DIRECTION_OUTBOUND,
        provider=provider,
        provider_status=status,
        from_phone=getattr(settings, "TWILIO_WHATSAPP_FROM", ""),
        to_phone=conversation.contact.phone_e164,
        body=body,
    )


@transaction.atomic
def process_inbound(payload: dict[str, Any], *, provider: str | None = None, simulated: bool = False) -> dict[str, Any]:
    raw_from = payload.get("From") or payload.get("phone") or payload.get("Phone") or ""
    raw_to = payload.get("To") or getattr(settings, "TWILIO_WHATSAPP_FROM", "")
    body = (payload.get("Body") or payload.get("message") or payload.get("body") or "").strip()
    phone = normalize_whatsapp_phone(raw_from)
    profile_name = payload.get("ProfileName") or ("Test User" if simulated else "")
    wa_id = payload.get("WaId") or ""
    message_sid = payload.get("MessageSid") or payload.get("SmsMessageSid") or ("mock-inbound" if simulated else "")
    media = parse_media(payload)
    provider_name = provider or (WhatsAppMessage.PROVIDER_TWILIO if not simulated else WhatsAppMessage.PROVIDER_MOCK)

    contact, application = match_or_create_contact(phone, profile_name, wa_id)
    conversation, _ = WhatsAppConversation.objects.get_or_create(contact=contact)

    open_ticket = _open_ticket_for(conversation)
    inbound = WhatsAppMessage.objects.create(
        conversation=conversation,
        ticket=open_ticket,
        direction=WhatsAppMessage.DIRECTION_INBOUND,
        provider=provider_name,
        provider_message_sid=message_sid,
        provider_status=WhatsAppMessage.STATUS_RECEIVED,
        from_phone=phone,
        to_phone=normalize_whatsapp_phone(raw_to) if raw_to else "",
        body=body,
        media_count=len(media),
        media_json=media,
        raw_payload=payload,
        received_at=timezone.now(),
    )

    reply = ""
    ticket = open_ticket
    category = ""
    lower_body = body.lower()

    if ticket:
        from_status = ticket.status
        if ticket.status in {SupportTicket.STATUS_WAITING_USER, SupportTicket.STATUS_WAITING_CUSTOMER}:
            ticket.status = SupportTicket.STATUS_IN_PROGRESS
        ticket.last_message_preview = body[:240]
        ticket.last_message_at = timezone.now()
        ticket.save(update_fields=["status", "last_message_preview", "last_message_at", "updated_at"])
        inbound.ticket = ticket
        inbound.save(update_fields=["ticket"])
        SupportTicketAuditLog.objects.create(
            ticket=ticket,
            action="customer_reply",
            from_status=from_status,
            to_status=ticket.status,
            note=body[:500],
            metadata={"message_id": inbound.pk},
        )
    elif lower_body in GREETING_WORDS or not body:
        reply = SUPPORT_MENU
        _save_auto_reply(conversation, None, reply)
    else:
        category = _category_from_message(body) or SupportTicket.CAT_GENERAL_SUPPORT
        priority = _priority_for(category, body)
        escalation_reason = ""
        if category == SupportTicket.CAT_FRAUD_REPORT:
            escalation_reason = "Immediate fraud report escalation"
        elif any(keyword in lower_body for keyword in LEGAL_KEYWORDS):
            escalation_reason = "Legal threat keyword detected"
            priority = SupportTicket.PRI_URGENT

        ticket = SupportTicket.objects.create(
            title=_ticket_title(category),
            description=body or "WhatsApp support request",
            category=category,
            priority=priority,
            status=SupportTicket.STATUS_NEW,
            source="whatsapp",
            sender_phone=phone,
            sender_name=profile_name or "",
            sender_type=contact.sender_type,
            linked_contact=contact,
            linked_merchant=contact.linked_merchant,
            linked_underwriter=contact.linked_underwriter,
            related_application=application,
            sla_due_at=_sla_due_for(category),
            escalation_reason=escalation_reason,
            last_message_preview=body[:240],
            last_message_at=timezone.now(),
            created_by=None,
        )
        conversation.active_ticket = ticket
        conversation.status = WhatsAppConversation.STATUS_OPEN
        inbound.ticket = ticket
        inbound.save(update_fields=["ticket"])
        SupportTicketAuditLog.objects.create(
            ticket=ticket,
            action="ticket_created",
            note=body[:500],
            metadata={"category": category, "message_id": inbound.pk, "simulated": simulated},
        )
        reply = _ticket_confirmation(ticket)
        _save_auto_reply(conversation, ticket, reply)

    conversation.last_message_at = timezone.now()
    conversation.last_message_preview = body[:240]
    conversation.save(update_fields=["active_ticket", "status", "last_message_at", "last_message_preview", "updated_at"])

    return {
        "ok": True,
        "simulated": simulated,
        "contact_id": contact.pk,
        "conversation_id": conversation.pk,
        "message_id": inbound.pk,
        "ticket_id": ticket.pk if ticket else None,
        "ticket_number": ticket.ticket_number if ticket else "",
        "category": category,
        "reply": reply,
        "state": conversation.status,
    }


def format_whatsapp_to(phone_e164: str) -> str:
    return phone_e164 if phone_e164.startswith("whatsapp:") else f"whatsapp:{phone_e164}"


def send_staff_reply(ticket: SupportTicket, user, body: str) -> dict[str, Any]:
    body = (body or "").strip()
    if not body:
        return {"ok": False, "error": "Message is required."}
    conversation = ticket.whatsapp_conversations.select_related("contact").first()
    if not conversation and ticket.linked_contact_id:
        conversation, _ = WhatsAppConversation.objects.get_or_create(contact=ticket.linked_contact)
    if not conversation:
        return {"ok": False, "error": "Ticket is not linked to a WhatsApp conversation."}

    provider = WhatsAppMessage.PROVIDER_TWILIO if provider_is_twilio() else WhatsAppMessage.PROVIDER_MOCK
    message = WhatsAppMessage.objects.create(
        conversation=conversation,
        ticket=ticket,
        direction=WhatsAppMessage.DIRECTION_OUTBOUND,
        provider=provider,
        provider_status=WhatsAppMessage.STATUS_QUEUED,
        from_phone=getattr(settings, "TWILIO_WHATSAPP_FROM", ""),
        to_phone=conversation.contact.phone_e164,
        body=body,
        sent_by_user=user,
    )

    if provider == WhatsAppMessage.PROVIDER_MOCK:
        message.provider_status = WhatsAppMessage.STATUS_DELIVERED
        message.provider_message_sid = f"mock-{message.pk}"
        message.save(update_fields=["provider_status", "provider_message_sid", "updated_at"])
    else:
        config = provider_config_status()
        if not config["ready"]:
            message.provider_status = WhatsAppMessage.STATUS_FAILED
            message.error_message = "Missing Twilio config: " + ", ".join(config["missing"])
            message.save(update_fields=["provider_status", "error_message", "updated_at"])
            return {"ok": False, "error": message.error_message, "message_id": message.pk}
        try:
            from twilio.rest import Client  # type: ignore

            client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
            kwargs = {
                "to": format_whatsapp_to(conversation.contact.phone_e164),
                "body": body,
                "status_callback": getattr(settings, "WHATSAPP_STATUS_CALLBACK_URL", ""),
            }
            if getattr(settings, "TWILIO_MESSAGING_SERVICE_SID", ""):
                kwargs["messaging_service_sid"] = settings.TWILIO_MESSAGING_SERVICE_SID
            else:
                kwargs["from_"] = format_whatsapp_to(settings.TWILIO_WHATSAPP_FROM)
            sent = client.messages.create(**kwargs)
            message.provider_message_sid = sent.sid
            message.provider_status = getattr(sent, "status", WhatsAppMessage.STATUS_QUEUED) or WhatsAppMessage.STATUS_QUEUED
            message.save(update_fields=["provider_message_sid", "provider_status", "updated_at"])
        except Exception as exc:
            logger.exception("Twilio WhatsApp support reply failed for ticket %s", ticket.pk)
            message.provider_status = WhatsAppMessage.STATUS_FAILED
            message.error_message = str(exc)
            message.save(update_fields=["provider_status", "error_message", "updated_at"])
            return {"ok": False, "error": message.error_message, "message_id": message.pk}

    from_status = ticket.status
    update_fields = ["status", "last_message_preview", "last_message_at", "updated_at"]
    if not ticket.first_response_at:
        ticket.first_response_at = timezone.now()
        update_fields.append("first_response_at")
    if ticket.status not in {SupportTicket.STATUS_WAITING_CUSTOMER, SupportTicket.STATUS_RESOLVED, SupportTicket.STATUS_CLOSED}:
        ticket.status = SupportTicket.STATUS_IN_PROGRESS
    ticket.last_message_preview = body[:240]
    ticket.last_message_at = timezone.now()
    ticket.save(update_fields=update_fields)

    conversation.last_message_at = timezone.now()
    conversation.last_message_preview = body[:240]
    conversation.save(update_fields=["last_message_at", "last_message_preview", "updated_at"])
    SupportTicketAuditLog.objects.create(
        ticket=ticket,
        actor=user,
        action="staff_reply",
        from_status=from_status,
        to_status=ticket.status,
        note=body[:500],
        metadata={"message_id": message.pk},
    )
    return {
        "ok": True,
        "message_id": message.pk,
        "provider_status": message.provider_status,
        "provider_message_sid": message.provider_message_sid,
    }


def update_message_status(payload: dict[str, Any]) -> dict[str, Any]:
    sid = payload.get("MessageSid") or payload.get("SmsMessageSid") or ""
    status = payload.get("MessageStatus") or payload.get("SmsStatus") or WhatsAppMessage.STATUS_UNKNOWN
    normalized = status if status in dict(WhatsAppMessage.STATUS_CHOICES) else WhatsAppMessage.STATUS_UNKNOWN
    try:
        message = WhatsAppMessage.objects.get(provider_message_sid=sid)
    except WhatsAppMessage.DoesNotExist:
        return {"ok": True, "matched": False}
    message.provider_status = normalized
    if normalized in {WhatsAppMessage.STATUS_FAILED, WhatsAppMessage.STATUS_UNDELIVERED}:
        error = payload.get("ErrorMessage") or payload.get("ErrorCode") or "Delivery failed"
        message.error_message = str(error)
    message.raw_payload = {**(message.raw_payload or {}), "status_callback": payload}
    message.save(update_fields=["provider_status", "error_message", "raw_payload", "updated_at"])
    return {"ok": True, "matched": True, "message_id": message.pk, "status": normalized}
