from datetime import timedelta

from django.conf import settings
from django.core.mail import mail_admins
from django.core.management.base import BaseCommand
from django.utils import timezone

from payments.models import AirtelCallbackLog, AirtelTransaction


class Command(BaseCommand):
    help = "Report actionable Airtel callback observability alerts."

    def add_arguments(self, parser):
        parser.add_argument("--notify", action="store_true", help="Email configured Django administrators when alerts exist.")

    def handle(self, *args, **options):
        timeout = int(getattr(settings, "AIRTEL_PENDING_CALLBACK_TIMEOUT_MINUTES", 30))
        cutoff = timezone.now() - timedelta(minutes=timeout)
        unresolved = AirtelCallbackLog.objects.filter(extracted_status=AirtelTransaction.STATUS_SUCCESS, processing_state__in=["UNMATCHED", "AMBIGUOUS"])
        failed = AirtelCallbackLog.objects.filter(processing_state="PROCESSING_FAILED")
        pending = AirtelTransaction.objects.filter(status__in=[AirtelTransaction.STATUS_INITIATED, AirtelTransaction.STATUS_PENDING], created_at__lte=cutoff, callback_logs__isnull=True)
        message = f"success_unresolved={unresolved.count()} processing_failed={failed.count()} pending_without_callback={pending.count()} timeout_minutes={timeout}"
        self.stdout.write(message)
        if options["notify"] and (unresolved.exists() or failed.exists() or pending.exists()):
            mail_admins("TengaSale Airtel callback alert", message, fail_silently=False)
