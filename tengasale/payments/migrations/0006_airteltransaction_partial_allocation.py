from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payments", "0005_airteltransaction_duplicate_callback_and_more")]

    operations = [
        migrations.AddField(
            model_name="airteltransaction",
            name="full_repayment_days_covered",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="airteltransaction",
            name="partial_credit_balance",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
    ]
