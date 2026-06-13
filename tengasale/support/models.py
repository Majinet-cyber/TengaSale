"""
Support app models.

SupportTicket      — issue/request raised by any authenticated user
TicketComment      — threaded comments on a ticket (some internal-only)
BugEvent           — system-level failure events captured by the platform
SupportConversation— WhatsApp / chatbot conversation session
SupportMessage     — individual inbound/outbound messages
SupportCategory    — configurable issue categories for chatbot routing
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


class SupportTicket(models.Model):
    # ── categories ──────────────────────────────────────────────────────────
    CAT_PAYMENT         = "payment"
    CAT_ACCESS          = "access"
    CAT_MERCHANT_ONBOARD = "merchant_onboarding"
    CAT_UNDERWRITER     = "underwriter"
    CAT_DEVICE_LOCK     = "device_lock"
    CAT_APP_ERROR       = "app_error"
    CAT_CONTRACT        = "contract"
    CAT_PAYOUT          = "payout"
    CAT_DATA_CORRECTION = "data_correction"
    CAT_OTHER           = "other"
    CAT_APPLICATION_STATUS = "application_status"
    CAT_PAYMENT_NOT_REFLECTING = "payment_not_reflecting"
    CAT_UNLOCK_REQUEST = "unlock_request"
    CAT_DEPOSIT_ISSUE = "deposit_issue"
    CAT_WRONG_PHONE_NUMBER = "wrong_phone_number"
    CAT_WRONG_ID_DETAILS = "wrong_id_details"
    CAT_CONTRACT_ISSUE = "contract_issue"
    CAT_WARRANTY_ISSUE = "warranty_issue"
    CAT_GUARANTOR_ISSUE = "guarantor_issue"
    CAT_MERCHANT_COMPLAINT = "merchant_complaint"
    CAT_UNDERWRITER_COMPLAINT = "underwriter_complaint"
    CAT_GENERAL_SUPPORT = "general_support"
    CAT_MERCHANT_SUPPORT = "merchant_support"
    CAT_HANDOVER_PROBLEM = "handover_problem"
    CAT_IMEI_SUBMISSION_PROBLEM = "imei_submission_problem"
    CAT_DEPOSIT_CONFIRMATION_ISSUE = "deposit_confirmation_issue"
    CAT_STOCK_ISSUE = "stock_issue"
    CAT_CONTRACT_GENERATION_ISSUE = "contract_generation_issue"
    CAT_MERCHANT_COMMISSION_ISSUE = "merchant_commission_issue"
    CAT_SUSPECTED_CUSTOMER_FRAUD = "suspected_customer_fraud"
    CAT_FINANCE_REVIEW = "finance_review"
    CAT_RECOVERY_REVIEW = "recovery_review"
    CAT_LEGAL_REVIEW = "legal_review"
    CAT_MANAGEMENT_ESCALATION = "management_escalation"
    CAT_FRAUD_REPORT = "fraud_report"
    CAT_SYSTEM_ISSUE = "system_issue"

    CATEGORY_CHOICES = [
        (CAT_PAYMENT,          "Payment issue"),
        (CAT_ACCESS,           "Login / access issue"),
        (CAT_MERCHANT_ONBOARD, "Merchant onboarding issue"),
        (CAT_UNDERWRITER,      "Underwriter issue"),
        (CAT_DEVICE_LOCK,      "Device lock issue"),
        (CAT_APP_ERROR,        "Application error"),
        (CAT_CONTRACT,         "Contract issue"),
        (CAT_PAYOUT,           "Payout issue"),
        (CAT_DATA_CORRECTION,  "Data correction request"),
        (CAT_APPLICATION_STATUS, "Application status"),
        (CAT_PAYMENT_NOT_REFLECTING, "Payment not reflecting"),
        (CAT_UNLOCK_REQUEST, "Unlock request"),
        (CAT_DEPOSIT_ISSUE, "Deposit issue"),
        (CAT_WRONG_PHONE_NUMBER, "Wrong phone number"),
        (CAT_WRONG_ID_DETAILS, "Wrong ID details"),
        (CAT_CONTRACT_ISSUE, "Contract issue"),
        (CAT_WARRANTY_ISSUE, "Warranty / damaged phone"),
        (CAT_GUARANTOR_ISSUE, "Guarantor issue"),
        (CAT_MERCHANT_COMPLAINT, "Merchant complaint"),
        (CAT_UNDERWRITER_COMPLAINT, "Underwriter complaint"),
        (CAT_GENERAL_SUPPORT, "General support"),
        (CAT_MERCHANT_SUPPORT, "Merchant support"),
        (CAT_HANDOVER_PROBLEM, "Handover problem"),
        (CAT_IMEI_SUBMISSION_PROBLEM, "IMEI submission problem"),
        (CAT_DEPOSIT_CONFIRMATION_ISSUE, "Deposit confirmation issue"),
        (CAT_STOCK_ISSUE, "Stock issue"),
        (CAT_CONTRACT_GENERATION_ISSUE, "Contract generation issue"),
        (CAT_MERCHANT_COMMISSION_ISSUE, "Merchant commission issue"),
        (CAT_SUSPECTED_CUSTOMER_FRAUD, "Suspected customer fraud"),
        (CAT_FINANCE_REVIEW, "Finance review"),
        (CAT_RECOVERY_REVIEW, "Recovery review"),
        (CAT_LEGAL_REVIEW, "Legal review"),
        (CAT_MANAGEMENT_ESCALATION, "Management escalation"),
        (CAT_FRAUD_REPORT, "Fraud report"),
        (CAT_SYSTEM_ISSUE, "System issue"),
        (CAT_OTHER,            "Other"),
    ]

    # ── priorities ───────────────────────────────────────────────────────────
    PRI_LOW      = "low"
    PRI_NORMAL   = "normal"
    PRI_MEDIUM   = "medium"
    PRI_HIGH     = "high"
    PRI_URGENT   = "urgent"
    PRI_CRITICAL = "critical"

    PRIORITY_CHOICES = [
        (PRI_LOW,      "Low"),
        (PRI_NORMAL,   "Normal"),
        (PRI_MEDIUM,   "Medium"),
        (PRI_HIGH,     "High"),
        (PRI_URGENT,   "Urgent"),
        (PRI_CRITICAL, "Critical"),
    ]

    # ── statuses ─────────────────────────────────────────────────────────────
    STATUS_NEW            = "new"
    STATUS_OPEN           = "open"
    STATUS_ASSIGNED       = "assigned"
    STATUS_IN_PROGRESS    = "in_progress"
    STATUS_WAITING_USER   = "waiting_user"
    STATUS_WAITING_CUSTOMER = "waiting_for_customer"
    STATUS_WAITING_PROV   = "waiting_provider"
    STATUS_ESCALATED      = "escalated"
    STATUS_RESOLVED       = "resolved"
    STATUS_CLOSED         = "closed"

    STATUS_CHOICES = [
        (STATUS_NEW,          "New"),
        (STATUS_OPEN,         "Open"),
        (STATUS_ASSIGNED,     "Assigned"),
        (STATUS_IN_PROGRESS,  "In Progress"),
        (STATUS_WAITING_USER, "Waiting on User"),
        (STATUS_WAITING_CUSTOMER, "Waiting for Customer"),
        (STATUS_WAITING_PROV, "Waiting on Provider"),
        (STATUS_ESCALATED,    "Escalated to HQ"),
        (STATUS_RESOLVED,     "Resolved"),
        (STATUS_CLOSED,       "Closed"),
    ]

    ticket_number = models.CharField(max_length=24, unique=True, blank=True, db_index=True)
    title       = models.CharField(max_length=200)
    description = models.TextField()
    category    = models.CharField(max_length=30, choices=CATEGORY_CHOICES, default=CAT_OTHER)
    priority    = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default=PRI_MEDIUM)
    status      = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN)

    created_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_tickets",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tickets",
    )

    # Optional context links
    related_merchant    = models.ForeignKey(
        "website.MerchantLead", on_delete=models.SET_NULL, null=True, blank=True, related_name="tickets"
    )
    related_application = models.ForeignKey(
        "applications.FinancingApplication", on_delete=models.SET_NULL, null=True, blank=True, related_name="tickets"
    )
    related_contract    = models.ForeignKey(
        "contracts.Contract", on_delete=models.SET_NULL, null=True, blank=True, related_name="tickets"
    )
    related_payment_ref = models.CharField(max_length=100, blank=True)
    related_device_imei = models.CharField(max_length=50, blank=True)

    screenshot      = models.FileField(upload_to="support/screenshots/", blank=True, null=True)
    internal_notes  = models.TextField(blank=True)
    resolution_note = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at  = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=30, blank=True, default="")
    sender_phone = models.CharField(max_length=30, blank=True, default="", db_index=True)
    sender_name = models.CharField(max_length=160, blank=True, default="")
    sender_type = models.CharField(max_length=30, blank=True, default="unknown")
    linked_contact = models.ForeignKey(
        "WhatsAppContact",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tickets",
    )
    linked_customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="customer_support_tickets",
    )
    linked_merchant = models.ForeignKey(
        "merchants.Merchant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_tickets",
    )
    linked_underwriter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="underwriter_support_tickets",
    )
    first_response_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    sla_due_at = models.DateTimeField(null=True, blank=True)
    escalation_reason = models.TextField(blank=True, default="")
    resolution_summary = models.TextField(blank=True, default="")
    last_message_preview = models.CharField(max_length=240, blank=True, default="")
    last_message_at = models.DateTimeField(null=True, blank=True)
    reopened_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Support Ticket"
        verbose_name_plural = "Support Tickets"

    def __str__(self):
        return f"#{self.pk} — {self.title}"

    def save(self, *args, **kwargs):
        needs_ticket_number = not self.ticket_number
        super().save(*args, **kwargs)
        if needs_ticket_number:
            year = (self.created_at or timezone.now()).year
            self.ticket_number = f"TS-{year}-{self.pk:06d}"
            super().save(update_fields=["ticket_number"])


class TicketComment(models.Model):
    ticket     = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name="comments")
    author     = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    body       = models.TextField()
    is_internal = models.BooleanField(
        default=False,
        help_text="Internal notes are visible only to HQ, Tech Support, and Merchant Administrators.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Ticket Comment"

    def __str__(self):
        return f"Comment on #{self.ticket_id} by {self.author_id}"


class BugEvent(models.Model):
    # ── sources ──────────────────────────────────────────────────────────────
    SRC_PAYMENT     = "payment"
    SRC_SMS         = "sms"
    SRC_DEVICE_LOCK = "device_lock"
    SRC_WEBHOOK     = "webhook"
    SRC_EMAIL       = "email"
    SRC_PDF         = "pdf"
    SRC_CONTRACT    = "contract"
    SRC_PAYOUT      = "payout"
    SRC_APP_ERROR   = "app_error"
    SRC_AUTH        = "auth"
    SRC_OTHER       = "other"

    SOURCE_CHOICES = [
        (SRC_PAYMENT,     "Payment failure"),
        (SRC_SMS,         "SMS / notification failure"),
        (SRC_DEVICE_LOCK, "Device lock / unlock failure"),
        (SRC_WEBHOOK,     "Webhook failure"),
        (SRC_EMAIL,       "Email failure"),
        (SRC_PDF,         "PDF generation failure"),
        (SRC_CONTRACT,    "Contract creation failure"),
        (SRC_PAYOUT,      "Payout failure"),
        (SRC_APP_ERROR,   "Application error"),
        (SRC_AUTH,        "Permission / auth error"),
        (SRC_OTHER,       "Other"),
    ]

    # ── severities ───────────────────────────────────────────────────────────
    SEV_LOW      = "low"
    SEV_MEDIUM   = "medium"
    SEV_HIGH     = "high"
    SEV_CRITICAL = "critical"

    SEVERITY_CHOICES = [
        (SEV_LOW,      "Low"),
        (SEV_MEDIUM,   "Medium"),
        (SEV_HIGH,     "High"),
        (SEV_CRITICAL, "Critical"),
    ]

    # ── statuses ─────────────────────────────────────────────────────────────
    STAT_NEW          = "new"
    STAT_INVESTIGATING = "investigating"
    STAT_FIXED        = "fixed"
    STAT_IGNORED      = "ignored"
    STAT_ESCALATED    = "escalated"

    BUG_STATUS_CHOICES = [
        (STAT_NEW,           "New"),
        (STAT_INVESTIGATING, "Investigating"),
        (STAT_FIXED,         "Fixed"),
        (STAT_IGNORED,       "Ignored"),
        (STAT_ESCALATED,     "Escalated"),
    ]

    source           = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=SRC_OTHER)
    severity         = models.CharField(max_length=10, choices=SEVERITY_CHOICES, default=SEV_MEDIUM)
    status           = models.CharField(max_length=20, choices=BUG_STATUS_CHOICES, default=STAT_NEW)
    message          = models.TextField()
    traceback_safe_summary = models.TextField(blank=True)

    related_user        = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="bug_events"
    )
    related_contract    = models.CharField(max_length=50, blank=True)
    related_application = models.CharField(max_length=50, blank=True)
    related_payment     = models.CharField(max_length=100, blank=True)
    related_device      = models.CharField(max_length=50, blank=True)

    metadata         = models.JSONField(default=dict, blank=True)
    first_seen_at    = models.DateTimeField(default=timezone.now)
    last_seen_at     = models.DateTimeField(default=timezone.now)
    occurrence_count = models.PositiveIntegerField(default=1)

    resolved_by      = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="resolved_bugs"
    )
    resolved_at      = models.DateTimeField(null=True, blank=True)
    resolution_note  = models.TextField(blank=True)

    class Meta:
        ordering = ["-last_seen_at"]
        verbose_name = "Bug Event"
        verbose_name_plural = "Bug Events"

    def __str__(self):
        return f"[{self.get_severity_display()}] {self.source}: {self.message[:80]}"

    @classmethod
    def record(cls, source, message, severity=SEV_MEDIUM, metadata=None, **kwargs):
        """
        Create or update a BugEvent.  Deduplicates by (source, message[:200]).
        Metadata is automatically sanitized before storage.
        """
        from .utils import sanitize_error_metadata
        safe_meta = sanitize_error_metadata(metadata or {})
        key_msg = message[:200]
        existing = cls.objects.filter(source=source, message__startswith=key_msg, status=cls.STAT_NEW).first()
        if existing:
            existing.occurrence_count += 1
            existing.last_seen_at = timezone.now()
            existing.metadata = safe_meta
            existing.save(update_fields=["occurrence_count", "last_seen_at", "metadata"])
            return existing
        return cls.objects.create(
            source=source,
            message=message,
            severity=severity,
            metadata=safe_meta,
            **kwargs,
        )


# ─────────────────────────────────────────────────────────────────────────────
# WhatsApp / Chatbot models
# ─────────────────────────────────────────────────────────────────────────────

class SupportConversation(models.Model):
    """Tracks a single WhatsApp conversation session with a user."""

    USER_TYPE_CUSTOMER    = "customer"
    USER_TYPE_MERCHANT    = "merchant"
    USER_TYPE_UNDERWRITER = "underwriter"
    USER_TYPE_STAFF       = "staff"
    USER_TYPE_HQ          = "hq"
    USER_TYPE_UNKNOWN     = "unknown"

    USER_TYPE_CHOICES = [
        (USER_TYPE_CUSTOMER,    "Customer"),
        (USER_TYPE_MERCHANT,    "Merchant"),
        (USER_TYPE_UNDERWRITER, "Underwriter"),
        (USER_TYPE_STAFF,       "Staff / Employee"),
        (USER_TYPE_HQ,          "HQ / Admin"),
        (USER_TYPE_UNKNOWN,     "Unknown"),
    ]

    LANG_EN = "en"
    LANG_NY = "ny"
    LANGUAGE_CHOICES = [
        (LANG_EN, "English"),
        (LANG_NY, "Chichewa"),
    ]

    # Chatbot state machine states
    STATE_WELCOME     = "welcome"
    STATE_CUSTOMER    = "customer"
    STATE_MERCHANT    = "merchant"
    STATE_UNDERWRITER = "underwriter"
    STATE_STAFF       = "staff"
    STATE_FRAUD       = "fraud"
    STATE_ISSUE       = "issue"
    STATE_TICKET_DONE = "ticket_done"
    STATE_HUMAN       = "human"

    STATE_CHOICES = [
        (STATE_WELCOME,     "Welcome / Main Menu"),
        (STATE_CUSTOMER,    "Customer Menu"),
        (STATE_MERCHANT,    "Merchant Menu"),
        (STATE_UNDERWRITER, "Underwriter Menu"),
        (STATE_STAFF,       "Staff Menu"),
        (STATE_FRAUD,       "Fraud Report Menu"),
        (STATE_ISSUE,       "Issue Captured"),
        (STATE_TICKET_DONE, "Ticket Created"),
        (STATE_HUMAN,       "Awaiting Human Agent"),
    ]

    phone_number    = models.CharField(max_length=30, db_index=True)
    whatsapp_name   = models.CharField(max_length=120, blank=True)
    user_type       = models.CharField(max_length=20, choices=USER_TYPE_CHOICES, default=USER_TYPE_UNKNOWN)
    current_state   = models.CharField(max_length=30, choices=STATE_CHOICES, default=STATE_WELCOME)
    language        = models.CharField(max_length=5, choices=LANGUAGE_CHOICES, default=LANG_EN)
    metadata        = models.JSONField(default=dict, blank=True)

    # Linked user if we can identify them
    linked_user     = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_conversations",
    )
    # Linked ticket if a ticket was raised
    active_ticket   = models.ForeignKey(
        "SupportTicket",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations",
    )

    last_message_at = models.DateTimeField(default=timezone.now)
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_message_at"]
        verbose_name = "Support Conversation"
        verbose_name_plural = "Support Conversations"

    def __str__(self):
        return f"Conversation {self.phone_number} [{self.current_state}]"

    @classmethod
    def get_or_create_for_phone(cls, phone_number, whatsapp_name=""):
        """Get or create a conversation for a phone number."""
        obj, created = cls.objects.get_or_create(
            phone_number=phone_number,
            defaults={"whatsapp_name": whatsapp_name or ""},
        )
        if not created and whatsapp_name and not obj.whatsapp_name:
            obj.whatsapp_name = whatsapp_name
            obj.save(update_fields=["whatsapp_name"])
        return obj, created


class SupportMessage(models.Model):
    """Individual message in a support conversation."""

    SENDER_USER   = "user"
    SENDER_BOT    = "bot"
    SENDER_ADMIN  = "admin"
    SENDER_SYSTEM = "system"

    SENDER_CHOICES = [
        (SENDER_USER,   "User"),
        (SENDER_BOT,    "Bot"),
        (SENDER_ADMIN,  "Admin"),
        (SENDER_SYSTEM, "System"),
    ]

    conversation = models.ForeignKey(
        SupportConversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    ticket = models.ForeignKey(
        "SupportTicket",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chat_messages",
    )
    sender      = models.CharField(max_length=10, choices=SENDER_CHOICES)
    message     = models.TextField()
    raw_payload = models.JSONField(default=dict, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Support Message"
        verbose_name_plural = "Support Messages"

    def __str__(self):
        return f"[{self.sender}] {self.message[:60]}"


class SupportCategory(models.Model):
    """Configurable support categories for chatbot routing."""

    name               = models.CharField(max_length=100)
    slug               = models.SlugField(unique=True)
    user_type          = models.CharField(
        max_length=20,
        choices=SupportConversation.USER_TYPE_CHOICES,
        default=SupportConversation.USER_TYPE_UNKNOWN,
    )
    default_priority   = models.CharField(
        max_length=10,
        choices=SupportTicket.PRIORITY_CHOICES,
        default=SupportTicket.PRI_MEDIUM,
    )
    maps_to_category   = models.CharField(
        max_length=30,
        choices=SupportTicket.CATEGORY_CHOICES,
        default=SupportTicket.CAT_OTHER,
    )
    chatbot_menu_label = models.CharField(max_length=120, blank=True)
    is_active          = models.BooleanField(default=True)
    sort_order         = models.PositiveSmallIntegerField(default=10)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "Support Category"
        verbose_name_plural = "Support Categories"

    def __str__(self):
        return self.name


class WhatsAppContact(models.Model):
    SENDER_CUSTOMER = "customer"
    SENDER_MERCHANT = "merchant"
    SENDER_UNDERWRITER = "underwriter"
    SENDER_UNKNOWN = "unknown"

    SENDER_TYPE_CHOICES = [
        (SENDER_CUSTOMER, "Customer"),
        (SENDER_MERCHANT, "Merchant"),
        (SENDER_UNDERWRITER, "Underwriter"),
        (SENDER_UNKNOWN, "Unknown"),
    ]

    phone_e164 = models.CharField(max_length=30, unique=True, db_index=True)
    whatsapp_id = models.CharField(max_length=80, blank=True, default="")
    profile_name = models.CharField(max_length=160, blank=True, default="")
    sender_type = models.CharField(max_length=30, choices=SENDER_TYPE_CHOICES, default=SENDER_UNKNOWN)
    linked_customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_customer_contacts",
    )
    linked_merchant = models.ForeignKey(
        "merchants.Merchant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_contacts",
    )
    linked_underwriter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_underwriter_contacts",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_seen_at", "-updated_at"]

    def __str__(self):
        return f"{self.phone_e164} ({self.sender_type})"


class WhatsAppConversation(models.Model):
    STATUS_OPEN = "open"
    STATUS_WAITING_FOR_CUSTOMER = "waiting_for_customer"
    STATUS_RESOLVED = "resolved"
    STATUS_CLOSED = "closed"

    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
        (STATUS_WAITING_FOR_CUSTOMER, "Waiting for Customer"),
        (STATUS_RESOLVED, "Resolved"),
        (STATUS_CLOSED, "Closed"),
    ]

    contact = models.OneToOneField(WhatsAppContact, on_delete=models.CASCADE, related_name="conversation")
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_OPEN)
    last_message_at = models.DateTimeField(null=True, blank=True)
    last_message_preview = models.CharField(max_length=240, blank=True, default="")
    active_ticket = models.ForeignKey(
        SupportTicket,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_conversations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_message_at", "-updated_at"]

    def __str__(self):
        return f"WhatsApp conversation {self.contact.phone_e164}"


class WhatsAppMessage(models.Model):
    DIRECTION_INBOUND = "inbound"
    DIRECTION_OUTBOUND = "outbound"
    PROVIDER_MOCK = "mock"
    PROVIDER_TWILIO = "twilio"

    STATUS_QUEUED = "queued"
    STATUS_SENT = "sent"
    STATUS_DELIVERED = "delivered"
    STATUS_READ = "read"
    STATUS_FAILED = "failed"
    STATUS_UNDELIVERED = "undelivered"
    STATUS_RECEIVED = "received"
    STATUS_UNKNOWN = "unknown"

    DIRECTION_CHOICES = [
        (DIRECTION_INBOUND, "Inbound"),
        (DIRECTION_OUTBOUND, "Outbound"),
    ]
    PROVIDER_CHOICES = [
        (PROVIDER_MOCK, "Mock"),
        (PROVIDER_TWILIO, "Twilio"),
    ]
    STATUS_CHOICES = [
        (STATUS_QUEUED, "Queued"),
        (STATUS_SENT, "Sent"),
        (STATUS_DELIVERED, "Delivered"),
        (STATUS_READ, "Read"),
        (STATUS_FAILED, "Failed"),
        (STATUS_UNDELIVERED, "Undelivered"),
        (STATUS_RECEIVED, "Received"),
        (STATUS_UNKNOWN, "Unknown"),
    ]

    conversation = models.ForeignKey(WhatsAppConversation, on_delete=models.CASCADE, related_name="messages")
    ticket = models.ForeignKey(SupportTicket, on_delete=models.SET_NULL, null=True, blank=True, related_name="whatsapp_messages")
    direction = models.CharField(max_length=20, choices=DIRECTION_CHOICES)
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, default=PROVIDER_MOCK)
    provider_message_sid = models.CharField(max_length=80, blank=True, default="", db_index=True)
    provider_status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_UNKNOWN)
    from_phone = models.CharField(max_length=30, blank=True, default="")
    to_phone = models.CharField(max_length=30, blank=True, default="")
    body = models.TextField(blank=True, default="")
    media_count = models.PositiveIntegerField(default=0)
    media_json = models.JSONField(default=list, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, default="")
    error_code = models.CharField(max_length=20, blank=True, default="")
    last_status_callback_at = models.DateTimeField(null=True, blank=True)
    sent_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_whatsapp_support_messages",
    )
    received_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.direction}: {self.body[:60]}"


class SupportTicketAuditLog(models.Model):
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name="audit_logs")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=80)
    from_status = models.CharField(max_length=30, blank=True, default="")
    to_status = models.CharField(max_length=30, blank=True, default="")
    note = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.ticket.ticket_number} {self.action}"


class SupportTicketInternalNote(models.Model):
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name="internal_note_entries")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    note = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Internal note on {self.ticket.ticket_number}"
