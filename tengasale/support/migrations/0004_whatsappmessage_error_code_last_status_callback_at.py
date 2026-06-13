# Generated manually 2026-06-13

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("support", "0003_supportticket_escalation_reason_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="whatsappmessage",
            name="error_code",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="whatsappmessage",
            name="last_status_callback_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
