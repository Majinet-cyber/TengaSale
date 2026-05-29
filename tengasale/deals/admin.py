from django.contrib import admin
from .models import DeviceBrand, DeviceDeal


@admin.register(DeviceBrand)
class DeviceBrandAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")
    search_fields = ("name",)


@admin.register(DeviceDeal)
class DeviceDealAdmin(admin.ModelAdmin):
    list_display = (
        "brand",
        "model_name",
        "specs",
        "condition",
        "min_cash_price",
        "max_cash_price",
        "default_cash_price",
        "deposit_percent",
        "loan_multiplier",
        "term_months",
        "stock_status",
        "is_lock_ready",
        "is_active",
        "is_featured",
        "popularity_score",
    )
    list_filter = ("brand", "condition", "stock_status", "is_lock_ready", "is_active", "is_featured", "catalog_source")
    search_fields = ("brand__name", "model_name", "specs", "notes")
    list_editable = (
        "condition",
        "min_cash_price",
        "max_cash_price",
        "default_cash_price",
        "deposit_percent",
        "loan_multiplier",
        "term_months",
        "stock_status",
        "is_lock_ready",
        "is_active",
        "is_featured",
        "popularity_score",
    )
    readonly_fields = ("created_at", "updated_at")
    ordering = ("brand__name", "-popularity_score", "model_name", "specs")
