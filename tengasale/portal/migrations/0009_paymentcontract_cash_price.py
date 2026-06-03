from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0008_paymenttransaction_balance_after_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="paymentcontract",
            name="cash_price",
            field=models.DecimalField(
                max_digits=12,
                decimal_places=2,
                default=Decimal("0"),
                help_text="Phone cash price — merchant settlement basis",
            ),
        ),
    ]
