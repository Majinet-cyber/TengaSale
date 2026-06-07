from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from applications.models import FinancingApplication


class CallEvidence(models.Model):
    STAGE_CUSTOMER_CALL = "customer_call"
    STAGE_GUARANTOR_CALL = "guarantor_call"
    STAGE_EMPLOYER_CALL = "employer_call"

    STAGE_CHOICES = [
        (STAGE_CUSTOMER_CALL, "Customer Call"),
        (STAGE_GUARANTOR_CALL, "Guarantor Call"),
        (STAGE_EMPLOYER_CALL, "Employer/Income Call"),
    ]

    application = models.ForeignKey(
        FinancingApplication,
        on_delete=models.CASCADE,
        related_name="call_evidence",
    )
    stage = models.CharField(max_length=30, choices=STAGE_CHOICES)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_call_evidence",
    )
    audio_file = models.FileField(upload_to="call_recordings/", blank=True, null=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    customer_notified = models.BooleanField(default=False)
    notification_script_confirmed = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Call Evidence"
        verbose_name_plural = "Call Evidence"

    def __str__(self):
        return f"{self.get_stage_display()} evidence for {self.application_id}"


class UnderwriterCallRecording(models.Model):
    STATUS_PENDING_QC = "pending_qc"
    STATUS_PASSED_QC = "passed_qc"
    STATUS_FAILED_QC = "failed_qc"

    STATUS_CHOICES = [
        (STATUS_PENDING_QC, "Pending QC"),
        (STATUS_PASSED_QC, "Passed QC"),
        (STATUS_FAILED_QC, "Failed QC"),
    ]

    application = models.ForeignKey(
        FinancingApplication,
        on_delete=models.CASCADE,
        related_name="underwriter_call_recordings",
    )
    contract = models.ForeignKey(
        "portal.PaymentContract",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="underwriter_call_recordings",
    )
    underwriter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="underwriter_call_recordings",
    )
    file = models.FileField(upload_to="underwriter_call_recordings/")
    original_filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=120, blank=True)
    file_size = models.PositiveIntegerField(default=0)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    consent_acknowledged = models.BooleanField(default=False)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING_QC)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_underwriter_call_recordings",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    qc_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-uploaded_at"]
        permissions = [
            ("underwriter_upload_call_recording", "Can upload underwriter call recordings"),
            ("hq_review_call_recording_qc", "Can review underwriter call recording QC"),
        ]
        verbose_name = "Underwriter Call Recording"
        verbose_name_plural = "Underwriter Call Recordings"

    @property
    def file_size_mb(self):
        return round((self.file_size or 0) / (1024 * 1024), 2)

    @property
    def can_delete(self):
        return self.status == self.STATUS_PENDING_QC

    def mark_reviewed(self, status, reviewed_by, notes=""):
        self.status = status
        self.reviewed_by = reviewed_by
        self.reviewed_at = timezone.now()
        self.qc_notes = notes
        self.save(update_fields=["status", "reviewed_by", "reviewed_at", "qc_notes"])

    def __str__(self):
        return f"{self.original_filename} for {self.application_id}"


class QCOffenseType(models.Model):
    SEVERITY_MINOR = "minor"
    SEVERITY_MODERATE = "moderate"
    SEVERITY_MAJOR = "major"
    SEVERITY_CRITICAL = "critical"

    SEVERITY_CHOICES = [
        (SEVERITY_MINOR, "Minor"),
        (SEVERITY_MODERATE, "Moderate"),
        (SEVERITY_MAJOR, "Major"),
        (SEVERITY_CRITICAL, "Critical"),
    ]

    code = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default=SEVERITY_MINOR)
    default_penalty_mwk = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["severity", "code"]
        verbose_name = "QC Offense Type"
        verbose_name_plural = "QC Offense Types"

    def __str__(self):
        return f"{self.code} - {self.name}"


