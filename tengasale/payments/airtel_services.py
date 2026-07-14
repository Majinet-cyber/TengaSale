from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import string
from decimal import Decimal, InvalidOperation
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from portal.models import PaymentTransaction
from portal.services import apply_payment_to_contract, finalize_paid_transaction_commission

from .airtel_client import AirtelClient, AirtelConfig
from .models import AirtelCallbackLog, AirtelTransaction

logger = logging.getLogger(__name__)

SUCCESS_VALUES = {"TS", "SUCCESS", "SUCCESSFUL", "TRANSACTION SUCCESSFUL"}
PENDING_VALUES = {"TIP", "PENDING", "IN_PROGRESS"}
EXPIRED_VALUES = {"TE", "EXPIRED"}
FAILED_VALUES = {"FAILED", "TF", "DECLINED", "REJECTED"}
TERMINAL_FAILURE_STATUSES = {AirtelTransaction.STATUS_FAILED, AirtelTransaction.STATUS_EXPIRED}


def normalize_airtel_status(value: Any) -> str:
    status = str(value or "").strip().upper()
    if status in SUCCESS_VALUES:
        return AirtelTransaction.STATUS_SUCCESS
    if status in PENDING_VALUES:
        return AirtelTransaction.STATUS_PENDING
    if status in EXPIRED_VALUES:
        return AirtelTransaction.STATUS_EXPIRED
    if status in FAILED_VALUES:
        return AirtelTransaction.STATUS_FAILED
    return AirtelTransaction.STATUS_UNKNOWN


def normalize_malawi_msisdn(value: str) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if digits.startswith("265"):
        digits = digits[3:]
    digits = digits.lstrip("0")
    if len(digits) != 9:
        raise ValueError("Enter a valid Malawi mobile money number.")
    return f"+265{digits}"


def _airtel_api_msisdn(value: str) -> str:
    return normalize_malawi_msisdn(value).lstrip("+")


def generate_airtel_reference() -> str:
    chars = string.ascii_uppercase + string.digits
    for _ in range(100):
        suffix = "".join(secrets.choice(chars) for _ in range(8))
        ref = f"TENGA-AIRTEL-{timezone.now().strftime('%Y%m%d')}-{suffix}"
        if not AirtelTransaction.objects.filter(internal_reference=ref).exists():
            return ref
    return f"TENGA-AIRTEL-{secrets.token_hex(8).upper()}"


def _extract_nested(data: Any, *paths: str) -> Any:
    for path in paths:
        current = data
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                current = None
                break
            current = current[part]
        if current not in (None, ""):
            return current
    return None


def _canonical_callback_payload(data: dict[str, Any]) -> bytes:
    payload = dict(data)
    payload.pop("hash", None)
    payload.pop("Hash", None)
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    sensitive = {"authorization", "x-api-key", "api-key", "cookie"}
    sanitized = {}
    for key, value in headers.items():
        sanitized[key] = "***" if str(key).lower() in sensitive else value
    return sanitized


def _collect_reference_values(data: dict[str, Any]) -> set[str]:
    values = set()
    names = {
        "internal_reference",
        "reference",
        "reference_id",
        "airtel_money_id",
        "airtel_transaction_id",
        "transaction_id",
        "id",
        "tx_ref",
    }

    def walk(obj: Any):
        if isinstance(obj, dict):
            for key, value in obj.items():
                lower = str(key).lower()
                if lower in names and value not in (None, ""):
                    values.add(str(value))
                walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)
    return values


def extract_airtel_status(data: dict[str, Any]) -> str:
    value = _extract_nested(
        data,
        "data.transaction.status_code",
        "data.transaction.status",
        "transaction.status_code",
        "transaction.status",
        "status.status",
        "status.code",
        "status",
    )
    message = _extract_nested(data, "data.transaction.message", "transaction.message", "status.message", "message")
    normalized = normalize_airtel_status(value)
    if normalized == AirtelTransaction.STATUS_UNKNOWN and message:
        normalized = normalize_airtel_status(message)
    return normalized


