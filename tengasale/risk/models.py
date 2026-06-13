"""
risk/models.py

Credit risk and identity models for TengaSale.
Supports credit scoring, identity profiling, external provider checks,
and device check readiness for Emajinet ID, warrant checks, and credit bureaus.
"""
import hashlib

from django.conf import settings
from django.db import models


class CustomerIdentityProfile(models.Model):
    VERIFICATION_PENDING = "pending"
    VERIFICATION_VERIFIED = "verified"
    VERIFICATION_FAILED = "failed"
    VERIFICATION_MANUAL = "manual"

    VERIFICATION_CHOICES = [
        (VERIFICATION_PENDING, "Pending"),
        (VERIFICATION_VERIFIED, "Verified"),
        (VERIFICATION_FAILED, "Failed"),
        (VERIFICATION_MANUAL, "Manual Review"),
    ]

    application = models.OneToOneField(
        "applications.FinancingApplication",
        on_delete=models.CASCADE,
        related_name="identity_profile",
    )
    emajinet_id = models.CharField(max_length=80, blank=True, null=True)
    national_id_hash = models.CharField(max_length=64, blank=True)
    phone_hash = models.CharField(max_length=64, blank=True)
    verification_status = models.CharField(
        max_length=20, choices=VERIFICATION_CHOICES, default=VERIFICATION_PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Customer Identity Profile"

    def set_national_id_hash(self, national_id: str) -> None:
        self.national_id_hash = hashlib.sha256(national_id.strip().upper().encode()).hexdigest()

    def set_phone_hash(self, phone: str) -> None:
        digits = "".join(ch for ch in phone if ch.isdigit())
        self.phone_hash = hashlib.sha256(digits.encode()).hexdigest()

    def __str__(self):
        return f"IdentityProfile for {self.application_id}"


class CreditRiskAssessment(models.Model):
    RISK_LOW = "low"
    RISK_MEDIUM = "medium"
    RISK_HIGH = "high"
    RISK_MANUAL = "manual_review"

    RISK_CHOICES = [
        (RISK_LOW, "Low Risk"),
        (RISK_MEDIUM, "Medium Risk"),
        (RISK_HIGH, "High Risk"),
        (RISK_MANUAL, "Manual Review"),
    ]

    DEPOSIT_LOW = 15
    DEPOSIT_MEDIUM = 20
    DEPOSIT_HIGH = 30

    application = models.OneToOneField(
        "applications.FinancingApplication",
        on_delete=models.CASCADE,
        related_name="credit_assessment",
    )

    affordability_score = models.IntegerField(default=0, help_text="Income vs repayment ratio score (0-100)")
    identity_score = models.IntegerField(default=0, help_text="KYC completeness and match score (0-100)")
    repayment_risk_score = models.IntegerField(default=0, help_text="Risk of non-repayment (0-100, higher = lower risk)")
    data_quality_score = models.IntegerField(default=0, help_text="Application completeness score (0-100)")
    final_score = models.IntegerField(default=0, help_text="Weighted final risk score (0-100)")
    risk_band = models.CharField(max_length=20, choices=RISK_CHOICES, default=RISK_MANUAL)
    recommended_deposit_percent = models.DecimalField(max_digits=5, decimal_places=2, default=20)
    reasons = models.JSONField(
        default=list,
        help_text="List of human-readable reasons for this assessment",
    )
    assessed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="credit_assessments_performed",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Credit Risk Assessment"

    @property
    def risk_band_label(self):
        return dict(self.RISK_CHOICES).get(self.risk_band, self.risk_band)

    @property
    def deposit_explanation(self):
        return (
            f"Recommended {self.recommended_deposit_percent}% deposit because: "
            + "; ".join(self.reasons or ["insufficient data for assessment"])
        )

    def __str__(self):
        return f"CreditRisk for app {self.application_id} — {self.risk_band}"


class ExternalCheck(models.Model):
    PROVIDER_EMAJINET = "emajinet_id"
    PROVIDER_WARRANT = "warrant_check"
    PROVIDER_CREDIT_BUREAU = "credit_bureau"
    PROVIDER_INTERNAL = "internal_history"

    PROVIDER_CHOICES = [
        (PROVIDER_EMAJINET, "Emajinet ID"),
        (PROVIDER_WARRANT, "Warrant Check"),
        (PROVIDER_CREDIT_BUREAU, "Credit Bureau"),
        (PROVIDER_INTERNAL, "Internal History"),
    ]

    STATUS_PENDING = "pending"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_SKIPPED = "skipped"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
        (STATUS_SKIPPED, "Skipped"),
    ]

    application = models.ForeignKey(
        "applications.FinancingApplication",
        on_delete=models.CASCADE,
        related_name="external_checks",
    )
    provider = models.CharField(max_length=30, choices=PROVIDER_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    request_reference = models.CharField(max_length=120, blank=True)
    response_summary = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "External Check"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.provider} for app {self.application_id} [{self.status}]"


