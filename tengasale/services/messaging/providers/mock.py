"""
services/messaging/providers/mock.py

Mock WhatsApp provider for testing and demo.
Records the send attempt as successful without any real network call.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from services.messaging.base import BaseMessagingProvider

logger = logging.getLogger("tengasale.messaging.mock")


class MockWhatsAppProvider(BaseMessagingProvider):
    """Mock provider — always succeeds. Used for testing and demo environments."""

    provider_name = "mock"

    def send_whatsapp_document(
        self,
        to_phone: str,
        document_url: str,
        filename: str,
        message: str,
    ) -> dict[str, Any]:
        mock_id = f"MOCK-{uuid.uuid4().hex[:16].upper()}"
        logger.info(
            "[MOCK] WhatsApp document sent to %s | file=%s | msg_id=%s",
            to_phone,
            filename,
            mock_id,
        )
        return self._success_response(
            provider_message_id=mock_id,
            status="sent",
            raw_response={
                "mock": True,
                "to": to_phone,
                "filename": filename,
                "document_url": document_url,
                "message_preview": message[:100],
            },
        )
