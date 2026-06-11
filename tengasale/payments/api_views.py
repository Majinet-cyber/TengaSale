from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from django.core.paginator import Paginator
from django.db.models import Count, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from portal.models import PaymentContract

from .airtel_client import AirtelConfig
from .airtel_services import (
    AirtelCallbackService,
    AirtelCollectionService,
    AirtelTransactionEnquiryService,
)
from .models import AirtelCallbackLog, AirtelTransaction
from .views import _hq_or_finance


def _json_body(request) -> tuple[dict, JsonResponse | None]:
    try:
        return json.loads(request.body.decode("utf-8")) if request.body else {}, None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}, JsonResponse({"success": False, "error": "Invalid JSON body."}, status=400)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def airtel_callback(request):
    if request.method == "GET":
        return JsonResponse({"ok": True, "provider": "airtel", "endpoint": "callback"})

    headers = {key: value for key, value in request.headers.items()}
    log, valid_body = AirtelCallbackService().handle_callback(request.body, headers)
    if not valid_body:
        return JsonResponse({"ok": False, "message": "Invalid callback body.", "log_id": log.id}, status=400)
    return JsonResponse({"ok": True, "provider": "airtel", "log_id": log.id}, status=200)


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
