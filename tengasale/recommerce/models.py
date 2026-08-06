from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class RecommerceLead(TimestampedModel):
    STATUS_NEW = "lead_received"
    STATUS_CHOICES = [(STATUS_NEW, "Lead received"), ("intake_started", "Intake started"), ("closed", "Closed")]
    source = models.CharField(max_length=80, blank=True)
    customer = models.ForeignKey("financing.Customer", null=True, blank=True, on_delete=models.PROTECT, related_name="recommerce_leads")
    category = models.CharField(max_length=40, default="trade_in_upgrade")
    location = models.CharField(max_length=160, blank=True)
    status = models.CharField(max_length=40, choices=STATUS_CHOICES, default=STATUS_NEW)
    assigned_role = models.CharField(max_length=80, blank=True)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="recommerce_leads")


class DeviceIntake(TimestampedModel):
    STATUSES = (
        "lead_received", "intake_started", "details_captured", "evidence_pending", "ready_for_assessment",
        "assessment_in_progress", "valuation_proposed", "approval_pending", "offer_ready",
        "customer_decision_pending", "accepted", "declined", "expired", "device_received",
        "settlement_pending", "settlement_complete", "refurbishment_required", "refurbishment_in_progress",
        "qa_pending", "qa_approved", "qa_rejected", "inventory_ready", "reserved", "sold", "swapped",
        "applied_to_deposit", "refinanced", "cancelled", "disputed", "fraud_review", "closed",
    )
    STATUS_CHOICES = [(value, value.replace("_", " ").title()) for value in STATUSES]
    customer = models.ForeignKey("financing.Customer", on_delete=models.PROTECT, related_name="device_intakes")
    lead = models.OneToOneField(RecommerceLead, null=True, blank=True, on_delete=models.SET_NULL, related_name="intake")
    existing_device = models.ForeignKey("financing.Device", null=True, blank=True, on_delete=models.PROTECT, related_name="recommerce_intakes")
    imei = models.CharField(max_length=40, db_index=True)
    serial_number = models.CharField(max_length=80, blank=True)
    brand = models.CharField(max_length=80)
    model = models.CharField(max_length=120)
    intake_location = models.CharField(max_length=160)
    declared_ownership = models.BooleanField(default=False)
    custody_status = models.CharField(max_length=40, default="not_received")
    status = models.CharField(max_length=40, choices=STATUS_CHOICES, default="intake_started", db_index=True)
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="opened_recommerce_intakes")
    assigned_role = models.CharField(max_length=80, blank=True)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_recommerce_intakes")
    due_at = models.DateTimeField(null=True, blank=True)
    frozen = models.BooleanField(default=False)

    class Meta:
        permissions = [
            ("approve_recommerce_valuation", "Can approve recommerce valuation"),
            ("settle_recommerce_case", "Can settle recommerce case"),
            ("override_recommerce_workflow", "Can override recommerce workflow"),
            ("view_recommerce_hq", "Can view HQ recommerce oversight"),
        ]


class OwnershipEvidence(TimestampedModel):
    intake = models.ForeignKey(DeviceIntake, on_delete=models.PROTECT, related_name="ownership_evidence")
    evidence_type = models.CharField(max_length=60)
    file = models.FileField(upload_to="private/recommerce/ownership/")
    verification_result = models.CharField(max_length=30, default="pending")
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    notes = models.TextField(blank=True)

    def delete(self, *args, **kwargs):
        raise ValidationError("Ownership evidence is retained as immutable history.")


class DeviceInspection(TimestampedModel):
    intake = models.ForeignKey(DeviceIntake, on_delete=models.PROTECT, related_name="inspections")
    diagnostics = models.JSONField(default=dict, blank=True)
    cosmetic_condition = models.TextField(blank=True)
    functional_condition = models.TextField(blank=True)
    battery_status = models.CharField(max_length=80, blank=True)
    charging_status = models.CharField(max_length=80, blank=True)
    network_status = models.CharField(max_length=80, blank=True)
    camera_status = models.CharField(max_length=80, blank=True)
    screen_status = models.CharField(max_length=80, blank=True)
    proposed_grade = models.CharField(max_length=20)
    assessor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="recommerce_inspections")
    evidence = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=30, default="draft")


class ValuationOffer(TimestampedModel):
    ROUTES = [("upgrade_credit", "Upgrade credit"), ("direct_swap", "Direct swap"), ("cash_quote", "Cash quote")]
    inspection = models.ForeignKey(DeviceInspection, on_delete=models.PROTECT, related_name="offers")
    proposed_value = models.DecimalField(max_digits=14, decimal_places=2)
    approved_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    route = models.CharField(max_length=30, choices=ROUTES)
    valid_until = models.DateTimeField()
    approval_status = models.CharField(max_length=30, default="pending")
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="approved_recommerce_offers")
    customer_decision = models.CharField(max_length=30, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)


class TradeInAgreement(TimestampedModel):
    customer = models.ForeignKey("financing.Customer", on_delete=models.PROTECT, related_name="trade_in_agreements")
    device = models.ForeignKey("financing.Device", null=True, blank=True, on_delete=models.PROTECT, related_name="trade_in_agreements")
    accepted_offer = models.OneToOneField(ValuationOffer, on_delete=models.PROTECT, related_name="agreement")
    terms_snapshot = models.TextField()
    accepted_at = models.DateTimeField()
    ownership_transfer_acknowledged = models.BooleanField(default=False)
    settlement_status = models.CharField(max_length=30, default="pending")

    def delete(self, *args, **kwargs):
        raise ValidationError("Accepted agreements cannot be deleted.")


