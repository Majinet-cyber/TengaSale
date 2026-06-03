import logging
import random
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from .models import SpinConfig, SpinGrant, SpinReward, SpinWallet, WeeklySpinJackpot

logger = logging.getLogger(__name__)

TWOPLACES = Decimal("0.01")

SPIN_REWARD_TIERS = [
    Decimal("100"),
    Decimal("500"),
    Decimal("1000"),
    Decimal("2500"),
    Decimal("5000"),
    Decimal("10000"),
    Decimal("25000"),
    Decimal("50000"),
]


class NoSpinsAvailable(Exception):
    pass


class SpinDisabled(Exception):
    pass


def get_spin_config():
    config, _ = SpinConfig.objects.get_or_create(pk=1)
    return config


@transaction.atomic
def award_spin_for_application(application, user):
    wallet, _ = SpinWallet.objects.select_for_update().get_or_create(user=user)
    _, created = SpinGrant.objects.get_or_create(application=application, user=user)
    if not created:
        return False

    wallet.available_spins += 1
    wallet.total_spins_earned += 1
    wallet.save(update_fields=["available_spins", "total_spins_earned"])
    return True


def current_week_start():
    now = timezone.localtime()
    start = now - timedelta(days=now.weekday())
    return start.replace(hour=0, minute=0, second=0, microsecond=0).date()


def jackpot_count_this_week(config):
    week_start = current_week_start()
    return WeeklySpinJackpot.objects.filter(week_start=week_start).count()


def reward_tier_for_amount(amount, config):
    if amount == config.jackpot_amount:
        return SpinReward.TIER_JACKPOT
    if amount >= Decimal("25000"):
        return SpinReward.TIER_BIG
    if amount >= Decimal("2500"):
        return SpinReward.TIER_MEDIUM
    return SpinReward.TIER_SMALL


def reward_pool(config):
    rewards = [
        Decimal("100"), Decimal("100"), Decimal("100"),
        Decimal("500"), Decimal("500"),
        Decimal("1000"), Decimal("1000"),
        Decimal("2500"), Decimal("5000"), Decimal("10000"),
        Decimal("25000"), Decimal("50000"),
    ]
    configured = config.small_rewards or []
    for value in configured:
        try:
            rewards.append(Decimal(str(value)))
        except Exception:
            continue
    if jackpot_count_this_week(config) < config.jackpot_limit_per_week:
        rewards.append(config.jackpot_amount)
    return rewards


@transaction.atomic
def perform_spin(user, chooser=None):
    config = get_spin_config()
    if not config.is_enabled:
        raise SpinDisabled("Spin rewards are currently disabled.")

    wallet, _ = SpinWallet.objects.select_for_update().get_or_create(user=user)
    if wallet.available_spins <= 0:
        raise NoSpinsAvailable("No spins available.")

    chooser = chooser or random.choice
    amount = Decimal(chooser(reward_pool(config))).quantize(TWOPLACES)
    if amount == config.jackpot_amount and jackpot_count_this_week(config) >= config.jackpot_limit_per_week:
        amount = Decimal("1000.00")

    wallet.available_spins -= 1
    wallet.total_spins_used += 1
    wallet.save(update_fields=["available_spins", "total_spins_used"])

    reward = SpinReward.objects.create(
        user=user,
        amount=amount,
        spin_date=timezone.now(),
        reward_tier=reward_tier_for_amount(amount, config),
        is_weekly_jackpot=amount == config.jackpot_amount,
    )
    if amount == config.jackpot_amount:
        WeeklySpinJackpot.objects.get_or_create(
            week_start=current_week_start(),
            defaults={
                "winner": user,
                "reward": reward,
                "amount": amount,
            },
        )
    return reward


def eligible_underwriters_for_week(week_start=None):
    """Underwriters with at least one approval or spin grant in the week."""
    from accounts.utils import is_underwriter

    week_start = week_start or current_week_start()
    week_end = week_start + timedelta(days=7)
    User = get_user_model()

    approved_ids = set(
        SpinGrant.objects.filter(created_at__date__gte=week_start, created_at__date__lt=week_end)
        .values_list("user_id", flat=True)
    )

    from applications.models import FinancingApplication

    reviewed_ids = set(
        FinancingApplication.objects.filter(
            reviewed_by__isnull=False,
            status="approved",
            updated_at__date__gte=week_start,
            updated_at__date__lt=week_end,
        ).values_list("reviewed_by_id", flat=True)
    )

    candidate_ids = approved_ids | reviewed_ids
    return [u for u in User.objects.filter(id__in=candidate_ids) if is_underwriter(u)]


@transaction.atomic
def award_weekly_spin_jackpot(force=False):
    """
    Guarantee one MWK 100,000 weekly jackpot among eligible underwriters.
    Returns the SpinReward or None.
    """
    config = get_spin_config()
    week_start = current_week_start()

    if WeeklySpinJackpot.objects.filter(week_start=week_start).exists():
        return None
    if not force and jackpot_count_this_week(config) >= config.jackpot_limit_per_week:
        return None

    eligible = eligible_underwriters_for_week(week_start)
    if not eligible:
        logger.info("No eligible underwriters for weekly jackpot week %s", week_start)
        return None

    winner = random.choice(eligible)
    reward = SpinReward.objects.create(
        user=winner,
        amount=config.jackpot_amount,
        spin_date=timezone.now(),
        reward_tier=SpinReward.TIER_JACKPOT,
        is_weekly_jackpot=True,
    )
    WeeklySpinJackpot.objects.create(
        week_start=week_start,
        winner=winner,
        reward=reward,
        amount=config.jackpot_amount,
    )
    logger.info("Awarded weekly spin jackpot MWK %s to %s for week %s", config.jackpot_amount, winner.username, week_start)
    return reward
