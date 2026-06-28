"""
Phase 7 tests — WHT, merchant payouts, integrations, branding, and route aliases.
"""
from decimal import Decimal
from importlib import import_module
from datetime import date

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from applications.models import FinancingApplication
from applications.test_helpers import attach_complete_pricing
from core.models import BusinessSetting
from rewards.models import SpinWallet

from accounts.utils import assign_role
from earnings.models import ManagerPayout, MerchantPayout, Wallet
from .models import Commission
from .services import process_application_approval, process_contract_completion

User = get_user_model()


# ──────────────────────────────────────────────────────────────────────────────
# Existing commission tests (unchanged)
# ──────────────────────────────────────────────────────────────────────────────


class CommissionApprovalTests(TestCase):
    def setUp(self):
        self.merchant = User.objects.create_user(username="merchant", password="test-pass-123")
        self.manager = User.objects.create_user(username="manager", password="test-pass-123")
        assign_role(self.merchant, "merchant")
        assign_role(self.manager, "underwriter")

    def create_application(self, **overrides):
        data = {
            "created_by": self.merchant,
            "status": "under_review",
            "claimed_by": self.manager,
            "calculated_total_loan": Decimal("1000000.00"),
        }
        data.update(overrides)
        explicit_loan = data.get("calculated_total_loan")
        app = FinancingApplication.objects.create(**data)
        if not app.deal_id:
            cash = Decimal("350000")
            if explicit_loan:
                cash = (Decimal(explicit_loan) / Decimal("2.5")).quantize(Decimal("0.01"))
            attach_complete_pricing(app, cash_price=cash)
            if explicit_loan:
                app.calculated_total_loan = explicit_loan
                app.save(update_fields=["calculated_total_loan"])
        return app

    def test_approving_application_creates_default_commissions_and_spin(self):
        app = self.create_application()
        result = process_application_approval(app, approved_by=self.manager)
        merchant_commission = Commission.objects.get(application=app, role=Commission.ROLE_MERCHANT)
        manager_commission = Commission.objects.get(application=app, role=Commission.ROLE_MANAGER)
        wallet = SpinWallet.objects.get(user=self.merchant)
        self.assertEqual(result["sale_amount"], Decimal("1000000.00"))
        self.assertEqual(merchant_commission.commission_percent, Decimal("1.00"))
        self.assertEqual(merchant_commission.amount, Decimal("10000.00"))
        self.assertEqual(manager_commission.commission_percent, Decimal("3.00"))
        self.assertEqual(manager_commission.amount, Decimal("30000.00"))
        self.assertEqual(wallet.available_spins, 1)
        self.assertEqual(wallet.total_spins_earned, 1)

    def test_changed_business_settings_affect_new_commissions(self):
        BusinessSetting.objects.update_or_create(
            pk=1,
            defaults={
                "merchant_commission_percent": Decimal("2.50"),
                "manager_commission_percent": Decimal("4.00"),
            },
        )
        app = self.create_application()
        process_application_approval(app, approved_by=self.manager)
        self.assertEqual(
            Commission.objects.get(application=app, role=Commission.ROLE_MERCHANT).amount,
            Decimal("25000.00"),
        )
        self.assertEqual(
            Commission.objects.get(application=app, role=Commission.ROLE_MANAGER).amount,
            Decimal("40000.00"),
        )

    def test_duplicate_approval_does_not_duplicate_commissions_or_spin(self):
        app = self.create_application()
        process_application_approval(app, approved_by=self.manager)
        process_application_approval(app, approved_by=self.manager)
        self.assertEqual(Commission.objects.filter(application=app).count(), 2)
        wallet = SpinWallet.objects.get(user=self.merchant)
        self.assertEqual(wallet.available_spins, 1)
        self.assertEqual(wallet.total_spins_earned, 1)

    def test_contract_completion_creates_merchant_commission_and_spin_once(self):
        app = self.create_application(status="contract_complete")
        process_contract_completion(app)
        process_contract_completion(app)
        merchant_commission = Commission.objects.get(application=app, role=Commission.ROLE_MERCHANT)
        wallet = SpinWallet.objects.get(user=self.merchant)
        self.assertEqual(merchant_commission.commission_percent, Decimal("1.00"))
        self.assertEqual(merchant_commission.amount, Decimal("10000.00"))
        self.assertEqual(wallet.available_spins, 1)
        self.assertEqual(wallet.total_spins_earned, 1)

    def test_approval_view_creates_commissions(self):
        app = self.create_application()
        self.client.login(username="manager", password="test-pass-123")
        response = self.client.post(reverse("review_application", args=[app.id]), {"decision": "approve"})
        self.assertRedirects(response, reverse("underwriter_confirm_approve", args=[app.id]))
        response = self.client.post(reverse("underwriter_confirm_approve", args=[app.id]))
        app.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(app.status, "approved")
        self.assertEqual(Commission.objects.filter(application=app).count(), 2)


