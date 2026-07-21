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

from .airtel_client import AirtelClient, AirtelConfig, AirtelConfigurationError
from .mobile_network import PROVIDER_AIRTEL, normalize_malawi_msisdn as normalize_malawi_number
from .models import AirtelCallbackLog, AirtelEnquiryLog, AirtelTransaction
from .callback_diagnostics import candidate_suggestions

logger = logging.getLogger(__name__)

SUCCESS_VALUES = {"TS", "SUCCESS", "SUCCESSFUL", "TRANSACTION SUCCESSFUL"}
PENDING_VALUES = {"TIP", "PENDING", "IN PROGRESS"}
EXPIRED_VALUES = {"TE", "EXPIRED"}
FAILED_VALUES = {"FAILED", "TF", "DECLINED", "REJECTED"}
TERMINAL_FAILURE_STATUSES = {AirtelTransaction.STATUS_FAILED, AirtelTransaction.STATUS_EXPIRED}


def normalize_airtel_status(value: Any) -> str:
    status = re.sub(r"[^A-Z0-9]+", " ", str(value or "").strip().upper()).strip()
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
    normalized = normalize_malawi_number(value)
    if not normalized.valid or normalized.provider != PROVIDER_AIRTEL:
        raise ValueError("Enter a valid Malawi mobile money number.")
    return normalized.international


def mask_msisdn(value: str) -> str:
    try:
        normalized = normalize_malawi_msisdn(value)
    except ValueError:
        return "***"
    return f"{normalized[:6]}***{normalized[-4:]}"


def _airtel_api_msisdn(value: str) -> str:
    # Airtel Malawi collections expect the national nine-digit number.
    return normalize_malawi_msisdn(value)[-9:]


def _allowed_test_msisdns(config: AirtelConfig) -> set[str]:
    normalized = set()
    for value in config.allowed_test_msisdns:
        try:
            normalized.add(normalize_malawi_msisdn(value))
        except ValueError:
            continue
    return normalized


def generate_airtel_reference() -> str:
    chars = string.ascii_uppercase + string.digits
    for _ in range(100):
        suffix = "".join(secrets.choice(chars) for _ in range(8))
        ref = f"TENGAAIRTEL{timezone.now().strftime('%Y%m%d')}{suffix}"
        if not AirtelTransaction.objects.filter(internal_reference=ref).exists():
            return ref
    return f"TENGAAIRTEL{secrets.token_hex(12).upper()}"[:64]


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
        "provider_reference": str(_extract_nested(data, "data.transaction.provider_reference", "transaction.provider_reference", "data.transaction.transaction_reference", "transaction.transaction_reference") or ""),
        "airtel_money_id": str(_extract_nested(data, "data.transaction.airtel_money_id", "transaction.airtel_money_id") or ""),
        "airtel_transaction_id": str(_extract_nested(data, "data.transaction.airtel_transaction_id", "transaction.airtel_transaction_id", "data.transaction.provider_transaction_id", "transaction.provider_transaction_id") or ""),
        "airtel_reference_id": str(_extract_nested(data, "data.transaction.reference_id", "transaction.reference_id", "reference_id") or ""),
    }


def store_confirmed_provider_references(airtel_tx: AirtelTransaction, data: dict[str, Any]) -> list[str]:
    """Store only identifiers returned in explicitly provider-owned fields."""
    refs = extract_airtel_references(data)
    changed = []
    sources = []
    for field, value in refs.items():
        if value and value != airtel_tx.internal_reference:
            setattr(airtel_tx, field, value)
            changed.append(field)
            sources.append(field)
    if sources:
        airtel_tx.provider_id_confirmed = True
        airtel_tx.provider_identifier_source = ",".join(sources)
        changed.extend(["provider_id_confirmed", "provider_identifier_source"])
    return changed


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


def extract_airtel_subscriber(data: dict[str, Any]) -> str:
    value = _extract_nested(data, "data.transaction.subscriber", "transaction.subscriber", "data.transaction.msisdn", "transaction.msisdn", "subscriber", "msisdn")
    if value:
        digits = re.sub(r"\D", "", str(value))
        return digits[-9:] if len(digits) >= 9 else ""
    message = str(_extract_nested(data, "data.transaction.message", "transaction.message", "message") or "")
    matches = re.findall(r"(?<!\d)(?:\+?265|0)?([89]\d{8})(?!\d)", message)
    return matches[-1] if matches else ""


