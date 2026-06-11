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
        self.stdout.write(f"callback_auth_enabled: {summary['callback_auth_enabled']}")
        self.stdout.write(f"callback_hash_key_present: {bool(config.callback_hash_key)} ({summary['callback_hash_key'] or 'missing'})")
        self.stdout.write(f"auth_token_present: {bool(config.auth_token)} ({summary['auth_token'] or 'missing'})")

