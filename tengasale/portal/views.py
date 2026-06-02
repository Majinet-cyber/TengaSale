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
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.models import AuditLog
from .models import PaymentContract, PaymentTransaction
from .payment_providers import get_payment_provider
from .services import (
    apply_payment_to_contract,
    calculate_early_settlement_options,
    calculate_remaining_amount,
    calculate_payment_behaviour,
    calculate_health_score,
    calculate_customer_insights,
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

def portal_search(request):
    """Landing / search page."""
    error = request.GET.get("error")
    return render(request, "portal/search.html", {
        "error": error,
        "query": request.GET.get("q", ""),
    })


def portal_search_post(request):
    """Process search and redirect to contract page."""
    q = request.GET.get("q", "").strip()
    if not q:
        return redirect("portal_search")

    contract = search_payment_contract(q)
    if contract:
        return redirect("portal_contract", contract_number=contract.contract_number)

    return render(request, "portal/search.html", {
        "searched": True,
        "query": q,
        "error": "No contract found matching that number, ID, or phone.",
    })


# ---------------------------------------------------------------------------
# Contract detail
# ---------------------------------------------------------------------------

def portal_contract(request, contract_number):
    """Contract detail page — Customer Financial Dashboard."""
    contract = get_object_or_404(PaymentContract, contract_number=contract_number)

    today = timezone.localdate()
    remaining = calculate_remaining_amount(contract)
    early_options = calculate_early_settlement_options(contract)

    # Lock status warning
    lock_warning = None
    lock_status = "safe"
    if contract.status == "completed":
        lock_status = "completed"
    elif contract.lock_date:
        days_until_lock = (contract.lock_date - today).days
        hours_until_lock = days_until_lock * 24
        if days_until_lock < 0:
            lock_status = "overdue"
            lock_warning = {
                "date": contract.lock_date,
                "amount": remaining,
                "days": days_until_lock,
                "hours": hours_until_lock,
            }
        elif days_until_lock <= 4:
            lock_status = "warning"
            lock_warning = {
                "date": contract.lock_date,
                "amount": (
                    contract.daily_price * max(days_until_lock, 0)
                    if contract.daily_price else remaining
                ),
                "days": days_until_lock,
                "hours": hours_until_lock,
            }
        else:
            lock_status = "safe"

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
    app = contract.source_application
    device_info = {
        "model": contract.device_model or "—",
        "imei": contract.imei_number or "",
        "brand": "",
        "merchant_name": "",
        "merchant_branch": "",
        "underwriter_name": "",
    }
    if app:
        # Fall back to application IMEI if contract doesn't have it yet
        if not device_info["imei"]:
            device_info["imei"] = getattr(app, "imei_number", "") or ""
        device_info["brand"] = getattr(app, "imei_api_brand", "") or ""
        if not device_info["brand"] and app.deal:
            try:
                device_info["brand"] = str(app.deal.brand) if hasattr(app.deal, "brand") else ""
            except Exception:
                pass
        if app.created_by:
            try:
                profile = app.created_by.profile
                if hasattr(profile, "merchant") and profile.merchant:
                    device_info["merchant_name"] = str(profile.merchant)
            except Exception:
                pass
        try:
            device_info["underwriter_name"] = app.underwriter_name or ""
        except Exception:
            pass

    # Deposit state
    deposit_required = contract.deposit_required or Decimal("0")
    deposit_paid_amt = contract.deposit_paid or Decimal("0")
    deposit_remaining = contract.deposit_remaining
    deposit_complete = contract.deposit_complete
    deposit_pending = deposit_required > 0 and not deposit_complete

    # Last payment info for lock card
    last_paid_tx = all_paid[0] if all_paid else None

    return render(request, "portal/contract.html", {
        "contract": contract,
        "remaining": remaining,
        "early_options": early_options,
        "lock_warning": lock_warning,
        "lock_status": lock_status,
        "recent_transactions": recent_transactions,
        "behaviour": behaviour,
        "health": health,
        "insights": insights,
        "recommended_amount": recommended_amount,
        "device_info": device_info,
        "last_paid_tx": last_paid_tx,
        "today": today,
        # Deposit context
        "deposit_required": deposit_required,
        "deposit_paid_amt": deposit_paid_amt,
        "deposit_remaining": deposit_remaining,
        "deposit_complete": deposit_complete,
        "deposit_pending": deposit_pending,
        "providers": [
            ("airtel_money", "Airtel Money"),
            ("tnm_mpamba", "TNM Mpamba"),
        ],
    })


# ---------------------------------------------------------------------------
# Payment initiation
# ---------------------------------------------------------------------------

@require_POST
def portal_payment(request, contract_number):
    """Initiate a payment against a contract."""
    contract = get_object_or_404(PaymentContract, contract_number=contract_number)

    if contract.status == PaymentContract.STATUS_COMPLETED:
        messages.info(request, "This contract is fully paid — no further payments are needed.")
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

    # Create pending transaction record
    tx = PaymentTransaction.objects.create(
        payment_contract=contract,
        provider=provider_name,
        payment_type=payment_type,
        amount=amount,
        currency="MWK",
        phone=phone,
        status=PaymentTransaction.STATUS_PENDING,
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
        tx.save(update_fields=["status", "raw_response"])
        messages.error(
            request,
            "Payment provider is temporarily unavailable. Please try again or contact support."
        )
        return redirect("portal_contract", contract_number=contract_number)

    tx.provider_reference = result.provider_reference
    tx.raw_response = result.raw

    if result.success:
        # Mock provider: immediately confirm payment
        if getattr(provider, "mode", "") == "mock":
            tx.status = PaymentTransaction.STATUS_PAID
            tx.paid_at = timezone.now()
            tx.save(update_fields=["provider_reference", "status", "paid_at", "raw_response"])
            from portal.services import apply_payment_to_contract as _apply
            apply_result = _apply(contract, amount, payment_type=payment_type)
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
        else:
            tx.status = PaymentTransaction.STATUS_PROCESSING
            tx.save(update_fields=["provider_reference", "status", "raw_response"])
            if result.redirect_url:
                return redirect(result.redirect_url)
            messages.info(request, "Payment is being processed. Check back shortly.")
    else:
        tx.status = PaymentTransaction.STATUS_FAILED
        tx.save(update_fields=["provider_reference", "status", "raw_response"])
        logger.warning(
            "Payment failed for contract %s: %s",
            contract_number, result.message
        )
        messages.error(
            request,
            "Payment provider is temporarily unavailable. Please try again or contact support."
        )

    return redirect("portal_contract", contract_number=contract_number)


# ---------------------------------------------------------------------------
# Payment history
# ---------------------------------------------------------------------------

def portal_history(request, contract_number):
    """Enhanced payment history with running balance."""
    contract = get_object_or_404(PaymentContract, contract_number=contract_number)

    month_filter = request.GET.get("month", "")
    method_filter = request.GET.get("method", "")

    qs = contract.transactions.order_by("created_at")

    if month_filter:
        try:
            parts = month_filter.split("-")
            qs = qs.filter(created_at__year=int(parts[0]), created_at__month=int(parts[1]))
        except (ValueError, IndexError):
            pass

    if method_filter:
        qs = qs.filter(provider=method_filter)

    all_txns = list(qs)

    # Compute running balance (chronological order)
    running_paid = Decimal("0")
    for tx in all_txns:
        if tx.status == PaymentTransaction.STATUS_PAID:
            running_paid += tx.amount
        tx.balance_after = max(contract.total_amount - running_paid, Decimal("0"))

    # Reverse for display (newest first)
    all_txns.reverse()

    return render(request, "portal/history.html", {
        "contract": contract,
        "transactions": all_txns,
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


# ---------------------------------------------------------------------------
# Webhook placeholder endpoints
# All return safe JSON and log to AuditLog.
# CSRF exempt — providers send raw POST from their own servers.
# ---------------------------------------------------------------------------

@csrf_exempt
def _webhook_handler(request, provider_name):
    """Internal webhook handler — logs receipt and returns safe JSON."""
    if request.method not in ("POST", "GET"):
        return JsonResponse({"ok": False, "message": "Method not allowed."}, status=405)

    # Parse body safely
    raw_body = request.body
    try:
        payload = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        payload = {"raw": raw_body.decode("utf-8", errors="replace")[:500]}

    # Log webhook receipt
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

    return JsonResponse({
        "ok": True,
        "received": True,
        "provider": provider_name,
        "message": f"Webhook acknowledged. Processing not yet implemented for {provider_name}.",
    })


@csrf_exempt
def webhook_paychangu(request):
    """PayChangu webhook endpoint."""
    return _webhook_handler(request, "paychangu")


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
