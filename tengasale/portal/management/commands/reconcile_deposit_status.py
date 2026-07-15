from django.core.management.base import BaseCommand

from core.models import AuditLog
from portal.models import PaymentContract
from portal.services import get_deposit_summary


class Command(BaseCommand):
    help = "Report deposit snapshot/ledger inconsistencies; use --apply to reconcile snapshots only."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report inconsistencies without changing data (the default).",
        )
        parser.add_argument("--apply", action="store_true", help="Update deposit snapshot fields from confirmed ledger totals.")

    def handle(self, *args, **options):
        if options["dry_run"] and options["apply"]:
            self.stderr.write(self.style.ERROR("Choose either --dry-run or --apply, not both."))
            return
        apply_changes = options["apply"]
        inconsistent = 0
        manual_review = 0
        for contract in PaymentContract.objects.all().iterator():
            summary = get_deposit_summary(contract)
            snapshot = contract.deposit_paid or 0
            active_without_deposit = (
                summary.required_amount > 0
                and not summary.is_fully_paid
                and contract.status in (PaymentContract.STATUS_ACTIVE, PaymentContract.STATUS_COMPLETED)
            )
            if snapshot == summary.confirmed_paid_amount and not active_without_deposit:
                continue
            inconsistent += 1
            review = " MANUAL_REVIEW" if active_without_deposit else ""
            if active_without_deposit:
                manual_review += 1
            self.stdout.write(
                f"{contract.contract_number} snapshot={snapshot} confirmed={summary.confirmed_paid_amount} "
                f"required={summary.required_amount} status={contract.status}{review}"
            )
            if apply_changes and snapshot != summary.confirmed_paid_amount:
                contract.deposit_paid = summary.confirmed_paid_amount
                update_fields = ["deposit_paid"]
                if not summary.is_fully_paid and contract.deposit_paid_at:
                    contract.deposit_paid_at = None
                    update_fields.append("deposit_paid_at")
                contract.save(update_fields=update_fields)
                AuditLog.objects.create(
                    user=None,
                    action=AuditLog.ACTION_PAYMENT,
                    object_type="PaymentContract",
                    object_id=str(contract.pk),
                    detail={
                        "event": "deposit_snapshot_reconciled",
                        "confirmed_deposit": str(summary.confirmed_paid_amount),
                        "manual_status_review_required": active_without_deposit,
                    },
                )
        mode = "APPLIED" if apply_changes else "DRY RUN"
        self.stdout.write(self.style.SUCCESS(
            f"{mode}: {inconsistent} inconsistent contract(s); {manual_review} require manual status/schedule review."
        ))