class RecommerceSettlement(TimestampedModel):
    agreement = models.OneToOneField(TradeInAgreement, on_delete=models.PROTECT, related_name="settlement")
    route = models.CharField(max_length=30)
    cash_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    deposit_credit_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    swap_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    payment_transaction = models.ForeignKey("portal.PaymentTransaction", null=True, blank=True, on_delete=models.PROTECT)
    wallet_transaction = models.ForeignKey("earnings.WalletTransaction", null=True, blank=True, on_delete=models.PROTECT)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    status = models.CharField(max_length=30, default="pending")


class RefurbishmentWorkOrder(TimestampedModel):
    intake = models.ForeignKey(DeviceIntake, on_delete=models.PROTECT, related_name="work_orders")
    diagnosis = models.TextField()
    assigned_technician = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="recommerce_work_orders")
    parts = models.JSONField(default=list, blank=True)
    labour = models.JSONField(default=list, blank=True)
    estimated_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    actual_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(max_length=30, default="assigned")
    completion_evidence = models.JSONField(default=list, blank=True)


class RecommerceQA(TimestampedModel):
    work_order = models.ForeignKey(RefurbishmentWorkOrder, on_delete=models.PROTECT, related_name="qa_reviews")
    qa_officer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="recommerce_qa_reviews")
    checklist = models.JSONField(default=dict)
    result = models.CharField(max_length=30, default="pending")
    rework_reason = models.TextField(blank=True)
    approved_grade = models.CharField(max_length=20, blank=True)
    data_wipe_confirmed = models.BooleanField(default=False)


class RecommerceInventoryItem(TimestampedModel):
    intake = models.OneToOneField(DeviceIntake, on_delete=models.PROTECT, related_name="inventory_item")
    device = models.ForeignKey("financing.Device", null=True, blank=True, on_delete=models.PROTECT, related_name="recommerce_inventory")
    acquisition_value = models.DecimalField(max_digits=14, decimal_places=2)
    repair_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    grade = models.CharField(max_length=20)
    location = models.CharField(max_length=160)
    availability = models.CharField(max_length=30, default="held")
    approved_public_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=30, default="pending_qa")
    reserved_for = models.ForeignKey("financing.Customer", null=True, blank=True, on_delete=models.PROTECT)


class RecommerceDisposition(TimestampedModel):
    inventory_item = models.ForeignKey(RecommerceInventoryItem, on_delete=models.PROTECT, related_name="dispositions")
    route = models.CharField(max_length=30)
    customer = models.ForeignKey("financing.Customer", null=True, blank=True, on_delete=models.PROTECT)
    contract = models.ForeignKey("financing.FinancingContract", null=True, blank=True, on_delete=models.PROTECT)
    application = models.ForeignKey("applications.FinancingApplication", null=True, blank=True, on_delete=models.PROTECT)
    final_value = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=30, default="pending")


class RecommerceTask(TimestampedModel):
    intake = models.ForeignKey(DeviceIntake, on_delete=models.CASCADE, related_name="tasks")
    task_type = models.CharField(max_length=60)
    assigned_role = models.CharField(max_length=80)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="recommerce_tasks")
    priority = models.CharField(max_length=20, default="normal")
    due_at = models.DateTimeField(null=True, blank=True)
    sla_minutes = models.PositiveIntegerField(default=1440)
    status = models.CharField(max_length=20, default="open")
    completion_evidence = models.JSONField(default=dict, blank=True)

    @property
    def is_overdue(self):
        return self.status == "open" and self.due_at and self.due_at < timezone.now()


class RecommerceEvent(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    intake = models.ForeignKey(DeviceIntake, on_delete=models.PROTECT, related_name="events")
    event_type = models.CharField(max_length=80)
    previous_values = models.JSONField(default=dict, blank=True)
    new_values = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Recommerce events are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Recommerce events are immutable.")


class RecommerceCommissionPlan(TimestampedModel):
    role = models.CharField(max_length=80)
    event_type = models.CharField(max_length=80)
    fixed_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    percentage = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    maximum_cap = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    effective_from = models.DateTimeField()
    effective_to = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    quality_threshold = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    requires_sla = models.BooleanField(default=False)
    cancellation_rule = models.JSONField(default=dict, blank=True)
    fraud_rule = models.JSONField(default=dict, blank=True)
    clawback_rule = models.JSONField(default=dict, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    approved_at = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=False)

    class Meta:
        unique_together = [("role", "event_type", "version")]


class RecommerceCommissionEvent(TimestampedModel):
    staff_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="recommerce_commission_events")
    plan = models.ForeignKey(RecommerceCommissionPlan, on_delete=models.PROTECT, related_name="events")
    intake = models.ForeignKey(DeviceIntake, on_delete=models.PROTECT, related_name="commission_events")
    task = models.ForeignKey(RecommerceTask, null=True, blank=True, on_delete=models.PROTECT)
    qualifying_event = models.CharField(max_length=80)
    gross_amount = models.DecimalField(max_digits=14, decimal_places=2)
    adjustment = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    final_amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=20, default="pending")
    plan_snapshot = models.JSONField(default=dict)
    reason = models.TextField(blank=True)


class RecommercePayoutRequest(TimestampedModel):
    staff_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="recommerce_payout_requests")
    wallet = models.ForeignKey("earnings.Wallet", null=True, blank=True, on_delete=models.PROTECT)
    requested_amount = models.DecimalField(max_digits=14, decimal_places=2)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="approved_recommerce_payouts")
    status = models.CharField(max_length=20, default="pending")
    reason = models.TextField(blank=True)
