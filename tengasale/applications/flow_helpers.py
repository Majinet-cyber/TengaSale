"""Merchant application step routing and guardrails."""

from __future__ import annotations

import logging
from decimal import Decimal

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse

from core.commercial import ALLOWED_CONTRACT_TERMS, pricing_from_application

logger = logging.getLogger("tengasale.applications")

KYC_IMAGE_FIELDS = ("customer_face_image", "id_front_image", "id_back_image")

KYC_IMAGE_LABELS = {
    "customer_face_image": "Selfie",
    "id_front_image": "ID Card Front",
    "id_back_image": "ID Card Back",
}


def kyc_image_field_present(field_file) -> bool:
    """True when the model field has a file name (matches template {% if app.field %})."""
    return bool(field_file and getattr(field_file, "name", ""))


def stored_file_exists(field_file):
    if not kyc_image_field_present(field_file):
        return False
    try:
        return field_file.storage.exists(field_file.name)
    except Exception:
        logger.exception("Could not verify uploaded file exists: %s", field_file.name)
        return kyc_image_field_present(field_file)


def kyc_image_field_ready(field_file) -> bool:
    """True when the image is present and reachable (storage check with name fallback)."""
    return stored_file_exists(field_file)


def kyc_images_complete(app) -> bool:
    return all(kyc_image_field_ready(getattr(app, field_name)) for field_name in KYC_IMAGE_FIELDS)


def kyc_missing_image_labels(app) -> list[str]:
    return [
        KYC_IMAGE_LABELS[field_name]
        for field_name in KYC_IMAGE_FIELDS
        if not kyc_image_field_ready(getattr(app, field_name))
    ]


def kyc_completion_flags(app) -> dict[str, bool]:
    return {
        field_name: kyc_image_field_ready(getattr(app, field_name))
        for field_name in KYC_IMAGE_FIELDS
    }


def customer_details_complete(app) -> bool:
    return bool(
        (app.customer_name or "").strip()
        and (app.national_id or "").strip()
        and (app.customer_phone or "").strip()
    )


def _customer_details_complete(app) -> bool:
    return customer_details_complete(app)


def application_has_complete_deal(app) -> bool:
    if not app.deal_id:
        return False
    cash = Decimal(str(app.selected_cash_price or 0))
    if cash <= 0:
        return False
    pricing = pricing_from_application(app)
    if pricing is None:
        return False
    term = getattr(app, "term_months", None)
    try:
        term_ok = int(term) in ALLOWED_CONTRACT_TERMS
    except (TypeError, ValueError):
        term_ok = False
    return term_ok


def location_step_complete(app) -> bool:
    return bool((app.region or "").strip() and (app.precise_location or app.location or "").strip())


def work_step_complete(app) -> bool:
    return bool((app.work_description or "").strip())


def merchant_application_continue_url(app) -> str:
    """Next merchant step for in-progress applications."""
    if app.status in {"rejected", "contract_complete", "completed", "active_contract"}:
        return reverse("application_detail", args=[app.id])

    if app.status in {"submitted", "pending_review", "resubmitted", "under_review"}:
        return reverse("application_detail", args=[app.id])

    if app.status in {"correction_requested", "sent_back"}:
        return reverse("application_corrections", args=[app.id])

    if app.status in {"started", "customer_details"}:
        if _customer_details_complete(app):
            return reverse("didit_verification", args=[app.id])
        return reverse("edit_customer_details", args=[app.id])

    if app.status in {"approved", "approved_pending_device_lock", "imei_required"}:
        return reverse("capture_imei", args=[app.id])

    if app.status == "device_locked":
        return reverse("contract_terms", args=[app.id])

    if not application_has_complete_deal(app):
        return reverse("choose_device", args=[app.id])

    if not kyc_images_complete(app):
        return reverse("kyc_capture", args=[app.id])

    if not location_step_complete(app):
        return reverse("location_details", args=[app.id])

    if not work_step_complete(app):
        return reverse("work_details", args=[app.id])

    return reverse("application_review", args=[app.id])


def redirect_if_deal_required(request, app):
    if application_has_complete_deal(app):
        return None
    messages.warning(request, "Select a phone deal before continuing.")
    return redirect("choose_device", app_id=app.id)


def redirect_if_kyc_required(request, app):
    if kyc_images_complete(app):
        return None
    messages.warning(request, "Complete KYC photos before continuing.")
    return redirect("kyc_capture", app_id=app.id)


def merchant_next_step_after_deal_selection(app) -> str:
    """Route after deal is saved: KYC, location, work, signature, review."""
    if not kyc_images_complete(app):
        return reverse("kyc_capture", args=[app.id])
    if not location_step_complete(app):
        return reverse("location_details", args=[app.id])
    if not work_step_complete(app):
        return reverse("work_details", args=[app.id])
    return reverse("application_review", args=[app.id])
