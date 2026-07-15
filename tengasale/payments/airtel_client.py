from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

STAGING_BASE_URL = "https://openapiuat.airtel.mw"
PRODUCTION_BASE_URL = "https://openapi.airtel.mw"
VALID_ENVIRONMENTS = {"staging", "production"}


class AirtelConfigurationError(RuntimeError):
    """Raised when Airtel-specific settings are unsafe or incomplete."""


def mask_secret(value: str) -> str:
    if not value:
        return ""
    value = str(value)
    if len(value) <= 6:
        return "***"
    return f"{value[:3]}***{value[-3:]}"


@dataclass(frozen=True)
class AirtelConfig:
    environment: str
    env: str
    base_url: str
    country: str
    currency: str
    merchant_code: str
    production_enabled: bool
    collections_enabled: bool
    dry_run: bool
    test_max_amount: str
    test_min_amount: str
    allowed_test_msisdns: tuple[str, ...]
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
        raw_environment = (
            getattr(settings, "AIRTEL_ENVIRONMENT", "")
            or getattr(settings, "AIRTEL_ENV", "staging")
            or "staging"
        )
        environment = str(raw_environment).strip().lower()
        if environment in {"uat", "test", "sandbox"}:
            environment = "staging"
        if environment in {"prod", "live"}:
            environment = "production"
        if environment not in VALID_ENVIRONMENTS:
            raise AirtelConfigurationError(
                "AIRTEL_ENVIRONMENT must be either 'staging' or 'production'."
            )

        production_enabled = bool(getattr(settings, "AIRTEL_PRODUCTION_ENABLED", False))
        if environment == "production" and not production_enabled:
            base_url = PRODUCTION_BASE_URL
        elif environment == "production":
            base_url = getattr(settings, "AIRTEL_BASE_URL", PRODUCTION_BASE_URL) or PRODUCTION_BASE_URL
        else:
            base_url = getattr(settings, "AIRTEL_BASE_URL", STAGING_BASE_URL) or STAGING_BASE_URL
            if base_url.rstrip("/") == PRODUCTION_BASE_URL:
                base_url = STAGING_BASE_URL

        private_key = (
            getattr(settings, "AIRTEL_PRIVATE_KEY", "")
            or getattr(settings, "AIRTEL_CALLBACK_SECRET", "")
            or getattr(settings, "AIRTEL_CALLBACK_HASH_KEY", "")
            or getattr(settings, "AIRTEL_KEY", "")
        )
        allowed_msisdns = tuple(
            value.strip()
            for value in str(getattr(settings, "AIRTEL_ALLOWED_TEST_MSISDNS", "") or "").split(",")
            if value.strip()
        )
        timeout_seconds = int(getattr(settings, "AIRTEL_REQUEST_TIMEOUT_SECONDS", 30))
        return cls(
            environment=environment,
            env=environment,
            base_url=base_url,
            country=getattr(settings, "AIRTEL_COUNTRY", "MW"),
            currency=getattr(settings, "AIRTEL_CURRENCY", "MWK"),
            merchant_code=getattr(settings, "AIRTEL_MERCHANT_CODE", ""),
            production_enabled=production_enabled,
            collections_enabled=bool(getattr(settings, "AIRTEL_COLLECTIONS_ENABLED", False)),
            dry_run=bool(getattr(settings, "AIRTEL_DRY_RUN", True)),
            test_max_amount=str(getattr(settings, "AIRTEL_TEST_MAX_AMOUNT", "1000")),
            test_min_amount=str(getattr(settings, "AIRTEL_TEST_MIN_AMOUNT", "100")),
            allowed_test_msisdns=allowed_msisdns,
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
            connect_timeout=int(getattr(settings, "AIRTEL_CONNECT_TIMEOUT", min(5, timeout_seconds))),
            read_timeout=int(getattr(settings, "AIRTEL_READ_TIMEOUT", timeout_seconds)),
        )

    def safe_summary(self) -> dict[str, Any]:
        return {
            "env": self.environment,
            "environment": self.environment,
            "base_url": self.base_url,
            "country": self.country,
            "currency": self.currency,
            "merchant_code_present": bool(self.merchant_code),
            "production_enabled": self.production_enabled,
            "collections_enabled": self.collections_enabled,
            "dry_run": self.dry_run,
            "allowed_test_numbers_configured": bool(self.allowed_test_msisdns),
            "test_max_amount": self.test_max_amount,
            "test_min_amount": self.test_min_amount,
            "callback_auth_enabled": self.callback_auth_enabled,
            "callback_secret_configured": bool(self.callback_hash_key),
            "auth_token_configured": bool(self.auth_token),
            "client_id_configured": bool(self.client_id),
            "client_secret_configured": bool(self.client_secret),
            "token_configured": bool(self.auth_token or (self.client_id and self.client_secret)),
        }

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def assert_production_allowed(self) -> None:
        if self.is_production and not self.production_enabled:
            raise AirtelConfigurationError(
                "Airtel production requests are blocked because AIRTEL_PRODUCTION_ENABLED is not true."
            )


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
        self.config.assert_production_allowed()
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
        self.config.assert_production_allowed()
        response = requests.post(
            self._url(path),
            json=payload,
            headers=self._headers(),
            timeout=self._timeout(),
        )
        return self._decode_response(response)

    def get(self, path: str) -> dict[str, Any]:
        self.config.assert_production_allowed()
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
