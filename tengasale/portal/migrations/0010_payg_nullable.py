from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0009_paymentcontract_cash_price"),
    ]

    operations = [
        migrations.AlterField(
            model_name="paymentcontract",
            name="payg_number",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Customer-facing PayG reference — format EXXXXXXX (8 chars, starts with E)",
                max_length=8,
                null=True,
                unique=True,
            ),
        ),
    ]
