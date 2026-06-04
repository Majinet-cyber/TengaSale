"""List Didit webhook destinations (no secrets)."""
from django.core.management.base import BaseCommand, CommandError

from integrations.didit import DiditAPIError, DiditConfigurationError, list_webhook_destinations


class Command(BaseCommand):
    help = "List Didit webhook destination labels, URLs, versions, and subscribed events."

    def handle(self, *args, **options):
        try:
            response = list_webhook_destinations()
        except DiditConfigurationError as exc:
            raise CommandError(str(exc)) from exc
        except DiditAPIError as exc:
            raise CommandError(exc.user_message) from exc

        destinations = response.get("results") or response.get("destinations") or response.get("data")
        if destinations is None and isinstance(response, list):
            destinations = response
        if not destinations:
            if isinstance(response, dict) and response.get("url"):
                destinations = [response]
            else:
                self.stdout.write("No webhook destinations found.")
                return

        if not isinstance(destinations, list):
            destinations = [destinations]

        for item in destinations:
            if not isinstance(item, dict):
                continue
            label = item.get("label") or "(no label)"
            url = item.get("url") or "(no url)"
            version = item.get("webhook_version") or item.get("version") or "?"
            events = item.get("subscribed_events") or item.get("events") or []
            dest_id = item.get("id") or item.get("destination_id") or "?"
            self.stdout.write(f"- [{dest_id}] {label}")
            self.stdout.write(f"    url: {url}")
            self.stdout.write(f"    version: {version}")
            self.stdout.write(f"    events: {', '.join(events) if events else '(none listed)'}")
