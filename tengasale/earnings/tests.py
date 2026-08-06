from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from applications.models import FinancingApplication
from commissions.models import Commission
from contracts.models import Contract
from rewards.models import SpinWallet
from portal.models import PaymentContract, PaymentTransaction

from accounts.utils import assign_role
from .models import Wallet, WalletTransaction


class EarningsUrlTests(TestCase):
    def test_earnings_home_url_name_resolves(self):
        self.assertEqual(reverse("earnings_home"), "/earnings/")

    def test_earnings_extra_url_names_resolve(self):
        self.assertEqual(reverse("merchant_leaderboard"), "/earnings/leaderboard/")
        self.assertEqual(reverse("spin_rewards"), "/earnings/spin/")
        self.assertEqual(reverse("payments_home"), "/payments/")


class EarningsPageTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="merchant", password="test-pass-123")
        assign_role(self.user, "merchant")
        self.client.login(username="merchant", password="test-pass-123")
        self.app = FinancingApplication.objects.create(
            created_by=self.user,
            status="approved",
            customer_name="Jane Banda",
            customer_phone="990870616",
            national_id="RQXFVZC9",
            selected_cash_price=Decimal("400000.00"),
            calculated_total_loan=Decimal("1000000.00"),
            calculated_deposit_amount=Decimal("52000.00"),
            calculated_monthly_payment=Decimal("83333.00"),
            calculated_daily_payment=Decimal("2778.00"),
            term_months=12,
        )

    def test_earnings_page_displays_pending_commission_and_spins(self):
        Commission.objects.create(
            user=self.user,
            application=self.app,
            role=Commission.ROLE_MERCHANT,
            commission_percent=Decimal("1.00"),
            sale_amount=Decimal("1000000.00"),
            amount=Decimal("10000.00"),
            status=Commission.STATUS_PENDING,
        )
        SpinWallet.objects.create(user=self.user, available_spins=2, total_spins_earned=2)

        response = self.client.get(reverse("earnings_home"))

        self.assertContains(response, "Pending commissions")
        self.assertContains(response, "MWK 10,000")
        self.assertContains(response, "Rewards")
        self.assertNotContains(response, "SPIN &amp; WIN")
        self.assertNotContains(response, 'data-testid="topbar-whatsapp"')
        self.assertContains(response, 'class="icon-button whatsapp-button"')
        self.assertContains(response, 'aria-label="WhatsApp support"')
        self.assertContains(response, "notification-button")
        self.assertContains(response, "logout-button")

    def test_earnings_page_shows_wallet_tabs_positive_negative_and_contract(self):
        wallet = Wallet.objects.create(user=self.user, balance=Decimal("5000.00"))
        contract = Contract.from_application(self.app)[0]
        WalletTransaction.objects.create(
            wallet=wallet,
            transaction_type="commission",
            amount=Decimal("1500.00"),
            contract_number=contract.contract_number,
        )
        WalletTransaction.objects.create(
            wallet=wallet,
            transaction_type="payout",
            amount=Decimal("-500.00"),
            contract_number=contract.contract_number,
        )

        response = self.client.get(f"{reverse('earnings_home')}?tab=transactions")

        self.assertContains(response, "Wallet balance")
        self.assertContains(response, "Overview")
        self.assertContains(response, "Transactions")
        self.assertContains(response, "Payouts")
        self.assertContains(response, "Rewards")
        self.assertContains(response, "amount-positive")
        self.assertContains(response, "amount-negative")
        self.assertContains(response, contract.contract_number)
        self.assertContains(response, "VIEW CONTRACT")

    def test_rewards_tab_contains_spin_and_win(self):
        SpinWallet.objects.create(user=self.user, available_spins=2, total_spins_earned=2)

        response = self.client.get(f"{reverse('earnings_home')}?tab=rewards")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rewards")
        self.assertContains(response, "Available spins")
        self.assertContains(response, "SPIN & WIN")

    def test_payouts_tab_renders(self):
        response = self.client.get(f"{reverse('earnings_home')}?tab=payouts")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Payout batches")
        self.assertContains(response, "Gross")
        self.assertContains(response, "Withholding tax")
        self.assertContains(response, "Net")

    def test_merchant_only_sees_own_transactions_and_commissions(self):
        other = get_user_model().objects.create_user(username="other", password="test-pass-123")
        assign_role(other, "merchant")
        other_app = FinancingApplication.objects.create(created_by=other, status="approved")
        Commission.objects.create(
            user=other,
            application=other_app,
            role=Commission.ROLE_MERCHANT,
            commission_percent=Decimal("1.00"),
            sale_amount=Decimal("1000000.00"),
            amount=Decimal("99999.00"),
            status=Commission.STATUS_PENDING,
        )

        response = self.client.get(reverse("earnings_home"))

        self.assertNotContains(response, "99999.00")


class MerchantLeaderboardTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.users = [
            User.objects.create_user(username=f"merchant{i}", password="test-pass-123")
            for i in range(12)
        ]
        for user in self.users:
            assign_role(user, "merchant")
        self.client.login(username="merchant0", password="test-pass-123")

    def test_leaderboard_ranks_by_sales_count_and_limits_top_ten(self):
        for index, user in enumerate(self.users):
            for _ in range(index + 1):
                FinancingApplication.objects.create(created_by=user, status="approved")

        response = self.client.get(reverse("merchant_leaderboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "merchant11")
        self.assertContains(response, "merchant2")
        self.assertNotContains(response, ">merchant1<")
        self.assertContains(response, "Your rank: #12")


class PaymentsPageTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="merchant", password="test-pass-123")
        self.other = User.objects.create_user(username="other", password="test-pass-123")
        assign_role(self.user, "merchant")
        assign_role(self.other, "merchant")
        self.client.login(username="merchant", password="test-pass-123")
        self.app = FinancingApplication.objects.create(
            created_by=self.user,
            status="contract_complete",
            customer_name="Jane Banda",
            customer_phone="990870616",
            national_id="RQXFVZC9",
            selected_cash_price=Decimal("400000.00"),
            calculated_total_loan=Decimal("1000000.00"),
            calculated_deposit_amount=Decimal("130000.00"),
            calculated_monthly_payment=Decimal("83333.00"),
            calculated_daily_payment=Decimal("2778.00"),
            term_months=12,
        )
        self.contract = Contract.from_application(self.app)[0]

    def test_payments_page_loads_with_status_guide_tabs_search_and_pagination(self):
        response = self.client.get(reverse("payments_home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Payments")
        for text in ["Pending", "TengaSale Processing", "External Processing", "Paid", "Failed"]:
            self.assertContains(response, text)
        self.assertContains(response, "DEVICE SALES")
        self.assertContains(response, "COMMISSIONS")
        self.assertContains(response, 'placeholder="Search payments"')
        self.assertContains(response, "pagination-controls")
        self.assertContains(response, "Rows per page")

    def test_payments_page_shows_rows_and_view_contract_when_available(self):
        Commission.objects.create(
            user=self.user,
            application=self.app,
            role=Commission.ROLE_MERCHANT,
            commission_percent=Decimal("1.00"),
            sale_amount=Decimal("1000000.00"),
            amount=Decimal("10000.00"),
            status=Commission.STATUS_PENDING,
        )

        response = self.client.get(reverse("payments_home"))

        self.assertContains(response, self.contract.contract_number)
        self.assertContains(response, "MWK 10,000")
        self.assertContains(response, "VIEW CONTRACT")

    def test_payments_page_only_shows_current_users_data(self):
        other_app = FinancingApplication.objects.create(
            created_by=self.other,
            status="contract_complete",
            customer_name="Other Customer",
            customer_phone="991111111",
            national_id="OTHER1234",
            selected_cash_price=Decimal("400000.00"),
            calculated_total_loan=Decimal("1000000.00"),
            calculated_deposit_amount=Decimal("999999.00"),
            calculated_monthly_payment=Decimal("83333.00"),
            calculated_daily_payment=Decimal("2778.00"),
            term_months=12,
        )
        other_contract = Contract.from_application(other_app)[0]

        response = self.client.get(reverse("payments_home"))

        self.assertContains(response, self.contract.contract_number)
        self.assertNotContains(response, other_contract.contract_number)
        self.assertNotContains(response, "Other Customer")

    def test_payments_page_uses_portal_transactions_and_colored_status_badges(self):
        payment_contract = PaymentContract.objects.create(
            source_application=self.app,
            customer_name="Jane Banda",
            customer_phone="990870616",
            customer_national_id="RQXFVZC9",
            device_model="TECNO Pop 10C",
            cash_price=Decimal("350000.00"),
            total_amount=Decimal("875000.00"),
            deposit_required=Decimal("130000.00"),
            deposit_paid=Decimal("130000.00"),
            amount_paid=Decimal("130000.00"),
        )
        tx = PaymentTransaction.objects.create(
            payment_contract=payment_contract,
            amount=Decimal("130000.00"),
            phone="990870616",
            provider=PaymentTransaction.PROVIDER_PAYCHANGU,
            provider_reference="PCG-123",
            status=PaymentTransaction.STATUS_TENGA_PROCESSING,
        )
        PaymentTransaction.objects.create(
            payment_contract=payment_contract,
            amount=Decimal("25000.00"),
            phone="990870616",
            provider=PaymentTransaction.PROVIDER_AIRTEL,
            provider_reference="AIRTEL-321",
            status=PaymentTransaction.STATUS_PROCESSING,
        )

        response = self.client.get(reverse("payments_home") + "?q=PCG-123")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PCG-123")
        self.assertContains(response, self.contract.contract_number)
        self.assertContains(response, "TengaSale Processing")
        self.assertContains(response, "badge-tenga-processing")
        self.assertNotContains(response, "AIRTEL-321")
