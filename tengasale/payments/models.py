"""
TengaSale Payments App — central payment models.

Covers:
  - PayoutBatch / PayoutItem  — outgoing batch disbursements
  - CommissionRule             — configurable commission rates
  - SalarySchedule             — monthly payroll records
  - SpinRewardPayout           — spin reward disbursement tracking
  - PaymentApproval            — approval records for outgoing money
  - PaymentAuditLog            — comprehensive audit trail

Customer payment transactions live in portal.PaymentTransaction.
Commission ledger entries live in commissions.CommissionLedger.
"""
from __future__ import annotations

import secrets
import string
from decimal import Decimal

from django.conf import settings
from django.db import models


def generate_status_token():
    return secrets.token_urlsafe(24)
from django.utils import timezone


# ──────────────────────────────────────────────────────────────────────────────
# Reference generators
# ──────────────────────────────────────────────────────────────────────────────

def _make_ref(prefix: str, length: int = 8) -> str:
    chars = string.ascii_uppercase + string.digits
    return f"{prefix}-{timezone.now().strftime('%Y%m%d')}-{''.join(secrets.choice(chars) for _ in range(length))}"


def _batch_number():
    for _ in range(100):
        ref = f"BATCH-{timezone.now().strftime('%Y%m')}-{secrets.token_hex(3).upper()}"
        if not PayoutBatch.objects.filter(batch_number=ref).exists():
            return ref
    return f"BATCH-{secrets.token_hex(8).upper()}"


# ──────────────────────────────────────────────────────────────────────────────
# Commission Rule
# ──────────────────────────────────────────────────────────────────────────────

class CommissionRule(models.Model):
    """Configurable commission rule for merchants and underwriters."""

    EARNER_MERCHANT = "merchant"
    EARNER_UNDERWRITER = "underwriter"
    EARNER_STAFF = "staff"

    EARNER_CHOICES = [
        (EARNER_MERCHANT, "Merchant"),
        (EARNER_UNDERWRITER, "Underwriter"),
        (EARNER_STAFF, "Staff"),
    ]

    name = models.CharField(max_length=120)
    earner_type = models.CharField(max_length=20, choices=EARNER_CHOICES)
    percentage = models.DecimalField(
        max_digits=6, decimal_places=4, default=Decimal("0"),
        help_text="Commission percentage (e.g. 7.00 = 7%)",
    )
    fixed_amount = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0"),
        help_text="Fixed MWK amount per qualifying event (use 0 if percent-based)",
    )
    applies_to_deposit = models.BooleanField(default=False)
    applies_to_repayments = models.BooleanField(default=True)
    applies_to_contract_total = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="commission_rules_created",
    )

    class Meta:
        ordering = ["earner_type", "name"]
        verbose_name = "Commission Rule"
        verbose_name_plural = "Commission Rules"

    def calculate(self, amount: Decimal) -> Decimal:
        """Calculate commission for a given amount."""
        if self.fixed_amount > 0:
            return self.fixed_amount
        if self.percentage > 0:
            return (Decimal(str(amount)) * self.percentage / 100).quantize(Decimal("0.01"))
        return Decimal("0")

    def __str__(self):
        rate = f"{self.percentage}%" if self.percentage else f"MWK {self.fixed_amount}"
        return f"{self.name} ({self.earner_type}) — {rate}"


# ──────────────────────────────────────────────────────────────────────────────
# Payout Batch
# ──────────────────────────────────────────────────────────────────────────────

