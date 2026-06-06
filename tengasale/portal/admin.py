from django.contrib import admin
from django.utils.html import format_html

from .models import PaymentContract, PaymentTransaction, RecoveryCost
from .payment_providers import get_integration_status


@admin.register(PaymentContract)
class PaymentContractAdmin(admin.ModelAdmin):
    list_display = (
        "contract_number", "payg_number", "customer_name",
        "masked_phone_display", "device_model",
        "total_amount", "amount_paid", "remaining_display",
        "progress_bar", "status_badge", "due_date",
        "lock_status_badge", "created_at",
    )
    list_filter = (
        "status",
        "term_months",
        "device_lock_provider",
        "device_lock_status",
        "device_enrollment_status",
        "created_at",
    )
    search_fields = (
        "contract_number", "payg_number",
        "customer_name", "customer_phone",
        "customer_national_id", "device_model",
    )
    readonly_fields = (
        "contract_number", "payg_number",
        "progress_percent", "remaining_amount",
        "created_at", "updated_at",
    )
    ordering = ("-created_at",)
    date_hierarchy = "created_at"

    fieldsets = (
        ("Contract Identity", {
            "fields": ("contract_number", "payg_number", "status"),
        }),
        ("Customer", {
            "fields": (
                "customer_name", "customer_phone",
                "customer_national_id", "device_model",
            ),
        }),
        ("Financials", {
            "fields": (
                "total_amount", "deposit_paid", "amount_paid",
                "remaining_amount", "progress_percent",
                "daily_price", "thirty_day_price", "term_months",
            ),
        }),
        ("Dates", {
            "fields": ("start_date", "due_date", "lock_date"),
        }),
        ("Device Lock", {
            "fields": (
                "device_lock_provider",
                "device_lock_status",
                "device_enrollment_status",
                "last_lock_sync_at",
                "last_lock_error",
            ),
            "classes": ("collapse",),
        }),
        ("Early Settlement Discounts", {
            "fields": (
                "early_settlement_3m_discount",
                "early_settlement_6m_discount",
                "early_settlement_9m_discount",
            ),
            "classes": ("collapse",),
        }),
        ("Links", {
            "fields": ("financing_contract",),
            "classes": ("collapse",),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )

    def masked_phone_display(self, obj):
        return obj.masked_phone
    masked_phone_display.short_description = "Phone"

    def remaining_display(self, obj):
        return f"MWK {obj.remaining_amount:,.0f}"
    remaining_display.short_description = "Remaining"

    def status_badge(self, obj):
        colours = {
            "active": "#168a45",
            "overdue": "#d93025",
            "locked": "#c62828",
            "completed": "#0d47a1",
            "cancelled": "#7a828c",
        }
        colour = colours.get(obj.status, "#7a828c")
        return format_html(
            '<span style="display:inline-block;padding:3px 10px;border-radius:999px;'
            'background:{colour};color:#fff;font-size:11px;font-weight:700;">{label}</span>',
            colour=colour,
            label=obj.get_status_display(),
        )
    status_badge.short_description = "Status"

    def lock_status_badge(self, obj):
        if not obj.device_lock_provider:
            return "—"
        colours = {
            "unlocked": "#168a45",
            "locked": "#d93025",
            "pending": "#ff6a00",
            "unknown": "#7a828c",
        }
        colour = colours.get(obj.device_lock_status, "#7a828c")
        return format_html(
            '<span style="display:inline-block;padding:2px 8px;border-radius:6px;'
            'background:{colour};color:#fff;font-size:10px;font-weight:700;">{label}</span>',
            colour=colour,
            label=obj.device_lock_status or "unknown",
        )
    lock_status_badge.short_description = "Lock"

    def progress_bar(self, obj):
        pct = obj.progress_percent
        colour = "#168a45" if pct >= 80 else "#ff6a00" if pct >= 40 else "#d93025"
        return format_html(
            '<div style="width:100px;background:#eee;border-radius:4px;overflow:hidden;">'
            '<div style="width:{pct}%;background:{colour};height:12px;"></div>'
            '</div> {pct}%',
            pct=pct, colour=colour,
        )
    progress_bar.short_description = "Progress"


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "internal_reference", "contract_number_display",
        "provider_badge", "amount", "currency",
        "masked_phone_display", "status_badge",
        "paid_at", "created_at",
    )
    list_filter = ("status", "provider", "currency", "created_at")
    search_fields = (
        "internal_reference", "provider_reference",
        "phone", "payment_contract__contract_number",
        "payment_contract__payg_number",
        "payment_contract__customer_name",
    )
    readonly_fields = ("internal_reference", "raw_response", "created_at", "updated_at")
    ordering = ("-created_at",)
    date_hierarchy = "created_at"

    def contract_number_display(self, obj):
        return obj.payment_contract.contract_number
    contract_number_display.short_description = "Contract"

    def masked_phone_display(self, obj):
        return obj.masked_phone
    masked_phone_display.short_description = "Phone"

    def status_badge(self, obj):
        colours = {
            "paid": "#168a45",
            "pending": "#ff6a00",
            "external_processing": "#0277bd",
            "failed": "#d93025",
            "cancelled": "#7a828c",
        }
        colour = colours.get(obj.status, "#7a828c")
        return format_html(
            '<span style="display:inline-block;padding:2px 8px;border-radius:6px;'
            'background:{colour};color:#fff;font-size:11px;font-weight:700;">{label}</span>',
            colour=colour,
            label=obj.get_status_display(),
        )
    status_badge.short_description = "Status"

    def provider_badge(self, obj):
        labels = {
            "mock": "Mock",
            "paychangu": "PayChangu",
            "airtel_money": "Airtel",
            "tnm_mpamba": "TNM",
            "paytrigger": "PayTrigger",
        }
        return labels.get(obj.provider, obj.provider)
    provider_badge.short_description = "Provider"


@admin.register(RecoveryCost)
class RecoveryCostAdmin(admin.ModelAdmin):
    list_display = (
        "contract", "cost_type", "amount", "is_chargeable_to_customer",
        "approved_at", "recorded_by", "incurred_at",
    )
    list_filter = ("cost_type", "is_chargeable_to_customer", "approved_at", "incurred_at")
    search_fields = (
        "contract__contract_number", "contract__payg_number",
        "contract__customer_name", "description",
    )
    readonly_fields = ("created_at", "updated_at")
    ordering = ("-incurred_at", "-created_at")
