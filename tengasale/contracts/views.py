import logging
from decimal import Decimal

from django.conf import settings as dj_settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import merchant_required
from applications.models import FinancingApplication
from commissions.services import process_contract_completion

from .forms import ContractSignatureForm, ImeiForm, MerchantTermsForm
from .models import Contract, ContractDocumentDelivery, LegalAcceptance, LegalDocumentTemplate

logger = logging.getLogger(__name__)


def _user_is_hq(user):
    try:
        return user.profile.role in ("hq", "tech_support", "underwriter")
    except Exception:
        return user.is_staff or user.is_superuser


def can_access_contract_flow(user, application):
    return application.created_by_id == user.id


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
            contract.terms_accepted_by_merchant = True
            contract.terms_confirmed_at = timezone.now()
            contract.terms_confirmed_by = request.user
            contract.status = Contract.STATUS_TERMS_ACCEPTED
            contract.save(update_fields=[
                "terms_accepted_by_merchant", "terms_confirmed_at", "terms_confirmed_by",
                "status", "updated_at",
            ])
            application.status = "contract_signature"
            application.save(update_fields=["status"])

            # Send notification to merchant
            try:
                from notifications.models import Notification
                Notification.send(
                    recipient=request.user,
                    notification_type=Notification.TYPE_TERMS_PENDING,
                    title="Contract Terms Confirmed",
                    body=f"You confirmed terms for {application.customer_name} — {contract.contract_number}",
                    link=application.get_continue_url(),
                    level=Notification.LEVEL_SUCCESS,
                )
            except Exception:
                pass

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
            contract.status = Contract.STATUS_SIGNED
            now = timezone.now()
            contract.terms_accepted_at = now
            ip = (
                request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
                or request.META.get("REMOTE_ADDR", "")
            )
            contract.terms_accepted_ip = ip
            contract.save()
            application.status = "imei_entry"
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

    return render(request, "contracts/progress.html", {
        "contract": contract,
        "application": application,
        "lock_profile": lock_profile,
        "lock_readiness": lock_readiness,
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

@merchant_required
def contract_pdf_initial(request, contract_id):
    """Serve or generate the initial signed PDF."""
    contract = get_object_or_404(Contract.objects.select_related("application"), id=contract_id)
    is_hq = _user_is_hq(request.user)
    if not is_hq and not can_access_contract_flow(request.user, contract.application):
        raise PermissionDenied

    if not contract.initial_pdf:
        from services.contracts.pdf_contracts import generate_customer_contract_pdf
        generate_customer_contract_pdf(contract, generated_by=request.user)
        contract.refresh_from_db()

    if not contract.initial_pdf:
        raise Http404("Initial contract PDF not available")

    return FileResponse(
        contract.initial_pdf.open("rb"),
        as_attachment=False,
        filename=f"TengaSale_Contract_{contract.contract_number}.pdf",
        content_type="application/pdf",
    )


@merchant_required
def contract_pdf_bundle(request, contract_id):
    """Serve or generate the initial contract bundle PDF."""
    contract = get_object_or_404(Contract.objects.select_related("application"), id=contract_id)
    is_hq = _user_is_hq(request.user)
    if not is_hq and not can_access_contract_flow(request.user, contract.application):
        raise PermissionDenied

    if not contract.contract_bundle_pdf:
        from services.contracts.pdf_contracts import generate_contract_bundle_pdf
        generate_contract_bundle_pdf(contract, generated_by=request.user)
        contract.refresh_from_db()

    if not contract.contract_bundle_pdf:
        raise Http404("Contract bundle PDF not available")

    return FileResponse(
        contract.contract_bundle_pdf.open("rb"),
        as_attachment=False,
        filename=f"TengaSale_Contract_{contract.contract_number}_Bundle.pdf",
        content_type="application/pdf",
    )


@merchant_required
def contract_pdf_completed_bundle(request, contract_id):
    """Serve or generate the completed contract bundle PDF."""
    contract = get_object_or_404(Contract.objects.select_related("application"), id=contract_id)
    is_hq = _user_is_hq(request.user)
    if not is_hq and not can_access_contract_flow(request.user, contract.application):
        raise PermissionDenied

    if not contract.completed_bundle_pdf:
        if contract.status != Contract.STATUS_COMPLETE:
            raise Http404("Contract not yet completed")
        from services.contracts.pdf_contracts import generate_completed_contract_bundle_pdf
        generate_completed_contract_bundle_pdf(contract, generated_by=request.user)
        contract.refresh_from_db()

    if not contract.completed_bundle_pdf:
        raise Http404("Completed bundle PDF not available")

    return FileResponse(
        contract.completed_bundle_pdf.open("rb"),
        as_attachment=False,
        filename=f"TengaSale_Contract_{contract.contract_number}_Bundle_Completed.pdf",
        content_type="application/pdf",
    )


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

    if not contract.completed_pdf:
        if contract.status != Contract.STATUS_COMPLETE:
            raise Http404("Contract not yet completed")
        from services.contracts.pdf_contracts import generate_completed_contract_pdf
        generate_completed_contract_pdf(contract, generated_by=request.user)
        contract.refresh_from_db()

    if not contract.completed_pdf:
        raise Http404("Completed contract PDF not available")

    return FileResponse(
        contract.completed_pdf.open("rb"),
        as_attachment=False,
        filename=f"TengaSale_Contract_{contract.contract_number}_Completed.pdf",
        content_type="application/pdf",
    )


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
