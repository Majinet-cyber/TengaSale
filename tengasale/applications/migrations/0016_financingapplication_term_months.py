from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0015_alter_financingapplication_gender"),
    ]

    operations = [
        migrations.AddField(
            model_name="financingapplication",
            name="term_months",
            field=models.PositiveIntegerField(
                default=12,
                help_text="Selected contract term in months: 3, 6, or 12",
            ),
        ),
    ]
