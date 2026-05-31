"""
Management command: check the IMEI API account balance.

Usage:
    python manage.py imei_check_balance
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Check the current IMEI verification API account balance/credits."

    def handle(self, *args, **options):
        from django.conf import settings
        from applications.services.imei_client import ImeiCheckClient, _mask_key

        key = getattr(settings, "IMEI_CHECK_API_KEY", "")
        if not key:
            self.stderr.write(self.style.ERROR("IMEI_CHECK_API_KEY is not configured."))
            return

        self.stdout.write(
            f"Using API key: {_mask_key(key)} "
            f"(endpoint: {getattr(settings, 'IMEI_CHECK_ALPHA_ENDPOINT_BASE', '?')})"
        )

        client = ImeiCheckClient()
        result = client.get_balance()

        if "error" in result and result["error"]:
            self.stderr.write(self.style.ERROR(f"API error: {result['error']}"))
            return

        self.stdout.write(self.style.SUCCESS("Balance / account info:"))
        for key_name, value in result.items():
            if key_name != "_raw":
                self.stdout.write(f"  {key_name}: {value}")
