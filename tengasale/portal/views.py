"""
Customer Payment Portal views — /pay/

Handles contract search, payment initiation, history, support,
and webhook placeholder endpoints for payment providers.

PayG routes (/pay/payg/EXXXXXXX/) are the primary customer-facing URLs.
Contract-number routes (/pay/contract/TS-MW-XXXXXXXX/) remain for backward compat.
"""

import json
import logging
import re
import secrets
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.models import AuditLog
from payments.airtel_client import AirtelConfigurationError
from payments.airtel_services import AirtelCollectionService, mask_msisdn
from payments.mobile_network import PROVIDER_AIRTEL, PROVIDER_TNM, detect_mobile_network
from payments.models import AirtelTransaction
from .models import PaymentContract, PaymentTransaction
from .services import (
    calculate_early_settlement_options,
    calculate_remaining_amount,
    calculate_payment_behaviour,
    calculate_health_score,
    calculate_customer_insights,
    build_device_timeline,
    compute_lock_state,
    get_deposit_summary,
    get_device_info,
    get_lock_provider_label,
    resolve_payable_contract,
    search_payment_contract,
)

_PAYG_RE = re.compile(r"^E[A-Z2-9]{7}$")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------

def _portal_audit(action, obj_type="", obj_id="", detail=None, request=None):
    """Write an AuditLog entry for portal actions (no user — public portal)."""
    ip = None
    if request:
        x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        ip = x_forwarded.split(",")[0].strip() if x_forwarded else request.META.get("REMOTE_ADDR")
    AuditLog.objects.create(
        user=None,
        action=action,
        object_type=obj_type,
        object_id=str(obj_id),
        detail=detail or {},
        ip_address=ip,
    )


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def _safe_resolution_message(result):
    if result.status == "ambiguous":
        return "We found more than one matching contract. Please contact TengaSale Support to verify the payment reference."
    if result.status == "inactive":
        return "This contract cannot currently accept payments. Please contact TengaSale Support."
    if result.status == "fully_paid":
        return "Contract fully paid. No payment is currently required."
    return "No contract found matching that number, ID, or phone."

def portal_search(request):
    """Landing / search page."""
    error = request.GET.get("error")
    candidates = [
        request.GET.get("contract", ""),
        request.GET.get("application", ""),
        request.GET.get("payg", ""),
        request.GET.get("q", ""),
        request.GET.get("ref", ""),
    ]
    query = next((candidate.strip() for candidate in candidates if candidate and candidate.strip()), "")
    payment_type = request.GET.get("payment_type", "").strip().lower()
    direct_lookup = bool(request.GET.get("contract") or request.GET.get("application") or request.GET.get("payg"))

    if query and (direct_lookup or payment_type == "deposit"):
        contract = None
        for candidate in candidates:
            candidate = (candidate or "").strip()
            if not candidate:
                continue
            result = resolve_payable_contract(candidate, country=request.GET.get("country", "MW"))
            contract = result.contract
            if contract:
                break
        if contract:
            params = {"payment_type": payment_type} if payment_type else {}
            suffix = f"?{urlencode(params)}" if params else ""
            return redirect(f"/pay/contract/{contract.contract_number}/{suffix}")
        error = _safe_resolution_message(result)

    return render(request, "portal/search.html", {
        "error": error,
        "query": query,
        "searched": bool(error and query),
    })


def portal_search_post(request):
    """Process search and redirect to contract page."""
    q = (
        request.GET.get("q", "")
        or request.GET.get("contract", "")
        or request.GET.get("application", "")
        or request.GET.get("payg", "")
        or request.GET.get("ref", "")
    ).strip()
    if not q:
        return redirect("portal_search")

    result = resolve_payable_contract(q, country=request.GET.get("country", "MW"))
    if result.contract:
        return redirect("portal_contract", contract_number=result.contract.contract_number)

    return render(request, "portal/search.html", {
        "searched": True,
        "query": q,
        "error": _safe_resolution_message(result),
    })


# ---------------------------------------------------------------------------
# Contract detail
# ---------------------------------------------------------------------------

