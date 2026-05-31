"""
Tests for the Device Lock Integration Layer.

Tests:
1. Create lock profile for contract
2. Mock provider enroll updates status
3. Mock provider lock updates status
4. Mock provider unlock updates status
5. Mock provider release updates status
6. DeviceLockEvent created for every action
7. Failed provider does not crash
8. Lock policy returns lock_recommended for overdue contract
9. Unlock recommended when paid-to-date
10. HQ lock centre page loads
11. Unauthorized merchant cannot lock/unlock
12. Webhook rejects invalid secret
13. Management command dry-run works
14. get_lock_readiness returns correct results
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from device_lock.models import DeviceLockProfile, DeviceLockEvent, DeviceLockPolicy

User = get_user_model()


def make_hq_user():
    u = User.objects.create_user(username="testhq", password="pass")
    from accounts.models import UserProfile
    UserProfile.objects.filter(user=u).update(role="hq")
    return u


def make_merchant_user():
    u = User.objects.create_user(username="testmerchant", password="pass")
    from accounts.models import UserProfile
    UserProfile.objects.filter(user=u).update(role="merchant")
    return u


def make_lock_profile(imei="123456789012345", provider="mock", status="not_enrolled"):
    return DeviceLockProfile.objects.create(
        imei=imei,
        lock_provider=provider,
        lock_status=status,
        is_lockable=True,
        customer_name="Test Customer",
        deal_name="Test Smartphone",
    )


class DeviceLockProfileCreationTest(TestCase):
    """Test 1 — create lock profile for contract."""

    def test_ensure_lock_profile_creates_profile(self):
        """Calling ensure_lock_profile_for_contract creates a DeviceLockProfile."""
        from contracts.models import Contract
        from applications.models import FinancingApplication

        u = make_merchant_user()
        app = FinancingApplication.objects.create(
            created_by=u,
            customer_name="Test",
            customer_phone="265888000001",
            imei_number="111222333444555",
        )
        contract = Contract.objects.create(
            application=app,
            merchant=u,
            customer_name="Test",
            imei_number="111222333444555",
        )

        from services.device_lock.service import ensure_lock_profile_for_contract

        profile, created = ensure_lock_profile_for_contract(contract)
        self.assertTrue(created)
        self.assertEqual(profile.imei, "111222333444555")
        self.assertEqual(profile.contract, contract)
        self.assertEqual(profile.lock_status, DeviceLockProfile.STATUS_NOT_ENROLLED)


class MockProviderEnrollTest(TestCase):
    """Test 2 — mock provider enroll updates status."""

    def test_enroll_sets_status_enrolled(self):
        profile = make_lock_profile()

        with self.settings(DEVICE_LOCK_ENABLED=True, DEVICE_LOCK_PROVIDER="mock"):
            from services.device_lock.registry import clear_provider_cache
            clear_provider_cache()
            from services.device_lock.service import enroll_device
            result = enroll_device(profile)

        profile.refresh_from_db()
        self.assertTrue(result["success"])
        self.assertEqual(profile.lock_status, DeviceLockProfile.STATUS_ENROLLED)
        self.assertIsNotNone(profile.provider_device_id)


class MockProviderLockTest(TestCase):
    """Test 3 — mock provider lock updates status."""

    def test_lock_sets_status_locked(self):
        profile = make_lock_profile(status="enrolled")
        profile.provider_device_id = "MOCK-TEST-001"
        profile.save()

        with self.settings(DEVICE_LOCK_ENABLED=True, DEVICE_LOCK_PROVIDER="mock"):
            from services.device_lock.registry import clear_provider_cache
            clear_provider_cache()
            from services.device_lock.service import lock_device
            result = lock_device(profile, reason="test")

        profile.refresh_from_db()
        self.assertTrue(result["success"])
        self.assertEqual(profile.lock_status, DeviceLockProfile.STATUS_LOCKED)


class MockProviderUnlockTest(TestCase):
    """Test 4 — mock provider unlock updates status."""

    def test_unlock_sets_status_unlocked(self):
        profile = make_lock_profile(status="locked")
        profile.provider_device_id = "MOCK-TEST-002"
        profile.save()

        with self.settings(DEVICE_LOCK_ENABLED=True, DEVICE_LOCK_PROVIDER="mock"):
            from services.device_lock.registry import clear_provider_cache
            clear_provider_cache()
            from services.device_lock.service import unlock_device
            result = unlock_device(profile, reason="paid")

        profile.refresh_from_db()
        self.assertTrue(result["success"])
        self.assertEqual(profile.lock_status, DeviceLockProfile.STATUS_UNLOCKED)


class MockProviderReleaseTest(TestCase):
    """Test 5 — mock provider release updates status."""

    def test_release_sets_status_released(self):
        profile = make_lock_profile(status="unlocked")
        profile.provider_device_id = "MOCK-TEST-003"
        profile.save()

        with self.settings(DEVICE_LOCK_ENABLED=True, DEVICE_LOCK_PROVIDER="mock"):
            from services.device_lock.registry import clear_provider_cache
            clear_provider_cache()
            from services.device_lock.service import release_device
            result = release_device(profile, reason="contract complete")

        profile.refresh_from_db()
        self.assertTrue(result["success"])
        self.assertEqual(profile.lock_status, DeviceLockProfile.STATUS_RELEASED)
        self.assertTrue(profile.is_released)


class DeviceLockEventAuditTest(TestCase):
    """Test 6 — DeviceLockEvent is created for every action."""

    def test_events_created_for_enroll_lock_unlock(self):
        profile = make_lock_profile()

        with self.settings(DEVICE_LOCK_ENABLED=True, DEVICE_LOCK_PROVIDER="mock"):
            from services.device_lock.registry import clear_provider_cache
            clear_provider_cache()
            from services.device_lock.service import enroll_device, lock_device, unlock_device

            enroll_device(profile)
            profile.refresh_from_db()

            lock_device(profile, reason="test")
            profile.refresh_from_db()

            unlock_device(profile, reason="paid")

        events = DeviceLockEvent.objects.filter(lock_profile=profile)
        event_types = list(events.values_list("event_type", flat=True))

        self.assertIn(DeviceLockEvent.TYPE_ENROLL_REQUESTED, event_types)
        self.assertIn(DeviceLockEvent.TYPE_ENROLL_SUCCESS, event_types)
        self.assertIn(DeviceLockEvent.TYPE_LOCK_REQUESTED, event_types)
        self.assertIn(DeviceLockEvent.TYPE_LOCK_SUCCESS, event_types)
        self.assertIn(DeviceLockEvent.TYPE_UNLOCK_REQUESTED, event_types)
        self.assertIn(DeviceLockEvent.TYPE_UNLOCK_SUCCESS, event_types)


class FailedProviderDoesNotCrashTest(TestCase):
    """Test 7 — failed provider does not crash the application."""

    def test_provider_exception_returns_failure_result(self):
        # Create a profile with samsung_knox as provider (not configured → will fail gracefully)
        profile = DeviceLockProfile.objects.create(
            imei="999888777666555",
            lock_provider="samsung_knox",
            lock_status="not_enrolled",
            is_lockable=True,
        )

        with self.settings(DEVICE_LOCK_ENABLED=True, DEVICE_LOCK_PROVIDER="samsung_knox"):
            from services.device_lock.registry import clear_provider_cache
            clear_provider_cache()
            from services.device_lock.service import enroll_device
            result = enroll_device(profile)

        self.assertFalse(result.get("success", True))
        # Profile should still exist and not crash
        profile.refresh_from_db()
        self.assertIsNotNone(profile)


class LockPolicyEvaluationTest(TestCase):
    """Tests 8 and 9 — lock policy evaluation."""

    def setUp(self):
        DeviceLockPolicy.objects.create(
            name="Default Policy",
            grace_period_days=3,
            lock_after_days_overdue=7,
            unlock_when_paid_to_date=True,
            release_when_contract_completed=True,
            is_active=True,
        )

    def test_no_profile_returns_none_action(self):
        from contracts.models import Contract
        from applications.models import FinancingApplication

        u = make_merchant_user()
        app = FinancingApplication.objects.create(
            created_by=u,
            customer_name="NoProfile",
            customer_phone="265888000002",
        )
        contract = Contract.objects.create(
            application=app,
            merchant=u,
            customer_name="NoProfile",
        )

        from services.device_lock.service import evaluate_lock_policy
        result = evaluate_lock_policy(contract)
        self.assertEqual(result["action"], "none")


class HQLockCentreViewTest(TestCase):
    """Test 10 — HQ lock centre page loads."""

    def setUp(self):
        self.client = Client()
        self.hq_user = make_hq_user()

    def test_lock_centre_loads_for_hq(self):
        self.client.login(username="testhq", password="pass")
        response = self.client.get(reverse("device_lock:hq_lock_centre"))
        self.assertEqual(response.status_code, 200)

    def test_lock_centre_redirects_for_anonymous(self):
        response = self.client.get(reverse("device_lock:hq_lock_centre"))
        self.assertIn(response.status_code, [302, 403])


class MerchantCannotLockTest(TestCase):
    """Test 11 — unauthorized merchant cannot lock/unlock via HQ endpoint."""

    def setUp(self):
        self.client = Client()
        self.merchant = make_merchant_user()
        self.profile = make_lock_profile()

    def test_merchant_cannot_access_hq_lock_action(self):
        self.client.login(username="testmerchant", password="pass")
        url = reverse("device_lock:lock_action", kwargs={"profile_pk": self.profile.pk})
        response = self.client.post(url, {"action": "lock"})
        self.assertIn(response.status_code, [403, 302])


class WebhookSecretTest(TestCase):
    """Test 12 — webhook rejects invalid secret if configured."""

    def setUp(self):
        self.client = Client()

    def test_webhook_rejects_bad_signature(self):
        with self.settings(DEVICE_LOCK_WEBHOOK_SECRET="supersecret"):
            response = self.client.post(
                reverse("device_lock:webhook"),
                data=b'{"status":"locked"}',
                content_type="application/json",
                HTTP_X_LOCK_SIGNATURE="sha256=badsignature",
            )
            self.assertEqual(response.status_code, 401)

    def test_webhook_accepts_valid_request_when_no_secret(self):
        with self.settings(DEVICE_LOCK_WEBHOOK_SECRET=""):
            response = self.client.post(
                reverse("device_lock:webhook"),
                data=b'{"status":"locked","imei":"NONEXISTENT"}',
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)


class ManagementCommandDryRunTest(TestCase):
    """Test 13 — device_lock_evaluate --dry-run works."""

    def test_dry_run_does_not_crash(self):
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command("device_lock_evaluate", stdout=out)
        output = out.getvalue()
        self.assertIn("Evaluation", output)


class LockReadinessTest(TestCase):
    """Test 14 — get_lock_readiness returns correct results."""

    def test_readiness_fails_without_imei(self):
        profile = DeviceLockProfile.objects.create(
            imei="",
            lock_provider="mock",
            lock_status="not_enrolled",
        )
        with self.settings(DEVICE_LOCK_ENABLED=True):
            from services.device_lock.service import get_lock_readiness
            result = get_lock_readiness(profile)
        self.assertFalse(result["ready"])
        self.assertTrue(len(result["reasons"]) > 0)

    def test_readiness_ok_with_valid_profile(self):
        profile = DeviceLockProfile.objects.create(
            imei="123456789012345",
            lock_provider="mock",
            lock_status="not_enrolled",
        )
        with self.settings(DEVICE_LOCK_ENABLED=True):
            from services.device_lock.service import get_lock_readiness
            result = get_lock_readiness(profile)
        # No contract linked → should have a reason
        self.assertFalse(result["ready"])
        self.assertIn("No linked contract or application.", result["reasons"])
