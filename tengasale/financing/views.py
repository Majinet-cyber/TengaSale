import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from accounts.utils import get_tengasale_role

from . import services
from .forms import CustomerForm, DeviceForm, FinancingContractForm, PaymentRecordForm, PaymentRejectForm, UnlockPinForm
from .models import Customer, Device, DeviceCommand, FinancingContract, PaymentRecord, UnlockToken
from .permissions import can_manage_financing


def _forbidden_response(request):
    if request.path.startswith("/api/") or request.headers.get("accept") == "application/json":
        return JsonResponse({"success": False, "message": "Permission denied.", "data": {}}, status=403)
    messages.error(request, "That financing action is not available for your role.")
    return redirect("home")


def _json_payload(request):
    if request.content_type == "application/json":
        try:
            return json.loads(request.body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return {}
    return request.POST


@login_required
def financing_dashboard(request):
    if not can_manage_financing(request.user):
        return _forbidden_response(request)
    contracts = FinancingContract.objects.select_related("customer", "device").order_by("-created_at")[:10]
    overdue_contracts = FinancingContract.objects.select_related("customer", "device").filter(
        status__in=[FinancingContract.STATUS_OVERDUE, FinancingContract.STATUS_LOCKED, FinancingContract.STATUS_DEFAULTED]
    )[:10]
    pending_payments = PaymentRecord.objects.select_related("customer", "contract", "contract__device").filter(
        verification_status=PaymentRecord.STATUS_PENDING
    )[:10]
    commands = DeviceCommand.objects.select_related("device", "contract", "contract__customer").order_by("-created_at")[:10]
    return render(
        request,
        "financing/dashboard.html",
        {
            "contracts": contracts,
            "overdue_contracts": overdue_contracts,
            "pending_payments": pending_payments,
            "commands": commands,
        },
    )


@login_required
def customers(request):
    if not can_manage_financing(request.user):
        return _forbidden_response(request)
    if request.method == "POST":
        form = CustomerForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Customer created.")
            return redirect("financing_customers")
    else:
        form = CustomerForm()
    rows = Customer.objects.order_by("-created_at")[:50]
    return render(request, "financing/customers.html", {"form": form, "customers": rows})


@login_required
def devices(request):
    if not can_manage_financing(request.user):
        return _forbidden_response(request)
    if request.method == "POST":
        form = DeviceForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Device registered.")
            return redirect("financing_devices")
    else:
        form = DeviceForm()
    rows = Device.objects.select_related("assigned_customer").order_by("-created_at")[:50]
    return render(request, "financing/devices.html", {"form": form, "devices": rows})


@login_required
def contracts(request):
    if not can_manage_financing(request.user):
        return _forbidden_response(request)
    if request.method == "POST":
        form = FinancingContractForm(request.POST)
        if form.is_valid():
            contract = form.save(commit=False)
            contract.created_by = request.user
            contract.save()
            services.build_repayment_schedule(contract)
            messages.success(request, "Financing contract created.")
            return redirect("financing_contracts")
    else:
        form = FinancingContractForm()
    rows = FinancingContract.objects.select_related("customer", "device", "created_by")
    if not (request.user.is_staff or request.user.is_superuser or get_tengasale_role(request.user) == "hq"):
        rows = rows.filter(created_by=request.user)
    portfolio_filter = request.GET.get("portfolio", "").strip().lower()
    if portfolio_filter == "active":
        rows = rows.filter(status=FinancingContract.STATUS_ACTIVE)
    elif portfolio_filter == "locked":
        rows = rows.filter(device__status=Device.STATUS_LOCKED)
    elif portfolio_filter == "overdue":
        rows = rows.filter(status=FinancingContract.STATUS_OVERDUE)
    elif portfolio_filter not in {"", "financed"}:
        portfolio_filter = ""
    rows = rows.order_by("-created_at")[:50]
    return render(request, "financing/contracts.html", {"form": form, "contracts": rows, "portfolio_filter": portfolio_filter})


@login_required
def pending_payments(request):
    if not can_manage_financing(request.user):
        return _forbidden_response(request)
    payments = PaymentRecord.objects.select_related("customer", "contract", "contract__device").filter(
        verification_status=PaymentRecord.STATUS_PENDING
    )
    return render(request, "financing/pending_payments.html", {"payments": payments})


@login_required
def payment_detail(request, payment_id):
    if not can_manage_financing(request.user):
        return _forbidden_response(request)
    payment = get_object_or_404(
        PaymentRecord.objects.select_related("customer", "contract", "contract__device"),
        id=payment_id,
    )
    reject_form = PaymentRejectForm(instance=payment)
    return render(request, "financing/payment_detail.html", {"payment": payment, "reject_form": reject_form})


@login_required
@require_POST
def approve_payment(request, payment_id):
    if not can_manage_financing(request.user):
        return _forbidden_response(request)
    payment = get_object_or_404(PaymentRecord, id=payment_id)
    services.verify_payment(payment, verified_by=request.user)
    messages.success(request, "Payment approved. Unlock PIN generated and mock unlock authorized.")
    return redirect("financing_payment_detail", payment_id=payment.id)


@login_required
@require_POST
def reject_payment(request, payment_id):
    if not can_manage_financing(request.user):
        return _forbidden_response(request)
    payment = get_object_or_404(PaymentRecord, id=payment_id)
    form = PaymentRejectForm(request.POST, instance=payment)
    reason = form.data.get("rejection_reason", "")
    services.reject_payment(payment, rejected_by=request.user, reason=reason)
    messages.error(request, "Payment rejected.")
    return redirect("financing_payment_detail", payment_id=payment.id)


@login_required
def submit_payment(request, contract_id):
    contract = get_object_or_404(FinancingContract.objects.select_related("customer", "device"), id=contract_id)
    if request.method == "POST":
        form = PaymentRecordForm(request.POST, request.FILES, initial={"contract": contract, "customer": contract.customer})
        if form.is_valid():
            payment = form.save(commit=False)
            payment.contract = contract
            payment.customer = contract.customer
            payment.save()
            messages.success(request, "Payment proof submitted for verification.")
            return redirect("customer_device_portal", contract_id=contract.id)
    else:
        form = PaymentRecordForm(initial={"contract": contract, "customer": contract.customer})
    return render(request, "financing/submit_payment.html", {"contract": contract, "form": form})


@login_required
@require_POST
def request_device_command(request, contract_id, command_type):
    if not can_manage_financing(request.user):
        return _forbidden_response(request)
    contract = get_object_or_404(FinancingContract.objects.select_related("device", "customer"), id=contract_id)
    command = services.issue_device_command(contract, command_type, created_by=request.user)
    if command.status == DeviceCommand.STATUS_SUCCESSFUL:
        messages.success(request, f"{command.get_command_type_display()} command completed in mock provider.")
    else:
        messages.error(request, command.error_message or "Device command failed.")
    return redirect("financing_dashboard")


@login_required
def command_history(request):
    if not can_manage_financing(request.user):
        return _forbidden_response(request)
    commands = DeviceCommand.objects.select_related("device", "contract", "contract__customer").order_by("-created_at")[:100]
    return render(request, "financing/command_history.html", {"commands": commands})


def customer_device_portal(request, contract_id):
    contract = get_object_or_404(FinancingContract.objects.select_related("customer", "device"), id=contract_id)
    next_schedule = contract.repayment_schedule.exclude(status="paid").order_by("due_date", "id").first()
    latest_token = contract.latest_unlock_token
    return render(
        request,
        "financing/customer_device.html",
        {
            "contract": contract,
            "device": contract.device,
            "next_schedule": next_schedule,
            "latest_token": latest_token,
        },
    )


@require_POST
def customer_unlock_pin(request, contract_id):
    contract = get_object_or_404(FinancingContract.objects.select_related("customer", "device"), id=contract_id)
    form = UnlockPinForm(request.POST)
    if form.is_valid():
        token_value = form.cleaned_data["token"].strip()
        token = UnlockToken.objects.filter(contract=contract, token=token_value).order_by("-created_at").first()
        if token and token.is_valid_now:
            token.mark_used()
            messages.success(request, "PIN accepted. Unlock authorized; native device integration is pending.")
        else:
            messages.error(request, "That PIN is not valid for this device.")
    return redirect("customer_device_portal", contract_id=contract.id)


@login_required
@require_POST
def api_register_device(request):
    if not can_manage_financing(request.user):
        return _forbidden_response(request)
    data = _json_payload(request)
    contract = get_object_or_404(FinancingContract.objects.select_related("device"), id=data.get("contract_id"))
    command = services.issue_device_command(contract, DeviceCommand.TYPE_REGISTER, created_by=request.user)
    return _command_json(command, "Device registration requested.")


@login_required
@require_POST
def api_lock_device(request):
    if not can_manage_financing(request.user):
        return _forbidden_response(request)
    data = _json_payload(request)
    contract = _contract_from_payload(data)
    command = services.issue_device_command(contract, DeviceCommand.TYPE_LOCK, created_by=request.user)
    return _command_json(command, "Device lock requested.")


@login_required
@require_POST
def api_unlock_device(request):
    if not can_manage_financing(request.user):
        return _forbidden_response(request)
    data = _json_payload(request)
    contract = _contract_from_payload(data)
    command = services.issue_device_command(contract, DeviceCommand.TYPE_UNLOCK, created_by=request.user)
    return _command_json(command, "Device unlock requested.")


@login_required
@require_POST
def api_device_status_post(request):
    if not can_manage_financing(request.user):
        return _forbidden_response(request)
    data = _json_payload(request)
    contract = _contract_from_payload(data)
    command = services.issue_device_command(contract, DeviceCommand.TYPE_REFRESH_STATUS, created_by=request.user)
    return _command_json(command, "Device status refreshed.")


@login_required
@require_POST
def api_verify_payment(request):
    if not can_manage_financing(request.user):
        return _forbidden_response(request)
    data = _json_payload(request)
    payment = get_object_or_404(PaymentRecord, id=data.get("payment_id"))
    payment, token = services.verify_payment(payment, verified_by=request.user)
    return JsonResponse(
        {
            "success": True,
            "message": "Payment verified and unlock authorization generated.",
            "data": {"payment_id": payment.id, "unlock_token_id": token.id, "valid_until": token.valid_until.isoformat()},
        }
    )


@login_required
@require_GET
def api_contract_status(request, contract_id):
    if not can_manage_financing(request.user):
        return _forbidden_response(request)
    contract = get_object_or_404(FinancingContract.objects.select_related("customer", "device"), id=contract_id)
    return JsonResponse(
        {
            "success": True,
            "message": "Contract status returned.",
            "data": _contract_status_data(contract),
        }
    )


@login_required
@require_GET
def api_device_status_get(request, device_id):
    if not can_manage_financing(request.user):
        return _forbidden_response(request)
    device = get_object_or_404(Device, id=device_id)
    return JsonResponse(
        {
            "success": True,
            "message": "Device status returned.",
            "data": {"device_id": device.id, "status": device.status, "imei": device.masked_imei},
        }
    )


def _contract_from_payload(data):
    if data.get("contract_id"):
        return get_object_or_404(FinancingContract.objects.select_related("device", "customer"), id=data.get("contract_id"))
    device = get_object_or_404(Device, id=data.get("device_id"))
    return get_object_or_404(FinancingContract.objects.select_related("device", "customer"), device=device)


def _command_json(command, message):
    return JsonResponse(
        {
            "success": command.status == DeviceCommand.STATUS_SUCCESSFUL,
            "message": message,
            "data": {
                "command_id": command.id,
                "status": command.status,
                "response": command.response_payload,
                "error": command.error_message,
            },
        },
        status=200 if command.status == DeviceCommand.STATUS_SUCCESSFUL else 400,
    )


def _contract_status_data(contract):
    latest_token = contract.latest_unlock_token
    return {
        "contract_id": contract.id,
        "customer": contract.customer.full_name,
        "device": str(contract.device),
        "contract_status": contract.status,
        "device_status": contract.device.status,
        "next_due_date": contract.next_due_date.isoformat() if contract.next_due_date else None,
        "lock_date": contract.lock_date.isoformat() if contract.lock_date else None,
        "amount_due": str(contract.amount_due),
        "latest_unlock_status": latest_token.status if latest_token else None,
    }
