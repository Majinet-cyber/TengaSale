from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from portal.models import PaymentContract, PaymentTransaction, RecoveryCost


def _money(value):
    return value or Decimal("0")


def _percent(numerator, denominator):
    numerator = _money(numerator)
    denominator = _money(denominator)
    if denominator <= 0:
        return Decimal("0")
    return round((numerator / denominator) * Decimal("100"), 2)


def days_in_arrears(contract):
    if getattr(contract, "analytics_days_late", 0):
        return int(contract.analytics_days_late)
    due_date = contract.due_date
    if due_date and due_date < timezone.localdate() and contract.remaining_amount > 0:
        return (timezone.localdate() - due_date).days
    return 0


def calculate_par_band(contract):
    days = days_in_arrears(contract)
    if days >= 30:
        return "PAR 30+"
    if days >= 7:
        return "PAR 7+"
    if days >= 1:
        return "PAR 1+"
    return "Current"


def calculate_contract_risk(contract):
    days = days_in_arrears(contract)
    risk = "Low"
    if days >= 30:
        risk = "High"
    elif days >= 7:
        risk = "Medium"
    elif days >= 1:
        risk = "Watch"

    app = getattr(contract, "source_application", None)
    try:
        questionnaire = app.call_questionnaire if app else None
        if questionnaire and questionnaire.recommendation == "reject":
            risk = "High"
        elif questionnaire and questionnaire.income_confidence_score == 1 and risk == "Low":
            risk = "Watch"
    except Exception:
        pass

    if app:
        guarantor_ok = bool(getattr(app, "next_of_kin_1_name", "") and getattr(app, "next_of_kin_1_phone", ""))
        if not guarantor_ok and risk == "Low":
            risk = "Watch"
    return risk


def calculate_default_rate(contracts=None):
    contracts = contracts or PaymentContract.objects.all()
    total = contracts.count()
    if not total:
        return Decimal("0")
    defaulted = contracts.filter(
        status__in=[
            PaymentContract.STATUS_REPOSSESSION_PENDING,
            PaymentContract.STATUS_REPOSSESSED,
            PaymentContract.STATUS_RESOLD,
            PaymentContract.STATUS_WRITTEN_OFF,
        ]
    ).count()
    return round(Decimal(defaulted) / Decimal(total) * Decimal("100"), 2)


def calculate_recovery_rate():
    due = PaymentContract.objects.filter(
        status__in=[
            PaymentContract.STATUS_REPOSSESSION_PENDING,
            PaymentContract.STATUS_REPOSSESSED,
            PaymentContract.STATUS_RESOLD,
            PaymentContract.STATUS_WRITTEN_OFF,
        ]
    ).aggregate(t=Sum("total_amount"))["t"] or Decimal("0")
    recovered = RecoveryCost.objects.filter(approved_at__isnull=False).aggregate(t=Sum("amount"))["t"] or Decimal("0")
    return _percent(recovered, due)


def calculate_average_deposit(contracts=None):
    contracts = contracts or PaymentContract.objects.all()
    totals = contracts.aggregate(
        deposit=Sum("deposit_required"),
        cash=Sum("cash_price"),
    )
    return _percent(totals["deposit"], totals["cash"])


def calculate_average_ticket_size(contracts=None):
    contracts = contracts or PaymentContract.objects.all()
    total = contracts.aggregate(t=Sum("total_amount"))["t"] or Decimal("0")
    count = contracts.count()
    return round(total / count, 2) if count else Decimal("0")


