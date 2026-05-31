from django.urls import path
from . import views

app_name = "device_lock"

urlpatterns = [
    # HQ Control Centre
    path("tengasale/hq/device-lock/", views.hq_device_lock_centre, name="hq_lock_centre"),
    path("tengasale/hq/device-lock/<int:profile_pk>/", views.hq_lock_profile_detail, name="profile_detail"),
    path("tengasale/hq/device-lock/<int:profile_pk>/action/", views.hq_lock_action, name="lock_action"),

    # Webhook endpoint
    path("api/device-lock/webhook/", views.device_lock_webhook, name="webhook"),
]