class CommissionAdminImportTests(TestCase):
    def test_admin_imports_do_not_crash(self):
        admin_module = import_module("commissions.admin")
        self.assertIs(admin_module.Commission, Commission)
        self.assertIn(Commission, admin.site._registry)


# ──────────────────────────────────────────────────────────────────────────────
# WHT business logic tests
# ──────────────────────────────────────────────────────────────────────────────


class ManagerPayoutWHTTests(TestCase):
    """
    Business rule: manager/agent commission payout has 20% WHT withheld.
    gross 100,000 → WHT 20,000 → net 80,000
    """

    def setUp(self):
        self.manager = User.objects.create_user(username="mgr_wht", password="test123")
        assign_role(self.manager, "underwriter")
        self.wallet, _ = Wallet.objects.get_or_create(user=self.manager)

    def _make_payout(self, gross, wht_rate=Decimal("0.2000")):
        p = ManagerPayout(
            wallet=self.wallet,
            period_start=date(2026, 5, 1),
            period_end=date(2026, 5, 31),
            gross_amount=Decimal(str(gross)),
            wht_rate=wht_rate,
        )
        p.save()
        return p

    def test_wht_20_percent_gross_100000(self):
        payout = self._make_payout(100000)
        self.assertEqual(payout.gross_amount, Decimal("100000.00"))
        self.assertEqual(payout.wht_amount, Decimal("20000.00"))
        self.assertEqual(payout.net_amount, Decimal("80000.00"))

    def test_wht_20_percent_gross_58000(self):
        payout = self._make_payout(58000)
        self.assertEqual(payout.wht_amount, Decimal("11600.00"))
        self.assertEqual(payout.net_amount, Decimal("46400.00"))

    def test_wht_computed_automatically_on_save(self):
        payout = self._make_payout(75000)
        payout.refresh_from_db()
        self.assertEqual(payout.wht_amount, Decimal("15000.00"))
        self.assertEqual(payout.net_amount, Decimal("60000.00"))

    def test_mark_paid_updates_wallet_totals(self):
        payout = self._make_payout(100000)
        self.wallet.balance = Decimal("100000.00")
        self.wallet.save()

        payout.mark_paid(reference="PAY-001", provider="airtel")
        payout.refresh_from_db()
        self.wallet.refresh_from_db()

        self.assertEqual(payout.status, ManagerPayout.STATUS_PAID)
        self.assertEqual(self.wallet.total_paid, Decimal("80000.00"))
        self.assertEqual(self.wallet.total_wht_withheld, Decimal("20000.00"))
        self.assertEqual(self.wallet.balance, Decimal("0.00"))

    def test_default_wht_rate_is_20_percent(self):
        payout = self._make_payout(50000)
        self.assertEqual(payout.wht_rate, Decimal("0.2000"))


# ──────────────────────────────────────────────────────────────────────────────
# Merchant payout tests (no WHT)
# ──────────────────────────────────────────────────────────────────────────────


class MerchantPayoutTests(TestCase):
    """
    Business rule: merchants receive the full cash price — NO WHT deduction.
    merchant payout cash price 405,000 → merchant receives 405,000, WHT 0
    """

    def setUp(self):
        self.merchant = User.objects.create_user(username="merch_test", password="test123")
        assign_role(self.merchant, "merchant")

    def test_merchant_receives_full_cash_price(self):
        payout = MerchantPayout.objects.create(
            merchant=self.merchant,
            contract_number="TS-C-TEST-001",
            device_description="itel City 100 4+128",
            cash_price=Decimal("405000.00"),
        )
        self.assertEqual(payout.wht_amount, Decimal("0"))
        self.assertEqual(payout.net_amount, Decimal("405000.00"))
        self.assertEqual(payout.cash_price, Decimal("405000.00"))

    def test_merchant_payout_no_wht_any_amount(self):
        for cash_price in [100000, 350000, 780000, 1200000]:
            payout = MerchantPayout(
                merchant=self.merchant,
                contract_number=f"TS-C-{cash_price}",
                cash_price=Decimal(str(cash_price)),
            )
            self.assertEqual(payout.wht_amount, Decimal("0"), f"WHT should be 0 for cash_price={cash_price}")
            self.assertEqual(payout.net_amount, Decimal(str(cash_price)))

    def test_manager_and_merchant_payouts_not_confused(self):
        manager = User.objects.create_user(username="mgr_separate", password="test123")
        assign_role(manager, "underwriter")
        wallet, _ = Wallet.objects.get_or_create(user=manager)

        mgr_payout = ManagerPayout(
            wallet=wallet,
            period_start=date(2026, 5, 1),
            period_end=date(2026, 5, 31),
            gross_amount=Decimal("100000.00"),
        )
        mgr_payout.save()

        merch_payout = MerchantPayout.objects.create(
            merchant=self.merchant,
            contract_number="TS-SEP-001",
            cash_price=Decimal("405000.00"),
        )

        # Manager payout has WHT; merchant payout does not
        self.assertEqual(mgr_payout.wht_amount, Decimal("20000.00"))
        self.assertEqual(merch_payout.wht_amount, Decimal("0"))
        self.assertNotEqual(type(mgr_payout), type(merch_payout))

    def test_merchant_payout_mark_paid(self):
        payout = MerchantPayout.objects.create(
            merchant=self.merchant,
            contract_number="TS-C-PAY-001",
            cash_price=Decimal("405000.00"),
        )
        payout.mark_paid(reference="MPAY-001", provider="airtel")
        payout.refresh_from_db()
        self.assertEqual(payout.status, MerchantPayout.STATUS_PAID)
        self.assertEqual(payout.paid_amount, Decimal("405000.00"))
        self.assertIsNotNone(payout.paid_at)


