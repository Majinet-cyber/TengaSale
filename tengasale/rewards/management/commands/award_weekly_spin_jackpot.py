from django.core.management.base import BaseCommand

from rewards.services import award_weekly_spin_jackpot


class Command(BaseCommand):
    help = "Award the weekly MWK 100,000 spin jackpot to one eligible underwriter."

    def handle(self, *args, **options):
        reward = award_weekly_spin_jackpot()
        if reward:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Awarded weekly jackpot {reward.amount} to {reward.user.username}."
                )
            )
        else:
            self.stdout.write("No weekly jackpot awarded (already awarded or no eligible underwriters).")
