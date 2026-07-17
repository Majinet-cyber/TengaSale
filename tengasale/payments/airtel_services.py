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
from portal.services import (
    apply_payment_to_contract,
    finalize_paid_transaction_commission,
)

from .airtel_client import (
    AirtelClient,
    AirtelConfig,
    AirtelConfigurationError,
)
from .mobile_network import (
    PROVIDER_AIRTEL,
    normalize_malawi_msisdn as normalize_malawi_number,
)
from .models import AirtelCallbackLog, AirtelTransaction

logger = logging.getLogger(__name__)

SUCCESS_VALUES = {
    "TS",
    "SUCCESS",
    "SUCCESSFUL",
    "TRANSACTION SUCCESSFUL",
}
PENDING_VALUES = {
    "TIP",
    "PENDING",
    "IN_PROGRESS",
}
EXPIRED_VALUES = {
    "TE",
    "EXPIRED",
}
FAILED_VALUES = {
    "FAILED",
    "TF",
    "DECLINED",
    "REJECTED",
}
TERMINAL_FAILURE_STATUSES = {
    AirtelTransaction.STATUS_FAILED,
    AirtelTransaction.STATUS_EXPIRED,
}


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
    return normalize_malawi_msisdn(value).lstrip("+")


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
        ref = (
            f"TENGA-AIRTEL-"
            f"{timezone.now().strftime('%Y%m%d')}-"
            f"{suffix}"
        )

        if not AirtelTransaction.objects.filter(
            internal_reference=ref
        ).exists():
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


def _canonical_callback_payload(
    data: dict[str, Any],
) -> bytes:
    payload = dict(data)
    payload.pop("hash", None)
    payload.pop("Hash", None)

    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sanitize_headers(
    headers: dict[str, str],
) -> dict[str, str]:
    sensitive = {
        "authorization",
        "x-api-key",
        "api-key",
        "cookie",
    }

    sanitized = {}

    for key, value in headers.items():
        sanitized[key] = (
            "***"
            if str(key).lower() in sensitive
            else value
        )

    return sanitized


def _collect_reference_values(
    data: dict[str, Any],
) -> set[str]:
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


def extract_airtel_status(
    data: dict[str, Any],
) -> str:
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

    message = _extract_nested(
        data,
        "data.transaction.message",
        "transaction.message",
        "status.message",
        "message",
    )

    normalized = normalize_airtel_status(value)

    if (
        normalized == AirtelTransaction.STATUS_UNKNOWN
        and message
    ):
        normalized = normalize_airtel_status(message)

    return normalized


def extract_airtel_references(
    data: dict[str, Any],
) -> dict[str, str]:
    return {
        "provider_reference": str(
            _extract_nested(
                data,
                "data.transaction.reference_id",
                "transaction.reference_id",
            )
            or ""
        ),
        "airtel_money_id": str(
            _extract_nested(
                data,
                "data.transaction.airtel_money_id",
                "transaction.airtel_money_id",
            )
            or ""
        ),
        "airtel_transaction_id": str(
            _extract_nested(
                data,
                "data.transaction.id",
                "transaction.id",
                "id",
            )
            or ""
        ),
        "airtel_reference_id": str(
            _extract_nested(
                data,
                "data.transaction.reference_id",
                "transaction.reference_id",
                "reference_id",
            )
            or ""
        ),
    }


def extract_airtel_amount(
    data: dict[str, Any],
) -> Decimal | None:
    value = _extract_nested(
        data,
        "data.transaction.amount",
        "data.transaction.transaction_amount",
        "transaction.amount",
        "transaction.transaction_amount",
        "amount",
    )

    if value in (None, ""):
        message = str(
            _extract_nested(
                data,
                "data.transaction.message",
                "transaction.message",
                "message",
            )
            or ""
        )

        match = re.search(
            r"(?i)\bMWK\s*([0-9][0-9,]*(?:\.\d+)?)",
            message,
        )

        if match:
            value = match.group(1).replace(",", "")

    if value in (None, ""):
        return None

    try:
        return Decimal(
            str(value).replace(",", "")
        ).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


