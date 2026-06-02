from django.urls import path
from django.views.generic import RedirectView

from . import views

urlpatterns = [
    path("home/", views.home, name="home"),
    path("home/", views.home, name="home_redirect"),
    path("merchant/", RedirectView.as_view(pattern_name="merchant_dashboard", permanent=False), name="merchant_shortcut"),
    path("underwriter/", RedirectView.as_view(pattern_name="underwriter_dashboard", permanent=False), name="underwriter_shortcut"),
    path("hq/", RedirectView.as_view(pattern_name="hq_dashboard", permanent=False), name="hq_shortcut"),
    path("tengasale/merchant/", views.merchant_dashboard, name="merchant_dashboard"),

    # ── HQ routes ──────────────────────────────────────────────────────────────
    path("tengasale/hq/", views.hq_dashboard, name="hq_dashboard"),
    path("tengasale/hq/users/", views.hq_users, name="hq_users"),
    path("tengasale/hq/users/<int:user_id>/", views.hq_user_edit, name="hq_user_edit"),
    path("tengasale/hq/deals/", views.hq_deals, name="hq_deals"),
    path("tengasale/hq/applications/", views.hq_applications, name="hq_applications"),
    path("tengasale/hq/underwriter-queue/", views.hq_underwriter_queue, name="hq_underwriter_queue"),
    path("tengasale/hq/commissions/", views.hq_commissions, name="hq_commissions"),
    path("tengasale/hq/commission-ledger/", views.hq_commission_ledger, name="hq_commission_ledger"),
    path("tengasale/hq/merchant-payouts/", views.hq_merchant_payouts, name="hq_merchant_payouts"),
    path("tengasale/hq/reports/", views.hq_reports, name="hq_reports"),
    path("tengasale/hq/simulations/", views.hq_simulations, name="hq_simulations"),
    path("tengasale/hq/operations/", views.hq_operations, name="hq_operations"),
    path("tengasale/hq/safe-operations/", views.hq_safe_operations, name="hq_safe_operations"),
    path("tengasale/hq/devices/", views.hq_devices, name="hq_devices"),
    path("tengasale/hq/staff-payouts/", views.hq_staff_payouts, name="hq_staff_payouts"),
    path("tengasale/hq/auto-approval/", views.hq_auto_approval, name="hq_auto_approval"),
    path("tengasale/hq/payment-collections/", views.hq_payment_collections, name="hq_payment_collections"),
    path("tengasale/hq/reconciliation/", views.hq_reconciliation, name="hq_reconciliation"),
    path("tengasale/hq/sales-analytics/", views.hq_sales_analytics, name="hq_sales_analytics"),
    path("tengasale/hq/underwriter-performance/", views.hq_underwriter_performance, name="hq_underwriter_performance"),
    path("tengasale/hq/fraud-checks/", views.hq_fraud_checks, name="hq_fraud_checks"),
    path("tengasale/hq/audit-trail/", views.hq_audit_trail, name="hq_audit_trail"),
    path("tengasale/hq/payment-intelligence/", views.hq_payment_intelligence, name="hq_payment_intelligence"),
    path("tengasale/hq/preview/merchant/", views.hq_merchant_preview, name="hq_merchant_preview"),
    path("tengasale/hq/preview/underwriter/", views.hq_underwriter_preview, name="hq_underwriter_preview"),

    # ── Short /hq/ aliases ─────────────────────────────────────────────────────
    path("hq/", RedirectView.as_view(pattern_name="hq_dashboard", permanent=False), name="hq_alias"),
    path("hq/commissions/", RedirectView.as_view(pattern_name="hq_commissions", permanent=False), name="hq_commissions_alias"),
    path("hq/commission-ledger/", RedirectView.as_view(pattern_name="hq_commission_ledger", permanent=False), name="hq_commission_ledger_alias"),
    path("hq/merchant-payouts/", RedirectView.as_view(pattern_name="hq_merchant_payouts", permanent=False), name="hq_merchant_payouts_alias"),
    path("hq/reports/", RedirectView.as_view(pattern_name="hq_reports", permanent=False), name="hq_reports_alias"),
    path("hq/simulations/", RedirectView.as_view(pattern_name="hq_simulations", permanent=False), name="hq_simulations_alias"),
    path("hq/operations/", RedirectView.as_view(pattern_name="hq_operations", permanent=False), name="hq_operations_alias"),
    path("hq/safe-operations/", RedirectView.as_view(pattern_name="hq_safe_operations", permanent=False), name="hq_safe_operations_alias"),
    path("hq/devices/", RedirectView.as_view(pattern_name="hq_devices", permanent=False), name="hq_devices_alias"),
    path("hq/staff-payouts/", RedirectView.as_view(pattern_name="hq_staff_payouts", permanent=False), name="hq_staff_payouts_alias"),
    path("hq/auto-approval/", RedirectView.as_view(pattern_name="hq_auto_approval", permanent=False), name="hq_auto_approval_alias"),
]
