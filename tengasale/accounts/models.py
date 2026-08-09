from django.conf import settings
from django.db import models
from django.utils import timezone
from decimal import Decimal


class Department(models.Model):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Rank(models.Model):
    code = models.CharField(max_length=3, unique=True)
    title = models.CharField(max_length=80, blank=True)
    base_salary_default_mwk = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    volt_rate_default_mwk = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    multiplier = models.DecimalField(max_digits=5, decimal_places=2)
    rank_multiplier = models.DecimalField(max_digits=5, decimal_places=2, default=1)
    monthly_ceiling_mwk = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    is_board_controlled = models.BooleanField(default=False)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code

    def save(self, *args, **kwargs):
        if not self.rank_multiplier:
            self.rank_multiplier = self.multiplier
        if self.rank_multiplier != self.multiplier:
            self.multiplier = self.rank_multiplier
        super().save(*args, **kwargs)

    @property
    def ceiling_label(self):
        if self.monthly_ceiling_mwk is None:
            return "Board controlled"
        return f"MWK {self.monthly_ceiling_mwk:,.0f}"


class StaffRole(models.Model):
    PORTAL_HQ = "hq"
    PORTAL_MERCHANT_ADMIN = "merchant_admin"
    PORTAL_UNDERWRITER = "underwriter"
    PORTAL_MERCHANT = "merchant"
    PORTAL_CUSTOMER = "customer"
    PORTAL_SUPPORT = "support"

    PORTAL_CHOICES = [
        (PORTAL_HQ, "HQ/Admin"),
        (PORTAL_MERCHANT_ADMIN, "Merchant Admin"),
        (PORTAL_UNDERWRITER, "Underwriter"),
        (PORTAL_MERCHANT, "Merchant"),
        (PORTAL_CUSTOMER, "Customer"),
        (PORTAL_SUPPORT, "Support"),
    ]

    code = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=120, unique=True)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="staff_roles")
    portal_role = models.CharField(max_length=30, choices=PORTAL_CHOICES, default=PORTAL_HQ)
    module_permissions = models.JSONField(default=list, blank=True)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["department__name", "name"]

    def __str__(self):
        return self.name