class PayoutBatch(models.Model):
    """A batch of outgoing payments grouped for approval and processing."""

    TYPE_MERCHANT = "merchant_commissions"
    TYPE_UNDERWRITER = "underwriter_commissions"
    TYPE_SALARIES = "salaries"
    TYPE_SPIN_REWARDS = "spin_rewards"
    TYPE_MIXED = "mixed"

    PAYOUT_TYPE_CHOICES = [
        (TYPE_MERCHANT, "Merchant Commissions"),
        (TYPE_UNDERWRITER, "Underwriter Commissions"),
        (TYPE_SALARIES, "Salaries"),
        (TYPE_SPIN_REWARDS, "Spin Rewards"),
        (TYPE_MIXED, "Mixed"),
    ]

    STATUS_DRAFT = "draft"
    STATUS_PENDING_APPROVAL = "pending_approval"
    STATUS_APPROVED = "approved"
    STATUS_PROCESSING = "processing"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_PENDING_APPROVAL, "Pending Approval"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    batch_number = models.CharField(max_length=40, unique=True, blank=True)
    payout_type = models.CharField(max_length=30, choices=PAYOUT_TYPE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    total_recipients = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="payout_batches_created",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="payout_batches_approved",
    )
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="payout_batches_processed",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Payout Batch"
        verbose_name_plural = "Payout Batches"

    def save(self, *args, **kwargs):
        if not self.batch_number:
            self.batch_number = _batch_number()
        super().save(*args, **kwargs)

    def recalculate_totals(self):
        agg = self.items.aggregate(
            total=models.Sum("amount"),
            count=models.Count("id"),
        )
        self.total_amount = agg["total"] or Decimal("0")
        self.total_recipients = agg["count"] or 0
        self.save(update_fields=["total_amount", "total_recipients", "updated_at"])

    def approve(self, user):
        self.status = self.STATUS_APPROVED
        self.approved_by = user
        self.approved_at = timezone.now()
        self.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
        PaymentAuditLog.objects.create(
            action="payout_batch_approved",
            user=user,
            batch=self,
            notes=f"Batch {self.batch_number} approved by {user}",
        )

    def __str__(self):
        return f"{self.batch_number} — {self.get_payout_type_display()} — {self.status}"


# ──────────────────────────────────────────────────────────────────────────────
# Payout Item
# ──────────────────────────────────────────────────────────────────────────────

class PayoutItem(models.Model):
    """Individual payout within a batch."""

    RECIPIENT_MERCHANT = "merchant"
    RECIPIENT_UNDERWRITER = "underwriter"
    RECIPIENT_STAFF = "staff"
    RECIPIENT_CUSTOMER = "customer"
    RECIPIENT_OTHER = "other"

    RECIPIENT_TYPE_CHOICES = [
        (RECIPIENT_MERCHANT, "Merchant"),
        (RECIPIENT_UNDERWRITER, "Underwriter"),
        (RECIPIENT_STAFF, "Staff"),
        (RECIPIENT_CUSTOMER, "Customer"),
        (RECIPIENT_OTHER, "Other"),
    ]

    METHOD_AIRTEL = "airtel_money"
    METHOD_TNM = "tnm_mpamba"
    METHOD_BANK = "bank_transfer"
    METHOD_CASH = "cash"
    METHOD_UNKNOWN = "unknown"

    PAYMENT_METHOD_CHOICES = [
        (METHOD_AIRTEL, "Airtel Money"),
        (METHOD_TNM, "TNM Mpamba"),
        (METHOD_BANK, "Bank Transfer"),
        (METHOD_CASH, "Cash"),
        (METHOD_UNKNOWN, "Unknown"),
    ]

    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_PAID = "paid"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_PAID, "Paid"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    batch = models.ForeignKey(PayoutBatch, on_delete=models.CASCADE, related_name="items")
    transaction = models.OneToOneField(
        "portal.PaymentTransaction", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="payout_item",
    )

    recipient_type = models.CharField(max_length=20, choices=RECIPIENT_TYPE_CHOICES)
    recipient_name = models.CharField(max_length=180)
    recipient_phone = models.CharField(max_length=30)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, default=METHOD_AIRTEL)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    failure_reason = models.TextField(blank=True)

    # Optional links to source records
    merchant = models.ForeignKey(
        "merchants.Merchant", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="payout_items",
    )
    underwriter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="underwriter_payout_items",
    )
    staff_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="staff_payout_items",
    )

    # Manual payment confirmation
    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="manually_paid_items",
    )
    proof_reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["batch", "recipient_name"]
        verbose_name = "Payout Item"
        verbose_name_plural = "Payout Items"

    def mark_paid(self, user=None, proof_reference="", notes=""):
        self.status = self.STATUS_PAID
        self.paid_at = timezone.now()
        if user:
            self.paid_by = user
        if proof_reference:
            self.proof_reference = proof_reference
        if notes:
            self.notes = notes
        self.save(update_fields=["status", "paid_at", "paid_by", "proof_reference", "notes", "updated_at"])
        PaymentAuditLog.objects.create(
            action="payout_item_manually_marked_paid",
            user=user,
            notes=f"Item {self.id} marked paid by {user}. Ref: {proof_reference}",
        )

    def __str__(self):
        return f"{self.recipient_name} — MWK {self.amount} ({self.status})"


