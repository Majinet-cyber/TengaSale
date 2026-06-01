"""
services/messaging/registry.py

Provider registry — returns the configured messaging provider instance.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.conf import settings

from services.messaging.base import BaseMessagingProvider

if TYPE_CHECKING:
    pass

logger = logging.getLogger("tengasale.messaging.registry")


def get_whatsapp_provider() -> BaseMessagingProvider:
    """
    Return the active WhatsApp provider based on WHATSAPP_PROVIDER setting.

    Always returns a provider instance — falls back to mock on misconfiguration.
    """
    provider_name = getattr(settings, "WHATSAPP_PROVIDER", "mock").lower()

    if provider_name == "twilio":
        from services.messaging.providers.twilio_whatsapp import TwilioWhatsAppProvider
        return TwilioWhatsAppProvider()

    if provider_name in ("meta_whatsapp", "meta"):
        from services.messaging.providers.meta_whatsapp import MetaWhatsAppProvider
        return MetaWhatsAppProvider()

    if provider_name != "mock":
        logger.warning(
            "Unknown WHATSAPP_PROVIDER=%r — falling back to mock provider", provider_name
        )

    from services.messaging.providers.mock import MockWhatsAppProvider
    return MockWhatsAppProvider()


def is_whatsapp_enabled() -> bool:
    """Return True if WhatsApp delivery is enabled in settings."""
    return (
        getattr(settings, "MESSAGING_ENABLED", True)
        and getattr(settings, "WHATSAPP_DELIVERY_ENABLED", True)
    )
