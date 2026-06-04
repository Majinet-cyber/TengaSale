"""
Create a Didit webhook destination and print the signing secret if returned.

Usage:
    python manage.py didit_create_webhook_destination \\
        --url https://tengasale.onrender.com/api/webhooks/didit/ \\
        --label "TengaSale Production Webhook"
"""
from django.core.management.base import BaseCommand, CommandError

from integrations.didit import (
    DiditAPIError,
    DiditConfigurationError,
    create_webhook_destination,
    extract_webhook_secret_from_response,
)


class Command(BaseCommand):
    help = "Create a Didit v3 webhook destination and print the signing secret if provided."

    def add_arguments(self, parser):
        parser.add_argument(
            "--url",
            required=True,
            help="Public webhook URL (must match TengaSale route)",
        )
        parser.add_argument(
            "--label",
            default="TengaSale Production Webhook",
            help="Label shown in Didit dashboard",
        )

    def handle(self, *args, **options):
        url = (options["url"] or "").strip()
        label = (options["label"] or "").strip()
        if not url.lower().startswith("https://"):
            raise CommandError("Webhook URL must use HTTPS.")

        try:
            response = create_webhook_destination(url=url, label=label)
        except DiditConfigurationError as exc:
            raise CommandError(str(exc)) from exc
        except DiditAPIError as exc:
            if exc.status_code in (400, 409, 422):
                self.stderr.write(
                    self.style.WARNING(
                        "A destination for this URL may already exist. Delete it in the Didit "
                        "dashboard or use a different URL/label, then rerun."
                    )
                )
            raise CommandError(exc.user_message) from exc

        destination_id = response.get("id") or response.get("destination_id")
        if destination_id:
            self.stdout.write(self.style.SUCCESS(f"Destination created (id={destination_id})."))
        else:
            self.stdout.write(self.style.SUCCESS("Destination created."))

        secret = extract_webhook_secret_from_response(response)
        if secret:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("Save this as DIDIT_WEBHOOK_SECRET in Render immediately."))
            self.stdout.write(self.style.WARNING("It may not be shown again."))
            self.stdout.write("")
            self.stdout.write(secret)
        else:
            self.stdout.write(
                "Didit did not return a signing secret in the API response. "
                "Check the Didit dashboard or contact Didit support."
            )
