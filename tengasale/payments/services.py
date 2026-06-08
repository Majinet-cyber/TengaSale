"""
TengaSale Payments — Service Layer

Functions:
  - get_hq_payment_summary()        — dashboard overview numbers
  - create_payout_batch()           — create a batch for approval
  - approve_payout_batch()          — HQ approves a batch
  - process_payout_batch()          — mark batch as processing, update items
  - generate_salary_schedule()      — generate payroll for a month
  - approve_salary_schedule()       — HQ approves a salary record
  - create_spin_reward_payout()     — create payout for a spin win
  - accrue_merchant_commission()    — create CommissionLedger for merchant
  - accrue_underwriter_commission() — create CommissionLedger for underwriter
  - notify_payout_processing()      — send SMS/notification
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from commissions.models import CommissionLedger, MerchantContractPayout
from portal.models import PaymentTransaction

from .models import (
    CommissionRule, PayoutBatch, PayoutItem,
    PaymentAuditLog, SalarySchedule, SpinRewardPayout,
)

logger = logging.getLogger(__name__)
User = get_user_model()


# ──────────────────────────────────────────────────────────────────────────────
# HQ Dashboard Summary
# ──────────────────────────────────────────────────────────────────────────────

def get_hq_payment_summary() -> dict:
    """Return overview numbers for the HQ Payment Center."""
    from django.db.models import Sum, Count, Q
    from django.utils import timezone as tz

    today = tz.localdate()
    month_start = today.replace(day=1)

    # Customer collections
    qs = PaymentTransaction.objects.all()
    paid_qs = qs.filter(status="paid")

    today_collected = paid_qs.filter(paid_at__date=today).aggregate(
        total=Sum("amount"))["total"] or Decimal("0")
    month_collected = paid_qs.filter(paid_at__date__gte=month_start).aggregate(
        total=Sum("amount"))["total"] or Decimal("0")
    failed_payments = qs.filter(status="failed").count()

    # Outgoing payouts
    pending_batches = PayoutBatch.objects.filter(
        status__in=[PayoutBatch.STATUS_PENDING_APPROVAL, PayoutBatch.STATUS_APPROVED]
    )
    pending_payout_total = pending_batches.aggregate(total=Sum("total_amount"))["total"] or Decimal("0")

    # Commissions payable (accrued, not yet paid)
    from commissions.models import CommissionLedger
    merchant_payable = CommissionLedger.objects.filter(
        merchant__isnull=False,
        entry_type="repayment_commission",
        amount__gt=0,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

    underwriter_payable = CommissionLedger.objects.filter(
        user__isnull=False,
        merchant__isnull=True,
        entry_type="repayment_commission",
        amount__gt=0,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

    # Salaries due (draft or pending_approval for current month)
    salaries_due = SalarySchedule.objects.filter(
        status__in=[SalarySchedule.STATUS_DRAFT, SalarySchedule.STATUS_PENDING_APPROVAL],
        salary_month__month=today.month,
        salary_month__year=today.year,
    ).aggregate(total=Sum("net_pay"))["total"] or Decimal("0")

    # Spin rewards pending
    spin_pending = SpinRewardPayout.objects.filter(
        status__in=[SpinRewardPayout.STATUS_PENDING, SpinRewardPayout.STATUS_PROCESSING]
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

    # Pending batches count
    pending_payout_count = PayoutBatch.objects.filter(
        status__in=[PayoutBatch.STATUS_PENDING_APPROVAL, PayoutBatch.STATUS_APPROVED]
    ).count()

    return {
        "today_collected": today_collected,
        "month_collected": month_collected,
        "failed_payments": failed_payments,
        "pending_payout_total": pending_payout_total,
        "pending_payout_count": pending_payout_count,
        "merchant_commissions_payable": merchant_payable,
        "underwriter_commissions_payable": underwriter_payable,
        "salaries_due": salaries_due,
        "spin_rewards_pending": spin_pending,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Payout Batches
# ──────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def create_payout_batch(
    payout_type: str,
    created_by,
    notes: str = "",
) -> PayoutBatch:
    """Create a new empty payout batch."""
    batch = PayoutBatch.objects.create(
        payout_type=payout_type,
        status=PayoutBatch.STATUS_DRAFT,
        created_by=created_by,
        notes=notes,
    )
    PaymentAuditLog.objects.create(
        action="payout_batch_created",
        user=created_by,
        batch=batch,
        notes=f"Batch {batch.batch_number} created ({payout_type})",
    )
    logger.info("PayoutBatch created: %s by %s", batch.batch_number, created_by)
    return batch


@transaction.atomic
def submit_batch_for_approval(batch: PayoutBatch, user) -> PayoutBatch:
    """Submit a draft batch for HQ approval."""
    if batch.status != PayoutBatch.STATUS_DRAFT:
        raise ValueError(f"Batch {batch.batch_number} is not in draft status.")
    batch.recalculate_totals()
    batch.status = PayoutBatch.STATUS_PENDING_APPROVAL
    batch.save(update_fields=["status", "updated_at"])
    PaymentAuditLog.objects.create(
        action="payout_batch_submitted",
        user=user,
        batch=batch,
        notes=f"Batch {batch.batch_number} submitted for approval. Total: MWK {batch.total_amount}",
    )
    return batch


@transaction.atomic
def approve_payout_batch(batch: PayoutBatch, approver) -> PayoutBatch:
    """HQ approves a payout batch."""
    if batch.status not in [PayoutBatch.STATUS_PENDING_APPROVAL, PayoutBatch.STATUS_DRAFT]:
        raise ValueError(f"Batch {batch.batch_number} cannot be approved from status: {batch.status}")
    batch.approve(approver)
    logger.info("PayoutBatch approved: %s by %s", batch.batch_number, approver)
    return batch


@transaction.atomic
def process_payout_batch(batch: PayoutBatch, processor) -> dict:
    """
    Mark a batch as processing. Each item is marked processing.
    In production, would trigger actual PayChangu payout API calls.
    Returns summary dict.
    """
    if batch.status != PayoutBatch.STATUS_APPROVED:
        raise ValueError(f"Batch {batch.batch_number} must be approved before processing.")

    batch.status = PayoutBatch.STATUS_PROCESSING
    batch.processed_by = processor
    batch.processed_at = timezone.now()
    batch.save(update_fields=["status", "processed_by", "processed_at", "updated_at"])

    items = batch.items.filter(status=PayoutItem.STATUS_PENDING)
    count = 0
    for item in items:
        item.status = PayoutItem.STATUS_PROCESSING
        item.save(update_fields=["status", "updated_at"])
        count += 1

    PaymentAuditLog.objects.create(
        action="payout_batch_processing",
        user=processor,
        batch=batch,
        notes=f"Batch {batch.batch_number} processing started. {count} items queued.",
    )

    logger.info("PayoutBatch processing: %s — %d items", batch.batch_number, count)
    return {"batch": batch.batch_number, "items_queued": count}


# ──────────────────────────────────────────────────────────────────────────────
# Commission Accrual
# ──────────────────────────────────────────────────────────────────────────────

def get_active_commission_rule(earner_type: str) -> Optional[CommissionRule]:
    """Return the first active commission rule for the given earner type."""
    return CommissionRule.objects.filter(earner_type=earner_type, is_active=True).first()


@transaction.atomic
def accrue_underwriter_commission(
    payment: PaymentTransaction,
    contract,
    underwriter,
    rule: Optional[CommissionRule] = None,
) -> Optional[CommissionLedger]:
    """
    Accrue underwriter commission for a successful payment.
    Deposit payments are excluded by default (unless rule.applies_to_deposit).
    Returns CommissionLedger entry or None if not applicable.
    """
    if not rule:
        rule = get_active_commission_rule(CommissionRule.EARNER_UNDERWRITER)

    if not rule:
        logger.info("No active underwriter commission rule — skipping accrual for payment %s", payment.internal_reference)
        return None

    # Check deposit exclusion
    is_deposit = payment.payment_type == "deposit"
    if is_deposit and not rule.applies_to_deposit:
        logger.info("Underwriter commission: deposit excluded by rule — payment %s", payment.internal_reference)
        return None

    # Use commissionable_amount if set, else full amount
    base = payment.commissionable_amount if payment.commissionable_amount else payment.amount
    commission_amount = rule.calculate(base)

    if commission_amount <= 0:
        return None

    # Check idempotency — prevent double-accrual
    existing = CommissionLedger.objects.filter(
        source_payment=payment,
        user=underwriter,
        entry_type=CommissionLedger.ENTRY_REPAYMENT,
    ).first()
    if existing:
        logger.info("Underwriter commission already accrued for payment %s", payment.internal_reference)
        return existing

    entry = CommissionLedger.objects.create(
        user=underwriter,
        contract=contract,
        entry_type=CommissionLedger.ENTRY_REPAYMENT,
        amount=commission_amount,
        base_amount=base,
        rate=rule.percentage / 100 if rule.percentage else Decimal("0"),
        description=f"Commission on payment {payment.internal_reference} — MWK {base} @ {rule.percentage}%",
        source_payment=payment,
    )
    logger.info(
        "Underwriter commission accrued: %s — MWK %s for payment %s",
        underwriter, commission_amount, payment.internal_reference,
    )
    return entry


@transaction.atomic
def accrue_merchant_commission(
    payment: PaymentTransaction,
    contract,
    merchant,
    rule: Optional[CommissionRule] = None,
) -> Optional[CommissionLedger]:
    """
    Accrue merchant commission for a successful payment.
    """
    if not rule:
        rule = get_active_commission_rule(CommissionRule.EARNER_MERCHANT)

    if not rule:
        return None

    is_deposit = payment.payment_type == "deposit"
    if is_deposit and not rule.applies_to_deposit:
        return None

    base = payment.commissionable_amount if payment.commissionable_amount else payment.amount
    commission_amount = rule.calculate(base)

    if commission_amount <= 0:
        return None

    existing = CommissionLedger.objects.filter(
        source_payment=payment,
        merchant=merchant,
        entry_type=CommissionLedger.ENTRY_REPAYMENT,
    ).first()
    if existing:
        return existing

    entry = CommissionLedger.objects.create(
        user=merchant.user if hasattr(merchant, "user") else None,
        contract=contract,
        merchant=merchant,
        entry_type=CommissionLedger.ENTRY_REPAYMENT,
        amount=commission_amount,
        base_amount=base,
        rate=rule.percentage / 100 if rule.percentage else Decimal("0"),
        description=f"Merchant commission on payment {payment.internal_reference}",
        source_payment=payment,
    )
    return entry


# ──────────────────────────────────────────────────────────────────────────────
# Salary Scheduling
# ──────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def generate_salary_schedule_for_month(salary_month, created_by) -> list[SalarySchedule]:
    """
    Generate salary schedule entries for all active staff for the given month.
    Uses UserProfile.base_salary if available, else 0.
    """
    from accounts.models import UserProfile

    profiles = UserProfile.objects.select_related("user").filter(
        role__in=["hq", "underwriter", "merchant_admin", "tech_support"]
    )

    created = []
    for profile in profiles:
        user = profile.user
        if not user.is_active:
            continue

        base = getattr(profile, "base_salary", Decimal("0")) or Decimal("0")
        role_name = profile.get_role_display() if hasattr(profile, "get_role_display") else profile.role

        schedule, created_flag = SalarySchedule.objects.get_or_create(
            staff_user=user,
            salary_month=salary_month,
            defaults={
                "staff_name": user.get_full_name() or user.username,
                "role": role_name,
                "base_salary": base,
                "bonus_amount": Decimal("0"),
                "volts_bonus": Decimal("0"),
                "penalty_deduction": Decimal("0"),
                "phone_number": getattr(profile, "phone_number", "") or "",
                "payment_method": SalarySchedule.METHOD_AIRTEL,
                "status": SalarySchedule.STATUS_DRAFT,
                "created_by": created_by,
            },
        )
        if created_flag:
            created.append(schedule)

    PaymentAuditLog.objects.create(
        action="salary_schedule_generated",
        user=created_by,
        notes=f"Generated {len(created)} salary entries for {salary_month.strftime('%B %Y')}",
    )
    return created


# ──────────────────────────────────────────────────────────────────────────────
# Spin Reward Payouts
# ──────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def create_spin_reward_payout(spin_reward, phone_number: str, payment_method: str, created_by=None) -> SpinRewardPayout:
    """Create a pending payout for a spin reward win."""
    # Check if already exists
    existing = SpinRewardPayout.objects.filter(spin_reward=spin_reward).first()
    if existing:
        return existing

    payout = SpinRewardPayout.objects.create(
        spin_reward=spin_reward,
        user=spin_reward.user,
        amount=spin_reward.amount,
        phone_number=phone_number,
        payment_method=payment_method,
        status=SpinRewardPayout.STATUS_PENDING,
        created_by=created_by,
    )

    PaymentAuditLog.objects.create(
        action="spin_reward_payout_created",
        user=spin_reward.user,
        notes=f"Spin reward payout created: MWK {spin_reward.amount} to {phone_number}",
    )
    return payout


# ──────────────────────────────────────────────────────────────────────────────
# Notifications (stubs — integrate with SMS when available)
# ──────────────────────────────────────────────────────────────────────────────

def notify_payout_processing(recipient_name: str, amount: Decimal, method: str, phone: str) -> bool:
    """
    Notify a payout recipient that their payment is being processed.
    Returns True if notification was sent (or queued).
    """
    message = (
        f"TengaSale: We are processing your payment of MWK {amount:,.0f} "
        f"to {method} number {phone}. You will receive it shortly."
    )
    logger.info("NOTIFY payout_processing: %s — %s", recipient_name, message)
    # TODO: integrate with SMS when configured
    return True


def notify_payment_received(customer_name: str, amount: Decimal, contract_ref: str,
                            balance: Decimal, days: int = 0) -> bool:
    """Notify customer of successful incoming payment."""
    days_part = f" Days remaining: {days}." if days else ""
    message = (
        f"TengaSale: Payment received. MWK {amount:,.0f} applied to contract {contract_ref}. "
        f"Balance: MWK {balance:,.0f}.{days_part}"
    )
    logger.info("NOTIFY payment_received: %s — %s", customer_name, message)
    return True


def notify_salary_processing(staff_name: str, amount: Decimal, method: str, phone: str) -> bool:
    """Notify staff member that salary is being processed."""
    message = (
        f"TengaSale: We are processing your salary payment of MWK {amount:,.0f} "
        f"to {method} number {phone}."
    )
    logger.info("NOTIFY salary_processing: %s — %s", staff_name, message)
    return True


def notify_spin_reward(user_name: str, amount: Decimal, phone: str) -> bool:
    """Notify spin winner of their reward."""
    message = (
        f"Congratulations {user_name}! Your spin reward of MWK {amount:,.0f} "
        f"is being processed to {phone}."
    )
    logger.info("NOTIFY spin_reward: %s — %s", user_name, message)
    return True
