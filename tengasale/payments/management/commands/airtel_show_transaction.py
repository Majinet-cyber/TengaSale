import json

from django.core.management.base import BaseCommand, CommandError

from payments.models import AirtelTransaction


class Command(BaseCommand):
    help = "Print a secret-safe Airtel transaction diagnostic trace."

    def add_arguments(self, parser):
        parser.add_argument("reference")

    def handle(self, *args, **options):
        try:
            tx = AirtelTransaction.objects.get(internal_reference=options["reference"])
        except AirtelTransaction.DoesNotExist as exc:
            raise CommandError("Airtel transaction not found.") from exc
        data = {
            "internal_reference": tx.internal_reference, "status": tx.status, "amount": str(tx.amount),
            "currency": tx.currency, "msisdn": f"{tx.customer_msisdn[:6]}***{tx.customer_msisdn[-4:]}",
            "contract": getattr(tx.contract, "contract_number", None), "provider_reference": tx.provider_reference,
            "airtel_money_id": tx.airtel_money_id, "airtel_transaction_id": tx.airtel_transaction_id,
            "airtel_reference_id": tx.airtel_reference_id, "provider_id_confirmed": tx.provider_id_confirmed,
            "callback_count": tx.callback_logs.count(), "repayment_posted": tx.repayment_posted,
            "last_enquiry_at": str(tx.last_enquiry_at or ""), "last_enquiry_status": tx.last_enquiry_status,
            "last_enquiry_error": tx.last_enquiry_error, "processing_note": tx.processing_note,
        }
        self.stdout.write(json.dumps(data, indent=2, default=str))