# ──────────────────────────────────────────────────────────────────────────────
# Salary Schedule
# ──────────────────────────────────────────────────────────────────────────────

class SalarySchedule(models.Model):
    """Monthly payroll record for a staff member."""

    STATUS_DRAFT = "draft"
    STATUS_PENDING_APPROVAL = "pending_approval"
    STATUS_APPROVED = "approved"
    STATUS_PROCESSING = "processing"
    STATUS_PAID = "paid"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_PENDING_APPROVAL, "Pending Approval"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_PAID, "Paid"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    METHOD_AIRTEL = "airtel_money"
    METHOD_TNM = "tnm_mpamba"
    METHOD_BANK = "bank_transfer"
    METHOD_CASH = "cash"

    PAYMENT_METHOD_CHOICES = [
        (METHOD_AIRTEL, "Airtel Money"),
        (METHOD_TNM, "TNM Mpamba"),
        (METHOD_BANK, "Bank Transfer"),
        (METHOD_CASH, "Cash"),
    ]

    staff_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="salary_schedules",
    )
    staff_name = models.CharField(max_length=180)
    role = models.CharField(max_length=80, blank=True)
    salary_month = models.DateField(help_text="First day of the salary month (e.g. 2026-06-01)")

    base_salary = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    bonus_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    volts_bonus = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0"),
        help_text="Bonus from Volts earnings",
    )
    penalty_deduction = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    net_pay = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"), editable=False)

    phone_number = models.CharField(max_length=30, blank=True)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, default=METHOD_AIRTEL)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)

    # Links
    payout_batch = models.ForeignKey(
        PayoutBatch, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="salary_items",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="salary_schedules_created",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="salary_schedules_approved",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-salary_month", "staff_name"]
        unique_together = [("staff_user", "salary_month")]
        verbose_name = "Salary Schedule"
        verbose_name_plural = "Salary Schedules"

    def save(self, *args, **kwargs):
        self.net_pay = (
            Decimal(str(self.base_salary or 0))
            + Decimal(str(self.bonus_amount or 0))
            + Decimal(str(self.volts_bonus or 0))
            - Decimal(str(self.penalty_deduction or 0))
        ).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)

    def approve(self, user):
        self.status = self.STATUS_APPROVED
        self.approved_by = user
        self.approved_at = timezone.now()
        self.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])

    def __str__(self):
        return f"{self.staff_name} — {self.salary_month.strftime('%B %Y')} — MWK {self.net_pay} ({self.status})"


# ──────────────────────────────────────────────────────────────────────────────
# Spin Reward Payout
# ──────────────────────────────────────────────────────────────────────────────

