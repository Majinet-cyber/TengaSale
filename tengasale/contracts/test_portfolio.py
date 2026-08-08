from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.utils import assign_role
from applications.models import FinancingApplication
from device_lock.models import DeviceLockProfile
from portal.models import PaymentContract

from .models import Contract
from .portfolio import (
    merchant_active_contracts,
    merchant_financed_contracts,
    merchant_locked_contracts,
    merchant_overdue_contracts,
)


class MerchantPortfolioTruthTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.merchant = User.objects.create_user("portfolio-merchant", password="pass12345")
        self.other = User.objects.create_user("other-merchant", password="pass12345")
        assign_role(self.merchant, "merchant")
        assign_role(self.other, "merchant")
        self.client.login(username="portfolio-merchant", password="pass12345")

    def make_contract(self, merchant, suffix, *, payment_status, lock_status="unlocked"):
        app = FinancingApplication.objects.create(
            created_by=merchant,
            status="contract_complete",
            customer_name=f"Customer {suffix}",
            customer_phone=f"990000{suffix}",
            national_id=f"ID{suffix}",
            imei_number=f"1234567890{int(suffix):05d}",
        )
        contract = Contract.objects.create(
            application=app,
            merchant=merchant,
            customer_name=app.customer_name,
            customer_phone=app.customer_phone,
            national_id=app.national_id,
            imei_number=app.imei_number,
            total_loan=Decimal("500000"),
            deposit_amount=Decimal("65000"),
            deposit_paid=True,
            status=Contract.STATUS_COMPLETE,
        )
        PaymentContract.objects.create(
            source_application=app,
            customer_name=app.customer_name,
            customer_phone=app.customer_phone,
            total_amount=Decimal("500000"),
            status=payment_status,
            device_lock_status=lock_status,
        )
        return contract

    def test_authoritative_counts_overlap_and_reconcile_with_lists(self):
        self.make_contract(self.merchant, "1", payment_status=PaymentContract.STATUS_ACTIVE)
        self.make_contract(self.merchant, "2", payment_status=PaymentContract.STATUS_OVERDUE, lock_status="locked")
        self.make_contract(self.other, "3", payment_status=PaymentContract.STATUS_OVERDUE, lock_status="locked")

        self.assertEqual(merchant_financed_contracts(self.merchant).count(), 2)
        self.assertEqual(merchant_active_contracts(self.merchant).count(), 1)
        self.assertEqual(merchant_locked_contracts(self.merchant).count(), 1)
        self.assertEqual(merchant_overdue_contracts(self.merchant).count(), 1)

        expected = {"financed": 2, "active": 1, "locked": 1, "overdue": 1}
        for portfolio_filter, count in expected.items():
            response = self.client.get(reverse("merchant_portfolio"), {"portfolio": portfolio_filter})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["result_count"], count)
            self.assertNotContains(response, "Create financing contract")
            self.assertNotContains(response, "Customer 3")

    def test_search_and_detail_are_merchant_isolated(self):
        own = self.make_contract(self.merchant, "4", payment_status=PaymentContract.STATUS_ACTIVE)
        other = self.make_contract(self.other, "5", payment_status=PaymentContract.STATUS_ACTIVE)
        response = self.client.get(reverse("merchant_portfolio"), {"q": "Customer"})
        self.assertContains(response, own.customer_name)
        self.assertNotContains(response, other.customer_name)
        self.assertEqual(self.client.get(reverse("contract_detail", args=[other.id])).status_code, 403)

    def test_unissued_contract_is_not_portfolio(self):
        app = FinancingApplication.objects.create(created_by=self.merchant, customer_name="Not issued")
        Contract.objects.create(application=app, merchant=self.merchant, customer_name="Not issued")
        self.assertEqual(merchant_financed_contracts(self.merchant).count(), 0)
