"""Tech Support portal views."""
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.decorators import tech_support_required
from accounts.utils import is_hq, is_hq_or_tech_support, is_merchant_admin
from core.models import AuditLog

from .models import BugEvent, SupportConversation, SupportTicket, TicketComment


HEALTH_SOURCES = [
    (BugEvent.SRC_PAYMENT, "Payments"),
    (BugEvent.SRC_WEBHOOK, "Webhooks"),
    (BugEvent.SRC_DEVICE_LOCK, "Device Lock"),
    (BugEvent.SRC_SMS, "SMS"),
    (BugEvent.SRC_EMAIL, "Email"),
    (BugEvent.SRC_PAYOUT, "Payouts"),
    (BugEvent.SRC_PDF, "PDF Generation"),
]


def _can_manage_ticket(user):
    return is_hq(user) or is_hq_or_tech_support(user) or is_merchant_admin(user)


def _visible_comments(ticket, user):
    qs = ticket.comments.select_related("author")
    if not _can_manage_ticket(user):
        qs = qs.filter(is_internal=False)
    return qs


def _audit_ticket(user, ticket, action, detail=None):
    AuditLog.objects.create(
        user=user,
        action=AuditLog.ACTION_KYC_CHANGE,
        object_type="SupportTicket",
        object_id=str(ticket.pk),
        detail={"action": action, "ticket": ticket.ticket_number, **(detail or {})},
    )


def _audit_bug(user, bug, action, detail=None):
    AuditLog.objects.create(
        user=user,
        action=AuditLog.ACTION_KYC_CHANGE,
        object_type="BugEvent",
        object_id=str(bug.pk),
        detail={"action": action, **(detail or {})},
    )


def _health_pulse(since):
    pulse = []
    for source, label in HEALTH_SOURCES:
        qs = BugEvent.objects.filter(source=source, last_seen_at__gte=since).exclude(status=BugEvent.STAT_FIXED)
        count = qs.count()
        critical = qs.filter(severity=BugEvent.SEV_CRITICAL).count()
        latest = qs.order_by("-last_seen_at").first()
        if critical:
            level, level_label = "critical", "Critical"
        elif count >= 3:
            level, level_label = "warning", "Warning"
        else:
            level, level_label = "healthy", "Healthy"
        pulse.append({
            "source": source,
            "label": label,
            "count": count,
            "level": level,
            "level_label": level_label,
            "last_seen": latest.last_seen_at if latest else None,
        })
    return pulse


@tech_support_required
def support_dashboard(request):
    today = timezone.now().date()
    since_24h = timezone.now() - timedelta(hours=24)

    open_tickets = SupportTicket.objects.filter(
        status__in=[SupportTicket.STATUS_OPEN, SupportTicket.STATUS_ASSIGNED, SupportTicket.STATUS_IN_PROGRESS]
    ).count()
    critical_tickets = SupportTicket.objects.filter(
        priority=SupportTicket.PRI_CRITICAL,
        status__in=[SupportTicket.STATUS_OPEN, SupportTicket.STATUS_ASSIGNED, SupportTicket.STATUS_IN_PROGRESS],
    ).count()
    my_tickets = SupportTicket.objects.filter(
        assigned_to=request.user,
        status__in=[SupportTicket.STATUS_OPEN, SupportTicket.STATUS_ASSIGNED, SupportTicket.STATUS_IN_PROGRESS],
    ).count()

    critical_bugs = BugEvent.objects.filter(severity=BugEvent.SEV_CRITICAL, status=BugEvent.STAT_NEW).count()
    new_bugs = BugEvent.objects.filter(status=BugEvent.STAT_NEW).count()

    failed_payments = BugEvent.objects.filter(
        source=BugEvent.SRC_PAYMENT, last_seen_at__date=today
    ).exclude(status=BugEvent.STAT_FIXED).count()
    failed_locks = BugEvent.objects.filter(
        source=BugEvent.SRC_DEVICE_LOCK, last_seen_at__date=today
    ).exclude(status=BugEvent.STAT_FIXED).count()
    unmatched_payments = BugEvent.objects.filter(
        source=BugEvent.SRC_PAYMENT, status=BugEvent.STAT_NEW
    ).count()

    ctx = {
        "is_hq_user": is_hq(request.user),
        "open_tickets": open_tickets,
        "critical_tickets": critical_tickets,
        "my_tickets": my_tickets,
        "critical_bugs": critical_bugs,
        "new_bugs": new_bugs,
        "failed_payments": failed_payments,
        "failed_locks": failed_locks,
        "unmatched_payments": unmatched_payments,
        "recent_tickets": SupportTicket.objects.select_related("created_by", "assigned_to").order_by("-created_at")[:10],
        "recent_bugs": BugEvent.objects.exclude(status=BugEvent.STAT_FIXED).order_by("-last_seen_at")[:6],
        "health_pulse": _health_pulse(since_24h),
    }
    return render(request, "support/dashboard.html", ctx)