class UserProfile(models.Model):
    ROLE_MERCHANT = "merchant"
    ROLE_MERCHANT_ADMIN = "merchant_admin"
    ROLE_UNDERWRITER = "underwriter"
    ROLE_TECH_SUPPORT = "tech_support"
    ROLE_HQ = "hq"

    ROLE_CHOICES = [
        (ROLE_MERCHANT, "Merchant"),
        (ROLE_MERCHANT_ADMIN, "Merchant Administrator"),
        (ROLE_UNDERWRITER, "Underwriter"),
        (ROLE_TECH_SUPPORT, "Tech Support"),
        (ROLE_HQ, "HQ"),
    ]

    USER_TYPE_FOUNDER = "founder"
    USER_TYPE_STAFF = "staff"
    USER_TYPE_MERCHANT = "merchant"
    USER_TYPE_CUSTOMER = "customer"
    USER_TYPE_EXECUTIVE = "executive"

    USER_TYPE_CHOICES = [
        (USER_TYPE_FOUNDER, "Founder"),
        (USER_TYPE_STAFF, "Staff"),
        (USER_TYPE_MERCHANT, "Merchant"),
        (USER_TYPE_CUSTOMER, "Customer"),
        (USER_TYPE_EXECUTIVE, "Executive"),
    ]

    STATUS_ACTIVE = "active"
    STATUS_INACTIVE = "inactive"
    STATUS_SUSPENDED = "suspended"
    STATUS_TERMINATED = "terminated"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_INACTIVE, "Inactive"),
        (STATUS_SUSPENDED, "Suspended"),
        (STATUS_TERMINATED, "Terminated"),
    ]

    PROBATION_NONE = "none"
    PROBATION_ACTIVE = "active"
    PROBATION_PASSED = "passed"
    PROBATION_EXTENDED = "extended"
    PROBATION_FAILED = "failed"

    PROBATION_CHOICES = [
        (PROBATION_NONE, "None"),
        (PROBATION_ACTIVE, "On probation"),
        (PROBATION_PASSED, "Passed"),
        (PROBATION_EXTENDED, "Extended"),
        (PROBATION_FAILED, "Failed"),
    ]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    full_name = models.CharField(max_length=180, blank=True)
    email = models.EmailField(blank=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, blank=True, null=True)
    phone_number = models.CharField(max_length=30, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="profiles")
    staff_role = models.ForeignKey(StaffRole, null=True, blank=True, on_delete=models.SET_NULL, related_name="profiles")
    rank = models.ForeignKey(Rank, null=True, blank=True, on_delete=models.SET_NULL, related_name="profiles")
    supervisor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="direct_reports",
    )
    user_type = models.CharField(max_length=20, choices=USER_TYPE_CHOICES, blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    is_founder = models.BooleanField(default=False)
    founder_equity_record = models.OneToOneField(
        "accounts.FounderEquityRecord",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="profile_record",
    )
    date_joined_company = models.DateField(null=True, blank=True)
    probation_status = models.CharField(max_length=20, choices=PROBATION_CHOICES, default=PROBATION_NONE)
    earnings_lock_enabled = models.BooleanField(default=False)
    earnings_pin_hash = models.CharField(max_length=255, blank=True, editable=False)
    earnings_lock_created_at = models.DateTimeField(null=True, blank=True, editable=False)
    earnings_pin_updated_at = models.DateTimeField(null=True, blank=True, editable=False)
    earnings_failed_attempt_count = models.PositiveSmallIntegerField(default=0, editable=False)
    earnings_locked_until = models.DateTimeField(null=True, blank=True, editable=False)
    earnings_recovery_phone = models.CharField(max_length=30, blank=True, editable=False)
    earnings_recovery_verified_at = models.DateTimeField(null=True, blank=True, editable=False)
    earnings_security_generation = models.PositiveIntegerField(default=1, editable=False)
    next_review_date = models.DateField(null=True, blank=True)
    base_salary_mwk = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    volt_rate_mwk = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    monthly_ceiling_mwk = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    can_approve_volts = models.BooleanField(default=False)
    can_approve_discipline = models.BooleanField(default=False)
    can_approve_payouts = models.BooleanField(default=False)
    can_override_scores = models.BooleanField(default=False)
    avatar_initials = models.CharField(max_length=4, blank=True)

    @property
    def display_name(self):
        return self.full_name or self.user.get_full_name() or self.user.username

    @property
    def public_email(self):
        return self.email or self.user.email

    @property
    def public_phone(self):
        return self.phone or self.phone_number

    @property
    def effective_base_salary_mwk(self):
        if self.base_salary_mwk is not None:
            return self.base_salary_mwk
        if self.rank:
            return self.rank.base_salary_default_mwk
        return Decimal("0")

    @property
    def effective_volt_rate_mwk(self):
        if self.volt_rate_mwk is not None:
            return self.volt_rate_mwk
        if self.rank:
            return self.rank.volt_rate_default_mwk
        return Decimal("0")

    @property
    def effective_monthly_ceiling_mwk(self):
        if self.monthly_ceiling_mwk is not None:
            return self.monthly_ceiling_mwk
        if self.rank:
            return self.rank.monthly_ceiling_mwk
        return None

    @property
    def initials(self):
        if self.avatar_initials:
            return self.avatar_initials
        parts = [part[:1] for part in self.display_name.split() if part]
        return "".join(parts[:2]).upper() or self.user.username[:2].upper()

    def __str__(self):
        return f"{self.user.username} - {self.role or 'No role'}"


class VoltsActionType(models.Model):
    name = models.CharField(max_length=160, unique=True)
    code = models.SlugField(max_length=140, blank=True, db_index=True)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="volts_action_types")
    base_volts = models.DecimalField(max_digits=10, decimal_places=2)
    requires_approval = models.BooleanField(default=True)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["department__name", "name"]

    def __str__(self):
        return self.name


