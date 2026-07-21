from django.core.management.base import BaseCommand

from payments.models import AirtelTransaction


class Command(BaseCommand):
    help = "Compare Airtel pending transactions, callbacks, enquiries, and repayment posting evidence."

    def add_arguments(self, parser):
        parser.add_argument("--reference")
        parser.add_argument("--limit", type=int, default=500)

    def handle(self, *args, **options):
        rows = AirtelTransaction.objects.select_related("payment_transaction").prefetch_related("callback_logs", "enquiry_logs").order_by("created_at")
        if options["reference"]:
            rows = rows.filter(internal_reference=options["reference"])
        issues = 0
        for tx in rows[:max(options["limit"], 0)]:
            callbacks = tx.callback_logs.count()
            enquiries = tx.enquiry_logs.count()
            posted = bool(tx.repayment_posted)
            mismatch = (tx.status == AirtelTransaction.STATUS_SUCCESS and not posted) or (posted and tx.status != AirtelTransaction.STATUS_SUCCESS)
            pending_without_evidence = tx.status in {AirtelTransaction.STATUS_INITIATED, AirtelTransaction.STATUS_PENDING} and not callbacks and not enquiries
            if mismatch or pending_without_evidence or tx.reconciliation_required:
                issues += 1
                self.stdout.write(f"{tx.internal_reference}: status={tx.status} callbacks={callbacks} enquiries={enquiries} repayment_posted={posted} reconciliation_required={tx.reconciliation_required}")
        self.stdout.write(f"Summary: issues={issues}")
