from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.paginator import Paginator
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Count, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import Resolver404, resolve
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from contracts.models import Contract
from portal.models import PaymentContract, PaymentTransaction
from portal.services import resolve_payable_contract

from .airtel_client import AirtelConfig, AirtelConfigurationError
from .airtel_services import (
    AirtelCallbackService,
    AirtelCollectionService,
    AirtelTransactionEnquiryService,
    mask_msisdn,
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
    query = (contract_number or "").strip()
    if not query:
        return None

    result = resolve_payable_contract(query, country="MW")
    portal_contract = result.contract
    if portal_contract:
        balance = max((portal_contract.total_amount or Decimal("0")) - (portal_contract.amount_paid or Decimal("0")), Decimal("0"))
        next_payment = portal_contract.thirty_day_price or portal_contract.daily_price or Decimal("0")
        return {
            "balance": balance,
            "next_payment": next_payment,
            "due_date": portal_contract.due_date.isoformat() if portal_contract.due_date else "Not available",
        }

    legal_contract = Contract.objects.filter(contract_number__iexact=query).first()
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
    try:
        config = AirtelConfig.from_settings()
        environment = config.environment
        callback_configured = bool(getattr(settings, "AIRTEL_CALLBACK_URL", ""))
    except AirtelConfigurationError:
        environment = "invalid"
        callback_configured = bool(getattr(settings, "AIRTEL_CALLBACK_URL", ""))
    return JsonResponse({
        "status": "ok",
        "provider": "airtel_money",
        "environment": environment,
        "callback_configured": callback_configured,
    })


def _callback_route_resolves() -> bool:
    try:
        match = resolve("/api/payments/airtel/callback/")
    except Resolver404:
        return False
    return getattr(match.func, "__name__", "") == "airtel_callback"


def _database_ready() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return True
    except Exception:
        return False


def _migrations_applied() -> bool:
    try:
        executor = MigrationExecutor(connection)
        return not executor.migration_plan(executor.loader.graph.leaf_nodes())
    except Exception:
        return False


def airtel_readiness_status() -> tuple[dict, int]:
    missing = []
    try:
        config = AirtelConfig.from_settings()
        config_error = ""
    except AirtelConfigurationError as exc:
        config = None
        config_error = str(exc)
        missing.append("AIRTEL_ENVIRONMENT")

    callback_route_resolves = _callback_route_resolves()
    database_ready = _database_ready()
    migrations_applied = _migrations_applied() if database_ready else False

    if config is None:
        payload = {
            "status": "not_ready",
            "environment": "invalid",
            "base_url": "",
            "collections_enabled": False,
            "dry_run": True,
            "callback_url": getattr(settings, "AIRTEL_CALLBACK_URL", ""),
            "callback_route_resolves": callback_route_resolves,
            "database_ready": database_ready,
            "migrations_applied": migrations_applied,
            "missing": missing,
            "configuration_error": config_error,
        }
        return payload, 503

    checks = {
        "client_id_configured": bool(config.client_id),
        "client_secret_configured": bool(config.client_secret),
        "private_key_configured": bool(config.private_key),
        "merchant_code_configured": bool(config.merchant_code),
        "allowed_test_numbers_configured": bool(config.allowed_test_msisdns),
        "callback_configured": bool(getattr(settings, "AIRTEL_CALLBACK_URL", "")),
        "callback_url_exact": getattr(settings, "AIRTEL_CALLBACK_URL", "")
        == "https://tengasale-api.onrender.com/api/payments/airtel/callback/",
        "country_is_mw": config.country == "MW",
        "currency_is_mwk": config.currency == "MWK",
        "test_max_configured": bool(
            config.test_max_amount and Decimal(str(config.test_max_amount)) > 0
        ),
        "production_disabled": not config.production_enabled,
        "staging_environment": not config.is_production,
    }
    for key, ok in checks.items():
        if not ok:
            missing.append(key.replace("_configured", "").upper())
    if not callback_route_resolves:
        missing.append("CALLBACK_ROUTE")
    if not database_ready:
        missing.append("DATABASE")
    if not migrations_applied:
        missing.append("MIGRATIONS")

    ready = not missing
    payload = {
        "status": "ready" if ready else "not_ready",
        "provider": "airtel_money",
        "environment": config.environment,
        "production_enabled": config.production_enabled,
        "base_url": config.base_url,
        "collections_enabled": config.collections_enabled,
        "dry_run": config.dry_run,
        "callback_url": getattr(settings, "AIRTEL_CALLBACK_URL", ""),
        "callback_route_resolves": callback_route_resolves,
        "client_id_configured": checks["client_id_configured"],
        "client_secret_configured": checks["client_secret_configured"],
        "private_key_configured": checks["private_key_configured"],
        "callback_key_configured": checks["private_key_configured"],
        "merchant_code_configured": checks["merchant_code_configured"],
        "allowed_test_numbers_configured": checks["allowed_test_numbers_configured"],
        "allowed_test_msisdns_configured": checks["allowed_test_numbers_configured"],
        "test_max_amount": config.test_max_amount,
        "test_max_configured": checks["test_max_configured"],
        "country": config.country,
        "currency": config.currency,
        "database_ready": database_ready,
        "migrations_applied": migrations_applied,
        "missing": missing,
    }
    return payload, 200 if ready else 503


@require_GET
def airtel_readiness(request):
    payload, status_code = airtel_readiness_status()
    return JsonResponse(payload, status=status_code)


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
    else:
        contract_query = (
            data.get("contract")
            or data.get("contract_number")
            or data.get("contract_query")
            or data.get("reference")
            or data.get("q")
            or ""
        )
        if contract_query:
            result = resolve_payable_contract(contract_query, country=data.get("country", "MW"))
            if not result.contract:
                message = "No matching payable contract was found."
                if result.status == "ambiguous":
                    message = "More than one contract matched that reference. Contact support to verify it."
                return JsonResponse({"success": False, "error": message, "reason": result.reason}, status=400)
            if not result.payable:
                message = "This contract cannot currently accept payments."
                if result.status == "fully_paid":
                    message = "Contract fully paid. No payment is currently required."
                return JsonResponse({"success": False, "error": message, "reason": result.reason}, status=400)
            contract = result.contract

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
    except AirtelConfigurationError as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=503)
    except ValueError as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=400)

    return JsonResponse({
        "success": True,
        "provider": "airtel_money",
        "internal_reference": tx.internal_reference,
        "status": tx.status,
        "dry_run": tx.status == AirtelTransaction.STATUS_DRY_RUN,
        "message": "Dry-run Airtel request recorded; nothing was sent." if tx.status == AirtelTransaction.STATUS_DRY_RUN else "Payment request sent. Please approve on your phone.",
    })


