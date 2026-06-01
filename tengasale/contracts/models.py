import random
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


def generate_contract_number():
    for _ in range(100):
        number = "E" + "".join(random.choices("0123456789", k=8))
        if not Contract.objects.filter(contract_number=number).exists():
            return number
    raise RuntimeError("Could not generate a unique contract number.")


class Contract(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_TERMS_ACCEPTED = "terms_accepted"
    STATUS_SIGNED = "signed"
    STATUS_IMEI_ENTERED = "imei_entered"
    STATUS_CONTRACT_CREATED = "contract_created"
    STATUS_WARRANTY_CHECKED = "warranty_checked"
    STATUS_LOCKING = "locking"
    STATUS_LOCKED = "locked"
    STATUS_DEPOSIT_PENDING = "deposit_pending"
    STATUS_COMPLETE = "complete"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_TERMS_ACCEPTED, "Terms Accepted"),
        (STATUS_SIGNED, "Signed"),
        (STATUS_IMEI_ENTERED, "IMEI Entered"),
        (STATUS_CONTRACT_CREATED, "Contract Created"),
        (STATUS_WARRANTY_CHECKED, "Warranty Checked"),
        (STATUS_LOCKING, "Locking"),
        (STATUS_LOCKED, "Locked"),
        (STATUS_DEPOSIT_PENDING, "Deposit Pending"),
        (STATUS_COMPLETE, "Complete"),
    ]

    application = models.OneToOneField(
        "applications.FinancingApplication",
        on_delete=models.CASCADE,
        related_name="contract",
    )
    contract_number = models.CharField(max_length=9, unique=True, blank=True)
    merchant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="contracts")
    customer_name = models.CharField(max_length=150)
    customer_phone = models.CharField(max_length=20, blank=True)
    national_id = models.CharField(max_length=20, blank=True)
    deal_name = models.CharField(max_length=200, blank=True)
    imei_number = models.CharField(max_length=15, blank=True)
    cash_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_loan = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    deposit_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    monthly_payment = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    daily_payment = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    terms_accepted_by_merchant = models.BooleanField(default=False)
    customer_contract_signature = models.ImageField(
        upload_to="contract_signatures/",
        blank=True,
        null=True,
    )
    customer_terms_accepted = models.BooleanField(default=False)
    warranty_checked = models.BooleanField(default=False)
    phone_locked = models.BooleanField(default=False)
    deposit_paid = models.BooleanField(default=False)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_DRAFT)

    # Post-approval tracking fields
    terms_accepted_at = models.DateTimeField(null=True, blank=True)
    terms_accepted_ip = models.CharField(max_length=45, blank=True)
    terms_otp_code = models.CharField(max_length=20, blank=True)
    terms_otp_verified_at = models.DateTimeField(null=True, blank=True)

    # PDF documents
    initial_pdf = models.FileField(upload_to="contract_pdfs/initial/", blank=True, null=True)
    completed_pdf = models.FileField(upload_to="contract_pdfs/completed/", blank=True, null=True)

    # Completion
    completed_at = models.DateTimeField(null=True, blank=True)
    ownership_transfer_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.contract_number:
            self.contract_number = generate_contract_number()
        super().save(*args, **kwargs)

    @classmethod
    def from_application(cls, application):
        deal_name = str(application.deal) if application.deal_id else ""
        return cls.objects.get_or_create(
            application=application,
            defaults={
                "merchant": application.created_by,
                "customer_name": application.customer_name,
                "customer_phone": application.customer_phone,
                "national_id": application.national_id,
                "deal_name": deal_name,
                "cash_price": application.selected_cash_price or Decimal("0"),
                "total_loan": application.calculated_total_loan or Decimal("0"),
                "deposit_amount": application.calculated_deposit_amount or Decimal("0"),
                "monthly_payment": application.calculated_monthly_payment or Decimal("0"),
                "daily_payment": application.calculated_daily_payment or Decimal("0"),
            },
        )

    def __str__(self):
        return self.contract_number or f"Contract for {self.application_id}"

    @property
    def finance_charges(self):
        return max(Decimal("0"), self.total_loan - self.cash_price)

    @property
    def total_contract_price(self):
        return self.total_loan

    @property
    def amount_financed(self):
        return max(Decimal("0"), self.total_loan - self.deposit_amount)

    @property
    def contract_term_months(self):
        if self.monthly_payment and self.monthly_payment > 0:
            amount = self.amount_financed
            return int((amount / self.monthly_payment).quantize(Decimal("1")))
        return 0


class ContractDocumentDelivery(models.Model):
    CHANNEL_WHATSAPP = "whatsapp"
    CHANNEL_SMS = "sms"
    CHANNEL_EMAIL = "email"
    CHANNEL_MANUAL = "manual"
    CHANNEL_CHOICES = [
        (CHANNEL_WHATSAPP, "WhatsApp"),
        (CHANNEL_SMS, "SMS"),
        (CHANNEL_EMAIL, "Email"),
        (CHANNEL_MANUAL, "Manual"),
    ]

    STATUS_PENDING = "pending"
    STATUS_SCHEDULED = "scheduled"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"
    STATUS_SKIPPED = "skipped"
    DELIVERY_STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_SCHEDULED, "Scheduled"),
        (STATUS_SENT, "Sent"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
        (STATUS_SKIPPED, "Skipped"),
    ]

    PROVIDER_MOCK = "mock"
    PROVIDER_TWILIO = "twilio"
    PROVIDER_META = "meta_whatsapp"
    PROVIDER_OTHER = "other"
    PROVIDER_CHOICES = [
        (PROVIDER_MOCK, "Mock (Test)"),
        (PROVIDER_TWILIO, "Twilio WhatsApp"),
        (PROVIDER_META, "Meta WhatsApp Business"),
        (PROVIDER_OTHER, "Other"),
    ]

    contract = models.ForeignKey(
        Contract,
        on_delete=models.CASCADE,
        related_name="document_deliveries",
    )
    application = models.ForeignKey(
        "applications.FinancingApplication",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="document_deliveries",
    )
    customer_phone = models.CharField(max_length=20, blank=True)
    normalized_customer_phone = models.CharField(max_length=20, blank=True)
    channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES, default=CHANNEL_WHATSAPP)
    delivery_status = models.CharField(
        max_length=20,
        choices=DELIVERY_STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    scheduled_for = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    completed_at_snapshot = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Snapshot of contract.completed_at at time of scheduling.",
    )
    pdf_file = models.FileField(upload_to="contract_pdfs/delivery/", blank=True, null=True)
    pdf_url = models.CharField(max_length=500, blank=True)
    message_text = models.TextField(blank=True)
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, default=PROVIDER_MOCK)
    provider_message_id = models.CharField(max_length=200, blank=True)
    provider_response = models.JSONField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="triggered_deliveries",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Contract Document Delivery"
        verbose_name_plural = "Contract Document Deliveries"
        indexes = [
            models.Index(fields=["delivery_status", "scheduled_for"]),
            models.Index(fields=["contract", "channel"]),
        ]

    def __str__(self):
        return f"Delivery #{self.pk} [{self.delivery_status}] for {self.contract}"

    @property
    def is_due(self):
        if self.delivery_status != self.STATUS_SCHEDULED:
            return False
        if not self.scheduled_for:
            return False
        return timezone.now() >= self.scheduled_for