def extract_airtel_references(data: dict[str, Any]) -> dict[str, str]:
    return {
        "provider_reference": str(_extract_nested(data, "data.transaction.reference_id", "transaction.reference_id") or ""),
        "airtel_money_id": str(_extract_nested(data, "data.transaction.airtel_money_id", "transaction.airtel_money_id") or ""),
        "airtel_transaction_id": str(_extract_nested(data, "data.transaction.id", "transaction.id", "id") or ""),
        "airtel_reference_id": str(_extract_nested(data, "data.transaction.reference_id", "transaction.reference_id", "reference_id") or ""),
    }


def extract_airtel_amount(data: dict[str, Any]) -> Decimal | None:
    value = _extract_nested(
        data,
        "data.transaction.amount",
        "data.transaction.transaction_amount",
        "transaction.amount",
        "transaction.transaction_amount",
        "amount",
    )
    if value in (None, ""):
        message = str(_extract_nested(data, "data.transaction.message", "transaction.message", "message") or "")
        match = re.search(r"(?i)\bMWK\s*([0-9][0-9,]*(?:\.\d+)?)", message)
        if match:
            value = match.group(1).replace(",", "")
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", "")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


class AirtelCollectionService:
    def __init__(self, client: AirtelClient | None = None):
        self.client = client or AirtelClient()

    @transaction.atomic
    def initiate_collection_payment(self, msisdn, amount, purpose, contract=None, customer=None) -> AirtelTransaction:
        try:
            amount = Decimal(str(amount)).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            raise ValueError("Enter a valid MWK amount.")
        if amount <= 0:
            raise ValueError("Amount must be greater than zero.")

        purpose = str(purpose or AirtelTransaction.PURPOSE_TEST).upper()
        valid_purposes = {choice[0] for choice in AirtelTransaction.PURPOSE_CHOICES}
        if purpose not in valid_purposes:
            raise ValueError("Unsupported Airtel payment purpose.")

        normalized_msisdn = normalize_malawi_msisdn(msisdn)
        internal_reference = generate_airtel_reference()
        payload = {
            "reference": internal_reference,
            "subscriber": {
                "country": self.client.config.country,
                "currency": self.client.config.currency,
                "msisdn": _airtel_api_msisdn(normalized_msisdn),
            },
            "transaction": {
                "amount": str(amount),
                "country": self.client.config.country,
                "currency": self.client.config.currency,
                "id": internal_reference,
            },
        }

        airtel_tx = AirtelTransaction.objects.create(
            internal_reference=internal_reference,
            customer_msisdn=normalized_msisdn,
            amount=amount,
            currency=self.client.config.currency,
            purpose=purpose,
            direction=AirtelTransaction.DIRECTION_COLLECTION,
            status=AirtelTransaction.STATUS_INITIATED,
            raw_request=payload,
            contract=contract,
            customer=customer,
        )

        if not self.client.is_configured_for_api_calls:
            airtel_tx.status = AirtelTransaction.STATUS_PENDING
            airtel_tx.raw_response = {
                "local_only": True,
                "message": "AIRTEL_AUTH_TOKEN is not configured; transaction was created locally.",
            }
            airtel_tx.save(update_fields=["status", "raw_response", "updated_at"])
            return airtel_tx

        try:
            response = self.client.post(self.client.config.collection_path, payload)
        except Exception as exc:
            logger.exception("Airtel collection API failed for %s", internal_reference)
            airtel_tx.status = AirtelTransaction.STATUS_PENDING
            airtel_tx.raw_response = {"error": str(exc)}
            airtel_tx.save(update_fields=["status", "raw_response", "updated_at"])
            return airtel_tx

        body = response.get("body", {})
        refs = extract_airtel_references(body if isinstance(body, dict) else {})
        airtel_tx.raw_response = response
        airtel_tx.status = extract_airtel_status(body) if isinstance(body, dict) else AirtelTransaction.STATUS_UNKNOWN
        if airtel_tx.status == AirtelTransaction.STATUS_UNKNOWN and response.get("ok"):
            airtel_tx.status = AirtelTransaction.STATUS_PENDING
        for field, value in refs.items():
            if value:
                setattr(airtel_tx, field, value)
        airtel_tx.save()
        return airtel_tx


