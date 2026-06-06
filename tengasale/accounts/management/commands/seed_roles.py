from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create TengaSale role groups."

    def handle(self, *args, **options):
        groups = [
            "Merchant",
            "Merchant Administrator",
            "Underwriter",
            "Tech Support",
            "HQ",
        ]
        for group_name in groups:
            Group.objects.get_or_create(name=group_name)

        call_command("seed_staff_system")

        self.stdout.write(self.style.SUCCESS(
            "Seeded TengaSale roles: Merchant, Merchant Administrator, Underwriter, Tech Support, HQ."
        ))
