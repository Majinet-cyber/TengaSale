from django.contrib import admin
from django.utils.html import format_html

from .models import (
    CustomerCallQuestionnaire,
    QCOffenseType,
    UnderwriterCallRecording,
    UnderwriterQCPenalty,
    UnderwriterReview,
)


@admin.register(UnderwriterReview)
class UnderwriterReviewAdmin(admin.ModelAdmin):
    list_display = (
        "application_link", "underwriter", "score_display",
        "decision_display", "updated_at",
    )
    list_filter = ("updated_at",)
    search_fields = (
        "application__application_number",
        "application__customer_name",
        "underwriter__username",
    )
    readonly_fields = ("application", "underwriter", "updated_at")
    ordering = ("-updated_at",)

    def application_link(self, obj):
        return str(obj.application)
    application_link.short_description = "Application"

    def score_display(self, obj):
        score = obj.completeness_score()
        colour = "#168a45" if score >= 80 else "#ff6a00" if score >= 50 else "#d93025"
        return format_html(
            '<span style="font-weight:700;color:{colour};">{score}%</span>',
            colour=colour, score=score,
        )
    score_display.short_description = "Score"

    def decision_display(self, obj):
        app = obj.application
        status_colours = {
            "approved": "#168a45",
            "rejected": "#d93025",
            "under_review": "#ff6a00",
            "sent_back": "#0277bd",
        }
        colour = status_colours.get(app.status, "#7a828c")
        return format_html(
            '<span style="color:{colour};font-weight:700;">{status}</span>',
            colour=colour, status=app.get_status_display(),
        )
    decision_display.short_description = "Decision"


@admin.register(CustomerCallQuestionnaire)
class CustomerCallQuestionnaireAdmin(admin.ModelAdmin):
    list_display = (
        "application",
        "recommendation",
        "risk_score",
        "completed_by",
        "completed_at",
    )
    list_filter = ("recommendation", "recording_consent_acknowledged", "completed_at")
    search_fields = (
        "application__application_number",
        "application__customer_name",
        "application__national_id",
        "completed_by__username",
    )
    readonly_fields = ("created_at", "updated_at", "completed_at", "completed_by", "recommendation", "risk_score")
    ordering = ("-completed_at", "-updated_at")


@admin.register(UnderwriterCallRecording)
class UnderwriterCallRecordingAdmin(admin.ModelAdmin):
    list_display = ("application", "underwriter", "original_filename", "status", "uploaded_at", "reviewed_by")
    list_filter = ("status", "consent_acknowledged", "uploaded_at", "reviewed_at")
    search_fields = ("application__application_number", "application__customer_name", "original_filename", "underwriter__username")
    readonly_fields = ("uploaded_at", "reviewed_at")
    ordering = ("-uploaded_at",)


@admin.register(QCOffenseType)
class QCOffenseTypeAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "severity", "default_penalty_mwk", "active")
    list_filter = ("severity", "active")
    search_fields = ("code", "name", "description")
    ordering = ("severity", "code")


@admin.register(UnderwriterQCPenalty)
class UnderwriterQCPenaltyAdmin(admin.ModelAdmin):
    list_display = ("application", "underwriter", "offense_type", "amount_mwk", "status", "issued_at")
    list_filter = ("status", "offense_type", "issued_at")
    search_fields = ("application__application_number", "underwriter__username", "offense_type__code", "notes")
    readonly_fields = ("issued_at", "reversed_at", "ledger_entry", "reversal_ledger_entry")
    ordering = ("-issued_at",)