@login_required
def ticket_list(request):
    qs = SupportTicket.objects.select_related("created_by", "assigned_to").order_by("-created_at")
    if not _can_manage_ticket(request.user):
        qs = qs.filter(created_by=request.user)

    status_f = request.GET.get("status", "")
    category_f = request.GET.get("category", "")
    priority_f = request.GET.get("priority", "")
    if status_f:
        qs = qs.filter(status=status_f)
    if category_f:
        qs = qs.filter(category=category_f)
    if priority_f:
        qs = qs.filter(priority=priority_f)

    ctx = {
        "is_hq_user": is_hq(request.user),
        "tickets": qs,
        "status_choices": SupportTicket.STATUS_CHOICES,
        "category_choices": SupportTicket.CATEGORY_CHOICES,
        "priority_choices": SupportTicket.PRIORITY_CHOICES,
        "status_f": status_f,
        "category_f": category_f,
        "priority_f": priority_f,
        "can_manage": _can_manage_ticket(request.user),
    }
    return render(request, "support/ticket_list.html", ctx)


@login_required
def ticket_create(request):
    lead_id = request.GET.get("lead_id") or request.POST.get("lead_id")
    related_lead = None
    if lead_id:
        try:
            from website.models import MerchantLead
            related_lead = MerchantLead.objects.get(pk=lead_id)
        except Exception:
            pass

    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        desc = request.POST.get("description", "").strip()
        category = request.POST.get("category", SupportTicket.CAT_OTHER)
        priority = request.POST.get("priority", SupportTicket.PRI_MEDIUM)

        if not title or not desc:
            messages.error(request, "Title and description are required.")
            return render(request, "support/ticket_form.html", {
                "category_choices": SupportTicket.CATEGORY_CHOICES,
                "priority_choices": SupportTicket.PRIORITY_CHOICES,
                "post": request.POST,
                "related_lead": related_lead,
                "is_hq_user": is_hq(request.user),
                "show_support_topbar": _can_manage_ticket(request.user),
            })

        ticket = SupportTicket.objects.create(
            title=title,
            description=desc,
            category=category,
            priority=priority,
            created_by=request.user,
            status=SupportTicket.STATUS_OPEN,
            related_merchant=related_lead,
            related_payment_ref=request.POST.get("related_payment_ref", "").strip(),
            related_device_imei=request.POST.get("related_device_imei", "").strip(),
        )
        if "screenshot" in request.FILES:
            ticket.screenshot = request.FILES["screenshot"]
            ticket.save(update_fields=["screenshot"])

        _audit_ticket(request.user, ticket, "created")
        messages.success(request, f"Ticket {ticket.ticket_number} created successfully.")
        return redirect("ticket_detail", ticket_id=ticket.pk)

    default_title = ""
    default_desc = ""
    if related_lead:
        default_title = f"Merchant lead: {related_lead.business_name}"
        default_desc = (
            f"Lead #{related_lead.pk} — {related_lead.business_name}\n"
            f"Owner: {related_lead.owner_full_name}\nPhone: {related_lead.phone}"
        )

    ctx = {
        "category_choices": SupportTicket.CATEGORY_CHOICES,
        "priority_choices": SupportTicket.PRIORITY_CHOICES,
        "post": {"title": default_title, "description": default_desc},
        "related_lead": related_lead,
        "is_hq_user": is_hq(request.user),
        "show_support_topbar": _can_manage_ticket(request.user),
    }
    return render(request, "support/ticket_form.html", ctx)


@login_required
def ticket_detail(request, ticket_id):
    ticket = get_object_or_404(SupportTicket, pk=ticket_id)
    can_manage = _can_manage_ticket(request.user)

    if not can_manage and ticket.created_by != request.user:
        messages.error(request, "You do not have permission to view this ticket.")
        return redirect("ticket_list")

    if request.method == "POST":
        body = request.POST.get("body", "").strip()
        if body:
            is_internal = request.POST.get("is_internal") == "1" and can_manage
            TicketComment.objects.create(
                ticket=ticket,
                author=request.user,
                body=body,
                is_internal=is_internal,
            )
            ticket.save(update_fields=["updated_at"])
            _audit_ticket(request.user, ticket, "comment_added", {"internal": is_internal})
            messages.success(request, "Comment added.")
        return redirect("ticket_detail", ticket_id=ticket_id)

    staff_users = get_user_model().objects.filter(
        profile__role__in=["hq", "tech_support", "merchant_admin"]
    ).select_related("profile").order_by("username") if can_manage else []

    ctx = {
        "is_hq_user": is_hq(request.user),
        "ticket": ticket,
        "comments": _visible_comments(ticket, request.user),
        "can_manage": can_manage,
        "staff_users": staff_users,
        "status_choices": SupportTicket.STATUS_CHOICES,
        "priority_choices": SupportTicket.PRIORITY_CHOICES,
    }
    return render(request, "support/ticket_detail.html", ctx)


