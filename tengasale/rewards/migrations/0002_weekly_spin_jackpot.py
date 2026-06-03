# Generated manually for spin jackpot tracking

from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("rewards", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="spinreward",
            name="is_weekly_jackpot",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="spinconfig",
            name="jackpot_amount",
            field=models.DecimalField(decimal_places=2, default=Decimal("100000.00"), max_digits=14),
        ),
        migrations.AlterField(
            model_name="spinreward",
            name="reward_tier",
            field=models.CharField(
                choices=[
                    ("small", "Small"),
                    ("medium", "Medium"),
                    ("big", "Big"),
                    ("jackpot", "Weekly Jackpot"),
                ],
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="WeeklySpinJackpot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("week_start", models.DateField(unique=True)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("awarded_at", models.DateTimeField(auto_now_add=True)),
                (
                    "reward",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="weekly_jackpot_record",
                        to="rewards.spinreward",
                    ),
                ),
                (
                    "winner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="weekly_spin_jackpots",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-week_start"],
            },
        ),
    ]
