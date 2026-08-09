from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from accounts.models import UserProfile
from accounts.utils import assign_role
from .models import EarningsSecurityEvent


class EarningsLockSecurityTests(TestCase):
    password = "test-pass-123"
    pin = "483920"

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="lockedmerchant", password=self.password)
        assign_role(self.user, "merchant")
        self.profile = UserProfile.objects.get(user=self.user)
        self.profile.phone_number = "+265991234567"
        self.profile.save(update_fields=["phone_number"])
        self.client.login(username=self.user.username, password=self.password)

    def enable(self):
        session = self.client.session
        session["earnings_owner_verified"] = {"purpose": "enable", "at": timezone.now().isoformat()}
        session.save()
        return self.client.post(reverse("earnings_lock_enable"), {"pin": self.pin, "confirm_pin": self.pin})

    def test_earnings_visible_when_lock_is_disabled(self):
        response = self.client.get(reverse("earnings_home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Wallet balance")

    def test_enabled_lock_blocks_page_and_direct_financial_route_without_leaking_values(self):
        self.enable()
        for url in (reverse("earnings_home"), reverse("payments_home")):
            response = self.client.get(url)
            self.assertRedirects(response, reverse("earnings_unlock"), fetch_redirect_response=False)
        locked = self.client.get(reverse("earnings_unlock"))
        self.assertContains(locked, "Earnings locked")
        self.assertNotContains(locked, "Wallet balance")

    def test_pin_is_hashed_and_never_rendered(self):
        self.enable()
        self.profile.refresh_from_db()
        self.assertNotEqual(self.profile.earnings_pin_hash, self.pin)
        self.assertTrue(check_password(self.pin, self.profile.earnings_pin_hash))
        response = self.client.get(reverse("profile_settings"))
        self.assertNotContains(response, self.pin)

    def test_correct_pin_unlocks_and_leaving_earnings_relocks(self):
        self.enable()
        response = self.client.post(reverse("earnings_unlock"), {"pin": self.pin})
        self.assertRedirects(response, reverse("earnings_home"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("earnings_home")).status_code, 200)
        self.client.get(reverse("merchant_dashboard"))
        self.assertRedirects(self.client.get(reverse("earnings_home")), reverse("earnings_unlock"), fetch_redirect_response=False)

    def test_wrong_pin_fails_and_five_attempts_trigger_cooldown(self):
        self.enable()
        for _ in range(5):
            response = self.client.post(reverse("earnings_unlock"), {"pin": "000000"})
        self.profile.refresh_from_db()
        self.assertIsNotNone(self.profile.earnings_locked_until)
        self.assertContains(response, "Too many attempts")
        self.assertEqual(EarningsSecurityEvent.objects.filter(user=self.user, event_type="EARNINGS_UNLOCK_FAILED").count(), 5)

    def test_reset_requires_owner_phone_verification_not_account_password(self):
        self.enable()
        new_pin = "246813"
        self.client.post(reverse("earnings_pin_reset"), {"password": self.password, "pin": new_pin, "confirm_pin": new_pin})
        self.profile.refresh_from_db()
        self.assertTrue(check_password(self.pin, self.profile.earnings_pin_hash))
        session = self.client.session
        session["earnings_owner_verified"] = {"purpose": "reset", "at": timezone.now().isoformat()}
        session.save()
        self.client.post(reverse("earnings_pin_reset"), {"pin": new_pin, "confirm_pin": new_pin})
        self.profile.refresh_from_db()
        self.assertTrue(check_password(new_pin, self.profile.earnings_pin_hash))
        self.assertTrue(EarningsSecurityEvent.objects.filter(user=self.user, event_type="EARNINGS_PIN_RESET").exists())

    def test_weak_pin_is_rejected(self):
        session = self.client.session
        session["earnings_owner_verified"] = {"purpose": "enable", "at": timezone.now().isoformat()}
        session.save()
        self.client.post(reverse("earnings_lock_enable"), {"pin": "123456", "confirm_pin": "123456"})
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.earnings_lock_enabled)

    def test_unlock_grant_expires_after_five_minutes(self):
        self.enable()
        self.client.post(reverse("earnings_unlock"), {"pin": self.pin})
        session = self.client.session
        grant = session["earnings_unlocked"]
        grant["at"] = (timezone.now() - timedelta(minutes=6)).isoformat()
        session["earnings_unlocked"] = grant
        session.save()
        self.assertRedirects(self.client.get(reverse("earnings_home")), reverse("earnings_unlock"), fetch_redirect_response=False)
