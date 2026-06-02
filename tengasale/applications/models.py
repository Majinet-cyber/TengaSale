import secrets
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone

from deals.models import DeviceDeal


class FinancingApplication(models.Model):
    phone_validator = RegexValidator(r"^\d{9}$", "Phone number must be exactly 9 digits.")
    national_id_validator = RegexValidator(
        r"^[A-Za-z0-9]{8}$",
        "National ID must be exactly 8 letters or numbers.",
    )

    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("started", "Start"),
        ("customer_details", "Customer Details"),
        ("device_selection", "Device Selection"),
        ("kyc", "KYC"),
        ("kyc_capture", "KYC Capture"),
        ("location", "Location"),
        ("location_details", "Location Details"),
        ("work", "Work"),
        ("work_details", "Work Details"),
        ("signature", "Signature"),
        ("submitted", "Submitted"),
        ("pending_review", "Pending Review"),
        ("under_review", "Under Review"),
        ("correction_requested", "Correction Requested"),
        ("sent_back", "Sent Back"),
        ("resubmitted", "Resubmitted"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("contract_terms", "Contract Terms"),
        ("contract_signature", "Contract Signature"),
        ("imei_entry", "IMEI Entry"),
        ("contract_creating", "Contract Creating"),
        ("warranty_check", "Warranty Check"),
        ("locking", "Locking"),
        ("deposit_pending", "Deposit Pending"),
        ("contract_complete", "Contract Complete"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
        ("imei_required", "IMEI Required"),
    ]

    REVIEW_STATUS_CHOICES = [
        ("", "Not reviewed"),
        ("pending_review", "Pending Review"),
        ("under_review", "Under Review"),
        ("sent_back", "Sent Back"),
        ("resubmitted", "Resubmitted"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]

    MERCHANT_STATUS_LABELS = {
        "pending_review": "Pending Review",
        "under_review": "Under Review",
        "needs_edit": "Sent Back",
        "approved": "Approved",
        "completed": "Completed",
        "rejected": "Rejected",
    }

    CORRECTION_FIELD_LABELS = {
        "customer_name": "Customer name",
        "national_id": "National ID",
        "customer_phone": "Customer phone",
        "occupation": "Occupation",
        "income_band": "Income band",
        "exact_monthly_income": "Exact monthly income",
        "selected_deal": "Selected deal",
        "selected_cash_price": "Selected cash price",
        "calculated_deposit_amount": "Calculated deposit amount",
        "customer_face_image": "Customer face image",
        "id_front_image": "ID front image",
        "id_back_image": "ID back image",
        "region": "Region",
        "district": "District",
        "traditional_authority": "Traditional authority",
        "precise_location": "Precise location",
        "gps_coordinates": "GPS coordinates",
        "map_screenshot": "Map screenshot",
        "next_of_kin_1_name": "Next of kin 1 name",
        "next_of_kin_1_phone": "Next of kin 1 phone",
        "next_of_kin_1_relationship": "Next of kin 1 relationship",
        "work_description": "Work description",
        "next_of_kin_2_name": "Next of kin 2 name",
        "next_of_kin_2_phone": "Next of kin 2 phone",
        "next_of_kin_2_relationship": "Next of kin 2 relationship",
        "proof_of_income_type": "Proof of income type",
        "proof_contact_name": "Proof contact name",
        "proof_contact_phone": "Proof contact phone",
        "proof_notes": "Proof notes",
        "proof_income_file": "Proof income file",
        "customer_signature": "Customer signature",
        "agreed_to_terms": "Agreed to terms",
        "gender": "Gender",
        "marital_status": "Marital status",
        "num_dependents": "Number of dependents",
        "date_of_birth": "Date of birth",
        "phone_user": "Who will use the phone",
        "device_purpose": "Device purpose",
    }

    CORRECTION_FIELD_ORDER = list(CORRECTION_FIELD_LABELS.keys())

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    application_number = models.CharField(max_length=50, unique=True, blank=True)

    customer_name = models.CharField(max_length=150, blank=True)
    customer_phone = models.CharField(max_length=9, blank=True, validators=[phone_validator])
    national_id = models.CharField(max_length=8, blank=True, validators=[national_id_validator])
    occupation = models.CharField(max_length=150, blank=True)
    income_band = models.CharField(max_length=40, blank=True)
    exact_monthly_income = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # ── Demographic enrichment ────────────────────────────────────────────────
    GENDER_MALE = "male"
    GENDER_FEMALE = "female"
    GENDER_OTHER = "other"
    GENDER_CHOICES = [
        ("", "Select gender"),
        (GENDER_MALE, "Male"),
        (GENDER_FEMALE, "Female"),
    ]

    MARITAL_SINGLE = "single"
    MARITAL_MARRIED = "married"
    MARITAL_DIVORCED = "divorced"
    MARITAL_WIDOWED = "widowed"
    MARITAL_SEPARATED = "separated"
    MARITAL_CHOICES = [
        ("", "Select marital status"),
        (MARITAL_SINGLE, "Single"),
        (MARITAL_MARRIED, "Married"),
        (MARITAL_DIVORCED, "Divorced"),
        (MARITAL_WIDOWED, "Widowed"),
        (MARITAL_SEPARATED, "Separated"),
    ]

    PHONE_USER_SELF = "customer_self"
    PHONE_USER_CHOICES = [
        ("", "Select who will use the phone"),
        (PHONE_USER_SELF, "The customer themselves"),
        ("spouse", "Spouse"),
        ("child", "Child"),
        ("parent", "Parent"),
        ("family_member", "Other family member"),
        ("friend", "Friend"),
        ("business_employee", "Business employee"),
        ("other", "Other"),
    ]

    DEVICE_PURPOSE_CHOICES = [
        ("", "Select purpose"),
        ("personal", "Personal use"),
        ("business", "Business use"),
        ("school", "School / education"),
        ("family_use", "Family use"),
        ("other", "Other"),
    ]

    RENT_OWN_CHOICES = [
        ("", "Select"),
        ("rent", "Renting"),
        ("own", "Owned home"),
        ("family_home", "Family/parental home"),
        ("employer_provided", "Employer-provided"),
        ("other", "Other"),
    ]

    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, blank=True, default="")
    marital_status = models.CharField(max_length=15, choices=MARITAL_CHOICES, blank=True, default="")
    num_dependents = models.PositiveSmallIntegerField(null=True, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    phone_user = models.CharField(max_length=20, choices=PHONE_USER_CHOICES, blank=True, default="")
    phone_user_other = models.CharField(max_length=100, blank=True, default="")
    device_purpose = models.CharField(max_length=20, choices=DEVICE_PURPOSE_CHOICES, blank=True, default="")
    household_income_contributors = models.PositiveSmallIntegerField(null=True, blank=True)
    rent_or_own_home = models.CharField(max_length=20, choices=RENT_OWN_CHOICES, blank=True, default="")
    third_party_phone_user_risk_flagged = models.BooleanField(
        default=False,
        help_text="Auto-flagged when phone will be used by someone other than the applicant",
    )

    income_source = models.CharField(max_length=150, blank=True)
    monthly_income = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    location = models.CharField(max_length=150, blank=True)

    deal = models.ForeignKey(DeviceDeal, on_delete=models.SET_NULL, null=True, blank=True)
    selected_cash_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    selected_deposit_percent = models.DecimalField(max_digits=5, decimal_places=2, default=13)
    selected_loan_multiplier = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("2.5"))
    calculated_total_loan = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    calculated_deposit_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    calculated_monthly_payment = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    calculated_daily_payment = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    calculated_6_month_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    calculated_6_month_monthly = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    calculated_6_month_daily = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    calculated_3_month_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    calculated_3_month_monthly = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    calculated_3_month_daily = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    imei_number = models.CharField(max_length=80, blank=True)

    # ── IMEI Verification ─────────────────────────────────────────────────────
    IMEI_STATUS_PENDING = "pending"
    IMEI_STATUS_MATCHED = "matched"
    IMEI_STATUS_POSSIBLE = "possible_match"
    IMEI_STATUS_MISMATCH = "mismatch"
    IMEI_STATUS_UNKNOWN = "unknown"
    IMEI_STATUS_API_ERROR = "api_error"

    IMEI_STATUS_CHOICES = [
        (IMEI_STATUS_PENDING, "Pending"),
        (IMEI_STATUS_MATCHED, "Matched"),
        (IMEI_STATUS_POSSIBLE, "Possible match"),
        (IMEI_STATUS_MISMATCH, "Mismatch"),
        (IMEI_STATUS_UNKNOWN, "Unknown"),
        (IMEI_STATUS_API_ERROR, "API error"),
    ]

    imei_verified = models.BooleanField(default=False)
    imei_verification_status = models.CharField(
        max_length=30, choices=IMEI_STATUS_CHOICES, blank=True, default=""
    )
    imei_api_brand = models.CharField(max_length=120, blank=True, null=True)
    imei_api_model = models.CharField(max_length=180, blank=True, null=True)
    imei_match_confidence = models.PositiveSmallIntegerField(blank=True, null=True)
    imei_verification_reason = models.TextField(blank=True, null=True)
    imei_verified_at = models.DateTimeField(blank=True, null=True)
    imei_order_id = models.CharField(max_length=80, blank=True, null=True)
    imei_raw_response = models.JSONField(blank=True, null=True)
    # Override fields — allow HQ / senior underwriter to unblock a mismatch
    imei_override = models.BooleanField(default=False)
    imei_override_reason = models.TextField(blank=True, null=True)
    imei_override_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="imei_overrides",
    )
    imei_override_at = models.DateTimeField(blank=True, null=True)

    customer_face_image = models.ImageField(upload_to="kyc/faces/", blank=True, null=True)
    id_front_image = models.ImageField(upload_to="kyc/id_front/", blank=True, null=True)
    id_back_image = models.ImageField(upload_to="kyc/id_back/", blank=True, null=True)
    customer_phone_image = models.ImageField(upload_to="kyc/customer_phone/", blank=True, null=True)
    correction_customer_face_image = models.BooleanField(default=False)
    correction_id_front_image = models.BooleanField(default=False)
    correction_id_back_image = models.BooleanField(default=False)
    correction_customer_phone_image = models.BooleanField(default=False)
    correction_notes = models.TextField(blank=True)

    region = models.CharField(max_length=30, blank=True)
    district = models.CharField(max_length=80, blank=True)
    traditional_authority = models.CharField(max_length=120, blank=True)
    precise_location = models.CharField(max_length=200, blank=True)
    gps_coordinates = models.CharField(max_length=80, blank=True)
    map_screenshot = models.ImageField(upload_to="locations/maps/", blank=True, null=True)
    next_of_kin_1_name = models.CharField(max_length=150, blank=True)
    next_of_kin_1_phone = models.CharField(max_length=9, blank=True, validators=[phone_validator])
    next_of_kin_1_relationship = models.CharField(max_length=80, blank=True)

    work_description = models.TextField(blank=True)
    next_of_kin_2_name = models.CharField(max_length=150, blank=True)
    next_of_kin_2_phone = models.CharField(max_length=9, blank=True, validators=[phone_validator])
    next_of_kin_2_relationship = models.CharField(max_length=80, blank=True)
    proof_of_income_type = models.CharField(max_length=40, blank=True)
    proof_contact_name = models.CharField(max_length=150, blank=True)
    proof_contact_phone = models.CharField(max_length=9, blank=True, validators=[phone_validator])
    proof_notes = models.TextField(blank=True)
    proof_income_file = models.FileField(upload_to="proof_income/", blank=True, null=True)

    signature_image = models.ImageField(upload_to="signatures/", blank=True, null=True)
    agreed_to_terms = models.BooleanField(default=False)

    status = models.CharField(max_length=40, choices=STATUS_CHOICES, default="started")

    claimed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="claimed_applications",
    )
    claimed_at = models.DateTimeField(null=True, blank=True)

    manager_comment = models.TextField(blank=True)
    correction_fields = models.JSONField(default=list, blank=True)
    address_check_answers = models.JSONField(default=dict, blank=True)
    income_check_answers = models.JSONField(default=dict, blank=True)
    review_status = models.CharField(
        max_length=40,
        blank=True,
        default="",
        choices=REVIEW_STATUS_CHOICES,
    )

    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_applications",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    # ── Repayment Confidence Signal ───────────────────────────────────────────
    RISK_BAND_LOW = "low"
    RISK_BAND_MEDIUM = "medium"
    RISK_BAND_HIGH = "high"
    RISK_BAND_CHOICES = [
        (RISK_BAND_LOW, "Low risk"),
        (RISK_BAND_MEDIUM, "Medium risk"),
        (RISK_BAND_HIGH, "High risk"),
    ]

    repayment_confidence_score = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Rule-based repayment confidence estimate 0–100. Null means not yet calculated.",
    )
    risk_band = models.CharField(
        max_length=10, choices=RISK_BAND_CHOICES, blank=True, default="",
        help_text="Risk band derived from confidence score.",
    )
    recommendation_note = models.TextField(
        blank=True, default="",
        help_text="Human-readable rule-based recommendation note.",
    )
    confidence_calculated_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        creating = self.pk is None
        super().save(*args, **kwargs)

        if creating and not self.application_number:
            self.application_number = f"TSM-{timezone.now().strftime('%y%m%d')}-{self.pk:06d}"
            super().save(update_fields=["application_number"])

    def submit(self):
        was_sent_back = self.status in {"sent_back", "correction_requested"}
        self.status = "pending_review"
        self.review_status = "resubmitted" if was_sent_back else "pending_review"
        self.submitted_at = timezone.now()
        self.claimed_by = None
        self.claimed_at = None
        self.correction_fields = []
        self.correction_notes = ""
        self.save(
            update_fields=[
                "status",
                "review_status",
                "submitted_at",
                "claimed_by",
                "claimed_at",
                "correction_fields",
                "correction_notes",
            ]
        )
        if was_sent_back:
            ApplicationCorrection.objects.filter(application=self, resolved=False).update(resolved=True)

    @property
    def is_waiting_for_review(self):
        return self.status in {"submitted", "pending_review", "resubmitted"} and not self.claimed_by_id

    @property
    def queue_position(self):
        if not self.pk or not self.is_waiting_for_review:
            return None

        submitted_at = self.submitted_at or self.created_at
        ahead = FinancingApplication.objects.filter(
            status__in=["submitted", "pending_review", "resubmitted"],
            claimed_by__isnull=True,
        ).filter(
            models.Q(submitted_at__lt=submitted_at)
            | models.Q(submitted_at=submitted_at, id__lt=self.id)
        ).count()
        return ahead + 1

    @property
    def applications_ahead(self):
        position = self.queue_position
        return max(position - 1, 0) if position else None

    @property
    def merchant_status_key(self):
        if self.status in {"correction_requested", "sent_back"}:
            return "needs_edit"

        if self.status in ["contract_complete", "completed"]:
            return "completed"

        if self.status in ["submitted", "pending_review", "resubmitted"]:
            return "under_review" if self.claimed_by_id else "pending_review"

        if self.status == "under_review":
            return "under_review"

        if self.status in ["approved", "rejected"]:
            return self.status

        return self.status

    @property
    def merchant_status_label(self):
        return self.MERCHANT_STATUS_LABELS.get(self.merchant_status_key, self.get_status_display())

    def get_continue_url(self):
        if self.status == "approved":
            return reverse("contract_terms", args=[self.id])

        if self.status in ["contract_terms", "contract_signature"]:
            contract = getattr(self, "contract", None)
            if contract and self.status == "contract_signature":
                return reverse("contract_signature", args=[contract.id])
            return reverse("contract_terms", args=[self.id])

        if self.status == "imei_entry":
            contract = getattr(self, "contract", None)
            return reverse("contract_imei", args=[contract.id]) if contract else reverse("contract_terms", args=[self.id])

        if self.status in ["contract_creating", "warranty_check", "locking", "deposit_pending"]:
            contract = getattr(self, "contract", None)
            return reverse("contract_progress", args=[contract.id]) if contract else reverse("contract_terms", args=[self.id])

        if self.status in ["contract_complete", "completed"]:
            contract = getattr(self, "contract", None)
            return reverse("contract_detail", args=[contract.id]) if contract else reverse("application_detail", args=[self.id])

        if self.status in ["started", "customer_details"]:
            return reverse("edit_customer_details", args=[self.id])

        if self.status == "device_selection":
            return reverse("location_details", args=[self.id])

        if self.status in ["location", "location_details"]:
            return reverse("location_details", args=[self.id])

        if self.status in ["work", "work_details"]:
            return reverse("work_details", args=[self.id])

        if self.status in ["kyc", "kyc_capture"]:
            return reverse("kyc_capture", args=[self.id])

        if self.status == "signature":
            return reverse("signature", args=[self.id])

        if self.status in {"correction_requested", "sent_back"}:
            return reverse("application_corrections", args=[self.id])

        if self.status == "imei_required":
            return reverse("capture_imei", args=[self.id])

        return reverse("application_detail", args=[self.id])

    def get_correction_start_url(self):
        page_map = {
            "customer": {
                "customer_name",
                "national_id",
                "customer_phone",
                "occupation",
                "income_band",
                "exact_monthly_income",
            },
            "device": {"selected_deal", "selected_cash_price", "calculated_deposit_amount"},
            "kyc": {"customer_face_image", "id_front_image", "id_back_image"},
            "location": {
                "region",
                "district",
                "traditional_authority",
                "precise_location",
                "map_screenshot",
                "next_of_kin_1_name",
                "next_of_kin_1_phone",
                "next_of_kin_1_relationship",
            },
            "work": {
                "work_description",
                "next_of_kin_2_name",
                "next_of_kin_2_phone",
                "next_of_kin_2_relationship",
                "proof_of_income_type",
                "proof_contact_name",
                "proof_contact_phone",
                "proof_notes",
            },
            "signature": {"customer_signature", "agreed_to_terms"},
        }
        fields = set(self.correction_fields or [])
        if fields & page_map["customer"]:
            return reverse("edit_customer_details", args=[self.id])
        if fields & page_map["device"]:
            return reverse("choose_device", args=[self.id])
        if fields & page_map["kyc"]:
            return reverse("kyc_capture", args=[self.id])
        if fields & page_map["location"]:
            return reverse("location_details", args=[self.id])
        if fields & page_map["work"]:
            return reverse("work_details", args=[self.id])
        if fields & page_map["signature"]:
            return reverse("signature", args=[self.id])
        return reverse("edit_customer_details", args=[self.id])

    def get_correction_url_for_field(self, field_name):
        page_map = {
            "edit_customer_details": {
                "customer_name",
                "national_id",
                "customer_phone",
                "occupation",
                "income_band",
                "exact_monthly_income",
            },
            "choose_device": {"selected_deal", "selected_cash_price", "calculated_deposit_amount"},
            "kyc_capture": {"customer_face_image", "id_front_image", "id_back_image"},
            "location_details": {
                "region",
                "district",
                "traditional_authority",
                "precise_location",
                "gps_coordinates",
                "map_screenshot",
                "next_of_kin_1_name",
                "next_of_kin_1_phone",
                "next_of_kin_1_relationship",
            },
            "work_details": {
                "work_description",
                "income_source",
                "proof_of_income_type",
                "proof_contact_name",
                "proof_contact_phone",
                "proof_notes",
                "proof_income_file",
                "next_of_kin_2_name",
                "next_of_kin_2_phone",
                "next_of_kin_2_relationship",
            },
            "signature": {"customer_signature", "agreed_to_terms"},
        }
        for route_name, fields in page_map.items():
            if field_name in fields:
                return reverse(route_name, args=[self.id])
        return self.get_correction_start_url()

    def correction_field_labels(self):
        return [
            self.CORRECTION_FIELD_LABELS.get(field_name, field_name.replace("_", " ").title())
            for field_name in self.correction_fields or []
        ]

    def active_corrections(self):
        return self.corrections.filter(resolved=False).order_by("section", "label", "id")

    def active_correction_map(self):
        return {correction.field_name: correction for correction in self.active_corrections()}

    def sync_correction_summary(self):
        active = list(self.active_corrections())
        self.correction_fields = [
            field_name
            for field_name in self.CORRECTION_FIELD_ORDER
            if any(correction.field_name == field_name for correction in active)
        ]
        extra_fields = [
            correction.field_name
            for correction in active
            if correction.field_name not in self.correction_fields
        ]
        self.correction_fields.extend(extra_fields)
        self.correction_notes = "\n".join(correction.note for correction in active if correction.note)
        self.correction_customer_face_image = "customer_face_image" in self.correction_fields
        self.correction_id_front_image = "id_front_image" in self.correction_fields
        self.correction_id_back_image = "id_back_image" in self.correction_fields
        self.correction_customer_phone_image = "customer_phone_image" in self.correction_fields

    def apply_deal_selection(self, deal, selected_cash_price):
        self.deal = deal
        self.selected_cash_price = selected_cash_price
        self.selected_deposit_percent = deal.deposit_percent
        self.selected_loan_multiplier = deal.loan_multiplier
        self.calculated_total_loan = deal.calculated_total_loan(selected_cash_price)
        self.calculated_deposit_amount = deal.calculated_deposit(selected_cash_price)
        self.calculated_monthly_payment = deal.calculated_monthly_payment(selected_cash_price)
        self.calculated_daily_payment = deal.calculated_daily_payment(selected_cash_price)
        self.calculated_6_month_total = deal.calculated_6_month_total(selected_cash_price)
        self.calculated_6_month_monthly = deal.calculated_6_month_monthly(selected_cash_price)
        self.calculated_6_month_daily = deal.calculated_6_month_daily(selected_cash_price)
        self.calculated_3_month_total = deal.calculated_3_month_total(selected_cash_price)
        self.calculated_3_month_monthly = deal.calculated_3_month_monthly(selected_cash_price)
        self.calculated_3_month_daily = deal.calculated_3_month_daily(selected_cash_price)

    def __str__(self):
        return f"{self.application_number} - {self.customer_name}"


