"""
Portal models — customer payment portal.

PaymentContract is the payment portal record (internal contract_number: TS-MW-XXXXXXXX).
payg_number is the customer PayG payment reference (EXXXXXXX — 8 chars, starts with E).

Customer-facing legal contract numbers (A + 7 chars) live on contracts.Contract.contract_number —
never reuse the PayG E-prefix generator for those.
PaymentTransaction records each individual payment attempt.
"""

import random
import secrets
import string

from django.db import models
from django.utils import timezone

# Characters used in PayG numbers: uppercase A-Z (excl. I, O) + digits 2-9
# Excludes confusing look-alike characters: I (vs 1), O (vs 0), 0, 1
_PAYG_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_contract_number():
    """Generate a unique TS-MW-XXXXXXXX contract number."""
    for _ in range(100):
        suffix = "".join(random.choices(string.digits, k=8))
        number = f"TS-MW-{suffix}"
        if not PaymentContract.objects.filter(contract_number=number).exists():
            return number
    raise RuntimeError("Could not generate a unique contract number after 100 attempts.")


def generate_payg_number():
    """
    Generate a unique PayG contract number in the format EXXXXXXX.

    - Exactly 8 characters.
    - Always starts with uppercase 'E'.
    - Remaining 7 characters are uppercase letters (A-Z, excl. I, O) and
      digits (2-9, excl. 0, 1) to avoid look-alike confusion.
    - Retries up to 50 times; raises RuntimeError if all attempts collide.
    """
    for _ in range(50):
        suffix = "".join(random.choices(_PAYG_CHARS, k=7))
        number = f"E{suffix}"
        if not PaymentContract.objects.filter(payg_number=number).exists():
            return number
    raise RuntimeError("Could not generate a unique PayG number after 50 attempts.")


def generate_payment_reference():
    """Generate a unique TS-PAY-XXXXXXXX internal payment reference."""
    for _ in range(100):
        suffix = "".join(random.choices(string.digits + string.ascii_uppercase, k=8))
        ref = f"TS-PAY-{suffix}"
        if not PaymentTransaction.objects.filter(internal_reference=ref).exists():
            return ref
    return f"TS-PAY-{secrets.token_urlsafe(8)[:8].upper()}"


