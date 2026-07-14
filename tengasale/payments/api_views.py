from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Count, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from contracts.models import Contract
from portal.models import PaymentContract

from .airtel_client import AirtelConfig
from .airtel_services import (
    AirtelCallbackService,
    AirtelCollectionService,
    AirtelTransactionEnquiryService,
)
from .models import AirtelCallbackLog, AirtelTransaction, USSDPaymentIntent, USSDSessionLog
from .views import _hq_or_finance


def _json_body(request) -> tuple[dict, JsonResponse | None]:
    try:
        return json.loads(request.body.decode("utf-8")) if request.body else {}, None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}, JsonResponse({"success": False, "error": "Invalid JSON body."}, status=400)


def plain_ussd_response(message: str) -> HttpResponse:
    return HttpResponse(message, content_type="text/plain; charset=utf-8")


def format_mwk(value):
    try:
        value = Decimal(value)
        return f"{value:,.0f}"
    except Exception:
        return str(value)


def get_contract_balance(contract_number: str):
    contract_number = (contract_number or "").strip()
    if not contract_number:
        return None

    portal_contract = (
        PaymentContract.objects.filter(contract_number__iexact=contract_number).first()
        or PaymentContract.objects.filter(payg_number__iexact=contract_number).first()
    )
    if portal_contract:
        balance = max((portal_contract.total_amount or Decimal("0")) - (portal_contract.amount_paid or Decimal("0")), Decimal("0"))
        next_payment = portal_contract.thirty_day_price or portal_contract.daily_price or Decimal("0")
        return {
            "balance": balance,
            "next_payment": next_payment,
            "due_date": portal_contract.due_date.isoformat() if portal_contract.due_date else "Not available",
        }

    legal_contract = Contract.objects.filter(contract_number__iexact=contract_number).first()
    if legal_contract:
        balance = max((legal_contract.total_loan or Decimal("0")) - (legal_contract.deposit_amount if legal_contract.deposit_paid else Decimal("0")), Decimal("0"))
        return {
            "balance": balance,
            "next_payment": legal_contract.monthly_payment or legal_contract.daily_payment or Decimal("0"),
            "due_date": "Not available",
        }

    return None


@csrf_exempt
@require_POST
def ussd_callback(request):
    session_id = request.POST.get("sessionId", "").strip()
    service_code = request.POST.get("serviceCode", "").strip()
    phone_number = request.POST.get("phoneNumber", "").strip()
    text = request.POST.get("text", "").strip()

    response = "END Invalid option. Please try again."

    try:
        parts = text.split("*") if text else []

        if text == "":
            response = "CON Welcome to TengaSale\n1. Pay\n2. Check balance"

        elif text == "1":
            response = "CON Enter amount you want to pay"

        elif len(parts) == 2 and parts[0] == "1":
            try:
                amount = Decimal(parts[1].strip())
                response = "CON Enter your TengaSale contract number" if amount > 0 else "END Invalid amount. Please try again."
            except (InvalidOperation, ValueError):
                response = "END Invalid amount. Please try again."

        elif len(parts) == 3 and parts[0] == "1":
            amount_raw = parts[1].strip()
            contract_number = parts[2].strip().upper()
            try:
                amount = Decimal(amount_raw)
                if amount <= 0:
                    response = "END Invalid amount. Please try again."
                elif not contract_number:
                    response = "END Invalid contract number. Please try again."
                else:
                    USSDPaymentIntent.objects.create(
                        phone_number=phone_number,
                        session_id=session_id,
                        service_code=service_code,
                        amount=amount,
                        contract_number=contract_number,
                        source="USSD",
                        status="PENDING",
                        raw_text=text,
                    )
                    response = "END Payment request received. TengaSale will verify and confirm shortly."
            except (InvalidOperation, ValueError):
                response = "END Invalid amount. Please try again."

        elif text == "2":
            response = "CON Enter your TengaSale contract number"

        elif len(parts) == 2 and parts[0] == "2":
            contract_number = parts[1].strip().upper()
            if not contract_number:
                response = "END Invalid contract number. Please try again."
            else:
                balance_info = get_contract_balance(contract_number)
                if balance_info:
                    balance = format_mwk(balance_info.get("balance"))
                    next_payment = format_mwk(balance_info.get("next_payment"))
                    due_date = balance_info.get("due_date") or "Not available"
                    response = f"END Balance: MWK {balance}. Next payment: MWK {next_payment}. Due date: {due_date}."
                else:
                    response = "END Contract not found. Please check your number or contact TengaSale support on +265990870616."

        else:
            response = "END Invalid option. Please try again."

    except Exception:
        response = "END Service temporarily unavailable. Please try again shortly."

    finally:
        try:
            USSDSessionLog.objects.create(
                session_id=session_id,
                service_code=service_code,
                phone_number=phone_number,
                text=text,
                response=response,
            )
        except Exception:
            pass

    return plain_ussd_response(response)


