"""Helpers for tests that need complete application pricing."""

from decimal import Decimal

from deals.models import DeviceBrand, DeviceDeal


def attach_complete_pricing(application, *, cash_price=None, term_months=12):
    """Attach a deal and calculated pricing so approval validation passes."""
    brand, _ = DeviceBrand.objects.get_or_create(name="TestBrand")
    cash = cash_price or Decimal("350000")
    deal, _ = DeviceDeal.objects.get_or_create(
        brand=brand,
        model_name="Test Phone",
        specs="4+64",
        defaults={
            "min_cash_price": cash,
            "max_cash_price": cash,
            "default_cash_price": cash,
            "cash_price": cash,
            "deposit_percent": Decimal("13"),
            "loan_multiplier": Decimal("2.5"),
            "term_months": 12,
        },
    )
    if not application.customer_phone:
        application.customer_phone = "991234567"
    if not application.customer_name:
        application.customer_name = "Test Customer"
    application.deal = deal
    application.apply_deal_selection(deal, cash, term_months=term_months)
    application.save(
        update_fields=[
            "customer_phone",
            "customer_name",
            "deal",
            "term_months",
            "selected_cash_price",
            "selected_deposit_percent",
            "selected_loan_multiplier",
            "calculated_total_loan",
            "calculated_deposit_amount",
            "calculated_monthly_payment",
            "calculated_daily_payment",
            "calculated_6_month_total",
            "calculated_6_month_monthly",
            "calculated_6_month_daily",
            "calculated_3_month_total",
            "calculated_3_month_monthly",
            "calculated_3_month_daily",
        ]
    )
    return application