@tech_support_required
def ticket_assign(request, ticket_id):
    ticket = get_object_or_404(SupportTicket, pk=ticket_id)
    if request.method == "POST":
        uid = request.POST.get("assigned_to")
        if uid:
            try:
                ticket.assigned_to = get_user_model().objects.get(pk=uid)
                ticket.status = SupportTicket.STATUS_ASSIGNED
                ticket.save(update_fields=["assigned_to", "status", "updated_at"])
                _audit_ticket(request.user, ticket, "assigned", {"to": ticket.assigned_to.username})
                messages.success(request, f"Ticket assigned to {ticket.assigned_to.username}.")
            except get_user_model().DoesNotExist:
                messages.error(request, "User not found.")
    return redirect("ticket_detail", ticket_id=ticket_id)


@tech_support_required
def ticket_assign_me(request, ticket_id):
    ticket = get_object_or_404(SupportTicket, pk=ticket_id)
    if request.method == "POST":
        ticket.assigned_to = request.user
        ticket.status = SupportTicket.STATUS_ASSIGNED
        ticket.save(update_fields=["assigned_to", "status", "updated_at"])
        _audit_ticket(request.user, ticket, "assigned_self")
        messages.success(request, f"Ticket {ticket.ticket_number} assigned to you.")
    return redirect("ticket_detail", ticket_id=ticket_id)


@tech_support_required
def ticket_status(request, ticket_id):
    ticket = get_object_or_404(SupportTicket, pk=ticket_id)
    if request.method == "POST":
        new_status = request.POST.get("status", "").strip()
        new_priority = request.POST.get("priority", "").strip()
        valid_status = {s for s, _ in SupportTicket.STATUS_CHOICES}
        if new_status in valid_status:
            ticket.status = new_status
        if new_priority in {p for p, _ in SupportTicket.PRIORITY_CHOICES}:
            ticket.priority = new_priority
        ticket.save(update_fields=["status", "priority", "updated_at"])
        _audit_ticket(request.user, ticket, "status_updated", {"status": ticket.status, "priority": ticket.priority})
        messages.success(request, "Ticket updated.")
    return redirect("ticket_detail", ticket_id=ticket_id)


@tech_support_required
def ticket_escalate(request, ticket_id):
    ticket = get_object_or_404(SupportTicket, pk=ticket_id)
    if request.method == "POST":
        ticket.status = SupportTicket.STATUS_ESCALATED
        ticket.save(update_fields=["status", "updated_at"])
        _audit_ticket(request.user, ticket, "escalated")
        messages.warning(request, f"Ticket {ticket.ticket_number} escalated to HQ.")
    return redirect("ticket_detail", ticket_id=ticket_id)


@tech_support_required
def ticket_resolve(request, ticket_id):
    ticket = get_object_or_404(SupportTicket, pk=ticket_id)
    if request.method == "POST":
        note = request.POST.get("resolution_note", "").strip()
        if not note:
            messages.error(request, "A resolution note is required to resolve a ticket.")
            return redirect("ticket_detail", ticket_id=ticket_id)
        ticket.resolution_note = note
        ticket.status = SupportTicket.STATUS_RESOLVED
        ticket.closed_at = timezone.now()
        ticket.save(update_fields=["resolution_note", "status", "closed_at", "updated_at"])
        _audit_ticket(request.user, ticket, "resolved")
        messages.success(request, f"Ticket {ticket.ticket_number} resolved.")
    return redirect("ticket_detail", ticket_id=ticket_id)


@tech_support_required
def ticket_reopen(request, ticket_id):
    ticket = get_object_or_404(SupportTicket, pk=ticket_id)
    if request.method == "POST":
        ticket.status = SupportTicket.STATUS_OPEN
        ticket.closed_at = None
        ticket.save(update_fields=["status", "closed_at", "updated_at"])
        _audit_ticket(request.user, ticket, "reopened")
        messages.success(request, f"Ticket {ticket.ticket_number} reopened.")
    return redirect("ticket_detail", ticket_id=ticket_id)


