import csv
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Avg, Count, Sum, Q, F
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.utils import timezone
from django.views.decorators.http import require_POST
from accounts.decorators import hq_required, merchant_required
from accounts.forms import HQUserForm
from accounts.models import (
    CompanyShareStructure,
    Department,
    DisciplineDispute,
    DisciplineEvent,
    DisciplineScorePeriod,
    ExecutiveSignature,
    FounderEquityRecord,
    KPITemplate,
    KPIResult,
    PenaltyTransaction,
    Rank,
    StaffRole,
    StaffDocument,
    UserProfile,
    VoltsActionType,
    VoltsTransaction,
)
from accounts.documents import generate_staff_document_pdf
from accounts.services import (
    MODULE_DISCIPLINE,
    MODULE_EQUITY,
    MODULE_KPIS,
    MODULE_PAYOUTS,
    MODULE_STAFF_DOCUMENTS,
    MODULE_STAFF_ROLES,
    MODULE_VOLTS,
    audit_sensitive_action,
    approve_discipline_period,
    approve_payout_impact,
    can_approve_volts,
    can_edit_equity,
    can_view_compensation,
    can_view_equity,
    founder_staff_dashboard_context,
    governance_dashboard_context,
    user_allowed_modules,
    user_can_access_module,
    visible_hq_modules,
)
from accounts.utils import primary_role, role_redirect_url
from core.business_hours import business_hours_context
from applications.models import FinancingApplication
from approvals.models import QCOffenseType, UnderwriterCallRecording, UnderwriterQCPenalty
from commissions.models import Commission, MerchantContractPayout, CommissionLedger
from contracts.models import Contract
from core.view_safety import safe_page
from financing.models import Device, DeviceCommand, FinancingContract, PaymentRecord
from merchants.models import Merchant
from rewards.models import SpinWallet
from .export_utils import dated_filename, export_pdf_response
from .portfolio_services import (
    calculate_portfolio_kpis,
    portfolio_chart_data,
    portfolio_rows,
)

MAX_ACTIVE_UNDERWRITER_REVIEWS = 5


ROLE_DISPLAY_LABELS = {
    UserProfile.ROLE_HQ: "HQ",
    UserProfile.ROLE_MERCHANT_ADMIN: "Merchant Admin",
    UserProfile.ROLE_UNDERWRITER: "Underwriter",
    UserProfile.ROLE_MERCHANT: "Merchant",
    UserProfile.ROLE_TECH_SUPPORT: "Tech Support",
    "customer_support": "Customer Support",
    "collections": "Collections",
    "finance_risk": "Finance & Risk",
    "legal_compliance": "Legal & Compliance",
    "ceo_strategy": "CEO / Strategy Lead",
}


@login_required
@safe_page("Profile settings")
def profile_settings(request):
    role = primary_role(request.user) or ""
    merchant = None
    merchant_status = ""
    active_reviews = 0
    reviewed_count = 0

    if role == UserProfile.ROLE_MERCHANT:
        merchant = Merchant.objects.filter(owner=request.user).order_by("-id").first()
        if merchant:
            agreement = merchant.active_agreement
            if agreement:
                merchant_status = agreement.get_status_display()
            else:
                merchant_status = "Good standing" if merchant.is_active else "Inactive"

    if role == UserProfile.ROLE_UNDERWRITER:
        active_reviews = FinancingApplication.objects.filter(
            claimed_by=request.user,
            status="under_review",
        ).count()
        reviewed_count = FinancingApplication.objects.filter(
            reviewed_by=request.user,
            status__in=["approved", "active_contract", "contract_complete", "completed", "rejected"],
        ).count()

    role_label = ROLE_DISPLAY_LABELS.get(role, role.replace("_", " ").title() or "User")
    return render(request, "dashboard/profile_settings.html", {
        "profile_role": role,
        "profile_role_label": role_label,
        "merchant": merchant,
        "merchant_status": merchant_status,
        "active_reviews": active_reviews,
        "reviewed_count": reviewed_count,
    })


def _clean_label(value, fallback="-"):
    if not value:
        return fallback
    return ROLE_DISPLAY_LABELS.get(str(value), str(value).replace("_", " ").title())


def _is_truthy_filter(value):
    return value in {"1", "true", "yes", "on"}


def _active_filter_chips(filters):
    chips = []
    for key, value in filters.items():
        if value in ("", None):
            continue
        chips.append({"key": key, "label": key.replace("_", " ").title(), "value": _clean_label(value, value)})
    return chips


def _profile_completion(profile):
    checks = [
        bool(profile.public_email),
        bool(profile.public_phone),
        bool(profile.role),
        bool(profile.department_id),
        bool(profile.staff_role_id),
        bool(profile.rank_id),
        bool(profile.supervisor_id),
    ]
    return round((sum(checks) / len(checks)) * 100)


def _latest_discipline_score(user_id):
    period = (
        DisciplineScorePeriod.objects.filter(user_id=user_id)
        .only("final_score")
        .order_by("-month")
        .first()
    )
    return period.final_score if period else None


def _profile_row(profile):
    monthly = founder_staff_dashboard_context(profile.user)["volts"]
    discipline_score = _latest_discipline_score(profile.user_id)
    performance_score = 100
    if discipline_score is not None:
        performance_score = min(100, max(0, round(float(discipline_score))))
    warnings = []
    if not profile.public_email:
        warnings.append("No email set")
    if not profile.rank_id:
        warnings.append("No rank assigned")
    if not profile.department_id:
        warnings.append("No department assigned")
    if not profile.supervisor_id:
        warnings.append("No supervisor assigned")
    if profile.is_founder and not FounderEquityRecord.objects.filter(founder_user=profile.user).exists():
        warnings.append("Founder equity incomplete")
    return {
        "profile": profile,
        "role_label": _clean_label(profile.role),
        "department_label": profile.department.name if profile.department else "No department",
        "staff_role_label": profile.staff_role.name if profile.staff_role else "No staff role",
        "rank_label": profile.rank.code if profile.rank else "No rank",
        "status_label": profile.get_status_display(),
        "type_label": profile.get_user_type_display() or "-",
        "monthly_volts": monthly["approved_volts"],
        "pending_volts": monthly["pending_volts"],
        "discipline_score": discipline_score,
        "performance_score": performance_score,
        "completion": _profile_completion(profile),
        "warnings": warnings,
    }


def _insight(title, severity, explanation, recommendation, href=""):
    return {
        "title": title,
        "severity": severity,
        "explanation": explanation,
        "recommendation": recommendation,
        "href": href,
    }


def staff_module_required(module):
    def decorator(view_func):
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if user_can_access_module(request.user, module):
                return view_func(request, *args, **kwargs)
            return render(
                request,
                "accounts/role_forbidden.html",
                {"dashboard_url": role_redirect_url(request.user)},
                status=403,
            )
        return wrapped
    return decorator


def home(request):
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    return redirect(role_redirect_url(request.user))


def merchant_dashboard_context(user):
    active_count = FinancingApplication.objects.filter(
        created_by=user,
        status__in=[
            "started",
            "customer_details",
            "device_selection",
            "kyc",
            "kyc_capture",
            "location_details",
            "work_details",
            "signature",
            "correction_requested",
            "imei_required",
            "submitted",
            "under_review",
        ],
    ).count()
    pending_queue_count = FinancingApplication.objects.filter(
        created_by=user,
        status__in=["submitted", "pending_review", "resubmitted", "under_review"],
    ).count()
    completed_count = FinancingApplication.objects.filter(
        created_by=user,
        status__in=["contract_complete", "completed", "active_contract"],
    ).count()
    archived_rejected_count = FinancingApplication.objects.filter(
        created_by=user,
        status__in=["rejected", "cancelled"],
    ).count()
    earnings_total = (
        Commission.objects.filter(user=user)
        .exclude(status=Commission.STATUS_CANCELLED)
        .aggregate(total=Sum("amount"))["total"]
        or 0
    )
    spin_wallet, _ = SpinWallet.objects.get_or_create(user=user)
    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    merchant_contracts = FinancingContract.objects.filter(created_by=user)
    pending_payments = PaymentRecord.objects.select_related("customer", "contract", "contract__device").filter(
        contract__created_by=user,
        verification_status=PaymentRecord.STATUS_PENDING,
    )
    recent_contracts = merchant_contracts.select_related("customer", "device").order_by("-created_at")[:5]
    overdue_customers = merchant_contracts.select_related("customer", "device").filter(
        status__in=[
            FinancingContract.STATUS_OVERDUE,
            FinancingContract.STATUS_LOCKED,
            FinancingContract.STATUS_DEFAULTED,
        ]
    )[:5]
    command_history = DeviceCommand.objects.select_related("device", "contract", "contract__customer").filter(
        contract__created_by=user
    )[:5]

    # New-style merchant payout data (MerchantContractPayout from portal contracts)
    contract_payouts = MerchantContractPayout.objects.filter(merchant=user).order_by("-created_at")
    payout_pending = contract_payouts.filter(status=MerchantContractPayout.STATUS_PENDING)
    payout_paid = contract_payouts.filter(status=MerchantContractPayout.STATUS_PAID)
    merchant_payout_summary = {
        "pending_count": payout_pending.count(),
        "pending_total": payout_pending.aggregate(t=Sum("total_payable"))["t"] or 0,
        "paid_count": payout_paid.count(),
        "paid_total": payout_paid.aggregate(t=Sum("total_payable"))["t"] or 0,
        "recent": contract_payouts[:5],
    }

    # Agreement status
    from merchants.models import Merchant as MerchantModel
    merchant_obj = MerchantModel.objects.filter(owner=user).first()
    merchant_agreement = merchant_obj.active_agreement if merchant_obj else None

    return {
        "active_count": active_count,
        "pending_queue_count": pending_queue_count,
        "completed_count": completed_count,
        "archived_rejected_count": archived_rejected_count,
        "earnings_total": earnings_total,
        "spin_wallet": spin_wallet,
        "merchant_payout_summary": merchant_payout_summary,
        "merchant_agreement": merchant_agreement,
        "device_financing_stats": {
            "total_financed_devices": Device.objects.filter(financing_contract__created_by=user).count(),
            "active_contracts": merchant_contracts.filter(status=FinancingContract.STATUS_ACTIVE).count(),
            "overdue_contracts": merchant_contracts.filter(status=FinancingContract.STATUS_OVERDUE).count(),
            "locked_devices": Device.objects.filter(
                financing_contract__created_by=user,
                status=Device.STATUS_LOCKED,
            ).count(),
            "payments_pending_verification": pending_payments.count(),
            "payments_verified_this_month": PaymentRecord.objects.filter(
                contract__created_by=user,
                verification_status=PaymentRecord.STATUS_VERIFIED,
                verified_at__gte=month_start,
            ).count(),
            "expected_monthly_collections": (
                merchant_contracts.filter(status=FinancingContract.STATUS_ACTIVE).aggregate(
                    total=Sum("monthly_payment_amount")
                )["total"]
                or 0
            ),
            "default_risk_count": merchant_contracts.filter(
                status__in=[FinancingContract.STATUS_OVERDUE, FinancingContract.STATUS_LOCKED]
            ).count(),
        },
        "recent_financing_contracts": recent_contracts,
        "overdue_financing_customers": overdue_customers,
        "pending_financing_payments": pending_payments[:5],
        "device_command_history": command_history,
        **business_hours_context(),
    }


@merchant_required
@safe_page("Merchant dashboard")
def merchant_dashboard(request):
    context = merchant_dashboard_context(request.user)
    return render(request, "dashboard/home.html", context)


def developer_preview_required(view_func):
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if settings.DEBUG and request.user.is_superuser:
            return view_func(request, *args, **kwargs)
        messages.warning(request, "Developer previews are not available.")
        return redirect(role_redirect_url(request.user))

    return wrapped


@hq_required
@safe_page("HQ dashboard")
def hq_dashboard(request):
    from portal.models import PaymentContract, PaymentTransaction
    from commissions.models import MerchantContractPayout, CommissionLedger
    from decimal import Decimal
    from datetime import timedelta

    User = get_user_model()
    total_merchants = User.objects.filter(profile__role="merchant").count()
    total_underwriters = User.objects.filter(profile__role="underwriter").count()
    total_hq_users = User.objects.filter(profile__role="hq").count()
    total_merchant_admins = User.objects.filter(profile__role="merchant_admin").count()
    total_tech_support = User.objects.filter(profile__role="tech_support").count()
    rejected_count = FinancingApplication.objects.filter(status="rejected").count()
    waiting_count = FinancingApplication.objects.filter(
        status__in=["submitted", "pending_review", "resubmitted"],
        claimed_by__isnull=True,
    ).count()
    total_commissions = (
        Commission.objects.exclude(status=Commission.STATUS_CANCELLED).aggregate(total=Sum("amount"))["total"]
        or 0
    )

    today = timezone.now().date()
    paid_today = (
        PaymentTransaction.objects.filter(status="paid", paid_at__date=today)
        .aggregate(t=Sum("amount"))["t"] or 0
    )
    collections_today = (
        PaymentTransaction.objects.filter(status="paid", paid_at__date=today)
        .aggregate(t=Sum("amount"))["t"] or Decimal("0")
    )
    sales_today = FinancingApplication.objects.filter(
        submitted_at__date=today,
    ).count()
    active_contracts_count = PaymentContract.objects.filter(status="active").count()
    locked_contracts_count = PaymentContract.objects.filter(status="locked").count()
    overdue_contracts_count = PaymentContract.objects.filter(status="overdue").count()
    merchant_payout_pending = (
        MerchantContractPayout.objects.filter(status="pending")
        .aggregate(t=Sum("total_payable"))["t"] or 0
    )
    # Approval conversion rate
    total_reviewed = FinancingApplication.objects.filter(
        status__in=["approved", "rejected"]
    ).count()
    approved_count_total = FinancingApplication.objects.filter(status="approved").count()
    approval_rate = round((approved_count_total / total_reviewed * 100) if total_reviewed else 0, 1)

    # UW commission payout liability (pending)
    from commissions.models import UnderwriterMonthlyPayout
    uw_payout_liability = (
        UnderwriterMonthlyPayout.objects.filter(status="pending")
        .aggregate(t=Sum("net_amount"))["t"] or Decimal("0")
    )
    # Device lock exposure (active + overdue)
    lock_exposure_count = PaymentContract.objects.filter(
        status__in=["active", "overdue", "locked"]
    ).count()

    # Portfolio value KPIs
    total_portfolio_value = (
        PaymentContract.objects.filter(status__in=["active", "overdue", "locked"])
        .aggregate(t=Sum("total_amount"))["t"] or Decimal("0")
    )
    total_payments_collected = (
        PaymentTransaction.objects.filter(status="paid")
        .aggregate(t=Sum("amount"))["t"] or Decimal("0")
    )
    outstanding_balance = max(Decimal("0"), total_portfolio_value - total_payments_collected)
    # Default rate: defaulted / total contracts that have been active
    defaulted_count = PaymentContract.objects.filter(status="defaulted").count()
    total_ever_active = PaymentContract.objects.filter(
        status__in=["active", "overdue", "locked", "defaulted", "completed"]
    ).count()
    default_rate = round((defaulted_count / total_ever_active * 100) if total_ever_active else 0, 1)
    risk_contracts_count = overdue_contracts_count + locked_contracts_count
    portfolio_at_risk = round(
        (risk_contracts_count / (active_contracts_count + risk_contracts_count) * 100)
        if (active_contracts_count + risk_contracts_count) else 0, 1
    )
    # Merchant & underwriter earnings
    merchant_earnings_total = (
        MerchantContractPayout.objects.filter(status="paid")
        .aggregate(t=Sum("total_payable"))["t"] or Decimal("0")
    )
    gross_profit_total = max(Decimal("0"), total_portfolio_value - merchant_earnings_total)
    try:
        uw_earnings_total = (
            CommissionLedger.objects.filter(status="paid")
            .aggregate(t=Sum("amount"))["t"] or Decimal("0")
        )
    except Exception:
        uw_earnings_total = Decimal("0")

    try:
        try:
            from portal.models import RecoveryCost
            recovery_value_total = (
                RecoveryCost.objects.filter(is_chargeable_to_customer=True, approved_at__isnull=False)
                .aggregate(t=Sum("amount"))["t"] or Decimal("0")
            )
        except Exception:
            recovery_value_total = Decimal("0")

        chart_days = []
        for offset in range(13, -1, -1):
            day = today - timedelta(days=offset)
            label = day.strftime("%d %b")
            contract_value = (
                PaymentContract.objects.filter(created_at__date=day)
                .aggregate(t=Sum("total_amount"))["t"] or Decimal("0")
            )
            payments_collected = (
                PaymentTransaction.objects.filter(status="paid", paid_at__date=day)
                .aggregate(t=Sum("amount"))["t"] or Decimal("0")
            )
            payout_cost = (
                MerchantContractPayout.objects.filter(created_at__date=day)
                .aggregate(t=Sum("total_payable"))["t"] or Decimal("0")
            )
            try:
                recovery_value = (
                    RecoveryCost.objects.filter(incurred_at__date=day, is_chargeable_to_customer=True, approved_at__isnull=False)
                    .aggregate(t=Sum("amount"))["t"] or Decimal("0")
                )
            except Exception:
                recovery_value = Decimal("0")
            outstanding_for_day = max(Decimal("0"), (contract_value or Decimal("0")) - (payments_collected or Decimal("0")))
            chart_days.append({
                "date": day.isoformat(),
                "label": label,
                "revenue": float(contract_value or 0),
                "payments": float(payments_collected or 0),
                "profit": float((contract_value or Decimal("0")) - (payout_cost or Decimal("0"))),
                "recovery": float(recovery_value or 0),
                "contract_value": float(contract_value or 0),
                "outstanding": float(outstanding_for_day or 0),
            })

        has_contract_values = any(day["contract_value"] for day in chart_days)
        has_payments = any(day["payments"] for day in chart_days)
        has_recovery = any(day["recovery"] for day in chart_days)
        has_payout_costs = MerchantContractPayout.objects.exists()
        financial_has_data = has_contract_values or has_payments or has_recovery
        financial_performance_chart = {
            "labels": [day["label"] for day in chart_days],
            "revenue": [day["revenue"] for day in chart_days],
            "payments": [day["payments"] for day in chart_days],
            "profit": [day["profit"] if has_payout_costs else 0 for day in chart_days],
            "recovery": [day["recovery"] for day in chart_days],
            "profitAvailable": has_payout_costs,
        }
        contract_value_chart = {
            "labels": [day["label"] for day in chart_days],
            "contractValue": [day["contract_value"] for day in chart_days],
            "payments": [day["payments"] for day in chart_days],
            "outstanding": [day["outstanding"] for day in chart_days],
        }
    except Exception:
        import logging

        logging.getLogger("tengasale.views").exception("HQ finance chart data failed")
        financial_has_data = False
        financial_performance_chart = {
            "labels": [],
            "revenue": [],
            "payments": [],
            "profit": [],
            "recovery": [],
            "profitAvailable": False,
        }
        contract_value_chart = {"labels": [], "contractValue": [], "payments": [], "outstanding": []}
        recovery_value_total = Decimal("0")

    # New role & portal KPIs
    from website.models import MerchantLead
    new_leads_count = MerchantLead.objects.filter(status=MerchantLead.STATUS_NEW).count()
    awaiting_hq_count = MerchantLead.objects.filter(status="awaiting_hq").count()
    try:
        from communications.models import SMSLog
        sms_total_sent = SMSLog.objects.filter(status=SMSLog.STATUS_SENT).count()
        sms_failed_count = SMSLog.objects.filter(status=SMSLog.STATUS_FAILED).count()
        sms_pending_config_count = SMSLog.objects.filter(status=SMSLog.STATUS_PENDING_CONFIG).count()
        sms_otp_sent_count = SMSLog.objects.filter(purpose=SMSLog.PURPOSE_OTP).count()
        sms_payment_confirmation_count = SMSLog.objects.filter(
            purpose=SMSLog.PURPOSE_PAYMENT_CONFIRMATION
        ).count()
        sms_due_reminder_count = SMSLog.objects.filter(
            purpose__in=[
                SMSLog.PURPOSE_DUE_REMINDER,
                SMSLog.PURPOSE_PAYMENT_DUE_TODAY,
                SMSLog.PURPOSE_ARREARS_REMINDER,
            ]
        ).count()
        recent_sms_logs = SMSLog.objects.select_related(
            "payment_contract",
            "contract",
            "application",
        ).order_by("-created_at")[:8]
    except Exception:
        sms_total_sent = 0
        sms_failed_count = 0
        sms_pending_config_count = 0
        sms_otp_sent_count = 0
        sms_payment_confirmation_count = 0
        sms_due_reminder_count = 0
        recent_sms_logs = []

    try:
        from support.models import SupportTicket, BugEvent
        open_tickets_count = SupportTicket.objects.filter(status=SupportTicket.STATUS_OPEN).count()
        critical_bugs_count = BugEvent.objects.filter(
            severity=BugEvent.SEV_CRITICAL, status=BugEvent.STAT_NEW
        ).count()
    except Exception:
        open_tickets_count = 0
        critical_bugs_count = 0

    try:
        from device_lock.models import DeviceLockProfile
        lock_locked_count = DeviceLockProfile.objects.filter(lock_status="locked").count()
    except Exception:
        lock_locked_count = 0

    context = {
        "total_merchants": total_merchants,
        "total_underwriters": total_underwriters,
        "total_hq_users": total_hq_users,
        "total_merchant_admins": total_merchant_admins,
        "total_tech_support": total_tech_support,
        "new_leads_count": new_leads_count,
        "awaiting_hq_count": awaiting_hq_count,
        "open_tickets_count": open_tickets_count,
        "critical_bugs_count": critical_bugs_count,
        "lock_locked_count": lock_locked_count,
        "pending_review_count": FinancingApplication.objects.filter(status="pending_review").count(),
        "under_review_count": FinancingApplication.objects.filter(status="under_review").count(),
        "approved_count": approved_count_total,
        "rejected_count": rejected_count,
        "completed_contracts_count": Contract.objects.filter(status=Contract.STATUS_COMPLETE).count(),
        "waiting_count": waiting_count,
        "total_commissions": total_commissions,
        "show_developer_preview": settings.DEBUG and request.user.is_superuser,
        "paid_today": paid_today,
        "active_contracts_count": active_contracts_count,
        "locked_contracts_count": locked_contracts_count,
        "overdue_contracts_count": overdue_contracts_count,
        "merchant_payout_pending": merchant_payout_pending,
        # Command Pulse
        "collections_today": collections_today,
        "sales_today": sales_today,
        "approval_rate": approval_rate,
        "uw_payout_liability": uw_payout_liability,
        "lock_exposure_count": lock_exposure_count,
        "financial_has_data": financial_has_data,
        "financial_performance_chart": financial_performance_chart,
        "contract_value_chart": contract_value_chart,
        "recovery_value_total": recovery_value_total,
        # Portfolio intelligence KPIs
        "total_portfolio_value": total_portfolio_value,
        "total_payments_collected": total_payments_collected,
        "outstanding_balance": outstanding_balance,
        "default_rate": default_rate,
        "portfolio_at_risk": portfolio_at_risk,
        "merchant_earnings_total": merchant_earnings_total,
        "gross_profit_total": gross_profit_total,
        "uw_earnings_total": uw_earnings_total,
        "sms_total_sent": sms_total_sent,
        "sms_failed_count": sms_failed_count,
        "sms_pending_config_count": sms_pending_config_count,
        "sms_otp_sent_count": sms_otp_sent_count,
        "sms_payment_confirmation_count": sms_payment_confirmation_count,
        "sms_due_reminder_count": sms_due_reminder_count,
        "recent_sms_logs": recent_sms_logs,
        "staff_modules": visible_hq_modules(request.user),
        "staff_module_keys": user_allowed_modules(request.user),
        **founder_staff_dashboard_context(request.user),
    }

    # Merchant Agreement Compliance Stats
    try:
        from merchants.models import MerchantAgreement
        agreement_signed_count = MerchantAgreement.objects.filter(
            status__in=[MerchantAgreement.STATUS_SIGNED, MerchantAgreement.STATUS_ACTIVE]
        ).values("merchant").distinct().count()
        agreement_unsigned_count = max(0, total_merchants - agreement_signed_count)
        recent_agreements = MerchantAgreement.objects.select_related(
            "merchant", "merchant__owner"
        ).filter(
            status__in=[MerchantAgreement.STATUS_SIGNED, MerchantAgreement.STATUS_ACTIVE]
        ).order_by("-signed_at")[:8]
        context["agreement_signed_count"] = agreement_signed_count
        context["agreement_unsigned_count"] = agreement_unsigned_count
        context["recent_agreements"] = recent_agreements
        context["agreement_compliance_pct"] = (
            round(100 * agreement_signed_count / total_merchants) if total_merchants else 0
        )
    except Exception:
        context["agreement_signed_count"] = 0
        context["agreement_unsigned_count"] = 0
        context["recent_agreements"] = []
        context["agreement_compliance_pct"] = 0

    return render(request, "dashboard/hq.html", context)


