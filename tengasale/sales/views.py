"""
Sales / Underwriter app views.

Provides the clean /sales/ routing layer on top of existing approvals logic.
All review-step business logic lives in approvals.views; this module adds the
clean URL surface and enriched context for the polished sales UI.
"""

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Sum
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import role_required, underwriter_required
from accounts.utils import is_hq, is_underwriter
from applications.models import ApplicationCorrection, ApplicationFieldReview, FinancingApplication
from applications.models import ApplicationCorrectionToken
from approvals.forms import CustomerCallQuestionnaireForm
from approvals.models import (
    CallEvidence,
    CustomerCallQuestionnaire,
    UnderwriterCallRecording,
    UnderwriterQCPenalty,
    UnderwriterReview,
)
from approvals.views import (
    MAX_ACTIVE,
    bool_from_post,
    cancel_application_commissions,
    correction_context,
    get_review,
    process_application_approval,
    review_guard,
    sync_legacy_corrections,
)
from commissions.models import Commission, CommissionLedger, UnderwriterMonthlyPayout
from commissions.services import get_underwriter_wallet_summary
from contracts.models import Contract
from core.models import AuditLog, QueueRule
from core.view_safety import safe_page
from earnings.models import ManagerPayout, MerchantPayout, Wallet, WalletTransaction
from rewards.models import SpinReward, SpinWallet


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _queue_rule():
    return QueueRule.for_country("MW")


def _cooldown_state(user):
    """Return (cooldown_remaining_seconds, can_claim_now) for the given user."""
    rule = _queue_rule()
    active_count = FinancingApplication.objects.filter(
        claimed_by=user, status="under_review"
    ).count()

    if active_count >= rule.max_active_applications:
        return 0, False

    last_claim = (
        FinancingApplication.objects.filter(claimed_by=user)
        .exclude(claimed_at=None)
        .order_by("-claimed_at")
        .first()
    )
    if last_claim and last_claim.claimed_at:
        elapsed = (timezone.now() - last_claim.claimed_at).total_seconds()
        cooldown_seconds = rule.cooldown_minutes * 60
        remaining = max(0, cooldown_seconds - elapsed)
        if remaining > 0:
            return remaining, False

    return 0, True


def _audit(user, action, obj_type="", obj_id="", detail=None, request=None):
    ip = None
    if request:
        x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        ip = x_forwarded.split(",")[0].strip() if x_forwarded else request.META.get("REMOTE_ADDR")
    AuditLog.objects.create(
        user=user,
        action=action,
        object_type=obj_type,
        object_id=str(obj_id),
        detail=detail or {},
        ip_address=ip,
    )


def _underwriter_or_hq(user):
    return is_underwriter(user) or is_hq(user) or user.is_staff or user.is_superuser


def _questionnaire_app_for_user(request, app_id):
    if is_hq(request.user) or request.user.is_staff or request.user.is_superuser:
        return get_object_or_404(
            FinancingApplication.objects.select_related("deal", "deal__brand", "created_by", "claimed_by"),
            id=app_id,
        ), None
    return review_guard(request, app_id)


def _merchant_business_name(app):
    try:
        merchant = app.created_by.merchant_set.first()
        if merchant:
            return merchant.business_name or merchant.trading_name or merchant.owner.get_full_name() or merchant.owner.username
    except Exception:
        pass
    return app.created_by.get_full_name() or app.created_by.username


def _format_review_duration(seconds):
    if not seconds:
        return "No data"
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    remaining_minutes = minutes % 60
    if remaining_minutes:
        return f"{hours}h {remaining_minutes}m"
    return f"{hours}h"


def _active_app_card(app):
    deal = app.deal
    device_label = str(deal) if deal else "Device pending"
    location = ", ".join(part for part in [app.region, app.district] if part) or "Location pending"
    score = app.repayment_confidence_score
    if score is None:
        risk_label = "Risk pending"
        risk_class = "neutral"
    elif score >= 75:
        risk_label = "Strong"
        risk_class = "strong"
    elif score >= 50:
        risk_label = "Moderate"
        risk_class = "moderate"
    else:
        risk_label = "Watchlist"
        risk_class = "watch"
    return {
        "app": app,
        "merchant_name": _merchant_business_name(app),
        "device_label": device_label,
        "location": location,
        "risk_label": risk_label,
        "risk_class": risk_class,
        "risk_score_display": score if score is not None else "",
    }


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------

@underwriter_required
@safe_page("Underwriter dashboard")
def sales_home(request):
    rule = _queue_rule()
    active_apps = FinancingApplication.objects.filter(
        claimed_by=request.user, status="under_review"
    ).select_related("deal", "deal__brand", "created_by").order_by("-claimed_at")
    active_count = active_apps.count()

    pending_qs = FinancingApplication.objects.filter(
        status="pending_review", claimed_by__isnull=True
    )
    pending_count = pending_qs.count()

    today = timezone.localdate()
    completed_statuses = [
        "approved",
        "approved_pending_device_lock",
        "device_locked",
        "active_contract",
        "completed",
        "contract_complete",
    ]

    completed_count = FinancingApplication.objects.filter(
        reviewed_by=request.user,
        status__in=completed_statuses,
    ).count()
    rejected_count = FinancingApplication.objects.filter(
        reviewed_by=request.user, status="rejected"
    ).count()
    reviewed_count = completed_count + rejected_count
    approved_today = FinancingApplication.objects.filter(
        reviewed_by=request.user,
        reviewed_at__date=today,
        status__in=completed_statuses,
    ).count()
    rejected_today = FinancingApplication.objects.filter(
        reviewed_by=request.user,
        reviewed_at__date=today,
        status="rejected",
    ).count()
    urgent_threshold = timezone.now() - timedelta(minutes=30)
    urgent_count = pending_qs.filter(submitted_at__lte=urgent_threshold).count()

    reviewed_samples = FinancingApplication.objects.filter(
        reviewed_by=request.user,
        submitted_at__isnull=False,
        reviewed_at__isnull=False,
        status__in=completed_statuses + ["rejected"],
    ).only("submitted_at", "reviewed_at").order_by("-reviewed_at")[:50]
    review_durations = [
        (app.reviewed_at - app.submitted_at).total_seconds()
        for app in reviewed_samples
        if app.reviewed_at and app.submitted_at and app.reviewed_at >= app.submitted_at
    ]
    average_review_time_label = _format_review_duration(
        sum(review_durations) / len(review_durations) if review_durations else None
    )

    cooldown_remaining, can_claim = _cooldown_state(request.user)

    wallet, _ = Wallet.objects.get_or_create(user=request.user)
    active_app_cards = [_active_app_card(app) for app in active_apps[:5]]

    return render(request, "sales/home.html", {
        "page_heading": "Home",
        "active_apps": active_apps[:5],
        "active_app_cards": active_app_cards,
        "active_count": active_count,
        "max_active": rule.max_active_applications,
        "pending_count": pending_count,
        "approved_today": approved_today,
        "rejected_today": rejected_today,
        "urgent_count": urgent_count,
        "average_review_time_label": average_review_time_label,
        "completed_count": completed_count,
        "reviewed_count": reviewed_count,
        "cooldown_remaining": int(cooldown_remaining),
        "can_claim": can_claim and pending_count > 0,
        "queue_rule": rule,
        "wallet": wallet,
    })


