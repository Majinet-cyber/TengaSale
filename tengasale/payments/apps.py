from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "payments"
    verbose_name = "TengaSale Payments"

    def ready(self):
        from . import checks  # noqa: F401