@tech_support_required
def bug_monitor(request):
    qs = BugEvent.objects.order_by("-last_seen_at")
    severity_f = request.GET.get("severity", "")
    source_f = request.GET.get("source", "")
    status_f = request.GET.get("status", "")
    if severity_f:
        qs = qs.filter(severity=severity_f)
    if source_f:
        qs = qs.filter(source=source_f)
    if status_f:
        qs = qs.filter(status=status_f)

    ctx = {
        "is_hq_user": is_hq(request.user),
        "bugs": qs[:200],
        "severity_choices": BugEvent.SEVERITY_CHOICES,
        "source_choices": BugEvent.SOURCE_CHOICES,
        "status_choices": BugEvent.BUG_STATUS_CHOICES,
        "severity_f": severity_f,
        "source_f": source_f,
        "status_f": status_f,
        "new_count": BugEvent.objects.filter(status=BugEvent.STAT_NEW).count(),
        "critical_count": BugEvent.objects.filter(severity=BugEvent.SEV_CRITICAL, status=BugEvent.STAT_NEW).count(),
    }
    return render(request, "support/bug_monitor.html", ctx)


@tech_support_required
def bug_detail(request, bug_id):
    bug = get_object_or_404(BugEvent, pk=bug_id)
    ctx = {
        "is_hq_user": is_hq(request.user),
        "bug": bug,
        "show_traceback": is_hq(request.user),
    }
    return render(request, "support/bug_detail.html", ctx)


@tech_support_required
def bug_resolve(request, bug_id):
    bug = get_object_or_404(BugEvent, pk=bug_id)
    if request.method == "POST":
        note = request.POST.get("resolution_note", "").strip()
        if not note:
            messages.error(request, "A resolution note is required.")
            return redirect("bug_detail", bug_id=bug_id)
        bug.status = BugEvent.STAT_FIXED
        bug.resolved_by = request.user
        bug.resolved_at = timezone.now()
        bug.resolution_note = note
        bug.save(update_fields=["status", "resolved_by", "resolved_at", "resolution_note"])
        _audit_bug(request.user, bug, "fixed", {"note": note[:200]})
        messages.success(request, "Bug marked as fixed.")
    return redirect("bug_detail", bug_id=bug_id)


@tech_support_required
def bug_ignore(request, bug_id):
    bug = get_object_or_404(BugEvent, pk=bug_id)
    if request.method == "POST":
        note = request.POST.get("resolution_note", "").strip()
        if not note:
            messages.error(request, "A reason is required to ignore a bug.")
            return redirect("bug_detail", bug_id=bug_id)
        bug.status = BugEvent.STAT_IGNORED
        bug.resolved_by = request.user
        bug.resolved_at = timezone.now()
        bug.resolution_note = note
        bug.save(update_fields=["status", "resolved_by", "resolved_at", "resolution_note"])
        _audit_bug(request.user, bug, "ignored", {"note": note[:200]})
        messages.info(request, "Bug marked as ignored.")
    return redirect("bug_detail", bug_id=bug_id)


@tech_support_required
def bug_to_ticket(request, bug_id):
    bug = get_object_or_404(BugEvent, pk=bug_id)
    if request.method == "POST":
        ticket = SupportTicket.objects.create(
            title=f"[Bug] {bug.message[:150]}",
            description=(
                f"Auto-generated from Bug #{bug.pk}.\n\n"
                f"Source: {bug.get_source_display()}\n"
                f"Severity: {bug.get_severity_display()}\n"
                f"First seen: {bug.first_seen_at}\n"
                f"Occurrences: {bug.occurrence_count}\n\n"
                f"{bug.traceback_safe_summary}"
            ),
            category=SupportTicket.CAT_APP_ERROR,
            priority=SupportTicket.PRI_HIGH if bug.severity in (BugEvent.SEV_HIGH, BugEvent.SEV_CRITICAL) else SupportTicket.PRI_MEDIUM,
            created_by=request.user,
            status=SupportTicket.STATUS_OPEN,
        )
        bug.status = BugEvent.STAT_INVESTIGATING
        bug.save(update_fields=["status"])
        _audit_bug(request.user, bug, "converted_to_ticket", {"ticket_id": ticket.pk})
        messages.success(request, f"Ticket {ticket.ticket_number} created from bug #{bug.pk}.")
        return redirect("ticket_detail", ticket_id=ticket.pk)
    return redirect("bug_detail", bug_id=bug_id)


@login_required
@tech_support_required
def conversation_list(request):
    """HQ view of all WhatsApp conversations."""
    from .models import WhatsAppConversation

    conversations = (
        WhatsAppConversation.objects
        .select_related("contact", "active_ticket")
        .order_by("-last_message_at", "-updated_at")[:200]
    )
    conversation_count = WhatsAppConversation.objects.count()
    open_conversations = WhatsAppConversation.objects.filter(
        status=WhatsAppConversation.STATUS_OPEN
    ).count()
    return render(request, "support/conversation_list.html", {
        "conversations": conversations,
        "conversation_count": conversation_count,
        "open_conversations": open_conversations,
        "page_title": "WhatsApp Conversations",
    })