@underwriter_required
def sales_queue_status(request):
    """JSON polling endpoint — used by Alpine.js every N seconds."""
    rule = _queue_rule()
    active_count = FinancingApplication.objects.filter(
        claimed_by=request.user, status="under_review"
    ).count()
    pending_count = FinancingApplication.objects.filter(
        status="pending_review", claimed_by__isnull=True
    ).count()
    cooldown_remaining, can_claim = _cooldown_state(request.user)
    return JsonResponse({
        "active_count": active_count,
        "max_active": rule.max_active_applications,
        "pending_count": pending_count,
        "cooldown_remaining": int(cooldown_remaining),
        "can_claim": can_claim and pending_count > 0,
    })


# ---------------------------------------------------------------------------
# Claim
# ---------------------------------------------------------------------------

@underwriter_required
@require_POST
def sales_claim_next(request):
    rule = _queue_rule()
    active_count = FinancingApplication.objects.filter(
        claimed_by=request.user, status="under_review"
    ).count()

    if active_count >= rule.max_active_applications:
        messages.error(request, f"You already have {active_count} active applications. Complete one before claiming more.")
        return redirect("sales_home")

    _, can_claim = _cooldown_state(request.user)
    if not can_claim:
        messages.error(request, "Please wait before claiming another application.")
        return redirect("sales_home")

    with transaction.atomic():
        app = (
            FinancingApplication.objects.select_for_update()
            .filter(status="pending_review", claimed_by__isnull=True)
            .order_by("submitted_at", "id")
            .first()
        )
        if not app:
            messages.info(request, "No applications pending in the queue.")
            return redirect("sales_home")
        app.claimed_by = request.user
        app.claimed_at = timezone.now()
        app.status = "under_review"
        app.review_status = "under_review"
        app.save(update_fields=["claimed_by", "claimed_at", "status", "review_status"])

    _audit(request.user, AuditLog.ACTION_CLAIM, "FinancingApplication", app.id,
           {"application_number": app.application_number}, request)
    try:
        from notifications.utils import notify_application_claimed

        notify_application_claimed(app, request.user)
    except Exception:
        pass
    messages.success(request, f"Application {app.application_number} claimed.")
    return redirect("sales_review_summary", app_id=app.id)


# ---------------------------------------------------------------------------
# Applications list
# ---------------------------------------------------------------------------

@underwriter_required
@safe_page("Underwriter applications")
def sales_applications(request):
    tab = request.GET.get("tab", "active")
    if tab == "completed":
        apps = FinancingApplication.objects.filter(
            reviewed_by=request.user,
            status__in=["approved", "approved_pending_device_lock", "device_locked", "active_contract", "completed", "contract_complete"],
        ).order_by("-reviewed_at")
    elif tab == "rejected":
        apps = FinancingApplication.objects.filter(
            reviewed_by=request.user, status="rejected"
        ).order_by("-reviewed_at")
    elif tab == "sent_back":
        apps = FinancingApplication.objects.filter(
            reviewed_by=request.user, status__in=["sent_back", "correction_requested"]
        ).order_by("-reviewed_at")
    elif tab == "queue":
        apps = FinancingApplication.objects.none()
    else:
        apps = FinancingApplication.objects.filter(
            claimed_by=request.user, status="under_review"
        ).order_by("-claimed_at")
        tab = "active"

    rule = _queue_rule()
    active_count = FinancingApplication.objects.filter(
        claimed_by=request.user, status="under_review"
    ).count()
    pending_count = FinancingApplication.objects.filter(
        status="pending_review", claimed_by__isnull=True
    ).count()
    cooldown_remaining, can_claim = _cooldown_state(request.user)

    return render(request, "sales/applications_list.html", {
        "page_heading": "Applications",
        "apps": apps.select_related("deal", "created_by")[:50],
        "tab": tab,
        "pending_count": pending_count,
        "active_count": active_count,
        "max_active": rule.max_active_applications,
        "cooldown_remaining": int(cooldown_remaining),
        "can_claim": can_claim and pending_count > 0,
    })


# ---------------------------------------------------------------------------
# Review steps (new templates, same business logic as approvals.views)
# ---------------------------------------------------------------------------

@underwriter_required
def sales_review_summary(request, app_id):
    from applications.services.duplicate_check import check_duplicate_customer
    app, response = review_guard(request, app_id)
    if response:
        return response
    review = get_review(app, request.user)

    # Run duplicate check and log if found
    dup_result = check_duplicate_customer(app)
    if dup_result["has_duplicates"]:
        _audit(request.user, "duplicate_detected", "FinancingApplication", app.pk,
               {"app": app.application_number, "flags": [f["type"] for f in dup_result["flags"]]},
               request)

    if request.method == "POST":
        review.summary_clear = bool_from_post(request, "summary_clear")
        review.save(update_fields=["summary_clear", "updated_at"])
        return redirect("sales_identity_check", app_id=app.id)
    return render(request, "sales/review_summary.html", {
        "page_heading": "Review Application",
        "app": app, "review": review, "step": 1, "total_steps": 6,
        "dup_result": dup_result,
        **correction_context(app),
    })


@underwriter_required
def sales_identity_check(request, app_id):
    app, response = review_guard(request, app_id)
    if response:
        return response
    review = get_review(app, request.user)
    if request.method == "POST":
        review.identity_signature_matches = bool_from_post(request, "identity_signature_matches")
        review.identity_info_matches = bool_from_post(request, "identity_info_matches")
        review.save(update_fields=["identity_signature_matches", "identity_info_matches", "updated_at"])
        return redirect("sales_address_check", app_id=app.id)
    return render(request, "sales/identity_check.html", {
        "page_heading": "Identity Check",
        "app": app, "review": review, "step": 2, "total_steps": 6,
        **correction_context(app),
    })


@underwriter_required
def sales_address_check(request, app_id):
    app, response = review_guard(request, app_id)
    if response:
        return response
    review = get_review(app, request.user)
    fields = ["location_neighbour_spoken", "location_confirmed", "location_traceable", "location_address_clear"]
    if request.method == "POST":
        for field in fields:
            setattr(review, field, bool_from_post(request, field))
        review.save(update_fields=[*fields, "updated_at"])
        app.address_check_answers = {
            "spoke_to_neighbour": review.location_neighbour_spoken,
            "neighbour_confirmed_location": review.location_confirmed,
            "can_locate_if_defaulted": review.location_traceable,
            "location_description_clear": review.location_address_clear,
        }
        app.save(update_fields=["address_check_answers"])
        return redirect("sales_customer_call", app_id=app.id)
    return render(request, "sales/address_check.html", {
        "page_heading": "Address Check",
        "app": app, "review": review, "step": 3, "total_steps": 6,
        **correction_context(app),
    })


