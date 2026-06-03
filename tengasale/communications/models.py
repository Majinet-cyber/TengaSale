import random
from datetime import timedelta

from django.db import models
from django.utils import timezone


class SMSLog(models.Model):
    STATUS_QUEUED = "queued"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_PENDING_CONFIG = "pending_config"

    STATUS_CHOICES = [
        (STATUS_QUEUED, "Queued"),
        (STATUS_SENT, "Sent"),
        (STATUS_FAILED, "Failed"),
        (STATUS_PENDING_CONFIG, "Pending Config"),
    ]

    PURPOSE_PAYMENT_CONFIRMATION = "payment_confirmation"
    PURPOSE_OTP = "otp"
    PURPOSE_DUE_REMINDER = "due_reminder"
    PURPOSE_PAYMENT_DUE_TODAY = "payment_due_today"
    PURPOSE_ARREARS_REMINDER = "arrears_reminder"
    PURPOSE_GENERAL = "general"

    PURPOSE_CHOICES = [
        (PURPOSE_PAYMENT_CONFIRMATION, "Payment Confirmation"),
        (PURPOSE_OTP, "OTP"),
        (PURPOSE_DUE_REMINDER, "Due Reminder"),
        (PURPOSE_PAYMENT_DUE_TODAY, "Payment Due Today"),
        (PURPOSE_ARREARS_REMINDER, "Arrears Reminder"),
        (PURPOSE_GENERAL, "General"),
    ]

    phone_number = models.CharField(max_length=30)
    message = models.TextField()
    purpose = models.CharField(max_length=50, choices=PURPOSE_CHOICES)
    contract = models.ForeignKey(
        "contracts.Contract", null=True, blank=True, on_delete=models.SET_NULL, related_name="sms_logs"
    )
    payment_contract = models.ForeignKey(
        "portal.PaymentContract", null=True, blank=True, on_delete=models.SET_NULL, related_name="sms_logs"
    )
    application = models.ForeignKey(
        "applications.FinancingApplication", null=True, blank=True, on_delete=models.SET_NULL, related_name="sms_logs"
    )
    payment_transaction = models.ForeignKey(
        "portal.PaymentTransaction", null=True, blank=True, on_delete=models.SET_NULL, related_name="sms_logs"
    )
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_QUEUED)
    provider = models.CharField(max_length=50, default="twilio")
    provider_message_id = models.CharField(max_length=120, blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    language = models.CharField(max_length=10, default="ny")
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["phone_number"]),
            models.Index(fields=["purpose", "status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.phone_number} - {self.purpose} - {self.status}"


def default_otp_expiry():
    return timezone.now() + timedelta(minutes=10)


class PhoneOTP(models.Model):
    STATUS_NOT_SENT = "not_sent"
    STATUS_SENT = "sent"
    STATUS_VERIFIED = "verified"
    STATUS_FAILED = "failed"
    STATUS_SKIPPED = "skipped"

    phone_number = models.CharField(max_length=30)
    otp_code = models.CharField(max_length=10)
    contract = models.ForeignKey("contracts.Contract", null=True, blank=True, on_delete=models.SET_NULL)
    payment_contract = models.ForeignKey("portal.PaymentContract", null=True, blank=True, on_delete=models.SET_NULL)
    application = models.ForeignKey(
        "applications.FinancingApplication", null=True, blank=True, on_delete=models.SET_NULL, related_name="phone_otps"
    )
    is_verified = models.BooleanField(default=False)
    attempts = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(default=default_otp_expiry)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["phone_number", "created_at"]),
            models.Index(fields=["is_verified", "expires_at"]),
        ]

    @classmethod
    def generate_code(cls):
        return str(random.randint(100000, 999999))

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    def __str__(self):
        return f"{self.phone_number} - {'verified' if self.is_verified else 'pending'}"
