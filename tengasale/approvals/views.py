from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import underwriter_required
from applications.models import ApplicationCorrection, FinancingApplication
from applications.services import calculate_repayment_confidence  # noqa: E402
from commissions.models import Commission
from commissions.services import cancel_application_commissions, process_application_approval

from .models import UnderwriterReview


MAX_ACTIVE = 5


def bool_from_post(request, name):
    value = request.POST.get(name)
    if value == "yes":
        return True
    if value == "no":
        return False
    return None


TERMINAL_STATUSES = frozenset({
    "approved", "rejected", "cancelled", "completed",
    "approved_pending_device_lock", "device_locked", "active_contract",
    "contract_terms", "contract_signature", "imei_entry",
    "contract_creating", "warranty_check", "locking",
    "deposit_pending", "contract_complete",
})

ALREADY_APPROVED_MSG = (
    "This contract has already been approved and is no longer available for underwriting."
)


def review_guard(request, app_id):
    app = get_object_or_404(
        FinancingApplication.objects.select_related("deal", "claimed_by", "created_by"),
        id=app_id,
    )
    if app.status == "approved" or app.status in TERMINAL_STATUSES - {"rejected", "cancelled"}:
        messages.error(request, ALREADY_APPROVED_MSG)
        return app, redirect("sales_home")
    if app.status == "rejected":
        messages.error(request, "This application has been rejected and is no longer available for underwriting.")
        return app, redirect("sales_home")
    if app.status == "cancelled":
        messages.error(request, "This application has been cancelled.")
        return app, redirect("sales_home")
    if app.claimed_by_id and app.claimed_by_id != request.user.id:
        messages.error(request, "This application is assigned to another underwriter.")
        return app, redirect("underwriter_dashboard")
    if app.status == "pending_review" and not app.claimed_by_id:
        messages.info(request, "Claim this application before reviewing it.")
        return app, redirect("underwriter_dashboard")
    return app, None


def get_review(app, user):
    review, _created = UnderwriterReview.objects.get_or_create(
        application=app,
        defaults={"underwriter": user},
    )
    if not review.underwriter_id:
        review.underwriter = user
        review.save(update_fields=["underwriter", "updated_at"])
    return review


def correction_context(app):
    return {
        "corrections": app.active_correction_map(),
        "correction_count": app.corrections.filter(resolved=False).count(),
    }


def sync_legacy_corrections(app):
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


@underwriter_required
def underwriter_dashboard(request):
    my_active = FinancingApplication.objects.filter(claimed_by=request.user, status="under_review")
    pending_count = FinancingApplication.objects.filter(status="pending_review", claimed_by__isnull=True).count()
    pending_queue = (
        FinancingApplication.objects.select_related("created_by")
        .filter(status="pending_review", claimed_by__isnull=True)
        .order_by("submitted_at", "id")[:10]
    )
    completed_reviews = FinancingApplication.objects.filter(
        reviewed_by=request.user,
        status__in=["approved", "approved_pending_device_lock", "device_locked", "active_contract", "completed", "contract_complete"],
    ).order_by("-reviewed_at")[:10]
    rejected_reviews = FinancingApplication.objects.filter(
        reviewed_by=request.user,
        status="rejected",
    ).order_by("-reviewed_at")[:10]
    underwriter_earnings = (
        Commission.objects.filter(user=request.user, role=Commission.ROLE_MANAGER)
        .exclude(status=Commission.STATUS_CANCELLED)
        .aggregate(total=Sum("amount"))["total"]
        or 0
    )
    return render(request, "approvals/home.html", {
        "my_active": my_active,
        "pending_count": pending_count,
        "pending_queue": pending_queue,
        "completed_reviews": completed_reviews,
        "rejected_reviews": rejected_reviews,
        "active_count": my_active.count(),
        "max_active": MAX_ACTIVE,
        "underwriter_earnings": underwriter_earnings,
        "can_claim": pending_count > 0 and my_active.count() < MAX_ACTIVE,
    })


