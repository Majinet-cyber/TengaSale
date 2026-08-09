from django.urls import path
from . import views

app_name = "protect"
urlpatterns = [
    path("", views.imei_check, name="imei_check"),
    path("check/", views.imei_check, name="imei_check_alias"),
    path("my/<str:contract_number>/", views.customer_home, name="customer_home"),
    path("my/<str:contract_number>/report/", views.report_device, name="report_device"),
    path("my/<str:contract_number>/ownership-certificate/", views.ownership_certificate, name="ownership_certificate"),
    path("cases/<uuid:case_id>/", views.case_detail, name="case_detail"),
    path("verify/<str:token>/", views.verify_certificate, name="verify_certificate"),
    path("officer/", views.officer_dashboard, name="officer_dashboard"),
    path("privacy/", views.privacy_dashboard, name="privacy_dashboard"),
]
