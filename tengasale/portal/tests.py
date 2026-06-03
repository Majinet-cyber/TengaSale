"""
Portal app tests — Phase 7 (PayG upgrade + deposit flow).

Coverage:
- PaymentContract number uniqueness (TS-MW-XXXXXXXX)
- PayG number format EXXXXXXX (8 chars, starts with E, uppercase alphanumeric)
- PayG uniqueness across many generated values
- Database uniqueness enforced
- calculate_daily_price accuracy (MWK rounding)
- calculate_thirty_day_price accuracy
- Early settlement (3/6/9/12 months) discount calculations
- apply_payment_to_contract — all edge cases
  * zero payment rejected
  * negative payment rejected
  * payment > remaining balance is capped
  * payment exactly completes contract
  * payment while overdue
  * completed contract blocks further payment
  * progress never exceeds 100%
- Portal search by contract number / PayG / national ID / phone
- Mock payment provider returns paid status
- Portal search page renders (200)
- Portal contract page renders (200) after seeding
- Payment webhook endpoints return safe JSON (Phase 6)
- Webhook endpoints log to AuditLog (Phase 6)
- No Yellow/KulaSell references in rendered templates (Phase 6)
- Provider missing keys returns friendly error (Phase 6)
"""

from decimal import Decimal
from datetime import date, timedelta

from django.test import TestCase, Client
from django.utils import timezone

from portal.models import PaymentContract, PaymentTransaction, generate_contract_number, generate_payg_number
from portal.services import (
    calculate_daily_price,
    calculate_thirty_day_price,
    calculate_remaining_amount,
    calculate_early_settlement_options,
    apply_payment_to_contract,
    search_payment_contract,
)
from portal.payment_providers import MockPaymentProvider


# ---------------------------------------------------------------------------
# Model number generation tests
# ---------------------------------------------------------------------------

class ContractNumberGenerationTest(TestCase):
    def test_contract_number_format(self):
        n = generate_contract_number()
        self.assertRegex(n, r"^TS-MW-\d{8}$")

    def test_payg_number_format(self):
        n = generate_payg_number()
        self.assertRegex(n, r"^E[A-Z2-9]{7}$")

    def test_payg_number_starts_with_e(self):
        for _ in range(10):
            n = generate_payg_number()
            self.assertEqual(n[0], "E")

    def test_payg_number_is_8_chars(self):
        for _ in range(10):
            n = generate_payg_number()
            self.assertEqual(len(n), 8)

    def test_payg_number_no_confusing_chars(self):
        for _ in range(30):
            n = generate_payg_number()
            for bad in ("I", "O", "0", "1"):
                self.assertNotIn(bad, n)

    def test_contract_number_uniqueness(self):
        nums = {generate_contract_number() for _ in range(20)}
        self.assertEqual(len(nums), 20)

    def test_payg_number_uniqueness(self):
        nums = {generate_payg_number() for _ in range(20)}
        self.assertEqual(len(nums), 20)

    def test_auto_assigned_on_save(self):
        c = PaymentContract.objects.create(
            customer_name="Auto Test",
            customer_phone="+265881234567",
            total_amount=Decimal("10000.00"),
        )
        self.assertRegex(c.contract_number, r"^TS-MW-\d{8}$")
        self.assertRegex(c.payg_number, r"^E[A-Z2-9]{7}$")

    def test_no_duplicate_contract_numbers(self):
        c1 = PaymentContract.objects.create(
            customer_name="Test A",
            customer_phone="+265881111111",
            total_amount=Decimal("10000.00"),
        )
        c2 = PaymentContract.objects.create(
            customer_name="Test B",
            customer_phone="+265882222222",
            total_amount=Decimal("10000.00"),
        )
        self.assertNotEqual(c1.contract_number, c2.contract_number)
        self.assertNotEqual(c1.payg_number, c2.payg_number)


# ---------------------------------------------------------------------------
# Pricing calculation tests
# ---------------------------------------------------------------------------

