from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0018_financingapplication_device_serial_number_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="financingapplication",
            name="kyc_provider",
            field=models.CharField(
                blank=True,
                choices=[("", "Not set"), ("manual", "Manual"), ("didit", "Didit")],
                default="",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="financingapplication",
            name="kyc_status",
            field=models.CharField(
                blank=True,
                choices=[
                    ("not_started", "Not Started"),
                    ("in_progress", "In Progress"),
                    ("awaiting_user", "Awaiting User"),
                    ("pending_review", "Pending Review"),
                    ("approved", "Approved"),
                    ("declined", "Declined"),
                    ("resubmitted", "Resubmitted"),
                    ("abandoned", "Abandoned"),
                    ("expired", "Expired"),
                    ("kyc_expired", "KYC Expired"),
                    ("manual_review", "Manual Review"),
                ],
                default="not_started",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="financingapplication",
            name="didit_session_id",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="financingapplication",
            name="didit_session_token",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="financingapplication",
            name="didit_verification_url",
            field=models.URLField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="financingapplication",
            name="didit_status",
            field=models.CharField(blank=True, default="", max_length=60),
        ),
        migrations.AddField(
            model_name="financingapplication",
            name="didit_workflow_id",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="financingapplication",
            name="didit_vendor_data",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="financingapplication",
            name="didit_metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="financingapplication",
            name="didit_decision",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="financingapplication",
            name="didit_summary",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="financingapplication",
            name="didit_last_event_id",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="financingapplication",
            name="didit_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="financingapplication",
            name="didit_completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="financingapplication",
            name="didit_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="financingapplication",
            name="didit_declined_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="financingapplication",
            name="didit_resubmit_info",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="financingapplication",
            name="didit_manual_override",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="financingapplication",
            name="didit_manual_override_reason",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.CreateModel(
            name="DiditWebhookEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_id", models.CharField(max_length=120, unique=True)),
                ("session_id", models.CharField(blank=True, default="", max_length=120)),
                ("webhook_type", models.CharField(blank=True, default="", max_length=80)),
                ("status", models.CharField(blank=True, default="", max_length=80)),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("processed", models.BooleanField(default=False)),
                ("processing_error", models.TextField(blank=True, null=True)),
            ],
            options={
                "verbose_name": "Didit Webhook Event",
                "verbose_name_plural": "Didit Webhook Events",
                "ordering": ["-received_at"],
            },
        ),
    ]