class AirtelTransactionEnquiryService:
    def __init__(self, client: AirtelClient | None = None):
        self.client = client or AirtelClient()

    @transaction.atomic
    def enquire(self, internal_reference: str) -> AirtelTransaction:
        airtel_tx = AirtelTransaction.objects.select_for_update().get(internal_reference=internal_reference)
        if not self.client.is_configured_for_api_calls:
            return airtel_tx
        reference = airtel_tx.airtel_transaction_id or airtel_tx.airtel_reference_id or airtel_tx.internal_reference
        path = self.client.config.enquiry_path_template.format(reference=reference)
        response = self.client.get(path)
        body = response.get("body", {})
        airtel_tx.raw_response = response
        if isinstance(body, dict):
            airtel_tx.status = extract_airtel_status(body)
            refs = extract_airtel_references(body)
            for field, value in refs.items():
                if value:
                    setattr(airtel_tx, field, value)
        airtel_tx.save()
        if airtel_tx.status == AirtelTransaction.STATUS_SUCCESS:
            AirtelCallbackService().apply_success(airtel_tx)
        return airtel_tx


class AirtelCallbackService:
    signature_headers = (
        "X-Airtel-Signature",
        "X-Signature",
        "Signature",
        "X-Callback-Signature",
        "Hash",
        "X-Hash",
    )

    def __init__(self, config: AirtelConfig | None = None):
        self.config = config or AirtelConfig.from_settings()

    def verify_signature(self, raw_body: bytes, headers: dict[str, str], parsed: dict[str, Any] | None = None) -> tuple[bool, bool]:
        if not self.config.callback_auth_enabled:
            return True, False
        provided = ""
        lower_headers = {str(k).lower(): str(v).strip() for k, v in headers.items()}
        for name in self.signature_headers:
            provided = lower_headers.get(name.lower(), "")
            if provided:
                break
        if not provided and isinstance(parsed, dict):
            provided = str(parsed.get("hash") or parsed.get("Hash") or "").strip()
        if not provided or not self.config.callback_hash_key:
            return False, False

        raw_digest = hmac.new(self.config.callback_hash_key.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        if hmac.compare_digest(provided.lower(), raw_digest.lower()):
            return True, True

        if isinstance(parsed, dict):
            canonical_digest = hmac.new(
                self.config.callback_hash_key.encode("utf-8"),
                _canonical_callback_payload(parsed),
                hashlib.sha256,
            ).hexdigest()
            if hmac.compare_digest(provided.lower(), canonical_digest.lower()):
                return True, True
        return False, True

    @transaction.atomic
    def handle_callback(self, raw_body: bytes, headers: dict[str, str]) -> tuple[AirtelCallbackLog, bool, int]:
        max_bytes = int(getattr(settings, "AIRTEL_CALLBACK_MAX_BYTES", 65536))
        safe_headers = _sanitize_headers(headers)
        if len(raw_body or b"") > max_bytes:
            log = AirtelCallbackLog.objects.create(
                received_headers=safe_headers,
                raw_body="",
                parsed_body=None,
                processing_error="Airtel callback body exceeds configured size limit.",
            )
            return log, False, 400

        try:
            parsed = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            log = AirtelCallbackLog.objects.create(
                received_headers=safe_headers,
                raw_body=raw_body.decode("utf-8", errors="replace"),
                parsed_body=None,
                processing_error=f"Invalid JSON: {exc}",
            )
            return log, False, 400

        if not isinstance(parsed, dict) or not isinstance(_extract_nested(parsed, "data.transaction", "transaction"), dict):
            log = AirtelCallbackLog.objects.create(
                received_headers=safe_headers,
                raw_body=raw_body.decode("utf-8", errors="replace"),
                parsed_body=parsed if isinstance(parsed, dict) else None,
                processing_error="Missing Airtel transaction object.",
            )
            return log, False, 400

        signature_valid, signature_found = self.verify_signature(raw_body, headers, parsed)
        log = AirtelCallbackLog.objects.create(
            received_headers=safe_headers,
            raw_body=raw_body.decode("utf-8", errors="replace"),
            parsed_body=parsed,
            signature_valid=signature_valid,
        )

        if self.config.callback_auth_enabled and not signature_valid:
            log.processing_error = "Invalid or missing Airtel callback signature."
            if not signature_found:
                log.processing_error = "Missing Airtel callback signature."
            log.save(update_fields=["processing_error"])
            return log, True, 401

        airtel_tx = self.find_transaction(parsed)
        if not airtel_tx:
            log.processing_error = "No matching Airtel transaction found."
            log.save(update_fields=["processing_error"])
            return log, True, 200

        log.transaction = airtel_tx
        status = extract_airtel_status(parsed)
        refs = extract_airtel_references(parsed)
        duplicate = airtel_tx.status == AirtelTransaction.STATUS_SUCCESS and bool(airtel_tx.processed_success_at)

        callback_amount = extract_airtel_amount(parsed)
        if callback_amount is not None and callback_amount != airtel_tx.amount:
            log.processing_error = "Airtel callback amount does not match original payment attempt."
            log.save(update_fields=["transaction", "processing_error"])
            airtel_tx.failure_reason = "Callback amount mismatch."
            airtel_tx.raw_callback = parsed
            airtel_tx.callback_verified = signature_valid
            airtel_tx.callback_received_at = timezone.now()
            airtel_tx.save(update_fields=["failure_reason", "raw_callback", "callback_verified", "callback_received_at", "updated_at"])
            return log, True, 400

        airtel_tx.raw_callback = parsed
        airtel_tx.callback_verified = signature_valid
        airtel_tx.callback_received_at = timezone.now()
        airtel_tx.status = status
        if status in TERMINAL_FAILURE_STATUSES:
            airtel_tx.failure_reason = str(
                _extract_nested(parsed, "data.transaction.message", "transaction.message", "message", "status.message")
                or "Airtel transaction failed."
            )[:500]
            airtel_tx.completed_at = timezone.now()
        for field, value in refs.items():
            if value:
                setattr(airtel_tx, field, value)
        airtel_tx.save()

        if duplicate:
            log.duplicate = True
            log.processed = True
            log.save(update_fields=["transaction", "duplicate", "processed"])
            return log, True, 200

        if status == AirtelTransaction.STATUS_SUCCESS:
            self.apply_success(airtel_tx)

        log.processed = True
        log.save(update_fields=["transaction", "processed"])
        return log, True, 200

    def find_transaction(self, payload: dict[str, Any]) -> AirtelTransaction | None:
        values = _collect_reference_values(payload)
        if not values:
            return None
        query = Q()
        for value in values:
            query |= (
                Q(internal_reference=value)
                | Q(provider_reference=value)
                | Q(airtel_money_id=value)
                | Q(airtel_transaction_id=value)
                | Q(airtel_reference_id=value)
            )
        return AirtelTransaction.objects.select_related("contract", "payment_transaction").filter(query).first()

    @transaction.atomic
    def apply_success(self, airtel_tx: AirtelTransaction) -> AirtelTransaction:
        airtel_tx = AirtelTransaction.objects.select_for_update().select_related("contract", "payment_transaction").get(
            pk=airtel_tx.pk
        )
        if airtel_tx.processed_success_at:
            return airtel_tx
        contract = airtel_tx.contract
        if not contract:
            airtel_tx.processed_success_at = timezone.now()
            airtel_tx.completed_at = airtel_tx.processed_success_at
            airtel_tx.processing_note = "Success received; no linked contract to credit."
            airtel_tx.save(update_fields=["processed_success_at", "completed_at", "processing_note", "updated_at"])
            return airtel_tx

        payment_type = PaymentTransaction.TYPE_REPAYMENT
        if airtel_tx.purpose == AirtelTransaction.PURPOSE_DEPOSIT:
            payment_type = PaymentTransaction.TYPE_DEPOSIT
            if getattr(contract, "deposit_access_days", 0) < 14:
                contract.deposit_access_days = 14
                contract.save(update_fields=["deposit_access_days"])

        portal_tx = airtel_tx.payment_transaction
        if not portal_tx:
            portal_tx = PaymentTransaction.objects.create(
                payment_contract=contract,
                provider=PaymentTransaction.PROVIDER_AIRTEL,
                payment_type=payment_type,
                amount=airtel_tx.amount,
                commissionable_amount=Decimal("0") if payment_type == PaymentTransaction.TYPE_DEPOSIT else airtel_tx.amount,
                currency=airtel_tx.currency,
                phone=airtel_tx.customer_msisdn,
                network=PaymentTransaction.NETWORK_AIRTEL,
                internal_reference=airtel_tx.internal_reference[:30],
                provider_reference=airtel_tx.airtel_money_id or airtel_tx.airtel_transaction_id or airtel_tx.provider_reference or "",
                status=PaymentTransaction.STATUS_PAID,
                raw_request=airtel_tx.raw_request,
                raw_response=airtel_tx.raw_response,
                webhook_payload=airtel_tx.raw_callback,
                initiated_at=airtel_tx.created_at,
                paid_at=timezone.now(),
                balance_before=contract.deposit_remaining if payment_type == PaymentTransaction.TYPE_DEPOSIT else contract.remaining_amount,
            )
            airtel_tx.payment_transaction = portal_tx
        elif portal_tx.status != PaymentTransaction.STATUS_PAID:
            portal_tx.status = PaymentTransaction.STATUS_PAID
            portal_tx.paid_at = timezone.now()
            portal_tx.webhook_payload = airtel_tx.raw_callback
            portal_tx.raw_response = airtel_tx.raw_response
            portal_tx.save(update_fields=["status", "paid_at", "webhook_payload", "raw_response", "updated_at"])

        result = apply_payment_to_contract(contract, airtel_tx.amount, payment_type=payment_type)
        portal_tx.balance_after = contract.deposit_remaining if payment_type == PaymentTransaction.TYPE_DEPOSIT else contract.remaining_amount
        portal_tx.save(update_fields=["balance_after", "updated_at"])
        try:
            finalize_paid_transaction_commission(portal_tx)
        except Exception:
            logger.exception("Airtel commission finalization failed for %s", airtel_tx.internal_reference)
        airtel_tx.processed_success_at = timezone.now()
        airtel_tx.completed_at = airtel_tx.processed_success_at
        airtel_tx.processing_note = f"Applied to contract {contract.contract_number}; days_extended={result.get('days_extended', 0)}"
        airtel_tx.save(update_fields=["payment_transaction", "processed_success_at", "completed_at", "processing_note", "updated_at"])
        return airtel_tx


class AirtelDisbursementService:
    def __init__(self, client: AirtelClient | None = None):
        self.client = client or AirtelClient()

    def create_disbursement(self, payload: dict[str, Any], *, approved: bool = False) -> dict[str, Any]:
        if not approved or not getattr(settings, "PAYOUTS_ALLOW_LIVE_DISBURSEMENTS", False):
            return {"ok": False, "message": "Airtel disbursements are disabled until explicit payout approval is enabled."}
        return self.client.post(self.client.config.disbursement_path, payload)


class AirtelMerchantRegistrationService:
    """Reserved for Airtel merchant registration workflows when Airtel enables them."""

    def __init__(self, client: AirtelClient | None = None):
        self.client = client or AirtelClient()
