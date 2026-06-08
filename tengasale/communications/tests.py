from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.utils import assign_role
from applications.models import FinancingApplication
from communications.models import PhoneOTP, SMSLog
from communications.services import (
    generate_phone_otp,
    normalize_malawi_phone,
    send_payment_confirmation_sms,
    send_sms,
    verify_phone_otp,
)
from communications.tasks import send_due_sms_reminders
from communications.templates import format_mwk, render_sms_template
from portal.models import PaymentContract, PaymentTransaction


class SMSTemplateTests(TestCase):
    def test_sms_template_rendering_in_chichewa(self):
        message = render_sms_template(
            "payment_confirmation",
            "ny",
            amount_paid="2,000",
            balance_left="125,760",
            days_left=4,
            next_payment_date="10 Jun 2026",
        )
        self.assertIn("Talandila ndalama MWK2,000", message)
        self.assertIn("Ngongole yotsala ndi MWK125,760", message)

    def test_sms_template_rendering_in_english(self):
        message = render_sms_template(
            "due_reminder",
            "en",
            due_date="10 Jun 2026",
            days_until_due=2,
            balance_left="125,760",
        )
        self.assertIn("TengaSale reminder", message)
        self.assertIn("Remaining balance: MWK125,760", message)

    def test_money_formatting(self):
        self.assertEqual(format_mwk(2000), "2,000")
        self.assertEqual(format_mwk(Decimal("222000.49")), "222,000")
        self.assertEqual(format_mwk(Decimal("1000000")), "1,000,000")

    def test_phone_number_normalization(self):
        cases = {
            "0999123456": "+265999123456",
            "0888 123 456": "+265888123456",
            "+265999123456": "+265999123456",
            "265999123456": "+265999123456",
            "999123456": "+265999123456",
        }
        for raw, expected in cases.items():
            self.assertEqual(normalize_malawi_phone(raw), expected)


class SMSDispatchTests(TestCase):
    @override_settings(SMS_ENABLED=False)
    def test_sms_log_created_even_when_twilio_not_configured(self):
        log = send_sms("+265999123456", "Test SMS", purpose="general")
        self.assertEqual(log.status, SMSLog.STATUS_PENDING_CONFIG)
        self.assertEqual(SMSLog.objects.count(), 1)

    @override_settings(SMS_ENABLED=False)
    def test_payment_confirmation_sms_triggered_after_payment(self):
        contract = PaymentContract.objects.create(
            customer_name="Payment Tester",
            customer_phone="0999123456",
            total_amount=Decimal("10000"),
            amount_paid=Decimal("2000"),
            daily_price=Decimal("100"),
            due_date=timezone.localdate() + timedelta(days=5),
        )
        tx = PaymentTransaction.objects.create(
            payment_contract=contract,
            amount=Decimal("2000"),
            phone="0999123456",
            status=PaymentTransaction.STATUS_PAID,
            paid_at=timezone.now(),
        )

        log = send_payment_confirmation_sms(tx)

        self.assertIsNotNone(log)
        self.assertEqual(log.purpose, SMSLog.PURPOSE_PAYMENT_CONFIRMATION)
        self.assertEqual(log.phone_number, "+265999123456")
        self.assertIn("MWK2,000", log.message)
        self.assertIn("MWK8,000", log.message)

    @override_settings(SMS_ENABLED=False)
    def test_payment_flow_creates_confirmation_sms_log(self):
        contract = PaymentContract.objects.create(
            customer_name="Portal Payment",
            customer_phone="0999123456",
            total_amount=Decimal("10000"),
            daily_price=Decimal("100"),
            due_date=timezone.localdate(),
        )
        client = Client(HTTP_HOST="127.0.0.1")
        response = client.post(
            reverse("portal_payment", args=[contract.contract_number]),
            {
                "provider": "mock",
                "phone": "0999123456",
                "amount": "1000",
                "payment_type": "repayment",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(SMSLog.objects.filter(
            purpose=SMSLog.PURPOSE_PAYMENT_CONFIRMATION,
            payment_contract=contract,
        ).exists())

    @override_settings(SMS_ENABLED=False)
    def test_otp_generated_and_sent_and_verification_works(self):
        app = FinancingApplication.objects.create(
            created_by=self._merchant(),
            customer_name="OTP Tester",
            customer_phone="999123456",
        )
        otp = generate_phone_otp(app.customer_phone, application=app)

        app.refresh_from_db()
        self.assertIsNotNone(otp)
        self.assertEqual(app.phone_verification_status, PhoneOTP.STATUS_SENT)
        self.assertTrue(SMSLog.objects.filter(purpose=SMSLog.PURPOSE_OTP).exists())
        self.assertTrue(verify_phone_otp(app.customer_phone, otp.otp_code, application=app))
        app.refresh_from_db()
        self.assertTrue(app.phone_verified)
        self.assertEqual(app.phone_verification_status, PhoneOTP.STATUS_VERIFIED)

    @override_settings(SMS_ENABLED=False)
    def test_otp_missing_does_not_block_application(self):
        merchant = self._merchant(username="otp-flow-merchant")
        client = Client(HTTP_HOST="127.0.0.1")
        client.force_login(merchant)
        app = FinancingApplication.objects.create(created_by=merchant, status="started")

        response = client.post(
            reverse("edit_customer_details", args=[app.id]),
            {
                "customer_name": "OTP Missing",
                "national_id": "ABCD1234",
                "customer_phone": "999123456",
                "occupation": "Employed",
                "income_band": "100k_150k",
                "exact_monthly_income": "150000",
                "phone_user": "customer_self",
            },
        )

        self.assertRedirects(response, reverse("choose_device", args=[app.id]))
        app.refresh_from_db()
        self.assertFalse(app.phone_verified)
        self.assertIn(app.phone_verification_status, [PhoneOTP.STATUS_SENT, PhoneOTP.STATUS_FAILED])

    @override_settings(SMS_ENABLED=False)
    def test_due_reminders_do_not_duplicate_on_same_day(self):
        contract = PaymentContract.objects.create(
            customer_name="Reminder Tester",
            customer_phone="0999123456",
            total_amount=Decimal("10000"),
            amount_paid=Decimal("1000"),
            daily_price=Decimal("100"),
            due_date=timezone.localdate() + timedelta(days=2),
            status=PaymentContract.STATUS_ACTIVE,
        )

        first = send_due_sms_reminders()
        second = send_due_sms_reminders()

        self.assertEqual(first["due_reminder"], 1)
        self.assertEqual(second["due_reminder"], 0)
        self.assertEqual(SMSLog.objects.filter(
            payment_contract=contract,
            purpose=SMSLog.PURPOSE_DUE_REMINDER,
        ).count(), 1)

    def _merchant(self, username="otp-merchant"):
        user, _ = get_user_model().objects.get_or_create(username=username)
        user.set_password("test-pass-123")
        user.save()
        assign_role(user, "merchant")
        return user
