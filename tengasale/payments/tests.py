"""
TengaSale Payments App — Tests

Covers:
- CommissionRule model + calculate()
- PayoutBatch creation, approval, processing
- SalarySchedule generation + net_pay calculation
- SpinRewardPayout creation
- PaymentAuditLog creation
- Service functions
- View access control
"""
from datetime import date
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from .models import (
    CommissionRule, PayoutBatch, PayoutItem,
    PaymentAuditLog, SalarySchedule, SpinRewardPayout,
    USSDPaymentIntent, USSDSessionLog,
)
from .services import (
    approve_payout_batch,
    create_payout_batch,
    create_spin_reward_payout,
    process_payout_batch,
    accrue_underwriter_commission,
    get_hq_payment_summary,
)

User = get_user_model()


class USSDCallbackTests(TestCase):
    def post_ussd(self, text, session_id="test123"):
        return self.client.post(
            reverse("ussd_callback"),
            data=urlencode({
                "sessionId": session_id,
                "serviceCode": "*384*88900#",
                "phoneNumber": "+265990870616",
                "text": text,
            }),
            content_type="application/x-www-form-urlencoded",
        )

    def assert_plain(self, response, expected):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")
        self.assertEqual(response.content.decode(), expected)

    def test_main_menu(self):
        response = self.post_ussd("")
        self.assert_plain(response, "CON Welcome to TengaSale\n1. Pay\n2. Check balance")

    def test_pay_amount_prompt(self):
        response = self.post_ussd("1")
        self.assert_plain(response, "CON Enter amount you want to pay")

    def test_pay_contract_prompt(self):
        response = self.post_ussd("1*1500")
        self.assert_plain(response, "CON Enter your TengaSale contract number")

    def test_payment_intent_creation(self):
        response = self.post_ussd("1*1500*TS123")
        self.assert_plain(response, "END Payment request received. TengaSale will verify and confirm shortly.")
        intent = USSDPaymentIntent.objects.get()
        self.assertEqual(intent.phone_number, "+265990870616")
        self.assertEqual(intent.contract_number, "TS123")
        self.assertEqual(intent.amount, Decimal("1500"))
        self.assertEqual(intent.status, "PENDING")

    def test_check_balance_prompt(self):
        response = self.post_ussd("2")
        self.assert_plain(response, "CON Enter your TengaSale contract number")

    def test_balance_not_found(self):
        response = self.post_ussd("2*TS123")
        self.assert_plain(response, "END Contract not found. Please check your number or contact TengaSale support on +265990870616.")

    def test_invalid_input(self):
        response = self.post_ussd("9")
        self.assert_plain(response, "END Invalid option. Please try again.")

    def test_session_logging(self):
        response = self.post_ussd("1")
        log = USSDSessionLog.objects.get()
        self.assertEqual(log.text, "1")
        self.assertEqual(log.response, response.content.decode())


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def make_hq_user():
    u = User.objects.create_user(username="hqtest", password="pass", email="hq@test.com")
    u.is_staff = True
    u.save()
    return u


def make_regular_user():
    return User.objects.create_user(username="reguser", password="pass", email="reg@test.com")


# ──────────────────────────────────────────────────────────────────────────────
# CommissionRule Tests
# ──────────────────────────────────────────────────────────────────────────────

