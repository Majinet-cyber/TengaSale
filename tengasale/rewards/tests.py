from decimal import Decimal
from importlib import import_module

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from applications.models import FinancingApplication
from accounts.utils import assign_role

from .models import SpinConfig, SpinReward, SpinWallet
from .services import NoSpinsAvailable, award_spin_for_application, perform_spin


class SpinRewardTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="merchant", password="test-pass-123")
        assign_role(self.user, "merchant")
        self.app = FinancingApplication.objects.create(created_by=self.user)

    def test_approved_sale_gives_one_spin_once(self):
        self.assertTrue(award_spin_for_application(self.app, self.user))
        self.assertFalse(award_spin_for_application(self.app, self.user))

        wallet = SpinWallet.objects.get(user=self.user)
        self.assertEqual(wallet.available_spins, 1)
        self.assertEqual(wallet.total_spins_earned, 1)

    def test_spin_with_available_spin_reduces_wallet_and_creates_reward(self):
        SpinWallet.objects.create(user=self.user, available_spins=1, total_spins_earned=1)

        reward = perform_spin(self.user, chooser=lambda pool: Decimal("500.00"))

        wallet = SpinWallet.objects.get(user=self.user)
        self.assertEqual(wallet.available_spins, 0)
        self.assertEqual(wallet.total_spins_used, 1)
        self.assertEqual(reward.amount, Decimal("500.00"))
        self.assertEqual(SpinReward.objects.count(), 1)

    def test_spin_with_no_spins_fails_safely(self):
        with self.assertRaises(NoSpinsAvailable):
            perform_spin(self.user)

    def test_jackpot_cannot_be_awarded_more_than_once_per_week(self):
        SpinConfig.objects.update_or_create(pk=1, defaults={"jackpot_amount": Decimal("100000.00")})
        SpinWallet.objects.create(user=self.user, available_spins=2, total_spins_earned=2)

        first = perform_spin(self.user, chooser=lambda pool: Decimal("100000.00"))
        second = perform_spin(self.user, chooser=lambda pool: Decimal("100000.00"))

        self.assertEqual(first.reward_tier, SpinReward.TIER_JACKPOT)
        self.assertEqual(second.reward_tier, SpinReward.TIER_SMALL)
        self.assertEqual(SpinReward.objects.filter(reward_tier=SpinReward.TIER_JACKPOT).count(), 1)

    def test_spin_page_with_no_spins_shows_error(self):
        self.client.login(username="merchant", password="test-pass-123")

        response = self.client.post(reverse("spin_rewards"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No spins available.")


class RewardsAdminImportTests(TestCase):
    def test_admin_imports_do_not_crash(self):
        admin_module = import_module("rewards.admin")

        self.assertIs(admin_module.SpinWallet, SpinWallet)
        self.assertIn(SpinWallet, admin.site._registry)
