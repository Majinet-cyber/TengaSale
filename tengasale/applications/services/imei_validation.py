"""IMEI validation helpers for capture and contract flows."""

from django.db.models import Q

from contracts.models import Contract


ACTIVE_CONTRACT_STATUSES = [
    Contract.STATUS_IMEI_ENTERED,
    Contract.STATUS_CONTRACT_CREATED,
    Contract.STATUS_WARRANTY_CHECKED,
    Contract.STATUS_LOCKING,
    Contract.STATUS_LOCKED,
    Contract.STATUS_DEPOSIT_PENDING,
]

IMEI_DUPLICATE_MESSAGE = "This IMEI is already attached to another active contract."


def normalize_imei(value):
    """Strip and keep digits only."""
    return "".join(ch for ch in str(value or "").strip() if ch.isdigit())


def is_valid_imei(value):
    imei = normalize_imei(value)
    return len(imei) == 15 and imei.isdigit()


def get_active_imei_conflict(imei, *, exclude_contract_id=None, exclude_application_id=None):
    """
    Return an active Contract using this IMEI, or None if no conflict.
    Completed contracts are ignored (replacement allowed per business rules).
    """
    imei = normalize_imei(imei)
    if not is_valid_imei(imei):
        return None

    qs = Contract.objects.filter(
        imei_number=imei,
        status__in=ACTIVE_CONTRACT_STATUSES,
    )
    if exclude_contract_id:
        qs = qs.exclude(pk=exclude_contract_id)
    if exclude_application_id:
        qs = qs.exclude(application_id=exclude_application_id)
    return qs.select_related("application").first()