class ApplicationCorrection(models.Model):
    application = models.ForeignKey(
        FinancingApplication,
        on_delete=models.CASCADE,
        related_name="corrections",
    )
    field_name = models.CharField(max_length=120)
    section = models.CharField(max_length=120, blank=True)
    label = models.CharField(max_length=180)
    note = models.TextField(blank=True)
    resolved = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="application_corrections",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["application", "resolved"]),
            models.Index(fields=["field_name"]),
        ]

    def __str__(self):
        return f"{self.application} - {self.label}"


class ApplicationCorrectionToken(models.Model):
    """
    Secure, time-limited token for customer self-correction of marked fields.
    The token is embedded in a link sent to the customer via SMS/email.
    """
    application = models.OneToOneField(
        FinancingApplication,
        on_delete=models.CASCADE,
        related_name="correction_token_obj",
    )
    token = models.CharField(max_length=64, unique=True, blank=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Application Correction Token"

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(32)
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(hours=72)
        super().save(*args, **kwargs)

    @property
    def is_valid(self) -> bool:
        return timezone.now() < self.expires_at

    def mark_used(self):
        self.used_at = timezone.now()
        self.save(update_fields=["used_at"])

    @classmethod
    def create_or_refresh(cls, application: FinancingApplication) -> "ApplicationCorrectionToken":
        obj, created = cls.objects.get_or_create(application=application)
        if not created:
            obj.token = secrets.token_urlsafe(32)
            obj.expires_at = timezone.now() + timedelta(hours=72)
            obj.used_at = None
            obj.save(update_fields=["token", "expires_at", "used_at"])
        return obj

    def __str__(self):
        return f"CorrectionToken for {self.application_id}"


class ApplicationFieldReview(models.Model):
    """
    Granular field-level marking for underwriter review.
    Allows marking individual fields needing customer correction
    with a status trail and reason.
    """

    SECTION_CUSTOMER = "customer"
    SECTION_INCOME = "income"
    SECTION_CONTACTS = "contacts"
    SECTION_LOCATION = "location"
    SECTION_DOCUMENTS = "documents"

    SECTION_CHOICES = [
        (SECTION_CUSTOMER, "Customer"),
        (SECTION_INCOME, "Income"),
        (SECTION_CONTACTS, "Contacts"),
        (SECTION_LOCATION, "Location"),
        (SECTION_DOCUMENTS, "Documents"),
    ]

    STATUS_MARKED = "marked"
    STATUS_CUSTOMER_UPDATED = "customer_updated"
    STATUS_ACCEPTED = "accepted"
    STATUS_DISMISSED = "dismissed"

    STATUS_CHOICES = [
        (STATUS_MARKED, "Needs Review"),
        (STATUS_CUSTOMER_UPDATED, "Customer Updated"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_DISMISSED, "Dismissed"),
    ]

    REASON_INCORRECT = "incorrect_information"
    REASON_MISSING = "missing_information"
    REASON_UNCLEAR_DOC = "unclear_document"
    REASON_NEEDS_CONFIRM = "needs_customer_confirmation"
    REASON_NEEDS_PROOF = "needs_updated_proof"
    REASON_OTHER = "other"

    REASON_CHOICES = [
        (REASON_INCORRECT, "Incorrect information"),
        (REASON_MISSING, "Missing information"),
        (REASON_UNCLEAR_DOC, "Unclear document"),
        (REASON_NEEDS_CONFIRM, "Needs customer confirmation"),
        (REASON_NEEDS_PROOF, "Needs updated proof"),
        (REASON_OTHER, "Other"),
    ]

    EDITABLE_FIELD_KEYS = [
        "full_name", "dob", "gender", "national_id", "primary_phone", "secondary_phone",
        "income_band", "income_amount", "income_frequency", "income_source", "occupation",
        "company_name", "income_source_description", "company_contact_name", "company_contact_phone",
        "proof_of_income",
        "guarantor_type", "guarantor_name", "guarantor_phone", "neighbour_name", "neighbour_phone",
        "region", "district", "area", "village", "traditional_authority", "gps_location",
        "selfie", "id_front", "id_back", "pre_approval_signature", "contract_signature",
    ]

    application = models.ForeignKey(
        FinancingApplication,
        on_delete=models.CASCADE,
        related_name="field_reviews",
    )
    field_key = models.CharField(max_length=80)
    field_label = models.CharField(max_length=180)
    section = models.CharField(max_length=30, choices=SECTION_CHOICES, blank=True)
    current_value_snapshot = models.TextField(blank=True)
    reason = models.CharField(max_length=40, choices=REASON_CHOICES, default=REASON_INCORRECT)
    comment = models.TextField(blank=True)
    marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="field_reviews_marked",
    )
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_MARKED)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["section", "field_key"]
        verbose_name = "Application Field Review"
        verbose_name_plural = "Application Field Reviews"
        indexes = [
            models.Index(fields=["application", "status"]),
            models.Index(fields=["field_key"]),
        ]

    def __str__(self):
        return f"{self.application_id} — {self.field_label} [{self.status}]"
