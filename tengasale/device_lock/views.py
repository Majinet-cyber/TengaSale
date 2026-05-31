"""
Views for Device Lock Integration Layer.

All lock actions go through the service layer.
HQ-only actions require the hq_required decorator.
"""

import logging
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from accounts.decorators import hq_required
from accounts.utils import is_hq, is_underwriter
from device_lock.models import DeviceLockProfile, DeviceLockEvent, DeviceLockPolicy

logger = logging.getLogger("tengasale.device_lock")


# ──────────────────────────────────────────────────────────────────────────────
# HQ — Lock Control Centre
# ──────────────────────────────────────────────────────────────────────────────

@hq_required
def hq_device_lock_centre(request):
    """
    HQ Device Lock Control Centre.
    Summary cards + action tables for enrollment, locking, and audit.
    """
    from django.db.models import Count, Q

    profiles = DeviceLockProfile.objects.select_related("merchant", "contract", "application")

    # Summary counts
    stats = {
        "lockable": profiles.filter(is_lockable=True, is_released=False).count(),
        "enrolled": profiles.filter(lock_status__in=["enrolled", "active"]).count(),
        "locked": profiles.filter(lock_status="locked").count(),
        "unlock_pending": profiles.filter(lock_status="unlock_pending").count(),
        "lock_pending": profiles.filter(lock_status="lock_pending").count(),
        "failures": profiles.filter(lock_status="failed").count(),
        "released": profiles.filter(is_released=True).count(),
        "not_enrolled": profiles.filter(lock_status="not_enrolled").count(),
    }

    # Tables
    needs_enrollment = profiles.filter(
        lock_status="not_enrolled", is_lockable=True, is_released=False
    ).order_by("-created_at")[:50]

    overdue_candidates = profiles.filter(
        lock_status__in=["enrolled", "active", "unlocked"],
        is_released=False,
    ).order_by("-updated_at")[:50]

    locked_devices = profiles.filter(lock_status="locked").order_by("-updated_at")[:50]

    failed_actions = DeviceLockEvent.objects.filter(
        event_type__in=[
            DeviceLockEvent.TYPE_ENROLL_FAILED,
            DeviceLockEvent.TYPE_LOCK_FAILED,
            DeviceLockEvent.TYPE_UNLOCK_FAILED,
            DeviceLockEvent.TYPE_RELEASE_FAILED,
        ]
    ).select_related("lock_profile", "triggered_by").order_by("-created_at")[:30]

    recent_synced = profiles.filter(last_sync_at__isnull=False).order_by("-last_sync_at")[:20]

    recent_events = DeviceLockEvent.objects.select_related(
        "lock_profile", "triggered_by"
    ).order_by("-created_at")[:30]

    policies = DeviceLockPolicy.objects.all().order_by("-is_active", "name")

    lock_enabled = getattr(settings, "DEVICE_LOCK_ENABLED", False)
    lock_provider = getattr(settings, "DEVICE_LOCK_PROVIDER", "mock")

    return render(request, "device_lock/hq_lock_centre.html", {
        "title": "Device Lock Control Centre",
        "stats": stats,
        "needs_enrollment": needs_enrollment,
        "overdue_candidates": overdue_candidates,
        "locked_devices": locked_devices,
        "failed_actions": failed_actions,
        "recent_synced": recent_synced,
        "recent_events": recent_events,
        "policies": policies,
        "lock_enabled": lock_enabled,
        "lock_provider": lock_provider,
    })


@hq_required
@require_POST
def hq_lock_action(request, profile_pk):
    """
    Perform a manual lock action (enroll/lock/unlock/release/sync)
    on a specific DeviceLockProfile. HQ only.
    """
    from services.device_lock.service import (
        enroll_device, lock_device, unlock_device,
        release_device, sync_device_status,
    )
    from device_lock.models import DeviceLockEvent

    profile = get_object_or_404(DeviceLockProfile, pk=profile_pk)
    action = request.POST.get("action", "")
    reason = request.POST.get("reason", "Manual action by HQ")

    source = DeviceLockEvent.SOURCE_ADMIN
    result = None

    try:
        if action == "enroll":
            result = enroll_device(profile, triggered_by=request.user, trigger_source=source)
        elif action == "lock":
            result = lock_device(profile, reason=reason, triggered_by=request.user, trigger_source=source)
        elif action == "unlock":
            result = unlock_device(profile, reason=reason, triggered_by=request.user, trigger_source=source)
        elif action == "release":
            result = release_device(profile, reason=reason, triggered_by=request.user, trigger_source=source)
        elif action == "sync":
            result = sync_device_status(profile, triggered_by=request.user, trigger_source=source)
        else:
            messages.error(request, f"Unknown action: {action}")
            return redirect("device_lock:hq_lock_centre")

        if result and result.get("success"):
            messages.success(request, f"{action.title()} succeeded for IMEI {profile.imei}. {result.get('message', '')}")
        else:
            err = result.get("error") if result else "Unknown error"
            messages.warning(request, f"{action.title()} did not succeed: {err}")

    except Exception as exc:
        logger.exception("hq_lock_action failed for profile %s action %s", profile_pk, action)
        messages.error(request, f"Action failed unexpectedly: {exc}")

    return redirect("device_lock:hq_lock_centre")


