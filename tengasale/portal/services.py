"""
Portal business logic services.

All pricing, payment allocation, and contract helper functions.
These are pure functions where possible — easy to test.

MWK rounding note:
  Prices are stored with 2dp for precision but displayed rounded to the
  nearest 1 MWK (use the mwk_round() helper for display/payment suggestions).
"""

import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from datetime import timedelta, date as date_type

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContractResolution:
    status: str
    contract: object | None = None
    reason: str = ""
    matches: int = 0

    @property
    def found(self) -> bool:
        return self.contract is not None

    @property
    def payable(self) -> bool:
        return self.status == "payable"


@dataclass(frozen=True)
class DepositSummary:
    required_amount: Decimal
    confirmed_paid_amount: Decimal
    pending_amount: Decimal
    remaining_amount: Decimal
    percentage_paid: Decimal
    status: str
    is_fully_paid: bool
    can_activate_contract: bool
    activated_at: object | None = None


def get_deposit_summary(contract) -> DepositSummary:
    """Return deposit state from confirmed ledger transactions, never snapshots."""
    from django.db.models import Sum
    from portal.models import PaymentTransaction

    required = Decimal(str(getattr(contract, "deposit_required", 0) or 0))
    deposits = contract.transactions.filter(payment_type=PaymentTransaction.TYPE_DEPOSIT)
    confirmed = deposits.filter(
        status=PaymentTransaction.STATUS_PAID,
        paid_at__isnull=False,
    )
    confirmed_amount = confirmed.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    confirmed_amount = min(max(confirmed_amount, Decimal("0")), required) if required > 0 else Decimal("0")
    pending_amount = deposits.filter(
        status__in=(
            PaymentTransaction.STATUS_PENDING,
            PaymentTransaction.STATUS_TENGA_PROCESSING,
            PaymentTransaction.STATUS_PROCESSING,
        )
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    remaining = max(required - confirmed_amount, Decimal("0"))
    percentage = (
        (confirmed_amount / required * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if required > 0 else Decimal("100")
    )
    is_fully_paid = required <= 0 or confirmed_amount >= required
    if required <= 0:
        status = "not_required"
    elif is_fully_paid:
        status = "paid"
    elif confirmed_amount > 0:
        status = "partially_paid"
    elif pending_amount > 0:
        status = "pending_confirmation"
    elif deposits.filter(status=PaymentTransaction.STATUS_REVERSED).exists():
        status = "refunded"
    elif deposits.filter(status=PaymentTransaction.STATUS_FAILED).exists():
        status = "failed"
    else:
        status = "unpaid"
    latest_confirmation = confirmed.order_by("-paid_at").values_list("paid_at", flat=True).first()
    activated_at = getattr(contract, "deposit_paid_at", None) if is_fully_paid else None
    return DepositSummary(
        required_amount=required,
        confirmed_paid_amount=confirmed_amount,
        pending_amount=pending_amount,
        remaining_amount=remaining,
        percentage_paid=min(percentage, Decimal("100")),
        status=status,
        is_fully_paid=is_fully_paid,
        can_activate_contract=required > 0 and is_fully_paid,
        activated_at=activated_at or latest_confirmation if is_fully_paid else None,
    )


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


@dataclass(frozen=True)
class LockWarning:
    date: object | None
    amount: Decimal
    days: int
    hours: int


@dataclass(frozen=True)
class LockState:
    status: str  # "safe" | "warning" | "overdue" | "completed" | "deposit_pending"
    warning: LockWarning | None
    remaining: Decimal
    fully_paid: bool


def compute_lock_state(contract) -> LockState:
    """
    Determine the customer-facing device lock status for a contract.

    This is a pure extraction of the lock-calculation block that used to live
    inline in portal_contract — behaviour is intentionally unchanged so both
    the contract page and the dedicated device-status page render identical
    lock state from one authoritative source. Do not alter the thresholds or
    precedence below without updating both call sites' tests.
    """
    from datetime import datetime, time as time_type

    now = timezone.now()
    remaining = calculate_remaining_amount(contract)
    fully_paid = remaining <= Decimal("0")

    warning = None
    status = "safe"
    if fully_paid or contract.status == "completed":
        status = "completed"
    elif contract.access_expires_at or contract.lock_date:
        lock_at = contract.access_expires_at
        if lock_at is None and contract.lock_date:
            lock_at = timezone.make_aware(
                datetime.combine(contract.lock_date, time_type.min),
                timezone.get_current_timezone(),
            )
        seconds_until_lock = (lock_at - now).total_seconds() if lock_at else 0
        hours_until_lock = int(seconds_until_lock // 3600)
        days_until_lock = int(seconds_until_lock // 86400)
        if seconds_until_lock < 0:
            status = "overdue"
            warning = LockWarning(
                date=lock_at,
                amount=remaining,
                days=days_until_lock,
                hours=hours_until_lock,
            )
        elif seconds_until_lock <= 4 * 86400:
            status = "warning"
            warning = LockWarning(
                date=lock_at,
                amount=(
                    contract.daily_price * max(days_until_lock, 0)
                    if contract.daily_price else remaining
                ),
                days=days_until_lock,
                hours=hours_until_lock,
            )
        else:
            status = "safe"

    deposit_summary = get_deposit_summary(contract)
    deposit_required = deposit_summary.required_amount
    deposit_pending = deposit_required > 0 and not deposit_summary.is_fully_paid
    if deposit_pending:
        status = "deposit_pending"
        warning = None

    return LockState(status=status, warning=warning, remaining=remaining, fully_paid=fully_paid)


_LOCK_EVENT_LABELS = {
    "enroll_requested": ("Device enrollment requested", "plain"),
    "enroll_success": ("Device enrolled", "done"),
    "enroll_failed": ("Device enrollment failed", "failed"),
    "lock_requested": ("Lock requested", "plain"),
    "lock_success": ("Device locked", "failed"),
    "lock_failed": ("Lock request failed", "failed"),
    "unlock_requested": ("Unlock scheduled", "current"),
    "unlock_success": ("Device unlocked", "done"),
    "unlock_failed": ("Unlock request failed", "failed"),
    "release_requested": ("Device release requested", "plain"),
    "release_success": ("Device released — ownership transferred", "done"),
    "release_failed": ("Release request failed", "failed"),
    "status_sync": ("Device status synced", "plain"),
    "webhook_received": ("Provider update received", "plain"),
    "manual_override": ("Manual override applied", "plain"),
}


def build_device_timeline(contract, limit: int = 12) -> list[dict]:
    """
    Merge payment activity and (best-effort) device-lock events into one
    reverse-chronological timeline for the device-status page. Read-only —
    never writes, never invents a status that isn't already on a real record.
    """
    from portal.models import PaymentTransaction

    events = []

    if contract.start_date:
        from datetime import datetime as datetime_type, time as time_type

        started_at = timezone.make_aware(
            datetime_type.combine(contract.start_date, time_type.min),
            timezone.get_current_timezone(),
        )
        events.append({
            "title": "Contract started",
            "desc": contract.device_model or "",
            "time": started_at,
            "state": "done",
            "_sort": started_at,
        })

    paid_types = {
        PaymentTransaction.STATUS_PAID: ("done", "confirmed"),
        PaymentTransaction.STATUS_FAILED: ("failed", "failed"),
        PaymentTransaction.STATUS_PROCESSING: ("current", "processing"),
        PaymentTransaction.STATUS_PENDING: ("current", "pending"),
    }
    for tx in contract.transactions.order_by("-created_at")[:limit]:
        state, _ = paid_types.get(tx.status, ("plain", tx.status))
        kind = "Deposit" if tx.payment_type == PaymentTransaction.TYPE_DEPOSIT else "Repayment"
        if tx.status == PaymentTransaction.STATUS_PAID:
            title = f"{kind} confirmed — MWK {tx.amount:,.0f}"
            when = tx.paid_at or tx.created_at
        elif tx.status == PaymentTransaction.STATUS_FAILED:
            title = f"{kind} attempt failed — MWK {tx.amount:,.0f}"
            when = tx.updated_at or tx.created_at
        else:
            title = f"{kind} attempt in progress — MWK {tx.amount:,.0f}"
            when = tx.created_at
        events.append({
            "title": title,
            "desc": f"{tx.get_provider_display()} · {tx.internal_reference}" if tx.internal_reference else tx.get_provider_display(),
            "time": when,
            "state": state,
            "_sort": when,
        })

    try:
        app = contract.source_application
        profile = getattr(app, "device_lock_profile", None) if app else None
        if profile:
            for ev in profile.events.order_by("-created_at")[:limit]:
                label, state = _LOCK_EVENT_LABELS.get(ev.event_type, (ev.get_event_type_display(), "plain"))
                events.append({
                    "title": label,
                    "desc": ev.notes or "",
                    "time": ev.created_at,
                    "state": state,
                    "_sort": ev.created_at,
                })
    except Exception:
        logger.debug("Device lock events unavailable for contract %s", contract.contract_number, exc_info=True)

    def _sort_key(event):
        value = event.get("_sort") or event.get("time")
        return value or timezone.now()

    events.sort(key=_sort_key, reverse=True)
    for event in events:
        event.pop("_sort", None)
    return events[:limit]


def get_lock_provider_label(contract) -> str:
    """Human-readable device-lock provider name, shared by contract and device-status pages."""
    if not contract.device_lock_provider:
        return "Device setup pending"
    provider_labels = {
        "mock": "Sandbox",
        "knox": "Knox",
        "nuovopay": "NuovoPay",
        "upya": "Upya",
    }
    return provider_labels.get(
        contract.device_lock_provider, contract.device_lock_provider.replace("_", " ").title()
    )


def get_device_info(contract) -> dict:
    """
    Assemble device/application display info for a contract. Extracted from
    portal_contract's inline block so the contract page and the dedicated
    device-status page read identical device details from one place.
    """
    app = contract.source_application
    device_info = {
        "model": contract.device_model or "—",
        "imei": contract.imei_number or "",
        "brand": "",
        "merchant_name": "",
        "merchant_branch": "",
        "underwriter_name": "",
    }
    if app:
        if not device_info["imei"]:
            device_info["imei"] = getattr(app, "imei_number", "") or ""
        if app.deal_id:
            try:
                device_info["brand"] = app.deal.brand.name
                device_info["model"] = str(app.deal)
            except Exception:
                pass
        if not device_info["brand"]:
            device_info["brand"] = getattr(app, "imei_api_brand", "") or ""
        if not device_info["model"] and contract.device_model:
            device_info["model"] = contract.device_model
        if app.created_by:
            try:
                profile = app.created_by.profile
                if hasattr(profile, "merchant") and profile.merchant:
                    device_info["merchant_name"] = str(profile.merchant)
            except Exception:
                pass
        try:
            device_info["underwriter_name"] = app.underwriter_name or ""
        except Exception:
            pass
    return device_info


def calculate_lock_date(contract) -> "date | None":
    """
    Lock date = due_date + 3 grace days (configurable).
    Returns None if due_date is not set.
    """
    if not contract.due_date:
        return None
    return contract.due_date + timedelta(days=3)


def get_deposit_access_days(contract) -> int:
    """
    Return the number of days unlocked by the deposit for this contract.
    Reads from contract.deposit_access_days if available, otherwise falls back
    to the DEFAULT_DEPOSIT_ACCESS_DAYS setting (default 7).
    """
    from django.conf import settings as django_settings
    if hasattr(contract, "deposit_access_days") and contract.deposit_access_days:
        return int(contract.deposit_access_days)
    return int(getattr(django_settings, "DEFAULT_DEPOSIT_ACCESS_DAYS", 7))


def days_covered_by_payment(amount: Decimal, daily_price: Decimal) -> int:
    """
    Calculate the number of full days covered by a payment amount.
    Returns 0 if daily_price is zero or invalid.
    """
    if not daily_price or daily_price <= Decimal("0"):
        return 0
    return int(amount / daily_price)


def amount_required_for_days(days: int, daily_price: Decimal) -> Decimal:
    """Return the exact amount required to cover a given number of days."""
    if not daily_price or daily_price <= Decimal("0") or days <= 0:
        return Decimal("0")
    return (Decimal(days) * daily_price).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def calculate_next_due_date(contract) -> "date":
    """
    Recalculate due date based on amount paid.

    Formula:
        due_date = start_date + deposit_access_days (if deposit complete)
                   + floor(amount_paid / daily_price)

    The deposit gives the customer their initial access window (7 days by default).
    Each subsequent repayment extends access proportionally.
    """
    if not contract.daily_price or contract.daily_price <= Decimal("0"):
        return timezone.localdate()

    deposit_offset = get_deposit_access_days(contract) if contract.deposit_complete else 0
    days_paid = int(contract.amount_paid / contract.daily_price)
    return contract.start_date + timedelta(days=deposit_offset + days_paid)


def calculate_early_settlement_options(contract) -> list[dict]:
    """
    Early payoff plans for 3, 6, and/or 12 months based on the contract term.

    Only terms allowed by core.commercial.allowed_early_payoff_terms are shown.
    No option is returned when pricing is incomplete or remaining balance is zero.
    """
    from core.commercial import allowed_early_payoff_terms, EARLY_PAYOFF_DISCOUNTS, has_valid_pricing

    if not has_valid_pricing(contract.total_amount):
        return []

    remaining = calculate_remaining_amount(contract)
    if remaining <= Decimal("0"):
        return []

    today = timezone.localdate()
    contract_term = int(contract.term_months or 12)
    payoff_terms = allowed_early_payoff_terms(contract_term)
    if not payoff_terms:
        return []

    full_term_total = contract.total_amount
    options = []

    discount_map = {
        3: Decimal(str(contract.early_settlement_3m_discount or EARLY_PAYOFF_DISCOUNTS[3])),
        6: Decimal(str(contract.early_settlement_6m_discount or EARLY_PAYOFF_DISCOUNTS[6])),
        12: Decimal("0"),
    }

    for term_months in payoff_terms:
        discount_percent = discount_map.get(term_months, Decimal("0"))
        if discount_percent > 0:
            discounted_remaining = remaining * (1 - discount_percent / 100)
            discounted_remaining = discounted_remaining.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            total_cost = contract.amount_paid + discounted_remaining
        else:
            discounted_remaining = remaining
            total_cost = full_term_total

        total_cost = total_cost.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        if total_cost <= Decimal("0"):
            continue

        savings = (full_term_total - total_cost).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        daily = calculate_daily_price(discounted_remaining, max(term_months, 1))
        monthly = calculate_thirty_day_price(discounted_remaining, max(term_months, 1))
        if daily <= Decimal("0") and discounted_remaining > Decimal("0"):
            continue

        options.append({
            "term_months": term_months,
            "discount_percent": discount_percent,
            "total_cost": total_cost,
            "remaining_to_pay": discounted_remaining,
            "daily_price": daily,
            "thirty_day_price": monthly,
            "savings": max(savings, Decimal("0")),
            "final_payoff_date": today + timedelta(days=term_months * 30),
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
    payment_at=None,
    accumulate_partial_days: bool = False,
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

    payment_at = payment_at or timezone.now()

    if amount is None or amount <= Decimal("0"):
        return {
            "applied": Decimal("0"),
            "arrears_cleared": Decimal("0"),
            "days_extended": 0,
            "payment_type": payment_type,
            "error": "Payment amount must be greater than zero.",
        }

    closed_statuses = {
        "completed",
        "resold",
        "written_off",
        "legally_closed",
    }
    if contract.status in closed_statuses:
        return {
            "applied": Decimal("0"),
            "arrears_cleared": Decimal("0"),
            "days_extended": 0,
            "payment_type": payment_type,
            "error": "Contract is closed and cannot accept access-restoring payments.",
        }

    # ── Deposit payment ──────────────────────────────────────────────────────
    if payment_type == PaymentTransaction.TYPE_DEPOSIT:
        authoritative = get_deposit_summary(contract)
        if authoritative.confirmed_paid_amount <= Decimal("0"):
            return {
                "applied": Decimal("0"),
                "arrears_cleared": Decimal("0"),
                "days_extended": 0,
                "payment_type": payment_type,
                "error": "Deposit payment has not been confirmed.",
            }
        previous_snapshot = min(
            Decimal(str(contract.deposit_paid or 0)),
            authoritative.confirmed_paid_amount,
        )
        applied = max(authoritative.confirmed_paid_amount - previous_snapshot, Decimal("0"))
        was_complete = (
            authoritative.required_amount <= 0
            or previous_snapshot >= authoritative.required_amount
        )
        contract.deposit_paid = authoritative.confirmed_paid_amount
        confirmed_complete = authoritative.is_fully_paid
        became_complete = confirmed_complete and not was_complete
        if confirmed_complete and was_complete and applied <= Decimal("0"):
            return {
                "applied": Decimal("0"),
                "arrears_cleared": Decimal("0"),
                "days_extended": 0,
                "payment_type": payment_type,
                "deposit_remaining": Decimal("0"),
                "deposit_complete": True,
                "error": "Deposit is already fully paid.",
            }
        access_expiry = None
        if became_complete and confirmed_complete:
            access_days = get_deposit_access_days(contract)
            access_expiry = payment_at + timedelta(days=access_days)
            contract.deposit_paid_at = payment_at
            contract.last_payment_at = payment_at
            contract.deposit_unlock_expires_at = access_expiry
            contract.access_expires_at = access_expiry
            contract.due_date = access_expiry.date()
            contract.lock_date = access_expiry.date()
            contract.status = "active"
        if db_save:
            update_fields = ["deposit_paid"]
            if became_complete and confirmed_complete:
                update_fields.extend([
                    "deposit_paid_at",
                    "last_payment_at",
                    "deposit_unlock_expires_at",
                    "access_expires_at",
                    "due_date",
                    "lock_date",
                    "status",
                ])
            contract.save(update_fields=update_fields)
            if became_complete and confirmed_complete:
                try:
                    from core.models import AuditLog
                    AuditLog.objects.create(
                        user=None,
                        action=AuditLog.ACTION_PAYMENT,
                        object_type="PaymentContract",
                        object_id=str(contract.pk),
                        detail={
                            "event": "deposit_confirmed_contract_activated",
                            "confirmed_deposit": str(authoritative.confirmed_paid_amount),
                        },
                    )
                except Exception:
                    logger.exception("Could not audit deposit activation for %s", contract.pk)
        return {
            "applied": applied,
            "arrears_cleared": Decimal("0"),
            "days_extended": get_deposit_access_days(contract) if became_complete and confirmed_complete else 0,
            "payment_type": payment_type,
            "deposit_remaining": authoritative.remaining_amount,
            "deposit_complete": authoritative.is_fully_paid,
            "deposit_paid_at": contract.deposit_paid_at,
            "deposit_unlock_expires_at": contract.deposit_unlock_expires_at,
            "access_expires_at": contract.access_expires_at,
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

    # Airtel staging supports sub-instalment payments. Preserve the exact
    # remainder and allocate only complete days; normal production/provider
    # behavior continues to use the individual confirmed payment.
    partial_credit = getattr(contract, "partial_repayment_credit", Decimal("0")) or Decimal("0")
    partial_rate = getattr(contract, "partial_repayment_daily_rate", Decimal("0")) or Decimal("0")
    if accumulate_partial_days and contract.daily_price and contract.daily_price > 0:
        allocation_rate = partial_rate if partial_credit > 0 and partial_rate > 0 else contract.daily_price
        accumulated = partial_credit + applied
        days_extended = int(accumulated / allocation_rate)
        contract.partial_repayment_credit = accumulated - (Decimal(days_extended) * allocation_rate)
        contract.partial_repayment_daily_rate = allocation_rate if contract.partial_repayment_credit > 0 else Decimal("0")
    else:
        days_extended = days_covered_by_payment(applied, contract.daily_price)

    # Update contract financials
    contract.amount_paid += applied

    # Safety: never exceed total_amount
    if contract.amount_paid > contract.total_amount:
        contract.amount_paid = contract.total_amount

    # Extend exact access timestamp. If the customer still has active access,
    # extend from the current expiry; if expired, extend from this payment time.
    current_expiry = getattr(contract, "access_expires_at", None)
    if current_expiry and timezone.is_naive(current_expiry):
        current_expiry = timezone.make_aware(current_expiry, timezone.get_current_timezone())
    if accumulate_partial_days and days_extended == 0:
        new_access_expiry = current_expiry
    else:
        base_time = current_expiry if current_expiry and current_expiry > payment_at else payment_at
        new_access_expiry = base_time + timedelta(days=days_extended)
    contract.last_payment_at = payment_at
    if new_access_expiry is not None:
        contract.access_expires_at = new_access_expiry
        contract.due_date = new_access_expiry.date()
        contract.lock_date = new_access_expiry.date()

    # Update status
    if contract.amount_paid >= contract.total_amount:
        contract.status = "completed"
    elif new_access_expiry and new_access_expiry <= timezone.now():
        contract.status = "overdue"
    elif contract.status == "repossession_pending":
        contract.status = "active"
    elif contract.deposit_complete:
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
            "daily_price", "thirty_day_price", "last_payment_at", "access_expires_at",
            "partial_repayment_credit", "partial_repayment_daily_rate",
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
        "partial_credit": getattr(contract, "partial_repayment_credit", Decimal("0")),
        "payment_type": payment_type,
        "new_due_date": contract.due_date,
        "new_lock_date": contract.lock_date,
        "access_expires_at": contract.access_expires_at,
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
# PayG assignment (after device lock only)
# ---------------------------------------------------------------------------

def is_device_locked_for_payg(payment_contract) -> bool:
    """True when the linked contract or portal record indicates device lock."""
    if payment_contract.device_lock_status == payment_contract.LOCK_STATUS_LOCKED:
        return True
    if payment_contract.device_enrollment_status == payment_contract.ENROLLMENT_ENROLLED:
        return True

    application = payment_contract.source_application
    if application:
        contract = getattr(application, "contract", None)
        if contract and contract.phone_locked:
            return True
        profile = None
        try:
            from device_lock.models import DeviceLockProfile

            profile = DeviceLockProfile.objects.filter(contract=contract).first()
        except Exception:
            profile = None
        if profile and profile.lock_status in ("enrolled", "locked"):
            return True
    return False


@transaction.atomic
def assign_payg_after_device_lock(payment_contract, triggered_by=None) -> bool:
    """
    Generate and persist PayG number once the device is locked/enrolled.
    Returns True when a new PayG number was assigned.
    """
    from portal.models import PaymentContract, generate_payg_number

    if not isinstance(payment_contract, PaymentContract):
        return False

    payment_contract = PaymentContract.objects.select_for_update().get(pk=payment_contract.pk)
    if payment_contract.payg_number:
        return False
    if not is_device_locked_for_payg(payment_contract):
        return False

    payment_contract.payg_number = generate_payg_number()
    meta = dict(payment_contract.provider_metadata or {})
    meta["payg_number"] = payment_contract.payg_number
    meta["payment_url"] = f"/pay/payg/{payment_contract.payg_number}/"
    meta["payg_assigned_at"] = timezone.now().isoformat()
    payment_contract.provider_metadata = meta
    payment_contract.save(update_fields=["payg_number", "provider_metadata"])

    logger.info(
        "Assigned PayG %s to PaymentContract %s after device lock",
        payment_contract.payg_number,
        payment_contract.contract_number,
    )

    try:
        from notifications.models import Notification

        application = payment_contract.source_application
        if application and application.created_by_id:
            Notification.send(
                recipient=application.created_by,
                notification_type=getattr(Notification, "TYPE_CONTRACT_UPDATE", "contract_update"),
                title="PayG code ready",
                body=(
                    f"Payment code {payment_contract.payg_number} is ready for "
                    f"{payment_contract.customer_name}."
                ),
                link=application.get_continue_url(),
                level=getattr(Notification, "LEVEL_SUCCESS", "success"),
            )
    except Exception:
        logger.debug("PayG notification skipped for contract %s", payment_contract.pk)

    return True


def sync_portal_lock_from_contract(contract, *, assign_payg=True):
    """Mirror merchant lock checklist onto PaymentContract and assign PayG if eligible."""
    application = contract.application
    try:
        payment_contract = application.payment_contract
    except Exception:
        return None

    payment_contract.device_lock_status = payment_contract.LOCK_STATUS_LOCKED
    payment_contract.device_enrollment_status = payment_contract.ENROLLMENT_ENROLLED
    payment_contract.save(update_fields=["device_lock_status", "device_enrollment_status"])

    if assign_payg:
        assign_payg_after_device_lock(payment_contract)
    return payment_contract


# ---------------------------------------------------------------------------
# Contract search
# ---------------------------------------------------------------------------

def _normalize_identifier(value: str) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _phone_last9(value: str) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if digits.startswith("265"):
        digits = digits[3:]
    digits = digits.lstrip("0")
    return digits if len(digits) == 9 else ""


def _payment_contract_resolution_status(contract) -> ContractResolution:
    from portal.models import PaymentContract

    remaining = calculate_remaining_amount(contract)
    inactive_statuses = {
        PaymentContract.STATUS_CANCELLED,
        PaymentContract.STATUS_REPOSSESSION_PENDING,
        PaymentContract.STATUS_REPOSSESSED,
        PaymentContract.STATUS_READY_FOR_RESALE,
        PaymentContract.STATUS_RESOLD,
        PaymentContract.STATUS_WRITTEN_OFF,
        PaymentContract.STATUS_LEGALLY_CLOSED,
    }
    if remaining <= Decimal("0"):
        return ContractResolution("fully_paid", contract, "fully_paid", 1)
    if contract.status in inactive_statuses:
        return ContractResolution("inactive", contract, contract.status, 1)
    if contract.status == PaymentContract.STATUS_COMPLETED:
        return ContractResolution("fully_paid", contract, "completed", 1)
    return ContractResolution("payable", contract, "payable", 1)


def _sync_contract_state_from_legal(payment_contract, legal_contract):
    if not legal_contract:
        return payment_contract

    update_fields = []
    # The legal-contract boolean is not payment evidence. Deposit state is
    # reconciled exclusively from confirmed PaymentTransaction ledger rows.

    if payment_contract.status == payment_contract.STATUS_COMPLETED and payment_contract.remaining_amount > 0:
        payment_contract.status = payment_contract.STATUS_ACTIVE
        update_fields.append("status")

    if update_fields:
        payment_contract.save(update_fields=list(dict.fromkeys(update_fields)))
    return payment_contract


def _contract_from_application(application):
    from portal.models import PaymentContract

    if not application:
        return None
    try:
        contract = application.payment_contract
    except PaymentContract.DoesNotExist:
        contract = None

    legal_contract = getattr(application, "contract", None)
    if contract:
        return _sync_contract_state_from_legal(contract, legal_contract)

    if legal_contract:
        try:
            synced = sync_portal_lock_from_contract(legal_contract)
            if synced:
                return _sync_contract_state_from_legal(synced, legal_contract)
            created = create_contract_from_application(application)
            return _sync_contract_state_from_legal(created, legal_contract)
        except Exception:
            logger.exception("Failed to sync payment contract for application %s", application.pk)
    return None


def _collect_contract_candidates(query: str):
    from django.db.models import Q

    from applications.models import FinancingApplication
    from contracts.models import Contract
    from portal.models import PaymentContract

    raw_q = (query or "").strip()
    lookup = _normalize_identifier(raw_q)
    phone = _phone_last9(raw_q)
    candidates = {}

    def add(contract):
        if contract:
            candidates[contract.pk] = contract

    direct_filter = (
        Q(contract_number__iexact=raw_q)
        | Q(contract_number__iexact=lookup)
        | Q(payg_number__iexact=raw_q)
        | Q(payg_number__iexact=lookup)
        | Q(customer_national_id__iexact=raw_q)
        | Q(customer_national_id__iexact=lookup)
        | Q(source_application__application_number__iexact=raw_q)
        | Q(source_application__application_number__iexact=lookup)
        | Q(source_application__national_id__iexact=raw_q)
        | Q(source_application__national_id__iexact=lookup)
        | Q(source_application__contract__contract_number__iexact=raw_q)
        | Q(source_application__contract__contract_number__iexact=lookup)
    )
    for contract in PaymentContract.objects.select_related("source_application", "source_application__contract").filter(direct_filter):
        add(_sync_contract_state_from_legal(contract, getattr(contract.source_application, "contract", None)))

    legal = (
        Contract.objects.select_related("application", "application__payment_contract")
        .filter(Q(contract_number__iexact=raw_q) | Q(contract_number__iexact=lookup))
        .first()
    )
    if legal and legal.application_id:
        add(_contract_from_application(legal.application))

    application_filter = (
        Q(application_number__iexact=raw_q)
        | Q(application_number__iexact=lookup)
        | Q(national_id__iexact=raw_q)
        | Q(national_id__iexact=lookup)
    )
    if phone:
        application_filter |= Q(customer_phone__endswith=phone) | Q(next_of_kin_1_phone__endswith=phone) | Q(next_of_kin_2_phone__endswith=phone)
    for application in (
        FinancingApplication.objects.select_related("contract", "payment_contract")
        .filter(application_filter)
        .order_by("-created_at", "-id")[:5]
    ):
        add(_contract_from_application(application))

    if phone:
        for contract in PaymentContract.objects.select_related("source_application", "source_application__contract").filter(
            Q(customer_phone__endswith=phone)
            | Q(source_application__customer_phone__endswith=phone)
            | Q(source_application__next_of_kin_1_phone__endswith=phone)
            | Q(source_application__next_of_kin_2_phone__endswith=phone)
        ):
            add(_sync_contract_state_from_legal(contract, getattr(contract.source_application, "contract", None)))

    if lookup.isdigit() and len(lookup) == 15:
        for contract in PaymentContract.objects.select_related("source_application", "source_application__contract").filter(
            Q(imei_number=lookup)
            | Q(source_application__imei_number=lookup)
            | Q(source_application__imei_number_2=lookup)
        ):
            add(_sync_contract_state_from_legal(contract, getattr(contract.source_application, "contract", None)))

    return list(candidates.values())


def resolve_payable_contract(query: str, country: str = "MW") -> ContractResolution:
    raw_q = (query or "").strip()
    if not raw_q:
        return ContractResolution("not_found", None, "empty_query", 0)
    if (country or "MW").strip().upper() != "MW":
        return ContractResolution("not_found", None, "unsupported_country", 0)

    matches = _collect_contract_candidates(raw_q)
    if not matches:
        return ContractResolution("not_found", None, "not_found", 0)
    if len(matches) > 1:
        payable=[contract for contract in matches if _payment_contract_resolution_status(contract).status=="payable"]
        if len(payable)==1:
            return ContractResolution("payable",payable[0],"unique_payable_match",len(matches))
        logger.warning(
            "Ambiguous payment contract search for query=%s country=%s matched=%s",
            _normalize_identifier(raw_q),
            country,
            [contract.pk for contract in matches],
        )
        return ContractResolution("ambiguous", None, "ambiguous", len(matches))

    result = _payment_contract_resolution_status(matches[0])
    return ContractResolution(result.status, result.contract, result.reason, len(matches))


def search_payment_contract(query: str, country: str = "MW"):
    result = resolve_payable_contract(query, country=country)
    return result.contract if result.found and result.status != "ambiguous" else None

    """
    Look up a PaymentContract by:
      - contract_number (TS-MW-XXXXXXXX)
      - payg_number (EXXXXXXX — PayG reference, not legal contract number)
      - legal contract number (contracts.Contract — A + 7 chars)
      - customer_national_id
      - customer_phone (last 9 digits matched)

    Returns the first matching PaymentContract or None.
    """
    from django.db.models import Case, F, IntegerField, Q, Value, When

    from portal.models import PaymentContract

    raw_q = (query or "").strip()
    q = raw_q
    if not q:
        return None
    lookup = "".join(c for c in q.upper() if c.isalnum())
    phone_digits = "".join(c for c in q if c.isdigit())
    phone_last9 = phone_digits[-9:] if len(phone_digits) >= 9 else ""

    def _best_match(qs):
        return (
            qs.select_related("source_application", "source_application__contract")
            .annotate(
                _match_rank=Case(
                    When(status=PaymentContract.STATUS_ACTIVE, then=Value(0)),
                    When(source_application__status="deposit_pending", then=Value(1)),
                    When(deposit_required__gt=F("deposit_paid"), then=Value(2)),
                    default=Value(3),
                    output_field=IntegerField(),
                )
            )
            .order_by("_match_rank", "-created_at", "-id")
            .first()
        )

    def _from_application(application):
        if not application:
            return None
        try:
            return application.payment_contract
        except PaymentContract.DoesNotExist:
            pass
        legal_contract = getattr(application, "contract", None)
        if legal_contract:
            try:
                contract = sync_portal_lock_from_contract(legal_contract)
                if contract:
                    return contract
                return create_contract_from_application(application)
            except Exception:
                logger.exception("Failed to sync payment contract for application %s", application.pk)
        return None

    # Customer-facing legal contract number (A + 7 chars) → linked payment contract
    if len(lookup) == 8 and lookup[0] == "A":
        from contracts.models import Contract

        legal = (
            Contract.objects.filter(contract_number__iexact=lookup)
            .select_related("application", "application__payment_contract")
            .first()
        )
        if legal and legal.application_id:
            contract = _from_application(legal.application)
            if contract:
                return contract

    if lookup.startswith("TSM"):
        from applications.models import FinancingApplication

        app = (
            FinancingApplication.objects.filter(application_number__iexact=q)
            .select_related("contract", "payment_contract")
            .first()
        )
        if not app:
            app = (
                FinancingApplication.objects.filter(application_number__iexact=lookup)
                .select_related("contract", "payment_contract")
                .first()
            )
        contract = _from_application(app)
        if contract:
            return contract

    # Direct contract number / PayG / national ID lookup
    contract = _best_match(
        PaymentContract.objects.filter(
            Q(contract_number__iexact=q)
            | Q(contract_number__iexact=lookup)
            | Q(payg_number__iexact=q)
            | Q(payg_number__iexact=lookup)
            | Q(customer_national_id__iexact=q)
            | Q(customer_national_id__iexact=lookup)
        )
    )
    if contract:
        return contract

    # IMEI lookup (exact match — IMEIs are unique per device)
    if phone_digits and len(phone_digits) == 15:
        contract = _best_match(
            PaymentContract.objects.filter(
                Q(imei_number=phone_digits)
                | Q(source_application__imei_number=phone_digits)
                | Q(source_application__imei_number_2=phone_digits)
            )
        )
        if contract:
            return contract

    # Phone lookup — support +265, 265, local 0, spaces, and hyphens.
    if phone_last9:
        contract = _best_match(
            PaymentContract.objects.filter(
                Q(customer_phone__endswith=phone_last9)
                | Q(source_application__customer_phone__endswith=phone_last9)
                | Q(source_application__next_of_kin_1_phone__endswith=phone_last9)
                | Q(source_application__next_of_kin_2_phone__endswith=phone_last9)
                | Q(source_application__proof_contact_phone__endswith=phone_last9)
            )
        )
    return contract


# ---------------------------------------------------------------------------
# Contract creation from approved FinancingApplication
# ---------------------------------------------------------------------------

def _apply_pricing_to_payment_contract(contract, pricing: dict) -> None:
    """Update PaymentContract commercial fields from a pricing snapshot."""
    contract.cash_price = pricing["cash_price"]
    contract.total_amount = pricing["contract_total"]
    contract.deposit_required = pricing["deposit_required"]
    contract.daily_price = pricing["daily_repayment"]
    contract.thirty_day_price = pricing["monthly_repayment"]
    contract.term_months = pricing["term_months"]
    if pricing.get("device_model") and not contract.device_model:
        contract.device_model = pricing["device_model"]


def sync_merchant_contract_from_application(application) -> None:
    """Keep contracts.Contract in sync with application pricing."""
    from contracts.models import Contract
    from core.commercial import pricing_from_application

    pricing = pricing_from_application(application)
    if not pricing:
        return
    contract, _ = Contract.from_application(application)
    contract.cash_price = pricing["cash_price"]
    contract.total_loan = pricing["contract_total"]
    contract.deposit_amount = pricing["deposit_required"]
    contract.monthly_payment = pricing["monthly_repayment"]
    contract.daily_payment = pricing["daily_repayment"]
    contract.term_months = pricing["term_months"]
    if application.imei_number and not contract.imei_number:
        contract.imei_number = application.imei_number
    contract.save(
        update_fields=[
            "cash_price", "total_loan", "deposit_amount",
            "monthly_payment", "daily_payment", "term_months", "imei_number",
        ]
    )


@transaction.atomic
def create_contract_from_application(application, approved_by=None) -> "PaymentContract":
    """
    Idempotently create a PaymentContract from an approved FinancingApplication.
    Also creates a MerchantContractPayout.
    Returns the (possibly existing) PaymentContract.
    """
    from portal.models import PaymentContract
    from core.commercial import pricing_from_application, sync_application_pricing_fields

    sync_application_pricing_fields(application, save=True)
    pricing = pricing_from_application(application)
    if not pricing:
        raise ValueError("Cannot create payment contract: deal pricing is incomplete.")

    # Idempotent: return or repair existing contract
    try:
        existing = application.payment_contract
        if existing.total_amount <= Decimal("0") or existing.daily_price <= Decimal("0"):
            _apply_pricing_to_payment_contract(existing, pricing)
            existing.save(
                update_fields=[
                    "cash_price", "total_amount", "deposit_required",
                    "daily_price", "thirty_day_price", "term_months", "device_model",
                ]
            )
        sync_merchant_contract_from_application(application)
        return existing
    except PaymentContract.DoesNotExist:
        pass

    imei = getattr(application, "imei_number", "") or ""
    provider_meta = {
        "imei": imei,
        "customer_phone": application.customer_phone or "",
        "device_model": pricing.get("device_model", ""),
    }

    # Resolve deposit_access_days from the deal or fall back to setting default
    from django.conf import settings as django_settings
    _default_access_days = int(getattr(django_settings, "DEFAULT_DEPOSIT_ACCESS_DAYS", 7))
    try:
        _deal = getattr(application, "deal", None)
        _deposit_access_days = int(_deal.unlock_days) if _deal and _deal.unlock_days else _default_access_days
    except Exception:
        _deposit_access_days = _default_access_days

    contract = PaymentContract.objects.create(
        source_application=application,
        customer_name=application.customer_name or "",
        customer_phone=application.customer_phone or "",
        customer_national_id=application.national_id or "",
        device_model=pricing.get("device_model", ""),
        imei_number=imei,
        cash_price=pricing["cash_price"],
        total_amount=pricing["contract_total"],
        deposit_required=pricing["deposit_required"],
        deposit_paid=Decimal("0"),
        daily_price=pricing["daily_repayment"],
        thirty_day_price=pricing["monthly_repayment"],
        term_months=pricing["term_months"],
        deposit_access_days=_deposit_access_days,
        status=(
            PaymentContract.STATUS_PENDING_ACTIVATION
            if pricing["deposit_required"] > Decimal("0")
            else PaymentContract.STATUS_ACTIVE
        ),
        provider_metadata=provider_meta,
    )

    # PayG is assigned only after device lock — not at approval
    if is_device_locked_for_payg(contract):
        assign_payg_after_device_lock(contract)

    logger.info(
        "Created PaymentContract %s from application %s (PayG: %s)",
        contract.contract_number,
        application.application_number,
        contract.payg_number or "pending lock",
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

    sync_merchant_contract_from_application(application)
    return contract


# ---------------------------------------------------------------------------
# Payment commission (underwriter 7% on repayments)
# ---------------------------------------------------------------------------

def resolve_underwriter_for_contract(contract):
    """Underwriter assigned at approval (claimed_by / reviewed_by on source application)."""
    app = getattr(contract, "source_application", None)
    if app is None:
        return None
    return getattr(app, "claimed_by", None) or getattr(app, "reviewed_by", None)


def finalize_paid_transaction_commission(tx):
    """
    Set commissionable_amount and create underwriter ledger entry for paid repayments.
    Deposits and non-repayment types do not earn commission.
    """
    from portal.models import PaymentTransaction
    from commissions.services import create_commission_for_payment

    if tx.status != PaymentTransaction.STATUS_PAID:
        return None

    if tx.amount is None or tx.amount <= Decimal("0"):
        return None

    if tx.payment_type == PaymentTransaction.TYPE_REPAYMENT:
        commissionable = Decimal(tx.amount)
    else:
        commissionable = Decimal("0")

    if tx.commissionable_amount != commissionable:
        tx.commissionable_amount = commissionable
        tx.save(update_fields=["commissionable_amount", "updated_at"])

    if commissionable <= 0:
        return None

    underwriter = resolve_underwriter_for_contract(tx.payment_contract)
    return create_commission_for_payment(tx, underwriter=underwriter)