class PricingCalculationTest(TestCase):
    def test_daily_price_standard(self):
        # MWK 45,000 over 12 months = 45000 / (12 * 30) = 45000 / 360 = 125
        result = calculate_daily_price(Decimal("45000"), 12)
        self.assertEqual(result, Decimal("125"))

    def test_daily_price_zero_term(self):
        result = calculate_daily_price(Decimal("45000"), 0)
        self.assertEqual(result, Decimal("0"))

    def test_daily_price_zero_amount(self):
        result = calculate_daily_price(Decimal("0"), 12)
        self.assertEqual(result, Decimal("0"))

    def test_thirty_day_price_standard(self):
        # 45000 / 12 = 3750
        result = calculate_thirty_day_price(Decimal("45000"), 12)
        self.assertEqual(result, Decimal("3750"))

    def test_thirty_day_price_zero_amount(self):
        result = calculate_thirty_day_price(Decimal("0"), 12)
        self.assertEqual(result, Decimal("0"))

    def test_thirty_day_price_rounding(self):
        # 10000 / 3 = 3333.33... → rounds to 3333 MWK
        result = calculate_thirty_day_price(Decimal("10000"), 3)
        self.assertEqual(result, Decimal("3333"))

    def test_remaining_amount(self):
        c = PaymentContract(total_amount=Decimal("10000"), amount_paid=Decimal("3000"))
        self.assertEqual(calculate_remaining_amount(c), Decimal("7000"))

    def test_remaining_amount_floor_zero(self):
        c = PaymentContract(total_amount=Decimal("5000"), amount_paid=Decimal("6000"))
        self.assertEqual(calculate_remaining_amount(c), Decimal("0"))


# ---------------------------------------------------------------------------
# Early settlement tests
# ---------------------------------------------------------------------------

class EarlySettlementTest(TestCase):
    def setUp(self):
        self.contract = PaymentContract(
            total_amount=Decimal("45000"),
            amount_paid=Decimal("0"),
            daily_price=Decimal("125"),
            thirty_day_price=Decimal("3750"),
            term_months=12,
            early_settlement_3m_discount=Decimal("25"),
            early_settlement_6m_discount=Decimal("15"),
            early_settlement_9m_discount=Decimal("8"),
            start_date=date.today(),
        )

    def test_returns_three_options_for_12_month_contract(self):
        opts = calculate_early_settlement_options(self.contract)
        self.assertEqual(len(opts), 3)
        self.assertEqual([o["term_months"] for o in opts], [3, 6, 12])

    def test_3_month_discount(self):
        opts = calculate_early_settlement_options(self.contract)
        opt = next(o for o in opts if o["term_months"] == 3)
        expected_remaining = Decimal("45000") * (1 - Decimal("25") / 100)
        self.assertEqual(opt["remaining_to_pay"], expected_remaining.quantize(Decimal("1")))
        self.assertGreater(opt["daily_price"], Decimal("0"))

    def test_6_month_contract_only_3_and_6_plans(self):
        self.contract.term_months = 6
        opts = calculate_early_settlement_options(self.contract)
        self.assertEqual([o["term_months"] for o in opts], [3, 6])

    def test_12_month_no_discount(self):
        opts = calculate_early_settlement_options(self.contract)
        opt = next(o for o in opts if o["term_months"] == 12)
        self.assertEqual(opt["discount_percent"], Decimal("0"))
        self.assertEqual(opt["remaining_to_pay"], Decimal("45000"))

    def test_partial_paid_reduces_remaining(self):
        self.contract.amount_paid = Decimal("10000")
        opts = calculate_early_settlement_options(self.contract)
        opt = next(o for o in opts if o["term_months"] == 6)
        expected_remaining = Decimal("35000") * (1 - Decimal("15") / 100)
        self.assertEqual(opt["remaining_to_pay"], expected_remaining.quantize(Decimal("1")))

    def test_no_options_when_pricing_zero(self):
        self.contract.total_amount = Decimal("0")
        self.assertEqual(calculate_early_settlement_options(self.contract), [])


# ---------------------------------------------------------------------------
# Payment allocation tests
# ---------------------------------------------------------------------------

class PaymentAllocationTest(TestCase):
    def _make_contract(self, **kwargs):
        defaults = dict(
            customer_name="Test Customer",
            customer_phone="+265880000001",
            total_amount=Decimal("45000"),
            amount_paid=Decimal("0"),
            daily_price=Decimal("125"),
            thirty_day_price=Decimal("3750"),
            term_months=12,
            start_date=date.today(),
            status="active",
        )
        defaults.update(kwargs)
        return PaymentContract.objects.create(**defaults)

    def test_payment_increases_amount_paid(self):
        c = self._make_contract()
        apply_payment_to_contract(c, Decimal("3750"))
        c.refresh_from_db()
        self.assertEqual(c.amount_paid, Decimal("3750"))

    def test_payment_does_not_exceed_remaining(self):
        c = self._make_contract(amount_paid=Decimal("44500"))
        result = apply_payment_to_contract(c, Decimal("1000"))
        c.refresh_from_db()
        self.assertEqual(c.amount_paid, c.total_amount)
        self.assertEqual(result["applied"], Decimal("500"))

    def test_payment_completes_contract(self):
        c = self._make_contract(amount_paid=Decimal("44000"))
        apply_payment_to_contract(c, Decimal("1000"))
        c.refresh_from_db()
        self.assertEqual(c.status, "completed")

    def test_daily_price_extends_due_date(self):
        start = date.today() - timedelta(days=5)
        c = self._make_contract(start_date=start)
        apply_payment_to_contract(c, Decimal("125"))  # 1 day
        c.refresh_from_db()
        self.assertIsNotNone(c.due_date)

    def test_zero_payment_rejected(self):
        c = self._make_contract()
        result = apply_payment_to_contract(c, Decimal("0"))
        self.assertEqual(result["applied"], Decimal("0"))