@csrf_exempt
@require_POST
def airtel_simulate_callback(request):
    try:
        config = AirtelConfig.from_settings()
    except AirtelConfigurationError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=503)
    if config.is_production:
        return JsonResponse({"ok": False, "error": "Airtel callback simulation is disabled in production."}, status=403)

    user = getattr(request, "user", None)
    secret = getattr(settings, "AIRTEL_SIMULATION_SECRET", "")
    provided_secret = request.headers.get("X-Airtel-Test-Secret", "")
    authorized = bool(user and user.is_authenticated and user.is_staff) or bool(secret and provided_secret == secret)
    if not authorized:
        return JsonResponse({"ok": False, "error": "Unauthorized."}, status=403)

    headers = {key: value for key, value in request.headers.items()}
    headers["X-Airtel-Simulated"] = "true"
    log, valid_body, status_code = AirtelCallbackService().handle_callback(request.body, headers)
    if not valid_body:
        return JsonResponse({"ok": False, "simulated": True, "message": "Invalid callback body.", "log_id": log.id}, status=400)
    if status_code in (401, 403):
        return JsonResponse({"ok": False, "simulated": True, "message": "Invalid callback signature.", "log_id": log.id}, status=status_code)
    if status_code >= 400:
        return JsonResponse({"ok": False, "simulated": True, "message": log.processing_error or "Callback rejected.", "log_id": log.id}, status=status_code)
    return JsonResponse({"ok": True, "simulated": True, "provider": "airtel_money", "log_id": log.id}, status=200)


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