ALLOWED_AUDIO_TYPES = {
    "audio/aac", "audio/mpeg", "audio/mp3", "audio/mp4",
    "audio/wav", "audio/webm", "audio/ogg", "audio/x-m4a", "video/webm",
}
ALLOWED_AUDIO_EXTENSIONS = (".mp3", ".m4a", ".wav", ".aac", ".ogg", ".webm")


def _call_recording_limit_bytes():
    return int(getattr(settings, "MAX_CALL_RECORDING_SIZE_MB", 20)) * 1024 * 1024


def _recording_contract_for_application(app):
    related = getattr(app, "portal_contracts", None)
    if related is None:
        return None
    return related.order_by("-created_at").first()


def _validate_underwriter_recording_upload(request, app, uploaded, consent_acknowledged):
    max_recordings = int(getattr(settings, "MAX_CALL_RECORDINGS_PER_APPLICATION", 3))
    current_count = UnderwriterCallRecording.objects.filter(application=app).count()
    if current_count >= max_recordings:
        messages.error(request, f"Recording not saved: maximum {max_recordings} recordings per application.")
        return False
    if not consent_acknowledged:
        messages.error(request, "Recording not saved: acknowledge customer recording consent first.")
        return False
    if uploaded.size > _call_recording_limit_bytes():
        messages.error(
            request,
            f"Recording not saved: file exceeds {getattr(settings, 'MAX_CALL_RECORDING_SIZE_MB', 20)} MB limit.",
        )
        return False
    content_type = uploaded.content_type or ""
    if content_type not in ALLOWED_AUDIO_TYPES and not uploaded.name.lower().endswith(ALLOWED_AUDIO_EXTENSIONS):
        messages.error(request, "Recording not saved: use mp3, m4a, wav, aac, ogg, or webm.")
        return False
    return True


def _save_underwriter_call_recording(request, app):
    uploaded = request.FILES.get("call_recording")
    if not uploaded:
        return None
    consent_acknowledged = request.POST.get("recording_consent_acknowledged") in {"yes", "true", "on", "1"}
    if not _validate_underwriter_recording_upload(request, app, uploaded, consent_acknowledged):
        return None
    questionnaire, _created = CustomerCallQuestionnaire.objects.get_or_create(application=app)
    if consent_acknowledged and not questionnaire.recording_consent_acknowledged:
        questionnaire.recording_consent_acknowledged = True
        questionnaire.save(update_fields=["recording_consent_acknowledged", "updated_at"])
    recording = UnderwriterCallRecording.objects.create(
        application=app,
        contract=_recording_contract_for_application(app),
        underwriter=request.user,
        file=uploaded,
        original_filename=uploaded.name,
        mime_type=uploaded.content_type or "",
        file_size=uploaded.size,
        consent_acknowledged=consent_acknowledged,
    )
    _audit(
        request.user,
        AuditLog.ACTION_KYC_CHANGE,
        "UnderwriterCallRecording",
        recording.pk,
        {
            "action": "recording_uploaded",
            "application": app.application_number,
            "filename": uploaded.name,
            "file_size": uploaded.size,
        },
        request,
    )
    messages.success(request, "Call recording uploaded for HQ QC.")
    return recording


def _save_call_recording(request, app, stage):
    """Legacy call evidence upload used by non-customer-call review steps."""
    uploaded = request.FILES.get("call_recording")
    if not uploaded:
        return None
    if uploaded.size > _call_recording_limit_bytes():
        messages.warning(
            request,
            f"Recording not saved: file exceeds {getattr(settings, 'MAX_CALL_RECORDING_SIZE_MB', 20)} MB limit.",
        )
        return None
    content_type = uploaded.content_type or ""
    if content_type not in ALLOWED_AUDIO_TYPES and not uploaded.name.lower().endswith(ALLOWED_AUDIO_EXTENSIONS):
        messages.warning(request, "Recording not saved: unsupported file type.")
        return None
    notified = bool_from_post(request, f"{stage.split('_')[0]}_notified_recording")
    evidence = CallEvidence.objects.create(
        application=app,
        stage=stage,
        uploaded_by=request.user,
        audio_file=uploaded,
        customer_notified=notified,
        notification_script_confirmed=notified,
    )
    AuditLog.objects.create(
        user=request.user,
        action=AuditLog.ACTION_KYC_CHANGE,
        object_type="CallEvidence",
        object_id=str(evidence.pk),
        detail={"stage": stage, "application": app.application_number},
    )
    return evidence


def _call_recording_gate(app):
    required = bool(getattr(settings, "REQUIRE_CALL_RECORDING_FOR_APPROVAL", True))
    recordings = UnderwriterCallRecording.objects.filter(application=app)
    has_recording = recordings.exists()
    consent_acknowledged = recordings.filter(consent_acknowledged=True).exists()
    can_approve = (not required) or (has_recording and consent_acknowledged)
    reason = ""
    if required and not has_recording:
        reason = "Call recording required before approval."
    elif required and not consent_acknowledged:
        reason = "Recording consent must be acknowledged before approval."
    return {
        "required": required,
        "has_recording": has_recording,
        "consent_acknowledged": consent_acknowledged,
        "can_approve": can_approve,
        "blocked_reason": reason,
        "count": recordings.count(),
    }


@underwriter_required
def sales_customer_call(request, app_id):
    app, response = review_guard(request, app_id)
    if response:
        return response
    review = get_review(app, request.user)
    call_fields = [
        "customer_spoken",
        "customer_intro_done",
        "customer_confirmed_application",
        "customer_confirmed_device",
        "customer_confirmed_deposit",
        "customer_confirmed_repayment",
        "customer_understands_direct_payment",
        "customer_understands_nonpayment",
    ]
    if request.method == "POST":
        for field in call_fields:
            setattr(review, field, bool_from_post(request, field))
        review.save(update_fields=[*call_fields, "updated_at"])
        return redirect("sales_income_check", app_id=app.id)
    call_recordings = UnderwriterCallRecording.objects.filter(application=app).select_related("underwriter", "reviewed_by")
    questionnaire, _created = CustomerCallQuestionnaire.objects.get_or_create(application=app)
    return render(request, "sales/customer_call.html", {
        "page_heading": "Customer Call",
        "app": app, "review": review, "step": 4, "total_steps": 6,
        "call_recordings": call_recordings,
        "questionnaire": questionnaire,
        "recording_limit": getattr(settings, "MAX_CALL_RECORDINGS_PER_APPLICATION", 3),
        "recording_size_mb": getattr(settings, "MAX_CALL_RECORDING_SIZE_MB", 20),
        **correction_context(app),
    })


@underwriter_required
@require_POST
def sales_upload_call_recording(request, app_id):
    app, response = review_guard(request, app_id)
    if response:
        return response
    _save_underwriter_call_recording(request, app)
    return redirect("sales_customer_call", app_id=app.id)


