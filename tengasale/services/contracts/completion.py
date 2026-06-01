"""
services/contracts/completion.py

Contract completion orchestration:
- Mark contract completed
- Generate completed PDF
- Schedule WhatsApp delivery
- Log audit events

Called from contract_progress view on deposit/completion action.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("tengasale.contracts.completion")


def handle_contract_completion(contract, *, triggered_by=None) -> dict:
    """
    Run all post-completion steps for a contract.

    Steps:
    1. Set completed_at / ownership_transfer_at if not already set
    2. Generate completed PDF
    3. Schedule WhatsApp delivery 24h later
    4. Audit log

    Never raises — returns a status dict for the caller to display.
    """
    result = {
        "pdf_generated": False,
        "delivery_scheduled": False,
        "delivery_id": None,
        "errors": [],
    }

    now = timezone.now()

    # 1. Set completion timestamps
    save_fields = []
    if not contract.completed_at:
        contract.completed_at = now
        save_fields.append("completed_at")
    if not contract.ownership_transfer_at:
        contract.ownership_transfer_at = now
        save_fields.append("ownership_transfer_at")
    if save_fields:
        contract.save(update_fields=save_fields + ["updated_at"])

    # 2. Generate completed PDF
    try:
        from services.contracts.pdf_contracts import generate_completed_contract_pdf
        pdf_bytes = generate_completed_contract_pdf(contract, generated_by=triggered_by)
        if pdf_bytes:
            result["pdf_generated"] = True
        else:
            result["errors"].append("PDF generation returned no content")
    except Exception as exc:
        logger.exception("Completed PDF generation failed for contract %s", contract.pk)
        result["errors"].append(f"PDF error: {exc}")

    # 3. Schedule WhatsApp delivery
    try:
        delivery = _schedule_whatsapp_delivery(contract, triggered_by=triggered_by)
        if delivery:
            result["delivery_scheduled"] = True
            result["delivery_id"] = delivery.pk
    except Exception as exc:
        logger.exception("WhatsApp scheduling failed for contract %s", contract.pk)
        result["errors"].append(f"Delivery scheduling error: {exc}")

    # 4. Audit log
    try:
        _log_completion_audit(contract, triggered_by=triggered_by)
    except Exception:
        logger.exception("Audit log failed for contract completion %s", contract.pk)

    return result


def _schedule_whatsapp_delivery(contract, *, triggered_by=None):
    """
    Create a ContractDocumentDelivery record scheduled for completed_at + 24h.
    Idempotent — does not create a duplicate if one already exists.
    """
    from contracts.models import ContractDocumentDelivery
    from services.messaging.phone import try_normalize_malawi_phone

    # Idempotency check
    existing = ContractDocumentDelivery.objects.filter(
        contract=contract,
        channel=ContractDocumentDelivery.CHANNEL_WHATSAPP,
        delivery_status__in=[
            ContractDocumentDelivery.STATUS_SCHEDULED,
            ContractDocumentDelivery.STATUS_SENT,
        ],
    ).first()
    if existing:
        logger.info("WhatsApp delivery already scheduled/sent for contract %s", contract.pk)
        return existing

    hours = getattr(settings, "WHATSAPP_SEND_CONTRACT_AFTER_HOURS", 24)
    completed_at = contract.completed_at or timezone.now()
    scheduled_for = completed_at + timedelta(hours=hours)

    raw_phone = contract.customer_phone or ""
    normalized_phone, phone_error = try_normalize_malawi_phone(raw_phone)

    provider = getattr(settings, "WHATSAPP_PROVIDER", "mock")

    # Build message text
    device_name = contract.deal_name or "your device"
    completed_str = completed_at.strftime("%d %b %Y, %H:%M")
    message_text = (
        f"Hello {contract.customer_name},\n\n"
        f"Your TengaSale smartphone financing contract has been completed. "
        f"Attached is your signed contract copy for your records.\n\n"
        f"Contract: {contract.contract_number}\n"
        f"Device: {device_name}\n"
        f"Completed: {completed_str}\n\n"
        f"Thank you for using TengaSale."
    )

    delivery_status = ContractDocumentDelivery.STATUS_SCHEDULED
    error_message = ""
    if not normalized_phone:
        delivery_status = ContractDocumentDelivery.STATUS_SKIPPED
        error_message = phone_error or "Invalid or missing customer WhatsApp number"
        logger.warning(
            "WhatsApp delivery skipped for contract %s — %s", contract.pk, error_message
        )

    delivery = ContractDocumentDelivery.objects.create(
        contract=contract,
        application=getattr(contract, "application", None),
        customer_phone=raw_phone,
        normalized_customer_phone=normalized_phone,
        channel=ContractDocumentDelivery.CHANNEL_WHATSAPP,
        delivery_status=delivery_status,
        scheduled_for=scheduled_for,
        completed_at_snapshot=completed_at,
        pdf_file=contract.completed_pdf if contract.completed_pdf else None,
        message_text=message_text,
        provider=provider,
        error_message=error_message,
        triggered_by=triggered_by,
    )
    logger.info(
        "WhatsApp delivery %s scheduled for contract %s at %s",
        delivery.pk,
        contract.contract_number,
        scheduled_for,
    )
    return delivery


def _log_completion_audit(contract, *, triggered_by=None):
    """Record contract completion in the AuditLog."""
    try:
        from core.models import AuditLog
        AuditLog.objects.create(
            user=triggered_by,
            action="contract_complete",
            object_type="Contract",
            object_id=str(contract.pk),
            detail={
                "contract_number": contract.contract_number,
                "completed_at": str(contract.completed_at),
                "customer": contract.customer_name,
            },
        )
    except Exception:
        logger.exception("Failed to write completion audit log for contract %s", contract.pk)


def regenerate_completed_pdf(contract, *, triggered_by=None) -> bool:
    """Regenerate the completed PDF for an already-completed contract."""
    try:
        from services.contracts.pdf_contracts import generate_completed_contract_pdf
        pdf_bytes = generate_completed_contract_pdf(contract, generated_by=triggered_by)
        if pdf_bytes:
            logger.info("Regenerated completed PDF for contract %s", contract.contract_number)
            return True
        return False
    except Exception:
        logger.exception("Regeneration failed for contract %s", contract.pk)
        return False
