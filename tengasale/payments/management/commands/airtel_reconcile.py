import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from payments.airtel_services import AirtelTransactionEnquiryService, confirmed_provider_reference
from payments.models import AirtelTransaction


class Command(BaseCommand):
    help = "Safely enquire and reconcile pending Airtel collections, or list the operator-visible reconciliation queue."

    def add_arguments(self, parser):
        parser.add_argument("--reference")
        parser.add_argument("--pending-older-than", type=int)
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--delay", type=float, default=0.25)
        parser.add_argument("--apply", action="store_true", help="Call Airtel and apply verified final results.")
        parser.add_argument("--dry-run", action="store_true", help="Explicitly select the default no-change mode.")
        parser.add_argument(
            "--queue",
            action="store_true",
            help=(
                "List the operator-visible reconciliation queue: unresolved transactions "
                "with no confirmed provider identifier. Always read-only — never calls "
                "Airtel and never guesses success or posts money, regardless of --apply."
            ),
        )

    def handle(self, *args, **options):
        if options["queue"]:
            return self._print_queue(options)
        qs = AirtelTransaction.objects.filter(direction=AirtelTransaction.DIRECTION_COLLECTION)
        if options["reference"]:
            qs = qs.filter(internal_reference=options["reference"])
        else:
            qs = qs.filter(status__in=[AirtelTransaction.STATUS_INITIATED, AirtelTransaction.STATUS_PENDING])
            if options["pending_older_than"] is not None:
                qs = qs.filter(created_at__lte=timezone.now() - timedelta(minutes=options["pending_older_than"]))
        rows = list(qs.order_by("created_at")[: max(options["limit"], 0)])
        totals = {"selected": len(rows), "changed": 0, "failed": 0}
        for index, tx in enumerate(rows):
            before = tx.status
            if not options["apply"]:
                self.stdout.write(f"DRY-RUN {tx.internal_reference}: {before}")
                continue
            try:
                result = AirtelTransactionEnquiryService().enquire(tx.internal_reference)
                changed = result.status != before or bool(result.processed_success_at)
                totals["changed"] += int(changed)
                self.stdout.write(f"{tx.internal_reference}: {before} -> {result.status}")
            except Exception as exc:
                totals["failed"] += 1
                self.stderr.write(f"{tx.internal_reference}: {exc.__class__.__name__}: {exc}")
            if index + 1 < len(rows) and options["delay"] > 0:
                time.sleep(options["delay"])
        self.stdout.write("Summary: " + ", ".join(f"{key}={value}" for key, value in totals.items()))

    def _print_queue(self, options):
        """Read-only report of unresolved Airtel transactions. Never contacts Airtel
        and never mutates any record — safe to run at any time, including in
        production, without --apply having any effect here."""
        qs = (
            AirtelTransaction.objects.filter(
                direction=AirtelTransaction.DIRECTION_COLLECTION,
                reconciliation_required=True,
                reconciliation_completed_at__isnull=True,
            )
            .order_by("created_at")
        )
        rows = list(qs[: max(options["limit"], 0)])
        self.stdout.write(f"Reconciliation queue: {len(rows)} unresolved transaction(s) (read-only, no changes made).")
        for tx in rows:
            has_confirmed_ref = bool(confirmed_provider_reference(tx))
            flags = []
            if tx.potential_overpayment:
                flags.append("POTENTIAL_OVERPAYMENT")
            if not has_confirmed_ref:
                flags.append("NO_CONFIRMED_PROVIDER_ID")
            if tx.retry_of_id or tx.superseded_by_id:
                flags.append("RETRY_CHAIN")
            self.stdout.write(
                f"{tx.internal_reference}: status={tx.status} purpose={tx.purpose} amount={tx.amount} "
                f"contract={tx.contract_id or '-'} created={tx.created_at.isoformat()} "
                f"note={tx.processing_note or '-'} flags={','.join(flags) or '-'}"
            )
