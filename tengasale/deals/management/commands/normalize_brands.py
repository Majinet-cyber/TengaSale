"""
Normalize device brand names in the database.

Merges variant spellings:
  Redmi / Xiaomi / Redmi/Xiaomi  → Redmi/Xiaomi
  TECNO / tecno / Tecno           → Tecno
  itel / ITEL / Itel              → Itel
  SAMSUNG / samsung               → Samsung
  INFINIX / Infinix               → Infinix

Removes brands left with zero active deals.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from deals.models import DeviceBrand, DeviceDeal


# Maps of (lowercased alias → canonical name)
CANONICAL = {
    "redmi":          "Redmi/Xiaomi",
    "xiaomi":         "Redmi/Xiaomi",
    "redmi/xiaomi":   "Redmi/Xiaomi",
    "redmi / xiaomi": "Redmi/Xiaomi",
    "redmi-xiaomi":   "Redmi/Xiaomi",
    "tecno":          "Tecno",
    "itel":           "Itel",
    "samsung":        "Samsung",
    "infinix":        "Infinix",
}


class Command(BaseCommand):
    help = "Normalize device brand names (remove duplicates, fix casing)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without saving",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be saved"))

        with transaction.atomic():
            self._normalize(dry_run)
            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(self.style.SUCCESS("Brand normalization complete."))

    def _normalize(self, dry_run):
        brands = list(DeviceBrand.objects.all().order_by("id"))
        processed = set()  # brand ids we have already handled

        for brand in brands:
            if brand.id in processed:
                continue

            canonical = CANONICAL.get(brand.name.strip().lower())
            if canonical is None:
                # Brand name not in our alias list — keep as-is but check for
                # exact duplicates (same name, different id)
                canonical = brand.name

            # Find the "primary" brand with this canonical name
            primary = (
                DeviceBrand.objects.filter(name=canonical)
                .order_by("id")
                .first()
            )

            if primary is None:
                # Rename this brand to the canonical form
                if brand.name != canonical:
                    self.stdout.write(
                        f"RENAME: {brand.name!r} → {canonical!r} (id={brand.id})"
                    )
                    if not dry_run:
                        brand.name = canonical
                        brand.is_active = True
                        brand.save(update_fields=["name", "is_active"])
                primary = brand
            elif primary.id != brand.id:
                # Merge `brand` into `primary`
                deal_count = DeviceDeal.objects.filter(brand=brand).count()
                self.stdout.write(
                    f"MERGE:  {brand.name!r} (id={brand.id}, {deal_count} deals)"
                    f" → {primary.name!r} (id={primary.id})"
                )
                if not dry_run:
                    DeviceDeal.objects.filter(brand=brand).update(brand=primary)
                    brand.delete()

            processed.add(brand.id)
            if primary:
                processed.add(primary.id)

        # Ensure primary brands are active
        if not dry_run:
            for canonical_name in set(CANONICAL.values()):
                DeviceBrand.objects.filter(name=canonical_name).update(is_active=True)
