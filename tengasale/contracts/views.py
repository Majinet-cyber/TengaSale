import json
import logging
import uuid
from decimal import Decimal
from urllib.parse import urlencode

from django.conf import settings as dj_settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from accounts.decorators import merchant_required
from applications.models import FinancingApplication
from commissions.services import process_contract_completion

from .forms import ContractSignatureForm, ImeiForm, MerchantTermsForm
from .models import Contract, ContractDocumentDelivery, LegalAcceptance, LegalDocumentTemplate
from .portfolio import PORTFOLIO_FILTERS, merchant_portfolio_contracts

logger = logging.getLogger("tengasale.contracts")
pdf_logger = logging.getLogger("tengasale.contracts.pdf")


def _user_is_hq(user):
    try:
        return user.profile.role in ("hq", "tech_support", "underwriter")
    except Exception:
        return user.is_staff or user.is_superuser


def can_access_contract_flow(user, application):
    return application.created_by_id == user.id


@merchant_required
def merchant_portfolio(request):
    portfolio_filter = request.GET.get("portfolio", "financed").strip().lower()
    if portfolio_filter not in PORTFOLIO_FILTERS:
        portfolio_filter = "financed"
    query = request.GET.get("q", "").strip()
    contracts = merchant_portfolio_contracts(request.user, portfolio_filter).select_related(
        "application", "application__payment_contract", "device_lock_profile"
    ).order_by("-active_at", "-created_at")
    if query:
        contracts = contracts.filter(
            Q(customer_name__icontains=query)
            | Q(customer_phone__icontains=query)
            | Q(contract_number__icontains=query)
            | Q(imei_number__icontains=query)
            | Q(application__payment_contract__payg_number__icontains=query)
        )
    return render(request, "contracts/portfolio_list.html", {
        "contracts": contracts,
        "portfolio_filter": portfolio_filter,
        "portfolio_title": portfolio_filter.title(),
        "query": query,
        "result_count": contracts.count(),
    })


def _try_create_lock_profile(contract, triggered_by=None):
    """
    Create a DeviceLockProfile for the contract after IMEI is captured.
    Auto-enrolls if DEVICE_LOCK_AUTO_ENROLL=True.
    Never raises — failures are logged and shown as a warning, not an error.
    """
    try:
        from services.device_lock.service import ensure_lock_profile_for_contract, enroll_device
        from device_lock.models import DeviceLockEvent

        profile, created = ensure_lock_profile_for_contract(contract)
        if created:
            logger.info("Created DeviceLockProfile pk=%s for contract %s", profile.pk, contract.contract_number)

        if getattr(dj_settings, "DEVICE_LOCK_AUTO_ENROLL", False) and profile.lock_status == "not_enrolled":
            enroll_device(
                profile,
                triggered_by=triggered_by,
                trigger_source=DeviceLockEvent.SOURCE_SYSTEM,
            )
    except Exception:
        logger.exception("Failed to create/enroll lock profile for contract %s", contract.pk)


