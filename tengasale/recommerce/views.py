from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import DeviceIntakeForm, TransitionForm
from .models import (
    DeviceIntake, RecommerceCommissionEvent, RecommerceEvent,
    RecommerceInventoryItem, RecommerceLead, RecommerceTask, ValuationOffer,
)
from .permissions import can_access_recommerce, is_recommerce_hq, recommerce_role
from .services import TRANSITIONS, transition_intake


def _require_access(user):
    if not can_access_recommerce(user):
        raise PermissionDenied


@login_required
def home(request):
    _require_access(request.user)
    role = recommerce_role(request.user) or "recommerce_hq"
    tasks = RecommerceTask.objects.filter(Q(assigned_to=request.user) | Q(assigned_role=role))
    now = timezone.now()
    commissions = RecommerceCommissionEvent.objects.filter(staff_user=request.user)
    context = {
        "role": role.replace("recommerce_", "").replace("_", " ").title(),
        "tasks": tasks.filter(status="open").select_related("intake")[:12],
        "overdue_count": tasks.filter(status="open", due_at__lt=now).count(),
        "completed_today": tasks.filter(status="completed", updated_at__date=timezone.localdate()).count(),
        "pending_earnings": commissions.filter(status="pending").aggregate(total=Sum("final_amount"))["total"] or Decimal("0"),
        "queue": DeviceIntake.objects.filter(Q(assigned_to=request.user) | Q(assigned_role=role)).select_related("customer")[:20],
    }
    return render(request, "recommerce/home.html", context)


@login_required
def intake_create(request):
    _require_access(request.user)
    if recommerce_role(request.user) not in {"recommerce_intake", "recommerce_supervisor", "recommerce_hq"} and not is_recommerce_hq(request.user):
        raise PermissionDenied
    form = DeviceIntakeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        intake = form.save(commit=False)
        intake.opened_by = request.user
        intake.assigned_to = request.user
        intake.assigned_role = "recommerce_intake"
        intake.save()
        RecommerceEvent.objects.create(actor=request.user, intake=intake, event_type="intake_created", new_values={"status": intake.status})
        messages.success(request, "Device intake created.")
        return redirect("recommerce:case", pk=intake.pk)
    return render(request, "recommerce/intake_form.html", {"form": form})


@login_required
def case_detail(request, pk):
    _require_access(request.user)
    intake = get_object_or_404(DeviceIntake.objects.select_related("customer", "assigned_to", "opened_by"), pk=pk)
    if not is_recommerce_hq(request.user) and intake.assigned_to_id not in (None, request.user.id) and recommerce_role(request.user) not in {"recommerce_supervisor"}:
        raise PermissionDenied
    form = TransitionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            transition_intake(intake, form.cleaned_data["next_status"], request.user, form.cleaned_data["reason"], form.cleaned_data["override"])
        except (ValidationError, PermissionDenied) as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Case updated.")
            return redirect("recommerce:case", pk=pk)
    return render(request, "recommerce/case.html", {"intake": intake, "form": form, "allowed": TRANSITIONS.get(intake.status, set())})


@login_required
def earnings(request):
    _require_access(request.user)
    events = RecommerceCommissionEvent.objects.filter(staff_user=request.user).select_related("plan", "intake")
    totals = {status: events.filter(status=status).aggregate(total=Sum("final_amount"))["total"] or 0 for status in ("pending", "approved", "paid", "clawed_back")}
    return render(request, "recommerce/earnings.html", {"events": events[:100], "totals": totals})


@login_required
def hq_overview(request):
    if not is_recommerce_hq(request.user):
        raise PermissionDenied
    counts = dict(DeviceIntake.objects.values_list("status").annotate(total=Count("id")))
    offers = ValuationOffer.objects.aggregate(proposed=Sum("proposed_value"), approved=Sum("approved_value"))
    inventory = RecommerceInventoryItem.objects.aggregate(acquisition=Sum("acquisition_value"), repair=Sum("repair_cost"), public_value=Sum("approved_public_price"))
    commissions = RecommerceCommissionEvent.objects.exclude(status__in=["paid", "clawed_back"]).aggregate(total=Sum("final_amount"))["total"] or 0
    return render(request, "recommerce/hq_overview.html", {"counts": counts, "offers": offers, "inventory": inventory, "commission_liability": commissions, "events": RecommerceEvent.objects.select_related("actor", "intake")[:30], "sla_breaches": RecommerceTask.objects.filter(status="open", due_at__lt=timezone.now()).count(), "lead_count": RecommerceLead.objects.count()})