def extract_payload_transaction_id(data: dict[str, Any]) -> str:
    return str(_extract_nested(data, "data.transaction.id", "transaction.id") or "")


class AirtelCollectionService:
    def __init__(self, client: AirtelClient | None = None):
        self.client = client or AirtelClient()

    @transaction.atomic
    def initiate_collection_payment(self, msisdn, amount, purpose, contract=None, customer=None, idempotency_key=None) -> AirtelTransaction:
        logger.info("airtel_initiation_started contract_id=%s environment=%s", getattr(contract, "pk", None), self.client.config.environment)
        self.client.config.assert_production_allowed()
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
        idempotency_key = str(idempotency_key or "").strip()[:120] or None
        if idempotency_key:
            existing = AirtelTransaction.objects.filter(idempotency_key=idempotency_key).first()
            if existing:
                return existing
        if contract is not None:
            contract = contract.__class__.objects.select_for_update().get(pk=contract.pk)
            remaining = contract.deposit_remaining if purpose == AirtelTransaction.PURPOSE_DEPOSIT else contract.remaining_amount
            if remaining <= Decimal("0"):
                raise ValueError("Contract fully paid. No payment is currently required.")
            blocked_statuses = {
                "cancelled",
                "repossession_pending",
                "repossessed",
                "ready_for_resale",
                "resold",
                "written_off",
                "legally_closed",
            }
            if contract.status in blocked_statuses:
                raise ValueError("This contract cannot currently accept payments.")
            if amount > remaining:
                raise ValueError(f"Payment amount exceeds the outstanding balance of MWK {remaining}.")
            duplicate = AirtelTransaction.objects.filter(
                contract=contract,
                direction=AirtelTransaction.DIRECTION_COLLECTION,
                status__in=[AirtelTransaction.STATUS_INITIATED, AirtelTransaction.STATUS_PENDING],
                processed_success_at__isnull=True,
                purpose=purpose,
                amount=amount,
            ).order_by("-created_at").first()
            if duplicate:
                return duplicate
        if not self.client.config.collections_enabled:
            raise ValueError("Airtel collections are disabled by configuration.")
        if self.client.config.environment == "staging":
            try:
                max_amount = Decimal(str(self.client.config.test_max_amount)).quantize(Decimal("0.01"))
                min_amount = Decimal(str(self.client.config.test_min_amount)).quantize(Decimal("0.01"))
            except (InvalidOperation, ValueError):
                raise AirtelConfigurationError("Airtel staging test amount limits must be valid MWK amounts.")
            if amount < min_amount:
                raise ValueError(f"Airtel staging payments must be at least MWK {min_amount:,.0f}.")
            if amount > max_amount:
                raise ValueError(f"Staging Airtel collections are limited to MWK {max_amount}.")
            allowed = _allowed_test_msisdns(self.client.config)
            if not allowed:
                raise ValueError("Airtel staging test MSISDN allowlist is not configured.")
            if normalized_msisdn not in allowed:
                raise ValueError("Airtel staging test MSISDN is not allowlisted.")

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
            environment=self.client.config.environment,
            customer_msisdn=normalized_msisdn,
            amount=amount,
            currency=self.client.config.currency,
            purpose=purpose,
            direction=AirtelTransaction.DIRECTION_COLLECTION,
            status=AirtelTransaction.STATUS_INITIATED,
            idempotency_key=idempotency_key,
            raw_request=payload,
            contract=contract,
            customer=customer,
        )
        if contract is not None:
            payment_type = (
                PaymentTransaction.TYPE_DEPOSIT
                if purpose == AirtelTransaction.PURPOSE_DEPOSIT
                else PaymentTransaction.TYPE_REPAYMENT
            )
            portal_tx = PaymentTransaction.objects.create(
                payment_contract=contract,
                provider=PaymentTransaction.PROVIDER_AIRTEL,
                payment_type=payment_type,
                amount=amount,
                commissionable_amount=Decimal("0") if payment_type == PaymentTransaction.TYPE_DEPOSIT else amount,
                currency=self.client.config.currency,
                phone=normalized_msisdn,
                network=PaymentTransaction.NETWORK_AIRTEL,
                balance_before=contract.deposit_remaining if payment_type == PaymentTransaction.TYPE_DEPOSIT else contract.remaining_amount,
                status=PaymentTransaction.STATUS_PENDING,
                raw_request=payload,
                initiated_at=timezone.now(),
            )
            airtel_tx.payment_transaction = portal_tx
            airtel_tx.save(update_fields=["payment_transaction", "updated_at"])

        if self.client.config.dry_run:
            airtel_tx.status = AirtelTransaction.STATUS_DRY_RUN
            airtel_tx.raw_response = {
                "dry_run": True,
                "message": "AIRTEL_DRY_RUN is enabled; request was validated and not sent to Airtel.",
            }
            airtel_tx.initiation_response = airtel_tx.raw_response
            airtel_tx.save(update_fields=["status", "raw_response", "initiation_response", "updated_at"])
            logger.info(
                "Airtel dry-run collection created ref=%s msisdn=%s amount=%s",
                internal_reference,
                mask_msisdn(normalized_msisdn),
                amount,
            )
            return airtel_tx

        if not self.client.is_configured_for_api_calls:
            airtel_tx.status = AirtelTransaction.STATUS_PENDING
            airtel_tx.raw_response = {
                "local_only": True,
                "message": "AIRTEL_AUTH_TOKEN is not configured; transaction was created locally.",
            }
            airtel_tx.initiation_response = airtel_tx.raw_response
            airtel_tx.save(update_fields=["status", "raw_response", "initiation_response", "updated_at"])
            return airtel_tx

        try:
            response = self.client.post(self.client.config.collection_path, payload)
        except Exception as exc:
            logger.warning("Airtel collection API failed for %s: %s", internal_reference, exc.__class__.__name__)
            airtel_tx.status = AirtelTransaction.STATUS_PENDING
            airtel_tx.raw_response = {"error": exc.__class__.__name__}
            airtel_tx.initiation_response = airtel_tx.raw_response
            airtel_tx.save(update_fields=["status", "raw_response", "initiation_response", "updated_at"])
            return airtel_tx

        body = response.get("body", {})
        airtel_tx.raw_response = response
        airtel_tx.initiation_response = response
        airtel_tx.initiation_http_status = response.get("http_status")
        airtel_tx.status = extract_airtel_status(body) if isinstance(body, dict) else AirtelTransaction.STATUS_UNKNOWN
        business_code = _extract_nested(body, "status.code", "data.status.code") if isinstance(body, dict) else None
        business_message = str(_extract_nested(body, "status.message", "message") or "") if isinstance(body, dict) else ""
        business_failed = not response.get("ok")
        try:
            business_failed = business_failed or int(str(business_code)) >= 400
        except (TypeError, ValueError):
            pass
        if business_failed:
            airtel_tx.status = AirtelTransaction.STATUS_FAILED
            airtel_tx.failure_reason = "Airtel Money could not process this payment request."
            if "agent" in business_message.lower() or "merchant" in business_message.lower():
                airtel_tx.processing_note = "Provider configuration error reported during collection initiation."
            logger.warning("airtel_initiation_rejected internal_reference=%s environment=%s", internal_reference, airtel_tx.environment)
        if airtel_tx.status == AirtelTransaction.STATUS_UNKNOWN:
            airtel_tx.status = AirtelTransaction.STATUS_PENDING
        if airtel_tx.status == AirtelTransaction.STATUS_SUCCESS:
            airtel_tx.status = AirtelTransaction.STATUS_PENDING
            airtel_tx.processing_note = "Collection request accepted; awaiting callback or enquiry confirmation."
        if not business_failed:
            airtel_tx.initiation_accepted_at = timezone.now()
            if not airtel_tx.processing_note:
                airtel_tx.processing_note = "Collection request accepted; awaiting customer confirmation."
        if isinstance(body, dict):
            store_confirmed_provider_references(airtel_tx, body)
        airtel_tx.save()
        if not business_failed:
            logger.info("airtel_initiation_accepted internal_reference=%s contract_id=%s environment=%s", internal_reference, airtel_tx.contract_id, airtel_tx.environment)
        if airtel_tx.status == AirtelTransaction.STATUS_FAILED and airtel_tx.payment_transaction:
            airtel_tx.payment_transaction.status = PaymentTransaction.STATUS_FAILED
            airtel_tx.payment_transaction.save(update_fields=["status", "updated_at"])
        return airtel_tx


