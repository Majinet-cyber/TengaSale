from __future__ import annotations

import json
import secrets
import hashlib
import time
import uuid
import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.paginator import Paginator
from django.core.cache import cache
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import Resolver404, resolve
from django.utils import timezone
from django.utils.dateparse import parse_date
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
from .callback_diagnostics import extract_callback_source_ip

logger = logging.getLogger(__name__)
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
@transaction.non_atomic_requests
def airtel_callback(request):
    started = time.monotonic()
    headers = {key: value for key, value in request.headers.items()}
    request_id=(request.headers.get("X-Request-ID") or request.headers.get("X-Render-Request-ID") or request.headers.get("Traceparent") or str(uuid.uuid4()))[:120]
    provider_request_id=(request.headers.get("X-Airtel-Request-ID") or request.headers.get("X-Trace-ID") or request.headers.get("Uber-Trace-Id") or "")[:120]
    source_ip=extract_callback_source_ip(headers,request.META.get("REMOTE_ADDR",""))
    body_read_error=None
    try:
        raw_body=request.body or b""
    except Exception as exc:
        raw_body=b"";body_read_error=exc
    fingerprint=hashlib.sha256(raw_body).hexdigest()
    entry_metadata={"event":"CALLBACK_RECEIVED","request_id":request_id,"received_at":timezone.now().isoformat(),"method":request.method,"path":request.path,"content_type":request.content_type or "","body_size":len(raw_body),"body_sha256":fingerprint,"source_ip":source_ip,"user_agent":request.META.get("HTTP_USER_AGENT","")[:1000],"provider_request_id":provider_request_id}
    logger.info("CALLBACK_RECEIVED %s",json.dumps(entry_metadata,sort_keys=True))
    try:
        lower_headers = {str(key).lower(): str(value) for key, value in headers.items()}
        log = AirtelCallbackLog.objects.create(
            received_headers=_safe_callback_headers(headers),
            raw_body=raw_body.decode("utf-8", errors="replace"),
            request_path=request.path,
            query_string=request.META.get("QUERY_STRING", "")[:4000],
            request_method=request.method,
            content_type=(request.content_type or "")[:120],
            body_size=len(raw_body),
            source_ip=source_ip,
            forwarded_for=request.META.get("HTTP_X_FORWARDED_FOR", "")[:2000],
            real_ip=request.META.get("HTTP_X_REAL_IP", "")[:64],
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:1000],
            request_id=request_id,
            provider_request_id=provider_request_id,
            signature_present=any(lower_headers.get(name.lower()) for name in AirtelCallbackService.signature_headers),
            authentication_mode="PENDING",
            body_sha256=fingerprint,
            processing_state="RECEIVED",
        )
    except Exception as exc:
        failure={**entry_metadata,"event":"CALLBACK_EVIDENCE_INSERT_FAILED","exception_class":exc.__class__.__name__,"fallback_response_status":503}
        logger.critical("CALLBACK_EVIDENCE_INSERT_FAILED %s",json.dumps(failure,sort_keys=True))
        return JsonResponse({"ok": False, "message": "Callback evidence capture failed."}, status=503)
    try:
        auth_enabled=AirtelConfig.from_settings().callback_auth_enabled
        log.authentication_mode="CONFIGURED_UNVERIFIED_HMAC" if auth_enabled else "DISABLED_UAT"
    except AirtelConfigurationError:
        log.authentication_mode="CONFIGURATION_ERROR"
    if not _save_callback_progress(log,["authentication_mode"],"authentication_mode"):
        return JsonResponse({"ok":False,"message":"Callback evidence update failed.","log_id":log.id},status=503)
    if body_read_error:
        log.processing_state="INVALID_PAYLOAD";log.error_class=body_read_error.__class__.__name__;log.processing_error="Unable to read callback request body."
        if not _save_callback_progress(log,["processing_state","error_class","processing_error"],"body_read_failure"):
            return JsonResponse({"ok":False,"message":"Callback evidence update failed.","log_id":log.id},status=503)
        return _finish_callback_response(request,log,400,{"ok":False,"message":"Invalid callback body.","log_id":log.id},started)
    if request.method != "POST":
        log.processing_state="METHOD_NOT_ALLOWED"; log.processing_error="Airtel callback endpoint accepts POST only."
        if not _save_callback_progress(log,["processing_state","processing_error"],"method_rejection"):
            return JsonResponse({"ok":False,"message":"Callback evidence update failed.","log_id":log.id},status=503)
        return _finish_callback_response(request, log, 405, {"ok": False, "message": "Method not allowed.", "log_id": log.id}, started)
    content_type=(request.content_type or "").lower()
    if raw_body and content_type!="application/json" and not content_type.endswith("+json"):
        log.processing_state="INVALID_PAYLOAD";log.processing_error="Airtel callback Content-Type must be application/json."
        if not _save_callback_progress(log,["processing_state","processing_error"],"content_type_rejection"):
            return JsonResponse({"ok":False,"message":"Callback evidence update failed.","log_id":log.id},status=503)
        return _finish_callback_response(request,log,415,{"ok":False,"message":"Invalid callback Content-Type.","log_id":log.id},started)
    try:
        log, valid_body, status_code = AirtelCallbackService().handle_callback(raw_body, headers, evidence_log=log)
    except Exception as exc:
        logger.exception("airtel_callback_processing_failed request_id=%s", request.headers.get("X-Request-ID", ""))
        log.processing_state="PROCESSING_FAILED"; log.error_class=exc.__class__.__name__; log.processing_error=str(exc)[:1000] or "Unhandled callback processing error."
        if not _save_callback_progress(log,["processing_state","error_class","processing_error"],"processing_failure"):
            return JsonResponse({"ok":False,"message":"Callback evidence update failed.","log_id":log.id},status=503)
        return _finish_callback_response(request, log, 500, {"ok": False, "message": "Callback processing failed.", "log_id": log.id}, started)
    response_body = {"ok": status_code < 400, "provider": "airtel_money", "log_id": log.id}
    if not valid_body:
        response_body = {"ok": False, "message": "Invalid callback body.", "log_id": log.id}
    elif status_code in (401, 403):
        response_body = {"ok": False, "message": "Invalid callback signature.", "log_id": log.id}
    elif status_code >= 400:
        response_body = {"ok": False, "message": log.processing_error or "Callback rejected.", "log_id": log.id}
    return _finish_callback_response(request, log, status_code, response_body, started)


