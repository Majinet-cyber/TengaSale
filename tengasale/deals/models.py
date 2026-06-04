from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class DeviceBrand(models.Model):
    name = models.CharField(max_length=100, unique=True)
    logo = models.ImageField(upload_to="brand_logos/", blank=True, null=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class DeviceDeal(models.Model):
    CONDITION_NEW = "new"
    CONDITION_USED = "used"
    CONDITION_REFURBISHED = "refurbished"
    CONDITION_CHOICES = [
        (CONDITION_NEW, "New"),
        (CONDITION_USED, "Used"),
        (CONDITION_REFURBISHED, "Refurbished"),
    ]

    STOCK_IN = "in_stock"
    STOCK_LIMITED = "limited"
    STOCK_OUT = "out_of_stock"
    STOCK_CHOICES = [
        (STOCK_IN, "In Stock"),
        (STOCK_LIMITED, "Limited Stock"),
        (STOCK_OUT, "Out of Stock"),
    ]

    LOCK_NONE = ""
    LOCK_KNOX = "knox"
    LOCK_NUOVO = "nuovo"
    LOCK_UPYA = "upya"
    LOCK_PREDIKT = "predikt"
    LOCK_PARETIX = "paretix"
    LOCK_TRANSUNION = "transunion"
    LOCK_MOCK = "mock"
    LOCK_OTHER = "other"
    LOCK_CHOICES = [
        ("", "None / Not Configured"),
        ("mock", "Mock / Sandbox"),
        ("knox", "Knox (Samsung)"),
        ("nuovo", "NuovoPay"),
        ("upya", "Upya"),
        ("predikt", "Predikt"),
        ("paretix", "Paretix"),
        ("transunion", "TransUnion"),
        ("other", "Other"),
    ]

    brand = models.ForeignKey(DeviceBrand, on_delete=models.CASCADE, related_name="deals")
    model_name = models.CharField(max_length=120)
    specs = models.CharField(max_length=80)
    condition = models.CharField(max_length=20, choices=CONDITION_CHOICES, default=CONDITION_NEW)
    country = models.CharField(max_length=10, default="MW")

    cash_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    min_cash_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    max_cash_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    default_cash_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    deposit_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=13,
        validators=[MinValueValidator(Decimal("13")), MaxValueValidator(Decimal("16"))],
    )
    loan_multiplier = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("2.5"))
    term_months = models.PositiveIntegerField(default=12)
    unlock_days = models.PositiveIntegerField(
        default=14,
        help_text="Number of days of device access granted by the deposit payment.",
    )

    total_12_month_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)
    popularity_score = models.PositiveIntegerField(default=0)

    # Commission rates
    merchant_commission_rate = models.DecimalField(
        max_digits=6, decimal_places=4, default=Decimal("0.0100"),
        help_text="Merchant commission rate (e.g. 0.01 = 1%)",
    )
    underwriter_commission_rate = models.DecimalField(
        max_digits=6, decimal_places=4, default=Decimal("0.0700"),
        help_text="Underwriter commission rate (e.g. 0.07 = 7%)",
    )
    arrears_penalty_rate = models.DecimalField(
        max_digits=6, decimal_places=4, default=Decimal("0.1400"),
        help_text="Arrears/penalty deduction rate (e.g. 0.14 = 14%)",
    )

    # Operations
    stock_status = models.CharField(max_length=20, choices=STOCK_CHOICES, default=STOCK_IN)
    is_lock_ready = models.BooleanField(default=True)
    lock_provider = models.CharField(max_length=20, blank=True, default="")
    catalog_source = models.CharField(max_length=60, blank=True, default="", db_index=True)
    notes = models.TextField(blank=True)

    # Audit
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="deals_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="deals_updated",
    )
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    def calculated_total_loan(self, cash_price=None):
        selected_price = Decimal(cash_price) if cash_price is not None else self.default_cash_price
        return round(selected_price * self.loan_multiplier, 2)

    def calculated_deposit(self, cash_price=None):
        total_loan = self.calculated_total_loan(cash_price)
        return round((total_loan * self.deposit_percent) / Decimal("100"), 2)

    def calculated_monthly_payment(self, cash_price=None, term_months=None):
        total_loan = self.calculated_total_loan(cash_price)
        term = int(term_months or self.term_months or 12)
        if term <= 0:
            term = 12
        return round(total_loan / Decimal(term), 2)

    def calculated_daily_payment(self, cash_price=None, term_months=None):
        total_loan = self.calculated_total_loan(cash_price)
        term = int(term_months or self.term_months or 12)
        if term <= 0:
            term = 12
        return round(total_loan / Decimal(term * 30), 2)

    def calculated_6_month_total(self, cash_price=None):
        return round(self.calculated_total_loan(cash_price) * Decimal("0.85"), 2)

    def calculated_6_month_monthly(self, cash_price=None):
        return round(self.calculated_6_month_total(cash_price) / Decimal("6"), 2)

    def calculated_6_month_daily(self, cash_price=None):
        return round(self.calculated_6_month_total(cash_price) / Decimal("180"), 2)

    def calculated_3_month_total(self, cash_price=None):
        return round(self.calculated_total_loan(cash_price) * Decimal("0.75"), 2)

    def calculated_3_month_monthly(self, cash_price=None):
        return round(self.calculated_3_month_total(cash_price) / Decimal("3"), 2)

    def calculated_3_month_daily(self, cash_price=None):
        return round(self.calculated_3_month_total(cash_price) / Decimal("90"), 2)

    @property
    def deposit_amount(self):
        return self.calculated_deposit(self.default_cash_price or self.cash_price)

    @property
    def monthly_payment(self):
        return self.calculated_monthly_payment(self.default_cash_price or self.cash_price)

    @property
    def daily_payment(self):
        return self.calculated_daily_payment(self.default_cash_price or self.cash_price)

    @property
    def weekly_payment(self):
        return round(self.daily_payment * 7, 2)

    @property
    def two_week_payment(self):
        return round(self.daily_payment * 14, 2)

    @property
    def two_month_payment(self):
        return round(self.monthly_payment * 2, 2)

    @property
    def early_3_month_price(self):
        return self.calculated_3_month_total(self.default_cash_price or self.cash_price)

    @property
    def early_6_month_price(self):
        return self.calculated_6_month_total(self.default_cash_price or self.cash_price)

    def clean(self):
        super().clean()
        errors = {}

        if self.deposit_percent is not None:
            if self.deposit_percent < Decimal("13"):
                errors["deposit_percent"] = "Deposit percent cannot be below 13%."
            elif self.deposit_percent > Decimal("16"):
                errors["deposit_percent"] = "Deposit percent cannot be above 16%."

        if (
            self.min_cash_price is not None
            and self.default_cash_price is not None
            and self.max_cash_price is not None
            and self.min_cash_price > 0
            and self.max_cash_price > 0
            and not self.min_cash_price <= self.default_cash_price <= self.max_cash_price
        ):
            errors["default_cash_price"] = "Default cash price must be between min and max cash price."

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.brand.name} {self.model_name} {self.specs}"
