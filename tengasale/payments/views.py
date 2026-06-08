"""
TengaSale HQ Payment Center views.
All views require HQ/admin access — enforced via decorators.
"""
from __future__ import annotations

import calendar
import json
import logging
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import hq_required
from commissions.models import CommissionLedger, MerchantContractPayout, UnderwriterMonthlyPayout
from portal.models import PaymentTransaction
from rewards.models import SpinReward

from .models import (
    CommissionRule, PayoutBatch, PayoutItem,
    PaymentAuditLog, SalarySchedule, SpinRewardPayout,
)
from .services import (
    approve_payout_batch,
    create_payout_batch,
    create_spin_reward_payout,
    generate_salary_schedule_for_month,
    get_hq_payment_summary,
    notify_payout_processing,
    process_payout_batch,
    submit_batch_for_approval,
)

logger = logging.getLogger(__name__)


def _hq_or_finance(user):
    """Return True if user has payment center access."""
    profile = getattr(user, "profile", None)
    if not profile:
        return user.is_staff or user.is_superuser
    role = getattr(profile, "role", "")
    return role in ("hq", "underwriter") or user.is_staff or user.is_superuser


# ──────────────────────────────────────────────────────────────────────────────
# Overview
# ──────────────────────────────────────────────────────────────────────────────

@login_required
def payment_center_home(request):
    """HQ Payment Center — Overview."""
    if not _hq_or_finance(request.user):
        messages.error(request, "You do not have access to the Payment Center.")
        return redirect("home")

    summary = get_hq_payment_summary()

    # Recent customer transactions
    recent_txns = PaymentTransaction.objects.select_related(
        "payment_contract"
    ).order_by("-created_at")[:10]

    # Pending approvals
    pending_batches = PayoutBatch.objects.filter(
        status__in=[PayoutBatch.STATUS_PENDING_APPROVAL, PayoutBatch.STATUS_APPROVED]
    ).order_by("-created_at")[:5]

    # Pending spin rewards
    pending_spins = SpinRewardPayout.objects.filter(
        status=SpinRewardPayout.STATUS_PENDING
    ).select_related("user", "spin_reward").order_by("-created_at")[:5]

    return render(request, "payments/hq_overview.html", {
        "summary": summary,
        "recent_txns": recent_txns,
        "pending_batches": pending_batches,
        "pending_spins": pending_spins,
        "page_title": "Payment Center",
        "active_tab": "overview",
    })


# ──────────────────────────────────────────────────────────────────────────────
# Customer Collections
# ──────────────────────────────────────────────────────────────────────────────

@login_required
def collections_list(request):
    """List all incoming customer payments."""
    if not _hq_or_finance(request.user):
        return redirect("home")

    qs = PaymentTransaction.objects.select_related("payment_contract").order_by("-created_at")

    # Filters
    status = request.GET.get("status", "")
    provider = request.GET.get("provider", "")
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    search = request.GET.get("q", "")

    if status:
        qs = qs.filter(status=status)
    if provider:
        qs = qs.filter(provider=provider)
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    if search:
        qs = qs.filter(
            Q(internal_reference__icontains=search)
            | Q(payment_contract__customer_name__icontains=search)
            | Q(payment_contract__contract_number__icontains=search)
            | Q(payment_contract__customer_phone__icontains=search)
        )

    paginator = Paginator(qs, 30)
    page = paginator.get_page(request.GET.get("page"))

    total_paid = qs.filter(status="paid").aggregate(t=Sum("amount"))["t"] or Decimal("0")
    total_failed = qs.filter(status="failed").count()

    return render(request, "payments/hq_collections.html", {
        "page_obj": page,
        "total_paid": total_paid,
        "total_failed": total_failed,
        "filter_status": status,
        "filter_provider": provider,
        "filter_search": search,
        "filter_date_from": date_from,
        "filter_date_to": date_to,
        "page_title": "Customer Collections",
        "active_tab": "collections",
    })