def _safe_callback_headers(headers):
    selected = {"content-type", "user-agent", "cf-connecting-ip", "cf-ipcountry", "true-client-ip", "x-real-ip", "x-forwarded-for", "x-request-id", "x-render-request-id", "x-airtel-request-id", "x-trace-id", "uber-trace-id", "traceparent"}
    signature_names = {name.lower() for name in AirtelCallbackService.signature_headers}
    result = {}
    for key, value in headers.items():
        lower = str(key).lower()
        if lower in signature_names:
            result[str(key)] = "***present***"
        elif lower in selected:
            result[str(key)] = str(value)[:2000]
    return result


def _finish_callback_response(request, log, status_code, response_body, started):
    log.response_status=status_code; log.response_body=response_body
    log.processing_duration_ms=max(0, int((time.monotonic()-started)*1000))
    log.processed_at=timezone.now()
    if not _save_callback_progress(log,["response_status","response_body","processing_duration_ms","processed_at"],"response_finalization"):
        return JsonResponse({"ok":False,"message":"Callback evidence finalization failed.","log_id":log.id},status=503)
    return JsonResponse(response_body, status=status_code)


def _save_callback_progress(log,fields,phase):
    try:
        log.save(update_fields=fields)
        return True
    except Exception as exc:
        metadata={"event":"CALLBACK_EVIDENCE_UPDATE_FAILED","callback_id":log.pk,"request_id":log.request_id,"phase":phase,"exception_class":exc.__class__.__name__,"fallback_response_status":503}
        logger.critical("CALLBACK_EVIDENCE_UPDATE_FAILED %s",json.dumps(metadata,sort_keys=True))
        return False


