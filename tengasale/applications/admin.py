from django.contrib import admin
from django.utils.html import format_html

from .models import DiditWebhookEvent, FinancingApplication


@admin.register(FinancingApplication)
class FinancingApplicationAdmin(admin.ModelAdmin):
    list_display = (
        "application_number",
        "customer_name",
        "customer_phone",
        "deal",
        "calculated_total_loan",
        "created_by",
        "reviewed_by",
        "status_badge",
        "submitted_at",
        "reviewed_at",
    )
    list_filter = (
        "status",
        "review_status",
        "gender",
        "marital_status",
        "phone_user",
        "third_party_phone_user_risk_flagged",
        "region",
        "deal__brand",
        "created_at",
        "submitted_at",
    )
    search_fields = (
        "application_number",
        "customer_name",
        "customer_phone",
        "national_id",
        "imei_number",
        "created_by__username",
        "reviewed_by__username",
    )
    readonly_fields = (
        "application_number",
        "created_at",
        "submitted_at",
        "reviewed_at",
        "didit_session_id",
        "didit_session_token",
        "didit_verification_url",
        "didit_decision",
        "didit_summary",
        "didit_metadata",
        "didit_last_event_id",
        "didit_started_at",
        "didit_completed_at",
        "didit_verified_at",
        "didit_declined_at",
    )
    fieldsets = (
        (None, {
            "fields": (
                "application_number",
                "customer_name",
                "customer_phone",
                "national_id",
                "status",
                "review_status",
                "created_by",
                "deal",
            ),
        }),
        ("Didit KYC", {
            "fields": (
                "kyc_provider",
                "kyc_status",
                "didit_session_id",
                "didit_verification_url",
                "didit_status",
                "didit_workflow_id",
                "didit_vendor_data",
                "didit_started_at",
                "didit_completed_at",
                "didit_verified_at",
                "didit_declined_at",
                "didit_manual_override",
                "didit_manual_override_reason",
                "didit_manual_override_by",
                "didit_manual_override_at",
                "didit_metadata",
                "didit_summary",
                "didit_decision",
                "didit_resubmit_info",
                "didit_last_event_id",
            ),
            "classes": ("collapse",),
        }),
    )
    ordering = ("-submitted_at",)
    date_hierarchy = "submitted_at"

    def status_badge(self, obj):
        colours = {
            "draft": "#7a828c",
            "pending_review": "#ff6a00",
            "under_review": "#0277bd",
            "approved": "#168a45",
            "rejected": "#d93025",
            "sent_back": "#8b5cf6",
            "completed": "#0d47a1",
            "contract_complete": "#0d47a1",
        }
        colour = colours.get(obj.status, "#7a828c")
        return format_html(
            '<span style="display:inline-block;padding:2px 8px;border-radius:6px;'
            'background:{colour};color:#fff;font-size:11px;font-weight:700;">{label}</span>',
            colour=colour,
            label=obj.get_status_display(),
        )
    status_badge.short_description = "Status"


@admin.register(DiditWebhookEvent)
class DiditWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("received_at", "event_id", "session_id", "webhook_type", "status", "processed")
    list_filter = ("webhook_type", "status", "processed", "received_at")
    search_fields = ("event_id", "session_id")
    readonly_fields = (
        "event_id",
        "session_id",
        "webhook_type",
        "status",
        "received_at",
        "payload",
        "processed",
        "processing_error",
    )
    ordering = ("-received_at",)
    date_hierarchy = "received_at"
