"""Production validation for Didit KYC settings."""
from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured


def _require_https_url(name: str, value: str) -> None:
    if not value:
        raise ImproperlyConfigured(f"{name} is required in production.")
    if not value.lower().startswith("https://"):
        raise ImproperlyConfigured(f"{name} must use HTTPS in production.")


def validate_didit_production_settings(
    *,
    debug: bool,
    api_key: str,
    workflow_id: str,
    webhook_secret: str,
    webhook_url: str,
    callback_url: str,
    allow_unsigned_webhooks: bool,
) -> None:
    """
    Enforce Didit production safety when DEBUG is False.
    Call from settings_production (and tests) with explicit values.
    """
    if debug:
        return

    if allow_unsigned_webhooks:
        raise ImproperlyConfigured(
            "DIDIT_ALLOW_UNSIGNED_WEBHOOKS cannot be True when DEBUG=False."
        )

    if not (api_key or "").strip():
        raise ImproperlyConfigured("DIDIT_API_KEY is required in production.")

    if not (workflow_id or "").strip():
        raise ImproperlyConfigured("DIDIT_WORKFLOW_ID is required in production.")

    if not (webhook_secret or "").strip():
        raise ImproperlyConfigured("DIDIT_WEBHOOK_SECRET is required in production.")

    _require_https_url("DIDIT_CALLBACK_URL", (callback_url or "").strip())
    _require_https_url("DIDIT_WEBHOOK_URL", (webhook_url or "").strip())