def customer_payment_status_payload(tx: AirtelTransaction) -> dict:
    contract = tx.contract
    portal_tx = tx.payment_transaction
    success_applied = bool(tx.processed_success_at or tx.repayment_posted)
    status_map = {
        AirtelTransaction.STATUS_FAILED: (
            "failed",
            "Payment failed or was declined. No money has been applied to this contract.",
            True,
        ),
        AirtelTransaction.STATUS_EXPIRED: (
            "expired",
            "Payment request expired before it was approved. No money has been applied to this contract.",
            True,
        ),
        AirtelTransaction.STATUS_REVERSED: (
            "reversed",
            "Payment was reversed. Please contact support before trying again.",
            True,
        ),
        AirtelTransaction.STATUS_DRY_RUN: (
            "request_sending",
            "Your payment request is being prepared. No payment has been applied yet.",
            False,
        ),
        AirtelTransaction.STATUS_INITIATED: (
            "request_sent",
            "Airtel Money request has been sent. Please approve it on your phone.",
            False,
        ),
        AirtelTransaction.STATUS_PENDING: (
            "pending_customer_approval",
            "Approve the prompt on your phone. Your balance updates only after Airtel confirms.",
            False,
        ),
    }
    public_status, message, final = status_map.get(
        tx.status,
        (
            "pending_provider_confirmation",
            "Airtel is still confirming this payment. Do not make another payment yet.",
            False,
        ),
    )
    if tx.status == AirtelTransaction.STATUS_SUCCESS:
        if success_applied or contract is None:
            public_status = "successful"
            message = "Payment confirmed. Your contract balance has been updated."
            final = True
        else:
            public_status = "pending_provider_confirmation"
            message = "Airtel returned a success status and TengaSale is finishing confirmation. Do not pay again yet."
            final = False
    if portal_tx and portal_tx.status == PaymentTransaction.STATUS_CANCELLED:
        public_status = "cancelled"
        message = "This payment request was cancelled. No money has been applied to this contract."
        final = True

    state_ui = {
        "request_sending": ("Sending payment request", 1),
        "request_sent": ("Confirm on your phone", 2),
        "pending_customer_approval": ("Confirm on your phone", 2),
        "pending_provider_confirmation": ("Waiting for Airtel confirmation", 3),
        "successful": ("Payment confirmed", 3),
        "failed": ("Payment failed", 3),
        "cancelled": ("Payment cancelled", 3),
        "expired": ("Payment request expired", 3),
        "reversed": ("Payment reversed", 3),
    }
    status_label, current_step = state_ui.get(public_status, ("Still confirming payment", 3))
    return {
        "success": True,
        "transaction_id": tx.internal_reference,
        "provider": "airtel_money",
        "provider_label": "Airtel Money",
        "status": public_status,
        "status_label": status_label,
        "current_step": current_step,
        "message": message,
        "final": final,
        "is_final": final,
        "amount": str(tx.amount),
        "currency": tx.currency,
        "masked_phone": mask_msisdn(tx.customer_msisdn),
        "airtel_money_id": tx.airtel_money_id or tx.airtel_transaction_id or "",
        "contract_number": contract.contract_number if contract else "",
        "payg_number": contract.payg_number if contract else "",
        "portal_transaction_status": portal_tx.status if portal_tx else "",
        "balance_after": str(portal_tx.balance_after) if portal_tx and portal_tx.balance_after is not None else "",
        "payment_type": (
            "deposit" if tx.purpose == AirtelTransaction.PURPOSE_DEPOSIT
            else "partial_repayment" if tx.full_repayment_days_covered == 0
            else "repayment"
        ),
        "full_repayment_days_covered": tx.full_repayment_days_covered,
        "partial_credit_balance": str(tx.partial_credit_balance),
        "updated_at": tx.updated_at.isoformat() if tx.updated_at else "",
        "created_at": tx.created_at.isoformat() if tx.created_at else "",
        "confirmed_at": tx.completed_at.isoformat() if tx.completed_at else "",
        "can_check_again": not final,
    }


@require_http_methods(["GET"])
def payment_transaction_status(request, transaction_id):
    tx = get_object_or_404(
        AirtelTransaction.objects.select_related("contract", "payment_transaction"),
        internal_reference=transaction_id,
    )
    if request.GET.get("enquire") == "1" and tx.status not in {
        AirtelTransaction.STATUS_SUCCESS,
        AirtelTransaction.STATUS_FAILED,
        AirtelTransaction.STATUS_EXPIRED,
        AirtelTransaction.STATUS_REVERSED,
    }:
        try:
            tx = AirtelTransactionEnquiryService().enquire(tx.internal_reference)
        except Exception:
            tx.refresh_from_db()
    return JsonResponse(customer_payment_status_payload(tx))


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
    try:
        config = AirtelConfig.from_settings().safe_summary()
    except AirtelConfigurationError as exc:
        config = {
            "environment": "invalid",
            "env": "invalid",
            "base_url": "",
            "country": "",
            "currency": "",
            "token_configured": False,
            "dry_run": True,
            "collections_enabled": False,
            "callback_auth_enabled": False,
            "configuration_error": str(exc),
        }

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
