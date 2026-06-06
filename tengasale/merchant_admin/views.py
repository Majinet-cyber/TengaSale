"""Merchant Administrator portal views."""
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.decorators import merchant_admin_required
from accounts.utils import is_hq
from core.business_hours import business_hours_context
from core.models import AuditLog
from website.models import MerchantLead

from .models import MerchantChecklist, MerchantKYC


PIPELINE_STAGES = [
    ("new", "New Lead"),
    ("contacted", "Contacted"),
    ("kyc_pending", "KYC Pending"),
    ("documents_pending", "Docs Pending"),
    ("site_visit_needed", "Site Visit"),
    ("awaiting_hq", "Awaiting HQ"),
    ("approved", "Approved"),
]


def _require_hq_approval():
    return getattr(settings, "REQUIRE_HQ_MERCHANT_APPROVAL", True)


def _audit(user, lead, action, detail=None):
    AuditLog.objects.create(
        user=user,
        action=AuditLog.ACTION_KYC_CHANGE,
        object_type="MerchantLead",
        object_id=str(lead.id),
        detail={"action": action, "lead": lead.business_name, **(detail or {})},
    )


def _pipeline_counts():
    counts = {}
    for key, _ in PIPELINE_STAGES:
        counts[key] = MerchantLead.objects.filter(status=key).count()
    return counts


def _portal_context(request, extra=None):
    ctx = {
        "is_hq_user": is_hq(request.user),
        "require_hq": _require_hq_approval(),
        **business_hours_context(),
    }
    if extra:
        ctx.update(extra)
    return ctx


@merchant_admin_required
def ma_dashboard(request):
    from support.models import SupportTicket
    from commissions.models import MerchantContractPayout
    from merchants.models import Merchant

    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    today = timezone.now().date()

    my_tickets = SupportTicket.objects.filter(
        assigned_to=request.user,
        status__in=[
            SupportTicket.STATUS_OPEN,
            SupportTicket.STATUS_ASSIGNED,
            SupportTicket.STATUS_IN_PROGRESS,
        ],
    ).count()

    ctx = _portal_context(request, {
        "new_leads": MerchantLead.objects.filter(status=MerchantLead.STATUS_NEW).count(),
        "total_merchants": Merchant.objects.count(),
        "active_merchants": Merchant.objects.filter(is_active=True).count(),
        "inactive_merchants": Merchant.objects.filter(is_active=False).count(),
        "pending_verification": MerchantLead.objects.filter(
            status__in=[
                MerchantLead.STATUS_KYC_PENDING,
                MerchantLead.STATUS_DOCUMENTS_PENDING,
                MerchantLead.STATUS_SITE_VISIT,
                MerchantLead.STATUS_AWAITING_HQ,
            ]
        ).count(),
        "suspended_merchants": MerchantLead.objects.filter(status=MerchantLead.STATUS_SUSPENDED).count(),
        "merchant_documents_missing": MerchantLead.objects.filter(status=MerchantLead.STATUS_DOCUMENTS_PENDING).count(),
        "commission_issues": MerchantContractPayout.objects.filter(status__in=["pending", "failed"]).count(),
        "kyc_pending": MerchantLead.objects.filter(status=MerchantLead.STATUS_KYC_PENDING).count(),
        "docs_pending": MerchantLead.objects.filter(status=MerchantLead.STATUS_DOCUMENTS_PENDING).count(),
        "site_visit": MerchantLead.objects.filter(status=MerchantLead.STATUS_SITE_VISIT).count(),
        "awaiting_hq": MerchantLead.objects.filter(status=MerchantLead.STATUS_AWAITING_HQ).count(),
        "approved_month": MerchantLead.objects.filter(
            status=MerchantLead.STATUS_APPROVED, updated_at__gte=month_start
        ).count(),
        "rejected_month": MerchantLead.objects.filter(
            status=MerchantLead.STATUS_REJECTED, updated_at__gte=month_start
        ).count(),
        "leads_today": MerchantLead.objects.filter(created_at__date=today).count(),
        "my_tickets": my_tickets,
        "pending_actions": (
            MerchantLead.objects.filter(
                status__in=[
                    MerchantLead.STATUS_NEW,
                    MerchantLead.STATUS_KYC_PENDING,
                    MerchantLead.STATUS_DOCUMENTS_PENDING,
                    MerchantLead.STATUS_SITE_VISIT,
                    MerchantLead.STATUS_AWAITING_HQ,
                ]
            ).count()
        ),
        "recent_leads": MerchantLead.objects.select_related("assigned_to").order_by("-created_at")[:8],
        "pipeline": [
            {"key": k, "label": lbl, "count": _pipeline_counts().get(k, 0)}
            for k, lbl in PIPELINE_STAGES
        ],
    })
    return render(request, "merchant_admin/dashboard.html", ctx)


