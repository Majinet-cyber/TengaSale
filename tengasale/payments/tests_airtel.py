import hashlib
import hmac
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from payments.airtel_client import AirtelClient
from payments.airtel_services import AirtelTransactionEnquiryService, normalize_airtel_status
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


def signed_payload(payload, key="testhash"):
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(key.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return {**payload, "hash": signature}


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
    AIRTEL_PRIVATE_KEY="testhash",
    AIRTEL_AUTH_TOKEN="",
    AIRTEL_CALLBACK_URL="https://tengasale-api.onrender.com/api/payments/airtel/callback/",
)
class AirtelApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.contract = make_contract()

    def test_callback_url_resolves_and_get_returns_405(self):
        response = self.client.get("/api/payments/airtel/callback/")
        self.assertEqual(response.status_code, 405)

    def test_health_endpoint_returns_safe_status(self):
        response = self.client.get("/api/payments/airtel/health/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["provider"], "airtel_money")
        self.assertEqual(data["environment"], "staging")
        self.assertTrue(data["callback_configured"])
        self.assertNotIn("testhash", response.content.decode("utf-8"))

    def test_callback_post_logs_raw_body(self):
        payload = signed_payload({"transaction": {"reference_id": "missing", "status_code": "TIP"}})
        response = self.client.post(
            "/api/payments/airtel/callback/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        log = AirtelCallbackLog.objects.get()
        self.assertIn("reference_id", log.raw_body)
        self.assertEqual(log.parsed_body, payload)

    def test_invalid_hash_is_rejected(self):
        payload = {"transaction": {"reference_id": "missing", "status_code": "TS"}, "hash": "bad"}
        response = self.client.post(
            "/api/payments/airtel/callback/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
        self.assertFalse(AirtelCallbackLog.objects.get().signature_valid)

    def test_malformed_json_returns_400(self):
        response = self.client.post(
            "/api/payments/airtel/callback/",
            data="{not-json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_missing_transaction_returns_400(self):
        response = self.client.post(
            "/api/payments/airtel/callback/",
            data=json.dumps(signed_payload({"status": "TS"})),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_unknown_internal_transaction_is_logged_safely(self):
        response = self.client.post(
            "/api/payments/airtel/callback/",
            data=json.dumps(signed_payload({"transaction": {"id": "UNKNOWN-REF", "status_code": "TS"}})),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("No matching", AirtelCallbackLog.objects.get().processing_error)

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
        payload = signed_payload({
            "transaction": {
                "reference_id": tx.internal_reference,
                "id": "ATX-123",
                "status_code": "TS",
                "message": "Paid MWK 15,000",
                "airtel_money_id": "MP210603.1234.L06941",
            }
        })
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
        self.assertEqual(tx.airtel_money_id, "MP210603.1234.L06941")
        self.assertIsNotNone(tx.completed_at)
        self.assertEqual(self.contract.deposit_paid, Decimal("15000"))
        self.assertEqual(self.contract.status, PaymentContract.STATUS_ACTIVE)
        self.assertEqual(self.contract.deposit_access_days, 14)
        self.assertIsNotNone(self.contract.access_expires_at)
        self.assertGreaterEqual((self.contract.access_expires_at - before).days, 13)
        self.assertEqual(PaymentTransaction.objects.filter(internal_reference=tx.internal_reference).count(), 1)
        self.assertEqual(PaymentTransaction.objects.filter(provider_reference="MP210603.1234.L06941").count(), 1)
        self.assertEqual(AirtelCallbackLog.objects.filter(duplicate=True).count(), 1)

    def test_amount_mismatch_is_rejected_without_posting_repayment(self):
        tx = AirtelTransaction.objects.create(
            internal_reference="TENGA-AIRTEL-20260611-MISMATCH",
            customer_msisdn="+265991234567",
            amount=Decimal("15000"),
            purpose=AirtelTransaction.PURPOSE_DEPOSIT,
            status=AirtelTransaction.STATUS_PENDING,
            contract=self.contract,
        )
        payload = signed_payload({
            "transaction": {
                "reference_id": tx.internal_reference,
                "status_code": "TS",
                "message": "Paid MWK 5,000",
            }
        })
        response = self.client.post(
            "/api/payments/airtel/callback/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.contract.refresh_from_db()
        tx.refresh_from_db()
        self.assertEqual(self.contract.deposit_paid, Decimal("0"))
        self.assertEqual(PaymentTransaction.objects.count(), 0)
        self.assertIn("mismatch", tx.failure_reason.lower())

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
        payload = signed_payload({"transaction": {"reference_id": tx.internal_reference, "status_code": "TF", "message": "Failed"}})
        response = self.client.post(
            "/api/payments/airtel/callback/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        contract.refresh_from_db()
        tx.refresh_from_db()
        self.assertEqual(contract.deposit_paid, Decimal("0"))
        self.assertEqual(contract.status, PaymentContract.STATUS_OVERDUE)
        self.assertEqual(tx.status, AirtelTransaction.STATUS_FAILED)
        self.assertEqual(PaymentTransaction.objects.count(), 0)

    @override_settings(AIRTEL_BASE_URL="https://openapiuat.airtel.mw")
    def test_staging_base_url_is_selected_from_environment(self):
        response = self.client.get("/api/payments/airtel/health/")
        self.assertEqual(response.json()["environment"], "staging")

    @override_settings(AIRTEL_BASE_URL="https://openapi.airtel.mw")
    def test_production_url_selected_only_when_configured(self):
        response = self.client.get("/api/payments/airtel/health/")
        self.assertEqual(response.json()["environment"], "production")

    @override_settings(
        AIRTEL_AUTH_TOKEN="",
        AIRTEL_CLIENT_ID="client-id",
        AIRTEL_CLIENT_SECRET="client-secret",
        AIRTEL_TOKEN_PATH="/auth/oauth2/token",
    )
    def test_access_token_is_requested_and_cached(self):
        AirtelClient._cached_access_token = ""
        AirtelClient._cached_access_token_expires_at = 0
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"access_token": "token-123", "expires_in": 3600}
        with patch("payments.airtel_client.requests.post", return_value=response) as post:
            client = AirtelClient()
            self.assertEqual(client.get_access_token(), "token-123")
            self.assertEqual(client.get_access_token(), "token-123")
        post.assert_called_once()

    @override_settings(AIRTEL_AUTH_TOKEN="", AIRTEL_CLIENT_ID="client-id", AIRTEL_CLIENT_SECRET="client-secret")
    def test_access_token_errors_are_handled(self):
        AirtelClient._cached_access_token = ""
        AirtelClient._cached_access_token_expires_at = 0
        response = Mock()
        response.status_code = 500
        response.json.return_value = {"error": "down"}
        with patch("payments.airtel_client.requests.post", return_value=response):
            with self.assertRaises(RuntimeError):
                AirtelClient().get_access_token()

    def test_transaction_enquiry_success_updates_and_posts_once(self):
        tx = AirtelTransaction.objects.create(
            internal_reference="TENGA-AIRTEL-20260611-ENQUIRY1",
            customer_msisdn="+265991234567",
            amount=Decimal("15000"),
            purpose=AirtelTransaction.PURPOSE_DEPOSIT,
            status=AirtelTransaction.STATUS_PENDING,
            contract=self.contract,
        )
        fake_client = SimpleNamespace(
            is_configured_for_api_calls=True,
            config=SimpleNamespace(enquiry_path_template="/standard/v1/payments/{reference}"),
            get=lambda path: {
                "ok": True,
                "http_status": 200,
                "body": {"transaction": {"reference_id": tx.internal_reference, "id": "ENQ-123", "status_code": "TS"}},
            },
        )
        AirtelTransactionEnquiryService(client=fake_client).enquire(tx.internal_reference)
        AirtelTransactionEnquiryService(client=fake_client).enquire(tx.internal_reference)
        tx.refresh_from_db()
        self.contract.refresh_from_db()
        self.assertEqual(tx.status, AirtelTransaction.STATUS_SUCCESS)
        self.assertEqual(self.contract.deposit_paid, Decimal("15000"))
        self.assertEqual(PaymentTransaction.objects.count(), 1)
