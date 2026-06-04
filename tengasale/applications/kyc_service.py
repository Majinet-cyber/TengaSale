"""
TengaSale KYC Provider Service — pluggable identity verification.

Supports three modes controlled by the KYC_PROVIDER environment variable:
  - manual   : HQ-reviewed photo uploads (default, always safe to use)
  - didit    : Didit identity verification (https://didit.me)
  - metamap  : MetaMap / Mati identity verification (https://metamap.com)

If KYC_PROVIDER is not set, or is 'manual', the manual KYC flow is used.
If the configured provider is not available (missing credentials), the system
falls back to manual KYC gracefully.

Common KYC statuses across all providers:
  not_started, pending, in_review, approved, rejected, failed, requires_retry
"""
from __future__ import annotations

import logging
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

# ── Common status constants ────────────────────────────────────────────────────
STATUS_NOT_STARTED = "not_started"
STATUS_PENDING = "pending"
STATUS_IN_REVIEW = "in_review"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_FAILED = "failed"
STATUS_REQUIRES_RETRY = "requires_retry"

KYC_STATUS_LABELS = {
    STATUS_NOT_STARTED: "Not Started",
    STATUS_PENDING: "Pending",
    STATUS_IN_REVIEW: "In Review",
    STATUS_APPROVED: "Approved",
    STATUS_REJECTED: "Rejected",
    STATUS_FAILED: "Failed",
    STATUS_REQUIRES_RETRY: "Requires Retry",
}


def get_kyc_provider() -> str:
    """Return the configured KYC provider name. Defaults to 'manual'."""
    return getattr(settings, "KYC_PROVIDER", "manual").lower().strip()


def is_provider_configured(provider: str) -> bool:
    """Check if the required credentials for a provider are present."""
    if provider == "didit":
        return bool(
            getattr(settings, "DIDIT_API_KEY", None)
            and getattr(settings, "DIDIT_WORKFLOW_ID", None)
        )
    if provider == "metamap":
        return bool(
            getattr(settings, "METAMAP_CLIENT_ID", None)
            and getattr(settings, "METAMAP_CLIENT_SECRET", None)
        )
    return True  # manual always available


def get_active_provider() -> str:
    """Return the active provider, falling back to manual if not configured."""
    configured = get_kyc_provider()
    if configured in ("didit", "metamap") and not is_provider_configured(configured):
        logger.warning(
            "KYC_PROVIDER=%s but credentials are not configured. Falling back to manual KYC.",
            configured,
        )
        return "manual"
    return configured


# ── Provider interface ─────────────────────────────────────────────────────────

def map_didit_status(didit_status: str) -> str:
    """Map a Didit-specific status string to a common TengaSale status."""
    mapping = {
        "Not Started": STATUS_NOT_STARTED,
        "In Progress": STATUS_PENDING,
        "Awaiting User": STATUS_PENDING,
        "In Review": STATUS_IN_REVIEW,
        "Approved": STATUS_APPROVED,
        "Declined": STATUS_REJECTED,
        "Resubmitted": STATUS_PENDING,
        "Abandoned": STATUS_FAILED,
        "Expired": STATUS_FAILED,
        "Kyc Expired": STATUS_FAILED,
        "approved": STATUS_APPROVED,
        "declined": STATUS_REJECTED,
        "pending_review": STATUS_IN_REVIEW,
    }
    return mapping.get(didit_status, STATUS_PENDING)


def map_metamap_status(mm_status: str) -> str:
    """Map a MetaMap-specific status string to a common TengaSale status."""
    mapping = {
        "reviewNeeded": STATUS_IN_REVIEW,
        "reviewCompleted": STATUS_APPROVED,
        "verified": STATUS_APPROVED,
        "rejected": STATUS_REJECTED,
        "inconclusive": STATUS_REQUIRES_RETRY,
        "error": STATUS_FAILED,
    }
    return mapping.get(mm_status, STATUS_PENDING)


def start_verification(application) -> dict[str, Any]:
    """
    Start a KYC verification session for the given application.

    Returns a dict with:
      provider        : active provider name
      reference       : provider-specific verification reference / session ID
      verification_url: URL to redirect the customer to (if applicable)
      status          : initial common status
      error           : error message if start failed (empty string on success)
    """
    provider = get_active_provider()
    result: dict[str, Any] = {
        "provider": provider,
        "reference": None,
        "verification_url": None,
        "status": STATUS_NOT_STARTED,
        "error": "",
    }

    if provider == "didit":
        try:
            from applications.didit_views import _start_didit_session  # type: ignore
            session = _start_didit_session(application)
            result["reference"] = session.get("session_id") or session.get("id")
            result["verification_url"] = session.get("url") or session.get("verification_url")
            result["status"] = STATUS_PENDING
        except Exception as exc:
            logger.exception("Didit start_verification failed: %s", exc)
            result["error"] = str(exc)
            result["status"] = STATUS_FAILED

    elif provider == "metamap":
        try:
            reference, url = _start_metamap_verification(application)
            result["reference"] = reference
            result["verification_url"] = url
            result["status"] = STATUS_PENDING
        except Exception as exc:
            logger.exception("MetaMap start_verification failed: %s", exc)
            result["error"] = str(exc)
            result["status"] = STATUS_FAILED

    else:
        # Manual KYC — no remote session needed
        result["status"] = STATUS_NOT_STARTED
        result["provider"] = "manual"

    return result