class PaymentContract(models.Model):
    """Public-facing financing contract that customers pay against."""

    STATUS_ACTIVE = "active"
    STATUS_OVERDUE = "overdue"
    STATUS_LOCKED = "locked"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_OVERDUE, "Overdue"),
        (STATUS_LOCKED, "Locked"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    contract_number = models.CharField(max_length=15, unique=True, blank=True)
    payg_number = models.CharField(
        max_length=8, unique=True, blank=True,
        help_text="Customer-facing PayG reference — format EXXXXXXX (8 chars, starts with E)",
        db_index=True,
    )

    # Link to existing financing contract (optional — can also stand alone for demo)
    financing_contract = models.OneToOneField(
        "financing.FinancingContract",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_contract",
    )

    # Link to the source financing application (TengaSale underwriter flow)
    source_application = models.OneToOneField(
        "applications.FinancingApplication",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_contract",
    )

    # Customer info (denormalised for portal access without auth)
    customer_name = models.CharField(max_length=180)
    customer_phone = models.CharField(max_length=30)
    customer_national_id = models.CharField(max_length=80, blank=True)
    device_model = models.CharField(max_length=120, blank=True)
    imei_number = models.CharField(
        max_length=20, blank=True, default="",
        help_text="Device IMEI — stored directly for fast portal lookup",
    )

    # Financials
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    deposit_required = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text="Required deposit amount for this contract",
    )
    deposit_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    daily_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    thirty_day_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Dates
    start_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(null=True, blank=True)
    lock_date = models.DateField(null=True, blank=True)
    term_months = models.PositiveIntegerField(default=12)

    # Status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)

    # Lock provider references (for MDM / PayG integration)
    lock_reference = models.CharField(
        max_length=120, blank=True, default="",
        help_text="Internal reference sent to lock provider at enrollment",
    )
    provider_contract_reference = models.CharField(
        max_length=120, blank=True, default="",
        help_text="Contract reference assigned by lock provider",
    )
    provider_device_reference = models.CharField(
        max_length=120, blank=True, default="",
        help_text="Device reference assigned by lock provider",
    )
    provider_metadata = models.JSONField(
        default=dict, blank=True,
        help_text=(
            "Metadata sent to/from lock provider, e.g. "
            '{"payg_number": "EXGH4456", "imei": "...", "payment_url": "..."}'
        ),
    )

    # Device locking (for PayG / MDM integration)
    LOCK_PROVIDER_NONE = ""
    LOCK_PROVIDER_MOCK = "mock"
    LOCK_PROVIDER_KNOX = "knox"
    LOCK_PROVIDER_NUOVOPAY = "nuovopay"
    LOCK_PROVIDER_UPYA = "upya"

    LOCK_STATUS_UNKNOWN = "unknown"
    LOCK_STATUS_UNLOCKED = "unlocked"
    LOCK_STATUS_LOCKED = "locked"
    LOCK_STATUS_PENDING = "pending"

    ENROLLMENT_NONE = "none"
    ENROLLMENT_PENDING = "pending"
    ENROLLMENT_ENROLLED = "enrolled"
    ENROLLMENT_FAILED = "failed"

    device_lock_provider = models.CharField(
        max_length=30, blank=True, default="",
        help_text="MDM/lock provider: mock, knox, nuovopay, upya, or empty for none",
    )
    device_lock_status = models.CharField(
        max_length=20, blank=True, default=LOCK_STATUS_UNKNOWN,
        help_text="Current lock state: unknown, unlocked, locked, pending",
    )
    device_enrollment_status = models.CharField(
        max_length=20, blank=True, default=ENROLLMENT_NONE,
        help_text="MDM enrollment state: none, pending, enrolled, failed",
    )
    last_lock_sync_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Last time lock status was synced with provider",
    )
    last_lock_error = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Last error message from lock provider",
    )

    # Pricing config
    early_settlement_3m_discount = models.DecimalField(
        max_digits=5, decimal_places=2, default=25,
        help_text="Percentage discount for 3-month early settlement",
    )
    early_settlement_6m_discount = models.DecimalField(
        max_digits=5, decimal_places=2, default=15,
        help_text="Percentage discount for 6-month early settlement",
    )
    early_settlement_9m_discount = models.DecimalField(
        max_digits=5, decimal_places=2, default=8,
        help_text="Percentage discount for 9-month early settlement",
    )

    # ── Underwriting / Behaviour Analytics (auto-refreshed on payment) ───────
    # Stored so HQ can query without re-computing across all contracts.
    analytics_payment_count = models.PositiveIntegerField(default=0)
    analytics_average_payment = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    analytics_largest_payment = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    analytics_smallest_payment = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    analytics_consistency = models.PositiveSmallIntegerField(
        default=0, help_text="Payment consistency 0–100 (days covered / days since start)"
    )
    analytics_discipline = models.CharField(
        max_length=30, blank=True, default="",
        help_text="Excellent / Good / Fair / Needs Improvement"
    )
    analytics_avg_interval_days = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Average days between payments"
    )
    analytics_days_late = models.PositiveIntegerField(
        default=0, help_text="Total days currently behind schedule"
    )
    analytics_preferred_provider = models.CharField(
        max_length=30, blank=True, default="", help_text="Most-used payment provider"
    )
    analytics_preferred_day = models.CharField(
        max_length=15, blank=True, default="", help_text="Day-of-week customer most often pays"
    )
    analytics_updated_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["contract_number"]),
            models.Index(fields=["customer_phone"]),
            models.Index(fields=["customer_national_id"]),
            models.Index(fields=["imei_number"]),
        ]

    def save(self, *args, **kwargs):
        if not self.contract_number:
            self.contract_number = generate_contract_number()
        if not self.payg_number:
            self.payg_number = generate_payg_number()
        super().save(*args, **kwargs)

    def __str__(self):
        payg = self.payg_number or self.contract_number
        return f"{payg} — {self.customer_name}"

    @property
    def deposit_remaining(self):
        """Amount still owed to complete the deposit."""
        from decimal import Decimal
        if not self.deposit_required:
            return Decimal("0")
        return max(self.deposit_required - self.deposit_paid, Decimal("0"))

    @property
    def deposit_complete(self):
        """True when the required deposit has been fully collected."""
        return self.deposit_remaining <= 0

    @property
    def remaining_amount(self):
        from decimal import Decimal
        return max(self.total_amount - self.amount_paid, Decimal("0"))

    @property
    def progress_percent(self):
        if not self.total_amount:
            return 0
        return min(100, round((self.amount_paid / self.total_amount) * 100))

    @property
    def masked_phone(self):
        p = str(self.customer_phone)
        if len(p) >= 6:
            return p[:3] + "***" + p[-3:]
        return p[:2] + "***"