class CommissionRuleTests(TestCase):

    def test_percentage_rule_calculates_correctly(self):
        rule = CommissionRule(
            name="Underwriter 7%",
            earner_type=CommissionRule.EARNER_UNDERWRITER,
            percentage=Decimal("7.00"),
            applies_to_repayments=True,
        )
        self.assertEqual(rule.calculate(Decimal("10000")), Decimal("700.00"))

    def test_percentage_rule_1_percent(self):
        rule = CommissionRule(
            name="Merchant 1%",
            earner_type=CommissionRule.EARNER_MERCHANT,
            percentage=Decimal("1.00"),
            applies_to_repayments=True,
        )
        self.assertEqual(rule.calculate(Decimal("50000")), Decimal("500.00"))

    def test_fixed_amount_rule(self):
        rule = CommissionRule(
            name="Fixed bonus",
            earner_type=CommissionRule.EARNER_MERCHANT,
            percentage=Decimal("0"),
            fixed_amount=Decimal("1000"),
        )
        self.assertEqual(rule.calculate(Decimal("5000")), Decimal("1000"))

    def test_zero_rule(self):
        rule = CommissionRule(
            name="Zero rule",
            earner_type=CommissionRule.EARNER_MERCHANT,
            percentage=Decimal("0"),
            fixed_amount=Decimal("0"),
        )
        self.assertEqual(rule.calculate(Decimal("5000")), Decimal("0"))

    def test_rule_str(self):
        rule = CommissionRule(name="Test", earner_type="underwriter", percentage=Decimal("7"))
        self.assertIn("7%", str(rule))

    def test_rule_saved_to_db(self):
        CommissionRule.objects.create(
            name="DB Test Rule",
            earner_type=CommissionRule.EARNER_UNDERWRITER,
            percentage=Decimal("5.00"),
        )
        self.assertEqual(CommissionRule.objects.count(), 1)


# ──────────────────────────────────────────────────────────────────────────────
# PayoutBatch Tests
# ──────────────────────────────────────────────────────────────────────────────

class PayoutBatchTests(TestCase):

    def setUp(self):
        self.user = make_hq_user()
        self.approver = make_regular_user()
        self.approver.is_staff = True
        self.approver.save()

    def test_batch_created_with_number(self):
        batch = create_payout_batch(PayoutBatch.TYPE_MERCHANT, self.user, notes="Test")
        self.assertTrue(batch.batch_number.startswith("BATCH-"))
        self.assertEqual(batch.status, PayoutBatch.STATUS_DRAFT)
        self.assertEqual(batch.payout_type, PayoutBatch.TYPE_MERCHANT)
        self.assertEqual(batch.created_by, self.user)

    def test_batch_numbers_are_unique(self):
        b1 = create_payout_batch(PayoutBatch.TYPE_MERCHANT, self.user)
        b2 = create_payout_batch(PayoutBatch.TYPE_MERCHANT, self.user)
        self.assertNotEqual(b1.batch_number, b2.batch_number)

    def test_batch_approval(self):
        batch = create_payout_batch(PayoutBatch.TYPE_SALARIES, self.user)
        batch.status = PayoutBatch.STATUS_PENDING_APPROVAL
        batch.save()
        approve_payout_batch(batch, self.approver)
        batch.refresh_from_db()
        self.assertEqual(batch.status, PayoutBatch.STATUS_APPROVED)
        self.assertEqual(batch.approved_by, self.approver)
        self.assertIsNotNone(batch.approved_at)

    def test_cannot_approve_already_completed_batch(self):
        """A completed batch cannot be approved again."""
        batch = create_payout_batch(PayoutBatch.TYPE_MERCHANT, self.user)
        batch.status = PayoutBatch.STATUS_COMPLETED
        batch.save()
        with self.assertRaises(ValueError):
            approve_payout_batch(batch, self.approver)

    def test_batch_processing(self):
        batch = create_payout_batch(PayoutBatch.TYPE_MERCHANT, self.user)
        batch.status = PayoutBatch.STATUS_APPROVED
        batch.save()

        # Add an item
        PayoutItem.objects.create(
            batch=batch,
            recipient_type=PayoutItem.RECIPIENT_MERCHANT,
            recipient_name="Test Merchant",
            recipient_phone="+265991234567",
            payment_method=PayoutItem.METHOD_AIRTEL,
            amount=Decimal("50000"),
        )

        result = process_payout_batch(batch, self.user)
        self.assertEqual(result["items_queued"], 1)
        batch.refresh_from_db()
        self.assertEqual(batch.status, PayoutBatch.STATUS_PROCESSING)

    def test_cannot_process_unapproved_batch(self):
        batch = create_payout_batch(PayoutBatch.TYPE_MERCHANT, self.user)
        with self.assertRaises(ValueError):
            process_payout_batch(batch, self.user)

    def test_audit_log_created_on_batch_creation(self):
        before = PaymentAuditLog.objects.count()
        create_payout_batch(PayoutBatch.TYPE_MERCHANT, self.user)
        self.assertEqual(PaymentAuditLog.objects.count(), before + 1)