class UnderwriterQCPenalty(models.Model):
    STATUS_PENDING = "pending"
    STATUS_APPLIED = "applied"
    STATUS_REVERSED = "reversed"
    STATUS_DISPUTED = "disputed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPLIED, "Applied"),
        (STATUS_REVERSED, "Reversed"),
        (STATUS_DISPUTED, "Disputed"),
    ]

    application = models.ForeignKey(
        FinancingApplication,
        on_delete=models.CASCADE,
        related_name="underwriter_qc_penalties",
    )
    contract = models.ForeignKey(
        "portal.PaymentContract",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="underwriter_qc_penalties",
    )
    underwriter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="qc_penalties",
    )
    offense_type = models.ForeignKey(
        QCOffenseType,
        on_delete=models.PROTECT,
        related_name="penalties",
    )
    amount_mwk = models.DecimalField(max_digits=14, decimal_places=2)
    notes = models.TextField(blank=True)
    evidence_recording = models.ForeignKey(
        UnderwriterCallRecording,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="qc_penalties",
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_qc_penalties",
    )
    issued_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    payroll_period = models.DateField(null=True, blank=True)
    ledger_entry = models.ForeignKey(
        "commissions.CommissionLedger",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="qc_penalties",
    )
    reversal_ledger_entry = models.ForeignKey(
        "commissions.CommissionLedger",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reversed_qc_penalties",
    )
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reversed_qc_penalties",
    )
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversal_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-issued_at"]
        permissions = [
            ("hq_issue_qc_penalty", "Can issue underwriter QC penalties"),
            ("hq_reverse_qc_penalty", "Can reverse underwriter QC penalties"),
            ("hq_override_penalty_amount", "Can override underwriter QC penalty amount"),
            ("underwriter_view_own_penalties", "Can view own underwriter QC penalties"),
            ("underwriter_dispute_penalty", "Can dispute own underwriter QC penalties"),
        ]
        verbose_name = "Underwriter QC Penalty"
        verbose_name_plural = "Underwriter QC Penalties"

    def apply_to_ledger(self, actor=None):
        if self.ledger_entry_id:
            return self.ledger_entry
        from commissions.models import CommissionLedger

        entry = CommissionLedger.objects.create(
            user=self.underwriter,
            contract=self.contract,
            application=self.application,
            entry_type=CommissionLedger.ENTRY_ADJUSTMENT,
            amount=Decimal("0") - Decimal(self.amount_mwk),
            base_amount=self.amount_mwk,
            description=f"QC penalty: {self.offense_type.name}",
            created_by=actor or self.issued_by,
            metadata={
                "qc_penalty_id": self.pk,
                "offense_code": self.offense_type.code,
                "evidence_recording_id": self.evidence_recording_id,
            },
        )
        self.ledger_entry = entry
        self.status = self.STATUS_APPLIED
        self.save(update_fields=["ledger_entry", "status"])
        return entry

    def reverse_to_ledger(self, actor, reason=""):
        if self.reversal_ledger_entry_id:
            return self.reversal_ledger_entry
        from commissions.models import CommissionLedger

        entry = CommissionLedger.objects.create(
            user=self.underwriter,
            contract=self.contract,
            application=self.application,
            entry_type=CommissionLedger.ENTRY_REVERSAL,
            amount=self.amount_mwk,
            base_amount=self.amount_mwk,
            description=f"QC penalty reversed: {self.offense_type.name}",
            created_by=actor,
            metadata={
                "qc_penalty_id": self.pk,
                "offense_code": self.offense_type.code,
                "reversal_reason": reason,
            },
        )
        self.reversal_ledger_entry = entry
        self.status = self.STATUS_REVERSED
        self.reversed_by = actor
        self.reversed_at = timezone.now()
        self.reversal_reason = reason
        self.save(update_fields=[
            "reversal_ledger_entry", "status", "reversed_by", "reversed_at", "reversal_reason",
        ])
        return entry

    def __str__(self):
        return f"{self.offense_type.code} - {self.underwriter_id} - MWK {self.amount_mwk}"