class FraudCheck(models.Model):
    """
    Records a duplicate-customer / existing-exposure fraud check
    performed before or during underwriter review.
    """

    RISK_NONE = "none"
    RISK_LOW = "low"
    RISK_MEDIUM = "medium"
    RISK_HIGH = "high"
    RISK_BLOCK = "block"

    RISK_CHOICES = [
        (RISK_NONE, "No Risk"),
        (RISK_LOW, "Low Risk"),
        (RISK_MEDIUM, "Medium Risk"),
        (RISK_HIGH, "High Risk"),
        (RISK_BLOCK, "Block — Do Not Proceed"),
    ]

    ACTION_PROCEED = "proceed"
    ACTION_MANUAL = "manual_review"
    ACTION_HQ = "require_hq_approval"
    ACTION_BLOCK = "block_new_contract"

    ACTION_CHOICES = [
        (ACTION_PROCEED, "Proceed"),
        (ACTION_MANUAL, "Manual Review"),
        (ACTION_HQ, "Require HQ Approval"),
        (ACTION_BLOCK, "Block New Contract"),
    ]

    RESOLUTION_OPEN = "open"
    RESOLUTION_CLEARED = "cleared"
    RESOLUTION_BLOCKED = "blocked"
    RESOLUTION_HQ_OVERRIDE = "hq_override"

    RESOLUTION_CHOICES = [
        (RESOLUTION_OPEN, "Open"),
        (RESOLUTION_CLEARED, "Cleared"),
        (RESOLUTION_BLOCKED, "Blocked"),
        (RESOLUTION_HQ_OVERRIDE, "HQ Override"),
    ]

    application = models.ForeignKey(
        "applications.FinancingApplication",
        on_delete=models.CASCADE,
        related_name="fraud_checks",
    )
    national_id_hash = models.CharField(max_length=64, blank=True, db_index=True)
    phone_hash = models.CharField(max_length=64, blank=True, db_index=True)
    result = models.JSONField(default=dict, blank=True)
    risk_level = models.CharField(max_length=10, choices=RISK_CHOICES, default=RISK_NONE)
    recommended_action = models.CharField(max_length=30, choices=ACTION_CHOICES, default=ACTION_PROCEED)
    checked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fraud_checks_performed",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fraud_checks_resolved",
    )
    resolution_status = models.CharField(max_length=20, choices=RESOLUTION_CHOICES, default=RESOLUTION_OPEN)
    resolution_notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "Fraud Check"
        ordering = ["-created_at"]

    @property
    def is_blocking(self):
        return self.risk_level in (self.RISK_HIGH, self.RISK_BLOCK)

    def __str__(self):
        return f"FraudCheck for app {self.application_id} — {self.risk_level}"


