"""HTTP endpoints for Didit KYC integration."""
from __future__ import annotations

import json
import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from accounts.decorators import merchant_required
from accounts.utils import is_hq, is_underwriter
from applications.didit_handlers import (
    apply_didit_decision,
    apply_didit_session_response,
    find_application_for_webhook,
    process_didit_webhook_payload,
)
from applications.models import FinancingApplication
from integrations.didit import (
    DiditAPIError,
    DiditConfigurationError,
    KYC_STATUS_DISPLAY,
    can_restart_didit_session,
    create_didit_session,
    didit_production_mode,
    format_didit_session_error_message,
    has_active_didit_session,
    retrieve_didit_decision,
    summarize_didit_api_error,
    verify_didit_webhook_signature_v2,
)

logger = logging.getLogger("applications.didit_views")


def user_can_manage_didit(user, application) -> bool:
    if user.is_superuser or is_hq(user) or is_underwriter(user):
        return True
    return application.created_by_id == user.id


def user_can_refresh_didit(user) -> bool:
    return bool(user.is_authenticated and (user.is_superuser or is_hq(user) or is_underwriter(user)))


def get_application_for_didit(request, app_id):
    app = get_object_or_404(FinancingApplication, pk=app_id)
    if not user_can_manage_didit(request.user, app):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    return app


def _format_didit_detail_snippet(detail) -> str:
    if not detail:
        return ""
    if isinstance(detail, dict):
        for key in ("message", "detail", "error", "errors"):
            value = detail.get(key)
            if value:
                return str(value)[:500]
        return str(detail)[:500]
    return str(detail)[:500]


def _didit_api_error_response(exc: DiditAPIError, *, app_id: int | None = None) -> JsonResponse:
    didit_detail = exc.payload if exc.payload is not None else {}
    error_text = exc.user_message or format_didit_session_error_message(
        status_code=exc.status_code,
        data=didit_detail,
        raw_text=exc.response_text or "",
    )
    detail_snippet = summarize_didit_api_error(didit_detail, exc.response_text or "") or _format_didit_detail_snippet(
        didit_detail
    )
    logger.error(
        "Didit API error for application %s (status=%s) error=%s detail=%s keys=%s",
        app_id or "?",
        exc.status_code,
        error_text[:500],
        detail_snippet or (exc.response_text or "")[:500],
        exc.request_payload_keys,
    )
    body: dict = {
        "success": False,
        "error": error_text,
        "didit_error_text": detail_snippet or error_text,
        "didit_status": exc.status_code,
        "didit_detail": didit_detail,
    }
    if exc.request_payload_keys:
        body["request_payload_keys"] = exc.request_payload_keys
    return JsonResponse(body, status=200)


@login_required
@require_POST
def didit_start_kyc(request, app_id):
    try:
        app = get_application_for_didit(request, app_id)
    except Exception:
        return JsonResponse(
            {"success": False, "error": "You do not have permission to start KYC for this application."},
            status=403,
        )

    if has_active_didit_session(app) and not can_restart_didit_session(app, request.user):
        return JsonResponse({
            "success": True,
            "session_id": app.didit_session_id,
            "url": app.didit_verification_url,
            "status": app.didit_status or app.get_kyc_status_display(),
            "message": "An active Didit verification session already exists.",
        })

    try:
        response = create_didit_session(app)
    except DiditConfigurationError as exc:
        return JsonResponse({"success": False, "error": str(exc)})
    except DiditAPIError as exc:
        return _didit_api_error_response(exc, app_id=app.pk)

    apply_didit_session_response(app, response)
    app.save(
        update_fields=[
            "kyc_provider",
            "kyc_status",
            "didit_session_id",
            "didit_session_token",
            "didit_verification_url",
            "didit_status",
            "didit_workflow_id",
            "didit_vendor_data",
            "didit_metadata",
            "didit_started_at",
        ]
    )

    return JsonResponse({
        "success": True,
        "session_id": app.didit_session_id,
        "url": app.didit_verification_url,
        "status": app.didit_status or KYC_STATUS_DISPLAY.get(app.kyc_status, app.kyc_status),
    })


@merchant_required
def didit_verification_step(request, app_id):
    """Legacy URL — Didit KYC is on the main KYC step after deal selection."""
    from applications.flow_helpers import application_has_complete_deal
    from applications.views import merchant_application

    app = merchant_application(request, app_id)
    if not application_has_complete_deal(app):
        return redirect("choose_device", app_id=app.id)
    return redirect("kyc_capture", app_id=app.id)


