from django.contrib import admin

from .models import Contract


@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    list_display = (
        "contract_number", "customer_name", "merchant", "term_months",
        "status", "terms_confirmed_at", "created_at",
    )
    search_fields = ("contract_number", "customer_name", "national_id", "imei_number")
    list_filter = ("status", "term_months", "warranty_checked", "phone_locked", "deposit_paid")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        ("Contract", {
            "fields": (
                "application", "contract_number", "merchant",
                "customer_name", "customer_phone", "national_id",
                "deal_name", "imei_number", "status",
            )
        }),
        ("Finance", {
            "fields": (
                "term_months", "cash_price", "total_loan",
                "deposit_amount", "monthly_payment", "daily_payment",
            )
        }),
        ("Terms Review", {
            "fields": (
                "terms_accepted_by_merchant", "terms_accepted_at", "terms_accepted_ip",
                "terms_otp_code", "terms_otp_verified_at",
                "terms_opened_at", "terms_confirmed_at", "terms_confirmed_by", "terms_version",
            )
        }),
        ("Completion", {
            "fields": (
                "warranty_checked", "phone_locked", "deposit_paid",
                "active_at", "completed_at", "ownership_transfer_at",
            )
        }),
        ("PDFs", {
            "fields": (
                "initial_pdf", "contract_bundle_pdf",
                "completed_pdf", "completed_bundle_pdf",
            )
        }),
        ("Audit", {"fields": ("created_at", "updated_at")}),
    )
