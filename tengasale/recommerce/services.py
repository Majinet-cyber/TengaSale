from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.utils import timezone

from .models import DeviceIntake, RecommerceCommissionEvent, RecommerceCommissionPlan, RecommerceEvent
from .permissions import is_recommerce_hq, recommerce_role


TRANSITIONS = {
    "lead_received": {"intake_started", "cancelled"}, "intake_started": {"details_captured", "cancelled"},
    "details_captured": {"evidence_pending", "ready_for_assessment"}, "evidence_pending": {"ready_for_assessment", "fraud_review"},
    "ready_for_assessment": {"assessment_in_progress", "cancelled"}, "assessment_in_progress": {"valuation_proposed", "evidence_pending"},
    "valuation_proposed": {"approval_pending"}, "approval_pending": {"offer_ready", "assessment_in_progress", "fraud_review"},
    "offer_ready": {"customer_decision_pending", "expired"}, "customer_decision_pending": {"accepted", "declined", "expired", "disputed"},
    "accepted": {"device_received", "cancelled"}, "device_received": {"settlement_pending", "fraud_review"},
    "settlement_pending": {"settlement_complete", "disputed"}, "settlement_complete": {"refurbishment_required", "qa_pending", "closed"},
    "refurbishment_required": {"refurbishment_in_progress"}, "refurbishment_in_progress": {"qa_pending"},
    "qa_pending": {"qa_approved", "qa_rejected"}, "qa_rejected": {"refurbishment_in_progress", "cancelled"},
    "qa_approved": {"inventory_ready"}, "inventory_ready": {"reserved", "sold", "swapped", "refinanced"},
    "reserved": {"inventory_ready", "sold", "swapped", "applied_to_deposit", "refinanced"},
    "sold": {"closed"}, "swapped": {"closed"}, "applied_to_deposit": {"closed"}, "refinanced": {"closed"},
    "declined": {"closed"}, "expired": {"closed"}, "cancelled": {"closed"}, "disputed": {"customer_decision_pending", "cancelled", "fraud_review"},
    "fraud_review": {"evidence_pending", "cancelled", "closed"}, "closed": set(),
}

ROLE_TARGETS = {
    "recommerce_intake": {"details_captured", "evidence_pending", "ready_for_assessment"},
    "recommerce_assessor": {"assessment_in_progress", "valuation_proposed"},
    "recommerce_technician": {"refurbishment_in_progress", "qa_pending"},
    "recommerce_qa": {"qa_approved", "qa_rejected", "inventory_ready"},
    "recommerce_inventory": {"reserved", "sold", "swapped", "applied_to_deposit", "refinanced", "closed"},
    "recommerce_supervisor": set(DeviceIntake.STATUSES), "recommerce_hq": set(DeviceIntake.STATUSES),
}


@transaction.atomic
def transition_intake(intake, next_status, actor, reason="", override=False):
    intake = DeviceIntake.objects.select_for_update().get(pk=intake.pk)
    previous = intake.status
    if intake.frozen and next_status != "fraud_review" and not is_recommerce_hq(actor):
        raise PermissionDenied("This case is frozen for review.")
    if override:
        if not is_recommerce_hq(actor) and recommerce_role(actor) != "recommerce_supervisor":
            raise PermissionDenied("Override permission required.")
        if not reason.strip():
            raise ValidationError("An override reason is required.")
    elif next_status not in TRANSITIONS.get(previous, set()):
        raise ValidationError(f"Invalid transition from {previous} to {next_status}.")
    role = recommerce_role(actor)
    if not is_recommerce_hq(actor) and next_status not in ROLE_TARGETS.get(role, set()):
        raise PermissionDenied("Your role cannot perform this transition.")
    if next_status == "offer_ready" and intake.inspections.filter(assessor=actor).exists() and not is_recommerce_hq(actor):
        raise PermissionDenied("An assessor cannot approve their own valuation.")
    if next_status in {"settlement_pending", "settlement_complete"} and intake.opened_by_id == actor.id and not is_recommerce_hq(actor):
        raise PermissionDenied("An intake officer cannot settle their own case.")
    if next_status in {"qa_approved", "qa_rejected"} and intake.work_orders.filter(assigned_technician=actor).exists():
        raise PermissionDenied("A technician cannot QA their own work.")
    intake.status = next_status
    intake.save(update_fields=["status", "updated_at"])
    RecommerceEvent.objects.create(actor=actor, intake=intake, event_type="status_transition", previous_values={"status": previous}, new_values={"status": next_status}, reason=reason)
    return intake


@transaction.atomic
def reassign_intake(intake, actor, user, role, reason):
    if recommerce_role(actor) not in {"recommerce_supervisor", "recommerce_hq"} and not is_recommerce_hq(actor):
        raise PermissionDenied("Supervisor access required.")
    if not reason.strip():
        raise ValidationError("A reassignment reason is required.")
    previous = {"assigned_to": intake.assigned_to_id, "assigned_role": intake.assigned_role}
    intake.assigned_to, intake.assigned_role = user, role
    intake.save(update_fields=["assigned_to", "assigned_role", "updated_at"])
    RecommerceEvent.objects.create(actor=actor, intake=intake, event_type="reassigned", previous_values=previous, new_values={"assigned_to": user.pk if user else None, "assigned_role": role}, reason=reason)
    return intake


def create_commission_event(*, staff_user, intake, event_type, base_amount=Decimal("0"), task=None):
    now = timezone.now()
    role = recommerce_role(staff_user)
    plan = RecommerceCommissionPlan.objects.filter(role=role, event_type=event_type, active=True, approved_at__isnull=False, effective_from__lte=now).filter(models.Q(effective_to__isnull=True) | models.Q(effective_to__gt=now)).order_by("-version").first()
    if not plan:
        return None
    gross = plan.fixed_amount or (Decimal(base_amount) * (plan.percentage or 0) / 100)
    if plan.maximum_cap is not None:
        gross = min(gross, plan.maximum_cap)
    return RecommerceCommissionEvent.objects.create(staff_user=staff_user, plan=plan, intake=intake, task=task, qualifying_event=event_type, gross_amount=gross, final_amount=gross, plan_snapshot={"version": plan.version, "fixed_amount": str(plan.fixed_amount or ""), "percentage": str(plan.percentage or ""), "maximum_cap": str(plan.maximum_cap or "")})