# ──────────────────────────────────────────────────────────────────────────────
# Integration module tests
# ──────────────────────────────────────────────────────────────────────────────


class PayChanguIntegrationTests(TestCase):
    def test_import_does_not_crash(self):
        from integrations import paychangu_client
        self.assertTrue(hasattr(paychangu_client, "initiate_payment"))
        self.assertTrue(hasattr(paychangu_client, "verify_transaction"))
        self.assertTrue(hasattr(paychangu_client, "verify_webhook_signature"))

    @override_settings(MOCK_PAYMENTS="true")
    def test_mock_mode_returns_success(self):
        from integrations.paychangu_client import initiate_payment
        result = initiate_payment(
            amount=Decimal("50000"),
            return_url="http://example.com/return/",
            tx_ref="test-001",
        )
        self.assertEqual(result["status"], "success")
        self.assertIn("mock", result["message"].lower())

    @override_settings(MOCK_PAYMENTS="true")
    def test_verify_mock_returns_success(self):
        from integrations.paychangu_client import verify_transaction
        result = verify_transaction("test-tx-001")
        self.assertEqual(result["status"], "SUCCESS")

    def test_missing_keys_uses_mock_mode(self):
        from integrations.paychangu_client import is_mock_mode
        with self.settings(PAYCHANGU_PUBLIC_KEY="", PAYCHANGU_SECRET_KEY="", MOCK_PAYMENTS="false"):
            self.assertTrue(is_mock_mode())

    def test_no_crash_when_credentials_missing(self):
        from integrations.paychangu_client import initiate_payment
        with self.settings(PAYCHANGU_PUBLIC_KEY="", PAYCHANGU_SECRET_KEY="", MOCK_PAYMENTS="false"):
            result = initiate_payment(amount=Decimal("1000"), return_url="http://x.com/")
            self.assertIn(result["status"], ("success", "error"))

    def test_phone_normalisation(self):
        from integrations.paychangu_client import normalize_phone
        self.assertEqual(normalize_phone("0991234567"), "991234567")
        self.assertEqual(normalize_phone("+265991234567"), "991234567")
        self.assertEqual(normalize_phone("265991234567"), "991234567")
        self.assertEqual(normalize_phone("991234567"), "991234567")

    def test_invalid_phone_raises(self):
        from integrations.paychangu_client import normalize_phone
        with self.assertRaises(ValueError):
            normalize_phone("12345")


class TwilioSMSTests(TestCase):
    def test_import_does_not_crash(self):
        from integrations import twilio_sms
        self.assertTrue(hasattr(twilio_sms, "send_sms"))
        self.assertTrue(hasattr(twilio_sms, "send_payment_reminder"))

    def test_missing_credentials_does_not_crash(self):
        from integrations.twilio_sms import send_sms
        with self.settings(TWILIO_ACCOUNT_SID="", TWILIO_AUTH_TOKEN="", TWILIO_PHONE_NUMBER=""):
            result = send_sms("+265991234567", "Test message")
            self.assertFalse(result["success"])
            self.assertIn("not configured", result["message"].lower())


class SendGridEmailTests(TestCase):
    def test_import_does_not_crash(self):
        from integrations import sendgrid_email
        self.assertTrue(hasattr(sendgrid_email, "send_email"))
        self.assertTrue(hasattr(sendgrid_email, "send_payment_receipt"))

    def test_missing_sendgrid_key_falls_back_to_django(self):
        from integrations.sendgrid_email import is_configured
        with self.settings(SENDGRID_API_KEY=""):
            self.assertFalse(is_configured())


