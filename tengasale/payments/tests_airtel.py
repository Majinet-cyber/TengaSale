import json
from decimal import Decimal

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from payments.airtel_services import normalize_airtel_status
from payments.models import AirtelCallbackLog, AirtelTransaction
from portal.models import PaymentContract, PaymentTransaction, generate_contract_number, generate_payg_number


def make_contract(**kwargs):
    defaults = {
        "contract_number": generate_contract_number(),
        "payg_number": generate_payg_number(),
        "customer_name": "Airtel Customer",
        "customer_phone": "0991234567",
        "device_model": "Tecno Spark",
        "total_amount": Decimal("150000"),
        "deposit_required": Decimal("15000"),
        "deposit_paid": Decimal("0"),
        "amount_paid": Decimal("0"),
        "daily_price": Decimal("1000"),
        "thirty_day_price": Decimal("30000"),
        "deposit_access_days": 7,
        "status": PaymentContract.STATUS_ACTIVE,
    }
    defaults.update(kwargs)
    return PaymentContract.objects.create(**defaults)


class AirtelStatusMappingTests(TestCase):
    def test_success_statuses(self):
        for value in ("TS", "SUCCESS", "SUCCESSFUL", "Transaction Successful"):
            self.assertEqual(normalize_airtel_status(value), AirtelTransaction.STATUS_SUCCESS)

    def test_pending_statuses(self):
        for value in ("TIP", "PENDING", "IN_PROGRESS"):
            self.assertEqual(normalize_airtel_status(value), AirtelTransaction.STATUS_PENDING)

    def test_expired_statuses(self):
        for value in ("TE", "EXPIRED"):
            self.assertEqual(normalize_airtel_status(value), AirtelTransaction.STATUS_EXPIRED)

    def test_failed_statuses(self):
        for value in ("FAILED", "TF", "DECLINED", "REJECTED"):
            self.assertEqual(normalize_airtel_status(value), AirtelTransaction.STATUS_FAILED)

    def test_unknown_status(self):
        self.assertEqual(normalize_airtel_status("SOMETHING_NEW"), AirtelTransaction.STATUS_UNKNOWN)


@override_settings(
    AIRTEL_ENV="uat",
    AIRTEL_CALLBACK_AUTH_ENABLED=True,
    AIRTEL_CALLBACK_HASH_KEY="testhash",
    AIRTEL_AUTH_TOKEN="",
)
class AirtelApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.contract = make_contract()

    def test_callback_get_health_returns_ok(self):
        response = self.client.get("/api/payments/airtel/callback/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "provider": "airtel", "endpoint": "callback"})

    def test_callback_post_logs_raw_body(self):
        payload = {"transaction": {"reference_id": "missing", "status": "TIP"}}
        response = self.client.post(
            "/api/payments/airtel/callback/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        log = AirtelCallbackLog.objects.get()
        self.assertIn("reference_id", log.raw_body)
        self.assertEqual(log.parsed_body, payload)

    def test_collection_initiate_stores_airtel_transaction_without_token(self):
        response = self.client.post(
            "/api/payments/airtel/collections/initiate/",
            data=json.dumps({
                "msisdn": "0991234567",
                "amount": 1000,
                "purpose": "DEPOSIT",
                "contract_id": self.contract.id,
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["provider"], "airtel")
        self.assertNotIn("testhash", response.content.decode("utf-8"))
        self.assertNotIn("AIRTEL_AUTH_TOKEN", response.content.decode("utf-8"))
        tx = AirtelTransaction.objects.get(internal_reference=data["internal_reference"])
        self.assertEqual(tx.status, AirtelTransaction.STATUS_PENDING)
        self.assertTrue(tx.raw_response["local_only"])

    def test_successful_deposit_callback_grants_14_day_access_and_is_idempotent(self):
        tx = AirtelTransaction.objects.create(
            internal_reference="TENGA-AIRTEL-20260611-ABC12345",
            customer_msisdn="+265991234567",
            amount=Decimal("15000"),
            purpose=AirtelTransaction.PURPOSE_DEPOSIT,
            direction=AirtelTransaction.DIRECTION_COLLECTION,
            status=AirtelTransaction.STATUS_PENDING,
            contract=self.contract,
        )
        payload = {
            "data": {
                "transaction": {
                    "reference_id": tx.internal_reference,
                    "id": "ATX-123",
                    "status": "TS",
                    "message": "Transaction Successful",
                }
            }
        }
        before = timezone.now()
        for _ in range(2):
            response = self.client.post(
                "/api/payments/airtel/callback/",
                data=json.dumps(payload),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)

        self.contract.refresh_from_db()
        tx.refresh_from_db()
        self.assertEqual(tx.status, AirtelTransaction.STATUS_SUCCESS)
        self.assertEqual(self.contract.deposit_paid, Decimal("15000"))
        self.assertEqual(self.contract.status, PaymentContract.STATUS_ACTIVE)
        self.assertEqual(self.contract.deposit_access_days, 14)
        self.assertIsNotNone(self.contract.access_expires_at)
        self.assertGreaterEqual((self.contract.access_expires_at - before).days, 13)
        self.assertEqual(PaymentTransaction.objects.filter(internal_reference=tx.internal_reference).count(), 1)
        self.assertEqual(AirtelCallbackLog.objects.filter(duplicate=True).count(), 1)

    def test_failed_deposit_does_not_activate_contract(self):
        contract = make_contract(status=PaymentContract.STATUS_OVERDUE)
        tx = AirtelTransaction.objects.create(
            internal_reference="TENGA-AIRTEL-20260611-FAIL0001",
            customer_msisdn="+265991234567",
            amount=Decimal("15000"),
            purpose=AirtelTransaction.PURPOSE_DEPOSIT,
            status=AirtelTransaction.STATUS_PENDING,
            contract=contract,
        )
        payload = {"data": {"transaction": {"reference_id": tx.internal_reference, "status": "FAILED"}}}
        response = self.client.post(
            "/api/payments/airtel/callback/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        contract.refresh_from_db()
        self.assertEqual(contract.deposit_paid, Decimal("0"))
        self.assertEqual(contract.status, PaymentContract.STATUS_OVERDUE)