@login_required
def transaction_detail(request, ref):
    """Transaction detail view."""
    if not _hq_or_finance(request.user):
        return redirect("home")
    txn = get_object_or_404(PaymentTransaction, internal_reference=ref)
    return render(request, "payments/hq_transaction_detail.html", {
        "txn": txn,
        "page_title": f"Transaction {ref}",
        "active_tab": "collections",
    })


# ──────────────────────────────────────────────────────────────────────────────
# Payout Batches
# ──────────────────────────────────────────────────────────────────────────────

@login_required
def payout_batches(request):
    """List payout batches."""
    if not _hq_or_finance(request.user):
        return redirect("home")

    qs = PayoutBatch.objects.prefetch_related("items").order_by("-created_at")
    ptype = request.GET.get("type", "")
    status = request.GET.get("status", "")
    if ptype:
        qs = qs.filter(payout_type=ptype)
    if status:
        qs = qs.filter(status=status)

    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "payments/hq_payout_batches.html", {
        "page_obj": page,
        "filter_type": ptype,
        "filter_status": status,
        "payout_types": PayoutBatch.PAYOUT_TYPE_CHOICES,
        "statuses": PayoutBatch.STATUS_CHOICES,
        "page_title": "Payout Batches",
        "active_tab": "payouts",
    })


@login_required
def payout_batch_detail(request, batch_number):
    """Detail view for a payout batch."""
    if not _hq_or_finance(request.user):
        return redirect("home")
    batch = get_object_or_404(PayoutBatch, batch_number=batch_number)
    items = batch.items.order_by("recipient_name")
    return render(request, "payments/hq_batch_detail.html", {
        "batch": batch,
        "items": items,
        "page_title": f"Batch {batch_number}",
        "active_tab": "payouts",
    })


@login_required
@require_POST
def payout_batch_approve(request, batch_number):
    """Approve a payout batch."""
    if not _hq_or_finance(request.user):
        return JsonResponse({"ok": False, "error": "Unauthorized"}, status=403)
    batch = get_object_or_404(PayoutBatch, batch_number=batch_number)
    try:
        approve_payout_batch(batch, request.user)
        messages.success(request, f"Batch {batch_number} approved.")
    except ValueError as e:
        messages.error(request, str(e))
    return redirect("payments:payout_batch_detail", batch_number=batch_number)


@login_required
@require_POST
def payout_batch_process(request, batch_number):
    """Mark a batch as processing."""
    if not _hq_or_finance(request.user):
        return JsonResponse({"ok": False, "error": "Unauthorized"}, status=403)
    batch = get_object_or_404(PayoutBatch, batch_number=batch_number)
    try:
        result = process_payout_batch(batch, request.user)
        messages.success(request, f"Batch {batch_number} processing started. {result['items_queued']} items queued.")
    except ValueError as e:
        messages.error(request, str(e))
    return redirect("payments:payout_batch_detail", batch_number=batch_number)


@login_required
@require_POST
def payout_item_mark_paid(request, item_id):
    """Manually mark a payout item as paid."""
    if not _hq_or_finance(request.user):
        return JsonResponse({"ok": False, "error": "Unauthorized"}, status=403)
    item = get_object_or_404(PayoutItem, id=item_id)
    proof_ref = request.POST.get("proof_reference", "").strip()
    notes = request.POST.get("notes", "").strip()
    if not proof_ref:
        messages.error(request, "Proof reference is required to mark as paid.")
        return redirect("payments:payout_batch_detail", batch_number=item.batch.batch_number)
    item.mark_paid(user=request.user, proof_reference=proof_ref, notes=notes)
    messages.success(request, f"Payout to {item.recipient_name} marked as paid.")
    notify_payout_processing(item.recipient_name, item.amount, item.payment_method, item.recipient_phone)
    return redirect("payments:payout_batch_detail", batch_number=item.batch.batch_number)


# ──────────────────────────────────────────────────────────────────────────────
# Merchant Payouts
# ──────────────────────────────────────────────────────────────────────────────