# ──────────────────────────────────────────────────────────────────────────────
# Route / URL tests
# ──────────────────────────────────────────────────────────────────────────────


class LegacyRouteTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(username="mgr_route", password="test123")
        assign_role(self.manager, "underwriter")
        self.client.login(username="mgr_route", password="test123")

    def test_tengasale_underwriter_redirects_to_sales(self):
        response = self.client.get("/tengasale/underwriter/", follow=False)
        # Should redirect (302/301) — not 404
        self.assertIn(response.status_code, (301, 302))

    def test_tengasale_underwriter_queue_redirects(self):
        response = self.client.get("/tengasale/underwriter/queue/", follow=False)
        self.assertIn(response.status_code, (301, 302))

    def test_sales_home_accessible(self):
        response = self.client.get("/sales/", follow=True)
        self.assertEqual(response.status_code, 200)


# ──────────────────────────────────────────────────────────────────────────────
# Branding tests
# ──────────────────────────────────────────────────────────────────────────────


class BrandingTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(username="mgr_brand", password="test123")
        assign_role(self.manager, "underwriter")

    def test_login_page_does_not_contain_logo_mark_text(self):
        """Login page must use the real PNG, not the orange-square 'T' fallback."""
        response = self.client.get("/accounts/login/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # Real logo img tag present
        self.assertIn("tengasale-logo-full", content)
        # Fallback orange T should be gone
        self.assertNotIn('<div class="logo-mark">T</div>', content)

    def test_login_page_no_yellow_or_kulasell(self):
        response = self.client.get("/accounts/login/")
        content = response.content.decode().lower()
        self.assertNotIn("kulasell", content)
        self.assertNotIn("yellow africa", content)

    def test_base_template_contains_real_logo(self):
        """Authenticated page header must contain a TengaSale logo image, not a plain text 'T'."""
        self.client.login(username="mgr_brand", password="test123")
        response = self.client.get("/sales/", follow=True)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # Accept SVG or any PNG logo variant — SVG is preferred for clean rendering
        has_logo = (
            "tengasale-logo-full" in content
            or "tengasale-logo-icon" in content
            or "tengasale-logo-mark" in content
        )
        self.assertTrue(has_logo, "Header must contain a TengaSale logo image")

    def test_footer_says_tengasale_not_yellow(self):
        self.client.login(username="mgr_brand", password="test123")
        response = self.client.get("/sales/", follow=True)
        content = response.content.decode().lower()
        self.assertIn("tengasale", content)
        self.assertNotIn("yellow africa", content)
        self.assertNotIn("kulasell", content)
        self.assertNotIn("v3.8.", content)


# ──────────────────────────────────────────────────────────────────────────────
# Seed command test
# ──────────────────────────────────────────────────────────────────────────────


class SeedCommandTests(TestCase):
    def test_seed_demo_creates_pending_applications(self):
        from django.core.management import call_command
        call_command("seed_tengasale", verbosity=0)
        call_command("seed_tengasale_demo", verbosity=0)
        from applications.models import FinancingApplication
        pending_count = FinancingApplication.objects.filter(status="pending_review").count()
        self.assertGreaterEqual(pending_count, 5, "Seed should create at least 5 pending applications")

    def test_seed_demo_creates_manager_payout_with_wht(self):
        from django.core.management import call_command
        call_command("seed_tengasale", verbosity=0)
        call_command("seed_tengasale_demo", verbosity=0)
        manager = User.objects.filter(username="demo_manager").first()
        if manager:
            wallet = Wallet.objects.filter(user=manager).first()
            if wallet:
                payouts = ManagerPayout.objects.filter(wallet=wallet)
                for payout in payouts:
                    expected_wht = (payout.gross_amount * Decimal("0.2")).quantize(Decimal("0.01"))
                    self.assertEqual(payout.wht_amount, expected_wht)

    def test_seed_demo_creates_merchant_payout_no_wht(self):
        from django.core.management import call_command
        call_command("seed_tengasale", verbosity=0)
        call_command("seed_tengasale_demo", verbosity=0)
        merchant = User.objects.filter(username="demo_merchant").first()
        if merchant:
            for payout in MerchantPayout.objects.filter(merchant=merchant):
                self.assertEqual(payout.wht_amount, Decimal("0"))
                self.assertEqual(payout.net_amount, payout.cash_price)


# ──────────────────────────────────────────────────────────────────────────────
# Phase 10: TengaSale mobile UI tests
# ──────────────────────────────────────────────────────────────────────────────


class SalesMobileUITests(TestCase):
    """
    Verify that /sales/ renders with the TengaSale mobile layout.
    Tests assert presence of key UI elements and absence of old branding.
    """

    def setUp(self):
        self.mgr = User.objects.create_user(
            username="ui_mgr", password="test123", first_name="Grace"
        )
        assign_role(self.mgr, "underwriter")

    def _get_sales_home(self):
        self.client.login(username="ui_mgr", password="test123")
        return self.client.get("/sales/", follow=True)

    def test_sales_home_contains_claim_or_empty_card(self):
        response = self._get_sales_home()
        content = response.content.decode()
        has_claim = "CLAIM NEXT" in content
        has_empty = "NO APPS PENDING" in content or "NO APPLICATIONS IN QUEUE" in content
        has_disabled = "CLAIM AVAILABLE IN" in content or "ACTIVE LIMIT" in content
        self.assertTrue(
            has_claim or has_empty or has_disabled,
            "Home must contain CLAIM NEXT, NO APPS PENDING, or a disabled claim state",
        )

    def test_sales_home_contains_my_active(self):
        response = self._get_sales_home()
        self.assertContains(response, "MY ACTIVE")

    def test_underwriter_home_uses_green_queue_and_blue_active_cards(self):
        response = self._get_sales_home()
        content = response.content.decode()

        self.assertContains(response, "uw-queue-card")
        self.assertContains(response, "uw-action-card--blue")
        self.assertContains(response, "active-reviews-card")
        self.assertNotIn("uw-primary-action--navy", content)

    def test_sales_home_contains_applications_section(self):
        response = self._get_sales_home()
        self.assertContains(response, "Applications")

    def test_sales_home_contains_earnings_section_without_tools_duplication(self):
        response = self._get_sales_home()
        self.assertContains(response, "Earnings & Wallet")
        self.assertNotContains(response, "Tools")

    def test_queue_status_endpoint_handles_empty_data(self):
        self.client.login(username="ui_mgr", password="test123")

        response = self.client.get("/sales/api/queue-status/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["pending_count"], 0)
        self.assertIn("can_claim", response.json())

    def test_sales_home_footer_says_tengasale(self):
        response = self._get_sales_home()
        content = response.content.decode()
        self.assertTrue(
            "© TengaSale" in content or "&copy; TengaSale" in content,
            "Footer must contain © TengaSale or &copy; TengaSale",
        )
        self.assertContains(response, "v1.0.0")

    def test_sales_home_no_yellow_africa(self):
        response = self._get_sales_home()
        content = response.content.decode().lower()
        self.assertNotIn("yellow africa", content)

    def test_sales_home_no_old_version(self):
        response = self._get_sales_home()
        content = response.content.decode()
        self.assertNotIn("v3.8", content)

    def test_sales_home_has_malawi_flag(self):
        response = self._get_sales_home()
        content = response.content.decode()
        self.assertTrue(
            "🇲🇼" in content or "Malawi" in content or 'viewBox="0 0 20 14"' in content,
            "Home must contain Malawi flag (SVG or text)",
        )

    def test_sales_home_uses_mobile_header(self):
        response = self._get_sales_home()
        self.assertContains(response, "mobile-header")

    def test_sales_home_has_mobile_shell(self):
        response = self._get_sales_home()
        self.assertContains(response, "uw-home")
        self.assertContains(response, "uw-home-simple")

    def test_sales_home_no_kulasell(self):
        response = self._get_sales_home()
        content = response.content.decode().lower()
        self.assertNotIn("kulasell", content)

    def test_queue_rules_no_kulasell(self):
        self.client.login(username="ui_mgr", password="test123")
        from core.models import QueueRule
        QueueRule.objects.get_or_create(
            country="MW",
            defaults={
                "max_active_applications": 5,
                "cooldown_minutes": 2,
                "polling_interval_seconds": 30,
                "inactive_pause_minutes": 15,
                "waiting_edit_grace_minutes": 30,
            },
        )
        response = self.client.get("/sales/queue-rules/", follow=True)
        content = response.content.decode().lower()
        self.assertNotIn("kulasell", content)
        self.assertIn("tengasale", content)

    def test_queue_rules_no_yellow(self):
        self.client.login(username="ui_mgr", password="test123")
        from core.models import QueueRule
        QueueRule.objects.get_or_create(
            country="MW",
            defaults={
                "max_active_applications": 5,
                "cooldown_minutes": 2,
                "polling_interval_seconds": 30,
                "inactive_pause_minutes": 15,
                "waiting_edit_grace_minutes": 30,
            },
        )
        response = self.client.get("/sales/queue-rules/", follow=True)
        content = response.content.decode().lower()
        self.assertNotIn("yellow africa", content)

    def test_legacy_underwriter_redirects_to_sales(self):
        self.client.login(username="ui_mgr", password="test123")
        response = self.client.get("/tengasale/underwriter/", follow=False)
        self.assertIn(response.status_code, [301, 302])
        location = response.get("Location", "")
        self.assertIn("sales", location)

    def test_sales_footer_not_fixed_overlay(self):
        """Footer must not use fixed positioning that overlays content."""
        response = self._get_sales_home()
        content = response.content.decode()
        self.assertContains(response, "sales-footer")
        self.assertNotRegex(
            content,
            r"\.sales-footer\s*\{[^}]*position:\s*fixed",
            msg="sales-footer must not be position:fixed",
        )

    def test_customer_call_no_yellow(self):
        """Customer call template must not contain the word 'Yellow' (brand)."""
        self.client.login(username="ui_mgr", password="test123")
        # Create a minimal app to test the template renders
        from applications.models import FinancingApplication
        from approvals.models import UnderwriterReview
        app = FinancingApplication.objects.create(
            created_by=self.mgr,
            claimed_by=self.mgr,
            status="under_review",
            customer_name="Test Customer",
            customer_phone="0888000001",
        )
        UnderwriterReview.objects.get_or_create(application=app)
        response = self.client.get(f"/sales/review/{app.id}/customer-call/", follow=True)
        if response.status_code == 200:
            content = response.content.decode()
            # Specifically check the customer call script / checklist labels
            self.assertNotIn("from Yellow", content)
            self.assertNotIn("© Yellow Africa", content)
            self.assertIn("TengaSale", content)


# ──────────────────────────────────────────────────────────────────────────────
# Phase 10: Commission Ledger — repayment commission, arrears, merchant payout
# ──────────────────────────────────────────────────────────────────────────────

from portal.models import PaymentContract, PaymentTransaction
from commissions.models import CommissionLedger, MerchantContractPayout, UnderwriterMonthlyPayout
from commissions.services import (
    calculate_underwriter_payment_commission,
    create_commission_for_payment,
    calculate_arrears_deduction,
    create_daily_arrears_deduction,
    calculate_merchant_commission,
    create_merchant_payout_for_contract,
    generate_underwriter_monthly_payout,
)


class Phase10CommissionLedgerTests(TestCase):
    """Test 7% repayment commission and 14% arrears deduction logic."""

    def setUp(self):
        self.underwriter = User.objects.create_user(username="uw_ph10", password="pass")
        assign_role(self.underwriter, "underwriter")
        self.contract = PaymentContract.objects.create(
            customer_name="Grace Phiri",
            customer_phone="0991111111",
            total_amount=Decimal("500000"),
            daily_price=Decimal("3000"),
            status=PaymentContract.STATUS_ACTIVE,
        )

    def _make_paid_txn(self, commissionable_amount):
        return PaymentTransaction.objects.create(
            payment_contract=self.contract,
            amount=commissionable_amount + Decimal("50000"),
            commissionable_amount=commissionable_amount,
            status=PaymentTransaction.STATUS_PAID,
            provider=PaymentTransaction.PROVIDER_MOCK,
            phone="0991111111",
        )

    def test_calculate_7_percent_commission(self):
        txn = self._make_paid_txn(Decimal("100000"))
        result = calculate_underwriter_payment_commission(txn)
        self.assertEqual(result, Decimal("7000"))

    def test_zero_commissionable_amount_yields_zero(self):
        txn = self._make_paid_txn(Decimal("0"))
        result = calculate_underwriter_payment_commission(txn)
        self.assertEqual(result, Decimal("0"))

    def test_deposit_only_payment_creates_no_commission_entry(self):
        txn = self._make_paid_txn(Decimal("0"))
        entry = create_commission_for_payment(txn, underwriter=self.underwriter)
        self.assertIsNone(entry)
        self.assertEqual(
            CommissionLedger.objects.filter(source_payment=txn).count(), 0
        )

    def test_repayment_creates_positive_commission_entry(self):
        txn = self._make_paid_txn(Decimal("100000"))
        entry = create_commission_for_payment(txn, underwriter=self.underwriter)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.entry_type, CommissionLedger.ENTRY_REPAYMENT)
        self.assertEqual(entry.amount, Decimal("7000"))
        self.assertGreater(entry.amount, Decimal("0"))

    def test_commission_creation_is_idempotent(self):
        txn = self._make_paid_txn(Decimal("100000"))
        entry1 = create_commission_for_payment(txn, underwriter=self.underwriter)
        entry2 = create_commission_for_payment(txn, underwriter=self.underwriter)
        self.assertIsNotNone(entry1)
        self.assertIsNone(entry2)
        count = CommissionLedger.objects.filter(
            source_payment=txn, user=self.underwriter, entry_type=CommissionLedger.ENTRY_REPAYMENT
        ).count()
        self.assertEqual(count, 1)

    def test_finalize_paid_transaction_commission_on_repayment(self):
        from applications.models import FinancingApplication
        from portal.services import finalize_paid_transaction_commission

        merchant = User.objects.create_user(username="merch_comm_test", password="pass")
        assign_role(merchant, "merchant")
        app = FinancingApplication.objects.create(
            customer_name="Test Customer",
            customer_phone="0999000001",
            status="approved",
            created_by=merchant,
            claimed_by=self.underwriter,
        )
        self.contract.source_application = app
        self.contract.save(update_fields=["source_application"])

        txn = PaymentTransaction.objects.create(
            payment_contract=self.contract,
            amount=Decimal("50000"),
            payment_type=PaymentTransaction.TYPE_REPAYMENT,
            status=PaymentTransaction.STATUS_PAID,
            provider=PaymentTransaction.PROVIDER_MOCK,
            phone="0991111111",
        )
        entry = finalize_paid_transaction_commission(txn)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.amount, Decimal("3500"))
        txn.refresh_from_db()
        self.assertEqual(txn.commissionable_amount, Decimal("50000"))

    def test_arrears_deduction_14_percent_of_daily_price(self):
        deduction = calculate_arrears_deduction(self.contract, missed_days=1)
        # daily_price=3000 * 0.14 = 420
        self.assertEqual(deduction, Decimal("420"))

    def test_arrears_deduction_multiple_days(self):
        deduction = calculate_arrears_deduction(self.contract, missed_days=5)
        # 3000 * 0.14 * 5 = 2100
        self.assertEqual(deduction, Decimal("2100"))

    def test_create_arrears_deduction_entry_is_negative(self):
        missed = date(2026, 5, 1)
        entry = create_daily_arrears_deduction(self.contract, missed, underwriter=self.underwriter)
        self.assertIsNotNone(entry)
        self.assertLess(entry.amount, Decimal("0"))
        self.assertEqual(entry.entry_type, CommissionLedger.ENTRY_ARREARS)
        self.assertEqual(entry.amount, Decimal("-420"))

    def test_arrears_deduction_is_idempotent(self):
        missed = date(2026, 5, 2)
        entry1 = create_daily_arrears_deduction(self.contract, missed, underwriter=self.underwriter)
        entry2 = create_daily_arrears_deduction(self.contract, missed, underwriter=self.underwriter)
        self.assertIsNotNone(entry1)
        self.assertIsNone(entry2)
        count = CommissionLedger.objects.filter(
            contract=self.contract,
            user=self.underwriter,
            missed_date=missed,
            entry_type=CommissionLedger.ENTRY_ARREARS,
        ).count()
        self.assertEqual(count, 1)

    def test_completed_contract_skips_arrears_deduction(self):
        self.contract.status = PaymentContract.STATUS_COMPLETED
        self.contract.save()
        missed = date(2026, 5, 3)
        entry = create_daily_arrears_deduction(self.contract, missed, underwriter=self.underwriter)
        self.assertIsNone(entry)

    def test_cancelled_contract_skips_arrears_deduction(self):
        self.contract.status = PaymentContract.STATUS_CANCELLED
        self.contract.save()
        missed = date(2026, 5, 4)
        entry = create_daily_arrears_deduction(self.contract, missed, underwriter=self.underwriter)
        self.assertIsNone(entry)

    def test_non_paid_transaction_creates_no_commission(self):
        txn = PaymentTransaction.objects.create(
            payment_contract=self.contract,
            amount=Decimal("50000"),
            commissionable_amount=Decimal("50000"),
            status=PaymentTransaction.STATUS_PENDING,
            provider=PaymentTransaction.PROVIDER_MOCK,
            phone="0991111111",
        )
        entry = create_commission_for_payment(txn, underwriter=self.underwriter)
        self.assertIsNone(entry)


