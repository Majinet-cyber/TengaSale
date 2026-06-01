"""
services/messaging/base.py

Provider-agnostic messaging interface.
All WhatsApp/SMS providers implement this protocol.
"""

from __future__ import annotations

from typing import Any, Optional


class BaseMessagingProvider:
    """
    Base class for all TengaSale messaging providers.

    All providers must implement `send_whatsapp_document`.
    """

    provider_name: str = "base"

    def send_whatsapp_document(
        self,
        to_phone: str,
        document_url: str,
        filename: str,
        message: str,
    ) -> dict[str, Any]:
        """
        Send a WhatsApp document message.

        Args:
            to_phone: E.164 phone number of the recipient.
            document_url: Publicly accessible URL to the PDF document.
            filename: Display name for the document.
            message: Caption / text message to accompany the document.

        Returns a normalised dict:
        {
            "success": True | False,
            "provider": "...",
            "provider_message_id": "...",
            "status": "sent" | "failed" | "queued",
            "raw_response": {...},
            "error": "...",
        }
        """
        raise NotImplementedError

    def _success_response(
        self,
        provider_message_id: str = "",
        status: str = "sent",
        raw_response: Optional[dict] = None,
    ) -> dict[str, Any]:
        return {
            "success": True,
            "provider": self.provider_name,
            "provider_message_id": provider_message_id,
            "status": status,
            "raw_response": raw_response or {},
            "error": "",
        }

    def _error_response(
        self,
        error: str,
        raw_response: Optional[dict] = None,
    ) -> dict[str, Any]:
        return {
            "success": False,
            "provider": self.provider_name,
            "provider_message_id": "",
            "status": "failed",
            "raw_response": raw_response or {},
            "error": error,
        }