@require_GET
def didit_callback_done(request):
    session_id = (request.GET.get("session_id") or request.GET.get("sessionId") or "").strip()
    vendor_data = (request.GET.get("vendor_data") or request.GET.get("application_id") or "").strip()

    app = None
    status_label = ""
    if vendor_data:
        try:
            app = FinancingApplication.objects.filter(pk=int(vendor_data)).first()
        except (TypeError, ValueError):
            app = None
    if not app and session_id:
        app = FinancingApplication.objects.filter(didit_session_id=session_id).first()

    if app:
        status_label = KYC_STATUS_DISPLAY.get(app.kyc_status, app.didit_status or "In Progress")

    return render(
        request,
        "applications/didit_callback_done.html",
        {
            "app": app,
            "session_id": session_id,
            "status_label": status_label,
        },
    )


def _webhook_log_context(payload: dict) -> dict:
    app = find_application_for_webhook(payload)
    return {
        "event_id": payload.get("event_id"),
        "session_id": payload.get("session_id"),
        "status": payload.get("status"),
        "webhook_type": payload.get("webhook_type"),
        "application_id": app.pk if app else None,
    }


@csrf_exempt
@require_POST
def didit_webhook(request):
    raw_body = request.body
    signature = request.headers.get("X-Signature-V2") or request.META.get("HTTP_X_SIGNATURE_V2")
    timestamp = request.headers.get("X-Timestamp") or request.META.get("HTTP_X_TIMESTAMP")

    if didit_production_mode() and not (getattr(settings, "DIDIT_WEBHOOK_SECRET", "") or "").strip():
        logger.error("Didit webhook rejected in production: DIDIT_WEBHOOK_SECRET is not configured.")
        return JsonResponse({"ok": False, "error": "Webhook secret not configured."}, status=401)

    ok, error = verify_didit_webhook_signature_v2(raw_body, signature, timestamp)
    if not ok:
        logger.warning("Didit webhook signature rejected: %s", error)
        return JsonResponse({"ok": False, "error": error}, status=401)

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    if not isinstance(payload, dict):
        return JsonResponse({"ok": False, "error": "Expected JSON object"}, status=400)

    log_ctx = _webhook_log_context(payload)
    logger.info(
        "Didit webhook received event_id=%s session_id=%s status=%s webhook_type=%s application_id=%s",
        log_ctx["event_id"],
        log_ctx["session_id"],
        log_ctx["status"],
        log_ctx["webhook_type"],
        log_ctx["application_id"],
    )

    processed_ok, message = process_didit_webhook_payload(payload)
    if processed_ok:
        logger.info(
            "Didit webhook processed event_id=%s application_id=%s message=%s",
            log_ctx["event_id"],
            log_ctx["application_id"],
            message,
        )
        return JsonResponse({"ok": True, "message": message}, status=200)

    logger.error(
        "Didit webhook processing failed event_id=%s application_id=%s message=%s",
        log_ctx["event_id"],
        log_ctx["application_id"],
        message,
    )
    return JsonResponse({"ok": False, "message": message}, status=500)


@login_required
@require_POST
def didit_refresh_decision(request, app_id):
    if not user_can_refresh_didit(request.user):
        return JsonResponse(
            {"success": False, "error": "Only underwriters or HQ staff can refresh Didit decisions."},
            status=403,
        )

    app = get_object_or_404(FinancingApplication, pk=app_id)

    if not app.didit_session_id:
        return JsonResponse({"success": False, "error": "No Didit session on this application."})

    try:
        decision = retrieve_didit_decision(app.didit_session_id)
    except DiditConfigurationError as exc:
        return JsonResponse({"success": False, "error": str(exc)})
    except DiditAPIError as exc:
        return _didit_api_error_response(exc, app_id=app.pk)

    apply_didit_decision(app, decision)
    status_raw = decision.get("status") or app.didit_status
    if status_raw:
        from integrations.didit import map_didit_status
        app.didit_status = status_raw
        app.kyc_status = map_didit_status(status_raw)
    app.save(update_fields=["didit_decision", "didit_summary", "didit_status", "kyc_status"])
    return JsonResponse({"success": True, "kyc_status": app.kyc_status})
