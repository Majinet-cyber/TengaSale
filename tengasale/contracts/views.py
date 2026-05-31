import logging
from decimal import Decimal

from django.conf import settings as dj_settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import merchant_required
from applications.models import FinancingApplication
from commissions.services import process_contract_completion

from .forms import ContractSignatureForm, ImeiForm, MerchantTermsForm
from .models import Contract

logger = logging.getLogger(__name__)


def can_access_contract_flow(user, application):
    return application.created_by_id == user.id


@merchant_required
def contract_terms(request, app_id):
    application = get_object_or_404(
        FinancingApplication.objects.select_related("created_by", "deal"),
        id=app_id,
    )
    if not can_access_contract_flow(request.user, application):
        raise PermissionDenied
    if application.status not in ["approved", "contract_terms", "contract_signature"]:
        return redirect(application.get_continue_url())

    contract = getattr(application, "contract", None)
    display_contract = contract or Contract(
        application=application,
        merchant=application.created_by,
        customer_name=application.customer_name,
        customer_phone=application.customer_phone,
        national_id=application.national_id,
        deal_name=str(application.deal) if application.deal_id else "",
        cash_price=application.selected_cash_price or Decimal("0"),
        total_loan=application.calculated_total_loan or Decimal("0"),
        deposit_amount=application.calculated_deposit_amount or Decimal("0"),
        monthly_payment=application.calculated_monthly_payment or Decimal("0"),
        daily_payment=application.calculated_daily_payment or Decimal("0"),
    )

    if request.method == "POST":
        form = MerchantTermsForm(request.POST)
        if form.is_valid():
            contract, _ = Contract.from_application(application)
            contract.terms_accepted_by_merchant = True
            contract.status = Contract.STATUS_TERMS_ACCEPTED
            contract.save(update_fields=["terms_accepted_by_merchant", "status", "updated_at"])
            application.status = "contract_signature"
            application.save(update_fields=["status"])
            return redirect("contract_signature", contract_id=contract.id)
    else:
        form = MerchantTermsForm()

    return render(request, "contracts/terms.html", {"application": application, "contract": display_contract, "form": form})


@merchant_required
def contract_signature(request, contract_id):
    contract = get_object_or_404(Contract.objects.select_related("application", "merchant"), id=contract_id)
    application = contract.application
    if not can_access_contract_flow(request.user, application):
        raise PermissionDenied

    if request.method == "POST":
        form = ContractSignatureForm(request.POST, instance=contract)
        if form.is_valid():
            contract = form.save(commit=False)
            contract.customer_contract_signature.save(form.signature_file.name, form.signature_file, save=False)
            contract.customer_terms_accepted = True
            contract.status = Contract.STATUS_SIGNED
            contract.save()
            application.status = "imei_entry"
            application.save(update_fields=["status"])
            return redirect("contract_imei", contract_id=contract.id)
    else:
        form = ContractSignatureForm(instance=contract)

    return render(request, "contracts/signature.html", {"contract": contract, "application": application, "form": form})


@merchant_required
def contract_imei(request, contract_id):
    contract = get_object_or_404(Contract.objects.select_related("application"), id=contract_id)
    application = contract.application
    if not can_access_contract_flow(request.user, application):
        raise PermissionDenied

    if request.method == "POST":
        form = ImeiForm(request.POST, instance=contract)
        if form.is_valid():
            contract = form.save(commit=False)
            contract.status = Contract.STATUS_IMEI_ENTERED
            contract.save()
            imei = contract.imei_number or ""
            application.imei_number = imei
            application.status = "contract_creating"
            application.save(update_fields=["imei_number", "status"])

            # Auto-verify IMEI against selected deal if enabled
            if (
                getattr(dj_settings, "IMEI_CHECK_ENABLED", True)
                and getattr(dj_settings, "IMEI_CHECK_AUTO_VERIFY", True)
                and imei
                and application.deal_id
            ):
                try:
                    from applications.services.imei_verification import (
                        save_verification_result,
                        verify_imei_against_selected_device,
                    )
                    result = verify_imei_against_selected_device(
                        imei=imei,
                        selected_device=application.deal,
                        user=request.user,
                    )
                    save_verification_result(application, result, user=request.user)
                except Exception:
                    logger.exception(
                        "IMEI auto-verification failed for application %s", application.pk
                    )

            return redirect("contract_progress", contract_id=contract.id)
    else:
        form = ImeiForm(instance=contract)

    return render(request, "contracts/imei.html", {
        "contract": contract,
        "application": application,
        "form": form,
        "imei_check_enabled": getattr(dj_settings, "IMEI_CHECK_ENABLED", True),
        "strict_mode": getattr(dj_settings, "IMEI_CHECK_STRICT_MODE", True),
    })


@merchant_required
def contract_progress(request, contract_id):
    contract = get_object_or_404(Contract.objects.select_related("application"), id=contract_id)
    application = contract.application
    if not can_access_contract_flow(request.user, application):
        raise PermissionDenied

    if contract.status == Contract.STATUS_IMEI_ENTERED:
        contract.status = Contract.STATUS_CONTRACT_CREATED
        contract.save(update_fields=["status", "updated_at"])
        application.status = "warranty_check"
        application.save(update_fields=["status"])

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "warranty":
            contract.warranty_checked = True
            contract.status = Contract.STATUS_WARRANTY_CHECKED
            contract.save(update_fields=["warranty_checked", "status", "updated_at"])
            application.status = "locking"
            application.save(update_fields=["status"])
            messages.success(request, "Warranty marked checked.")
        elif action == "locked":
            # TODO: replace this placeholder with Upya device locking once integration is available.
            contract.phone_locked = True
            contract.status = Contract.STATUS_LOCKED
            contract.save(update_fields=["phone_locked", "status", "updated_at"])
            application.status = "deposit_pending"
            application.save(update_fields=["status"])
            messages.success(request, "Phone marked locked.")
        elif action == "deposit":
            # TODO: replace this placeholder with deposit payment verification once integration is available.
            contract.deposit_paid = True
            contract.status = Contract.STATUS_COMPLETE
            contract.save(update_fields=["deposit_paid", "status", "updated_at"])
            application.status = "contract_complete"
            application.save(update_fields=["status"])
            process_contract_completion(application)
            return redirect("contract_complete", contract_id=contract.id)

        return redirect("contract_progress", contract_id=contract.id)

    return render(request, "contracts/progress.html", {"contract": contract, "application": application})


@merchant_required
def contract_complete(request, contract_id):
    contract = get_object_or_404(Contract.objects.select_related("application"), id=contract_id)
    if not can_access_contract_flow(request.user, contract.application):
        raise PermissionDenied
    return render(request, "contracts/complete.html", {"contract": contract, "application": contract.application})


@merchant_required
def contract_detail(request, contract_id):
    contract = get_object_or_404(Contract.objects.select_related("application", "merchant"), id=contract_id)
    if not can_access_contract_flow(request.user, contract.application):
        raise PermissionDenied
    return render(request, "contracts/detail.html", {"contract": contract, "application": contract.application})

# Create your views here.