@login_required
def merchant_payouts(request):
    """Merchant commission payouts."""
    if not _hq_or_finance(request.user):
        return redirect("home")

    payouts = MerchantContractPayout.objects.select_related(
        "merchant", "contract"
    ).order_by("-created_at")

    status = request.GET.get("status", "")
    if status:
        payouts = payouts.filter(status=status)

    total_payable = payouts.filter(
        status=MerchantContractPayout.STATUS_PENDING
    ).aggregate(t=Sum("total_payable"))["t"] or Decimal("0")

    paginator = Paginator(payouts, 30)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "payments/hq_merchant_payouts.html", {
        "page_obj": page,
        "total_payable": total_payable,
        "filter_status": status,
        "page_title": "Merchant Payouts",
        "active_tab": "merchant",
    })


# ──────────────────────────────────────────────────────────────────────────────
# Underwriter Payouts
# ──────────────────────────────────────────────────────────────────────────────

@login_required
def underwriter_payouts(request):
    """Underwriter commission payouts."""
    if not _hq_or_finance(request.user):
        return redirect("home")

    payouts = UnderwriterMonthlyPayout.objects.select_related("user").order_by("-period_end")

    status = request.GET.get("status", "")
    if status:
        payouts = payouts.filter(status=status)

    total_payable = payouts.filter(
        status=UnderwriterMonthlyPayout.STATUS_PENDING
    ).aggregate(t=Sum("net_amount"))["t"] or Decimal("0")

    paginator = Paginator(payouts, 30)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "payments/hq_underwriter_payouts.html", {
        "page_obj": page,
        "total_payable": total_payable,
        "filter_status": status,
        "page_title": "Underwriter Payouts",
        "active_tab": "underwriter",
    })


# ──────────────────────────────────────────────────────────────────────────────
# Salaries
# ──────────────────────────────────────────────────────────────────────────────

@login_required
def salaries(request):
    """Salary schedule management."""
    if not _hq_or_finance(request.user):
        return redirect("home")

    today = timezone.localdate()
    month_str = request.GET.get("month", today.strftime("%Y-%m"))
    try:
        salary_month = date(int(month_str[:4]), int(month_str[5:7]), 1)
    except (ValueError, IndexError):
        salary_month = today.replace(day=1)

    schedules = SalarySchedule.objects.filter(
        salary_month=salary_month
    ).select_related("staff_user", "approved_by").order_by("staff_name")

    total_net = schedules.aggregate(t=Sum("net_pay"))["t"] or Decimal("0")
    approved_count = schedules.filter(status=SalarySchedule.STATUS_APPROVED).count()

    # Build month navigation
    months = []
    for i in range(-6, 3):
        m = today.month + i
        y = today.year + (m - 1) // 12
        m = ((m - 1) % 12) + 1
        months.append(date(y, m, 1))

    return render(request, "payments/hq_salaries.html", {
        "schedules": schedules,
        "salary_month": salary_month,
        "months": months,
        "total_net": total_net,
        "approved_count": approved_count,
        "page_title": "Salaries",
        "active_tab": "salaries",
    })


@login_required
@require_POST
def generate_payroll(request):
    """Generate salary schedule for a month."""
    if not _hq_or_finance(request.user):
        return JsonResponse({"ok": False, "error": "Unauthorized"}, status=403)

    month_str = request.POST.get("month", "")
    try:
        salary_month = date(int(month_str[:4]), int(month_str[5:7]), 1)
    except (ValueError, IndexError):
        messages.error(request, "Invalid month.")
        return redirect("payments:salaries")

    created = generate_salary_schedule_for_month(salary_month, request.user)
    messages.success(request, f"Generated {len(created)} salary entries for {salary_month.strftime('%B %Y')}.")
    return redirect(f"{'/'.join(request.build_absolute_uri().split('/')[:3])}/payments/salaries/?month={month_str}")


@login_required
@require_POST
def approve_salary(request, salary_id):
    """Approve a single salary record."""
    if not _hq_or_finance(request.user):
        return JsonResponse({"ok": False, "error": "Unauthorized"}, status=403)
    schedule = get_object_or_404(SalarySchedule, id=salary_id)
    schedule.approve(request.user)
    messages.success(request, f"Salary for {schedule.staff_name} approved.")
    return redirect("payments:salaries")


