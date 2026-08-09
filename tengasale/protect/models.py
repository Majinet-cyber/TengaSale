import secrets
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


def verification_token():
    return secrets.token_urlsafe(32)


class DeviceRegistryEntry(models.Model):
    FINANCING = [(x, x.replace("_", " ").title()) for x in ("active", "completed", "written_off")]
    PROTECTION = [(x, x.replace("_", " ").title()) for x in ("protected", "reported_lost", "reported_stolen", "under_recovery", "recovered")]
    DEVICE = [(x, x.replace("_", " ").title()) for x in ("financed", "active", "payment_locked", "theft_locked", "released", "transferred")]
    RESALE = [(x, x.replace("_", " ").title()) for x in ("not_eligible", "pending", "approved", "transferred")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contract = models.OneToOneField("contracts.Contract", on_delete=models.PROTECT, related_name="protect_registry")
    imei = models.CharField(max_length=20, unique=True, db_index=True)
    imei_2 = models.CharField(max_length=20, blank=True, db_index=True)
    serial_number = models.CharField(max_length=120, blank=True)
    provider = models.CharField(max_length=40, blank=True)
    financing_status = models.CharField(max_length=24, choices=FINANCING, default="active")
    protection_status = models.CharField(max_length=24, choices=PROTECTION, default="protected")
    device_status = models.CharField(max_length=24, choices=DEVICE, default="financed")
    resale_status = models.CharField(max_length=24, choices=RESALE, default="not_eligible")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def masked_imei(self):
        return f"{'*' * max(0, len(self.imei) - 4)}{self.imei[-4:]}"


class ProtectCase(models.Model):
    TYPE_LOST = "lost"
    TYPE_STOLEN = "stolen"
    TYPE_CHOICES = [(TYPE_LOST, "Lost device"), (TYPE_STOLEN, "Stolen device")]
    STATUS_CHOICES = [(x, x.replace("_", " ").title()) for x in (
        "new", "under_investigation", "awaiting_police_report", "awaiting_approval",
        "recovery_active", "recovered", "cancelled", "closed_not_recovered", "closed_recovered"
    )]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case_number = models.CharField(max_length=20, unique=True, editable=False)
    registry_entry = models.ForeignKey(DeviceRegistryEntry, on_delete=models.PROTECT, related_name="cases")
    case_type = models.CharField(max_length=12, choices=TYPE_CHOICES)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="new", db_index=True)
    incident_date = models.DateField()
    incident_time = models.TimeField(null=True, blank=True)
    incident_location = models.CharField(max_length=250)
    description = models.TextField()
    police_station = models.CharField(max_length=150, blank=True)
    police_reference = models.CharField(max_length=100, blank=True)
    alternative_phone = models.CharField(max_length=30)
    alternative_contact_name = models.CharField(max_length=150, blank=True)
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="protect_cases_opened")
    assigned_officer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="protect_cases_assigned")
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.case_number:
            prefix = f"TP-{timezone.now():%Y}-"
            last = ProtectCase.objects.filter(case_number__startswith=prefix).order_by("-case_number").values_list("case_number", flat=True).first()
            sequence = int(last.rsplit("-", 1)[-1]) + 1 if last else 1
            self.case_number = f"{prefix}{sequence:06d}"
        super().save(*args, **kwargs)


class ProtectConsent(models.Model):
    case = models.OneToOneField(ProtectCase, on_delete=models.PROTECT, related_name="consent")
    policy_version = models.CharField(max_length=30)
    text_snapshot = models.TextField()
    authentication_method = models.CharField(max_length=30)
    authenticated_reference = models.CharField(max_length=100, blank=True)
    customer_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    accepted_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_valid(self):
        return self.revoked_at is None


class ProtectDocument(models.Model):
    TYPES = [(x, x.replace("_", " ").title()) for x in ("police_report", "legal_authority", "recovery_evidence")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(ProtectCase, on_delete=models.PROTECT, related_name="documents")
    document_type = models.CharField(max_length=30, choices=TYPES)
    file = models.FileField(upload_to="protect/documents/%Y/%m/")
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    sha256 = models.CharField(max_length=64)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    uploaded_at = models.DateTimeField(auto_now_add=True)


class LocationAccessSession(models.Model):
    STATUS_CHOICES = [(x, x.replace("_", " ").title()) for x in ("requested", "authorised", "rejected", "expired", "terminated")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(ProtectCase, on_delete=models.PROTECT, related_name="location_sessions")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="protect_location_requests_made")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="protect_location_requests_approved")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="requested")
    reason = models.TextField()
    starts_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.approved_by_id and self.approved_by_id == self.requested_by_id:
            raise ValidationError("Maker-checker policy prevents self-approval.")

    @property
    def is_active(self):
        return self.status == "authorised" and self.starts_at and self.expires_at and self.starts_at <= timezone.now() < self.expires_at


class LocationRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(LocationAccessSession, on_delete=models.PROTECT, related_name="requests")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reason = models.TextField()
    provider = models.CharField(max_length=40, blank=True)
    provider_request_id = models.CharField(max_length=100, blank=True)
    result_status = models.CharField(max_length=30, default="pending")
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    accuracy_metres = models.PositiveIntegerField(null=True, blank=True)
    device_timestamp = models.DateTimeField(null=True, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)


class ProtectAuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(ProtectCase, null=True, blank=True, on_delete=models.PROTECT, related_name="audit_events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    actor_role = models.CharField(max_length=60, blank=True)
    action = models.CharField(max_length=60, db_index=True)
    object_type = models.CharField(max_length=60, blank=True)
    object_reference = models.CharField(max_length=100, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    previous_state = models.JSONField(default=dict, blank=True)
    new_state = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if self.pk and ProtectAuditEvent.objects.filter(pk=self.pk).exists():
            raise ValidationError("Protect audit events are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Protect audit events are immutable.")


class DeviceCertificate(models.Model):
    TYPES = [(x, x.replace("_", " ").title()) for x in ("ownership", "stolen_report", "recovery", "financing_completion")]
    STATES = [("valid", "Valid"), ("revoked", "Revoked"), ("superseded", "Superseded")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    certificate_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    registry_entry = models.ForeignKey(DeviceRegistryEntry, on_delete=models.PROTECT, related_name="certificates")
    case = models.ForeignKey(ProtectCase, null=True, blank=True, on_delete=models.PROTECT, related_name="certificates")
    certificate_type = models.CharField(max_length=30, choices=TYPES)
    version = models.PositiveIntegerField(default=1)
    verification_token = models.CharField(max_length=64, default=verification_token, unique=True, editable=False)
    status = models.CharField(max_length=20, choices=STATES, default="valid")
    supersedes = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT)
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["registry_entry", "certificate_type", "version"], name="unique_protect_certificate_version")]
