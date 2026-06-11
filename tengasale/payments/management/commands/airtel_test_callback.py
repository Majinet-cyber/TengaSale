import json
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.test import Client

from payments.models import AirtelTransaction


class Command(BaseCommand):
    help = "POST a sample Airtel callback to the local callback handler."

    def add_arguments(self, parser):
        parser.add_argument("--reference", default="TENGA-AIRTEL-TEST", help="Internal/reference id to include in the sample callback.")

    def handle(self, *args, **options):
        reference = options["reference"]
        AirtelTransaction.objects.get_or_create(
            internal_reference=reference,
            defaults={
                "customer_msisdn": "+265991234567",
                "amount": Decimal("1000"),
                "currency": "MWK",
                "purpose": AirtelTransaction.PURPOSE_TEST,
                "direction": AirtelTransaction.DIRECTION_COLLECTION,
                "status": AirtelTransaction.STATUS_PENDING,
            },
        )
        payload = {
            "data": {
                "additional_info": {"mq_txn_id": "SAMPLE-MQ-TXN"},
                "transaction": {
                    "airtel_money_id": "sample-airtel-money-id",
                    "reference_id": reference,
                    "id": "sample-airtel-transaction-id",
                    "status": "TS",
                    "message": "Transaction Successful",
                },
            },
            "status": {
                "response_code": "DP02100001001",
                "code": "200",
                "success": True,
                "message": "success",
            },
        }
        client = Client()
        response = client.post(
            "/api/payments/airtel/callback/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_HOST="localhost",
        )
        self.stdout.write(f"status_code: {response.status_code}")
        self.stdout.write(response.content.decode("utf-8"))
        tx = AirtelTransaction.objects.filter(internal_reference=reference).first()
        if response.status_code == 200 and tx and tx.status == AirtelTransaction.STATUS_SUCCESS:
            self.stdout.write(self.style.SUCCESS("Airtel callback handler accepted and processed the sample callback."))
        else:
            self.stdout.write(self.style.ERROR("Airtel callback handler did not fully process the sample callback."))
