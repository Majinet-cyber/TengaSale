from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("accounts", "0009_userprofile_earnings_lock")]
    operations = [
        migrations.AddField(model_name="userprofile", name="earnings_recovery_phone", field=models.CharField(blank=True, editable=False, max_length=30)),
        migrations.AddField(model_name="userprofile", name="earnings_recovery_verified_at", field=models.DateTimeField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name="userprofile", name="earnings_security_generation", field=models.PositiveIntegerField(default=1, editable=False)),
    ]
