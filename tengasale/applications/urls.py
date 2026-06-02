from django.urls import path
from . import views

urlpatterns = [
    path("new/", views.new_application, name="new_application"),
    path("<int:app_id>/customer/", views.edit_customer_details, name="edit_customer_details"),
    path("<int:app_id>/device/", views.choose_device, name="choose_device"),
    path("<int:app_id>/kyc/", views.kyc_capture, name="kyc_capture"),
    path("<int:app_id>/location/", views.location_details, name="location_details"),
    path("<int:app_id>/work/", views.work_details, name="work_details"),
    path("<int:app_id>/signature/", views.signature, name="signature"),
    path("<int:app_id>/review/", views.application_review, name="application_review"),
    path("<int:app_id>/imei/", views.capture_imei, name="capture_imei"),
    path("<int:app_id>/verify-imei/", views.verify_imei_ajax, name="verify_imei_ajax"),
    path("<int:app_id>/detail/", views.application_detail, name="application_detail"),
    path("<int:app_id>/submitted/", views.application_submitted, name="application_submitted"),
    path("<int:app_id>/corrections/", views.application_corrections, name="application_corrections"),

    path("<str:token>/correct/", views.customer_field_correction, name="customer_field_correction"),

    path("active/", views.active_applications, name="active_applications"),
    path("pending/", views.pending_applications, name="pending_applications"),
    path("needs-edit/", views.needs_edit_applications, name="needs_edit_applications"),
    path("approved/", views.approved_applications, name="approved_applications"),
    path("completed/", views.completed_applications, name="completed_applications"),
    path("rejected/", views.rejected_applications, name="rejected_applications"),
]