@underwriter_required
@require_POST
def sales_delete_call_recording(request, recording_id):
    recording = get_object_or_404(UnderwriterCallRecording, pk=recording_id)
    if recording.underwriter_id != request.user.id or recording.application.claimed_by_id != request.user.id:
        raise PermissionDenied
    if not recording.can_delete:
        messages.error(request, "Only pending-QC recordings can be deleted.")
        return redirect("sales_customer_call", app_id=recording.application_id)
    app_id = recording.application_id
    filename = recording.original_filename
    recording.delete()
    _audit(
        request.user,
        AuditLog.ACTION_KYC_CHANGE,
        "UnderwriterCallRecording",
        recording_id,
        {"action": "recording_deleted", "filename": filename},
        request,
    )
    messages.success(request, "Recording deleted.")
    return redirect("sales_customer_call", app_id=app_id)


@role_required(_underwriter_or_hq)
def sales_call_recording_file(request, recording_id):
    recording = get_object_or_404(UnderwriterCallRecording, pk=recording_id)
    user = request.user
    allowed = (
        is_hq(user)
        or user.is_staff
        or user.is_superuser
        or recording.underwriter_id == user.id
        or recording.application.claimed_by_id == user.id
    )
    if not allowed:
        raise PermissionDenied
    if not recording.file:
        raise Http404("Recording file not found.")
    return FileResponse(recording.file.open("rb"), content_type=recording.mime_type or "application/octet-stream")


@underwriter_required
def sales_income_check(request, app_id):
    app, response = review_guard(request, app_id)
    if response:
        return response
    review = get_review(app, request.user)
    income_fields = [
        "income_understood",
        "income_contact_spoken",
        "income_confirmed",
        "income_source_dependable",
        "income_contact_confident",
    ]
    monthly_income = Decimal(app.exact_monthly_income or app.monthly_income or 0)
    monthly_payment = Decimal(app.calculated_monthly_payment or 0)
    recommended_income = monthly_payment * Decimal("10")
    income_multiple = (monthly_income / monthly_payment).quantize(Decimal("0.1")) if monthly_payment else Decimal("0")
    if request.method == "POST":
        for field in income_fields:
            setattr(review, field, bool_from_post(request, field))
        review.save(update_fields=[*income_fields, "updated_at"])
        return redirect("sales_final_review", app_id=app.id)
    return render(request, "sales/income_check.html", {
        "page_heading": "Income Check",
        "app": app,
        "review": review,
        "step": 5,
        "total_steps": 6,
        "monthly_income": monthly_income,
        "monthly_payment": monthly_payment,
        "recommended_income": recommended_income,
        "income_multiple": income_multiple,
        **correction_context(app),
    })


@underwriter_required
def sales_final_review(request, app_id):
    app, response = review_guard(request, app_id)
    if response:
        return response
    review = get_review(app, request.user)

    if request.method == "POST":
        decision = request.POST.get("decision")
        review.comment = request.POST.get("manager_comment", "")
        review.save(update_fields=["comment", "updated_at"])
        app.manager_comment = review.comment
        app.reviewed_by = request.user
        app.reviewed_at = timezone.now()

        if decision == "approve":
            return redirect("sales_confirm_approve", app_id=app.id)

        if decision == "reject":
            reject_reason = request.POST.get("reject_reason", "").strip()
            if not reject_reason:
                messages.error(request, "A rejection reason is required.")
                return redirect("sales_final_review", app_id=app.id)
            app.status = "rejected"
            app.review_status = "rejected"
            app.manager_comment = reject_reason
            cancel_application_commissions(app)
            app.save(update_fields=["status", "review_status", "manager_comment", "reviewed_by", "reviewed_at"])
            _audit(request.user, AuditLog.ACTION_REJECT, "FinancingApplication", app.id,
                   {"reason": reject_reason}, request)
            try:
                from notifications.utils import notify_application_rejected

                notify_application_rejected(app, rejected_by=request.user)
            except Exception:
                pass
            messages.error(request, "Application rejected.")
            return render(request, "sales/reject_success.html", {"app": app, "page_heading": "Rejected"})

        if decision == "request_correction":
            if not app.corrections.filter(resolved=False).exists() and not review.comment.strip():
                messages.error(request, "Mark at least one correction or add a comment before sending back.")
                return redirect("sales_final_review", app_id=app.id)
            app.status = "sent_back"
            app.review_status = "sent_back"
            sync_legacy_corrections(app)
            app.save(update_fields=["status", "review_status", "manager_comment", "reviewed_by", "reviewed_at"])
            messages.success(request, "Application sent back for corrections.")
            return redirect("sales_home")

    score = review.completeness_score()
    score_class = "score-green" if score >= 80 else "score-orange" if score >= 50 else "score-red"
    from risk.services import find_existing_customer_exposure
    exposure = find_existing_customer_exposure(
        national_id=app.national_id,
        phone=app.customer_phone,
        exclude_application_id=app.pk,
    )
    from risk.models import FraudCheck
    latest_fraud_check = FraudCheck.objects.filter(application=app).order_by("-created_at").first()

    # IMEI verification context
    from django.conf import settings as dj_settings
    from accounts.utils import is_hq
    imei_status = app.imei_verification_status or ""
    imei_ctx = {
        "imei_status": imei_status,
        "imei_verified": app.imei_verified,
        "imei_api_brand": app.imei_api_brand or "",
        "imei_api_model": app.imei_api_model or "",
        "imei_match_confidence": app.imei_match_confidence,
        "imei_verification_reason": app.imei_verification_reason or "",
        "imei_verified_at": app.imei_verified_at,
        "imei_number": app.imei_number or "",
        "imei_override": app.imei_override,
        "imei_override_reason": app.imei_override_reason or "",
        "imei_override_by": app.imei_override_by,
        "imei_override_at": app.imei_override_at,
        "imei_raw_response": app.imei_raw_response or {},
        "imei_check_enabled": getattr(dj_settings, "IMEI_CHECK_ENABLED", True),
        "strict_mode": getattr(dj_settings, "IMEI_CHECK_STRICT_MODE", True),
        "can_override_imei": is_hq(request.user) or request.user.is_staff,
    }

    return render(request, "sales/final_review.html", {
        "page_heading": "Final Review",
        "app": app,
        "review": review,
        "score": score,
        "score_class": score_class,
        "step": 6,
        "total_steps": 6,
        "exposure": exposure,
        "fraud_check": latest_fraud_check,
        **imei_ctx,
        **correction_context(app),
    })


