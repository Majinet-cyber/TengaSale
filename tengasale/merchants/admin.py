from django.contrib import admin
from .models import Merchant, MerchantAgreement


@admin.register(Merchant)
class MerchantAdmin(admin.ModelAdmin):
    list_display = ("business_name", "owner", "phone_number", "location", "is_active")
    list_filter = ("is_active",)
    search_fields = ("business_name", "owner__username", "phone_number")


@admin.register(MerchantAgreement)
class MerchantAgreementAdmin(admin.ModelAdmin):
    list_display = (
        "reference_number", "merchant", "signer_name", "status",
        "agreement_version", "signed_at", "created_at",
    )
    list_filter = ("status", "agreement_version")
    search_fields = ("reference_number", "signer_name", "merchant__business_name")
    readonly_fields = (
        "reference_number", "signed_at", "viewed_at", "ip_address",
        "user_agent", "created_at", "updated_at", "signature_image", "pdf_file",
    )
    actions = ["mark_active", "mark_suspended"]

    @admin.action(description="Mark selected agreements as Active")
    def mark_active(self, request, queryset):
        queryset.update(status=MerchantAgreement.STATUS_ACTIVE)

    @admin.action(description="Mark selected agreements as Suspended")
    def mark_suspended(self, request, queryset):
        queryset.update(status=MerchantAgreement.STATUS_SUSPENDED)