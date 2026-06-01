"""
services/messaging/providers/twilio_whatsapp.py

Twilio WhatsApp provider.
Sends WhatsApp documents via Twilio's Content API or Media Messages API.
Falls back gracefully if credentials are missing.
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings

from services.messaging.base import BaseMessagingProvider

logger = logging.getLogger("tengasale.messaging.twilio")


class TwilioWhatsAppProvider(BaseMessagingProvider):
    provider_name = "twilio"

    def __init__(self):
        self.api_key = getattr(settings, "WHATSAPP_API_KEY", "") or getattr(settings, "TWILIO_ACCOUNT_SID", "")
        self.api_secret = getattr(settings, "WHATSAPP_API_SECRET", "") or getattr(settings, "TWILIO_AUTH_TOKEN", "")
        self.from_number = getattr(settings, "WHATSAPP_FROM_NUMBER", "") or getattr(settings, "TWILIO_PHONE_NUMBER", "")
        self.timeout = getattr(settings, "WHATSAPP_TIMEOUT_SECONDS", 20)

    def send_whatsapp_document(
        self,
        to_phone: str,
        document_url: str,
        filename: str,
        message: str,
    ) -> dict[str, Any]:
        if not self.api_key or not self.api_secret:
            return self._error_response(
                "Twilio credentials not configured (WHATSAPP_API_KEY / WHATSAPP_API_SECRET)"
            )
        if not document_url:
            return self._error_response("PDF URL unavailable — cannot send via Twilio WhatsApp")

        try:
            from twilio.rest import Client  # type: ignore

            client = Client(self.api_key, self.api_secret)
            from_wa = f"whatsapp:{self.from_number}"
            to_wa = f"whatsapp:{to_phone}"
            msg = client.messages.create(
                from_=from_wa,
                to=to_wa,
                body=message,
                media_url=[document_url],
            )
            logger.info("Twilio WhatsApp sent to %s: SID=%s", to_phone, msg.sid)
            return self._success_response(
                provider_message_id=msg.sid,
                status="queued",
                raw_response={"sid": msg.sid, "status": msg.status},
            )
        except ImportError:
            return self._error_response(
                "twilio package not installed — add twilio to requirements.txt"
            )
        except Exception as exc:
            logger.exception("Twilio WhatsApp send failed for %s", to_phone)
            return self._error_response(str(exc))