@csrf_exempt
@require_http_methods(["GET", "POST", "HEAD"])
def airtel_callback_health(request):
    correlation_id=str(uuid.uuid4())
    source=request.META.get("REMOTE_ADDR", "unknown")
    key=f"airtel-callback-health:{source}"
    count=cache.get(key, 0)
    if count >= 30:
        return JsonResponse({"ok": False, "request_id": correlation_id}, status=429)
    cache.set(key, count+1, 60)
    logger.info("airtel_callback_health request_id=%s method=%s", correlation_id, request.method)
    return JsonResponse({"ok": True, "service": "airtel-callback-connectivity", "request_id": correlation_id})


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
        "callback_url_exact": getattr(settings, "AIRTEL_CALLBACK_URL", "").strip()
        == "https://tengasale.onrender.com/api/payments/airtel/callback/",
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
            idempotency_key=request.headers.get("Idempotency-Key") or data.get("idempotency_key"),
            retry_of_reference=data.get("retry_of") or data.get("retry_of_reference"),
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
        "status_token": tx.status_token,
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
    raw_body=request.body or b""
    log=AirtelCallbackLog.objects.create(received_headers=_safe_callback_headers(headers),raw_body=raw_body.decode("utf-8",errors="replace"),request_path=request.path,request_method=request.method,content_type=request.content_type or "",body_size=len(raw_body),source_ip=extract_callback_source_ip(headers,request.META.get("REMOTE_ADDR","")),user_agent=request.META.get("HTTP_USER_AGENT","")[:1000],body_sha256=hashlib.sha256(raw_body).hexdigest(),authentication_mode="SIMULATION",processing_state="RECEIVED")
    log, valid_body, status_code = AirtelCallbackService().handle_callback(raw_body, headers, evidence_log=log)
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
    user = getattr(request, "user", None)
    staff = bool(user and user.is_authenticated and user.is_staff)
    if not staff and not secrets.compare_digest(str(request.GET.get("token", "")), tx.status_token):
        return JsonResponse({"success": False, "error": "Not found."}, status=404)
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
            "Airtel Money request sent. Complete any prompt on the Airtel phone. Awaiting final confirmation; do not pay again.",
            False,
        ),
        AirtelTransaction.STATUS_PENDING: (
            "pending_customer_approval",
            "Airtel Money request sent. Complete any prompt on the Airtel phone. Awaiting final confirmation; do not pay again.",
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
    if tx.potential_overpayment:
        # A confirmed success arrived after a retry-chain sibling already posted
        # this customer action. This is neither a routine pending state nor a
        # normal success — it must be shown as under manual review, and the
        # customer must never be encouraged to pay again while it is unresolved.
        public_status = "manual_review"
        message = "This payment is under manual review by TengaSale. Do not make another payment while this is being checked."
        final = False
    elif not final and tx.reconciliation_required:
        public_status = "reconciliation_required"
        message = "Airtel reported payment activity, but final confirmation has not reached TengaSale. Do not pay again."
    elif not final and tx.created_at and (timezone.now() - tx.created_at).total_seconds() >= int(getattr(settings, "AIRTEL_PENDING_RECONCILIATION_MINUTES", 10)) * 60:
        public_status = "confirmation_delayed"
        message = "Do not submit another payment. Use the reference below if you contact support."
    if portal_tx and portal_tx.status == PaymentTransaction.STATUS_CANCELLED:
        public_status = "cancelled"
        message = "This payment request was cancelled. No money has been applied to this contract."
        final = True

    state_ui = {
        "request_sending": ("Sending payment request", 1),
        "request_sent": ("Airtel Money request sent", 2),
        "pending_customer_approval": ("Airtel Money request sent", 2),
        "pending_provider_confirmation": ("Waiting for Airtel confirmation", 3),
        "confirmation_delayed": ("Your payment is still being confirmed", 3),
        "reconciliation_required": ("Awaiting Airtel confirmation", 3),
        "successful": ("Payment confirmed", 3),
        "failed": ("Payment failed", 3),
        "cancelled": ("Payment cancelled", 3),
        "expired": ("Payment request expired", 3),
        "reversed": ("Payment reversed", 3),
        "manual_review": ("Payment under review", 3),
    }
    status_label, current_step = state_ui.get(public_status, ("Still confirming payment", 3))
    # may_retry() is the single source of truth for retry eligibility: it is
    # True for definite terminal failures (FAILED/EXPIRED/UNKNOWN) *and* for an
    # attempt that is nominally still PENDING/INITIATED but has exceeded its
    # active lifetime (a stale attempt the customer never got confirmation
    # for). A fresh, still-active attempt never qualifies — unsafe immediate
    # retry stays blocked while confirmation could still legitimately arrive.
    can_retry = tx.may_retry()
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
        "can_retry": can_retry,
    }