class VoltsTransaction(models.Model):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_REVERSED = "reversed"
    STATUS_PAID = "paid"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_REVERSED, "Reversed"),
        (STATUS_PAID, "Paid"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="volts_transactions")
    action_type = models.ForeignKey(VoltsActionType, on_delete=models.PROTECT, related_name="transactions")
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="volts_transactions")
    base_volts = models.DecimalField(max_digits=10, decimal_places=2)
    rank_multiplier = models.DecimalField(max_digits=5, decimal_places=2, default=1)
    quality_score = models.DecimalField(max_digits=5, decimal_places=2, default=1)
    discipline_score = models.DecimalField(max_digits=5, decimal_places=2, default=1)
    results_score = models.DecimalField(max_digits=5, decimal_places=2, default=1)
    final_volts = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    monetary_value_per_volt = models.DecimalField(max_digits=10, decimal_places=2, default=100)
    estimated_amount_mwk = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    evidence_text = models.TextField(blank=True)
    evidence_file = models.FileField(upload_to="volts_evidence/", blank=True, null=True)
    related_application = models.ForeignKey(
        "applications.FinancingApplication",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="volts_transactions",
    )
    related_contract = models.ForeignKey(
        "contracts.Contract",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="volts_transactions",
    )
    related_merchant = models.ForeignKey(
        "merchants.Merchant",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="volts_transactions",
    )
    related_payment = models.ForeignKey(
        "portal.PaymentTransaction",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="volts_transactions",
    )
    related_task_reference = models.CharField(max_length=120, blank=True)
    ceiling_adjustment_mwk = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_volts_transactions",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status", "created_at"]),
            models.Index(fields=["department", "status", "created_at"]),
        ]

    def calculate(self):
        self.department = self.department or self.action_type.department
        self.base_volts = self.base_volts or self.action_type.base_volts
        try:
            profile = self.user.profile
        except Exception:
            profile = None
        if profile and profile.rank:
            self.rank_multiplier = profile.rank.rank_multiplier
        raw_final_volts = (
            self.base_volts
            * self.rank_multiplier
            * self.quality_score
            * self.discipline_score
            * self.results_score
        )
        self.final_volts = raw_final_volts.quantize(Decimal("0.01"))
        self.estimated_amount_mwk = (raw_final_volts * self.monetary_value_per_volt).quantize(Decimal("0.01"))
        return self.final_volts

    def save(self, *args, **kwargs):
        self.calculate()
        super().save(*args, **kwargs)

    def approve(self, approver):
        if approver == self.user:
            raise ValueError("Users cannot approve their own volts transaction.")
        self.status = self.STATUS_APPROVED
        self.approved_by = approver
        self.approved_at = timezone.now()
        self.save(update_fields=["status", "approved_by", "approved_at", "department", "base_volts", "rank_multiplier", "final_volts", "estimated_amount_mwk", "updated_at"])

    def __str__(self):
        return f"{self.user} - {self.action_type} - {self.final_volts} volts"


class PenaltyType(models.Model):
    name = models.CharField(max_length=160, unique=True)
    default_volts_deducted = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class PenaltyTransaction(models.Model):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_REVERSED = "reversed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_REVERSED, "Reversed"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="penalty_transactions")
    penalty_type = models.ForeignKey(PenaltyType, on_delete=models.PROTECT, related_name="transactions")
    volts_deducted = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    percentage_deduction = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    reason = models.TextField()
    evidence = models.TextField(blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_penalties",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.volts_deducted:
            self.volts_deducted = self.penalty_type.default_volts_deducted
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user} - {self.penalty_type}"


class CompensationCycle(models.Model):
    name = models.CharField(max_length=120, unique=True)
    start_day = models.PositiveSmallIntegerField(default=25)
    end_day = models.PositiveSmallIntegerField(default=24)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.start_day} to {self.end_day})"


class DisciplineEventType(models.Model):
    SEVERITY_LOW = "low"
    SEVERITY_MEDIUM = "medium"
    SEVERITY_HIGH = "high"
    SEVERITY_CRITICAL = "critical"

    SEVERITY_CHOICES = [
        (SEVERITY_LOW, "Low"),
        (SEVERITY_MEDIUM, "Medium"),
        (SEVERITY_HIGH, "High"),
        (SEVERITY_CRITICAL, "Critical"),
    ]

    name = models.CharField(max_length=180, unique=True)
    code = models.SlugField(max_length=160, unique=True)
    description = models.TextField(blank=True)
    deduction_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    bonus_percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default=SEVERITY_LOW)
    evidence_required = models.BooleanField(default=False)
    auto_generated = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["severity", "name"]

    def __str__(self):
        return self.name