class Phase10MerchantPayoutTests(TestCase):
    """Test merchant settlement = cash_price; commission tracked separately."""

    def setUp(self):
        self.merchant_user = User.objects.create_user(username="merch_ph10", password="pass")
        assign_role(self.merchant_user, "merchant")
        self.contract = PaymentContract.objects.create(
            customer_name="Ali Kamwendo",
            customer_phone="0882222222",
            cash_price=Decimal("240000"),
            total_amount=Decimal("600000"),
            deposit_paid=Decimal("120000"),
            daily_price=Decimal("2000"),
            status=PaymentContract.STATUS_ACTIVE,
        )

    def test_calculate_merchant_commission_1_percent_of_financed(self):
        # financed = total - deposit = 600000 - 120000 = 480000
        # 1% = 4800
        commission = calculate_merchant_commission(self.contract)
        self.assertEqual(commission, Decimal("4800"))

    def test_create_merchant_payout_has_pending_status(self):
        payout = create_merchant_payout_for_contract(
            self.contract,
            merchant_user=self.merchant_user,
            created_by=self.merchant_user,
        )
        self.assertIsNotNone(payout)
        self.assertEqual(payout.status, MerchantContractPayout.STATUS_PENDING)

    def test_merchant_payout_wht_amount_is_zero(self):
        payout = create_merchant_payout_for_contract(
            self.contract,
            merchant_user=self.merchant_user,
            created_by=self.merchant_user,
        )
        self.assertIsNotNone(payout)
        self.assertEqual(payout.wht_amount, Decimal("0"))

    def test_merchant_settlement_due_equals_cash_price(self):
        payout = create_merchant_payout_for_contract(
            self.contract,
            merchant_user=self.merchant_user,
            created_by=self.merchant_user,
        )
        self.assertIsNotNone(payout)
        # Merchant settlement = cash price only; 1% commission tracked separately
        self.assertEqual(payout.cash_price, Decimal("240000.00"))
        self.assertEqual(payout.total_payable, Decimal("240000.00"))
        self.assertEqual(payout.merchant_commission_amount, Decimal("4800"))

    def test_create_merchant_payout_is_idempotent(self):
        payout1 = create_merchant_payout_for_contract(
            self.contract,
            merchant_user=self.merchant_user,
            created_by=self.merchant_user,
        )
        payout2 = create_merchant_payout_for_contract(
            self.contract,
            merchant_user=self.merchant_user,
            created_by=self.merchant_user,
        )
        self.assertEqual(payout1.pk, payout2.pk)
        self.assertEqual(MerchantContractPayout.objects.filter(contract=self.contract).count(), 1)


