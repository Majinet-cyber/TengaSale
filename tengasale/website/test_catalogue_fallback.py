from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from deals.models import DeviceBrand, DeviceDeal
from .views import _get_public_phone_offers


class PublicCatalogueFallbackTests(TestCase):
    def test_empty_live_catalogue_exposes_disclosed_historical_plans(self):
        DeviceDeal.objects.all().delete()
        offers = _get_public_phone_offers()
        self.assertEqual([o["name"] for o in offers], ["itel A50C", "Tecno Spark 50", "Samsung Galaxy A15"])
        self.assertEqual([o["deposit"] for o in offers], [120000, 175000, 220000])
        self.assertEqual([o["payments"] for o in offers], [
            {"daily": 2450, "weekly": 17150, "monthly": 73500},
            {"daily": 3100, "weekly": 21700, "monthly": 93000},
            {"daily": 4250, "weekly": 29750, "monthly": 127500},
        ])
        self.assertTrue(all(o["is_demo"] and o["id"] is None for o in offers))
        response = self.client.get(reverse("public_home"))
        self.assertContains(response, "Illustrative plans")
        self.assertContains(response, "final availability and pricing")
        self.assertFalse(DeviceDeal.objects.exists())

    def test_eligible_live_deal_overrides_all_demo_plans(self):
        brand = DeviceBrand.objects.create(name="Tecno")
        deal = DeviceDeal.objects.create(brand=brand, model_name="Live phone", default_cash_price=Decimal("480000"), deposit_percent=Decimal("13"), loan_multiplier=Decimal("2.5"), term_months=12, is_active=True, stock_status=DeviceDeal.STOCK_IN)
        offers = _get_public_phone_offers()
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0]["id"], deal.pk)
        self.assertFalse(offers[0]["is_demo"])
        self.assertEqual(offers[0]["payments"]["weekly"], int(round(deal.weekly_payment)))
        self.assertNotContains(self.client.get(reverse("public_home")), 'id="catalogueNotice"')

    def test_inactive_or_out_of_stock_deals_do_not_suppress_fallback(self):
        brand = DeviceBrand.objects.create(name="Tecno")
        DeviceDeal.objects.create(brand=brand, model_name="Unavailable", is_active=True, stock_status=DeviceDeal.STOCK_OUT)
        self.assertTrue(all(o["is_demo"] for o in _get_public_phone_offers()))
