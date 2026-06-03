from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


class Commission(models.Model):
    ROLE_MERCHANT = "merchant"
    ROLE_MANAGER = "manager"

    ROLE_CHOICES = [
        (ROLE_MERCHANT, "Merchant"),
        (ROLE_MANAGER, "Manager"),
    ]

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_PAID = "paid"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_PAID, "Paid"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="commissions")
    application = models.ForeignKey(
        "applications.FinancingApplication",
        on_delete=models.CASCADE,
        related_name="commissions",
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    commission_percent = models.DecimalField(max_digits=5, decimal_places=2)
    sale_amount = models.DecimalField(max_digits=14, decimal_places=2)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["application", "user", "role"],
                name="unique_commission_per_application_user_role",
            ),
        ]

    def __str__(self):
        return f"{self.user} {self.role} commission for {self.application}"


class CommissionLedger(models.Model):
    """
    Double-entry style ledger for underwriter commissions and deductions.
    Positive amounts = earnings; negative = deductions.
    """

    ENTRY_REPAYMENT = "repayment_commission"
    ENTRY_ARREARS = "arrears_deduction"
    ENTRY_WHT = "monthly_wht_deduction"
    ENTRY_PAYOUT = "monthly_payout"
    ENTRY_ADJUSTMENT = "manual_adjustment"
    ENTRY_REVERSAL = "reversal"

    ENTRY_TYPE_CHOICES = [
        (ENTRY_REPAYMENT, "Repayment Commission"),
        (ENTRY_ARREARS, "Arrears Deduction"),
        (ENTRY_WHT, "Monthly WHT Deduction"),
        (ENTRY_PAYOUT, "Monthly Payout"),
        (ENTRY_ADJUSTMENT, "Manual Adjustment"),
        (ENTRY_REVERSAL, "Reversal"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="commission_ledger_entries",
    )
    contract = models.ForeignKey(
        "portal.PaymentContract",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="commission_ledger_entries",
    )
    application = models.ForeignKey(
        "applications.FinancingApplication",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="commission_ledger_entries",
    )
    merchant = models.ForeignKey(
        "merchants.Merchant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="commission_ledger_entries",
    )
    entry_type = models.CharField(max_length=30, choices=ENTRY_TYPE_CHOICES)
    amount = models.DecimalField(max_digits=14, decimal_places=2, help_text="Signed: positive=earn, negative=deduct")
    base_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    rate = models.DecimalField(max_digits=6, decimal_places=4, default=Decimal("0"))
    description = models.TextField(blank=True)
    source_payment = models.ForeignKey(
        "portal.PaymentTransaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="commission_ledger_entries",
    )
    missed_date = models.DateField(null=True, blank=True)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="commission_ledger_entries_created",
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Commission Ledger Entry"
        verbose_name_plural = "Commission Ledger"
        constraints = [
            models.UniqueConstraint(
                fields=["source_payment", "user", "entry_type"],
                condition=models.Q(entry_type="repayment_commission", source_payment__isnull=False),
                name="unique_repayment_commission_per_payment_user",
            ),
            models.UniqueConstraint(
                fields=["contract", "user", "missed_date", "entry_type"],
                condition=models.Q(entry_type="arrears_deduction", missed_date__isnull=False),
                name="unique_arrears_deduction_per_contract_user_date",
            ),
        ]

    def __str__(self):
        return f"{self.user_id} | {self.entry_type} | {self.amount} | {self.created_at.date()}"


class UnderwriterMonthlyPayout(models.Model):
    """
    Monthly payout record for an underwriter with 20% WHT applied to positive gross.
    Calculated from CommissionLedger entries for the period.
    """

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

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="monthly_payouts",
    )
    period_start = models.DateField()
    period_end = models.DateField()
    gross_commission = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    wht_rate = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0.2000"))
    wht_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"), editable=False)
    net_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"), editable=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    destination_phone = models.CharField(max_length=30, blank=True)
    destination_bank = models.CharField(max_length=120, blank=True)
    provider_reference = models.CharField(max_length=120, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-period_end"]
        unique_together = [("user", "period_start", "period_end")]
        verbose_name = "Underwriter Monthly Payout"
        verbose_name_plural = "Underwriter Monthly Payouts"

    def save(self, *args, **kwargs):
        gross = Decimal(self.gross_commission or 0)
        self.wht_amount = (max(gross, Decimal("0")) * self.wht_rate).quantize(Decimal("0.01"))
        self.net_amount = (gross - self.wht_amount).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)

    def mark_paid(self, reference="", paid_at=None):
        self.status = self.STATUS_PAID
        self.provider_reference = reference
        self.paid_at = paid_at or timezone.now()
        self.save(update_fields=["status", "provider_reference", "paid_at", "updated_at"])

    def __str__(self):
        return f"{self.user_id} | {self.period_start}–{self.period_end} | Net {self.net_amount} | {self.status}"


class MerchantContractPayout(models.Model):
    """
    Payout record linking a portal PaymentContract to a merchant.
    Merchant settlement due = cash_price. Optional 1% commission on financed amount is separate.
    """

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

    METHOD_BANK = "bank"
    METHOD_AIRTEL = "airtel_money"
    METHOD_MANUAL = "manual"

    PAYOUT_METHOD_CHOICES = [
        (METHOD_BANK, "Bank Transfer"),
        (METHOD_AIRTEL, "Airtel Money"),
        (METHOD_MANUAL, "Manual"),
    ]

    merchant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="contract_payouts",
    )
    contract = models.OneToOneField(
        "portal.PaymentContract",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="merchant_payout",
    )
    cash_price = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    deposit_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    financed_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    merchant_commission_rate = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0.0100"))
    merchant_commission_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    total_payable = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    payout_method = models.CharField(max_length=20, choices=PAYOUT_METHOD_CHOICES, default=METHOD_MANUAL)
    destination_name = models.CharField(max_length=120, blank=True)
    destination_account = models.CharField(max_length=120, blank=True)
    destination_phone = models.CharField(max_length=30, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    provider_reference = models.CharField(max_length=120, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="merchant_payouts_created",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Merchant Contract Payout"
        verbose_name_plural = "Merchant Contract Payouts"

    @property
    def merchant_settlement_due(self):
        """Phone cash price owed to merchant (excludes separate commission)."""
        return self.cash_price

    @property
    def wht_amount(self):
        return Decimal("0")

    def mark_paid(self, reference="", paid_at=None):
        self.status = self.STATUS_PAID
        self.provider_reference = reference
        self.paid_at = paid_at or timezone.now()
        self.save(update_fields=["status", "provider_reference", "paid_at", "updated_at"])

    def __str__(self):
        return f"Merchant payout | Contract {self.contract_id} | Total {self.total_payable} | {self.status}"
