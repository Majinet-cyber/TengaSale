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
    multiplier = models.DecimalField(max_digits=5, decimal_places=2)
    monthly_ceiling_mwk = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code

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

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, blank=True, null=True)
    phone_number = models.CharField(max_length=30, blank=True)
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
    date_joined_company = models.DateField(null=True, blank=True)
    avatar_initials = models.CharField(max_length=4, blank=True)

    @property
    def display_name(self):
        return self.user.get_full_name() or self.user.username

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
            self.rank_multiplier = profile.rank.multiplier
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