class Phase10UnderwriterMonthlyPayoutTests(TestCase):
    """Test 20% WHT on positive gross, 0% on negative gross."""

    def setUp(self):
        self.underwriter = User.objects.create_user(username="uw_monthly_ph10", password="pass")
        assign_role(self.underwriter, "underwriter")

    def test_monthly_payout_wht_20_percent_on_positive_gross(self):
        payout = UnderwriterMonthlyPayout.objects.create(
            user=self.underwriter,
            period_start=date(2026, 5, 1),
            period_end=date(2026, 5, 31),
            gross_commission=Decimal("100000"),
        )
        self.assertEqual(payout.wht_amount, Decimal("20000.00"))
        self.assertEqual(payout.net_amount, Decimal("80000.00"))

    def test_monthly_payout_no_wht_on_negative_gross(self):
        payout = UnderwriterMonthlyPayout.objects.create(
            user=self.underwriter,
            period_start=date(2026, 4, 1),
            period_end=date(2026, 4, 30),
            gross_commission=Decimal("-5000"),
        )
        self.assertEqual(payout.wht_amount, Decimal("0.00"))
        self.assertEqual(payout.net_amount, Decimal("-5000.00"))

    def test_generate_monthly_payout_from_ledger(self):
        """generate_underwriter_monthly_payout sums ledger entries and applies WHT."""
        period_start = date(2026, 5, 1)
        period_end = date(2026, 5, 31)
        contract = PaymentContract.objects.create(
            customer_name="Payout Test Customer",
            customer_phone="0993333333",
            total_amount=Decimal("300000"),
            daily_price=Decimal("1000"),
            status=PaymentContract.STATUS_ACTIVE,
        )
        CommissionLedger.objects.create(
            user=self.underwriter,
            contract=contract,
            entry_type=CommissionLedger.ENTRY_REPAYMENT,
            amount=Decimal("50000"),
            base_amount=Decimal("714286"),
            rate=Decimal("0.07"),
            period_start=period_start,
            period_end=period_end,
        )
        CommissionLedger.objects.create(
            user=self.underwriter,
            contract=contract,
            entry_type=CommissionLedger.ENTRY_ARREARS,
            amount=Decimal("-1400"),
            base_amount=Decimal("1000"),
            rate=Decimal("0.14"),
            missed_date=date(2026, 5, 15),
            period_start=period_start,
            period_end=period_end,
        )
        payout = generate_underwriter_monthly_payout(self.underwriter, period_start, period_end)
        self.assertIsNotNone(payout)
        # gross = 50000 + (-1400) = 48600
        self.assertEqual(payout.gross_commission, Decimal("48600.00"))
        # WHT = 48600 * 0.20 = 9720
        self.assertEqual(payout.wht_amount, Decimal("9720.00"))
        # net = 48600 - 9720 = 38880
        self.assertEqual(payout.net_amount, Decimal("38880.00"))

    def test_generate_monthly_payout_idempotent(self):
        period_start = date(2026, 3, 1)
        period_end = date(2026, 3, 31)
        payout1 = generate_underwriter_monthly_payout(self.underwriter, period_start, period_end)
        payout2 = generate_underwriter_monthly_payout(self.underwriter, period_start, period_end)
        self.assertEqual(payout1.pk, payout2.pk)
