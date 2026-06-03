"""
Repair PaymentContract and Contract rows with zero commercial fields from source applications.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand

from applications.models import FinancingApplication
from core.commercial import pricing_from_application, sync_application_pricing_fields
from portal.models import PaymentContract
from portal.services import _apply_pricing_to_payment_contract, sync_merchant_contract_from_application


class Command(BaseCommand):
    help = "Backfill zero pricing on payment contracts from linked applications."

    def handle(self, *args, **options):
        repaired = 0
        skipped = 0
        for app in FinancingApplication.objects.filter(
            status__in=["approved", "contract_complete", "completed", "deposit_pending", "locking"],
        ).select_related("deal"):
            pricing = pricing_from_application(app)
            if not pricing:
                sync_application_pricing_fields(app, save=True)
                pricing = pricing_from_application(app)
            if not pricing:
                skipped += 1
                continue
            try:
                pc = app.payment_contract
            except PaymentContract.DoesNotExist:
                skipped += 1
                continue
            if pc.total_amount > Decimal("0") and pc.daily_price > Decimal("0") and pc.cash_price > Decimal("0"):
                continue
            _apply_pricing_to_payment_contract(pc, pricing)
            pc.save(
                update_fields=[
                    "cash_price", "total_amount", "deposit_required",
                    "daily_price", "thirty_day_price", "term_months", "device_model",
                ]
            )
            sync_merchant_contract_from_application(app)
            repaired += 1
        self.stdout.write(self.style.SUCCESS(f"Repaired {repaired} contract(s); skipped {skipped}."))