class SpinRewardPayout(models.Model):
    """Payout record for a spin reward win. Links rewards.SpinReward to a payout."""

    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_PAID = "paid"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_PAID, "Paid"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    METHOD_AIRTEL = "airtel_money"
    METHOD_TNM = "tnm_mpamba"
    METHOD_BANK = "bank_transfer"

    PAYMENT_METHOD_CHOICES = [
        (METHOD_AIRTEL, "Airtel Money"),
        (METHOD_TNM, "TNM Mpamba"),
        (METHOD_BANK, "Bank Transfer"),
    ]

    spin_reward = models.OneToOneField(
        "rewards.SpinReward", on_delete=models.CASCADE, related_name="payout",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="spin_reward_payouts",
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    phone_number = models.CharField(max_length=30, blank=True)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, default=METHOD_AIRTEL)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)

    payout_batch = models.ForeignKey(
        PayoutBatch, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="spin_reward_payouts",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="spin_payouts_created",
    )
    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="spin_payouts_paid",
    )
    proof_reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    failure_reason = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Spin Reward Payout"
        verbose_name_plural = "Spin Reward Payouts"

    def mark_paid(self, user=None, proof_reference=""):
        self.status = self.STATUS_PAID
        self.paid_at = timezone.now()
        self.paid_by = user
        self.proof_reference = proof_reference
        self.save(update_fields=["status", "paid_at", "paid_by", "proof_reference", "updated_at"])

    def __str__(self):
        return f"Spin reward {self.spin_reward_id} — MWK {self.amount} — {self.status}"


# ──────────────────────────────────────────────────────────────────────────────
# Payment Approval
# ──────────────────────────────────────────────────────────────────────────────

class PaymentApproval(models.Model):
    """Approval record for outgoing payments requiring authorization."""

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_WITHDRAWN = "withdrawn"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_WITHDRAWN, "Withdrawn"),
    ]

    batch = models.ForeignKey(
        PayoutBatch, on_delete=models.CASCADE, null=True, blank=True,
        related_name="approvals",
    )
    salary_schedule = models.ForeignKey(
        SalarySchedule, on_delete=models.CASCADE, null=True, blank=True,
        related_name="approvals",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="payment_approvals_requested",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="payment_approvals_given",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    notes = models.TextField(blank=True)
    rejection_reason = models.TextField(blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))

    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Payment Approval"
        verbose_name_plural = "Payment Approvals"

    def approve(self, user, notes=""):
        self.status = self.STATUS_APPROVED
        self.approved_by = user
        self.approved_at = timezone.now()
        if notes:
            self.notes = notes
        self.save(update_fields=["status", "approved_by", "approved_at", "notes", "updated_at"])

    def reject(self, user, reason=""):
        self.status = self.STATUS_REJECTED
        self.approved_by = user
        self.rejection_reason = reason
        self.save(update_fields=["status", "approved_by", "rejection_reason", "updated_at"])

    def __str__(self):
        return f"Approval {self.id} — {self.status} — MWK {self.amount}"


# ──────────────────────────────────────────────────────────────────────────────
# Payment Audit Log
# ──────────────────────────────────────────────────────────────────────────────

class PaymentAuditLog(models.Model):
    """Audit trail for all significant payment actions."""

    action = models.CharField(max_length=80, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="payment_audit_logs",
    )
    # Link to portal PaymentTransaction
    transaction_ref = models.CharField(max_length=40, blank=True, db_index=True,
        help_text="portal.PaymentTransaction.internal_reference")
    batch = models.ForeignKey(
        PayoutBatch, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="audit_logs",
    )
    notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Payment Audit Log"
        verbose_name_plural = "Payment Audit Log"

    def __str__(self):
        return f"{self.action} | {self.user_id} | {self.created_at.strftime('%Y-%m-%d %H:%M')}"


# ──────────────────────────────────────────────────────────────────────────────
# Payment Summary (virtual — computed, not stored)
# ──────────────────────────────────────────────────────────────────────────────
# HQ dashboard summary data is computed in views from existing models.
# No additional stored model needed for that.


