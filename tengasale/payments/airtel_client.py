from __future__ import annotations

import logging
import time
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
    private_key: str
    client_id: str
    client_secret: str
    auth_token: str
    pin: str
    message_signing_enabled: bool
    signature_secret: str
    key: str
    collection_path: str
    enquiry_path_template: str
    token_path: str
    disbursement_path: str
    connect_timeout: int
    read_timeout: int

    @classmethod
    def from_settings(cls) -> "AirtelConfig":
        private_key = (
            getattr(settings, "AIRTEL_PRIVATE_KEY", "")
            or getattr(settings, "AIRTEL_CALLBACK_HASH_KEY", "")
            or getattr(settings, "AIRTEL_KEY", "")
        )
        return cls(
            env=getattr(settings, "AIRTEL_ENV", "uat"),
            base_url=getattr(settings, "AIRTEL_BASE_URL", "https://openapiuat.airtel.mw"),
            country=getattr(settings, "AIRTEL_COUNTRY", "MW"),
            currency=getattr(settings, "AIRTEL_CURRENCY", "MWK"),
            merchant_code=getattr(settings, "AIRTEL_MERCHANT_CODE", ""),
            callback_auth_enabled=getattr(settings, "AIRTEL_CALLBACK_AUTH_ENABLED", False),
            callback_hash_key=private_key,
            private_key=private_key,
            client_id=getattr(settings, "AIRTEL_CLIENT_ID", "") or getattr(settings, "AIRTEL_MONEY_CLIENT_ID", ""),
            client_secret=getattr(settings, "AIRTEL_CLIENT_SECRET", "") or getattr(settings, "AIRTEL_MONEY_CLIENT_SECRET", ""),
            auth_token=getattr(settings, "AIRTEL_AUTH_TOKEN", ""),
            pin=getattr(settings, "AIRTEL_PIN", ""),
            message_signing_enabled=getattr(settings, "AIRTEL_MESSAGE_SIGNING_ENABLED", False),
            signature_secret=getattr(settings, "AIRTEL_SIGNATURE_SECRET", ""),
            key=getattr(settings, "AIRTEL_KEY", ""),
            collection_path=getattr(settings, "AIRTEL_COLLECTION_PATH", "/merchant/v1/payments/"),
            enquiry_path_template=getattr(settings, "AIRTEL_ENQUIRY_PATH_TEMPLATE", "/standard/v1/payments/{reference}"),
            token_path=getattr(settings, "AIRTEL_TOKEN_PATH", "/auth/oauth2/token"),
            disbursement_path=getattr(settings, "AIRTEL_DISBURSEMENT_PATH", "/standard/v3/disbursements"),
            connect_timeout=int(getattr(settings, "AIRTEL_CONNECT_TIMEOUT", 5)),
            read_timeout=int(getattr(settings, "AIRTEL_READ_TIMEOUT", 20)),
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
            "token_configured": bool(self.auth_token or (self.client_id and self.client_secret)),
        }


class AirtelClient:
    """Thin HTTP client for direct Airtel Money API calls."""

    _cached_access_token: str = ""
    _cached_access_token_expires_at: float = 0

    def __init__(self, config: AirtelConfig | None = None):
        self.config = config or AirtelConfig.from_settings()

    @property
    def is_configured_for_api_calls(self) -> bool:
        return bool(self.config.auth_token or (self.config.client_id and self.config.client_secret))

    def _url(self, path: str) -> str:
        return f"{self.config.base_url.rstrip('/')}/{path.lstrip('/')}"

    def _timeout(self) -> tuple[int, int]:
        return (self.config.connect_timeout, self.config.read_timeout)

    def get_access_token(self) -> str:
        if self.config.auth_token:
            return self.config.auth_token

        now = time.time()
        if self._cached_access_token and self._cached_access_token_expires_at > now + 60:
            return self._cached_access_token

        if not (self.config.client_id and self.config.client_secret):
            raise RuntimeError("Airtel API credentials are not configured.")

        response = requests.post(
            self._url(self.config.token_path),
            json={
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "grant_type": "client_credentials",
            },
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=self._timeout(),
        )
        decoded = self._decode_response(response)
        if not decoded["ok"]:
            raise RuntimeError(f"Airtel token request failed with HTTP {decoded['http_status']}.")

        body = decoded.get("body") if isinstance(decoded.get("body"), dict) else {}
        token = (
            body.get("access_token")
            or body.get("accessToken")
            or body.get("token")
            or body.get("data", {}).get("access_token")
            or body.get("data", {}).get("accessToken")
        )
        if not token:
            raise RuntimeError("Airtel token response did not include an access token.")

        try:
            expires_in = int(body.get("expires_in") or body.get("expiresIn") or body.get("data", {}).get("expires_in") or 3600)
        except (TypeError, ValueError):
            expires_in = 3600
        self.__class__._cached_access_token = str(token)
        self.__class__._cached_access_token_expires_at = now + max(expires_in - 60, 60)
        return self._cached_access_token

    def _headers(self) -> dict[str, str]:
        headers = {
            "X-Country": self.config.country,
            "X-Currency": self.config.currency,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        headers["Authorization"] = f"Bearer {self.get_access_token()}"
        return headers

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = requests.post(
            self._url(path),
            json=payload,
            headers=self._headers(),
            timeout=self._timeout(),
        )
        return self._decode_response(response)

    def get(self, path: str) -> dict[str, Any]:
        response = requests.get(
            self._url(path),
            headers=self._headers(),
            timeout=self._timeout(),
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