@login_required
@require_POST
def approve_all_salaries(request):
    """Approve all draft salaries for a month."""
    if not _hq_or_finance(request.user):
        return JsonResponse({"ok": False, "error": "Unauthorized"}, status=403)

    month_str = request.POST.get("month", "")
    try:
        salary_month = date(int(month_str[:4]), int(month_str[5:7]), 1)
    except (ValueError, IndexError):
        messages.error(request, "Invalid month.")
        return redirect("payments:salaries")

    count = 0
    for schedule in SalarySchedule.objects.filter(
        salary_month=salary_month, status=SalarySchedule.STATUS_DRAFT
    ):
        schedule.approve(request.user)
        count += 1

    messages.success(request, f"Approved {count} salary records for {salary_month.strftime('%B %Y')}.")
    return redirect(f"../../salaries/?month={month_str}")


# ──────────────────────────────────────────────────────────────────────────────
# Spin Rewards
# ──────────────────────────────────────────────────────────────────────────────

@login_required
def spin_reward_payouts(request):
    """Spin reward payout management."""
    if not _hq_or_finance(request.user):
        return redirect("home")

    payouts = SpinRewardPayout.objects.select_related(
        "user", "spin_reward", "created_by"
    ).order_by("-created_at")

    status = request.GET.get("status", "")
    if status:
        payouts = payouts.filter(status=status)

    total_pending = payouts.filter(
        status=SpinRewardPayout.STATUS_PENDING
    ).aggregate(t=Sum("amount"))["t"] or Decimal("0")

    paginator = Paginator(payouts, 30)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "payments/hq_spin_rewards.html", {
        "page_obj": page,
        "total_pending": total_pending,
        "filter_status": status,
        "page_title": "Spin Reward Payouts",
        "active_tab": "spin",
    })


@login_required
@require_POST
def spin_reward_mark_paid(request, payout_id):
    """Mark a spin reward payout as paid."""
    if not _hq_or_finance(request.user):
        return JsonResponse({"ok": False, "error": "Unauthorized"}, status=403)
    payout = get_object_or_404(SpinRewardPayout, id=payout_id)
    proof = request.POST.get("proof_reference", "").strip()
    if not proof:
        messages.error(request, "Proof reference required.")
        return redirect("payments:spin_reward_payouts")
    payout.mark_paid(user=request.user, proof_reference=proof)
    messages.success(request, f"Spin reward for {payout.user} marked as paid.")
    return redirect("payments:spin_reward_payouts")


@login_required
@require_POST
def spin_reward_process(request, payout_id):
    """Mark spin reward as processing and notify user."""
    if not _hq_or_finance(request.user):
        return JsonResponse({"ok": False, "error": "Unauthorized"}, status=403)
    payout = get_object_or_404(SpinRewardPayout, id=payout_id)
    payout.status = SpinRewardPayout.STATUS_PROCESSING
    payout.save(update_fields=["status", "updated_at"])
    notify_payout_processing(
        str(payout.user), payout.amount,
        payout.get_payment_method_display(), payout.phone_number
    )
    messages.success(request, f"Processing spin reward for {payout.user}.")
    return redirect("payments:spin_reward_payouts")


# ──────────────────────────────────────────────────────────────────────────────
# Commission Rules
# ──────────────────────────────────────────────────────────────────────────────

@login_required
def commission_rules(request):
    """Manage commission rules."""
    if not _hq_or_finance(request.user):
        return redirect("home")

    rules = CommissionRule.objects.order_by("earner_type", "name")
    return render(request, "payments/hq_commission_rules.html", {
        "rules": rules,
        "page_title": "Commission Rules",
        "active_tab": "settings",
    })