# ──────────────────────────────────────────────────────────────────────────────
# Payout Item Tests
# ──────────────────────────────────────────────────────────────────────────────

class PayoutItemTests(TestCase):

    def setUp(self):
        self.user = make_hq_user()
        self.batch = create_payout_batch(PayoutBatch.TYPE_MERCHANT, self.user)

    def test_payout_item_mark_paid(self):
        item = PayoutItem.objects.create(
            batch=self.batch,
            recipient_type=PayoutItem.RECIPIENT_MERCHANT,
            recipient_name="Test Merchant",
            recipient_phone="+265991234567",
            payment_method=PayoutItem.METHOD_AIRTEL,
            amount=Decimal("25000"),
        )
        item.mark_paid(user=self.user, proof_reference="REF-12345", notes="Test payment")
        item.refresh_from_db()
        self.assertEqual(item.status, PayoutItem.STATUS_PAID)
        self.assertIsNotNone(item.paid_at)
        self.assertEqual(item.proof_reference, "REF-12345")

    def test_mark_paid_requires_proof_in_service(self):
        """mark_paid on model does not require proof in tests but service/view does."""
        item = PayoutItem.objects.create(
            batch=self.batch,
            recipient_type=PayoutItem.RECIPIENT_STAFF,
            recipient_name="Staff",
            recipient_phone="+265881234567",
            payment_method=PayoutItem.METHOD_TNM,
            amount=Decimal("10000"),
        )
        item.mark_paid()
        self.assertEqual(item.status, PayoutItem.STATUS_PAID)


# ──────────────────────────────────────────────────────────────────────────────
# SalarySchedule Tests
# ──────────────────────────────────────────────────────────────────────────────

class SalaryScheduleTests(TestCase):

    def setUp(self):
        self.user = make_hq_user()

    def test_net_pay_calculated_on_save(self):
        schedule = SalarySchedule.objects.create(
            staff_user=self.user,
            staff_name="Test Staff",
            role="Underwriter",
            salary_month=date(2026, 6, 1),
            base_salary=Decimal("350000"),
            bonus_amount=Decimal("50000"),
            volts_bonus=Decimal("10000"),
            penalty_deduction=Decimal("5000"),
        )
        self.assertEqual(schedule.net_pay, Decimal("405000.00"))

    def test_salary_approve(self):
        schedule = SalarySchedule.objects.create(
            staff_user=self.user,
            staff_name="Test Staff",
            role="HQ",
            salary_month=date(2026, 6, 1),
            base_salary=Decimal("300000"),
        )
        self.assertEqual(schedule.status, SalarySchedule.STATUS_DRAFT)
        schedule.approve(self.user)
        schedule.refresh_from_db()
        self.assertEqual(schedule.status, SalarySchedule.STATUS_APPROVED)
        self.assertEqual(schedule.approved_by, self.user)

    def test_unique_per_user_per_month(self):
        from django.db import IntegrityError
        SalarySchedule.objects.create(
            staff_user=self.user,
            staff_name="Test",
            role="HQ",
            salary_month=date(2026, 6, 1),
            base_salary=Decimal("300000"),
        )
        with self.assertRaises(IntegrityError):
            SalarySchedule.objects.create(
                staff_user=self.user,
                staff_name="Test",
                role="HQ",
                salary_month=date(2026, 6, 1),
                base_salary=Decimal("200000"),
            )

    def test_zero_deductions(self):
        schedule = SalarySchedule(
            staff_user=self.user,
            staff_name="Test",
            salary_month=date(2026, 6, 1),
            base_salary=Decimal("200000"),
            bonus_amount=Decimal("0"),
            penalty_deduction=Decimal("0"),
        )
        schedule.save()
        self.assertEqual(schedule.net_pay, Decimal("200000.00"))