class DisciplineScorePeriod(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_PENDING_REVIEW = "pending_review"
    STATUS_REVIEWED = "reviewed"
    STATUS_DISPUTED = "disputed"
    STATUS_APPROVED = "approved"
    STATUS_LOCKED = "locked"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_PENDING_REVIEW, "Pending review"),
        (STATUS_REVIEWED, "Reviewed"),
        (STATUS_DISPUTED, "Disputed"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_LOCKED, "Locked"),
    ]

    DISPUTE_NONE = "none"
    DISPUTE_SUBMITTED = "submitted"
    DISPUTE_UNDER_REVIEW = "under_review"
    DISPUTE_RESOLVED = "resolved"
    DISPUTE_REJECTED = "rejected"

    DISPUTE_CHOICES = [
        (DISPUTE_NONE, "None"),
        (DISPUTE_SUBMITTED, "Submitted"),
        (DISPUTE_UNDER_REVIEW, "Under review"),
        (DISPUTE_RESOLVED, "Resolved"),
        (DISPUTE_REJECTED, "Rejected"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="discipline_score_periods")
    month = models.DateField()
    opening_score = models.DecimalField(max_digits=5, decimal_places=2, default=100)
    automatic_penalties_total = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    manual_adjustments_total = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    bonus_points_total = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    final_score = models.DecimalField(max_digits=5, decimal_places=2, default=100)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_discipline_periods")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="approved_discipline_periods")
    approved_at = models.DateTimeField(null=True, blank=True)
    payout_impact_approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="payout_approved_discipline_periods")
    payout_impact_approved_at = models.DateTimeField(null=True, blank=True)
    user_acknowledged_at = models.DateTimeField(null=True, blank=True)
    user_response_text = models.TextField(blank=True)
    dispute_status = models.CharField(max_length=24, choices=DISPUTE_CHOICES, default=DISPUTE_NONE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-month", "user__username"]
        unique_together = [("user", "month")]
        indexes = [models.Index(fields=["user", "month", "status"])]

    def recalculate(self):
        accepted_events = self.events.filter(status=DisciplineEvent.STATUS_ACCEPTED)
        self.automatic_penalties_total = sum(
            (event.deduction_percentage for event in accepted_events.filter(source=DisciplineEvent.SOURCE_AUTOMATIC)),
            Decimal("0"),
        )
        manual_penalties = sum(
            (event.deduction_percentage for event in accepted_events.exclude(source=DisciplineEvent.SOURCE_AUTOMATIC)),
            Decimal("0"),
        )
        self.manual_adjustments_total = manual_penalties
        self.bonus_points_total = sum((event.bonus_percentage or Decimal("0") for event in accepted_events), Decimal("0"))
        score = self.opening_score - self.automatic_penalties_total - self.manual_adjustments_total + self.bonus_points_total
        self.final_score = max(Decimal("0"), min(Decimal("110"), score)).quantize(Decimal("0.01"))
        return self.final_score

    @property
    def payout_score(self):
        return min(self.final_score, Decimal("100"))

    @property
    def band_label(self):
        score = self.final_score
        if score >= 95:
            return "Excellent discipline"
        if score >= 85:
            return "Good discipline"
        if score >= 70:
            return "Acceptable but needs improvement"
        if score >= 50:
            return "Poor discipline"
        return "Serious concern"

    def approve(self, approver):
        if approver_id := getattr(approver, "id", None):
            if approver_id == self.user_id:
                raise ValueError("Users cannot approve their own discipline score.")
        self.recalculate()
        self.status = self.STATUS_APPROVED
        self.approved_by = approver
        self.approved_at = timezone.now()
        self.save()

    def acknowledge(self):
        self.user_acknowledged_at = timezone.now()
        self.save(update_fields=["user_acknowledged_at", "updated_at"])

    def save(self, *args, **kwargs):
        if self.month:
            self.month = self.month.replace(day=1)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user} discipline {self.month:%Y-%m}"