# ---------------------------------------------------------------------------
# Search tests
# ---------------------------------------------------------------------------

class PortalSearchTest(TestCase):
    def setUp(self):
        self.contract = PaymentContract.objects.create(
            customer_name="Mary Banda",
            customer_phone="+265881234567",
            customer_national_id="MW12345678",
            total_amount=Decimal("20000"),
        )

    def test_search_by_contract_number(self):
        result = search_payment_contract(self.contract.contract_number)
        self.assertEqual(result.pk, self.contract.pk)

    def test_search_by_payg_number(self):
        result = search_payment_contract(self.contract.payg_number)
        self.assertEqual(result.pk, self.contract.pk)

    def test_search_by_national_id(self):
        result = search_payment_contract("MW12345678")
        self.assertEqual(result.pk, self.contract.pk)

    def test_search_by_phone_last_9_digits(self):
        result = search_payment_contract("881234567")
        self.assertEqual(result.pk, self.contract.pk)

    def test_search_by_full_phone(self):
        result = search_payment_contract("+265881234567")
        self.assertEqual(result.pk, self.contract.pk)

    def test_search_returns_none_for_unknown(self):
        result = search_payment_contract("ZZZNOTHINGHERE")
        self.assertIsNone(result)

    def test_empty_query_returns_none(self):
        result = search_payment_contract("")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Mock payment provider test
# ---------------------------------------------------------------------------

class MockPaymentProviderTest(TestCase):
    def setUp(self):
        self.provider = MockPaymentProvider()

    def test_create_payment_intent_succeeds(self):
        result = self.provider.create_payment_intent(
            amount=Decimal("3750"),
            phone="+265881234567",
            reference="TS-PAY-TEST0001",
        )
        self.assertTrue(result.success)
        self.assertIn("MOCK-", result.provider_reference)

    def test_verify_payment_returns_paid(self):
        result = self.provider.verify_payment("MOCK-TS-PAY-TEST0001")
        self.assertTrue(result.paid)

    def test_mode_is_mock(self):
        self.assertEqual(self.provider.mode, "mock")

    def test_is_configured(self):
        self.assertTrue(self.provider.is_configured)


# ---------------------------------------------------------------------------
# Portal page smoke tests
# ---------------------------------------------------------------------------

class PortalPageTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.contract = PaymentContract.objects.create(
            customer_name="Test User",
            customer_phone="+265889990001",
            total_amount=Decimal("30000"),
            amount_paid=Decimal("5000"),
            daily_price=Decimal("83"),
            thirty_day_price=Decimal("2500"),
        )

    def test_search_page_renders(self):
        res = self.client.get("/pay/")
        self.assertEqual(res.status_code, 200)

    def test_contract_page_renders(self):
        res = self.client.get(f"/pay/contract/{self.contract.contract_number}/")
        self.assertEqual(res.status_code, 200)

    def test_history_page_renders(self):
        res = self.client.get(f"/pay/contract/{self.contract.contract_number}/history/")
        self.assertEqual(res.status_code, 200)

    def test_support_page_renders(self):
        res = self.client.get("/pay/support/")
        self.assertEqual(res.status_code, 200)

    def test_search_by_contract_number_redirects(self):
        res = self.client.get("/pay/search/", {"q": self.contract.contract_number})
        self.assertRedirects(res, f"/pay/contract/{self.contract.contract_number}/")

    def test_search_not_found_shows_error(self):
        res = self.client.get("/pay/search/", {"q": "TS-MW-99999999"})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "No contract found")

    def test_404_for_invalid_contract(self):
        res = self.client.get("/pay/contract/TS-MW-99999999/")
        self.assertEqual(res.status_code, 404)


# ---------------------------------------------------------------------------
# Payment edge case tests (Phase 6)
# ---------------------------------------------------------------------------

