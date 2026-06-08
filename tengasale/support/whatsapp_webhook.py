"""
WhatsApp inbound webhook for TengaSale Support Chatbot.

Receives Twilio WhatsApp sandbox messages, runs them through the
chatbot state machine, saves messages, and sends replies via Twilio.

In development (missing credentials) it logs bot replies to console
and returns an XML stub so the Twilio sandbox still acknowledges.
"""

import logging
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils import timezone

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Twilio helper — optional; gracefully degrades if library missing
# ─────────────────────────────────────────────────────────────────────────────

def _send_whatsapp_reply(to_number: str, body: str) -> bool:
    """Send a WhatsApp message via Twilio.  Returns True on success."""
    sid   = getattr(settings, "TWILIO_ACCOUNT_SID", "")
    token = getattr(settings, "TWILIO_AUTH_TOKEN", "")
    from_number = getattr(settings, "TWILIO_WHATSAPP_FROM", "")

    if not (sid and token and from_number):
        logger.warning("Twilio credentials not configured — bot reply (console only):\n%s", body)
        print(f"\n[BOT REPLY to {to_number}]\n{body}\n")
        return False

    try:
        from twilio.rest import Client  # type: ignore
        client = Client(sid, token)
        client.messages.create(
            from_=f"whatsapp:{from_number}",
            to=f"whatsapp:{to_number}",
            body=body,
        )
        return True
    except ImportError:
        logger.warning("twilio library not installed — bot reply (console only):\n%s", body)
        print(f"\n[BOT REPLY to {to_number}]\n{body}\n")
        return False
    except Exception as exc:
        logger.error("Failed to send WhatsApp reply to %s: %s", to_number, exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Webhook endpoint
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def whatsapp_webhook(request):
    """
    POST /api/support/whatsapp/webhook/

    Receives inbound WhatsApp messages from Twilio.
    Returns TwiML <Response/> for Twilio compatibility.
    """
    # Import here to avoid circular imports at module load
    from support.models import SupportConversation, SupportMessage
    from support.chatbot import process_message

    # Parse Twilio POST params
    from_number  = request.POST.get("From", "")    # e.g. "whatsapp:+265..."
    body         = request.POST.get("Body", "").strip()
    profile_name = request.POST.get("ProfileName", "")

    # Strip "whatsapp:" prefix Twilio adds
    phone = from_number.replace("whatsapp:", "").strip()

    if not phone:
        logger.warning("WhatsApp webhook received request with no From number.")
        return _twiml_empty()

    logger.info("WhatsApp inbound from %s: %r", phone, body[:200])

    try:
        # Get or create conversation
        conv, created = SupportConversation.get_or_create_for_phone(phone, profile_name)

        # Save inbound message
        SupportMessage.objects.create(
            conversation=conv,
            sender=SupportMessage.SENDER_USER,
            message=body,
            raw_payload={
                "From": from_number,
                "Body": body,
                "ProfileName": profile_name,
            },
        )

        # Update last seen
        conv.last_message_at = timezone.now()
        conv.save(update_fields=["last_message_at", "updated_at"])

        # Run state machine
        reply = process_message(conv, body)

        # Save bot reply
        SupportMessage.objects.create(
            conversation=conv,
            sender=SupportMessage.SENDER_BOT,
            message=reply,
        )

        # Send reply via Twilio (or log to console in dev)
        _send_whatsapp_reply(phone, reply)

    except Exception as exc:
        logger.exception("Error processing WhatsApp message from %s: %s", phone, exc)
        # Still return 200 so Twilio does not keep retrying
        return _twiml_empty()

    return _twiml_empty()


def _twiml_empty() -> HttpResponse:
    """Return a minimal TwiML response (empty — bot replies are sent separately)."""
    return HttpResponse(
        '<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
        content_type="text/xml",
        status=200,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Simulation endpoint for development / testing
# ─────────────────────────────────────────────────────────────────────────────

def whatsapp_simulate(request):
    """
    GET/POST /api/support/whatsapp/simulate/?phone=+265...&body=1

    Development-only endpoint to simulate WhatsApp messages without Twilio.
    Returns JSON {reply: "..."} for easy testing.
    """
    if not settings.DEBUG:
        from django.http import Http404
        raise Http404

    from support.models import SupportConversation, SupportMessage
    from support.chatbot import process_message

    if request.method == "POST":
        phone = request.POST.get("phone", "+265000000000")
        body  = request.POST.get("body", "")
    else:
        phone = request.GET.get("phone", "+265000000000")
        body  = request.GET.get("body", "")

    conv, _ = SupportConversation.get_or_create_for_phone(phone, "Test User")
    SupportMessage.objects.create(
        conversation=conv,
        sender=SupportMessage.SENDER_USER,
        message=body,
    )
    conv.last_message_at = timezone.now()
    conv.save(update_fields=["last_message_at", "updated_at"])

    reply = process_message(conv, body)
    SupportMessage.objects.create(
        conversation=conv,
        sender=SupportMessage.SENDER_BOT,
        message=reply,
    )

    return JsonResponse({
        "phone": phone,
        "input": body,
        "state": conv.current_state,
        "reply": reply,
    })
