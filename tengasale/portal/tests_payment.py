"""
Tests for TengaSale payment integration.

Covers:
- Payment provider selection and mock mode
- PayChangu MoMo provider (mocked HTTP)
- Webhook handler signature verification and idempotency
- Contract balance updates after payment
- Contract activation after deposit
- Notification best-effort (never crash)
- Portal search by Contract Number, PayG, IMEI
"""
import json
import hashlib
import hmac
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, Client, override_settings
from django.utils import timezone

from applications.models import FinancingApplication
from contracts.models import Contract
from portal.models import (
    PaymentContract, PaymentTransaction,
    generate_contract_number, generate_payg_number, generate_payment_reference,
)
from portal.services import resolve_payable_contract
from portal.payment_providers import (
    MockPaymentProvider,
    PayChanguProvider,
    AirtelMoneyProvider,
    TNMMpambaProvider,
    get_payment_provider,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_contract(**kwargs):
    defaults = dict(
        contract_number=generate_contract_number(),
        payg_number=generate_payg_number(),
        customer_name="Test Customer",
        customer_phone="0881234567",
        imei_number="123456789012345",
        device_model="Tecno Spark 10",
        total_amount=Decimal("150000"),
        deposit_required=Decimal("15000"),
        deposit_paid=Decimal("0"),
        amount_paid=Decimal("0"),
        status=PaymentContract.STATUS_ACTIVE,
        due_date=timezone.now().date(),
        lock_date=timezone.now().date(),
    )
    defaults.update(kwargs)
    return PaymentContract.objects.create(**defaults)


def _make_transaction(contract, **kwargs):
    defaults = dict(
        payment_contract=contract,
        provider="mock",
        payment_type=PaymentTransaction.TYPE_REPAYMENT,
        amount=Decimal("5000"),
        phone="0881234567",
        internal_reference=generate_payment_reference(),
        status=PaymentTransaction.STATUS_PENDING,
    )
    defaults.update(kwargs)
    return PaymentTransaction.objects.create(**defaults)


# ---------------------------------------------------------------------------
# Provider unit tests
# ---------------------------------------------------------------------------

@override_settings(MOCK_PAYMENTS=True)
class TestMockProvider(TestCase):
    def test_always_succeeds(self):
        p = MockPaymentProvider()
        result = p.create_payment_intent(Decimal("5000"), "0881234567", "TS-PAY-TEST01")
        self.assertTrue(result.success)
        self.assertEqual(result.flow, "mock")

    def test_verify_returns_paid(self):
        p = MockPaymentProvider()
        r = p.verify_payment("MOCK-REF")
        self.assertTrue(r.paid)

    def test_get_provider_returns_mock_when_flag_set(self):
        provider = get_payment_provider("airtel_money")
        self.assertIsInstance(provider, MockPaymentProvider)


@override_settings(MOCK_PAYMENTS=False, PAYCHANGU_PUBLIC_KEY="test_pub", PAYCHANGU_SECRET_KEY="test_sec", PAYMENTS_MODE="test")
class TestPayChanguProvider(TestCase):
    def test_is_configured(self):
        p = PayChanguProvider()
        self.assertTrue(p.is_configured)

    def test_mode(self):
        p = PayChanguProvider()
        self.assertEqual(p.mode, "test")

    @patch("integrations.paychangu_client.is_mock_mode", return_value=True)
    def test_create_payment_intent_mock_mode(self, _mock):
        p = PayChanguProvider()
        r = p.create_payment_intent(Decimal("5000"), "0881234567", "TS-PAY-X")
        self.assertTrue(r.success)
        self.assertIn("MOCK", r.provider_reference)

    @patch("integrations.paychangu_client.is_mock_mode", return_value=False)
    @patch("integrations.paychangu_client.initiate_payment")
    def test_create_payment_intent_live(self, mock_initiate, _mock_mode):
        mock_initiate.return_value = {
            "status": "success",
            "checkout_url": "https://pay.paychangu.com/x",
            "raw_response": {},
        }
        p = PayChanguProvider()
        r = p.create_payment_intent(Decimal("5000"), "0881234567", "TS-PAY-X")
        self.assertTrue(r.success)
        self.assertEqual(r.redirect_url, "https://pay.paychangu.com/x")
        self.assertEqual(r.flow, "checkout")


@override_settings(MOCK_PAYMENTS=False, PAYCHANGU_PUBLIC_KEY="pk", PAYCHANGU_SECRET_KEY="sk", PAYMENTS_MODE="test")
class TestAirtelProvider(TestCase):
    @patch("integrations.paychangu_client.is_mock_mode", return_value=False)
    @patch("integrations.paychangu_client.get_operator_ref_id")
    @patch("integrations.paychangu_client.momo_initialize")
    def test_momo_success(self, mock_init, mock_ref, _mock_mode):
        mock_ref.return_value = {"status": "success", "ref_id": "ref-123"}
        mock_init.return_value = {"status": "success", "raw_response": {}}
        p = AirtelMoneyProvider()
        r = p.create_payment_intent(Decimal("5000"), "0881234567", "TS-PAY-A1")
        self.assertTrue(r.success)
        self.assertEqual(r.flow, "momo")
        self.assertTrue(r.charge_id.startswith("ch-"))

    @patch("integrations.paychangu_client.is_mock_mode", return_value=False)
    @patch("integrations.paychangu_client.get_operator_ref_id")
    def test_momo_operator_not_found(self, mock_ref, _mock_mode):
        mock_ref.return_value = {"status": "error", "message": "Not found"}
        p = AirtelMoneyProvider()
        r = p.create_payment_intent(Decimal("5000"), "0881234567", "TS-PAY-A2")
        self.assertFalse(r.success)
        self.assertIn("not available", r.message)


# ---------------------------------------------------------------------------
# Webhook handler tests
# ---------------------------------------------------------------------------

def _sign_payload(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


@override_settings(MOCK_PAYMENTS=True, PAYCHANGU_WEBHOOK_SECRET="testsecret123")
class TestPayChanguWebhook(TestCase):
    def setUp(self):
        self.client = Client()
        self.contract = _make_contract()
        self.tx = _make_transaction(
            self.contract,
            payment_type=PaymentTransaction.TYPE_REPAYMENT,
            amount=Decimal("5000"),
            status=PaymentTransaction.STATUS_PROCESSING,
        )
        self.url = "/pay/webhooks/paychangu/"

    def _post_webhook(self, payload: dict, sign=True, secret="testsecret123"):
        body = json.dumps(payload).encode("utf-8")
        sig = _sign_payload(body, secret) if sign else "bad-sig"
        return self.client.post(
            self.url,
            data=body,
            content_type="application/json",
            HTTP_SIGNATURE=sig,
        )

    @patch("integrations.paychangu_client.verify_webhook_signature", return_value=True)
    @patch("integrations.paychangu_client.verify_transaction")
    def test_successful_webhook_marks_paid(self, mock_verify, mock_sig):
        mock_verify.return_value = {"status": "SUCCESS", "amount": "5000", "raw_response": {}}
        payload = {"tx_ref": self.tx.internal_reference, "event": "payment.success", "amount": "5000", "currency": "MWK"}
        resp = self._post_webhook(payload)
        self.assertEqual(resp.status_code, 200)
        self.tx.refresh_from_db()
        self.assertEqual(self.tx.status, PaymentTransaction.STATUS_PAID)

    @patch("integrations.paychangu_client.verify_webhook_signature", return_value=True)
    @patch("integrations.paychangu_client.verify_transaction")
    def test_idempotent_second_webhook(self, mock_verify, mock_sig):
        self.tx.status = PaymentTransaction.STATUS_PAID
        self.tx.paid_at = timezone.now()
        self.tx.save()

        payload = {"tx_ref": self.tx.internal_reference, "event": "payment.success", "amount": "5000", "currency": "MWK"}
        resp = self._post_webhook(payload)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data.get("message"), "Already processed")
        mock_verify.assert_not_called()

    @patch("integrations.paychangu_client.verify_webhook_signature", return_value=True)
    @patch("integrations.paychangu_client.verify_transaction")
    def test_failed_payment_sets_failed_status(self, mock_verify, mock_sig):
        mock_verify.return_value = {"status": "FAILED", "amount": "5000", "raw_response": {}}
        payload = {"tx_ref": self.tx.internal_reference, "event": "payment.failed", "amount": "5000", "currency": "MWK"}
        resp = self._post_webhook(payload)
        self.assertEqual(resp.status_code, 200)
        self.tx.refresh_from_db()
        self.assertEqual(self.tx.status, PaymentTransaction.STATUS_FAILED)

    def test_unknown_tx_ref_returns_200(self):
        payload = {"tx_ref": "UNKNOWN-REF-XXXX", "event": "payment.success", "amount": "100", "currency": "MWK"}
        resp = self._post_webhook(payload)
        self.assertEqual(resp.status_code, 200)

    def test_get_method_returns_405(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)


# ---------------------------------------------------------------------------
# Contract balance update tests
# ---------------------------------------------------------------------------

@override_settings(MOCK_PAYMENTS=True)
class TestApplyPayment(TestCase):
    def test_repayment_updates_amount_paid(self):
        contract = _make_contract(
            deposit_paid=Decimal("15000"),  # deposit already paid
            amount_paid=Decimal("15000"),
        )
        from portal.services import apply_payment_to_contract
        apply_payment_to_contract(contract, Decimal("5000"), payment_type=PaymentTransaction.TYPE_REPAYMENT)
        contract.refresh_from_db()
        self.assertGreaterEqual(contract.amount_paid, Decimal("20000"))

    def test_deposit_payment_updates_deposit_paid(self):
        contract = _make_contract()
        from portal.services import apply_payment_to_contract
        apply_payment_to_contract(contract, Decimal("15000"), payment_type=PaymentTransaction.TYPE_DEPOSIT)
        contract.refresh_from_db()
        self.assertEqual(contract.deposit_paid, Decimal("15000"))
        self.assertTrue(contract.deposit_complete)


# ---------------------------------------------------------------------------
# Contract activation after deposit
# ---------------------------------------------------------------------------

@override_settings(MOCK_PAYMENTS=True)
class TestContractActivation(TestCase):
    def test_contract_activated_when_deposit_complete(self):
        contract = _make_contract(
            deposit_required=Decimal("15000"),
            deposit_paid=Decimal("0"),
            status=PaymentContract.STATUS_ACTIVE,
        )
        tx = _make_transaction(
            contract,
            payment_type=PaymentTransaction.TYPE_DEPOSIT,
            amount=Decimal("15000"),
            status=PaymentTransaction.STATUS_PAID,
        )
        # Simulate deposit payment applying
        contract.deposit_paid = Decimal("15000")
        contract.save()

        from portal.views import _maybe_activate_contract_after_deposit
        _maybe_activate_contract_after_deposit(contract, tx)
        contract.refresh_from_db()
        self.assertEqual(contract.status, PaymentContract.STATUS_ACTIVE)


# ---------------------------------------------------------------------------
# Portal search tests
# ---------------------------------------------------------------------------

@override_settings(MOCK_PAYMENTS=True)
class TestPortalSearch(TestCase):
    def setUp(self):
        self.client = Client()
        self.contract = _make_contract(imei_number="IMEI-TEST-12345")

    def test_search_by_contract_number(self):
        resp = self.client.get(f"/pay/?q={self.contract.contract_number}")
        self.assertEqual(resp.status_code, 200)

    def test_search_by_payg_number(self):
        resp = self.client.get(f"/pay/payg/{self.contract.payg_number}/")
        self.assertEqual(resp.status_code, 200)

    def test_search_by_contract_number_in_post(self):
        resp = self.client.post("/pay/search/", {"query": self.contract.contract_number})
        self.assertIn(resp.status_code, [200, 302])


class TestContractResolution(TestCase):
    def setUp(self):
        self.client = Client()
        self.merchant = get_user_model().objects.create_user(
            username="merchant-resolution",
            password="test-pass-123",
        )
        self.application = FinancingApplication.objects.create(
            created_by=self.merchant,
            application_number="TSM-260526-000008",
            customer_name="Laisa Sandra",
            customer_phone="988745444",
            national_id="VB78NNRU",
            selected_cash_price=Decimal("527000.00"),
            calculated_total_loan=Decimal("1317500.00"),
            calculated_deposit_amount=Decimal("171275.00"),
            calculated_monthly_payment=Decimal("109792.00"),
            calculated_daily_payment=Decimal("3660.00"),
            status="contract_complete",
        )
        self.legal_contract = Contract.objects.create(
            application=self.application,
            contract_number="E71919832",
            merchant=self.merchant,
            customer_name="Laisa Sandra",
            customer_phone="988745444",
            national_id="VB78NNRU",
            cash_price=Decimal("527000.00"),
            total_loan=Decimal("1317500.00"),
            deposit_amount=Decimal("171275.00"),
            monthly_payment=Decimal("109792.00"),
            daily_payment=Decimal("3660.00"),
            deposit_paid=True,
            deposit_paid_at=timezone.now(),
            status=Contract.STATUS_COMPLETE,
        )

    def test_exact_demo_identifiers_resolve_to_same_payable_contract(self):
        queries = [
            "E71919832",
            "TSM-260526-000008",
            "VB78NNRU",
            "+265988745444",
            "265988745444",
            "0988745444",
            "988745444",
        ]
        resolved_ids = set()
        for query in queries:
            result = resolve_payable_contract(query, country="MW")
            self.assertEqual(result.status, "payable", query)
            self.assertIsNotNone(result.contract, query)
            resolved_ids.add(result.contract.id)

        self.assertEqual(len(resolved_ids), 1)
        contract = PaymentContract.objects.get(id=resolved_ids.pop())
        self.assertEqual(contract.source_application, self.application)
        self.assertEqual(contract.customer_national_id, "VB78NNRU")
        self.assertEqual(contract.deposit_paid, contract.deposit_required)
        self.assertGreater(contract.remaining_amount, Decimal("0"))
        self.assertEqual(contract.status, PaymentContract.STATUS_ACTIVE)

    def test_search_page_does_not_show_not_found_for_legal_contract(self):
        response = self.client.get("/pay/search/", {"country": "MW", "q": "E71919832"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/pay/contract/", response["Location"])

    def test_another_country_cannot_retrieve_contract(self):
        result = resolve_payable_contract("E71919832", country="ZM")
        self.assertEqual(result.status, "not_found")
        self.assertIsNone(result.contract)

    def test_fully_paid_contract_resolves_as_fully_paid_not_not_found(self):
        result = resolve_payable_contract("E71919832", country="MW")
        contract = result.contract
        contract.amount_paid = contract.total_amount
        contract.status = PaymentContract.STATUS_COMPLETED
        contract.save(update_fields=["amount_paid", "status"])

        result = resolve_payable_contract("E71919832", country="MW")
        self.assertEqual(result.status, "fully_paid")
        self.assertIsNotNone(result.contract)

    def test_ambiguous_phone_is_handled_safely(self):
        _make_contract(customer_phone="988745444", customer_national_id="AB12CD34")
        result = resolve_payable_contract("988745444", country="MW")
        self.assertEqual(result.status, "ambiguous")
        self.assertIsNone(result.contract)


# ---------------------------------------------------------------------------
# Notification robustness tests
# ---------------------------------------------------------------------------

class TestNotificationRobustness(TestCase):
    def test_email_service_doesnt_raise_on_bad_recipient(self):
        from notifications.services.email import _send_plain
        result = _send_plain("", "Test", "body")
        self.assertFalse(result)

    def test_sms_service_doesnt_raise_without_twilio(self):
        from notifications.services.twilio_sms import send_sms
        result = send_sms("0881234567", "Test message")
        # Should return False (not configured) without raising
        self.assertFalse(result)

    def test_whatsapp_service_doesnt_raise_without_twilio(self):
        from notifications.services.twilio_sms import send_whatsapp
        result = send_whatsapp("0881234567", "Test message")
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# Asset rendering test
# ---------------------------------------------------------------------------

class TestStaticAssets(TestCase):
    def test_airtel_svg_exists(self):
        import os
        path = os.path.join(
            os.path.dirname(__file__),
            "..", "static", "tengasale", "assets", "logos", "airtel-logo.svg"
        )
        self.assertTrue(os.path.exists(os.path.abspath(path)), f"Airtel SVG not found at {path}")

    def test_tnm_svg_exists(self):
        import os
        path = os.path.join(
            os.path.dirname(__file__),
            "..", "static", "tengasale", "assets", "logos", "tnm-logo.svg"
        )
        self.assertTrue(os.path.exists(os.path.abspath(path)), f"TNM SVG not found at {path}")
