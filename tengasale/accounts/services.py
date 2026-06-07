from calendar import monthrange
from decimal import Decimal

from django.db.models import Avg, Count, Sum
from django.utils import timezone

from core.models import AuditLog

from .models import (
    DisciplineEvent,
    DisciplineScorePeriod,
    FounderEquityRecord,
    KPIResult,
    PenaltyTransaction,
    StaffDocument,
    UserProfile,
    VoltsTransaction,
)


MODULE_DASHBOARD = "dashboard"
MODULE_APPLICATIONS = "applications"
MODULE_CONTRACTS = "contracts"
MODULE_PAYMENTS = "payments"
MODULE_UNDERWRITING = "underwriting"
MODULE_MERCHANTS = "merchants"
MODULE_MERCHANT_ADMIN = "merchant_admin"
MODULE_COLLECTIONS = "collections"
MODULE_LEGAL_RECOVERY = "legal_recovery"
MODULE_SALES_LEADS = "sales_leads"
MODULE_ANALYTICS = "analytics"
MODULE_VOLTS = "volts"
MODULE_EQUITY = "equity"
MODULE_STAFF_ROLES = "staff_roles"
MODULE_SETTINGS = "settings"
MODULE_AUDIT_LOGS = "audit_logs"
MODULE_TECH = "tech"
MODULE_DISCIPLINE = "discipline"
MODULE_KPIS = "kpis"
MODULE_PAYOUTS = "payouts"
MODULE_STAFF_DOCUMENTS = "staff_documents"

ALL_HQ_MODULES = [
    MODULE_DASHBOARD,
    MODULE_APPLICATIONS,
    MODULE_CONTRACTS,
    MODULE_PAYMENTS,
    MODULE_UNDERWRITING,
    MODULE_MERCHANTS,
    MODULE_MERCHANT_ADMIN,
    MODULE_COLLECTIONS,
    MODULE_LEGAL_RECOVERY,
    MODULE_SALES_LEADS,
    MODULE_ANALYTICS,
    MODULE_VOLTS,
    MODULE_EQUITY,
    MODULE_STAFF_ROLES,
    MODULE_SETTINGS,
    MODULE_AUDIT_LOGS,
    MODULE_TECH,
    MODULE_DISCIPLINE,
    MODULE_KPIS,
    MODULE_PAYOUTS,
    MODULE_STAFF_DOCUMENTS,
]

MODULE_LABELS = {
    MODULE_DASHBOARD: "Dashboard",
    MODULE_APPLICATIONS: "Applications",
    MODULE_CONTRACTS: "Contracts",
    MODULE_PAYMENTS: "Payments",
    MODULE_UNDERWRITING: "Underwriting",
    MODULE_MERCHANTS: "Merchants",
    MODULE_MERCHANT_ADMIN: "Merchant Admin",
    MODULE_COLLECTIONS: "Collections",
    MODULE_LEGAL_RECOVERY: "Legal & Recovery",
    MODULE_SALES_LEADS: "Sales & Leads",
    MODULE_ANALYTICS: "Analytics",
    MODULE_VOLTS: "Volts Engine",
    MODULE_EQUITY: "Founder Equity",
    MODULE_STAFF_ROLES: "Staff & Roles",
    MODULE_SETTINGS: "Settings",
    MODULE_AUDIT_LOGS: "Audit Logs",
    MODULE_TECH: "Technology",
    MODULE_DISCIPLINE: "Discipline Governance",
    MODULE_KPIS: "KPI Scorecards",
    MODULE_PAYOUTS: "Payout Approvals",
    MODULE_STAFF_DOCUMENTS: "Staff Documents",
}


def _profile(user):
    try:
        return user.profile
    except Exception:
        return None


def is_ceo_or_super_admin(user):
    profile = _profile(user)
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    role_name = getattr(getattr(profile, "staff_role", None), "name", "")
    return role_name == "CEO / Strategy Lead"


def is_finance_risk_lead(user):
    profile = _profile(user)
    role_name = getattr(getattr(profile, "staff_role", None), "name", "")
    return role_name == "Finance, Risk & Administration Lead"


def is_legal_lead(user):
    profile = _profile(user)
    role_name = getattr(getattr(profile, "staff_role", None), "name", "")
    return role_name == "Legal & Compliance Lead"


def is_department_head(user):
    profile = _profile(user)
    if not profile or not profile.staff_role:
        return False
    return "Lead" in profile.staff_role.name or profile.can_approve_discipline or profile.can_approve_volts


def user_allowed_modules(user):
    profile = _profile(user)
    if is_ceo_or_super_admin(user):
        return set(ALL_HQ_MODULES)
    if not profile:
        return set()
    if profile.staff_role and profile.staff_role.module_permissions:
        return set(profile.staff_role.module_permissions)
    if profile.role == UserProfile.ROLE_MERCHANT_ADMIN:
        return {MODULE_MERCHANT_ADMIN, MODULE_MERCHANTS}
    if profile.role == UserProfile.ROLE_UNDERWRITER:
        return {MODULE_UNDERWRITING, MODULE_APPLICATIONS}
    if profile.role == UserProfile.ROLE_TECH_SUPPORT:
        return {MODULE_TECH, MODULE_AUDIT_LOGS}
    if profile.role == UserProfile.ROLE_HQ:
        return {MODULE_DASHBOARD, MODULE_ANALYTICS, MODULE_AUDIT_LOGS}
    return set()