@merchant_required
def contract_terms(request, app_id):
    application = get_object_or_404(
        FinancingApplication.objects.select_related("created_by", "deal"),
        id=app_id,
    )
    if not can_access_contract_flow(request.user, application):
        raise PermissionDenied
    contract = getattr(application, "contract", None)
    if application.status in ["approved", "approved_pending_device_lock", "imei_required"] and not application.imei_number and not contract:
        return redirect("capture_imei", app_id=application.id)
    if application.status not in ["approved", "approved_pending_device_lock", "device_locked", "contract_terms", "contract_signature"]:
        return redirect(application.get_continue_url())

    if contract:
        display_contract = contract
    else:
        from core.commercial import pricing_from_application, sync_application_pricing_fields

        sync_application_pricing_fields(application, save=True)
        pricing = pricing_from_application(application)
        if not pricing:
            messages.error(request, "Contract terms are missing pricing. Please reselect the device deal before continuing.")
            return redirect(application.get_continue_url())
        display_contract = Contract(
            application=application,
            merchant=application.created_by,
            customer_name=application.customer_name,
            customer_phone=application.customer_phone,
            national_id=application.national_id,
            deal_name=str(application.deal) if application.deal_id else "",
            cash_price=pricing["cash_price"],
            total_loan=pricing["contract_total"],
            deposit_amount=pricing["deposit_required"],
            monthly_payment=pricing["monthly_repayment"],
            daily_payment=pricing["daily_repayment"],
            term_months=pricing["term_months"],
        )

    # Load active legal documents for display
    master_terms_doc = LegalDocumentTemplate.get_active(LegalDocumentTemplate.TYPE_MASTER_TERMS)
    summary_doc = LegalDocumentTemplate.get_active(LegalDocumentTemplate.TYPE_CONTRACT_SUMMARY)

    # Track that merchant opened the terms page
    if contract and not contract.terms_opened_at:
        contract.terms_opened_at = timezone.now()
        contract.save(update_fields=["terms_opened_at"])

    if request.method == "POST":
        form = MerchantTermsForm(request.POST)
        if form.is_valid():
            contract, _ = Contract.from_application(application)
            if application.imei_number and not contract.imei_number:
                contract.imei_number = application.imei_number
            contract.terms_accepted_by_merchant = True
            contract.terms_confirmed_at = timezone.now()
            contract.terms_confirmed_by = request.user
            contract.status = Contract.STATUS_TERMS_ACCEPTED
            contract.save(update_fields=[
                "imei_number",
                "terms_accepted_by_merchant", "terms_confirmed_at", "terms_confirmed_by",
                "status", "updated_at",
            ])
            application.status = "contract_signature"
            application.save(update_fields=["status"])

            # Create LegalAcceptance records for both documents (merchant confirms on behalf)
            now = timezone.now()
            ip = (
                request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
                or request.META.get("REMOTE_ADDR", "")
            )
            ua = request.META.get("HTTP_USER_AGENT", "")
            for doc in [master_terms_doc, summary_doc]:
                if doc:
                    LegalAcceptance.objects.update_or_create(
                        application=application,
                        contract=contract,
                        legal_document=doc,
                        defaults={
                            "accepted_by_user": request.user,
                            "accepted_name": contract.customer_name,
                            "accepted_phone": contract.customer_phone,
                            "accepted_national_id": contract.national_id or "",
                            "acceptance_method": LegalAcceptance.METHOD_MOBILE_ACCEPTANCE,
                            "accepted_at": now,
                            "ip_address": ip or None,
                            "user_agent": ua,
                            "acceptance_text_snapshot": f"{doc.title} v{doc.version}",
                        },
                    )
                    _audit(request.user, "legal_document_accepted", "LegalAcceptance", f"{application.pk}:{doc.pk}", {
                        "document": str(doc),
                        "customer": contract.customer_name,
                        "method": LegalAcceptance.METHOD_MOBILE_ACCEPTANCE,
                    })

            return redirect("contract_signature", contract_id=contract.id)
    else:
        form = MerchantTermsForm()

    return render(request, "contracts/terms.html", {
        "application": application,
        "contract": display_contract,
        "form": form,
        "master_terms_doc": master_terms_doc,
        "summary_doc": summary_doc,
    })


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
            if application.imei_number and not contract.imei_number:
                contract.imei_number = application.imei_number
            contract.status = Contract.STATUS_IMEI_ENTERED if contract.imei_number else Contract.STATUS_SIGNED
            now = timezone.now()
            contract.terms_accepted_at = now
            ip = (
                request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
                or request.META.get("REMOTE_ADDR", "")
            )
            contract.terms_accepted_ip = ip
            contract.save()
            application.status = "contract_creating" if contract.imei_number else "imei_entry"
            application.save(update_fields=["status"])

            # Update LegalAcceptance records to digital_signature method + attach signature image
            ua = request.META.get("HTTP_USER_AGENT", "")
            for acc in LegalAcceptance.objects.filter(application=application, contract=contract):
                acc.acceptance_method = LegalAcceptance.METHOD_DIGITAL_SIGNATURE
                acc.accepted_at = now
                acc.ip_address = ip or None
                acc.user_agent = ua
                if form.signature_file:
                    from django.core.files.base import ContentFile
                    sig_copy = ContentFile(form.signature_file.read(), name=f"legal_sig_{acc.pk}_{acc.legal_document_id}.png")
                    acc.signature_image.save(sig_copy.name, sig_copy, save=False)
                acc.save()

            # Generate initial signed PDF + bundle PDF
            _generate_initial_pdf_async(contract, triggered_by=request.user)
            _generate_bundle_pdf_async(contract, triggered_by=request.user)

            # Audit
            _audit(request.user, "contract_terms_signed", "Contract", str(contract.pk), {
                "contract_number": contract.contract_number,
                "customer": contract.customer_name,
                "master_terms_accepted": True,
                "summary_accepted": True,
            })

            if contract.imei_number:
                return redirect("contract_progress", contract_id=contract.id)
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

            # Create DeviceLockProfile (and optionally auto-enroll)
            _try_create_lock_profile(contract, triggered_by=request.user)

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
            contract.phone_locked = True
            contract.status = Contract.STATUS_LOCKED
            contract.save(update_fields=["phone_locked", "status", "updated_at"])
            application.status = "deposit_pending"
            application.save(update_fields=["status"])
            messages.success(request, "Phone marked locked.")

            try:
                from portal.services import sync_portal_lock_from_contract

                payment_contract = sync_portal_lock_from_contract(contract)
                if payment_contract and payment_contract.payg_number:
                    messages.info(request, f"PayG code {payment_contract.payg_number} is ready for customer payments.")
                elif payment_contract:
                    messages.info(request, "PayG code will appear once device lock is confirmed.")
            except Exception:
                logger.exception("Failed to sync PayG after lock for contract %s", contract.pk)

            # Fire device lock if auto-lock is enabled
            if getattr(dj_settings, "DEVICE_LOCK_AUTO_LOCK", False):
                try:
                    from device_lock.models import DeviceLockProfile, DeviceLockEvent
                    from services.device_lock.service import lock_device
                    profile = DeviceLockProfile.objects.filter(contract=contract).first()
                    if profile:
                        lock_device(
                            profile,
                            reason="Contract marked locked by merchant",
                            triggered_by=request.user,
                            trigger_source=DeviceLockEvent.SOURCE_MERCHANT,
                        )
                except Exception:
                    logger.exception("Auto-lock failed for contract %s", contract.pk)

        elif action == "deposit":
            contract.deposit_paid = True
            contract.status = Contract.STATUS_COMPLETE
            contract.save(update_fields=["deposit_paid", "status", "updated_at"])
            application.status = "contract_complete"
            application.save(update_fields=["status"])
            process_contract_completion(application)

            # Fire device release if policy says so
            try:
                from device_lock.models import DeviceLockProfile, DeviceLockEvent
                from services.device_lock.service import release_device, evaluate_lock_policy
                profile = DeviceLockProfile.objects.filter(contract=contract).first()
                if profile:
                    evaluation = evaluate_lock_policy(contract)
                    if evaluation.get("action") == "release" and getattr(dj_settings, "DEVICE_LOCK_AUTO_UNLOCK", False):
                        release_device(
                            profile,
                            reason="Contract completed — auto-release",
                            triggered_by=request.user,
                            trigger_source=DeviceLockEvent.SOURCE_SYSTEM,
                        )
            except Exception:
                logger.exception("Auto-release failed for contract %s", contract.pk)

            # Generate completed PDF and schedule WhatsApp delivery
            try:
                from services.contracts.completion import handle_contract_completion
                completion_result = handle_contract_completion(contract, triggered_by=request.user)
                if completion_result.get("delivery_scheduled"):
                    messages.success(request, "Contract completed. Final PDF will be sent to customer via WhatsApp in 24 hours.")
                elif completion_result.get("pdf_generated"):
                    messages.success(request, "Contract completed. Final PDF generated.")
                if completion_result.get("errors"):
                    for err in completion_result["errors"]:
                        logger.warning("Completion step warning for contract %s: %s", contract.pk, err)
            except Exception:
                logger.exception("Completion workflow failed for contract %s", contract.pk)

            return redirect("contract_complete", contract_id=contract.id)

        return redirect("contract_progress", contract_id=contract.id)

    # Fetch lock profile for display
    lock_profile = None
    lock_readiness = None
    try:
        from device_lock.models import DeviceLockProfile
        from services.device_lock.service import get_lock_readiness
        lock_profile = DeviceLockProfile.objects.filter(contract=contract).first()
        if lock_profile:
            lock_readiness = get_lock_readiness(lock_profile)
    except Exception:
        pass

    deposit_due = contract.deposit_amount
    pricing_complete = contract.total_loan > 0 and contract.daily_payment > 0
    try:
        portal_pc = application.payment_contract
        if portal_pc.deposit_required > 0:
            deposit_due = portal_pc.deposit_remaining
        if portal_pc.total_amount > 0:
            pricing_complete = True
    except Exception:
        pass

    return render(request, "contracts/progress.html", {
        "contract": contract,
        "application": application,
        "lock_profile": lock_profile,
        "lock_readiness": lock_readiness,
        "deposit_due": deposit_due,
        "pricing_complete": pricing_complete,
        "show_lock_diagnostics": _user_is_hq(request.user),
    })


