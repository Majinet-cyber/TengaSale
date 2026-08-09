from django.contrib import admin
from .models import (DeviceCertificate, DeviceRegistryEntry, LocationAccessSession,
                     LocationRequest, ProtectAuditEvent, ProtectCase, ProtectConsent, ProtectDocument)


@admin.register(ProtectCase)
class ProtectCaseAdmin(admin.ModelAdmin):
    list_display = ("case_number", "case_type", "status", "created_at")
    search_fields = ("case_number", "registry_entry__imei", "registry_entry__contract__customer_name")
    readonly_fields = ("case_number", "created_at", "updated_at")


@admin.register(ProtectAuditEvent)
class ProtectAuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "actor_role", "object_reference")
    readonly_fields = [field.name for field in ProtectAuditEvent._meta.fields]
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False


admin.site.register([DeviceRegistryEntry, ProtectConsent, ProtectDocument, LocationAccessSession, LocationRequest, DeviceCertificate])
