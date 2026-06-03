from django.contrib import admin

from .models import PhoneOTP, SMSLog


@admin.register(SMSLog)
class SMSLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "phone_number", "purpose", "status", "language", "provider", "sent_at")
    list_filter = ("purpose", "status", "language", "provider", "created_at")
    search_fields = (
        "phone_number",
        "message",
        "provider_message_id",
        "payment_contract__contract_number",
        "payment_contract__customer_name",
        "contract__contract_number",
        "application__application_number",
        "application__customer_name",
    )
    readonly_fields = ("created_at", "sent_at")
    date_hierarchy = "created_at"


@admin.register(PhoneOTP)
class PhoneOTPAdmin(admin.ModelAdmin):
    list_display = ("created_at", "phone_number", "application", "is_verified", "attempts", "expires_at")
    list_filter = ("is_verified", "created_at", "expires_at")
    search_fields = ("phone_number", "otp_code", "application__application_number", "application__customer_name")
    readonly_fields = ("created_at",)