def portal_contract(request, contract_number):
    """Contract detail page — Customer Financial Dashboard."""
    contract = get_object_or_404(PaymentContract, contract_number=contract_number)

    today = timezone.localdate()
    now = timezone.now()
    early_options = calculate_early_settlement_options(contract)
    deposit_summary = get_deposit_summary(contract)
    inactive_statuses = {
        PaymentContract.STATUS_CANCELLED,
        PaymentContract.STATUS_REPOSSESSION_PENDING,
        PaymentContract.STATUS_REPOSSESSED,
        PaymentContract.STATUS_READY_FOR_RESALE,
        PaymentContract.STATUS_RESOLD,
        PaymentContract.STATUS_WRITTEN_OFF,
        PaymentContract.STATUS_LEGALLY_CLOSED,
    }
    inactive_reason = contract.get_status_display() if contract.status in inactive_statuses else ""

    # Lock status warning — shared with the dedicated device-status page.
    lock_state = compute_lock_state(contract)
    remaining = lock_state.remaining
    fully_paid = lock_state.fully_paid
    lock_status = lock_state.status
    lock_warning = lock_state.warning

    # All paid transactions (for analytics + recent display)
    all_paid = list(
        contract.transactions.filter(status=PaymentTransaction.STATUS_PAID)
        .order_by("-paid_at")
    )
    recent_transactions = all_paid[:6]

    # Analytics (computed from real data only)
    behaviour = calculate_payment_behaviour(contract)
    health = calculate_health_score(contract, behaviour)
    insights = calculate_customer_insights(contract, behaviour)

    # Recommended payment amount
    avg_interval = behaviour.get("avg_interval_days")
    if avg_interval and contract.daily_price:
        recommended_amount = int(contract.daily_price * avg_interval)
    elif contract.daily_price:
        recommended_amount = int(contract.daily_price * 7)
    else:
        recommended_amount = int(remaining)

    # Device / application info
    device_info = get_device_info(contract)

    # Authoritative deposit state comes from confirmed ledger transactions.
    deposit_required = deposit_summary.required_amount
    deposit_paid_amt = deposit_summary.confirmed_paid_amount
    deposit_remaining = deposit_summary.remaining_amount
    deposit_complete = deposit_summary.is_fully_paid
    deposit_pending = deposit_required > 0 and not deposit_complete
    payment_type = request.GET.get("payment_type", "").strip().lower()
    deposit_focus = payment_type == "deposit" and deposit_pending

    # Last payment info for lock card
    last_paid_tx = all_paid[0] if all_paid else None

    from core.commercial import pricing_from_application

    app = contract.source_application
    commercial = pricing_from_application(app) if app else None
    if commercial is None and contract.pricing_complete:
        commercial = {
            "cash_price": contract.cash_price,
            "deposit_required": contract.deposit_required,
            "contract_total": contract.total_amount,
            "daily_repayment": contract.daily_price,
            "monthly_repayment": contract.thirty_day_price,
            "term_months": contract.term_months,
        }
    pricing_complete = contract.pricing_complete

    next_required_amount = deposit_remaining if deposit_pending else recommended_amount
    next_required_date = contract.access_expires_at or contract.lock_date or contract.due_date
    if lock_warning:
        next_required_amount = lock_warning.amount
        next_required_date = lock_warning.date

    # If the deposit is awaiting confirmation, surface a link back to the live
    # status page for the specific attempt instead of a dead-end disabled
    # button — customers must always have a path to check status or (once the
    # attempt qualifies) retry, never be stuck looking at a permanently
    # disabled control with no next step.
    pending_deposit_attempt = None
    if deposit_pending and deposit_summary.status == "pending_confirmation":
        pending_deposit_attempt = (
            AirtelTransaction.objects.filter(
                contract=contract,
                purpose=AirtelTransaction.PURPOSE_DEPOSIT,
                direction=AirtelTransaction.DIRECTION_COLLECTION,
            )
            .exclude(status=AirtelTransaction.STATUS_EXPIRED)
            .order_by("-created_at")
            .first()
        )

    lock_provider_label = get_lock_provider_label(contract)
    from core.formatting import format_mwk

    def _mwk(value):
        if value is None:
            return "—"
        return format_mwk(value) or "—"

    fin_items = [
        {"label": "Paid", "value": _mwk(contract.amount_paid) if pricing_complete else "—", "tone": "success"},
        {
            "label": "Remaining",
            "value": _mwk(remaining) if pricing_complete else "—",
            "tone": "danger" if contract.status == "overdue" else "",
            "emphasis": True,
        },
        {"label": "Total", "value": _mwk(contract.total_amount) if pricing_complete and contract.total_amount else "Pending setup"},
    ]
    fin_bar_tone = "green" if contract.progress_percent >= 80 else ""

    deposit_fin_items = [
        {"label": "Deposit required", "value": _mwk(deposit_required), "tone": "warning"},
        {"label": "Confirmed", "value": _mwk(deposit_paid_amt), "tone": "success" if deposit_paid_amt else ""},
        {"label": "Remaining", "value": _mwk(deposit_remaining), "tone": "success" if deposit_complete else "warning", "emphasis": True},
    ]
    deposit_fin_caption = f"{deposit_remaining:,.0f} MWK outstanding" if deposit_remaining else "Deposit complete"
    deposit_access_days = contract.deposit_access_days or 7
    deposit_paid_notice = (
        f"Deposit of {_mwk(deposit_required)} is complete. Deposit unlocks the device for "
        f"{deposit_access_days} day{'s' if deposit_access_days != 1 else ''}"
    )
    if contract.deposit_unlock_expires_at:
        deposit_paid_notice += f", until {timezone.localtime(contract.deposit_unlock_expires_at).strftime('%d %b %Y %H:%M')}."
    else:
        deposit_paid_notice += "."

    contract_rows = [
        {"label": "Contract number", "value": contract.contract_number, "mono": True},
        {"label": "PayG number", "value": contract.payg_number or "Pending device setup", "mono": True},
        {"label": "Primary phone", "value": contract.masked_phone},
        {"label": "Customer name", "value": contract.customer_name or "Not captured"},
        {"label": "Device model", "value": device_info.get("model") or contract.device_model or "Not captured"},
        {"label": "Brand", "value": device_info.get("brand") or "Not captured"},
        {"label": "IMEI", "value": device_info.get("imei") or "Not entered yet", "mono": True},
        {"label": "Contract start", "value": contract.start_date.strftime("%d %b %Y") if contract.start_date else "—"},
        {"label": "Daily rate", "value": _mwk(contract.daily_price) if contract.daily_price else "Pending setup"},
        {"label": "Deposit access", "value": f"{deposit_access_days} days"},
        {"label": "Lock provider", "value": lock_provider_label},
        {
            "label": "Device status",
            "value": (
                contract.device_lock_status.capitalize()
                if contract.device_lock_status and contract.device_lock_status != "unknown"
                else "Not synced yet"
            ),
        },
    ]
    if contract.access_expires_at:
        contract_rows.insert(
            -2,
            {
                "label": "Access valid until",
                "value": timezone.localtime(contract.access_expires_at).strftime("%d %b %Y %H:%M"),
            },
        )

    recent_events = []
    for tx in recent_transactions:
        when = tx.paid_at or tx.created_at
        recent_events.append({
            "title": f"{tx.amount:,.0f} MWK · {tx.get_status_display()}",
            "desc": " · ".join(filter(None, [
                when.strftime("%d %b %Y, %H:%M") if when else "",
                tx.get_provider_display(),
                tx.masked_phone if getattr(tx, "phone", None) else "",
                tx.internal_reference or "",
            ])),
            "time": when,
            "state": (
                "done" if tx.status == PaymentTransaction.STATUS_PAID
                else "failed" if tx.status == PaymentTransaction.STATUS_FAILED
                else "current"
            ),
        })

    lock_band_meta = "View status"
    if lock_warning and lock_status == "warning":
        days = lock_warning.days
        lock_band_meta = f"{days} day{'s' if days != 1 else ''}"

    return render(request, "portal/contract.html", {
        "payment_idempotency_key": secrets.token_urlsafe(24),
        "contract": contract,
        "remaining": remaining,
        "early_options": early_options,
        "lock_warning": lock_warning,
        "lock_status": lock_status,
        "recent_transactions": recent_transactions,
        "recent_events": recent_events,
        "behaviour": behaviour,
        "health": health,
        "insights": insights,
        "recommended_amount": recommended_amount,
        "device_info": device_info,
        "last_paid_tx": last_paid_tx,
        "today": today,
        "deposit_required": deposit_required,
        "deposit_paid_amt": deposit_paid_amt,
        "deposit_remaining": deposit_remaining,
        "deposit_complete": deposit_complete,
        "deposit_pending": deposit_pending,
        "deposit_focus": deposit_focus,
        "deposit_summary": deposit_summary,
        "payment_type": payment_type,
        "next_required_amount": next_required_amount,
        "next_required_date": next_required_date,
        "providers": [
            ("airtel_money", "Airtel Money"),
            ("tnm_mpamba", "TNM Mpamba"),
        ],
        "commercial": commercial,
        "pricing_complete": pricing_complete,
        "lock_provider_label": lock_provider_label,
        "fully_paid": fully_paid,
        "inactive_reason": inactive_reason,
        "inactive_payment_notice": (
            f"This contract cannot currently accept payments: {inactive_reason}."
            if inactive_reason else ""
        ),
        "pending_deposit_attempt": pending_deposit_attempt,
        "airtel_test_min_amount": getattr(settings, "AIRTEL_TEST_MIN_AMOUNT", "100"),
        "airtel_test_max_amount": getattr(settings, "AIRTEL_TEST_MAX_AMOUNT", "1000"),
        "airtel_staging": getattr(settings, "AIRTEL_ENVIRONMENT", "staging").lower() == "staging",
        "fin_items": fin_items,
        "fin_bar_tone": fin_bar_tone,
        "deposit_fin_items": deposit_fin_items,
        "deposit_fin_caption": deposit_fin_caption,
        "deposit_paid_notice": deposit_paid_notice,
        "contract_rows": contract_rows,
        "device_status_url": f"/pay/contract/{contract.contract_number}/status/",
        "lock_band_meta": lock_band_meta,
    })


