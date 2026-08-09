from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


class Wallet(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_earned = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_paid = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_wht_withheld = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(max_length=30, default="active")

    def __str__(self):
        return f"{self.user.username} wallet"

    @property
    def available_balance(self):
        return self.balance

    @property
    def pending_payout(self):
        return self.managerpayouts.filter(status="pending").aggregate(
            total=models.Sum("net_amount")
        )["total"] or Decimal("0")

    @property
    def paid_this_month(self):
        now = timezone.now()
        return self.managerpayouts.filter(
            status="paid",
            paid_at__year=now.year,
            paid_at__month=now.month,
        ).aggregate(total=models.Sum("net_amount"))["total"] or Decimal("0")


class EarningsSecurityEvent(models.Model):
    EVENT_CHOICES = [
        ("EARNINGS_LOCK_ENABLED", "Earnings lock enabled"),
        ("EARNINGS_LOCK_DISABLED", "Earnings lock disabled"),
        ("EARNINGS_UNLOCK_SUCCESS", "Earnings unlock succeeded"),
        ("EARNINGS_UNLOCK_FAILED", "Earnings unlock failed"),
        ("EARNINGS_PIN_CHANGED", "Earnings PIN changed"),
        ("EARNINGS_PIN_RESET", "Earnings PIN reset"),
        ("EARNINGS_RATE_LIMITED", "Earnings access rate limited"),
        ("EARNINGS_RECOVERY_STARTED", "Owner recovery started"),
        ("EARNINGS_RECOVERY_COMPLETED", "Owner recovery completed"),
        ("PAYOUT_REAUTH_SUCCESS", "Payout PIN reauthentication succeeded"),
    ]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="earnings_security_events")
    event_type = models.CharField(max_length=40, choices=EVENT_CHOICES)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "event_type", "created_at"], name="earn_sec_user_event_idx")]


class EarningsRecoveryChallenge(models.Model):
    PURPOSE_ENABLE = "enable"
    PURPOSE_RESET = "reset"
    PURPOSE_CHOICES = [(PURPOSE_ENABLE, "Enable lock"), (PURPOSE_RESET, "Reset PIN")]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="earnings_recovery_challenges")
    purpose = models.CharField(max_length=12, choices=PURPOSE_CHOICES)
    code_hash = models.CharField(max_length=255, editable=False)
    phone_mask = models.CharField(max_length=30)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "purpose", "created_at"], name="earn_recovery_lookup_idx")]


class WalletTransaction(models.Model):
    TYPE_CHOICES = [
        ("commission_credit", "Commission Credit"),
        ("commission", "Payment Commission"),
        ("payout", "Wallet Payout"),
        ("wallet_payout", "Wallet Payout"),
        ("payout_debit", "Payout Debit"),
        ("wht_deduction", "WHT Deduction"),
        ("tax", "Withholding Tax"),
        ("arrears_deduction", "Arrears Deduction"),
        ("adjustment", "Adjustment"),
        ("bonus", "Bonus"),
        ("manual_credit", "Manual Credit"),
        ("spin_reward", "Spin Reward"),
    ]

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="transactions")
    transaction_type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    description = models.CharField(max_length=255, blank=True)
    contract_number = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.wallet.user.username} - {self.transaction_type} - {self.amount}"


class ManagerPayout(models.Model):
    """
    Monthly payout record for manager / underwriter / agent.

    Business rule:
      - gross_amount = sum of commissions for the payout period
      - wht_amount   = gross_amount * wht_rate  (default 20%)
      - net_amount   = gross_amount - wht_amount
    WHT (Withholding Tax) applies to ALL manager/agent commission payouts.
    """

    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_PAID = "paid"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_PAID, "Paid"),
        (STATUS_FAILED, "Failed"),
    ]

    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.CASCADE,
        related_name="managerpayouts",
    )
    period_start = models.DateField()
    period_end = models.DateField()

    gross_amount = models.DecimalField(max_digits=14, decimal_places=2)
    wht_rate = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0.2000"))
    wht_amount = models.DecimalField(max_digits=14, decimal_places=2, editable=False)
    net_amount = models.DecimalField(max_digits=14, decimal_places=2, editable=False)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    destination_phone = models.CharField(max_length=20, blank=True)
    provider = models.CharField(max_length=40, blank=True)
    reference = models.CharField(max_length=100, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-period_end"]
        verbose_name = "Manager Payout"
        verbose_name_plural = "Manager Payouts"

    def save(self, *args, **kwargs):
        self.wht_amount = (self.gross_amount * self.wht_rate).quantize(Decimal("0.01"))
        self.net_amount = (self.gross_amount - self.wht_amount).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.wallet.user.username} | "
            f"{self.period_start}–{self.period_end} | "
            f"Gross {self.gross_amount} | Net {self.net_amount} | {self.status}"
        )

    def mark_paid(self, reference="", provider="", paid_at=None):
        self.status = self.STATUS_PAID
        self.reference = reference
        self.provider = provider
        self.paid_at = paid_at or timezone.now()
        self.save(update_fields=["status", "reference", "provider", "paid_at", "updated_at"])
        # Also update wallet totals
        self.wallet.total_paid = (self.wallet.total_paid or Decimal("0")) + self.net_amount
        self.wallet.total_wht_withheld = (
            self.wallet.total_wht_withheld or Decimal("0")
        ) + self.wht_amount
        self.wallet.balance = max(
            Decimal("0"),
            (self.wallet.balance or Decimal("0")) - self.gross_amount,
        )
        self.wallet.save(update_fields=["total_paid", "total_wht_withheld", "balance"])


