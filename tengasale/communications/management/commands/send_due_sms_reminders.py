from django.core.management.base import BaseCommand

from communications.tasks import send_due_sms_reminders


class Command(BaseCommand):
    help = "Send TengaSale due, payment-day, and arrears SMS reminders."

    def handle(self, *args, **options):
        counts = send_due_sms_reminders()
        self.stdout.write(self.style.SUCCESS(
            "SMS reminders complete: "
            f"due={counts['due_reminder']}, "
            f"today={counts['payment_due_today']}, "
            f"arrears={counts['arrears_reminder']}, "
            f"skipped={counts['skipped']}"
        ))