class DisciplineEvent(models.Model):
    SOURCE_AUTOMATIC = "automatic"
    SOURCE_MANAGER = "manager"
    SOURCE_AUDIT = "audit"
    SOURCE_QC = "qc"
    SOURCE_CUSTOMER_SUPPORT = "customer_support"
    SOURCE_FINANCE = "finance"
    SOURCE_LEGAL = "legal"

    SOURCE_CHOICES = [
        (SOURCE_AUTOMATIC, "Automatic"),
        (SOURCE_MANAGER, "Manager"),
        (SOURCE_AUDIT, "Audit"),
        (SOURCE_QC, "QC"),
        (SOURCE_CUSTOMER_SUPPORT, "Customer Support"),
        (SOURCE_FINANCE, "Finance"),
        (SOURCE_LEGAL, "Legal"),
    ]

    STATUS_PENDING = "pending"
    STATUS_ACCEPTED = "accepted"
    STATUS_REJECTED = "rejected"
    STATUS_REVERSED = "reversed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_REVERSED, "Reversed"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="discipline_events")
    period = models.ForeignKey(DisciplineScorePeriod, on_delete=models.CASCADE, related_name="events")
    event_type = models.ForeignKey(DisciplineEventType, on_delete=models.PROTECT, related_name="events")
    source = models.CharField(max_length=30, choices=SOURCE_CHOICES, default=SOURCE_MANAGER)
    deduction_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    bonus_percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    evidence_text = models.TextField(blank=True)
    evidence_file = models.FileField(upload_to="discipline_evidence/", blank=True, null=True)
    related_application = models.ForeignKey("applications.FinancingApplication", null=True, blank=True, on_delete=models.SET_NULL, related_name="discipline_events")
    related_contract = models.ForeignKey("contracts.Contract", null=True, blank=True, on_delete=models.SET_NULL, related_name="discipline_events")
    related_merchant = models.ForeignKey("merchants.Merchant", null=True, blank=True, on_delete=models.SET_NULL, related_name="discipline_events")
    related_payment = models.ForeignKey("portal.PaymentTransaction", null=True, blank=True, on_delete=models.SET_NULL, related_name="discipline_events")
    related_task_reference = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_discipline_events")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_discipline_events")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="approved_discipline_events")
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "status", "created_at"])]

    def save(self, *args, **kwargs):
        if not self.deduction_percentage:
            self.deduction_percentage = self.event_type.deduction_percentage
        if self.bonus_percentage is None:
            self.bonus_percentage = self.event_type.bonus_percentage
        super().save(*args, **kwargs)

    def approve(self, approver):
        if approver == self.user:
            raise ValueError("Users cannot approve their own discipline event.")
        if self.event_type.evidence_required and not (self.evidence_text or self.evidence_file):
            raise ValueError("Evidence is required for this discipline event.")
        self.status = self.STATUS_ACCEPTED
        self.approved_by = approver
        self.approved_at = timezone.now()
        self.save()
        self.period.recalculate()
        self.period.save(update_fields=[
            "automatic_penalties_total",
            "manual_adjustments_total",
            "bonus_points_total",
            "final_score",
            "updated_at",
        ])

    def __str__(self):
        return f"{self.user} - {self.event_type}"


class DisciplineDispute(models.Model):
    STATUS_SUBMITTED = "submitted"
    STATUS_UNDER_REVIEW = "under_review"
    STATUS_ACCEPTED = "accepted"
    STATUS_PARTIALLY_ACCEPTED = "partially_accepted"
    STATUS_REJECTED = "rejected"
    STATUS_RESOLVED = "resolved"

    STATUS_CHOICES = [
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_UNDER_REVIEW, "Under review"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_PARTIALLY_ACCEPTED, "Partially accepted"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_RESOLVED, "Resolved"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="discipline_disputes")
    discipline_score_period = models.ForeignKey(DisciplineScorePeriod, on_delete=models.CASCADE, related_name="disputes")
    discipline_event = models.ForeignKey(DisciplineEvent, null=True, blank=True, on_delete=models.SET_NULL, related_name="disputes")
    dispute_text = models.TextField()
    supporting_file = models.FileField(upload_to="discipline_disputes/", blank=True, null=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_SUBMITTED)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_discipline_disputes")
    resolution_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def resolve(self, reviewer, status, notes=""):
        if reviewer == self.user:
            raise ValueError("Users cannot resolve their own discipline dispute.")
        self.reviewed_by = reviewer
        self.status = status
        self.resolution_notes = notes
        self.resolved_at = timezone.now()
        self.save()

    def __str__(self):
        return f"{self.user} dispute {self.status}"