class AirtelTransaction(models.Model):
    """Direct Airtel Money transaction audit record."""

    PURPOSE_DEPOSIT = "DEPOSIT"
    PURPOSE_INSTALLMENT = "INSTALLMENT"
    PURPOSE_ARREARS = "ARREARS"
    PURPOSE_UNLOCK = "UNLOCK"
    PURPOSE_REFUND = "REFUND"
    PURPOSE_COMMISSION = "COMMISSION"
    PURPOSE_TEST = "TEST"

    PURPOSE_CHOICES = [
        (PURPOSE_DEPOSIT, "Deposit"),
        (PURPOSE_INSTALLMENT, "Installment"),
        (PURPOSE_ARREARS, "Arrears"),
        (PURPOSE_UNLOCK, "Unlock"),
        (PURPOSE_REFUND, "Refund"),
        (PURPOSE_COMMISSION, "Commission"),
        (PURPOSE_TEST, "Test"),
    ]

    DIRECTION_COLLECTION = "COLLECTION"
    DIRECTION_DISBURSEMENT = "DISBURSEMENT"

    DIRECTION_CHOICES = [
        (DIRECTION_COLLECTION, "Collection"),
        (DIRECTION_DISBURSEMENT, "Disbursement"),
    ]

    STATUS_INITIATED = "INITIATED"
    STATUS_PENDING = "PENDING"
    STATUS_DRY_RUN = "DRY_RUN"
    STATUS_SUCCESS = "SUCCESS"
    STATUS_FAILED = "FAILED"
    STATUS_REVERSED = "REVERSED"
    STATUS_EXPIRED = "EXPIRED"
    STATUS_UNKNOWN = "UNKNOWN"

    STATUS_CHOICES = [
        (STATUS_INITIATED, "Initiated"),
        (STATUS_PENDING, "Pending"),
        (STATUS_DRY_RUN, "Dry run"),
        (STATUS_SUCCESS, "Success"),
        (STATUS_FAILED, "Failed"),
        (STATUS_REVERSED, "Reversed"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_UNKNOWN, "Unknown"),
    ]

    internal_reference = models.CharField(max_length=64, unique=True, db_index=True)
    idempotency_key = models.CharField(max_length=120, null=True, blank=True, unique=True)
    status_token = models.CharField(max_length=64, default=generate_status_token, unique=True, editable=False, null=True)
    environment = models.CharField(max_length=20, default="staging", db_index=True)
    provider = models.CharField(max_length=30, default="airtel_money", db_index=True)
    provider_reference = models.CharField(max_length=120, null=True, blank=True, db_index=True)
    airtel_money_id = models.CharField(max_length=120, null=True, blank=True, db_index=True)
    airtel_transaction_id = models.CharField(max_length=120, null=True, blank=True, db_index=True)
    airtel_reference_id = models.CharField(max_length=120, null=True, blank=True, db_index=True)
    customer_msisdn = models.CharField(max_length=30)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=5, default="MWK")
    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES)
    direction = models.CharField(max_length=20, choices=DIRECTION_CHOICES, default=DIRECTION_COLLECTION)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_INITIATED)
    raw_request = models.JSONField(default=dict, blank=True)
    raw_response = models.JSONField(default=dict, blank=True)
    raw_callback = models.JSONField(default=dict, blank=True)
    callback_verified = models.BooleanField(default=False)
    callback_received_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(blank=True)
    contract = models.ForeignKey(
        "portal.PaymentContract",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="airtel_transactions",
    )
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="airtel_transactions",
    )
    payment_transaction = models.OneToOneField(
        "portal.PaymentTransaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="airtel_transaction",
    )
    repayment_posted = models.BooleanField(default=False)
    full_repayment_days_covered = models.PositiveIntegerField(default=0)
    partial_credit_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    duplicate_callback = models.BooleanField(default=False)
    processed_success_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    processing_note = models.TextField(blank=True)
    initiation_accepted_at = models.DateTimeField(null=True, blank=True)
    provider_id_confirmed = models.BooleanField(default=False)
    provider_identifier_source = models.CharField(max_length=120, blank=True)
    last_enquiry_at = models.DateTimeField(null=True, blank=True)
    enquiry_attempt_count = models.PositiveIntegerField(default=0)
    last_enquiry_reference = models.CharField(max_length=120, blank=True)
    last_enquiry_path = models.CharField(max_length=255, blank=True)
    last_enquiry_status = models.CharField(max_length=20, blank=True)
    last_enquiry_error = models.TextField(blank=True)
    last_enquiry_response = models.JSONField(default=dict, blank=True)
    reconciliation_required = models.BooleanField(default=False)
    reconciliation_completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["purpose", "direction"]),
            models.Index(fields=["environment", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["airtel_money_id"],
                condition=models.Q(airtel_money_id__isnull=False) & ~models.Q(airtel_money_id=""),
                name="uniq_airtel_money_id_when_present",
            ),
            models.UniqueConstraint(
                fields=["airtel_transaction_id"],
                condition=models.Q(airtel_transaction_id__isnull=False) & ~models.Q(airtel_transaction_id=""),
                name="uniq_airtel_tx_id_when_present",
            ),
        ]

    def __str__(self):
        return f"{self.internal_reference} - {self.status}"


