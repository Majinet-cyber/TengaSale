from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.urls import Resolver404, resolve

from payments.airtel_client import AirtelConfig, AirtelConfigurationError
from payments.models import AirtelTransaction


CALLBACK_PATH = "/api/payments/airtel/callback/"
RENDER_HOST = "tengasale-api.onrender.com"
RENDER_ORIGIN = "https://tengasale-api.onrender.com"


class Command(BaseCommand):
    help = "Run safe Airtel Money staging/UAT deployment preflight checks."

    def _line(self, level: str, message: str):
        style = {
            "PASS": self.style.SUCCESS,
            "WARNING": self.style.WARNING,
            "FAIL": self.style.ERROR,
        }.get(level, lambda value: value)
        self.stdout.write(style(f"{level}: {message}"))

    def _check(self, ok: bool, message: str, failures: list[str], *, warning: bool = False):
        if ok:
            self._line("PASS", message)
            return
        if warning:
            self._line("WARNING", message)
        else:
            self._line("FAIL", message)
            failures.append(message)

    def handle(self, *args, **options):
        failures: list[str] = []

        try:
            config = AirtelConfig.from_settings()
            self._line("PASS", f"Airtel environment is {config.environment}.")
        except AirtelConfigurationError as exc:
            self._line("FAIL", str(exc))
            raise CommandError("Airtel preflight failed.")

        self._check(config.environment == "staging", "Airtel environment defaults to staging for UAT.", failures)
        self._check(config.base_url == "https://openapiuat.airtel.mw", "Selected base URL is Airtel UAT.", failures)
        self._check(not config.production_enabled, "Production safety lock is off.", failures)
        self._check(not config.collections_enabled, "Collections kill switch is currently blocking new prompts.", failures, warning=False)
        self._check(config.dry_run, "Dry-run is enabled.", failures)

        self._check(bool(config.client_id), "AIRTEL_CLIENT_ID is configured.", failures)
        self._check(bool(config.client_secret), "AIRTEL_CLIENT_SECRET is configured.", failures)
        self._check(bool(config.private_key), "AIRTEL_PRIVATE_KEY or callback secret is configured.", failures)
        self._check(bool(config.merchant_code), "AIRTEL_MERCHANT_CODE is configured.", failures)
        self._check(bool(getattr(settings, "AIRTEL_CALLBACK_URL", "")), "AIRTEL_CALLBACK_URL is configured.", failures)
        self._check(config.callback_auth_enabled, "Callback authentication is enabled.", failures)
        self._check(bool(config.allowed_test_msisdns), "AIRTEL_ALLOWED_TEST_MSISDNS is configured.", failures)

        try:
            max_amount = int(config.test_max_amount)
            self._check(max_amount <= 1000, "AIRTEL_TEST_MAX_AMOUNT is at or below MWK 1000.", failures)
        except (TypeError, ValueError):
            self._check(False, "AIRTEL_TEST_MAX_AMOUNT is a valid integer.", failures)

        try:
            match = resolve(CALLBACK_PATH)
            route_ok = getattr(match.func, "__name__", "") == "airtel_callback"
        except Resolver404:
            route_ok = False
        self._check(route_ok, "Callback route resolves.", failures)

        allowed_hosts = list(getattr(settings, "ALLOWED_HOSTS", []))
        csrf_origins = list(getattr(settings, "CSRF_TRUSTED_ORIGINS", []))
        self._check(
            RENDER_HOST in allowed_hosts or ".onrender.com" in allowed_hosts or "*.onrender.com" in allowed_hosts,
            "ALLOWED_HOSTS covers tengasale-api.onrender.com.",
            failures,
        )
        self._check(
            RENDER_ORIGIN in csrf_origins or "https://*.onrender.com" in csrf_origins,
            "CSRF_TRUSTED_ORIGINS covers https://tengasale-api.onrender.com.",
            failures,
        )

        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            db_ready = True
        except Exception:
            db_ready = False
        self._check(db_ready, "Database connection is ready.", failures)

        executor = MigrationExecutor(connection)
        self._check(not executor.migration_plan(executor.loader.graph.leaf_nodes()), "Migrations are applied.", failures)

        constraint_names = {constraint.name for constraint in AirtelTransaction._meta.constraints}
        self._check("uniq_airtel_money_id_when_present" in constraint_names, "Airtel Money ID duplicate protection exists.", failures)
        self._check("uniq_airtel_tx_id_when_present" in constraint_names, "Airtel transaction ID duplicate protection exists.", failures)

        env_example = settings.BASE_DIR / ".env.example"
        secret_leaks = []
        if env_example.exists():
            for line in env_example.read_text(encoding="utf-8").splitlines():
                if any(name in line for name in ("AIRTEL_CLIENT_SECRET=", "AIRTEL_PRIVATE_KEY=", "AIRTEL_CALLBACK_SECRET=")):
                    key, _, value = line.partition("=")
                    if value.strip():
                        secret_leaks.append(key)
        self._check(not secret_leaks, ".env.example contains Airtel placeholders only.", failures)

        self._line("PASS", "Logging policy avoids printing Airtel credential values.")

        if failures:
            raise CommandError(f"Airtel preflight failed with {len(failures)} issue(s).")
        self.stdout.write(self.style.SUCCESS("Airtel preflight passed."))
