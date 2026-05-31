"""
management command: device_lock_evaluate

Evaluate all active contracts against the lock policy.
Prints recommended lock/unlock/release actions.
Use --execute to apply changes (default: dry run).
"""

from django.core.management.base import BaseCommand
from contracts.models import Contract
from device_lock.models import DeviceLockProfile, DeviceLockEvent


class Command(BaseCommand):
    help = "Evaluate lock policy for all active contracts and recommend/execute lock actions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=True,
            help="Print recommended actions without executing (default).",
        )
        parser.add_argument(
            "--execute",
            action="store_true",
            default=False,
            help="Actually execute the lock/unlock/release actions.",
        )

    def handle(self, *args, **options):
        from services.device_lock.service import (
            evaluate_lock_policy, lock_device, unlock_device, release_device,
        )

        execute = options.get("execute", False)
        mode = "EXECUTE" if execute else "DRY-RUN"

        self.stdout.write(f"\n🔐 Device Lock Policy Evaluation — mode: {mode}\n{'='*60}")

        contracts = Contract.objects.exclude(
            status__in=[Contract.STATUS_DRAFT]
        ).select_related("application")

        total = contracts.count()
        self.stdout.write(f"Evaluating {total} contract(s)...\n")

        counts = {"lock": 0, "unlock": 0, "release": 0, "none": 0, "lock_recommended": 0}

        for contract in contracts:
            evaluation = evaluate_lock_policy(contract)
            action = evaluation.get("action", "none")
            reason = evaluation.get("reason", "")
            profile = evaluation.get("profile")
            counts[action if action in counts else "none"] += 1

            if action == "none":
                continue

            imei = profile.imei if profile else "N/A"
            contract_num = contract.contract_number

            self.stdout.write(
                f"  [{action.upper():20}] {contract_num} IMEI:{imei} — {reason}"
            )

            if execute and profile and action in ("lock", "unlock", "release"):
                result = None
                if action == "lock":
                    result = lock_device(
                        profile,
                        reason=reason,
                        trigger_source=DeviceLockEvent.SOURCE_MANAGEMENT_COMMAND,
                    )
                elif action == "unlock":
                    result = unlock_device(
                        profile,
                        reason=reason,
                        trigger_source=DeviceLockEvent.SOURCE_MANAGEMENT_COMMAND,
                    )
                elif action == "release":
                    result = release_device(
                        profile,
                        reason=reason,
                        trigger_source=DeviceLockEvent.SOURCE_MANAGEMENT_COMMAND,
                    )

                if result:
                    status = "✓ done" if result.get("success") else f"✗ {result.get('error')}"
                    self.stdout.write(f"         → {status}")

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(
            f"Summary: lock={counts['lock']} unlock={counts['unlock']} "
            f"release={counts['release']} lock_recommended={counts['lock_recommended']} "
            f"no_action={counts['none']}"
        )
        if not execute:
            self.stdout.write(
                self.style.WARNING(
                    "\nThis was a dry run. Pass --execute to apply actions."
                )
            )