class DeviceConnectivitySignal(models.Model):
    """
    Device connectivity / telemetry snapshot for a financed device.

    Used for BOTH fraud risk detection AND support diagnostics:
    - Offline for many days with arrears → risk signal
    - Offline after payment / unlock request → support/sync signal

    Principle: offline days are a risk/support signal, not automatic proof of fraud.
    Do not label customers as fraudsters without HQ review.
    """

    # ── Provider ──────────────────────────────────────────────────────
    PROVIDER_PAYTRIGGER = "paytrigger"
    PROVIDER_UPYA = "upya"
    PROVIDER_MANUAL = "manual"
    PROVIDER_MOCK = "mock"

    PROVIDER_CHOICES = [
        (PROVIDER_PAYTRIGGER, "PayTrigger"),
        (PROVIDER_UPYA, "Upya"),
        (PROVIDER_MANUAL, "Manual Entry"),
        (PROVIDER_MOCK, "Mock / Placeholder"),
    ]

    # ── Lock state ────────────────────────────────────────────────────
    LOCK_ACTIVE = "active"
    LOCK_WARNING = "warning"
    LOCK_LOCKED = "locked"
    LOCK_RELEASED = "released"
    LOCK_UNKNOWN = "unknown"

    LOCK_STATE_CHOICES = [
        (LOCK_ACTIVE, "Active"),
        (LOCK_WARNING, "Warning"),
        (LOCK_LOCKED, "Locked"),
        (LOCK_RELEASED, "Released"),
        (LOCK_UNKNOWN, "Unknown"),
    ]

    # ── Risk band ─────────────────────────────────────────────────────
    RISK_NORMAL = "normal"
    RISK_WATCH = "watch"
    RISK_WARNING = "warning"
    RISK_HIGH = "high"
    RISK_CRITICAL = "critical"

    RISK_BAND_CHOICES = [
        (RISK_NORMAL, "Normal"),
        (RISK_WATCH, "Watch"),
        (RISK_WARNING, "Warning"),
        (RISK_HIGH, "High Risk"),
        (RISK_CRITICAL, "Critical"),
    ]

    # ── Connectivity status (support view) ────────────────────────────
    CONN_RECENTLY_ONLINE = "recently_online"
    CONN_OFFLINE_PAID = "offline_paid"
    CONN_OFFLINE_ARREARS = "offline_arrears"
    CONN_OFFLINE_AFTER_UNLOCK = "offline_after_unlock"
    CONN_OFFLINE_AFTER_PAYMENT = "offline_after_payment"
    CONN_OFFLINE_BEFORE_LOCK = "offline_before_lock"
    CONN_OFFLINE_AFTER_LOCK = "offline_after_lock"
    CONN_SYNC_PENDING = "sync_pending"
    CONN_NEEDS_INTERNET = "needs_internet"
    CONN_POSSIBLE_EVASION = "possible_evasion"
    CONN_RECOVERY_REVIEW = "recovery_review"
    CONN_UNKNOWN = "unknown"

    CONNECTIVITY_STATUS_CHOICES = [
        (CONN_RECENTLY_ONLINE, "Recently Online"),
        (CONN_OFFLINE_PAID, "Offline — Paid Up"),
        (CONN_OFFLINE_ARREARS, "Offline — With Arrears"),
        (CONN_OFFLINE_AFTER_UNLOCK, "Offline After Unlock Request"),
        (CONN_OFFLINE_AFTER_PAYMENT, "Offline After Payment"),
        (CONN_OFFLINE_BEFORE_LOCK, "Offline Before Lock Command"),
        (CONN_OFFLINE_AFTER_LOCK, "Offline After Lock Command"),
        (CONN_SYNC_PENDING, "Sync Pending"),
        (CONN_NEEDS_INTERNET, "Customer Needs Internet Connection"),
        (CONN_POSSIBLE_EVASION, "Possible Evasion — Needs Review"),
        (CONN_RECOVERY_REVIEW, "Recovery Review Required"),
        (CONN_UNKNOWN, "Unknown"),
    ]

    # ── Sync status ───────────────────────────────────────────────────
    SYNC_SYNCED = "synced"
    SYNC_PENDING_DEVICE = "pending_device_online"
    SYNC_FAILED = "failed"
    SYNC_UNKNOWN = "unknown"

    SYNC_STATUS_CHOICES = [
        (SYNC_SYNCED, "Synced"),
        (SYNC_PENDING_DEVICE, "Pending — Device Must Come Online"),
        (SYNC_FAILED, "Failed"),
        (SYNC_UNKNOWN, "Unknown"),
    ]

    # ── Pending command type ──────────────────────────────────────────
    CMD_LOCK = "lock"
    CMD_UNLOCK = "unlock"
    CMD_NONE = "none"

    PENDING_CMD_CHOICES = [
        (CMD_LOCK, "Lock"),
        (CMD_UNLOCK, "Unlock"),
        (CMD_NONE, "None"),
    ]

    # ── Links ─────────────────────────────────────────────────────────
    application = models.ForeignKey(
        "applications.FinancingApplication",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="connectivity_signals",
    )
    contract = models.ForeignKey(
        "portal.PaymentContract",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="connectivity_signals",
    )
    lock_profile = models.ForeignKey(
        "device_lock.DeviceLockProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="connectivity_signals",
    )

    # ── Parties ───────────────────────────────────────────────────────
    customer_name = models.CharField(max_length=150, blank=True)
    customer_phone = models.CharField(max_length=30, blank=True, db_index=True)
    merchant_name = models.CharField(max_length=150, blank=True)
    underwriter_name = models.CharField(max_length=150, blank=True)

    # ── Device identity ───────────────────────────────────────────────
    imei = models.CharField(max_length=20, blank=True, db_index=True)
    phone_number = models.CharField(max_length=30, blank=True)
    device_model = models.CharField(max_length=120, blank=True)

    # ── Provider ──────────────────────────────────────────────────────
    provider = models.CharField(
        max_length=20, choices=PROVIDER_CHOICES, default=PROVIDER_MOCK
    )

    # ── Connectivity ──────────────────────────────────────────────────
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_seen_source = models.CharField(max_length=80, blank=True)
    days_offline = models.IntegerField(default=0, help_text="Computed or manually entered days since last seen online")

    # ── Lock state ────────────────────────────────────────────────────
    lock_state = models.CharField(
        max_length=20, choices=LOCK_STATE_CHOICES, default=LOCK_UNKNOWN
    )
    sim_changed = models.BooleanField(null=True, blank=True)
    location_changed = models.BooleanField(null=True, blank=True)
    repeated_failed_lock_attempts = models.IntegerField(default=0)

    # ── Payment / financial ───────────────────────────────────────────
    last_payment_at = models.DateTimeField(null=True, blank=True)
    arrears_days = models.IntegerField(default=0)
    balance_due = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # ── Pending command ───────────────────────────────────────────────
    last_unlock_request_at = models.DateTimeField(null=True, blank=True)
    last_lock_command_at = models.DateTimeField(null=True, blank=True)
    pending_command_type = models.CharField(
        max_length=10, choices=PENDING_CMD_CHOICES, default=CMD_NONE
    )
    pending_command_age_hours = models.IntegerField(
        default=0,
        help_text="Hours since last pending command was issued",
    )

    # ── Connectivity / support status ─────────────────────────────────
    connectivity_status = models.CharField(
        max_length=30, choices=CONNECTIVITY_STATUS_CHOICES, default=CONN_UNKNOWN
    )
    sync_status = models.CharField(
        max_length=30, choices=SYNC_STATUS_CHOICES, default=SYNC_UNKNOWN
    )
    connectivity_help_needed = models.BooleanField(
        default=False,
        help_text="True when device needs to come online before a command can sync",
    )

    # ── Risk / support scoring ────────────────────────────────────────
    risk_band = models.CharField(
        max_length=20, choices=RISK_BAND_CHOICES, default=RISK_NORMAL
    )
    risk_score = models.IntegerField(
        default=0,
        help_text="0–100 composite risk score (higher = more risk)",
    )
    support_priority = models.IntegerField(
        default=0,
        help_text="0–100 support priority score (higher = needs more attention)",
    )
    recommended_action = models.TextField(blank=True)

    # ── Audit ─────────────────────────────────────────────────────────
    notes = models.TextField(blank=True)
    is_false_positive = models.BooleanField(default=False)
    false_positive_marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="false_positive_signals",
    )
    sync_instruction_sent_at = models.DateTimeField(null=True, blank=True)
    escalated_at = models.DateTimeField(null=True, blank=True)
    raw_payload = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Device Connectivity Signal"
        verbose_name_plural = "Device Connectivity Signals"
        ordering = ["-risk_score", "-days_offline", "-created_at"]

    def __str__(self):
        return (
            f"ConnSignal IMEI:{self.imei or '?'} "
            f"offline:{self.days_offline}d risk:{self.risk_band}"
        )

    # ── Risk scoring ──────────────────────────────────────────────────

    @classmethod
    def compute_risk_band(cls, days_offline: int) -> str:
        """Offline-days → risk band mapping (configurable thresholds)."""
        if days_offline <= 2:
            return cls.RISK_NORMAL
        if days_offline <= 5:
            return cls.RISK_WATCH
        if days_offline <= 10:
            return cls.RISK_WARNING
        if days_offline <= 20:
            return cls.RISK_HIGH
        return cls.RISK_CRITICAL

    @classmethod
    def compute_risk_score(
        cls,
        days_offline: int = 0,
        arrears_days: int = 0,
        days_since_payment: int | None = None,
        device_value: float = 0,
        sim_changed: bool = False,
        location_changed: bool = False,
        failed_lock_attempts: int = 0,
    ) -> int:
        """
        Composite risk score 0–100.

        days_offline         → up to 40 pts
        arrears_days         → up to 30 pts
        missed payments      → up to 15 pts
        device value         → up to  5 pts
        SIM/location anomaly → up to 10 pts
        """
        score = 0

        # days_offline → 40 pts (0 d=0, 2 d=10, 5 d=20, 10 d=30, 21+ d=40)
        if days_offline >= 21:
            score += 40
        elif days_offline >= 11:
            score += 30
        elif days_offline >= 6:
            score += 20
        elif days_offline >= 3:
            score += 10

        # arrears_days → 30 pts (0=0, 7=10, 14=20, 30+=30)
        if arrears_days >= 30:
            score += 30
        elif arrears_days >= 14:
            score += 20
        elif arrears_days >= 7:
            score += 10

        # days_since_payment → 15 pts (None or >30 d = max)
        if days_since_payment is None or days_since_payment > 30:
            score += 15
        elif days_since_payment > 14:
            score += 8

        # device value > MWK 300,000 → 5 pts
        if device_value and device_value > 300_000:
            score += 5

        # SIM/location anomaly → 10 pts
        if sim_changed:
            score += 5
        if location_changed:
            score += 3
        if failed_lock_attempts >= 3:
            score += 2

        return min(score, 100)

    @classmethod
    def compute_recommended_action(
        cls, risk_band: str, connectivity_status: str, pending_command_type: str
    ) -> str:
        """Generate a human-readable recommended action."""
        # Sync / support needs first
        if connectivity_status == cls.CONN_OFFLINE_PAID:
            return (
                "Ask customer to connect the phone to mobile data or Wi-Fi for 5–10 minutes "
                "so the payment/unlock status can sync."
            )
        if connectivity_status in (cls.CONN_OFFLINE_AFTER_UNLOCK, cls.CONN_OFFLINE_AFTER_PAYMENT):
            return (
                "Command or payment pending. Ask customer to bring device online so the update "
                "can sync. Send internet sync instruction via WhatsApp."
            )
        if pending_command_type in (cls.CMD_LOCK, cls.CMD_UNLOCK):
            return (
                f"{pending_command_type.title()} command pending. "
                "Device must come online before the command can take effect."
            )

        # Risk-band recommendations
        if risk_band == cls.RISK_NORMAL:
            return "No action required."
        if risk_band == cls.RISK_WATCH:
            return "Send friendly WhatsApp check-in. Monitor for 48 hours."
        if risk_band == cls.RISK_WARNING:
            return (
                "Underwriter follow-up required. Merchant check-in. "
                "Ask customer to connect device to the internet."
            )
        if risk_band == cls.RISK_HIGH:
            return (
                "Escalate to recovery desk. Attempt lock status verification. "
                "Call customer and guarantors. Review payment behaviour."
            )
        if risk_band == cls.RISK_CRITICAL:
            return (
                "HQ fraud/recovery review required. Prepare repossession workflow if "
                "commercially appropriate. Freeze follow-up approvals for same customer "
                "until reviewed. Escalate to management if device value exceeds threshold."
            )
        return "Review signal manually."

    @classmethod
    def compute_connectivity_status(
        cls,
        days_offline: int,
        arrears_days: int,
        pending_command_type: str,
        last_unlock_request_at=None,
        last_lock_command_at=None,
        last_payment_at=None,
        last_seen_at=None,
    ) -> str:
        """Derive the human-readable connectivity status."""
        from django.utils import timezone

        if days_offline == 0 and last_seen_at:
            return cls.CONN_RECENTLY_ONLINE

        # Offline after a lock command
        if last_lock_command_at and last_seen_at and last_lock_command_at > last_seen_at:
            return cls.CONN_OFFLINE_AFTER_LOCK

        # Offline after an unlock request
        if last_unlock_request_at and last_seen_at and last_unlock_request_at > last_seen_at:
            return cls.CONN_OFFLINE_AFTER_UNLOCK

        # Paid but offline
        if arrears_days == 0 and days_offline > 0:
            return cls.CONN_OFFLINE_PAID

        # Offline with arrears
        if arrears_days > 0 and days_offline > 0:
            if days_offline >= 11:
                return cls.CONN_POSSIBLE_EVASION
            return cls.CONN_OFFLINE_ARREARS

        # Pending command
        if pending_command_type != cls.CMD_NONE:
            return cls.CONN_SYNC_PENDING

        return cls.CONN_UNKNOWN


