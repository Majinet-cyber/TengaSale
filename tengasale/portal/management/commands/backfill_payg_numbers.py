"""
Management command: backfill_payg_numbers

Finds all PaymentContracts that have a missing or non-conforming payg_number
(i.e. not in EXXXXXXX 8-char format starting with E), generates a fresh
unique PayG number for each, and saves it.

Usage:
    python manage.py backfill_payg_numbers
    python manage.py backfill_payg_numbers --dry-run
"""

import re

from django.core.management.base import BaseCommand
from django.db import transaction

_PAYG_PATTERN = re.compile(r"^E[A-Z2-9]{7}$")


class Command(BaseCommand):
    help = "Backfill missing or legacy PayG numbers on all PaymentContracts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be changed without saving.",
        )

    def handle(self, *args, **options):
        from portal.models import PaymentContract, generate_payg_number

        dry_run = options["dry_run"]

        # Contracts that need a fresh PayG number:
        # either blank or not matching the new EXXXXXXX format
        all_contracts = PaymentContract.objects.all().only("id", "payg_number", "contract_number")
        to_update = [c for c in all_contracts if not _PAYG_PATTERN.match(c.payg_number or "")]

        if not to_update:
            self.stdout.write(self.style.SUCCESS("All PayG numbers are up to date. Nothing to do."))
            return

        self.stdout.write(f"Found {len(to_update)} contract(s) needing a new PayG number.")

        updated = 0
        errors = 0

        for contract in to_update:
            old = contract.payg_number or "(empty)"
            if dry_run:
                self.stdout.write(f"  [dry-run] {contract.contract_number}: {old} -> (new)")
                continue
            try:
                with transaction.atomic():
                    # Temporarily clear so generate_payg_number doesn't collide with itself
                    contract.payg_number = ""
                    new_number = generate_payg_number()
                    contract.payg_number = new_number
                    contract.save(update_fields=["payg_number"])
                    self.stdout.write(f"  {contract.contract_number}: {old} -> {new_number}")
                    updated += 1
            except Exception as exc:
                self.stderr.write(f"  ERROR for {contract.contract_number}: {exc}")
                errors += 1

        if dry_run:
            self.stdout.write(self.style.WARNING(f"Dry run complete. {len(to_update)} would be updated."))
        else:
            self.stdout.write(
                self.style.SUCCESS(f"Done. Updated: {updated}. Errors: {errors}.")
            )
