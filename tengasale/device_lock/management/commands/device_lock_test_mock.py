"""
management command: device_lock_test_mock

Create or use a test DeviceLockProfile and run a full enroll → lock → unlock → release
cycle using the mock provider. Useful for verifying the integration layer works end-to-end.
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run a full mock device lock cycle (enroll → lock → unlock → release) for testing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--imei",
            type=str,
            default="TEST1234567890",
            help="IMEI to test with (default: TEST1234567890).",
        )

    def handle(self, *args, **options):
        from django.conf import settings
        from device_lock.models import DeviceLockProfile, DeviceLockEvent
        from services.device_lock.service import (
            enroll_device, lock_device, unlock_device, release_device, get_lock_readiness,
        )

        imei = options["imei"]
        self.stdout.write(f"\n🧪 Device Lock Mock Test — IMEI: {imei}")
        self.stdout.write("=" * 60)

        # Get or create test profile
        profile, created = DeviceLockProfile.objects.get_or_create(
            imei=imei,
            defaults={
                "lock_provider": "mock",
                "lock_status": DeviceLockProfile.STATUS_NOT_ENROLLED,
                "is_lockable": True,
                "customer_name": "Test Customer",
                "deal_name": "Test Smartphone Deal",
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created new test lock profile (pk={profile.pk})"))
        else:
            # Reset for clean test
            profile.lock_provider = "mock"
            profile.lock_status = DeviceLockProfile.STATUS_NOT_ENROLLED
            profile.is_released = False
            profile.provider_device_id = None
            profile.last_error = None
            profile.save()
            self.stdout.write(f"Using existing profile (pk={profile.pk}), reset to not_enrolled")

        # Check readiness
        readiness = get_lock_readiness(profile)
        lock_enabled = getattr(settings, "DEVICE_LOCK_ENABLED", False)
        self.stdout.write(f"\nDEVICE_LOCK_ENABLED = {lock_enabled}")
        self.stdout.write(f"Readiness: {'READY' if readiness['ready'] else 'NOT READY — ' + ', '.join(readiness['reasons'])}")

        steps = [
            ("enroll", enroll_device),
            ("lock", lock_device),
            ("unlock", unlock_device),
            ("release", release_device),
        ]

        for step_name, step_fn in steps:
            self.stdout.write(f"\n── Step: {step_name.upper()} ──")
            if step_name in ("lock", "unlock", "release"):
                result = step_fn(
                    profile,
                    reason=f"Test {step_name} via management command",
                    trigger_source=DeviceLockEvent.SOURCE_MANAGEMENT_COMMAND,
                )
            else:
                result = step_fn(
                    profile,
                    trigger_source=DeviceLockEvent.SOURCE_MANAGEMENT_COMMAND,
                )

            success = result.get("success", False)
            profile.refresh_from_db()

            if success:
                self.stdout.write(self.style.SUCCESS(f"  ✓ {result.get('message', 'OK')}"))
                self.stdout.write(f"    Status now: {profile.lock_status}")
                if result.get("provider_device_id"):
                    self.stdout.write(f"    Provider Device ID: {result['provider_device_id']}")
            else:
                if lock_enabled:
                    self.stdout.write(self.style.ERROR(f"  ✗ {result.get('error', 'failed')}"))
                else:
                    self.stdout.write(f"  ℹ {result.get('message', 'disabled')} (lock disabled in settings)")

        # Print event count
        event_count = DeviceLockEvent.objects.filter(lock_profile=profile).count()
        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"Events logged: {event_count}")
        self.stdout.write(self.style.SUCCESS("\nMock test complete."))

        if not lock_enabled:
            self.stdout.write(
                self.style.WARNING(
                    "\nNote: DEVICE_LOCK_ENABLED=False — actions were no-ops. "
                    "Set DEVICE_LOCK_ENABLED=True to test with the mock provider."
                )
            )