def portal_device_status(request, contract_number):
    """
    Dedicated device/lock status page — the authoritative customer-facing
    view of current device state, outstanding balance, next lock date, last
    successful payment, and the payment/lock timeline. Read-only: it renders
    the same lock_state and deposit data portal_contract already computes,
    from the shared compute_lock_state()/get_deposit_summary() helpers, so
    the two pages can never disagree about lock status.
    """
    contract = get_object_or_404(PaymentContract, contract_number=contract_number)

    lock_state = compute_lock_state(contract)
    deposit_summary = get_deposit_summary(contract)
    device_info = get_device_info(contract)
    lock_provider_label = get_lock_provider_label(contract)
    timeline = build_device_timeline(contract)

    all_paid = list(
        contract.transactions.filter(status=PaymentTransaction.STATUS_PAID).order_by("-paid_at")
    )
    last_paid_tx = all_paid[0] if all_paid else None

    # Any payment attempt currently awaiting confirmation, for either deposit
    # or a regular repayment — surfaced so the customer always has a "check
    # status" path instead of a dead end while a payment is in flight.
    pending_attempt = (
        AirtelTransaction.objects.filter(
            contract=contract,
            direction=AirtelTransaction.DIRECTION_COLLECTION,
        )
        .exclude(status__in=[
            AirtelTransaction.STATUS_SUCCESS,
            AirtelTransaction.STATUS_FAILED,
            AirtelTransaction.STATUS_EXPIRED,
            AirtelTransaction.STATUS_REVERSED,
        ])
        .order_by("-created_at")
        .first()
    )

    from core.formatting import format_mwk

    def _mwk(value):
        if value is None:
            return "—"
        return format_mwk(value) or "—"

    status = lock_state.status
    warning = lock_state.warning
    remaining = lock_state.remaining
    device_lock = (contract.device_lock_status or "").lower()

    # Map backend lock_status + device_lock_status onto a single customer hero.
    # Never claim immediate unlock when the provider still reports pending.
    if status == "deposit_pending":
        hero_variant, hero_icon = "warning", "alert"
        hero_title = "Deposit required"
        hero_desc = "Pay the deposit to activate this contract and unlock device access."
    elif status == "completed" or lock_state.fully_paid:
        hero_variant, hero_icon = "success", "check-circle"
        hero_title = "Contract completed"
        hero_desc = "Full repayment is complete. Ownership transfers after the agreement is closed."
    elif device_lock == "locked" or status == "overdue" or contract.status == "locked":
        hero_variant, hero_icon = "danger", "lock"
        hero_title = "Device locked" if device_lock == "locked" or contract.status == "locked" else "Payment overdue"
        hero_desc = "Pay the outstanding balance to restore device access. Unlock timing follows the lock provider schedule."
    elif device_lock == "pending":
        hero_variant, hero_icon = "info", "clock"
        hero_title = "Unlock pending"
        hero_desc = "A payment was confirmed and unlock has been scheduled with the lock provider. This is not always immediate."
    elif status == "warning" and warning:
        hero_variant, hero_icon = "warning", "clock"
        days = warning.days
        hero_title = f"Device locks in {days} day{'s' if days != 1 else ''}"
        hero_desc = f"Pay {_mwk(warning.amount)} before the lock date to keep your device unlocked."
    else:
        hero_variant, hero_icon = "success", "unlock"
        hero_title = "Device active"
        hero_desc = "Your account is in good standing. Keep payments current to avoid lock."

    hero_live = bool(pending_attempt) or device_lock == "pending"
    hero_live_label = "Payment being verified" if pending_attempt else ("Unlock in progress" if device_lock == "pending" else "")
    hero_meta = [item for item in [
        contract.payg_number or contract.contract_number,
        device_info.get("model") or contract.device_model or "",
        f"Outstanding {_mwk(remaining)}" if remaining else "",
    ] if item]

    fin_items = [
        {"label": "Outstanding", "value": _mwk(remaining), "tone": "danger" if status in {"overdue", "warning"} else "brand", "emphasis": True},
        {"label": "Paid", "value": _mwk(contract.amount_paid), "tone": "success"},
        {"label": "Daily rate", "value": _mwk(contract.daily_price) if contract.daily_price else "—"},
    ]

    meta_rows = []
    if warning and warning.date:
        meta_rows.append({
            "label": "Next lock date",
            "value": (
                timezone.localtime(warning.date).strftime("%d %b %Y %H:%M")
                if hasattr(warning.date, "hour")
                else warning.date.strftime("%d %b %Y")
            ),
            "tone": "danger" if status == "overdue" else "warning",
        })
    elif contract.access_expires_at:
        meta_rows.append({
            "label": "Access valid until",
            "value": timezone.localtime(contract.access_expires_at).strftime("%d %b %Y %H:%M"),
        })
    if last_paid_tx:
        meta_rows.append({
            "label": "Last successful payment",
            "value": f"{_mwk(last_paid_tx.amount)} · {(last_paid_tx.paid_at or last_paid_tx.created_at).strftime('%d %b %Y')}",
            "tone": "success",
        })
    if warning:
        meta_rows.append({"label": "Days remaining", "value": str(max(warning.days, 0)), "tone": "warning" if warning.days >= 0 else "danger"})
    meta_rows.extend([
        {"label": "Deposit access", "value": f"{contract.deposit_access_days or 7} days"},
        {"label": "Device lock status", "value": device_lock.capitalize() if device_lock and device_lock != "unknown" else "Not synced yet"},
        {"label": "Lock provider", "value": lock_provider_label},
        {"label": "IMEI", "value": device_info.get("imei") or "Not entered yet", "mono": True},
    ])
    if deposit_summary.required_amount:
        meta_rows.insert(0, {
            "label": "Deposit",
            "value": (
                "Fully paid" if deposit_summary.is_fully_paid
                else f"{_mwk(deposit_summary.remaining_amount)} remaining"
            ),
            "tone": "success" if deposit_summary.is_fully_paid else "warning",
        })

    timeline_events = []
    for event in timeline:
        when = event.get("time")
        timeline_events.append({
            "title": event.get("title", ""),
            "desc": event.get("desc", ""),
            "time": when,
            "time_display": timezone.localtime(when).strftime("%d %b %Y, %H:%M") if when else "",
            "state": event.get("state") or "plain",
        })

    tech_rows = [
        {"label": "Contract number", "value": contract.contract_number, "mono": True},
        {"label": "PayG number", "value": contract.payg_number or "Pending", "mono": True},
        {"label": "Provider contract ref", "value": contract.provider_contract_reference or "—", "mono": True},
        {"label": "Provider device ref", "value": contract.provider_device_reference or "—", "mono": True},
        {"label": "Enrollment status", "value": (contract.device_enrollment_status or "none").replace("_", " ").capitalize()},
        {
            "label": "Last lock sync",
            "value": (
                timezone.localtime(contract.last_lock_sync_at).strftime("%d %b %Y %H:%M")
                if contract.last_lock_sync_at else "Never"
            ),
        },
    ]
    if contract.last_lock_error:
        tech_rows.append({"label": "Last lock error", "value": contract.last_lock_error, "tone": "danger"})

    contract_url = f"/pay/contract/{contract.contract_number}/"
    actions = []
    if pending_attempt and pending_attempt.status_token:
        actions.append({
            "type": "link",
            "label": "Check payment status",
            "icon": "refresh",
            "url": f"/pay/payment/{pending_attempt.internal_reference}/?token={pending_attempt.status_token}",
            "variant": "primary",
        })
    elif not lock_state.fully_paid and status != "completed":
        pay_url = contract_url
        if status == "deposit_pending":
            pay_url = f"{contract_url}?payment_type=deposit"
        actions.append({
            "type": "link",
            "label": "Pay deposit" if status == "deposit_pending" else "Pay now",
            "icon": "receipt",
            "url": pay_url,
            "variant": "primary",
        })
    actions.append({
        "type": "link",
        "label": "Back to contract",
        "icon": "back",
        "url": contract_url,
        "variant": "secondary",
    })

    return render(request, "portal/device_status.html", {
        "contract": contract,
        "lock_state": lock_state,
        "lock_status": status,
        "lock_warning": warning,
        "remaining": remaining,
        "fully_paid": lock_state.fully_paid,
        "deposit_summary": deposit_summary,
        "device_info": device_info,
        "lock_provider_label": lock_provider_label,
        "timeline": timeline,
        "timeline_events": timeline_events,
        "last_paid_tx": last_paid_tx,
        "pending_attempt": pending_attempt,
        "hero_variant": hero_variant,
        "hero_icon": hero_icon,
        "hero_title": hero_title,
        "hero_desc": hero_desc,
        "hero_live": hero_live,
        "hero_live_label": hero_live_label,
        "hero_meta": hero_meta,
        "fin_items": fin_items,
        "meta_rows": meta_rows,
        "tech_rows": tech_rows,
        "actions": actions,
    })


