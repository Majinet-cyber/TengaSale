from django.conf import settings
from django.core.management.base import BaseCommand

from integrations.upya.exceptions import UpyaError
from integrations.upya.payment_gateway import UpyaPaymentGateway


class Command(BaseCommand):
    help = "Check Upya Manage and Payment Gateway readiness without changing remote state."

    def add_arguments(self, parser):
        parser.add_argument("--reference", help="Optional non-PII test contract reference for a live read.")

    def handle(self, *args, **options):
        manage_ready = all((settings.UPYA_MANAGE_BASE_URL, settings.UPYA_MANAGE_USERNAME, settings.UPYA_MANAGE_PASSWORD))
        gateway_ready = all((
            settings.UPYA_PAYMENT_GATEWAY_BASE_URL, settings.UPYA_PAYMENT_GATEWAY_USERNAME,
            settings.UPYA_PAYMENT_GATEWAY_PASSWORD, settings.UPYA_PAYMENT_GATEWAY_CLIENT_IDENTIFIER,
        ))
        self.stdout.write(f"Manage API: {'CONFIGURED' if manage_ready else 'FAIL (credentials missing)'}")
        self.stdout.write(f"Payment Gateway: {'CONFIGURED' if gateway_ready else 'FAIL (credentials/clientIdentifier missing)'}")
        reference = options.get("reference")
        if reference and gateway_ready:
            try:
                options_found = UpyaPaymentGateway().get_payment_options(reference=reference)
            except UpyaError as exc:
                self.stdout.write(self.style.ERROR(f"Payment Gateway live read: FAIL ({exc.__class__.__name__})"))
            else:
                self.stdout.write(self.style.SUCCESS(f"Payment Gateway live read: OK ({len(options_found)} contract result(s))"))
        elif not reference:
            self.stdout.write("Payment Gateway live read: SKIPPED (pass --reference to test safely)")