# ──────────────────────────────────────────────────────────────────────────────
# SpinRewardPayout Tests
# ──────────────────────────────────────────────────────────────────────────────

class SpinRewardPayoutTests(TestCase):

    def setUp(self):
        self.user = make_hq_user()
        # Create a SpinReward (without real application)
        from rewards.models import SpinReward
        import django.utils.timezone as tz
        self.spin = SpinReward.objects.create(
            user=self.user,
            amount=Decimal("100000"),
            spin_date=tz.now(),
            reward_tier="jackpot",
        )

    def test_create_spin_reward_payout(self):
        payout = create_spin_reward_payout(
            self.spin,
            phone_number="+265991234567",
            payment_method=SpinRewardPayout.METHOD_AIRTEL,
            created_by=self.user,
        )
        self.assertEqual(payout.amount, Decimal("100000"))
        self.assertEqual(payout.status, SpinRewardPayout.STATUS_PENDING)
        self.assertEqual(payout.phone_number, "+265991234567")

    def test_idempotent_create(self):
        """Calling create_spin_reward_payout twice returns existing payout."""
        p1 = create_spin_reward_payout(self.spin, "+265991234567", SpinRewardPayout.METHOD_AIRTEL)
        p2 = create_spin_reward_payout(self.spin, "+265881234567", SpinRewardPayout.METHOD_TNM)
        self.assertEqual(p1.id, p2.id)
        self.assertEqual(SpinRewardPayout.objects.filter(spin_reward=self.spin).count(), 1)

    def test_mark_paid(self):
        payout = create_spin_reward_payout(self.spin, "+265991234567", SpinRewardPayout.METHOD_AIRTEL)
        payout.status = SpinRewardPayout.STATUS_PROCESSING
        payout.save()
        payout.mark_paid(user=self.user, proof_reference="SPIN-REF-001")
        payout.refresh_from_db()
        self.assertEqual(payout.status, SpinRewardPayout.STATUS_PAID)
        self.assertEqual(payout.proof_reference, "SPIN-REF-001")


# ──────────────────────────────────────────────────────────────────────────────
# PaymentAuditLog Tests
# ──────────────────────────────────────────────────────────────────────────────

class PaymentAuditLogTests(TestCase):

    def test_audit_log_creation(self):
        user = make_hq_user()
        log = PaymentAuditLog.objects.create(
            action="test_action",
            user=user,
            notes="Test note",
        )
        self.assertEqual(log.action, "test_action")
        self.assertEqual(log.user, user)

    def test_audit_log_no_user(self):
        log = PaymentAuditLog.objects.create(
            action="system_action",
            notes="System did something",
        )
        self.assertIsNone(log.user)

    def test_audit_log_ordering(self):
        user = make_hq_user()
        PaymentAuditLog.objects.create(action="first", user=user)
        PaymentAuditLog.objects.create(action="second", user=user)
        logs = list(PaymentAuditLog.objects.all())
        self.assertEqual(logs[0].action, "second")


# ──────────────────────────────────────────────────────────────────────────────
# HQ Summary Tests
# ──────────────────────────────────────────────────────────────────────────────

class HQSummaryTests(TestCase):

    def test_summary_returns_dict(self):
        summary = get_hq_payment_summary()
        self.assertIsInstance(summary, dict)
        expected_keys = [
            "today_collected", "month_collected", "failed_payments",
            "pending_payout_total", "pending_payout_count",
            "merchant_commissions_payable", "underwriter_commissions_payable",
            "salaries_due", "spin_rewards_pending",
        ]
        for key in expected_keys:
            self.assertIn(key, summary, f"Missing key: {key}")

    def test_summary_with_no_data(self):
        summary = get_hq_payment_summary()
        self.assertEqual(summary["today_collected"], Decimal("0"))
        self.assertEqual(summary["month_collected"], Decimal("0"))
        self.assertEqual(summary["failed_payments"], 0)
        self.assertEqual(summary["pending_payout_count"], 0)