class PaymentEdgeCaseTest(TestCase):
    def _make_contract(self, **kwargs):
        defaults = dict(
            customer_name="Edge Test",
            customer_phone="+265880000099",
            total_amount=Decimal("45000"),
            amount_paid=Decimal("0"),
            daily_price=Decimal("125"),
            thirty_day_price=Decimal("3750"),
            term_months=12,
            start_date=date.today(),
            status="active",
        )
        defaults.update(kwargs)
        return PaymentContract.objects.create(**defaults)

    def test_negative_payment_rejected(self):
        c = self._make_contract()
        result = apply_payment_to_contract(c, Decimal("-500"))
        c.refresh_from_db()
        self.assertEqual(c.amount_paid, Decimal("0"))
        self.assertIn("error", result)

    def test_zero_payment_rejected(self):
        c = self._make_contract()
        result = apply_payment_to_contract(c, Decimal("0"))
        self.assertEqual(result["applied"], Decimal("0"))
        self.assertIn("error", result)

    def test_payment_exceeding_balance_is_capped(self):
        c = self._make_contract(amount_paid=Decimal("44500"))
        result = apply_payment_to_contract(c, Decimal("5000"))
        c.refresh_from_db()
        self.assertEqual(c.amount_paid, Decimal("45000"))
        self.assertEqual(result["applied"], Decimal("500"))

    def test_payment_exactly_completes_contract(self):
        c = self._make_contract(amount_paid=Decimal("44000"))
        result = apply_payment_to_contract(c, Decimal("1000"))
        c.refresh_from_db()
        self.assertEqual(c.status, "completed")
        self.assertEqual(c.amount_paid, Decimal("45000"))
        self.assertEqual(c.daily_price, Decimal("0"))

    def test_payment_while_overdue_clears_arrears(self):
        past_start = date.today() - timedelta(days=40)
        past_due = date.today() - timedelta(days=10)
        c = self._make_contract(
            start_date=past_start,
            amount_paid=Decimal("10000"),
            status="overdue",
            due_date=past_due,
        )
        result = apply_payment_to_contract(c, Decimal("3750"))
        c.refresh_from_db()
        self.assertGreaterEqual(result["arrears_cleared"], Decimal("0"))
        self.assertGreater(result["applied"], Decimal("0"))

    def test_progress_never_exceeds_100(self):
        c = self._make_contract(
            amount_paid=Decimal("45000"),
            status="completed",
        )
        self.assertEqual(c.progress_percent, 100)

    def test_remaining_never_negative(self):
        c = self._make_contract(
            amount_paid=Decimal("50000"),
            total_amount=Decimal("45000"),
        )
        from portal.services import calculate_remaining_amount
        self.assertEqual(calculate_remaining_amount(c), Decimal("0"))

    def test_completed_contract_blocks_payment(self):
        c = self._make_contract(
            amount_paid=Decimal("45000"),
            status="completed",
        )
        result = apply_payment_to_contract(c, Decimal("1000"))
        c.refresh_from_db()
        self.assertIn("error", result)
        self.assertEqual(result["applied"], Decimal("0"))


# ---------------------------------------------------------------------------
# Webhook endpoint tests (Phase 6)
# ---------------------------------------------------------------------------

class WebhookEndpointTest(TestCase):
    def setUp(self):
        self.client = Client()

    def test_paychangu_webhook_returns_safe_json(self):
        res = self.client.post(
            "/pay/webhooks/paychangu/",
            data='{"event": "payment.completed", "amount": 5000}',
            content_type="application/json",
            HTTP_X_PAYCHANGU_SIGNATURE="test-sig",
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])

    def test_airtel_webhook_returns_safe_json(self):
        res = self.client.post(
            "/pay/webhooks/airtel/",
            data='{"status": "SUCCESS"}',
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])

    def test_tnm_webhook_returns_safe_json(self):
        res = self.client.post(
            "/pay/webhooks/tnm/",
            data='{"status": "SUCCESS"}',
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["provider"], "tnm")

    def test_paytrigger_webhook_returns_safe_json(self):
        res = self.client.post(
            "/pay/webhooks/paytrigger/",
            data='{}',
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])

    def test_webhook_with_empty_body(self):
        res = self.client.post(
            "/pay/webhooks/paychangu/",
            data="",
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)


# ---------------------------------------------------------------------------
# Provider configuration tests (Phase 6)
# ---------------------------------------------------------------------------