@merchant_admin_required
def ma_leads(request):
    status_filter = request.GET.get("status", "")
    leads = MerchantLead.objects.select_related("assigned_to").order_by("-created_at")
    if status_filter:
        leads = leads.filter(status=status_filter)

    ctx = _portal_context(request, {
        "leads": leads,
        "status_filter": status_filter,
        "status_choices": MerchantLead.STATUS_CHOICES,
    })
    return render(request, "merchant_admin/leads.html", ctx)


@merchant_admin_required
def ma_lead_create(request):
    if request.method == "POST":
        business_name = request.POST.get("business_name", "").strip()
        owner_full_name = request.POST.get("owner_full_name", "").strip()
        phone = request.POST.get("phone", "").strip()
        district = request.POST.get("district", "").strip()
        business_type = request.POST.get("business_type", "phone_shop")

        if not all([business_name, owner_full_name, phone, district]):
            messages.error(request, "Business name, owner, phone, and district are required.")
        else:
            lead = MerchantLead.objects.create(
                business_name=business_name,
                owner_full_name=owner_full_name,
                phone=phone,
                whatsapp_phone=request.POST.get("whatsapp_phone", "").strip(),
                email=request.POST.get("email", "").strip(),
                district=district,
                area=request.POST.get("area", "").strip(),
                business_type=business_type,
                message=request.POST.get("message", "").strip(),
                source="manual_entry",
                assigned_to=request.user,
            )
            _audit(request.user, lead, "manual_lead_created")
            messages.success(request, f"Lead for {lead.business_name} created.")
            return redirect("ma_lead_detail", lead_id=lead.pk)

    ctx = _portal_context(request, {
        "business_type_choices": MerchantLead.BUSINESS_TYPE_CHOICES,
    })
    return render(request, "merchant_admin/lead_create.html", ctx)


@merchant_admin_required
def ma_lead_detail(request, lead_id):
    lead = get_object_or_404(MerchantLead, id=lead_id)
    kyc, _ = MerchantKYC.objects.get_or_create(lead=lead)
    checklist, _ = MerchantChecklist.objects.get_or_create(lead=lead)

    ctx = _portal_context(request, {
        "lead": lead,
        "kyc": kyc,
        "checklist": checklist,
        "status_choices": MerchantLead.STATUS_CHOICES,
        "all_staff": get_user_model().objects.filter(
            profile__role__in=["merchant_admin", "hq"]
        ).select_related("profile").order_by("username"),
    })
    return render(request, "merchant_admin/lead_detail.html", ctx)