class KPITemplate(models.Model):
    role = models.ForeignKey(StaffRole, null=True, blank=True, on_delete=models.SET_NULL, related_name="kpi_templates")
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="kpi_templates")
    name = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    target_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    weight = models.DecimalField(max_digits=6, decimal_places=2, default=1)
    lower_is_better = models.BooleanField(default=False)
    data_source = models.CharField(max_length=120, blank=True)
    affects_quality_score = models.BooleanField(default=False)
    affects_discipline_score = models.BooleanField(default=False)
    affects_results_score = models.BooleanField(default=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["department__name", "role__name", "name"]
        unique_together = [("role", "department", "name")]

    def __str__(self):
        return self.name


class KPIResult(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_REVIEWED = "reviewed"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_REVIEWED, "Reviewed"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="kpi_results")
    kpi_template = models.ForeignKey(KPITemplate, on_delete=models.PROTECT, related_name="results")
    period = models.DateField()
    actual_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    target_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    score = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    weighted_score = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    evidence = models.TextField(blank=True)
    calculated_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_kpi_results")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="approved_kpi_results")

    class Meta:
        ordering = ["-period", "user__username", "kpi_template__name"]
        unique_together = [("user", "kpi_template", "period")]

    def calculate(self):
        target = self.target_value or self.kpi_template.target_value
        self.target_value = target
        if not target:
            self.score = Decimal("0")
        elif self.kpi_template.lower_is_better:
            self.score = min(Decimal("110"), (target / max(self.actual_value, Decimal("0.01"))) * Decimal("100"))
        else:
            self.score = min(Decimal("110"), (self.actual_value / target) * Decimal("100"))
        self.weighted_score = (self.score * self.kpi_template.weight).quantize(Decimal("0.01"))
        self.calculated_at = timezone.now()
        return self.score

    def save(self, *args, **kwargs):
        if not self.target_value and self.kpi_template_id:
            self.target_value = self.kpi_template.target_value
        self.calculate()
        super().save(*args, **kwargs)

    def approve(self, approver):
        if approver == self.user:
            raise ValueError("Users cannot approve their own KPI result.")
        self.status = self.STATUS_APPROVED
        self.approved_by = approver
        self.save(update_fields=["status", "approved_by", "score", "weighted_score", "target_value", "calculated_at"])

    def __str__(self):
        return f"{self.user} - {self.kpi_template} - {self.period:%Y-%m}"


class StaffDocument(models.Model):
    DOC_EMPLOYMENT_AGREEMENT = "employment_agreement"
    DOC_COMPENSATION_ANNEXURE = "compensation_annexure_a"
    DOC_KPI_VOLTS_ANNEXURE = "kpi_volts_annexure_b"
    DOC_MONTHLY_VOLTS = "monthly_volts_statement"
    DOC_MONTHLY_REVIEW = "monthly_performance_review"
    DOC_DISCIPLINE_REPORT = "discipline_score_report"
    DOC_PROMOTION_REVIEW = "promotion_rank_review"
    DOC_WARNING_LETTER = "warning_letter"
    DOC_TERMINATION_LETTER = "termination_letter"

    DOC_CHOICES = [
        (DOC_EMPLOYMENT_AGREEMENT, "Employment Agreement"),
        (DOC_COMPENSATION_ANNEXURE, "Compensation Annexure A"),
        (DOC_KPI_VOLTS_ANNEXURE, "KPI & Volts Annexure B"),
        (DOC_MONTHLY_VOLTS, "Monthly Volts Statement"),
        (DOC_MONTHLY_REVIEW, "Monthly Performance Review"),
        (DOC_DISCIPLINE_REPORT, "Discipline Score Report"),
        (DOC_PROMOTION_REVIEW, "Promotion/Rank Review"),
        (DOC_WARNING_LETTER, "Warning Letter"),
        (DOC_TERMINATION_LETTER, "Termination Letter"),
    ]

    # ── extended document types ─────────────────────────────────────────────
    DOC_SUSPENSION_LETTER = "suspension_letter"
    DOC_APPOINTMENT_LETTER = "appointment_letter"
    DOC_CONFIDENTIALITY_AGREEMENT = "confidentiality_agreement"
    DOC_ROLE_RESPONSIBILITY = "role_responsibility_schedule"

    # Add new choices while keeping old ones intact
    DOC_CHOICES = [
        (DOC_EMPLOYMENT_AGREEMENT, "Employment Agreement"),
        (DOC_COMPENSATION_ANNEXURE, "Compensation Annexure A"),
        (DOC_KPI_VOLTS_ANNEXURE, "KPI & Volts Annexure B"),
        (DOC_MONTHLY_VOLTS, "Monthly Volts Statement"),
        (DOC_MONTHLY_REVIEW, "Monthly Performance Review"),
        (DOC_DISCIPLINE_REPORT, "Discipline Score Report"),
        (DOC_PROMOTION_REVIEW, "Promotion/Rank Review"),
        (DOC_WARNING_LETTER, "Warning Letter"),
        (DOC_TERMINATION_LETTER, "Termination Letter"),
        (DOC_SUSPENSION_LETTER, "Suspension Letter"),
        (DOC_APPOINTMENT_LETTER, "Appointment Letter"),
        (DOC_CONFIDENTIALITY_AGREEMENT, "Confidentiality & Data Protection Agreement"),
        (DOC_ROLE_RESPONSIBILITY, "Role Responsibility Schedule"),
    ]

    # ── document status workflow ────────────────────────────────────────────
    STATUS_DRAFT    = "draft"
    STATUS_PENDING  = "pending_signature"
    STATUS_SIGNED   = "signed"
    STATUS_ISSUED   = "issued"
    STATUS_REVOKED  = "revoked"
    STATUS_ARCHIVED = "archived"

    STATUS_CHOICES = [
        (STATUS_DRAFT,   "Draft"),
        (STATUS_PENDING, "Pending CEO Signature"),
        (STATUS_SIGNED,  "Signed"),
        (STATUS_ISSUED,  "Issued"),
        (STATUS_REVOKED, "Revoked"),
        (STATUS_ARCHIVED,"Archived"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="staff_documents")
    document_type = models.CharField(max_length=50, choices=DOC_CHOICES)
    document_number = models.CharField(max_length=80, unique=True)
    version = models.CharField(max_length=20, default="1.0")
    prepared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="prepared_staff_documents",
    )
    signed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="signed_staff_documents",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    # Executive signature snapshot embedded at generation time
    used_signature = models.ForeignKey(
        "ExecutiveSignature", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="used_in_documents",
    )
    generated_at = models.DateTimeField(auto_now_add=True)
    signed_at    = models.DateTimeField(null=True, blank=True)
    issued_at    = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-generated_at"]

    def __str__(self):
        return self.document_number


class FounderEquityRecord(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_PAUSED = "paused"
    STATUS_FORFEITED = "forfeited"
    STATUS_COMPLETED = "completed"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_PAUSED, "Paused"),
        (STATUS_FORFEITED, "Forfeited"),
        (STATUS_COMPLETED, "Completed"),
    ]

    founder_user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="founder_equity")
    role_title = models.CharField(max_length=160)
    allocated_shares = models.PositiveIntegerField()
    vested_shares = models.PositiveIntegerField(default=0)
    unvested_shares = models.PositiveIntegerField(default=0)
    vesting_start_date = models.DateField(null=True, blank=True)
    cliff_date = models.DateField(null=True, blank=True)
    vesting_years = models.PositiveSmallIntegerField(default=4)
    vesting_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    inactive_flag = models.BooleanField(default=False)
    breach_flag = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["role_title"]

    def save(self, *args, **kwargs):
        self.unvested_shares = max(self.allocated_shares - self.vested_shares, 0)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.founder_user} - {self.allocated_shares} shares"