@require_http_methods(["GET"])
def payment_transaction_status(request, transaction_id):
    tx = get_object_or_404(
        AirtelTransaction.objects.select_related("contract", "payment_transaction"),
        internal_reference=transaction_id,
    )
    if not secrets.compare_digest(str(request.GET.get("token", "")), tx.status_token):
        return JsonResponse({"success": False, "error": "Not found."}, status=404)
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
    callback_qs = AirtelCallbackLog.objects.select_related("transaction").order_by("-created_at")
    callback_filters = {
        "callback_date": request.GET.get("callback_date", "").strip(),
        "callback_status": request.GET.get("callback_status", "").strip(),
        "airtel_money_id": request.GET.get("airtel_money_id", "").strip(),
        "provider_transaction_id": request.GET.get("provider_transaction_id", "").strip(),
        "subscriber": request.GET.get("subscriber", "").strip(),
        "tengasale_reference": request.GET.get("tengasale_reference", "").strip(),
        "processing_state": request.GET.get("processing_state", "").strip(),
    }
    callback_date=parse_date(callback_filters["callback_date"])
    if callback_date: callback_qs=callback_qs.filter(created_at__date=callback_date)
    if callback_filters["callback_status"]: callback_qs=callback_qs.filter(extracted_status=callback_filters["callback_status"])
    if callback_filters["airtel_money_id"]: callback_qs=callback_qs.filter(extracted_airtel_money_id__icontains=callback_filters["airtel_money_id"])
    if callback_filters["provider_transaction_id"]: callback_qs=callback_qs.filter(provider_transaction_id__icontains=callback_filters["provider_transaction_id"])
    if callback_filters["subscriber"]: callback_qs=callback_qs.filter(extracted_subscriber__endswith=callback_filters["subscriber"][-4:])
    if callback_filters["tengasale_reference"]: callback_qs=callback_qs.filter(Q(transaction__internal_reference__icontains=callback_filters["tengasale_reference"])|Q(matched_identifier__icontains=callback_filters["tengasale_reference"]))
    if callback_filters["processing_state"]: callback_qs=callback_qs.filter(processing_state=callback_filters["processing_state"])
    callback_logs = callback_qs[:100]
    pending_timeout=int(getattr(settings,"AIRTEL_PENDING_CALLBACK_TIMEOUT_MINUTES",30))
    alert_cutoff=timezone.now()-timedelta(minutes=pending_timeout)
    alerts={
        "success_unresolved":AirtelCallbackLog.objects.filter(extracted_status=AirtelTransaction.STATUS_SUCCESS,processing_state__in=["UNMATCHED","AMBIGUOUS"]).count(),
        "processing_failed":AirtelCallbackLog.objects.filter(processing_state="PROCESSING_FAILED").count(),
        "pending_without_callback":AirtelTransaction.objects.filter(status__in=[AirtelTransaction.STATUS_INITIATED,AirtelTransaction.STATUS_PENDING],created_at__lte=alert_cutoff,callback_logs__isnull=True).count(),
        "pending_timeout_minutes":pending_timeout,
    }
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
        "callback_filters": callback_filters,
        "callback_states": ["RECEIVED","METHOD_NOT_ALLOWED","INVALID_PAYLOAD","AUTH_FAILED","UNMATCHED","AMBIGUOUS","MATCHED","PROCESSED","DUPLICATE","PROCESSING_FAILED"],
        "alerts": alerts,
        "config": config,
        "filter_status": status,
        "status_counts": qs.values("status").annotate(count=Count("id")),
        "page_title": "Airtel Money",
        "active_tab": "airtel",
    })