def staff_dashboard(request):
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    try:
        profile = request.user.profile
    except Exception:
        profile = None
    if not (
        profile
        and profile.user_type in [
            UserProfile.USER_TYPE_FOUNDER,
            UserProfile.USER_TYPE_STAFF,
            UserProfile.USER_TYPE_EXECUTIVE,
        ]
    ) and not user_can_access_module(request.user, MODULE_STAFF_ROLES):
        return render(
            request,
            "accounts/role_forbidden.html",
            {"dashboard_url": role_redirect_url(request.user)},
            status=403,
        )
    return render(request, "dashboard/staff_dashboard.html", founder_staff_dashboard_context(request.user))


@staff_module_required(MODULE_VOLTS)
def hq_volts_engine(request):
    transactions = VoltsTransaction.objects.select_related(
        "user",
        "user__profile",
        "user__profile__rank",
        "user__profile__staff_role",
        "action_type",
        "department",
        "approved_by",
    ).order_by("-created_at")
    filters = {
        "start_date": request.GET.get("start_date", ""),
        "end_date": request.GET.get("end_date", ""),
        "department": request.GET.get("department", ""),
        "role": request.GET.get("role", ""),
        "rank": request.GET.get("rank", ""),
        "user": request.GET.get("user", ""),
        "status": request.GET.get("status", ""),
    }
    if filters["start_date"]:
        start_date = parse_date(filters["start_date"])
        if start_date:
            transactions = transactions.filter(created_at__date__gte=start_date)
    if filters["end_date"]:
        end_date = parse_date(filters["end_date"])
        if end_date:
            transactions = transactions.filter(created_at__date__lte=end_date)
    if filters["department"]:
        transactions = transactions.filter(department_id=filters["department"])
    if filters["role"]:
        transactions = transactions.filter(user__profile__role=filters["role"])
    if filters["rank"]:
        transactions = transactions.filter(user__profile__rank_id=filters["rank"])
    if filters["user"]:
        transactions = transactions.filter(user_id=filters["user"])
    if filters["status"]:
        transactions = transactions.filter(status=filters["status"])

    pending = transactions.filter(status=VoltsTransaction.STATUS_PENDING)
    approved = transactions.filter(status__in=[VoltsTransaction.STATUS_APPROVED, VoltsTransaction.STATUS_PAID])
    reversed_transactions = transactions.filter(status=VoltsTransaction.STATUS_REVERSED)
    pending_amount = pending.aggregate(total=Sum("estimated_amount_mwk"))["total"] or Decimal("0")
    top_earner = (
        approved.values("user__username", "user__first_name", "user__last_name")
        .annotate(total=Sum("final_volts"), pay=Sum("estimated_amount_mwk"))
        .order_by("-pay")
        .first()
    )
    highest_pending_department = (
        pending.values("department__name")
        .annotate(total=Sum("final_volts"), count=Count("id"))
        .order_by("-count")
        .first()
    )
    month_summary = {
        "total_volts": approved.aggregate(total=Sum("final_volts"))["total"] or Decimal("0"),
        "approved_volts": approved.aggregate(total=Sum("final_volts"))["total"] or Decimal("0"),
        "pending_volts": pending.aggregate(total=Sum("final_volts"))["total"] or Decimal("0"),
        "reversed_volts": reversed_transactions.aggregate(total=Sum("final_volts"))["total"] or Decimal("0"),
        "estimated_payroll": approved.aggregate(total=Sum("estimated_amount_mwk"))["total"] or Decimal("0"),
        "pending_amount": pending_amount,
        "pending_count": pending.count(),
        "approved_count": approved.count(),
        "reversed_count": reversed_transactions.count(),
        "penalties": PenaltyTransaction.objects.filter(status=PenaltyTransaction.STATUS_APPROVED).count(),
        "top_earner": top_earner,
        "highest_pending_department": highest_pending_department,
    }
    by_department = (
        approved
        .values("department__name")
        .annotate(total=Sum("final_volts"), count=Count("id"))
        .order_by("-total")
    )
    by_user = (
        approved
        .values("user", "user__username", "user__first_name", "user__last_name")
        .annotate(total=Sum("final_volts"), pay=Sum("estimated_amount_mwk"))
        .order_by("-total")[:20]
    )
    insights = []
    if pending.count():
        insights.append(_insight(
            "Volts pending approval",
            "warning",
            f"{pending.count()} transaction(s) are waiting for approval.",
            "Department leads should review evidence and approve or reject before payroll.",
            reverse("hq_volts_engine"),
        ))
    if reversed_transactions.count():
        insights.append(_insight(
            "Reversed volts detected",
            "critical",
            f"{reversed_transactions.count()} reversed transaction(s) are inside this filter view.",
            "Open the evidence trail before approving related payouts.",
            reverse("hq_volts_engine") + "?status=reversed",
        ))
    if not insights:
        insights.append(_insight(
            "Volts approvals clear",
            "success",
            "No pending approval risk in the current filter view.",
            "Keep approval discipline tight and audit large payouts before month-end.",
            reverse("hq_volts_engine"),
        ))
    return render(request, "dashboard/hq_volts_engine.html", {
        "summary": month_summary,
        "pending_transactions": pending[:50],
        "transactions": transactions[:100],
        "action_types": VoltsActionType.objects.select_related("department").filter(active=True),
        "departments": Department.objects.filter(active=True),
        "ranks": Rank.objects.filter(active=True),
        "roles": UserProfile.ROLE_CHOICES,
        "statuses": VoltsTransaction.STATUS_CHOICES,
        "staff_users": get_user_model().objects.select_related("profile").filter(profile__isnull=False).order_by("username")[:300],
        "filters": filters,
        "active_filter_chips": _active_filter_chips(filters),
        "by_department": by_department,
        "by_user": by_user,
        "insights": insights,
        "formula": "approved_volts_pay = approved_volts x volt_rate x rank_multiplier x quality_score x discipline_score x results_score",
        "can_approve": True,
    })


@staff_module_required(MODULE_VOLTS)
def hq_volts_action(request, transaction_id):
    if request.method != "POST":
        return redirect("hq_volts_engine")
    transaction = get_object_or_404(VoltsTransaction, id=transaction_id)
    action = request.POST.get("action")
    if not can_approve_volts(request.user, transaction):
        messages.error(request, "You cannot approve or reject this volts transaction.")
        return redirect("hq_volts_engine")
    if action == "approve":
        try:
            transaction.approve(request.user)
            audit_sensitive_action(request.user, "volts_approved", transaction, {"worker": transaction.user_id})
            messages.success(request, "Volts transaction approved.")
        except ValueError as exc:
            messages.error(request, str(exc))
    elif action == "reject":
        transaction.status = VoltsTransaction.STATUS_REJECTED
        transaction.approved_by = request.user
        transaction.approved_at = timezone.now()
        transaction.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
        audit_sensitive_action(request.user, "volts_rejected", transaction, {"worker": transaction.user_id})
        messages.success(request, "Volts transaction rejected.")
    return redirect("hq_volts_engine")


@staff_module_required(MODULE_EQUITY)
def hq_founder_equity(request):
    if not can_edit_equity(request.user):
        return render(
            request,
            "accounts/role_forbidden.html",
            {"dashboard_url": role_redirect_url(request.user)},
            status=403,
        )
    records = FounderEquityRecord.objects.select_related("founder_user", "founder_user__profile").order_by("role_title")
    structure = CompanyShareStructure.objects.first()
    if not structure:
        structure = CompanyShareStructure.objects.create(
            total_authorized_shares=100000,
            founder_pool=70000,
            investor_reserve=20000,
            employee_advisor_pool=10000,
        )
    founder_total = records.aggregate(total=Sum("allocated_shares"))["total"] or 0
    vested_total = records.aggregate(total=Sum("vested_shares"))["total"] or 0
    unvested_total = records.aggregate(total=Sum("unvested_shares"))["total"] or 0
    authorized = getattr(structure, "total_authorized_shares", 0) or 0
    unallocated = max(authorized - founder_total, 0)
    inactive_count = records.filter(Q(inactive_flag=True) | Q(breach_flag=True)).count()
    founder_rows = []
    for record in records:
        profile = getattr(record.founder_user, "profile", None)
        latest_period = (
            DisciplineScorePeriod.objects.filter(user=record.founder_user)
            .only("final_score")
            .order_by("-month")
            .first()
        )
        founder_rows.append({
            "record": record,
            "profile": profile,
            "last_kpi_score": (
                KPIResult.objects.filter(user=record.founder_user)
                .order_by("-period")
                .values_list("score", flat=True)
                .first()
            ),
            "last_discipline_score": latest_period.final_score if latest_period else None,
            "risk_flag": "Inactive founder" if record.inactive_flag else ("Breach flag" if record.breach_flag else "On track"),
            "risk_class": "inactive" if record.inactive_flag else ("breach" if record.breach_flag else "ok"),
        })
    insights = []
    if not records.exists():
        insights.append(_insight(
            "Founder equity records incomplete",
            "critical",
            "No founder allocation records exist yet.",
            "Run the staff system seed command or add founder records before generating legal annexures.",
        ))
    if founder_total != 70000:
        insights.append(_insight(
            "Founder allocation differs from target",
            "warning",
            f"Founder allocated shares are {founder_total:,}; target founder pool allocation is 70,000.",
            "Review founder records and regenerate vesting annexures after correction.",
        ))
    if inactive_count:
        insights.append(_insight(
            "Inactive founder flags",
            "critical",
            f"{inactive_count} founder record(s) have inactive or breach flags.",
            "Pause vesting review and open the audit trail before any ownership change.",
        ))
    if not insights:
        insights.append(_insight(
            "Founder equity structure healthy",
            "success",
            "Founder records align with the internal ownership cockpit.",
            "Keep signed legal documents synced with internal tracking.",
        ))
    return render(request, "dashboard/hq_founder_equity.html", {
        "records": records,
        "founder_rows": founder_rows,
        "structure": structure,
        "founder_total": founder_total,
        "vested_total": vested_total,
        "unvested_total": unvested_total,
        "unallocated": unallocated,
        "inactive_count": inactive_count,
        "insights": insights,
        "legal_notice": "The app tracks equity and vesting internally, but signed legal documents govern actual ownership.",
    })


@staff_module_required(MODULE_STAFF_ROLES)
def hq_staff_roles(request):
    User = get_user_model()
    profiles = UserProfile.objects.select_related("user", "department", "staff_role", "rank", "supervisor").order_by("user__username")
    filters = {
        "q": request.GET.get("q", "").strip(),
        "role": request.GET.get("role", ""),
        "department": request.GET.get("department", ""),
        "rank": request.GET.get("rank", ""),
        "status": request.GET.get("status", ""),
        "founder": request.GET.get("founder", ""),
        "missing": request.GET.get("missing", ""),
    }
    if filters["q"]:
        profiles = profiles.filter(
            Q(user__username__icontains=filters["q"])
            | Q(user__first_name__icontains=filters["q"])
            | Q(user__last_name__icontains=filters["q"])
            | Q(user__email__icontains=filters["q"])
            | Q(full_name__icontains=filters["q"])
            | Q(email__icontains=filters["q"])
            | Q(phone_number__icontains=filters["q"])
            | Q(phone__icontains=filters["q"])
        )
    if filters["role"]:
        profiles = profiles.filter(role=filters["role"])
    if filters["department"]:
        profiles = profiles.filter(department_id=filters["department"])
    if filters["rank"]:
        profiles = profiles.filter(rank_id=filters["rank"])
    if filters["status"]:
        profiles = profiles.filter(status=filters["status"])
    if filters["founder"]:
        profiles = profiles.filter(is_founder=_is_truthy_filter(filters["founder"]))
    if filters["missing"] == "email":
        profiles = profiles.filter(Q(email="") & Q(user__email=""))
    elif filters["missing"] == "rank":
        profiles = profiles.filter(rank__isnull=True)
    elif filters["missing"] == "department":
        profiles = profiles.filter(department__isnull=True)
    elif filters["missing"] == "supervisor":
        profiles = profiles.filter(supervisor__isnull=True)

    profile_rows = [_profile_row(profile) for profile in profiles[:200]]
    all_profiles = UserProfile.objects.select_related("user", "department", "staff_role", "rank")
    missing_email_count = all_profiles.filter(Q(email="") & Q(user__email="")).count()
    missing_rank_count = all_profiles.filter(rank__isnull=True).count()
    suspended_count = all_profiles.filter(status=UserProfile.STATUS_SUSPENDED).count()
    underwriter_count = all_profiles.filter(role=UserProfile.ROLE_UNDERWRITER).count()
    merchant_admin_count = all_profiles.filter(role=UserProfile.ROLE_MERCHANT_ADMIN).count()
    merchant_count = all_profiles.filter(role=UserProfile.ROLE_MERCHANT).count()
    pending_reviews = all_profiles.filter(Q(next_review_date__lte=timezone.localdate()) | Q(probation_status=UserProfile.PROBATION_ACTIVE)).count()
    insights = []
    if missing_email_count:
        insights.append(_insight(
            "Users missing email",
            "warning",
            f"{missing_email_count} profile(s) cannot receive formal notices by email.",
            "Filter by Missing Email and complete identity records.",
            reverse("hq_staff_roles") + "?missing=email",
        ))
    if missing_rank_count:
        insights.append(_insight(
            "Staff profile missing rank",
            "critical",
            f"{missing_rank_count} profile(s) have no rank, weakening compensation governance.",
            "Assign rank before Volts payout or compensation documents are generated.",
            reverse("hq_staff_roles") + "?missing=rank",
        ))
    if suspended_count:
        insights.append(_insight(
            "Suspended users present",
            "info",
            f"{suspended_count} user(s) are suspended.",
            "Review access and audit logs before reactivation.",
            reverse("hq_staff_roles") + "?status=suspended",
        ))
    if not insights:
        insights.append(_insight(
            "Identity governance healthy",
            "success",
            "No major rank, email, or suspension warnings are active.",
            "Keep profile completeness above 90% and review staff monthly.",
            reverse("hq_staff_roles"),
        ))
    return render(request, "dashboard/hq_staff_roles.html", {
        "profiles": profiles,
        "profile_rows": profile_rows,
        "departments": Department.objects.filter(active=True),
        "roles": StaffRole.objects.select_related("department").filter(active=True),
        "portal_roles": UserProfile.ROLE_CHOICES,
        "statuses": UserProfile.STATUS_CHOICES,
        "ranks": Rank.objects.filter(active=True),
        "founder_count": all_profiles.filter(is_founder=True).count(),
        "staff_count": all_profiles.filter(user_type__in=[UserProfile.USER_TYPE_STAFF, UserProfile.USER_TYPE_EXECUTIVE, UserProfile.USER_TYPE_FOUNDER]).count(),
        "active_count": all_profiles.filter(status=UserProfile.STATUS_ACTIVE).count(),
        "merchant_count": merchant_count,
        "underwriter_count": underwriter_count,
        "merchant_admin_count": merchant_admin_count,
        "missing_email_count": missing_email_count,
        "missing_rank_count": missing_rank_count,
        "suspended_count": suspended_count,
        "pending_reviews": pending_reviews,
        "filters": filters,
        "active_filter_chips": _active_filter_chips(filters),
        "insights": insights,
        "users": User.objects.select_related("profile").order_by("username")[:200],
    })


@staff_module_required(MODULE_DISCIPLINE)
def hq_discipline_governance(request):
    return render(request, "dashboard/hq_discipline_governance.html", governance_dashboard_context())