class PaymentTransaction(models.Model):
    """Individual payment transaction against a PaymentContract."""

    PROVIDER_MOCK = "mock"
    PROVIDER_PAYCHANGU = "paychangu"
    PROVIDER_AIRTEL = "airtel_money"
    PROVIDER_TNM = "tnm_mpamba"
    PROVIDER_PAYTRIGGER = "paytrigger"

    PROVIDER_CHOICES = [
        (PROVIDER_MOCK, "Mock / Sandbox"),
        (PROVIDER_PAYCHANGU, "PayChangu"),
        (PROVIDER_AIRTEL, "Airtel Money"),
        (PROVIDER_TNM, "TNM Mpamba"),
        (PROVIDER_PAYTRIGGER, "PayTrigger"),
    ]

    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "external_processing"
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

    TYPE_DEPOSIT = "deposit"
    TYPE_REPAYMENT = "repayment"
    TYPE_PENALTY = "penalty"
    TYPE_ADJUSTMENT = "adjustment"
    TYPE_REFUND = "refund"

    PAYMENT_TYPE_CHOICES = [
        (TYPE_DEPOSIT, "Deposit"),
        (TYPE_REPAYMENT, "Repayment"),
        (TYPE_PENALTY, "Penalty"),
        (TYPE_ADJUSTMENT, "Adjustment"),
        (TYPE_REFUND, "Refund"),
    ]

    NETWORK_AIRTEL = "airtel"
    NETWORK_TNM = "tnm"
    NETWORK_OTHER = "other"
    NETWORK_CHOICES = [
        (NETWORK_AIRTEL, "Airtel Money"),
        (NETWORK_TNM, "TNM Mpamba"),
        (NETWORK_OTHER, "Other"),
    ]

    payment_contract = models.ForeignKey(
        PaymentContract,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, default=PROVIDER_MOCK)
    payment_type = models.CharField(
        max_length=15, choices=PAYMENT_TYPE_CHOICES, default=TYPE_REPAYMENT,
        help_text="DEPOSIT for initial deposit, REPAYMENT for regular instalments, etc.",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    commissionable_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Amount eligible for underwriter commission (excludes deposit)",
    )
    currency = models.CharField(max_length=5, default="MWK")
    phone = models.CharField(max_length=30)

    # Network: the mobile money operator (airtel / tnm) — separate from gateway provider
    network = models.CharField(
        max_length=10, choices=NETWORK_CHOICES, default=NETWORK_OTHER, blank=True,
        help_text="Mobile money network used: airtel, tnm, or other",
    )

    internal_reference = models.CharField(max_length=30, unique=True, blank=True)
    provider_reference = models.CharField(max_length=120, blank=True)

    # For PayChangu MoMo: charge_id is separate from tx_ref
    charge_id = models.CharField(
        max_length=120, blank=True, default="",
        help_text="PayChangu MoMo charge_id (used for verification calls)",
    )

    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default=STATUS_PENDING)

    # Balance snapshots (recorded at payment time for receipt / audit)
    balance_before = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Contract balance before this payment was applied",
    )
    balance_after = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Contract balance after this payment was applied",
    )

    # Audit payloads
    raw_request = models.JSONField(
        default=dict, blank=True,
        help_text="Raw payload sent to payment provider",
    )
    raw_response = models.JSONField(default=dict, blank=True)
    webhook_payload = models.JSONField(
        default=dict, blank=True,
        help_text="Raw payload received from provider webhook",
    )

    initiated_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When the payment was first initiated with the provider",
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.internal_reference:
            self.internal_reference = generate_payment_reference()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.internal_reference} — MWK {self.amount} [{self.status}]"

    @property
    def masked_phone(self):
        p = str(self.phone)
        if len(p) >= 6:
            return p[:3] + "***" + p[-3:]
        return p[:2] + "***"
