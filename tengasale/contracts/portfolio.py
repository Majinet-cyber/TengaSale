"""Authoritative merchant portfolio querysets.

Applications describe workflow. Portfolio records are issued contracts: the
deposit is confirmed and the contract is complete/active. Subsets intentionally
overlap (for example, a financed contract may also be locked and overdue).
"""
from django.db.models import Q

from device_lock.models import DeviceLockProfile
from portal.models import PaymentContract

from .models import Contract


def merchant_financed_contracts(merchant):
    return Contract.objects.filter(merchant=merchant, deposit_paid=True).filter(
        Q(status=Contract.STATUS_COMPLETE) | Q(active_at__isnull=False)
    ).distinct()


def merchant_active_contracts(merchant):
    return merchant_financed_contracts(merchant).filter(
        application__payment_contract__status=PaymentContract.STATUS_ACTIVE
    )


def merchant_locked_contracts(merchant):
    return merchant_financed_contracts(merchant).filter(
        Q(device_lock_profile__lock_status=DeviceLockProfile.STATUS_LOCKED)
        | Q(application__payment_contract__device_lock_status=PaymentContract.LOCK_STATUS_LOCKED)
    ).distinct()


def merchant_overdue_contracts(merchant):
    return merchant_financed_contracts(merchant).filter(
        application__payment_contract__status=PaymentContract.STATUS_OVERDUE
    )


PORTFOLIO_FILTERS = {
    "financed": merchant_financed_contracts,
    "active": merchant_active_contracts,
    "locked": merchant_locked_contracts,
    "overdue": merchant_overdue_contracts,
}


def merchant_portfolio_contracts(merchant, portfolio_filter):
    return PORTFOLIO_FILTERS[portfolio_filter](merchant)