@underwriter_required
def sales_confirm_approve(request, app_id):
    app, response = review_guard(request, app_id)
    if response:
        return response

    # Run fraud/exposure check and enforce approval guard
    from risk.services import run_fraud_check, can_approve_application
    fraud_check = run_fraud_check(app, checked_by=request.user)
    approval_guard = can_approve_application(app, requesting_user=request.user)
    recording_gate = _call_recording_gate(app)

    if request.method == "POST":
        if not recording_gate["can_approve"]:
            messages.error(request, recording_gate["blocked_reason"])
            return render(request, "sales/confirm_approve.html", {
                "app": app,
                "page_heading": "Confirm Approve",
                "fraud_check": fraud_check,
                "approval_guard": approval_guard,
                "recording_gate": recording_gate,
            })

        # Block if fraud check says cannot approve (unless HQ override already applied)
        if not approval_guard["can_approve"]:
            messages.error(request, approval_guard["blocked_reason"])
            return render(request, "sales/confirm_approve.html", {
                "app": app,
                "page_heading": "Confirm Approve",
                "fraud_check": fraud_check,
                "approval_guard": approval_guard,
                "recording_gate": recording_gate,
            })

        from core.commercial import validate_application_pricing

        pricing_ok, pricing_missing = validate_application_pricing(app)
        if not pricing_ok:
            messages.error(
                request,
                "Cannot approve yet. Missing: " + ", ".join(pricing_missing) + ".",
            )
            return render(request, "sales/confirm_approve.html", {
                "app": app,
                "page_heading": "Confirm Approve",
                "fraud_check": fraud_check,
                "approval_guard": approval_guard,
                "recording_gate": recording_gate,
            })

        from approvals.views import ALREADY_APPROVED_MSG, TERMINAL_STATUSES
        with transaction.atomic():
            app_locked = (
                FinancingApplication.objects.select_for_update()
                .filter(pk=app_id)
                .first()
            )
            if app_locked is None:
                messages.error(request, "Application not found.")
                return redirect("sales_home")
            if app_locked.status in TERMINAL_STATUSES or app_locked.status in ("rejected", "cancelled"):
                messages.error(request, ALREADY_APPROVED_MSG)
                return redirect("sales_home")
            if app_locked.status != "under_review" or app_locked.claimed_by_id != request.user.id:
                messages.error(request, ALREADY_APPROVED_MSG)
                return redirect("sales_home")

            app_locked.status = "approved"
            app_locked.review_status = "approved"
            app_locked.reviewed_by = request.user
            app_locked.reviewed_at = timezone.now()
            app_locked.correction_fields = []
            app_locked.correction_notes = ""
            app_locked.save(update_fields=[
                "status", "review_status", "reviewed_by", "reviewed_at",
                "correction_fields", "correction_notes",
            ])
            app_locked.corrections.filter(resolved=False).update(resolved=True)
            process_application_approval(app_locked, approved_by=request.user)
            app = app_locked

        _audit(request.user, AuditLog.ACTION_APPROVE, "FinancingApplication", app.id,
               {"application_number": app.application_number}, request)
        return render(request, "sales/approve_success.html", {
            "app": app,
            "portal_contract": None,
            "page_heading": "Approved",
        })
    return render(request, "sales/confirm_approve.html", {
        "app": app,
        "page_heading": "Confirm Approve",
        "fraud_check": fraud_check,
        "approval_guard": approval_guard,
        "recording_gate": recording_gate,
    })


# ---------------------------------------------------------------------------
# Queue rules
# ---------------------------------------------------------------------------

@underwriter_required
def sales_queue_rules(request):
    rule = _queue_rule()
    return render(request, "sales/queue_rules.html", {"rule": rule, "page_heading": "Queue Rules"})


# ---------------------------------------------------------------------------
# Customer call questionnaire
# ---------------------------------------------------------------------------

@role_required(_underwriter_or_hq)
def sales_call_questionnaire(request, app_id):
    app, response = _questionnaire_app_for_user(request, app_id)
    if response:
        return response

    questionnaire, _created = CustomerCallQuestionnaire.objects.get_or_create(application=app)
    if request.method == "POST":
        form = CustomerCallQuestionnaireForm(request.POST, instance=questionnaire)
        if form.is_valid():
            questionnaire = form.save(commit=False)
            questionnaire.complete(request.user)
            questionnaire.save()
            messages.success(request, "Customer call questionnaire saved.")
            return redirect("sales_call_questionnaire", app_id=app.id)
    else:
        form = CustomerCallQuestionnaireForm(instance=questionnaire)

    deal = app.deal
    weekly_price = (app.calculated_daily_payment or Decimal("0")) * Decimal("7")
    call_facts = {
        "merchant_name": _merchant_business_name(app),
        "device_label": str(deal) if deal else "No phone selected",
        "device_model": f"{deal.brand.name} {deal.model_name}" if deal else "",
        "device_specs": getattr(deal, "specs", "") if deal else "",
        "deposit": app.calculated_deposit_amount or Decimal("0"),
        "daily_price": app.calculated_daily_payment or Decimal("0"),
        "weekly_price": weekly_price,
        "monthly_price": app.calculated_monthly_payment or Decimal("0"),
        "total_repayment": app.calculated_total_loan or Decimal("0"),
    }

    return render(request, "sales/call_questionnaire.html", {
        "app": app,
        "form": form,
        "questionnaire": questionnaire,
        "call_facts": call_facts,
        "page_heading": "Customer Call Questionnaire",
    })


# ---------------------------------------------------------------------------
# Wallet / Earnings
# ---------------------------------------------------------------------------

