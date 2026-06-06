from calendar import monthrange
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from core.models import AuditLog

from .models import FounderEquityRecord, PenaltyTransaction, UserProfile, VoltsTransaction


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
    if user_can_access_module(user, MODULE_VOLTS) and transaction:
        return profile.department_id and profile.department_id == transaction.department_id
    return False


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
    ceiling = getattr(getattr(profile, "rank", None), "monthly_ceiling_mwk", None)
    capped_pay = min(estimated_pay, ceiling) if ceiling is not None else estimated_pay
    return {
        "approved_volts": approved_volts,
        "pending_volts": pending_volts,
        "penalty_volts": penalty_volts,
        "estimated_pay": estimated_pay,
        "capped_pay": capped_pay,
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
    return {
        "profile": profile,
        "allowed_modules": visible_hq_modules(user),
        "volts": volts,
        "equity": equity,
        "department_score": department_score(user),
        "pending_responsibilities": volts["transactions"].filter(status=VoltsTransaction.STATUS_PENDING).count(),
        "penalties_count": volts["penalties"].filter(status=PenaltyTransaction.STATUS_APPROVED).count(),
    }


def audit_sensitive_action(user, action, obj, detail=None):
    AuditLog.objects.create(
        user=user,
        action=action,
        object_type=obj.__class__.__name__,
        object_id=str(obj.pk),
        detail=detail or {},
    )
