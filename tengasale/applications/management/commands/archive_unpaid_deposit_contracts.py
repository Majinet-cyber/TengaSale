from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from applications.views import archive_unpaid_deposit_applications


class Command(BaseCommand):
    help = "Archive deposit-pending applications whose deposit was not paid within 48 hours."

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(hours=48)
        archived = archive_unpaid_deposit_applications(cutoff=cutoff)
        self.stdout.write(self.style.SUCCESS(f"Archived {archived} unpaid deposit application(s)."))