@underwriter_required
def sales_wallet(request):
    from datetime import timedelta

    wallet, _ = Wallet.objects.get_or_create(user=request.user)

    # Legacy wallet transactions
    transactions = wallet.transactions.all()[:50]
    tx_type_labels = {
        "commission_credit": "Commission Credit",
        "commission": "Commission Credit",
        "manual_credit": "Manual Credit",
        "payout": "Payout",
        "wallet_payout": "Payout",
        "payout_debit": "Payout",
        "tax": "WHT Deduction",
        "wht_deduction": "WHT Deduction",
        "arrears_deduction": "Arrears Deduction",
        "adjustment": "Adjustment",
        "bonus": "Manual Credit",
        "spin_reward": "Spin Reward",
    }
    transaction_rows = [
        {
            "created_at": tx.created_at,
            "type_label": tx_type_labels.get(tx.transaction_type, tx.get_transaction_type_display()),
            "contract_number": tx.contract_number,
            "amount": tx.amount,
            "is_credit": tx.amount >= 0,
        }
        for tx in transactions
    ]

    # Monthly payout records with WHT breakdown (legacy earnings.ManagerPayout)
    payout_records = list(
        ManagerPayout.objects.filter(wallet=wallet).order_by("-period_end")[:12]
    )

    # New Commission Ledger entries
    ledger_entries = CommissionLedger.objects.filter(user=request.user).select_related("contract")[:100]
    ledger_rows = []
    for entry in ledger_entries:
        customer_display = ""
        if entry.contract:
            name = entry.contract.customer_name or ""
            if len(name) > 4:
                customer_display = name[:2] + "***" + name[-1:]
            else:
                customer_display = "***"
        ledger_rows.append({
            "created_at": entry.created_at,
            "contract_number": entry.contract.contract_number if entry.contract else "",
            "customer_masked": customer_display,
            "type": entry.get_entry_type_display(),
            "base_amount": entry.base_amount,
            "rate": entry.rate,
            "amount": entry.amount,
            "is_credit": entry.amount >= 0,
            "entry_type": entry.entry_type,
        })

    # New monthly payouts (commissions.UnderwriterMonthlyPayout)
    monthly_payouts = UnderwriterMonthlyPayout.objects.filter(user=request.user).order_by("-period_end")[:12]

    # Summary from commissions service
    summary = get_underwriter_wallet_summary(request.user)
    pending_payout_total = (
        UnderwriterMonthlyPayout.objects.filter(
            user=request.user,
            status__in=[
                UnderwriterMonthlyPayout.STATUS_PENDING,
                UnderwriterMonthlyPayout.STATUS_PROCESSING,
            ],
        ).aggregate(t=Sum("net_amount"))["t"] or Decimal("0")
    )
    gross_earnings_total = summary.get("total_commissions_earned", Decimal("0")) or Decimal("0")
    deductions_total = abs(summary.get("total_arrears_deductions", Decimal("0")) or Decimal("0"))
    net_earnings_total = gross_earnings_total - deductions_total
    qc_penalties = (
        UnderwriterQCPenalty.objects.filter(underwriter=request.user)
        .select_related("offense_type", "application", "evidence_recording", "issued_by")
        .order_by("-issued_at")[:50]
    )
    qc_penalty_totals = {
        item["status"]: {"total": item["total"] or Decimal("0"), "count": item["count"]}
        for item in (
            UnderwriterQCPenalty.objects.filter(underwriter=request.user)
            .values("status")
            .annotate(total=Sum("amount_mwk"), count=Count("id"))
        )
    }

    today = timezone.now().date()
    earnings_days = []
    for offset in range(13, -1, -1):
        day = today - timedelta(days=offset)
        day_entries = CommissionLedger.objects.filter(user=request.user, created_at__date=day)
        gross = (
            day_entries.filter(entry_type=CommissionLedger.ENTRY_REPAYMENT, amount__gt=0)
            .aggregate(t=Sum("amount"))["t"] or Decimal("0")
        )
        deduction_total = (
            day_entries.filter(
                entry_type__in=[
                    CommissionLedger.ENTRY_ARREARS,
                    CommissionLedger.ENTRY_WHT,
                    CommissionLedger.ENTRY_ADJUSTMENT,
                ],
                amount__lt=0,
            ).aggregate(t=Sum("amount"))["t"] or Decimal("0")
        )
        deductions = abs(deduction_total)
        earnings_days.append({
            "label": day.strftime("%d %b"),
            "gross": float(gross),
            "deductions": float(deductions),
            "net": float(gross - deductions),
        })

    earnings_chart = {
        "labels": [day["label"] for day in earnings_days],
        "gross": [day["gross"] for day in earnings_days],
        "deductions": [day["deductions"] for day in earnings_days],
        "net": [day["net"] for day in earnings_days],
    }
    earnings_chart_has_data = any(
        day["gross"] or day["deductions"] or day["net"] for day in earnings_days
    )

    active_tab = request.GET.get("tab", "earnings")

    spin_wallet, _ = SpinWallet.objects.get_or_create(user=request.user)
    spin_rewards = SpinReward.objects.filter(user=request.user).order_by("-spin_date")[:5]
    spin_bonus_total = SpinReward.objects.filter(user=request.user).aggregate(t=Sum("amount"))["t"] or Decimal("0")

    return render(request, "sales/wallet.html", {
        "page_heading": "Wallet",
        "wallet": wallet,
        "transaction_rows": transaction_rows,
        "payout_records": payout_records,
        "ledger_rows": ledger_rows,
        "monthly_payouts": monthly_payouts,
        "summary": summary,
        "pending_payout_total": pending_payout_total,
        "gross_earnings_total": gross_earnings_total,
        "deductions_total": deductions_total,
        "net_earnings_total": net_earnings_total,
        "qc_penalties": qc_penalties,
        "qc_penalty_totals": qc_penalty_totals,
        "earnings_chart": earnings_chart,
        "earnings_chart_has_data": earnings_chart_has_data,
        "active_tab": active_tab,
        "spin_wallet": spin_wallet,
        "spin_rewards": spin_rewards,
        "spin_bonus_total": spin_bonus_total,
    })


@underwriter_required
@require_POST
def sales_qc_penalty_dispute(request, penalty_id):
    penalty = get_object_or_404(
        UnderwriterQCPenalty.objects.select_related("offense_type"),
        pk=penalty_id,
        underwriter=request.user,
    )
    if penalty.status == UnderwriterQCPenalty.STATUS_REVERSED:
        messages.info(request, "This penalty has already been reversed.")
        return redirect("sales_wallet")
    explanation = (request.POST.get("explanation") or "").strip()
    if not explanation:
        messages.error(request, "Add a short explanation before submitting a dispute.")
        return redirect("sales_wallet")
    before = {"status": penalty.status, "notes": penalty.notes}
    penalty.status = UnderwriterQCPenalty.STATUS_DISPUTED
    penalty.notes = (penalty.notes + "\n\nDispute: " + explanation).strip()
    penalty.save(update_fields=["status", "notes"])
    _audit(
        request.user,
        AuditLog.ACTION_KYC_CHANGE,
        "UnderwriterQCPenalty",
        penalty.pk,
        {
            "action": "penalty_disputed",
            "offense": penalty.offense_type.code,
            "before": before,
            "after": {"status": penalty.status, "notes": penalty.notes},
        },
        request,
    )
    messages.success(request, "Penalty dispute sent to HQ.")
    return redirect("sales_wallet")


# ---------------------------------------------------------------------------
# Field marking (Part B)
# ---------------------------------------------------------------------------

