from django.contrib import admin
from .models import DeviceLockProfile, DeviceLockEvent, DeviceLockPolicy


@admin.register(DeviceLockPolicy)
class DeviceLockPolicyAdmin(admin.ModelAdmin):
    list_display = ["name", "is_active", "grace_period_days", "lock_after_days_overdue", "created_at"]
    list_filter = ["is_active"]
    search_fields = ["name"]


class DeviceLockEventInline(admin.TabularInline):
    model = DeviceLockEvent
    extra = 0
    readonly_fields = ["event_type", "status_before", "status_after", "triggered_by", "trigger_source", "error_message", "created_at"]
    can_delete = False
    max_num = 0


@admin.register(DeviceLockProfile)
class DeviceLockProfileAdmin(admin.ModelAdmin):
    list_display = [
        "imei", "customer_name", "lock_provider", "lock_status",
        "is_lockable", "is_released", "last_sync_at", "created_at",
    ]
    list_filter = ["lock_provider", "lock_status", "is_lockable", "is_released"]
    search_fields = ["imei", "customer_name", "customer_phone", "serial_number"]
    readonly_fields = ["created_at", "updated_at"]
    inlines = [DeviceLockEventInline]


@admin.register(DeviceLockEvent)
class DeviceLockEventAdmin(admin.ModelAdmin):
    list_display = [
        "lock_profile", "event_type", "status_before", "status_after",
        "triggered_by", "trigger_source", "created_at",
    ]
    list_filter = ["event_type", "trigger_source"]
    search_fields = ["lock_profile__imei", "error_message"]
    readonly_fields = ["created_at"]
