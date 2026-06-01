from decimal import Decimal
from io import StringIO
from importlib import import_module

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounts.utils import assign_role
from .models import DeviceBrand, DeviceDeal


class DealUrlTests(TestCase):
    def test_all_deals_url_name_resolves(self):
        self.assertEqual(reverse("all_deals"), "/deals/")


class AllDealsPageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="merchant", password="test-pass-123")
        assign_role(self.user, "merchant")
        self.client.login(username="merchant", password="test-pass-123")

    def test_all_deals_page_loads_as_guided_selector(self):
        response = self.client.get(reverse("all_deals"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose your new smartphone")
        self.assertContains(response, "Compare cash price, deposit, repayments, and early settlement options")
        for brand_name in ["TECNO", "itel", "Redmi"]:
            self.assertContains(response, brand_name)
        self.assertContains(response, "Choose a model")
        self.assertContains(response, "Choose specs")
        self.assertContains(response, "Pay early, pay less")
        self.assertContains(response, "3 months")
        self.assertContains(response, "25% discount")
        self.assertContains(response, "6 months")
        self.assertContains(response, "15% discount")
        self.assertContains(response, "deal-summary-card")
        self.assertContains(response, f'href="{settings.TENGASALE_WHATSAPP_LINK}"')
        self.assertContains(response, 'class="icon-button whatsapp-button"')
        self.assertContains(response, 'aria-label="WhatsApp support"')
        self.assertContains(response, "notification-button")
        self.assertContains(response, "logout-button")

    def test_seeded_deals_render_in_page_data(self):
        call_command("seed_tengasale")

        response = self.client.get(reverse("all_deals"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "all-deals-data")
        self.assertContains(response, "loan_multiplier")
        self.assertContains(response, "deposit_percent")
        self.assertContains(response, "default_cash_price")


class DealAdminImportTests(TestCase):
    def test_importing_deals_admin_does_not_create_model_conflicts(self):
        admin_module = import_module("deals.admin")

        self.assertIs(admin_module.DeviceBrand, DeviceBrand)
        self.assertIs(admin_module.DeviceDeal, DeviceDeal)

    def test_device_models_are_registered_in_admin(self):
        self.assertIn(DeviceBrand, admin.site._registry)
        self.assertIn(DeviceDeal, admin.site._registry)


class DeviceDealModelTests(TestCase):
    def make_deal(self, **overrides):
        brand = overrides.pop("brand", None) or DeviceBrand.objects.filter(name="TECNO").first()
        if brand is None:
            brand = DeviceBrand.objects.create(name="TECNO")
        data = {
            "brand": brand,
            "model_name": "Pop 10C",
            "specs": "2+64",
            "min_cash_price": Decimal("320000.00"),
            "max_cash_price": Decimal("380000.00"),
            "default_cash_price": Decimal("350000.00"),
            "cash_price": Decimal("350000.00"),
            "deposit_percent": Decimal("13.00"),
            "loan_multiplier": Decimal("2.50"),
            "term_months": 12,
            "total_12_month_price": Decimal("875000.00"),
        }
        data.update(overrides)
        return DeviceDeal(**data)

    def test_device_brand_can_be_created(self):
        brand = DeviceBrand.objects.create(name="Tenga")

        self.assertEqual(str(brand), "Tenga")

    def test_deposit_percent_below_13_is_invalid(self):
        deal = self.make_deal(deposit_percent=Decimal("12.99"))

        with self.assertRaises(ValidationError):
            deal.full_clean()

    def test_deposit_percent_above_16_is_invalid(self):
        deal = self.make_deal(deposit_percent=Decimal("16.01"))

        with self.assertRaises(ValidationError):
            deal.full_clean()

    def test_deposit_percent_13_and_16_are_valid(self):
        for percent in [Decimal("13.00"), Decimal("16.00")]:
            deal = self.make_deal(deposit_percent=percent)
            deal.full_clean()

    def test_default_cash_price_must_be_between_min_and_max(self):
        deal = self.make_deal(default_cash_price=Decimal("390000.00"))

        with self.assertRaises(ValidationError):
            deal.full_clean()

    def test_device_deal_calculates_prices_from_selected_cash_price(self):
        deal = self.make_deal()

        selected_cash_price = Decimal("400000.00")

        self.assertEqual(deal.calculated_total_loan(selected_cash_price), Decimal("1000000.00"))
        self.assertEqual(deal.calculated_deposit(selected_cash_price), Decimal("130000.00"))
        self.assertEqual(deal.calculated_monthly_payment(selected_cash_price), Decimal("83333.33"))
        self.assertEqual(deal.calculated_daily_payment(selected_cash_price), Decimal("2777.78"))
        self.assertEqual(deal.calculated_6_month_total(selected_cash_price), Decimal("850000.00"))
        self.assertEqual(deal.calculated_6_month_monthly(selected_cash_price), Decimal("141666.67"))
        self.assertEqual(deal.calculated_6_month_daily(selected_cash_price), Decimal("4722.22"))
        self.assertEqual(deal.calculated_3_month_total(selected_cash_price), Decimal("750000.00"))
        self.assertEqual(deal.calculated_3_month_monthly(selected_cash_price), Decimal("250000.00"))
        self.assertEqual(deal.calculated_3_month_daily(selected_cash_price), Decimal("8333.33"))


class SeedTengaSaleCommandTests(TestCase):
    def test_seed_tengasale_creates_required_brands_and_deals(self):
        call_command("seed_tengasale")

        for brand_name in ["TECNO", "itel", "Redmi"]:
            brand = DeviceBrand.objects.get(name=brand_name)
            self.assertTrue(DeviceDeal.objects.filter(brand=brand).exists())

    def test_seed_tengasale_is_idempotent(self):
        call_command("seed_tengasale")
        first_brand_count = DeviceBrand.objects.count()
        first_deal_count = DeviceDeal.objects.count()

        call_command("seed_tengasale")

        self.assertEqual(DeviceBrand.objects.count(), first_brand_count)
        self.assertEqual(DeviceDeal.objects.count(), first_deal_count)


class SeedDeviceCatalogCommandTests(TestCase):
    def test_dry_run_does_not_create_catalog_records(self):
        out = StringIO()

        call_command("seed_device_catalog", "--dry-run", stdout=out)

        self.assertEqual(DeviceDeal.objects.filter(catalog_source="seed_device_catalog").count(), 0)
        self.assertIn("DRY RUN summary", out.getvalue())

    def test_seed_device_catalog_creates_expected_catalog(self):
        call_command("seed_device_catalog", stdout=StringIO())

        self.assertEqual(DeviceDeal.objects.filter(catalog_source="seed_device_catalog").count(), 40)
        samsung = DeviceDeal.objects.get(
            brand__name="Samsung",
            model_name="Galaxy A15",
            specs="4GB RAM / 128GB storage",
            condition=DeviceDeal.CONDITION_NEW,
        )
        self.assertEqual(samsung.stock_status, DeviceDeal.STOCK_IN)
        self.assertTrue(samsung.is_active)
        self.assertTrue(samsung.is_lock_ready)
        self.assertEqual(samsung.deposit_percent, Decimal("13.00"))
        self.assertEqual(samsung.term_months, 12)
        self.assertGreater(samsung.popularity_score, 0)

    def test_seed_device_catalog_is_idempotent(self):
        call_command("seed_device_catalog", stdout=StringIO())
        first_count = DeviceDeal.objects.filter(catalog_source="seed_device_catalog").count()

        call_command("seed_device_catalog", stdout=StringIO())

        self.assertEqual(DeviceDeal.objects.filter(catalog_source="seed_device_catalog").count(), first_count)

    def test_update_only_skips_missing_records(self):
        out = StringIO()

        call_command("seed_device_catalog", "--update-only", stdout=out)

        self.assertEqual(DeviceDeal.objects.filter(catalog_source="seed_device_catalog").count(), 0)
        self.assertIn("40 skipped", out.getvalue())

    def test_clear_deletes_only_seeded_catalog_records(self):
        brand = DeviceBrand.objects.create(name="Custom")
        DeviceDeal.objects.create(
            brand=brand,
            model_name="Owner Created",
            specs="4GB RAM / 128GB storage",
            cash_price=Decimal("100000.00"),
            min_cash_price=Decimal("95000.00"),
            max_cash_price=Decimal("110000.00"),
            default_cash_price=Decimal("100000.00"),
            deposit_percent=Decimal("13.00"),
            loan_multiplier=Decimal("2.50"),
            term_months=12,
            total_12_month_price=Decimal("250000.00"),
            catalog_source="",
        )
        call_command("seed_device_catalog", stdout=StringIO())

        call_command("seed_device_catalog", "--clear", stdout=StringIO())

        self.assertEqual(DeviceDeal.objects.filter(catalog_source="seed_device_catalog").count(), 0)
        self.assertTrue(DeviceDeal.objects.filter(model_name="Owner Created").exists())