@merchant_required
def contract_complete(request, contract_id):
    contract = get_object_or_404(Contract.objects.select_related("application"), id=contract_id)
    if not can_access_contract_flow(request.user, contract.application):
        raise PermissionDenied
    return render(request, "contracts/complete.html", {"contract": contract, "application": contract.application})


@merchant_required
def contract_detail(request, contract_id):
    contract = get_object_or_404(Contract.objects.select_related("application", "merchant"), id=contract_id)
    application = contract.application
    is_hq = _user_is_hq(request.user)

    # Merchants can only see their own contracts; HQ can see all
    if not is_hq and not can_access_contract_flow(request.user, application):
        raise PermissionDenied

    lock_profile = None
    lock_readiness = None
    try:
        from device_lock.models import DeviceLockProfile
        from services.device_lock.service import get_lock_readiness
        lock_profile = DeviceLockProfile.objects.filter(contract=contract).first()
        if lock_profile:
            lock_readiness = get_lock_readiness(lock_profile)
    except Exception:
        pass

    latest_delivery = ContractDocumentDelivery.objects.filter(
        contract=contract,
        channel=ContractDocumentDelivery.CHANNEL_WHATSAPP,
    ).order_by("-created_at").first()

    # Legal acceptance records
    legal_acceptances = LegalAcceptance.objects.filter(
        application=application,
    ).select_related("legal_document").order_by("-accepted_at")

    master_terms_acceptance = legal_acceptances.filter(
        legal_document__document_type=LegalDocumentTemplate.TYPE_MASTER_TERMS,
    ).first()
    summary_acceptance = legal_acceptances.filter(
        legal_document__document_type=LegalDocumentTemplate.TYPE_CONTRACT_SUMMARY,
    ).first()

    return render(request, "contracts/detail.html", {
        "contract": contract,
        "application": application,
        "lock_profile": lock_profile,
        "lock_readiness": lock_readiness,
        "can_control_lock": False,
        "latest_delivery": latest_delivery,
        "is_hq": is_hq,
        "legal_acceptances": legal_acceptances,
        "master_terms_acceptance": master_terms_acceptance,
        "summary_acceptance": summary_acceptance,
    })


# ─────────────────────────────────────────────────────────────────────────────
# PDF download views
# ─────────────────────────────────────────────────────────────────────────────

def _wants_json_pdf_error(request) -> bool:
    accept = request.headers.get("Accept", "")
    return (
        "application/json" in accept
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )


def _pdf_unavailable_response(request, message: str, *, status: int = 503):
    if _wants_json_pdf_error(request):
        return JsonResponse({"error": message}, status=status)
    return HttpResponse(message, status=status, content_type="text/plain")


