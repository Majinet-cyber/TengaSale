from django.urls import path
from . import views

app_name = "payments"

urlpatterns = [
    # Overview
    path("", views.payment_center_home, name="home"),

    # Customer Collections
    path("collections/", views.collections_list, name="collections"),
    path("collections/<str:ref>/", views.transaction_detail, name="transaction_detail"),

    # Payout Batches
    path("batches/", views.payout_batches, name="payout_batches"),
    path("batches/create/", views.create_batch, name="create_batch"),
    path("batches/<str:batch_number>/", views.payout_batch_detail, name="payout_batch_detail"),
    path("batches/<str:batch_number>/approve/", views.payout_batch_approve, name="payout_batch_approve"),
    path("batches/<str:batch_number>/process/", views.payout_batch_process, name="payout_batch_process"),
    path("items/<int:item_id>/mark-paid/", views.payout_item_mark_paid, name="payout_item_mark_paid"),

    # Merchant Payouts
    path("merchant-payouts/", views.merchant_payouts, name="merchant_payouts"),

    # Underwriter Payouts
    path("underwriter-payouts/", views.underwriter_payouts, name="underwriter_payouts"),

    # Salaries
    path("salaries/", views.salaries, name="salaries"),
    path("salaries/generate/", views.generate_payroll, name="generate_payroll"),
    path("salaries/<int:salary_id>/approve/", views.approve_salary, name="approve_salary"),
    path("salaries/approve-all/", views.approve_all_salaries, name="approve_all_salaries"),

    # Spin Rewards
    path("spin-rewards/", views.spin_reward_payouts, name="spin_reward_payouts"),
    path("spin-rewards/<int:payout_id>/process/", views.spin_reward_process, name="spin_reward_process"),
    path("spin-rewards/<int:payout_id>/mark-paid/", views.spin_reward_mark_paid, name="spin_reward_mark_paid"),

    # Commission Rules
    path("commission-rules/", views.commission_rules, name="commission_rules"),
    path("commission-rules/save/", views.commission_rule_save, name="commission_rule_save"),
    path("commission-rules/<int:rule_id>/save/", views.commission_rule_save, name="commission_rule_edit"),

    # Settings / Audit
    path("settings/", views.payment_settings, name="settings"),
    path("audit-log/", views.audit_log, name="audit_log"),
]
