from django.urls import path
from . import views

urlpatterns = [
    path("agreement/", views.agreement_view, name="merchant_agreement"),
    path("agreement/sign/", views.agreement_sign, name="merchant_agreement_sign"),
    path("agreement/status/", views.agreement_status, name="merchant_agreement_status"),
    path("agreement/<int:agreement_id>/pdf/", views.agreement_pdf_download, name="merchant_agreement_pdf"),
    path("agreement/<int:agreement_id>/view/", views.agreement_pdf_view, name="merchant_agreement_pdf_view"),
]
