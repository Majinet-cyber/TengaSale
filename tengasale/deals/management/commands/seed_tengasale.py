from decimal import Decimal

from django.core.management.base import BaseCommand

from core.services import get_business_settings
from deals.models import DeviceBrand, DeviceDeal
from geography.data import MALAWI_TAS_BY_REGION
from geography.models import District, Region, TraditionalAuthority
from rewards.services import get_spin_config


class Command(BaseCommand):
    help = "Seed initial TengaSale data."

    def handle(self, *args, **options):
        self.seed_business_settings()
        self.seed_geography()
        self.seed_deals()

    def seed_business_settings(self):
        get_business_settings()
        get_spin_config()
        self.stdout.write(self.style.SUCCESS("Seeded business and spin settings."))

    def seed_geography(self):
        created_regions = 0
        created_districts = 0
        created_tas = 0
        for region_name, districts in MALAWI_TAS_BY_REGION.items():
            region, was_region_created = Region.objects.get_or_create(name=region_name)
            created_regions += int(was_region_created)
            for district_name, ta_names in districts.items():
                district, was_district_created = District.objects.get_or_create(region=region, name=district_name)
                created_districts += int(was_district_created)

                TraditionalAuthority.objects.filter(
                    district=district,
                    name__in=[f"{district_name} TA {number}" for number in range(1, 4)],
                ).delete()
                for ta_name in ta_names:
                    _, was_ta_created = TraditionalAuthority.objects.get_or_create(
                        district=district,
                        name=ta_name,
                    )
                    created_tas += int(was_ta_created)

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded geography: {created_regions} regions, {created_districts} districts, {created_tas} TAs created."
            )
        )

    def seed_deals(self):
        deals = [
            ("TECNO", "Pop 10C", "2+64", 320000, 380000, 350000),
            ("TECNO", "Spark 40", "4+128", 400000, 480000, 450000),
            ("TECNO", "Spark 50", "4+128", 500000, 600000, 550000),
            ("TECNO", "Camon 40", "8+256", 700000, 850000, 780000),
            ("itel", "City 100", "4+128", 380000, 450000, 405000),
            ("itel", "A100", "4+128", 350000, 430000, 390000),
            ("itel", "A50", "3+64", 280000, 340000, 310000),
            ("Redmi/Xiaomi", "A3", "4+128", 420000, 500000, 460000),
            ("Redmi/Xiaomi", "13C", "6+128", 520000, 650000, 580000),
            ("Redmi/Xiaomi", "A5", "4+128", 450000, 540000, 495000),
        ]

        brands = {}
        for brand_name in ["TECNO", "itel", "Redmi/Xiaomi"]:
            brand, _ = DeviceBrand.objects.update_or_create(
                name=brand_name,
                defaults={"is_active": True},
            )
            brands[brand_name] = brand

        created = 0
        updated = 0
        for brand_name, model_name, specs, min_price, max_price, default_price in deals:
            total_loan = Decimal(default_price) * Decimal("2.5")
            deal, was_created = DeviceDeal.objects.update_or_create(
                brand=brands[brand_name],
                model_name=model_name,
                specs=specs,
                defaults={
                    "cash_price": Decimal(default_price),
                    "min_cash_price": Decimal(min_price),
                    "max_cash_price": Decimal(max_price),
                    "default_cash_price": Decimal(default_price),
                    "deposit_percent": Decimal("13"),
                    "loan_multiplier": Decimal("2.5"),
                    "term_months": 12,
                    "total_12_month_price": total_loan,
                    "is_active": True,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(f"Seeded TengaSale deals: {created} created, {updated} updated.")
        )