@merchant_admin_required
def ma_lead_kyc(request, lead_id):
    lead = get_object_or_404(MerchantLead, id=lead_id)
    if request.method != "POST":
        return redirect("ma_lead_detail", lead_id=lead_id)

    kyc, _ = MerchantKYC.objects.get_or_create(lead=lead, defaults={"collected_by": request.user})
    kyc.collected_by = request.user

    kyc.national_id_number = request.POST.get("national_id_number", "").strip()
    kyc.business_reg_number = request.POST.get("business_reg_number", "").strip()
    kyc.region = request.POST.get("region", "").strip()
    kyc.physical_address = request.POST.get("physical_address", "").strip()
    kyc.trading_area = request.POST.get("trading_area", "").strip()
    kyc.bank_name = request.POST.get("bank_name", "").strip()
    kyc.bank_account_name = request.POST.get("bank_account_name", "").strip()
    kyc.bank_account_number = request.POST.get("bank_account_number", "").strip()
    kyc.airtel_money_number = request.POST.get("airtel_money_number", "").strip()
    kyc.tnm_mpamba_number = request.POST.get("tnm_mpamba_number", "").strip()
    kyc.notes = request.POST.get("notes", "").strip()

    lat = request.POST.get("gps_latitude", "").strip()
    lon = request.POST.get("gps_longitude", "").strip()
    try:
        kyc.gps_latitude = float(lat) if lat else None
        kyc.gps_longitude = float(lon) if lon else None
    except ValueError:
        pass

    for field in ["business_certificate", "tax_certificate", "shop_photo_1", "shop_photo_2"]:
        if field in request.FILES:
            setattr(kyc, field, request.FILES[field])

    kyc.save()

    if lead.status == MerchantLead.STATUS_NEW:
        lead.status = MerchantLead.STATUS_KYC_PENDING
        lead.save(update_fields=["status", "updated_at"])

    _audit(request.user, lead, "kyc_saved")
    messages.success(request, "KYC details saved.")
    return redirect("ma_lead_detail", lead_id=lead_id)


@merchant_admin_required
def ma_checklist(request, lead_id):
    lead = get_object_or_404(MerchantLead, id=lead_id)
    if request.method != "POST":
        return redirect("ma_lead_detail", lead_id=lead_id)

    checklist, _ = MerchantChecklist.objects.get_or_create(lead=lead, defaults={"assigned_to": request.user})

    def _bool(key):
        val = request.POST.get(key, "")
        if val == "yes":
            return True
        if val == "no":
            return False
        return None

    checklist.spoke_to_owner = _bool("spoke_to_owner")
    checklist.shop_physically_traceable = _bool("shop_physically_traceable")
    checklist.documents_verified = _bool("documents_verified")
    checklist.bank_name_matches = _bool("bank_name_matches")
    checklist.understands_settlement = _bool("understands_settlement")
    checklist.understands_no_wht = _bool("understands_no_wht")
    checklist.understands_fraud_consequences = _bool("understands_fraud_consequences")
    checklist.approved_for_pilot = _bool("approved_for_pilot")
    checklist.notes = request.POST.get("notes", "").strip()
    checklist.site_visit_completed = request.POST.get("site_visit_completed") == "yes"

    if request.POST.get("site_visit_date"):
        from datetime import date
        try:
            checklist.site_visit_date = date.fromisoformat(request.POST["site_visit_date"])
        except ValueError:
            pass

    checklist.save()
    _audit(request.user, lead, "checklist_saved")
    messages.success(request, "Checklist saved.")
    return redirect("ma_lead_detail", lead_id=lead_id)


@merchant_admin_required
def ma_lead_status(request, lead_id):
    lead = get_object_or_404(MerchantLead, id=lead_id)
    if request.method != "POST":
        return redirect("ma_lead_detail", lead_id=lead_id)

    new_status = request.POST.get("status", "").strip()
    valid_statuses = {s for s, _ in MerchantLead.STATUS_CHOICES}
    if new_status not in valid_statuses:
        messages.error(request, "Invalid status.")
        return redirect("ma_lead_detail", lead_id=lead_id)

    old_status = lead.status
    lead.status = new_status

    assigned_to_id = request.POST.get("assigned_to")
    if assigned_to_id:
        try:
            lead.assigned_to_id = int(assigned_to_id)
        except (ValueError, TypeError):
            pass

    lead.save(update_fields=["status", "assigned_to", "updated_at"])
    _audit(request.user, lead, "status_change", {"from": old_status, "to": new_status})
    messages.success(request, f"Status updated to {lead.get_status_display()}.")
    return redirect("ma_lead_detail", lead_id=lead_id)