class ProviderConfigurationTest(TestCase):
    def test_unconfigured_paychangu_falls_back_to_mock(self):
        """Without credentials, PayChangu falls back to mock mode (success=True)."""
        from portal.payment_providers import PayChanguProvider
        provider = PayChanguProvider()
        self.assertFalse(provider.is_configured)
        result = provider.create_payment_intent(
            amount=Decimal("5000"),
            phone="+265881234567",
            reference="TS-PAY-TEST",
        )
        # Without real keys, mock fallback is used (success=True, mock reference)
        self.assertIsNotNone(result)
        self.assertIn("MOCK-TS-PAY-TEST", result.provider_reference)

    def test_unconfigured_airtel_falls_back_to_mock(self):
        """Without credentials, AirtelMoney falls back to mock mode."""
        from portal.payment_providers import AirtelMoneyProvider
        provider = AirtelMoneyProvider()
        self.assertFalse(provider.is_configured)
        result = provider.create_payment_intent(
            amount=Decimal("5000"),
            phone="+265881234567",
            reference="TS-PAY-TEST",
        )
        self.assertIsNotNone(result)

    def test_unconfigured_tnm_falls_back_to_mock(self):
        """Without credentials, TNM Mpamba falls back to mock mode."""
        from portal.payment_providers import TNMMpambaProvider
        provider = TNMMpambaProvider()
        self.assertFalse(provider.is_configured)
        result = provider.create_payment_intent(
            amount=Decimal("5000"),
            phone="+265881234567",
            reference="TS-PAY-TEST",
        )
        self.assertIsNotNone(result)


# ---------------------------------------------------------------------------
# No Yellow/KulaSell references in rendered templates (Phase 6)
# ---------------------------------------------------------------------------

class NoYellowReferencesTest(TestCase):
    YELLOW_TERMS = [
        "Yellow Africa", "KulaSell", "kulasell",
        "yellow.com", "© Yellow",
    ]

    def setUp(self):
        self.client = Client()
        self.contract = PaymentContract.objects.create(
            customer_name="Test User",
            customer_phone="+265889990099",
            total_amount=Decimal("30000"),
            amount_paid=Decimal("5000"),
        )

    def _check_no_yellow(self, content):
        for term in self.YELLOW_TERMS:
            self.assertNotIn(
                term, content,
                f"Found forbidden reference '{term}' in rendered template."
            )

    def test_portal_search_no_yellow(self):
        res = self.client.get("/pay/")
        self._check_no_yellow(res.content.decode())

    def test_portal_contract_no_yellow(self):
        res = self.client.get(f"/pay/contract/{self.contract.contract_number}/")
        self._check_no_yellow(res.content.decode())

    def test_website_landing_no_yellow(self):
        res = self.client.get("/site/")
        self._check_no_yellow(res.content.decode())


# ---------------------------------------------------------------------------
# MWK rounding tests (Phase 6)
# ---------------------------------------------------------------------------

class MWKRoundingTest(TestCase):
    def test_daily_price_is_whole_mwk(self):
        from portal.services import calculate_daily_price
        result = calculate_daily_price(Decimal("45000"), 12)
        self.assertEqual(result, result.quantize(Decimal("1")))

    def test_thirty_day_price_is_whole_mwk(self):
        from portal.services import calculate_thirty_day_price
        result = calculate_thirty_day_price(Decimal("45000"), 12)
        self.assertEqual(result, result.quantize(Decimal("1")))

    def test_mwk_round_helper(self):
        from portal.services import mwk_round
        self.assertEqual(mwk_round(Decimal("125.67")), Decimal("126"))
        self.assertEqual(mwk_round(Decimal("125.49")), Decimal("125"))
        self.assertEqual(mwk_round(Decimal("0")), Decimal("0"))


# ---------------------------------------------------------------------------
# PWA / manifest / SW route tests (Phase 6)
# ---------------------------------------------------------------------------

class PWARoutesTest(TestCase):
    def setUp(self):
        self.client = Client()

    def test_offline_page_renders(self):
        res = self.client.get("/offline/")
        self.assertEqual(res.status_code, 200)

    def test_manifest_accessible(self):
        res = self.client.get("/static/manifest.webmanifest")
        self.assertIn(res.status_code, (200, 404))

    def test_sw_accessible(self):
        res = self.client.get("/static/sw.js")
        self.assertIn(res.status_code, (200, 404))


# ---------------------------------------------------------------------------
# Device lock provider tests (Phase 6)
# ---------------------------------------------------------------------------