@staff_module_required(MODULE_DISCIPLINE)
def hq_discipline_action(request, period_id):
    if request.method != "POST":
        return redirect("hq_discipline_governance")
    period = get_object_or_404(DisciplineScorePeriod, id=period_id)
    action = request.POST.get("action")
    reason = request.POST.get("reason", "")
    try:
        if action == "approve":
            approve_discipline_period(period, request.user, reason=reason)
            messages.success(request, "Discipline score approved.")
        elif action == "approve_payout":
            approve_payout_impact(period, request.user, reason=reason)
            messages.success(request, "Payout impact approved.")
        elif action == "lock":
            if period.user_id == request.user.id:
                raise ValueError("Users cannot lock their own discipline score.")
            period.status = DisciplineScorePeriod.STATUS_LOCKED
            period.save(update_fields=["status", "updated_at"])
            audit_sensitive_action(request.user, "discipline_score_locked", period, detail={"reason": reason}, reason=reason, request=request)
            messages.success(request, "Discipline score locked.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("hq_discipline_governance")


@staff_module_required(MODULE_KPIS)
def hq_kpi_dashboard(request):
    results = KPIResult.objects.select_related("user", "user__profile", "user__profile__department", "kpi_template").order_by("-period")
    templates = KPITemplate.objects.select_related("role", "department").filter(active=True)
    return render(request, "dashboard/hq_kpi_dashboard.html", {
        "results": results[:120],
        "templates": templates,
        "avg_score": results.aggregate(avg=Avg("score"))["avg"] or Decimal("0"),
        "approved_count": results.filter(status=KPIResult.STATUS_APPROVED).count(),
        "draft_count": results.filter(status=KPIResult.STATUS_DRAFT).count(),
        "department_scores": results.values("user__profile__department__name").annotate(avg=Avg("score"), count=Count("id")).order_by("user__profile__department__name"),
    })


@staff_module_required(MODULE_PAYOUTS)
def hq_payout_approvals(request):
    periods = DisciplineScorePeriod.objects.select_related(
        "user", "user__profile", "user__profile__department", "approved_by", "payout_impact_approved_by"
    ).filter(status=DisciplineScorePeriod.STATUS_APPROVED).order_by("-month")
    rows = []
    for period in periods[:100]:
        if can_view_compensation(request.user, period.user):
            rows.append({"period": period, "volts": founder_staff_dashboard_context(period.user)["volts"]})
    return render(request, "dashboard/hq_payout_approvals.html", {
        "rows": rows,
        "pending_count": periods.filter(payout_impact_approved_at__isnull=True).count(),
    })


@staff_module_required(MODULE_STAFF_DOCUMENTS)
def hq_staff_documents(request):
    profiles = (
        UserProfile.objects
        .select_related("user", "department", "staff_role", "rank")
        .filter(user__is_active=True)
        .order_by("user__first_name", "user__last_name")
    )
    active_sig = ExecutiveSignature.get_active()
    return render(request, "dashboard/hq_staff_documents.html", {
        "profiles": profiles,
        "document_types": StaffDocument.DOC_CHOICES,
        "active_signature": active_sig,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Operational Document / Notice Templates
# ─────────────────────────────────────────────────────────────────────────────

@hq_required
def hq_operational_notices(request):
    """Polished operational document templates for HQ use."""
    from portal.models import PaymentContract
    from decimal import Decimal

    # Gather a sample of overdue/active applications for context data in previews
    overdue_apps = (
        FinancingApplication.objects
        .select_related("deal", "merchant", "claimed_by")
        .filter(status__in=["active_contract", "rejected"])
        .order_by("-submitted_at")[:20]
    )

    overdue_contracts = (
        PaymentContract.objects
        .select_related("application", "application__deal")
        .filter(status__in=["overdue", "locked"])
        .order_by("-created_at")[:20]
    )

    today_str = timezone.now().strftime("%d %B %Y")

    return render(request, "dashboard/hq_operational_notices.html", {
        "overdue_apps": overdue_apps,
        "overdue_contracts": overdue_contracts,
        "today_str": today_str,
        "doc_types": [
            {
                "id": "termination_notice",
                "title": "Customer Termination Notice",
                "icon": "person-x",
                "color": "red",
                "description": "Formal notice of account termination due to default.",
                "tone": "Firm, lawful, final.",
            },
            {
                "id": "final_demand",
                "title": "Final Demand Notice",
                "icon": "exclamation-triangle",
                "color": "red",
                "description": "Last payment demand before legal/recovery steps.",
                "tone": "Urgent, clear, professional.",
            },
            {
                "id": "device_lock_warning",
                "title": "Device Lock Warning",
                "icon": "lock",
                "color": "amber",
                "description": "Warning that device will be locked if payment is not made.",
                "tone": "Informative, preventive.",
            },
            {
                "id": "payment_reminder",
                "title": "Payment Reminder",
                "icon": "calendar-check",
                "color": "blue",
                "description": "Friendly reminder of upcoming or overdue payment.",
                "tone": "Polite, helpful.",
            },
            {
                "id": "merchant_warning",
                "title": "Merchant Warning",
                "icon": "shop",
                "color": "amber",
                "description": "Formal warning to merchant about compliance or quality issues.",
                "tone": "Professional, corrective.",
            },
            {
                "id": "uw_quality_warning",
                "title": "Underwriter Quality Warning",
                "icon": "person-check",
                "color": "amber",
                "description": "Warning to underwriter about review quality or errors.",
                "tone": "Internal, constructive.",
            },
            {
                "id": "recovery_assignment",
                "title": "Recovery Assignment Note",
                "icon": "arrow-repeat",
                "color": "orange",
                "description": "Note assigning a customer account to recovery team.",
                "tone": "Internal, operational.",
            },
            {
                "id": "contract_summary",
                "title": "Contract Summary",
                "icon": "file-text",
                "color": "green",
                "description": "Summary of customer financing agreement terms.",
                "tone": "Factual, neutral.",
            },
            {
                "id": "guarantor_notice",
                "title": "Guarantor Notice",
                "icon": "people",
                "color": "blue",
                "description": "Notice to guarantor about default liability.",
                "tone": "Formal, lawful.",
            },
            {
                "id": "settlement_confirmation",
                "title": "Settlement Confirmation",
                "icon": "check-circle",
                "color": "green",
                "description": "Confirmation that an account has been fully settled.",
                "tone": "Positive, conclusive.",
            },
        ],
    })


# ─────────────────────────────────────────────────────────────────────────────
# Executive Signature Management
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def hq_executive_signatures(request):
    """Manage CEO / executive signatures used in official documents."""
    from accounts.utils import is_hq
    if not (is_hq(request.user) or request.user.is_superuser):
        return render(request, "accounts/role_forbidden.html",
                      {"dashboard_url": role_redirect_url(request.user)}, status=403)

    sigs = ExecutiveSignature.objects.select_related("user", "created_by", "revoked_by").order_by("-created_at")

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "upload":
            full_name = request.POST.get("full_name", "").strip()
            position  = request.POST.get("position", "Chief Executive Officer").strip()
            image     = request.FILES.get("signature_image")
            if not full_name or not image:
                messages.error(request, "Full name and signature image are required.")
            else:
                # Deactivate all existing active signatures before uploading new one
                ExecutiveSignature.objects.filter(is_active=True).update(is_active=False)
                sig = ExecutiveSignature.objects.create(
                    user=request.user,
                    full_name=full_name,
                    position=position,
                    signature_image=image,
                    is_active=True,
                    created_by=request.user,
                )
                audit_sensitive_action(request.user, "executive_signature_uploaded", sig, request=request)
                messages.success(request, f"Signature for {full_name} uploaded and set as active.")
            return redirect("hq_executive_signatures")

        if action == "revoke":
            sig_id = request.POST.get("signature_id")
            sig = get_object_or_404(ExecutiveSignature, id=sig_id)
            from django.utils import timezone as tz
            sig.is_active = False
            sig.revoked_at = tz.now()
            sig.revoked_by = request.user
            sig.save(update_fields=["is_active", "revoked_at", "revoked_by"])
            audit_sensitive_action(request.user, "executive_signature_revoked", sig, request=request)
            messages.warning(request, f"Signature for {sig.full_name} has been revoked.")
            return redirect("hq_executive_signatures")

        if action == "set_active":
            sig_id = request.POST.get("signature_id")
            sig = get_object_or_404(ExecutiveSignature, id=sig_id)
            ExecutiveSignature.objects.filter(is_active=True).update(is_active=False)
            sig.is_active = True
            sig.revoked_at = None
            sig.revoked_by = None
            sig.save(update_fields=["is_active", "revoked_at", "revoked_by"])
            audit_sensitive_action(request.user, "executive_signature_activated", sig, request=request)
            messages.success(request, f"Signature for {sig.full_name} set as active.")
            return redirect("hq_executive_signatures")

    active_sig = ExecutiveSignature.get_active()
    return render(request, "dashboard/hq_executive_signatures.html", {
        "signatures": sigs,
        "active_signature": active_sig,
        "page_title": "Executive Signatures",
    })


def staff_document_pdf(request, user_id, document_type):
    User = get_user_model()
    target_user = get_object_or_404(User, id=user_id)
    may_view_own = request.user.is_authenticated and request.user.id == target_user.id
    may_generate = request.user.is_authenticated and user_can_access_module(request.user, MODULE_STAFF_DOCUMENTS)
    if document_type in {StaffDocument.DOC_COMPENSATION_ANNEXURE, StaffDocument.DOC_MONTHLY_VOLTS}:
        may_generate = may_generate and can_view_compensation(request.user, target_user)
    if document_type == StaffDocument.DOC_EMPLOYMENT_AGREEMENT:
        try:
            target_profile = target_user.profile
        except Exception:
            target_profile = None
        if getattr(target_profile, "is_founder", False):
            may_generate = may_generate and can_view_equity(request.user, target_user)
    if not (may_view_own or may_generate):
        return render(request, "accounts/role_forbidden.html", {"dashboard_url": role_redirect_url(request.user)}, status=403)
    valid_types = {code for code, _label in StaffDocument.DOC_CHOICES}
    if document_type not in valid_types:
        messages.error(request, "Unknown staff document type.")
        return redirect("staff_dashboard")
    pdf_bytes, record = generate_staff_document_pdf(target_user, document_type, prepared_by=request.user, request=request)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{record.document_number}.pdf"'
    return response


def staff_discipline_acknowledge(request, period_id):
    period = get_object_or_404(DisciplineScorePeriod, id=period_id)
    if not request.user.is_authenticated or request.user.id != period.user_id:
        return render(request, "accounts/role_forbidden.html", {"dashboard_url": role_redirect_url(request.user)}, status=403)
    if request.method == "POST":
        period.acknowledge()
        audit_sensitive_action(request.user, "discipline_score_acknowledged", period, request=request)
        messages.success(request, "Discipline score acknowledged.")
    return redirect("staff_dashboard")


def staff_discipline_dispute(request, period_id):
    period = get_object_or_404(DisciplineScorePeriod, id=period_id)
    if not request.user.is_authenticated or request.user.id != period.user_id:
        return render(request, "accounts/role_forbidden.html", {"dashboard_url": role_redirect_url(request.user)}, status=403)
    if request.method == "POST":
        text = request.POST.get("dispute_text", "").strip()
        if text:
            dispute = DisciplineDispute.objects.create(
                user=request.user,
                discipline_score_period=period,
                dispute_text=text,
            )
            period.dispute_status = DisciplineScorePeriod.DISPUTE_SUBMITTED
            period.status = DisciplineScorePeriod.STATUS_DISPUTED
            period.user_response_text = text
            period.save(update_fields=["dispute_status", "status", "user_response_text", "updated_at"])
            audit_sensitive_action(request.user, "discipline_dispute_submitted", dispute, request=request)
            messages.success(request, "Response submitted.")
        else:
            messages.error(request, "Please enter your response before submitting.")
    return redirect("staff_dashboard")


@hq_required
def hq_users(request):
    from core.models import AuditLog
    User = get_user_model()
    if request.method == "POST":
        form = HQUserForm(request.POST, creating=True)
        if form.is_valid():
            new_user = form.save()
            AuditLog.objects.create(
                user=request.user,
                action="hq_user_create",
                object_type="User",
                object_id=str(new_user.pk),
                detail={
                    "username": new_user.username,
                    "role": form.cleaned_data.get("role", ""),
                    "created_by": request.user.username,
                },
            )
            messages.success(request, f"User '{new_user.username}' created successfully.")
            return redirect("hq_users")
    else:
        form = HQUserForm(creating=True)

    users = User.objects.select_related("profile").prefetch_related("groups").order_by("username")
    rows = [{"user": user, "role": primary_role(user) or "unassigned"} for user in users]
    return render(request, "dashboard/hq_users.html", {"form": form, "rows": rows})


@hq_required
def hq_user_edit(request, user_id):
    User = get_user_model()
    user_obj = get_object_or_404(User.objects.select_related("profile").prefetch_related("groups"), id=user_id)
    if request.method == "POST":
        form = HQUserForm(request.POST, instance=user_obj)
        if form.is_valid():
            form.save()
            messages.success(request, "User updated successfully.")
            return redirect("hq_users")
    else:
        form = HQUserForm(instance=user_obj)
    return render(request, "dashboard/hq_user_edit.html", {"form": form, "user_obj": user_obj})


@hq_required
def hq_deals(request):
    from deals.models import DeviceBrand, DeviceDeal
    from decimal import Decimal, InvalidOperation
    from core.models import AuditLog
    from collections import OrderedDict

    msg = None
    msg_type = "info"

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "add_deal":
            try:
                brand_id = request.POST.get("brand_id")
                brand = DeviceBrand.objects.get(pk=brand_id)
                deal = DeviceDeal(
                    brand=brand,
                    model_name=request.POST.get("model_name", "").strip(),
                    specs=request.POST.get("specs", "").strip(),
                    cash_price=Decimal(request.POST.get("cash_price", "0") or "0"),
                    min_cash_price=Decimal(request.POST.get("min_cash_price", "0") or "0"),
                    max_cash_price=Decimal(request.POST.get("max_cash_price", "0") or "0"),
                    default_cash_price=Decimal(request.POST.get("default_cash_price", "0") or "0"),
                    deposit_percent=Decimal(request.POST.get("deposit_percent", "13") or "13"),
                    loan_multiplier=Decimal(request.POST.get("loan_multiplier", "2.5") or "2.5"),
                    term_months=int(request.POST.get("term_months", "12") or "12"),
                    unlock_days=int(request.POST.get("unlock_days", "7") or "7"),
                    is_active="is_active" in request.POST,
                    is_featured="is_featured" in request.POST,
                    stock_status=request.POST.get("stock_status", "in_stock"),
                    lock_provider=request.POST.get("lock_provider", ""),
                    country=request.POST.get("country", "MW").strip() or "MW",
                    notes=request.POST.get("notes", "").strip(),
                )
                deal.full_clean()
                deal.save()
                AuditLog.objects.create(
                    user=request.user,
                    action="hq_deal_add",
                    object_type="DeviceDeal",
                    object_id=str(deal.pk),
                    detail={"brand": brand.name, "model": deal.model_name},
                )
                msg = f"Deal '{deal}' created successfully."
                msg_type = "success"
            except Exception as exc:
                msg = f"Error creating deal: {exc}"
                msg_type = "error"

        elif action == "edit_deal":
            deal_id = request.POST.get("deal_id")
            try:
                deal = DeviceDeal.objects.get(pk=deal_id)
                brand_id = request.POST.get("brand_id")
                deal.brand = DeviceBrand.objects.get(pk=brand_id)
                deal.model_name = request.POST.get("model_name", deal.model_name).strip()
                deal.specs = request.POST.get("specs", deal.specs).strip()
                deal.cash_price = Decimal(request.POST.get("cash_price") or str(deal.cash_price))
                deal.min_cash_price = Decimal(request.POST.get("min_cash_price") or str(deal.min_cash_price))
                deal.max_cash_price = Decimal(request.POST.get("max_cash_price") or str(deal.max_cash_price))
                deal.default_cash_price = Decimal(request.POST.get("default_cash_price") or str(deal.default_cash_price))
                deal.deposit_percent = Decimal(request.POST.get("deposit_percent") or str(deal.deposit_percent))
                deal.loan_multiplier = Decimal(request.POST.get("loan_multiplier") or str(deal.loan_multiplier))
                deal.term_months = int(request.POST.get("term_months") or str(deal.term_months))
                deal.unlock_days = int(request.POST.get("unlock_days") or str(deal.unlock_days))
                deal.is_active = "is_active" in request.POST
                deal.is_featured = "is_featured" in request.POST
                deal.stock_status = request.POST.get("stock_status", deal.stock_status)
                deal.lock_provider = request.POST.get("lock_provider", deal.lock_provider)
                deal.country = request.POST.get("country", deal.country).strip() or "MW"
                deal.notes = request.POST.get("notes", deal.notes).strip()
                deal.full_clean()
                deal.save()
                AuditLog.objects.create(
                    user=request.user,
                    action="hq_deal_edit",
                    object_type="DeviceDeal",
                    object_id=str(deal.pk),
                    detail={"model": deal.model_name},
                )
                msg = f"Deal '{deal}' updated."
                msg_type = "success"
            except Exception as exc:
                msg = f"Error updating deal: {exc}"
                msg_type = "error"

        elif action == "toggle_deal":
            deal_id = request.POST.get("deal_id")
            try:
                deal = DeviceDeal.objects.get(pk=deal_id)
                deal.is_active = not deal.is_active
                deal.save(update_fields=["is_active"])
                AuditLog.objects.create(
                    user=request.user,
                    action="hq_deal_toggle",
                    object_type="DeviceDeal",
                    object_id=str(deal.pk),
                    detail={"is_active": deal.is_active},
                )
                msg = f"Deal '{deal}' {'activated' if deal.is_active else 'deactivated'}."
                msg_type = "success"
            except Exception as exc:
                msg = f"Error: {exc}"
                msg_type = "error"

        elif action == "add_brand":
            brand_name = request.POST.get("brand_name", "").strip()
            if brand_name:
                existing_brand = DeviceBrand.objects.filter(name__iexact=brand_name).first()
                if existing_brand:
                    msg = f"Brand '{existing_brand.name}' already exists."
                    msg_type = "info"
                else:
                    brand = DeviceBrand.objects.create(name=brand_name)
                    AuditLog.objects.create(
                        user=request.user,
                        action="hq_brand_add",
                        object_type="DeviceBrand",
                        object_id=str(brand.pk),
                        detail={"brand": brand.name},
                    )
                    msg = f"Brand '{brand.name}' created."
                    msg_type = "success"
            else:
                msg = "Brand name is required."
                msg_type = "error"

    search = request.GET.get("search", "").strip()
    brand_filter = request.GET.get("brand", "")
    status_filter = request.GET.get("status", "")

    deals_qs = DeviceDeal.objects.select_related("brand").order_by("brand__name", "model_name")
    if search:
        deals_qs = deals_qs.filter(
            Q(model_name__icontains=search)
            | Q(specs__icontains=search)
            | Q(brand__name__icontains=search)
        )
    if brand_filter:
        deals_qs = deals_qs.filter(brand_id=brand_filter)
    if status_filter == "active":
        deals_qs = deals_qs.filter(is_active=True)
    elif status_filter == "inactive":
        deals_qs = deals_qs.filter(is_active=False)

    brands = DeviceBrand.objects.order_by("name")
    main_brand_keys = ["TECNO", "ITEL", "SAMSUNG", "REDMI"]
    brand_groups = OrderedDict()

    def brand_group_key(brand_name):
        normalized = (brand_name or "").strip().upper()
        if "XIAOMI" in normalized:
            return "REDMI"
        for key in main_brand_keys:
            if key in normalized:
                return key
        return "OTHER"

    def display_brand_id_for(key):
        for brand in brands:
            if brand_group_key(brand.name) == key:
                return brand.pk
        return None

    for key in [*main_brand_keys, "OTHER"]:
        brand_groups[key] = {
            "key": key,
            "name": key.title() if key != "OTHER" else "Other",
            "deals": [],
            "count": 0,
            "active_count": 0,
            "inactive_count": 0,
            "in_stock_count": 0,
            "brand_id": display_brand_id_for(key) if key != "OTHER" else None,
        }

    brand_groups["TECNO"]["name"] = "TECNO"
    brand_groups["ITEL"]["name"] = "ITEL"
    brand_groups["SAMSUNG"]["name"] = "SAMSUNG"
    brand_groups["REDMI"]["name"] = "REDMI"

    for deal in deals_qs:
        key = brand_group_key(deal.brand.name if deal.brand_id else "")
        if key not in brand_groups:
            key = "OTHER"
        group = brand_groups[key]
        group["deals"].append(deal)
        group["count"] += 1
        if deal.is_active:
            group["active_count"] += 1
        else:
            group["inactive_count"] += 1
        if deal.stock_status == DeviceDeal.STOCK_IN:
            group["in_stock_count"] += 1
        if key != "OTHER" and not group["brand_id"]:
            group["brand_id"] = deal.brand_id

    brand_groups = [brand_groups[key] for key in [*main_brand_keys, "OTHER"] if brand_groups[key]["count"] > 0 or key != "OTHER"]

    # Catalog intelligence stats
    all_deals = deals_qs
    total_active_models = all_deals.filter(is_active=True).count()
    total_in_stock = all_deals.filter(stock_status=DeviceDeal.STOCK_IN).count()
    deposit_pcts = list(all_deals.values_list("deposit_percent", flat=True))
    avg_deposit_pct = round(sum(deposit_pcts) / len(deposit_pcts), 1) if deposit_pcts else None

    return render(request, "dashboard/hq_deals.html", {
        "deals": deals_qs,
        "brand_groups": brand_groups,
        "brands": brands,
        "search": search,
        "brand_filter": brand_filter,
        "status_filter": status_filter,
        "msg": msg,
        "msg_type": msg_type,
        "total_active_models": total_active_models,
        "total_in_stock": total_in_stock,
        "avg_deposit_pct": avg_deposit_pct,
    })


@hq_required
def hq_applications(request):
    search = request.GET.get("search", "").strip()
    status_filter = request.GET.get("status", "")
    merchant_filter = request.GET.get("merchant", "").strip()
    dup_filter = request.GET.get("dup_risk", "")

    apps_qs = FinancingApplication.objects.select_related(
        "created_by", "created_by__profile",
        "claimed_by", "claimed_by__profile",
        "reviewed_by",
        "deal", "deal__brand",
    ).order_by("-created_at")

    if search:
        apps_qs = apps_qs.filter(
            Q(customer_name__icontains=search)
            | Q(national_id__icontains=search)
            | Q(customer_phone__icontains=search)
            | Q(application_number__icontains=search)
        )
    if status_filter:
        apps_qs = apps_qs.filter(status=status_filter)
    if merchant_filter:
        apps_qs = apps_qs.filter(
            Q(created_by__username__icontains=merchant_filter)
            | Q(created_by__first_name__icontains=merchant_filter)
            | Q(created_by__last_name__icontains=merchant_filter)
        )
    if dup_filter == "1":
        apps_qs = apps_qs.filter(third_party_phone_user_risk_flagged=True)

    export = request.GET.get("export", "")
    export_rows_qs = apps_qs[:1000]
    if export == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{dated_filename("applications", "csv")}"'
        writer = csv.writer(response)
        writer.writerow([
            "Application", "Customer", "Phone", "Merchant", "Underwriter",
            "Deal", "Deposit %", "Status", "Submitted",
        ])
        for app in export_rows_qs:
            writer.writerow([
                app.application_number or app.pk,
                app.customer_name,
                app.customer_phone,
                app.created_by.get_full_name() or app.created_by.username if app.created_by_id else "",
                app.claimed_by.get_full_name() or app.claimed_by.username if app.claimed_by_id else "",
                str(app.deal) if app.deal_id else "",
                app.selected_deposit_percent or "",
                app.get_status_display(),
                app.submitted_at.isoformat() if app.submitted_at else "",
            ])
        return response
    if export == "pdf":
        rows = [
            [
                app.application_number or app.pk,
                app.customer_name,
                app.customer_phone,
                app.created_by.get_full_name() or app.created_by.username if app.created_by_id else "",
                app.claimed_by.get_full_name() or app.claimed_by.username if app.claimed_by_id else "",
                str(app.deal) if app.deal_id else "",
                app.get_status_display(),
                app.submitted_at.strftime("%Y-%m-%d") if app.submitted_at else "",
            ]
            for app in export_rows_qs
        ]
        return export_pdf_response(
            "Applications",
            ["App", "Customer", "Phone", "Merchant", "Underwriter", "Deal", "Status", "Submitted"],
            rows,
            filename=dated_filename("applications", "pdf"),
            filters=[
                ("Search", search),
                ("Status", status_filter),
                ("Merchant", merchant_filter),
                ("Third-party risk", "Yes" if dup_filter == "1" else ""),
            ],
            summary=[("Matching applications", apps_qs.count())],
            landscape=True,
        )

    total_count = apps_qs.count()
    apps = apps_qs[:200]

    return render(
        request,
        "dashboard/hq_applications.html",
        {
            "title": "Applications",
            "subtitle": "Review submitted applications across the platform.",
            "apps": apps,
            "total_count": total_count,
            "search": search,
            "status_filter": status_filter,
            "merchant_filter": merchant_filter,
            "dup_filter": dup_filter,
            "show_actions": True,
        },
    )


@hq_required
def hq_underwriter_queue(request):
    User = get_user_model()
    search = request.GET.get("search", "").strip()

    apps_qs = (
        FinancingApplication.objects.select_related("created_by", "claimed_by", "reviewed_by")
        .filter(status__in=["pending_review", "under_review", "approved", "rejected", "resubmitted"])
        .order_by("-submitted_at", "-created_at")
    )
    if search:
        apps_qs = apps_qs.filter(
            Q(customer_name__icontains=search)
            | Q(application_number__icontains=search)
        )
    apps = apps_qs[:100]

    # Underwriter performance summary
    underwriters = User.objects.filter(profile__role="underwriter").order_by("username")
    uw_stats = []
    for uw in underwriters[:20]:
        active = FinancingApplication.objects.filter(claimed_by=uw, status="under_review").count()
        approved_today = FinancingApplication.objects.filter(
            reviewed_by=uw, status="approved",
            reviewed_at__date=timezone.now().date()
        ).count()
        rejected_today = FinancingApplication.objects.filter(
            reviewed_by=uw, status="rejected",
            reviewed_at__date=timezone.now().date()
        ).count()
        uw_stats.append({
            "user": uw,
            "active": active,
            "approved_today": approved_today,
            "rejected_today": rejected_today,
        })

    return render(
        request,
        "dashboard/hq_underwriter_queue.html",
        {
            "title": "Underwriter Queue",
            "subtitle": "Monitor submitted, claimed, and reviewed applications.",
            "apps": apps,
            "search": search,
            "uw_stats": uw_stats,
            "show_actions": True,
        },
    )


@hq_required
def hq_commissions(request):
    from commissions.models import CommissionLedger, MerchantContractPayout, UnderwriterMonthlyPayout
    from decimal import Decimal
    import csv
    from django.http import HttpResponse

    # ── CSV export ──────────────────────────────────────────────────
    export = request.GET.get("export", "")
    if export == "commissions_csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="commissions_ledger.csv"'
        writer = csv.writer(response)
        writer.writerow(["User", "Role", "Contract", "Type", "Base Amount", "Rate", "Amount", "Date"])
        for e in CommissionLedger.objects.select_related("user", "contract").order_by("-created_at")[:5000]:
            writer.writerow([
                e.user.username if e.user_id else "",
                "underwriter",
                e.contract.contract_number if e.contract_id else "",
                e.entry_type, e.base_amount, e.rate, e.amount,
                e.created_at.date(),
            ])
        return response

    if export == "merchant_csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="merchant_commissions.csv"'
        writer = csv.writer(response)
        writer.writerow(["Merchant", "Contract", "Cash Price", "Financed", "Commission", "Total Payable", "Status", "Date"])
        for p in MerchantContractPayout.objects.select_related("merchant", "contract").order_by("-created_at")[:5000]:
            writer.writerow([
                p.merchant.username if p.merchant_id else "",
                p.contract.contract_number if p.contract_id else "",
                p.cash_price, p.financed_amount, p.merchant_commission_amount,
                p.total_payable, p.status, p.created_at.date(),
            ])
        return response

    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    today_ledger = CommissionLedger.objects.filter(created_at__date=now.date())
    month_ledger = CommissionLedger.objects.filter(created_at__gte=month_start)

    def _sum(qs):
        return qs.aggregate(t=Sum("amount"))["t"] or Decimal("0")

    today_commissions = _sum(today_ledger.filter(entry_type=CommissionLedger.ENTRY_REPAYMENT))
    month_commissions = _sum(month_ledger.filter(entry_type=CommissionLedger.ENTRY_REPAYMENT))
    today_arrears = _sum(today_ledger.filter(entry_type=CommissionLedger.ENTRY_ARREARS))
    month_arrears = _sum(month_ledger.filter(entry_type=CommissionLedger.ENTRY_ARREARS))

    estimated_wht = max(month_commissions + month_arrears, Decimal("0")) * Decimal("0.20")
    net_uw_payout = month_commissions + month_arrears - estimated_wht

    # Merchant commissions
    merchant_payouts_pending = MerchantContractPayout.objects.filter(
        status=MerchantContractPayout.STATUS_PENDING
    ).aggregate(t=Sum("total_payable"))["t"] or Decimal("0")
    merchant_payouts_month = MerchantContractPayout.objects.filter(
        created_at__gte=month_start
    ).aggregate(t=Sum("merchant_commission_amount"))["t"] or Decimal("0")

    # WHT payable
    wht_pending_payouts = UnderwriterMonthlyPayout.objects.filter(
        status=UnderwriterMonthlyPayout.STATUS_PENDING
    ).aggregate(t=Sum("wht_amount"))["t"] or Decimal("0")

    # Top earners (underwriters)
    User = get_user_model()
    underwriter_users = User.objects.filter(profile__role="underwriter")
    top_earners = []
    for uw in underwriter_users[:15]:
        earned = _sum(CommissionLedger.objects.filter(user=uw, entry_type=CommissionLedger.ENTRY_REPAYMENT))
        deductions = _sum(CommissionLedger.objects.filter(user=uw, entry_type=CommissionLedger.ENTRY_ARREARS))
        wht = max(earned + deductions, Decimal("0")) * Decimal("0.20")
        top_earners.append({
            "user": uw,
            "earned": earned,
            "deductions": deductions,
            "gross": earned + deductions,
            "wht": wht,
            "net": (earned + deductions) - wht,
        })
    top_earners.sort(key=lambda x: x["net"], reverse=True)

    # Filters for ledger table
    user_filter = request.GET.get("user", "")
    type_filter = request.GET.get("type", "")
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")

    ledger_qs = CommissionLedger.objects.select_related("user", "contract").order_by("-created_at")
    if user_filter:
        ledger_qs = ledger_qs.filter(user__username__icontains=user_filter)
    if type_filter:
        ledger_qs = ledger_qs.filter(entry_type=type_filter)
    if date_from:
        try:
            from datetime import date
            ledger_qs = ledger_qs.filter(created_at__date__gte=date.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            from datetime import date
            ledger_qs = ledger_qs.filter(created_at__date__lte=date.fromisoformat(date_to))
        except ValueError:
            pass

    ledger_entries = ledger_qs[:100]
    merchant_payout_list = MerchantContractPayout.objects.select_related(
        "merchant", "contract"
    ).order_by("-created_at")[:50]
    uw_payouts = UnderwriterMonthlyPayout.objects.select_related("user").order_by("-period_end")[:30]
    commissions = Commission.objects.select_related("user", "application").order_by("-created_at")[:30]

    return render(request, "dashboard/hq_commissions.html", {
        "today_commissions": today_commissions,
        "month_commissions": month_commissions,
        "today_arrears": today_arrears,
        "month_arrears": month_arrears,
        "estimated_wht": estimated_wht,
        "net_uw_payout": net_uw_payout,
        "merchant_payouts_pending": merchant_payouts_pending,
        "merchant_payouts_month": merchant_payouts_month,
        "wht_pending_payouts": wht_pending_payouts,
        "top_earners": top_earners,
        "ledger_entries": ledger_entries,
        "merchant_payout_list": merchant_payout_list,
        "uw_payouts": uw_payouts,
        "commissions": commissions,
        "user_filter": user_filter,
        "type_filter": type_filter,
        "date_from": date_from,
        "date_to": date_to,
    })


@hq_required
def hq_merchant_payouts(request):
    from core.models import AuditLog

    msg = None
    msg_type = "info"

    def _merchant_profile_for(user):
        if not user:
            return None
        return Merchant.objects.filter(owner=user).order_by("id").first()

    def _block_if_non_compliant(payout, action_name):
        merchant_profile = _merchant_profile_for(payout.merchant)
        if merchant_profile and merchant_profile.is_compliance_complete:
            return False, merchant_profile, ""
        reason = merchant_profile.payout_block_reason if merchant_profile else "Blocked: merchant profile missing"
        payout.status = MerchantContractPayout.STATUS_BLOCKED
        payout.hold_reason = reason
        payout.save(update_fields=["status", "hold_reason", "updated_at"])
        AuditLog.objects.create(
            user=request.user,
            action=f"hq_payout_{action_name}_blocked",
            object_type="MerchantContractPayout",
            object_id=str(payout.pk),
            detail={"reason": reason, "amount": str(payout.total_payable)},
        )
        return True, merchant_profile, reason

    if request.method == "POST":
        action = request.POST.get("action", "")
        payout_id = request.POST.get("payout_id")
        try:
            payout = MerchantContractPayout.objects.get(pk=payout_id)
            if action in {"approve_payout", "queue_payout", "mark_paid", "release_hold"}:
                blocked, _, reason = _block_if_non_compliant(payout, action)
                if blocked:
                    messages.error(request, f"Payout #{payout.pk} blocked. {reason}")
                    return redirect(f"{reverse('hq_merchant_payouts')}?status=blocked")

            if action == "approve_payout":
                payout.status = MerchantContractPayout.STATUS_APPROVED
                payout.hold_reason = ""
                payout.save(update_fields=["status", "hold_reason", "updated_at"])
                AuditLog.objects.create(
                    user=request.user,
                    action="hq_payout_approved",
                    object_type="MerchantContractPayout",
                    object_id=str(payout.pk),
                    detail={"amount": str(payout.total_payable)},
                )
                messages.success(request, f"Payout #{payout.pk} approved for settlement control.")
                return redirect("hq_merchant_payouts")
            if action == "mark_paid":
                ref = request.POST.get("reference", "").strip()
                payout.mark_paid(reference=ref)
                AuditLog.objects.create(
                    user=request.user,
                    action="hq_payout_mark_paid",
                    object_type="MerchantContractPayout",
                    object_id=str(payout.pk),
                    detail={"reference": ref, "amount": str(payout.total_payable)},
                )
                messages.success(request, f"Payout #{payout.pk} marked as manually paid.")
                return redirect("hq_merchant_payouts")
            elif action == "queue_payout":
                payout.status = MerchantContractPayout.STATUS_PROCESSING
                payout.hold_reason = ""
                payout.save(update_fields=["status", "hold_reason", "updated_at"])
                AuditLog.objects.create(
                    user=request.user,
                    action="hq_payout_queued",
                    object_type="MerchantContractPayout",
                    object_id=str(payout.pk),
                    detail={"amount": str(payout.total_payable)},
                )
                messages.success(request, f"Payout #{payout.pk} queued for internal processing.")
                return redirect("hq_merchant_payouts")
            elif action == "hold_payout":
                reason = request.POST.get("hold_reason", "").strip() or "Held for internal review"
                payout.status = MerchantContractPayout.STATUS_HELD
                payout.hold_reason = reason[:255]
                payout.save(update_fields=["status", "hold_reason", "updated_at"])
                AuditLog.objects.create(
                    user=request.user,
                    action="hq_payout_held",
                    object_type="MerchantContractPayout",
                    object_id=str(payout.pk),
                    detail={"reason": reason, "amount": str(payout.total_payable)},
                )
                messages.info(request, f"Payout #{payout.pk} held for review.")
                return redirect("hq_merchant_payouts")
            elif action == "release_hold":
                payout.status = MerchantContractPayout.STATUS_PENDING
                payout.hold_reason = ""
                payout.save(update_fields=["status", "hold_reason", "updated_at"])
                messages.success(request, f"Payout #{payout.pk} released back to pending.")
                return redirect("hq_merchant_payouts")
            elif action == "mark_disputed":
                reason = request.POST.get("hold_reason", "").strip() or "Marked disputed by HQ"
                payout.status = MerchantContractPayout.STATUS_DISPUTED
                payout.hold_reason = reason[:255]
                payout.save(update_fields=["status", "hold_reason", "updated_at"])
                messages.info(request, f"Payout #{payout.pk} marked as disputed.")
                return redirect("hq_merchant_payouts")
            elif action == "add_note":
                payout.internal_note = request.POST.get("internal_note", "").strip()
                payout.save(update_fields=["internal_note", "updated_at"])
                messages.success(request, f"Internal note updated for payout #{payout.pk}.")
                return redirect("hq_merchant_payouts")
            elif action == "retry_payout":
                payout.status = MerchantContractPayout.STATUS_PENDING
                payout.hold_reason = ""
                payout.save(update_fields=["status", "hold_reason", "updated_at"])
                messages.info(request, f"Payout #{payout.pk} reset to pending for retry.")
                return redirect("hq_merchant_payouts")
        except MerchantContractPayout.DoesNotExist:
            msg = "Payout not found."
            msg_type = "error"
        except Exception as exc:
            msg = f"Error: {exc}"
            msg_type = "error"

    # Filters - apply BEFORE slicing
    status_filter = request.GET.get("status", "")
    merchant_filter = request.GET.get("merchant", "").strip()

    all_payouts = MerchantContractPayout.objects.select_related("merchant", "contract").order_by("-created_at")
    if status_filter:
        all_payouts = all_payouts.filter(status=status_filter)
    if merchant_filter:
        all_payouts = all_payouts.filter(merchant__username__icontains=merchant_filter)

    export = request.GET.get("export", "")
    if export == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{dated_filename("merchant-payout-control", "csv")}"'
        writer = csv.writer(response)
        writer.writerow([
            "Payout ID", "Merchant", "Compliance", "Status", "Cash Price",
            "Commission", "Total Payable", "Method", "Hold Reason", "Provider Reference", "Created",
        ])
        profiles = {
            merchant.owner_id: merchant for merchant in Merchant.objects.filter(
                owner_id__in=all_payouts.values_list("merchant_id", flat=True)
            ).select_related("owner")
        }
        for payout in all_payouts[:1000]:
            merchant_profile = profiles.get(payout.merchant_id)
            writer.writerow([
                payout.pk,
                payout.merchant.get_full_name() or payout.merchant.username,
                merchant_profile.compliance_label if merchant_profile else "Merchant profile missing",
                payout.get_status_display(),
                payout.cash_price,
                payout.merchant_commission_amount,
                payout.total_payable,
                payout.get_payout_method_display(),
                payout.hold_reason,
                payout.provider_reference,
                payout.created_at.isoformat(),
            ])
        return response
    if export == "pdf":
        profiles = {
            merchant.owner_id: merchant for merchant in Merchant.objects.filter(
                owner_id__in=all_payouts.values_list("merchant_id", flat=True)
            ).select_related("owner")
        }
        export_payouts = list(all_payouts[:1000])
        rows = []
        for payout in export_payouts:
            merchant_profile = profiles.get(payout.merchant_id)
            rows.append([
                payout.pk,
                payout.merchant.get_full_name() or payout.merchant.username,
                merchant_profile.compliance_label if merchant_profile else "Merchant profile missing",
                payout.get_status_display(),
                payout.total_payable,
                payout.get_payout_method_display(),
                payout.hold_reason,
                payout.created_at.strftime("%Y-%m-%d"),
            ])
        return export_pdf_response(
            "Merchant Payout Control",
            ["ID", "Merchant", "Compliance", "Status", "Payable", "Method", "Control note", "Created"],
            rows,
            filename=dated_filename("merchant-payout-control", "pdf"),
            filters=[("Status", status_filter), ("Merchant", merchant_filter)],
            summary=[("Records", len(export_payouts))],
            landscape=True,
        )

    # Totals on full (possibly filtered) queryset — NEVER filter on a sliced queryset
    pending_total = MerchantContractPayout.objects.filter(
        status=MerchantContractPayout.STATUS_PENDING
    ).aggregate(t=Sum("total_payable"))["t"] or Decimal("0")

    paid_total = MerchantContractPayout.objects.filter(
        status=MerchantContractPayout.STATUS_PAID
    ).aggregate(t=Sum("total_payable"))["t"] or Decimal("0")

    commission_total = MerchantContractPayout.objects.aggregate(
        t=Sum("merchant_commission_amount")
    )["t"] or Decimal("0")

    processing_total = MerchantContractPayout.objects.filter(
        status=MerchantContractPayout.STATUS_PROCESSING
    ).aggregate(t=Sum("total_payable"))["t"] or Decimal("0")

    approved_total = MerchantContractPayout.objects.filter(
        status=MerchantContractPayout.STATUS_APPROVED
    ).aggregate(t=Sum("total_payable"))["t"] or Decimal("0")
    held_total = MerchantContractPayout.objects.filter(
        status__in=[
            MerchantContractPayout.STATUS_HELD,
            MerchantContractPayout.STATUS_DISPUTED,
            MerchantContractPayout.STATUS_BLOCKED,
        ]
    ).aggregate(t=Sum("total_payable"))["t"] or Decimal("0")
    compliance_missing_count = sum(
        1 for merchant in Merchant.objects.all()
        if not merchant.is_compliance_complete
    )

    # Slice only for display
    payouts = list(all_payouts[:100])
    profiles = {
        merchant.owner_id: merchant for merchant in Merchant.objects.filter(
            owner_id__in=[payout.merchant_id for payout in payouts]
        ).select_related("owner")
    }
    payout_rows = []
    for payout in payouts:
        merchant_profile = profiles.get(payout.merchant_id)
        payout_rows.append({
            "payout": payout,
            "merchant_profile": merchant_profile,
            "compliance_label": merchant_profile.compliance_label if merchant_profile else "Merchant profile missing",
            "is_compliant": bool(merchant_profile and merchant_profile.is_compliance_complete),
            "block_reason": merchant_profile.payout_block_reason if merchant_profile else "Blocked: merchant profile missing",
        })

    return render(request, "dashboard/hq_merchant_payouts.html", {
        "payouts": payouts,
        "payout_rows": payout_rows,
        "pending_total": pending_total,
        "approved_total": approved_total,
        "paid_total": paid_total,
        "commission_total": commission_total,
        "processing_total": processing_total,
        "held_total": held_total,
        "compliance_missing_count": compliance_missing_count,
        "status_choices": MerchantContractPayout.STATUS_CHOICES,
        "status_filter": status_filter,
        "merchant_filter": merchant_filter,
        "msg": msg,
        "msg_type": msg_type,
    })


@hq_required
def hq_reports(request):
    import csv
    from django.http import HttpResponse
    from django.utils import timezone
    from portal.models import PaymentContract, PaymentTransaction

    report_type = request.GET.get("type", "")

    if report_type == "portfolio_csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="portfolio_report.csv"'
        writer = csv.writer(response)
        writer.writerow([
            "Contract Number", "PayG Number", "Customer Name",
            "Total Amount", "Amount Paid", "Remaining", "Status",
            "Start Date", "Due Date",
        ])
        for c in PaymentContract.objects.all().order_by("-created_at"):
            writer.writerow([
                c.contract_number, c.payg_number, c.customer_name,
                c.total_amount, c.amount_paid, c.remaining_amount,
                c.status, c.start_date, c.due_date,
            ])
        return response

    if report_type == "applications_csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="applications_report.csv"'
        writer = csv.writer(response)
        writer.writerow([
            "Application Number", "Customer Name", "Status",
            "Created By", "Created At", "Claimed By", "Reviewed At",
        ])
        for a in FinancingApplication.objects.all().order_by("-created_at"):
            writer.writerow([
                a.application_number, a.customer_name, a.status,
                a.created_by.username if a.created_by_id else "",
                a.created_at.date(),
                a.claimed_by.username if a.claimed_by_id else "",
                a.reviewed_at.date() if a.reviewed_at else "",
            ])
        return response

    if report_type == "collections_csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="collections_report.csv"'
        writer = csv.writer(response)
        writer.writerow([
            "Contract", "Customer", "Provider", "Amount",
            "Commissionable", "Status", "Paid At",
        ])
        for t in PaymentTransaction.objects.filter(status="paid").order_by("-paid_at"):
            writer.writerow([
                t.payment_contract.contract_number,
                t.payment_contract.customer_name,
                t.provider, t.amount, t.commissionable_amount,
                t.status, t.paid_at,
            ])
        return response

    if report_type == "commissions_csv":
        from commissions.models import CommissionLedger
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="commissions_report.csv"'
        writer = csv.writer(response)
        writer.writerow([
            "User", "Entry Type", "Amount", "Base Amount",
            "Rate", "Contract", "Description", "Created At",
        ])
        for entry in CommissionLedger.objects.select_related("user", "contract").order_by("-created_at"):
            writer.writerow([
                entry.user.username,
                entry.entry_type, entry.amount, entry.base_amount,
                entry.rate,
                entry.contract.contract_number if entry.contract_id else "",
                entry.description, entry.created_at.date(),
            ])
        return response

    if report_type == "merchant_payouts_csv":
        from commissions.models import MerchantContractPayout
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="merchant_payouts_report.csv"'
        writer = csv.writer(response)
        writer.writerow([
            "Merchant", "Contract", "Cash Price", "Financed Amount",
            "Commission", "Total Payable", "Status", "Created At",
        ])
        for p in MerchantContractPayout.objects.select_related("merchant", "contract").order_by("-created_at"):
            writer.writerow([
                p.merchant.username,
                p.contract.contract_number if p.contract_id else "",
                p.cash_price, p.financed_amount, p.merchant_commission_amount,
                p.total_payable, p.status, p.created_at.date(),
            ])
        return response

    from portal.models import PaymentContract, PaymentTransaction
    from decimal import Decimal

    total_portfolio = PaymentContract.objects.aggregate(t=Sum("total_amount"))["t"] or Decimal("0")
    total_paid = PaymentContract.objects.aggregate(t=Sum("amount_paid"))["t"] or Decimal("0")
    active_contracts = PaymentContract.objects.filter(status="active").count()
    overdue_amount = PaymentContract.objects.filter(
        status__in=["overdue", "locked"]
    ).aggregate(t=Sum("total_amount"))["t"] or Decimal("0")

    return render(request, "dashboard/hq_reports.html", {
        "title": "Reports",
        "total_portfolio": total_portfolio,
        "total_paid": total_paid,
        "active_contracts": active_contracts,
        "overdue_amount": overdue_amount,
    })


@hq_required
def hq_simulations(request):
    """TengaSale Financing Simulator — investor-grade phone financing projections."""
    result = None
    form_data = {}

    if request.method == "POST":
        from decimal import Decimal

        def _d(key, default):
            v = request.POST.get(key, str(default)) or str(default)
            try:
                return Decimal(v)
            except Exception:
                return Decimal(str(default))

        def _i(key, default):
            v = request.POST.get(key, str(default)) or str(default)
            try:
                return max(1, int(v))
            except Exception:
                return default

        try:
            D = Decimal

            # ── Inputs ──────────────────────────────────────────────────────
            num_devices        = _i("num_devices", 100)
            cash_price         = _d("cash_price", 400000)
            contract_value     = _d("contract_value", 1000000)
            deposit_pct        = _d("deposit_pct", 20) / D("100")
            term_months        = _i("term_months", 12)
            collection_rate    = _d("collection_rate", 90) / D("100")
            default_rate       = _d("default_rate", 5) / D("100")
            lock_recovery_rate = _d("lock_recovery_rate", 60) / D("100")
            merchant_rate      = _d("merchant_commission_rate", 1) / D("100")
            uw_rate            = _d("uw_commission_rate", 7) / D("100")
            arrears_penalty    = _d("arrears_penalty_rate", 14) / D("100")
            wht_rate           = _d("wht_rate", 20) / D("100")
            monthly_tech_cost  = _d("monthly_tech_cost", 0)
            lock_cost_per_dev  = _d("lock_cost_per_device", 0)
            sms_cost_per_cust  = _d("sms_cost_per_customer", 0)

            # ── Customer impact ─────────────────────────────────────────────
            people_connected = num_devices
            deposit_per      = contract_value * deposit_pct
            financed_per     = contract_value - deposit_per
            monthly_repayment = financed_per / D(str(term_months))
            daily_repayment  = financed_per / D(str(term_months * 30))
            total_deposits   = deposit_per * D(str(num_devices))
            total_financed   = financed_per * D(str(num_devices))

            # ── Merchant (commission applies to financed amount, not contract value) ──
            merchant_comm_per      = financed_per * merchant_rate
            merchant_payout_per    = cash_price + merchant_comm_per
            total_merchant_payouts = merchant_payout_per * D(str(num_devices))

            # ── Expected collections (collection_rate on financed, per spec) ─
            expected_collections_per = financed_per * collection_rate
            expected_total_col       = expected_collections_per * D(str(num_devices))
            expected_monthly_col     = expected_total_col / D(str(term_months))
            total_inflows            = total_deposits + expected_total_col
            gross_sales_value        = contract_value * D(str(num_devices))

            # ── Underwriter (commission on expected collections, deposit excluded) ──
            uw_gross_per          = expected_collections_per * uw_rate
            default_exposure_per  = financed_per * default_rate
            arrears_deduction_per = default_exposure_per * arrears_penalty
            uw_after_ded_per      = max(D("0"), uw_gross_per - arrears_deduction_per)
            uw_wht_per            = uw_after_ded_per * wht_rate
            uw_net_per            = uw_after_ded_per - uw_wht_per

            total_uw_gross    = uw_gross_per * D(str(num_devices))
            total_uw_wht      = uw_wht_per * D(str(num_devices))
            total_uw_net      = uw_net_per * D(str(num_devices))
            total_arrears_ded = arrears_deduction_per * D(str(num_devices))

            # ── Operating costs ──────────────────────────────────────────────
            total_tech_cost = monthly_tech_cost * D(str(term_months))
            total_lock_cost = lock_cost_per_dev * D(str(num_devices)) * D(str(term_months))
            total_sms_cost  = sms_cost_per_cust * D(str(num_devices)) * D(str(term_months))
            total_op_costs  = total_tech_cost + total_lock_cost + total_sms_cost

            # ── Risk ─────────────────────────────────────────────────────────
            default_exposure     = default_exposure_per * D(str(num_devices))
            lock_recovery        = default_exposure * lock_recovery_rate
            unrecovered_exposure = default_exposure - lock_recovery
            portfolio_at_risk    = round(float(default_rate) * 100, 1)
            total_outflows       = total_merchant_payouts + total_uw_net + total_op_costs
            break_even_col_rate  = (
                float(total_outflows / total_financed * 100) if total_financed > 0 else 0
            )

            # ── Profit (spec formula: deposits + collections + lock_recovery − costs) ──
            estimated_profit   = (
                total_deposits + expected_total_col + lock_recovery
                - total_merchant_payouts - total_uw_net - total_op_costs
            )
            expected_cash_flow = total_inflows - total_merchant_payouts - total_uw_net - total_op_costs

            # ── Scenario comparison ──────────────────────────────────────────
            def _scenario(col_rate, def_rate_pct, label, badge, insight):
                col    = D(str(col_rate)) / D("100")
                dflt   = D(str(def_rate_pct)) / D("100")
                exp_c  = financed_per * col * D(str(num_devices))
                uw_g   = exp_c * uw_rate
                def_e  = financed_per * dflt * D(str(num_devices))
                arr_d  = def_e * arrears_penalty
                uw_d   = max(D("0"), uw_g - arr_d)
                uw_n   = uw_d - uw_d * wht_rate
                lk_r   = def_e * lock_recovery_rate
                profit = total_deposits + exp_c + lk_r - total_merchant_payouts - uw_n - total_op_costs
                return {
                    "label": label,
                    "badge": badge,
                    "col_rate": col_rate,
                    "def_rate": def_rate_pct,
                    "insight": insight,
                    "expected_collections": round(exp_c, 0),
                    "estimated_profit": round(profit, 0),
                    "profitable": profit > 0,
                }

            scenarios = [
                _scenario(75, 15, "Conservative", "warning",
                          "Still viable if lock recovery remains above 60%."),
                _scenario(90, 5, "Base Case", "primary",
                          "Expected operating outcome for a healthy portfolio."),
                _scenario(95, 3, "Aggressive", "success",
                          "Exceptional performance — requires strong underwriting."),
                _scenario(60, 30, "High Default Stress", "danger",
                          "Loss scenario. Lock recovery critical to limit exposure."),
            ]

            # ── Narrative ────────────────────────────────────────────────────
            narrative = (
                f"If TengaSale finances {num_devices:,} devices at MWK {int(contract_value):,} "
                f"contract value with {int(deposit_pct * 100)}% deposits, approximately "
                f"{people_connected:,} people gain access to smartphones. The platform would "
                f"collect MWK {int(total_deposits):,} in deposits, manage a "
                f"MWK {int(total_financed):,} financed portfolio, and expect "
                f"MWK {int(expected_total_col):,} in customer repayments at a "
                f"{int(collection_rate * 100)}% collection rate. Estimated profit over "
                f"{term_months} months is MWK {int(estimated_profit):,}."
            )

            # ── Recommendation ───────────────────────────────────────────────
            if estimated_profit > 0 and float(default_rate) <= 0.05:
                recommendation = "Safe"
                rec_color = "#16a34a"
                rec_bg = "rgba(22,163,74,0.10)"
            elif estimated_profit > 0 and float(default_rate) <= 0.15:
                recommendation = "Watch"
                rec_color = "#d97706"
                rec_bg = "rgba(217,119,6,0.10)"
            else:
                recommendation = "Danger"
                rec_color = "#dc2626"
                rec_bg = "rgba(220,38,38,0.10)"

            # ── Sensitivity analysis ─────────────────────────────────────────
            # Helper: recalculate profit with a single parameter change
            def _profit_at(col_r=collection_rate, def_r=default_rate,
                           dep_p=deposit_pct, lk_r=lock_recovery_rate,
                           m_r=merchant_rate):
                _dep_per   = contract_value * dep_p
                _fin_per   = contract_value - _dep_per
                _tot_dep   = _dep_per * D(str(num_devices))
                _tot_fin   = _fin_per * D(str(num_devices))
                _exp_col   = _fin_per * col_r * D(str(num_devices))
                _uw_g      = _exp_col * uw_rate
                _def_e     = _fin_per * def_r * D(str(num_devices))
                _arr_d     = _def_e * arrears_penalty
                _uw_d      = max(D("0"), _uw_g - _arr_d)
                _uw_n      = _uw_d - _uw_d * wht_rate
                _lk_r      = _def_e * lk_r
                _merch_pay = (cash_price + _fin_per * m_r) * D(str(num_devices))
                return _tot_dep + _exp_col + _lk_r - _merch_pay - _uw_n - total_op_costs

            _base = _profit_at()
            _sensitivity_items = []

            # +1% collection rate
            _delta = _profit_at(col_r=collection_rate + D("0.01")) - _base
            _sensitivity_items.append({
                "label": "+1% Collection Rate",
                "delta": round(_delta, 0),
                "positive": _delta >= 0,
                "description": "Each additional 1% in collection rate",
            })
            # -1% default rate
            _delta = _profit_at(def_r=max(D("0"), default_rate - D("0.01"))) - _base
            _sensitivity_items.append({
                "label": "-1% Default Rate",
                "delta": round(_delta, 0),
                "positive": _delta >= 0,
                "description": "Each 1% reduction in default rate",
            })
            # +5% deposit
            _delta = _profit_at(dep_p=min(D("0.80"), deposit_pct + D("0.05"))) - _base
            _sensitivity_items.append({
                "label": "+5% Deposit",
                "delta": round(_delta, 0),
                "positive": _delta >= 0,
                "description": "Each 5% increase in deposit percentage",
            })
            # +10% lock recovery
            _delta = _profit_at(lk_r=min(D("1.0"), lock_recovery_rate + D("0.10"))) - _base
            _sensitivity_items.append({
                "label": "+10% Lock Recovery",
                "delta": round(_delta, 0),
                "positive": _delta >= 0,
                "description": "Each 10% improvement in lock recovery rate",
            })
            # -1% merchant commission
            _delta = _profit_at(m_r=max(D("0"), merchant_rate - D("0.01"))) - _base
            _sensitivity_items.append({
                "label": "-1% Merchant Commission",
                "delta": round(_delta, 0),
                "positive": _delta >= 0,
                "description": "Each 1% reduction in merchant commission rate",
            })

            # Capital efficiency metrics
            total_capital_required   = total_merchant_payouts  # capital out upfront
            deposit_leverage         = round(float(total_deposits / total_capital_required * 100), 1) if total_capital_required > 0 else 0
            revenue_yield            = round(float(total_inflows / total_capital_required * 100), 1) if total_capital_required > 0 else 0
            gross_margin_pct         = round(float(estimated_profit / total_inflows * 100), 1) if total_inflows > 0 else 0
            net_margin_pct           = round(float(estimated_profit / gross_sales_value * 100), 1) if gross_sales_value > 0 else 0
            return_on_portfolio      = round(float(estimated_profit / total_financed * 100), 1) if total_financed > 0 else 0

            # Operating cost breakdown total
            total_op_cost_display = round(total_op_costs, 0)

            # Profit/loss indicator
            profit_positive = estimated_profit >= 0

            # ── Cashflow waterfall bars ───────────────────────────────────────
            _wf_rows = [
                ("Deposits Collected",    round(total_deposits, 0),         "#16a34a", False),
                ("Customer Repayments",   round(expected_total_col, 0),     "#2563eb", False),
                ("Lock Recovery",         round(lock_recovery, 0),          "#0891b2", False),
                ("Merchant Payouts",      round(total_merchant_payouts, 0), "#dc2626", True),
                ("Underwriter Payout",    round(total_uw_net, 0),           "#d97706", True),
                ("Operating Costs",       round(total_op_costs, 0),         "#7c3aed", True),
                ("Estimated Margin",      round(abs(estimated_profit), 0),
                 "#16a34a" if estimated_profit >= 0 else "#dc2626",
                 estimated_profit < 0),
            ]
            _wf_max = max(float(v) for _, v, _, _ in _wf_rows) or 1
            waterfall_items = [
                {
                    "label": lbl,
                    "value": v,
                    "width_pct": round(float(v) / _wf_max * 100, 1),
                    "color": color,
                    "is_outflow": is_out,
                }
                for lbl, v, color, is_out in _wf_rows
            ]

            # ── Risk exposure bars ────────────────────────────────────────────
            _risk_max = float(default_exposure) if float(default_exposure) > 0 else 1
            risk_items = [
                {
                    "label": "Default Exposure",
                    "value": round(default_exposure, 0),
                    "width_pct": 100.0,
                    "color": "#dc2626",
                },
                {
                    "label": "Lock Recovery",
                    "value": round(lock_recovery, 0),
                    "width_pct": round(float(lock_recovery) / _risk_max * 100, 1),
                    "color": "#16a34a",
                },
                {
                    "label": "Unrecovered Amount",
                    "value": round(unrecovered_exposure, 0),
                    "width_pct": round(float(unrecovered_exposure) / _risk_max * 100, 1),
                    "color": "#d97706",
                },
            ]

            result = {
                # Inputs
                "num_devices": num_devices,
                "cash_price": cash_price,
                "contract_value": contract_value,
                "deposit_pct_display": int(deposit_pct * 100),
                "term_months": term_months,
                "collection_rate_display": int(collection_rate * 100),
                "default_rate_display": round(float(default_rate) * 100, 1),
                # Customer impact
                "people_connected": people_connected,
                "deposit_per": round(deposit_per, 0),
                "financed_per": round(financed_per, 0),
                "monthly_repayment": round(monthly_repayment, 0),
                "daily_repayment": round(daily_repayment, 0),
                "total_deposits": round(total_deposits, 0),
                "total_financed": round(total_financed, 0),
                # Company economics
                "gross_sales_value": round(gross_sales_value, 0),
                "expected_monthly_collections": round(expected_monthly_col, 0),
                "expected_total_collections": round(expected_total_col, 0),
                "merchant_payout_per": round(merchant_payout_per, 0),
                "total_merchant_payouts": round(total_merchant_payouts, 0),
                "merchant_comm_per": round(merchant_comm_per, 0),
                "total_uw_gross": round(total_uw_gross, 0),
                "total_uw_wht": round(total_uw_wht, 0),
                "total_uw_net": round(total_uw_net, 0),
                "total_arrears_ded": round(total_arrears_ded, 0),
                "expected_cash_flow": round(expected_cash_flow, 0),
                "estimated_profit": round(estimated_profit, 0),
                "total_inflows": round(total_inflows, 0),
                "total_outflows": round(total_outflows, 0),
                # Risk
                "default_exposure": round(default_exposure, 0),
                "lock_recovery": round(lock_recovery, 0),
                "unrecovered_exposure": round(unrecovered_exposure, 0),
                "portfolio_at_risk": portfolio_at_risk,
                "break_even_col_rate": round(break_even_col_rate, 1),
                # Charts
                "waterfall_items": waterfall_items,
                "risk_items": risk_items,
                # Scenarios
                "scenarios": scenarios,
                # Narrative
                "narrative": narrative,
                # Recommendation
                "recommendation": recommendation,
                "rec_color": rec_color,
                "rec_bg": rec_bg,
                # Sensitivity analysis
                "sensitivity_items": _sensitivity_items,
                # Capital efficiency
                "total_capital_required": round(total_capital_required, 0),
                "deposit_leverage": deposit_leverage,
                "revenue_yield": revenue_yield,
                "gross_margin_pct": gross_margin_pct,
                "net_margin_pct": net_margin_pct,
                "return_on_portfolio": return_on_portfolio,
                # Operating cost breakdown
                "total_tech_cost": round(total_tech_cost, 0),
                "total_lock_cost": round(total_lock_cost, 0),
                "total_sms_cost": round(total_sms_cost, 0),
                "total_op_cost_display": total_op_cost_display,
                "profit_positive": profit_positive,
                # Aliases for test compatibility
                "deposit_amount": round(deposit_per, 0),
                "financed_amount": round(financed_per, 0),
                "uw_gross_commission": round(total_uw_gross, 0),
                "uw_wht": round(total_uw_wht, 0),
                "merchant_payout": round(total_merchant_payouts, 0),
            }
            form_data = request.POST
        except Exception:
            result = None
            form_data = request.POST

    return render(request, "dashboard/hq_simulations.html", {
        "result": result,
        "form_data": form_data,
        "deposit_options": ["15", "20", "30"],
        "term_options": [3, 6, 12],
    })


@hq_required
def hq_operations(request):
    """
    Safe audited HQ operations.
    All actions are permission-checked, logged, and require confirmation.
    No dangerous one-click irreversible operations.
    """
    from core.models import AuditLog

    action = request.POST.get("action") if request.method == "POST" else None
    result_message = None
    result_type = "info"

    if action == "reassign_application":
        app_id = request.POST.get("app_id")
        new_underwriter_id = request.POST.get("underwriter_id")
        try:
            app = FinancingApplication.objects.get(pk=app_id)
            User = get_user_model()
            new_uw = User.objects.get(pk=new_underwriter_id, profile__role="underwriter")
            old_uw = app.claimed_by
            app.claimed_by = new_uw
            app.save(update_fields=["claimed_by"])
            AuditLog.objects.create(
                user=request.user,
                action="hq_reassign_application",
                object_type="FinancingApplication",
                object_id=str(app.pk),
                detail={"from": str(old_uw), "to": str(new_uw), "app": app.application_number},
            )
            result_message = f"Application {app.application_number} reassigned to {new_uw.get_full_name() or new_uw.username}."
            result_type = "success"
        except Exception as exc:
            result_message = f"Reassign failed: {exc}"
            result_type = "error"

    elif action == "release_stuck_claim":
        app_id = request.POST.get("app_id")
        try:
            app = FinancingApplication.objects.get(pk=app_id)
            old_uw = app.claimed_by
            app.claimed_by = None
            app.claimed_at = None
            app.status = "pending_review"
            app.save(update_fields=["claimed_by", "claimed_at", "status"])
            AuditLog.objects.create(
                user=request.user,
                action="hq_release_stuck_claim",
                object_type="FinancingApplication",
                object_id=str(app.pk),
                detail={"released_from": str(old_uw), "app": app.application_number},
            )
            result_message = f"Claim released. Application {app.application_number} is back in the queue."
            result_type = "success"
        except Exception as exc:
            result_message = f"Release failed: {exc}"
            result_type = "error"

    elif action == "mark_payment_manual_review":
        txn_id = request.POST.get("transaction_id")
        try:
            from portal.models import PaymentTransaction
            txn = PaymentTransaction.objects.get(pk=txn_id)
            txn.status = PaymentTransaction.STATUS_PENDING
            txn.save(update_fields=["status"])
            AuditLog.objects.create(
                user=request.user,
                action="hq_mark_payment_manual_review",
                object_type="PaymentTransaction",
                object_id=str(txn.pk),
                detail={"contract": str(txn.payment_contract_id), "amount": str(txn.amount)},
            )
            result_message = f"Transaction {txn.internal_reference} marked for manual review."
            result_type = "success"
        except Exception as exc:
            result_message = f"Mark for review failed: {exc}"
            result_type = "error"

    elif action == "resend_correction_link":
        app_id = request.POST.get("app_id")
        try:
            from applications.models import ApplicationCorrectionToken
            app = FinancingApplication.objects.get(pk=app_id)
            token_obj = ApplicationCorrectionToken.create_or_refresh(app)
            AuditLog.objects.create(
                user=request.user,
                action="hq_resend_correction_link",
                object_type="FinancingApplication",
                object_id=str(app.pk),
                detail={"app": app.application_number, "token": token_obj.token[:8] + "..."},
            )
            result_message = f"Correction token refreshed for {app.application_number}. Token: {token_obj.token[:12]}..."
            result_type = "success"
        except Exception as exc:
            result_message = f"Resend failed: {exc}"
            result_type = "error"

    User = get_user_model()
    underwriters = User.objects.filter(profile__role="underwriter").order_by("username")
    stuck_apps = FinancingApplication.objects.filter(
        status="under_review", claimed_by__isnull=False
    ).select_related("claimed_by").order_by("-claimed_at")[:20]

    from portal.models import PaymentTransaction
    pending_transactions = PaymentTransaction.objects.filter(
        status__in=["pending", "processing"]
    ).select_related("payment_contract").order_by("-created_at")[:20]

    audit_recent = AuditLog.objects.filter(
        action__startswith="hq_"
    ).select_related("user").order_by("-timestamp")[:20]

    return render(request, "dashboard/hq_operations.html", {
        "title": "Safe Operations",
        "underwriters": underwriters,
        "stuck_apps": stuck_apps,
        "pending_transactions": pending_transactions,
        "audit_recent": audit_recent,
        "result_message": result_message,
        "result_type": result_type,
    })


@hq_required
def hq_commission_ledger(request):
    """Commission ledger — alias for hq_commissions with same content."""
    return hq_commissions(request)


@hq_required
def hq_safe_operations(request):
    """Safe operations center — alias for hq_operations."""
    return hq_operations(request)


@hq_required
def hq_devices(request):
    """Device enrollment management page."""
    from portal.models import PaymentContract
    from core.models import AuditLog

    msg = None
    msg_type = "info"

    if request.method == "POST":
        action = request.POST.get("action", "")
        contract_id = request.POST.get("contract_id")
        try:
            contract = PaymentContract.objects.get(pk=contract_id)
            if action == "enroll_device":
                contract.device_enrollment_status = PaymentContract.ENROLLMENT_PENDING
                contract.save(update_fields=["device_enrollment_status", "updated_at"])
                AuditLog.objects.create(
                    user=request.user,
                    action="hq_device_enroll",
                    object_type="PaymentContract",
                    object_id=str(contract.pk),
                    detail={"contract": contract.contract_number},
                )
                msg = f"Enrollment initiated for {contract.contract_number}."
                msg_type = "success"
            elif action == "lock_device":
                AuditLog.objects.create(
                    user=request.user,
                    action="hq_device_lock_request_blocked",
                    object_type="PaymentContract",
                    object_id=str(contract.pk),
                    detail={"contract": contract.contract_number, "reason": "No connected lock provider command configured"},
                )
                msg = f"Lock request for {contract.contract_number} was not sent. Configure a lock provider command before changing device state."
                msg_type = "error"
            elif action == "unlock_device":
                AuditLog.objects.create(
                    user=request.user,
                    action="hq_device_unlock_request_blocked",
                    object_type="PaymentContract",
                    object_id=str(contract.pk),
                    detail={"contract": contract.contract_number, "reason": "No connected lock provider command configured"},
                )
                msg = f"Unlock request for {contract.contract_number} was not sent. Configure a lock provider command before changing device state."
                msg_type = "error"
            elif action == "release_device":
                contract.device_enrollment_status = PaymentContract.ENROLLMENT_NONE
                contract.device_lock_status = PaymentContract.LOCK_STATUS_UNLOCKED
                contract.save(update_fields=["device_enrollment_status", "device_lock_status", "updated_at"])
                AuditLog.objects.create(
                    user=request.user,
                    action="hq_device_release",
                    object_type="PaymentContract",
                    object_id=str(contract.pk),
                    detail={"contract": contract.contract_number},
                )
                msg = f"Device released for {contract.contract_number}."
                msg_type = "success"
        except PaymentContract.DoesNotExist:
            msg = "Contract not found."
            msg_type = "error"
        except Exception as exc:
            msg = f"Error: {exc}"
            msg_type = "error"

    # Filters
    enroll_filter = request.GET.get("enrollment", "")
    lock_filter = request.GET.get("lock", "")
    search = request.GET.get("search", "").strip()
    provider_filter = request.GET.get("provider", "")

    devices_qs = PaymentContract.objects.order_by("-created_at")
    if enroll_filter:
        devices_qs = devices_qs.filter(device_enrollment_status=enroll_filter)
    if lock_filter:
        devices_qs = devices_qs.filter(device_lock_status=lock_filter)
    if provider_filter:
        devices_qs = devices_qs.filter(device_lock_provider=provider_filter)
    if search:
        devices_qs = devices_qs.filter(
            Q(contract_number__icontains=search)
            | Q(payg_number__icontains=search)
            | Q(customer_name__icontains=search)
            | Q(customer_phone__icontains=search)
            | Q(device_model__icontains=search)
            | Q(imei_number__icontains=search)
            | Q(source_application__contract__contract_number__icontains=search)
        )

    export = request.GET.get("export", "")
    if export in {"csv", "pdf"}:
        export_devices = list(devices_qs[:1000])
        if export == "csv":
            response = HttpResponse(content_type="text/csv")
            response["Content-Disposition"] = f'attachment; filename="{dated_filename("devices", "csv")}"'
            writer = csv.writer(response)
            writer.writerow([
                "Contract", "Customer", "Phone", "Device", "IMEI",
                "Enrollment", "Lock Status", "Provider", "Last Sync", "Last Error",
            ])
            for device in export_devices:
                writer.writerow([
                    device.contract_number,
                    device.customer_name,
                    device.customer_phone,
                    device.device_model,
                    device.imei_number,
                    device.device_enrollment_status,
                    device.device_lock_status,
                    device.device_lock_provider,
                    device.last_lock_sync_at.isoformat() if device.last_lock_sync_at else "",
                    device.last_lock_error,
                ])
            return response
        rows = [
            [
                device.contract_number,
                device.customer_name,
                device.device_model,
                device.imei_number,
                device.device_enrollment_status,
                device.device_lock_status,
                device.device_lock_provider,
                device.last_lock_sync_at.strftime("%Y-%m-%d %H:%M") if device.last_lock_sync_at else "Never",
            ]
            for device in export_devices
        ]
        return export_pdf_response(
            "Devices and Locking Register",
            ["Contract", "Customer", "Device", "IMEI", "Enrollment", "Lock", "Provider", "Last sync"],
            rows,
            filename=dated_filename("devices-locking", "pdf"),
            filters=[("Enrollment", enroll_filter), ("Lock", lock_filter), ("Provider", provider_filter), ("Search", search)],
            summary=[("Records", len(export_devices))],
            landscape=True,
        )

    # Summary counts
    total_enrolled = PaymentContract.objects.filter(
        device_enrollment_status=PaymentContract.ENROLLMENT_ENROLLED
    ).count()
    total_pending = PaymentContract.objects.filter(
        device_enrollment_status=PaymentContract.ENROLLMENT_PENDING
    ).count()
    total_failed = PaymentContract.objects.filter(
        device_enrollment_status=PaymentContract.ENROLLMENT_FAILED
    ).count()
    total_locked = PaymentContract.objects.filter(
        device_lock_status=PaymentContract.LOCK_STATUS_LOCKED
    ).count()

    return render(request, "dashboard/hq_devices.html", {
        "devices": devices_qs[:100],
        "enroll_filter": enroll_filter,
        "lock_filter": lock_filter,
        "search": search,
        "provider_filter": provider_filter,
        "total_enrolled": total_enrolled,
        "total_pending": total_pending,
        "total_failed": total_failed,
        "total_locked": total_locked,
        "msg": msg,
        "msg_type": msg_type,
    })


@hq_required
def hq_repossession_resale(request):
    """HQ recovery operations for repossession, resale, and recovery costs."""
    from decimal import Decimal, InvalidOperation

    from core.models import AuditLog
    from portal.models import PaymentContract, RecoveryCost

    msg = None
    msg_type = "info"

    if request.method == "POST":
        action = request.POST.get("action", "")
        contract_id = request.POST.get("contract_id")
        try:
            contract = PaymentContract.objects.get(pk=contract_id)
            previous_status = contract.status
            audit_detail = {"contract": contract.contract_number, "previous_status": previous_status}

            if action == "mark_repossession_pending":
                contract.status = PaymentContract.STATUS_REPOSSESSION_PENDING
                msg = f"Repossession pending for {contract.contract_number}."
            elif action == "cancel_repossession":
                contract.status = PaymentContract.STATUS_ACTIVE
                msg = f"Repossession cancelled for {contract.contract_number}."
            elif action == "mark_repossessed":
                contract.status = PaymentContract.STATUS_REPOSSESSED
                msg = f"Device marked repossessed for {contract.contract_number}."
            elif action == "mark_ready_for_resale":
                contract.status = PaymentContract.STATUS_READY_FOR_RESALE
                msg = f"Device marked ready for resale for {contract.contract_number}."
            elif action == "record_resale":
                proceeds = Decimal(request.POST.get("resale_amount") or "0")
                contract.amount_paid = min(
                    contract.total_amount,
                    (contract.amount_paid or Decimal("0")) + max(proceeds, Decimal("0")),
                )
                contract.status = PaymentContract.STATUS_RESOLD
                audit_detail["resale_amount"] = str(proceeds)
                msg = f"Resale recorded for {contract.contract_number}."
            elif action == "write_off":
                contract.status = PaymentContract.STATUS_WRITTEN_OFF
                msg = f"Contract written off for {contract.contract_number}."
            elif action == "add_recovery_cost":
                amount = Decimal(request.POST.get("amount") or "0")
                if amount <= 0:
                    raise ValueError("Recovery cost amount must be greater than zero.")
                cost = RecoveryCost.objects.create(
                    contract=contract,
                    cost_type=request.POST.get("cost_type") or RecoveryCost.COST_OTHER,
                    amount=amount,
                    description=request.POST.get("description", "").strip(),
                    recorded_by=request.user,
                    is_chargeable_to_customer=request.POST.get("is_chargeable_to_customer") == "on",
                )
                audit_detail["recovery_cost_id"] = cost.pk
                audit_detail["amount"] = str(amount)
                AuditLog.objects.create(
                    user=request.user,
                    action="hq_recovery_cost_added",
                    object_type="RecoveryCost",
                    object_id=str(cost.pk),
                    detail=audit_detail,
                )
                messages.success(request, f"Recovery cost added for {contract.contract_number}.")
                return redirect("hq_repossession_resale")
            elif action == "approve_recovery_cost":
                cost = RecoveryCost.objects.get(pk=request.POST.get("cost_id"), contract=contract)
                cost.approved_by = request.user
                cost.approved_at = timezone.now()
                cost.save(update_fields=["approved_by", "approved_at", "updated_at"])
                audit_detail["recovery_cost_id"] = cost.pk
                AuditLog.objects.create(
                    user=request.user,
                    action="hq_recovery_cost_approved",
                    object_type="RecoveryCost",
                    object_id=str(cost.pk),
                    detail=audit_detail,
                )
                messages.success(request, f"Recovery cost approved for {contract.contract_number}.")
                return redirect("hq_repossession_resale")
            else:
                raise ValueError("Unknown recovery action.")

            contract.save(update_fields=["status", "amount_paid", "updated_at"])
            audit_detail["new_status"] = contract.status
            AuditLog.objects.create(
                user=request.user,
                action=f"hq_recovery_{action}",
                object_type="PaymentContract",
                object_id=str(contract.pk),
                detail=audit_detail,
            )
            msg_type = "success"
        except (PaymentContract.DoesNotExist, RecoveryCost.DoesNotExist):
            msg = "Recovery record not found."
            msg_type = "error"
        except (InvalidOperation, ValueError) as exc:
            msg = str(exc)
            msg_type = "error"

    contracts = PaymentContract.objects.order_by("-created_at")
    costs = RecoveryCost.objects.select_related("contract", "recorded_by", "approved_by")[:50]

    return render(request, "dashboard/hq_repossession_resale.html", {
        "msg": msg,
        "msg_type": msg_type,
        "cost_type_choices": RecoveryCost.COST_TYPE_CHOICES,
        "repossession_eligible": contracts.filter(status__in=[PaymentContract.STATUS_OVERDUE, PaymentContract.STATUS_LOCKED])[:25],
        "repossession_pending": contracts.filter(status=PaymentContract.STATUS_REPOSSESSION_PENDING)[:25],
        "repossessed_devices": contracts.filter(status=PaymentContract.STATUS_REPOSSESSED)[:25],
        "ready_for_resale": contracts.filter(status=PaymentContract.STATUS_READY_FOR_RESALE)[:25],
        "resold_devices": contracts.filter(status=PaymentContract.STATUS_RESOLD)[:25],
        "written_off": contracts.filter(status=PaymentContract.STATUS_WRITTEN_OFF)[:25],
        "recovery_costs": costs,
        "recovery_cost_total": RecoveryCost.objects.aggregate(t=Sum("amount"))["t"] or Decimal("0"),
        "approved_recovery_cost_total": (
            RecoveryCost.objects.filter(approved_at__isnull=False, is_chargeable_to_customer=True)
            .aggregate(t=Sum("amount"))["t"] or Decimal("0")
        ),
    })


@hq_required
def hq_portfolio(request):
    from portal.models import PaymentContract

    contracts = PaymentContract.objects.select_related("source_application", "source_application__created_by")
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    risk_filter = request.GET.get("risk", "").strip()
    par_filter = request.GET.get("par", "").strip()

    if status:
        contracts = contracts.filter(status=status)
    if query:
        contracts = contracts.filter(
            Q(contract_number__icontains=query)
            | Q(customer_name__icontains=query)
            | Q(customer_phone__icontains=query)
            | Q(imei_number__icontains=query)
            | Q(source_application__created_by__username__icontains=query)
            | Q(source_application__created_by__first_name__icontains=query)
            | Q(source_application__created_by__last_name__icontains=query)
        )

    rows = portfolio_rows(contracts)
    if risk_filter:
        rows = [row for row in rows if row["risk"].lower() == risk_filter.lower()]
    if par_filter:
        rows = [row for row in rows if row["par"].lower().replace(" ", "_").replace("+", "plus") == par_filter]

    page = Paginator(rows, 25).get_page(request.GET.get("page"))
    return render(request, "dashboard/hq_portfolio.html", {
        "kpis": calculate_portfolio_kpis(contracts),
        "chart_data": portfolio_chart_data(contracts),
        "rows": page,
        "query": query,
        "status": status,
        "risk_filter": risk_filter,
        "par_filter": par_filter,
        "status_choices": PaymentContract.STATUS_CHOICES,
    })


@hq_required
def hq_portfolio_investor_report(request):
    from portal.models import PaymentContract

    contracts = PaymentContract.objects.select_related("source_application", "source_application__created_by")
    rows = portfolio_rows(contracts)
    risk_counts = {
        "Low": sum(1 for row in rows if row["risk"] == "Low"),
        "Watch": sum(1 for row in rows if row["risk"] == "Watch"),
        "Medium": sum(1 for row in rows if row["risk"] == "Medium"),
        "High": sum(1 for row in rows if row["risk"] == "High"),
    }
    par_counts = {
        "current": sum(1 for row in rows if row["par"] == "Current"),
        "par_1": sum(1 for row in rows if row["par"] == "PAR 1+"),
        "par_7": sum(1 for row in rows if row["par"] == "PAR 7+"),
        "par_30": sum(1 for row in rows if row["par"] == "PAR 30+"),
    }
    return render(request, "dashboard/hq_portfolio_investor_report.html", {
        "as_of": timezone.localdate(),
        "kpis": calculate_portfolio_kpis(contracts),
        "chart_data": portfolio_chart_data(contracts),
        "rows": rows[:20],
        "risk_counts": risk_counts,
        "par_counts": par_counts,
        "export_mode": request.GET.get("export") == "pdf",
    })


@hq_required
def hq_portfolio_export_csv(request):
    rows = portfolio_rows()
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="tengasale-portfolio.csv"'
    writer = csv.writer(response)
    writer.writerow([
        "Contract Number", "Customer Name", "Customer Phone", "Merchant Name",
        "Underwriter", "Device Model", "IMEI", "Loan Amount", "Deposit",
        "Total Paid", "Remaining Balance", "Status", "Risk Rating", "PAR",
        "Recovery Status", "Created At", "Guarantor 1 Name", "Guarantor 1 Phone",
        "Guarantor 2 Name", "Guarantor 2 Phone", "Guarantor 3 Name", "Guarantor 3 Phone",
    ])
    for row in rows:
        contract = row["contract"]
        writer.writerow([
            contract.contract_number, row["customer"], row["phone"], row["merchant"],
            row["underwriter"], row["device"], row["imei"], row["loan"], row["deposit"],
            row["paid"], row["balance"], row["status"], row["risk"], row["par"],
            row["recovery"], row["created"], row["guarantor_1_name"], row["guarantor_1_phone"],
            row["guarantor_2_name"], row["guarantor_2_phone"], row["guarantor_3_name"],
            row["guarantor_3_phone"],
        ])
    return response


@hq_required
def hq_portfolio_export_excel(request):
    rows = portfolio_rows()
    response = HttpResponse(content_type="application/vnd.ms-excel")
    response["Content-Disposition"] = 'attachment; filename="tengasale-portfolio.xls"'
    writer = csv.writer(response, delimiter="\t")
    writer.writerow([
        "Contract Number", "Customer Name", "Customer Phone", "Merchant Name",
        "Underwriter", "Device Model", "IMEI", "Loan Amount", "Deposit",
        "Total Paid", "Remaining Balance", "Status", "Risk Rating", "PAR",
        "Recovery Status", "Created At", "Guarantor 1 Name", "Guarantor 1 Phone",
        "Guarantor 2 Name", "Guarantor 2 Phone", "Guarantor 3 Name", "Guarantor 3 Phone",
    ])
    for row in rows:
        contract = row["contract"]
        writer.writerow([
            contract.contract_number, row["customer"], row["phone"], row["merchant"],
            row["underwriter"], row["device"], row["imei"], row["loan"], row["deposit"],
            row["paid"], row["balance"], row["status"], row["risk"], row["par"],
            row["recovery"], row["created"], row["guarantor_1_name"], row["guarantor_1_phone"],
            row["guarantor_2_name"], row["guarantor_2_phone"], row["guarantor_3_name"],
            row["guarantor_3_phone"],
        ])
    return response


@hq_required
def hq_portfolio_export_pdf(request):
    return redirect(f"{reverse('hq_portfolio_investor_report')}?export=pdf")


@hq_required
def hq_staff_payouts(request):
    """Monthly underwriter/staff payout management."""
    from commissions.models import CommissionLedger, UnderwriterMonthlyPayout
    from decimal import Decimal
    from core.models import AuditLog

    msg = None
    msg_type = "info"

    User = get_user_model()
    underwriters = User.objects.filter(profile__role="underwriter").order_by("username")

    if request.method == "POST":
        action = request.POST.get("action", "")
        payout_id = request.POST.get("payout_id")

        if action == "generate_payout":
            uw_id = request.POST.get("uw_id")
            period_start_str = request.POST.get("period_start")
            period_end_str = request.POST.get("period_end")
            try:
                from datetime import date
                uw = User.objects.get(pk=uw_id)
                period_start = date.fromisoformat(period_start_str)
                period_end = date.fromisoformat(period_end_str)
                gross = CommissionLedger.objects.filter(
                    user=uw,
                    entry_type=CommissionLedger.ENTRY_REPAYMENT,
                    created_at__date__gte=period_start,
                    created_at__date__lte=period_end,
                ).aggregate(t=Sum("amount"))["t"] or Decimal("0")
                deductions = CommissionLedger.objects.filter(
                    user=uw,
                    entry_type=CommissionLedger.ENTRY_ARREARS,
                    created_at__date__gte=period_start,
                    created_at__date__lte=period_end,
                ).aggregate(t=Sum("amount"))["t"] or Decimal("0")
                net_gross = gross + deductions  # deductions are negative
                payout, created = UnderwriterMonthlyPayout.objects.get_or_create(
                    user=uw,
                    period_start=period_start,
                    period_end=period_end,
                    defaults={"gross_commission": net_gross},
                )
                if not created:
                    payout.gross_commission = net_gross
                    payout.save()
                AuditLog.objects.create(
                    user=request.user,
                    action="hq_staff_payout_generated",
                    object_type="UnderwriterMonthlyPayout",
                    object_id=str(payout.pk),
                    detail={"uw": uw.username, "gross": str(net_gross)},
                )
                msg = f"Payout {'created' if created else 'updated'} for {uw.username}: MWK {payout.net_amount:,.2f} net."
                msg_type = "success"
            except Exception as exc:
                msg = f"Error generating payout: {exc}"
                msg_type = "error"

        elif action == "mark_paid" and payout_id:
            try:
                payout = UnderwriterMonthlyPayout.objects.get(pk=payout_id)
                ref = request.POST.get("reference", "").strip()
                payout.mark_paid(reference=ref)
                AuditLog.objects.create(
                    user=request.user,
                    action="hq_staff_payout_paid",
                    object_type="UnderwriterMonthlyPayout",
                    object_id=str(payout.pk),
                    detail={"uw": payout.user.username, "reference": ref},
                )
                msg = f"Payout #{payout.pk} for {payout.user.username} marked as paid."
                msg_type = "success"
            except Exception as exc:
                msg = f"Error: {exc}"
                msg_type = "error"

    payouts = UnderwriterMonthlyPayout.objects.select_related("user").order_by("-period_end", "-created_at")[:100]

    total_pending_net = UnderwriterMonthlyPayout.objects.filter(
        status=UnderwriterMonthlyPayout.STATUS_PENDING
    ).aggregate(t=Sum("net_amount"))["t"] or Decimal("0")

    total_wht_withheld = UnderwriterMonthlyPayout.objects.filter(
        status=UnderwriterMonthlyPayout.STATUS_PAID
    ).aggregate(t=Sum("wht_amount"))["t"] or Decimal("0")

    today = timezone.now().date()
    default_period_start = today.replace(day=1).isoformat()
    default_period_end = today.isoformat()

    return render(request, "dashboard/hq_staff_payouts.html", {
        "payouts": payouts,
        "underwriters": underwriters,
        "total_pending_net": total_pending_net,
        "total_wht_withheld": total_wht_withheld,
        "default_period_start": default_period_start,
        "default_period_end": default_period_end,
        "msg": msg,
        "msg_type": msg_type,
    })


@hq_required
def hq_auto_approval(request):
    """Auto-approval engine management."""
    from applications.services.auto_approval import evaluate_auto_approval, auto_approve_application
    from core.models import AuditLog
    from decimal import Decimal

    msg = None
    msg_type = "info"

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "approve_selected":
            app_ids = request.POST.getlist("app_ids")
            approved_count = 0
            failed_count = 0
            for app_id in app_ids:
                try:
                    app = FinancingApplication.objects.get(pk=app_id)
                    result = evaluate_auto_approval(app)
                    if result["eligible"]:
                        auto_approve_application(app, approved_by=request.user)
                        approved_count += 1
                    else:
                        failed_count += 1
                except Exception:
                    failed_count += 1
            msg = f"Auto-approved {approved_count} application(s). {failed_count} blocked."
            msg_type = "success" if approved_count > 0 else "warning"

    # Evaluate pending applications
    pending_apps = FinancingApplication.objects.filter(
        status__in=["pending_review", "submitted", "resubmitted"],
        claimed_by__isnull=True,
    ).select_related("created_by", "deal").order_by("-submitted_at")[:50]

    eligibility_results = []
    for app in pending_apps:
        try:
            result = evaluate_auto_approval(app)
        except Exception as exc:
            result = {"eligible": False, "reasons": [str(exc)], "blocks": [str(exc)]}
        eligibility_results.append({"app": app, "result": result})

    eligible_count = sum(1 for r in eligibility_results if r["result"]["eligible"])
    blocked_count = len(eligibility_results) - eligible_count

    return render(request, "dashboard/hq_auto_approval.html", {
        "eligibility_results": eligibility_results,
        "eligible_count": eligible_count,
        "blocked_count": blocked_count,
        "msg": msg,
        "msg_type": msg_type,
    })


@hq_required
def hq_payment_collections(request):
    """Payment collections control page."""
    from portal.models import PaymentContract, PaymentTransaction

    today = timezone.now().date()
    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    collections_today = (
        PaymentTransaction.objects.filter(status="paid", paid_at__date=today)
        .aggregate(t=Sum("amount"))["t"] or Decimal("0")
    )
    collections_month = (
        PaymentTransaction.objects.filter(status="paid", paid_at__gte=month_start)
        .aggregate(t=Sum("amount"))["t"] or Decimal("0")
    )
    failed_payments = PaymentTransaction.objects.filter(
        status__in=["failed", "cancelled"]
    ).count()
    pending_payments = PaymentTransaction.objects.filter(
        status__in=["pending", "processing"]
    ).count()

    # Filters
    status_filter = request.GET.get("status", "")
    provider_filter = request.GET.get("provider", "")
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")

    txns_qs = PaymentTransaction.objects.select_related("payment_contract").order_by("-created_at")
    if status_filter:
        txns_qs = txns_qs.filter(status=status_filter)
    if provider_filter:
        txns_qs = txns_qs.filter(provider=provider_filter)
    if date_from:
        try:
            from datetime import date
            txns_qs = txns_qs.filter(created_at__date__gte=date.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            from datetime import date
            txns_qs = txns_qs.filter(created_at__date__lte=date.fromisoformat(date_to))
        except ValueError:
            pass

    export = request.GET.get("export", "")
    if export in {"csv", "pdf"}:
        export_txns = list(txns_qs[:2000])
        if export == "csv":
            response = HttpResponse(content_type="text/csv")
            response["Content-Disposition"] = f'attachment; filename="{dated_filename("payments", "csv")}"'
            writer = csv.writer(response)
            writer.writerow(["Contract", "Customer", "Amount", "Provider", "Status", "Paid At", "Reference"])
            for txn in export_txns:
                writer.writerow([
                    txn.payment_contract.contract_number if txn.payment_contract_id else "",
                    txn.payment_contract.customer_name if txn.payment_contract_id else "",
                    txn.amount,
                    txn.provider,
                    txn.status,
                    txn.paid_at.isoformat() if txn.paid_at else "",
                    txn.provider_reference or txn.internal_reference,
                ])
            return response
        rows = [
            [
                txn.payment_contract.contract_number if txn.payment_contract_id else "Unlinked",
                txn.payment_contract.customer_name if txn.payment_contract_id else "",
                txn.amount,
                txn.provider,
                txn.status,
                txn.paid_at.strftime("%Y-%m-%d %H:%M") if txn.paid_at else "",
                txn.provider_reference or txn.internal_reference,
            ]
            for txn in export_txns
        ]
        return export_pdf_response(
            "Payment Collections",
            ["Contract", "Customer", "Amount", "Provider", "Status", "Paid at", "Reference"],
            rows,
            filename=dated_filename("payments", "pdf"),
            filters=[("Status", status_filter), ("Provider", provider_filter), ("From", date_from), ("To", date_to)],
            summary=[("Records", len(export_txns)), ("Today", collections_today), ("Month", collections_month)],
            landscape=True,
        )

    transactions = txns_qs[:100]
    providers = PaymentTransaction.objects.values_list("provider", flat=True).distinct().order_by("provider")

    return render(request, "dashboard/hq_payment_collections.html", {
        "collections_today": collections_today,
        "collections_month": collections_month,
        "failed_payments": failed_payments,
        "pending_payments": pending_payments,
        "transactions": transactions,
        "providers": providers,
        "status_filter": status_filter,
        "provider_filter": provider_filter,
        "date_from": date_from,
        "date_to": date_to,
    })


@hq_required
def hq_reconciliation(request):
    """Payment reconciliation control page."""
    from portal.models import PaymentContract, PaymentTransaction
    from core.models import AuditLog

    msg = None
    msg_type = "info"

    if request.method == "POST":
        action = request.POST.get("action", "")
        txn_id = request.POST.get("txn_id")
        try:
            txn = PaymentTransaction.objects.get(pk=txn_id)
            if action == "mark_reconciled":
                txn.status = "paid"
                txn.save(update_fields=["status"])
                AuditLog.objects.create(
                    user=request.user,
                    action="hq_reconcile_manual",
                    object_type="PaymentTransaction",
                    object_id=str(txn.pk),
                    detail={"reference": txn.internal_reference, "amount": str(txn.amount)},
                )
                msg = f"Transaction {txn.internal_reference} marked as reconciled."
                msg_type = "success"
        except PaymentTransaction.DoesNotExist:
            msg = "Transaction not found."
            msg_type = "error"
        except Exception as exc:
            msg = f"Error: {exc}"
            msg_type = "error"

    # Matched: paid transactions with a contract
    matched = PaymentTransaction.objects.filter(
        status="paid", payment_contract__isnull=False
    ).count()
    # Unmatched: pending/failed with no contract link or no external reference
    unmatched = PaymentTransaction.objects.filter(
        status__in=["pending", "processing"],
        payment_contract__isnull=True,
    ).count()
    # Duplicate references
    from django.db.models import Count
    dup_refs = (
        PaymentTransaction.objects.exclude(provider_reference="")
        .exclude(provider_reference__isnull=True)
        .values("provider_reference")
        .annotate(cnt=Count("id"))
        .filter(cnt__gt=1)
        .count()
    )
    # External pending
    ext_pending = PaymentTransaction.objects.filter(status="processing").count()
    total_transactions = PaymentTransaction.objects.count()
    reconciliation_rate = round((matched / total_transactions) * 100, 1) if total_transactions else 0
    unlinked_paid = PaymentTransaction.objects.filter(status="paid", payment_contract__isnull=True).count()
    missing_provider_ref = PaymentTransaction.objects.filter(
        Q(provider_reference="") | Q(provider_reference__isnull=True)
    ).count()
    manual_review_total = unmatched + ext_pending + dup_refs + unlinked_paid

    # Recent unmatched for review
    unmatched_qs = PaymentTransaction.objects.filter(
        Q(status__in=["pending", "processing"]) | Q(payment_contract__isnull=True, status="failed")
    ).select_related("payment_contract").order_by("-created_at")[:50]

    export = request.GET.get("export", "")
    if export in {"csv", "pdf"}:
        review_qs = PaymentTransaction.objects.filter(
            Q(status__in=["pending", "processing"]) | Q(payment_contract__isnull=True, status="failed")
        ).select_related("payment_contract").order_by("-created_at")[:1000]
        if export == "csv":
            response = HttpResponse(content_type="text/csv")
            response["Content-Disposition"] = f'attachment; filename="{dated_filename("reconciliation", "csv")}"'
            writer = csv.writer(response)
            writer.writerow(["Reference", "Provider", "Amount", "Status", "Contract", "Created"])
            for txn in review_qs:
                writer.writerow([
                    txn.provider_reference or txn.internal_reference,
                    txn.provider,
                    txn.amount,
                    txn.status,
                    txn.payment_contract.contract_number if txn.payment_contract_id else "",
                    txn.created_at.isoformat(),
                ])
            return response
        rows = [
            [
                txn.provider_reference or txn.internal_reference,
                txn.provider,
                txn.amount,
                txn.status,
                txn.payment_contract.contract_number if txn.payment_contract_id else "Unlinked",
                txn.created_at.strftime("%Y-%m-%d %H:%M"),
            ]
            for txn in review_qs
        ]
        return export_pdf_response(
            "Payment Reconciliation",
            ["Reference", "Provider", "Amount", "Status", "Contract", "Created"],
            rows,
            filename=dated_filename("reconciliation", "pdf"),
            summary=[
                ("Matched", matched),
                ("Unmatched", unmatched),
                ("Duplicate refs", dup_refs),
                ("Match rate", f"{reconciliation_rate}%"),
            ],
            landscape=True,
        )

    return render(request, "dashboard/hq_reconciliation.html", {
        "matched": matched,
        "unmatched": unmatched,
        "dup_refs": dup_refs,
        "ext_pending": ext_pending,
        "reconciliation_rate": reconciliation_rate,
        "unlinked_paid": unlinked_paid,
        "missing_provider_ref": missing_provider_ref,
        "manual_review_total": manual_review_total,
        "unmatched_qs": unmatched_qs,
        "msg": msg,
        "msg_type": msg_type,
    })


@hq_required
def hq_sales_analytics(request):
    """Sales analytics and performance page."""
    from collections import Counter
    from decimal import Decimal
    import csv
    from django.http import HttpResponse

    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="sales_analytics.csv"'
        writer = csv.writer(response)
        writer.writerow([
            "Application #", "Customer", "Status", "Merchant",
            "Deal Brand", "Deal Model", "Deposit %", "Cash Price",
            "Gender", "Marital Status", "Dependents", "District",
            "Submitted At",
        ])
        for a in FinancingApplication.objects.select_related(
            "created_by", "deal", "deal__brand"
        ).order_by("-created_at")[:2000]:
            writer.writerow([
                a.application_number, a.customer_name, a.status,
                a.created_by.username if a.created_by_id else "",
                a.deal.brand.name if a.deal_id and a.deal.brand_id else "",
                a.deal.model_name if a.deal_id else "",
                a.selected_deposit_percent,
                a.selected_cash_price,
                a.gender, a.marital_status, a.num_dependents or 0,
                a.district,
                a.submitted_at.date() if a.submitted_at else "",
            ])
        return response

    total_apps = FinancingApplication.objects.count()
    submitted = FinancingApplication.objects.filter(
        status__in=["submitted", "pending_review", "under_review", "approved",
                    "rejected", "completed", "contract_complete"]
    ).count()
    approved = FinancingApplication.objects.filter(status="approved").count()
    completed = FinancingApplication.objects.filter(
        status__in=["completed", "contract_complete"]
    ).count()

    # By merchant (top 10)
    by_merchant = (
        FinancingApplication.objects.values("created_by__username")
        .annotate(cnt=Count("id"))
        .order_by("-cnt")[:10]
    )
    # Sales by day (last 14 days)
    from datetime import timedelta as td
    last_14 = []
    for i in range(13, -1, -1):
        day = (timezone.now() - td(days=i)).date()
        cnt = FinancingApplication.objects.filter(submitted_at__date=day).count()
        last_14.append({"date": str(day), "count": cnt})

    # Deposit band breakdown
    dep_15 = FinancingApplication.objects.filter(selected_deposit_percent__lte=16).count()
    dep_20 = FinancingApplication.objects.filter(
        selected_deposit_percent__gt=16, selected_deposit_percent__lte=22
    ).count()
    dep_30 = FinancingApplication.objects.filter(selected_deposit_percent__gt=22).count()

    # Gender breakdown
    gender_counts = {}
    for g, label in FinancingApplication.GENDER_CHOICES:
        if g:
            gender_counts[label] = FinancingApplication.objects.filter(gender=g).count()

    # Marital status
    marital_counts = {}
    for m, label in FinancingApplication.MARITAL_CHOICES:
        if m:
            marital_counts[label] = FinancingApplication.objects.filter(marital_status=m).count()

    # By brand
    from deals.models import DeviceDeal, DeviceBrand
    brand_counts = []
    for brand in DeviceBrand.objects.filter(is_active=True).order_by("name"):
        cnt = FinancingApplication.objects.filter(deal__brand=brand).count()
        brand_counts.append({"brand": brand.name, "count": cnt})

    return render(request, "dashboard/hq_sales_analytics.html", {
        "total_apps": total_apps,
        "submitted": submitted,
        "approved": approved,
        "completed": completed,
        "by_merchant": list(by_merchant),
        "last_14": last_14,
        "dep_15": dep_15,
        "dep_20": dep_20,
        "dep_30": dep_30,
        "gender_counts": gender_counts,
        "marital_counts": marital_counts,
        "brand_counts": brand_counts,
    })


@hq_required
def hq_underwriter_performance(request):
    """Underwriter performance analytics page."""
    from commissions.models import CommissionLedger
    from decimal import Decimal

    User = get_user_model()
    underwriters = User.objects.filter(profile__role="underwriter").order_by("username")

    stats = []
    for uw in underwriters:
        reviewed = FinancingApplication.objects.filter(reviewed_by=uw).count()
        approved_uw = FinancingApplication.objects.filter(reviewed_by=uw, status="approved").count()
        rejected_uw = FinancingApplication.objects.filter(reviewed_by=uw, status="rejected").count()
        approval_rate = round((approved_uw / reviewed * 100) if reviewed else 0, 1)
        rejection_rate = round((rejected_uw / reviewed * 100) if reviewed else 0, 1)

        gross = CommissionLedger.objects.filter(
            user=uw, entry_type=CommissionLedger.ENTRY_REPAYMENT
        ).aggregate(t=Sum("amount"))["t"] or Decimal("0")
        deductions = CommissionLedger.objects.filter(
            user=uw, entry_type=CommissionLedger.ENTRY_ARREARS
        ).aggregate(t=Sum("amount"))["t"] or Decimal("0")
        net = gross + deductions
        qc_reviewed = UnderwriterCallRecording.objects.filter(
            underwriter=uw
        ).exclude(status=UnderwriterCallRecording.STATUS_PENDING_QC).count()
        qc_passed = UnderwriterCallRecording.objects.filter(
            underwriter=uw,
            status=UnderwriterCallRecording.STATUS_PASSED_QC,
        ).count()
        qc_failed = UnderwriterCallRecording.objects.filter(
            underwriter=uw,
            status=UnderwriterCallRecording.STATUS_FAILED_QC,
        ).count()
        qc_pass_rate = round((qc_passed / qc_reviewed * 100) if qc_reviewed else 0, 1)
        qc_penalties = UnderwriterQCPenalty.objects.filter(underwriter=uw).aggregate(
            t=Sum("amount_mwk")
        )["t"] or Decimal("0")

        # Average review time (approved/rejected with reviewed_at and submitted_at)
        apps_with_times = FinancingApplication.objects.filter(
            reviewed_by=uw,
            reviewed_at__isnull=False,
            submitted_at__isnull=False,
        ).values_list("submitted_at", "reviewed_at")[:100]
        if apps_with_times:
            durations = [(r - s).total_seconds() / 3600 for s, r in apps_with_times]
            avg_review_hours = round(sum(durations) / len(durations), 1)
        else:
            avg_review_hours = None

        stats.append({
            "user": uw,
            "reviewed": reviewed,
            "approved": approved_uw,
            "rejected": rejected_uw,
            "approval_rate": approval_rate,
            "rejection_rate": rejection_rate,
            "gross_commission": gross,
            "deductions": deductions,
            "net_commission": net,
            "avg_review_hours": avg_review_hours,
            "qc_reviewed": qc_reviewed,
            "qc_pass_rate": qc_pass_rate,
            "qc_failed": qc_failed,
            "qc_penalties": qc_penalties,
        })

    stats.sort(key=lambda x: x["reviewed"], reverse=True)

    return render(request, "dashboard/hq_underwriter_performance.html", {
        "stats": stats,
    })


@hq_required
def hq_call_recording_qc(request):
    """HQ review queue for underwriter call recording evidence."""
    User = get_user_model()
    status = request.GET.get("status", "").strip()
    underwriter_id = request.GET.get("underwriter", "").strip()
    search = request.GET.get("q", "").strip()

    recordings = (
        UnderwriterCallRecording.objects.select_related(
            "application", "application__deal", "application__created_by", "underwriter", "reviewed_by"
        )
        .annotate(penalty_total=Sum("qc_penalties__amount_mwk"))
        .order_by("-uploaded_at")
    )
    if status:
        recordings = recordings.filter(status=status)
    if underwriter_id:
        recordings = recordings.filter(underwriter_id=underwriter_id)
    if search:
        recordings = recordings.filter(
            Q(application__application_number__icontains=search)
            | Q(application__customer_name__icontains=search)
            | Q(application__customer_phone__icontains=search)
            | Q(underwriter__username__icontains=search)
        )

    month_start = timezone.localdate().replace(day=1)
    base_recordings = UnderwriterCallRecording.objects.all()
    penalty_base = UnderwriterQCPenalty.objects.all()
    approved_without_recording = FinancingApplication.objects.filter(
        status__in=["approved", "approved_pending_device_lock", "device_locked", "active_contract", "completed"],
        underwriter_call_recordings__isnull=True,
    ).count()

    offense_counts = (
        UnderwriterQCPenalty.objects.values("offense_type__code", "offense_type__name")
        .annotate(count=Count("id"), total=Sum("amount_mwk"))
        .order_by("-count")[:8]
    )
    underwriter_quality = []
    for uw in User.objects.filter(profile__role="underwriter").order_by("username")[:50]:
        uw_recordings = base_recordings.filter(underwriter=uw)
        reviewed = uw_recordings.exclude(status=UnderwriterCallRecording.STATUS_PENDING_QC).count()
        passed = uw_recordings.filter(status=UnderwriterCallRecording.STATUS_PASSED_QC).count()
        underwriter_quality.append({
            "user": uw,
            "reviewed": reviewed,
            "pass_rate": round((passed / reviewed * 100) if reviewed else 0, 1),
            "penalties": penalty_base.filter(underwriter=uw).aggregate(t=Sum("amount_mwk"))["t"] or Decimal("0"),
        })
    underwriter_quality.sort(key=lambda row: (row["reviewed"], row["pass_rate"]), reverse=True)

    return render(request, "dashboard/hq_call_recording_qc.html", {
        "recordings": recordings[:100],
        "offense_types": QCOffenseType.objects.filter(active=True).order_by("severity", "code"),
        "underwriters": User.objects.filter(profile__role="underwriter").order_by("username"),
        "filters": {"status": status, "underwriter": underwriter_id, "q": search},
        "qc_status_choices": UnderwriterCallRecording.STATUS_CHOICES,
        "kpis": {
            "pending": base_recordings.filter(status=UnderwriterCallRecording.STATUS_PENDING_QC).count(),
            "failed_month": base_recordings.filter(
                status=UnderwriterCallRecording.STATUS_FAILED_QC,
                reviewed_at__date__gte=month_start,
            ).count(),
            "penalties_issued": penalty_base.aggregate(t=Sum("amount_mwk"))["t"] or Decimal("0"),
            "penalties_reversed": penalty_base.filter(status=UnderwriterQCPenalty.STATUS_REVERSED).aggregate(t=Sum("amount_mwk"))["t"] or Decimal("0"),
            "approved_without_recording": approved_without_recording,
        },
        "offense_counts": offense_counts,
        "underwriter_quality": underwriter_quality[:8],
    })


@hq_required
@require_POST
def hq_call_recording_qc_action(request, recording_id):
    recording = get_object_or_404(
        UnderwriterCallRecording.objects.select_related("application", "underwriter"),
        pk=recording_id,
    )
    result = request.POST.get("qc_result", "")
    notes = (request.POST.get("notes") or "").strip()
    offense_ids = request.POST.getlist("offense_ids")

    before = {"status": recording.status, "qc_notes": recording.qc_notes}
    if result == "pass":
        recording.mark_reviewed(UnderwriterCallRecording.STATUS_PASSED_QC, request.user, notes)
        messages.success(request, "Recording marked as passed QC.")
    elif result in {"fail_warning", "fail_penalty"}:
        recording.mark_reviewed(UnderwriterCallRecording.STATUS_FAILED_QC, request.user, notes)
        if result == "fail_penalty":
            offenses = QCOffenseType.objects.filter(pk__in=offense_ids, active=True)
            created_count = 0
            with transaction.atomic():
                for offense in offenses:
                    amount = offense.default_penalty_mwk
                    override_key = f"amount_{offense.pk}"
                    raw_override = (request.POST.get(override_key) or "").strip()
                    if raw_override:
                        if not request.user.has_perm("approvals.hq_override_penalty_amount"):
                            messages.error(request, "Penalty amount override requires HQ override permission.")
                            return redirect("hq_call_recording_qc")
                        if not notes:
                            messages.error(request, "Penalty overrides require notes.")
                            return redirect("hq_call_recording_qc")
                        amount = Decimal(raw_override)
                    penalty = UnderwriterQCPenalty.objects.create(
                        application=recording.application,
                        contract=recording.contract,
                        underwriter=recording.underwriter,
                        offense_type=offense,
                        amount_mwk=amount,
                        notes=notes,
                        evidence_recording=recording,
                        issued_by=request.user,
                    )
                    penalty.apply_to_ledger(actor=request.user)
                    created_count += 1
            messages.success(request, f"Recording failed QC and {created_count} penalty item(s) were applied.")
        else:
            messages.warning(request, "Recording failed QC with warning only.")
    else:
        messages.error(request, "Choose a QC result.")
        return redirect("hq_call_recording_qc")

    try:
        from core.models import AuditLog

        AuditLog.objects.create(
            user=request.user,
            action=AuditLog.ACTION_KYC_CHANGE,
            object_type="UnderwriterCallRecording",
            object_id=str(recording.pk),
            detail={
                "action": "call_recording_qc_saved",
                "before": before,
                "after": {"status": recording.status, "qc_notes": recording.qc_notes},
                "offense_ids": offense_ids,
            },
        )
    except Exception:
        pass
    return redirect("hq_call_recording_qc")


@hq_required
@require_POST
def hq_qc_penalty_reverse(request, penalty_id):
    penalty = get_object_or_404(UnderwriterQCPenalty.objects.select_related("offense_type"), pk=penalty_id)
    reason = (request.POST.get("reason") or "").strip()
    if not reason:
        messages.error(request, "Reversal reason is required.")
        return redirect("hq_call_recording_qc")
    penalty.reverse_to_ledger(actor=request.user, reason=reason)
    try:
        from core.models import AuditLog

        AuditLog.objects.create(
            user=request.user,
            action=AuditLog.ACTION_KYC_CHANGE,
            object_type="UnderwriterQCPenalty",
            object_id=str(penalty.pk),
            detail={"action": "penalty_reversed", "reason": reason},
        )
    except Exception:
        pass
    messages.success(request, "QC penalty reversed and ledger credit created.")
    return redirect("hq_call_recording_qc")


@hq_required
def hq_fraud_checks(request):
    """Fraud detection and risk flags page."""
    from core.models import AuditLog
    from django.db.models import Count

    msg = None
    msg_type = "info"

    if request.method == "POST":
        action = request.POST.get("action", "")
        app_id = request.POST.get("app_id")
        note = request.POST.get("note", "").strip()
        try:
            app = FinancingApplication.objects.get(pk=app_id)
            if action == "mark_cleared":
                app.manager_comment = f"[FRAUD-CLEARED] {note}" if note else "[FRAUD-CLEARED]"
                app.save(update_fields=["manager_comment"])
                AuditLog.objects.create(
                    user=request.user, action="hq_fraud_cleared",
                    object_type="FinancingApplication", object_id=str(app.pk),
                    detail={"app": app.application_number, "note": note},
                )
                msg = f"Application {app.application_number} marked as cleared."
                msg_type = "success"
            elif action == "mark_blocked":
                app.status = "rejected"
                app.manager_comment = f"[FRAUD-BLOCKED] {note}" if note else "[FRAUD-BLOCKED]"
                app.save(update_fields=["status", "manager_comment"])
                AuditLog.objects.create(
                    user=request.user, action="hq_fraud_blocked",
                    object_type="FinancingApplication", object_id=str(app.pk),
                    detail={"app": app.application_number, "note": note},
                )
                msg = f"Application {app.application_number} blocked."
                msg_type = "warning"
        except FinancingApplication.DoesNotExist:
            msg = "Application not found."
            msg_type = "error"
        except Exception as exc:
            msg = f"Error: {exc}"
            msg_type = "error"

    # Duplicate national IDs
    dup_ids = (
        FinancingApplication.objects.exclude(national_id="")
        .values("national_id")
        .annotate(cnt=Count("id"))
        .filter(cnt__gt=1)
        .order_by("-cnt")[:20]
    )
    # Duplicate phones
    dup_phones = (
        FinancingApplication.objects.exclude(customer_phone="")
        .values("customer_phone")
        .annotate(cnt=Count("id"))
        .filter(cnt__gt=1)
        .order_by("-cnt")[:20]
    )
    # Same guarantor phone used multiple times
    dup_guarantors = (
        FinancingApplication.objects.exclude(next_of_kin_1_phone="")
        .values("next_of_kin_1_phone")
        .annotate(cnt=Count("id"))
        .filter(cnt__gt=2)
        .order_by("-cnt")[:20]
    )
    # Duplicate IMEI
    dup_imei = (
        FinancingApplication.objects.exclude(imei_number="")
        .values("imei_number")
        .annotate(cnt=Count("id"))
        .filter(cnt__gt=1)
        .order_by("-cnt")[:10]
    )
    # Third-party phone user risk flags
    third_party_flags = FinancingApplication.objects.filter(
        third_party_phone_user_risk_flagged=True,
        status__in=["pending_review", "under_review", "submitted"],
    ).select_related("created_by").order_by("-created_at")[:20]

    # Applications with blocked/fraud notes
    fraud_marked = FinancingApplication.objects.filter(
        manager_comment__startswith="[FRAUD"
    ).select_related("created_by").order_by("-created_at")[:30]

    # ── Device mismatch (IMEI verification) ──────────────────────────────────
    from django.db.models import Q as DQ
    imei_mismatches = (
        FinancingApplication.objects.filter(
            imei_verification_status="mismatch",
            imei_override=False,
        )
        .select_related("created_by", "deal", "deal__brand")
        .order_by("-imei_verified_at")[:30]
    )
    imei_possible = (
        FinancingApplication.objects.filter(
            imei_verification_status="possible_match",
        )
        .select_related("created_by", "deal", "deal__brand")
        .order_by("-imei_verified_at")[:20]
    )
    imei_errors = (
        FinancingApplication.objects.filter(
            imei_verification_status__in=["api_error", "unknown"],
        )
        .exclude(imei_number="")
        .select_related("created_by", "deal", "deal__brand")
        .order_by("-imei_verified_at")[:20]
    )
    imei_mismatch_count = FinancingApplication.objects.filter(
        imei_verification_status="mismatch", imei_override=False
    ).count()
    imei_possible_count = FinancingApplication.objects.filter(
        imei_verification_status="possible_match"
    ).count()
    imei_error_count = FinancingApplication.objects.filter(
        imei_verification_status__in=["api_error", "unknown"]
    ).exclude(imei_number="").count()

    # ── Device Connectivity Intelligence ─────────────────────────────────────
    from risk.models import DeviceConnectivitySignal
    from django.utils import timezone as tz

    # Handle connectivity POST actions
    conn_action = request.POST.get("conn_action", "") if request.method == "POST" else ""
    signal_id = request.POST.get("signal_id")
    if conn_action and signal_id:
        try:
            signal = DeviceConnectivitySignal.objects.get(pk=signal_id)
            if conn_action == "mark_false_positive":
                signal.is_false_positive = True
                signal.false_positive_marked_by = request.user
                signal.save(update_fields=["is_false_positive", "false_positive_marked_by", "updated_at"])
                AuditLog.objects.create(
                    user=request.user, action="connectivity_false_positive",
                    object_type="DeviceConnectivitySignal", object_id=str(signal.pk),
                    detail={"imei": signal.imei},
                )
                msg = f"Signal for IMEI {signal.imei} marked as false positive."
                msg_type = "success"
            elif conn_action == "send_sync_instruction":
                signal.sync_instruction_sent_at = tz.now()
                signal.save(update_fields=["sync_instruction_sent_at", "updated_at"])
                AuditLog.objects.create(
                    user=request.user, action="sync_instruction_sent",
                    object_type="DeviceConnectivitySignal", object_id=str(signal.pk),
                    detail={"imei": signal.imei, "customer": signal.customer_name},
                )
                msg = f"Internet sync instruction sent for {signal.customer_name}."
                msg_type = "success"
            elif conn_action == "escalate_recovery":
                signal.escalated_at = tz.now()
                signal.connectivity_status = DeviceConnectivitySignal.CONN_RECOVERY_REVIEW
                signal.save(update_fields=["escalated_at", "connectivity_status", "updated_at"])
                AuditLog.objects.create(
                    user=request.user, action="recovery_escalation",
                    object_type="DeviceConnectivitySignal", object_id=str(signal.pk),
                    detail={"imei": signal.imei},
                )
                msg = f"IMEI {signal.imei} escalated to recovery desk."
                msg_type = "warning"
        except DeviceConnectivitySignal.DoesNotExist:
            if not msg:
                msg = "Connectivity signal not found."
                msg_type = "error"
        except Exception as exc:
            if not msg:
                msg = f"Error: {exc}"
                msg_type = "error"

    # Fetch connectivity signals
    connectivity_signals = (
        DeviceConnectivitySignal.objects.filter(is_false_positive=False)
        .select_related("application", "lock_profile")
        .order_by("-risk_score", "-days_offline")[:50]
    )

    # Connectivity KPI counters
    conn_offline_total = DeviceConnectivitySignal.objects.filter(
        is_false_positive=False, days_offline__gte=1
    ).count()
    conn_high_risk = DeviceConnectivitySignal.objects.filter(
        is_false_positive=False, risk_band__in=["high", "critical"]
    ).count()
    conn_critical = DeviceConnectivitySignal.objects.filter(
        is_false_positive=False, risk_band="critical"
    ).count()
    conn_paid_offline = DeviceConnectivitySignal.objects.filter(
        is_false_positive=False,
        connectivity_status="offline_paid",
    ).count()
    conn_arrears_offline = DeviceConnectivitySignal.objects.filter(
        is_false_positive=False,
        connectivity_status="offline_arrears",
    ).count()
    conn_sync_pending = DeviceConnectivitySignal.objects.filter(
        is_false_positive=False,
        sync_status="pending_device_online",
    ).count()
    conn_needs_internet = DeviceConnectivitySignal.objects.filter(
        is_false_positive=False,
        connectivity_help_needed=True,
    ).count()
    conn_escalated = DeviceConnectivitySignal.objects.filter(
        is_false_positive=False,
        escalated_at__isnull=False,
    ).count()

    # Telemetry provider status from settings
    paytrigger_configured = bool(
        getattr(settings, "PAYTRIGGER_API_KEY", None)
        or getattr(settings, "ENABLE_PAYTRIGGER", False)
    )
    upya_configured = bool(
        getattr(settings, "UPYA_API_KEY", None)
        or getattr(settings, "ENABLE_UPYA", False)
    )
    telemetry_connected = paytrigger_configured or upya_configured

    return render(request, "dashboard/hq_fraud_checks.html", {
        "dup_ids": list(dup_ids),
        "dup_phones": list(dup_phones),
        "dup_guarantors": list(dup_guarantors),
        "dup_imei": list(dup_imei),
        "third_party_flags": third_party_flags,
        "fraud_marked": fraud_marked,
        "msg": msg,
        "msg_type": msg_type,
        # IMEI mismatch
        "imei_mismatches": imei_mismatches,
        "imei_possible": imei_possible,
        "imei_errors": imei_errors,
        "imei_mismatch_count": imei_mismatch_count,
        "imei_possible_count": imei_possible_count,
        "imei_error_count": imei_error_count,
        # Connectivity intelligence
        "connectivity_signals": connectivity_signals,
        "conn_offline_total": conn_offline_total,
        "conn_high_risk": conn_high_risk,
        "conn_critical": conn_critical,
        "conn_paid_offline": conn_paid_offline,
        "conn_arrears_offline": conn_arrears_offline,
        "conn_sync_pending": conn_sync_pending,
        "conn_needs_internet": conn_needs_internet,
        "conn_escalated": conn_escalated,
        "telemetry_connected": telemetry_connected,
    })


@hq_required
def hq_audit_trail(request):
    """System audit trail page."""
    from core.models import AuditLog
    import csv
    from django.http import HttpResponse

    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="audit_trail.csv"'
        writer = csv.writer(response)
        writer.writerow(["Timestamp", "User", "Action", "Object Type", "Object ID", "IP", "Detail"])
        for entry in AuditLog.objects.select_related("user").order_by("-timestamp")[:5000]:
            writer.writerow([
                entry.timestamp, entry.user.username if entry.user_id else "",
                entry.action, entry.object_type, entry.object_id,
                entry.ip_address or "", str(entry.detail or ""),
            ])
        return response

    action_filter = request.GET.get("action", "")
    user_filter = request.GET.get("user", "")
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    object_type_filter = request.GET.get("object_type", "")

    qs = AuditLog.objects.select_related("user").order_by("-timestamp")
    if action_filter:
        qs = qs.filter(action__icontains=action_filter)
    if user_filter:
        qs = qs.filter(Q(user__username__icontains=user_filter) | Q(user__get_full_name__icontains=user_filter))
    if date_from:
        try:
            from datetime import date
            qs = qs.filter(timestamp__date__gte=date.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            from datetime import date
            qs = qs.filter(timestamp__date__lte=date.fromisoformat(date_to))
        except ValueError:
            pass
    if object_type_filter:
        qs = qs.filter(object_type__icontains=object_type_filter)

    entries = qs[:200]
    total_today = AuditLog.objects.filter(timestamp__date=timezone.now().date()).count()
    total_all = AuditLog.objects.count()
    object_types = AuditLog.objects.values_list("object_type", flat=True).distinct().order_by("object_type")

    User = get_user_model()
    all_users = User.objects.filter(
        pk__in=AuditLog.objects.values_list("user", flat=True).distinct()
    ).order_by("username")

    return render(request, "dashboard/hq_audit_trail.html", {
        "entries": entries,
        "total_today": total_today,
        "total_all": total_all,
        "action_filter": action_filter,
        "user_filter": user_filter,
        "date_from": date_from,
        "date_to": date_to,
        "object_type_filter": object_type_filter,
        "object_types": object_types,
        "all_users": all_users,
    })


@hq_required
def hq_payment_intelligence(request):
    """
    HQ Payment Intelligence Dashboard — Section 12.

    Shows aggregated payment behaviour analytics across all active contracts,
    broken down by district, occupation, merchant, device model, and underwriter.
    Uses only real stored data from PaymentContract.analytics_* fields.
    No fake AI or predictions.
    """
    from portal.models import PaymentContract, PaymentTransaction

    # ── Overall portfolio KPIs ─────────────────────────────────────────────
    all_contracts = PaymentContract.objects.exclude(status="cancelled")
    total_contracts = all_contracts.count()
    active_contracts = all_contracts.filter(status="active").count()
    completed_contracts = all_contracts.filter(status="completed").count()
    overdue_contracts = all_contracts.filter(status__in=["overdue", "locked"]).count()

    portfolio_agg = all_contracts.aggregate(
        total_collected=Sum("amount_paid"),
        total_value=Sum("total_amount"),
        avg_consistency=Avg("analytics_consistency"),
        avg_payment_count=Avg("analytics_payment_count"),
        avg_days_late=Avg("analytics_days_late"),
    )
    total_collected = portfolio_agg["total_collected"] or 0
    total_value = portfolio_agg["total_value"] or 0
    portfolio_completion = round((completed_contracts / total_contracts * 100) if total_contracts else 0, 1)
    portfolio_default_rate = round((overdue_contracts / total_contracts * 100) if total_contracts else 0, 1)
    avg_consistency = round(portfolio_agg["avg_consistency"] or 0, 1)
    avg_days_late = round(portfolio_agg["avg_days_late"] or 0, 1)

    # ── By District ────────────────────────────────────────────────────────
    by_district = (
        all_contracts
        .filter(source_application__isnull=False)
        .values(district=F("source_application__district"))
        .annotate(
            total=Count("id"),
            completed=Count("id", filter=Q(status="completed")),
            overdue=Count("id", filter=Q(status__in=["overdue", "locked"])),
            avg_cons=Avg("analytics_consistency"),
            avg_late=Avg("analytics_days_late"),
            collected=Sum("amount_paid"),
        )
        .exclude(district="")
        .order_by("-avg_cons")[:20]
    )

    # ── By Occupation ──────────────────────────────────────────────────────
    by_occupation = (
        all_contracts
        .filter(source_application__isnull=False)
        .values(occupation=F("source_application__occupation"))
        .annotate(
            total=Count("id"),
            completed=Count("id", filter=Q(status="completed")),
            overdue=Count("id", filter=Q(status__in=["overdue", "locked"])),
            avg_cons=Avg("analytics_consistency"),
            avg_late=Avg("analytics_days_late"),
            collected=Sum("amount_paid"),
        )
        .exclude(occupation="")
        .order_by("-avg_cons")[:20]
    )

    # ── By Merchant (created_by on source_application) ─────────────────────
    by_merchant = (
        all_contracts
        .filter(source_application__isnull=False, source_application__created_by__isnull=False)
        .values(
            merchant_id=F("source_application__created_by__id"),
            merchant_name=F("source_application__created_by__get_full_name"),
        )
        .annotate(
            total=Count("id"),
            completed=Count("id", filter=Q(status="completed")),
            overdue=Count("id", filter=Q(status__in=["overdue", "locked"])),
            avg_cons=Avg("analytics_consistency"),
            collected=Sum("amount_paid"),
        )
        .order_by("-avg_cons")[:20]
    )

    # ── By Device Model ────────────────────────────────────────────────────
    by_device = (
        all_contracts
        .exclude(device_model="")
        .values("device_model")
        .annotate(
            total=Count("id"),
            completed=Count("id", filter=Q(status="completed")),
            overdue=Count("id", filter=Q(status__in=["overdue", "locked"])),
            avg_cons=Avg("analytics_consistency"),
            avg_late=Avg("analytics_days_late"),
            collected=Sum("amount_paid"),
        )
        .order_by("-avg_cons")[:20]
    )

    # ── Payment method distribution ────────────────────────────────────────
    by_provider = (
        PaymentTransaction.objects
        .filter(status="paid")
        .values("provider")
        .annotate(total=Count("id"), volume=Sum("amount"))
        .order_by("-volume")
    )

    # ── Discipline distribution ────────────────────────────────────────────
    discipline_dist = (
        all_contracts
        .exclude(analytics_discipline="")
        .values("analytics_discipline")
        .annotate(count=Count("id"))
        .order_by("-count")
    )

    # ── Average completion stats ───────────────────────────────────────────
    completed_qs = all_contracts.filter(status="completed")
    avg_completion_payments = completed_qs.aggregate(avg=Avg("analytics_payment_count"))["avg"] or 0

    return render(request, "dashboard/hq_payment_intelligence.html", {
        # Portfolio KPIs
        "total_contracts": total_contracts,
        "active_contracts": active_contracts,
        "completed_contracts": completed_contracts,
        "overdue_contracts": overdue_contracts,
        "total_collected": total_collected,
        "total_value": total_value,
        "portfolio_completion": portfolio_completion,
        "portfolio_default_rate": portfolio_default_rate,
        "avg_consistency": avg_consistency,
        "avg_days_late": avg_days_late,
        "avg_completion_payments": round(avg_completion_payments, 1),
        # Breakdowns
        "by_district": list(by_district),
        "by_occupation": list(by_occupation),
        "by_merchant": list(by_merchant),
        "by_device": list(by_device),
        "by_provider": list(by_provider),
        "discipline_dist": list(discipline_dist),
    })


@developer_preview_required
def hq_merchant_preview(request):
    context = merchant_dashboard_context(request.user)
    context["is_developer_preview"] = True
    return render(request, "dashboard/home.html", context)


@developer_preview_required
def hq_underwriter_preview(request):
    my_active = FinancingApplication.objects.filter(
        claimed_by=request.user,
        status="under_review",
    )
    pending_count = FinancingApplication.objects.filter(
        status="pending_review",
        claimed_by__isnull=True,
    ).count()
    pending_queue = FinancingApplication.objects.select_related("created_by").filter(
        status="pending_review",
        claimed_by__isnull=True,
    ).order_by("submitted_at", "id")[:10]
    completed_reviews = FinancingApplication.objects.filter(
        reviewed_by=request.user,
        status__in=["approved", "completed", "contract_complete"],
    ).order_by("-reviewed_at")[:10]
    underwriter_earnings = (
        Commission.objects.filter(user=request.user, role=Commission.ROLE_MANAGER)
        .exclude(status=Commission.STATUS_CANCELLED)
        .aggregate(total=Sum("amount"))["total"]
        or 0
    )
    return render(
        request,
        "approvals/home.html",
        {
            "my_active": my_active,
            "pending_count": pending_count,
            "pending_queue": pending_queue,
            "completed_reviews": completed_reviews,
            "active_count": my_active.count(),
            "max_active": MAX_ACTIVE_UNDERWRITER_REVIEWS,
            "underwriter_earnings": underwriter_earnings,
            "can_claim": pending_count > 0 and my_active.count() < MAX_ACTIVE_UNDERWRITER_REVIEWS,
            "is_developer_preview": True,
        },
    )


# ── HQ Merchant Agreement Management ───────────────────────────────────────

@hq_required
def hq_merchant_agreements(request):
    from merchants.models import Merchant, MerchantAgreement

    if request.method == "POST":
        action = request.POST.get("action", "")
        merchant_id = request.POST.get("merchant_id")
        merchant = get_object_or_404(Merchant, id=merchant_id)
        if action == "approve_compliance":
            if not merchant.certificate_file:
                messages.error(request, "Merchant has not uploaded a certificate.")
            elif not merchant.has_signed_agreement:
                messages.error(request, "Merchant must sign the agreement before compliance approval.")
            else:
                merchant.certificate_status = Merchant.CERTIFICATE_APPROVED
                merchant.compliance_reviewed_by = request.user
                merchant.compliance_reviewed_at = timezone.now()
                merchant.compliance_rejection_reason = ""
                merchant.save(update_fields=[
                    "certificate_status",
                    "compliance_reviewed_by",
                    "compliance_reviewed_at",
                    "compliance_rejection_reason",
                ])
                messages.success(request, f"{merchant.business_name} compliance approved.")
        elif action == "reject_compliance":
            reason = request.POST.get("rejection_reason", "").strip()
            if not reason:
                messages.error(request, "Add a rejection reason before rejecting compliance.")
            else:
                merchant.certificate_status = Merchant.CERTIFICATE_REJECTED
                merchant.compliance_reviewed_by = request.user
                merchant.compliance_reviewed_at = timezone.now()
                merchant.compliance_rejection_reason = reason
                merchant.save(update_fields=[
                    "certificate_status",
                    "compliance_reviewed_by",
                    "compliance_reviewed_at",
                    "compliance_rejection_reason",
                ])
                messages.info(request, f"{merchant.business_name} compliance rejected.")
        return redirect("hq_merchant_agreements")

    agreements = (
        MerchantAgreement.objects
        .select_related("merchant", "merchant__owner")
        .order_by("-created_at")
    )

    # Get merchants without any agreement
    merchants_with_agreement = agreements.values_list("merchant_id", flat=True).distinct()
    merchants_all = Merchant.objects.select_related("owner").all()
    merchants_unsigned = merchants_all.exclude(id__in=merchants_with_agreement)
    merchant_compliance_rows = []
    for merchant in merchants_all.prefetch_related("agreements").order_by("business_name"):
        merchant_compliance_rows.append({
            "merchant": merchant,
            "agreement": merchant.agreements.order_by("-created_at").first(),
            "can_approve": merchant.has_signed_agreement and bool(merchant.certificate_file),
        })

    stats = {
        "total": agreements.count(),
        "signed": agreements.filter(status__in=["signed", "active"]).count(),
        "pending": agreements.filter(status__in=["not_started", "viewed"]).count(),
        "suspended": agreements.filter(status="suspended").count(),
        "no_agreement": merchants_unsigned.count(),
        "compliance_approved": sum(1 for merchant in merchants_all if merchant.is_compliance_complete),
        "compliance_pending": merchants_all.filter(certificate_status=Merchant.CERTIFICATE_PENDING).count(),
        "compliance_rejected": merchants_all.filter(certificate_status=Merchant.CERTIFICATE_REJECTED).count(),
        "compliance_blocked": sum(1 for merchant in merchants_all if not merchant.is_compliance_complete),
    }

    return render(request, "dashboard/hq_merchant_agreements.html", {
        "agreements": agreements[:100],
        "merchant_compliance_rows": merchant_compliance_rows,
        "merchants_unsigned": merchants_unsigned[:50],
        "stats": stats,
    })


@hq_required
def hq_agreement_require_resign(request, agreement_id):
    from merchants.models import MerchantAgreement
    if request.method == "POST":
        agreement = get_object_or_404(MerchantAgreement, id=agreement_id)
        agreement.status = MerchantAgreement.STATUS_EXPIRED
        agreement.save(update_fields=["status", "updated_at"])
        messages.success(request, f"Agreement {agreement.reference_number} marked as expired. Merchant will need to re-sign.")
    return redirect("hq_merchant_agreements")


@hq_required
def hq_agreement_pdf(request, agreement_id):
    from merchants.models import MerchantAgreement
    from django.http import FileResponse
    agreement = get_object_or_404(MerchantAgreement, id=agreement_id)
    if not agreement.pdf_file:
        try:
            from merchants.pdf import generate_merchant_agreement_pdf
            generate_merchant_agreement_pdf(agreement)
            agreement.refresh_from_db()
        except Exception as exc:
            messages.error(request, f"PDF could not be generated: {exc}")
            return redirect("hq_merchant_agreements")
    try:
        response = FileResponse(agreement.pdf_file.open("rb"), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="agreement-{agreement.reference_number}.pdf"'
        return response
    except FileNotFoundError:
        messages.error(request, "PDF file not found.")
        return redirect("hq_merchant_agreements")


# ── HQ WhatsApp Bot Operations Dashboard ────────────────────────────────────

@hq_required
def hq_whatsapp_bot(request):
    from support.models import SupportTicket, WhatsAppConversation, WhatsAppMessage
    from support.whatsapp_ops import (
        display_whatsapp_url,
        mask_secret,
        provider_config_status,
        provider_label,
        whatsapp_status_callback_path,
        whatsapp_webhook_path,
    )

    now = timezone.now()
    today = now.date()
    config = provider_config_status()
    whatsapp_tickets = SupportTicket.objects.filter(source="whatsapp")
    active_statuses = [
        SupportTicket.STATUS_NEW,
        SupportTicket.STATUS_OPEN,
        SupportTicket.STATUS_ASSIGNED,
        SupportTicket.STATUS_IN_PROGRESS,
        SupportTicket.STATUS_WAITING_CUSTOMER,
        SupportTicket.STATUS_WAITING_USER,
        SupportTicket.STATUS_ESCALATED,
    ]
    open_tickets = whatsapp_tickets.filter(status__in=active_statuses).count()
    urgent_tickets = whatsapp_tickets.filter(
        priority__in=[SupportTicket.PRI_URGENT, SupportTicket.PRI_CRITICAL],
        status__in=active_statuses,
    ).count()
    overdue_sla = whatsapp_tickets.filter(sla_due_at__lt=now, status__in=active_statuses).count()
    waiting_for_customer = whatsapp_tickets.filter(
        status__in=[SupportTicket.STATUS_WAITING_CUSTOMER, SupportTicket.STATUS_WAITING_USER]
    ).count()
    resolved_today = whatsapp_tickets.filter(status=SupportTicket.STATUS_RESOLVED, resolved_at__date=today).count()
    first_response_values = [
        (ticket.first_response_at - ticket.created_at).total_seconds() / 60
        for ticket in whatsapp_tickets.exclude(first_response_at__isnull=True)[:100]
    ]
    avg_first_response = round(sum(first_response_values) / len(first_response_values), 1) if first_response_values else None
    wa_sent_today = WhatsAppMessage.objects.filter(
        direction=WhatsAppMessage.DIRECTION_OUTBOUND,
        created_at__date=today,
    ).count()
    wa_failed_today = WhatsAppMessage.objects.filter(
        direction=WhatsAppMessage.DIRECTION_OUTBOUND,
        provider_status__in=[WhatsAppMessage.STATUS_FAILED, WhatsAppMessage.STATUS_UNDELIVERED],
        created_at__date=today,
    ).count()
    wa_queued = WhatsAppMessage.objects.filter(provider_status=WhatsAppMessage.STATUS_QUEUED).count()
    delivery_rate = round((wa_sent_today - wa_failed_today) / wa_sent_today * 100, 1) if wa_sent_today else None
    last_inbound = WhatsAppMessage.objects.filter(direction=WhatsAppMessage.DIRECTION_INBOUND).order_by("-created_at").first()
    last_outbound = WhatsAppMessage.objects.filter(direction=WhatsAppMessage.DIRECTION_OUTBOUND).order_by("-created_at").first()
    last_status_callback = None
    for message in WhatsAppMessage.objects.order_by("-updated_at")[:100]:
        raw_payload = message.raw_payload or {}
        if isinstance(raw_payload, dict) and raw_payload.get("status_callback"):
            last_status_callback = message
            break
    conversation_count = WhatsAppConversation.objects.count()
    open_conversations = WhatsAppConversation.objects.filter(status=WhatsAppConversation.STATUS_OPEN).count()
    recent_messages = WhatsAppMessage.objects.select_related("conversation__contact", "ticket").order_by("-created_at")[:8]
    recent_tickets = whatsapp_tickets.select_related(
        "assigned_to", "linked_contact", "related_application"
    ).order_by("-last_message_at", "-created_at")[:12]
    escalations = whatsapp_tickets.filter(
        Q(priority__in=[SupportTicket.PRI_URGENT, SupportTicket.PRI_CRITICAL])
        | Q(category__in=[
            SupportTicket.CAT_FRAUD_REPORT,
            SupportTicket.CAT_LEGAL_REVIEW,
            SupportTicket.CAT_MERCHANT_COMPLAINT,
            SupportTicket.CAT_UNDERWRITER_COMPLAINT,
        ])
        | Q(sla_due_at__lt=now)
        | Q(created_at__lt=now - timedelta(hours=24), status__in=active_statuses)
    ).order_by("sla_due_at", "-created_at")[:10]
    templates = [
        {"name": "tengasale_ticket_created", "enabled": True, "category": "Support lifecycle"},
        {"name": "tengasale_ticket_assigned", "enabled": True, "category": "Support lifecycle"},
        {"name": "tengasale_ticket_waiting_customer", "enabled": True, "category": "Support lifecycle"},
        {"name": "tengasale_ticket_escalated", "enabled": True, "category": "Support lifecycle"},
        {"name": "tengasale_ticket_resolved", "enabled": True, "category": "Support lifecycle"},
        {"name": "tengasale_ticket_closed", "enabled": True, "category": "Support lifecycle"},
        {"name": "tengasale_support_menu", "enabled": True, "category": "Active session utility"},
        {"name": "tengasale_otp_verification", "enabled": True, "category": "Authentication"},
    ]
    return render(request, "dashboard/hq_whatsapp_bot.html", {
        "page_title": "WhatsApp Support Operations",
        "messaging_provider": config["mode"],
        "provider_label": provider_label(),
        "provider_ready": config["ready"],
        "provider_missing": config["missing"],
        "channel_healthy": config["ready"] and config["mode"] != "mock",
        "whatsapp_diagnostics": {
            "provider_mode": config["mode"],
            "account_sid_configured": config["account_sid_configured"],
            "auth_token_configured": config["auth_token_configured"],
            "messaging_service_sid_configured": config["messaging_service_sid_configured"],
            "sender_configured": config["sender_configured"],
            "validate_signature": config["validate_signature"],
            "last_outbound_attempt": last_outbound,
            "last_twilio_sid": last_outbound.provider_message_sid if last_outbound else "",
            "last_twilio_status": last_outbound.provider_status if last_outbound else "",
            "last_twilio_error": last_outbound.error_message if last_outbound else "",
            "last_twilio_error_code": last_outbound.error_code if last_outbound else "",
            "last_status_callback_at": last_outbound.last_status_callback_at if last_outbound else None,
            "last_inbound": last_inbound,
            "last_inbound_sender": mask_secret(last_inbound.from_phone) if last_inbound else "",
            "last_status_callback": last_status_callback,
        },
        "twilio_wa_number": mask_secret(config["sender"]),
        "messaging_service_sid": mask_secret(config["messaging_service_sid"]),
        "webhook_url": display_whatsapp_url(whatsapp_webhook_path()),
        "status_callback_url": display_whatsapp_url(whatsapp_status_callback_path()),
        "wa_sent_today": wa_sent_today,
        "wa_failed_today": wa_failed_today,
        "wa_queued": wa_queued,
        "delivery_rate": delivery_rate,
        "last_inbound": last_inbound,
        "last_outbound": last_outbound,
        "conversation_count": conversation_count,
        "open_conversations": open_conversations,
        "open_tickets": open_tickets,
        "urgent_tickets": urgent_tickets,
        "overdue_sla": overdue_sla,
        "waiting_for_customer": waiting_for_customer,
        "resolved_today": resolved_today,
        "avg_first_response": avg_first_response,
        "recent_messages": recent_messages,
        "recent_tickets": recent_tickets,
        "escalations": escalations,
        "status_choices": SupportTicket.STATUS_CHOICES,
        "category_choices": SupportTicket.CATEGORY_CHOICES,
        "priority_choices": SupportTicket.PRIORITY_CHOICES,
        "templates": templates,
        "automations": [],
    })


# ─────────────────────────────────────────────────────────────────────────────
# HQ Emergency Payout Approvals
# ─────────────────────────────────────────────────────────────────────────────

@hq_required
def hq_emergency_payouts(request):
    """HQ view: list and process emergency payout requests from underwriters."""
    from earnings.models import EmergencyPayoutRequest

    pending = EmergencyPayoutRequest.objects.filter(
        status=EmergencyPayoutRequest.STATUS_PENDING,
    ).select_related("wallet__user").order_by("-created_at")

    approved = EmergencyPayoutRequest.objects.filter(
        status=EmergencyPayoutRequest.STATUS_APPROVED,
    ).select_related("wallet__user", "reviewed_by").order_by("-reviewed_at")[:20]

    paid_recent = EmergencyPayoutRequest.objects.filter(
        status=EmergencyPayoutRequest.STATUS_PAID,
    ).select_related("wallet__user", "reviewed_by").order_by("-paid_at")[:20]

    rejected_recent = EmergencyPayoutRequest.objects.filter(
        status=EmergencyPayoutRequest.STATUS_REJECTED,
    ).select_related("wallet__user", "reviewed_by").order_by("-reviewed_at")[:10]

    return render(request, "dashboard/hq_emergency_payouts.html", {
        "page_title": "Emergency Payout Approvals",
        "pending": pending,
        "approved": approved,
        "paid_recent": paid_recent,
        "rejected_recent": rejected_recent,
    })


@hq_required
@require_POST
def hq_emergency_payout_action(request, payout_id):
    """Approve, reject, or mark as paid an emergency payout request."""
    from earnings.models import EmergencyPayoutRequest

    payout = get_object_or_404(EmergencyPayoutRequest, id=payout_id)
    action = request.POST.get("action", "")
    note = request.POST.get("note", "").strip()
    payment_reference = request.POST.get("payment_reference", "").strip()
    payment_provider = request.POST.get("payment_provider", "").strip()

    # Prevent self-approval
    if payout.wallet.user == request.user:
        messages.error(request, "You cannot approve your own payout request.")
        return redirect("hq_emergency_payouts")

    now = timezone.now()

    if action == "approve":
        if payout.status != EmergencyPayoutRequest.STATUS_PENDING:
            messages.error(request, "Only pending requests can be approved.")
            return redirect("hq_emergency_payouts")
        # Enforce 20% limit
        emergency_info = EmergencyPayoutRequest.get_monthly_emergency_limit(payout.wallet)
        if payout.requested_amount > emergency_info["limit"]:
            messages.error(request, f"Request exceeds 20% emergency limit of {emergency_info['limit']:,.0f} MWK.")
            return redirect("hq_emergency_payouts")
        payout.status = EmergencyPayoutRequest.STATUS_APPROVED
        payout.reviewed_by = request.user
        payout.reviewed_at = now
        payout.review_note = note
        payout.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note", "updated_at"])
        messages.success(request, f"Emergency payout for {payout.wallet.user.get_full_name() or payout.wallet.user.username} approved.")

    elif action == "reject":
        if payout.status not in (EmergencyPayoutRequest.STATUS_PENDING, EmergencyPayoutRequest.STATUS_APPROVED):
            messages.error(request, "Cannot reject this request in its current state.")
            return redirect("hq_emergency_payouts")
        payout.status = EmergencyPayoutRequest.STATUS_REJECTED
        payout.reviewed_by = request.user
        payout.reviewed_at = now
        payout.review_note = note
        payout.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note", "updated_at"])
        messages.success(request, "Emergency payout request rejected.")

    elif action == "mark_paid":
        if payout.status != EmergencyPayoutRequest.STATUS_APPROVED:
            messages.error(request, "Only approved requests can be marked as paid.")
            return redirect("hq_emergency_payouts")
        if not payment_reference:
            messages.error(request, "A payment reference is required to mark as paid.")
            return redirect("hq_emergency_payouts")
        payout.status = EmergencyPayoutRequest.STATUS_PAID
        payout.payment_reference = payment_reference
        payout.payment_provider = payment_provider or "manual"
        payout.paid_at = now
        payout.reviewed_by = request.user
        payout.save(update_fields=[
            "status", "payment_reference", "payment_provider",
            "paid_at", "reviewed_by", "updated_at",
        ])
        # Debit wallet balance
        try:
            wallet = payout.wallet
            from earnings.models import WalletTransaction
            WalletTransaction.objects.create(
                wallet=wallet,
                transaction_type="payout_debit",
                amount=-payout.requested_amount,
                description=f"Emergency payout — ref {payment_reference}",
            )
            from django.db.models import F
            wallet.balance = max(
                Decimal("0"),
                (wallet.balance or Decimal("0")) - payout.requested_amount,
            )
            wallet.total_paid = (wallet.total_paid or Decimal("0")) + payout.requested_amount
            wallet.save(update_fields=["balance", "total_paid"])
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Wallet debit failed for emergency payout %s", payout.pk)

        # Notify underwriter
        try:
            from notifications.models import Notification
            Notification.send(
                recipient=payout.wallet.user,
                notification_type=Notification.TYPE_PAYMENT_RECEIVED,
                title="Emergency Payout Paid",
                body=f"Your emergency payout of MWK {payout.requested_amount:,.0f} has been paid. Ref: {payment_reference}.",
                link="/sales/emergency-payout/",
                level=Notification.LEVEL_SUCCESS,
            )
        except Exception:
            pass

        messages.success(request, f"Emergency payout of MWK {payout.requested_amount:,.0f} marked as paid.")

    else:
        messages.error(request, "Invalid action.")

    return redirect("hq_emergency_payouts")