class UnderwriterReview(models.Model):
    application = models.OneToOneField(
        FinancingApplication,
        on_delete=models.CASCADE,
        related_name="underwriter_review",
    )
    underwriter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    summary_clear = models.BooleanField(null=True, blank=True)

    identity_signature_matches = models.BooleanField(null=True, blank=True)
    identity_info_matches = models.BooleanField(null=True, blank=True)
    identity_selfie_matches = models.BooleanField(null=True, blank=True)
    identity_images_clear = models.BooleanField(null=True, blank=True)

    momo_name_matches = models.BooleanField(null=True, blank=True)

    customer_spoken = models.BooleanField(null=True, blank=True)
    customer_intro_done = models.BooleanField(null=True, blank=True)
    customer_confirmed_application = models.BooleanField(null=True, blank=True)
    customer_confirmed_device = models.BooleanField(null=True, blank=True)
    customer_confirmed_deposit = models.BooleanField(null=True, blank=True)
    customer_confirmed_repayment = models.BooleanField(null=True, blank=True)
    customer_understands_direct_payment = models.BooleanField(null=True, blank=True)
    customer_understands_nonpayment = models.BooleanField(null=True, blank=True)

    income_understood = models.BooleanField(null=True, blank=True)
    income_contact_spoken = models.BooleanField(null=True, blank=True)
    income_confirmed = models.BooleanField(null=True, blank=True)
    income_source_dependable = models.BooleanField(null=True, blank=True)
    income_contact_confident = models.BooleanField(null=True, blank=True)

    guarantor_spoken = models.BooleanField(null=True, blank=True)
    guarantor_confirmed_customer = models.BooleanField(null=True, blank=True)
    guarantor_confident_repayment = models.BooleanField(null=True, blank=True)
    contacts_reachable = models.BooleanField(null=True, blank=True)

    location_neighbour_spoken = models.BooleanField(null=True, blank=True)
    location_confirmed = models.BooleanField(null=True, blank=True)
    location_traceable = models.BooleanField(null=True, blank=True)
    location_address_clear = models.BooleanField(null=True, blank=True)

    deal_phone_correct = models.BooleanField(null=True, blank=True)
    deal_deposit_understood = models.BooleanField(null=True, blank=True)
    deal_repayment_understood = models.BooleanField(null=True, blank=True)
    deal_lock_understood = models.BooleanField(null=True, blank=True)
    deal_legal_understood = models.BooleanField(null=True, blank=True)

    comment = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    REQUIRED_FIELDS = [
        "summary_clear",
        "identity_signature_matches",
        "identity_info_matches",
        "identity_selfie_matches",
        "identity_images_clear",
        "momo_name_matches",
        "customer_spoken",
        "customer_intro_done",
        "customer_confirmed_application",
        "customer_confirmed_device",
        "customer_confirmed_deposit",
        "customer_confirmed_repayment",
        "customer_understands_direct_payment",
        "customer_understands_nonpayment",
        "income_understood",
        "income_contact_spoken",
        "income_confirmed",
        "income_source_dependable",
        "income_contact_confident",
        "guarantor_spoken",
        "guarantor_confirmed_customer",
        "guarantor_confident_repayment",
        "contacts_reachable",
        "location_neighbour_spoken",
        "location_confirmed",
        "location_traceable",
        "location_address_clear",
        "deal_phone_correct",
        "deal_deposit_understood",
        "deal_repayment_understood",
        "deal_lock_understood",
        "deal_legal_understood",
    ]

    def completeness_score(self):
        total = len(self.REQUIRED_FIELDS)
        answered = sum(1 for field in self.REQUIRED_FIELDS if getattr(self, field) is not None)
        return round((answered / total) * 100) if total else 0

    def __str__(self):
        return f"Review for {self.application}"


class CustomerCallQuestionnaire(models.Model):
    RECOMMEND_APPROVE = "approve"
    RECOMMEND_REVIEW = "review"
    RECOMMEND_REJECT = "reject"

    RECOMMENDATION_CHOICES = [
        (RECOMMEND_APPROVE, "Approve"),
        (RECOMMEND_REVIEW, "Needs Review"),
        (RECOMMEND_REJECT, "Recommend Reject"),
    ]

    application = models.OneToOneField(
        FinancingApplication,
        on_delete=models.CASCADE,
        related_name="call_questionnaire",
    )
    recording_consent_acknowledged = models.BooleanField(default=False)
    identity_score = models.PositiveSmallIntegerField(null=True, blank=True)
    work_context_score = models.PositiveSmallIntegerField(null=True, blank=True)
    income_confidence_score = models.PositiveSmallIntegerField(null=True, blank=True)
    price_understanding_score = models.PositiveSmallIntegerField(null=True, blank=True)
    payment_understanding_score = models.PositiveSmallIntegerField(null=True, blank=True)
    determined_income = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    closing_notes = models.TextField(blank=True)
    recommendation = models.CharField(
        max_length=32,
        choices=RECOMMENDATION_CHOICES,
        default=RECOMMEND_REVIEW,
    )
    risk_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="completed_call_questionnaires",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    SCORE_FIELDS = [
        "identity_score",
        "work_context_score",
        "income_confidence_score",
        "price_understanding_score",
        "payment_understanding_score",
    ]
    CRITICAL_FIELDS = [
        "identity_score",
        "price_understanding_score",
        "payment_understanding_score",
    ]

    class Meta:
        ordering = ["-completed_at", "-updated_at"]
        verbose_name = "Customer Call Questionnaire"
        verbose_name_plural = "Customer Call Questionnaires"

    def calculate_outcome(self):
        scores = [int(getattr(self, field) or 0) for field in self.SCORE_FIELDS]
        actual_score = sum(scores)
        self.risk_score = round((actual_score / 15) * 100, 2)

        if any(int(getattr(self, field) or 0) == 1 for field in self.CRITICAL_FIELDS):
            self.recommendation = self.RECOMMEND_REJECT
        elif all(int(getattr(self, field) or 0) == 3 for field in self.SCORE_FIELDS) and self.determined_income:
            self.recommendation = self.RECOMMEND_APPROVE
        elif all(int(getattr(self, field) or 0) >= 2 for field in self.SCORE_FIELDS):
            self.recommendation = self.RECOMMEND_REVIEW
        else:
            self.recommendation = self.RECOMMEND_REVIEW
        return self.recommendation

    def complete(self, user):
        self.completed_by = user
        self.completed_at = timezone.now()
        self.calculate_outcome()

    @property
    def recommendation_label(self):
        return dict(self.RECOMMENDATION_CHOICES).get(self.recommendation, "Needs Review")

    @property
    def recommendation_badge_class(self):
        return {
            self.RECOMMEND_APPROVE: "call-result--approve",
            self.RECOMMEND_REVIEW: "call-result--review",
            self.RECOMMEND_REJECT: "call-result--reject",
        }.get(self.recommendation, "call-result--review")

    def __str__(self):
        return f"Customer call questionnaire for {self.application_id}"


