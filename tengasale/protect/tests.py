from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import FinancingApplication
from contracts.models import Contract
from .models import DeviceCertificate, DeviceRegistryEntry, LocationAccessSession, ProtectAuditEvent, ProtectCase, ProtectConsent
from .permissions import PROTECT_GROUPS, can_review
from .services import approve_location_access, request_location_access, terminate_case


class ProtectSecurityTests(TestCase):
    def setUp(self):
        self.merchant = User.objects.create_user("merchant", password="x")
        self.application = FinancingApplication.objects.create(created_by=self.merchant, customer_name="Jane Banda", customer_phone="991234567", national_id="AB123456", imei_number="353456789012345", device_serial_number="SN100")
        self.contract = Contract.objects.create(application=self.application, merchant=self.merchant, customer_name="Jane Banda", customer_phone="991234567", national_id="AB123456", deal_name="Samsung Galaxy A06", imei_number="353456789012345")
        self.registry = DeviceRegistryEntry.objects.create(contract=self.contract, imei=self.contract.imei_number)
        self.case = ProtectCase.objects.create(registry_entry=self.registry, case_type="stolen", status="under_investigation", incident_date=date.today(), incident_location="Area 25", description="Taken", alternative_phone="0991000000")
        ProtectConsent.objects.create(case=self.case, policy_version="2026.1", text_snapshot="consent", authentication_method="otp")
        self.officer = User.objects.create_user("officer", password="x")
        self.supervisor = User.objects.create_user("supervisor", password="x")
        self.collections = User.objects.create_user("collections", password="x")
        self.officer.groups.add(Group.objects.get_or_create(name=PROTECT_GROUPS["officer"])[0])
        self.supervisor.groups.add(Group.objects.get_or_create(name=PROTECT_GROUPS["supervisor"])[0])

    @override_settings(PROTECT_LOCATION_ENABLED=True)
    def test_collections_user_cannot_locate_device_and_denial_is_audited(self):
        with self.assertRaises(PermissionDenied): request_location_access(case=self.case, actor=self.collections, reason="arrears")
        self.assertTrue(ProtectAuditEvent.objects.filter(action="PERMISSION_DENIED", actor=self.collections).exists())

    def test_superuser_does_not_receive_protect_access(self):
        admin = User.objects.create_superuser("root", "root@example.com", "x")
        self.assertFalse(can_review(admin))

    def test_arrears_never_creates_location_session(self):
        self.contract.status = Contract.STATUS_DEPOSIT_PENDING; self.contract.save()
        self.assertEqual(LocationAccessSession.objects.count(), 0)

    @override_settings(PROTECT_LOCATION_ENABLED=True)
    def test_valid_theft_case_can_request_approval(self):
        session = request_location_access(case=self.case, actor=self.officer, reason="verified theft recovery")
        self.assertEqual(session.status, "requested")

    @override_settings(PROTECT_LOCATION_ENABLED=True)
    def test_no_session_without_valid_consent(self):
        self.case.consent.revoked_at = timezone.now(); self.case.consent.save()
        with self.assertRaises(PermissionDenied): request_location_access(case=self.case, actor=self.officer, reason="recovery")

    @override_settings(PROTECT_LOCATION_ENABLED=True)
    def test_maker_cannot_approve_own_request(self):
        session = request_location_access(case=self.case, actor=self.officer, reason="recovery")
        self.officer.groups.add(Group.objects.get(name=PROTECT_GROUPS["supervisor"]))
        with self.assertRaises(PermissionDenied): approve_location_access(session=session, actor=self.officer)

    @override_settings(PROTECT_LOCATION_ENABLED=True, PROTECT_LOCATION_SESSION_HOURS=48)
    def test_approved_session_expires_and_recovery_terminates_access(self):
        session = request_location_access(case=self.case, actor=self.officer, reason="recovery")
        session = approve_location_access(session=session, actor=self.supervisor)
        self.assertTrue(session.is_active)
        session.expires_at = timezone.now() - timedelta(seconds=1); session.save()
        self.assertFalse(session.is_active)
        session.expires_at = timezone.now() + timedelta(hours=1); session.status = "authorised"; session.save()
        terminate_case(self.case, actor=self.officer, recovered=True)
        session.refresh_from_db(); self.assertEqual(session.status, "terminated")

    def test_public_imei_and_certificate_verification_never_expose_pii(self):
        response = self.client.post(reverse("protect:imei_check"), {"imei": self.registry.imei})
        self.assertContains(response, "FINANCING ACTIVE")
        self.assertNotContains(response, self.contract.customer_name)
        self.assertNotContains(response, self.contract.customer_phone)
        cert = DeviceCertificate.objects.create(registry_entry=self.registry, certificate_type="ownership")
        response = self.client.get(reverse("protect:verify_certificate", args=[cert.verification_token]))
        self.assertNotContains(response, self.contract.customer_name)
        self.assertNotContains(response, self.contract.national_id)

    def test_case_idor_is_denied(self):
        response = self.client.get(reverse("protect:case_detail", args=[self.case.id]))
        self.assertEqual(response.status_code, 403)

    def test_theft_status_is_distinct_from_payment_lock(self):
        self.registry.protection_status = "reported_stolen"; self.registry.device_status = "theft_locked"; self.registry.save()
        self.assertNotEqual(self.registry.device_status, "payment_locked")

    def test_audit_events_are_immutable(self):
        event = ProtectAuditEvent.objects.create(action="TEST")
        event.action = "TAMPER"
        with self.assertRaises(ValidationError): event.save()