@underwriter_required(sensitive=True)
def claim_next(request):
    active_count = FinancingApplication.objects.filter(claimed_by=request.user, status="under_review").count()
    if active_count >= MAX_ACTIVE:
        messages.error(request, "You can only have 5 active applications.")
        return redirect("underwriter_dashboard")

    with transaction.atomic():
        app = (
            FinancingApplication.objects.select_for_update()
            .filter(status="pending_review", claimed_by__isnull=True)
            .order_by("submitted_at", "id")
            .first()
        )
        if not app:
            messages.info(request, "No apps pending.")
            return redirect("underwriter_dashboard")
        app.claimed_by = request.user
        app.claimed_at = timezone.now()
        app.status = "under_review"
        app.review_status = "under_review"
        app.save(update_fields=["claimed_by", "claimed_at", "status", "review_status"])

    messages.success(request, "Application claimed.")
    return redirect("underwriter_review_application", app_id=app.id)


@underwriter_required(sensitive=True)
def review_application(request, app_id):
    app, response = review_guard(request, app_id)
    if response:
        return response

    if request.method == "POST":
        decision = request.POST.get("decision")
        app.manager_comment = request.POST.get("manager_comment", "")
        app.reviewed_by = request.user
        app.reviewed_at = timezone.now()

        if decision == "approve":
            return redirect("underwriter_confirm_approve", app_id=app.id)
        if decision == "reject":
            if not app.manager_comment.strip():
                messages.error(request, "Underwriter comment is required when rejecting an application.")
                return redirect("underwriter_final_review", app_id=app.id)
            app.status = "rejected"
            app.review_status = "rejected"
            cancel_application_commissions(app)
            app.save(update_fields=["status", "review_status", "manager_comment", "reviewed_by", "reviewed_at"])
            messages.error(request, "Application rejected.")
            return redirect("underwriter_dashboard")
        if decision == "request_correction":
            if not app.corrections.filter(resolved=False).exists() and not app.manager_comment.strip():
                messages.error(request, "Mark at least one correction or add an underwriter comment.")
                return redirect("underwriter_final_review", app_id=app.id)
            app.status = "sent_back"
            app.review_status = "sent_back"
            sync_legacy_corrections(app)
            app.save(update_fields=["status", "review_status", "manager_comment", "reviewed_by", "reviewed_at"])
            messages.success(request, "Sent back for edit.")
            return redirect("underwriter_dashboard")

    review = get_review(app, request.user)
    steps = [
        ("Summary Review", reverse("underwriter_review_summary", args=[app.id])),
        ("Identity Check", reverse("underwriter_identity_check", args=[app.id])),
        ("MoMo Check", reverse("underwriter_momo_check", args=[app.id])),
        ("Customer Call", reverse("underwriter_customer_call", args=[app.id])),
        ("Income Check", reverse("underwriter_income_check", args=[app.id])),
        ("Location Check", reverse("underwriter_location_check", args=[app.id])),
        ("Final Decision", reverse("underwriter_final_review", args=[app.id])),
    ]
    confidence = calculate_repayment_confidence(app)
    return render(
        request,
        "dashboard/underwriter_review_summary.html",
        {"app": app, "review": review, "steps": steps, "is_hub": True, "confidence": confidence, **correction_context(app)},
    )


@underwriter_required
def active_reviews(request):
    apps = FinancingApplication.objects.filter(claimed_by=request.user, status="under_review").order_by("-claimed_at")
    return render(request, "approvals/list.html", {"title": "My Active Reviews", "apps": apps})


@underwriter_required
def queue(request):
    apps = FinancingApplication.objects.filter(status="pending_review", claimed_by__isnull=True).order_by("submitted_at", "id")
    return render(request, "approvals/list.html", {"title": "Queue", "apps": apps, "claim_mode": True})


@underwriter_required
def completed_reviews(request):
    statuses = ["approved", "completed", "contract_complete"]
    if request.GET.get("status") == "rejected":
        statuses = ["rejected"]
    apps = FinancingApplication.objects.filter(reviewed_by=request.user, status__in=statuses).order_by("-reviewed_at")
    title = "Rejected Reviews" if request.GET.get("status") == "rejected" else "Completed Reviews"
    return render(request, "approvals/list.html", {"title": title, "apps": apps})


