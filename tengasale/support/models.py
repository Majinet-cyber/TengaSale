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
        (CAT_OTHER,            "Other"),
    ]

    # ── priorities ───────────────────────────────────────────────────────────
    PRI_LOW      = "low"
    PRI_MEDIUM   = "medium"
    PRI_HIGH     = "high"
    PRI_CRITICAL = "critical"

    PRIORITY_CHOICES = [
        (PRI_LOW,      "Low"),
        (PRI_MEDIUM,   "Medium"),
        (PRI_HIGH,     "High"),
        (PRI_CRITICAL, "Critical"),
    ]

    # ── statuses ─────────────────────────────────────────────────────────────
    STATUS_OPEN           = "open"
    STATUS_ASSIGNED       = "assigned"
    STATUS_IN_PROGRESS    = "in_progress"
    STATUS_WAITING_USER   = "waiting_user"
    STATUS_WAITING_PROV   = "waiting_provider"
    STATUS_ESCALATED      = "escalated"
    STATUS_RESOLVED       = "resolved"
    STATUS_CLOSED         = "closed"

    STATUS_CHOICES = [
        (STATUS_OPEN,         "Open"),
        (STATUS_ASSIGNED,     "Assigned"),
        (STATUS_IN_PROGRESS,  "In Progress"),
        (STATUS_WAITING_USER, "Waiting on User"),
        (STATUS_WAITING_PROV, "Waiting on Provider"),
        (STATUS_ESCALATED,    "Escalated to HQ"),
        (STATUS_RESOLVED,     "Resolved"),
        (STATUS_CLOSED,       "Closed"),
    ]

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

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Support Ticket"
        verbose_name_plural = "Support Tickets"

    def __str__(self):
        return f"#{self.pk} — {self.title}"

    @property
    def ticket_number(self):
        return f"TS-{self.pk:05d}"


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
