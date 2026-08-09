from django.contrib import admin
from django.contrib.admin.sites import NotRegistered
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from .models import (
    CompanyShareStructure,
    CompensationCycle,
    Department,
    DisciplineDispute,
    DisciplineEvent,
    DisciplineEventType,
    DisciplineScorePeriod,
    FounderEquityRecord,
    KPITemplate,
    KPIResult,
    PenaltyTransaction,
    PenaltyType,
    Rank,
    StaffRole,
    StaffDocument,
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
        "full_name",
        "email",
        "phone_number",
        "phone",
        "department",
        "staff_role",
        "rank",
        "supervisor",
        "user_type",
        "status",
        "is_founder",
        "founder_equity_record",
        "date_joined_company",
        "probation_status",
        "next_review_date",
        "base_salary_mwk",
        "volt_rate_mwk",
        "monthly_ceiling_mwk",
        "can_approve_volts",
        "can_approve_discipline",
        "can_approve_payouts",
        "can_override_scores",
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
    list_display = ("user", "role", "staff_role", "department", "rank", "status", "earnings_access", "earnings_lock_enabled", "earnings_pin_updated_at")
    list_filter = ("role", "staff_role", "department", "rank", "status", "earnings_access", "earnings_lock_enabled", "is_founder")
    search_fields = ("user__username", "user__email", "phone_number", "user__first_name", "user__last_name")
    readonly_fields = ("earnings_lock_created_at", "earnings_pin_updated_at", "earnings_recovery_verified_at", "earnings_security_generation")


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "active")
    list_filter = ("active",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Rank)
class RankAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "base_salary_default_mwk", "volt_rate_default_mwk", "rank_multiplier", "monthly_ceiling_mwk", "is_board_controlled", "active")
    list_filter = ("active", "is_board_controlled")
    search_fields = ("code", "title")


@admin.register(StaffRole)
class StaffRoleAdmin(admin.ModelAdmin):
    list_display = ("name", "department", "portal_role", "active")
    list_filter = ("department", "portal_role", "active")
    search_fields = ("name", "code")


@admin.register(VoltsActionType)
class VoltsActionTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "department", "base_volts", "requires_approval", "active")
    list_filter = ("department", "requires_approval", "active")
    search_fields = ("name", "code")


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


@admin.register(CompensationCycle)
class CompensationCycleAdmin(admin.ModelAdmin):
    list_display = ("name", "start_day", "end_day", "active", "created_at")
    list_filter = ("active",)


@admin.register(DisciplineEventType)
class DisciplineEventTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "deduction_percentage", "bonus_percentage", "severity", "evidence_required", "auto_generated", "active")
    list_filter = ("severity", "evidence_required", "auto_generated", "active")
    search_fields = ("name", "code", "description")


@admin.register(DisciplineScorePeriod)
class DisciplineScorePeriodAdmin(admin.ModelAdmin):
    list_display = ("user", "month", "final_score", "status", "dispute_status", "reviewed_by", "approved_by", "payout_impact_approved_by")
    list_filter = ("status", "dispute_status", "month")
    search_fields = ("user__username", "user__first_name", "user__last_name", "user_response_text")


@admin.register(DisciplineEvent)
class DisciplineEventAdmin(admin.ModelAdmin):
    list_display = ("user", "event_type", "source", "deduction_percentage", "bonus_percentage", "status", "approved_by", "created_at")
    list_filter = ("source", "status", "event_type__severity")
    search_fields = ("user__username", "event_type__name", "evidence_text")


@admin.register(DisciplineDispute)
class DisciplineDisputeAdmin(admin.ModelAdmin):
    list_display = ("user", "discipline_score_period", "status", "reviewed_by", "created_at", "resolved_at")
    list_filter = ("status",)
    search_fields = ("user__username", "dispute_text", "resolution_notes")


@admin.register(KPITemplate)
class KPITemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "role", "department", "target_value", "weight", "lower_is_better", "active")
    list_filter = ("department", "role", "active", "affects_quality_score", "affects_discipline_score", "affects_results_score")
    search_fields = ("name", "description", "data_source")


@admin.register(KPIResult)
class KPIResultAdmin(admin.ModelAdmin):
    list_display = ("user", "kpi_template", "period", "actual_value", "target_value", "score", "weighted_score", "status", "approved_by")
    list_filter = ("status", "period", "kpi_template__department")
    search_fields = ("user__username", "kpi_template__name", "evidence")


@admin.register(StaffDocument)
class StaffDocumentAdmin(admin.ModelAdmin):
    list_display = ("document_number", "user", "document_type", "version", "prepared_by", "generated_at")
    list_filter = ("document_type", "version")
    search_fields = ("document_number", "user__username", "user__first_name", "user__last_name")
