"""
management command: device_lock_sync

Syncs lock statuses for all active, enrolled DeviceLockProfiles.
"""

from django.core.management.base import BaseCommand
from device_lock.models import DeviceLockProfile, DeviceLockEvent


class Command(BaseCommand):
    help = "Sync device lock statuses from the configured provider for all enrolled profiles."

    def handle(self, *args, **options):
        from services.device_lock.service import sync_device_status

        profiles = DeviceLockProfile.objects.filter(
            lock_status__in=[
                DeviceLockProfile.STATUS_ENROLLED,
                DeviceLockProfile.STATUS_ACTIVE,
                DeviceLockProfile.STATUS_LOCKED,
                DeviceLockProfile.STATUS_UNLOCKED,
                DeviceLockProfile.STATUS_LOCK_PENDING,
                DeviceLockProfile.STATUS_UNLOCK_PENDING,
            ],
            is_released=False,
        )

        total = profiles.count()
        self.stdout.write(f"Syncing {total} active lock profile(s)...")

        success_count = 0
        failure_count = 0

        for profile in profiles:
            result = sync_device_status(
                profile,
                trigger_source=DeviceLockEvent.SOURCE_MANAGEMENT_COMMAND,
            )
            if result.get("success"):
                success_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓ IMEI {profile.imei} → {profile.lock_status}"
                    )
                )
            else:
                failure_count += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"  ✗ IMEI {profile.imei}: {result.get('error', 'unknown error')}"
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nSync complete. {success_count} succeeded, {failure_count} failed."
            )
        )