@underwriter_required(sensitive=True)
def summary_review(request, app_id):
    app, response = review_guard(request, app_id)
    if response:
        return response
    review = get_review(app, request.user)
    if request.method == "POST":
        review.summary_clear = bool_from_post(request, "summary_clear")
        review.save(update_fields=["summary_clear", "updated_at"])
        messages.success(request, "Summary review saved.")
        return redirect("underwriter_identity_check", app_id=app.id)
    confidence = calculate_repayment_confidence(app)
    return render(request, "dashboard/underwriter_review_summary.html", {"app": app, "review": review, "is_hub": False, "confidence": confidence, **correction_context(app)})


@underwriter_required(sensitive=True)
def identity_check(request, app_id):
    app, response = review_guard(request, app_id)
    if response:
        return response
    review = get_review(app, request.user)
    identity_fields = [
        "identity_signature_matches",
        "identity_info_matches",
        "identity_selfie_matches",
        "identity_images_clear",
    ]
    if request.method == "POST":
        for field in identity_fields:
            setattr(review, field, bool_from_post(request, field))
        review.save(update_fields=[*identity_fields, "updated_at"])
        messages.success(request, "Identity check saved.")
        return redirect("underwriter_momo_check", app_id=app.id)
    return render(request, "dashboard/underwriter_identity_check.html", {"app": app, "review": review, **correction_context(app)})


@underwriter_required(sensitive=True)
def momo_check(request, app_id):
    app, response = review_guard(request, app_id)
    if response:
        return response
    review = get_review(app, request.user)
    if request.method == "POST":
        review.momo_name_matches = bool_from_post(request, "momo_name_matches")
        review.save(update_fields=["momo_name_matches", "updated_at"])
        messages.success(request, "MoMo check saved.")
        return redirect("underwriter_customer_call", app_id=app.id)
    return render(request, "dashboard/underwriter_momo_check.html", {"app": app, "review": review})


@underwriter_required(sensitive=True)
def customer_call(request, app_id):
    app, response = review_guard(request, app_id)
    if response:
        return response
    review = get_review(app, request.user)
    fields = [
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
        for field in fields:
            setattr(review, field, bool_from_post(request, field))
        review.save(update_fields=[*fields, "updated_at"])
        messages.success(request, "Customer call saved.")
        return redirect("underwriter_income_check", app_id=app.id)
    return render(request, "dashboard/underwriter_customer_call.html", {"app": app, "review": review})


@underwriter_required(sensitive=True)
def income_check(request, app_id):
    app, response = review_guard(request, app_id)
    if response:
        return response
    review = get_review(app, request.user)
    fields = [
        "income_understood", "income_contact_spoken", "income_confirmed",
        "income_source_dependable", "income_contact_confident",
        "guarantor_spoken", "guarantor_confirmed_customer",
        "guarantor_confident_repayment", "contacts_reachable",
    ]
    monthly_income = Decimal(app.exact_monthly_income or app.monthly_income or 0)
    monthly_payment = Decimal(app.calculated_monthly_payment or 0)
    recommended_income = monthly_payment * Decimal("10")
    income_multiple = (monthly_income / monthly_payment).quantize(Decimal("0.1")) if monthly_payment else Decimal("0")
    if request.method == "POST":
        for field in fields:
            setattr(review, field, bool_from_post(request, field))
        review.save(update_fields=[*fields, "updated_at"])
        app.income_check_answers = {field: getattr(review, field) for field in fields}
        app.save(update_fields=["income_check_answers"])
        messages.success(request, "Income check saved.")
        return redirect("underwriter_location_check", app_id=app.id)
    return render(request, "dashboard/underwriter_income_check.html", {
        "app": app,
        "review": review,
        "monthly_income": monthly_income,
        "monthly_payment": monthly_payment,
        "recommended_income": recommended_income,
        "income_multiple": income_multiple,
        **correction_context(app),
    })


@underwriter_required(sensitive=True)
def location_check(request, app_id):
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
            "address_is_clear": review.location_address_clear,
        }
        app.save(update_fields=["address_check_answers"])
        messages.success(request, "Location check saved.")
        return redirect("underwriter_final_review", app_id=app.id)
    return render(request, "dashboard/underwriter_location_check.html", {"app": app, "review": review, **correction_context(app)})