def _serve_contract_pdf_file(request, contract, file_field, filename: str):
    """Open a stored PDF field; log and return a safe error response on failure."""
    pdf_file = getattr(contract, file_field, None)
    if not pdf_file:
        pdf_logger.error(
            "PDF missing contract_id=%s user=%s field=%s",
            contract.id,
            request.user.pk,
            file_field,
        )
        return _pdf_unavailable_response(request, "Unable to generate contract PDF")

    pdf_path = ""
    try:
        pdf_path = pdf_file.path
    except Exception:
        pdf_path = pdf_file.name

    try:
        pdf_logger.info(
            "Serving PDF contract_id=%s number=%s user=%s path=%s",
            contract.id,
            contract.contract_number,
            request.user.pk,
            pdf_path,
        )
        return FileResponse(
            pdf_file.open("rb"),
            as_attachment=False,
            filename=filename,
            content_type="application/pdf",
        )
    except Exception:
        pdf_logger.exception(
            "PDF read failed contract_id=%s number=%s user=%s path=%s",
            contract.id,
            contract.contract_number,
            request.user.pk,
            pdf_path,
        )
        return _pdf_unavailable_response(request, "Unable to generate contract PDF")


@merchant_required
def contract_pdf_initial(request, contract_id):
    """Serve or generate the initial signed PDF."""
    contract = get_object_or_404(Contract.objects.select_related("application"), id=contract_id)
    is_hq = _user_is_hq(request.user)
    if not is_hq and not can_access_contract_flow(request.user, contract.application):
        raise PermissionDenied

    try:
        if not contract.initial_pdf:
            from services.contracts.pdf_contracts import generate_customer_contract_pdf

            generate_customer_contract_pdf(contract, generated_by=request.user)
            contract.refresh_from_db()

        if not contract.initial_pdf:
            pdf_logger.error(
                "Initial PDF generation returned empty contract_id=%s number=%s user=%s",
                contract.id,
                contract.contract_number,
                request.user.pk,
            )
            return _pdf_unavailable_response(request, "Unable to generate contract PDF")

        return _serve_contract_pdf_file(
            request,
            contract,
            "initial_pdf",
            f"TengaSale_Contract_{contract.contract_number}.pdf",
        )
    except Exception:
        pdf_logger.exception(
            "contract_pdf_initial failed contract_id=%s number=%s user=%s",
            contract.id,
            getattr(contract, "contract_number", "?"),
            request.user.pk,
        )
        return _pdf_unavailable_response(request, "Unable to generate contract PDF")


@merchant_required
def contract_pdf_bundle(request, contract_id):
    """Serve or generate the initial contract bundle PDF."""
    contract = get_object_or_404(Contract.objects.select_related("application"), id=contract_id)
    is_hq = _user_is_hq(request.user)
    if not is_hq and not can_access_contract_flow(request.user, contract.application):
        raise PermissionDenied

    try:
        if not contract.contract_bundle_pdf:
            from services.contracts.pdf_contracts import generate_contract_bundle_pdf

            generate_contract_bundle_pdf(contract, generated_by=request.user)
            contract.refresh_from_db()

        if not contract.contract_bundle_pdf:
            pdf_logger.error(
                "Bundle PDF generation returned empty contract_id=%s number=%s user=%s",
                contract.id,
                contract.contract_number,
                request.user.pk,
            )
            return _pdf_unavailable_response(request, "Unable to generate contract PDF")

        return _serve_contract_pdf_file(
            request,
            contract,
            "contract_bundle_pdf",
            f"TengaSale_Contract_{contract.contract_number}_Bundle.pdf",
        )
    except Exception:
        pdf_logger.exception(
            "contract_pdf_bundle failed contract_id=%s number=%s user=%s",
            contract.id,
            contract.contract_number,
            request.user.pk,
        )
        return _pdf_unavailable_response(request, "Unable to generate contract PDF")


@merchant_required
def contract_pdf_completed_bundle(request, contract_id):
    """Serve or generate the completed contract bundle PDF."""
    contract = get_object_or_404(Contract.objects.select_related("application"), id=contract_id)
    is_hq = _user_is_hq(request.user)
    if not is_hq and not can_access_contract_flow(request.user, contract.application):
        raise PermissionDenied

    try:
        if not contract.completed_bundle_pdf:
            if contract.status != Contract.STATUS_COMPLETE:
                raise Http404("Contract not yet completed")
            from services.contracts.pdf_contracts import generate_completed_contract_bundle_pdf

            generate_completed_contract_bundle_pdf(contract, generated_by=request.user)
            contract.refresh_from_db()

        if not contract.completed_bundle_pdf:
            pdf_logger.error(
                "Completed bundle PDF empty contract_id=%s number=%s user=%s",
                contract.id,
                contract.contract_number,
                request.user.pk,
            )
            return _pdf_unavailable_response(request, "Unable to generate contract PDF")

        return _serve_contract_pdf_file(
            request,
            contract,
            "completed_bundle_pdf",
            f"TengaSale_Contract_{contract.contract_number}_Bundle_Completed.pdf",
        )
    except Http404:
        raise
    except Exception:
        pdf_logger.exception(
            "contract_pdf_completed_bundle failed contract_id=%s number=%s user=%s",
            contract.id,
            contract.contract_number,
            request.user.pk,
        )
        return _pdf_unavailable_response(request, "Unable to generate contract PDF")


@merchant_required
def contract_master_terms_view(request, contract_id):
    """Display the Master Terms and Conditions accepted for this contract."""
    contract = get_object_or_404(Contract.objects.select_related("application"), id=contract_id)
    is_hq = _user_is_hq(request.user)
    if not is_hq and not can_access_contract_flow(request.user, contract.application):
        raise PermissionDenied

    master_terms = LegalDocumentTemplate.get_active(LegalDocumentTemplate.TYPE_MASTER_TERMS)
    return render(request, "contracts/master_terms_view.html", {
        "contract": contract,
        "master_terms": master_terms,
    })


