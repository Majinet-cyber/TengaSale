from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Department, StaffRole
from financing.models import Customer

from .models import DeviceInspection, DeviceIntake, RecommerceCommissionPlan, RecommerceEvent, RefurbishmentWorkOrder
from .services import create_commission_event, transition_intake


User = get_user_model()


class RecommerceWorkflowTests(TestCase):
    def setUp(self):
        self.department = Department.objects.create(name="Recommerce", slug="recommerce")
        self.customer = Customer.objects.create(full_name="Tadala Phiri", phone_number="0999000000", customer_id_number="ID-1")
        self.intake_user = self.user_with_role("intake", "recommerce_intake")
        self.assessor = self.user_with_role("assessor", "recommerce_assessor")
        self.technician = self.user_with_role("technician", "recommerce_technician")
        self.qa = self.user_with_role("qa", "recommerce_qa")
        self.supervisor = self.user_with_role("supervisor", "recommerce_supervisor")
        self.hq = User.objects.create_superuser("hq-recommerce", "hq@example.com", "pass")
        self.intake = DeviceIntake.objects.create(customer=self.customer, imei="123456789012345", brand="Tenga", model="One", intake_location="Blantyre", declared_ownership=True, opened_by=self.intake_user, assigned_to=self.intake_user, assigned_role="recommerce_intake")

    def user_with_role(self, username, code):
        user = User.objects.create_user(username=username, password="pass")
        role = StaffRole.objects.create(code=code, name=code.replace("_", " ").title(), department=self.department, portal_role="hq")
        user.profile.staff_role = role
        user.profile.save(update_fields=["staff_role"])
        return user

    def test_valid_transition_creates_immutable_event(self):
        transition_intake(self.intake, "details_captured", self.intake_user)
        self.intake.refresh_from_db()
        self.assertEqual(self.intake.status, "details_captured")
        event = self.intake.events.get(event_type="status_transition")
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            event.delete()

    def test_invalid_shortcut_is_rejected(self):
        with self.assertRaises(ValidationError):
            transition_intake(self.intake, "sold", self.supervisor)

    def test_override_requires_reason(self):
        with self.assertRaises(ValidationError):
            transition_intake(self.intake, "fraud_review", self.supervisor, override=True)
        transition_intake(self.intake, "fraud_review", self.supervisor, reason="Duplicate device signal", override=True)

    def test_assessor_cannot_approve_own_valuation(self):
        self.intake.status = "approval_pending"
        self.intake.save(update_fields=["status"])
        DeviceInspection.objects.create(intake=self.intake, assessor=self.assessor, proposed_grade="B")
        with self.assertRaises(PermissionDenied):
            transition_intake(self.intake, "offer_ready", self.assessor)

    def test_technician_cannot_qa_own_work(self):
        self.intake.status = "qa_pending"
        self.intake.save(update_fields=["status"])
        RefurbishmentWorkOrder.objects.create(intake=self.intake, diagnosis="Screen", assigned_technician=self.technician)
        with self.assertRaises(PermissionDenied):
            transition_intake(self.intake, "qa_approved", self.technician)

    def test_intake_officer_cannot_settle_own_case(self):
        self.intake.status = "device_received"
        self.intake.save(update_fields=["status"])
        with self.assertRaises(PermissionDenied):
            transition_intake(self.intake, "settlement_pending", self.intake_user)

    def test_hq_can_access_consolidated_overview(self):
        self.client.force_login(self.hq)
        self.assertEqual(self.client.get(reverse("recommerce:hq_overview")).status_code, 200)

    def test_public_cannot_access_private_recommerce(self):
        response = self.client.get(reverse("recommerce:home"))
        self.assertEqual(response.status_code, 302)

    def test_commission_uses_approved_versioned_plan(self):
        plan = RecommerceCommissionPlan.objects.create(role="recommerce_assessor", event_type="assessment_approved", fixed_amount=Decimal("1000"), effective_from=timezone.now() - timedelta(days=1), version=2, approved_by=self.hq, approved_at=timezone.now(), active=True)
        event = create_commission_event(staff_user=self.assessor, intake=self.intake, event_type="assessment_approved")
        self.assertEqual(event.final_amount, Decimal("1000"))
        self.assertEqual(event.plan_snapshot["version"], 2)


class PublicStrategySafetyTests(TestCase):
    def test_public_home_keeps_premium_sections_and_safe_marker(self):
        response = self.client.get("/")
        self.assertContains(response, "A smartphone is")
        self.assertContains(response, "not a luxury")
        self.assertContains(response, "2× monthly income")
        self.assertContains(response, "Your phone can")
        self.assertContains(response, "take you further")
        self.assertContains(response, "TENGA_PUBLIC_STRATEGY_SAFE_V1")
        self.assertContains(response, 'id="lockUi"')
        self.assertContains(response, "support@tenga.africa")
        content = response.content.decode().lower()
        for phrase in ("support@" + "emajinet.africa", "imei and device verification", "blacklisted devices", "securely data-wiped", "commission rate", "fraud threshold", "valuation formula", "chain of custody"):
            self.assertNotIn(phrase, content)
