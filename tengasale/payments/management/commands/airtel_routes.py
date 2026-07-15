from django.core.management.base import BaseCommand
from django.urls import get_resolver


PUBLIC_AIRTEL_ROUTES = (
    ("GET", "/api/payments/airtel/health/", "airtel_health"),
    ("GET", "/api/payments/airtel/readiness/", "airtel_readiness"),
    ("POST", "/api/payments/airtel/callback/", "airtel_callback"),
)


class Command(BaseCommand):
    help = "Print and validate the public Airtel Money routes."

    def handle(self, *args, **options):
        resolver = get_resolver()
        for method, route, expected_name in PUBLIC_AIRTEL_ROUTES:
            match = resolver.resolve(route)
            if match.url_name != expected_name:
                raise RuntimeError(f"{route} resolves to {match.url_name!r}, expected {expected_name!r}")
            self.stdout.write(f"{method:<4} {route}")