def user_can_access_module(user, module):
    return module in user_allowed_modules(user)


def visible_hq_modules(user):
    allowed = user_allowed_modules(user)
    return [
        {"key": key, "label": MODULE_LABELS[key]}
        for key in ALL_HQ_MODULES
        if key in allowed
    ]


def can_edit_equity(user):
    return is_ceo_or_super_admin(user) or user_can_access_module(user, MODULE_EQUITY)


def can_approve_volts(user, transaction=None):
    if not user or not user.is_authenticated:
        return False
    if transaction and transaction.user_id == user.id:
        return False
    if is_ceo_or_super_admin(user):
        return True
    profile = _profile(user)
    if not profile:
        return False
    if (user_can_access_module(user, MODULE_VOLTS) or profile.can_approve_volts) and transaction:
        return profile.department_id and profile.department_id == transaction.department_id
    return False


def can_view_compensation(viewer, target_user):
    if not viewer or not viewer.is_authenticated:
        return False
    if viewer.id == target_user.id:
        return True
    if is_ceo_or_super_admin(viewer) or is_finance_risk_lead(viewer):
        return True
    return user_can_access_module(viewer, MODULE_PAYOUTS)


def can_view_equity(viewer, target_user=None):
    if not viewer or not viewer.is_authenticated:
        return False
    if is_ceo_or_super_admin(viewer) or user_can_access_module(viewer, MODULE_EQUITY):
        return True
    if target_user and viewer.id == target_user.id:
        return FounderEquityRecord.objects.filter(founder_user=target_user).exists()
    return False


def can_review_discipline(reviewer, period_or_user):
    if not reviewer or not reviewer.is_authenticated:
        return False
    target_user = getattr(period_or_user, "user", period_or_user)
    if getattr(target_user, "id", None) == reviewer.id:
        return False
    if is_ceo_or_super_admin(reviewer):
        return True
    reviewer_profile = _profile(reviewer)
    target_profile = _profile(target_user)
    if not reviewer_profile or not target_profile:
        return False
    if reviewer_profile.can_approve_discipline or user_can_access_module(reviewer, MODULE_DISCIPLINE):
        return reviewer_profile.department_id and reviewer_profile.department_id == target_profile.department_id
    return False


def can_approve_payout_impact(approver, period):
    if not approver or not approver.is_authenticated or period.user_id == approver.id:
        return False
    if is_ceo_or_super_admin(approver):
        return True
    profile = _profile(approver)
    return bool(profile and (profile.can_approve_payouts or is_finance_risk_lead(approver)))


def approve_discipline_period(period, reviewer, reason=""):
    if not can_review_discipline(reviewer, period):
        raise ValueError("You cannot approve this discipline score.")
    period.approve(reviewer)
    audit_sensitive_action(
        reviewer,
        "discipline_score_approved",
        period,
        detail={"reason": reason, "final_score": str(period.final_score)},
        reason=reason,
    )
    return period


def approve_payout_impact(period, approver, reason=""):
    if not can_approve_payout_impact(approver, period):
        raise ValueError("You cannot approve this payout impact.")
    period.payout_impact_approved_by = approver
    period.payout_impact_approved_at = timezone.now()
    period.save(update_fields=["payout_impact_approved_by", "payout_impact_approved_at", "updated_at"])
    audit_sensitive_action(
        approver,
        "payout_impact_approved",
        period,
        detail={"reason": reason, "final_score": str(period.final_score)},
        reason=reason,
    )
    return period


def current_month_range():
    today = timezone.localdate()
    start = today.replace(day=1)
    end = today.replace(day=monthrange(today.year, today.month)[1])
    return start, end


