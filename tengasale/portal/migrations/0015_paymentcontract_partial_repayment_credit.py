from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("portal", "0014_alter_paymenttransaction_status")]

    operations = [
        migrations.AddField(
            model_name="paymentcontract",
            name="partial_repayment_credit",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text="Confirmed regular repayments not yet allocated to a full repayment day.",
                max_digits=12,
            ),
        ),
        migrations.AddField(
            model_name="paymentcontract",
            name="partial_repayment_daily_rate",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text="Daily rate used for the current partial repayment accumulation cycle.",
                max_digits=10,
            ),
        ),
    ]
