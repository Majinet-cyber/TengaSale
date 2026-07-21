from django.contrib import admin
from .models import (
    CommissionRule, PayoutBatch, PayoutItem,
    SalarySchedule, SpinRewardPayout, PaymentApproval, PaymentAuditLog,
    AirtelTransaction, AirtelCallbackLog, AirtelEnquiryLog, USSDPaymentIntent, USSDSessionLog,
)


@admin.register(CommissionRule)
class CommissionRuleAdmin(admin.ModelAdmin):
    list_display = ["name", "earner_type", "percentage", "fixed_amount", "is_active", "created_at"]
    list_filter = ["earner_type", "is_active"]
    search_fields = ["name"]


class PayoutItemInline(admin.TabularInline):
    model = PayoutItem
    extra = 0
    readonly_fields = ["status", "paid_at", "paid_by"]


@admin.register(PayoutBatch)
class PayoutBatchAdmin(admin.ModelAdmin):
    list_display = ["batch_number", "payout_type", "status", "total_amount", "total_recipients", "created_at"]
    list_filter = ["payout_type", "status"]
    search_fields = ["batch_number"]
    readonly_fields = ["batch_number", "created_at", "updated_at"]
    inlines = [PayoutItemInline]


@admin.register(PayoutItem)
class PayoutItemAdmin(admin.ModelAdmin):
    list_display = ["recipient_name", "recipient_type", "amount", "payment_method", "status", "batch"]
    list_filter = ["recipient_type", "status", "payment_method"]
    search_fields = ["recipient_name", "recipient_phone"]
    readonly_fields = ["created_at", "updated_at", "paid_at"]


@admin.register(SalarySchedule)
class SalaryScheduleAdmin(admin.ModelAdmin):
    list_display = ["staff_name", "role", "salary_month", "base_salary", "net_pay", "status"]
    list_filter = ["status", "salary_month", "payment_method"]
    search_fields = ["staff_name", "phone_number"]
    readonly_fields = ["net_pay", "created_at", "updated_at", "approved_at", "paid_at"]


@admin.register(SpinRewardPayout)
class SpinRewardPayoutAdmin(admin.ModelAdmin):
    list_display = ["user", "amount", "phone_number", "payment_method", "status", "created_at"]
    list_filter = ["status", "payment_method"]
    search_fields = ["user__username", "phone_number"]
    readonly_fields = ["created_at", "updated_at", "paid_at"]


@admin.register(PaymentApproval)
class PaymentApprovalAdmin(admin.ModelAdmin):
    list_display = ["id", "status", "amount", "requested_by", "approved_by", "created_at"]
    list_filter = ["status"]
    readonly_fields = ["created_at", "updated_at", "approved_at"]


@admin.register(PaymentAuditLog)
class PaymentAuditLogAdmin(admin.ModelAdmin):
    list_display = ["action", "user", "transaction_ref", "created_at"]
    list_filter = ["action"]
    search_fields = ["action", "transaction_ref", "notes"]
    readonly_fields = ["created_at"]


@admin.register(AirtelTransaction)
class AirtelTransactionAdmin(admin.ModelAdmin):
    list_display = [
        "internal_reference",
        "environment",
        "masked_customer_msisdn",
        "amount",
        "currency",
        "purpose",
        "status",
        "airtel_money_id",
        "initiation_accepted_at",
        "callback_received_at",
        "repayment_posted",
        "duplicate_callback",
        "completed_at",
        "created_at",
        "last_enquiry_at",
        "reconciliation_required",
    ]
    list_filter = ["environment", "status", "purpose", "direction", "callback_verified", "repayment_posted", "reconciliation_required", "duplicate_callback", "created_at"]
    search_fields = [
        "internal_reference",
        "provider_reference",
        "airtel_money_id",
        "airtel_transaction_id",
        "airtel_reference_id",
        "customer_msisdn",
    ]
    readonly_fields = [
        "internal_reference",
        "raw_request",
        "raw_response",
        "initiation_response",
        "initiation_http_status",
        "raw_callback",
        "callback_received_at",
        "failure_reason",
        "repayment_posted",
        "duplicate_callback",
        "processed_success_at",
        "completed_at",
        "created_at",
        "updated_at",
        "last_enquiry_response",
        "last_enquiry_http_status",
        "last_enquiry_at",
        "last_enquiry_reference",
        "last_enquiry_path",
        "last_enquiry_status",
        "last_enquiry_error",
    ]

    @admin.display(description="MSISDN")
    def masked_customer_msisdn(self, obj):
        value = obj.customer_msisdn or ""
        return f"{value[:4]}***{value[-4:]}" if len(value) >= 8 else "***"


@admin.register(AirtelCallbackLog)
class AirtelCallbackLogAdmin(admin.ModelAdmin):
    list_display = ["id", "created_at", "source_ip", "request_id", "short_fingerprint", "extracted_status", "matched_identifier", "matched_field", "response_status", "processing_state", "duplicate", "signature_valid"]
    list_filter = ["processing_state", "response_status", "signature_valid", "processed", "duplicate", "created_at"]
    search_fields = ["id", "transaction__internal_reference", "matched_identifier", "provider_transaction_id", "request_id", "provider_request_id", "body_sha256", "source_ip", "extracted_status", "raw_body", "processing_error"]
    readonly_fields = [field.name for field in AirtelCallbackLog._meta.fields]

    @admin.display(description="Fingerprint")
    def short_fingerprint(self, obj):
        return obj.body_sha256[:12] if obj.body_sha256 else "not captured"


@admin.register(AirtelEnquiryLog)
class AirtelEnquiryLogAdmin(admin.ModelAdmin):
    list_display = ["transaction", "created_at", "provider_status", "http_status", "reference", "error_class"]
    list_filter = ["provider_status", "http_status", "error_class", "created_at"]
    search_fields = ["transaction__internal_reference", "reference", "path", "error_message"]
    readonly_fields = [field.name for field in AirtelEnquiryLog._meta.fields]


@admin.register(USSDPaymentIntent)
class USSDPaymentIntentAdmin(admin.ModelAdmin):
    list_display = (
        "phone_number",
        "contract_number",
        "amount",
        "status",
        "created_at",
        "provider_reference",
    )
    list_filter = ("status", "created_at")
    search_fields = ("phone_number", "contract_number", "session_id")
    readonly_fields = ("created_at", "updated_at")


@admin.register(USSDSessionLog)
class USSDSessionLogAdmin(admin.ModelAdmin):
    list_display = (
        "phone_number",
        "session_id",
        "service_code",
        "text",
        "created_at",
    )
    list_filter = ("created_at",)
    search_fields = ("phone_number", "session_id", "text")
