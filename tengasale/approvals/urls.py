"""
/tengasale/underwriter/ — legacy-compatible URL aliases.

All routes redirect to the modern /sales/ equivalents so there is only one UI.
Legacy bookmarks and existing links continue to work transparently.
"""
from django.urls import path
from django.views.generic import RedirectView

from . import views


def _sales(name, **kwargs):
    """Return a permanent redirect view to the named sales URL."""
    from django.urls import reverse_lazy
    return RedirectView.as_view(url=reverse_lazy(name, kwargs=kwargs), permanent=False)


# ────────────────────────────────────────────────────────────────────────────
# Legacy redirects  →  modern /sales/ routes
# ────────────────────────────────────────────────────────────────────────────
urlpatterns = [
    # Dashboard / home
    path("", RedirectView.as_view(pattern_name="sales_home", permanent=False), name="underwriter_dashboard"),
    path("", RedirectView.as_view(pattern_name="sales_home", permanent=False), name="manager_home"),

    # Claim
    path("claim-next/", RedirectView.as_view(pattern_name="sales_claim_next", permanent=False), name="underwriter_claim_next"),
    path("claim-next/", RedirectView.as_view(pattern_name="sales_claim_next", permanent=False), name="claim_next"),

    # Queue
    path("queue/", RedirectView.as_view(url="/sales/applications/?tab=queue", permanent=False), name="underwriter_queue"),

    # Review steps — still serve real views (needed for POST actions from legacy URLs)
    path("review/<int:app_id>/", views.review_application, name="underwriter_review_application"),
    path("review/<int:app_id>/", views.review_application, name="review_application"),
    path("review/<int:app_id>/summary/", views.summary_review, name="underwriter_review_summary"),
    path("review/<int:app_id>/identity/", views.identity_check, name="underwriter_identity_check"),
    path("review/<int:app_id>/momo/", views.momo_check, name="underwriter_momo_check"),
    path("review/<int:app_id>/customer-call/", views.customer_call, name="underwriter_customer_call"),
    path("review/<int:app_id>/income/", views.income_check, name="underwriter_income_check"),
    path("review/<int:app_id>/location/", views.location_check, name="underwriter_location_check"),
    path("review/<int:app_id>/final/", views.final_review, name="underwriter_final_review"),
    path("review/<int:app_id>/correction/", views.correction_action, name="underwriter_correction_action"),
    path("review/<int:app_id>/address-check/", views.legacy_address_check, name="underwriter_address_check"),
    path("review/<int:app_id>/income-check/", views.legacy_income_check, name="underwriter_income_check_legacy"),
    path("review/<int:app_id>/confirm-approve/", views.confirm_approve, name="underwriter_confirm_approve"),

    # Lists — redirect to sales equivalents
    path("active/", RedirectView.as_view(pattern_name="sales_applications", permanent=False), name="underwriter_active_reviews"),
    path("completed/", RedirectView.as_view(url="/sales/applications/?tab=completed", permanent=False), name="underwriter_completed_reviews"),
]