# ---------------------------------------------------------------------------
# Payment initiation
# ---------------------------------------------------------------------------

@require_POST
def _legacy_portal_payment_provider_flow(request, contract_number):
    """Initiate a payment against a contract."""
    raise RuntimeError("Legacy provider payment flow is disabled; use portal_payment.")
    contract = get_object_or_404(PaymentContract, contract_number=contract_number)

    remaining_now = calculate_remaining_amount(contract)
    if contract.status == PaymentContract.STATUS_COMPLETED and remaining_now <= Decimal("0"):
        messages.info(request, "Contract fully paid. No payment is currently required.")
        return redirect("portal_contract", contract_number=contract_number)

    closed_statuses = {
        PaymentContract.STATUS_RESOLD,
        PaymentContract.STATUS_WRITTEN_OFF,
        PaymentContract.STATUS_LEGALLY_CLOSED,
    }
    if contract.status in closed_statuses:
        messages.info(request, "This contract is closed - no further access-restoring payments are available.")
        return redirect("portal_contract", contract_number=contract_number)

    # Parse form inputs
    provider_name = request.POST.get("provider", "mock")
    phone_raw = request.POST.get("phone", "").strip()
    amount_raw = request.POST.get("amount", "0").strip()
    # payment_type: deposit or repayment (from hidden form field)
    payment_type_raw = request.POST.get("payment_type", "").strip().lower()

    # Validate amount
    try:
        amount = Decimal(amount_raw)
    except (InvalidOperation, ValueError):
        messages.error(request, "Invalid amount entered. Please try again.")
        return redirect("portal_contract", contract_number=contract_number)

    if amount <= Decimal("0"):
        messages.error(request, "Payment amount must be greater than zero.")
        return redirect("portal_contract", contract_number=contract_number)

    if amount < Decimal("100"):
        messages.error(request, "Minimum payment is MWK 100.")
        return redirect("portal_contract", contract_number=contract_number)

    # Determine whether this is a deposit or repayment
    from portal.services import calculate_deposit_payment_type
    payment_type = calculate_deposit_payment_type(contract, payment_type_raw)

    if payment_type == PaymentTransaction.TYPE_DEPOSIT:
        deposit_remaining = contract.deposit_remaining
        if deposit_remaining <= Decimal("0"):
            messages.info(request, "Deposit is already fully paid.")
            return redirect("portal_contract", contract_number=contract_number)
        if amount > deposit_remaining:
            amount = deposit_remaining
    else:
        remaining = calculate_remaining_amount(contract)
        if remaining <= Decimal("0"):
            messages.info(request, "This contract is fully paid.")
            return redirect("portal_contract", contract_number=contract_number)
        if amount > remaining:
            amount = remaining

    # Normalise phone number to +265 format
    phone = phone_raw.replace(" ", "")
    if not phone.startswith("+265") and not phone.startswith("265"):
        phone = f"+265{phone.lstrip('0')}"

    # Determine network from provider_name
    network = ""
    if provider_name == "airtel_money":
        network = PaymentTransaction.NETWORK_AIRTEL
    elif provider_name == "tnm_mpamba":
        network = PaymentTransaction.NETWORK_TNM

    # Snapshot balances before payment
    balance_before = (
        contract.deposit_remaining if payment_type == PaymentTransaction.TYPE_DEPOSIT
        else contract.remaining_amount
    )

    # Create pending transaction record
    tx = PaymentTransaction.objects.create(
        payment_contract=contract,
        provider=provider_name,
        payment_type=payment_type,
        amount=amount,
        currency="MWK",
        phone=phone,
        network=network,
        balance_before=balance_before,
        status=PaymentTransaction.STATUS_PENDING,
        initiated_at=timezone.now(),
    )

    # Initiate with provider
    provider = get_payment_provider(provider_name)
    try:
        result = provider.create_payment_intent(
            amount=amount,
            phone=phone,
            reference=tx.internal_reference,
            description=f"TengaSale PayG {contract.payg_number} — {payment_type}",
        )
    except Exception as exc:
        logger.exception("Payment provider error for %s", tx.internal_reference)
        tx.status = PaymentTransaction.STATUS_FAILED
        tx.raw_response = {"error": str(exc)}
        tx.save(update_fields=["status", "raw_response", "updated_at"])
        messages.error(
            request,
            "Payment provider is temporarily unavailable. Please try again or contact support."
        )
        return redirect("portal_contract", contract_number=contract_number)

    tx.provider_reference = result.provider_reference
    tx.charge_id = result.charge_id or ""
    tx.raw_response = result.raw

    if result.success:
        # Mock provider: immediately confirm payment
        if getattr(provider, "mode", "") == "mock":
            tx.status = PaymentTransaction.STATUS_PAID
            tx.paid_at = timezone.now()
            tx.save(update_fields=["provider_reference", "charge_id", "status", "paid_at", "raw_response", "updated_at"])
            from portal.services import apply_payment_to_contract as _apply
            apply_result = _apply(contract, amount, payment_type=payment_type)
            tx.balance_after = contract.remaining_amount
            tx.save(update_fields=["balance_after", "updated_at"])
            from portal.services import finalize_paid_transaction_commission
            finalize_paid_transaction_commission(tx)
            _portal_audit(
                AuditLog.ACTION_PAYMENT,
                "PaymentContract",
                contract.id,
                {
                    "contract_number": contract_number,
                    "payg_number": contract.payg_number,
                    "payment_type": payment_type,
                    "amount": str(amount),
                    "provider": provider_name,
                    "reference": tx.internal_reference,
                    "days_extended": apply_result.get("days_extended", 0),
                    "new_status": apply_result.get("status", ""),
                },
                request,
            )
            messages.success(
                request,
                f"Payment of MWK {amount:,.0f} applied successfully. Ref: {tx.internal_reference}"
            )
            _send_payment_notifications(tx, contract)
        else:
            tx.status = PaymentTransaction.STATUS_PROCESSING
            tx.save(update_fields=["provider_reference", "charge_id", "status", "raw_response", "updated_at"])
            if result.redirect_url:
                return redirect(result.redirect_url)
            if result.flow == "momo":
                messages.info(
                    request,
                    f"PIN prompt sent to {phone}. Please enter your PIN on your phone to confirm the payment of MWK {amount:,.0f}. Ref: {tx.internal_reference}"
                )
            else:
                messages.info(request, "Payment is being processed. Check back shortly.")
    else:
        tx.status = PaymentTransaction.STATUS_FAILED
        tx.save(update_fields=["provider_reference", "charge_id", "status", "raw_response", "updated_at"])
        logger.warning(
            "Payment failed for contract %s: %s",
            contract_number, result.message
        )
        messages.error(
            request,
            "Payment provider is temporarily unavailable. Please try again or contact support."
        )

    return redirect("portal_contract", contract_number=contract_number)


