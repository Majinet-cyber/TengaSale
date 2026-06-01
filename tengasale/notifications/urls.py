from django.urls import path

from . import views

urlpatterns = [
    path("", views.notification_list, name="notifications_list"),
    path("<int:pk>/read/", views.mark_read, name="notification_mark_read"),
    path("mark-all-read/", views.mark_all_read, name="notifications_mark_all_read"),
    path("api/unread-count/", views.unread_count_api, name="notifications_unread_count"),
    path("api/dropdown/", views.dropdown_partial, name="notifications_dropdown"),
]
