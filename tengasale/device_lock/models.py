from django.conf import settings
from django.db import models
from django.utils import timezone


class DeviceLockProfile(models.Model):
    """Central profile linking a financed device to its lock provider state."""

    PROVIDER_NONE = "none"
    PROVIDER_MOCK = "mock"
    PROVIDER_SAMSUNG_KNOX = "samsung_knox"
    PROVIDER_TRUSTONIC = "trustonic"
    PROVIDER_PAYTRIGGER = "paytrigger"
    PROVIDER_OTHER = "other"

    PROVIDER_CHOICES = [
        (PROVIDER_NONE, "None (no lock provider)"),
        (PROVIDER_MOCK, "Mock (demo/test)"),
        (PROVIDER_SAMSUNG_KNOX, "Samsung Knox"),
        (PROVIDER_TRUSTONIC, "Trustonic"),
        (PROVIDER_PAYTRIGGER, "PayTrigger"),
        (PROVIDER_OTHER, "Other"),
    ]

    STATUS_NOT_ENROLLED = "not_enrolled"
    STATUS_ENROLLMENT_PENDING = "enrollment_pending"
    STATUS_ENROLLED = "enrolled"
    STATUS_ACTIVE = "active"
    STATUS_LOCK_PENDING = "lock_pending"
    STATUS_LOCKED = "locked"
    STATUS_UNLOCK_PENDING = "unlock_pending"
    STATUS_UNLOCKED = "unlocked"
    STATUS_RELEASED = "released"
    STATUS_FAILED = "failed"
    STATUS_UNKNOWN = "unknown"

    STATUS_CHOICES = [
        (STATUS_NOT_ENROLLED, "Not Enrolled"),
        (STATUS_ENROLLMENT_PENDING, "Enrollment Pending"),
        (STATUS_ENROLLED, "Enrolled"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_LOCK_PENDING, "Lock Pending"),
        (STATUS_LOCKED, "Locked"),
        (STATUS_UNLOCK_PENDING, "Unlock Pending"),
        (STATUS_UNLOCKED, "Unlocked"),
        (STATUS_RELEASED, "Released"),
        (STATUS_FAILED, "Failed"),
        (STATUS_UNKNOWN, "Unknown"),
    ]

    # Contract / application link (nullable so it works even if no contract yet)
    application = models.OneToOneField(
        "applications.FinancingApplication",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="device_lock_profile",
    )
    contract = models.OneToOneField(
        "contracts.Contract",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="device_lock_profile",
    )

    # People
    customer_name = models.CharField(max_length=150, blank=True)
    customer_phone = models.CharField(max_length=30, blank=True)
    merchant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="device_lock_profiles",
    )

    # Device identity
    imei = models.CharField(max_length=20, db_index=True)
    serial_number = models.CharField(max_length=60, blank=True, null=True)
    deal_name = models.CharField(max_length=200, blank=True)

    # Provider config
    lock_provider = models.CharField(
        max_length=20, choices=PROVIDER_CHOICES, default=PROVIDER_NONE, db_index=True
    )
    provider_device_id = models.CharField(max_length=200, blank=True, null=True)
    provider_enrollment_id = models.CharField(max_length=200, blank=True, null=True)
    provider_contract_id = models.CharField(max_length=200, blank=True, null=True)

    # Status
    lock_status = models.CharField(
        max_length=25, choices=STATUS_CHOICES, default=STATUS_NOT_ENROLLED, db_index=True
    )
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_lock_attempt_at = models.DateTimeField(null=True, blank=True)
    last_unlock_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, null=True)

    # Flags
    is_lockable = models.BooleanField(default=False)
    is_released = models.BooleanField(default=False)

    # Policy link
    policy = models.ForeignKey(
        "DeviceLockPolicy",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="profiles",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Device Lock Profile"
        verbose_name_plural = "Device Lock Profiles"

    def __str__(self):
        return f"LockProfile IMEI:{self.imei} [{self.get_lock_status_display()}]"

    @property
    def is_locked(self):
        return self.lock_status == self.STATUS_LOCKED

    @property
    def is_active(self):
        return self.lock_status in (self.STATUS_ACTIVE, self.STATUS_ENROLLED, self.STATUS_UNLOCKED)

    @property
    def status_css_class(self):
        mapping = {
            self.STATUS_LOCKED: "danger",
            self.STATUS_LOCK_PENDING: "warning",
            self.STATUS_UNLOCK_PENDING: "warning",
            self.STATUS_UNLOCKED: "success",
            self.STATUS_ACTIVE: "success",
            self.STATUS_ENROLLED: "success",
            self.STATUS_ENROLLMENT_PENDING: "warning",
            self.STATUS_RELEASED: "secondary",
            self.STATUS_FAILED: "danger",
            self.STATUS_NOT_ENROLLED: "secondary",
            self.STATUS_UNKNOWN: "secondary",
        }
        return mapping.get(self.lock_status, "secondary")


class DeviceLockEvent(models.Model):
    """Immutable audit log entry for every device lock action."""

    TYPE_ENROLL_REQUESTED = "enroll_requested"
    TYPE_ENROLL_SUCCESS = "enroll_success"
    TYPE_ENROLL_FAILED = "enroll_failed"
    TYPE_LOCK_REQUESTED = "lock_requested"
    TYPE_LOCK_SUCCESS = "lock_success"
    TYPE_LOCK_FAILED = "lock_failed"
    TYPE_UNLOCK_REQUESTED = "unlock_requested"
    TYPE_UNLOCK_SUCCESS = "unlock_success"
    TYPE_UNLOCK_FAILED = "unlock_failed"
    TYPE_RELEASE_REQUESTED = "release_requested"
    TYPE_RELEASE_SUCCESS = "release_success"
    TYPE_RELEASE_FAILED = "release_failed"
    TYPE_STATUS_SYNC = "status_sync"
    TYPE_WEBHOOK_RECEIVED = "webhook_received"
    TYPE_MANUAL_OVERRIDE = "manual_override"

    EVENT_TYPE_CHOICES = [
        (TYPE_ENROLL_REQUESTED, "Enroll Requested"),
        (TYPE_ENROLL_SUCCESS, "Enroll Success"),
        (TYPE_ENROLL_FAILED, "Enroll Failed"),
        (TYPE_LOCK_REQUESTED, "Lock Requested"),
        (TYPE_LOCK_SUCCESS, "Lock Success"),
        (TYPE_LOCK_FAILED, "Lock Failed"),
        (TYPE_UNLOCK_REQUESTED, "Unlock Requested"),
        (TYPE_UNLOCK_SUCCESS, "Unlock Success"),
        (TYPE_UNLOCK_FAILED, "Unlock Failed"),
        (TYPE_RELEASE_REQUESTED, "Release Requested"),
        (TYPE_RELEASE_SUCCESS, "Release Success"),
        (TYPE_RELEASE_FAILED, "Release Failed"),
        (TYPE_STATUS_SYNC, "Status Sync"),
        (TYPE_WEBHOOK_RECEIVED, "Webhook Received"),
        (TYPE_MANUAL_OVERRIDE, "Manual Override"),
    ]

    SOURCE_SYSTEM = "system"
    SOURCE_ADMIN = "admin"
    SOURCE_UNDERWRITER = "underwriter"
    SOURCE_MERCHANT = "merchant"
    SOURCE_REPAYMENT_ENGINE = "repayment_engine"
    SOURCE_WEBHOOK = "webhook"
    SOURCE_MANAGEMENT_COMMAND = "management_command"

    TRIGGER_SOURCE_CHOICES = [
        (SOURCE_SYSTEM, "System"),
        (SOURCE_ADMIN, "Admin"),
        (SOURCE_UNDERWRITER, "Underwriter"),
        (SOURCE_MERCHANT, "Merchant"),
        (SOURCE_REPAYMENT_ENGINE, "Repayment Engine"),
        (SOURCE_WEBHOOK, "Webhook"),
        (SOURCE_MANAGEMENT_COMMAND, "Management Command"),
    ]

    lock_profile = models.ForeignKey(
        DeviceLockProfile,
        on_delete=models.CASCADE,
        related_name="events",
    )
    event_type = models.CharField(max_length=30, choices=EVENT_TYPE_CHOICES, db_index=True)
    status_before = models.CharField(max_length=25, blank=True)
    status_after = models.CharField(max_length=25, blank=True)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="device_lock_events",
    )
    trigger_source = models.CharField(
        max_length=25,
        choices=TRIGGER_SOURCE_CHOICES,
        default=SOURCE_SYSTEM,
    )
    request_payload = models.JSONField(null=True, blank=True)
    response_payload = models.JSONField(null=True, blank=True)
    error_message = models.TextField(blank=True, null=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Device Lock Event"
        verbose_name_plural = "Device Lock Events"

    def __str__(self):
        return f"{self.get_event_type_display()} — {self.lock_profile}"

    @property
    def is_success(self):
        return self.event_type in (
            self.TYPE_ENROLL_SUCCESS,
            self.TYPE_LOCK_SUCCESS,
            self.TYPE_UNLOCK_SUCCESS,
            self.TYPE_RELEASE_SUCCESS,
        )

    @property
    def is_failure(self):
        return self.event_type in (
            self.TYPE_ENROLL_FAILED,
            self.TYPE_LOCK_FAILED,
            self.TYPE_UNLOCK_FAILED,
            self.TYPE_RELEASE_FAILED,
        )


class DeviceLockPolicy(models.Model):
    """Configurable policy that governs when devices are locked/unlocked."""

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    # Grace and overdue thresholds (in days)
    grace_period_days = models.PositiveIntegerField(default=3)
    lock_after_days_overdue = models.PositiveIntegerField(default=7)

    # Automatic actions
    unlock_when_paid_to_date = models.BooleanField(default=True)
    release_when_contract_completed = models.BooleanField(default=True)
    allow_manual_override = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_active", "name"]
        verbose_name = "Device Lock Policy"
        verbose_name_plural = "Device Lock Policies"

    def __str__(self):
        return f"{self.name} ({'active' if self.is_active else 'inactive'})"