@require_POST
def portal_payment(request, contract_number):
    """Initiate an Airtel collection without confirming payment locally."""
    contract = get_object_or_404(PaymentContract, contract_number=contract_number)

    remaining_now = calculate_remaining_amount(contract)
    if contract.status == PaymentContract.STATUS_COMPLETED and remaining_now <= Decimal("0"):
        messages.info(request, "Contract fully paid. No payment is currently required.")
        return redirect("portal_contract", contract_number=contract_number)

    closed_statuses = {
        PaymentContract.STATUS_RESOLD,
        PaymentContract.STATUS_WRITTEN_OFF,
        PaymentContract.STATUS_LEGALLY_CLOSED,
    }
    if contract.status in closed_statuses:
        messages.info(request, "This contract is closed - no further access-restoring payments are available.")
        return redirect("portal_contract", contract_number=contract_number)

    phone_raw = request.POST.get("phone", "").strip()
    amount_raw = request.POST.get("amount", "0").strip()
    payment_type_raw = request.POST.get("payment_type", "").strip().lower()

    try:
        amount = Decimal(amount_raw)
    except (InvalidOperation, ValueError):
        messages.error(request, "Invalid amount entered. Please try again.")
        return redirect("portal_contract", contract_number=contract_number)

    if amount <= Decimal("0"):
        messages.error(request, "Payment amount must be greater than zero.")
        return redirect("portal_contract", contract_number=contract_number)
    is_airtel_staging = getattr(settings, "AIRTEL_ENVIRONMENT", "staging").lower() == "staging"
    minimum = Decimal(str(getattr(settings, "AIRTEL_TEST_MIN_AMOUNT", "100"))) if is_airtel_staging else Decimal("100")
    if amount < minimum:
        messages.error(request, f"Minimum payment is MWK {minimum:,.0f}.")
        return redirect("portal_contract", contract_number=contract_number)

    retry_of_reference = (request.POST.get("retry_of") or "").strip()

    from portal.services import calculate_deposit_payment_type
    payment_type = calculate_deposit_payment_type(contract, payment_type_raw)
    if payment_type == PaymentTransaction.TYPE_DEPOSIT:
        deposit_state = get_deposit_summary(contract)
        deposit_remaining = deposit_state.remaining_amount
        if deposit_remaining <= Decimal("0"):
            messages.info(request, "Deposit is already fully paid.")
            return redirect("portal_contract", contract_number=contract_number)
        # An explicit retry (customer clicked "Retry" from a specific stale/failed
        # attempt) is always allowed through — the service layer independently
        # validates that the referenced attempt actually qualifies for retry. A
        # plain "pay again" submission (no retry_of) is still blocked while a
        # deposit payment is genuinely pending confirmation.
        if deposit_state.status == "pending_confirmation" and not retry_of_reference:
            messages.info(request, "A deposit payment is still awaiting confirmation. Do not pay again yet.")
            return redirect("portal_contract", contract_number=contract_number)
        if amount > deposit_remaining:
            amount = deposit_remaining
    else:
        remaining = calculate_remaining_amount(contract)
        if remaining <= Decimal("0"):
            messages.info(request, "This contract is fully paid.")
            return redirect("portal_contract", contract_number=contract_number)
        if amount > remaining:
            amount = remaining

    network = detect_mobile_network(phone_raw)
    if not network.valid:
        messages.error(request, "Enter a valid Malawi mobile money number starting with 09 or +265 9.")
        return redirect("portal_contract", contract_number=contract_number)
    if network.provider == PROVIDER_TNM:
        messages.error(request, "TNM Mpamba payments are not yet available. Please use an Airtel Money number.")
        return redirect("portal_contract", contract_number=contract_number)
    if network.provider != PROVIDER_AIRTEL:
        messages.error(request, "This mobile money network is not currently supported. Please use Airtel Money.")
        return redirect("portal_contract", contract_number=contract_number)

    purpose = (
        AirtelTransaction.PURPOSE_DEPOSIT
        if payment_type == PaymentTransaction.TYPE_DEPOSIT
        else AirtelTransaction.PURPOSE_INSTALLMENT
    )
    try:
        airtel_tx = AirtelCollectionService().initiate_collection_payment(
            msisdn=network.international,
            amount=amount,
            purpose=purpose,
            contract=contract,
            customer=request.user if request.user.is_authenticated else None,
            idempotency_key=request.POST.get("idempotency_key"),
            retry_of_reference=retry_of_reference or None,
        )
    except AirtelConfigurationError:
        logger.exception("Airtel configuration error while initiating contract payment %s", contract_number)
        messages.error(request, "Airtel Money is not ready for live payments. Please contact support.")
        return redirect("portal_contract", contract_number=contract_number)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("portal_contract", contract_number=contract_number)
    except Exception:
        logger.exception("Airtel payment initiation failed for contract %s", contract_number)
        messages.error(request, "Airtel Money is temporarily unavailable. Please try again or contact support.")
        return redirect("portal_contract", contract_number=contract_number)

    _portal_audit(
        AuditLog.ACTION_PAYMENT,
        "PaymentContract",
        contract.id,
        {
            "contract_number": contract_number,
            "payg_number": contract.payg_number,
            "payment_type": payment_type,
            "amount": str(amount),
            "provider": PROVIDER_AIRTEL,
            "reference": airtel_tx.internal_reference,
            "status": airtel_tx.status,
        },
        request,
    )
    if airtel_tx.status == AirtelTransaction.STATUS_FAILED:
        messages.error(request, "Airtel Money could not process this payment request. Please try again later.")
    elif airtel_tx.status == AirtelTransaction.STATUS_DRY_RUN:
        messages.info(request, "Your payment request is being prepared. No payment has been applied yet.")
    else:
        messages.info(
            request,
            f"Airtel Money request sent to {mask_msisdn(airtel_tx.customer_msisdn)}. Complete any prompt on the phone and do not pay again while final confirmation is pending. Reference: {airtel_tx.internal_reference}."
        )
    return redirect(f"{redirect('portal_payment_wait', internal_reference=airtel_tx.internal_reference).url}?token={airtel_tx.status_token}")