@hq_required
def hq_lock_profile_detail(request, profile_pk):
    """Detail view for a single DeviceLockProfile."""
    from services.device_lock.service import get_lock_readiness

    profile = get_object_or_404(
        DeviceLockProfile.objects.select_related("contract", "application", "merchant"),
        pk=profile_pk,
    )
    events = profile.events.select_related("triggered_by").order_by("-created_at")[:50]
    readiness = get_lock_readiness(profile)
    lock_enabled = getattr(settings, "DEVICE_LOCK_ENABLED", False)
    is_mock = getattr(settings, "DEVICE_LOCK_PROVIDER", "mock") == "mock"

    return render(request, "device_lock/profile_detail.html", {
        "title": f"Lock Profile — {profile.imei}",
        "profile": profile,
        "events": events,
        "readiness": readiness,
        "lock_enabled": lock_enabled,
        "is_mock": is_mock,
    })


# ──────────────────────────────────────────────────────────────────────────────
# Webhook endpoint
# ──────────────────────────────────────────────────────────────────────────────

@csrf_exempt
def device_lock_webhook(request):
    """
    Future-ready webhook endpoint for device lock provider callbacks.

    Currently logs all incoming events. When a real provider is configured,
    implement signature verification and status updates here.
    """
    import hashlib
    import hmac
    import json

    if request.method != "POST":
        return JsonResponse({"status": "ok", "message": "Webhook endpoint ready."})

    webhook_secret = getattr(settings, "DEVICE_LOCK_WEBHOOK_SECRET", "")
    if webhook_secret:
        sig_header = request.headers.get("X-Lock-Signature", "")
        computed = hmac.new(
            webhook_secret.encode(), request.body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(f"sha256={computed}", sig_header):
            logger.warning("Device lock webhook: invalid signature")
            return JsonResponse({"error": "Invalid signature"}, status=401)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid JSON payload"}, status=400)

    imei = payload.get("imei") or payload.get("device_id") or ""
    provider = payload.get("provider", getattr(settings, "DEVICE_LOCK_PROVIDER", "unknown"))
    event_status = payload.get("status", "unknown")

    profile = None
    if imei:
        profile = DeviceLockProfile.objects.filter(imei=imei).first()
        if not profile and payload.get("provider_device_id"):
            profile = DeviceLockProfile.objects.filter(
                provider_device_id=payload["provider_device_id"]
            ).first()

    if profile:
        old_status = profile.lock_status
        new_status = _map_webhook_status(event_status)
        if new_status and new_status != old_status:
            profile.lock_status = new_status
            from django.utils import timezone
            profile.last_sync_at = timezone.now()
            profile.save(update_fields=["lock_status", "last_sync_at", "updated_at"])

        DeviceLockEvent.objects.create(
            lock_profile=profile,
            event_type=DeviceLockEvent.TYPE_WEBHOOK_RECEIVED,
            status_before=old_status,
            status_after=profile.lock_status,
            trigger_source=DeviceLockEvent.SOURCE_WEBHOOK,
            response_payload=payload,
        )
        return JsonResponse({"status": "processed", "profile_id": profile.pk})

    lock_provider_name = getattr(settings, "DEVICE_LOCK_PROVIDER", "none")
    if lock_provider_name == "none":
        return JsonResponse({
            "status": "no_provider",
            "message": "No lock provider configured. Webhook received but not processed.",
        })

    logger.info("Device lock webhook: no matching profile for IMEI=%s payload=%s", imei, payload)
    return JsonResponse({"status": "unmatched", "message": "No matching device profile found."})


def _map_webhook_status(provider_status):
    """Map incoming provider status strings to our canonical status values."""
    mapping = {
        "locked": DeviceLockProfile.STATUS_LOCKED,
        "unlocked": DeviceLockProfile.STATUS_UNLOCKED,
        "enrolled": DeviceLockProfile.STATUS_ENROLLED,
        "released": DeviceLockProfile.STATUS_RELEASED,
        "failed": DeviceLockProfile.STATUS_FAILED,
        "active": DeviceLockProfile.STATUS_ACTIVE,
    }
    return mapping.get(str(provider_status).lower())