class DeviceCheck(models.Model):
    """
    Records a device authenticity / warranty / lock-eligibility check.
    Supports mock providers now; real provider integrations added later.
    """

    STATUS_PENDING = "pending"
    STATUS_PASSED = "passed"
    STATUS_FAILED = "failed"
    STATUS_SKIPPED = "skipped"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PASSED, "Passed"),
        (STATUS_FAILED, "Failed"),
        (STATUS_SKIPPED, "Skipped"),
    ]

    WARRANTY_VALID = "valid"
    WARRANTY_EXPIRED = "expired"
    WARRANTY_UNKNOWN = "unknown"

    WARRANTY_CHOICES = [
        (WARRANTY_VALID, "Valid"),
        (WARRANTY_EXPIRED, "Expired"),
        (WARRANTY_UNKNOWN, "Unknown"),
    ]

    PROVIDER_MOCK = "mock"
    PROVIDER_IMEI_DB = "imei_db"
    PROVIDER_MANUFACTURER = "manufacturer"

    PROVIDER_CHOICES = [
        (PROVIDER_MOCK, "Mock / Placeholder"),
        (PROVIDER_IMEI_DB, "IMEI Database"),
        (PROVIDER_MANUFACTURER, "Manufacturer API"),
    ]

    application = models.ForeignKey(
        "applications.FinancingApplication",
        on_delete=models.CASCADE,
        related_name="device_checks",
        null=True,
        blank=True,
    )
    contract = models.ForeignKey(
        "portal.PaymentContract",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="device_checks",
    )
    imei = models.CharField(max_length=20, blank=True)
    provider = models.CharField(max_length=30, choices=PROVIDER_CHOICES, default=PROVIDER_MOCK)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    warranty_status = models.CharField(max_length=20, choices=WARRANTY_CHOICES, default=WARRANTY_UNKNOWN)
    lock_eligible = models.BooleanField(default=False, help_text="Whether this device can be enrolled in PayG lock")
    response_summary = models.TextField(blank=True)
    checked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Device Check"
        ordering = ["-checked_at"]

    def __str__(self):
        return f"DeviceCheck IMEI={self.imei or '?'} [{self.status}] via {self.provider}"