class AirtelCollectionService:
    def __init__(
        self,
        client: AirtelClient | None = None,
    ):
        self.client = client or AirtelClient()

    @transaction.atomic
    def initiate_collection_payment(
        self,
        msisdn,
        amount,
        purpose,
        contract=None,
        customer=None,
    ) -> AirtelTransaction:
        self.client.config.assert_production_allowed()

        try:
            amount = Decimal(str(amount)).quantize(
                Decimal("0.01")
            )
        except (InvalidOperation, ValueError):
            raise ValueError("Enter a valid MWK amount.")

        if amount <= 0:
            raise ValueError(
                "Amount must be greater than zero."
            )

        purpose = str(
            purpose or AirtelTransaction.PURPOSE_TEST
        ).upper()

        valid_purposes = {
            choice[0]
            for choice in AirtelTransaction.PURPOSE_CHOICES
        }

        if purpose not in valid_purposes:
            raise ValueError(
                "Unsupported Airtel payment purpose."
            )

        normalized_msisdn = normalize_malawi_msisdn(
            msisdn
        )

        if contract is not None:
            contract = (
                contract.__class__.objects
                .select_for_update()
                .get(pk=contract.pk)
            )

            remaining = (
                contract.deposit_remaining
                if purpose == AirtelTransaction.PURPOSE_DEPOSIT
                else contract.remaining_amount
            )

            if remaining <= Decimal("0"):
                raise ValueError(
                    "Contract fully paid. "
                    "No payment is currently required."
                )

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
                raise ValueError(
                    "This contract cannot currently accept payments."
                )

            if amount > remaining:
                raise ValueError(
                    "Payment amount exceeds the outstanding "
                    f"balance of MWK {remaining}."
                )

            duplicate_exists = (
                AirtelTransaction.objects.filter(
                    contract=contract,
                    direction=(
                        AirtelTransaction
                        .DIRECTION_COLLECTION
                    ),
                    status__in=[
                        AirtelTransaction.STATUS_INITIATED,
                        AirtelTransaction.STATUS_PENDING,
                    ],
                    processed_success_at__isnull=True,
                ).exists()
            )

            if duplicate_exists:
                raise ValueError(
                    "A payment request is already pending "
                    "for this contract."
                )

        if not self.client.config.collections_enabled:
            raise ValueError(
                "Airtel collections are disabled "
                "by configuration."
            )

        if self.client.config.environment == "staging":
            try:
                max_amount = Decimal(
                    str(
                        self.client.config.test_max_amount
                    )
                ).quantize(Decimal("0.01"))

                min_amount = Decimal(
                    str(
                        self.client.config.test_min_amount
                    )
                ).quantize(Decimal("0.01"))

            except (InvalidOperation, ValueError):
                raise AirtelConfigurationError(
                    "Airtel staging test amount limits "
                    "must be valid MWK amounts."
                )

            if amount < min_amount:
                raise ValueError(
                    "Airtel staging payments must be "
                    f"at least MWK {min_amount:,.0f}."
                )

            if amount > max_amount:
                raise ValueError(
                    "Staging Airtel collections are "
                    f"limited to MWK {max_amount}."
                )

            allowed = _allowed_test_msisdns(
                self.client.config
            )

            if not allowed:
                raise ValueError(
                    "Airtel staging test MSISDN "
                    "allowlist is not configured."
                )

            if normalized_msisdn not in allowed:
                raise ValueError(
                    "Airtel staging test MSISDN "
                    "is not allowlisted."
                )

        internal_reference = generate_airtel_reference()

        payload = {
            "reference": internal_reference,
            "subscriber": {
                "country": self.client.config.country,
                "currency": self.client.config.currency,
                "msisdn": _airtel_api_msisdn(
                    normalized_msisdn
                ),
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
            direction=(
                AirtelTransaction.DIRECTION_COLLECTION
            ),
            status=AirtelTransaction.STATUS_INITIATED,
            raw_request=payload,
            contract=contract,
            customer=customer,
        )

        if contract is not None:
            payment_type = (
                PaymentTransaction.TYPE_DEPOSIT
                if purpose
                == AirtelTransaction.PURPOSE_DEPOSIT
                else PaymentTransaction.TYPE_REPAYMENT
            )

            portal_tx = PaymentTransaction.objects.create(
                payment_contract=contract,
                provider=(
                    PaymentTransaction.PROVIDER_AIRTEL
                ),
                payment_type=payment_type,
                amount=amount,
                commissionable_amount=(
                    Decimal("0")
                    if payment_type
                    == PaymentTransaction.TYPE_DEPOSIT
                    else amount
                ),
                currency=self.client.config.currency,
                phone=normalized_msisdn,
                network=(
                    PaymentTransaction.NETWORK_AIRTEL
                ),
                balance_before=(
                    contract.deposit_remaining
                    if payment_type
                    == PaymentTransaction.TYPE_DEPOSIT
                    else contract.remaining_amount
                ),
                status=PaymentTransaction.STATUS_PENDING,
                raw_request=payload,
                initiated_at=timezone.now(),
            )

            airtel_tx.payment_transaction = portal_tx

            airtel_tx.save(
                update_fields=[
                    "payment_transaction",
                    "updated_at",
                ]
            )

        if self.client.config.dry_run:
            airtel_tx.status = (
                AirtelTransaction.STATUS_DRY_RUN
            )

            airtel_tx.raw_response = {
                "dry_run": True,
                "message": (
                    "AIRTEL_DRY_RUN is enabled; "
                    "request was validated and not sent "
                    "to Airtel."
                ),
            }

            airtel_tx.save(
                update_fields=[
                    "status",
                    "raw_response",
                    "updated_at",
                ]
            )

            logger.info(
                "Airtel dry-run collection created "
                "ref=%s msisdn=%s amount=%s",
                internal_reference,
                mask_msisdn(normalized_msisdn),
                amount,
            )

            return airtel_tx

        if not self.client.is_configured_for_api_calls:
            airtel_tx.status = (
                AirtelTransaction.STATUS_PENDING
            )

            airtel_tx.raw_response = {
                "local_only": True,
                "message": (
                    "AIRTEL_AUTH_TOKEN is not configured; "
                    "transaction was created locally."
                ),
            }

            airtel_tx.save(
                update_fields=[
                    "status",
                    "raw_response",
                    "updated_at",
                ]
            )

            return airtel_tx

        try:
            response = self.client.post(
                self.client.config.collection_path,
                payload,
            )

        except Exception as exc:
            logger.warning(
                "Airtel collection API failed for %s: %s",
                internal_reference,
                exc.__class__.__name__,
            )

            airtel_tx.status = (
                AirtelTransaction.STATUS_PENDING
            )

            airtel_tx.raw_response = {
                "error": exc.__class__.__name__,
            }

            airtel_tx.save(
                update_fields=[
                    "status",
                    "raw_response",
                    "updated_at",
                ]
            )

            return airtel_tx

        body = response.get("body", {})

        refs = extract_airtel_references(
            body if isinstance(body, dict) else {}
        )

        airtel_tx.raw_response = response

        airtel_tx.status = (
            extract_airtel_status(body)
            if isinstance(body, dict)
            else AirtelTransaction.STATUS_UNKNOWN
        )

        business_code = (
            _extract_nested(
                body,
                "status.code",
                "data.status.code",
            )
            if isinstance(body, dict)
            else None
        )

        business_message = (
            str(
                _extract_nested(
                    body,
                    "status.message",
                    "message",
                )
                or ""
            )
            if isinstance(body, dict)
            else ""
        )

        business_failed = not response.get("ok")

        try:
            business_failed = (
                business_failed
                or int(str(business_code)) >= 400
            )
        except (TypeError, ValueError):
            pass

        if business_failed:
            airtel_tx.status = (
                AirtelTransaction.STATUS_FAILED
            )

            airtel_tx.failure_reason = (
                "Airtel Money could not process "
                "this payment request."
            )

            if (
                "agent" in business_message.lower()
                or "merchant" in business_message.lower()
            ):
                airtel_tx.processing_note = (
                    "Provider configuration error reported "
                    "during collection initiation."
                )

        if (
            airtel_tx.status
            == AirtelTransaction.STATUS_UNKNOWN
        ):
            airtel_tx.status = (
                AirtelTransaction.STATUS_PENDING
            )

        if (
            airtel_tx.status
            == AirtelTransaction.STATUS_SUCCESS
        ):
            airtel_tx.status = (
                AirtelTransaction.STATUS_PENDING
            )

            airtel_tx.processing_note = (
                "Collection request accepted; awaiting "
                "callback or enquiry confirmation."
            )

        for field, value in refs.items():
            if value:
                setattr(airtel_tx, field, value)

        airtel_tx.save()

        if (
            airtel_tx.status
            == AirtelTransaction.STATUS_FAILED
            and airtel_tx.payment_transaction
        ):
            airtel_tx.payment_transaction.status = (
                PaymentTransaction.STATUS_FAILED
            )

            airtel_tx.payment_transaction.save(
                update_fields=[
                    "status",
                    "updated_at",
                ]
            )

        return airtel_tx


class AirtelTransactionEnquiryService:
    def __init__(
        self,
        client: AirtelClient | None = None,
    ):
        self.client = client or AirtelClient()

    @transaction.atomic
    def enquire(
        self,
        internal_reference: str,
    ) -> AirtelTransaction:
        airtel_tx = (
            AirtelTransaction.objects
            .select_for_update()
            .get(
                internal_reference=internal_reference
            )
        )

        if not self.client.is_configured_for_api_calls:
            return airtel_tx

        reference = (
            airtel_tx.airtel_transaction_id
            or airtel_tx.airtel_reference_id
            or airtel_tx.internal_reference
        )

        path = (
            self.client.config
            .enquiry_path_template
            .format(reference=reference)
        )

        response = self.client.get(path)
        body = response.get("body", {})

        airtel_tx.raw_response = response

        if isinstance(body, dict):
            airtel_tx.status = extract_airtel_status(
                body
            )

            refs = extract_airtel_references(body)

            for field, value in refs.items():
                if value:
                    setattr(airtel_tx, field, value)

        airtel_tx.save()

        if (
            airtel_tx.status
            == AirtelTransaction.STATUS_SUCCESS
        ):
            AirtelCallbackService().apply_success(
                airtel_tx
            )

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

    def __init__(
        self,
        config: AirtelConfig | None = None,
    ):
        self.config = (
            config or AirtelConfig.from_settings()
        )

    def verify_signature(
        self,
        raw_body: bytes,
        headers: dict[str, str],
        parsed: dict[str, Any] | None = None,
    ) -> tuple[bool, bool]:
        if not self.config.callback_auth_enabled:
            return True, False

        provided = ""

        lower_headers = {
            str(key).lower(): str(value).strip()
            for key, value in headers.items()
        }

        for name in self.signature_headers:
            provided = lower_headers.get(
                name.lower(),
                "",
            )

            if provided:
                break

        if (
            not provided
            and isinstance(parsed, dict)
        ):
            provided = str(
                parsed.get("hash")
                or parsed.get("Hash")
                or ""
            ).strip()

        if (
            not provided
            or not self.config.callback_hash_key
        ):
            return False, False

        raw_digest = hmac.new(
            self.config.callback_hash_key.encode(
                "utf-8"
            ),
            raw_body,
            hashlib.sha256,
        ).hexdigest()

        if hmac.compare_digest(
            provided.lower(),
            raw_digest.lower(),
        ):
            return True, True

        if isinstance(parsed, dict):
            canonical_digest = hmac.new(
                self.config.callback_hash_key.encode(
                    "utf-8"
                ),
                _canonical_callback_payload(parsed),
                hashlib.sha256,
            ).hexdigest()

            if hmac.compare_digest(
                provided.lower(),
                canonical_digest.lower(),
            ):
                return True, True

        return False, True

    @transaction.atomic
    def handle_callback(
        self,
        raw_body: bytes,
        headers: dict[str, str],
    ) -> tuple[
        AirtelCallbackLog,
        bool,
        int,
    ]:
        max_bytes = int(
            getattr(
                settings,
                "AIRTEL_CALLBACK_MAX_BYTES",
                65536,
            )
        )

        safe_headers = _sanitize_headers(headers)

        if len(raw_body or b"") > max_bytes:
            log = AirtelCallbackLog.objects.create(
                received_headers=safe_headers,
                raw_body="",
                parsed_body=None,
                processing_error=(
                    "Airtel callback body exceeds "
                    "configured size limit."
                ),
            )

            return log, False, 400

        try:
            parsed = (
                json.loads(
                    raw_body.decode("utf-8")
                )
                if raw_body
                else {}
            )

        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            log = AirtelCallbackLog.objects.create(
                received_headers=safe_headers,
                raw_body=raw_body.decode(
                    "utf-8",
                    errors="replace",
                ),
                parsed_body=None,
                processing_error=(
                    f"Invalid JSON: {exc}"
                ),
            )

            return log, False, 400

        transaction_object = _extract_nested(
            parsed,
            "data.transaction",
            "transaction",
        )

        if (
            not isinstance(parsed, dict)
            or not isinstance(
                transaction_object,
                dict,
            )
        ):
            log = AirtelCallbackLog.objects.create(
                received_headers=safe_headers,
                raw_body=raw_body.decode(
                    "utf-8",
                    errors="replace",
                ),
                parsed_body=(
                    parsed
                    if isinstance(parsed, dict)
                    else None
                ),
                processing_error=(
                    "Missing Airtel transaction object."
                ),
            )

            return log, False, 400

        signature_valid, signature_found = (
            self.verify_signature(
                raw_body,
                headers,
                parsed,
            )
        )

        log = AirtelCallbackLog.objects.create(
            received_headers=safe_headers,
            raw_body=raw_body.decode(
                "utf-8",
                errors="replace",
            ),
            parsed_body=parsed,
            signature_valid=signature_valid,
        )

        if (
            self.config.callback_auth_enabled
            and not signature_valid
        ):
            log.processing_error = (
                "Invalid or missing Airtel "
                "callback signature."
            )

            if not signature_found:
                log.processing_error = (
                    "Missing Airtel callback signature."
                )

            log.save(
                update_fields=["processing_error"]
            )

            return log, True, 401

        airtel_tx = self.find_transaction(parsed)

        if not airtel_tx:
            log.processing_error = (
                "No matching Airtel transaction found."
            )

            log.save(
                update_fields=["processing_error"]
            )

            return log, True, 200

        log.transaction = airtel_tx

        status = extract_airtel_status(parsed)
        refs = extract_airtel_references(parsed)

        duplicate = (
            airtel_tx.status
            == AirtelTransaction.STATUS_SUCCESS
            and bool(
                airtel_tx.processed_success_at
            )
        )

        callback_amount = extract_airtel_amount(
            parsed
        )

        if (
            callback_amount is not None
            and callback_amount != airtel_tx.amount
        ):
            log.processing_error = (
                "Airtel callback amount does not "
                "match original payment attempt."
            )

            log.save(
                update_fields=[
                    "transaction",
                    "processing_error",
                ]
            )

            airtel_tx.failure_reason = (
                "Callback amount mismatch."
            )

            airtel_tx.raw_callback = parsed
            airtel_tx.callback_verified = (
                signature_valid
            )
            airtel_tx.callback_received_at = (
                timezone.now()
            )

            airtel_tx.save(
                update_fields=[
                    "failure_reason",
                    "raw_callback",
                    "callback_verified",
                    "callback_received_at",
                    "updated_at",
                ]
            )

            return log, True, 400

        airtel_tx.raw_callback = parsed
        airtel_tx.callback_verified = (
            signature_valid
        )
        airtel_tx.callback_received_at = (
            timezone.now()
        )
        airtel_tx.status = status

        if status in TERMINAL_FAILURE_STATUSES:
            airtel_tx.failure_reason = str(
                _extract_nested(
                    parsed,
                    "data.transaction.message",
                    "transaction.message",
                    "message",
                    "status.message",
                )
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

            airtel_tx.duplicate_callback = True

            airtel_tx.save(
                update_fields=[
                    "duplicate_callback",
                    "updated_at",
                ]
            )

            log.save(
                update_fields=[
                    "transaction",
                    "duplicate",
                    "processed",
                ]
            )

            return log, True, 200

        if (
            status
            == AirtelTransaction.STATUS_SUCCESS
        ):
            self.apply_success(airtel_tx)

        log.processed = True

        log.save(
            update_fields=[
                "transaction",
                "processed",
            ]
        )

        return log, True, 200

    def find_transaction(
        self,
        payload: dict[str, Any],
    ) -> AirtelTransaction | None:
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

        return (
            AirtelTransaction.objects
            .select_related(
                "contract",
                "payment_transaction",
            )
            .filter(query)
            .first()
        )

    @transaction.atomic
    def apply_success(
        self,
        airtel_tx: AirtelTransaction,
    ) -> AirtelTransaction:
        # Lock only the AirtelTransaction row.
        #
        # contract and payment_transaction are nullable.
        # Including them in a select_related() outer join
        # while using SELECT ... FOR UPDATE causes PostgreSQL:
        #
        # FOR UPDATE cannot be applied to the nullable side
        # of an outer join.
        airtel_tx = (
            AirtelTransaction.objects
            .select_for_update()
            .get(pk=airtel_tx.pk)
        )

        if airtel_tx.processed_success_at:
            return airtel_tx

        contract = airtel_tx.contract

        if not contract:
            airtel_tx.processed_success_at = (
                timezone.now()
            )

            airtel_tx.completed_at = (
                airtel_tx.processed_success_at
            )

            airtel_tx.processing_note = (
                "Success received; no linked "
                "contract to credit."
            )

            airtel_tx.save(
                update_fields=[
                    "processed_success_at",
                    "completed_at",
                    "processing_note",
                    "updated_at",
                ]
            )

            return airtel_tx

        payment_type = (
            PaymentTransaction.TYPE_REPAYMENT
        )

        if (
            airtel_tx.purpose
            == AirtelTransaction.PURPOSE_DEPOSIT
        ):
            payment_type = (
                PaymentTransaction.TYPE_DEPOSIT
            )

            if (
                getattr(
                    contract,
                    "deposit_access_days",
                    0,
                )
                < 14
            ):
                contract.deposit_access_days = 14

                contract.save(
                    update_fields=[
                        "deposit_access_days",
                    ]
                )

        portal_tx = airtel_tx.payment_transaction

        if not portal_tx:
            portal_tx = (
                PaymentTransaction.objects.create(
                    payment_contract=contract,
                    provider=(
                        PaymentTransaction
                        .PROVIDER_AIRTEL
                    ),
                    payment_type=payment_type,
                    amount=airtel_tx.amount,
                    commissionable_amount=(
                        Decimal("0")
                        if payment_type
                        == PaymentTransaction
                        .TYPE_DEPOSIT
                        else airtel_tx.amount
                    ),
                    currency=airtel_tx.currency,
                    phone=airtel_tx.customer_msisdn,
                    network=(
                        PaymentTransaction
                        .NETWORK_AIRTEL
                    ),
                    internal_reference=(
                        airtel_tx
                        .internal_reference[:30]
                    ),
                    provider_reference=(
                        airtel_tx.airtel_money_id
                        or airtel_tx
                        .airtel_transaction_id
                        or airtel_tx
                        .provider_reference
                        or ""
                    ),
                    status=(
                        PaymentTransaction
                        .STATUS_PAID
                    ),
                    raw_request=(
                        airtel_tx.raw_request
                    ),
                    raw_response=(
                        airtel_tx.raw_response
                    ),
                    webhook_payload=(
                        airtel_tx.raw_callback
                    ),
                    initiated_at=(
                        airtel_tx.created_at
                    ),
                    paid_at=timezone.now(),
                    balance_before=(
                        contract.deposit_remaining
                        if payment_type
                        == PaymentTransaction
                        .TYPE_DEPOSIT
                        else contract.remaining_amount
                    ),
                )
            )

            airtel_tx.payment_transaction = (
                portal_tx
            )

        elif (
            portal_tx.status
            != PaymentTransaction.STATUS_PAID
        ):
            portal_tx.status = (
                PaymentTransaction.STATUS_PAID
            )

            portal_tx.paid_at = timezone.now()

            portal_tx.provider_reference = (
                airtel_tx.airtel_money_id
                or airtel_tx.airtel_transaction_id
                or airtel_tx.provider_reference
                or ""
            )

            portal_tx.webhook_payload = (
                airtel_tx.raw_callback
            )

            portal_tx.raw_response = (
                airtel_tx.raw_response
            )

            portal_tx.save(
                update_fields=[
                    "status",
                    "paid_at",
                    "provider_reference",
                    "webhook_payload",
                    "raw_response",
                    "updated_at",
                ]
            )

        result = apply_payment_to_contract(
            contract,
            airtel_tx.amount,
            payment_type=payment_type,
            accumulate_partial_days=(
                airtel_tx.environment == "staging"
                and payment_type
                == PaymentTransaction.TYPE_REPAYMENT
            ),
        )

        portal_tx.balance_after = (
            contract.deposit_remaining
            if payment_type
            == PaymentTransaction.TYPE_DEPOSIT
            else contract.remaining_amount
        )

        portal_tx.save(
            update_fields=[
                "balance_after",
                "updated_at",
            ]
        )

        try:
            finalize_paid_transaction_commission(
                portal_tx
            )

        except Exception:
            logger.exception(
                "Airtel commission finalization "
                "failed for %s",
                airtel_tx.internal_reference,
            )

        airtel_tx.processed_success_at = (
            timezone.now()
        )

        airtel_tx.completed_at = (
            airtel_tx.processed_success_at
        )

        airtel_tx.repayment_posted = True

        airtel_tx.full_repayment_days_covered = (
            result.get("days_extended", 0)
        )

        airtel_tx.partial_credit_balance = (
            result.get(
                "partial_credit",
                Decimal("0"),
            )
        )

        airtel_tx.processing_note = (
            f"Applied to contract "
            f"{contract.contract_number}; "
            f"days_extended="
            f"{result.get('days_extended', 0)}"
        )

        airtel_tx.save(
            update_fields=[
                "payment_transaction",
                "processed_success_at",
                "completed_at",
                "repayment_posted",
                "full_repayment_days_covered",
                "partial_credit_balance",
                "processing_note",
                "updated_at",
            ]
        )

        return airtel_tx


class AirtelDisbursementService:
    def __init__(
        self,
        client: AirtelClient | None = None,
    ):
        self.client = client or AirtelClient()

    def create_disbursement(
        self,
        payload: dict[str, Any],
        *,
        approved: bool = False,
    ) -> dict[str, Any]:
        if (
            not approved
            or not getattr(
                settings,
                "PAYOUTS_ALLOW_LIVE_DISBURSEMENTS",
                False,
            )
        ):
            return {
                "ok": False,
                "message": (
                    "Airtel disbursements are disabled "
                    "until explicit payout approval "
                    "is enabled."
                ),
            }

        return self.client.post(
            self.client.config.disbursement_path,
            payload,
        )


class AirtelMerchantRegistrationService:
    """
    Reserved for Airtel merchant registration workflows
    when Airtel enables them.
    """

    def __init__(
        self,
        client: AirtelClient | None = None,
    ):
        self.client = client or AirtelClient()
