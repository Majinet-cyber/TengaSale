from django.urls import path

from . import views


urlpatterns = [
    # Contract flow
    path("<int:app_id>/terms/", views.contract_terms, name="contract_terms"),
    path("<int:contract_id>/signature/", views.contract_signature, name="contract_signature"),
    path("<int:contract_id>/imei/", views.contract_imei, name="contract_imei"),
    path("<int:contract_id>/progress/", views.contract_progress, name="contract_progress"),
    path("<int:contract_id>/complete/", views.contract_complete, name="contract_complete"),
    path("<int:contract_id>/", views.contract_detail, name="contract_detail"),

    # PDF downloads
    path("<int:contract_id>/pdf/initial/", views.contract_pdf_initial, name="contract_pdf_initial"),
    path("<int:contract_id>/pdf/completed/", views.contract_pdf_completed, name="contract_pdf_completed"),

    # HQ delivery management (POST only, require HQ role)
    path("delivery/<int:delivery_id>/send-now/", views.hq_delivery_send_now, name="hq_delivery_send_now"),
    path("delivery/<int:delivery_id>/cancel/", views.hq_delivery_cancel, name="hq_delivery_cancel"),
    path("delivery/<int:delivery_id>/retry/", views.hq_delivery_retry, name="hq_delivery_retry"),
    path("<int:contract_id>/regen-pdf/", views.hq_regen_completed_pdf, name="hq_regen_completed_pdf"),
]
