from django.contrib import admin

from .models import AppVersion, AppVersionSeen, AuditLog, BusinessSetting, QueueRule


@admin.register(AppVersion)
class AppVersionAdmin(admin.ModelAdmin):
    list_display = ["version", "title", "is_active", "created_at", "created_by"]
    list_filter = ["is_active"]
    readonly_fields = ["created_at"]
    search_fields = ["version", "title"]


@admin.register(AppVersionSeen)
class AppVersionSeenAdmin(admin.ModelAdmin):
    list_display = ["user", "version", "seen_at"]
    readonly_fields = ["seen_at"]


@admin.register(BusinessSetting)
class BusinessSettingAdmin(admin.ModelAdmin):
    list_display = (
        "merchant_commission_percent",
        "manager_commission_percent",
        "loan_multiplier",
        "spin_enabled",
        "updated_at",
    )
    readonly_fields = ("updated_at",)


@admin.register(QueueRule)
class QueueRuleAdmin(admin.ModelAdmin):
    list_display = (
        "country",
        "cooldown_minutes",
        "max_active_applications",
        "polling_interval_seconds",
        "inactive_pause_minutes",
        "waiting_edit_grace_minutes",
        "updated_at",
    )
    readonly_fields = ("updated_at",)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "timestamp", "action_display", "user",
        "object_type", "object_id", "ip_address",
    )
    list_filter = ("action", "object_type", "timestamp")
    search_fields = (
        "user__username", "object_id",
        "object_type", "ip_address",
        "detail",
    )
    readonly_fields = (
        "user", "action", "object_type",
        "object_id", "detail", "ip_address", "timestamp",
    )
    ordering = ("-timestamp",)
    date_hierarchy = "timestamp"

    def action_display(self, obj):
        return obj.get_action_display()
    action_display.short_description = "Action"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