@underwriter_required(sensitive=True)
def final_review(request, app_id):
    app, response = review_guard(request, app_id)
    if response:
        return response
    review = get_review(app, request.user)
    deal_fields = [
        "deal_phone_correct", "deal_deposit_understood",
        "deal_repayment_understood", "deal_lock_understood", "deal_legal_understood",
    ]
    if request.method == "POST":
        for field in deal_fields:
            setattr(review, field, bool_from_post(request, field))
        review.comment = request.POST.get("manager_comment", "")
        review.save(update_fields=[*deal_fields, "comment", "updated_at"])
        app.manager_comment = review.comment
        app.save(update_fields=["manager_comment"])
        return review_application(request, app_id)

    score = review.completeness_score()
    score_class = "score-green" if score >= 80 else "score-orange" if score >= 50 else "score-red"
    return render(request, "dashboard/underwriter_final_review.html", {"app": app, "review": review, "score": score, "score_class": score_class, **correction_context(app)})


@underwriter_required(sensitive=True)
@require_POST
def correction_action(request, app_id):
    app, response = review_guard(request, app_id)
    if response:
        return JsonResponse({"ok": False, "error": "Application unavailable."}, status=403)
    field_name = request.POST.get("field_name", "").strip()
    label = request.POST.get("label", "").strip() or FinancingApplication.CORRECTION_FIELD_LABELS.get(field_name, field_name)
    section = request.POST.get("section", "").strip()
    note = request.POST.get("note", "").strip()
    action = request.POST.get("action", "mark")
    if not field_name:
        return JsonResponse({"ok": False, "error": "Missing field."}, status=400)
    correction = ApplicationCorrection.objects.filter(application=app, field_name=field_name, resolved=False).first()
    if action == "remove":
        if correction:
            correction.resolved = True
            correction.save(update_fields=["resolved", "updated_at"])
        sync_legacy_corrections(app)
        return JsonResponse({"ok": True, "active": False, "note": ""})
    if correction:
        correction.note = note
        correction.label = label
        correction.section = section
        correction.save(update_fields=["note", "label", "section", "updated_at"])
    else:
        correction = ApplicationCorrection.objects.create(
            application=app,
            field_name=field_name,
            section=section,
            label=label,
            note=note,
            resolved=False,
            created_by=request.user,
        )
    sync_legacy_corrections(app)
    return JsonResponse({"ok": True, "active": True, "note": correction.note})


@underwriter_required(sensitive=True)
def confirm_approve(request, app_id):
    app, response = review_guard(request, app_id)
    if response:
        return response
    if request.method == "POST":
        from core.commercial import validate_application_pricing

        pricing_ok, pricing_missing = validate_application_pricing(app)
        if not pricing_ok:
            messages.error(
                request,
                "Cannot approve yet. Missing: " + ", ".join(pricing_missing) + ".",
            )
            return render(request, "approvals/confirm_approve.html", {"app": app})

        with transaction.atomic():
            app_locked = (
                FinancingApplication.objects.select_for_update()
                .filter(pk=app_id)
                .first()
            )
            if app_locked is None:
                messages.error(request, "Application not found.")
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
        return render(request, "approvals/approve_success.html", {"app": app})
    return render(request, "approvals/confirm_approve.html", {"app": app})


def legacy_underwriter_dashboard(request):
    return redirect("underwriter_dashboard")


def legacy_claim_next(request):
    return redirect("underwriter_claim_next")


def legacy_review_application(request, app_id):
    return redirect("underwriter_review_application", app_id=app_id)


def legacy_address_check(request, app_id):
    return redirect("underwriter_location_check", app_id=app_id)


def legacy_income_check(request, app_id):
    return redirect("underwriter_income_check", app_id=app_id)
