from django.conf import settings
from django.db import models
from django.utils import timezone


class Notification(models.Model):
    TYPE_APP_SUBMITTED = "app_submitted"
    TYPE_APP_SENT_BACK = "app_sent_back"
    TYPE_APP_APPROVED = "app_approved"
    TYPE_APP_REJECTED = "app_rejected"
    TYPE_TERMS_PENDING = "terms_pending"
    TYPE_READY_ACTIVATION = "ready_activation"
    TYPE_APP_ACTIVATED = "app_activated"
    TYPE_APP_COMPLETED = "app_completed"
    TYPE_APP_DEFAULTED = "app_defaulted"
    TYPE_NEW_APPLICATION = "new_application"
    TYPE_ASSIGNED = "assigned"
    TYPE_RESUBMISSION = "resubmission"
    TYPE_SLA_WARNING = "sla_warning"
    TYPE_HIGH_RISK = "high_risk"
    TYPE_IMEI_MISMATCH = "imei_mismatch"
    TYPE_WARRANTY_FAILURE = "warranty_failure"
    TYPE_HIGH_EXPOSURE = "high_exposure"
    TYPE_DEFAULT_RISK = "default_risk"
    TYPE_SYSTEM_ERROR = "system_error"
    TYPE_APP_UPDATE = "app_update"
    TYPE_MAINTENANCE = "maintenance"

    TYPE_CHOICES = [
        (TYPE_APP_SUBMITTED, "Application Submitted"),
        (TYPE_APP_SENT_BACK, "Application Sent Back"),
        (TYPE_APP_APPROVED, "Application Approved"),
        (TYPE_APP_REJECTED, "Application Rejected"),
        (TYPE_TERMS_PENDING, "Awaiting Terms Review"),
        (TYPE_READY_ACTIVATION, "Ready for Activation"),
        (TYPE_APP_ACTIVATED, "Contract Activated"),
        (TYPE_APP_COMPLETED, "Contract Completed"),
        (TYPE_APP_DEFAULTED, "Contract Defaulted"),
        (TYPE_NEW_APPLICATION, "New Application"),
        (TYPE_ASSIGNED, "Application Assigned"),
        (TYPE_RESUBMISSION, "Application Resubmitted"),
        (TYPE_SLA_WARNING, "SLA Warning"),
        (TYPE_HIGH_RISK, "High Risk Application"),
        (TYPE_IMEI_MISMATCH, "IMEI Mismatch"),
        (TYPE_WARRANTY_FAILURE, "Warranty Check Failure"),
        (TYPE_HIGH_EXPOSURE, "High Exposure"),
        (TYPE_DEFAULT_RISK, "Default Risk"),
        (TYPE_SYSTEM_ERROR, "System Error"),
        (TYPE_APP_UPDATE, "App Update"),
        (TYPE_MAINTENANCE, "Maintenance Notice"),
    ]

    LEVEL_INFO = "info"
    LEVEL_SUCCESS = "success"
    LEVEL_WARNING = "warning"
    LEVEL_DANGER = "danger"
    LEVEL_CHOICES = [
        (LEVEL_INFO, "Info"),
        (LEVEL_SUCCESS, "Success"),
        (LEVEL_WARNING, "Warning"),
        (LEVEL_DANGER, "Danger"),
    ]

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
        null=True,
        blank=True,
        help_text="Null means broadcast to all users",
    )
    notification_type = models.CharField(max_length=40, choices=TYPE_CHOICES)
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default=LEVEL_INFO)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    link = models.CharField(max_length=500, blank=True)
    object_type = models.CharField(max_length=80, blank=True)
    object_id = models.CharField(max_length=40, blank=True)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "is_read", "created_at"]),
            models.Index(fields=["notification_type", "created_at"]),
        ]

    def __str__(self):
        return f"{self.notification_type} → {self.recipient_id} — {self.title[:40]}"

    def mark_read(self):
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=["is_read", "read_at"])

    @classmethod
    def send(cls, recipient, notification_type, title, body="", link="",
             object_type="", object_id="", level=LEVEL_INFO):
        return cls.objects.create(
            recipient=recipient,
            notification_type=notification_type,
            title=title,
            body=body,
            link=link,
            object_type=object_type,
            object_id=str(object_id),
            level=level,
        )

    @classmethod
    def broadcast(cls, notification_type, title, body="", link="", level=LEVEL_INFO):
        """Create a broadcast notification (no specific recipient)."""
        return cls.objects.create(
            recipient=None,
            notification_type=notification_type,
            title=title,
            body=body,
            link=link,
            level=level,
        )

    @classmethod
    def unread_count_for(cls, user):
        from django.db.models import Q
        return cls.objects.filter(
            Q(recipient=user) | Q(recipient__isnull=True),
            is_read=False,
        ).count()

    @classmethod
    def for_user(cls, user, limit=30):
        from django.db.models import Q
        return cls.objects.filter(
            Q(recipient=user) | Q(recipient__isnull=True),
        ).order_by("-created_at")[:limit]