class EmergencyPayoutRequest(models.Model):
    """
    Underwriter emergency payout request.

    Business rule: underwriters may request up to 20% of their earned-this-month
    commission as an emergency payout before the normal month-end payout.
    The remaining 80% stays for the normal salary/payout cycle.
    Only HQ / Finance / CFO roles may approve and pay these requests.
    """

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_PAID = "paid"
    STATUS_REJECTED = "rejected"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending HQ Approval"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_PAID, "Paid"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    EMERGENCY_PAYOUT_LIMIT_PERCENT = Decimal("0.20")

    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.CASCADE,
        related_name="emergency_payout_requests",
    )
    requested_amount = models.DecimalField(max_digits=14, decimal_places=2)
    reason = models.TextField(blank=True)
    payout_phone = models.CharField(max_length=20, blank=True)
    confirmed = models.BooleanField(
        default=False,
        help_text="Requester confirmed understanding of 20% emergency payout rule.",
    )

    # Snapshot of earnings at request time for audit
    earned_this_month_snapshot = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    emergency_limit_snapshot = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    already_requested_snapshot = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)

    # Approval / payment tracking
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_emergency_payouts",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)
    payment_reference = models.CharField(max_length=100, blank=True)
    payment_provider = models.CharField(max_length=40, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Emergency Payout Request"
        verbose_name_plural = "Emergency Payout Requests"

    def __str__(self):
        return (
            f"Emergency payout {self.wallet.user.username} | "
            f"MWK {self.requested_amount} | {self.status}"
        )

    @classmethod
    def get_monthly_emergency_limit(cls, wallet, year=None, month=None):
        """
        Calculate the available emergency payout limit for the given wallet.
        Returns a dict with keys: earned, limit, already_requested, available.
        """
        from django.utils import timezone

        now = timezone.now()
        year = year or now.year
        month = month or now.month

        # Sum of positive wallet transactions this month (commission credits)
        earned = wallet.transactions.filter(
            transaction_type__in=("commission_credit", "commission"),
            created_at__year=year,
            created_at__month=month,
            amount__gt=0,
        ).aggregate(total=models.Sum("amount"))["total"] or Decimal("0")

        limit = (earned * cls.EMERGENCY_PAYOUT_LIMIT_PERCENT).quantize(Decimal("0.01"))

        # Already requested (approved or paid) this month
        already_requested = cls.objects.filter(
            wallet=wallet,
            status__in=(cls.STATUS_APPROVED, cls.STATUS_PAID),
            created_at__year=year,
            created_at__month=month,
        ).aggregate(total=models.Sum("requested_amount"))["total"] or Decimal("0")

        available = max(Decimal("0"), limit - already_requested)

        return {
            "earned": earned,
            "limit": limit,
            "already_requested": already_requested,
            "available": available,
            "remaining_for_salary": max(Decimal("0"), earned - already_requested),
        }


class MerchantPayout(models.Model):
    """
    Payout to a merchant for the cash price of a device they sold on financing.

    Business rule:
      - Merchant receives the agreed cash price of the device.
      - NO WHT deduction for merchants in this workflow.
      - Payout is separate from manager/agent commission.
    """

    STATUS_PENDING = "pending"
    STATUS_EXTERNAL_PROCESSING = "external_processing"
    STATUS_PAID = "paid"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_EXTERNAL_PROCESSING, "External Processing"),
        (STATUS_PAID, "Paid"),
        (STATUS_FAILED, "Failed"),
    ]

    merchant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="merchant_payouts",
    )
    contract_number = models.CharField(max_length=100, blank=True)
    device_description = models.CharField(max_length=200, blank=True)
    cash_price = models.DecimalField(max_digits=14, decimal_places=2)
    deposit_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    financed_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    merchant_commission_rate = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0.0100"))
    merchant_commission_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    total_payable = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    paid_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))

    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_PENDING)
    provider = models.CharField(max_length=40, blank=True)
    reference = models.CharField(max_length=100, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Merchant Payout"
        verbose_name_plural = "Merchant Payouts"

    @property
    def wht_amount(self):
        return Decimal("0")

    @property
    def net_amount(self):
        return self.total_payable if self.total_payable else self.cash_price

    def __str__(self):
        return (
            f"Merchant {self.merchant.username} | "
            f"Contract {self.contract_number} | "
            f"Cash price {self.cash_price} | {self.status}"
        )

    def mark_paid(self, reference="", provider="", paid_at=None):
        self.status = self.STATUS_PAID
        self.reference = reference
        self.provider = provider
        self.paid_amount = self.cash_price
        self.paid_at = paid_at or timezone.now()
        self.save(
            update_fields=["status", "reference", "provider", "paid_amount", "paid_at", "updated_at"]
        )