def portal_payment_wait(request, internal_reference):
    """Waiting page for pending Airtel collection confirmation."""
    airtel_tx = get_object_or_404(
        AirtelTransaction.objects.select_related("contract", "payment_transaction"),
        internal_reference=internal_reference,
    )
    if not secrets.compare_digest(str(request.GET.get("token", "")), airtel_tx.status_token):
        raise Http404
    contract = airtel_tx.contract
    from deals.brand_utils import get_brand_logo
    from payments.api_views import customer_payment_status_payload

    status_data = customer_payment_status_payload(airtel_tx)
    retry_payment_type = (
        "deposit" if airtel_tx.purpose == AirtelTransaction.PURPOSE_DEPOSIT else "repayment"
    )
    current_step = int(status_data.get("current_step") or 2)
    is_final = bool(status_data.get("is_final"))
    failed = status_data.get("status") in {"failed", "cancelled", "expired", "reversed"}

    def _step_state(step_number):
        if is_final and not failed:
            return "done"
        if failed and step_number == current_step:
            return "failed"
        if step_number < current_step:
            return "done"
        if step_number == current_step:
            return "current"
        return "pending"

    progress_steps = [
        {"label": "Request sent", "state": _step_state(1)},
        {"label": "Customer action", "state": _step_state(2)},
        {"label": "Payment confirmed", "state": _step_state(3)},
    ]

    payment_type_label = {
        "deposit": "Deposit",
        "partial_repayment": "Partial repayment",
    }.get(status_data.get("payment_type"), "Repayment")

    detail_rows = [
        {"label": "Phone", "value": mask_msisdn(airtel_tx.customer_msisdn)},
        {"label": "Payment type", "value": payment_type_label},
        {"label": "Network", "value": "Airtel Money"},
        {"label": "Amount", "value": f"{airtel_tx.currency} {airtel_tx.amount:,.0f}"},
    ]
    if contract:
        detail_rows.append({"label": "Contract", "value": contract.contract_number, "mono": True})
        if contract.payg_number:
            detail_rows.append({"label": "PayG code", "value": contract.payg_number, "mono": True})
    detail_rows.extend([
        {"label": "Reference", "value": airtel_tx.internal_reference, "mono": True},
        {"label": "Initiated", "value": timezone.localtime(airtel_tx.created_at).strftime("%d %b %Y, %H:%M %Z")},
    ])

    tech_rows = [
        {"label": "Internal reference", "value": airtel_tx.internal_reference, "mono": True},
    ]
    if airtel_tx.provider_reference:
        tech_rows.append({"label": "Provider reference", "value": airtel_tx.provider_reference, "mono": True})
    initiation = airtel_tx.initiation_response or {}
    if isinstance(initiation, dict) and initiation.get("provider_trace_id"):
        tech_rows.append({"label": "Provider trace ID", "value": initiation["provider_trace_id"], "mono": True})
    tech_rows.append({
        "label": "Callback status",
        "value": (
            f"Received{' & verified' if airtel_tx.callback_verified else ''} · "
            f"{timezone.localtime(airtel_tx.callback_received_at).strftime('%d %b %Y, %H:%M')}"
            if airtel_tx.callback_received_at else "Not yet received"
        ),
        "tone": "muted",
    })
    if airtel_tx.reconciliation_required and airtel_tx.processing_note:
        tech_rows.append({"label": "Reconciliation note", "value": airtel_tx.processing_note, "tone": "warning"})

    if status_data.get("status") == "expired":
        retry_desc = (
            "The previous attempt expired before it could be confirmed and can no longer receive "
            "a late confirmation. Starting a new request is safe — it will not double-charge you."
        )
    elif status_data.get("status") == "failed":
        retry_desc = (
            "The previous attempt failed or was declined and can no longer receive a late confirmation. "
            "Starting a new request is safe — it will not double-charge you."
        )
    else:
        retry_desc = (
            "The previous attempt ended without a successful confirmation and can no longer receive "
            "a late confirmation. Starting a new request is safe — it will not double-charge you."
        )

    deposit_access_days = (contract.deposit_access_days if contract else None) or 7

    return render(
        request,
        "portal/payment_wait.html",
        {
            "airtel_tx": airtel_tx,
            "contract": contract,
            "retry_payment_type": retry_payment_type,
            "provider_label": "Airtel Money",
            "provider_name": "Airtel Money",
            "provider_slug": "airtel_money",
            "provider_logo_url": get_brand_logo("Airtel Money"),
            "masked_phone": mask_msisdn(airtel_tx.customer_msisdn),
            "amount_display": f"{airtel_tx.amount:,.0f}",
            "transaction_reference": airtel_tx.internal_reference,
            "contract_reference": contract.contract_number if contract else "",
            "payg_code": contract.payg_number if contract else "",
            "status_data": status_data,
            "is_staff_view": bool(request.user.is_authenticated and request.user.is_staff),
            "is_dry_run": airtel_tx.status == AirtelTransaction.STATUS_DRY_RUN,
            "status_url": f"/api/payments/{airtel_tx.internal_reference}/status/?token={airtel_tx.status_token}",
            "return_url": (
                redirect("portal_contract", contract_number=contract.contract_number).url
                if contract
                else ""
            ),
            "progress_steps": progress_steps,
            "detail_rows": detail_rows,
            "tech_rows": tech_rows,
            "retry_desc": retry_desc,
            "retry_action_url": (
                f"/pay/contract/{contract.contract_number}/payment/" if contract else ""
            ),
            "retry_fields": [
                {"name": "phone", "value": airtel_tx.customer_msisdn},
                {"name": "amount", "value": str(airtel_tx.amount)},
                {"name": "payment_type", "value": retry_payment_type},
                {"name": "retry_of", "value": airtel_tx.internal_reference},
            ],
            "ambiguous_statuses": {
                "confirmation_delayed",
                "reconciliation_required",
                "manual_review",
                "pending_provider_confirmation",
            },
            "deposit_access_days": deposit_access_days,
            "airtel_money_id": status_data.get("airtel_money_id") or "",
        },
    )


# ---------------------------------------------------------------------------
# Payment history
# ---------------------------------------------------------------------------

def portal_history(request, contract_number):
    """Enhanced payment history with running balance."""
    contract = get_object_or_404(PaymentContract, contract_number=contract_number)

    month_filter = request.GET.get("month", "")
    method_filter = request.GET.get("method", "")

    from portal.models import PaymentTransaction

    qs = (
        contract.transactions.filter(amount__gt=0)
        .exclude(status=PaymentTransaction.STATUS_FAILED)
        .order_by("created_at")
    )

    if month_filter:
        try:
            parts = month_filter.split("-")
            qs = qs.filter(created_at__year=int(parts[0]), created_at__month=int(parts[1]))
        except (ValueError, IndexError):
            pass

    if method_filter:
        qs = qs.filter(provider=method_filter)

    all_txns = list(qs)

    # Compute running instalment balance (chronological order; deposits tracked separately)
    running_instalments = Decimal("0")
    for tx in all_txns:
        if tx.status == PaymentTransaction.STATUS_PAID:
            if tx.payment_type == PaymentTransaction.TYPE_DEPOSIT:
                tx.ledger_kind = "deposit"
            else:
                running_instalments += tx.amount
                tx.ledger_kind = "repayment"
            tx.balance_after = max(contract.total_amount - running_instalments, Decimal("0"))
        else:
            tx.ledger_kind = tx.payment_type or "repayment"
            tx.balance_after = None

    # Reverse for display (newest first)
    all_txns.reverse()

    deposit_summary = get_deposit_summary(contract)
    from core.formatting import format_mwk

    def _mwk(value):
        if value is None:
            return "—"
        return format_mwk(value) or "—"

    history_fin_items = [
        {"label": "Instalments", "value": _mwk(contract.amount_paid), "tone": "success"},
        {"label": "Confirmed deposit", "value": _mwk(deposit_summary.confirmed_paid_amount)},
        {"label": "Remaining", "value": _mwk(contract.remaining_amount), "emphasis": True},
        {"label": "Complete", "value": f"{contract.progress_percent}%"},
    ]

    history_events = []
    for tx in all_txns:
        when = tx.paid_at or tx.created_at
        state = (
            "done" if tx.status == PaymentTransaction.STATUS_PAID
            else "failed" if tx.status == PaymentTransaction.STATUS_FAILED
            else "current"
        )
        history_events.append({
            "title": f"{_mwk(tx.amount)} · {tx.get_status_display()}",
            "desc": " · ".join(filter(None, [
                tx.get_payment_type_display() if tx.payment_type else "",
                tx.get_provider_display(),
                tx.masked_phone if getattr(tx, "phone", None) else "",
                tx.internal_reference or "",
                f"Balance after {_mwk(tx.balance_after)}" if tx.status == PaymentTransaction.STATUS_PAID and tx.balance_after is not None else "",
            ])),
            "time": when,
            "time_display": timezone.localtime(when).strftime("%d %b %Y, %H:%M") if when else "",
            "state": state,
        })

    return render(request, "portal/history.html", {
        "contract": contract,
        "deposit_summary": deposit_summary,
        "transactions": all_txns,
        "history_fin_items": history_fin_items,
        "history_events": history_events,
        "month_filter": month_filter,
        "method_filter": method_filter,
        "providers": PaymentTransaction.PROVIDER_CHOICES,
    })