def get_verification_status(application) -> str:
    """
    Return the current common KYC status for an application.
    For manual KYC, returns STATUS_APPROVED if all images are uploaded,
    otherwise STATUS_NOT_STARTED.
    """
    provider = get_active_provider()

    if provider == "didit":
        raw = getattr(application, "didit_status", None) or getattr(
            application, "kyc_status", None
        )
        if raw:
            return map_didit_status(str(raw))
        return STATUS_NOT_STARTED

    if provider == "metamap":
        raw = getattr(application, "metamap_status", None) or getattr(
            application, "kyc_status", None
        )
        if raw:
            return map_metamap_status(str(raw))
        return STATUS_NOT_STARTED

    # Manual KYC
    has_selfie = bool(getattr(application, "customer_face_image", None))
    has_id = bool(getattr(application, "id_front_image", None))
    if has_selfie and has_id:
        return STATUS_IN_REVIEW
    return STATUS_NOT_STARTED


def handle_webhook(payload: dict[str, Any], provider: str | None = None) -> dict[str, Any]:
    """
    Handle an incoming webhook from a KYC provider.

    Returns a dict with:
      application_id : ID of the application this relates to (or None)
      new_status     : common KYC status mapped from provider payload
      raw_status     : original provider status string
      error          : error message (empty on success)
    """
    active = provider or get_active_provider()
    result: dict[str, Any] = {
        "application_id": None,
        "new_status": STATUS_PENDING,
        "raw_status": "",
        "error": "",
    }

    if active == "didit":
        try:
            from applications.didit_handlers import handle_didit_webhook_payload  # type: ignore
            data = handle_didit_webhook_payload(payload)
            result.update(data)
            if "status" in data:
                result["new_status"] = map_didit_status(data["status"])
                result["raw_status"] = data["status"]
        except Exception as exc:
            logger.exception("Didit webhook handling failed: %s", exc)
            result["error"] = str(exc)

    elif active == "metamap":
        try:
            data = _handle_metamap_webhook(payload)
            result.update(data)
            if "status" in data:
                result["new_status"] = map_metamap_status(data["status"])
                result["raw_status"] = data["status"]
        except Exception as exc:
            logger.exception("MetaMap webhook handling failed: %s", exc)
            result["error"] = str(exc)

    return result


# ── MetaMap (stub — add real implementation when credentials are available) ────

def _start_metamap_verification(application) -> tuple[str, str]:
    """
    Start a MetaMap verification. Returns (reference, verification_url).
    Requires: METAMAP_CLIENT_ID, METAMAP_CLIENT_SECRET, METAMAP_FLOW_ID in settings.
    """
    import requests  # type: ignore

    client_id = settings.METAMAP_CLIENT_ID  # type: ignore
    client_secret = settings.METAMAP_CLIENT_SECRET  # type: ignore
    flow_id = getattr(settings, "METAMAP_FLOW_ID", "")

    # Get OAuth token
    token_resp = requests.post(
        "https://api.metamap.com/oauth",
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        timeout=20,
    )
    token_resp.raise_for_status()
    token = token_resp.json()["access_token"]

    # Create verification
    resp = requests.post(
        "https://api.metamap.com/v1/verifications",
        json={
            "flowId": flow_id,
            "metadata": {
                "applicationId": str(application.pk),
                "applicantName": getattr(application, "customer_name", ""),
            },
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    reference = data.get("id", "")
    url = data.get("verificationUrl") or data.get("url") or ""
    return reference, url


def _handle_metamap_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    """Parse a MetaMap webhook payload and return normalised data."""
    event_name = payload.get("eventName", "")
    resource = payload.get("resource", {})
    verification_id = resource.get("id", "")
    metamap_status = payload.get("status") or resource.get("status", "")
    metadata = resource.get("metadata", {})
    application_id = metadata.get("applicationId")
    return {
        "application_id": application_id,
        "verification_id": verification_id,
        "status": metamap_status,
        "event": event_name,
    }


def verify_metamap_webhook_signature(payload_bytes: bytes, signature: str) -> bool:
    """
    Verify a MetaMap webhook signature.
    MetaMap uses HMAC-SHA256 with the METAMAP_WEBHOOK_SECRET.
    """
    import hashlib
    import hmac

    secret = getattr(settings, "METAMAP_WEBHOOK_SECRET", "")
    if not secret:
        logger.warning("METAMAP_WEBHOOK_SECRET not configured — skipping signature check.")
        return True
    mac = hmac.new(secret.encode(), payload_bytes, hashlib.sha256)
    expected = mac.hexdigest()
    return hmac.compare_digest(expected, signature.lstrip("sha256="))
