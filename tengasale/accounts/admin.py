from django.contrib import admin
from django.contrib.admin.sites import NotRegistered
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from .models import (
    CompanyShareStructure,
    Department,
    FounderEquityRecord,
    PenaltyTransaction,
    PenaltyType,
    Rank,
    StaffRole,
    UserProfile,
    VoltsActionType,
    VoltsTransaction,
)


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    fk_name = "user"
    can_delete = False
    extra = 0
    fields = (
        "role",
        "phone_number",
        "department",
        "staff_role",
        "rank",
        "supervisor",
        "user_type",
        "status",
        "is_founder",
        "date_joined_company",
        "avatar_initials",
    )


class UserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline,)


try:
    admin.site.unregister(User)
except NotRegistered:
    pass
admin.site.register(User, UserAdmin)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "staff_role", "department", "rank", "user_type", "status", "is_founder")
    list_filter = ("role", "staff_role", "department", "rank", "user_type", "status", "is_founder")
    search_fields = ("user__username", "user__email", "phone_number", "user__first_name", "user__last_name")


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "active")
    list_filter = ("active",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Rank)
class RankAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "multiplier", "monthly_ceiling_mwk", "active")
    list_filter = ("active",)
    search_fields = ("code", "title")


@admin.register(StaffRole)
class StaffRoleAdmin(admin.ModelAdmin):
    list_display = ("name", "department", "portal_role", "active")
    list_filter = ("department", "portal_role", "active")
    search_fields = ("name", "code")


@admin.register(VoltsActionType)
class VoltsActionTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "department", "base_volts", "requires_approval", "active")
    list_filter = ("department", "requires_approval", "active")
    search_fields = ("name",)


@admin.register(VoltsTransaction)
class VoltsTransactionAdmin(admin.ModelAdmin):
    list_display = ("user", "action_type", "department", "final_volts", "estimated_amount_mwk", "status", "approved_by", "created_at")
    list_filter = ("status", "department", "action_type")
    search_fields = ("user__username", "user__first_name", "user__last_name", "action_type__name", "evidence_text")
    readonly_fields = ("final_volts", "estimated_amount_mwk", "created_at", "updated_at")


@admin.register(PenaltyType)
class PenaltyTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "default_volts_deducted", "active")
    list_filter = ("active",)
    search_fields = ("name",)


@admin.register(PenaltyTransaction)
class PenaltyTransactionAdmin(admin.ModelAdmin):
    list_display = ("user", "penalty_type", "volts_deducted", "status", "approved_by", "created_at")
    list_filter = ("status", "penalty_type")
    search_fields = ("user__username", "reason", "evidence")


@admin.register(FounderEquityRecord)
class FounderEquityRecordAdmin(admin.ModelAdmin):
    list_display = ("founder_user", "role_title", "allocated_shares", "vested_shares", "unvested_shares", "vesting_status")
    list_filter = ("vesting_status", "inactive_flag", "breach_flag")
    search_fields = ("founder_user__username", "role_title")


@admin.register(CompanyShareStructure)
class CompanyShareStructureAdmin(admin.ModelAdmin):
    list_display = ("total_authorized_shares", "founder_pool", "investor_reserve", "employee_advisor_pool", "updated_at")