# ---------------------------------------------------------------------------
# Support
# ---------------------------------------------------------------------------

def portal_support(request):
    return render(request, "portal/support.html", {})


# ---------------------------------------------------------------------------
# PayG routes — primary customer-facing URLs (/pay/payg/EXXXXXXX/)
# These resolve the contract by payg_number and delegate to existing views.
# ---------------------------------------------------------------------------

def _get_contract_by_payg(payg_number):
    """
    Look up a PaymentContract by its PayG number.
    Returns (contract, error_response) — exactly one will be non-None.
    """
    payg = payg_number.strip().upper()
    if not _PAYG_RE.match(payg):
        return None, "invalid_format"
    try:
        return PaymentContract.objects.get(payg_number=payg), None
    except PaymentContract.DoesNotExist:
        return None, "not_found"


def portal_payg(request, payg_number):
    """Resolve by PayG number and redirect to the contract detail page."""
    contract, err = _get_contract_by_payg(payg_number)
    if err == "invalid_format":
        return render(request, "portal/search.html", {
            "searched": True,
            "query": payg_number,
            "error": (
                "Invalid PayG number format. "
                "A PayG number starts with E and is exactly 8 characters long, e.g. EXGH4456."
            ),
        }, status=400)
    if err == "not_found":
        return render(request, "portal/search.html", {
            "searched": True,
            "query": payg_number,
            "error": "PayG contract not found. Please check the number or contact support.",
        }, status=404)
    return portal_contract(request, contract.contract_number)


@require_POST
def portal_payg_payment(request, payg_number):
    """Accept a payment POST for a PayG number."""
    contract, err = _get_contract_by_payg(payg_number)
    if err:
        messages.error(request, "PayG contract not found.")
        return redirect("portal_search")
    return portal_payment(request, contract.contract_number)


def portal_payg_history(request, payg_number):
    """Payment history for a PayG number."""
    contract, err = _get_contract_by_payg(payg_number)
    if err:
        return render(request, "portal/search.html", {
            "searched": True,
            "query": payg_number,
            "error": "PayG contract not found. Please check the number or contact support.",
        }, status=404)
    return portal_history(request, contract.contract_number)


def portal_payg_device_status(request, payg_number):
    """Device/lock status page for a PayG number."""
    contract, err = _get_contract_by_payg(payg_number)
    if err:
        return render(request, "portal/search.html", {
            "searched": True,
            "query": payg_number,
            "error": "PayG contract not found. Please check the number or contact support.",
        }, status=404)
    return portal_device_status(request, contract.contract_number)


# ---------------------------------------------------------------------------
# Webhook placeholder endpoints
# All return safe JSON and log to AuditLog.
# CSRF exempt — providers send raw POST from their own servers.
# ---------------------------------------------------------------------------

@csrf_exempt
def _webhook_handler(request, provider_name):
    """Generic webhook handler for non-PayChangu providers — logs receipt."""
    if request.method not in ("POST", "GET"):
        return JsonResponse({"ok": False, "message": "Method not allowed."}, status=405)

    raw_body = request.body
    try:
        payload = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        payload = {"raw": raw_body.decode("utf-8", errors="replace")[:500]}

    try:
        _portal_audit(
            AuditLog.ACTION_WEBHOOK,
            "Webhook",
            provider_name,
            {
                "provider": provider_name,
                "method": request.method,
                "content_type": request.content_type,
                "payload_keys": list(payload.keys()) if isinstance(payload, dict) else [],
            },
            request,
        )
    except Exception:
        logger.exception("Failed to audit webhook for %s", provider_name)

    logger.info("Webhook received from provider=%s", provider_name)
    return JsonResponse({"ok": True, "received": True, "provider": provider_name})


@csrf_exempt
def webhook_paychangu(request):
    """
    PayChangu webhook endpoint — processes payment confirmations.

    Verifies HMAC signature, updates transaction status, applies payment to
    contract, and sends notifications. Always returns HTTP 200 to prevent retries.
    """
    from integrations import paychangu_client
    from portal.services import apply_payment_to_contract

    if request.method not in ("POST",):
        return JsonResponse({"ok": False}, status=405)

    raw_body = request.body

    # --- Signature verification ---
    signature = (
        request.headers.get("Signature", "")
        or request.headers.get("X-Signature", "")
        or request.headers.get("X-PayChangu-Signature", "")
        or request.META.get("HTTP_SIGNATURE", "")
        or request.META.get("HTTP_X_SIGNATURE", "")
        or request.META.get("HTTP_X_PAYCHANGU_SIGNATURE", "")
    ).strip()

    webhook_secret = getattr(settings, "PAYCHANGU_WEBHOOK_SECRET", "")
    signature_valid = False

    if signature and webhook_secret:
        signature_valid = paychangu_client.verify_webhook_signature(raw_body, signature)
        if not signature_valid:
            logger.warning("PayChangu webhook: signature mismatch — proceeding with caution")
    elif not webhook_secret:
        # No secret configured: accept but flag
        signature_valid = True
        logger.warning("PayChangu webhook: PAYCHANGU_WEBHOOK_SECRET not set — accepting without verification")
    else:
        logger.warning("PayChangu webhook: missing signature header")

    # --- Parse payload ---
    try:
        data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except Exception as exc:
        logger.error("PayChangu webhook: invalid JSON body: %s", exc)
        return JsonResponse({"ok": True, "message": "Invalid JSON"}, status=200)

    # --- Extract tx_ref ---
    tx_ref = (
        data.get("tx_ref")
        or data.get("reference")
        or data.get("transaction_id")
        or data.get("payment_reference")
        or data.get("transaction_reference")
        or ""
    )
    event_type = data.get("event", "payment.webhook")

    logger.info("PayChangu webhook: tx_ref=%s event=%s sig_valid=%s", tx_ref, event_type, signature_valid)

    _portal_audit(
        AuditLog.ACTION_WEBHOOK,
        "PayChangu",
        tx_ref,
        {
            "tx_ref": tx_ref,
            "event": event_type,
            "signature_valid": signature_valid,
            "keys": list(data.keys()),
        },
        request,
    )

    if not tx_ref:
        logger.warning("PayChangu webhook: no tx_ref in payload, keys=%s", list(data.keys()))
        return JsonResponse({"ok": True}, status=200)

    # --- Find transaction by internal_reference (= tx_ref sent to PayChangu) ---
    try:
        tx = PaymentTransaction.objects.select_related("payment_contract").get(
            internal_reference=tx_ref
        )
    except PaymentTransaction.DoesNotExist:
        logger.warning("PayChangu webhook: transaction not found for tx_ref=%s", tx_ref)
        return JsonResponse({"ok": True, "message": "Transaction not found"}, status=200)

    # --- Idempotency: skip if already paid ---
    if tx.status == PaymentTransaction.STATUS_PAID:
        logger.info("PayChangu webhook: tx %s already paid — skipping", tx_ref)
        return JsonResponse({"ok": True, "message": "Already processed"}, status=200)

    # --- Store webhook payload ---
    tx.webhook_payload = data
    tx.save(update_fields=["webhook_payload", "updated_at"])

    # --- Verify with PayChangu API ---
    try:
        if tx.charge_id:
            verify_result = paychangu_client.momo_verify(tx.charge_id)
        else:
            verify_result = paychangu_client.verify_transaction(tx_ref)
        verified_status = verify_result.get("status", "PENDING")
    except Exception as exc:
        logger.error("PayChangu webhook: verify call failed for %s: %s", tx_ref, exc)
        # Fall back to event_type from webhook payload
        verified_status = "SUCCESS" if "success" in event_type.lower() else "PENDING"

    logger.info("PayChangu webhook: verify=%s for tx_ref=%s", verified_status, tx_ref)

    # --- Update transaction status ---
    from django.utils import timezone as tz
    contract = tx.payment_contract

    if verified_status == "SUCCESS":
        balance_before = contract.remaining_amount
        tx.status = PaymentTransaction.STATUS_PAID
        tx.paid_at = tz.now()
        tx.balance_before = balance_before
        tx.raw_response = verify_result.get("raw_response", {})
        tx.save(update_fields=["status", "paid_at", "balance_before", "raw_response", "updated_at"])

        # Apply payment to contract
        try:
            result = apply_payment_to_contract(
                contract,
                tx.amount,
                payment_type=tx.payment_type,
            )
            balance_after = contract.remaining_amount
            tx.balance_after = balance_after
            tx.save(update_fields=["balance_after", "updated_at"])

            from portal.services import finalize_paid_transaction_commission
            finalize_paid_transaction_commission(tx)

            logger.info(
                "PayChangu webhook: applied MWK %s to contract %s (type=%s, result=%s)",
                tx.amount, contract.contract_number, tx.payment_type, result.get("status"),
            )
        except Exception as exc:
            logger.exception("PayChangu webhook: apply_payment_to_contract failed for %s: %s", tx_ref, exc)

        # Activate contract if deposit now complete
        _maybe_activate_contract_after_deposit(contract, tx)

        # Notifications (best-effort — never crash the webhook)
        _send_payment_notifications(tx, contract)

        _portal_audit(
            AuditLog.ACTION_PAYMENT,
            "PaymentTransaction",
            tx.id,
            {
                "tx_ref": tx_ref,
                "contract": contract.contract_number,
                "payg_number": contract.payg_number,
                "amount": str(tx.amount),
                "payment_type": tx.payment_type,
                "status": "paid",
            },
            request,
        )

    elif verified_status == "FAILED":
        tx.status = PaymentTransaction.STATUS_FAILED
        tx.raw_response = verify_result.get("raw_response", {})
        tx.save(update_fields=["status", "raw_response", "updated_at"])
        logger.info("PayChangu webhook: tx %s FAILED", tx_ref)

    else:
        # Still PENDING — PayChangu may send more webhooks
        logger.info("PayChangu webhook: tx %s still PENDING", tx_ref)

    return JsonResponse({"ok": True}, status=200)


