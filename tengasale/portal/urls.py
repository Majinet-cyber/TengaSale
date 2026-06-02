from django.urls import path
from . import views

urlpatterns = [
    path("", views.portal_search, name="portal_search"),
    path("support/", views.portal_support, name="portal_support"),
    path("search/", views.portal_search_post, name="portal_search_post"),

    # Contract routes by internal contract_number (backward-compatible)
    path("contract/<str:contract_number>/", views.portal_contract, name="portal_contract"),
    path("contract/<str:contract_number>/payment/", views.portal_payment, name="portal_payment"),
    path("contract/<str:contract_number>/history/", views.portal_history, name="portal_history"),

    # Customer-facing PayG routes (primary customer payment URL)
    # e.g. /pay/payg/EXGH4456/
    path("payg/<str:payg_number>/", views.portal_payg, name="portal_payg"),
    path("payg/<str:payg_number>/payment/", views.portal_payg_payment, name="portal_payg_payment"),
    path("payg/<str:payg_number>/history/", views.portal_payg_history, name="portal_payg_history"),

    # Webhook receiver endpoints — protected placeholders
    path("webhooks/paychangu/", views.webhook_paychangu, name="webhook_paychangu"),
    path("webhooks/paytrigger/", views.webhook_paytrigger, name="webhook_paytrigger"),
    path("webhooks/airtel/", views.webhook_airtel, name="webhook_airtel"),
    path("webhooks/tnm/", views.webhook_tnm, name="webhook_tnm"),
]
