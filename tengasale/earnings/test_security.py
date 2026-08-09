from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.test import TestCase
from django.urls import reverse

from accounts.models import UserProfile
from accounts.utils import assign_role
from .models import EarningsSecurityEvent


class EarningsLockSecurityTests(TestCase):
    password = "test-pass-123"
    pin = "4839"

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="lockedmerchant", password=self.password)
        assign_role(self.user, "merchant")
        self.profile = UserProfile.objects.get(user=self.user)
        self.client.login(username=self.user.username, password=self.password)

    def enable(self):
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
            response = self.client.post(reverse("earnings_unlock"), {"pin": "0000"})
        self.profile.refresh_from_db()
        self.assertIsNotNone(self.profile.earnings_locked_until)
        self.assertContains(response, "Too many attempts")
        self.assertEqual(EarningsSecurityEvent.objects.filter(user=self.user, event_type="EARNINGS_UNLOCK_FAILED").count(), 5)

    def test_reset_requires_main_account_password(self):
        self.enable()
        self.client.post(reverse("earnings_pin_reset"), {"password": "wrong", "pin": "2468", "confirm_pin": "2468"})
        self.profile.refresh_from_db()
        self.assertTrue(check_password(self.pin, self.profile.earnings_pin_hash))
        self.client.post(reverse("earnings_pin_reset"), {"password": self.password, "pin": "2468", "confirm_pin": "2468"})
        self.profile.refresh_from_db()
        self.assertTrue(check_password("2468", self.profile.earnings_pin_hash))
        self.assertTrue(EarningsSecurityEvent.objects.filter(user=self.user, event_type="EARNINGS_PIN_RESET").exists())
