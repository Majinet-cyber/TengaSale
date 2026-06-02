"""
Management command: refresh_analytics

Backfills / refreshes the analytics snapshot fields on all PaymentContract rows.
Run after deploying Section 11 analytics fields, or as a scheduled task.

Usage:
    python manage.py refresh_analytics
    python manage.py refresh_analytics --contract TS-MW-12345678
"""

from django.core.management.base import BaseCommand

from portal.models import PaymentContract
from portal.services import refresh_contract_analytics


class Command(BaseCommand):
    help = "Refresh stored payment analytics on PaymentContract rows"

    def add_arguments(self, parser):
        parser.add_argument(
            "--contract",
            type=str,
            default="",
            help="Refresh a single contract by number (default: all contracts)",
        )

    def handle(self, *args, **options):
        contract_num = options.get("contract", "").strip()

        if contract_num:
            qs = PaymentContract.objects.filter(contract_number=contract_num)
        else:
            qs = PaymentContract.objects.all()

        total = qs.count()
        self.stdout.write(f"Refreshing analytics for {total} contract(s)...")

        updated = 0
        errors = 0
        for contract in qs.iterator():
            try:
                refresh_contract_analytics(contract, db_save=True)
                updated += 1
                if updated % 100 == 0:
                    self.stdout.write(f"  {updated}/{total} processed...")
            except Exception as exc:
                errors += 1
                self.stderr.write(f"  ERROR on {contract.contract_number}: {exc}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {updated} updated, {errors} error(s)."
            )
        )
