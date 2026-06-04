import base64
import logging
import os
from io import BytesIO

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import merchant_required
from .models import Merchant, MerchantAgreement
from .pdf import generate_merchant_agreement_pdf, COMPANY_NAME, COMPANY_ADDRESS, GOVERNING_LAW

logger = logging.getLogger("tengasale.merchants")


def _get_or_create_agreement(request):
    """Get merchant and their current agreement (or create a new one)."""
    merchant = Merchant.objects.filter(owner=request.user).first()
    if not merchant:
        return None, None
    agreement = merchant.agreements.filter(
        status__in=[
            MerchantAgreement.STATUS_NOT_STARTED,
            MerchantAgreement.STATUS_VIEWED,
        ]
    ).order_by("-created_at").first()
    if not agreement:
        # Check if they already have a signed one
        signed = merchant.agreements.filter(
            status__in=[MerchantAgreement.STATUS_SIGNED, MerchantAgreement.STATUS_ACTIVE]
        ).order_by("-signed_at").first()
        if signed:
            return merchant, signed
        agreement = MerchantAgreement.objects.create(
            merchant=merchant,
            agreement_version=MerchantAgreement.AGREEMENT_VERSION,
        )
    return merchant, agreement


@merchant_required
def agreement_view(request):
    """Main agreement page — merchant reads the agreement."""
    merchant, agreement = _get_or_create_agreement(request)
    if not merchant:
        messages.error(request, "Merchant profile not found. Contact support.")
        return redirect("home")

    if agreement.status == MerchantAgreement.STATUS_NOT_STARTED:
        agreement.status = MerchantAgreement.STATUS_VIEWED
        agreement.viewed_at = timezone.now()
        agreement.save(update_fields=["status", "viewed_at"])

    return render(request, "merchants/agreement.html", {
        "merchant": merchant,
        "agreement": agreement,
        "page_title": "Merchant Participation Agreement — TengaSale",
        "company_name": COMPANY_NAME,
        "company_address": COMPANY_ADDRESS,
        "governing_law": GOVERNING_LAW,
    })


@merchant_required
def agreement_sign(request):
    """Signature page — merchant acknowledges boxes and draws signature."""
    merchant, agreement = _get_or_create_agreement(request)
    if not merchant:
        messages.error(request, "Merchant profile not found.")
        return redirect("home")

    if agreement.is_signed:
        messages.info(request, "You have already signed the agreement.")
        return redirect("merchant_agreement_status")

    if request.method == "POST":
        sig_data = request.POST.get("signature_data", "").strip()
        agreed_read = request.POST.get("agreed_read") == "on"
        agreed_good_faith = request.POST.get("agreed_good_faith") == "on"
        agreed_genuine_customers = request.POST.get("agreed_genuine_customers") == "on"
        agreed_discretion = request.POST.get("agreed_discretion") == "on"
        agreed_commissions = request.POST.get("agreed_commissions") == "on"
        agreed_electronic_signature = request.POST.get("agreed_electronic_signature") == "on"
        signer_name = request.POST.get("signer_name", "").strip()

        errors = []
        if not sig_data or sig_data == "data:," or len(sig_data) < 50:
            errors.append("Please draw your signature in the signature box.")
        if not all([agreed_read, agreed_good_faith, agreed_genuine_customers,
                    agreed_discretion, agreed_commissions, agreed_electronic_signature]):
            errors.append("Please check all acknowledgement boxes before signing.")
        if not signer_name:
            errors.append("Please enter your full name.")

        if errors:
            for err in errors:
                messages.error(request, err)
            return render(request, "merchants/agreement_sign.html", {
                "merchant": merchant,
                "agreement": agreement,
                "post_data": request.POST,
            })

        # Save signature image from base64
        try:
            if "," in sig_data:
                header, data = sig_data.split(",", 1)
            else:
                data = sig_data
            sig_bytes = base64.b64decode(data)
            sig_filename = f"sig_{agreement.reference_number}.png"
            from django.core.files.base import ContentFile
            agreement.signature_image.save(sig_filename, ContentFile(sig_bytes), save=False)
        except Exception as exc:
            logger.error("Signature save failed: %s", exc)
            messages.error(request, "Could not save signature. Please try again.")
            return render(request, "merchants/agreement_sign.html", {
                "merchant": merchant,
                "agreement": agreement,
                "post_data": request.POST,
            })

        # Get IP and user agent
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        ip = x_forwarded_for.split(",")[0].strip() if x_forwarded_for else request.META.get("REMOTE_ADDR")

        agreement.signer_name = signer_name
        agreement.business_name = merchant.business_name
        agreement.signer_phone = merchant.phone_number
        agreement.signer_email = request.user.email or ""
        agreement.agreed_read = agreed_read
        agreement.agreed_good_faith = agreed_good_faith
        agreement.agreed_genuine_customers = agreed_genuine_customers
        agreement.agreed_discretion = agreed_discretion
        agreement.agreed_commissions = agreed_commissions
        agreement.agreed_electronic_signature = agreed_electronic_signature
        agreement.status = MerchantAgreement.STATUS_SIGNED
        agreement.signed_at = timezone.now()
        agreement.ip_address = ip
        agreement.user_agent = request.META.get("HTTP_USER_AGENT", "")[:500]
        agreement.save()

        # Generate PDF
        try:
            generate_merchant_agreement_pdf(agreement)
        except Exception as exc:
            logger.error("PDF generation failed for agreement %s: %s", agreement.reference_number, exc)

        messages.success(request, "Agreement signed successfully! Welcome to TengaSale.")
        return redirect("merchant_agreement_status")

    return render(request, "merchants/agreement_sign.html", {
        "merchant": merchant,
        "agreement": agreement,
    })