def calculate_portfolio_kpis(contracts=None):
    contracts = contracts or PaymentContract.objects.all()
    portfolio_value = contracts.aggregate(t=Sum("total_amount"))["t"] or Decimal("0")
    deposits = contracts.aggregate(t=Sum("deposit_paid"))["t"] or Decimal("0")
    paid = contracts.aggregate(t=Sum("amount_paid"))["t"] or Decimal("0")
    outstanding = max(portfolio_value - paid, Decimal("0"))
    write_off_balance = contracts.filter(status=PaymentContract.STATUS_WRITTEN_OFF).aggregate(t=Sum("total_amount"))["t"] or Decimal("0")
    recovered_write_offs = RecoveryCost.objects.filter(
        contract__status=PaymentContract.STATUS_WRITTEN_OFF,
        approved_at__isnull=False,
    ).aggregate(t=Sum("amount"))["t"] or Decimal("0")
    return {
        "active_contracts": contracts.filter(status=PaymentContract.STATUS_ACTIVE).count(),
        "portfolio_value": portfolio_value,
        "total_deposits": deposits,
        "outstanding_balance": outstanding,
        "total_paid": paid,
        "average_deposit": calculate_average_deposit(contracts),
        "average_ticket_size": calculate_average_ticket_size(contracts),
        "default_rate": calculate_default_rate(contracts),
        "recovery_rate": calculate_recovery_rate(),
        "par_1": sum(1 for c in contracts if days_in_arrears(c) >= 1),
        "par_7": sum(1 for c in contracts if days_in_arrears(c) >= 7),
        "par_30": sum(1 for c in contracts if days_in_arrears(c) >= 30),
        "write_off_balance": write_off_balance,
        "recovered_from_write_offs": recovered_write_offs,
    }


def portfolio_chart_data(contracts=None):
    contracts = contracts or PaymentContract.objects.all()
    contract_ids = list(contracts.values_list("id", flat=True))
    status_counts = {}
    for value, label in PaymentContract.STATUS_CHOICES:
        count = contracts.filter(status=value).count()
        if count:
            status_counts[label] = count

    today = timezone.localdate()
    month_labels = []
    new_contracts = []
    payments = []
    for offset in range(5, -1, -1):
        first = (today.replace(day=1) - timedelta(days=offset * 31)).replace(day=1)
        month_labels.append(first.strftime("%b %Y"))
        new_contracts.append(contracts.filter(created_at__year=first.year, created_at__month=first.month).count())
        payments.append(float(PaymentTransaction.objects.filter(
            payment_contract_id__in=contract_ids,
            status=PaymentTransaction.STATUS_PAID,
            paid_at__year=first.year,
            paid_at__month=first.month,
        ).aggregate(t=Sum("amount"))["t"] or Decimal("0")))

    return {
        "statusLabels": list(status_counts.keys()) or ["No contracts"],
        "statusValues": list(status_counts.values()) or [0],
        "monthLabels": month_labels,
        "newContracts": new_contracts,
        "payments": payments,
    }


def portfolio_rows(contracts=None):
    contracts = (contracts or PaymentContract.objects.all()).select_related("source_application", "source_application__created_by")
    rows = []
    for contract in contracts:
        app = contract.source_application
        merchant_name = ""
        underwriter = ""
        if app:
            merchant_name = app.created_by.get_full_name() or app.created_by.username
            if app.claimed_by:
                underwriter = app.claimed_by.get_full_name() or app.claimed_by.username
        rows.append({
            "contract": contract,
            "customer": contract.customer_name,
            "merchant": merchant_name,
            "underwriter": underwriter,
            "device": contract.device_model,
            "loan": contract.total_amount,
            "deposit": contract.deposit_paid,
            "paid": contract.amount_paid,
            "balance": contract.remaining_amount,
            "status": contract.get_status_display(),
            "risk": calculate_contract_risk(contract),
            "par": calculate_par_band(contract),
            "days_arrears": days_in_arrears(contract),
            "recovery": contract.get_status_display() if "reposs" in contract.status or "resale" in contract.status else "Normal",
            "created": contract.created_at,
            "imei": contract.imei_number,
            "phone": contract.customer_phone,
            "guarantor_1_name": getattr(app, "next_of_kin_1_name", "") if app else "",
            "guarantor_1_phone": getattr(app, "next_of_kin_1_phone", "") if app else "",
            "guarantor_2_name": getattr(app, "next_of_kin_2_name", "") if app else "",
            "guarantor_2_phone": getattr(app, "next_of_kin_2_phone", "") if app else "",
            "guarantor_3_name": getattr(app, "proof_contact_name", "") if app else "",
            "guarantor_3_phone": getattr(app, "proof_contact_phone", "") if app else "",
        })
    return rows