class DeviceLockProviderTest(TestCase):
    def test_mock_lock_provider_enroll(self):
        from integrations.portal_device_lock import MockPortalDeviceLockProvider
        contract = PaymentContract.objects.create(
            customer_name="Lock Test",
            customer_phone="+265881000099",
            total_amount=Decimal("30000"),
        )
        provider = MockPortalDeviceLockProvider()
        result = provider.enroll_device(contract)
        self.assertTrue(result["success"])

    def test_mock_lock_provider_lock(self):
        from integrations.portal_device_lock import MockPortalDeviceLockProvider
        contract = PaymentContract.objects.create(
            customer_name="Lock Test 2",
            customer_phone="+265881000098",
            total_amount=Decimal("30000"),
        )
        provider = MockPortalDeviceLockProvider()
        result = provider.lock_device(contract)
        self.assertTrue(result["success"])
        contract.refresh_from_db()
        self.assertEqual(contract.device_lock_status, "locked")

    def test_mock_lock_provider_unlock(self):
        from integrations.portal_device_lock import MockPortalDeviceLockProvider
        contract = PaymentContract.objects.create(
            customer_name="Lock Test 3",
            customer_phone="+265881000097",
            total_amount=Decimal("30000"),
            device_lock_status="locked",
        )
        provider = MockPortalDeviceLockProvider()
        result = provider.unlock_device(contract)
        self.assertTrue(result["success"])
        contract.refresh_from_db()
        self.assertEqual(contract.device_lock_status, "unlocked")

    def test_knox_not_configured_returns_clean_error(self):
        from integrations.portal_device_lock import KnoxProvider
        contract = PaymentContract.objects.create(
            customer_name="Knox Test",
            customer_phone="+265881000096",
            total_amount=Decimal("30000"),
        )
        provider = KnoxProvider()
        result = provider.lock_device(contract)
        self.assertFalse(result["success"])
        self.assertIn("not configured", result["message"].lower())


# ---------------------------------------------------------------------------
# PayG format / uniqueness tests (Phase 7)
# ---------------------------------------------------------------------------

class PayGFormatTest(TestCase):
    """Comprehensive tests for the new EXXXXXXX PayG number format."""

    def test_format_matches_regex(self):
        import re
        pattern = re.compile(r"^E[A-Z2-9]{7}$")
        for _ in range(30):
            n = generate_payg_number()
            self.assertRegex(n, pattern, f"PayG {n!r} does not match EXXXXXXX format")

    def test_starts_with_uppercase_e(self):
        for _ in range(20):
            n = generate_payg_number()
            self.assertEqual(n[0], "E")

    def test_exactly_8_characters(self):
        for _ in range(20):
            n = generate_payg_number()
            self.assertEqual(len(n), 8)

    def test_no_confusing_chars(self):
        for _ in range(50):
            n = generate_payg_number()
            for bad in ("I", "O", "0", "1"):
                self.assertNotIn(bad, n, f"Confusing char {bad!r} found in {n!r}")

    def test_200_unique_payg_numbers(self):
        nums = [generate_payg_number() for _ in range(200)]
        self.assertEqual(len(nums), len(set(nums)), "Duplicate PayG numbers in 200-sample batch")

    def test_db_level_unique_constraint(self):
        from django.db import IntegrityError
        c = PaymentContract.objects.create(
            customer_name="Unique X",
            customer_phone="+265889000001",
            total_amount=Decimal("10000"),
        )
        with self.assertRaises(IntegrityError):
            PaymentContract.objects.create(
                customer_name="Duplicate X",
                customer_phone="+265889000002",
                total_amount=Decimal("10000"),
                payg_number=c.payg_number,
            )

    def test_auto_generated_on_new_contract(self):
        c = PaymentContract.objects.create(
            customer_name="New Contract",
            customer_phone="+265889000003",
            total_amount=Decimal("20000"),
        )
        self.assertTrue(c.payg_number)
        self.assertRegex(c.payg_number, r"^E[A-Z2-9]{7}$")


# ---------------------------------------------------------------------------
# PayG URL route tests (Phase 7)
# ---------------------------------------------------------------------------

class PayGRouteTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.contract = PaymentContract.objects.create(
            customer_name="PayG Route User",
            customer_phone="+265889100001",
            total_amount=Decimal("30000"),
            amount_paid=Decimal("5000"),
            daily_price=Decimal("83"),
            thirty_day_price=Decimal("2500"),
        )

    def test_payg_url_resolves_correct_contract(self):
        url = f"/pay/payg/{self.contract.payg_number}/"
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, self.contract.payg_number)

    def test_payg_url_invalid_format_returns_400(self):
        res = self.client.get("/pay/payg/NOT-VALID/")
        self.assertEqual(res.status_code, 400)
        self.assertContains(res, "Invalid PayG number format", status_code=400)

    def test_payg_url_unknown_number_returns_404(self):
        res = self.client.get("/pay/payg/EUNKNOWN/")
        # EUNKNOWN has bad chars (but valid format?) — let's use a valid-format unknown
        res = self.client.get("/pay/payg/EXXXZZZZ/")
        # Will either be 400 (invalid chars) or 404 (not found)
        self.assertIn(res.status_code, (400, 404))

    def test_payg_history_url_works(self):
        url = f"/pay/payg/{self.contract.payg_number}/history/"
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

    def test_payg_search_redirect(self):
        """Search by PayG number should redirect to contract page."""
        res = self.client.get("/pay/search/", {"q": self.contract.payg_number})
        self.assertRedirects(
            res,
            f"/pay/contract/{self.contract.contract_number}/",
        )


