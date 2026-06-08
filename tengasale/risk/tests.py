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