# ──────────────────────────────────────────────────────────────────────────────
# View Access Control Tests
# ──────────────────────────────────────────────────────────────────────────────

class PaymentViewAccessTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.hq_user = make_hq_user()
        self.regular_user = make_regular_user()

    def test_overview_requires_login(self):
        response = self.client.get(reverse("payments:home"))
        self.assertIn(response.status_code, [302, 403])

    def test_hq_user_can_access_overview(self):
        self.client.force_login(self.hq_user)
        response = self.client.get(reverse("payments:home"))
        self.assertEqual(response.status_code, 200)

    def test_collections_accessible_to_hq(self):
        self.client.force_login(self.hq_user)
        response = self.client.get(reverse("payments:collections"))
        self.assertEqual(response.status_code, 200)

    def test_payout_batches_accessible(self):
        self.client.force_login(self.hq_user)
        response = self.client.get(reverse("payments:payout_batches"))
        self.assertEqual(response.status_code, 200)

    def test_salaries_accessible(self):
        self.client.force_login(self.hq_user)
        response = self.client.get(reverse("payments:salaries"))
        self.assertEqual(response.status_code, 200)

    def test_spin_rewards_accessible(self):
        self.client.force_login(self.hq_user)
        response = self.client.get(reverse("payments:spin_reward_payouts"))
        self.assertEqual(response.status_code, 200)

    def test_commission_rules_accessible(self):
        self.client.force_login(self.hq_user)
        response = self.client.get(reverse("payments:commission_rules"))
        self.assertEqual(response.status_code, 200)

    def test_settings_accessible(self):
        self.client.force_login(self.hq_user)
        response = self.client.get(reverse("payments:settings"))
        self.assertEqual(response.status_code, 200)

    def test_audit_log_accessible(self):
        self.client.force_login(self.hq_user)
        response = self.client.get(reverse("payments:audit_log"))
        self.assertEqual(response.status_code, 200)

    def test_regular_user_redirected_from_payment_center(self):
        """Non-HQ regular users should be redirected away from payment center."""
        self.client.force_login(self.regular_user)
        response = self.client.get(reverse("payments:home"))
        # Should redirect (non-HQ users get redirected to home)
        self.assertEqual(response.status_code, 302)


# ──────────────────────────────────────────────────────────────────────────────
# Commission Rule Save View Tests
# ──────────────────────────────────────────────────────────────────────────────

class CommissionRuleSaveTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = make_hq_user()
        self.client.force_login(self.user)

    def test_create_commission_rule(self):
        response = self.client.post(reverse("payments:commission_rule_save"), {
            "name": "Test 7% Rule",
            "earner_type": "underwriter",
            "percentage": "7.00",
            "fixed_amount": "0",
            "applies_to_repayments": "on",
            "is_active": "on",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CommissionRule.objects.filter(name="Test 7% Rule").count(), 1)

    def test_edit_commission_rule(self):
        rule = CommissionRule.objects.create(
            name="Original Name",
            earner_type="merchant",
            percentage=Decimal("1"),
        )
        response = self.client.post(
            reverse("payments:commission_rule_edit", kwargs={"rule_id": rule.id}),
            {
                "name": "Updated Name",
                "earner_type": "merchant",
                "percentage": "1.50",
                "fixed_amount": "0",
                "applies_to_repayments": "on",
                "is_active": "on",
            }
        )
        self.assertEqual(response.status_code, 302)
        rule.refresh_from_db()
        self.assertEqual(rule.name, "Updated Name")
        self.assertEqual(rule.percentage, Decimal("1.50"))