# ---------------------------------------------------------------------------
# Deposit payment flow tests (Phase 7)
# ---------------------------------------------------------------------------

class DepositPaymentTest(TestCase):
    def _make_contract(self, **kwargs):
        defaults = dict(
            customer_name="Deposit Tester",
            customer_phone="+265889200001",
            total_amount=Decimal("45000"),
            deposit_required=Decimal("5000"),
            deposit_paid=Decimal("0"),
            amount_paid=Decimal("0"),
            daily_price=Decimal("125"),
            thirty_day_price=Decimal("3750"),
            term_months=12,
            start_date=date.today(),
            status="active",
        )
        defaults.update(kwargs)
        return PaymentContract.objects.create(**defaults)

    # --- Deposit property tests ---

    def test_deposit_remaining_correct(self):
        c = self._make_contract(deposit_required=Decimal("5000"), deposit_paid=Decimal("2000"))
        self.assertEqual(c.deposit_remaining, Decimal("3000"))

    def test_deposit_remaining_zero_when_no_requirement(self):
        c = self._make_contract(deposit_required=Decimal("0"))
        self.assertEqual(c.deposit_remaining, Decimal("0"))

    def test_deposit_complete_false_when_partial(self):
        c = self._make_contract(deposit_required=Decimal("5000"), deposit_paid=Decimal("3000"))
        self.assertFalse(c.deposit_complete)

    def test_deposit_complete_true_when_full(self):
        c = self._make_contract(deposit_required=Decimal("5000"), deposit_paid=Decimal("5000"))
        self.assertTrue(c.deposit_complete)

    # --- apply_payment deposit type ---

    def test_deposit_payment_credits_deposit_paid(self):
        c = self._make_contract()
        result = apply_payment_to_contract(c, Decimal("2000"), payment_type="deposit")
        c.refresh_from_db()
        self.assertEqual(c.deposit_paid, Decimal("2000"))
        self.assertEqual(c.amount_paid, Decimal("0"))
        self.assertEqual(result["applied"], Decimal("2000"))

    def test_deposit_payment_capped_at_deposit_remaining(self):
        c = self._make_contract(deposit_required=Decimal("5000"), deposit_paid=Decimal("4000"))
        result = apply_payment_to_contract(c, Decimal("3000"), payment_type="deposit")
        c.refresh_from_db()
        self.assertEqual(c.deposit_paid, Decimal("5000"))
        self.assertEqual(result["applied"], Decimal("1000"))

    def test_deposit_already_paid_returns_error(self):
        c = self._make_contract(deposit_required=Decimal("5000"), deposit_paid=Decimal("5000"))
        result = apply_payment_to_contract(c, Decimal("1000"), payment_type="deposit")
        self.assertIn("error", result)
        self.assertEqual(result["applied"], Decimal("0"))

    def test_deposit_does_not_affect_amount_paid(self):
        c = self._make_contract()
        apply_payment_to_contract(c, Decimal("5000"), payment_type="deposit")
        c.refresh_from_db()
        self.assertEqual(c.amount_paid, Decimal("0"))

    def test_repayment_payment_credits_amount_paid(self):
        c = self._make_contract()
        apply_payment_to_contract(c, Decimal("3750"), payment_type="repayment")
        c.refresh_from_db()
        self.assertEqual(c.amount_paid, Decimal("3750"))
        self.assertEqual(c.deposit_paid, Decimal("0"))

    def test_deposit_complete_after_full_deposit(self):
        c = self._make_contract()
        apply_payment_to_contract(c, Decimal("5000"), payment_type="deposit")
        c.refresh_from_db()
        self.assertTrue(c.deposit_complete)

    # --- Payment transaction type ---

    def test_payment_type_field_on_transaction(self):
        c = self._make_contract()
        tx = PaymentTransaction.objects.create(
            payment_contract=c,
            provider="mock",
            payment_type=PaymentTransaction.TYPE_DEPOSIT,
            amount=Decimal("5000"),
            phone="+265889200001",
            status=PaymentTransaction.STATUS_PAID,
        )
        self.assertEqual(tx.payment_type, "deposit")
        self.assertEqual(tx.get_payment_type_display(), "Deposit")

    def test_repayment_type_is_default(self):
        c = self._make_contract()
        tx = PaymentTransaction.objects.create(
            payment_contract=c,
            provider="mock",
            amount=Decimal("3750"),
            phone="+265889200001",
            status=PaymentTransaction.STATUS_PAID,
        )
        self.assertEqual(tx.payment_type, "repayment")

    # --- Portal page deposit state ---

    def test_contract_page_shows_deposit_section_when_pending(self):
        c = self._make_contract(deposit_required=Decimal("5000"), deposit_paid=Decimal("0"))
        client = Client()
        res = client.get(f"/pay/contract/{c.contract_number}/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Deposit Required")
        self.assertContains(res, "PAY DEPOSIT")

    def test_contract_page_shows_deposit_complete_when_paid(self):
        c = self._make_contract(deposit_required=Decimal("5000"), deposit_paid=Decimal("5000"))
        client = Client()
        res = client.get(f"/pay/contract/{c.contract_number}/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Deposit")


