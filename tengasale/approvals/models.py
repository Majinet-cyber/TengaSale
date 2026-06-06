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
