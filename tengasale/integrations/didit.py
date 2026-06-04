"""
Didit identity verification integration for TengaSale.

API docs: https://verification.didit.me (v3 sessions and webhooks).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

try:
    import requests
    from requests.exceptions import HTTPError, RequestException
except ImportError:
    requests = None  # type: ignore
    HTTPError = Exception  # type: ignore
    RequestException = Exception  # type: ignore

DIDIT_API_BASE = "https://verification.didit.me"
WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS = 300
DIDIT_WORKFLOW_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
WORKFLOW_MISMATCH_USER_MESSAGE = (
    "Didit rejected the workflow_id. Confirm the workflow belongs to the same "
    "Didit app/API key and is published/active."
)

DIDIT_STATUS_MAP = {
    "Not Started": "not_started",
    "In Progress": "in_progress",
    "Awaiting User": "awaiting_user",
    "In Review": "pending_review",
    "Approved": "approved",
    "Declined": "declined",
    "Resubmitted": "resubmitted",
    "Abandoned": "abandoned",
    "Expired": "expired",
    "Kyc Expired": "kyc_expired",
}

KYC_STATUS_DISPLAY = {
    "not_started": "Not Started",
    "in_progress": "In Progress",
    "awaiting_user": "Awaiting User",
    "pending_review": "Pending Review",
    "approved": "Approved",
    "declined": "Declined",
    "resubmitted": "Resubmitted",
    "abandoned": "Abandoned",
    "expired": "Expired",
    "kyc_expired": "KYC Expired",
    "manual_review": "Manual Review",
}

RESTARTABLE_KYC_STATUSES = frozenset({
    "declined",
    "abandoned",
    "expired",
    "kyc_expired",
    "not_started",
})

ACTIVE_DIDIT_KYC_STATUSES = frozenset({
    "in_progress",
    "awaiting_user",
    "pending_review",
    "approved",
    "resubmitted",
    "manual_review",
})


class DiditConfigurationError(Exception):
    """Raised when required Didit settings are missing."""


class DiditAPIError(Exception):
    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        payload: Any = None,
        *,
        user_message: str | None = None,
        response_text: str | None = None,
        request_payload_keys: list[str] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload
        self.response_text = response_text
        self.request_payload_keys = request_payload_keys or []
        self.user_message = user_message or didit_user_facing_message(
            status_code=status_code,
            detail=message,
            payload=payload,
            response_text=response_text,
        )


def didit_production_mode() -> bool:
    """True when Django DEBUG is off (production-style webhook rules)."""
    return not getattr(settings, "DEBUG", True)


def didit_user_facing_message(
    *,
    status_code: int | None = None,
    detail: str = "",
    payload: Any = None,
    response_text: str | None = None,
) -> str:
    combined = " ".join(
        part
        for part in (
            detail or "",
            response_text or "",
            json.dumps(payload) if isinstance(payload, (dict, list)) else str(payload or ""),
        )
        if part
    ).lower()
    detail_lower = (detail or "").lower()
    if status_code == 403:
        return "Didit authentication failed. Check DIDIT_API_KEY."
    if status_code in (400, 404, 422) and "workflow" in combined:
        return WORKFLOW_MISMATCH_USER_MESSAGE
    if status_code in (400, 404, 422) and "workflow" in detail_lower:
        return WORKFLOW_MISMATCH_USER_MESSAGE
    if status_code == 400:
        return "Didit rejected the session request."
    if "timeout" in detail_lower or "timed out" in detail_lower:
        return "Could not reach Didit. Try again."
    if "request failed" in detail_lower or "connection" in detail_lower:
        return "Could not reach Didit. Try again."
    if status_code and status_code >= 500:
        return "Didit verification is temporarily unavailable. Try again."
    return "Didit verification is temporarily unavailable. Try again."


def map_didit_status(status: str | None) -> str:
    if not status:
        return "not_started"
    mapped = DIDIT_STATUS_MAP.get(status)
    if mapped:
        return mapped
    logger.warning("Unknown Didit status %r — mapping to manual_review", status)
    return "manual_review"


def shorten_floats(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: shorten_floats(item) for key, item in value.items()}
    if isinstance(value, list):
        return [shorten_floats(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def sort_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: sort_keys(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [sort_keys(item) for item in value]
    return value


def canonicalize_didit_payload(payload: Any) -> str:
    normalized = sort_keys(shorten_floats(payload))
    return json.dumps(normalized, separators=(",", ":"), ensure_ascii=False)


def verify_didit_webhook_signature_v2(raw_body: bytes, signature: str | None, timestamp: str | None) -> tuple[bool, str]:
    """
    Verify Didit X-Signature-V2. Returns (ok, error_message).
    """
    secret = (getattr(settings, "DIDIT_WEBHOOK_SECRET", "") or "").strip()
    allow_unsigned = getattr(settings, "DIDIT_ALLOW_UNSIGNED_WEBHOOKS", False)
    production = didit_production_mode()

    if production:
        if not secret:
            logger.error("Didit webhook rejected: DIDIT_WEBHOOK_SECRET is required in production.")
            return False, "DIDIT_WEBHOOK_SECRET is required in production."
        allow_unsigned = False

    if not secret:
        if allow_unsigned and not production:
            logger.warning(
                "Didit webhook accepted WITHOUT signature verification "
                "(DIDIT_WEBHOOK_SECRET missing, DIDIT_ALLOW_UNSIGNED_WEBHOOKS=True). "
                "Use only for local development."
            )
            return True, ""
        return False, "Webhook secret not configured."

    if not signature:
        return False, "Missing X-Signature-V2 header."
    if not timestamp:
        return False, "Missing X-Timestamp header."

    try:
        ts_value = float(timestamp)
    except (TypeError, ValueError):
        return False, "Invalid X-Timestamp header."

    if abs(time.time() - ts_value) > WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS:
        return False, "Webhook timestamp outside allowed window."

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False, "Invalid JSON body."

    canonical = canonicalize_didit_payload(payload)
    expected = hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature.strip()):
        return False, "Invalid webhook signature."
    return True, ""


def _api_headers() -> dict[str, str]:
    api_key = (getattr(settings, "DIDIT_API_KEY", "") or "").strip()
    if not api_key:
        raise DiditConfigurationError("Didit API key is not configured. Set DIDIT_API_KEY.")
    return {"x-api-key": api_key, "Content-Type": "application/json", "Accept": "application/json"}


def is_uuid_like_workflow_id(workflow_id: str) -> bool:
    return bool(DIDIT_WORKFLOW_UUID_RE.match((workflow_id or "").strip()))


def _parse_didit_response_body(response) -> tuple[str, Any]:
    """Return (raw_text, parsed_json_or_fallback_dict). Never logs secrets."""
    text = (getattr(response, "text", None) or "")[:8000]
    if not getattr(response, "content", None):
        return text, {}
    try:
        parsed = response.json()
    except ValueError:
        parsed = {"raw": text} if text else {}
    return text, parsed


def _safe_didit_detail_for_client(parsed: Any, raw_text: str) -> Any:
    if isinstance(parsed, dict) and parsed:
        return parsed
    if isinstance(parsed, list):
        return parsed
    if raw_text:
        return {"raw": raw_text[:2000]}
    return {}


def summarize_didit_api_error(data: Any, raw_text: str = "") -> str:
    """Human-readable Didit error text for logs and UI (no secrets)."""
    if isinstance(data, dict):
        detail = data.get("detail")
        if detail:
            return str(detail)
        message = data.get("message")
        if message:
            return str(message) if not isinstance(message, list) else " ".join(str(item) for item in message)
        error = data.get("error")
        if error:
            return str(error)
        parts: list[str] = []
        for key, value in data.items():
            if key in {"raw"}:
                continue
            if isinstance(value, list):
                parts.append(f"{key}: {', '.join(str(item) for item in value)}")
            elif value is not None:
                parts.append(f"{key}: {value}")
        if parts:
            return "; ".join(parts)
    if isinstance(data, list):
        return " ".join(str(item) for item in data)
    text = (raw_text or "").strip()
    return text[:2000] if text else ""


def format_didit_session_error_message(
    *,
    status_code: int | None,
    data: Any,
    raw_text: str = "",
) -> str:
    """User-facing message including the real Didit API error body when available."""
    summary = summarize_didit_api_error(data, raw_text)
    combined = summary.lower()
    if status_code == 403:
        return "Didit authentication failed. Check DIDIT_API_KEY."
    if status_code in (400, 404, 422) and "workflow" in combined:
        if summary:
            return f"{WORKFLOW_MISMATCH_USER_MESSAGE} ({summary})"
        return WORKFLOW_MISMATCH_USER_MESSAGE
    if status_code == 400:
        if summary:
            return f"Didit rejected the session request: {summary}"
        return "Didit rejected the session request."
    if summary:
        return summary
    return didit_user_facing_message(status_code=status_code, detail=summary, payload=data, response_text=raw_text)


def validate_didit_session_prerequisites(application) -> None:
    """Validate Didit session settings before calling the API."""
    workflow_id = (getattr(settings, "DIDIT_WORKFLOW_ID", "") or "").strip()
    if not workflow_id:
        raise DiditConfigurationError("Didit workflow ID is not configured. Set DIDIT_WORKFLOW_ID.")
    if not is_uuid_like_workflow_id(workflow_id):
        raise DiditConfigurationError(
            "DIDIT_WORKFLOW_ID must be a UUID (e.g. xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)."
        )

    callback = (getattr(settings, "DIDIT_CALLBACK_URL", "") or "").strip()
    if not callback:
        raise DiditConfigurationError("Didit callback URL is not configured. Set DIDIT_CALLBACK_URL.")
    if didit_production_mode() and not callback.lower().startswith("https://"):
        raise DiditConfigurationError("DIDIT_CALLBACK_URL must use HTTPS in production.")

    if application.pk is None:
        raise DiditConfigurationError("Application must be saved before starting Didit KYC.")

    vendor_data = str(application.pk)
    if not isinstance(vendor_data, str) or not vendor_data:
        raise DiditConfigurationError("vendor_data must be a non-empty string.")

    app_id_meta = str(application.pk)
    if not isinstance(app_id_meta, str) or not app_id_meta:
        raise DiditConfigurationError("metadata.application_id must be a non-empty string.")


def _split_customer_name(full_name: str) -> tuple[str, str]:
    parts = (full_name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def build_expected_details(application) -> dict[str, str]:
    expected: dict[str, str] = {}
    first_name, last_name = _split_customer_name(getattr(application, "customer_name", "") or "")
    if first_name:
        expected["first_name"] = first_name
    if last_name:
        expected["last_name"] = last_name
    dob = getattr(application, "date_of_birth", None)
    if dob:
        expected["date_of_birth"] = dob.isoformat()
    if expected:
        expected["id_country"] = "MW"
    return expected


def build_didit_session_payload(application) -> dict:
    """
    Minimal POST /v3/session/ body only.

    Does not send expected_details, contact_details, phone, DOB, names,
    id_country, or vendor_business_id. DIDIT_SEND_EXPECTED_DETAILS is reserved
    for a future opt-in once minimal sessions succeed in production.
    """
    workflow_id = (getattr(settings, "DIDIT_WORKFLOW_ID", "") or "").strip()
    callback = (getattr(settings, "DIDIT_CALLBACK_URL", "") or "").strip()
    return {
        "workflow_id": workflow_id,
        "vendor_data": str(application.pk),
        "callback": callback,
        "metadata": {
            "source": "tengasale",
            "application_id": str(application.pk),
        },
    }


def sanitize_didit_session_payload_for_log(payload: dict) -> dict:
    """Payload safe for logs and management commands (no secrets)."""
    sanitized: dict[str, Any] = {
        "workflow_id": payload.get("workflow_id"),
        "vendor_data": payload.get("vendor_data"),
        "callback": payload.get("callback"),
        "metadata": payload.get("metadata"),
    }
    return sanitized


def _request(method: str, path: str, json_body: dict | None = None) -> dict:
    if requests is None:
        raise DiditAPIError("The requests library is not installed.", user_message="Didit is not available.")

    url = f"{DIDIT_API_BASE}{path}"
    timeout = int(getattr(settings, "DIDIT_REQUEST_TIMEOUT_SECONDS", 15))
    try:
        response = requests.request(
            method,
            url,
            headers=_api_headers(),
            json=json_body,
            timeout=timeout,
        )
    except RequestException as exc:
        if requests is not None and isinstance(exc, requests.exceptions.Timeout):
            logger.error("Didit API timeout: %s %s (timeout=%ss)", method, path, timeout)
            raise DiditAPIError(
                f"Didit API timeout after {timeout}s",
                user_message="Could not reach Didit. Try again.",
            ) from exc
        logger.error("Didit API request failed: %s %s — %s", method, path, type(exc).__name__)
        raise DiditAPIError(
            f"Didit API request failed: {exc}",
            user_message="Could not reach Didit. Try again.",
        ) from exc

    raw_text, data = _parse_didit_response_body(response)

    if response.status_code >= 400:
        message = data.get("message") if isinstance(data, dict) else None
        if message is None and isinstance(data, dict):
            message = data.get("detail")
        if message is None:
            message = raw_text or "Didit API error"
        if isinstance(message, list):
            message = " ".join(str(item) for item in message)
        message = str(message)
        payload_keys = list(json_body.keys()) if isinstance(json_body, dict) else []
        client_detail = _safe_didit_detail_for_client(data, raw_text)
        error_summary = summarize_didit_api_error(client_detail, raw_text)
        logger.error(
            "Didit API %s %s -> %s body=%s raw=%s request_keys=%s",
            method,
            path,
            response.status_code,
            json.dumps(client_detail, default=str)[:2000],
            (raw_text or "")[:2000],
            payload_keys,
        )
        user_message = format_didit_session_error_message(
            status_code=response.status_code,
            data=client_detail,
            raw_text=raw_text,
        )
        raise DiditAPIError(
            message,
            status_code=response.status_code,
            payload=client_detail,
            response_text=raw_text,
            request_payload_keys=payload_keys,
            user_message=user_message,
        )

    if not isinstance(data, dict):
        return {"data": data}
    return data


def create_webhook_destination(
    *,
    url: str,
    label: str,
    webhook_version: str = "v3",
    subscribed_events: list[str] | None = None,
) -> dict:
    body = {
        "label": label,
        "url": url,
        "webhook_version": webhook_version,
        "subscribed_events": subscribed_events or ["status.updated", "data.updated"],
    }
    return _request("POST", "/v3/webhook/destinations/", json_body=body)


def list_webhook_destinations() -> Any:
    return _request("GET", "/v3/webhook/destinations/")


def extract_webhook_secret_from_response(data: dict) -> str | None:
    """Return signing secret from Didit destination response if present."""
    for key in (
        "secret",
        "webhook_secret",
        "signing_secret",
        "shared_secret",
        "signing_key",
    ):
        value = data.get(key)
        if value:
            return str(value).strip()
    nested = data.get("destination") or data.get("data")
    if isinstance(nested, dict):
        return extract_webhook_secret_from_response(nested)
    return None


def create_didit_session(application) -> dict:
    validate_didit_session_prerequisites(application)
    body = build_didit_session_payload(application)
    logger.info(
        "Didit create session request payload=%s",
        json.dumps(sanitize_didit_session_payload_for_log(body), default=str),
    )
    return _request("POST", "/v3/session/", json_body=body)


def retrieve_didit_decision(session_id: str) -> dict:
    session_id = (session_id or "").strip()
    if not session_id:
        raise DiditConfigurationError("session_id is required.")
    return _request("GET", f"/v3/session/{session_id}/decision/")


def _first_record(items: Any) -> dict | None:
    if isinstance(items, list) and items:
        first = items[0]
        return first if isinstance(first, dict) else None
    return None


def _safe_get(record: dict | None, *keys: str, default=None):
    if not record:
        return default
    for key in keys:
        if key in record and record[key] is not None:
            return record[key]
    return default


def extract_didit_summary(decision: dict | None) -> dict:
    """Extract a reviewer-friendly summary from Didit v3 plural decision arrays."""
    decision = decision or {}

    id_doc = _first_record(decision.get("id_verifications"))
    liveness = _first_record(decision.get("liveness_checks"))
    face_match = _first_record(decision.get("face_matches"))
    aml = _first_record(decision.get("aml_screenings"))
    ip_analysis = _first_record(decision.get("ip_analyses"))

    warnings: list = []
    for block in (id_doc, face_match, liveness):
        if isinstance(block, dict):
            block_warnings = block.get("warnings")
            if isinstance(block_warnings, list):
                warnings.extend(block_warnings)

    return {
        "document": {
            "first_name": _safe_get(id_doc, "first_name"),
            "last_name": _safe_get(id_doc, "last_name"),
            "document_type": _safe_get(id_doc, "document_type", "type"),
            "document_number": _safe_get(id_doc, "document_number", "number"),
            "date_of_birth": _safe_get(id_doc, "date_of_birth"),
            "nationality": _safe_get(id_doc, "nationality"),
            "issuing_state": _safe_get(id_doc, "issuing_state", "issuing_country"),
            "expiration_date": _safe_get(id_doc, "expiration_date", "expiry_date"),
            "address": _safe_get(id_doc, "address", "full_address"),
            "warnings": warnings or None,
        },
        "liveness": {
            "status": _safe_get(liveness, "status"),
            "score": _safe_get(liveness, "score"),
            "method": _safe_get(liveness, "method"),
            "face_quality": _safe_get(liveness, "face_quality"),
            "face_luminance": _safe_get(liveness, "face_luminance"),
        },
        "face_match": {
            "status": _safe_get(face_match, "status"),
            "score": _safe_get(face_match, "score"),
            "warnings": _safe_get(face_match, "warnings"),
        },
        "aml": {
            "status": _safe_get(aml, "status"),
            "total_hits": _safe_get(aml, "total_hits", "hits_count"),
            "hits": _safe_get(aml, "hits", "matches"),
        },
        "ip_device": {
            "status": _safe_get(ip_analysis, "status"),
            "ip_address": _safe_get(ip_analysis, "ip_address", "ip"),
            "country": _safe_get(ip_analysis, "country"),
            "vpn": _safe_get(ip_analysis, "vpn", "is_vpn"),
            "proxy": _safe_get(ip_analysis, "proxy", "is_proxy"),
            "tor": _safe_get(ip_analysis, "tor", "is_tor"),
            "hosting": _safe_get(ip_analysis, "hosting", "is_hosting"),
            "risk_score": _safe_get(ip_analysis, "risk_score", "risk"),
        },
    }


def has_active_didit_session(application) -> bool:
    if not application.didit_session_id:
        return False
    status = (application.kyc_status or "").strip()
    if status in RESTARTABLE_KYC_STATUSES:
        return False
    return status in ACTIVE_DIDIT_KYC_STATUSES or bool(application.didit_verification_url)


def can_restart_didit_session(application, user) -> bool:
    from accounts.utils import is_hq

    if user.is_superuser or is_hq(user):
        return True
    status = (application.kyc_status or "").strip()
    return status in RESTARTABLE_KYC_STATUSES
