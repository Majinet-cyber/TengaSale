from decimal import Decimal

from django.db import migrations


OFFENSES = [
    ("UNCLEAR_ID", "Unclear ID / unreadable identity document", "minor", "5000.00"),
    ("UNCLEAR_CUSTOMER_PHOTO", "Unclear customer photo or selfie", "minor", "5000.00"),
    ("POOR_CALL_AUDIO", "Poor or inaudible call recording", "minor", "5000.00"),
    ("MISSING_CALL_RECORDING", "Missing required call recording", "moderate", "10000.00"),
    ("PHONE_ID_MISMATCH", "Customer phone number does not match ID / registered customer identity", "moderate", "20000.00"),
    ("CUSTOMER_NOT_CALLED", "Customer was not called before approval", "major", "30000.00"),
    ("BAD_CUSTOMER_EDUCATION", "Customer poorly educated on payment terms", "major", "30000.00"),
    ("PAYMENT_TERMS_NOT_EXPLAINED", "Customer did not understand how much or how often to pay", "major", "35000.00"),
    ("INVALID_PHONE_NUMBER", "Invalid phone number used in application", "critical", "50000.00"),
    ("FRAUDULENT_OR_FALSE_APPROVAL", "False, reckless, or fraudulent approval", "critical", "100000.00"),
]


def seed_offense_types(apps, schema_editor):
    QCOffenseType = apps.get_model("approvals", "QCOffenseType")
    for code, name, severity, amount in OFFENSES:
        QCOffenseType.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "description": name,
                "severity": severity,
                "default_penalty_mwk": Decimal(amount),
                "active": True,
            },
        )


def unseed_offense_types(apps, schema_editor):
    QCOffenseType = apps.get_model("approvals", "QCOffenseType")
    QCOffenseType.objects.filter(code__in=[code for code, *_rest in OFFENSES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("approvals", "0007_qcoffensetype_underwritercallrecording_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_offense_types, unseed_offense_types),
    ]
