from datetime import timedelta

from django.utils import timezone

from .models import SMSLog
from .services import send_contract_reminder


def send_due_sms_reminders():
    from portal.models import PaymentContract

    today = timezone.localdate()
    due_soon = today + timedelta(days=2)
    counts = {"due_reminder": 0, "payment_due_today": 0, "arrears_reminder": 0, "skipped": 0}

    active = PaymentContract.objects.filter(status=PaymentContract.STATUS_ACTIVE)
    for contract in active.filter(due_date=due_soon):
        if contract.remaining_amount <= 0:
            continue
        if send_contract_reminder(contract, SMSLog.PURPOSE_DUE_REMINDER):
            counts["due_reminder"] += 1
        else:
            counts["skipped"] += 1

    for contract in active.filter(due_date=today):
        if contract.remaining_amount <= 0:
            continue
        if send_contract_reminder(contract, SMSLog.PURPOSE_PAYMENT_DUE_TODAY):
            counts["payment_due_today"] += 1
        else:
            counts["skipped"] += 1

    arrears = PaymentContract.objects.filter(
        status__in=[
            PaymentContract.STATUS_ACTIVE,
            PaymentContract.STATUS_OVERDUE,
            PaymentContract.STATUS_LOCKED,
        ],
        due_date__lt=today,
    )
    for contract in arrears:
        if contract.remaining_amount <= 0:
            continue
        if send_contract_reminder(contract, SMSLog.PURPOSE_ARREARS_REMINDER):
            counts["arrears_reminder"] += 1
        else:
            counts["skipped"] += 1
    return counts