@merchant_admin_required
def ma_recommend(request, lead_id):
    lead = get_object_or_404(MerchantLead, id=lead_id)
    if request.method != "POST":
        return redirect("ma_lead_detail", lead_id=lead_id)

    action = request.POST.get("action", "")
    reason = request.POST.get("reason", "").strip()
    checklist, _ = MerchantChecklist.objects.get_or_create(lead=lead)

    if action == "approve":
        checklist.recommendation = MerchantChecklist.RECOMMEND_APPROVE
        checklist.save(update_fields=["recommendation", "updated_at"])
        if _require_hq_approval():
            lead.status = MerchantLead.STATUS_AWAITING_HQ
            messages.info(request, "Approval recommended. Awaiting HQ final approval.")
        else:
            lead.status = MerchantLead.STATUS_APPROVED
            messages.success(request, "Merchant approved.")
        lead.save(update_fields=["status", "updated_at"])

    elif action == "reject":
        if not reason:
            messages.error(request, "A rejection reason is required.")
            return redirect("ma_lead_detail", lead_id=lead_id)
        checklist.recommendation = MerchantChecklist.RECOMMEND_REJECT
        checklist.notes = (checklist.notes + "\n\n[REJECT] " + reason).strip()
        checklist.save(update_fields=["recommendation", "notes", "updated_at"])
        lead.status = MerchantLead.STATUS_REJECTED
        lead.save(update_fields=["status", "updated_at"])
        messages.warning(request, "Merchant lead rejected.")

    elif action == "escalate":
        checklist.recommendation = MerchantChecklist.RECOMMEND_ESCALATE
        checklist.save(update_fields=["recommendation", "updated_at"])
        lead.status = MerchantLead.STATUS_AWAITING_HQ
        lead.save(update_fields=["status", "updated_at"])
        messages.info(request, "Escalated to HQ.")

    _audit(request.user, lead, "recommendation", {"choice": action, "reason": reason[:200] if reason else ""})
    return redirect("ma_lead_detail", lead_id=lead_id)


@merchant_admin_required
def ma_lead_note(request, lead_id):
    lead = get_object_or_404(MerchantLead, id=lead_id)
    if request.method != "POST":
        return redirect("ma_lead_detail", lead_id=lead_id)

    note = request.POST.get("note", "").strip()
    if not note:
        messages.error(request, "Note cannot be empty.")
        return redirect("ma_lead_detail", lead_id=lead_id)

    kyc, _ = MerchantKYC.objects.get_or_create(lead=lead)
    kyc.notes = (kyc.notes + "\n\n[" + timezone.now().strftime("%d %b %Y %H:%M") + "] " + note).strip()
    kyc.save(update_fields=["notes", "updated_at"])
    _audit(request.user, lead, "internal_note", {"note": note[:200]})
    messages.success(request, "Internal note added.")
    return redirect("ma_lead_detail", lead_id=lead_id)


@merchant_admin_required
def ma_lead_ticket(request, lead_id):
    from support.models import SupportTicket

    lead = get_object_or_404(MerchantLead, id=lead_id)
    if request.method != "POST":
        return redirect("ma_lead_detail", lead_id=lead_id)

    title = request.POST.get("title", "").strip() or f"Merchant lead: {lead.business_name}"
    description = request.POST.get("description", "").strip() or (
        f"Support ticket for merchant lead #{lead.pk} — {lead.business_name}\n"
        f"Owner: {lead.owner_full_name}\nPhone: {lead.phone}\nDistrict: {lead.district}"
    )

    ticket = SupportTicket.objects.create(
        title=title,
        description=description,
        category=SupportTicket.CAT_MERCHANT_ONBOARD,
        priority=SupportTicket.PRI_MEDIUM,
        created_by=request.user,
        related_merchant=lead,
        status=SupportTicket.STATUS_OPEN,
    )
    _audit(request.user, lead, "ticket_created", {"ticket_id": ticket.pk})
    messages.success(request, f"Ticket {ticket.ticket_number} created for this lead.")
    return redirect("ticket_detail", ticket_id=ticket.pk)