@login_required
@require_POST
def commission_rule_save(request, rule_id=None):
    """Create or update a commission rule."""
    if not _hq_or_finance(request.user):
        return JsonResponse({"ok": False, "error": "Unauthorized"}, status=403)

    data = request.POST
    try:
        percentage = Decimal(data.get("percentage", "0") or "0")
        fixed = Decimal(data.get("fixed_amount", "0") or "0")
    except InvalidOperation:
        messages.error(request, "Invalid amount.")
        return redirect("payments:commission_rules")

    if rule_id:
        rule = get_object_or_404(CommissionRule, id=rule_id)
    else:
        rule = CommissionRule(created_by=request.user)

    rule.name = data.get("name", "").strip()
    rule.earner_type = data.get("earner_type", "").strip()
    rule.percentage = percentage
    rule.fixed_amount = fixed
    rule.applies_to_deposit = data.get("applies_to_deposit") == "on"
    rule.applies_to_repayments = data.get("applies_to_repayments", "on") == "on"
    rule.applies_to_contract_total = data.get("applies_to_contract_total") == "on"
    rule.is_active = data.get("is_active", "on") == "on"
    rule.description = data.get("description", "")
    rule.save()

    messages.success(request, f"Commission rule '{rule.name}' saved.")
    return redirect("payments:commission_rules")


# ──────────────────────────────────────────────────────────────────────────────
# Payment Settings
# ──────────────────────────────────────────────────────────────────────────────

@login_required
def payment_settings(request):
    """Payment system settings overview."""
    if not _hq_or_finance(request.user):
        return redirect("home")

    from django.conf import settings as django_settings
    from integrations import paychangu_client

    config = {
        "mock_payments": getattr(django_settings, "MOCK_PAYMENTS", True),
        "paychangu_configured": paychangu_client.is_configured(),
        "paychangu_mock_mode": paychangu_client.is_mock_mode(),
        "paychangu_api_base": getattr(django_settings, "PAYCHANGU_API_BASE", "https://api.paychangu.com"),
        "payments_mode": getattr(django_settings, "PAYMENTS_MODE", "test"),
        "allow_live_charges": getattr(django_settings, "PAYMENTS_ALLOW_LIVE_CHARGES", False),
        "allow_live_disbursements": getattr(django_settings, "PAYOUTS_ALLOW_LIVE_DISBURSEMENTS", False),
        "callback_url": getattr(django_settings, "PAYCHANGU_CALLBACK_URL", ""),
        "webhook_secret_set": bool(getattr(django_settings, "PAYCHANGU_WEBHOOK_SECRET", "")),
        "public_key_set": bool(getattr(django_settings, "PAYCHANGU_PUBLIC_KEY", "")),
        "secret_key_set": bool(getattr(django_settings, "PAYCHANGU_SECRET_KEY", "")),
    }

    # Recent audit log
    recent_audit = PaymentAuditLog.objects.order_by("-created_at")[:20]

    return render(request, "payments/hq_payment_settings.html", {
        "config": config,
        "recent_audit": recent_audit,
        "page_title": "Payment Settings",
        "active_tab": "settings",
    })


# ──────────────────────────────────────────────────────────────────────────────
# Audit Log
# ──────────────────────────────────────────────────────────────────────────────

@login_required
def audit_log(request):
    """Payment audit log."""
    if not _hq_or_finance(request.user):
        return redirect("home")

    qs = PaymentAuditLog.objects.select_related("user", "batch").order_by("-created_at")
    action = request.GET.get("action", "")
    if action:
        qs = qs.filter(action__icontains=action)

    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "payments/hq_audit_log.html", {
        "page_obj": page,
        "filter_action": action,
        "page_title": "Payment Audit Log",
        "active_tab": "settings",
    })


# ──────────────────────────────────────────────────────────────────────────────
# Create Payout Batch
# ──────────────────────────────────────────────────────────────────────────────

@login_required
@require_POST
def create_batch(request):
    """Create a new payout batch."""
    if not _hq_or_finance(request.user):
        return JsonResponse({"ok": False, "error": "Unauthorized"}, status=403)

    payout_type = request.POST.get("payout_type", "").strip()
    notes = request.POST.get("notes", "").strip()

    if payout_type not in dict(PayoutBatch.PAYOUT_TYPE_CHOICES):
        messages.error(request, "Invalid payout type.")
        return redirect("payments:payout_batches")

    batch = create_payout_batch(payout_type, request.user, notes=notes)
    messages.success(request, f"Batch {batch.batch_number} created.")
    return redirect("payments:payout_batch_detail", batch_number=batch.batch_number)