def monthly_volts_summary(user, start=None, end=None):
    start = start or current_month_range()[0]
    end = end or current_month_range()[1]
    transactions = VoltsTransaction.objects.filter(user=user, created_at__date__gte=start, created_at__date__lte=end)
    penalties = PenaltyTransaction.objects.filter(user=user, created_at__date__gte=start, created_at__date__lte=end)
    approved_volts = transactions.filter(status__in=[VoltsTransaction.STATUS_APPROVED, VoltsTransaction.STATUS_PAID]).aggregate(
        total=Sum("final_volts")
    )["total"] or Decimal("0")
    pending_volts = transactions.filter(status=VoltsTransaction.STATUS_PENDING).aggregate(total=Sum("final_volts"))["total"] or Decimal("0")
    estimated_pay = transactions.filter(status__in=[VoltsTransaction.STATUS_APPROVED, VoltsTransaction.STATUS_PAID]).aggregate(
        total=Sum("estimated_amount_mwk")
    )["total"] or Decimal("0")
    penalty_volts = penalties.filter(status=PenaltyTransaction.STATUS_APPROVED).aggregate(total=Sum("volts_deducted"))["total"] or Decimal("0")
    profile = _profile(user)
    base_salary = getattr(profile, "effective_base_salary_mwk", Decimal("0")) if profile else Decimal("0")
    ceiling = getattr(profile, "effective_monthly_ceiling_mwk", None) if profile else None
    capped_pay = min(estimated_pay, ceiling) if ceiling is not None else estimated_pay
    monthly_pay = base_salary + estimated_pay
    final_monthly_pay = min(monthly_pay, ceiling) if ceiling is not None else monthly_pay
    ceiling_adjustment = max(monthly_pay - final_monthly_pay, Decimal("0"))
    return {
        "approved_volts": approved_volts,
        "pending_volts": pending_volts,
        "penalty_volts": penalty_volts,
        "estimated_pay": estimated_pay,
        "capped_pay": capped_pay,
        "base_salary": base_salary,
        "monthly_pay": monthly_pay,
        "final_monthly_pay": final_monthly_pay,
        "ceiling_adjustment": ceiling_adjustment,
        "monthly_ceiling": ceiling,
        "transactions": transactions,
        "penalties": penalties,
    }


def department_score(user):
    profile = _profile(user)
    if not profile or not profile.department_id:
        return 0
    start, end = current_month_range()
    department_transactions = VoltsTransaction.objects.filter(
        department=profile.department,
        created_at__date__gte=start,
        created_at__date__lte=end,
    )
    approved = department_transactions.filter(status__in=[VoltsTransaction.STATUS_APPROVED, VoltsTransaction.STATUS_PAID]).count()
    total = department_transactions.count()
    return round((approved / total) * 100) if total else 0


def founder_staff_dashboard_context(user):
    profile = _profile(user)
    volts = monthly_volts_summary(user)
    equity = FounderEquityRecord.objects.filter(founder_user=user).first()
    discipline = DisciplineScorePeriod.objects.filter(user=user).order_by("-month").first()
    kpis = KPIResult.objects.filter(user=user).select_related("kpi_template").order_by("-period", "kpi_template__name")[:8]
    documents = StaffDocument.objects.filter(user=user).order_by("-generated_at")[:10]
    return {
        "profile": profile,
        "allowed_modules": visible_hq_modules(user),
        "volts": volts,
        "equity": equity,
        "discipline": discipline,
        "kpis": kpis,
        "documents": documents,
        "department_score": department_score(user),
        "pending_responsibilities": volts["transactions"].filter(status=VoltsTransaction.STATUS_PENDING).count(),
        "penalties_count": volts["penalties"].filter(status=PenaltyTransaction.STATUS_APPROVED).count(),
    }


def governance_dashboard_context():
    periods = DisciplineScorePeriod.objects.select_related("user", "user__profile", "user__profile__department")
    events = DisciplineEvent.objects.select_related("event_type", "user", "user__profile__department")
    kpis = KPIResult.objects.select_related("kpi_template", "user", "user__profile__department")
    return {
        "discipline_periods": periods.order_by("-month")[:80],
        "discipline_events": events.order_by("-created_at")[:80],
        "pending_disputes": periods.filter(dispute_status__in=[
            DisciplineScorePeriod.DISPUTE_SUBMITTED,
            DisciplineScorePeriod.DISPUTE_UNDER_REVIEW,
        ]).count(),
        "pending_discipline_reviews": periods.filter(status=DisciplineScorePeriod.STATUS_PENDING_REVIEW).count(),
        "score_distribution": periods.values("status").annotate(count=Count("id")).order_by("status"),
        "department_discipline": periods.values("user__profile__department__name").annotate(avg_score=Avg("final_score"), count=Count("id")).order_by("user__profile__department__name"),
        "kpi_results": kpis.order_by("-period")[:80],
        "avg_kpi_score": kpis.aggregate(avg=Avg("score"))["avg"] or Decimal("0"),
        "pending_volts": VoltsTransaction.objects.filter(status=VoltsTransaction.STATUS_PENDING).count(),
        "pending_payout_approvals": periods.filter(payout_impact_approved_at__isnull=True, status=DisciplineScorePeriod.STATUS_APPROVED).count(),
    }


def audit_sensitive_action(user, action, obj, detail=None, old_value=None, new_value=None, reason="", request=None):
    target_model = obj.__class__.__name__
    target_id = str(obj.pk)
    AuditLog.objects.create(
        user=user,
        action=action,
        object_type=target_model,
        object_id=target_id,
        target_model=target_model,
        target_id=target_id,
        detail=detail or {},
        old_value=old_value or {},
        new_value=new_value or {},
        reason=reason or "",
        ip_address=request.META.get("REMOTE_ADDR") if request else None,
        user_agent=request.META.get("HTTP_USER_AGENT", "") if request else "",
    )
