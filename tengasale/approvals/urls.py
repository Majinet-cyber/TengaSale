"""
/tengasale/underwriter/ — legacy-compatible URL aliases.

All routes serve the modern /sales/ review wizard so there is only one UI.
Legacy bookmarks and existing links continue to work transparently.
"""
from django.shortcuts import redirect
from django.urls import path
from django.views.generic import RedirectView

from . import views as legacy_views
from sales import views as sales_views


def _legacy_review(request, app_id):
    if request.method == "POST" and request.POST.get("decision"):
        return legacy_views.review_application(request, app_id)
    return sales_views.sales_review_summary(request, app_id)


def _legacy_identity(request, app_id):
    return sales_views.sales_identity_check(request, app_id)


def _legacy_customer_call(request, app_id):
    return sales_views.sales_customer_call(request, app_id)


def _legacy_income(request, app_id):
    return sales_views.sales_income_check(request, app_id)


def _legacy_final(request, app_id):
    return sales_views.sales_final_review(request, app_id)


def _legacy_confirm_approve(request, app_id):
    return sales_views.sales_confirm_approve(request, app_id)


def _legacy_address(request, app_id):
    return sales_views.sales_address_check(request, app_id)


def _legacy_call_questionnaire(request, app_id):
    return sales_views.sales_call_questionnaire(request, app_id)


def _legacy_correction(request, app_id):
    return legacy_views.correction_action(request, app_id)


# ────────────────────────────────────────────────────────────────────────────
# Legacy aliases  →  modern /sales/ routes
# ────────────────────────────────────────────────────────────────────────────
urlpatterns = [
    # Dashboard / home
    path("", RedirectView.as_view(pattern_name="sales_home", permanent=False), name="underwriter_dashboard"),
    path("", RedirectView.as_view(pattern_name="sales_home", permanent=False), name="manager_home"),

    # Claim — GET must not hit POST-only endpoint
    path("claim-next/", RedirectView.as_view(pattern_name="sales_home", permanent=False), name="underwriter_claim_next"),
    path("claim-next/", RedirectView.as_view(pattern_name="sales_home", permanent=False), name="claim_next"),

    # Queue
    path("queue/", RedirectView.as_view(url="/sales/applications/?tab=queue", permanent=False), name="underwriter_queue"),

    # Review steps — modern sales views (single UI surface)
    path("review/<int:app_id>/", _legacy_review, name="underwriter_review_application"),
    path("review/<int:app_id>/", _legacy_review, name="review_application"),
    path("review/<int:app_id>/summary/", _legacy_review, name="underwriter_review_summary"),
    path("review/<int:app_id>/identity/", _legacy_identity, name="underwriter_identity_check"),
    path("review/<int:app_id>/momo/", _legacy_customer_call, name="underwriter_momo_check"),
    path("review/<int:app_id>/customer-call/", _legacy_customer_call, name="underwriter_customer_call"),
    path("review/<int:app_id>/call-questionnaire/", _legacy_call_questionnaire, name="underwriter_call_questionnaire"),
    path("review/<int:app_id>/income/", _legacy_income, name="underwriter_income_check"),
    path("review/<int:app_id>/location/", _legacy_address, name="underwriter_location_check"),
    path("review/<int:app_id>/final/", _legacy_final, name="underwriter_final_review"),
    path("review/<int:app_id>/correction/", _legacy_correction, name="underwriter_correction_action"),
    path("review/<int:app_id>/address-check/", _legacy_address, name="underwriter_address_check"),
    path("review/<int:app_id>/income-check/", _legacy_income, name="underwriter_income_check_legacy"),
    path("review/<int:app_id>/confirm-approve/", _legacy_confirm_approve, name="underwriter_confirm_approve"),

    # Lists — redirect to sales equivalents
    path("active/", RedirectView.as_view(url="/sales/applications/?tab=active", permanent=False), name="underwriter_active_reviews"),
    path("completed/", RedirectView.as_view(url="/sales/applications/?tab=completed", permanent=False), name="underwriter_completed_reviews"),
]
