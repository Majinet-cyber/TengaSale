"""Apply Didit webhook payloads and API responses to FinancingApplication records."""
from __future__ import annotations

import logging
from typing import Any

from django.db import transaction
from django.utils import timezone

from integrations.didit import extract_didit_summary, map_didit_status

logger = logging.getLogger("applications.didit_handlers")


def apply_didit_session_response(application, response: dict) -> None:
    """Persist fields from create-session API response."""
    application.kyc_provider = "didit"
    application.didit_session_id = response.get("session_id") or application.didit_session_id
    application.didit_session_token = response.get("session_token") or application.didit_session_token
    application.didit_verification_url = response.get("url") or application.didit_verification_url
    application.didit_status = response.get("status") or application.didit_status
    application.didit_workflow_id = response.get("workflow_id") or application.didit_workflow_id
    application.didit_vendor_data = response.get("vendor_data") or str(application.pk)
    if response.get("metadata"):
        application.didit_metadata = response.get("metadata")
    application.kyc_status = map_didit_status(application.didit_status)
    if not application.didit_started_at:
        application.didit_started_at = timezone.now()


def apply_didit_decision(application, decision: dict | None) -> None:
    if not decision:
        return
    application.didit_decision = decision
    application.didit_summary = extract_didit_summary(decision)


def _parse_application_id(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def find_application_for_webhook(payload: dict):
    from applications.models import FinancingApplication

    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    app_id = _parse_application_id(
        metadata.get("application_id")
        or payload.get("application_id")
        or payload.get("vendor_data")
    )
    if app_id:
        app = FinancingApplication.objects.filter(pk=app_id).first()
        if app:
            return app

    session_id = (payload.get("session_id") or "").strip()
    if session_id:
        return FinancingApplication.objects.filter(didit_session_id=session_id).first()
    return None


def apply_didit_webhook_status(application, payload: dict) -> None:
    """Update KYC fields from a Didit webhook payload."""
    status_raw = payload.get("status") or application.didit_status
    mapped = map_didit_status(status_raw)

    application.kyc_provider = "didit"
    application.didit_status = status_raw
    application.kyc_status = mapped
    application.didit_last_event_id = payload.get("event_id") or application.didit_last_event_id

    if payload.get("session_id"):
        application.didit_session_id = payload["session_id"]
    if payload.get("workflow_id"):
        application.didit_workflow_id = payload["workflow_id"]
    if payload.get("vendor_data"):
        application.didit_vendor_data = str(payload["vendor_data"])
    if payload.get("metadata"):
        application.didit_metadata = payload["metadata"]

    decision = payload.get("decision")
    if decision:
        apply_didit_decision(application, decision)

    resubmit_info = payload.get("resubmit_info")
    if resubmit_info:
        application.didit_resubmit_info = resubmit_info

    now = timezone.now()

    if mapped == "approved":
        application.didit_verified_at = application.didit_verified_at or now
        application.didit_completed_at = application.didit_completed_at or now
    elif mapped == "declined":
        application.didit_declined_at = application.didit_declined_at or now
    elif mapped in {"abandoned", "expired", "kyc_expired"}:
        if decision:
            application.didit_completed_at = application.didit_completed_at or now

    if mapped in {"pending_review", "approved", "declined"} and not application.didit_completed_at:
        pass


def process_didit_webhook_payload(payload: dict) -> tuple[bool, str]:
    """
    Idempotently process webhook JSON. Returns (processed_ok, message).
    """
    from applications.models import DiditWebhookEvent, FinancingApplication

    event_id = (payload.get("event_id") or "").strip()
    if not event_id:
        return False, "Missing event_id"

    if DiditWebhookEvent.objects.filter(event_id=event_id).exists():
        return True, "Duplicate event ignored"

    event = DiditWebhookEvent.objects.create(
        event_id=event_id,
        session_id=(payload.get("session_id") or "")[:120],
        webhook_type=(payload.get("webhook_type") or "")[:80],
        status=(payload.get("status") or "")[:80],
        payload=payload,
    )

    try:
        with transaction.atomic():
            application = find_application_for_webhook(payload)
            if not application:
                event.processing_error = "Application not found for webhook payload."
                event.save(update_fields=["processing_error"])
                return True, event.processing_error

            apply_didit_webhook_status(application, payload)
            application.save(
                update_fields=[
                    "kyc_provider",
                    "kyc_status",
                    "didit_status",
                    "didit_session_id",
                    "didit_workflow_id",
                    "didit_vendor_data",
                    "didit_metadata",
                    "didit_decision",
                    "didit_summary",
                    "didit_resubmit_info",
                    "didit_last_event_id",
                    "didit_verified_at",
                    "didit_completed_at",
                    "didit_declined_at",
                ]
            )
            event.processed = True
            event.save(update_fields=["processed"])
        return True, "Processed"
    except Exception as exc:
        logger.exception("Didit webhook processing failed for event %s", event_id)
        event.processing_error = str(exc)[:2000]
        event.save(update_fields=["processing_error"])
        return False, str(exc)
