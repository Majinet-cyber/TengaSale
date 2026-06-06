"""
commissions/services.py

TengaSale commission and payout business logic.

Rules:
  - Deposit excluded from underwriter commission.
  - Underwriter earns 7% on each actual repayment after deposit.
  - Missed day creates 14% arrears deduction on contract.daily_price.
  - Merchant settlement due = cash_price (phone selling price).
  - Optional merchant commission (1% financed) tracked separately from settlement.
  - Underwriter monthly payout has 20% WHT on positive gross.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from core.services import get_business_settings

from .models import Commission, CommissionLedger, MerchantContractPayout, UnderwriterMonthlyPayout

logger = logging.getLogger(__name__)

User = get_user_model()

TWOPLACES = Decimal("0.01")
ZEROPLACE = Decimal("1")

UNDERWRITER_COMMISSION_RATE = Decimal("0.07")
ARREARS_DEDUCTION_RATE = Decimal("0.14")
MERCHANT_COMMISSION_RATE = Decimal("0.01")
WHT_RATE = Decimal("0.20")


def _round(value: Decimal) -> Decimal:
    """Round to nearest whole MWK."""
    return Decimal(value or 0).quantize(ZEROPLACE, rounding=ROUND_HALF_UP)


def _money(value) -> Decimal:
    return Decimal(value or 0).quantize(TWOPLACES)


def _audit(user, action, obj_type="", obj_id="", detail=None):
    try:
        from core.models import AuditLog
        AuditLog.objects.create(
            user=user,
            action=action,
            object_type=obj_type,
            object_id=str(obj_id),
            detail=detail or {},
        )
    except Exception as exc:
        logger.warning("AuditLog write failed: %s", exc)


# ──────────────────────────────────────────────────────────────────────────────
# 1. Calculate underwriter payment commission
# ──────────────────────────────────────────────────────────────────────────────

def calculate_underwriter_payment_commission(payment) -> Decimal:
    """
    Calculate 7% commission on commissionable_amount.
    Deposit-only payments yield 0.
    """
    commissionable = Decimal(getattr(payment, "commissionable_amount", 0) or 0)
    if commissionable <= 0:
        return Decimal("0")
    return _round(commissionable * UNDERWRITER_COMMISSION_RATE)


# ──────────────────────────────────────────────────────────────────────────────
# 2. Create commission for a payment
# ──────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def create_commission_for_payment(payment, underwriter=None) -> Optional[CommissionLedger]:
    """
    Create a repayment_commission ledger entry for a paid transaction.
    Skips deposit-only payments (commissionable_amount == 0).
    Idempotent: skips if entry already exists for this payment+user.
    """
    from portal.models import PaymentTransaction

    if payment.status != PaymentTransaction.STATUS_PAID:
        return None

    commissionable = Decimal(getattr(payment, "commissionable_amount", 0) or 0)
    if commissionable <= 0:
        return None

    commission_amount = _round(commissionable * UNDERWRITER_COMMISSION_RATE)
    if commission_amount <= 0:
        return None

    if underwriter is None:
        from portal.services import resolve_underwriter_for_contract

        contract = payment.payment_contract
        underwriter = resolve_underwriter_for_contract(contract) if contract else None

    if underwriter is None:
        logger.warning("create_commission_for_payment: no underwriter found for payment %s", payment.pk)
        return None

    paid_at = getattr(payment, "paid_at", None) or getattr(payment, "created_at", None)
    period_start = None
    period_end = None
    if paid_at is not None:
        paid_date = paid_at.date() if hasattr(paid_at, "date") else paid_at
        period_start = paid_date.replace(day=1)
        if paid_date.month == 12:
            period_end = paid_date.replace(day=31)
        else:
            from calendar import monthrange
            last_day = monthrange(paid_date.year, paid_date.month)[1]
            period_end = paid_date.replace(day=last_day)

    try:
        entry = CommissionLedger.objects.create(
            user=underwriter,
            contract=payment.payment_contract,
            entry_type=CommissionLedger.ENTRY_REPAYMENT,
            amount=commission_amount,
            base_amount=commissionable,
            rate=UNDERWRITER_COMMISSION_RATE,
            description=f"7% commission on MWK {commissionable} repayment (ref: {payment.internal_reference})",
            source_payment=payment,
            period_start=period_start,
            period_end=period_end,
        )
        _audit(underwriter, "commission_created", "CommissionLedger", entry.pk,
               {"amount": str(commission_amount), "payment_ref": payment.internal_reference})
        logger.info("Commission created: %s MWK for user %s payment %s", commission_amount, underwriter, payment.pk)
        return entry
    except IntegrityError:
        logger.info("Duplicate commission skipped for payment %s user %s", payment.pk, underwriter)
        return None


# ──────────────────────────────────────────────────────────────────────────────
# 3. Calculate arrears deduction
# ──────────────────────────────────────────────────────────────────────────────

def calculate_arrears_deduction(contract, missed_days: int = 1) -> Decimal:
    """14% of daily_price per missed day."""
    daily_price = Decimal(getattr(contract, "daily_price", 0) or 0)
    if daily_price <= 0 or missed_days <= 0:
        return Decimal("0")
    return _round(daily_price * ARREARS_DEDUCTION_RATE * missed_days)


# ──────────────────────────────────────────────────────────────────────────────
# 4. Create daily arrears deduction
# ──────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def create_daily_arrears_deduction(contract, missed_date: date, underwriter=None) -> Optional[CommissionLedger]:
    """
    Create a negative arrears_deduction ledger entry.
    Only applies to active, overdue, or locked contracts.
    One deduction per contract/user/missed_date — idempotent.
    Completed contracts are skipped.
    """
    from portal.models import PaymentContract

    if contract.status in (PaymentContract.STATUS_COMPLETED, PaymentContract.STATUS_CANCELLED):
        return None

    if contract.status not in (PaymentContract.STATUS_ACTIVE, PaymentContract.STATUS_OVERDUE, PaymentContract.STATUS_LOCKED):
        return None

    deduction = calculate_arrears_deduction(contract, missed_days=1)
    if deduction <= 0:
        return None

    if underwriter is None:
        logger.warning("create_daily_arrears_deduction: no underwriter for contract %s", contract.pk)
        return None

    try:
        entry = CommissionLedger.objects.create(
            user=underwriter,
            contract=contract,
            entry_type=CommissionLedger.ENTRY_ARREARS,
            amount=-deduction,
            base_amount=Decimal(contract.daily_price),
            rate=ARREARS_DEDUCTION_RATE,
            description=f"14% arrears deduction for missed day {missed_date}",
            missed_date=missed_date,
        )
        _audit(underwriter, "arrears_deduction_created", "CommissionLedger", entry.pk,
               {"amount": str(-deduction), "missed_date": str(missed_date), "contract": str(contract.pk)})
        logger.info("Arrears deduction: -%s MWK user %s contract %s date %s",
                    deduction, underwriter, contract.pk, missed_date)
        return entry
    except IntegrityError:
        logger.info("Duplicate arrears deduction skipped: contract %s user %s date %s",
                    contract.pk, underwriter, missed_date)
        return None


# ──────────────────────────────────────────────────────────────────────────────
# 5. Calculate merchant commission
# ──────────────────────────────────────────────────────────────────────────────

def calculate_merchant_commission(contract) -> Decimal:
    """1% of financed_amount (total_amount - deposit_paid)."""
    total = Decimal(getattr(contract, "total_amount", 0) or 0)
    deposit = Decimal(getattr(contract, "deposit_paid", 0) or 0)
    financed = max(total - deposit, Decimal("0"))
    return _round(financed * MERCHANT_COMMISSION_RATE)


# ──────────────────────────────────────────────────────────────────────────────
# 6. Create merchant payout for contract
# ──────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def create_merchant_payout_for_contract(contract, merchant_user=None, created_by=None) -> Optional[MerchantContractPayout]:
    """
    Idempotently create a MerchantContractPayout for a portal PaymentContract.
    Merchant settlement due = cash_price (phone cash price).
    merchant_commission_amount is tracked separately (1% of financed amount).
    No WHT on merchant settlement.
    """
    existing = MerchantContractPayout.objects.filter(contract=contract).first()
    if existing:
        return existing

    total_amount = Decimal(getattr(contract, "total_amount", 0) or 0)
    deposit_amount = Decimal(getattr(contract, "deposit_paid", 0) or 0)
    financed_amount = max(total_amount - deposit_amount, Decimal("0"))

    cash_price = Decimal(getattr(contract, "cash_price", 0) or 0)
    if cash_price <= 0:
        app = getattr(contract, "source_application", None)
        if app is not None:
            cash_price = Decimal(getattr(app, "selected_cash_price", 0) or 0)
    if cash_price <= 0:
        logger.warning(
            "create_merchant_payout_for_contract: missing cash_price on contract %s", contract.pk
        )
        return None

    merchant_commission = _round(financed_amount * MERCHANT_COMMISSION_RATE)
    total_payable = _money(cash_price)

    if merchant_user is None:
        logger.warning("create_merchant_payout_for_contract: no merchant_user provided for contract %s", contract.pk)
        return None

    payout = MerchantContractPayout.objects.create(
        merchant=merchant_user,
        contract=contract,
        cash_price=cash_price,
        deposit_amount=deposit_amount,
        financed_amount=financed_amount,
        merchant_commission_rate=MERCHANT_COMMISSION_RATE,
        merchant_commission_amount=merchant_commission,
        total_payable=total_payable,
        status=MerchantContractPayout.STATUS_PENDING,
        created_by=created_by,
    )
    _audit(created_by or merchant_user, "merchant_payout_created", "MerchantContractPayout", payout.pk,
           {"contract": str(contract.pk), "total_payable": str(total_payable)})
    logger.info("Merchant payout created: %s for user %s contract %s", total_payable, merchant_user, contract.pk)
    return payout


# ──────────────────────────────────────────────────────────────────────────────
# 7. Generate underwriter monthly payout
# ──────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def generate_underwriter_monthly_payout(
    user, period_start: date, period_end: date
) -> UnderwriterMonthlyPayout:
    """
    Sum all CommissionLedger entries for the period, apply 20% WHT on positive gross.
    Returns an UnderwriterMonthlyPayout (creates or returns existing).
    """
    existing = UnderwriterMonthlyPayout.objects.filter(
        user=user, period_start=period_start, period_end=period_end
    ).first()
    if existing:
        return existing

    entries = CommissionLedger.objects.filter(
        user=user,
        period_start=period_start,
        period_end=period_end,
        entry_type__in=[
            CommissionLedger.ENTRY_REPAYMENT,
            CommissionLedger.ENTRY_ARREARS,
            CommissionLedger.ENTRY_ADJUSTMENT,
        ],
    )
    gross = _money(sum(e.amount for e in entries))

    payout = UnderwriterMonthlyPayout.objects.create(
        user=user,
        period_start=period_start,
        period_end=period_end,
        gross_commission=gross,
        wht_rate=WHT_RATE,
    )

    if payout.wht_amount > 0:
        CommissionLedger.objects.create(
            user=user,
            entry_type=CommissionLedger.ENTRY_WHT,
            amount=-payout.wht_amount,
            base_amount=gross,
            rate=WHT_RATE,
            description=f"20% WHT for period {period_start}–{period_end}",
            period_start=period_start,
            period_end=period_end,
        )

    _audit(user, "monthly_payout_generated", "UnderwriterMonthlyPayout", payout.pk,
           {"gross": str(gross), "wht": str(payout.wht_amount), "net": str(payout.net_amount)})
    return payout


# ──────────────────────────────────────────────────────────────────────────────
# 8. Get underwriter wallet summary
# ──────────────────────────────────────────────────────────────────────────────

def get_underwriter_wallet_summary(user) -> dict:
    """Return a comprehensive wallet summary dict for an underwriter."""
    from django.db.models import Sum
    from django.utils import timezone

    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).date()
    month_end = now.date()

    all_entries = CommissionLedger.objects.filter(user=user)

    def _sum_type(entries, entry_type, positive_only=False):
        qs = entries.filter(entry_type=entry_type)
        if positive_only:
            qs = qs.filter(amount__gt=0)
        return qs.aggregate(total=Sum("amount"))["total"] or Decimal("0")

    total_commissions_earned = _sum_type(all_entries, CommissionLedger.ENTRY_REPAYMENT)
    total_arrears_deductions = _sum_type(all_entries, CommissionLedger.ENTRY_ARREARS)

    month_entries = all_entries.filter(created_at__date__gte=month_start)
    current_month_gross = _money(sum(
        e.amount for e in month_entries.filter(
            entry_type__in=[CommissionLedger.ENTRY_REPAYMENT, CommissionLedger.ENTRY_ARREARS, CommissionLedger.ENTRY_ADJUSTMENT]
        )
    ))
    current_month_wht_estimate = _money(max(current_month_gross, Decimal("0")) * WHT_RATE)
    current_month_net_estimate = _money(current_month_gross - current_month_wht_estimate)

    paid_payouts = UnderwriterMonthlyPayout.objects.filter(user=user, status=UnderwriterMonthlyPayout.STATUS_PAID)
    pending_payouts = UnderwriterMonthlyPayout.objects.filter(
        user=user, status__in=[UnderwriterMonthlyPayout.STATUS_PENDING, UnderwriterMonthlyPayout.STATUS_PROCESSING]
    )

    total_paid_net = _money(paid_payouts.aggregate(total=Sum("net_amount"))["total"] or 0)
    available_balance = _money(
        total_commissions_earned + total_arrears_deductions - total_paid_net
    )

    return {
        "total_commissions_earned": total_commissions_earned,
        "total_arrears_deductions": total_arrears_deductions,
        "current_month_gross": current_month_gross,
        "current_month_wht_estimate": current_month_wht_estimate,
        "current_month_net_estimate": current_month_net_estimate,
        "pending_payouts": pending_payouts,
        "paid_payouts": paid_payouts,
        "available_balance": available_balance,
    }


# ──────────────────────────────────────────────────────────────────────────────
# 9. Get merchant settlement summary
# ──────────────────────────────────────────────────────────────────────────────

def get_merchant_settlement_summary(merchant_user) -> dict:
    """Return settlement summary for a merchant."""
    from django.db.models import Sum

    payouts = MerchantContractPayout.objects.filter(merchant=merchant_user)
    pending = payouts.filter(status=MerchantContractPayout.STATUS_PENDING)
    paid = payouts.filter(status=MerchantContractPayout.STATUS_PAID)
    failed = payouts.filter(status=MerchantContractPayout.STATUS_FAILED)

    return {
        "pending_cash_settlements": _money(pending.aggregate(t=Sum("cash_price"))["t"] or 0),
        "paid_cash_settlements": _money(paid.aggregate(t=Sum("cash_price"))["t"] or 0),
        "merchant_commissions": _money(payouts.aggregate(t=Sum("merchant_commission_amount"))["t"] or 0),
        "total_payable": _money(payouts.aggregate(t=Sum("total_payable"))["t"] or 0),
        "failed_payouts": failed.count(),
        "pending_count": pending.count(),
        "paid_count": paid.count(),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Legacy commission functions (keep working for existing tests)
# ──────────────────────────────────────────────────────────────────────────────

TWOPLACES_OLD = Decimal("0.01")


def money(value):
    return Decimal(value or 0).quantize(TWOPLACES_OLD)


def get_sale_amount(application, settings=None):
    settings = settings or get_business_settings()
    sale_amount = application.calculated_total_loan or Decimal("0")
    if sale_amount <= 0 and application.selected_cash_price:
        multiplier = application.selected_loan_multiplier or settings.loan_multiplier
        sale_amount = application.selected_cash_price * multiplier
    return money(sale_amount)


def calculate_commission_amount(sale_amount, percent):
    return money((sale_amount * Decimal(percent or 0)) / Decimal("100"))


@transaction.atomic
def process_application_approval(application, approved_by):
    settings = get_business_settings()
    sale_amount = get_sale_amount(application, settings)
    if sale_amount <= 0:
        return {
            "sale_amount": sale_amount,
            "merchant_commission": None,
            "manager_commission": None,
            "spin_granted": False,
        }

    merchant_amount = calculate_commission_amount(sale_amount, settings.merchant_commission_percent)
    merchant_commission, _ = Commission.objects.get_or_create(
        application=application,
        user=application.created_by,
        role=Commission.ROLE_MERCHANT,
        defaults={
            "commission_percent": settings.merchant_commission_percent,
            "sale_amount": sale_amount,
            "amount": merchant_amount,
            "status": Commission.STATUS_PENDING,
        },
    )

    manager_commission = None
    if approved_by:
        manager_amount = calculate_commission_amount(sale_amount, settings.manager_commission_percent)
        manager_commission, _ = Commission.objects.get_or_create(
            application=application,
            user=approved_by,
            role=Commission.ROLE_MANAGER,
            defaults={
                "commission_percent": settings.manager_commission_percent,
                "sale_amount": sale_amount,
                "amount": manager_amount,
                "status": Commission.STATUS_PENDING,
            },
        )

    spin_granted = False
    if settings.spin_enabled:
        from rewards.services import award_spin_for_application, get_spin_config

        if get_spin_config().is_enabled:
            spin_granted = award_spin_for_application(application, application.created_by)
            if approved_by:
                award_spin_for_application(application, approved_by)

    try:
        from notifications.utils import notify_application_approved

        notify_application_approved(application, approved_by=approved_by)
    except Exception:
        pass

    return {
        "sale_amount": sale_amount,
        "merchant_commission": merchant_commission,
        "manager_commission": manager_commission,
        "spin_granted": spin_granted,
    }


@transaction.atomic
def process_contract_completion(application):
    settings = get_business_settings()
    sale_amount = get_sale_amount(application, settings)
    if sale_amount <= 0:
        return {"sale_amount": sale_amount, "merchant_commission": None, "spin_granted": False}

    merchant_amount = calculate_commission_amount(sale_amount, settings.merchant_commission_percent)
    merchant_commission, _ = Commission.objects.get_or_create(
        application=application,
        user=application.created_by,
        role=Commission.ROLE_MERCHANT,
        defaults={
            "commission_percent": settings.merchant_commission_percent,
            "sale_amount": sale_amount,
            "amount": merchant_amount,
            "status": Commission.STATUS_PENDING,
        },
    )

    spin_granted = False
    if settings.spin_enabled:
        from rewards.services import award_spin_for_application, get_spin_config

        if get_spin_config().is_enabled:
            spin_granted = award_spin_for_application(application, application.created_by)

    return {
        "sale_amount": sale_amount,
        "merchant_commission": merchant_commission,
        "spin_granted": spin_granted,
    }


def cancel_application_commissions(application):
    Commission.objects.filter(application=application).exclude(status=Commission.STATUS_PAID).update(
        status=Commission.STATUS_CANCELLED
    )
