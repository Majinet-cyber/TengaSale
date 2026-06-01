"""
services/messaging/providers/meta_whatsapp.py

Meta (Facebook) WhatsApp Business API provider.
Falls back gracefully if credentials are missing.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from django.conf import settings

from services.messaging.base import BaseMessagingProvider

logger = logging.getLogger("tengasale.messaging.meta")


class MetaWhatsAppProvider(BaseMessagingProvider):
    provider_name = "meta_whatsapp"

    def __init__(self):
        self.access_token = getattr(settings, "WHATSAPP_ACCESS_TOKEN", "")
        self.phone_number_id = getattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "")
        self.timeout = getattr(settings, "WHATSAPP_TIMEOUT_SECONDS", 20)

    def send_whatsapp_document(
        self,
        to_phone: str,
        document_url: str,
        filename: str,
        message: str,
    ) -> dict[str, Any]:
        if not self.access_token or not self.phone_number_id:
            return self._error_response(
                "Meta WhatsApp credentials not configured "
                "(WHATSAPP_ACCESS_TOKEN / WHATSAPP_PHONE_NUMBER_ID)"
            )
        if not document_url:
            return self._error_response("PDF URL unavailable — cannot send via Meta WhatsApp")

        try:
            import urllib.request

            url = (
                f"https://graph.facebook.com/v19.0/{self.phone_number_id}/messages"
            )
            payload = {
                "messaging_product": "whatsapp",
                "to": to_phone.lstrip("+"),
                "type": "document",
                "document": {
                    "link": document_url,
                    "filename": filename,
                    "caption": message,
                },
            }
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            import urllib.error
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = json.loads(resp.read())
                    msg_id = raw.get("messages", [{}])[0].get("id", "")
                    logger.info("Meta WhatsApp sent to %s: id=%s", to_phone, msg_id)
                    return self._success_response(
                        provider_message_id=msg_id,
                        status="sent",
                        raw_response=raw,
                    )
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                logger.error("Meta WhatsApp HTTP error %s: %s", exc.code, body)
                return self._error_response(f"HTTP {exc.code}: {body[:300]}", raw_response={"status": exc.code})
        except Exception as exc:
            logger.exception("Meta WhatsApp send failed for %s", to_phone)
            return self._error_response(str(exc))
