"""
Shared commercial pricing definitions for TengaSale.

cash_price — merchant settlement basis (phone selling price).
contract_total — customer total over the selected term (financed total / loan total).
deposit_required — upfront deposit before repayments.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

ALLOWED_CONTRACT_TERMS = (3, 6, 12)
EARLY_PAYOFF_DISCOUNTS = {
    3: Decimal("25"),
    6: Decimal("15"),
    12: Decimal("0"),
}
DEFAULT_LOAN_MULTIPLIER = Decimal("2.5")


def _money(value) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0")


def _deal_cash_price(deal) -> Decimal:
    """Return the stored deal cash price without inventing a value."""
    if deal is None:
        return Decimal("0")
    for attr in ("default_cash_price", "cash_price", "min_cash_price", "max_cash_price"):
        amount = _money(getattr(deal, attr, None))
        if amount > 0:
            return amount
    return Decimal("0")


def application_cash_price(application) -> Decimal:
    """Authoritative cash price for an application, preserving deal data."""
    cash = _money(getattr(application, "selected_cash_price", None))
    if cash > 0:
        return cash
    return _deal_cash_price(getattr(application, "deal", None))


def normalize_term_months(term, *, for_new_contract: bool = False) -> int:
    """Return a positive term; enforce 3/6/12 for new selections."""
    try:
        t = int(term)
    except (TypeError, ValueError):
        t = 12
    if t <= 0:
        t = 12
    if for_new_contract and t not in ALLOWED_CONTRACT_TERMS:
        return 12
    return t


def allowed_early_payoff_terms(contract_term: int) -> list[int]:
    """Early payoff plans available for the selected contract term."""
    term = normalize_term_months(contract_term)
    if term >= 12:
        return [3, 6, 12]
    if term >= 6:
        return [3, 6]
    return []


def contract_total_from_cash(cash_price, multiplier=None) -> Decimal:
    cash = _money(cash_price)
    if cash <= 0:
        return Decimal("0")
    mult = _money(multiplier or DEFAULT_LOAN_MULTIPLIER)
    if mult <= 0:
        mult = DEFAULT_LOAN_MULTIPLIER
    return (cash * mult).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def deposit_from_total(total_loan, deposit_percent) -> Decimal:
    total = _money(total_loan)
    if total <= 0:
        return Decimal("0")
    pct = _money(deposit_percent)
    return (total * pct / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def daily_repayment_from_total(contract_total, term_months) -> Decimal:
    total = _money(contract_total)
    term = normalize_term_months(term_months)
    if total <= 0 or term <= 0:
        return Decimal("0")
    daily = total / Decimal(term * 30)
    return daily.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def monthly_repayment_from_total(contract_total, term_months) -> Decimal:
    total = _money(contract_total)
    term = normalize_term_months(term_months)
    if total <= 0 or term <= 0:
        return Decimal("0")
    monthly = total / Decimal(term)
    return monthly.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def pricing_from_application(application) -> dict[str, Any] | None:
    """Build commercial snapshot from a FinancingApplication."""
    if application is None:
        return None

    cash = application_cash_price(application)
    if cash <= 0:
        return None

    total = _money(getattr(application, "calculated_total_loan", 0))
    if total <= 0:
        mult = _money(getattr(application, "selected_loan_multiplier", 0) or DEFAULT_LOAN_MULTIPLIER)
        total = contract_total_from_cash(cash, mult)

    deposit = _money(getattr(application, "calculated_deposit_amount", 0))
    if deposit <= 0 and application.deal_id:
        deal = application.deal
        deposit = deposit_from_total(total, getattr(application, "selected_deposit_percent", None) or deal.deposit_percent)

    term = normalize_term_months(getattr(application, "term_months", None) or 12)

    daily = _money(getattr(application, "calculated_daily_payment", 0))
    monthly = _money(getattr(application, "calculated_monthly_payment", 0))
    if daily <= 0:
        daily = daily_repayment_from_total(total, term)
    if monthly <= 0:
        monthly = monthly_repayment_from_total(total, term)

    if total <= 0 or daily <= 0 or monthly <= 0:
        return None

    return {
        "cash_price": cash,
        "deposit_required": deposit,
        "contract_total": total,
        "daily_repayment": daily,
        "monthly_repayment": monthly,
        "term_months": term,
        "device_model": str(application.deal) if application.deal_id else "",
    }


def validate_application_pricing(application) -> tuple[bool, list[str]]:
    """Return (ok, missing_field_labels) for underwriter approval."""
    missing = []
    if not application.deal_id:
        missing.append("device/deal")
    if not (application.customer_name or "").strip():
        missing.append("customer name")
    if not (application.customer_phone or "").strip():
        missing.append("phone number")

    cash = application_cash_price(application)
    if cash <= 0:
        missing.append("cash price")

    pricing = pricing_from_application(application)
    if pricing is None:
        if _money(application.calculated_total_loan) <= 0:
            missing.append("contract total")
        if _money(application.calculated_daily_payment) <= 0:
            missing.append("daily repayment")
    else:
        if pricing["deposit_required"] <= 0 and application.deal_id:
            missing.append("deposit required")
        term = getattr(application, "term_months", None)
        if not term or int(term) not in ALLOWED_CONTRACT_TERMS:
            missing.append("term (3, 6, or 12 months)")

    return len(missing) == 0, missing


def has_valid_pricing(amount) -> bool:
    """True when a money field is present and > 0."""
    return _money(amount) > 0


def sync_application_pricing_fields(application, *, save: bool = False) -> bool:
    """Recompute stored pricing on application from deal + cash + term."""
    if not application.deal_id:
        return False

    deal = application.deal
    cash = application_cash_price(application)
    if cash <= 0:
        return False
    term = normalize_term_months(
        getattr(application, "term_months", None) or deal.term_months,
        for_new_contract=True,
    )
    application.term_months = term
    application.apply_deal_selection(deal, cash, term_months=term)
    if save:
        application.save(
            update_fields=[
                "selected_cash_price",
                "term_months",
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
    return True
