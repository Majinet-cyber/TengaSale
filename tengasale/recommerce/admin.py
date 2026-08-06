from django.contrib import admin

from .models import (
    DeviceInspection, DeviceIntake, OwnershipEvidence, RecommerceCommissionEvent,
    RecommerceCommissionPlan, RecommerceDisposition, RecommerceEvent,
    RecommerceInventoryItem, RecommerceLead, RecommercePayoutRequest, RecommerceQA,
    RecommerceSettlement, RecommerceTask, RefurbishmentWorkOrder, TradeInAgreement,
    ValuationOffer,
)

for model in (RecommerceLead, DeviceIntake, OwnershipEvidence, DeviceInspection, ValuationOffer, TradeInAgreement, RecommerceSettlement, RefurbishmentWorkOrder, RecommerceQA, RecommerceInventoryItem, RecommerceDisposition, RecommerceTask, RecommerceCommissionPlan, RecommerceCommissionEvent, RecommercePayoutRequest):
    admin.site.register(model)


@admin.register(RecommerceEvent)
class RecommerceEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "intake", "actor", "created_at")
    readonly_fields = ("actor", "intake", "event_type", "previous_values", "new_values", "reason", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