class ReviewQuestion(models.Model):
    """
    Structured questionnaire question for a review stage.
    Questions are configurable per stage and can be risk-weighted.
    """

    STAGE_IDENTITY = "identity"
    STAGE_ADDRESS = "address"
    STAGE_CUSTOMER_CALL = "customer_call"
    STAGE_GUARANTOR_CALL = "guarantor_call"
    STAGE_EMPLOYER_CALL = "employer_call"
    STAGE_INCOME = "income"
    STAGE_FINAL = "final_review"

    STAGE_CHOICES = [
        (STAGE_IDENTITY, "Identity Check"),
        (STAGE_ADDRESS, "Address Check"),
        (STAGE_CUSTOMER_CALL, "Customer Call"),
        (STAGE_GUARANTOR_CALL, "Guarantor Call"),
        (STAGE_EMPLOYER_CALL, "Employer / Income Call"),
        (STAGE_INCOME, "Income Check"),
        (STAGE_FINAL, "Final Review"),
    ]

    ANSWER_YES_NO = "yes_no"
    ANSWER_TEXT = "text"
    ANSWER_NUMBER = "number"
    ANSWER_CHOICE = "choice"
    ANSWER_FILE = "file"
    ANSWER_AUDIO = "audio"

    ANSWER_TYPE_CHOICES = [
        (ANSWER_YES_NO, "Yes / No"),
        (ANSWER_TEXT, "Text"),
        (ANSWER_NUMBER, "Number"),
        (ANSWER_CHOICE, "Choice"),
        (ANSWER_FILE, "File"),
        (ANSWER_AUDIO, "Audio"),
    ]

    stage = models.CharField(max_length=30, choices=STAGE_CHOICES)
    question_key = models.CharField(max_length=80, unique=True)
    question_text = models.TextField()
    help_text = models.TextField(blank=True)
    answer_type = models.CharField(max_length=20, choices=ANSWER_TYPE_CHOICES, default=ANSWER_YES_NO)
    required = models.BooleanField(default=True)
    risk_weight = models.PositiveIntegerField(default=1)
    fail_if_no = models.BooleanField(
        default=False,
        help_text="If True, a 'No' answer flags this as a risk factor",
    )
    order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["stage", "order", "id"]
        verbose_name = "Review Question"
        verbose_name_plural = "Review Questions"

    def __str__(self):
        return f"[{self.stage}] {self.question_key}"


class ReviewAnswer(models.Model):
    """
    Answer to a structured review question for a specific application review.
    """

    application = models.ForeignKey(
        FinancingApplication,
        on_delete=models.CASCADE,
        related_name="review_answers",
    )
    review = models.ForeignKey(
        UnderwriterReview,
        on_delete=models.CASCADE,
        related_name="structured_answers",
    )
    question = models.ForeignKey(
        ReviewQuestion,
        on_delete=models.CASCADE,
        related_name="answers",
    )
    answer_value = models.TextField(blank=True)
    answered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="review_answers_given",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("review", "question")]
        ordering = ["question__order"]
        verbose_name = "Review Answer"
        verbose_name_plural = "Review Answers"

    def __str__(self):
        return f"{self.question.question_key}: {self.answer_value[:40]}"
