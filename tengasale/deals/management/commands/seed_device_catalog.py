from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from deals.models import DeviceBrand, DeviceDeal


CATALOG_SOURCE = "seed_device_catalog"
LOAN_MULTIPLIER = Decimal("2.50")
DEPOSIT_PERCENT = Decimal("13.00")
TERM_MONTHS = 12


CATALOG = [
    # TECNO
    ("Tecno", "Spark 50", "4GB RAM / 128GB storage", "500000", 98),
    ("Tecno", "Spark 30C", "4GB RAM / 128GB storage", "390000", 92),
    ("Tecno", "Spark 30", "8GB RAM / 128GB storage", "520000", 90),
    ("Tecno", "Spark 20", "8GB RAM / 256GB storage", "560000", 88),
    ("Tecno", "Pop 9", "3GB RAM / 64GB storage", "280000", 86),
    ("Tecno", "Pop 8", "3GB RAM / 64GB storage", "240000", 84),
    ("Tecno", "Camon 30", "8GB RAM / 256GB storage", "780000", 82),
    ("Tecno", "Camon 30S", "8GB RAM / 256GB storage", "820000", 80),
    ("Tecno", "Camon 40", "8GB RAM / 256GB storage", "900000", 78),
    ("Tecno", "Pova 6 Neo", "8GB RAM / 128GB storage", "660000", 76),
    # Itel
    ("Itel", "A70", "3GB RAM / 128GB storage", "260000", 96),
    ("Itel", "A80", "4GB RAM / 128GB storage", "310000", 94),
    ("Itel", "A90", "4GB RAM / 128GB storage", "340000", 90),
    ("Itel", "S23", "8GB RAM / 128GB storage", "390000", 88),
    ("Itel", "S24", "8GB RAM / 256GB storage", "490000", 86),
    ("Itel", "P55", "4GB RAM / 128GB storage", "330000", 84),
    ("Itel", "P55+", "8GB RAM / 256GB storage", "430000", 82),
    ("Itel", "RS4", "8GB RAM / 128GB storage", "520000", 80),
    ("Itel", "A60s", "4GB RAM / 64GB storage", "230000", 78),
    ("Itel", "P40", "4GB RAM / 64GB storage", "250000", 76),
    # Redmi/Xiaomi
    ("Redmi/Xiaomi", "Redmi A3", "3GB RAM / 64GB storage", "320000", 96),
    ("Redmi/Xiaomi", "Redmi A3x", "3GB RAM / 64GB storage", "300000", 94),
    ("Redmi/Xiaomi", "Redmi A5", "4GB RAM / 128GB storage", "390000", 92),
    ("Redmi/Xiaomi", "Redmi 13C", "4GB RAM / 128GB storage", "430000", 90),
    ("Redmi/Xiaomi", "Redmi 14C", "4GB RAM / 128GB storage", "480000", 88),
    ("Redmi/Xiaomi", "Redmi Note 13", "6GB RAM / 128GB storage", "660000", 86),
    ("Redmi/Xiaomi", "Redmi Note 14", "6GB RAM / 128GB storage", "740000", 84),
    ("Redmi/Xiaomi", "Redmi 12", "8GB RAM / 256GB storage", "560000", 82),
    ("Redmi/Xiaomi", "Redmi 13", "8GB RAM / 256GB storage", "620000", 80),
    ("Redmi/Xiaomi", "Poco C65", "6GB RAM / 128GB storage", "520000", 78),
    # Samsung
    ("Samsung", "Galaxy A05", "4GB RAM / 64GB storage", "360000", 96),
    ("Samsung", "Galaxy A05s", "4GB RAM / 128GB storage", "470000", 94),
    ("Samsung", "Galaxy A06", "4GB RAM / 64GB storage", "390000", 92),
    ("Samsung", "Galaxy A15", "4GB RAM / 128GB storage", "560000", 90),
    ("Samsung", "Galaxy A16", "4GB RAM / 128GB storage", "620000", 88),
    ("Samsung", "Galaxy A25", "6GB RAM / 128GB storage", "820000", 86),
    ("Samsung", "Galaxy A35", "8GB RAM / 128GB storage", "1050000", 84),
    ("Samsung", "Galaxy A55", "8GB RAM / 256GB storage", "1450000", 82),
    ("Samsung", "Galaxy S23", "8GB RAM / 256GB storage", "2200000", 80),
    ("Samsung", "Galaxy S24", "8GB RAM / 256GB storage", "2800000", 78),
]