@merchant_required
def agreement_status(request):
    """Shows current agreement status with download link."""
    merchant = Merchant.objects.filter(owner=request.user).first()
    if not merchant:
        messages.error(request, "Merchant profile not found.")
        return redirect("home")

    agreement = merchant.agreements.order_by("-created_at").first()
    return render(request, "merchants/agreement_status.html", {
        "merchant": merchant,
        "agreement": agreement,
        "page_title": "Agreement Status — TengaSale",
    })


@merchant_required
def agreement_pdf_download(request, agreement_id):
    """Download the signed PDF agreement."""
    merchant = get_object_or_404(Merchant, owner=request.user)
    agreement = get_object_or_404(MerchantAgreement, id=agreement_id, merchant=merchant)

    if not agreement.is_signed:
        messages.error(request, "Agreement has not been signed yet.")
        return redirect("merchant_agreement_status")

    if not agreement.pdf_file:
        # Try to regenerate
        try:
            generate_merchant_agreement_pdf(agreement)
            agreement.refresh_from_db()
        except Exception as exc:
            logger.error("PDF regen failed: %s", exc)
            messages.error(request, "PDF could not be generated. Please contact support.")
            return redirect("merchant_agreement_status")

    if not agreement.pdf_file:
        messages.error(request, "PDF not available. Please contact support.")
        return redirect("merchant_agreement_status")

    try:
        response = FileResponse(
            agreement.pdf_file.open("rb"),
            content_type="application/pdf",
        )
        filename = f"merchant-agreement-{merchant.id}-{agreement.signed_at.strftime('%Y%m%d')}.pdf"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
    except FileNotFoundError:
        messages.error(request, "PDF file not found. Please contact support.")
        return redirect("merchant_agreement_status")


@merchant_required
def agreement_pdf_view(request, agreement_id):
    """View (inline) the signed PDF agreement."""
    merchant = get_object_or_404(Merchant, owner=request.user)
    agreement = get_object_or_404(MerchantAgreement, id=agreement_id, merchant=merchant)

    if not agreement.is_signed:
        messages.error(request, "Agreement has not been signed yet.")
        return redirect("merchant_agreement_status")

    if not agreement.pdf_file:
        try:
            generate_merchant_agreement_pdf(agreement)
            agreement.refresh_from_db()
        except Exception as exc:
            logger.error("PDF regen failed: %s", exc)
            messages.error(request, "PDF not available. Please contact support.")
            return redirect("merchant_agreement_status")

    try:
        response = FileResponse(
            agreement.pdf_file.open("rb"),
            content_type="application/pdf",
        )
        filename = f"merchant-agreement-{merchant.id}.pdf"
        response["Content-Disposition"] = f'inline; filename="{filename}"'
        return response
    except FileNotFoundError:
        messages.error(request, "PDF file not found.")
        return redirect("merchant_agreement_status")
