"""
Management command: test an IMEI check and show normalised brand/model output.

Usage:
    python manage.py imei_check_test 358089361347363
    python manage.py imei_check_test 358089361347363 --service-id 12345
    python manage.py imei_check_test 358089361347363 --selected-brand Tecno --selected-model "Spark 50"
"""

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Submit a test IMEI check and display the normalised verification result."

    def add_arguments(self, parser):
        parser.add_argument("imei", help="15-digit IMEI to check")
        parser.add_argument(
            "--service-id",
            dest="service_id",
            default="",
            help="IMEI check service ID (overrides IMEI_CHECK_SERVICE_ID setting)",
        )
        parser.add_argument(
            "--selected-brand",
            dest="selected_brand",
            default="",
            help="Brand of the selected smartphone (for match scoring)",
        )
        parser.add_argument(
            "--selected-model",
            dest="selected_model",
            default="",
            help="Model of the selected smartphone (for match scoring)",
        )
        parser.add_argument(
            "--force-recheck",
            action="store_true",
            dest="force_recheck",
            help="Bypass cache and force a fresh API call",
        )

    def handle(self, *args, **options):
        from django.conf import settings
        from applications.services.imei_client import ImeiCheckClient, _mask_key
        from applications.services.imei_verification import (
            extract_brand_model_from_api_result,
            score_device_match,
            normalize_brand,
            normalize_model,
        )

        imei = options["imei"].strip()
        if len(imei) != 15 or not imei.isdigit():
            raise CommandError("IMEI must be exactly 15 digits.")

        service_id = options.get("service_id") or getattr(settings, "IMEI_CHECK_SERVICE_ID", "")
        selected_brand = options.get("selected_brand", "")
        selected_model = options.get("selected_model", "")

        key = getattr(settings, "IMEI_CHECK_API_KEY", "")
        self.stdout.write(
            f"Checking IMEI {imei} "
            f"(key={_mask_key(key)}, service_id={service_id or '(not set)'})"
        )

        # Cache bypass if --force-recheck
        if options["force_recheck"]:
            from django.core.cache import cache
            cache.delete(f"imei_check:{imei}")

        client = ImeiCheckClient()
        raw = client.check_imei(imei, service_id=service_id or None)

        self.stdout.write("\n── Raw normalised response ──────────────────────────")
        for k, v in raw.items():
            if k != "object":
                self.stdout.write(f"  {k}: {v}")
        if raw.get("object"):
            self.stdout.write(f"  object: {raw['object']}")

        # Extract brand / model
        api_brand, api_model = extract_brand_model_from_api_result(
            raw.get("raw_result", ""),
            raw.get("object", {}),
        )

        self.stdout.write("\n── Parsed device info ───────────────────────────────")
        self.stdout.write(f"  API brand (canonical): {api_brand!r}")
        self.stdout.write(f"  API model (raw):       {api_model!r}")
        self.stdout.write(f"  API model (normalised):{normalize_model(api_model)!r}")

        # Score if selected device provided
        if selected_brand or selected_model:
            confidence, reasons, match_status = score_device_match(
                selected_brand, selected_model, api_brand, api_model, raw.get("raw_result", "")
            )
            self.stdout.write("\n── Match scoring ────────────────────────────────────")
            self.stdout.write(f"  Selected brand:  {normalize_brand(selected_brand)!r}")
            self.stdout.write(f"  Selected model:  {normalize_model(selected_model)!r}")
            self.stdout.write(f"  Match status:    {match_status}")
            self.stdout.write(f"  Confidence:      {confidence}%")
            self.stdout.write("  Reasons:")
            for r in reasons:
                self.stdout.write(f"    - {r}")

            if match_status == "match":
                self.stdout.write(self.style.SUCCESS(f"\nResult: {match_status.upper()} ({confidence}%)"))
            elif match_status == "possible_match":
                self.stdout.write(self.style.WARNING(f"\nResult: {match_status.upper()} ({confidence}%)"))
            else:
                self.stdout.write(self.style.ERROR(f"\nResult: {match_status.upper()} ({confidence}%)"))