@merchant_required
def contract_pdf_completed(request, contract_id):
    """Serve or generate the completed PDF."""
    contract = get_object_or_404(Contract.objects.select_related("application"), id=contract_id)
    is_hq = _user_is_hq(request.user)
    if not is_hq and not can_access_contract_flow(request.user, contract.application):
        raise PermissionDenied

    try:
        if not contract.completed_pdf:
            if contract.status != Contract.STATUS_COMPLETE:
                raise Http404("Contract not yet completed")
            from services.contracts.pdf_contracts import generate_completed_contract_pdf

            generate_completed_contract_pdf(contract, generated_by=request.user)
            contract.refresh_from_db()

        if not contract.completed_pdf:
            pdf_logger.error(
                "Completed PDF empty contract_id=%s number=%s user=%s",
                contract.id,
                contract.contract_number,
                request.user.pk,
            )
            return _pdf_unavailable_response(request, "Unable to generate contract PDF")

        return _serve_contract_pdf_file(
            request,
            contract,
            "completed_pdf",
            f"TengaSale_Contract_{contract.contract_number}_Completed.pdf",
        )
    except Http404:
        raise
    except Exception:
        pdf_logger.exception(
            "contract_pdf_completed failed contract_id=%s number=%s user=%s",
            contract.id,
            contract.contract_number,
            request.user.pk,
        )
        return _pdf_unavailable_response(request, "Unable to generate contract PDF")


# ─────────────────────────────────────────────────────────────────────────────
# HQ delivery management views
# ─────────────────────────────────────────────────────────────────────────────

def _require_hq(request):
    if not request.user.is_authenticated or not _user_is_hq(request.user):
        raise PermissionDenied


@require_POST
def hq_delivery_send_now(request, delivery_id):
    """Manually trigger a WhatsApp delivery immediately (HQ/admin only)."""
    _require_hq(request)
    delivery = get_object_or_404(ContractDocumentDelivery, id=delivery_id)
    contract = delivery.contract

    if delivery.delivery_status == ContractDocumentDelivery.STATUS_SENT:
        return JsonResponse({"ok": False, "error": "Already sent."}, status=400)
    if delivery.delivery_status == ContractDocumentDelivery.STATUS_CANCELLED:
        return JsonResponse({"ok": False, "error": "Delivery cancelled."}, status=400)

    from services.messaging.registry import get_whatsapp_provider
    from contracts.management.commands.send_scheduled_contract_documents import Command

    cmd = Command()
    pdf_url = cmd._resolve_pdf_url(delivery, contract)
    provider = get_whatsapp_provider()

    delivery.attempt_count += 1
    delivery.last_attempt_at = timezone.now()
    delivery.save(update_fields=["attempt_count", "last_attempt_at", "updated_at"])

    result = provider.send_whatsapp_document(
        to_phone=delivery.normalized_customer_phone or delivery.customer_phone,
        document_url=pdf_url or "",
        filename=f"TengaSale_Contract_{contract.contract_number}_Completed.pdf",
        message=delivery.message_text,
    )

    if result.get("success"):
        delivery.delivery_status = ContractDocumentDelivery.STATUS_SENT
        delivery.sent_at = timezone.now()
        delivery.provider_message_id = result.get("provider_message_id", "")
        delivery.provider_response = result.get("raw_response")
        delivery.error_message = ""
        delivery.triggered_by = request.user
        delivery.save(update_fields=["delivery_status", "sent_at", "provider_message_id", "provider_response", "error_message", "triggered_by", "updated_at"])
        _audit(request.user, "contract_pdf_sent", "ContractDocumentDelivery", str(delivery.pk), {
            "contract_number": contract.contract_number,
            "forced_by": request.user.username,
        })
        return JsonResponse({"ok": True, "msg": "Sent successfully."})
    else:
        error = result.get("error", "Unknown error")
        delivery.error_message = error
        delivery.provider_response = result.get("raw_response")
        delivery.save(update_fields=["error_message", "provider_response", "updated_at"])
        return JsonResponse({"ok": False, "error": error}, status=500)


@require_POST
def hq_delivery_cancel(request, delivery_id):
    """Cancel a scheduled delivery (HQ/admin only)."""
    _require_hq(request)
    delivery = get_object_or_404(ContractDocumentDelivery, id=delivery_id)
    if delivery.delivery_status == ContractDocumentDelivery.STATUS_SENT:
        return JsonResponse({"ok": False, "error": "Cannot cancel already-sent delivery."}, status=400)
    delivery.delivery_status = ContractDocumentDelivery.STATUS_CANCELLED
    delivery.save(update_fields=["delivery_status", "updated_at"])
    _audit(request.user, "contract_delivery_cancelled", "ContractDocumentDelivery", str(delivery.pk), {
        "contract_number": delivery.contract.contract_number,
        "cancelled_by": request.user.username,
    })
    return JsonResponse({"ok": True, "msg": "Delivery cancelled."})


@require_POST
def hq_delivery_retry(request, delivery_id):
    """Reset a failed delivery back to scheduled for retry (HQ/admin only)."""
    _require_hq(request)
    delivery = get_object_or_404(ContractDocumentDelivery, id=delivery_id)
    if delivery.delivery_status not in (ContractDocumentDelivery.STATUS_FAILED, ContractDocumentDelivery.STATUS_SKIPPED):
        return JsonResponse({"ok": False, "error": "Only failed/skipped deliveries can be retried."}, status=400)
    delivery.delivery_status = ContractDocumentDelivery.STATUS_SCHEDULED
    delivery.scheduled_for = timezone.now()
    delivery.error_message = ""
    delivery.save(update_fields=["delivery_status", "scheduled_for", "error_message", "updated_at"])
    _audit(request.user, "contract_delivery_retry", "ContractDocumentDelivery", str(delivery.pk), {
        "contract_number": delivery.contract.contract_number,
        "retried_by": request.user.username,
    })
    return JsonResponse({"ok": True, "msg": "Delivery rescheduled for immediate retry."})


