"""
Portal business logic services.

All pricing, payment allocation, and contract helper functions.
These are pure functions where possible — easy to test.

MWK rounding note:
  Prices are stored with 2dp for precision but displayed rounded to the
  nearest 1 MWK (use the mwk_round() helper for display/payment suggestions).
"""

import logging
from decimal import ROUND_HALF_UP, Decimal
from datetime import timedelta, date as date_type

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MWK rounding helper
# ---------------------------------------------------------------------------

def mwk_round(amount: Decimal) -> Decimal:
    """Round to nearest whole MWK (no decimals for display/payment suggestions)."""
    if amount is None:
        return Decimal("0")
    return amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Pricing helpers
# ---------------------------------------------------------------------------

def calculate_daily_price(total_amount: Decimal, term_months: int) -> Decimal:
    """Daily price = total / (term_months * 30), rounded to nearest 1 MWK."""
    if not total_amount or not term_months:
        return Decimal("0")
    daily = total_amount / Decimal(term_months * 30)
    return daily.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def calculate_thirty_day_price(total_amount: Decimal, term_months: int) -> Decimal:
    """30-day price = total / term_months, rounded to nearest 1 MWK."""
    if not total_amount or not term_months:
        return Decimal("0")
    monthly = total_amount / Decimal(term_months)
    return monthly.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def calculate_remaining_amount(contract) -> Decimal:
    """Remaining = total_amount - amount_paid (floor at 0)."""
    remaining = contract.total_amount - contract.amount_paid
    return max(remaining, Decimal("0"))


def calculate_lock_date(contract) -> "date | None":
    """
    Lock date = due_date + 3 grace days (configurable).
    Returns None if due_date is not set.
    """
    if not contract.due_date:
        return None
    return contract.due_date + timedelta(days=3)


def calculate_next_due_date(contract) -> "date":
    """
    Recalculate due date based on amount paid.
    Each daily_price paid extends active usage by 1 day from start_date.
    """
    if not contract.daily_price or contract.daily_price <= 0:
        # Fallback: extend by term if fully paid, else today
        return timezone.localdate()

    days_paid = int(contract.amount_paid / contract.daily_price)
    return contract.start_date + timedelta(days=days_paid)