class AirtelCallbackLog(models.Model):
    """Raw Airtel callback log retained for support and reconciliation."""

    transaction = models.ForeignKey(
        AirtelTransaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="callback_logs",
    )
    received_headers = models.JSONField(default=dict, blank=True)
    raw_body = models.TextField()
    parsed_body = models.JSONField(null=True, blank=True)
    signature_valid = models.BooleanField(default=False)
    processed = models.BooleanField(default=False)
    duplicate = models.BooleanField(default=False)
    processing_error = models.TextField(null=True, blank=True)
    request_path = models.CharField(max_length=255, blank=True)
    query_string = models.TextField(blank=True)
    request_method = models.CharField(max_length=12, blank=True)
    content_type = models.CharField(max_length=120, blank=True)
    body_size = models.PositiveIntegerField(default=0)
    source_ip = models.GenericIPAddressField(null=True, blank=True)
    forwarded_for = models.TextField(blank=True)
    real_ip = models.CharField(max_length=64, blank=True)
    user_agent = models.TextField(blank=True)
    request_id = models.CharField(max_length=120, blank=True, db_index=True)
    provider_request_id = models.CharField(max_length=120, blank=True, db_index=True)
    signature_present = models.BooleanField(default=False)
    authentication_mode = models.CharField(max_length=40, blank=True)
    processing_state = models.CharField(max_length=40, default="RECEIVED", db_index=True)
    body_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    candidate_identifiers = models.JSONField(default=list, blank=True)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    response_body = models.JSONField(default=dict, blank=True)
    matched_identifier = models.CharField(max_length=120, blank=True)
    matched_field = models.CharField(max_length=80, blank=True)
    matching_details = models.JSONField(default=dict, blank=True)
    extracted_status = models.CharField(max_length=20, blank=True)
    extracted_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    provider_transaction_id = models.CharField(max_length=120, blank=True)
    error_class = models.CharField(max_length=120, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    processing_duration_ms = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["response_status", "created_at"]),
            models.Index(fields=["processing_state", "created_at"]),
        ]

    def __str__(self):
        ref = self.transaction.internal_reference if self.transaction_id else "unmatched"
        return f"Airtel callback {ref} - {self.created_at}"


class USSDPaymentIntent(models.Model):
    SOURCE_CHOICES = [
        ("USSD", "USSD"),
    ]

    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("PROCESSING", "Processing"),
        ("CONFIRMED", "Confirmed"),
        ("FAILED", "Failed"),
        ("CANCELLED", "Cancelled"),
    ]

    phone_number = models.CharField(max_length=32)
    session_id = models.CharField(max_length=128, db_index=True)
    service_code = models.CharField(max_length=64, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    contract_number = models.CharField(max_length=64, db_index=True)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="USSD")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")
    raw_text = models.TextField(blank=True)
    provider_reference = models.CharField(max_length=128, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.phone_number} - {self.contract_number} - MWK {self.amount}"


class USSDSessionLog(models.Model):
    session_id = models.CharField(max_length=128, db_index=True)
    service_code = models.CharField(max_length=64, blank=True)
    phone_number = models.CharField(max_length=32, blank=True)
    text = models.TextField(blank=True)
    response = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.phone_number} - {self.text}"
