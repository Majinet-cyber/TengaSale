from __future__ import annotations

import logging
import json

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from accounts.utils import is_hq, is_hq_or_tech_support

from .models import SupportTicket, WhatsAppMessage
from .whatsapp_ops import (
    SUPPORT_MENU,
    mask_secret,
    process_inbound,
    provider_config_status,
    provider_mode,
    send_whatsapp_message,
    send_staff_reply,
    update_message_status,
)

logger = logging.getLogger(__name__)


def _twiml_response(body: str = "") -> HttpResponse:
    if body:
        escaped = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        content = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{escaped}</Message></Response>'
    else:
        content = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
    return HttpResponse(content, content_type="text/xml", status=200)


def _form_payload(request) -> dict:
    return {key: request.POST.get(key, "") for key in request.POST.keys()}


def _has_support_permission(user) -> bool:
    return user.is_authenticated and (user.is_superuser or is_hq(user) or is_hq_or_tech_support(user))


def _validate_twilio_signature(request) -> bool:
    if not getattr(settings, "TWILIO_VALIDATE_SIGNATURE", False):
        return True
    token = getattr(settings, "TWILIO_AUTH_TOKEN", "")
    if not token:
        logger.warning("TWILIO_VALIDATE_SIGNATURE is enabled but TWILIO_AUTH_TOKEN is missing.")
        return False
    signature = request.headers.get("X-Twilio-Signature", "")
    if not signature:
        return False
    try:
        from twilio.request_validator import RequestValidator  # type: ignore
    except Exception:
        logger.warning("twilio package is not installed; cannot validate Twilio signature.")
        return False
    validator = RequestValidator(token)
    return validator.validate(request.build_absolute_uri(), request.POST, signature)


@csrf_exempt
@require_POST
def whatsapp_webhook(request):
    if not _validate_twilio_signature(request):
        return HttpResponse("Invalid Twilio signature", status=403)
    try:
        result = process_inbound(_form_payload(request), provider="twilio", simulated=False)
    except Exception as exc:
        logger.exception("WhatsApp webhook processing failed: %s", exc)
        return _twiml_response()
    return _twiml_response(result.get("reply") or "")


@csrf_exempt
@require_POST
def whatsapp_status(request):
    if not _validate_twilio_signature(request):
        return HttpResponse("Invalid Twilio signature", status=403)
    try:
        result = update_message_status(_form_payload(request))
    except Exception as exc:
        logger.exception("WhatsApp status callback failed: %s", exc)
        return JsonResponse({"ok": False, "error": "status_callback_failed"}, status=200)
    return JsonResponse(result, status=200)


@login_required
@require_http_methods(["GET", "POST"])
def whatsapp_simulate(request):
    if not _has_support_permission(request.user):
        return JsonResponse({"ok": False, "error": "Permission denied."}, status=403)
    data = request.POST if request.method == "POST" else request.GET
    phone = data.get("phone", "").strip() or "+265883596135"
    message = data.get("message") or data.get("body") or data.get("Body") or "Hi"
    payload = {
        "From": f"whatsapp:{phone}",
        "To": getattr(settings, "TWILIO_WHATSAPP_FROM", "whatsapp:+15558359870"),
        "Body": message,
        "ProfileName": data.get("profile_name", "Test User"),
        "WaId": phone.replace("+", "").replace("whatsapp:", ""),
        "MessageSid": f"mock-inbound-{int(timezone.now().timestamp())}",
        "NumMedia": "0",
    }
    result = process_inbound(payload, provider="mock", simulated=True)
    return JsonResponse(result, status=200)


@login_required
@require_POST
def whatsapp_send_real_test(request):
    if not _has_support_permission(request.user):
        return JsonResponse({"ok": False, "error": "Permission denied."}, status=403)
    if provider_mode() == "mock":
        return JsonResponse(
            {"ok": False, "error": "Provider is mock. Real WhatsApp sending is disabled."},
            status=400,
        )
    if request.content_type == "application/json":
        try:
            data = json.loads(request.body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)
    else:
        data = request.POST
    phone = (data.get("phone") or "").strip()
    message = (data.get("message") or data.get("body") or "").strip()
    result = send_whatsapp_message(phone, message or "Hi from TengaSale Support", sent_by=request.user)
    return JsonResponse(result, status=200 if result.get("ok") else 400)


@login_required
def whatsapp_health(request):
    if not _has_support_permission(request.user):
        return JsonResponse({"ok": False, "error": "Permission denied."}, status=403)
    config = provider_config_status()
    last_inbound = WhatsAppMessage.objects.filter(direction=WhatsAppMessage.DIRECTION_INBOUND).order_by("-created_at").first()
    last_outbound = WhatsAppMessage.objects.filter(direction=WhatsAppMessage.DIRECTION_OUTBOUND).order_by("-created_at").first()
    return JsonResponse(
        {
            "ok": True,
            "provider": config["label"],
            "ready": config["ready"],
            "missing": config["missing"],
            "messaging_service_sid": mask_secret(config["messaging_service_sid"]),
            "sender": mask_secret(config["sender"]),
            "webhook_url": getattr(settings, "WHATSAPP_WEBHOOK_URL", "/tengasale/support/whatsapp/webhook/"),
            "status_callback_url": getattr(settings, "WHATSAPP_STATUS_CALLBACK_URL", "/tengasale/support/whatsapp/status/"),
            "last_inbound": last_inbound.created_at.isoformat() if last_inbound else None,
            "last_outbound": last_outbound.created_at.isoformat() if last_outbound else None,
        },
        status=200,
    )


@login_required
@require_POST
def whatsapp_ticket_reply(request, ticket_id):
    if not _has_support_permission(request.user):
        return JsonResponse({"ok": False, "error": "Permission denied."}, status=403)
    try:
        ticket = SupportTicket.objects.get(pk=ticket_id, source="whatsapp")
    except SupportTicket.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Ticket not found."}, status=404)
    message = request.POST.get("message", "")
    result = send_staff_reply(ticket, request.user, message)
    return JsonResponse(result, status=200 if result.get("ok") else 400)


def support_menu_text() -> str:
    return SUPPORT_MENU