def _maybe_activate_contract_after_deposit(contract, tx):
    """
    After a successful deposit payment, check if deposit is now complete.
    If so, ensure contract status is active.
    """
    if tx.payment_type != PaymentTransaction.TYPE_DEPOSIT:
        return
    contract.refresh_from_db()
    deposit_state = get_deposit_summary(contract)
    update_fields = []
    if contract.deposit_paid != deposit_state.confirmed_paid_amount:
        contract.deposit_paid = deposit_state.confirmed_paid_amount
        update_fields.append("deposit_paid")
    if deposit_state.can_activate_contract and contract.status not in (
        PaymentContract.STATUS_ACTIVE, PaymentContract.STATUS_COMPLETED
    ):
        contract.status = PaymentContract.STATUS_ACTIVE
        update_fields.append("status")
    if update_fields:
        contract.save(update_fields=update_fields)
    if "status" in update_fields:
        _portal_audit(
            AuditLog.ACTION_PAYMENT,
            "PaymentContract",
            contract.pk,
            {
                "event": "deposit_confirmed_contract_activated",
                "transaction": tx.internal_reference,
                "confirmed_deposit": str(deposit_state.confirmed_paid_amount),
            },
        )
        logger.info(
            "Contract %s activated after deposit fully paid (deposit_paid=%s)",
            contract.contract_number, deposit_state.confirmed_paid_amount,
        )


def _send_payment_notifications(tx, contract):
    """Send email/SMS notifications after a confirmed payment. Best-effort."""
    try:
        from notifications.services.email import send_payment_receipt_email
        send_payment_receipt_email(tx, contract)
    except Exception as exc:
        logger.warning("Failed to send payment receipt email: %s", exc)

    try:
        from notifications.services.twilio_sms import send_payment_receipt_sms
        send_payment_receipt_sms(tx, contract)
    except Exception as exc:
        logger.warning("Failed to send payment receipt SMS: %s", exc)

    try:
        from communications.services import send_payment_confirmation_sms
        send_payment_confirmation_sms(tx)
    except Exception as exc:
        logger.warning("Failed to log/send audited payment confirmation SMS: %s", exc)


# ---------------------------------------------------------------------------
# Payment return / status pages
# ---------------------------------------------------------------------------

def payment_return(request):
    """
    Browser return page after PayChangu checkout.
    Customer lands here after payment — shows pending/success/fail state.
    """
    tx_ref = (
        request.GET.get("tx_ref")
        or request.GET.get("reference")
        or request.GET.get("transaction_id")
        or ""
    ).strip()

    context = {"tx_ref": tx_ref, "status": "pending"}

    if tx_ref:
        try:
            tx = PaymentTransaction.objects.select_related("payment_contract").get(
                internal_reference=tx_ref
            )
            if tx.status == PaymentTransaction.STATUS_PAID:
                context["status"] = "success"
                context["tx"] = tx
                context["contract"] = tx.payment_contract
            elif tx.status == PaymentTransaction.STATUS_FAILED:
                context["status"] = "failed"
                context["tx"] = tx
            else:
                context["status"] = "pending"
                context["tx"] = tx
                context["contract"] = tx.payment_contract
        except PaymentTransaction.DoesNotExist:
            context["status"] = "not_found"

    return render(request, "portal/payment_return.html", context)


def payment_status_json(request):
    """JSON polling endpoint — client-side can check payment progress."""
    tx_ref = request.GET.get("tx_ref", "").strip()
    if not tx_ref:
        return JsonResponse({"status": "error", "message": "Missing tx_ref"}, status=400)

    try:
        tx = PaymentTransaction.objects.get(internal_reference=tx_ref)
    except PaymentTransaction.DoesNotExist:
        return JsonResponse({"status": "not_found"}, status=404)

    if tx.status == PaymentTransaction.STATUS_PAID:
        contract = tx.payment_contract
        return JsonResponse({
            "status": "success",
            "message": f"Payment of MWK {tx.amount:,.0f} confirmed.",
            "redirect": f"/pay/contract/{contract.contract_number}/",
        })
    elif tx.status == PaymentTransaction.STATUS_FAILED:
        return JsonResponse({"status": "failed", "message": "Payment failed. Please try again."})
    else:
        return JsonResponse({
            "status": "pending",
            "message": "Waiting for payment confirmation...",
        })


@csrf_exempt
def webhook_paytrigger(request):
    """PayTrigger webhook endpoint."""
    return _webhook_handler(request, "paytrigger")


@csrf_exempt
def webhook_airtel(request):
    """Airtel Money webhook endpoint."""
    return _webhook_handler(request, "airtel")


@csrf_exempt
def webhook_tnm(request):
    """TNM Mpamba webhook endpoint."""
    return _webhook_handler(request, "tnm")
