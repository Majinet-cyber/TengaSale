import hashlib
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

    # Contract term (3, 6, or 12 months)
    term_months = models.PositiveIntegerField(
        default=12,
        help_text="Contract term in months: 3, 6, or 12",
    )

    # Post-approval tracking fields
    terms_accepted_at = models.DateTimeField(null=True, blank=True)
    terms_accepted_ip = models.CharField(max_length=45, blank=True)
    terms_otp_code = models.CharField(max_length=20, blank=True)
    terms_otp_verified_at = models.DateTimeField(null=True, blank=True)

    # Merchant terms review tracking (required before activation)
    terms_opened_at = models.DateTimeField(null=True, blank=True)
    terms_confirmed_at = models.DateTimeField(null=True, blank=True)
    terms_confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="terms_confirmed_contracts",
    )
    terms_version = models.CharField(max_length=20, blank=True, default="v1")

    # PDF documents
    initial_pdf = models.FileField(upload_to="contract_pdfs/initial/", blank=True, null=True)
    completed_pdf = models.FileField(upload_to="contract_pdfs/completed/", blank=True, null=True)
    # Legal bundle PDFs (Contract Summary + Master Terms + Acceptance Certificate)
    contract_bundle_pdf = models.FileField(upload_to="contract_pdfs/bundle/", blank=True, null=True)
    completed_bundle_pdf = models.FileField(upload_to="contract_pdfs/bundle_completed/", blank=True, null=True)

    # Activation
    active_at = models.DateTimeField(null=True, blank=True)

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
    def payg_number(self):
        """
        Customer-facing PayG number from the linked PaymentContract (portal).
        Returns empty string if not yet created.
        """
        try:
            return self.application.payment_contract.payg_number or ""
        except Exception:
            return ""

    @property
    def payment_url(self):
        """Canonical customer-facing payment URL using PayG number."""
        payg = self.payg_number
        if payg:
            return f"/pay/payg/{payg}/"
        return f"/pay/contract/{self.contract_number}/" if self.contract_number else ""

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
        """Returns the stored term_months, falling back to calculated value."""
        if self.term_months and self.term_months > 0:
            return self.term_months
        if self.monthly_payment and self.monthly_payment > 0:
            amount = self.amount_financed
            return int((amount / self.monthly_payment).quantize(Decimal("1")))
        return 12

    @property
    def terms_review_confirmed(self):
        return bool(self.terms_confirmed_at)

    @property
    def can_activate(self):
        """Contract can only be activated after terms are confirmed by merchant."""
        return self.terms_review_confirmed


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


# ─────────────────────────────────────────────────────────────────────────────
# Legal document versioning
# ─────────────────────────────────────────────────────────────────────────────

class LegalDocumentTemplate(models.Model):
    TYPE_CONTRACT_SUMMARY = "customer_contract_summary"
    TYPE_MASTER_TERMS = "master_terms"
    TYPE_PRIVACY_NOTICE = "privacy_notice"
    TYPE_DEVICE_LOCK_TERMS = "device_lock_terms"

    DOCUMENT_TYPE_CHOICES = [
        (TYPE_CONTRACT_SUMMARY, "Customer Contract Summary"),
        (TYPE_MASTER_TERMS, "Master Terms and Conditions"),
        (TYPE_PRIVACY_NOTICE, "Privacy Notice"),
        (TYPE_DEVICE_LOCK_TERMS, "Device Lock Terms"),
    ]

    document_type = models.CharField(max_length=40, choices=DOCUMENT_TYPE_CHOICES)
    version = models.CharField(max_length=20)
    title = models.CharField(max_length=200)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    body_html = models.TextField(help_text="Full HTML content of this legal document version.")
    source_file = models.FileField(upload_to="legal_docs/source/", blank=True, null=True)
    checksum = models.CharField(max_length=64, blank=True, help_text="SHA-256 hash of body_html for integrity verification.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("document_type", "version")]
        ordering = ["-effective_from", "-version"]
        verbose_name = "Legal Document Template"
        verbose_name_plural = "Legal Document Templates"

    def save(self, *args, **kwargs):
        if self.body_html:
            self.checksum = hashlib.sha256(self.body_html.encode("utf-8")).hexdigest()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_document_type_display()} v{self.version}"

    @classmethod
    def get_active(cls, document_type: str) -> "LegalDocumentTemplate | None":
        return cls.objects.filter(document_type=document_type, is_active=True).order_by("-effective_from").first()


class LegalAcceptance(models.Model):
    METHOD_OTP = "otp"
    METHOD_DIGITAL_SIGNATURE = "digital_signature"
    METHOD_MOBILE_ACCEPTANCE = "mobile_acceptance"
    METHOD_BIOMETRIC = "biometric_confirmation"
    METHOD_STAFF = "staff_confirmed"

    ACCEPTANCE_METHOD_CHOICES = [
        (METHOD_OTP, "OTP Verification"),
        (METHOD_DIGITAL_SIGNATURE, "Digital/Touchscreen Signature"),
        (METHOD_MOBILE_ACCEPTANCE, "Mobile Acceptance (Checkbox/Tap)"),
        (METHOD_BIOMETRIC, "Biometric Confirmation"),
        (METHOD_STAFF, "Staff Confirmed"),
    ]

    application = models.ForeignKey(
        "applications.FinancingApplication",
        on_delete=models.CASCADE,
        related_name="legal_acceptances",
    )
    contract = models.ForeignKey(
        "contracts.Contract",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="legal_acceptances",
    )
    legal_document = models.ForeignKey(
        LegalDocumentTemplate,
        on_delete=models.PROTECT,
        related_name="acceptances",
    )
    accepted_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="legal_acceptances",
    )
    accepted_name = models.CharField(max_length=150, blank=True)
    accepted_phone = models.CharField(max_length=20, blank=True)
    accepted_national_id = models.CharField(max_length=20, blank=True)
    acceptance_method = models.CharField(
        max_length=30,
        choices=ACCEPTANCE_METHOD_CHOICES,
        default=METHOD_MOBILE_ACCEPTANCE,
    )
    accepted_at = models.DateTimeField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    device_reference = models.CharField(max_length=200, blank=True)
    otp_reference = models.CharField(max_length=50, blank=True)
    signature_image = models.ImageField(upload_to="legal_signatures/", blank=True, null=True)
    acceptance_text_snapshot = models.TextField(
        blank=True,
        help_text="Snapshot of document title/version at time of acceptance for audit purposes.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-accepted_at"]
        verbose_name = "Legal Acceptance"
        verbose_name_plural = "Legal Acceptances"
        indexes = [
            models.Index(fields=["application", "legal_document"]),
            models.Index(fields=["contract", "legal_document"]),
        ]

    def __str__(self):
        return f"Acceptance of {self.legal_document} by {self.accepted_name or self.accepted_by_user_id} at {self.accepted_at:%Y-%m-%d %H:%M}"
