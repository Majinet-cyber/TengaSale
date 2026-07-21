from django.urls import path

from . import api_views


urlpatterns = [
    path("airtel/health/", api_views.airtel_health, name="airtel_health"),
    path("airtel/readiness/", api_views.airtel_readiness, name="airtel_readiness"),
    path("airtel/callback/", api_views.airtel_callback, name="airtel_callback"),
    path("airtel/callback-health/", api_views.airtel_callback_health, name="airtel_callback_health"),
    path("airtel/test/simulate-callback/", api_views.airtel_simulate_callback, name="airtel_simulate_callback"),
    path("airtel/collections/initiate/", api_views.airtel_collection_initiate, name="airtel_collection_initiate"),
    path(
        "airtel/transactions/<str:internal_reference>/enquiry/",
        api_views.airtel_transaction_enquiry,
        name="airtel_transaction_enquiry",
    ),
    path("<str:transaction_id>/status/", api_views.payment_transaction_status, name="payment_transaction_status"),
]