@require_POST
def hq_regen_completed_pdf(request, contract_id):
    """Regenerate the completed PDF for a contract (HQ/admin only)."""
    _require_hq(request)
    contract = get_object_or_404(Contract, id=contract_id)
    from services.contracts.completion import regenerate_completed_pdf
    ok = regenerate_completed_pdf(contract, triggered_by=request.user)
    if ok:
        _audit(request.user, "contract_pdf_regenerated", "Contract", str(contract.pk), {
            "contract_number": contract.contract_number,
        })
        return JsonResponse({"ok": True, "msg": "Completed PDF regenerated."})
    return JsonResponse({"ok": False, "error": "PDF regeneration failed. Check server logs."}, status=500)


# ─────────────────────────────────────────────────────────────────────────────
# Deposit Payment Flow
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_mw_phone(phone: str) -> str:
    """Normalize a Malawi phone number to +265XXXXXXXXX format."""
    digits = "".join(c for c in phone if c.isdigit())
    if digits.startswith("265") and len(digits) == 12:
        return f"+{digits}"
    if digits.startswith("0") and len(digits) == 10:
        return f"+265{digits[1:]}"
    if len(digits) == 9:
        return f"+265{digits}"
    return f"+265{digits}"


def _detect_mw_operator(phone: str) -> str:
    """Detect Airtel Money or TNM Mpamba from normalized Malawi phone."""
    digits = "".join(c for c in phone if c.isdigit())
    if digits.startswith("265"):
        digits = digits[3:]
    if digits.startswith("0"):
        digits = digits[1:]
    if digits.startswith("88") or digits.startswith("99") or digits.startswith("98"):
        return "airtel"
    if digits.startswith("08") or digits.startswith("38"):
        return "tnm"
    return ""


@merchant_required
def contract_pay_deposit(request, contract_id):
    """Premium deposit payment page — creates PayChangu checkout and tracks payment."""
    contract = get_object_or_404(
        Contract.objects.select_related("application", "merchant"),
        id=contract_id,
    )
    application = contract.application

    if not can_access_contract_flow(request.user, application):
        raise PermissionDenied

    if contract.deposit_paid:
        messages.success(request, "Deposit has already been paid for this contract.")
        return redirect("contract_complete", contract_id=contract.id)

    if not contract.phone_locked:
        messages.warning(request, "Please lock the device before proceeding to deposit payment.")
        return redirect("contract_progress", contract_id=contract.id)

    try:
        from portal.services import sync_portal_lock_from_contract

        sync_portal_lock_from_contract(contract)
    except Exception:
        logger.exception("Failed to sync portal payment contract before deposit redirect for contract %s", contract.pk)
        messages.error(request, "Could not prepare the payment portal. Please try again.")
        return redirect("contract_progress", contract_id=contract.id)

    query = urlencode({
        "contract": contract.contract_number,
        "q": contract.contract_number,
        "payment_type": "deposit",
    })
    return redirect(f"/pay/?{query}")

    deposit_amount = contract.deposit_amount
    if deposit_amount <= 0:
        try:
            pc = application.payment_contract
            deposit_amount = pc.deposit_required if pc.deposit_required > 0 else deposit_amount
        except Exception:
            pass

    # Fetch existing portal PaymentContract if available for deposit info
    portal_pc = None
    try:
        portal_pc = application.payment_contract
    except Exception:
        pass

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "create_checkout":
            phone = request.POST.get("phone", "").strip()
            if not phone:
                return JsonResponse({"ok": False, "error": "Phone number is required."}, status=400)

            try:
                normalized_phone = _normalize_mw_phone(phone)
            except Exception:
                return JsonResponse({"ok": False, "error": "Invalid phone number format."}, status=400)

            if contract.deposit_payment_status == Contract.DEPOSIT_STATUS_PAID:
                return JsonResponse({"ok": False, "error": "Deposit already paid."}, status=400)

            # Check for existing pending checkout (idempotency)
            if contract.deposit_payment_status == Contract.DEPOSIT_STATUS_CHECKOUT_CREATED and contract.deposit_payment_tx_ref:
                return JsonResponse({
                    "ok": True,
                    "status": contract.deposit_payment_status,
                    "tx_ref": contract.deposit_payment_tx_ref,
                    "message": "Checkout already created. Waiting for payment.",
                })

            from integrations.paychangu_client import initiate_payment
            tx_ref = f"dep-{contract.contract_number}-{uuid.uuid4().hex[:8]}"

            return_url = request.build_absolute_uri(f"/contracts/{contract.id}/pay-deposit/")
            callback_url = request.build_absolute_uri("/contracts/webhooks/deposit/")

            result = initiate_payment(
                amount=deposit_amount,
                currency="MWK",
                tx_ref=tx_ref,
                return_url=return_url,
                callback_url=callback_url,
                customer_name=contract.customer_name,
                customer_phone=normalized_phone,
                description=f"Deposit for contract {contract.contract_number}",
                meta={
                    "contract_id": contract.id,
                    "contract_number": contract.contract_number,
                    "payment_type": "deposit",
                },
            )

            if result.get("status") == "success":
                contract.deposit_payment_status = Contract.DEPOSIT_STATUS_CHECKOUT_CREATED
                contract.deposit_payment_tx_ref = tx_ref
                contract.deposit_payer_phone = normalized_phone
                contract.save(update_fields=[
                    "deposit_payment_status", "deposit_payment_tx_ref",
                    "deposit_payer_phone", "updated_at",
                ])
                _audit(request.user, "deposit_checkout_created", "Contract", str(contract.id), {
                    "contract_number": contract.contract_number,
                    "tx_ref": tx_ref,
                    "amount": str(deposit_amount),
                    "phone": normalized_phone,
                })
                return JsonResponse({
                    "ok": True,
                    "checkout_url": result["checkout_url"],
                    "tx_ref": tx_ref,
                    "status": Contract.DEPOSIT_STATUS_CHECKOUT_CREATED,
                    "message": "Checkout created. Redirecting to payment...",
                })
            else:
                return JsonResponse({
                    "ok": False,
                    "error": result.get("message", "Could not create payment checkout. Please try again."),
                }, status=400)

    # Build payment state context
    payment_status = contract.deposit_payment_status or Contract.DEPOSIT_STATUS_NOT_STARTED
    payment_states = [
        {"key": "not_started", "label": "Deposit Required", "done": payment_status not in (Contract.DEPOSIT_STATUS_NOT_STARTED,)},
        {"key": "checkout_created", "label": "Checkout Started", "done": payment_status in (
            Contract.DEPOSIT_STATUS_AWAITING, Contract.DEPOSIT_STATUS_PROCESSING,
            Contract.DEPOSIT_STATUS_PAID,
        )},
        {"key": "awaiting_confirmation", "label": "Customer Confirming", "done": payment_status in (
            Contract.DEPOSIT_STATUS_PROCESSING, Contract.DEPOSIT_STATUS_PAID,
        )},
        {"key": "processing", "label": "Payment confirming", "done": payment_status == Contract.DEPOSIT_STATUS_PAID},
        {"key": "paid", "label": "Contract Activated", "done": payment_status == Contract.DEPOSIT_STATUS_PAID},
    ]

    amount_after_deposit = max(Decimal("0"), (contract.total_loan or Decimal("0")) - deposit_amount)

    return render(request, "contracts/pay_deposit.html", {
        "contract": contract,
        "application": application,
        "portal_pc": portal_pc,
        "deposit_amount": deposit_amount,
        "amount_after_deposit": amount_after_deposit,
        "payment_status": payment_status,
        "payment_states": payment_states,
        "is_paid": contract.deposit_paid,
        "is_failed": payment_status == Contract.DEPOSIT_STATUS_FAILED,
        "is_pending": payment_status in (
            Contract.DEPOSIT_STATUS_CHECKOUT_CREATED,
            Contract.DEPOSIT_STATUS_AWAITING,
            Contract.DEPOSIT_STATUS_PROCESSING,
        ),
        "mock_mode": getattr(dj_settings, "MOCK_PAYMENTS", "true").lower() == "true"
            if isinstance(getattr(dj_settings, "MOCK_PAYMENTS", "true"), str)
            else bool(getattr(dj_settings, "MOCK_PAYMENTS", True)),
    })