@underwriter_required
@require_POST
def sales_mark_field(request, app_id):
    """Mark a specific field for customer review/correction."""
    app, response = review_guard(request, app_id)
    if response:
        return response

    field_key = request.POST.get("field_key", "").strip()
    field_label = request.POST.get("field_label", "").strip()
    reason = request.POST.get("reason", ApplicationFieldReview.REASON_MISSING)
    comment = request.POST.get("comment", "").strip()
    section = request.POST.get("section", ApplicationFieldReview.SECTION_CUSTOMER)
    current_value = request.POST.get("current_value", "").strip()

    if not field_key:
        messages.error(request, "No field key provided.")
        return redirect("sales_review_summary", app_id=app.id)

    # Build label from field_key if not provided
    if not field_label:
        field_label = field_key.replace("_", " ").title()

    valid_reasons = {choice[0] for choice in ApplicationFieldReview.REASON_CHOICES}
    if reason not in valid_reasons:
        reason = ApplicationFieldReview.REASON_MISSING

    review_obj, _ = ApplicationFieldReview.objects.get_or_create(
        application=app,
        field_key=field_key,
        defaults={
            "field_label": field_label,
            "section": section,
            "reason": reason,
            "comment": comment,
            "current_value_snapshot": current_value,
            "marked_by": request.user,
            "status": ApplicationFieldReview.STATUS_MARKED,
        },
    )
    if not _:
        review_obj.reason = reason
        review_obj.comment = comment
        review_obj.field_label = field_label
        review_obj.section = section
        review_obj.current_value_snapshot = current_value
        review_obj.marked_by = request.user
        review_obj.status = ApplicationFieldReview.STATUS_MARKED
        review_obj.save()

    reason_label = dict(ApplicationFieldReview.REASON_CHOICES).get(reason, "Needs review")
    correction_note = reason_label
    if comment:
        correction_note = f"{reason_label}: {comment}"
    ApplicationCorrection.objects.update_or_create(
        application=app,
        field_name=field_key,
        resolved=False,
        defaults={
            "section": section,
            "label": field_label,
            "note": correction_note,
            "created_by": request.user,
        },
    )
    app.sync_correction_summary()
    app.save(
        update_fields=[
            "correction_fields",
            "correction_notes",
            "correction_customer_face_image",
            "correction_id_front_image",
            "correction_id_back_image",
            "correction_customer_phone_image",
        ]
    )

    _audit(request.user, AuditLog.ACTION_KYC_CHANGE, "ApplicationFieldReview", review_obj.pk,
           {"field_key": field_key, "reason": reason, "app": app.application_number}, request)

    messages.success(request, f"Field '{field_label}' marked for review.")
    next_url = request.POST.get("next", "")
    if next_url:
        return redirect(next_url)
    return redirect("sales_review_summary", app_id=app.id)


@underwriter_required
def sales_field_reviews(request, app_id):
    """Show all field reviews for an application."""
    app, response = review_guard(request, app_id)
    if response:
        return response
    field_reviews = ApplicationFieldReview.objects.filter(application=app).order_by("section", "field_key")
    return render(request, "sales/field_reviews.html", {
        "page_heading": "Field Reviews",
        "app": app,
        "field_reviews": field_reviews,
    })


@underwriter_required
@require_POST
def sales_dismiss_field_review(request, review_id):
    """Dismiss a field review mark."""
    review_obj = get_object_or_404(ApplicationFieldReview, pk=review_id)
    app, response = review_guard(request, review_obj.application_id)
    if response:
        return response
    review_obj.status = ApplicationFieldReview.STATUS_DISMISSED
    review_obj.save(update_fields=["status", "updated_at"])
    ApplicationCorrection.objects.filter(
        application=review_obj.application,
        field_name=review_obj.field_key,
        resolved=False,
    ).update(resolved=True)
    review_obj.application.sync_correction_summary()
    review_obj.application.save(
        update_fields=[
            "correction_fields",
            "correction_notes",
            "correction_customer_face_image",
            "correction_id_front_image",
            "correction_id_back_image",
            "correction_customer_phone_image",
        ]
    )
    messages.success(request, "Field review dismissed.")
    return redirect("sales_field_reviews", app_id=review_obj.application_id)


@underwriter_required
@require_POST
def sales_accept_field_review(request, review_id):
    """Accept a customer-updated field review."""
    review_obj = get_object_or_404(ApplicationFieldReview, pk=review_id)
    app, response = review_guard(request, review_obj.application_id)
    if response:
        return response
    review_obj.status = ApplicationFieldReview.STATUS_ACCEPTED
    review_obj.save(update_fields=["status", "updated_at"])
    ApplicationCorrection.objects.filter(
        application=review_obj.application,
        field_name=review_obj.field_key,
        resolved=False,
    ).update(resolved=True)
    review_obj.application.sync_correction_summary()
    review_obj.application.save(
        update_fields=[
            "correction_fields",
            "correction_notes",
            "correction_customer_face_image",
            "correction_id_front_image",
            "correction_id_back_image",
            "correction_customer_phone_image",
        ]
    )
    _audit(request.user, AuditLog.ACTION_KYC_CHANGE, "ApplicationFieldReview", review_obj.pk,
           {"field_key": review_obj.field_key, "action": "accepted"}, request)
    messages.success(request, f"Field '{review_obj.field_label}' accepted.")
    return redirect("sales_field_reviews", app_id=review_obj.application_id)


@underwriter_required
def sales_send_correction_link(request, app_id):
    """Generate/refresh correction token and show the customer edit link."""
    app, response = review_guard(request, app_id)
    if response:
        return response

    marked_fields = ApplicationFieldReview.objects.filter(
        application=app, status=ApplicationFieldReview.STATUS_MARKED
    )
    if not marked_fields.exists():
        messages.warning(request, "No fields are currently marked for review.")
        return redirect("sales_review_summary", app_id=app.id)

    token_obj = ApplicationCorrectionToken.create_or_refresh(app)

    _audit(request.user, AuditLog.ACTION_KYC_CHANGE, "ApplicationCorrectionToken", token_obj.pk,
           {"app": app.application_number, "action": "correction_link_generated"}, request)

    correction_url = request.build_absolute_uri(
        f"/applications/{token_obj.token}/correct/"
    )

    return render(request, "sales/correction_link.html", {
        "page_heading": "Customer Edit Link",
        "app": app,
        "token_obj": token_obj,
        "correction_url": correction_url,
        "marked_fields": marked_fields,
    })


# ---------------------------------------------------------------------------
# Guarantor call review step
# ---------------------------------------------------------------------------

@underwriter_required
def sales_guarantor_call(request, app_id):
    app, response = review_guard(request, app_id)
    if response:
        return response
    review = get_review(app, request.user)
    guarantor_fields = [
        "location_neighbour_spoken",
        "location_confirmed",
        "location_traceable",
    ]
    if request.method == "POST":
        for field in guarantor_fields:
            setattr(review, field, bool_from_post(request, field))
        review.save(update_fields=[*guarantor_fields, "updated_at"])
        _save_call_recording(request, app, CallEvidence.STAGE_GUARANTOR_CALL)
        return redirect("sales_income_check", app_id=app.id)
    call_evidence = app.call_evidence.filter(stage=CallEvidence.STAGE_GUARANTOR_CALL).first()
    return render(request, "sales/guarantor_call.html", {
        "page_heading": "Guarantor Call",
        "app": app,
        "review": review,
        "step": 5,
        "total_steps": 7,
        "call_evidence": call_evidence,
        **correction_context(app),
    })


# ---------------------------------------------------------------------------
# Payments list (underwriter can view portal payments)
# ---------------------------------------------------------------------------

@underwriter_required
def sales_payments(request):
    from portal.models import PaymentContract, PaymentTransaction
    recent_transactions = PaymentTransaction.objects.select_related("payment_contract").order_by("-created_at")[:50]
    return render(request, "sales/payments_list.html", {
        "page_heading": "Payments",
        "transactions": recent_transactions,
    })


