from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["title", "notification_type", "level", "recipient", "is_read", "created_at"]
    list_filter = ["notification_type", "level", "is_read"]
    search_fields = ["title", "body", "recipient__username"]
    readonly_fields = ["created_at", "read_at"]
    date_hierarchy = "created_at"
