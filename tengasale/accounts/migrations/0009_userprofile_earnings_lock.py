from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("accounts", "0008_staffdocument_issued_at_staffdocument_signed_at_and_more")]
    operations = [
        migrations.AddField(model_name="userprofile", name="earnings_lock_enabled", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="userprofile", name="earnings_pin_hash", field=models.CharField(blank=True, editable=False, max_length=255)),
        migrations.AddField(model_name="userprofile", name="earnings_lock_created_at", field=models.DateTimeField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name="userprofile", name="earnings_pin_updated_at", field=models.DateTimeField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name="userprofile", name="earnings_failed_attempt_count", field=models.PositiveSmallIntegerField(default=0, editable=False)),
        migrations.AddField(model_name="userprofile", name="earnings_locked_until", field=models.DateTimeField(blank=True, editable=False, null=True)),
    ]