# ---------------------------------------------------------------------------
# IMEI recheck (underwriter/HQ can force a fresh API call)
# ---------------------------------------------------------------------------

@underwriter_required
@require_POST
def sales_imei_recheck(request, app_id):
    """Force a fresh IMEI API check for an application, bypassing cache."""
    from django.conf import settings as dj_settings
    from accounts.utils import is_hq
    from applications.services.imei_verification import (
        verify_imei_against_selected_device,
        save_verification_result,
    )

    app = get_object_or_404(FinancingApplication, pk=app_id)

    imei = app.imei_number or ""
    if not imei or len(imei) != 15:
        messages.error(request, "No valid IMEI on this application to recheck.")
        return redirect("sales_final_review", app_id=app.id)

    if not app.deal_id:
        messages.error(request, "No deal selected on this application.")
        return redirect("sales_final_review", app_id=app.id)

    if not getattr(dj_settings, "IMEI_CHECK_ENABLED", True):
        messages.warning(request, "IMEI verification is disabled in settings.")
        return redirect("sales_final_review", app_id=app.id)

    try:
        result = verify_imei_against_selected_device(
            imei=imei,
            selected_device=app.deal,
            user=request.user,
            force_recheck=True,
        )
        save_verification_result(app, result, user=request.user)
        status_label = result.get("match_status", "unknown").replace("_", " ").title()
        messages.success(request, f"IMEI rechecked. Status: {status_label}.")
    except Exception as exc:
        messages.error(request, f"IMEI recheck failed: {exc}")

    return redirect("sales_final_review", app_id=app.id)


# ---------------------------------------------------------------------------
# IMEI mismatch override (HQ / senior staff only)
# ---------------------------------------------------------------------------

@require_POST
def sales_imei_override(request, app_id):
    """
    Allow HQ or staff to override an IMEI mismatch with a written reason.
    This unblocks the application for normal approval flow.
    """
    from accounts.utils import is_hq
    from core.models import AuditLog

    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())

    if not (is_hq(request.user) or request.user.is_staff):
        messages.error(request, "Only HQ or staff can override an IMEI mismatch.")
        return redirect("sales_final_review", app_id=app_id)

    app = get_object_or_404(FinancingApplication, pk=app_id)
    override_reason = request.POST.get("override_reason", "").strip()

    if not override_reason:
        messages.error(request, "An override reason is required.")
        return redirect("sales_final_review", app_id=app.id)

    app.imei_override = True
    app.imei_override_reason = override_reason
    app.imei_override_by = request.user
    app.imei_override_at = timezone.now()
    app.save(update_fields=["imei_override", "imei_override_reason", "imei_override_by", "imei_override_at"])

    AuditLog.objects.create(
        user=request.user,
        action="imei_override",
        object_type="FinancingApplication",
        object_id=str(app.pk),
        detail={
            "application": app.application_number,
            "imei": app.imei_number,
            "override_reason": override_reason,
            "imei_status": app.imei_verification_status,
        },
    )
    messages.success(request, "IMEI mismatch override recorded. Normal approval is now allowed.")
    return redirect("sales_final_review", app_id=app.id)


# ---------------------------------------------------------------------------
# Emergency Payout Request (Underwriter / Sales staff)
# ---------------------------------------------------------------------------

@underwriter_required
def sales_emergency_payout_request(request):
    """
    Emergency payout request view.
    Underwriters can request up to 20% of their current-month earned commission.
    """
    from earnings.models import EmergencyPayoutRequest, Wallet

    wallet, _ = Wallet.objects.get_or_create(user=request.user)
    emergency_info = EmergencyPayoutRequest.get_monthly_emergency_limit(wallet)

    pending_requests = EmergencyPayoutRequest.objects.filter(
        wallet=wallet, status=EmergencyPayoutRequest.STATUS_PENDING,
    ).order_by("-created_at")[:5]

    paid_requests = EmergencyPayoutRequest.objects.filter(
        wallet=wallet, status=EmergencyPayoutRequest.STATUS_PAID,
    ).order_by("-paid_at")[:10]

    if request.method == "POST":
        try:
            requested_amount = Decimal(request.POST.get("amount", "0"))
        except Exception:
            messages.error(request, "Invalid amount entered.")
            return redirect("sales_emergency_payout_request")

        reason = request.POST.get("reason", "").strip()
        payout_phone = request.POST.get("payout_phone", "").strip()
        confirmed = request.POST.get("confirmed") == "on"

        errors = []
        if requested_amount <= 0:
            errors.append("Amount must be greater than zero.")
        if requested_amount > emergency_info["available"]:
            errors.append(
                f"Requested amount exceeds available limit of MWK {emergency_info['available']:,.0f}."
            )
        if not reason:
            errors.append("A reason is required.")
        if not confirmed:
            errors.append("You must confirm the 20% emergency payout rule.")

        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            ep = EmergencyPayoutRequest.objects.create(
                wallet=wallet,
                requested_amount=requested_amount,
                reason=reason,
                payout_phone=payout_phone,
                confirmed=confirmed,
                earned_this_month_snapshot=emergency_info["earned"],
                emergency_limit_snapshot=emergency_info["limit"],
                already_requested_snapshot=emergency_info["already_requested"],
                status=EmergencyPayoutRequest.STATUS_PENDING,
            )
            _audit(
                request.user, "emergency_payout_requested",
                "EmergencyPayoutRequest", str(ep.pk),
                {
                    "amount": str(requested_amount),
                    "reason": reason,
                    "earned_snapshot": str(emergency_info["earned"]),
                    "limit_snapshot": str(emergency_info["limit"]),
                },
                request=request,
            )
            messages.success(
                request,
                f"Emergency payout request of MWK {requested_amount:,.0f} submitted. "
                "HQ will review and process your request."
            )
            return redirect("sales_emergency_payout_request")

    return render(request, "sales/emergency_payout_request.html", {
        "wallet": wallet,
        "emergency_info": emergency_info,
        "pending_requests": pending_requests,
        "paid_requests": paid_requests,
    })


@underwriter_required
@require_POST
def sales_emergency_payout_cancel(request, payout_id):
    """Cancel a pending emergency payout request."""
    from earnings.models import EmergencyPayoutRequest, Wallet
    wallet, _ = Wallet.objects.get_or_create(user=request.user)
    payout = get_object_or_404(EmergencyPayoutRequest, id=payout_id, wallet=wallet)
    if payout.status != EmergencyPayoutRequest.STATUS_PENDING:
        messages.error(request, "Only pending requests can be cancelled.")
        return redirect("sales_emergency_payout_request")
    payout.status = EmergencyPayoutRequest.STATUS_CANCELLED
    payout.save(update_fields=["status", "updated_at"])
    _audit(request.user, "emergency_payout_cancelled", "EmergencyPayoutRequest", str(payout.pk), {})
    messages.success(request, "Emergency payout request cancelled.")
    return redirect("sales_emergency_payout_request")
