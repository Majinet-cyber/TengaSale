import logging
import time
from urllib.parse import quote

import requests
from django.conf import settings

from .dto import UpyaCustomerDetails, UpyaPaymentOption, UpyaPaymentRecord, optional_decimal
from .exceptions import (
    UpyaAuthenticationError,
    UpyaBadResponse,
    UpyaConfigurationError,
    UpyaCustomerNotFound,
    UpyaTimeout,
)

logger = logging.getLogger(__name__)


class UpyaPaymentGateway:
    """Customer-facing Upya Payment Gateway client.

    TENGA OWNS THE EXPERIENCE.
    UPYA OWNS PAYGO TRUTH.
    """

    def __init__(self, session=None):
        self.base_url = settings.UPYA_PAYMENT_GATEWAY_BASE_URL.rstrip("/")
        self.username = settings.UPYA_PAYMENT_GATEWAY_USERNAME
        self.password = settings.UPYA_PAYMENT_GATEWAY_PASSWORD
        self.client_identifier = settings.UPYA_PAYMENT_GATEWAY_CLIENT_IDENTIFIER
        self.timeout = (settings.UPYA_CONNECT_TIMEOUT, settings.UPYA_READ_TIMEOUT)
        self.session = session or requests.Session()

    def _ensure_configured(self):
        if not all((self.base_url, self.username, self.password, self.client_identifier)):
            raise UpyaConfigurationError("Upya Payment Gateway credentials/clientIdentifier are required.")

    def _request(self, method, path, **kwargs):
        self._ensure_configured()
        started = time.monotonic()
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                auth=(self.username, self.password),
                timeout=self.timeout,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                **kwargs,
            )
        except requests.Timeout as exc:
            raise UpyaTimeout("Upya request timed out.") from exc
        except requests.RequestException as exc:
            raise UpyaBadResponse("Upya request failed.") from exc
        finally:
            logger.info("upya_payment_gateway_request path=%s latency_ms=%d", path, int((time.monotonic() - started) * 1000))
        if response.status_code in (401, 403):
            raise UpyaAuthenticationError("Upya Payment Gateway rejected the credentials.")
        if response.status_code == 400:
            raise UpyaCustomerNotFound("Upya could not resolve the customer reference.")
        if not response.ok:
            raise UpyaBadResponse(f"Upya returned HTTP {response.status_code}.")
        try:
            return response.json()
        except ValueError as exc:
            raise UpyaBadResponse("Upya returned invalid JSON.") from exc

    def get_payment_options(self, *, subscriber=None, reference=None):
        if bool(subscriber) == bool(reference):
            raise ValueError("Provide exactly one of subscriber or reference.")
        payload = {"subscriber": subscriber} if subscriber else {"reference": reference}
        raw = self._request("POST", f"/api/payment/options/{quote(self.client_identifier, safe='')}", json=payload)
        items = raw.get("data", raw) if isinstance(raw, dict) and "data" in raw else raw
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            raise UpyaBadResponse("Upya payment options had an unexpected shape.")
        try:
            return [UpyaPaymentOption.from_payload(item) for item in items]
        except ValueError as exc:
            raise UpyaBadResponse(str(exc)) from exc

    def get_customer_details(self, reference):
        raw = self._request("GET", "/api/customer/details", params={"reference": reference})
        contract = raw.get("contract") or {}
        client = raw.get("client") or {}
        profile, contact = client.get("profile") or {}, client.get("contact") or {}
        schedule, asset = contract.get("schedule") or {}, contract.get("asset") or {}
        number = contract.get("contractNumber")
        if not number:
            raise UpyaBadResponse("Upya customer details omitted contractNumber.")
        try:
            frequency = int(schedule["freq"]) if schedule.get("freq") is not None else None
        except (TypeError, ValueError):
            frequency = None
        return UpyaCustomerDetails(
            contract_number=str(number), first_name=str(profile.get("firstName") or ""),
            last_name=str(profile.get("lastName") or ""), mobile=str(contact.get("mobile") or ""),
            total_paid=optional_decimal(contract.get("totalPaid")), total_cost=optional_decimal(contract.get("totalCost")),
            recurring_payment=optional_decimal(schedule.get("recurringPayment")), frequency=frequency,
            product=str(asset.get("product") or ""), serial_number=str(asset.get("serialNumber") or ""),
        )

    def get_last_payments(self, reference, limit=1):
        raw = self._request("GET", "/api/customer/lastPayment", params={"reference": reference, "limit": limit})
        payments = raw.get("payments") or []
        if not isinstance(payments, list):
            raise UpyaBadResponse("Upya payments had an unexpected shape.")
        result = []
        for item in payments:
            message = item.get("message") or ""
            if isinstance(message, dict):
                message = message.get("text") or ""
            result.append(UpyaPaymentRecord(
                transaction_id=str(item.get("transactionId") or ""), amount=optional_decimal(item.get("amount")),
                currency=str(item.get("ccy") or ""), date=str(item.get("date") or ""), message=str(message),
            ))
        return result

    def check_payment_status(self, payload):
        if not isinstance(payload, dict) or not payload:
            raise ValueError("A documented Upya status payload is required.")
        return self._request("POST", "/api/payment/checkStatus", json=payload)