@csrf_exempt
@require_POST
def airtel_callback(request):
    headers = {key: value for key, value in request.headers.items()}
    log, valid_body, status_code = AirtelCallbackService().handle_callback(request.body, headers)
    if not valid_body:
        return JsonResponse({"ok": False, "message": "Invalid callback body.", "log_id": log.id}, status=400)
    if status_code in (401, 403):
        return JsonResponse({"ok": False, "message": "Invalid callback signature.", "log_id": log.id}, status=status_code)
    if status_code >= 400:
        return JsonResponse({"ok": False, "message": log.processing_error or "Callback rejected.", "log_id": log.id}, status=status_code)
    return JsonResponse({"ok": True, "provider": "airtel_money", "log_id": log.id}, status=200)


@require_GET
def airtel_health(request):
    config = AirtelConfig.from_settings()
    env = "production" if config.base_url.rstrip("/") == "https://openapi.airtel.mw" else "staging"
    return JsonResponse({
        "status": "ok",
        "provider": "airtel_money",
        "environment": env,
        "callback_configured": bool(getattr(settings, "AIRTEL_CALLBACK_URL", "")),
    })


@csrf_exempt
@require_http_methods(["POST"])
def airtel_collection_initiate(request):
    data, error = _json_body(request)
    if error:
        return error

    contract = None
    contract_id = data.get("contract_id")
    if contract_id not in (None, ""):
        contract = get_object_or_404(PaymentContract, id=contract_id)

    try:
        amount = Decimal(str(data.get("amount", "")))
    except (InvalidOperation, ValueError):
        return JsonResponse({"success": False, "error": "Enter a valid MWK amount."}, status=400)

    try:
        tx = AirtelCollectionService().initiate_collection_payment(
            msisdn=data.get("msisdn", ""),
            amount=amount,
            purpose=data.get("purpose", AirtelTransaction.PURPOSE_TEST),
            contract=contract,
            customer=request.user if getattr(request, "user", None) and request.user.is_authenticated else None,
        )
    except ValueError as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=400)

    return JsonResponse({
        "success": True,
        "provider": "airtel",
        "internal_reference": tx.internal_reference,
        "status": tx.status,
        "message": "Payment request sent. Please approve on your phone.",
    })


@require_http_methods(["GET"])
def airtel_transaction_enquiry(request, internal_reference):
    tx = get_object_or_404(AirtelTransaction, internal_reference=internal_reference)
    try:
        tx = AirtelTransactionEnquiryService().enquire(tx.internal_reference)
    except Exception:
        tx.refresh_from_db()
    return JsonResponse({
        "success": True,
        "provider": "airtel",
        "internal_reference": tx.internal_reference,
        "status": tx.status,
    })


def airtel_dashboard(request):
    if not request.user.is_authenticated or not _hq_or_finance(request.user):
        return JsonResponse({"ok": False, "error": "Unauthorized"}, status=403)

    today = timezone.localdate()
    month_start = today.replace(day=1)
    qs = AirtelTransaction.objects.all()
    successful = qs.filter(status=AirtelTransaction.STATUS_SUCCESS)

    summary = {
        "collections_today": successful.filter(created_at__date=today).aggregate(total=Sum("amount"))["total"] or 0,
        "collections_month": successful.filter(created_at__date__gte=month_start).aggregate(total=Sum("amount"))["total"] or 0,
        "successful_payments": successful.count(),
        "pending_payments": qs.filter(status=AirtelTransaction.STATUS_PENDING).count(),
        "failed_payments": qs.filter(status=AirtelTransaction.STATUS_FAILED).count(),
        "expired_payments": qs.filter(status=AirtelTransaction.STATUS_EXPIRED).count(),
        "unknown_payments": qs.filter(status=AirtelTransaction.STATUS_UNKNOWN).count(),
        "callbacks_received": AirtelCallbackLog.objects.count(),
        "duplicate_callbacks": AirtelCallbackLog.objects.filter(duplicate=True).count(),
    }

    status = request.GET.get("status", "")
    if status:
        qs = qs.filter(status=status)

    page = Paginator(qs.select_related("contract").order_by("-created_at"), 25).get_page(request.GET.get("page"))
    callback_logs = AirtelCallbackLog.objects.select_related("transaction").order_by("-created_at")[:25]
    config = AirtelConfig.from_settings().safe_summary()

    return render(request, "payments/hq_airtel.html", {
        "summary": summary,
        "page_obj": page,
        "callback_logs": callback_logs,
        "config": config,
        "filter_status": status,
        "status_counts": qs.values("status").annotate(count=Count("id")),
        "page_title": "Airtel Money",
        "active_tab": "airtel",
    })
