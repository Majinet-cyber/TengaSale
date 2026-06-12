import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


class Merchant(models.Model):
    CERTIFICATE_NOT_SUBMITTED = "not_submitted"
    CERTIFICATE_PENDING = "pending_review"
    CERTIFICATE_APPROVED = "approved"
    CERTIFICATE_REJECTED = "rejected"

    CERTIFICATE_STATUS_CHOICES = [
        (CERTIFICATE_NOT_SUBMITTED, "Not Submitted"),
        (CERTIFICATE_PENDING, "Pending Review"),
        (CERTIFICATE_APPROVED, "Approved"),
        (CERTIFICATE_REJECTED, "Rejected"),
    ]

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    business_name = models.CharField(max_length=150)
    phone_number = models.CharField(max_length=30)
    location = models.CharField(max_length=150, blank=True)
    is_active = models.BooleanField(default=True)
    certificate_file = models.FileField(upload_to="merchant_certificates/", null=True, blank=True)
    certificate_status = models.CharField(
        max_length=20,
        choices=CERTIFICATE_STATUS_CHOICES,
        default=CERTIFICATE_NOT_SUBMITTED,
    )
    certificate_uploaded_at = models.DateTimeField(null=True, blank=True)
    compliance_reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="merchant_compliance_reviews",
    )
    compliance_reviewed_at = models.DateTimeField(null=True, blank=True)
    compliance_rejection_reason = models.TextField(blank=True)

    def __str__(self):
        return self.business_name

    @property
    def active_agreement(self):
        return self.agreements.filter(
            status__in=[MerchantAgreement.STATUS_SIGNED, MerchantAgreement.STATUS_ACTIVE]
        ).order_by("-signed_at").first()

    @property
    def has_signed_agreement(self):
        return self.agreements.filter(
            status__in=[MerchantAgreement.STATUS_SIGNED, MerchantAgreement.STATUS_ACTIVE]
        ).exists()

    @property
    def compliance_status(self):
        if not self.has_signed_agreement:
            return "pending_signature"
        if self.certificate_status == self.CERTIFICATE_NOT_SUBMITTED:
            return "pending_certificate"
        if self.certificate_status == self.CERTIFICATE_PENDING:
            return "submitted"
        if self.certificate_status == self.CERTIFICATE_APPROVED:
            return "approved"
        return "rejected"

    @property
    def compliance_label(self):
        return {
            "pending_signature": "Pending Signature",
            "pending_certificate": "Pending Certificate",
            "submitted": "Submitted for Review",
            "approved": "Approved",
            "rejected": "Rejected",
        }.get(self.compliance_status, "Not Started")

    @property
    def is_compliance_complete(self):
        return self.has_signed_agreement and self.certificate_status == self.CERTIFICATE_APPROVED

    @property
    def payout_block_reason(self):
        if not self.has_signed_agreement:
            return "Blocked: merchant agreement unsigned"
        if self.certificate_status != self.CERTIFICATE_APPROVED:
            return "Blocked: merchant compliance certificate not approved"
        return ""


class MerchantAgreement(models.Model):
    AGREEMENT_VERSION = "1.0"

    STATUS_NOT_STARTED = "not_started"
    STATUS_VIEWED = "viewed"
    STATUS_SIGNED = "signed"
    STATUS_ACTIVE = "active"
    STATUS_SUSPENDED = "suspended"
    STATUS_TERMINATED = "terminated"
    STATUS_EXPIRED = "expired"

    STATUS_CHOICES = [
        (STATUS_NOT_STARTED, "Not Started"),
        (STATUS_VIEWED, "Viewed"),
        (STATUS_SIGNED, "Signed"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_SUSPENDED, "Suspended"),
        (STATUS_TERMINATED, "Terminated"),
        (STATUS_EXPIRED, "Expired"),
    ]

    merchant = models.ForeignKey(
        Merchant, on_delete=models.CASCADE, related_name="agreements"
    )
    reference_number = models.CharField(max_length=30, unique=True, blank=True)
    agreement_version = models.CharField(max_length=20, default=AGREEMENT_VERSION)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_NOT_STARTED)

    # Signer details
    signer_name = models.CharField(max_length=200, blank=True)
    signer_email = models.CharField(max_length=200, blank=True)
    signer_phone = models.CharField(max_length=30, blank=True)
    business_name = models.CharField(max_length=200, blank=True)

    # Signature capture
    signature_image = models.ImageField(upload_to="merchant_agreements/signatures/", null=True, blank=True)
    agreed_read = models.BooleanField(default=False)
    agreed_good_faith = models.BooleanField(default=False)
    agreed_genuine_customers = models.BooleanField(default=False)
    agreed_discretion = models.BooleanField(default=False)
    agreed_commissions = models.BooleanField(default=False)
    agreed_electronic_signature = models.BooleanField(default=False)

    # Timestamps and metadata
    viewed_at = models.DateTimeField(null=True, blank=True)
    signed_at = models.DateTimeField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    # Generated PDF
    pdf_file = models.FileField(upload_to="merchant_agreements/pdfs/", null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Merchant Agreement"
        verbose_name_plural = "Merchant Agreements"

    def __str__(self):
        return f"Agreement {self.reference_number} — {self.merchant.business_name} ({self.status})"

    def save(self, *args, **kwargs):
        if not self.reference_number:
            self.reference_number = self._generate_reference()
        super().save(*args, **kwargs)

    @staticmethod
    def _generate_reference():
        uid = uuid.uuid4().hex[:8].upper()
        return f"MPA-{uid}"

    @property
    def is_signed(self):
        return self.status in [self.STATUS_SIGNED, self.STATUS_ACTIVE]

    @property
    def all_boxes_checked(self):
        return all([
            self.agreed_read,
            self.agreed_good_faith,
            self.agreed_genuine_customers,
            self.agreed_discretion,
            self.agreed_commissions,
            self.agreed_electronic_signature,
        ])