def calculate_early_settlement_options(contract) -> list[dict]:
    """
    Return early settlement options for 9, 10, 11, 12 months.

    Discounts are derived from the contract's 9-month discount field,
    interpolating for 10 and 11 months. 12 months is always no discount.

    Each option shows:
      - term_months
      - total_cost
      - remaining_to_pay
      - daily_price
      - thirty_day_price
      - discount_percent
      - savings (vs 12-month option)
      - final_payoff_date
    """
    remaining = calculate_remaining_amount(contract)
    today = timezone.localdate()

    discount_9m = Decimal(str(contract.early_settlement_9m_discount))
    discount_10m = (discount_9m * Decimal("0.67")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    discount_11m = (discount_9m * Decimal("0.33")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

    settlement_configs = [
        (9, discount_9m),
        (10, discount_10m),
        (11, discount_11m),
        (12, Decimal("0")),
    ]

    full_term_total = contract.total_amount
    options = []

    for term_months, discount_percent in settlement_configs:
        if discount_percent > 0:
            discounted_remaining = remaining * (1 - discount_percent / 100)
            discounted_remaining = discounted_remaining.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            total_cost = contract.amount_paid + discounted_remaining
        else:
            discounted_remaining = remaining
            total_cost = full_term_total

        total_cost = total_cost.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        savings = (full_term_total - total_cost).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        final_payoff_date = today + timedelta(days=term_months * 30)

        options.append({
            "term_months": term_months,
            "discount_percent": discount_percent,
            "total_cost": total_cost,
            "remaining_to_pay": discounted_remaining,
            "daily_price": calculate_daily_price(discounted_remaining, max(term_months, 1)),
            "thirty_day_price": calculate_thirty_day_price(discounted_remaining, max(term_months, 1)),
            "savings": max(savings, Decimal("0")),
            "final_payoff_date": final_payoff_date,
        })

    return options


# ---------------------------------------------------------------------------
# Payment behaviour analytics
# ---------------------------------------------------------------------------

def calculate_payment_behaviour(contract) -> dict:
    """
    Calculate payment behaviour analytics from transaction history.
    Returns only real data — no estimations or AI.
    """
    paid_txns = list(
        contract.transactions.filter(status="paid")
        .order_by("paid_at")
        .values("amount", "paid_at", "provider")
    )

    empty = {
        "consistency": 0,
        "discipline": "No payments yet",
        "pattern": "No data",
        "average_payment": Decimal("0"),
        "largest_payment": Decimal("0"),
        "smallest_payment": Decimal("0"),
        "longest_streak": 0,
        "current_streak": 0,
        "days_late_this_month": 0,
        "payment_count": 0,
        "last_payment_date": None,
        "last_payment_amount": Decimal("0"),
        "preferred_day": None,
        "preferred_provider": None,
        "avg_interval_days": None,
    }

    if not paid_txns:
        return empty

    amounts = [Decimal(str(t["amount"])) for t in paid_txns]

    paid_dates = []
    for t in paid_txns:
        dt = t["paid_at"]
        if dt is None:
            continue
        paid_dates.append(dt.date() if hasattr(dt, "date") else dt)

    if not paid_dates:
        return empty

    avg_payment = sum(amounts) / len(amounts)
    largest = max(amounts)
    smallest = min(amounts)

    today = timezone.localdate()
    days_since_start = max((today - contract.start_date).days, 1)

    daily = contract.daily_price or Decimal("1")
    if daily > 0:
        days_paid = int(contract.amount_paid / daily)
    else:
        days_paid = 0

    consistency = min(100, round((days_paid / days_since_start) * 100))

    sorted_dates = sorted(set(paid_dates))
    intervals = [
        (sorted_dates[i] - sorted_dates[i - 1]).days
        for i in range(1, len(sorted_dates))
    ]

    if intervals:
        avg_interval = sum(intervals) / len(intervals)
        if avg_interval <= 2:
            pattern = "Daily"
        elif avg_interval <= 8:
            pattern = "Weekly"
        elif avg_interval <= 18:
            pattern = "Bi-weekly"
        else:
            pattern = "Monthly"
    else:
        avg_interval = None
        pattern = "First payment"

    longest_streak = days_paid
    current_streak = 0
    if contract.due_date and contract.due_date >= today:
        covered_to = contract.due_date
        current_streak = min((covered_to - today).days + days_paid, days_paid)
    else:
        current_streak = max(0, days_paid - (
            (today - contract.due_date).days if contract.due_date else 0
        ))

    days_late_this_month = 0
    first_of_month = today.replace(day=1)
    if contract.due_date and contract.due_date < first_of_month:
        days_late_this_month = min((first_of_month - contract.due_date).days, today.day)
    elif contract.due_date and contract.due_date < today:
        days_late_this_month = (today - contract.due_date).days

    if consistency >= 95:
        discipline = "Excellent"
    elif consistency >= 80:
        discipline = "Good"
    elif consistency >= 60:
        discipline = "Fair"
    else:
        discipline = "Needs Improvement"

    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    day_counts: dict = {}
    for d in paid_dates:
        name = day_names[d.weekday()]
        day_counts[name] = day_counts.get(name, 0) + 1
    preferred_day = max(day_counts, key=day_counts.get) if day_counts else None

    providers = [t["provider"] for t in paid_txns]
    provider_counts: dict = {}
    for p in providers:
        provider_counts[p] = provider_counts.get(p, 0) + 1
    preferred_provider = max(provider_counts, key=provider_counts.get) if provider_counts else None

    return {
        "consistency": consistency,
        "discipline": discipline,
        "pattern": pattern,
        "average_payment": avg_payment.quantize(Decimal("1"), rounding=ROUND_HALF_UP),
        "largest_payment": largest,
        "smallest_payment": smallest,
        "longest_streak": longest_streak,
        "current_streak": current_streak,
        "days_late_this_month": days_late_this_month,
        "payment_count": len(paid_txns),
        "last_payment_date": sorted_dates[-1] if sorted_dates else None,
        "last_payment_amount": amounts[-1] if amounts else Decimal("0"),
        "preferred_day": preferred_day,
        "preferred_provider": preferred_provider,
        "avg_interval_days": round(avg_interval) if avg_interval is not None else None,
    }


# ---------------------------------------------------------------------------
# Customer financial health score
# ---------------------------------------------------------------------------

def calculate_health_score(contract, behaviour: dict) -> dict:
    """
    Calculate a 0–100 financial health score using only real transaction data.
    No AI, no predictions — purely auditable arithmetic.
    """
    consistency = behaviour.get("consistency", 0)
    days_late = behaviour.get("days_late_this_month", 0)
    pattern = behaviour.get("pattern", "")
    payment_count = behaviour.get("payment_count", 0)
    progress = contract.progress_percent

    score_consistency = int(consistency * 0.40)

    if days_late == 0:
        score_late = 20
    elif days_late <= 3:
        score_late = 15
    elif days_late <= 7:
        score_late = 8
    else:
        score_late = 0

    if pattern in ("Daily", "Weekly"):
        score_freq = 20
    elif pattern == "Bi-weekly":
        score_freq = 14
    elif pattern == "Monthly":
        score_freq = 8
    else:
        score_freq = 5

    score_progress = int(progress * 0.20)

    total = min(100, max(0, score_consistency + score_late + score_freq + score_progress))

    if total >= 85:
        grade = "Excellent"
    elif total >= 70:
        grade = "Good"
    elif total >= 50:
        grade = "Fair"
    else:
        grade = "Needs Attention"

    reasons = []
    if days_late == 0 and payment_count > 0:
        reasons.append({"positive": True, "text": "No overdue days in current period"})
    elif days_late > 0:
        reasons.append({"positive": False, "text": f"{days_late} overdue day(s) this period"})

    if pattern in ("Daily", "Weekly"):
        reasons.append({"positive": True, "text": f"Consistent {pattern.lower()} payment rhythm"})
    elif pattern == "Monthly":
        reasons.append({"positive": False, "text": "Monthly payments — more frequent is better"})

    if consistency >= 90:
        reasons.append({"positive": True, "text": f"{consistency}% of contract days covered"})
    elif consistency < 60:
        reasons.append({"positive": False, "text": f"Only {consistency}% of contract days covered"})

    if payment_count >= 20:
        reasons.append({"positive": True, "text": f"{payment_count} successful payments made"})

    if progress >= 50:
        reasons.append({"positive": True, "text": f"{progress}% of contract completed"})

    return {
        "score": total,
        "grade": grade,
        "reasons": reasons[:5],
    }


# ---------------------------------------------------------------------------
# Customer insights
# ---------------------------------------------------------------------------

def calculate_customer_insights(contract, behaviour: dict) -> list[str]:
    """
    Generate plain-English insights using only real contract and payment data.
    """
    insights = []
    today = timezone.localdate()

    progress = contract.progress_percent
    insights.append(f"You have paid {progress}% of your contract.")

    days_since_start = (today - contract.start_date).days
    if days_since_start > 0:
        insights.append(f"You have been on this contract for {days_since_start} days.")

    avg = behaviour.get("average_payment")
    if avg and avg > 0:
        insights.append(f"Your average payment is MWK {int(avg):,}.")

    largest = behaviour.get("largest_payment")
    if largest and largest > 0:
        insights.append(f"Your largest payment was MWK {int(largest):,}.")

    preferred_day = behaviour.get("preferred_day")
    if preferred_day:
        insights.append(f"You usually pay on {preferred_day}s.")

    daily = contract.daily_price
    if daily and daily > 0 and days_since_start > 0:
        expected_paid = daily * days_since_start
        actual_paid = contract.amount_paid
        diff_days = int((actual_paid - expected_paid) / daily)
        if diff_days > 0:
            insights.append(f"You are ahead of schedule by {diff_days} day(s).")
        elif diff_days < -1:
            insights.append(f"You are behind schedule by {abs(diff_days)} day(s).")
        else:
            insights.append("You are on schedule.")

    interval = behaviour.get("avg_interval_days")
    if interval:
        insights.append(f"You typically pay every {interval} day(s).")

    return insights


# ---------------------------------------------------------------------------
# Payment type helper
# ---------------------------------------------------------------------------

def calculate_deposit_payment_type(contract, payment_type_hint: str) -> str:
    """
    Determine the correct payment type for a new transaction.

    Rules:
    - If the caller explicitly requests 'deposit' AND the deposit is not yet complete,
      return TYPE_DEPOSIT.
    - If the contract has an outstanding deposit (deposit_required > deposit_paid)
      and no explicit type is specified, default to deposit to prioritise it.
    - Otherwise return TYPE_REPAYMENT.
    """
    from portal.models import PaymentTransaction
    if payment_type_hint == PaymentTransaction.TYPE_DEPOSIT:
        if not contract.deposit_complete:
            return PaymentTransaction.TYPE_DEPOSIT
    if payment_type_hint not in (
        PaymentTransaction.TYPE_DEPOSIT,
        PaymentTransaction.TYPE_REPAYMENT,
        PaymentTransaction.TYPE_PENALTY,
        PaymentTransaction.TYPE_ADJUSTMENT,
        PaymentTransaction.TYPE_REFUND,
    ):
        # Auto-detect: if deposit is outstanding, default to deposit
        if not contract.deposit_complete and contract.deposit_required > Decimal("0"):
            return PaymentTransaction.TYPE_DEPOSIT
    return payment_type_hint or PaymentTransaction.TYPE_REPAYMENT


# ---------------------------------------------------------------------------
# Payment allocation
# ---------------------------------------------------------------------------

def apply_payment_to_contract(
    contract,
    amount: Decimal,
    db_save: bool = True,
    payment_type: str = "repayment",
) -> dict:
    """
    Apply a payment to a contract.

    Payment type handling:
    - TYPE_DEPOSIT: credited to deposit_paid, not amount_paid.
      Once deposit_required is met, contract can become active.
    - TYPE_REPAYMENT (default): credited to amount_paid, extends usage days.

    Allocation order (repayment):
    1. First clears any arrears (overdue amount).
    2. Remaining payment extends active usage days.

    Guards:
    - Zero or negative amounts return without modifying the contract.
    - Payments are capped at the remaining balance / deposit remaining.
    - Completed contracts are not modified.
    - Progress is always capped at 100%.

    Returns a dict with allocation details.
    """
    from portal.models import PaymentTransaction

    if amount is None or amount <= Decimal("0"):
        return {
            "applied": Decimal("0"),
            "arrears_cleared": Decimal("0"),
            "days_extended": 0,
            "payment_type": payment_type,
            "error": "Payment amount must be greater than zero.",
        }

    if contract.status == "completed":
        return {
            "applied": Decimal("0"),
            "arrears_cleared": Decimal("0"),
            "days_extended": 0,
            "payment_type": payment_type,
            "error": "Contract is already completed.",
        }

    # ── Deposit payment ──────────────────────────────────────────────────────
    if payment_type == PaymentTransaction.TYPE_DEPOSIT:
        deposit_remaining = contract.deposit_remaining
        if deposit_remaining <= Decimal("0"):
            return {
                "applied": Decimal("0"),
                "arrears_cleared": Decimal("0"),
                "days_extended": 0,
                "payment_type": payment_type,
                "error": "Deposit is already fully paid.",
            }
        applied = min(amount, deposit_remaining)
        contract.deposit_paid = (contract.deposit_paid or Decimal("0")) + applied
        if db_save:
            contract.save(update_fields=["deposit_paid"])
        return {
            "applied": applied,
            "arrears_cleared": Decimal("0"),
            "days_extended": 0,
            "payment_type": payment_type,
            "deposit_remaining": contract.deposit_remaining,
            "deposit_complete": contract.deposit_complete,
            "status": contract.status,
        }

    # ── Repayment (and other types) ──────────────────────────────────────────
    remaining_before = calculate_remaining_amount(contract)

    if remaining_before <= Decimal("0"):
        contract.status = "completed"
        if db_save:
            contract.save(update_fields=["status"])
        return {
            "applied": Decimal("0"),
            "arrears_cleared": Decimal("0"),
            "days_extended": 0,
            "payment_type": payment_type,
            "error": "Contract is already fully paid.",
        }

    # Cap payment at remaining balance — no overpayment
    applied = min(amount, remaining_before)

    # Determine arrears (amount that was already overdue)
    arrears_cleared = Decimal("0")
    if contract.due_date and contract.due_date < timezone.localdate():
        overdue_days = (timezone.localdate() - contract.due_date).days
        if contract.daily_price and contract.daily_price > 0:
            arrears_amount = Decimal(overdue_days) * contract.daily_price
            arrears_cleared = min(applied, arrears_amount)

    # Calculate days extended by this payment
    days_extended = 0
    if contract.daily_price and contract.daily_price > 0:
        days_extended = int(applied / contract.daily_price)

    # Update contract financials
    contract.amount_paid += applied

    # Safety: never exceed total_amount
    if contract.amount_paid > contract.total_amount:
        contract.amount_paid = contract.total_amount

    # Recalculate due date and lock date
    new_due = calculate_next_due_date(contract)
    contract.due_date = new_due
    contract.lock_date = new_due + timedelta(days=3)

    # Update status
    if contract.amount_paid >= contract.total_amount:
        contract.status = "completed"
    elif new_due < timezone.localdate():
        contract.status = "overdue"
    else:
        contract.status = "active"

    # Recalculate pricing on remaining balance for next period
    remaining_after = calculate_remaining_amount(contract)
    if remaining_after > Decimal("0") and contract.status != "completed":
        contract.daily_price = calculate_daily_price(remaining_after, max(contract.term_months, 1))
        contract.thirty_day_price = calculate_thirty_day_price(remaining_after, max(contract.term_months, 1))
    elif contract.status == "completed":
        contract.daily_price = Decimal("0")
        contract.thirty_day_price = Decimal("0")

    if db_save:
        contract.save(update_fields=[
            "amount_paid", "due_date", "lock_date", "status",
            "daily_price", "thirty_day_price",
        ])
        # Refresh stored analytics after every successful payment
        try:
            refresh_contract_analytics(contract, db_save=True)
        except Exception:
            logger.exception("Failed to refresh analytics for contract %s", contract.pk)

    return {
        "applied": applied,
        "arrears_cleared": arrears_cleared,
        "days_extended": days_extended,
        "payment_type": payment_type,
        "new_due_date": contract.due_date,
        "new_lock_date": contract.lock_date,
        "remaining": remaining_after,
        "status": contract.status,
    }


# ---------------------------------------------------------------------------
# Analytics snapshot persistence (Section 11)
# ---------------------------------------------------------------------------

def refresh_contract_analytics(contract, db_save: bool = True) -> None:
    """
    Re-compute and persist behaviour analytics onto the PaymentContract row.

    Called automatically after every payment so HQ can query without
    re-running per-contract calculations at report time.
    """
    behaviour = calculate_payment_behaviour(contract)
    today = timezone.localdate()

    days_late = 0
    if contract.due_date and contract.due_date < today:
        days_late = (today - contract.due_date).days

    contract.analytics_payment_count = behaviour["payment_count"]
    contract.analytics_average_payment = behaviour["average_payment"] or Decimal("0")
    contract.analytics_largest_payment = behaviour["largest_payment"] or Decimal("0")
    contract.analytics_smallest_payment = behaviour["smallest_payment"] or Decimal("0")
    contract.analytics_consistency = behaviour["consistency"]
    contract.analytics_discipline = behaviour["discipline"]
    contract.analytics_avg_interval_days = behaviour["avg_interval_days"]
    contract.analytics_days_late = days_late
    contract.analytics_preferred_provider = behaviour["preferred_provider"] or ""
    contract.analytics_preferred_day = behaviour["preferred_day"] or ""
    contract.analytics_updated_at = timezone.now()

    if db_save:
        contract.save(update_fields=[
            "analytics_payment_count",
            "analytics_average_payment",
            "analytics_largest_payment",
            "analytics_smallest_payment",
            "analytics_consistency",
            "analytics_discipline",
            "analytics_avg_interval_days",
            "analytics_days_late",
            "analytics_preferred_provider",
            "analytics_preferred_day",
            "analytics_updated_at",
        ])


# ---------------------------------------------------------------------------
# Contract search
# ---------------------------------------------------------------------------

def search_payment_contract(query: str):
    """
    Look up a PaymentContract by:
      - contract_number (TS-MW-XXXXXXXX)
      - payg_number (EXXXXXXX — PayG reference, not legal contract number)
      - legal contract number (contracts.Contract — A + 7 chars)
      - customer_national_id
      - customer_phone (last 9 digits matched)

    Returns the first matching PaymentContract or None.
    """
    from portal.models import PaymentContract

    q = query.strip()
    if not q:
        return None

    # Customer-facing legal contract number (A + 7 chars) → linked payment contract
    if len(q) == 8 and q[0].upper() == "A":
        from contracts.models import Contract

        legal = (
            Contract.objects.filter(contract_number__iexact=q)
            .select_related("application")
            .first()
        )
        if legal and legal.application_id:
            try:
                return legal.application.payment_contract
            except PaymentContract.DoesNotExist:
                pass

    # Direct contract number / PayG / national ID lookup
    contract = (
        PaymentContract.objects.filter(contract_number__iexact=q).first()
        or PaymentContract.objects.filter(payg_number__iexact=q).first()
        or PaymentContract.objects.filter(customer_national_id__iexact=q).first()
    )
    if contract:
        return contract

    # IMEI lookup (exact match — IMEIs are unique per device)
    if q.isdigit() and len(q) == 15:
        contract = PaymentContract.objects.filter(imei_number=q).first()
        if contract:
            return contract

    # Phone lookup — strip country code and match last 9 digits
    digits = "".join(c for c in q if c.isdigit())
    if len(digits) >= 9:
        last9 = digits[-9:]
        contract = PaymentContract.objects.filter(
            customer_phone__endswith=last9
        ).first()
    return contract


# ---------------------------------------------------------------------------
# Contract creation from approved FinancingApplication
# ---------------------------------------------------------------------------

@transaction.atomic
def create_contract_from_application(application, approved_by=None) -> "PaymentContract":
    """
    Idempotently create a PaymentContract from an approved FinancingApplication.
    Also creates a MerchantContractPayout.
    Returns the (possibly existing) PaymentContract.
    """
    from portal.models import PaymentContract

    # Idempotent: return existing contract if already created
    try:
        return application.payment_contract
    except PaymentContract.DoesNotExist:
        pass

    total_amount = Decimal(application.calculated_total_loan or 0)
    deposit_required = Decimal(
        getattr(application, "calculated_deposit_amount", None)
        or getattr(application, "deposit_amount", None)
        or 0
    )
    term = getattr(application, "term_months", None) or 12
    daily = calculate_daily_price(total_amount, term)
    monthly = calculate_thirty_day_price(total_amount, term)
    imei = getattr(application, "imei_number", "") or ""

    # Build provider metadata for lock integration
    payg_placeholder = ""  # will be set by save() auto-generation
    provider_meta = {
        "imei": imei,
        "customer_phone": application.customer_phone or "",
        "device_model": str(application.deal) if application.deal else "",
    }

    contract = PaymentContract.objects.create(
        source_application=application,
        customer_name=application.customer_name or "",
        customer_phone=application.customer_phone or "",
        customer_national_id=application.national_id or "",
        device_model=str(application.deal) if application.deal else "",
        imei_number=imei,
        total_amount=total_amount,
        deposit_required=deposit_required,
        deposit_paid=Decimal("0"),
        daily_price=daily,
        thirty_day_price=monthly,
        term_months=term,
        status=PaymentContract.STATUS_ACTIVE,
        provider_metadata=provider_meta,
    )

    # Now that payg_number is assigned, update provider_metadata with it
    provider_meta["payg_number"] = contract.payg_number
    provider_meta["payment_url"] = f"/pay/payg/{contract.payg_number}/"
    contract.provider_metadata = provider_meta
    contract.save(update_fields=["provider_metadata"])
    logger.info(
        "Created PaymentContract %s (PayG: %s) from application %s",
        contract.contract_number,
        contract.payg_number,
        application.application_number,
    )

    # Create merchant payout (idempotent)
    try:
        from commissions.services import create_merchant_payout_for_contract
        merchant_user = application.created_by
        create_merchant_payout_for_contract(
            contract,
            merchant_user=merchant_user,
            created_by=approved_by,
        )
    except Exception as exc:
        logger.warning("Could not create MerchantContractPayout for contract %s: %s", contract.pk, exc)

    return contract