class AirtelTransactionEnquiryService:
    def __init__(self, client: AirtelClient | None = None):
        self.client = client or AirtelClient()

    @transaction.atomic
    def enquire(self, internal_reference: str) -> AirtelTransaction:
        logger.info("airtel_enquiry_started internal_reference=%s", internal_reference)
        airtel_tx = AirtelTransaction.objects.select_for_update().get(internal_reference=internal_reference)
        min_interval = int(getattr(settings, "AIRTEL_ENQUIRY_MIN_INTERVAL_SECONDS", 30))
        max_attempts = int(getattr(settings, "AIRTEL_ENQUIRY_MAX_ATTEMPTS", 20))
        if airtel_tx.enquiry_attempt_count >= max_attempts:
            airtel_tx.reconciliation_required = True
            airtel_tx.last_enquiry_error = "Enquiry attempt limit reached; operator review required."
            airtel_tx.save(update_fields=["reconciliation_required", "last_enquiry_error", "updated_at"])
            AirtelEnquiryLog.objects.create(transaction=airtel_tx, provider_status=AirtelTransaction.STATUS_UNKNOWN, error_class="AttemptLimit", error_message=airtel_tx.last_enquiry_error)
            return airtel_tx
        if airtel_tx.last_enquiry_at and (timezone.now() - airtel_tx.last_enquiry_at).total_seconds() < min_interval:
            AirtelEnquiryLog.objects.create(transaction=airtel_tx, provider_status=AirtelTransaction.STATUS_UNKNOWN, error_class="Throttled", error_message="Enquiry skipped by minimum interval protection.")
            return airtel_tx
        airtel_tx.last_enquiry_at = timezone.now()
        airtel_tx.enquiry_attempt_count += 1
        if not self.client.is_configured_for_api_calls:
            airtel_tx.last_enquiry_error = "Airtel API credentials are not configured."
            airtel_tx.save(update_fields=["last_enquiry_at", "enquiry_attempt_count", "last_enquiry_error", "updated_at"])
            AirtelEnquiryLog.objects.create(transaction=airtel_tx, error_class="ConfigurationError", error_message=airtel_tx.last_enquiry_error)
            return airtel_tx
        candidates = [
            airtel_tx.airtel_money_id,
            airtel_tx.airtel_transaction_id if airtel_tx.provider_id_confirmed else "",
            airtel_tx.provider_reference if airtel_tx.provider_id_confirmed else "",
            airtel_tx.airtel_reference_id,
        ]
        reference = next((value for value in candidates if value), "")
        if not reference and bool(getattr(settings, "AIRTEL_ALLOW_MERCHANT_REFERENCE_ENQUIRY", getattr(settings, "AIRTEL_ENQUIRY_SUPPORTS_MERCHANT_REFERENCE", False))):
            reference = airtel_tx.internal_reference
        if not reference:
            airtel_tx.last_enquiry_status = AirtelTransaction.STATUS_UNKNOWN
            airtel_tx.last_enquiry_error = "No confirmed provider identifier is available for enquiry."
            airtel_tx.reconciliation_required = True
            airtel_tx.processing_note = "Provider enquiry unavailable; existing status preserved."
            airtel_tx.save()
            AirtelEnquiryLog.objects.create(transaction=airtel_tx, provider_status=AirtelTransaction.STATUS_UNKNOWN, error_class="MissingProviderIdentifier", error_message=airtel_tx.last_enquiry_error)
            return airtel_tx
        path = self.client.config.enquiry_path_template.format(reference=reference)
        airtel_tx.last_enquiry_reference = reference
        airtel_tx.last_enquiry_path = path
        try:
            response = self.client.get(path)
        except Exception as exc:
            airtel_tx.last_enquiry_error = f"{exc.__class__.__name__}: {exc}"[:1000]
            airtel_tx.reconciliation_required = True
            airtel_tx.save()
            AirtelEnquiryLog.objects.create(transaction=airtel_tx, reference=reference, path=path, error_class=exc.__class__.__name__, error_message=str(exc)[:1000])
            logger.exception("enquiry_failed internal_reference=%s", airtel_tx.internal_reference)
            return airtel_tx
        body = response.get("body", {})
        airtel_tx.last_enquiry_response = response
        airtel_tx.last_enquiry_http_status = response.get("http_status")
        enquiry_status = AirtelTransaction.STATUS_UNKNOWN
        if isinstance(body, dict):
            enquiry_status = extract_airtel_status(body)
            airtel_tx.last_enquiry_status = enquiry_status
            airtel_tx.last_enquiry_error = "" if enquiry_status != AirtelTransaction.STATUS_UNKNOWN else str(_extract_nested(body, "status.message", "message") or "Unrecognised provider status")[:1000]
            store_confirmed_provider_references(airtel_tx, body)
            if enquiry_status != AirtelTransaction.STATUS_UNKNOWN:
                airtel_tx.status = enquiry_status
            else:
                airtel_tx.reconciliation_required = True
                airtel_tx.processing_note = "Provider enquiry returned no recognised status; existing status preserved."
        airtel_tx.save()
        AirtelEnquiryLog.objects.create(transaction=airtel_tx, reference=reference, path=path, http_status=response.get("http_status"), provider_status=enquiry_status, response=response)
        logger.info("airtel_enquiry_completed internal_reference=%s outcome=%s", airtel_tx.internal_reference, airtel_tx.last_enquiry_status)
        if airtel_tx.last_enquiry_status == AirtelTransaction.STATUS_SUCCESS:
            AirtelCallbackService().apply_success(airtel_tx)
            airtel_tx.refresh_from_db()
            airtel_tx.reconciliation_required = False
            airtel_tx.reconciliation_completed_at = timezone.now()
            airtel_tx.processing_note = "Final success confirmed by reconciliation enquiry."
            airtel_tx.save(update_fields=["reconciliation_required", "reconciliation_completed_at", "processing_note", "updated_at"])
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

    def handle_callback(self, raw_body: bytes, headers: dict[str, str], *, evidence_log: AirtelCallbackLog) -> tuple[AirtelCallbackLog, bool, int]:
        max_bytes = int(getattr(settings, "AIRTEL_CALLBACK_MAX_BYTES", 65536))
        log = evidence_log
        logger.info("airtel_callback_received body_size=%s", len(raw_body or b""))
        if len(raw_body or b"") > max_bytes:
            log.processing_error="Airtel callback body exceeds configured size limit."
            log.processing_state="INVALID_PAYLOAD"
            log.save(update_fields=["processing_error", "processing_state"])
            return log, False, 413

        try:
            parsed = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            log.processing_error=f"Invalid JSON: {exc}"
            log.processing_state="INVALID_PAYLOAD"; log.error_class=exc.__class__.__name__
            log.save(update_fields=["processing_error", "processing_state", "error_class"])
            return log, False, 400

        if not isinstance(parsed, dict) or not isinstance(_extract_nested(parsed, "data.transaction", "transaction"), dict):
            log.parsed_body=parsed if isinstance(parsed, dict) else None
            log.processing_error="Missing Airtel transaction object."; log.processing_state="INVALID_PAYLOAD"
            log.save(update_fields=["parsed_body", "processing_error", "processing_state"])
            return log, False, 400

        signature_valid, signature_found = self.verify_signature(raw_body, headers, parsed)
        log.parsed_body=parsed
        log.signature_valid=signature_valid
        log.signature_present=signature_found
        log.signature_validation_result="VALID" if signature_valid else "INVALID" if signature_found else "MISSING" if self.config.callback_auth_enabled else "NOT_CHECKED"
        log.candidate_identifiers=sorted(_collect_reference_values(parsed))
        log.extracted_status=extract_airtel_status(parsed)
        log.extracted_amount=extract_airtel_amount(parsed)
        log.extracted_subscriber=extract_airtel_subscriber(parsed)
        log.extracted_provider_identifiers=extract_airtel_references(parsed)
        log.extracted_provider_identifiers["payload_transaction_id"]=extract_payload_transaction_id(parsed)
        log.provider_transaction_id=extract_payload_transaction_id(parsed)[:120]
        log.save(update_fields=["parsed_body", "signature_valid", "signature_present", "signature_validation_result", "candidate_identifiers", "extracted_status", "extracted_amount", "extracted_subscriber", "extracted_provider_identifiers", "provider_transaction_id"])

        if self.config.callback_auth_enabled and not signature_valid:
            log.processing_error = "Invalid or missing Airtel callback signature."
            log.processing_state = "AUTH_FAILED"
            if not signature_found:
                log.processing_error = "Missing Airtel callback signature."
            log.save(update_fields=["processing_error", "processing_state"])
            return log, True, 401

        airtel_tx, match = self.find_transaction_match(parsed)
        if not airtel_tx:
            log.processing_error = match.get("error", "No matching Airtel transaction found.")
            log.processing_state = "AMBIGUOUS" if match.get("ambiguous") else "UNMATCHED"
            log.matching_details = match
            subscriber = extract_airtel_subscriber(parsed)
            callback_amount = extract_airtel_amount(parsed)
            pool = AirtelTransaction.objects.filter(
                environment=self.config.environment,
                status__in=[AirtelTransaction.STATUS_INITIATED, AirtelTransaction.STATUS_PENDING],
            )
            if subscriber:
                pool = pool.filter(customer_msisdn__endswith=subscriber)
            suggestions = candidate_suggestions(transactions=pool.order_by("-created_at")[:20], subscriber=subscriber, amount=callback_amount, received_at=log.created_at, environment=self.config.environment)
            log.candidate_suggestions = suggestions
            if len(suggestions) > 1:
                log.processing_state = "AMBIGUOUS"
                log.processing_error = "No strong identifier match; multiple investigation-only candidates found."
            log.save(update_fields=["processing_error", "processing_state", "matching_details", "candidate_suggestions"])
            logger.warning("airtel_callback_%s callback_log_id=%s", "ambiguous" if match.get("ambiguous") else "unmatched", log.pk)
            return log, True, 200

        log.transaction = airtel_tx
        status = extract_airtel_status(parsed)
        duplicate = airtel_tx.status == AirtelTransaction.STATUS_SUCCESS and bool(airtel_tx.processed_success_at)
        log.matched_identifier = match["value"]
        log.matched_field = match["field"]
        log.matching_details = match
        log.extracted_status = status

        callback_amount = extract_airtel_amount(parsed)
        log.extracted_amount = callback_amount
        log.processing_state = "MATCHED"
        log.save(update_fields=["transaction", "processing_state", "matched_identifier", "matched_field", "matching_details", "extracted_status", "extracted_amount"])
        if callback_amount is not None and callback_amount != airtel_tx.amount:
            log.processing_error = "Airtel callback amount does not match original payment attempt."
            log.processing_state = "PROCESSING_FAILED"
            log.save(update_fields=["transaction", "processing_error", "processing_state", "matched_identifier", "matched_field", "matching_details", "extracted_status", "extracted_amount"])
            airtel_tx.failure_reason = "Callback amount mismatch."
            airtel_tx.raw_callback = parsed
            airtel_tx.callback_verified = signature_valid
            airtel_tx.callback_received_at = timezone.now()
            airtel_tx.save(update_fields=["failure_reason", "raw_callback", "callback_verified", "callback_received_at", "updated_at"])
            return log, True, 400

        airtel_tx.raw_callback = parsed
        airtel_tx.callback_verified = signature_valid
        airtel_tx.callback_received_at = timezone.now()
        if status != AirtelTransaction.STATUS_UNKNOWN:
            airtel_tx.status = status
        else:
            airtel_tx.reconciliation_required = True
            airtel_tx.processing_note = "Callback returned no recognised status; existing status preserved."
        if status in TERMINAL_FAILURE_STATUSES:
            airtel_tx.failure_reason = str(
                _extract_nested(parsed, "data.transaction.message", "transaction.message", "message", "status.message")
                or "Airtel transaction failed."
            )[:500]
            airtel_tx.completed_at = timezone.now()
        store_confirmed_provider_references(airtel_tx, parsed)
        airtel_tx.save()

        if duplicate:
            log.duplicate = True
            log.processed = True
            log.processing_state = "DUPLICATE"
            log.processed_at = timezone.now()
            airtel_tx.duplicate_callback = True
            airtel_tx.save(update_fields=["duplicate_callback", "updated_at"])
            log.save(update_fields=["transaction", "duplicate", "processed", "processing_state", "processed_at", "matched_identifier", "matched_field", "matching_details", "extracted_status", "extracted_amount"])
            return log, True, 200

        if status == AirtelTransaction.STATUS_SUCCESS:
            try:
                self.apply_success(airtel_tx)
            except Exception as exc:
                log.processing_error = str(exc)[:1000]
                log.error_class = exc.__class__.__name__
                log.processing_state = "PROCESSING_FAILED"
                log.save(update_fields=["transaction", "processing_error", "error_class", "processing_state", "matched_identifier", "matched_field", "matching_details", "extracted_status", "extracted_amount"])
                logger.exception("callback_processing_failed callback_log_id=%s internal_reference=%s", log.pk, airtel_tx.internal_reference)
                return log, True, 500

        log.processed = True
        log.processing_state = "PROCESSED" if status != AirtelTransaction.STATUS_UNKNOWN else "MATCHED"
        log.processed_at = timezone.now()
        log.save(update_fields=["transaction", "processed", "processing_state", "processed_at", "matched_identifier", "matched_field", "matching_details", "extracted_status", "extracted_amount"])
        logger.info("airtel_callback_%s callback_log_id=%s internal_reference=%s", "success_applied" if status == AirtelTransaction.STATUS_SUCCESS else "matched", log.pk, airtel_tx.internal_reference)
        return log, True, 200

    def find_transaction(self, payload: dict[str, Any]) -> AirtelTransaction | None:
        return self.find_transaction_match(payload)[0]

    def find_transaction_match(self, payload: dict[str, Any]) -> tuple[AirtelTransaction | None, dict[str, Any]]:
        values = sorted(_collect_reference_values(payload))
        attempts = []
        fields = ("internal_reference", "provider_reference", "airtel_money_id", "airtel_transaction_id", "airtel_reference_id")
        for field in fields:
            for value in values:
                query = {field: value}
                if field in {"provider_reference", "airtel_transaction_id"}:
                    query["provider_id_confirmed"] = True
                matches = list(AirtelTransaction.objects.filter(**query).order_by("pk")[:2])
                attempts.append({"field": field, "value": value, "count": len(matches)})
                if len(matches) > 1:
                    return None, {"ambiguous": True, "error": "Multiple Airtel transactions matched callback identifier.", "attempts": attempts}
                if len(matches) == 1:
                    return matches[0], {"field": field, "value": value, "attempts": attempts}
        return None, {"error": "No matching Airtel transaction found.", "candidates": values, "attempts": attempts}

    @transaction.atomic
    def apply_success(self, airtel_tx: AirtelTransaction) -> AirtelTransaction:
        airtel_tx = AirtelTransaction.objects.select_for_update().get(pk=airtel_tx.pk)
        if airtel_tx.processed_success_at:
            return airtel_tx
        contract = None
        if airtel_tx.contract_id:
            contract_model = AirtelTransaction._meta.get_field("contract").remote_field.model
            contract = contract_model.objects.select_for_update().get(pk=airtel_tx.contract_id)
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

        portal_tx = None
        if airtel_tx.payment_transaction_id:
            portal_tx = PaymentTransaction.objects.select_for_update().get(pk=airtel_tx.payment_transaction_id)
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
            portal_tx.provider_reference = airtel_tx.airtel_money_id or airtel_tx.airtel_transaction_id or airtel_tx.provider_reference or ""
            portal_tx.webhook_payload = airtel_tx.raw_callback
            portal_tx.raw_response = airtel_tx.raw_response
            portal_tx.save(update_fields=["status", "paid_at", "provider_reference", "webhook_payload", "raw_response", "updated_at"])

        result = apply_payment_to_contract(
            contract,
            airtel_tx.amount,
            payment_type=payment_type,
            accumulate_partial_days=(
                airtel_tx.environment == "staging"
                and payment_type == PaymentTransaction.TYPE_REPAYMENT
            ),
        )
        portal_tx.balance_after = contract.deposit_remaining if payment_type == PaymentTransaction.TYPE_DEPOSIT else contract.remaining_amount
        portal_tx.save(update_fields=["balance_after", "updated_at"])
        finalize_paid_transaction_commission(portal_tx)
        airtel_tx.processed_success_at = timezone.now()
        airtel_tx.completed_at = airtel_tx.processed_success_at
        airtel_tx.repayment_posted = True
        airtel_tx.full_repayment_days_covered = result.get("days_extended", 0)
        airtel_tx.partial_credit_balance = result.get("partial_credit", Decimal("0"))
        airtel_tx.processing_note = f"Applied to contract {contract.contract_number}; days_extended={result.get('days_extended', 0)}"
        airtel_tx.save(update_fields=["payment_transaction", "processed_success_at", "completed_at", "repayment_posted", "full_repayment_days_covered", "partial_credit_balance", "processing_note", "updated_at"])
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