class Command(BaseCommand):
    help = "Seed the production-ready TengaSale device finance catalog."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show changes without saving them.")
        parser.add_argument(
            "--update-only",
            action="store_true",
            help="Update matching existing catalog records and skip missing devices.",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete only records previously marked with the seed_device_catalog source.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        update_only = options["update_only"]
        clear = options["clear"]

        if clear:
            return self._clear(dry_run=dry_run)

        created = 0
        updated = 0
        skipped = 0

        with transaction.atomic():
            brands = {}
            for brand_name, model_name, specs, price, score in CATALOG:
                if brand_name not in brands:
                    brand = self._get_brand(brand_name, dry_run=dry_run)
                    brands[brand_name] = brand
                else:
                    brand = brands[brand_name]

                defaults = self._deal_defaults(price, score)
                existing = None if brand is None else self._find_existing(brand, model_name, specs)

                if existing is None and update_only:
                    skipped += 1
                    self.stdout.write(f"SKIP missing: {brand_name} {model_name} {specs}")
                    continue

                if dry_run:
                    action = "UPDATE" if existing else "CREATE"
                    if existing is None and not update_only:
                        created += 1
                    elif existing is not None:
                        updated += 1
                    self.stdout.write(f"{action}: {brand_name} {model_name} {specs} MWK {price}")
                    continue

                deal, was_created = DeviceDeal.objects.update_or_create(
                    brand=brand,
                    model_name=model_name,
                    specs=specs,
                    condition=DeviceDeal.CONDITION_NEW,
                    defaults=defaults,
                )
                if was_created:
                    created += 1
                    self.stdout.write(f"CREATED: {deal}")
                else:
                    updated += 1
                    self.stdout.write(f"UPDATED: {deal}")

            if dry_run:
                transaction.set_rollback(True)

        self._summary(created=created, updated=updated, skipped=skipped, dry_run=dry_run)

    def _get_brand(self, brand_name, dry_run=False):
        brand = DeviceBrand.objects.filter(name__iexact=brand_name).order_by("id").first()
        if brand:
            if not dry_run and brand.name != brand_name and not DeviceBrand.objects.filter(name=brand_name).exists():
                brand.name = brand_name
                brand.is_active = True
                brand.save(update_fields=["name", "is_active"])
            elif not dry_run and not brand.is_active:
                brand.is_active = True
                brand.save(update_fields=["is_active"])
            return brand

        if dry_run:
            self.stdout.write(f"CREATE BRAND: {brand_name}")
            return None

        brand, _ = DeviceBrand.objects.update_or_create(
            name=brand_name,
            defaults={"is_active": True},
        )
        return brand

    def _find_existing(self, brand, model_name, specs):
        return DeviceDeal.objects.filter(
            brand=brand,
            model_name=model_name,
            specs=specs,
            condition=DeviceDeal.CONDITION_NEW,
        ).first()

    def _deal_defaults(self, price, score):
        cash_price = Decimal(price)
        min_price = (cash_price * Decimal("0.95")).quantize(Decimal("1"))
        max_price = (cash_price * Decimal("1.10")).quantize(Decimal("1"))
        total_price = (cash_price * LOAN_MULTIPLIER).quantize(Decimal("0.01"))
        monthly_payment = (total_price / Decimal(TERM_MONTHS)).quantize(Decimal("0.01"))
        daily_payment = (total_price / Decimal(TERM_MONTHS * 30)).quantize(Decimal("0.01"))
        weekly_payment = (daily_payment * Decimal("7")).quantize(Decimal("0.01"))

        return {
            "cash_price": cash_price,
            "min_cash_price": min_price,
            "max_cash_price": max_price,
            "default_cash_price": cash_price,
            "deposit_percent": DEPOSIT_PERCENT,
            "loan_multiplier": LOAN_MULTIPLIER,
            "term_months": TERM_MONTHS,
            "total_12_month_price": total_price,
            "country": "MW",
            "is_active": True,
            "is_featured": score >= 88,
            "popularity_score": score,
            "merchant_commission_rate": Decimal("0.0100"),
            "underwriter_commission_rate": Decimal("0.0700"),
            "arrears_penalty_rate": Decimal("0.1400"),
            "stock_status": DeviceDeal.STOCK_IN,
            "is_lock_ready": True,
            "lock_provider": DeviceDeal.LOCK_OTHER,
            "catalog_source": CATALOG_SOURCE,
            "notes": (
                "Seeded production catalog placeholder. Condition: New. "
                f"Editable finance values; derived payments at seed time were approx "
                f"MWK {daily_payment:,.2f}/day, MWK {weekly_payment:,.2f}/week, "
                f"MWK {monthly_payment:,.2f}/month."
            ),
        }

    def _clear(self, dry_run=False):
        qs = DeviceDeal.objects.filter(catalog_source=CATALOG_SOURCE)
        count = qs.count()
        if dry_run:
            self.stdout.write(f"DRY RUN: would delete {count} seeded device catalog record(s).")
            self._summary(created=0, updated=0, skipped=0, dry_run=True)
            return

        deleted, _ = qs.delete()
        self.stdout.write(self.style.WARNING(f"Deleted {deleted} seeded device catalog record(s)."))
        self._summary(created=0, updated=0, skipped=0, dry_run=False)

    def _summary(self, created, updated, skipped, dry_run):
        prefix = "DRY RUN summary" if dry_run else "Seed summary"
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}: {created} created, {updated} updated, {skipped} skipped."
            )
        )
