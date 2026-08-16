from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from applications.models import FinancingApplication
from portal.models import PaymentContract, PaymentTransaction

from .analytics import application_trend, collections_trend, grouped_breakdown


class HQAnalyticsTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.contract = PaymentContract.objects.create(
            customer_name="Graph Customer",
            customer_phone="0999000111",
            total_amount=Decimal("100000"),
            status=PaymentContract.STATUS_ACTIVE,
        )

    def payment(self, *, amount, days_ago, status="paid", provider="airtel_money"):
        txn = PaymentTransaction.objects.create(
            payment_contract=self.contract,
            amount=Decimal(amount),
            phone="0999000111",
            status=status,
            provider=provider,
        )
        stamp = timezone.now() - timedelta(days=days_ago)
        PaymentTransaction.objects.filter(pk=txn.pk).update(paid_at=stamp, created_at=stamp)
        return txn

    def test_collections_trend_uses_paid_database_values_and_fills_zero_days(self):
        self.payment(amount="2500", days_ago=1)
        self.payment(amount="7500", days_ago=1)
        self.payment(amount="9999", days_ago=1, status="failed")

        trend = collections_trend(PaymentTransaction.objects.all(), today=self.today)

        self.assertEqual(len(trend), 7)
        yesterday = trend[-2]
        self.assertEqual(yesterday["amount"], Decimal("10000"))
        self.assertEqual(yesterday["count"], 2)
        self.assertEqual(yesterday["height"], 100.0)
        self.assertEqual(trend[-1]["amount"], Decimal("0"))

    def test_application_trend_counts_submissions_and_approved_subset(self):
        user = get_user_model().objects.create_user(username="graph-owner")
        for status in ("submitted", "approved", "approved"):
            app = FinancingApplication.objects.create(created_by=user, status=status)
            stamp = timezone.now() - timedelta(days=2)
            FinancingApplication.objects.filter(pk=app.pk).update(submitted_at=stamp)

        trend = application_trend(FinancingApplication.objects.all(), today=self.today)

        point = trend[-3]
        self.assertEqual(point["applications"], 3)
        self.assertEqual(point["approvals"], 2)

    def test_grouped_breakdown_uses_full_queryset_and_empty_state_is_empty(self):
        self.payment(amount="1000", days_ago=0, provider="airtel_money")
        self.payment(amount="1000", days_ago=0, provider="airtel_money")
        self.payment(amount="1000", days_ago=0, provider="tnm_mpamba")

        split = grouped_breakdown(
            PaymentTransaction.objects.all(),
            "provider",
            labels=dict(PaymentTransaction.PROVIDER_CHOICES),
        )

        self.assertEqual([(row["key"], row["count"]) for row in split], [("airtel_money", 2), ("tnm_mpamba", 1)])
        self.assertEqual(grouped_breakdown(PaymentTransaction.objects.none(), "provider"), [])
