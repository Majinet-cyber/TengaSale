"""
contracts/management/commands/send_scheduled_contract_documents.py

Management command to send scheduled WhatsApp contract documents.

Usage:
  python manage.py send_scheduled_contract_documents
  python manage.py send_scheduled_contract_documents --dry-run
  python manage.py send_scheduled_contract_documents --limit 50
  python manage.py send_scheduled_contract_documents --contract-id <id>
  python manage.py send_scheduled_contract_documents --contract-id <id> --force

Schedule: Run every 15–60 minutes via cron / Render cron job.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger("tengasale.contracts.delivery")

MAX_ATTEMPTS = 3


class Command(BaseCommand):
    help = "Send scheduled WhatsApp contract completion documents."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            dest="dry_run",
            help="Show what would be sent without actually sending.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=50,
            help="Maximum number of deliveries to process in one run (default: 50).",
        )
        parser.add_argument(
            "--contract-id",
            type=int,
            dest="contract_id",
            help="Only process deliveries for the given contract ID.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Send even if delivery is not yet due (combined with --contract-id).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]
        contract_id = options.get("contract_id")
        force = options.get("force", False)

        if dry_run:
            self.stdout.write(self.style.WARNING("[DRY RUN] No messages will be sent."))

        from contracts.models import ContractDocumentDelivery
        from services.messaging.registry import get_whatsapp_provider, is_whatsapp_enabled

        now = timezone.now()

        qs = ContractDocumentDelivery.objects.filter(
            channel=ContractDocumentDelivery.CHANNEL_WHATSAPP,
            delivery_status=ContractDocumentDelivery.STATUS_SCHEDULED,
        ).select_related("contract", "contract__application", "contract__merchant")

        if contract_id:
            qs = qs.filter(contract_id=contract_id)
            if force:
                # Allow sending before scheduled_for
                self.stdout.write(f"Force-sending delivery for contract ID {contract_id}")
            else:
                qs = qs.filter(scheduled_for__lte=now)
        else:
            qs = qs.filter(scheduled_for__lte=now)

        qs = qs.order_by("scheduled_for")[:limit]

        deliveries = list(qs)
        self.stdout.write(f"Found {len(deliveries)} delivery(ies) due.")

        if not deliveries:
            self.stdout.write("Nothing to send.")
            return

        if not is_whatsapp_enabled():
            self.stdout.write(
                self.style.WARNING(
                    "MESSAGING_ENABLED / WHATSAPP_DELIVERY_ENABLED is False — skipping all sends."
                )
            )
            if not dry_run:
                return

        provider = get_whatsapp_provider()
        self.stdout.write(f"Using provider: {provider.provider_name}")

        sent_count = 0
        failed_count = 0

        for delivery in deliveries:
            contract = delivery.contract
            label = f"Delivery #{delivery.pk} (Contract {contract.contract_number})"
            self.stdout.write(f"Processing {label} -> {delivery.normalized_customer_phone or delivery.customer_phone}")

            if not delivery.normalized_customer_phone:
                self._mark_failed(delivery, "Invalid or missing customer WhatsApp number", dry_run=dry_run)
                failed_count += 1
                self.stdout.write(self.style.ERROR(f"  Skipped: no valid phone number"))
                continue

            # Resolve PDF URL
            pdf_url = self._resolve_pdf_url(delivery, contract)

            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  [DRY RUN] Would send to {delivery.normalized_customer_phone} | url={pdf_url or '(no url)'}"
                    )
                )
                continue

            # Update attempt tracking
            delivery.attempt_count += 1
            delivery.last_attempt_at = now
            delivery.save(update_fields=["attempt_count", "last_attempt_at", "updated_at"])

            # Send
            result = provider.send_whatsapp_document(
                to_phone=delivery.normalized_customer_phone,
                document_url=pdf_url or "",
                filename=f"TengaSale_Contract_{contract.contract_number}.pdf",
                message=delivery.message_text,
            )

            if result.get("success"):
                delivery.delivery_status = ContractDocumentDelivery.STATUS_SENT
                delivery.sent_at = timezone.now()
                delivery.provider_message_id = result.get("provider_message_id", "")
                delivery.provider_response = result.get("raw_response")
                delivery.error_message = ""
                delivery.save(update_fields=[
                    "delivery_status", "sent_at", "provider_message_id",
                    "provider_response", "error_message", "updated_at",
                ])
                sent_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  Sent — msg_id={result.get('provider_message_id', '')}"
                    )
                )
                self._audit_sent(delivery, contract)
            else:
                error = result.get("error", "Unknown error")
                if delivery.attempt_count >= MAX_ATTEMPTS:
                    delivery.delivery_status = ContractDocumentDelivery.STATUS_FAILED
                else:
                    # Retry later — keep scheduled
                    pass
                delivery.error_message = error
                delivery.provider_response = result.get("raw_response")
                delivery.save(update_fields=[
                    "delivery_status", "error_message", "provider_response", "updated_at",
                ])
                failed_count += 1
                self.stdout.write(self.style.ERROR(f"  Failed: {error}"))
                self._audit_failed(delivery, contract, error)

        self.stdout.write("")
        self.stdout.write(
            f"Done. Sent: {sent_count} | Failed: {failed_count} | Total: {len(deliveries)}"
        )

    def _resolve_pdf_url(self, delivery, contract) -> str:
        """
        Resolve the PDF URL for the delivery.

        Uses stored pdf_url if available, otherwise builds from MEDIA_URL.
        If CONTRACT_PDF_PUBLIC_URL_MODE=True and CONTRACT_PDF_BASE_URL is set,
        build an absolute URL.
        """
        if delivery.pdf_url:
            return delivery.pdf_url

        pdf_file = delivery.pdf_file or contract.completed_pdf
        if not pdf_file:
            return ""

        public_mode = getattr(settings, "CONTRACT_PDF_PUBLIC_URL_MODE", False)
        base_url = getattr(settings, "CONTRACT_PDF_BASE_URL", "").rstrip("/")

        if public_mode and base_url:
            url = f"{base_url}{settings.MEDIA_URL}{pdf_file.name}"
            # Store for future use
            delivery.pdf_url = url
            delivery.save(update_fields=["pdf_url", "updated_at"])
            return url

        # Fall back: return MEDIA_URL path (may not be accessible externally)
        try:
            return pdf_file.url
        except Exception:
            return ""

    def _mark_failed(self, delivery, reason: str, *, dry_run: bool = False):
        if dry_run:
            return
        delivery.delivery_status = delivery.STATUS_FAILED
        delivery.error_message = reason
        delivery.save(update_fields=["delivery_status", "error_message", "updated_at"])

    def _audit_sent(self, delivery, contract):
        try:
            from core.models import AuditLog
            AuditLog.objects.create(
                user=delivery.triggered_by,
                action="contract_pdf_sent",
                object_type="ContractDocumentDelivery",
                object_id=str(delivery.pk),
                detail={
                    "contract_number": contract.contract_number,
                    "customer_phone": delivery.normalized_customer_phone,
                    "provider": delivery.provider,
                    "provider_message_id": delivery.provider_message_id,
                },
            )
        except Exception:
            logger.exception("Audit log failed for sent delivery %s", delivery.pk)

    def _audit_failed(self, delivery, contract, error: str):
        try:
            from core.models import AuditLog
            AuditLog.objects.create(
                user=None,
                action="contract_pdf_failed",
                object_type="ContractDocumentDelivery",
                object_id=str(delivery.pk),
                detail={
                    "contract_number": contract.contract_number,
                    "customer_phone": delivery.normalized_customer_phone,
                    "provider": delivery.provider,
                    "error": error,
                    "attempt_count": delivery.attempt_count,
                },
            )
        except Exception:
            logger.exception("Audit log failed for failed delivery %s", delivery.pk)
