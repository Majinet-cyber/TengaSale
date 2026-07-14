from django.core.management.base import BaseCommand

from payments.airtel_client import AirtelConfig


class Command(BaseCommand):
    help = "Print safe Airtel Money configuration status."

    def handle(self, *args, **options):
        config = AirtelConfig.from_settings()
        summary = config.safe_summary()
        self.stdout.write(f"environment: {summary['env']}")
        self.stdout.write(f"base_url: {summary['base_url']}")
        self.stdout.write(f"country/currency: {summary['country']}/{summary['currency']}")
        self.stdout.write(f"merchant_code_present: {summary['merchant_code_present']}")
        self.stdout.write(f"collections_enabled: {summary['collections_enabled']}")
        self.stdout.write(f"dry_run: {summary['dry_run']}")
        self.stdout.write(f"callback_auth_enabled: {summary['callback_auth_enabled']}")
        self.stdout.write(f"callback_secret_configured: {summary['callback_secret_configured']}")
        self.stdout.write(f"token_configured: {summary['token_configured']}")
