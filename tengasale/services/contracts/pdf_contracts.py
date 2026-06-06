"""
services/contracts/pdf_contracts.py

TengaSale contract PDF generation service.

Generates signed customer contracts and completed contract PDFs
from database records using xhtml2pdf (HTML → PDF).
"""

from __future__ import annotations

import base64
import io
import logging
import mimetypes
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from django.conf import settings
from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from django.utils import timezone

logger = logging.getLogger("tengasale.contracts.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────


def generate_customer_contract_pdf(contract, *, purpose: str = "initial", generated_by=None) -> Optional[bytes]:
    """
    Generate the initial signed customer contract PDF.

    Saves the file to contract.initial_pdf and returns raw bytes.
    Returns None if PDF generation fails (never raises).
    """
    try:
        context = get_contract_context(contract)
        context["purpose"] = purpose
        context["generated_by_name"] = _user_display(generated_by)
        context["generated_at"] = timezone.now()
        context.update(_get_legal_context(contract))
        html = render_contract_html(contract, context, template="contracts/pdf/contract_initial.html")
        pdf_bytes = _html_to_pdf(html)
        if pdf_bytes:
            filename = f"contract_{contract.contract_number}_initial.pdf"
            contract.initial_pdf.save(filename, ContentFile(pdf_bytes), save=True)
            logger.info("Initial contract PDF generated for %s", contract.contract_number)
        return pdf_bytes
    except Exception:
        logger.exception("Failed to generate initial PDF for contract %s", getattr(contract, "contract_number", "?"))
        return None


def generate_master_terms_pdf(version: str = "1.0") -> Optional[bytes]:
    """
    Generate a standalone Master Terms and Conditions PDF for the given version.

    Returns raw bytes or None on failure.
    """
    try:
        from contracts.models import LegalDocumentTemplate
        doc = LegalDocumentTemplate.objects.filter(
            document_type=LegalDocumentTemplate.TYPE_MASTER_TERMS,
            version=version,
        ).first()
        if not doc:
            logger.error("Master Terms v%s not found in database. Run seed_legal_documents first.", version)
            return None
        context = {
            "doc": doc,
            "generated_at": timezone.now(),
            "version": version,
        }
        html = render_to_string("contracts/pdf/master_terms.html", context)
        return _html_to_pdf(html)
    except Exception:
        logger.exception("Failed to generate Master Terms PDF v%s", version)
        return None


def generate_contract_bundle_pdf(contract, *, generated_by=None) -> Optional[bytes]:
    """
    Generate the initial contract bundle: Contract Summary + Master Terms + Acceptance Certificate.

    Saves to contract.contract_bundle_pdf and returns raw bytes.
    """
    try:
        context = get_contract_context(contract)
        context["purpose"] = "initial_bundle"
        context["generated_by_name"] = _user_display(generated_by)
        context["generated_at"] = timezone.now()
        # Inject Master Terms doc body and acceptance records
        context.update(_get_legal_context(contract))
        html = render_to_string("contracts/pdf/contract_bundle.html", context)
        pdf_bytes = _html_to_pdf(html)
        if pdf_bytes:
            filename = f"contract_{contract.contract_number}_bundle.pdf"
            contract.contract_bundle_pdf.save(filename, ContentFile(pdf_bytes), save=True)
            logger.info("Contract bundle PDF generated for %s", contract.contract_number)
        return pdf_bytes
    except Exception:
        logger.exception("Failed to generate bundle PDF for contract %s", getattr(contract, "contract_number", "?"))
        return None


def generate_completed_contract_bundle_pdf(contract, *, generated_by=None) -> Optional[bytes]:
    """
    Generate the completed bundle: completed Contract Summary + Master Terms + Acceptance Certificate + Completion Stamp.

    Saves to contract.completed_bundle_pdf and returns raw bytes.
    """
    try:
        context = get_contract_context(contract)
        context["purpose"] = "completed_bundle"
        context["generated_by_name"] = _user_display(generated_by)
        context["generated_at"] = timezone.now()
        context.update(_get_legal_context(contract))
        context.update(_build_stamp_context(contract))
        html = render_to_string("contracts/pdf/contract_bundle_completed.html", context)
        pdf_bytes = _html_to_pdf(html)
        if pdf_bytes:
            filename = f"contract_{contract.contract_number}_bundle_completed.pdf"
            contract.completed_bundle_pdf.save(filename, ContentFile(pdf_bytes), save=True)
            logger.info("Completed bundle PDF generated for %s", contract.contract_number)
        return pdf_bytes
    except Exception:
        logger.exception("Failed to generate completed bundle PDF for contract %s", getattr(contract, "contract_number", "?"))
        return None


def generate_completed_contract_pdf(contract, *, generated_by=None) -> Optional[bytes]:
    """
    Generate the completed/stamped contract PDF.

    Saves the file to contract.completed_pdf and returns raw bytes.
    Returns None if PDF generation fails (never raises).
    """
    try:
        context = get_contract_context(contract)
        context["purpose"] = "completed"
        context["generated_by_name"] = _user_display(generated_by)
        context["generated_at"] = timezone.now()
        context.update(_get_legal_context(contract))
        stamp = _build_stamp_context(contract)
        context.update(stamp)
        html = render_contract_html(contract, context, template="contracts/pdf/contract_completed.html")
        pdf_bytes = _html_to_pdf(html)
        if pdf_bytes:
            filename = f"contract_{contract.contract_number}_completed.pdf"
            contract.completed_pdf.save(filename, ContentFile(pdf_bytes), save=True)
            logger.info("Completed contract PDF generated for %s", contract.contract_number)
        return pdf_bytes
    except Exception:
        logger.exception("Failed to generate completed PDF for contract %s", getattr(contract, "contract_number", "?"))
        return None


def get_contract_context(contract) -> dict[str, Any]:
    """
    Build the full template context dict from a Contract instance.
    Pulls in related application, deal, merchant, lock profile, etc.
    """
    app = getattr(contract, "application", None)
    deal = getattr(app, "deal", None) if app else None
    merchant_user = getattr(contract, "merchant", None)

    # Merchant info
    merchant_name = ""
    merchant_phone = ""
    if merchant_user:
        merchant_name = merchant_user.get_full_name() or merchant_user.username
        try:
            from merchants.models import Merchant
            merchant_obj = Merchant.objects.filter(owner=merchant_user).first()
            if merchant_obj:
                merchant_name = merchant_obj.business_name or merchant_name
                merchant_phone = getattr(merchant_obj, "phone_number", "") or ""
        except Exception:
            pass

    # Device details
    brand = ""
    model = ""
    specs = ""
    if deal:
        try:
            brand = deal.brand.name if deal.brand_id else ""
        except Exception:
            brand = ""
        model = getattr(deal, "name", "") or getattr(deal, "model_name", "") or str(deal)
        specs = getattr(deal, "specs", "") or getattr(deal, "variant", "") or ""

    imei = contract.imei_number or (app.imei_number if app else "") or ""
    serial_number = ""
    try:
        from device_lock.models import DeviceLockProfile
        profile = DeviceLockProfile.objects.filter(contract=contract).first()
        if profile:
            serial_number = profile.serial_number or ""
    except Exception:
        pass

    # Financial
    cash_price = contract.cash_price or 0
    deposit = contract.deposit_amount or 0
    total_loan = contract.total_loan or 0
    monthly_payment = contract.monthly_payment or 0
    daily_payment = contract.daily_payment or 0
    amount_financed = max(0, float(total_loan) - float(deposit))
    finance_charges = max(0, float(total_loan) - float(cash_price))
    total_contract_price = float(total_loan)

    # Payment term
    term_months = 0
    if monthly_payment and float(monthly_payment) > 0 and amount_financed > 0:
        term_months = round(amount_financed / float(monthly_payment))

    # Customer demographics from application
    region = district = ta = address = date_of_birth = gender = ""
    guarantor_1_name = guarantor_1_phone = ""
    guarantor_2_name = guarantor_2_phone = ""
    guarantor_3_name = guarantor_3_phone = ""
    if app:
        region = app.region or ""
        district = app.district or ""
        ta = app.traditional_authority or ""
        address = app.precise_location or app.location or ""
        date_of_birth = str(app.date_of_birth) if app.date_of_birth else ""
        gender = app.get_gender_display() if app.gender else ""
        guarantor_1_name = getattr(app, "next_of_kin_1_name", "") or ""
        guarantor_1_phone = getattr(app, "next_of_kin_1_phone", "") or ""
        guarantor_2_name = getattr(app, "next_of_kin_2_name", "") or ""
        guarantor_2_phone = getattr(app, "next_of_kin_2_phone", "") or ""
        guarantor_3_name = getattr(app, "next_of_kin_3_name", "") or ""
        guarantor_3_phone = getattr(app, "next_of_kin_3_phone", "") or ""

    # Signatures — embed as data URI so xhtml2pdf can render without HTTP/media URLs
    customer_sig_url = _filefield_data_uri(contract.customer_contract_signature)

    # Approval
    approved_by_name = ""
    approved_at = None
    if app:
        reviewer = getattr(app, "reviewed_by", None)
        if reviewer:
            approved_by_name = reviewer.get_full_name() or reviewer.username
        approved_at = getattr(app, "reviewed_at", None)

    # IMEI / KYC / lock status
    imei_status = ""
    kyc_status = "KYC documents collected"
    lock_status = ""
    if app:
        imei_status = app.get_imei_verification_status_display() if app.imei_verification_status else "Pending"
    try:
        from device_lock.models import DeviceLockProfile
        profile = DeviceLockProfile.objects.filter(contract=contract).first()
        if profile:
            lock_status = profile.get_lock_status_display()
    except Exception:
        pass

    # PayG number and customer payment URL (from linked portal PaymentContract)
    payg_number = ""
    payment_url = ""
    deposit_paid_at = None
    deposit_unlock_expires_at = None
    access_expires_at = None
    try:
        payg_number = contract.payg_number or ""
        payment_url = contract.payment_url or ""
        portal_contract = getattr(app, "payment_contract", None) if app else None
        if portal_contract:
            deposit_paid_at = portal_contract.deposit_paid_at
            deposit_unlock_expires_at = portal_contract.deposit_unlock_expires_at
            access_expires_at = portal_contract.access_expires_at
    except Exception:
        pass

    return {
        # Contract header
        "contract_number": contract.contract_number,
        "payg_number": payg_number,
        "payment_url": payment_url,
        "contract_date": contract.created_at.date() if contract.created_at else "",
        "application_id": app.application_number if app else "",
        "contract_status": contract.get_status_display(),

        # Customer
        "customer_full_name": contract.customer_name or (app.customer_name if app else ""),
        "national_id_or_passport": contract.national_id or (app.national_id if app else ""),
        "phone_number": contract.customer_phone or (app.customer_phone if app else ""),
        "physical_address": address,
        "region": region,
        "district": district,
        "ta_area": ta,
        "date_of_birth": date_of_birth,
        "gender": gender,
        "guarantor_1_name": guarantor_1_name,
        "guarantor_1_phone": guarantor_1_phone,
        "guarantor_2_name": guarantor_2_name,
        "guarantor_2_phone": guarantor_2_phone,
        "guarantor_3_name": guarantor_3_name,
        "guarantor_3_phone": guarantor_3_phone,

        # Merchant
        "merchant_name": merchant_name,
        "merchant_phone": merchant_phone,
        "sales_rep": merchant_name,

        # Device
        "device_brand": brand,
        "device_model": model,
        "device_specs": specs,
        "device_name": f"{brand} {model}".strip() or contract.deal_name,
        "imei_number": imei,
        "serial_number": serial_number,
        "retail_cash_price": cash_price,
        "deposit_paid": deposit,
        "amount_financed": amount_financed,
        "finance_charges": finance_charges,
        "total_contract_price": total_contract_price,

        # Payment — base amounts
        "contract_term_months": term_months or (contract.term_months if hasattr(contract, "term_months") else 12),
        "instalment_amount": monthly_payment,
        "weekly_instalment": round(float(daily_payment) * 7, 2) if daily_payment else 0,
        "two_week_instalment": round(float(daily_payment) * 14, 2) if daily_payment else 0,
        "two_month_instalment": round(float(monthly_payment) * 2, 2) if monthly_payment else 0,
        "daily_instalment": daily_payment,
        "payment_frequency": "Flexible - daily, weekly, monthly, or custom amount",
        "total_payable": total_contract_price,
        "payment_channels": "Airtel Money, TNM Mpamba, Bank Transfer",

        # Deposit access
        "deposit_access_days": getattr(deal, "unlock_days", None) or 7,
        "deposit_paid_at": deposit_paid_at,
        "deposit_unlock_expires_at": deposit_unlock_expires_at,
        "access_expires_at": access_expires_at,
        "activation_date": contract.active_at.date() if getattr(contract, "active_at", None) else (contract.created_at.date() if contract.created_at else ""),

        # Signatures
        "customer_sig_url": customer_sig_url,
        "customer_signature_at": contract.terms_accepted_at,
        "otp_code": contract.terms_otp_code or "",
        "otp_verified_at": contract.terms_otp_verified_at,
        "authorised_representative": "TengaSale Authorised Representative",
        "authorised_at": approved_at,
        "approved_by_name": approved_by_name,

        # Verification
        "imei_verification_status": imei_status,
        "kyc_status": kyc_status,
        "lock_status": lock_status,
        "approval_timestamp": approved_at,

        # Completion
        "completed_at": contract.completed_at,
        "ownership_transfer_at": contract.ownership_transfer_at,
    }


def _get_legal_context(contract) -> dict[str, Any]:
    """
    Fetch LegalDocumentTemplate and LegalAcceptance records for the contract.
    Used in bundle PDF templates.
    """
    master_terms_doc = None
    master_terms_body = ""
    acceptance_records = []
    master_terms_version = "1.0"
    master_terms_accepted_at = None
    master_terms_acceptance_method = ""
    master_terms_acceptance_id = ""
    summary_accepted_at = None
    summary_acceptance_id = ""

    try:
        from contracts.models import LegalDocumentTemplate, LegalAcceptance
        # Master Terms doc
        master_terms_doc = LegalDocumentTemplate.objects.filter(
            document_type=LegalDocumentTemplate.TYPE_MASTER_TERMS,
            is_active=True,
        ).order_by("-effective_from").first()
        if master_terms_doc:
            master_terms_version = master_terms_doc.version
            master_terms_body = master_terms_doc.body_html or ""

        app = getattr(contract, "application", None)
        if app:
            acceptances = LegalAcceptance.objects.filter(application=app).select_related("legal_document").order_by("-accepted_at")
            for acc in acceptances:
                acceptance_records.append(acc)
                if acc.legal_document.document_type == LegalDocumentTemplate.TYPE_MASTER_TERMS:
                    master_terms_accepted_at = acc.accepted_at
                    master_terms_acceptance_method = acc.get_acceptance_method_display()
                    master_terms_acceptance_id = str(acc.pk)
                elif acc.legal_document.document_type == LegalDocumentTemplate.TYPE_CONTRACT_SUMMARY:
                    summary_accepted_at = acc.accepted_at
                    summary_acceptance_id = str(acc.pk)
    except Exception:
        logger.exception("Error loading legal context for contract %s", getattr(contract, "contract_number", "?"))

    return {
        "master_terms_doc": master_terms_doc,
        "master_terms_body": master_terms_body,
        "master_terms_version": master_terms_version,
        "master_terms_accepted_at": master_terms_accepted_at,
        "master_terms_acceptance_method": master_terms_acceptance_method,
        "master_terms_acceptance_id": master_terms_acceptance_id,
        "summary_accepted_at": summary_accepted_at,
        "summary_acceptance_id": summary_acceptance_id,
        "legal_acceptance_records": acceptance_records,
    }


def render_contract_html(contract, context: dict, *, template: str = "contracts/pdf/contract_initial.html") -> str:
    """Render the contract HTML from a Django template."""
    return render_to_string(template, context)


def stamp_contract_pdf(pdf_bytes: bytes, stamp_context: dict) -> bytes:
    """
    Add a completion stamp page to an existing PDF.
    Currently returns a freshly generated completed PDF (stamp embedded in template).
    """
    return pdf_bytes


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────


def _build_stamp_context(contract) -> dict:
    """Build the completion record / stamp fields for the completed PDF."""
    from django.conf import settings as dj_settings
    from datetime import timedelta

    hours = getattr(dj_settings, "WHATSAPP_SEND_CONTRACT_AFTER_HOURS", 24)
    scheduled_for = None
    if contract.completed_at:
        scheduled_for = contract.completed_at + timedelta(hours=hours)

    # Lock status
    lock_release_status = "Removal scheduled within 24 hours"
    try:
        from device_lock.models import DeviceLockProfile
        profile = DeviceLockProfile.objects.filter(contract=contract).first()
        if profile:
            status = getattr(profile, "lock_status", "")
            if status in {"released", "unlocked", "removed"}:
                lock_release_status = "Released"
    except Exception:
        pass

    # Delivery record
    delivery_record_id = ""
    try:
        from contracts.models import ContractDocumentDelivery
        delivery = ContractDocumentDelivery.objects.filter(contract=contract).order_by("-created_at").first()
        if delivery:
            delivery_record_id = str(delivery.pk)
    except Exception:
        pass

    return {
        "stamp_contract_number": contract.contract_number,
        "stamp_customer": contract.customer_name,
        "stamp_customer_phone": contract.customer_phone,
        "stamp_merchant": _merchant_display(contract),
        "stamp_device": contract.deal_name,
        "stamp_imei": contract.imei_number,
        "stamp_total_contract_price": contract.total_loan,
        "stamp_total_paid": contract.total_loan,
        "stamp_completed_at": contract.completed_at,
        "stamp_generated_at": timezone.now(),
        "stamp_scheduled_for": scheduled_for,
        "stamp_delivery_record_id": delivery_record_id,
        "stamp_ownership_transfer": "Transferred" if contract.ownership_transfer_at else "Pending transfer",
        "stamp_lock_release": lock_release_status,
    }


def _merchant_display(contract) -> str:
    merchant = getattr(contract, "merchant", None)
    if merchant:
        return merchant.get_full_name() or merchant.username
    return ""


def _user_display(user) -> str:
    if user is None:
        return "TengaSale System"
    return user.get_full_name() or getattr(user, "username", "System")


def _filefield_data_uri(file_field) -> str:
    """Return a data: URI for an ImageField/FileField, or empty string."""
    if not file_field:
        return ""
    try:
        path = Path(file_field.path)
        if not path.is_file():
            return file_field.url if hasattr(file_field, "url") else ""
        mime, _ = mimetypes.guess_type(str(path))
        if not mime:
            mime = "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"
    except Exception:
        logger.warning("Could not embed file as data URI: %s", getattr(file_field, "name", ""))
        try:
            return file_field.url
        except Exception:
            return ""


def _pdf_link_callback(uri: str, rel: str) -> str:
    """Resolve /media/ and file paths for embedded images (signatures) in PDFs."""
    if not uri:
        return uri
    if uri.startswith("data:"):
        return uri
    if uri.startswith("file://"):
        return uri

    media_url = getattr(settings, "MEDIA_URL", "/media/") or "/media/"
    def _local_file_uri(rel_path: str) -> str:
        local = Path(settings.MEDIA_ROOT) / rel_path.replace("\\", "/").lstrip("/")
        if local.is_file():
            return local.resolve().as_uri()
        logger.warning("PDF image not on disk: %s", local)
        return ""

    if uri.startswith(media_url):
        resolved = _local_file_uri(uri[len(media_url):])
        if resolved:
            return resolved

    if uri.startswith("/"):
        resolved = _local_file_uri(uri.lstrip("/"))
        if resolved:
            return resolved

    if os.path.isfile(uri):
        return Path(uri).resolve().as_uri()

    return uri


def _html_to_pdf(html: str) -> Optional[bytes]:
    """Convert HTML string to PDF bytes using xhtml2pdf."""
    try:
        from xhtml2pdf import pisa

        buf = io.BytesIO()
        result = pisa.CreatePDF(
            src=io.StringIO(html),
            dest=buf,
            encoding="utf-8",
            link_callback=_pdf_link_callback,
        )
        if result.err:
            logger.error("xhtml2pdf reported errors: %s", result.err)
            return None
        return buf.getvalue()
    except Exception:
        logger.exception("PDF generation failed")
        return None
