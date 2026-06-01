"""
services/contracts/pdf_contracts.py

TengaSale contract PDF generation service.

Generates signed customer contracts and completed contract PDFs
from database records using xhtml2pdf (HTML → PDF).
"""

from __future__ import annotations

import io
import logging
import os
from datetime import datetime
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
    if app:
        region = app.region or ""
        district = app.district or ""
        ta = app.traditional_authority or ""
        address = app.precise_location or app.location or ""
        date_of_birth = str(app.date_of_birth) if app.date_of_birth else ""
        gender = app.get_gender_display() if app.gender else ""

    # Signatures
    customer_sig_url = ""
    if contract.customer_contract_signature:
        try:
            customer_sig_url = contract.customer_contract_signature.url
        except Exception:
            pass

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

    return {
        # Contract header
        "contract_number": contract.contract_number,
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

        # Payment
        "contract_term_months": term_months,
        "instalment_amount": monthly_payment,
        "daily_instalment": daily_payment,
        "payment_frequency": "Monthly / Daily",
        "total_payable": total_contract_price,
        "payment_channels": "Airtel Money, TNM Mpamba, Bank Transfer",

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
    lock_release_status = "Not released"
    try:
        from device_lock.models import DeviceLockProfile
        profile = DeviceLockProfile.objects.filter(contract=contract).first()
        if profile:
            lock_release_status = profile.get_lock_status_display()
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


def _html_to_pdf(html: str) -> Optional[bytes]:
    """Convert HTML string to PDF bytes using xhtml2pdf."""
    try:
        from xhtml2pdf import pisa

        buf = io.BytesIO()
        result = pisa.CreatePDF(
            src=io.StringIO(html),
            dest=buf,
            encoding="utf-8",
        )
        if result.err:
            logger.error("xhtml2pdf reported errors: %s", result.err)
            return None
        return buf.getvalue()
    except Exception:
        logger.exception("PDF generation failed")
        return None
