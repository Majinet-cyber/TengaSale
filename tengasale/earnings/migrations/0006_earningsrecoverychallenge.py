from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [("earnings", "0005_earningssecurityevent"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(name="EarningsRecoveryChallenge", fields=[
        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
        ("purpose", models.CharField(choices=[("enable", "Enable lock"), ("reset", "Reset PIN")], max_length=12)),
        ("code_hash", models.CharField(editable=False, max_length=255)),
        ("phone_mask", models.CharField(max_length=30)),
        ("expires_at", models.DateTimeField()),
        ("attempts", models.PositiveSmallIntegerField(default=0)),
        ("verified_at", models.DateTimeField(blank=True, null=True)),
        ("created_at", models.DateTimeField(auto_now_add=True)),
        ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="earnings_recovery_challenges", to=settings.AUTH_USER_MODEL)),
    ], options={"ordering": ["-created_at"], "indexes": [models.Index(fields=["user", "purpose", "created_at"], name="earn_recovery_lookup_idx")]})]