class CompanyShareStructure(models.Model):
    total_authorized_shares = models.PositiveIntegerField(default=100000)
    founder_pool = models.PositiveIntegerField(default=70000)
    investor_reserve = models.PositiveIntegerField(default=20000)
    employee_advisor_pool = models.PositiveIntegerField(default=10000)
    notes = models.TextField(
        default="The app tracks equity and vesting internally, but signed legal documents govern actual ownership.",
        blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Company Share Structure"
        verbose_name_plural = "Company Share Structures"

    def __str__(self):
        return f"{self.total_authorized_shares:,} authorized shares"


class ExecutiveSignature(models.Model):
    """
    Stores an authorized executive (CEO/director) signature image for embedding
    in official staff documents.  Only CEO or superadmin can upload/manage.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="executive_signatures",
        help_text="The executive whose signature this is.",
    )
    full_name = models.CharField(max_length=200)
    position  = models.CharField(max_length=200, default="Chief Executive Officer")
    # Stored in a private location; never expose URL publicly without auth
    signature_image = models.ImageField(
        upload_to="signatures/executive/",
        help_text="PNG/JPG of the handwritten or digital signature.",
    )
    is_active  = models.BooleanField(
        default=True,
        help_text="Only one active signature should exist at a time per position.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="signature_uploads",
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="signature_revocations",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Executive Signature"
        verbose_name_plural = "Executive Signatures"

    def __str__(self):
        status = "ACTIVE" if self.is_active else "REVOKED"
        return f"[{status}] {self.full_name} — {self.position}"

    @classmethod
    def get_active(cls):
        """Return the currently active CEO/executive signature, or None."""
        return cls.objects.filter(is_active=True).first()