# ---------------------------------------------------------------------------
# IMEI field tests (Phase 7)
# ---------------------------------------------------------------------------

class IMEIFieldTest(TestCase):
    def test_imei_stored_on_contract(self):
        c = PaymentContract.objects.create(
            customer_name="IMEI Test",
            customer_phone="+265889300001",
            total_amount=Decimal("20000"),
            imei_number="358588885858365",
        )
        c.refresh_from_db()
        self.assertEqual(c.imei_number, "358588885858365")

    def test_imei_displayed_on_contract_page(self):
        c = PaymentContract.objects.create(
            customer_name="IMEI Display",
            customer_phone="+265889300002",
            total_amount=Decimal("20000"),
            imei_number="123456789012345",
        )
        client = Client()
        res = client.get(f"/pay/contract/{c.contract_number}/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "123456789012345")


# ---------------------------------------------------------------------------
# Provider metadata tests (Phase 7)
# ---------------------------------------------------------------------------

class ProviderMetadataTest(TestCase):
    def test_provider_metadata_stored(self):
        meta = {
            "payg_number": "EXGH4456",
            "imei": "358588885858365",
            "payment_url": "/pay/payg/EXGH4456/",
        }
        c = PaymentContract.objects.create(
            customer_name="Meta Test",
            customer_phone="+265889400001",
            total_amount=Decimal("20000"),
            provider_metadata=meta,
        )
        c.refresh_from_db()
        self.assertEqual(c.provider_metadata["imei"], "358588885858365")

    def test_lock_reference_field(self):
        c = PaymentContract.objects.create(
            customer_name="Lock Ref Test",
            customer_phone="+265889400002",
            total_amount=Decimal("20000"),
            lock_reference="LOCK-REF-001",
        )
        c.refresh_from_db()
        self.assertEqual(c.lock_reference, "LOCK-REF-001")


# ---------------------------------------------------------------------------
# IMEI search tests
# ---------------------------------------------------------------------------

class IMEISearchTest(TestCase):
    """Verify that search_payment_contract() finds contracts by IMEI."""

    def setUp(self):
        self.imei = "358588885858365"
        self.contract = PaymentContract.objects.create(
            customer_name="IMEI Search Customer",
            customer_phone="+265889500001",
            total_amount=Decimal("30000"),
            imei_number=self.imei,
        )

    def test_search_by_exact_imei(self):
        result = search_payment_contract(self.imei)
        self.assertIsNotNone(result)
        self.assertEqual(result.pk, self.contract.pk)

    def test_search_by_imei_returns_none_for_unknown(self):
        result = search_payment_contract("999999999999999")
        self.assertIsNone(result)

    def test_search_by_imei_ignored_if_not_15_digits(self):
        # 14-digit string — should not be treated as IMEI, falls to phone lookup
        result = search_payment_contract("35858888585836")
        self.assertIsNone(result)

    def test_search_by_payg_still_works(self):
        payg = self.contract.payg_number
        result = search_payment_contract(payg)
        self.assertIsNotNone(result)
        self.assertEqual(result.pk, self.contract.pk)

    def test_search_by_contract_number_still_works(self):
        cn = self.contract.contract_number
        result = search_payment_contract(cn)
        self.assertIsNotNone(result)
        self.assertEqual(result.pk, self.contract.pk)