@merchant_required
def contract_deposit_status(request, contract_id):
    """JSON endpoint — frontend polls to refresh deposit payment status."""
    contract = get_object_or_404(Contract, id=contract_id)
    if not can_access_contract_flow(request.user, contract.application):
        raise PermissionDenied

    # Optionally verify with PayChangu if still pending
    status = contract.deposit_payment_status or Contract.DEPOSIT_STATUS_NOT_STARTED
    if status in (Contract.DEPOSIT_STATUS_CHECKOUT_CREATED, Contract.DEPOSIT_STATUS_AWAITING) and contract.deposit_payment_tx_ref:
        try:
            from integrations.paychangu_client import verify_transaction
            result = verify_transaction(contract.deposit_payment_tx_ref)
            if result.get("status") == "SUCCESS":
                _apply_deposit_paid(contract, result)
                status = Contract.DEPOSIT_STATUS_PAID
            elif result.get("status") == "FAILED":
                contract.deposit_payment_status = Contract.DEPOSIT_STATUS_FAILED
                contract.save(update_fields=["deposit_payment_status", "updated_at"])
                status = Contract.DEPOSIT_STATUS_FAILED
        except Exception:
            logger.exception("Deposit status check failed for contract %s", contract.pk)

    return JsonResponse({
        "status": status,
        "deposit_paid": contract.deposit_paid,
        "tx_ref": contract.deposit_payment_tx_ref,
        "redirect_url": f"/contracts/{contract.id}/complete/" if contract.deposit_paid else None,
    })


