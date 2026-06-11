from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def mask_secret(value: str) -> str:
    if not value:
        return ""
    value = str(value)
    if len(value) <= 6:
        return "***"
    return f"{value[:3]}***{value[-3:]}"


@dataclass(frozen=True)
class AirtelConfig:
    env: str
    base_url: str
    country: str
    currency: str
    merchant_code: str
    callback_auth_enabled: bool
    callback_hash_key: str
    client_id: str
    client_secret: str
    auth_token: str
    pin: str
    message_signing_enabled: bool
    signature_secret: str
    key: str
    collection_path: str
    enquiry_path_template: str
    disbursement_path: str

    @classmethod
    def from_settings(cls) -> "AirtelConfig":
        return cls(
            env=getattr(settings, "AIRTEL_ENV", "uat"),
            base_url=getattr(settings, "AIRTEL_BASE_URL", "https://openapiuat.airtel.mw"),
            country=getattr(settings, "AIRTEL_COUNTRY", "MW"),
            currency=getattr(settings, "AIRTEL_CURRENCY", "MWK"),
            merchant_code=getattr(settings, "AIRTEL_MERCHANT_CODE", ""),
            callback_auth_enabled=getattr(settings, "AIRTEL_CALLBACK_AUTH_ENABLED", False),
            callback_hash_key=getattr(settings, "AIRTEL_CALLBACK_HASH_KEY", ""),
            client_id=getattr(settings, "AIRTEL_CLIENT_ID", ""),
            client_secret=getattr(settings, "AIRTEL_CLIENT_SECRET", ""),
            auth_token=getattr(settings, "AIRTEL_AUTH_TOKEN", ""),
            pin=getattr(settings, "AIRTEL_PIN", ""),
            message_signing_enabled=getattr(settings, "AIRTEL_MESSAGE_SIGNING_ENABLED", False),
            signature_secret=getattr(settings, "AIRTEL_SIGNATURE_SECRET", ""),
            key=getattr(settings, "AIRTEL_KEY", ""),
            collection_path=getattr(settings, "AIRTEL_COLLECTION_PATH", "/merchant/v1/payments/"),
            enquiry_path_template=getattr(settings, "AIRTEL_ENQUIRY_PATH_TEMPLATE", "/standard/v1/payments/{reference}"),
            disbursement_path=getattr(settings, "AIRTEL_DISBURSEMENT_PATH", "/standard/v3/disbursements"),
        )

    def safe_summary(self) -> dict[str, Any]:
        return {
            "env": self.env,
            "base_url": self.base_url,
            "country": self.country,
            "currency": self.currency,
            "merchant_code_present": bool(self.merchant_code),
            "callback_auth_enabled": self.callback_auth_enabled,
            "callback_hash_key": mask_secret(self.callback_hash_key),
            "auth_token": mask_secret(self.auth_token),
            "client_id": mask_secret(self.client_id),
        }


class AirtelClient:
    """Thin HTTP client for direct Airtel Money API calls."""

    def __init__(self, config: AirtelConfig | None = None):
        self.config = config or AirtelConfig.from_settings()

    @property
    def is_configured_for_api_calls(self) -> bool:
        return bool(self.config.auth_token)

    def _url(self, path: str) -> str:
        return f"{self.config.base_url.rstrip('/')}/{path.lstrip('/')}"

    def _headers(self) -> dict[str, str]:
        headers = {
            "x-country": self.config.country,
            "x-currency": self.config.currency,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.config.auth_token:
            headers["Authorization"] = f"Bearer {self.config.auth_token}"
        return headers

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = requests.post(
            self._url(path),
            json=payload,
            headers=self._headers(),
            timeout=30,
        )
        return self._decode_response(response)

    def get(self, path: str) -> dict[str, Any]:
        response = requests.get(
            self._url(path),
            headers=self._headers(),
            timeout=30,
        )
        return self._decode_response(response)

    @staticmethod
    def _decode_response(response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text}
        return {
            "http_status": response.status_code,
            "ok": 200 <= response.status_code < 300,
            "body": body,
        }
