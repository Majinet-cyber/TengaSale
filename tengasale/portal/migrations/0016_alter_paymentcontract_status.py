from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("portal", "0015_paymentcontract_partial_repayment_credit"),
    ]

    operations = [
        migrations.AlterField(
            model_name="paymentcontract",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending_activation", "Pending activation"),
                    ("active", "Active"),
                    ("overdue", "Overdue"),
                    ("locked", "Locked"),
                    ("completed", "Completed"),
                    ("cancelled", "Cancelled"),
                    ("repossession_pending", "Repossession Pending"),
                    ("repossessed", "Repossessed"),
                    ("ready_for_resale", "Ready for Resale"),
                    ("resold", "Resold"),
                    ("written_off", "Written Off / Shortfall"),
                    ("legally_closed", "Legally Closed"),
                ],
                default="active",
                max_length=20,
            ),
        ),
    ]