@csrf_exempt
def contract_deposit_webhook(request):
    """
    PayChangu webhook receiver for deposit payments.
    Verifies signature, confirms payment, marks deposit paid — idempotently.
    """
    if request.method != "POST":
        return HttpResponse(status=405)

    raw_body = request.body
    signature = request.headers.get("X-Paychangu-Signature", "") or request.META.get("HTTP_X_PAYCHANGU_SIGNATURE", "")

    # Verify webhook signature
    from integrations.paychangu_client import verify_webhook_signature, is_mock_mode
    if not is_mock_mode():
        if not verify_webhook_signature(raw_body, signature):
            logger.warning("Deposit webhook: invalid signature")
            return JsonResponse({"error": "Invalid signature"}, status=400)

    try:
        data = json.loads(raw_body)
    except Exception:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    # Extract key fields
    tx_ref = (
        data.get("tx_ref")
        or data.get("data", {}).get("tx_ref")
        or data.get("txRef", "")
    )
    raw_status = (
        data.get("status")
        or data.get("data", {}).get("status")
        or ""
    ).upper()
    amount_received = Decimal(str(
        data.get("amount")
        or data.get("data", {}).get("amount")
        or 0
    ))

    if not tx_ref:
        logger.warning("Deposit webhook: missing tx_ref in payload")
        return JsonResponse({"error": "Missing tx_ref"}, status=400)

    # Find the contract by tx_ref (deposit payments use prefix dep-)
    if not tx_ref.startswith("dep-"):
        return JsonResponse({"ok": True, "message": "Not a deposit payment"})

    contract = Contract.objects.filter(deposit_payment_tx_ref=tx_ref).first()
    if not contract:
        logger.warning("Deposit webhook: no contract found for tx_ref=%s", tx_ref)
        return JsonResponse({"error": "Contract not found"}, status=404)

    # Idempotency — already paid, skip
    if contract.deposit_paid:
        return JsonResponse({"ok": True, "message": "Already paid"})

    if raw_status in ("SUCCESSFUL", "SUCCESS", "COMPLETED"):
        # Validate amount
        expected = contract.deposit_amount
        if amount_received > 0 and abs(amount_received - expected) > Decimal("1"):
            logger.warning(
                "Deposit webhook: amount mismatch contract=%s expected=%s received=%s",
                contract.contract_number, expected, amount_received,
            )
            contract.deposit_payment_status = Contract.DEPOSIT_STATUS_MISMATCH
            contract.save(update_fields=["deposit_payment_status", "updated_at"])
            _audit(None, "deposit_amount_mismatch", "Contract", str(contract.id), {
                "tx_ref": tx_ref,
                "expected": str(expected),
                "received": str(amount_received),
            })
            return JsonResponse({"ok": True, "message": "Amount mismatch flagged for review"})

        _apply_deposit_paid(contract, {"tx_ref": tx_ref, "raw_response": data})

    elif raw_status in ("FAILED", "CANCELLED", "CANCELED"):
        contract.deposit_payment_status = Contract.DEPOSIT_STATUS_FAILED
        contract.save(update_fields=["deposit_payment_status", "updated_at"])
        _audit(None, "deposit_payment_failed", "Contract", str(contract.id), {"tx_ref": tx_ref})

    return JsonResponse({"ok": True})


def _apply_deposit_paid(contract, result):
    """Mark contract deposit as paid after verified PayChangu callback. Idempotent."""
    if contract.deposit_paid:
        return

    now = timezone.now()
    tx_ref = result.get("tx_ref", contract.deposit_payment_tx_ref)

    contract.deposit_paid = True
    contract.deposit_paid_at = now
    contract.deposit_payment_status = Contract.DEPOSIT_STATUS_PAID
    contract.deposit_payment_reference = tx_ref
    contract.status = Contract.STATUS_COMPLETE
    contract.save(update_fields=[
        "deposit_paid", "deposit_paid_at", "deposit_payment_status",
        "deposit_payment_reference", "status", "updated_at",
    ])

    application = contract.application
    application.status = "contract_complete"
    application.save(update_fields=["status"])

    # Run contract completion workflow
    try:
        from commissions.services import process_contract_completion
        process_contract_completion(application)
    except Exception:
        logger.exception("Commission processing failed after deposit paid for contract %s", contract.pk)

    # Create in-app notification for merchant
    try:
        from notifications.models import Notification
        Notification.send(
            recipient=contract.merchant,
            notification_type=Notification.TYPE_DEPOSIT_PAID,
            title="Deposit Paid",
            body=f"Deposit of MWK {contract.deposit_amount:,.0f} paid for contract {contract.contract_number}.",
            link=f"/contracts/{contract.id}/complete/",
            level=Notification.LEVEL_SUCCESS,
        )
    except Exception:
        logger.exception("Notification creation failed for deposit payment contract %s", contract.pk)

    _audit(None, "deposit_payment_confirmed", "Contract", str(contract.id), {
        "contract_number": contract.contract_number,
        "tx_ref": tx_ref,
        "amount": str(contract.deposit_amount),
        "customer": contract.customer_name,
    })

    logger.info("Deposit marked PAID for contract %s tx_ref=%s", contract.contract_number, tx_ref)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _generate_initial_pdf_async(contract, *, triggered_by=None):
    """Generate initial signed PDF — never raises."""
    try:
        from services.contracts.pdf_contracts import generate_customer_contract_pdf
        generate_customer_contract_pdf(contract, purpose="initial", generated_by=triggered_by)
    except Exception:
        logger.exception("Initial PDF generation failed for contract %s", contract.pk)


def _generate_bundle_pdf_async(contract, *, triggered_by=None):
    """Generate initial contract bundle PDF (Summary + Master Terms + Acceptance Certificate) — never raises."""
    try:
        from services.contracts.pdf_contracts import generate_contract_bundle_pdf
        generate_contract_bundle_pdf(contract, generated_by=triggered_by)
    except Exception:
        logger.exception("Bundle PDF generation failed for contract %s", contract.pk)


def _audit(user, action: str, object_type: str, object_id: str, detail: dict):
    """Write to AuditLog — never raises."""
    try:
        from core.models import AuditLog
        AuditLog.objects.create(
            user=user,
            action=action,
            object_type=object_type,
            object_id=object_id,
            detail=detail,
        )
    except Exception:
        logger.exception("AuditLog write failed for action=%s", action)
