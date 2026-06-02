"""
Migration 0006 — PayG upgrade.

- Alters payg_number max_length from 10 to 8.
- Adds imei_number, deposit_required to PaymentContract.
- Adds lock_reference, provider_contract_reference, provider_device_reference,
  provider_metadata to PaymentContract.
- Adds payment_type to PaymentTransaction.
- Adds imei_number index to PaymentContract.

Note: existing payg_number values (TSG######) are longer than 8 chars and will be
backfilled by the backfill_payg_numbers management command before the non-null
constraint is enforced. The field stays blank=True so backfill can clear + regenerate.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0005_add_analytics_fields"),
    ]

    operations = [
        # ------------------------------------------------------------------
        # PaymentContract — payg_number: relax max_length, keep unique+blank
        # (old TSGxxxxxx values will be overwritten by backfill command)
        # ------------------------------------------------------------------
        migrations.AlterField(
            model_name="paymentcontract",
            name="payg_number",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Customer-facing PayG reference — format EXXXXXXX (8 chars, starts with E)",
                max_length=8,
                unique=True,
            ),
        ),

        # ------------------------------------------------------------------
        # PaymentContract — new fields
        # ------------------------------------------------------------------
        migrations.AddField(
            model_name="paymentcontract",
            name="imei_number",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Device IMEI — stored directly for fast portal lookup",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="paymentcontract",
            name="deposit_required",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text="Required deposit amount for this contract",
                max_digits=12,
            ),
        ),
        migrations.AddField(
            model_name="paymentcontract",
            name="lock_reference",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Internal reference sent to lock provider at enrollment",
                max_length=120,
            ),
        ),
        migrations.AddField(
            model_name="paymentcontract",
            name="provider_contract_reference",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Contract reference assigned by lock provider",
                max_length=120,
            ),
        ),
        migrations.AddField(
            model_name="paymentcontract",
            name="provider_device_reference",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Device reference assigned by lock provider",
                max_length=120,
            ),
        ),
        migrations.AddField(
            model_name="paymentcontract",
            name="provider_metadata",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text=(
                    "Metadata sent to/from lock provider, e.g. "
                    '{"payg_number": "EXGH4456", "imei": "...", "payment_url": "..."}'
                ),
            ),
        ),

        # ------------------------------------------------------------------
        # PaymentContract — add imei_number index
        # ------------------------------------------------------------------
        migrations.AddIndex(
            model_name="paymentcontract",
            index=models.Index(fields=["imei_number"], name="portal_paym_imei_nu_idx"),
        ),

        # ------------------------------------------------------------------
        # PaymentTransaction — payment_type
        # ------------------------------------------------------------------
        migrations.AddField(
            model_name="paymenttransaction",
            name="payment_type",
            field=models.CharField(
                choices=[
                    ("deposit", "Deposit"),
                    ("repayment", "Repayment"),
                    ("penalty", "Penalty"),
                    ("adjustment", "Adjustment"),
                    ("refund", "Refund"),
                ],
                default="repayment",
                help_text="DEPOSIT for initial deposit, REPAYMENT for regular instalments, etc.",
                max_length=15,
            ),
        ),
    ]
