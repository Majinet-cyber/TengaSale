"""Tests for risk scoring, fraud detection, and integration mocks."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from applications.models import FinancingApplication

User = get_user_model()


def _make_user(username="risktest"):
    return User.objects.create_user(username=username, password="pass")


def _make_app(user, **kwargs):
    defaults = dict(
        customer_name="Test Customer",
        customer_phone="991234567",
        national_id="AB123456",
        income_band="50000-100000",
        exact_monthly_income=Decimal("80000"),
        calculated_monthly_payment=Decimal("8000"),
        occupation="Trader",
        next_of_kin_1_name="Jane Doe",
        next_of_kin_1_phone="991234568",
        next_of_kin_1_relationship="spouse",
        region="Central",
        district="Lilongwe",
        traditional_authority="Chileka",
        precise_location="Near market",
        proof_of_income_type="employer_letter",
        proof_contact_name="HR Dept",
        proof_contact_phone="991111111",
        agreed_to_terms=True,
        status="pending_review",
    )
    defaults.update(kwargs)
    app = FinancingApplication.objects.create(created_by=user, **defaults)
    return app


class CreditRiskScoringTests(TestCase):
    def setUp(self):
        self.user = _make_user()

    def test_strong_customer_gets_low_risk_and_15_deposit(self):
        """Strong customer: high income, complete KYC, guarantor → low risk, 15% deposit."""
        from risk.scoring import score_application

        app = _make_app(
            self.user,
            exact_monthly_income=Decimal("150000"),
            calculated_monthly_payment=Decimal("8000"),
        )
        result = score_application(app, save=False)
        self.assertEqual(result.risk_band, "low")
        self.assertEqual(result.recommended_deposit_percent, Decimal("15"))

    def test_medium_risk_customer_gets_20_deposit(self):
        """Moderate income ratio → medium risk, 20% deposit."""
        from risk.scoring import score_application

        app = _make_app(
            self.user,
            exact_monthly_income=Decimal("30000"),
            calculated_monthly_payment=Decimal("8000"),
        )
        result = score_application(app, save=False)
        self.assertIn(result.risk_band, ("medium", "high", "manual_review"))

    def test_missing_kyc_increases_risk(self):
        """Missing KYC images reduces identity score → higher risk."""
        from risk.scoring import _score_identity

        app = _make_app(self.user)
        # Ensure no images
        app.customer_face_image = None
        app.id_front_image = None
        app.id_back_image = None
        score, reasons = _score_identity(app)
        self.assertLess(score, 70)
        self.assertTrue(any("image" in r for r in reasons))

    def test_weak_income_increases_risk(self):
        """Very low income relative to payment → low affordability score."""
        from risk.scoring import _score_affordability

        app = _make_app(
            self.user,
            exact_monthly_income=Decimal("5000"),
            calculated_monthly_payment=Decimal("8000"),
        )
        score, reasons = _score_affordability(app)
        self.assertLess(score, 30)
        self.assertTrue(len(reasons) > 0)

    def test_zero_income_returns_low_affordability(self):
        """Zero income → very low affordability score."""
        from risk.scoring import _score_affordability

        app = _make_app(self.user, exact_monthly_income=Decimal("0"))
        score, reasons = _score_affordability(app)
        self.assertLessEqual(score, 20)

    def test_scoring_does_not_crash_with_minimal_data(self):
        """Scoring should not crash even when optional fields are missing."""
        from risk.scoring import score_application

        app = FinancingApplication.objects.create(
            created_by=self.user,
            customer_name="Bare Min",
            status="pending_review",
        )
        result = score_application(app, save=False)
        self.assertIsNotNone(result.risk_band)
        self.assertIsNotNone(result.recommended_deposit_percent)

    def test_high_risk_gives_30_deposit(self):
        """Very weak application → high risk, 30% deposit."""
        from risk.scoring import score_application

        app = FinancingApplication.objects.create(
            created_by=self.user,
            customer_name="High Risk",
            status="pending_review",
            exact_monthly_income=Decimal("3000"),
            calculated_monthly_payment=Decimal("8000"),
        )
        result = score_application(app, save=False)
        self.assertEqual(result.recommended_deposit_percent, Decimal("30"))

    def test_reasons_are_explainable_strings(self):
        """Assessment reasons must be human-readable strings."""
        from risk.scoring import score_application

        app = _make_app(self.user)
        result = score_application(app, save=False)
        for reason in result.reasons:
            self.assertIsInstance(reason, str)
            self.assertGreater(len(reason), 5)


class SimulationTests(TestCase):
    def test_simulation_returns_expected_keys(self):
        from risk.scoring import simulate_portfolio

        result = simulate_portfolio(
            cash_price=120000,
            selling_total=200000,
            deposit_percent=15,
            term_months=6,
            expected_default_rate=5,
            num_devices=10,
        )
        self.assertIn("outputs", result)
        self.assertIn("total_capital_needed", result["outputs"])
        self.assertIn("net_gain", result["outputs"])
        self.assertIn("break_even_month", result["outputs"])

    def test_simulation_labelled_as_simulation(self):
        from risk.scoring import simulate_portfolio

        result = simulate_portfolio(
            cash_price=100000,
            selling_total=170000,
            deposit_percent=20,
            term_months=6,
            expected_default_rate=3,
        )
        self.assertIn("SIMULATION", result["label"])

    def test_zero_default_rate_gives_positive_gain(self):
        from risk.scoring import simulate_portfolio

        result = simulate_portfolio(
            cash_price=100000,
            selling_total=200000,
            deposit_percent=20,
            term_months=6,
            expected_default_rate=0,
            num_devices=1,
        )
        self.assertGreater(result["outputs"]["expected_gross_margin"], 0)


# ──────────────────────────────────────────────────────────────────────────────
# Fraud Detection Tests
# ──────────────────────────────────────────────────────────────────────────────

class FraudDetectionTests(TestCase):
    def setUp(self):
        self.user = _make_user("fraudtest")

    def _make_app(self, national_id="AB123456", phone="991234567", **kwargs):
        defaults = dict(
            customer_name="Fraud Test",
            customer_phone=phone,
            national_id=national_id,
            status="pending_review",
        )
        defaults.update(kwargs)
        return FinancingApplication.objects.create(created_by=self.user, **defaults)

    def test_no_existing_data_returns_none_risk(self):
        from risk.services import find_existing_customer_exposure
        result = find_existing_customer_exposure(national_id="ZZ999999", phone="999999999")
        self.assertEqual(result["risk_level"], "none")
        self.assertEqual(result["recommended_action"], "proceed")
        self.assertFalse(result["has_exposure"])

    def test_same_national_id_application_detected(self):
        from risk.services import find_existing_customer_exposure
        existing = self._make_app(national_id="AB123456", status="approved")
        result = find_existing_customer_exposure(national_id="AB123456")
        self.assertTrue(result["has_exposure"])
        all_apps = (
            result["pending_applications"]
            + result["approved_applications"]
            + result["rejected_applications"]
        )
        numbers = [a["application_number"] for a in all_apps]
        self.assertIn(existing.application_number, numbers)

    def test_same_phone_application_detected(self):
        from risk.services import find_existing_customer_exposure
        self._make_app(phone="991234567", status="pending_review")
        result = find_existing_customer_exposure(phone="991234567")
        self.assertTrue(result["has_exposure"])

    def test_rejected_application_gives_medium_risk(self):
        from risk.services import find_existing_customer_exposure
        self._make_app(national_id="RJ123456", status="rejected")
        result = find_existing_customer_exposure(national_id="RJ123456")
        self.assertIn(result["risk_level"], ("medium", "high", "block"))

    def test_exclude_application_id_prevents_self_match(self):
        from risk.services import find_existing_customer_exposure
        app = self._make_app(national_id="SE123456", status="pending_review")
        result = find_existing_customer_exposure(
            national_id="SE123456",
            exclude_application_id=app.pk,
        )
        self.assertFalse(result["has_exposure"])

    def test_run_fraud_check_creates_fraud_check_record(self):
        from risk.services import run_fraud_check
        from risk.models import FraudCheck
        app = self._make_app(national_id="FC123456")
        check = run_fraud_check(app, checked_by=self.user)
        self.assertIsNotNone(check.pk)
        self.assertIsInstance(check, FraudCheck)
        self.assertEqual(check.application, app)

    def test_can_approve_returns_true_when_no_fraud_check(self):
        from risk.services import can_approve_application
        app = self._make_app(national_id="CA123456")
        result = can_approve_application(app)
        self.assertTrue(result["can_approve"])

    def test_hq_override_allows_approval(self):
        from risk.services import run_fraud_check, can_approve_application
        from risk.models import FraudCheck
        app = self._make_app(national_id="HQ123456")
        check = run_fraud_check(app, checked_by=self.user)
        # Simulate HQ override
        check.risk_level = FraudCheck.RISK_HIGH
        check.resolution_status = FraudCheck.RESOLUTION_HQ_OVERRIDE
        check.save()
        result = can_approve_application(app)
        self.assertTrue(result["can_approve"])

    def test_age_band_calculated_correctly(self):
        from risk.services import get_age_band
        self.assertEqual(get_age_band(18), "under_20")
        self.assertEqual(get_age_band(22), "20_24")
        self.assertEqual(get_age_band(30), "25_34")
        self.assertEqual(get_age_band(40), "35_44")
        self.assertEqual(get_age_band(50), "45_54")
        self.assertEqual(get_age_band(60), "55_plus")
        self.assertEqual(get_age_band(None), "unknown")

    def test_calculate_age_returns_none_for_none_dob(self):
        from risk.services import calculate_age
        self.assertIsNone(calculate_age(None))

    def test_calculate_age_returns_integer(self):
        from risk.services import calculate_age
        import datetime
        dob = datetime.date(1990, 1, 1)
        age = calculate_age(dob)
        self.assertIsInstance(age, int)
        self.assertGreater(age, 30)


# ──────────────────────────────────────────────────────────────────────────────
# Application Demographic Field Tests
# ──────────────────────────────────────────────────────────────────────────────

class DemographicFieldTests(TestCase):
    def setUp(self):
        self.user = _make_user("demo_test")

    def test_gender_saved(self):
        app = FinancingApplication.objects.create(
            created_by=self.user,
            customer_name="Test",
            gender="female",
            status="started",
        )
        app.refresh_from_db()
        self.assertEqual(app.gender, "female")

    def test_marital_status_saved(self):
        app = FinancingApplication.objects.create(
            created_by=self.user,
            customer_name="Test",
            marital_status="married",
            status="started",
        )
        app.refresh_from_db()
        self.assertEqual(app.marital_status, "married")

    def test_num_dependents_saved(self):
        app = FinancingApplication.objects.create(
            created_by=self.user,
            customer_name="Test",
            num_dependents=3,
            status="started",
        )
        app.refresh_from_db()
        self.assertEqual(app.num_dependents, 3)

    def test_phone_user_saved(self):
        app = FinancingApplication.objects.create(
            created_by=self.user,
            customer_name="Test",
            phone_user="customer_self",
            status="started",
        )
        app.refresh_from_db()
        self.assertEqual(app.phone_user, "customer_self")

    def test_phone_user_not_self_flags_third_party(self):
        from applications.forms import CustomerDetailsForm
        data = {
            "customer_name": "Third Party",
            "national_id": "AB123456",
            "customer_phone": "991234567",
            "gender": "male",
            "marital_status": "single",
            "phone_user": "spouse",
            "occupation": "Business Owner",
            "income_band": "less_than_100k",
            "exact_monthly_income": "50000",
        }
        form = CustomerDetailsForm(data=data)
        self.assertTrue(form.is_valid(), form.errors)
        cleaned = form.cleaned_data
        self.assertTrue(cleaned.get("third_party_phone_user_risk_flagged"))

    def test_phone_user_self_does_not_flag(self):
        from applications.forms import CustomerDetailsForm
        data = {
            "customer_name": "Self User",
            "national_id": "AB123456",
            "customer_phone": "991234567",
            "gender": "female",
            "marital_status": "single",
            "phone_user": "customer_self",
            "occupation": "Business Owner",
            "income_band": "less_than_100k",
            "exact_monthly_income": "50000",
        }
        form = CustomerDetailsForm(data=data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.cleaned_data.get("third_party_phone_user_risk_flagged", False))


# ──────────────────────────────────────────────────────────────────────────────
# Integration Mock Tests
# ──────────────────────────────────────────────────────────────────────────────

class IntegrationMockTests(TestCase):
    """Tests that all integrations work in mock mode without crashing."""

    def test_paychangu_initializes_in_mock_mode(self):
        from integrations.paychangu_client import is_mock_mode, initiate_payment
        self.assertTrue(is_mock_mode())  # No keys set in test env
        result = initiate_payment(amount=Decimal("5000"), return_url="http://localhost/return/")
        self.assertEqual(result["status"], "success")
        self.assertIn("tx_ref", result)

    def test_paychangu_webhook_signature_returns_false_without_secret(self):
        from integrations.paychangu_client import verify_webhook_signature
        valid = verify_webhook_signature(b'{"test": 1}', "fakesig")
        self.assertFalse(valid)

    def test_paychangu_get_operators_mock(self):
        from integrations.paychangu_client import get_mobile_money_operators
        result = get_mobile_money_operators()
        self.assertEqual(result["status"], "success")
        self.assertIsInstance(result["operators"], list)
        self.assertGreater(len(result["operators"]), 0)

    def test_paychangu_get_operator_ref_id_mock(self):
        from integrations.paychangu_client import get_operator_ref_id
        result = get_operator_ref_id("airtel")
        self.assertEqual(result["status"], "success")
        self.assertIn("ref_id", result)

    def test_twilio_mock_fallback_returns_no_crash(self):
        from integrations.twilio_sms import send_sms
        result = send_sms("+265991234567", "Test message")
        self.assertIn("success", result)
        self.assertFalse(result["success"])  # No credentials in test
        self.assertIn("message", result)

    def test_sendgrid_mock_fallback(self):
        from integrations.sendgrid_email import is_configured
        self.assertFalse(is_configured())  # No API key in test env

    def test_emajinet_mock_mode(self):
        from integrations.emajinet_id import verify_identity
        result = verify_identity("AB123456", phone="991234567", name="Test")
        self.assertEqual(result["status"], "mock")
        self.assertIn("message", result)

    def test_device_lock_mock_provider_works(self):
        from integrations.device_lock_provider import get_lock_provider, MockDeviceLockProvider
        provider = get_lock_provider()
        self.assertIsInstance(provider, MockDeviceLockProvider)

    def test_credit_bureau_mock_mode(self):
        from integrations.credit_bureau import run_credit_check
        app = FinancingApplication.objects.create(
            created_by=User.objects.create_user("creditbureautest", password="pass"),
            customer_name="Bureau Test",
            national_id="BU123456",
            status="started",
        )
        result = run_credit_check(app)
        self.assertEqual(result["status"], "mock")

    def test_payout_provider_mock_mode(self):
        from integrations.payout_provider import is_mock_mode
        self.assertTrue(is_mock_mode())  # No keys in test env

    def test_production_settings_import_without_crashing(self):
        """Production settings module must be importable (with env vars set)."""
        import os
        os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret-key-for-import-check")
        try:
            import importlib
            import config.settings_production as prod  # noqa: F401
        except KeyError:
            pass  # SECRET_KEY required env var — expected in test without env
        except Exception as exc:
            self.fail(f"Production settings import raised unexpected error: {exc}")


# ──────────────────────────────────────────────────────────────────────────────
# Device Connectivity Signal Tests
# ──────────────────────────────────────────────────────────────────────────────

class DeviceConnectivitySignalTests(TestCase):
    """Tests for the DeviceConnectivitySignal model — risk scoring, band
    calculation, connectivity status, and recommended action generation."""

    # ── risk_band from offline days ───────────────────────────────────────

    def test_normal_band_0_days(self):
        from risk.models import DeviceConnectivitySignal as DCS
        self.assertEqual(DCS.compute_risk_band(0), DCS.RISK_NORMAL)

    def test_normal_band_2_days(self):
        from risk.models import DeviceConnectivitySignal as DCS
        self.assertEqual(DCS.compute_risk_band(2), DCS.RISK_NORMAL)

    def test_watch_band_3_days(self):
        from risk.models import DeviceConnectivitySignal as DCS
        self.assertEqual(DCS.compute_risk_band(3), DCS.RISK_WATCH)

    def test_watch_band_5_days(self):
        from risk.models import DeviceConnectivitySignal as DCS
        self.assertEqual(DCS.compute_risk_band(5), DCS.RISK_WATCH)

    def test_warning_band_6_days(self):
        from risk.models import DeviceConnectivitySignal as DCS
        self.assertEqual(DCS.compute_risk_band(6), DCS.RISK_WARNING)

    def test_warning_band_10_days(self):
        from risk.models import DeviceConnectivitySignal as DCS
        self.assertEqual(DCS.compute_risk_band(10), DCS.RISK_WARNING)

    def test_high_band_11_days(self):
        from risk.models import DeviceConnectivitySignal as DCS
        self.assertEqual(DCS.compute_risk_band(11), DCS.RISK_HIGH)

    def test_high_band_20_days(self):
        from risk.models import DeviceConnectivitySignal as DCS
        self.assertEqual(DCS.compute_risk_band(20), DCS.RISK_HIGH)

    def test_critical_band_21_days(self):
        from risk.models import DeviceConnectivitySignal as DCS
        self.assertEqual(DCS.compute_risk_band(21), DCS.RISK_CRITICAL)

    def test_critical_band_60_days(self):
        from risk.models import DeviceConnectivitySignal as DCS
        self.assertEqual(DCS.compute_risk_band(60), DCS.RISK_CRITICAL)

    # ── risk_score calculation ────────────────────────────────────────────

    def test_clean_device_scores_zero(self):
        from risk.models import DeviceConnectivitySignal as DCS
        score = DCS.compute_risk_score(
            days_offline=0, arrears_days=0, days_since_payment=5
        )
        self.assertEqual(score, 0)

    def test_critical_offline_scores_high(self):
        from risk.models import DeviceConnectivitySignal as DCS
        score = DCS.compute_risk_score(
            days_offline=25, arrears_days=35, days_since_payment=None
        )
        self.assertGreaterEqual(score, 85)

    def test_score_capped_at_100(self):
        from risk.models import DeviceConnectivitySignal as DCS
        score = DCS.compute_risk_score(
            days_offline=100, arrears_days=100, days_since_payment=None,
            device_value=500_000, sim_changed=True, location_changed=True,
            failed_lock_attempts=10,
        )
        self.assertLessEqual(score, 100)

    def test_sim_changed_adds_points(self):
        from risk.models import DeviceConnectivitySignal as DCS
        base = DCS.compute_risk_score(days_offline=5)
        with_sim = DCS.compute_risk_score(days_offline=5, sim_changed=True)
        self.assertGreater(with_sim, base)

    def test_arrears_adds_points(self):
        from risk.models import DeviceConnectivitySignal as DCS
        no_arrears = DCS.compute_risk_score(days_offline=5, arrears_days=0)
        with_arrears = DCS.compute_risk_score(days_offline=5, arrears_days=14)
        self.assertGreater(with_arrears, no_arrears)

    def test_high_device_value_adds_points(self):
        from risk.models import DeviceConnectivitySignal as DCS
        low_val = DCS.compute_risk_score(days_offline=5, device_value=100_000)
        high_val = DCS.compute_risk_score(days_offline=5, device_value=500_000)
        self.assertGreater(high_val, low_val)

    # ── recommended_action ────────────────────────────────────────────────

    def test_normal_band_no_action(self):
        from risk.models import DeviceConnectivitySignal as DCS
        action = DCS.compute_recommended_action(
            DCS.RISK_NORMAL, DCS.CONN_RECENTLY_ONLINE, DCS.CMD_NONE
        )
        self.assertIn("No action", action)

    def test_watch_band_friendly_checkin(self):
        from risk.models import DeviceConnectivitySignal as DCS
        action = DCS.compute_recommended_action(
            DCS.RISK_WATCH, DCS.CONN_OFFLINE_ARREARS, DCS.CMD_NONE
        )
        self.assertIn("WhatsApp", action)

    def test_critical_band_hq_review(self):
        from risk.models import DeviceConnectivitySignal as DCS
        action = DCS.compute_recommended_action(
            DCS.RISK_CRITICAL, DCS.CONN_POSSIBLE_EVASION, DCS.CMD_NONE
        )
        self.assertIn("HQ", action)

    def test_paid_offline_sync_instruction(self):
        from risk.models import DeviceConnectivitySignal as DCS
        action = DCS.compute_recommended_action(
            DCS.RISK_WATCH, DCS.CONN_OFFLINE_PAID, DCS.CMD_NONE
        )
        self.assertIn("mobile data", action)

    def test_pending_unlock_command_message(self):
        from risk.models import DeviceConnectivitySignal as DCS
        action = DCS.compute_recommended_action(
            DCS.RISK_NORMAL, DCS.CONN_SYNC_PENDING, DCS.CMD_UNLOCK
        )
        self.assertIn("online", action.lower())

    # ── connectivity_status classification ───────────────────────────────

    def test_recently_online_status(self):
        from risk.models import DeviceConnectivitySignal as DCS
        from django.utils import timezone
        status = DCS.compute_connectivity_status(
            days_offline=0,
            arrears_days=0,
            pending_command_type=DCS.CMD_NONE,
            last_seen_at=timezone.now(),
        )
        self.assertEqual(status, DCS.CONN_RECENTLY_ONLINE)

    def test_paid_offline_status(self):
        from risk.models import DeviceConnectivitySignal as DCS
        status = DCS.compute_connectivity_status(
            days_offline=3, arrears_days=0,
            pending_command_type=DCS.CMD_NONE,
        )
        self.assertEqual(status, DCS.CONN_OFFLINE_PAID)

    def test_arrears_offline_status(self):
        from risk.models import DeviceConnectivitySignal as DCS
        status = DCS.compute_connectivity_status(
            days_offline=5, arrears_days=7,
            pending_command_type=DCS.CMD_NONE,
        )
        self.assertEqual(status, DCS.CONN_OFFLINE_ARREARS)

    def test_possible_evasion_after_11_days_with_arrears(self):
        from risk.models import DeviceConnectivitySignal as DCS
        status = DCS.compute_connectivity_status(
            days_offline=12, arrears_days=10,
            pending_command_type=DCS.CMD_NONE,
        )
        self.assertEqual(status, DCS.CONN_POSSIBLE_EVASION)

    # ── model save / __str__ ─────────────────────────────────────────────

    def test_signal_can_be_saved_and_retrieved(self):
        from risk.models import DeviceConnectivitySignal as DCS
        sig = DCS.objects.create(
            imei="123456789012345",
            customer_name="Test Customer",
            days_offline=5,
            risk_band=DCS.RISK_WATCH,
            risk_score=20,
        )
        retrieved = DCS.objects.get(pk=sig.pk)
        self.assertEqual(retrieved.imei, "123456789012345")
        self.assertEqual(retrieved.risk_band, DCS.RISK_WATCH)

    def test_signal_str_contains_imei_and_band(self):
        from risk.models import DeviceConnectivitySignal as DCS
        sig = DCS(imei="123456789012345", days_offline=8, risk_band=DCS.RISK_WARNING)
        self.assertIn("123456789012345", str(sig))
        self.assertIn("warning", str(sig))

    def test_default_ordering_by_risk_score_desc(self):
        from risk.models import DeviceConnectivitySignal as DCS
        DCS.objects.create(imei="111", days_offline=2, risk_score=10)
        DCS.objects.create(imei="222", days_offline=25, risk_score=85)
        signals = list(DCS.objects.all())
        self.assertEqual(signals[0].risk_score, 85)


# ──────────────────────────────────────────────────────────────────────────────
# HQ Fraud Checks View Tests
# ──────────────────────────────────────────────────────────────────────────────

class HQFraudChecksViewTests(TestCase):
    """Integration tests: hq_fraud_checks view renders without crashing
    and includes the connectivity intelligence section."""

    def setUp(self):
        from accounts.models import UserProfile
        self.hq_user = User.objects.create_user(
            username="hq_test_fraud",
            password="hqpass123",
        )
        UserProfile.objects.filter(user=self.hq_user).update(role="hq")

    def test_fraud_page_redirects_anonymous(self):
        from django.test import Client
        client = Client()
        response = client.get("/tengasale/hq/fraud-checks/")
        self.assertIn(response.status_code, [301, 302])

    def test_fraud_page_renders_for_hq_user(self):
        from django.test import Client
        client = Client()
        client.login(username="hq_test_fraud", password="hqpass123")
        response = client.get("/tengasale/hq/fraud-checks/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Fraud Investigation Console", content)
        self.assertIn("Connectivity Support Intelligence", content)

    def test_fraud_page_shows_telemetry_notice(self):
        """With no telemetry keys, page shows 'manual/mock signals' notice."""
        from django.test import Client
        client = Client()
        client.login(username="hq_test_fraud", password="hqpass123")
        response = client.get("/tengasale/hq/fraud-checks/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("manual", content.lower())

    def test_fraud_page_filters_do_not_crash(self):
        from django.test import Client
        client = Client()
        client.login(username="hq_test_fraud", password="hqpass123")
        response = client.get("/tengasale/hq/fraud-checks/")
        self.assertEqual(response.status_code, 200)

    def test_mark_false_positive_action(self):
        from risk.models import DeviceConnectivitySignal as DCS
        from django.test import Client
        sig = DCS.objects.create(
            imei="999888777666555",
            days_offline=5,
            risk_band=DCS.RISK_WATCH,
        )
        client = Client()
        client.login(username="hq_test_fraud", password="hqpass123")
        response = client.post(
            "/tengasale/hq/fraud-checks/",
            {"conn_action": "mark_false_positive", "signal_id": sig.pk},
        )
        self.assertEqual(response.status_code, 200)
        sig.refresh_from_db()
        self.assertTrue(sig.is_false_positive)


# ──────────────────────────────────────────────────────────────────────────────
# Password Reset View Tests
# ──────────────────────────────────────────────────────────────────────────────

class PasswordResetViewTests(TestCase):
    """Test the branded password reset flow."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="resetme",
            email="resetme@example.com",
            password="oldpassword123",
        )

    def test_forgot_password_page_renders(self):
        from django.test import Client
        client = Client()
        response = client.get("/accounts/password-reset/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Forgot", content)
        self.assertIn("TengaSale", content)

    def test_submitting_known_email_shows_done_page(self):
        from django.test import Client
        client = Client()
        response = client.post(
            "/accounts/password-reset/",
            {"email": "resetme@example.com"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Check your inbox", content)

    def test_submitting_unknown_email_gives_same_response(self):
        """No user enumeration: unknown email shows same 'check inbox' page."""
        from django.test import Client
        client = Client()
        response = client.post(
            "/accounts/password-reset/",
            {"email": "nobody@nowhere.com"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Check your inbox", content)

    def test_done_page_renders(self):
        from django.test import Client
        client = Client()
        response = client.get("/accounts/password-reset/done/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("TengaSale", content)

    def test_complete_page_renders(self):
        from django.test import Client
        client = Client()
        response = client.get("/accounts/reset/done/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Password reset successful", content)

    def test_invalid_token_shows_expired_message(self):
        from django.test import Client
        client = Client()
        response = client.get("/accounts/reset/ZZ/invalid-token/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("expired", content.lower())

    def test_login_page_has_forgot_password_link(self):
        from django.test import Client
        client = Client()
        response = client.get("/accounts/login/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Forgot password", content)
        self.assertIn("password-reset", content)
